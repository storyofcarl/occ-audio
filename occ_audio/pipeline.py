"""The orchestrator — source script -> segments -> Seed Audio generation ->
stitched audiobook/radioplay.

The API itself has no continuation mechanism, but a project may opt into
``continuation_seconds`` (METHODOLOGY.md §2a): each segment then carries a
tail clip of the *previous* segment's own generated audio forward as a
reference, covering whichever character(s) actually spoke in that trailing
window. Continuation-linked segments must run in order (each needs its
predecessor's finished audio to extract the tail clip from); segments that
are NOT continuation-linked to their neighbor still run concurrently with
other independent segments/chains — the same cut-vs-chain concurrency shape
occ's video pipeline uses. With ``continuation_seconds`` left at 0 (the
default), every segment is independent and the whole run is parallel.

Segments unchanged since a prior run are copied, not regenerated. Everything
is ``--dry-run``-safe.
"""
from __future__ import annotations

import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .backends.base import AudioReference, AudioSpec, BackendError
from .backends.registry import get_audio_backend
from .backends.seed_audio import MAX_TEXT_PROMPT_CHARS, PRICE_PER_MINUTE_USD
from .config import ProjectConfig
from .manifest import RunManifest, log, new_run_dir, write_json
from .prompts import build_prompt
from .reuse import ReuseIndex, cumulative_hash, references_hash, segment_input_hash
from .script_source import load_source, normalize_speakers
from .segmenter import DraftSegment, segment_source
from .stitch import concat_audios, extract_tail_audio

MAX_CONCURRENCY = 32
PROMPT_CHAR_LIMIT = MAX_TEXT_PROMPT_CHARS


@dataclass
class RunOptions:
    dry_run: bool = False
    max_segments: int | None = None
    segments_filter: list[str] | None = None
    run_name: str | None = None
    regen: str | None = None           # "all" | comma-separated segment ids
    concurrency: int | None = None


@dataclass
class RunResult:
    run_dir: Path
    final_path: Path | None
    latest_path: Path | None = None
    segments: list[Path] = field(default_factory=list)
    dry_run: bool = False
    plan_lines: list[str] = field(default_factory=list)
    reused: int = 0
    generated: int = 0
    failed: list[str] = field(default_factory=list)
    segment_hashes: dict[str, str] = field(default_factory=dict)
    estimated_new_clips: int = 0        # clips NOT covered by reuse (will cost)
    estimated_new_seconds: float = 0.0  # rough duration estimate, not billed fact
    estimated_cost_usd: float = 0.0


@dataclass
class SegmentJob:
    index: int
    draft: DraftSegment
    prompt: str
    references: list[AudioReference]
    audio_tag: dict[str, str]
    input_hash: str                    # this segment's own inputs, unchained
    cumulative: str                    # folded with the chain so far (reuse key)
    reuse_hit: dict | None
    is_continuation: bool = False
    continuation_index: int | None = None   # index into `references` that is
        # the not-yet-real tail-clip placeholder, substituted at execution time


def _safe_slug(text: str) -> str:
    text = re.sub(r"\s+", "_", text.strip())
    text = re.sub(r"[^A-Za-z0-9._-]+", "", text)
    return text[:80] or "segment"


def _ext_for(audio_format: str) -> str:
    return {"wav": "wav", "mp3": "mp3", "pcm": "pcm", "ogg_opus": "ogg"}.get(
        audio_format, "wav")


def _next_iteration(output_dir: Path, slug: str, ext: str) -> int:
    highest = 0
    for p in output_dir.glob(f"{slug}_v*.{ext}"):
        m = re.fullmatch(rf"{re.escape(slug)}_v(\d+)\.{re.escape(ext)}", p.name)
        if m:
            highest = max(highest, int(m.group(1)))
    return highest + 1


