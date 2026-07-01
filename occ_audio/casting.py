"""Voice casting: character extraction + audition-take generation.

Casting is a distinct, gated phase (METHODOLOGY.md §3): for each character
(all, or only "key" characters, per ``cast_mode``), generate 4 short T2A
(no-reference) audition takes off the character's ``voice_note`` brief, each
varying one axis (pace/energy). The user listens and records their choice by
hand in ``project.yaml`` — this module never auto-selects.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .backends.base import AudioSpec, ProgressFn
from .backends.registry import get_audio_backend
from .config import ProjectConfig
from .script_source import SourceDocument, speaking_characters

AUDITION_LINE = (
    "Hello. It's good to finally meet you. "
    "I've been looking forward to this for a long time."
)

# (variant label, extra descriptor appended to the voice_note)
AUDITION_VARIANTS: list[tuple[str, str]] = [
    ("baseline", ""),
    ("faster pace", "speaking quickly and energetically"),
    ("slower pace", "speaking slowly and deliberately"),
    ("heightened emotion", "with more emotional intensity and feeling"),
]

MIN_LINES_FOR_KEY_CAST = 2


@dataclass
class AuditionTake:
    variant: str
    prompt: str
    path: Path


def extract_characters(doc: SourceDocument, cast_mode: str,
                       min_lines: int = MIN_LINES_FOR_KEY_CAST) -> list[str]:
    """Speaking character names, ranked by line count.

    ``all`` mode returns every named speaker. ``key`` mode returns speakers
    at/above ``min_lines``, falling back to the single most-frequent speaker
    if none clear the threshold (so a short/sparse script still gets a cast).
    """
    counts = speaking_characters(doc)
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    if cast_mode == "all":
        return [name for name, _ in ranked]
    keyed = [name for name, n in ranked if n >= min_lines]
    if keyed:
        return keyed
    return [ranked[0][0]] if ranked else []


def audition_prompt(voice_note: str, variant_desc: str, line: str = AUDITION_LINE) -> str:
    voice = voice_note.strip()
    if variant_desc:
        voice = f"{voice}, {variant_desc}" if voice else variant_desc
    voice = voice or "a neutral adult voice"
    return f'A character ({voice}) says: "{line}"'


def _extension_for(audio_format: str) -> str:
    return {"wav": "wav", "mp3": "mp3", "pcm": "pcm", "ogg_opus": "ogg"}.get(
        audio_format, "wav")


def generate_auditions(project: ProjectConfig, character: str, *,
                       n: int = 4, on_progress: ProgressFn | None = None,
                       ) -> list[AuditionTake]:
    """Generate ``n`` audition takes for ``character`` and write them to
    ``<casting_dir>/<character>/take_{1..n}.<ext>`` plus a manifest.json
    recording the exact prompt used per take. Paid — one call per take."""
    voice_note = project.voice_note(character)
    backend, vendor_id = get_audio_backend(project.defaults.model)
    backend.preflight()

    out_dir = project.casting_dir / character
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = _extension_for(project.defaults.audio_format)

    variants = AUDITION_VARIANTS[:n]
    takes: list[AuditionTake] = []
    for i, (label, desc) in enumerate(variants, start=1):
        prompt = audition_prompt(voice_note, desc)
        spec = AudioSpec(
            text_prompt=prompt,
            audio_format=project.defaults.audio_format,
            sample_rate=project.defaults.sample_rate,
        )
        if on_progress:
            on_progress(f"{character} take {i}/{len(variants)} ({label})")
        result = backend.generate(spec, model=vendor_id, on_progress=on_progress)
        take_path = out_dir / f"take_{i}.{ext}"
        take_path.write_bytes(result.audio_bytes)
        takes.append(AuditionTake(variant=label, prompt=prompt, path=take_path))

    manifest = {
        "character": character,
        "voice_note": voice_note,
        "takes": [
            {"variant": t.variant, "prompt": t.prompt, "file": t.path.name}
            for t in takes
        ],
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return takes


def load_casting_manifest(project: ProjectConfig, character: str) -> dict | None:
    path = project.casting_dir / character / "manifest.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
