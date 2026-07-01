"""Unit tests for project.yaml parsing.
Run: python tests/test_config.py"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from occ_audio.config import load_project  # noqa: E402

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


def _write_project(tmp: Path, *, mode: str = "audiobook", cast_mode: str = "key") -> Path:
    (tmp / "script.txt").write_text(
        "Chapter One\n\nHero said, \"Hello there.\"\n", encoding="utf-8")
    (tmp / "project.yaml").write_text(
        "\n".join([
            "name: test_project",
            f"mode: {mode}",
            "source: script.txt",
            f"cast_mode: {cast_mode}",
            "cast:",
            "  Narrator:",
            "    voice_note: warm female, 40s",
            "  Hero:",
            "    voice_note: young male, energetic",
            "defaults:",
            "  target_segment_seconds: 90",
        ]) + "\n",
        encoding="utf-8")
    return tmp / "project.yaml"


def test_load_project_basic() -> None:
    print("load_project: parses mode/source/cast/defaults")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        project = load_project(str(_write_project(root)))
        check("mode parsed", project.mode == "audiobook")
        check("cast_mode parsed", project.cast_mode == "key")
        check("source resolved to existing file", project.source.is_file())
        check("cast has Narrator and Hero",
              set(project.cast) == {"Narrator", "Hero"})
        check("voice_note carried through",
              project.voice_note("Hero") == "young male, energetic")
        check("case-insensitive voice_note lookup",
              project.voice_note("hero") == "young male, energetic")
        check("no reference means not locked", not project.is_locked("Hero"))
        check("target_segment_seconds override applied",
              project.defaults.target_segment_seconds == 90)


def test_load_project_rejects_bad_mode() -> None:
    print("load_project: rejects an invalid mode")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        path = _write_project(root, mode="video")
        try:
            load_project(str(path))
            check("invalid mode raises ValueError", False)
        except ValueError:
            check("invalid mode raises ValueError", True)


def test_load_project_missing_source() -> None:
    print("load_project: missing source file raises FileNotFoundError")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "project.yaml").write_text(
            "name: t\nsource: nope.txt\n", encoding="utf-8")
        try:
            load_project(str(root / "project.yaml"))
            check("missing source raises", False)
        except FileNotFoundError:
            check("missing source raises", True)


def test_voice_reference_resolves_locked() -> None:
    print("load_project: a cast reference resolves and is_locked() is true")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "script.txt").write_text("Hero said, \"Hi.\"\n", encoding="utf-8")
        (root / "take.wav").write_bytes(b"\x00\x01")
        (root / "project.yaml").write_text(
            "\n".join([
                "name: t",
                "source: script.txt",
                "cast:",
                "  Hero:",
                "    voice_note: young male",
                "    reference: take.wav",
            ]) + "\n", encoding="utf-8")
        project = load_project(str(root / "project.yaml"))
        check("reference resolves to the file", project.voice_reference("Hero") is not None)
        check("is_locked is true", project.is_locked("Hero"))


if __name__ == "__main__":
    test_load_project_basic()
    test_load_project_rejects_bad_mode()
    test_load_project_missing_source()
    test_voice_reference_resolves_locked()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
