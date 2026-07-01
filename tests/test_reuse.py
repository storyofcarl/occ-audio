"""Unit tests for cross-run reuse hashing.
Run: python tests/test_reuse.py"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from occ_audio.backends.base import AudioReference          # noqa: E402
from occ_audio.reuse import (                                # noqa: E402
    file_hash, references_hash, segment_input_hash,
)

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


def _base_kwargs(**overrides) -> dict:
    base = dict(text_prompt="hello", model="seed-audio-1.0", audio_format="wav",
                sample_rate=24000, speech_rate=0, loudness_rate=0, pitch_rate=0,
                reference_hash="")
    base.update(overrides)
    return base


def test_segment_hash_changes_with_prompt() -> None:
    print("segment_input_hash: a different prompt gives a different hash")
    h1 = segment_input_hash(**_base_kwargs(text_prompt="hello"))
    h2 = segment_input_hash(**_base_kwargs(text_prompt="goodbye"))
    check("different prompts hash differently", h1 != h2)
    check("same inputs hash identically",
          h1 == segment_input_hash(**_base_kwargs(text_prompt="hello")))


def test_segment_hash_changes_with_audio_config() -> None:
    print("segment_input_hash: a different audio_config gives a different hash")
    h1 = segment_input_hash(**_base_kwargs(speech_rate=0))
    h2 = segment_input_hash(**_base_kwargs(speech_rate=20))
    check("different speech_rate hashes differently", h1 != h2)


def test_references_hash_stable_and_order_sensitive() -> None:
    print("references_hash: content-stable, empty-list-safe, order-sensitive")
    with tempfile.TemporaryDirectory() as d:
        a = Path(d) / "a.wav"
        b = Path(d) / "b.wav"
        a.write_bytes(b"AAAA")
        b.write_bytes(b"BBBB")
        ref_a = AudioReference(audio_path=str(a))
        ref_b = AudioReference(audio_path=str(b))

        check("empty references hash to empty string", references_hash([]) == "")
        h_ab = references_hash([ref_a, ref_b])
        h_ba = references_hash([ref_b, ref_a])
        check("order matters (a,b) != (b,a)", h_ab != h_ba)
        check("same order re-hashes identically",
              h_ab == references_hash([ref_a, ref_b]))

        a.write_bytes(b"CHANGED")
        h_ab_changed = references_hash([ref_a, ref_b])
        check("changing a referenced file's content invalidates the hash",
              h_ab_changed != h_ab)


def test_speaker_reference_hashes_by_id_not_file() -> None:
    print("references_hash: a speaker-id reference hashes by its id string")
    h1 = references_hash([AudioReference(speaker="voice-hero")])
    h2 = references_hash([AudioReference(speaker="voice-hero")])
    h3 = references_hash([AudioReference(speaker="voice-villain")])
    check("same speaker id hashes identically", h1 == h2)
    check("different speaker id hashes differently", h1 != h3)


def test_file_hash_is_content_based() -> None:
    print("file_hash: identical bytes hash identically regardless of path")
    with tempfile.TemporaryDirectory() as d:
        p1 = Path(d) / "one.wav"
        p2 = Path(d) / "two.wav"
        p1.write_bytes(b"same-bytes")
        p2.write_bytes(b"same-bytes")
        check("identical content, different filenames, same hash",
              file_hash(p1) == file_hash(p2))


if __name__ == "__main__":
    test_segment_hash_changes_with_prompt()
    test_segment_hash_changes_with_audio_config()
    test_references_hash_stable_and_order_sensitive()
    test_speaker_reference_hashes_by_id_not_file()
    test_file_hash_is_content_based()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
