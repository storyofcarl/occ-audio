"""Project + environment configuration.

A *project* is a directory with a ``project.yaml``. The YAML declares the
mode (audiobook/radioplay), the source script, the cast's voice notes and
locked references, and generation defaults.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .common import load_env

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class CastEntry:
    voice_note: str = ""            # age/gender/accent/tone/personality brief;
                                     # ALWAYS carried as the fallback description,
                                     # even when a reference is locked
    reference: Path | None = None   # locked audition take (local file)
    speaker: str | None = None      # a Doubao TTS voice id / voice-clone id,
                                     # used instead of a local reference file


@dataclass
class Defaults:
    language: str = "en"
    audio_format: str = "wav"        # wav | mp3 | pcm | ogg_opus
    sample_rate: int = 24000
    speech_rate: int = 0
    loudness_rate: int = 0
    pitch_rate: int = 0
    target_segment_seconds: int = 110   # headroom under the 120s API cap
    concurrency: int = 8
    model: str = "seed-audio-1.0"
    continuation_seconds: int = 0       # 0 = off. >0 enables carrying a tail
                                        # clip of each segment's own generated
                                        # audio forward as a reference for the
                                        # next segment (a poor-man's chain —
                                        # the API has no native continuation
                                        # field, but references[] accepts any
                                        # audio, including our own prior
                                        # output). Recommended: 20-30s. Only
                                        # covers whichever character(s) are
                                        # actually speaking in that trailing
                                        # window — see segmenter.tail_characters.
    max_chain: int = 0                  # cap on consecutive continuation-
                                        # linked segments before re-anchoring
                                        # with a fresh (non-continuation)
                                        # segment; 0 = unlimited. Mirrors occ's
                                        # video max_chain — repeated reference-
                                        # of-a-reference can compound drift.


@dataclass
class ProjectConfig:
    name: str
    root: Path
    mode: str                         # audiobook | radioplay
    source: Path
    output_dir: Path
    cast_mode: str                    # key | all
    cast: dict[str, CastEntry]
    defaults: Defaults
    segments: dict[str, dict]         # per-segment overrides, keyed by segment id
    casting_dir: Path
    name_aliases: dict[str, str] = field(default_factory=dict)  # variant spelling -> canonical
    sequence_starts: list[int] = field(default_factory=list)  # 1-based heading
        # occurrence index where each new narrative sequence begins (e.g. the
        # 8-sequence structure); the continuation chain (METHODOLOGY.md §2a)
        # only links segments within the same sequence, so different
        # sequences become independent chains that generate in parallel.
        # Empty = the whole script is one sequence (today's default behavior).
    raw: dict = field(default_factory=dict)

    def segment_override(self, seg_id: str) -> dict:
        return self.segments.get(seg_id, {}) or {}

    def field_for(self, seg_id: str, name: str, default):
        """Per-segment override -> project default -> hardcoded default."""
        ov = self.segment_override(seg_id)
        if name in ov and ov[name] is not None:
            return ov[name]
        return getattr(self.defaults, name, default)

    def cast_entry(self, name: str) -> CastEntry | None:
        return _ci_lookup(self.cast, name)

    def voice_note(self, name: str) -> str:
        entry = self.cast_entry(name)
        return entry.voice_note if entry else ""

    def voice_reference(self, name: str) -> Path | None:
        """A character's locked reference audio file, if any (case-insensitive)."""
        entry = self.cast_entry(name)
        if entry and entry.reference and entry.reference.is_file():
            return entry.reference
        return None

    def voice_speaker(self, name: str) -> str | None:
        entry = self.cast_entry(name)
        return entry.speaker if entry else None

    def is_locked(self, name: str) -> bool:
        return bool(self.voice_reference(name) or self.voice_speaker(name))


def _ci_lookup(table: dict, key: str):
    if not key:
        return None
    want = key.strip().lower()
    for k, v in table.items():
        if k.strip().lower() == want:
            return v
    return None


def _resolve(root: Path, value: str | None) -> Path | None:
    if not value:
        return None
    p = Path(value)
    return p if p.is_absolute() else (root / p).resolve()


