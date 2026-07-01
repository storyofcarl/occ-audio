"""BytePlus Seed Audio 1.0 backend.

Synchronous REST call — see ``docs/seed-audio-1.0-http-api.md`` (the
authoritative technical spec) and ``docs/seed-audio-1.0-overview.md`` (the
prompting guide). No task/poll loop: the response carries the generated
audio inline as base64.
"""
from __future__ import annotations

import base64
import os
import time

from ..common import file_to_base64, get_env_int, post_json
from .base import AudioBackend, AudioReference, AudioResult, AudioSpec, BackendError, ProgressFn

DEFAULT_BASE_URL = "https://voice.ap-southeast-1.bytepluses.com/api/v3/tts/create"
MAX_AUDIO_REFERENCES = 3
MAX_IMAGE_REFERENCES = 1
MAX_TEXT_PROMPT_CHARS = 2048
MAX_OUTPUT_SECONDS = 120
PRICE_PER_MINUTE_USD = 0.15   # billed on original_duration, per docs/seed-audio-1.0-http-api.md


def _base_url() -> str:
    return os.getenv("SEED_AUDIO_BASE_URL", DEFAULT_BASE_URL)


def _looks_like_placeholder(value: str) -> bool:
    low = value.strip().lower()
    return (not low) or any(t in low for t in ("your_", "example", "replace", "changeme"))


def _api_key() -> str:
    key = os.getenv("SEED_AUDIO_API_KEY")
    if not key or _looks_like_placeholder(key):
        raise BackendError(
            "Missing/placeholder SEED_AUDIO_API_KEY. Set a real key in .env "
            "(see .env.example)."
        )
    return key


def _validate_references(references: list[AudioReference]) -> None:
    audio_refs = [r for r in references if r.is_audio()]
    image_refs = [r for r in references if r.is_image()]
    if audio_refs and image_refs:
        raise BackendError(
            "Cannot mix audio and image references in one Seed Audio request "
            f"({len(audio_refs)} audio + {len(image_refs)} image)."
        )
    if len(audio_refs) > MAX_AUDIO_REFERENCES:
        raise BackendError(
            f"Seed Audio allows at most {MAX_AUDIO_REFERENCES} audio "
            f"references per call; got {len(audio_refs)}. This is the "
            f"3-voice rule (METHODOLOGY.md §2) — split the segment or "
            f"fall back some characters to voice_note-only description."
        )
    if len(image_refs) > MAX_IMAGE_REFERENCES:
        raise BackendError(
            f"Seed Audio allows at most {MAX_IMAGE_REFERENCES} image "
            f"reference per call; got {len(image_refs)}."
        )
    for r in references:
        set_count = sum(bool(v) for v in (
            r.speaker, r.audio_path, r.audio_url, r.image_path, r.image_url))
        if set_count != 1:
            raise BackendError(
                f"Each AudioReference must set exactly one of speaker/"
                f"audio_path/audio_url/image_path/image_url; got {set_count}."
            )


def _reference_payload(ref: AudioReference) -> dict:
    if ref.speaker:
        return {"speaker": ref.speaker}
    if ref.audio_path:
        return {"audio_data": file_to_base64(ref.audio_path)}
    if ref.audio_url:
        return {"audio_url": ref.audio_url}
    if ref.image_path:
        return {"image_data": file_to_base64(ref.image_path)}
    if ref.image_url:
        return {"image_url": ref.image_url}
    raise BackendError("AudioReference has no reference set.")


class SeedAudioBackend(AudioBackend):
    name = "seed_audio"

    def preflight(self) -> None:
        _api_key()

    def generate(self, spec: AudioSpec, *, model: str,
                 on_progress: ProgressFn | None = None) -> AudioResult:
        if len(spec.text_prompt) > MAX_TEXT_PROMPT_CHARS:
            raise BackendError(
                f"text_prompt is {len(spec.text_prompt)} chars, over the "
                f"{MAX_TEXT_PROMPT_CHARS}-char limit. Split the segment.")
        _validate_references(spec.references)

        api_key = _api_key()
        payload: dict = {
            "model": model,
            "text_prompt": spec.text_prompt,
            "audio_config": {
                "format": spec.audio_format,
                "sample_rate": spec.sample_rate,
                "speech_rate": spec.speech_rate,
                "loudness_rate": spec.loudness_rate,
                "pitch_rate": spec.pitch_rate,
            },
            "watermark": spec.watermark or {},
        }
        if spec.references:
            payload["references"] = [_reference_payload(r) for r in spec.references]

        headers = {
            "Content-Type": "application/json",
            "X-Api-Key": api_key,
            "X-Api-Request-Id": f"occ-audio-{int(time.time() * 1000):x}",
        }

        if on_progress:
            on_progress(f"requesting audio ({model}, {len(spec.references)} ref(s))")
        timeout = get_env_int("SEED_AUDIO_TIMEOUT", 180)
        result = post_json(_base_url(), payload, headers, timeout=timeout)

        code = result.get("code")
        if code not in (None, 0):
            raise BackendError(
                f"Seed Audio returned code={code}: {result.get('message')}")
        audio_b64 = result.get("audio")
        if not audio_b64:
            raise BackendError(
                f"Seed Audio response has no 'audio' field: "
                f"{str(result)[:400]}")

        return AudioResult(
            audio_bytes=base64.b64decode(audio_b64),
            duration=float(result.get("duration") or 0.0),
            original_duration=float(result.get("original_duration") or 0.0),
            temp_url=result.get("url"),
            raw={k: v for k, v in result.items() if k != "audio"},
        )
