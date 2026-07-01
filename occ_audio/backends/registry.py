"""Model registry — friendly model name -> (backend, vendor model id).

Only one vendor/model exists today (BytePlus Seed Audio 1.0), but this keeps
the same shape occ uses so a second TTS vendor can be added later without
touching the pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass

from .base import AudioBackend, BackendError


@dataclass(frozen=True)
class ModelEntry:
    backend: str       # backend key
    vendor_id: str     # id the vendor API expects
    note: str = ""


MODEL_REGISTRY: dict[str, ModelEntry] = {
    "seed-audio-1.0": ModelEntry("seed_audio", "seed-audio-1.0"),
}

_ALIASES = {
    "seed-audio": "seed-audio-1.0",
    "seedaudio": "seed-audio-1.0",
}

_BACKEND_CACHE: dict[str, AudioBackend] = {}


def _canonical(name: str) -> str:
    key = name.strip().lower()
    return _ALIASES.get(key, key)


def _make_backend(backend_key: str) -> AudioBackend:
    if backend_key in _BACKEND_CACHE:
        return _BACKEND_CACHE[backend_key]
    if backend_key == "seed_audio":
        from .seed_audio import SeedAudioBackend
        backend: AudioBackend = SeedAudioBackend()
    else:
        raise BackendError(f"Unknown backend '{backend_key}'.")
    _BACKEND_CACHE[backend_key] = backend
    return backend


def get_audio_backend(name: str) -> tuple[AudioBackend, str]:
    """Return ``(backend, vendor_model_id)`` for a friendly model name."""
    canon = _canonical(name)
    entry = MODEL_REGISTRY.get(canon)
    if entry is None:
        known = ", ".join(sorted(MODEL_REGISTRY))
        raise BackendError(f"Unknown model '{name}'. Known models: {known}")
    return _make_backend(entry.backend), entry.vendor_id


def list_models() -> list[tuple[str, ModelEntry]]:
    return sorted(MODEL_REGISTRY.items())