def _publish_latest(project: ProjectConfig, final_path: Path) -> Path:
    out_dir = Path(project.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = _safe_slug(project.name)
    ext = final_path.suffix.lstrip(".")
    versioned = out_dir / f"{slug}_v{_next_iteration(out_dir, slug, ext):02d}.{ext}"
    _copy_with_retry(final_path, versioned)
    _copy_with_retry(final_path, out_dir / f"{slug}_latest.{ext}")
    return versioned


def _assemble_output(project: ProjectConfig, segment_paths: list[Path],
                     run_dir: Path) -> Path:
    ext = _ext_for(project.defaults.audio_format)
    final_path = run_dir / f"final.{ext}"
    concat_audios([str(s) for s in segment_paths], str(final_path))
    return final_path


def _regen_set(regen: str | None, all_ids: list[str]) -> set[str]:
    if not regen:
        return set()
    if regen.strip().lower() == "all":
        return set(all_ids)
    return {s.strip() for s in regen.split(",") if s.strip()}


def estimate_cost(jobs: list[SegmentJob]) -> tuple[int, float, float]:
    """(clips to actually generate, estimated total seconds, estimated USD)
    for the jobs that are NOT reuse hits. This is a rough estimate off the
    segmenter's words-per-minute duration guess, not a billing fact — actual
    cost is based on the real ``original_duration`` Seed Audio returns."""
    to_generate = [j for j in jobs if not j.reuse_hit]
    seconds = sum(j.draft.estimated_seconds for j in to_generate)
    cost = (seconds / 60.0) * PRICE_PER_MINUTE_USD
    return len(to_generate), seconds, cost


def format_cost_line(clips: int, seconds: float, cost: float) -> str:
    return (f"COST ESTIMATE: {clips} clip(s) to generate, ~{seconds:.0f}s "
            f"(~{seconds / 60.0:.1f} min) -> ~${cost:.2f} at "
            f"${PRICE_PER_MINUTE_USD:.2f}/min (estimate only, actual "
            f"billing is per-clip real duration)")


def _plan_line(job: SegmentJob, total: int) -> str:
    d = job.draft
    bits = [
        f"{job.index + 1}/{total} {d.seg_id} seq{d.sequence_id} ~{d.estimated_seconds:.0f}s "
        f"{len(job.prompt)}chars",
        f"locked={','.join(d.locked_characters) or '(none)'}",
    ]
    if d.overflow_characters:
        bits.append(f"overflow={','.join(d.overflow_characters)}")
    if job.is_continuation:
        covered = [n for n, t in job.audio_tag.items() if t == "@Audio1"]
        bits.append(f"continuation=prev-tail({','.join(covered)})")
    if d.warnings:
        bits.append(f"WARN:{'; '.join(d.warnings)}")
    if len(job.prompt) > PROMPT_CHAR_LIMIT:
        bits.append(f"OVER {PROMPT_CHAR_LIMIT}-char limit")
    bits.append("segment=" + (f"REUSE({job.reuse_hit['run']})"
                              if job.reuse_hit else "generate"))
    return "  ".join(bits)


def _plan_jobs(drafts: list[DraftSegment], project: ProjectConfig,
              reuse_index: ReuseIndex, regen_ids: set[str],
              ) -> tuple[list[SegmentJob], list[str]]:
    jobs: list[SegmentJob] = []
    continuation_seconds = project.defaults.continuation_seconds
    max_chain = project.defaults.max_chain
    prev_cumulative: str | None = None
    chain_len = 1

    for i, draft in enumerate(drafts):
        prev_draft = drafts[i - 1] if i > 0 else None
        prospective_len = chain_len + 1
        same_sequence = prev_draft is not None and prev_draft.sequence_id == draft.sequence_id
        use_continuation = bool(
            continuation_seconds > 0 and prev_draft is not None and same_sequence
            and prev_draft.tail_characters
            and (max_chain <= 0 or prospective_len <= max_chain)
        )
        continuation_characters = prev_draft.tail_characters if use_continuation else None

        prompt, references, audio_tag, continuation_index = build_prompt(
            draft, project, continuation_characters=continuation_characters)
        hashable_refs = [r for idx, r in enumerate(references) if idx != continuation_index]
        ref_hash = references_hash(hashable_refs)
        input_hash = segment_input_hash(
            text_prompt=prompt, model=project.defaults.model,
            audio_format=project.defaults.audio_format,
            sample_rate=project.defaults.sample_rate,
            speech_rate=project.defaults.speech_rate,
            loudness_rate=project.defaults.loudness_rate,
            pitch_rate=project.defaults.pitch_rate,
            reference_hash=ref_hash,
        )
        cumulative = cumulative_hash(input_hash, prev_cumulative if use_continuation else None)
        reuse_hit = None if draft.seg_id in regen_ids else reuse_index.segment(cumulative)

        jobs.append(SegmentJob(
            index=i, draft=draft, prompt=prompt, references=references,
            audio_tag=audio_tag, input_hash=input_hash, cumulative=cumulative,
            reuse_hit=reuse_hit, is_continuation=use_continuation,
            continuation_index=continuation_index,
        ))
        prev_cumulative = cumulative
        chain_len = prospective_len if use_continuation else 1

    plan_lines = [_plan_line(job, len(jobs)) for job in jobs]
    return jobs, plan_lines


def _group_jobs(jobs: list[SegmentJob]) -> list[list[SegmentJob]]:
    """Group consecutive continuation-linked segments together; each group
    runs sequentially (a chain), different groups run concurrently."""
    groups: list[list[SegmentJob]] = []
    for job in jobs:
        if job.is_continuation and groups:
            groups[-1].append(job)
        else:
            groups.append([job])
    return groups


def _copy_with_retry(src: str | Path, dst: str | Path, attempts: int = 8) -> None:
    """Copy a file via plain read/write (not ``shutil.copy2``'s CopyFile2
    path, which has been observed to hit a spurious WinError 32 sharing
    violation on a file an ffmpeg subprocess just finished writing), with a
    short retry for the rare case the source is still momentarily locked."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    last_exc: OSError | None = None
    for attempt in range(attempts):
        try:
            dst.write_bytes(Path(src).read_bytes())
            return
        except OSError as exc:
            last_exc = exc
            time.sleep(0.2 * (attempt + 1))
    raise last_exc


def _execute_job(job: SegmentJob, project: ProjectConfig, run_dir: Path,
                 manifest: RunManifest, lock: threading.Lock) -> dict:
    d = job.draft
    slug = _safe_slug(d.seg_id)
    ext = _ext_for(project.defaults.audio_format)
    seg_path = run_dir / "segments" / f"{job.index + 1:02d}_{slug}.{ext}"

    try:
        (run_dir / "prompts" / f"{job.index + 1:02d}_{slug}.txt").write_text(
            job.prompt + "\n", encoding="utf-8")

        if job.reuse_hit:
            _copy_with_retry(job.reuse_hit["output"], seg_path)
            record = {
                "index": job.index + 1, "seg_id": d.seg_id,
                "cumulative_hash": job.cumulative,
                "reused_from": job.reuse_hit["run"], "output": str(seg_path),
            }
            kind = "reused"
            log(f"  [{job.index + 1}] {d.seg_id} REUSED from {job.reuse_hit['run']}")
        else:
            backend, vendor_id = get_audio_backend(project.defaults.model)
            spec = AudioSpec(
                text_prompt=job.prompt, references=job.references,
                audio_format=project.defaults.audio_format,
                sample_rate=project.defaults.sample_rate,
                speech_rate=project.defaults.speech_rate,
                loudness_rate=project.defaults.loudness_rate,
                pitch_rate=project.defaults.pitch_rate,
            )
            log(f"  [{job.index + 1}] {d.seg_id} generating "
                f"({project.defaults.model}, {len(job.references)} ref(s)"
                + (", continuation" if job.is_continuation else "") + ")")
            result = None
            for attempt in (1, 2):
                try:
                    result = backend.generate(
                        spec, model=vendor_id,
                        on_progress=lambda m, s=d.seg_id: log(f"  {s}: {m}"))
                    break
                except Exception as exc:  # noqa: BLE001
                    if attempt == 1:
                        log(f"  [{job.index + 1}] {d.seg_id} attempt 1 failed "
                            f"({exc}) - retrying")
                        continue
                    raise BackendError(
                        f"{exc} -- failed twice; the prompt likely needs "
                        f"a remedy") from exc
            seg_path.parent.mkdir(parents=True, exist_ok=True)
            seg_path.write_bytes(result.audio_bytes)
            write_json(run_dir / "results" / f"{job.index + 1:02d}_{slug}.json",
                       result.raw)
            record = {
                "index": job.index + 1, "seg_id": d.seg_id,
                "cumulative_hash": job.cumulative, "reused_from": None,
                "output": str(seg_path), "duration": result.duration,
                "original_duration": result.original_duration,
            }
            kind = "generated"
            log(f"  [{job.index + 1}] {d.seg_id} saved {seg_path.name}")

        with lock:
            manifest.add_segment(record)
        return {"index": job.index, "seg_id": d.seg_id, "path": seg_path,
                "error": None, "kind": kind}
    except Exception as exc:  # noqa: BLE001
        log(f"  [{job.index + 1}] {d.seg_id} FAILED: {exc}")
        return {"index": job.index, "seg_id": d.seg_id, "path": None,
                "error": str(exc)}


def _execute_chain(chain: list[SegmentJob], project: ProjectConfig, run_dir: Path,
                   manifest: RunManifest, lock: threading.Lock) -> list[dict]:
    ext = _ext_for(project.defaults.audio_format)
    results: list[dict] = []
    prev_output: Path | None = None
    aborted = False

    for job in chain:
        if aborted:
            results.append({"index": job.index, "seg_id": job.draft.seg_id,
                            "path": None,
                            "error": "skipped (an earlier segment in the "
                                     "chain failed, so its tail clip does "
                                     "not exist)"})
            continue

        if job.continuation_index is not None:
            if prev_output is None:
                aborted = True
                results.append({"index": job.index, "seg_id": job.draft.seg_id,
                                "path": None,
                                "error": "continuation-linked segment has no "
                                         "predecessor output in this run"})
                continue
            tail_path = (run_dir / "tails"
                        / f"{_safe_slug(job.draft.seg_id)}_prev_tail.{ext}")
            extract_tail_audio(str(prev_output),
                               project.defaults.continuation_seconds,
                               str(tail_path))
            job.references[job.continuation_index] = AudioReference(
                audio_path=str(tail_path))

        result = _execute_job(job, project, run_dir, manifest, lock)
        results.append(result)
        if result["path"]:
            prev_output = result["path"]
        else:
            aborted = True

    return results


def _select(project: ProjectConfig, options: RunOptions) -> list[DraftSegment]:
    doc = normalize_speakers(load_source(project.source), project.name_aliases)
    drafts = segment_source(doc, project)
    if options.segments_filter:
        wanted = set(options.segments_filter)
        drafts = [d for d in drafts if d.seg_id in wanted]
    if options.max_segments is not None:
        drafts = drafts[: max(0, options.max_segments)]
    if not drafts:
        raise ValueError("No segments selected to generate.")
    return drafts


def run(project: ProjectConfig, options: RunOptions) -> RunResult:
    drafts = _select(project, options)
    regen_ids = _regen_set(options.regen, [d.seg_id for d in drafts])
    reuse_index = ReuseIndex.build(project.output_dir)
    jobs, plan_lines = _plan_jobs(drafts, project, reuse_index, regen_ids)

    concurrency = min(options.concurrency or project.defaults.concurrency,
                      MAX_CONCURRENCY)
    header = (f"project={project.name} segments={len(jobs)} mode={project.mode} "
              f"concurrency={concurrency} "
              f"prior-runs-indexed={len(reuse_index.segments)} segment(s)")
    log(header)
    for line in plan_lines:
        log("  " + line)

    clips, seconds, cost = estimate_cost(jobs)
    cost_line = format_cost_line(clips, seconds, cost)
    log(cost_line)

    if options.dry_run:
        log("DRY RUN - no API calls, no files written.")
        return RunResult(
            run_dir=Path(project.output_dir) / "runs" / "(dry-run)",
            final_path=None, dry_run=True,
            plan_lines=[header] + plan_lines,
            segment_hashes={j.draft.seg_id: j.cumulative for j in jobs},
            estimated_new_clips=clips, estimated_new_seconds=seconds,
            estimated_cost_usd=cost)

    run_dir = new_run_dir(project.output_dir, options.run_name)
    meta = {
        "occ_audio_version": __version__,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "project": project.name,
        "mode": project.mode,
        "source": str(project.source),
        "segments": len(jobs),
        "concurrency": concurrency,
    }
    manifest = RunManifest(run_dir=run_dir, meta=meta)
    manifest.save()
    write_json(run_dir / "run.json", meta)

    if any(not j.reuse_hit for j in jobs):
        get_audio_backend(project.defaults.model)[0].preflight()

    groups = _group_jobs(jobs)
    lock = threading.Lock()
    all_results: list[dict] = []
    workers = max(1, min(concurrency, len(groups)))
    log(f"executing {len(groups)} group(s) ({len(jobs)} segment(s)), "
        f"up to {workers} concurrent")
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_execute_chain, g, project, run_dir, manifest, lock)
                   for g in groups]
        for future in futures:
            all_results.extend(future.result())

    all_results.sort(key=lambda r: r["index"])
    ok = [r for r in all_results if r["path"]]
    failed = [r for r in all_results if not r["path"]]
    reused = sum(1 for r in ok if r.get("kind") == "reused")
    generated = sum(1 for r in ok if r.get("kind") == "generated")

    final_path: Path | None = None
    latest_path: Path | None = None
    if failed:
        log(f"{len(failed)} of {len(jobs)} segment(s) FAILED - NOT stitching an "
            f"incomplete file:")
        for r in failed:
            log(f"  {r['seg_id']}: {r['error']}")
        log(f"the {len(ok)} finished segment(s) are saved; re-run to complete "
            f"(finished segments reuse for free).")
    elif ok:
        final_path = _assemble_output(project, [r["path"] for r in ok], run_dir)
        latest_path = _publish_latest(project, final_path)
        log(f"stitched all {len(ok)} segment(s) ({reused} reused, {generated} "
            f"generated) -> {final_path}")
        log(f"published -> {latest_path}")

    manifest.meta.update({
        "final": str(final_path) if final_path else None,
        "latest": str(latest_path) if latest_path else None,
        "reused": reused, "generated": generated,
        "failed": [r["seg_id"] for r in failed],
    })
    manifest.save()
    return RunResult(
        run_dir=run_dir, final_path=final_path, latest_path=latest_path,
        segments=[r["path"] for r in ok], dry_run=False, plan_lines=plan_lines,
        reused=reused, generated=generated, failed=[r["seg_id"] for r in failed],
        segment_hashes={j.draft.seg_id: j.cumulative for j in jobs},
        estimated_new_clips=clips, estimated_new_seconds=seconds,
        estimated_cost_usd=cost)


def preview(project: ProjectConfig) -> list[SegmentJob]:
    """Assemble every segment's prompt without generating - for pre-spend review."""
    drafts = _select(project, RunOptions())
    jobs, _ = _plan_jobs(drafts, project, ReuseIndex.empty(), set())
    return jobs


def restitch(project: ProjectConfig, run_dir: Path) -> tuple[Path, Path]:
    """Re-concatenate the segments in a run folder. Returns (final, latest)."""
    seg_dir = run_dir / "segments"
    ext = _ext_for(project.defaults.audio_format)
    segments = sorted(seg_dir.glob(f"*.{ext}")) if seg_dir.is_dir() else []
    if not segments:
        raise FileNotFoundError(f"No segment .{ext} files found in {seg_dir}")
    final_path = _assemble_output(project, segments, run_dir)
    return final_path, _publish_latest(project, final_path)
