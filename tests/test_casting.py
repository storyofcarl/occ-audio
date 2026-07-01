"""Unit tests for character extraction + audition-take generation.
Run: python tests/test_casting.py"""
from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import occ_audio.casting as casting               # noqa: E402
from occ_audio.backends.base import AudioResult    # noqa: E402
from occ_audio.config import load_project          # noqa: E402
from occ_audio.script_source import Beat, SourceDocument  # noqa: E402


@dataclass
class _FakeProject:
    cast_mode: str = "key"
    cast: dict = field(default_factory=dict)

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
    print("extract_characters: 'key' casts ONLY characters declared in "
          "project.cast (never auto-discovers minor characters by line "
          "count); 'all' casts every speaker found in the script")
    beats = []
    # Alice/Bob/Eve all speak plenty of lines (Eve included), but only
    # Alice and Bob are declared in the project's cast: block.
    for name, n in (("Alice", 5), ("Bob", 4), ("Eve", 10)):
        for _ in range(n):
            beats.append(Beat(kind="dialogue", text="line", speaker=name))
    doc = SourceDocument(path=Path("x"), format="screenplay", beats=beats)

    key_project = _FakeProject(cast_mode="key", cast={"Alice": None, "Bob": None})
    key_cast = casting.extract_characters(doc, key_project)
    check("key mode drops Eve even though she has the most lines",
          "Eve" not in key_cast)
    check("key mode keeps exactly the declared characters",
          set(key_cast) == {"Alice", "Bob"})

    all_project = _FakeProject(cast_mode="all", cast={"Alice": None, "Bob": None})
    all_cast = casting.extract_characters(doc, all_project)
    check("all mode keeps everyone regardless of what's declared",
          set(all_cast) == {"Alice", "Bob", "Eve"})

    key_project_undeclared = _FakeProject(cast_mode="key", cast={"Someone Not In Script": None})
    check("key mode drops a declared name that never actually speaks",
          casting.extract_characters(doc, key_project_undeclared) == [])


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
        check("every take used the identical prompt (no manufactured "
              "pace/emotion variants) — stateless randomness alone gives "
              "the variation",
              len(set(fake.calls)) == 1)


def test_generate_auditions_rejects_blank_voice_note() -> None:
    print("generate_auditions: refuses to cast a character with no voice_note "
          "(an unconstrained voice isn't a real audition)")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "script.txt").write_text("Extra said, \"Hi.\"\n", encoding="utf-8")
        (root / "project.yaml").write_text(
            "\n".join(["name: t", "source: script.txt", "cast:", "  Extra:", "    voice_note: \"\""]) + "\n",
            encoding="utf-8")
        project = load_project(str(root / "project.yaml"))
        fake = FakeBackend()
        casting.get_audio_backend = lambda name: (fake, "seed-audio-1.0")
        try:
            casting.generate_auditions(project, "Extra", n=4)
            check("raises for a blank voice_note", False)
        except casting.BackendError:
            check("raises for a blank voice_note", True)
        check("no backend calls made", len(fake.calls) == 0)


class _FlakyOnceBackend:
    """Fails the very first call, succeeds on every call after — simulates
    a one-off transient API error (like the real 'text unreadable' HTTP 500
    seen mid-run, where the identical prompt succeeded on retry)."""

    def __init__(self) -> None:
        self.attempts = 0

    def preflight(self) -> None:
        pass

    def generate(self, spec, *, model, on_progress=None):
        self.attempts += 1
        if self.attempts == 1:
            raise RuntimeError("HTTP 500: TTSInvalidText: text unreadable")
        return AudioResult(audio_bytes=b"\x00\x01fake", duration=15.0, original_duration=15.0)


def test_generate_auditions_retries_once_on_transient_failure() -> None:
    print("generate_auditions: retries a failed take once before giving up "
          "(a transient API hiccup shouldn't kill the whole cast run)")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "script.txt").write_text("Hero said, \"Hi.\"\n", encoding="utf-8")
        (root / "project.yaml").write_text(
            "\n".join(["name: t", "source: script.txt", "cast:", "  Hero:",
                      "    voice_note: a booming voice"]) + "\n", encoding="utf-8")
        project = load_project(str(root / "project.yaml"))
        flaky = _FlakyOnceBackend()
        casting.get_audio_backend = lambda name: (flaky, "seed-audio-1.0")

        takes = casting.generate_auditions(project, "Hero", n=1)
        check("take succeeds after one retry", len(takes) == 1)
        check("exactly 2 attempts made (fail then succeed)", flaky.attempts == 2)


if __name__ == "__main__":
    test_extract_characters_key_vs_all()
    test_generate_auditions_writes_takes_and_manifest()
    test_generate_auditions_rejects_blank_voice_note()
    test_generate_auditions_retries_once_on_transient_failure()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
