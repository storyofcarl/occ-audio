"""Backend abstraction: specs, results, and the ABC the Seed Audio backend
implements.

Unlike occ's video backends (async task + poll), Seed Audio 1.0 is a
**synchronous** call — one POST, one response carrying the audio inline. See
``docs/seed-audio-1.0-http-api.md``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable


class BackendError(RuntimeError):
    """Raised for any vendor/API failure, or a client-side spec violation
    caught before spending a call (e.g. too many references)."""


@dataclass
class AudioReference:
    """One entry in Seed Audio's ``references[]``.

    Exactly one of ``speaker`` / ``audio_path`` / ``audio_url`` /
    ``image_path`` / ``image_url`` should be set per the API's mutual-
    exclusion rules (see ``AudioSpec`` docstring for the combined
    constraints across the whole references list).
    """
    speaker: str | None = None          # a Doubao TTS voice id or voice-clone id
    audio_path: str | None = None       # local file -> base64 audio_data
    audio_url: str | None = None        # remote URL, passed through
    image_path: str | None = None       # local file -> base64 image_data
    image_url: str | None = None        # remote URL, passed through

    def is_audio(self) -> bool:
        return bool(self.speaker or self.audio_path or self.audio_url)

    def is_image(self) -> bool:
        return bool(self.image_path or self.image_url)


@dataclass
class AudioSpec:
    """A single Seed Audio 1.0 generation request, vendor-neutral.

    ``references`` is either:
    * up to 3 audio references (``speaker`` | ``audio_path`` | ``audio_url``
      per entry), addressed in the prompt as @Audio1/@Audio2/@Audio3 in the
      same order, OR
    * exactly 1 image reference (never mixed with audio references).

    Client-side validation of these limits happens in the backend, before
    any HTTP call — see METHODOLOGY.md §2 ("the 3-voice rule").
    """
    text_prompt: str
    references: list[AudioReference] = field(default_factory=list)
    audio_format: str = "wav"           # wav | mp3 | pcm | ogg_opus
    sample_rate: int = 24000            # 8000..48000, see the API doc
    speech_rate: int = 0                # -50..100 (100 = 2.0x speed)
    loudness_rate: int = 0              # -50..100 (100 = 2.0x volume)
    pitch_rate: int = 0                 # -12..12
    watermark: dict = field(default_factory=dict)


@dataclass
class AudioResult:
    audio_bytes: bytes
    duration: float
    original_duration: float
    temp_url: str | None = None         # valid ~2h; do not rely on this for storage
    raw: dict = field(default_factory=dict)


ProgressFn = Callable[[str], None]


class AudioBackend(ABC):
    """Generates audio. One instance per vendor."""

    name: str = "audio-backend"

    @abstractmethod
    def generate(self, spec: AudioSpec, *, model: str,
                 on_progress: ProgressFn | None = None) -> AudioResult:
        """Run one audio-generation call to completion (synchronous)."""

    def preflight(self) -> None:
        """Raise BackendError if the backend cannot be used (missing key, etc.)."""
