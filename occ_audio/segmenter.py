"""Chunk a parsed source document into Seed-Audio-sized segments.

Segmentation is deterministic given the source text and project config, so
``scan``/``preview`` derive it live each run rather than round-tripping
through a separate persisted file — the "draft for review" is the printed
plan/prompt output itself (METHODOLOGY.md §4).

Three constraints per segment, enforced here:
1. raw text stays under ``TEXT_BUDGET_CHARS`` (headroom under the API's
   2048-char ``text_prompt`` limit — the exact assembled prompt is checked
   again in ``prompts``/``pipeline`` since formatting adds a little more).
2. estimated spoken duration stays near ``target_segment_seconds``.
3. at most 3 characters are *locked* (given a real @AudioN reference) per
   segment — the 3-voice rule (METHODOLOGY.md §2). This does NOT force an
   extra split: a segment with more than 3 speakers keeps them all, but
   only the 3 with the most lines in that segment are locked; the rest are
   flagged as ``overflow_characters`` and fall back to voice_note-only
   description.

Scene/chapter headings are preferred break points — a heading always closes
the segment in progress before starting a new one.

When ``project.defaults.continuation_seconds`` is set (>0), each segment
also records ``tail_characters`` — whichever speaker(s) are actually voiced
within that trailing window of estimated speech. A tail clip of this
segment's own generated audio can only carry a character's voice forward if
that character actually spoke in the clip; ``tail_characters`` is how
``prompts.build_prompt`` knows who the next segment's continuation reference
covers. See METHODOLOGY.md §2a.

Each segment also gets a ``sequence_id`` — which narrative sequence (per
``project.sequence_starts``, a list of 1-based heading occurrence numbers)
it belongs to. The pipeline only chains continuation across segments in the
SAME sequence, so different sequences become independent generation chains
that run in parallel — see METHODOLOGY.md §2b.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import ProjectConfig
from .script_source import Beat, SourceDocument

TEXT_BUDGET_CHARS = 1800     # headroom under the 2048-char API limit
WORDS_PER_MINUTE = 150       # rough narration/dialogue pace estimate
MAX_LOCKED_VOICES = 3
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class DraftSegment:
    seg_id: str
    beats: list[Beat] = field(default_factory=list)
    locked_characters: list[str] = field(default_factory=list)
    overflow_characters: list[str] = field(default_factory=list)
    estimated_seconds: float = 0.0
    raw_chars: int = 0
    warnings: list[str] = field(default_factory=list)
    tail_characters: list[str] = field(default_factory=list)   # speaker(s)
        # actually voiced within the trailing continuation_seconds window of
        # this segment — the only character(s) a tail-clip reference of THIS
        # segment's audio can carry forward to the next one. Empty when
        # continuation is disabled (continuation_seconds == 0).
    sequence_id: int = 1   # which narrative sequence this segment belongs to;
        # the chain only links segments with the same sequence_id.


def _estimate_seconds(text: str, wpm: int = WORDS_PER_MINUTE) -> float:
    words = len(text.split())
    return words / wpm * 60.0 if words else 0.0


def _beat_seconds(beat: Beat) -> float:
    return _estimate_seconds(beat.text)


def _speaker_of(beat: Beat, mode: str) -> str | None:
    if beat.kind == "dialogue":
        return beat.speaker
    if beat.kind == "narration" and mode == "audiobook":
        return "Narrator"
    return None


def _tail_characters(beats: list[Beat], mode: str, continuation_seconds: int) -> list[str]:
    """Speaker(s) covering the trailing ``continuation_seconds`` of estimated
    speech, walking backward from the end of the segment. Empty when
    continuation is disabled or the segment has no attributable speech in
    that window."""
    if continuation_seconds <= 0:
        return []
    covered: list[str] = []
    seen: set[str] = set()
    remaining = float(continuation_seconds)
    for beat in reversed(beats):
        if remaining <= 0:
            break
        speaker = _speaker_of(beat, mode)
        if speaker and speaker not in seen:
            seen.add(speaker)
            covered.append(speaker)
        remaining -= _beat_seconds(beat)
    return covered


def _beat_len(beat: Beat) -> int:
    overhead = len(beat.speaker) + 4 if beat.speaker else 0
    return len(beat.text) + overhead


def _split_oversized_beat(beat: Beat, budget: int) -> list[Beat]:
    """A single beat longer than the whole budget (a long paragraph or a
    long speech) is split on sentence boundaries into same-kind/same-speaker
    sub-beats, each under budget."""
    if _beat_len(beat) <= budget:
        return [beat]
    sentences = _SENTENCE_RE.split(beat.text.strip()) or [beat.text]
    out: list[Beat] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if current and len(candidate) > budget:
            out.append(Beat(kind=beat.kind, text=current, speaker=beat.speaker))
            current = sentence
        else:
            current = candidate
    if current:
        out.append(Beat(kind=beat.kind, text=current, speaker=beat.speaker))
    return out


def segment_source(doc: SourceDocument, project: ProjectConfig) -> list[DraftSegment]:
    target_seconds = project.defaults.target_segment_seconds
    continuation_seconds = project.defaults.continuation_seconds
    sequence_starts = set(project.sequence_starts)
    segments: list[DraftSegment] = []
    current: list[Beat] = []
    current_chars = 0
    seg_num = 0
    heading_count = 0
    sequence_id = 1

    def flush() -> None:
        nonlocal current, current_chars, seg_num
        if not current:
            return
        seg_num += 1
        text_all = " ".join(b.text for b in current)
        counts: dict[str, int] = {}
        for b in current:
            if b.kind == "dialogue" and b.speaker:
                counts[b.speaker] = counts.get(b.speaker, 0) + 1
            elif b.kind == "narration" and project.mode == "audiobook":
                # The narrator reads every narration beat in audiobook mode,
                # so it competes for a lock slot like any speaking character —
                # and usually wins one, which is the point: narrator voice
                # consistency matters more than a minor one-line character.
                counts["Narrator"] = counts.get("Narrator", 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: -kv[1])
        locked = [n for n, _ in ranked[:MAX_LOCKED_VOICES]]
        overflow = [n for n, _ in ranked[MAX_LOCKED_VOICES:]]

        warnings: list[str] = []
        if overflow:
            warnings.append(
                f"{len(overflow)} character(s) beyond the {MAX_LOCKED_VOICES}-voice "
                f"cap fall back to voice_note-only description: {', '.join(overflow)}")
        unattributed = sum(1 for b in current if b.kind == "dialogue" and not b.speaker)
        if unattributed:
            warnings.append(
                f"{unattributed} unattributed dialogue line(s) — assign a "
                f"speaker before casting.")

        segments.append(DraftSegment(
            seg_id=f"S{seg_num:03d}", beats=list(current),
            locked_characters=locked, overflow_characters=overflow,
            estimated_seconds=_estimate_seconds(text_all),
            raw_chars=current_chars, warnings=warnings,
            tail_characters=_tail_characters(current, project.mode, continuation_seconds),
            sequence_id=sequence_id,
        ))
        current = []
        current_chars = 0

    for raw_beat in doc.beats:
        if raw_beat.kind == "heading":
            flush()   # a heading always closes the segment in progress
            heading_count += 1
            if heading_count in sequence_starts:
                sequence_id += 1
            continue
        for beat in _split_oversized_beat(raw_beat, TEXT_BUDGET_CHARS):
            blen = _beat_len(beat)
            projected_seconds = _estimate_seconds(
                " ".join(b.text for b in current) + " " + beat.text)
            if current and (current_chars + blen > TEXT_BUDGET_CHARS
                            or projected_seconds > target_seconds):
                flush()
            current.append(beat)
            current_chars += blen
    flush()
    return segments
