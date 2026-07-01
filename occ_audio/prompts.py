"""Assemble a Seed Audio ``text_prompt`` + its ordered ``references[]`` for
one segment.

Reference order must exactly match @AudioN order in the assembled text —
this is the one binding contract with the API (METHODOLOGY.md §6). In
**audiobook** mode narration is read verbatim by "Narrator" (a pseudo cast
entry, configured like any character); in **radioplay** mode narration
becomes ambience/SFX/music direction and only cast dialogue is performed.

**A character's full voice description appears only on their first mention
in a segment; every later line is just their bare name (or name + @AudioN
tag).** This matches BytePlus's own demonstrated convention in both example
styles (``docs/seed-audio-1.0-overview.md``): the "Great War" T2A example
fully describes each character once ("Aric (young prince, breathless but
brave...)") and then just names them on every later line ("Mara, gripping
her sword,"); the League of Legends TA2A example does the same with a
reference tag, dropping the voice note but keeping "( voiced by <<TGT_SPK2>>
)" on repeats. Repeating the full description every line was measured to
blow well past the 2048-char limit on multi-character dialogue-heavy
segments for no benefit — the model already carries the established voice
forward within one continuous prompt, so re-describing it is redundant
exactly the way re-describing a reference image is in occ's video pipeline.

**Continuation reference** (METHODOLOGY.md §2a): when the caller passes
``continuation_characters`` (the character(s) the *previous* segment's tail
clip actually covers, from ``segmenter.DraftSegment.tail_characters``), this
function reserves the first @AudioN slot for that clip and uses it for those
character(s) instead of their original casting audition — freeing the
remaining slots for characters the continuation clip does NOT cover. The
returned reference at ``continuation_index`` is a **placeholder** (the real
tail-clip file doesn't exist until the previous segment has actually been
generated) — the pipeline substitutes the real file at execution time and
must exclude this placeholder from any content hash (see ``reuse.py``).
"""
from __future__ import annotations

from .backends.base import AudioReference
from .config import ProjectConfig
from .segmenter import DraftSegment

MAX_REFERENCES = 3
_PENDING_CONTINUATION_MARKER = "<pending-continuation-tail-clip>"


def build_prompt(segment: DraftSegment, project: ProjectConfig, *,
                 continuation_characters: list[str] | None = None,
                 ) -> tuple[str, list[AudioReference], dict[str, str], int | None]:
    references: list[AudioReference] = []
    audio_tag: dict[str, str] = {}
    covered = set(continuation_characters or [])
    continuation_index: int | None = None

    if covered:
        references.append(AudioReference(audio_path=_PENDING_CONTINUATION_MARKER))
        continuation_index = 0
        tag = "@Audio1"
        for name in covered:
            audio_tag[name] = tag

    for name in segment.locked_characters:
        if name in audio_tag or len(references) >= MAX_REFERENCES:
            continue
        ref_path = project.voice_reference(name)
        speaker = project.voice_speaker(name)
        if ref_path:
            references.append(AudioReference(audio_path=str(ref_path)))
        elif speaker:
            references.append(AudioReference(speaker=speaker))
        else:
            continue  # cast declared a lock slot but recorded no reference yet
        audio_tag[name] = f"@Audio{len(references)}"

    def first_mention(name: str) -> str:
        tag = audio_tag.get(name)
        note = project.voice_note(name)
        if tag and name in covered:
            return f"{tag}, continuing from the previous segment"
        if tag and note:
            return f"{tag}, {note}"
        if tag:
            return tag
        return note or "a neutral voice"

    def repeat_mention(name: str) -> str | None:
        """None means no parenthetical at all — a bare name, as BytePlus's
        own un-referenced repeat mentions do (e.g. "Mara, gripping her
        sword,")."""
        return audio_tag.get(name)

    mentioned: set[str] = set()
    lines: list[str] = []
    for beat in segment.beats:
        if beat.kind == "narration":
            if project.mode == "radioplay":
                lines.append(f"[Ambience/SFX: {beat.text}]")
                continue
            name = "Narrator"
            if name not in mentioned:
                lines.append(f'Narrator ({first_mention(name)}) reads: "{beat.text}"')
                mentioned.add(name)
            else:
                desc = repeat_mention(name)
                lines.append(f'Narrator ({desc}) reads: "{beat.text}"' if desc
                            else f'Narrator reads: "{beat.text}"')
        elif beat.kind == "dialogue":
            name = beat.speaker or "Unknown"
            if name not in mentioned:
                lines.append(f'{name} ({first_mention(name)}) says: "{beat.text}"')
                mentioned.add(name)
            else:
                desc = repeat_mention(name)
                lines.append(f'{name} ({desc}) says: "{beat.text}"' if desc
                            else f'{name} says: "{beat.text}"')

    return "\n".join(lines), references, audio_tag, continuation_index
