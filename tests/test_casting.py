"""Unit tests for character extraction + audition-take generation.
Run: python tests/test_casting.py"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import occ_audio.casting as casting               # noqa: E402
from occ_audio.backends.base import AudioResult    # noqa: E402
from occ_audio.config import load_project          # noqa: E402
from occ_audio.script_source import Beat, SourceDocument  # noqa: E402

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


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def preflight(self) -> None:
        pass

    def generate(self, spec, *, model, on_progress=None):
        self.calls.append(spec.text_prompt)
        return AudioResult(audio_bytes=b"\x00\x01fake", duration=15.0,
                           original_duration=15.0)


def test_extract_characters_key_vs_all() -> None:
    print("extract_characters: 'key' keeps frequent speakers; 'all' keeps everyone")
    beats = []
    for name, n in (("Alice", 5), ("Bob", 4), ("Eve", 1)):
        for _ in range(n):
            beats.append(Beat(kind="dialogue", text="line", speaker=name))
    doc = SourceDocument(path=Path("x"), format="screenplay", beats=beats)
    key_cast = casting.extract_characters(doc, "key", min_lines=2)
    check("key mode drops the one-line character", "Eve" not in key_cast)
    check("key mode keeps the frequent speakers", set(key_cast) == {"Alice", "Bob"})
    all_cast = casting.extract_characters(doc, "all")
    check("all mode keeps everyone", set(all_cast) == {"Alice", "Bob", "Eve"})


def test_generate_auditions_writes_takes_and_manifest() -> None:
    print("generate_auditions: writes N takes + a manifest.json, no auto-selection")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "script.txt").write_text("Hero said, \"Hi.\"\n", encoding="utf-8")
        (root / "project.yaml").write_text(
            "\n".join([
                "name: t", "source: script.txt",
                "cast:", "  Hero:", "    voice_note: young male, energetic",
            ]) + "\n", encoding="utf-8")
        project = load_project(str(root / "project.yaml"))

        fake = FakeBackend()
        casting.get_audio_backend = lambda name: (fake, "seed-audio-1.0")

        takes = casting.generate_auditions(project, "Hero", n=4)
        check("4 takes returned", len(takes) == 4)
        check("4 files written", all(t.path.is_file() for t in takes))
        check("4 backend calls made", len(fake.calls) == 4)
        check("voice_note appears in every prompt",
              all("young male, energetic" in c for c in fake.calls))
        check("variants differ across takes",
              len({t.variant for t in takes}) == 4)

        manifest = casting.load_casting_manifest(project, "Hero")
        check("manifest recorded", manifest is not None)
        check("manifest carries voice_note", manifest["voice_note"] == "young male, energetic")
        check("manifest has 4 take entries", len(manifest["takes"]) == 4)
        check("no reference auto-selected in project.yaml",
              project.voice_reference("Hero") is None)


if __name__ == "__main__":
    test_extract_characters_key_vs_all()
    test_generate_auditions_writes_takes_and_manifest()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