def _parse_cast(root: Path, raw: dict | None) -> dict[str, CastEntry]:
    """Parse the cast map. Each entry is one of::

        Name: { voice_note: ..., reference: path, speaker: doubao-voice-id }

    ``voice_note`` is always kept, even for a locked character — it is the
    fallback description used in any segment where the 3-reference cap
    leaves this character without a slot (METHODOLOGY.md §2).
    """
    out: dict[str, CastEntry] = {}
    for name, value in (raw or {}).items():
        name = str(name)
        value = value or {}
        if not isinstance(value, dict):
            raise ValueError(
                f"cast '{name}' must be a mapping with voice_note/reference/"
                f"speaker, got {value!r}.")
        ref_raw = value.get("reference")
        out[name] = CastEntry(
            voice_note=str(value.get("voice_note") or "").strip(),
            reference=_resolve(root, str(ref_raw)) if ref_raw else None,
            speaker=(str(value.get("speaker")).strip()
                     if value.get("speaker") else None),
        )
    return out


def find_project_file(project: str) -> Path:
    """Accept a project dir, a project.yaml path, or a name under projects/."""
    p = Path(project)
    candidates = [
        p, p / "project.yaml", p / "project.yml",
        REPO_ROOT / "projects" / project / "project.yaml",
        REPO_ROOT / "projects" / project / "project.yml",
    ]
    for c in candidates:
        if c.is_file():
            return c.resolve()
    raise FileNotFoundError(
        f"Could not locate a project.yaml for '{project}'. Looked in: "
        + ", ".join(str(c) for c in candidates)
    )


def load_project(project: str) -> ProjectConfig:
    """Load and validate a project, and load .env files into the environment."""
    project_file = find_project_file(project)
    root = project_file.parent
    data = yaml.safe_load(project_file.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{project_file} must contain a YAML mapping.")

    load_env([
        REPO_ROOT / ".env", REPO_ROOT / ".env.local",
        root / ".env", root / ".env.local", Path.cwd() / ".env",
    ])

    mode = str(data.get("mode") or "audiobook").lower()
    if mode not in ("audiobook", "radioplay"):
        raise ValueError(f"mode must be 'audiobook' or 'radioplay', got {mode!r}.")

    source = _resolve(root, data.get("source") or "script.txt")
    if source is None or not source.exists():
        raise FileNotFoundError(
            f"Source script not found for project '{data.get('name', project)}': {source}"
        )

    cast_mode = str(data.get("cast_mode") or "key").lower()
    if cast_mode not in ("key", "all"):
        raise ValueError(f"cast_mode must be 'key' or 'all', got {cast_mode!r}.")

    d = data.get("defaults") or {}
    defaults = Defaults(
        language=str(d.get("language", "en")),
        audio_format=str(d.get("audio_format", "wav")),
        sample_rate=int(d.get("sample_rate", 24000)),
        speech_rate=int(d.get("speech_rate", 0)),
        loudness_rate=int(d.get("loudness_rate", 0)),
        pitch_rate=int(d.get("pitch_rate", 0)),
        target_segment_seconds=max(1, int(d.get("target_segment_seconds", 110))),
        concurrency=max(1, int(d.get("concurrency", 8))),
        model=str(d.get("model", "seed-audio-1.0")),
        continuation_seconds=max(0, int(d.get("continuation_seconds", 0))),
        max_chain=max(0, int(d.get("max_chain", 0))),
    )

    output = data.get("output") or {}
    output_dir = _resolve(root, output.get("dir") or "outputs") or (root / "outputs")
    casting_dir = _resolve(root, data.get("casting_dir") or "casting") or (root / "casting")

    segments = data.get("segments") or {}
    if not isinstance(segments, dict):
        raise ValueError("'segments:' must be a mapping of segment id -> overrides.")

    cast = _parse_cast(root, data.get("cast"))

    aliases_raw = data.get("character_aliases") or {}
    if not isinstance(aliases_raw, dict):
        raise ValueError("'character_aliases:' must be a mapping of variant -> canonical name.")
    name_aliases = {str(k): str(v) for k, v in aliases_raw.items()}

    sequence_starts_raw = data.get("sequence_starts") or []
    if not isinstance(sequence_starts_raw, list):
        raise ValueError("'sequence_starts:' must be a list of heading occurrence numbers.")
    sequence_starts = [int(n) for n in sequence_starts_raw]

    return ProjectConfig(
        name=str(data.get("name") or root.name),
        root=root,
        mode=mode,
        source=source,
        output_dir=output_dir,
        cast_mode=cast_mode,
        cast=cast,
        defaults=defaults,
        segments={str(k): (v or {}) for k, v in segments.items()},
        casting_dir=casting_dir,
        name_aliases=name_aliases,
        sequence_starts=sequence_starts,
        raw=data,
    )
