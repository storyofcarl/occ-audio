"""Unit tests for segmentation constraints (char budget / 3-voice cap).
Run: python tests/test_segmenter.py"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from occ_audio.script_source import Beat, SourceDocument  # noqa: E402
from occ_audio.segmenter import (  # noqa: E402
    MAX_LOCKED_VOICES, TEXT_BUDGET_CHARS, segment_source,
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


@dataclass
class _FakeDefaults:
    target_segment_seconds: int = 110
    continuation_seconds: int = 0


@dataclass
class _FakeProject:
    mode: str = "audiobook"
    defaults: _FakeDefaults = None
    sequence_starts: list = None

    def __post_init__(self):
        if self.defaults is None:
            self.defaults = _FakeDefaults()
        if self.sequence_starts is None:
            self.sequence_starts = []


def test_respects_char_budget() -> None:
    print("segment_source: no segment exceeds the text budget")
    # Many short sentences (not one giant run-on) so sentence-boundary
    # splitting has somewhere to cut.
    long_text = "This is a short sentence. " * 200
    doc = SourceDocument(path=Path("x"), format="prose", beats=[
        Beat(kind="narration", text=long_text.strip()),
    ])
    segments = segment_source(doc, _FakeProject())
    check("at least one segment produced", len(segments) >= 1)
    check("every segment's raw_chars stays under budget",
          all(s.raw_chars <= TEXT_BUDGET_CHARS for s in segments))


def test_caps_locked_characters_at_three() -> None:
    print("segment_source: locks at most 3 characters per segment, "
          "flags the rest as overflow")
    beats = []
    for name, n in (("Alice", 5), ("Bob", 4), ("Carl", 3), ("Dana", 2), ("Eve", 1)):
        for _ in range(n):
            beats.append(Beat(kind="dialogue", text=f"{name} speaks a line.", speaker=name))
    doc = SourceDocument(path=Path("x"), format="screenplay", beats=beats)
    segments = segment_source(doc, _FakeProject())
    check("exactly one segment (small input)", len(segments) == 1)
    seg = segments[0]
    check("locked characters capped at 3", len(seg.locked_characters) <= MAX_LOCKED_VOICES)
    check("locked = the 3 with the most lines",
          seg.locked_characters == ["Alice", "Bob", "Carl"])
    check("overflow = the rest", seg.overflow_characters == ["Dana", "Eve"])
    check("a warning is recorded for the overflow",
          any("voice_note-only" in w for w in seg.warnings))


def test_flags_unattributed_dialogue() -> None:
    print("segment_source: flags dialogue with no attributed speaker")
    doc = SourceDocument(path=Path("x"), format="prose", beats=[
        Beat(kind="dialogue", text="Who said that?", speaker=None),
    ])
    segments = segment_source(doc, _FakeProject())
    check("a warning mentions unattributed dialogue",
          any("unattributed" in w for w in segments[0].warnings))


def test_heading_forces_a_break() -> None:
    print("segment_source: a heading always closes the current segment")
    doc = SourceDocument(path=Path("x"), format="screenplay", beats=[
        Beat(kind="narration", text="Opening action."),
        Beat(kind="heading", text="INT. ROOM - DAY"),
        Beat(kind="narration", text="Next scene action."),
    ])
    segments = segment_source(doc, _FakeProject())
    check("two segments produced (split at the heading)", len(segments) == 2)


def test_narrator_competes_for_lock_slot_in_audiobook_mode() -> None:
    print("segment_source: Narrator counts as a speaker in audiobook mode")
    beats = [Beat(kind="narration", text="Some narration.")]
    doc = SourceDocument(path=Path("x"), format="prose", beats=beats)
    segments = segment_source(doc, _FakeProject(mode="audiobook"))
    check("Narrator appears in locked_characters",
          "Narrator" in segments[0].locked_characters)

    segments_radio = segment_source(doc, _FakeProject(mode="radioplay"))
    check("Narrator does NOT appear in radioplay mode",
          "Narrator" not in segments_radio[0].locked_characters)


def test_tail_characters_disabled_by_default() -> None:
    print("segment_source: tail_characters is empty when continuation_seconds is 0")
    beats = [Beat(kind="dialogue", text="A short final line.", speaker="Bob")]
    doc = SourceDocument(path=Path("x"), format="screenplay", beats=beats)
    segments = segment_source(doc, _FakeProject())
    check("tail_characters empty when continuation disabled",
          segments[0].tail_characters == [])


def test_tail_characters_only_covers_the_trailing_window() -> None:
    print("segment_source: tail_characters only covers who speaks in the "
          "trailing continuation_seconds window, not the whole segment")
    # Alice speaks a long opening line (~20s at 150wpm), then Bob speaks a
    # short final line. A short continuation window should cover only Bob.
    beats = [
        Beat(kind="dialogue", text=("word " * 50).strip(), speaker="Alice"),
        Beat(kind="dialogue", text="A short final line.", speaker="Bob"),
    ]
    doc = SourceDocument(path=Path("x"), format="screenplay", beats=beats)
    project = _FakeProject(defaults=_FakeDefaults(continuation_seconds=1))
    segments = segment_source(doc, project)
    check("only Bob is covered by the short trailing window",
          segments[0].tail_characters == ["Bob"])

    # A continuation window long enough to span the whole segment covers both.
    project_wide = _FakeProject(defaults=_FakeDefaults(continuation_seconds=60))
    segments_wide = segment_source(doc, project_wide)
    check("a wide window covers both speakers, most-recent first",
          segments_wide[0].tail_characters == ["Bob", "Alice"])


def test_sequence_id_defaults_to_one() -> None:
    print("segment_source: sequence_id is 1 for every segment when "
          "sequence_starts is empty (today's default behavior)")
    doc = SourceDocument(path=Path("x"), format="screenplay", beats=[
        Beat(kind="narration", text="Scene A action."),
        Beat(kind="heading", text="INT. ROOM - DAY"),
        Beat(kind="narration", text="Scene B action."),
        Beat(kind="heading", text="INT. HALL - DAY"),
        Beat(kind="narration", text="Scene C action."),
    ])
    segments = segment_source(doc, _FakeProject())
    check("all 3 segments are sequence_id 1",
          [s.sequence_id for s in segments] == [1, 1, 1])


def test_sequence_starts_advances_sequence_id() -> None:
    print("segment_source: sequence_starts advances sequence_id at the "
          "declared heading occurrence numbers")
    doc = SourceDocument(path=Path("x"), format="screenplay", beats=[
        Beat(kind="narration", text="Seq 1 scene 1."),
        Beat(kind="heading", text="INT. A - DAY"),   # heading #1
        Beat(kind="narration", text="Seq 1 scene 2."),
        Beat(kind="heading", text="INT. B - DAY"),   # heading #2 -> new sequence
        Beat(kind="narration", text="Seq 2 scene 1."),
        Beat(kind="heading", text="INT. C - DAY"),   # heading #3
        Beat(kind="narration", text="Seq 2 scene 2."),
        Beat(kind="heading", text="INT. D - DAY"),   # heading #4 -> new sequence
        Beat(kind="narration", text="Seq 3 scene 1."),
    ])
    project = _FakeProject(sequence_starts=[2, 4])
    segments = segment_source(doc, project)
    check("5 segments produced", len(segments) == 5)
    check("sequence ids advance at headings #2 and #4",
          [s.sequence_id for s in segments] == [1, 1, 2, 2, 3])


if __name__ == "__main__":
    test_respects_char_budget()
    test_caps_locked_characters_at_three()
    test_flags_unattributed_dialogue()
    test_heading_forces_a_break()
    test_narrator_competes_for_lock_slot_in_audiobook_mode()
    test_tail_characters_disabled_by_default()
    test_tail_characters_only_covers_the_trailing_window()
    test_sequence_id_defaults_to_one()
    test_sequence_starts_advances_sequence_id()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
