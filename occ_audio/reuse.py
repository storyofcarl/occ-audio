"""Cross-run segment reuse — hash-based, so re-running an unchanged project
copies prior segment audio instead of regenerating (zero API cost).

The API itself has no continuation mechanism, but the optional
continuation-reference feature (METHODOLOGY.md §2a) makes a segment's own
identity depend on its predecessor's output (the tail clip carried forward),
so a chained segment's reuse identity must fold in the whole chain so far —
exactly like occ's video-chain ``cumulative_hash``. A project that never
enables ``continuation_seconds`` never chains, and ``cumulative_hash``
degenerates to the segment's own input hash unchanged.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from .backends.base import AudioReference


def _sha(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update((part or "").encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def file_hash(path: Path | str) -> str:
    """Content hash of a file's bytes (16 hex)."""
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _reference_hash(ref: AudioReference) -> str:
    if ref.audio_path:
        return _sha("audio-file", file_hash(ref.audio_path))
    if ref.image_path:
        return _sha("image-file", file_hash(ref.image_path))
    if ref.speaker:
        return _sha("speaker", ref.speaker)
    if ref.audio_url:
        return _sha("audio-url", ref.audio_url)
    if ref.image_url:
        return _sha("image-url", ref.image_url)
    return _sha("empty-ref")


def references_hash(references: list[AudioReference]) -> str:
    if not references:
        return ""
    return _sha(*[_reference_hash(r) for r in references])


def segment_input_hash(*, text_prompt: str, model: str, audio_format: str,
                       sample_rate: int, speech_rate: int, loudness_rate: int,
                       pitch_rate: int, reference_hash: str) -> str:
    """Identity of one segment's own inputs, before any chain folding."""
    return _sha(
        "seg", text_prompt, model, audio_format, str(sample_rate),
        str(speech_rate), str(loudness_rate), str(pitch_rate), reference_hash,
    )


def cumulative_hash(input_hash: str, prev_cumulative: str | None) -> str:
    """Fold a segment's input hash with the chain so far (only meaningful
    when continuation_seconds > 0 links this segment to its predecessor)."""
    if prev_cumulative is None:
        return input_hash
    return _sha("chain", prev_cumulative, input_hash)


@dataclass
class ReuseIndex:
    """Lookup of reusable segments, built from every prior run's manifest."""
    segments: dict[str, dict] = field(default_factory=dict)   # hash -> record

    @classmethod
    def empty(cls) -> "ReuseIndex":
        return cls()

    @classmethod
    def build(cls, output_dir: Path) -> "ReuseIndex":
        idx = cls()
        runs = Path(output_dir) / "runs"
        if not runs.is_dir():
            return idx
        for manifest_path in sorted(runs.glob("*/manifest.json"),
                                    key=lambda p: p.stat().st_mtime):
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            run_name = manifest_path.parent.name
            for seg in data.get("segments", []):
                h = seg.get("cumulative_hash")
                out = seg.get("output")
                if h and out and Path(out).is_file():
                    idx.segments.setdefault(h, {"output": out, "run": run_name})
        return idx

    def segment(self, cumulative: str | None) -> dict | None:
        return self.segments.get(cumulative) if cumulative else None
