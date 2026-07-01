"""Unit + integration tests for the continuation-reference chain feature.
Run: python tests/test_pipeline_continuation.py"""
from __future__ import annotations

import struct
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import occ_audio.pipeline as pipeline                         # noqa: E402
from occ_audio.backends.base import AudioResult               # noqa: E402
from occ_audio.config import load_project                      # noqa: E402
from occ_audio.pipeline import RunOptions, SegmentJob, _group_jobs  # noqa: E402
from occ_audio.segmenter import DraftSegment                   # noqa: E402

_passed = 0
_failed = 0


def check(label: str, condition: bool) -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}")


def _fake_job(seg_id: str, is_continuation: bool) -> SegmentJob:
    return SegmentJob(
        index=int(seg_id[1:]) - 1, draft=DraftSegment(seg_id=seg_id),
        prompt="p", references=[], audio_tag={}, input_hash="h", cumulative="h",
        reuse_hit=None, is_continuation=is_continuation, continuation_index=None,
    )


def test_group_jobs_chains_consecutive_continuations() -> None:
    print("_group_jobs: consecutive continuation-linked segments share a group")
    jobs = [
        _fake_job("S001", False),
        _fake_job("S002", True),
        _fake_job("S003", True),
        _fake_job("S004", False),
        _fake_job("S005", True),
    ]
    groups = _group_jobs(jobs)
    check("2 groups produced", len(groups) == 2)
    check("group 1 = S001,S002,S003",
          [j.draft.seg_id for j in groups[0]] == ["S001", "S002", "S003"])
    check("group 2 = S004,S005 (S005 continues S004)",
          [j.draft.seg_id for j in groups[1]] == ["S004", "S005"])


def _make_wav_bytes(n_samples: int = 4800) -> bytes:
    data = b"\x00\x00" * n_samples
    header = (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt "
              + struct.pack("<IHHIIHH", 16, 1, 1, 24000, 48000, 2, 16)
              + b"data" + struct.pack("<I", len(data)))
    return header + data


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def preflight(self) -> None:
        pass

    def generate(self, spec, *, model, on_progress=None):
        self.calls.append({"prompt": spec.text_prompt, "references": list(spec.references)})
        return AudioResult(audio_bytes=_make_wav_bytes(), duration=0.2,
                           original_duration=0.2)


def _write_chained_project(root: Path) -> Path:
    (root / "script.txt").write_text(
        "\n\n".join([
            "Chapter One",
            'Hero said, "This is the first line of the story."',
            "Chapter Two",
            'Hero said, "This continues right after the first line."',
            "Chapter Three",
            'Hero said, "And this is the final line of the story."',
        ]) + "\n", encoding="utf-8")
    (root / "project.yaml").write_text(
        "\n".join([
            "name: chain_test", "source: script.txt", "cast_mode: all",
            "cast:", "  Hero:", "    voice_note: a narrator voice",
            "defaults:",
            "  target_segment_seconds: 5",   # force one segment per chapter
            "  continuation_seconds: 20",
        ]) + "\n", encoding="utf-8")
    return root / "project.yaml"


def test_chained_run_generates_tail_clips_and_manifest() -> None:
    print("run(): a continuation-enabled project chains segments, "
          "extracts tail clips, and records cumulative_hash")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        project = load_project(str(_write_chained_project(root)))

        fake = FakeBackend()
        pipeline.get_audio_backend = lambda name: (fake, "seed-audio-1.0")

        result = pipeline.run(project, RunOptions())
        check("run succeeded with a final file", result.final_path is not None)
        check("3 segments generated, none reused", result.generated == 3 and result.reused == 0)
        check("no failures", not result.failed)

        # At least one call should have carried a real (non-placeholder) tail
        # clip reference once the chain reaches its second link.
        tail_dir = result.run_dir / "tails"
        check("a tail clip file was written", tail_dir.is_dir()
              and any(tail_dir.iterdir()))

        import json
        manifest = json.loads((result.run_dir / "manifest.json").read_text(encoding="utf-8"))
        check("every segment record carries a cumulative_hash",
              all("cumulative_hash" in s for s in manifest["segments"]))

        # A second run with the same project should reuse every segment via
        # the cumulative hash chain (nothing changed).
        fake2 = FakeBackend()
        pipeline.get_audio_backend = lambda name: (fake2, "seed-audio-1.0")
        result2 = pipeline.run(project, RunOptions())
        check("second run reuses all 3 segments via the chain hash",
              result2.reused == 3 and result2.generated == 0)
        check("no backend calls made on the fully-reused second run",
              len(fake2.calls) == 0)


if __name__ == "__main__":
    test_group_jobs_chains_consecutive_continuations()
    test_chained_run_generates_tail_clips_and_manifest()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
