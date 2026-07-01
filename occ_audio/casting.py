"""Voice casting: character extraction + audition-take generation.

Casting is a distinct, gated phase (METHODOLOGY.md §3): for each character
(all, or only "key" characters, per ``cast_mode``), generate N short T2A
(no-reference) audition takes off the character's ``voice_note`` brief. The
user listens and records their choice by hand in ``project.yaml`` — this
module never auto-selects.

Every take uses the exact same prompt — no manufactured pace/emotion
variants. Seed Audio is stateless and non-deterministic per call, so
re-running the identical prompt N times already produces N distinct voice
realizations; a "faster pace"/"heightened emotion" descriptor only steers
*performance* on that one line, not the underlying voice, so it added
nothing but noise to what's being auditioned.

A character with no ``voice_note`` has nothing to describe the voice with,
so Seed Audio would pick an unconstrained, uncontrolled voice — casting
that is a wasted call, not a real audition. ``generate_auditions`` refuses
to run for a character with a blank ``voice_note``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .backends.base import AudioSpec, BackendError, ProgressFn
from .backends.registry import get_audio_backend
from .backends.seed_audio import PRICE_PER_MINUTE_USD
from .config import ProjectConfig
from .script_source import SourceDocument, speaking_characters

AUDITION_LINE = (
    "Hello. It's good to finally meet you. "
    "I've been looking forward to this for a long time."
)
ESTIMATED_AUDITION_SECONDS = 15   # each take is a short T2A clip by design


def estimate_audition_cost(n_characters: int, n_takes: int) -> tuple[int, float, float]:
    """(clips, estimated seconds, estimated USD) for casting ``n_characters``
    with ``n_takes`` audition takes each. Estimate only — actual billing is
    per-clip real duration."""
    clips = n_characters * n_takes
    seconds = clips * ESTIMATED_AUDITION_SECONDS
    cost = (seconds / 60.0) * PRICE_PER_MINUTE_USD
    return clips, seconds, cost

@dataclass
class AuditionTake:
    variant: str
    prompt: str
    path: Path


def extract_characters(doc: SourceDocument, project: ProjectConfig) -> list[str]:
    """Speaking character names to cast, per ``project.cast_mode``.

    ``all`` returns every named speaker found in the script, ranked by line
    count — can be a large (expensive) list for a script with many minor
    one-scene characters.

    ``key`` returns ONLY the characters explicitly declared in
    ``project.yaml``'s ``cast:`` block that actually speak in the script. It
    does NOT auto-discover minor characters by a line-count threshold —
    that auto-discovery previously cast every 2+-line character in the
    script regardless of what was declared, which silently balloons cost
    on an ensemble script. Declare a character in ``cast:`` to cast them.
    """
    counts = speaking_characters(doc)
    if project.cast_mode == "all":
        return [name for name, _ in sorted(counts.items(), key=lambda kv: -kv[1])]
    speaking_lower = {name.strip().lower() for name in counts}
    return [name for name in project.cast if name.strip().lower() in speaking_lower]


def audition_prompt(voice_note: str, line: str = AUDITION_LINE) -> str:
    return f'A character ({voice_note.strip()}) says: "{line}"'


def _extension_for(audio_format: str) -> str:
    return {"wav": "wav", "mp3": "mp3", "pcm": "pcm", "ogg_opus": "ogg"}.get(
        audio_format, "wav")


def generate_auditions(project: ProjectConfig, character: str, *,
                       n: int = 4, on_progress: ProgressFn | None = None,
                       ) -> list[AuditionTake]:
    """Generate ``n`` audition takes for ``character`` — the same prompt
    every time, relying on Seed Audio's stateless per-call randomness for
    variation — and write them to
    ``<casting_dir>/<character>/take_{1..n}.<ext>`` plus a manifest.json
    recording the prompt used. Paid — one call per take.

    Raises if the character has no ``voice_note``: with nothing describing
    the voice, Seed Audio picks an unconstrained voice and the take isn't a
    real audition of anything.
    """
    voice_note = project.voice_note(character)
    if not voice_note.strip():
        raise BackendError(
            f"{character} has no voice_note in project.yaml — add one "
            f"before casting (age/gender/accent/tone/personality). "
            f"Without it Seed Audio picks an unconstrained voice, which "
            f"isn't a real audition.")
    backend, vendor_id = get_audio_backend(project.defaults.model)
    backend.preflight()

    out_dir = project.casting_dir / character
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = _extension_for(project.defaults.audio_format)

    prompt = audition_prompt(voice_note)
    takes: list[AuditionTake] = []
    for i in range(1, n + 1):
        spec = AudioSpec(
            text_prompt=prompt,
            audio_format=project.defaults.audio_format,
            sample_rate=project.defaults.sample_rate,
        )
        if on_progress:
            on_progress(f"{character} take {i}/{n}")
        result = None
        for attempt in (1, 2):
            try:
                result = backend.generate(spec, model=vendor_id, on_progress=on_progress)
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 1:
                    if on_progress:
                        on_progress(f"{character} take {i}/{n} attempt 1 failed "
                                    f"({exc}) - retrying")
                    continue
                raise BackendError(
                    f"{character} take {i}/{n} failed twice: {exc}") from exc
        take_path = out_dir / f"take_{i}.{ext}"
        take_path.write_bytes(result.audio_bytes)
        takes.append(AuditionTake(variant=f"take {i}", prompt=prompt, path=take_path))

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
