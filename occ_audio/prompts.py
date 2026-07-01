"""Assemble a Seed Audio ``text_prompt`` + its ordered ``references[]`` for
one segment.

Reference order must exactly match @AudioN order in the assembled text —
this is the one binding contract with the API (METHODOLOGY.md §6). In
**audiobook** mode narration is read verbatim by "Narrator" (a pseudo cast
entry, configured like any character); in **radioplay** mode narration
becomes ambience/SFX/music direction and only cast dialogue is performed.

The exact inline tagging style below (``Name (@Audio1) says: "..."``) follows
the technical API doc's own examples (``docs/seed-audio-1.0-http-api.md``,
"Use @Audio1 as the narrator voice and read the following line naturally").
Treat it as a look-dev-validated-on-first-segment point, not gospel — confirm
on one cheap real call before wide spend, same as any new prompt convention.

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

    def voice_for(name: str) -> str:
        tag = audio_tag.get(name)
        note = project.voice_note(name)
        if tag and name in covered:
            return f"{tag}, continuing from the previous segment"
        if tag and note:
            return f"{tag}, {note}"
        if tag:
            return tag
        return note or "a neutral voice"

    lines: list[str] = []
    for beat in segment.beats:
        if beat.kind == "narration":
            if project.mode == "radioplay":
                lines.append(f"[Ambience/SFX: {beat.text}]")
            else:
                lines.append(f'Narrator ({voice_for("Narrator")}) reads: "{beat.text}"')
        elif beat.kind == "dialogue":
            name = beat.speaker or "Unknown"
            lines.append(f'{name} ({voice_for(name)}) says: "{beat.text}"')

    return "\n".join(lines), references, audio_tag, continuation_index
