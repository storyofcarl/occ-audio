"""Unit tests for the Seed Audio 1.0 backend request shape + client-side
validation of the API's hard limits.
Run: python tests/test_backend_seed_audio.py"""
from __future__ import annotations

import base64
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import occ_audio.backends.seed_audio as seed_audio          # noqa: E402
from occ_audio.backends.base import AudioReference, AudioSpec, BackendError  # noqa: E402

_passed = 0
_failed = 0

os.environ["SEED_AUDIO_API_KEY"] = "key-test"


def check(label: str, condition: bool) -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}")


class FakePostJson:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.payload: dict | None = None
        self.headers: dict | None = None

    def __call__(self, url, payload, headers, timeout=120):
        self.payload = payload
        self.headers = headers
        return self.response


def _fake_response(audio_bytes: bytes = b"fake-wav-bytes") -> dict:
    return {
        "code": 0, "message": "ok",
        "audio": base64.b64encode(audio_bytes).decode("ascii"),
        "duration": 12.3, "original_duration": 12.3,
        "url": "https://example/temp.wav",
    }


def test_synchronous_call_decodes_audio() -> None:
    print("seed_audio backend: one sync POST, decodes base64 audio inline")
    fake = FakePostJson(_fake_response(b"hello-audio"))
    seed_audio.post_json = fake

    result = seed_audio.SeedAudioBackend().generate(
        AudioSpec(text_prompt="Narrator says hello."), model="seed-audio-1.0")

    check("model sent in payload", fake.payload["model"] == "seed-audio-1.0")
    check("text_prompt sent verbatim", fake.payload["text_prompt"] == "Narrator says hello.")
    check("no references key when none given", "references" not in fake.payload)
    check("X-Api-Key header set", fake.headers["X-Api-Key"] == "key-test")
    check("audio decoded from base64", result.audio_bytes == b"hello-audio")
    check("duration parsed", result.duration == 12.3)
    check("temp_url carried through, not relied on for storage",
          result.temp_url == "https://example/temp.wav")


def test_local_audio_reference_becomes_audio_data() -> None:
    print("seed_audio backend: local audio_path -> base64 audio_data in references[]")
    with tempfile.TemporaryDirectory() as d:
        clip = Path(d) / "clip.wav"
        clip.write_bytes(b"RIFFfake-audio-bytes")
        fake = FakePostJson(_fake_response())
        seed_audio.post_json = fake

        seed_audio.SeedAudioBackend().generate(
            AudioSpec(text_prompt="Hero (@Audio1) says: hi.",
                     references=[AudioReference(audio_path=str(clip))]),
            model="seed-audio-1.0")

        refs = fake.payload["references"]
        check("exactly one reference sent", len(refs) == 1)
        check("audio_data is base64 of the file bytes",
              base64.b64decode(refs[0]["audio_data"]) == clip.read_bytes())


def test_rejects_more_than_three_audio_references() -> None:
    print("seed_audio backend: rejects >3 audio references before any HTTP call")
    seed_audio.post_json = FakePostJson(_fake_response())
    refs = [AudioReference(speaker=f"voice-{i}") for i in range(4)]
    try:
        seed_audio.SeedAudioBackend().generate(
            AudioSpec(text_prompt="x", references=refs), model="seed-audio-1.0")
        check("raises BackendError for 4 audio refs", False)
    except BackendError as exc:
        check("raises BackendError for 4 audio refs", "3" in str(exc))


def test_rejects_mixed_audio_and_image_references() -> None:
    print("seed_audio backend: rejects mixing audio and image references")
    seed_audio.post_json = FakePostJson(_fake_response())
    refs = [AudioReference(speaker="voice-1"), AudioReference(image_url="https://x/y.png")]
    try:
        seed_audio.SeedAudioBackend().generate(
            AudioSpec(text_prompt="x", references=refs), model="seed-audio-1.0")
        check("raises BackendError for mixed refs", False)
    except BackendError as exc:
        check("raises BackendError for mixed refs", "mix" in str(exc).lower())


def test_rejects_oversized_text_prompt() -> None:
    print("seed_audio backend: rejects a text_prompt over the 2048-char limit")
    seed_audio.post_json = FakePostJson(_fake_response())
    try:
        seed_audio.SeedAudioBackend().generate(
            AudioSpec(text_prompt="x" * 2049), model="seed-audio-1.0")
        check("raises BackendError for oversized prompt", False)
    except BackendError as exc:
        check("raises BackendError for oversized prompt", "2048" in str(exc))


def test_missing_api_key_raises() -> None:
    print("seed_audio backend: missing/placeholder API key raises on preflight")
    old = os.environ.pop("SEED_AUDIO_API_KEY", None)
    try:
        try:
            seed_audio.SeedAudioBackend().preflight()
            check("raises BackendError when key missing", False)
        except BackendError:
            check("raises BackendError when key missing", True)
    finally:
        if old is not None:
            os.environ["SEED_AUDIO_API_KEY"] = old


if __name__ == "__main__":
    test_synchronous_call_decodes_audio()
    test_local_audio_reference_becomes_audio_data()
    test_rejects_more_than_three_audio_references()
    test_rejects_mixed_audio_and_image_references()
    test_rejects_oversized_text_prompt()
    test_missing_api_key_raises()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
