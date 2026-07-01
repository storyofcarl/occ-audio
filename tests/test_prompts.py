"""Unit tests for prompt assembly, including the continuation-reference
slot allocation.
Run: python tests/test_prompts.py"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from occ_audio.config import load_project        # noqa: E402
from occ_audio.prompts import build_prompt        # noqa: E402
from occ_audio.segmenter import DraftSegment      # noqa: E402
from occ_audio.script_source import Beat          # noqa: E402

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


def _project(tmp: Path, cast_yaml: list[str]) -> object:
    (tmp / "script.txt").write_text("Hero said, \"Hi.\"\n", encoding="utf-8")
    (tmp / "project.yaml").write_text(
        "\n".join(["name: t", "source: script.txt", "cast:"] + cast_yaml) + "\n",
        encoding="utf-8")
    return load_project(str(tmp / "project.yaml"))


def test_continuation_takes_first_slot_and_covers_only_named_characters() -> None:
    print("build_prompt: continuation reference takes @Audio1 and covers "
          "only the characters passed in continuation_characters")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "hero.wav").write_bytes(b"\x00\x01")
        (root / "villain.wav").write_bytes(b"\x00\x02")
        project = _project(root, [
            "  Hero:", "    voice_note: energetic", "    reference: hero.wav",
            "  Villain:", "    voice_note: cold and slow", "    reference: villain.wav",
        ])
        segment = DraftSegment(
            seg_id="S002",
            beats=[
                Beat(kind="dialogue", text="Still here.", speaker="Hero"),
                Beat(kind="dialogue", text="So am I.", speaker="Villain"),
            ],
            locked_characters=["Hero", "Villain"],
        )
        prompt, references, audio_tag, continuation_index = build_prompt(
            segment, project, continuation_characters=["Hero"])

        check("continuation_index is 0", continuation_index == 0)
        check("Hero is tagged @Audio1 (continuation, not the audition file)",
              audio_tag["Hero"] == "@Audio1")
        check("Villain still gets a real slot (their own audition)",
              audio_tag["Villain"] == "@Audio2")
        check("2 references total", len(references) == 2)
        check("Villain's reference is their own audition file, not the "
              "continuation placeholder",
              references[1].audio_path and references[1].audio_path.endswith("villain.wav"))
        check("prompt marks Hero as continuing",
              "continuing from the previous segment" in prompt)


def test_no_continuation_uses_normal_locked_slots() -> None:
    print("build_prompt: without continuation_characters, normal locked-slot "
          "allocation applies")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        project = _project(root, ["  Hero:", "    voice_note: energetic"])
        segment = DraftSegment(
            seg_id="S001",
            beats=[Beat(kind="dialogue", text="Hi.", speaker="Hero")],
            locked_characters=["Hero"],
        )
        prompt, references, audio_tag, continuation_index = build_prompt(segment, project)
        check("no continuation_index", continuation_index is None)
        check("Hero falls back to voice_note text (no reference configured, "
              "so no slot/tag is consumed at all)",
              "energetic" in prompt and "Hero" not in audio_tag and not references)


def test_continuation_slot_frees_room_for_a_third_character() -> None:
    print("build_prompt: a continuation covering 1 character leaves room "
          "for 2 more locked characters within the 3-slot cap")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "b.wav").write_bytes(b"\x00\x01")
        (root / "c.wav").write_bytes(b"\x00\x02")
        project = _project(root, [
            "  A:", "    voice_note: a",
            "  B:", "    voice_note: b", "    reference: b.wav",
            "  C:", "    voice_note: c", "    reference: c.wav",
        ])
        segment = DraftSegment(
            seg_id="S003", beats=[], locked_characters=["A", "B", "C"])
        prompt, references, audio_tag, continuation_index = build_prompt(
            segment, project, continuation_characters=["A"])
        check("3 references total (continuation + B + C)", len(references) == 3)
        check("A via continuation, B and C via their own auditions",
              audio_tag == {"A": "@Audio1", "B": "@Audio2", "C": "@Audio3"})


def test_full_descriptor_only_on_first_mention() -> None:
    print("build_prompt: a character's full voice description appears only "
          "on their first line in the segment; later lines are bare (or "
          "tag-only for a locked character) — repeating it every line blew "
          "past the 2048-char limit for no benefit")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "hero.wav").write_bytes(b"\x00\x01")
        project = _project(root, [
            "  Hero:", "    voice_note: a booming heroic voice", "    reference: hero.wav",
            "  Villain:", "    voice_note: a cold sneering voice",
        ])
        segment = DraftSegment(
            seg_id="S001",
            beats=[
                Beat(kind="dialogue", text="First line.", speaker="Hero"),
                Beat(kind="dialogue", text="Reply.", speaker="Villain"),
                Beat(kind="dialogue", text="Second line.", speaker="Hero"),
                Beat(kind="dialogue", text="Second reply.", speaker="Villain"),
            ],
            locked_characters=["Hero", "Villain"],
        )
        prompt, references, audio_tag, _ = build_prompt(segment, project)
        lines = prompt.splitlines()

        check("Hero's first line carries the full tag+voice_note",
              lines[0] == 'Hero (@Audio1, a booming heroic voice) says: "First line."')
        check("Villain's first line carries the full voice_note (unlocked)",
              lines[1] == 'Villain (a cold sneering voice) says: "Reply."')
        check("Hero's second line is tag-only, no repeated voice_note",
              lines[2] == 'Hero (@Audio1) says: "Second line."')
        check("Villain's second line is bare (no reference, no repeated note)",
              lines[3] == 'Villain says: "Second reply."')
        check("voice_note text appears exactly once per character in the prompt",
              prompt.count("a booming heroic voice") == 1
              and prompt.count("a cold sneering voice") == 1)


if __name__ == "__main__":
    test_continuation_takes_first_slot_and_covers_only_named_characters()
    test_no_continuation_uses_normal_locked_slots()
    test_continuation_slot_frees_room_for_a_third_character()
    test_full_descriptor_only_on_first_mention()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
