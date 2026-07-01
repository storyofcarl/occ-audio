"""Command-line interface - ``python -m occ_audio <command>``.

Commands:
    cast       generate audition takes for characters (paid)
    scan       show the segmentation / reuse plan without calling any API
    preview    assemble every prompt for review (no API calls)
    generate   source -> Seed Audio segments -> stitched audiobook/radioplay
    stitch     re-concatenate the segments of an existing run
    models     list registered models and their backends
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .backends.base import BackendError
from .backends.registry import list_models
from .casting import estimate_audition_cost, extract_characters, generate_auditions
from .config import load_project
from .manifest import latest_run_dir, log
from .pipeline import (
    PROMPT_CHAR_LIMIT, RunOptions, estimate_cost, format_cost_line, preview,
    restitch, run,
)
from .script_source import load_source, normalize_speakers


def _split_csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


def cmd_cast(args: argparse.Namespace) -> int:
    project = load_project(args.project)
    doc = normalize_speakers(load_source(project.source), project.name_aliases)
    characters = extract_characters(doc, project)
    wanted = _split_csv(args.characters)
    if wanted:
        characters = [c for c in characters if c in wanted]
    if not characters:
        print("No speaking characters found to cast.", file=sys.stderr)
        return 1

    to_cast = [c for c in characters if args.force or not project.is_locked(c)]
    clips, seconds, cost = estimate_audition_cost(len(to_cast), args.n)
    print(f"COST ESTIMATE: {len(to_cast)} character(s) x {args.n} take(s) = "
          f"{clips} clip(s), ~{seconds:.0f}s (~{seconds / 60.0:.1f} min) -> "
          f"~${cost:.2f} at $0.15/min (estimate only)\n")

    for name in characters:
        if project.is_locked(name) and not args.force:
            print(f"{name}: already locked (reference set) - skipping "
                  f"(use --force to regenerate auditions)")
            continue
        print(f"{name}: generating {args.n} audition take(s) ...")
        takes = generate_auditions(project, name, n=args.n,
                                   on_progress=lambda m: log(f"  {m}"))
        for t in takes:
            print(f"  [{t.variant}] {t.path}")
        print(f"  Listen, then set cast.{name}.reference in project.yaml "
              f"to your chosen take.")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    project = load_project(args.project)
    result = run(project, RunOptions(dry_run=True,
                                     segments_filter=_split_csv(args.segments)))
    print()
    for line in result.plan_lines:
        print("  " + line)
    print(f"\n{format_cost_line(result.estimated_new_clips, result.estimated_new_seconds, result.estimated_cost_usd)}")
    return 0


def _ref_label(project, name: str) -> str:
    ref = project.voice_reference(name)
    speaker = project.voice_speaker(name)
    if ref:
        return f"{name}: reference={ref.name}"
    if speaker:
        return f"{name}: speaker={speaker}"
    return f"{name}: voice_note only (not locked)"


def cmd_preview(args: argparse.Namespace) -> int:
    project = load_project(args.project)
    jobs = preview(project)
    out = Path(args.out) if args.out else project.root / "_prompts_preview.md"
    blocks = [f"# {project.name} - assembled prompts (preview)\n"]
    over: list[str] = []
    for job in jobs:
        n = len(job.prompt)
        if n > PROMPT_CHAR_LIMIT:
            over.append(job.draft.seg_id)
        continuation_names = ({n for n, t in job.audio_tag.items() if t == "@Audio1"}
                              if job.is_continuation else set())
        ref_lines = [
            f"  {tag}  {name}: continuation (tail clip of the previous segment)"
            if name in continuation_names else f"  {tag}  {_ref_label(project, name)}"
            for name, tag in sorted(job.audio_tag.items(), key=lambda kv: kv[1])
        ]
        if job.draft.overflow_characters:
            ref_lines.append(
                f"  overflow (voice_note only): "
                f"{', '.join(job.draft.overflow_characters)}")
        refs = "\n".join(ref_lines) or "  (none)"
        flag = "  -- OVER LIMIT" if n > PROMPT_CHAR_LIMIT else ""
        warn = ("\n\nWarnings:\n" + "\n".join(f"  {w}" for w in job.draft.warnings)
                if job.draft.warnings else "")
        blocks.append(
            f"\n## {job.draft.seg_id}  (~{job.draft.estimated_seconds:.0f}s)  -  "
            f"{n} chars{flag}\n\nReferences:\n{refs}{warn}\n\n"
            f"Prompt:\n```\n{job.prompt}\n```")
    out.write_text("\n".join(blocks) + "\n", encoding="utf-8")
    print(f"{len(jobs)} prompt(s) written -> {out}\n")
    for job in jobs:
        n = len(job.prompt)
        flag = "  <-- OVER LIMIT" if n > PROMPT_CHAR_LIMIT else ""
        print(f"  {job.draft.seg_id:<10} {n:>5} chars  "
              f"{len(job.references)} ref(s){flag}")
    clips, seconds, cost = estimate_cost(jobs)
    print(f"\n{format_cost_line(clips, seconds, cost)}")
    if over:
        print(f"\nWARNING: over the {PROMPT_CHAR_LIMIT}-char limit: {', '.join(over)}")
        return 1
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    project = load_project(args.project)
    result = run(project, RunOptions(
        dry_run=args.dry_run,
        max_segments=args.max_segments,
        segments_filter=_split_csv(args.segments),
        run_name=args.run_name,
        regen=args.regen,
        concurrency=args.concurrency,
    ))
    if result.dry_run:
        print("\nDry run complete - no API calls, no files written.")
        return 0
    if result.final_path:
        print("\nDone - complete audio.")
        print(f"  File:     {result.latest_path or result.final_path}")
        print(f"  Archive:  {result.final_path}")
        print(f"  Segments: {result.generated} generated, {result.reused} reused")
        return 0
    done = result.generated + result.reused
    if done:
        print(f"\nIncomplete - {len(result.failed)} segment(s) failed; "
              f"{done} finished. No file stitched (would have gaps).")
        print(f"  Failed:  {', '.join(result.failed)}")
        print(f"  Re-run to finish - the {done} finished segment(s) reuse free.")
    else:
        print(f"\nFailed - every segment failed: {', '.join(result.failed)}")
    return 1


def cmd_stitch(args: argparse.Namespace) -> int:
    project = load_project(args.project)
    run_dir = Path(args.run) if args.run else latest_run_dir(project.output_dir)
    if not run_dir or not run_dir.is_dir():
        print("No run folder found to stitch. Run `generate` first.", file=sys.stderr)
        return 1
    final, latest = restitch(project, run_dir)
    print(f"Stitched -> {final}")
    print(f"File     -> {latest}")
    return 0


def cmd_models(_args: argparse.Namespace) -> int:
    print(f"occ-audio {__version__} - registered models\n")
    for name, entry in list_models():
        note = f"  ({entry.note})" if entry.note else ""
        print(f"    {name:<20} backend={entry.backend:<12} {entry.vendor_id}{note}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="occ_audio",
        description="One-click creation - config-driven audiobook/radioplay generation.",
    )
    parser.add_argument("--version", action="version", version=f"occ-audio {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("cast", help="Generate audition takes for characters.")
    c.add_argument("project", help="Project name, directory, or path to project.yaml")
    c.add_argument("--characters", help="Comma-separated character names to cast.")
    c.add_argument("--force", action="store_true",
                   help="Regenerate auditions even for already-locked characters.")
    c.add_argument("-n", type=int, default=4, help="Number of audition takes (default 4).")
    c.set_defaults(func=cmd_cast)

    sc = sub.add_parser("scan", help="Show the segmentation/reuse plan (no API calls).")
    sc.add_argument("project", help="Project name, directory, or path to project.yaml")
    sc.add_argument("--segments", help="Comma-separated segment ids.")
    sc.set_defaults(func=cmd_scan)

    pv = sub.add_parser("preview",
                        help="Assemble every prompt for review (no API calls).")
    pv.add_argument("project", help="Project name, directory, or path to project.yaml")
    pv.add_argument("--out", help="Output file (default: <project>/_prompts_preview.md).")
    pv.set_defaults(func=cmd_preview)

    g = sub.add_parser("generate", help="Run the full pipeline.")
    g.add_argument("project", help="Project name, directory, or path to project.yaml")
    g.add_argument("--dry-run", action="store_true", help="Print the plan; call no APIs.")
    g.add_argument("--max-segments", type=int,
                   help="Only run the first N segments (smoke test).")
    g.add_argument("--segments", help="Comma-separated segment ids to generate.")
    g.add_argument("--regen", help="Bypass reuse: 'all' or comma-separated segment ids.")
    g.add_argument("--concurrency", type=int,
                   help="How many segments to generate at once (default: project setting).")
    g.add_argument("--run-name", help="Custom run folder name under outputs/runs/.")
    g.set_defaults(func=cmd_generate)

    s = sub.add_parser("stitch", help="Re-stitch an existing run's segments.")
    s.add_argument("project", help="Project name, directory, or path to project.yaml")
    s.add_argument("--run", help="Run folder path (default: most recent).")
    s.set_defaults(func=cmd_stitch)

    m = sub.add_parser("models", help="List registered models.")
    m.set_defaults(func=cmd_models)

    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (BackendError, FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
