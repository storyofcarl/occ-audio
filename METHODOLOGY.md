# METHODOLOGY — the source-to-audio playbook

This is the non-negotiable rulebook for occ-audio. It exists because every
rule here maps to a real, tested limit of the BytePlus Seed Audio 1.0 API
(`docs/seed-audio-1.0-http-api.md`) — skipping this doc means re-learning
those limits the expensive way (a paid call that gets rejected, or worse,
one that silently produces the wrong voice).

## 1. Know the model

Seed Audio 1.0 (`seed-audio-1.0`) is a **synchronous** reference-to-audio
API — one `POST`, one response, no task/poll loop:

- `POST https://voice.ap-southeast-1.bytepluses.com/api/v3/tts/create`
- Auth: `X-Api-Key` header (new-console mode).
- `text_prompt` ≤ **2048 characters**.
- `references[]`: up to **3** audio references (`speaker` | `audio_data` |
  `audio_url`, one per entry, mutually exclusive) — OR exactly 1 image
  reference. Audio and image references are never mixed in one call.
- Each reference audio file: ≤ 30s, ≤ 10MB, wav/mp3/pcm/ogg_opus.
- Output cap: **120 seconds per call**. Billed on `original_duration` at
  $0.15/min.
- Response carries `audio` (base64) — decode and persist it immediately.
  The `url` field is a temporary (2h) convenience link, not storage.
- In-prompt, referenced voices are addressed `@Audio1` / `@Audio2` /
  `@Audio3`, **in the same order** as `references[]`. Get the order wrong
  and the voices swap — this is the one binding contract with the API.

The API itself has **no formal continuation mechanism** (no equivalent of
Seedance's `reference_video` chain-extend field). By default every segment
is a fully independent, stateless call — but §2a describes an opt-in
technique that emulates one using the ordinary reference-audio slot.

## 2. The 3-voice rule (the single biggest constraint)

**At most 3 characters can have a hard voice-lock (real reference audio) in
any one segment.** This is an API limit, not a stylistic choice, and it
drives everything downstream:

- A character *with* a locked reference is `@AudioN`-tagged and must never
  also be described in the text — the reference carries the voice.
- A character *without* a locked reference for a given segment is voiced by
  a consistent, verbatim-reused **voice_note** (age/gender/accent/tone/
  personality, written once at casting time) — never a fresh ad-hoc
  description, or voice consistency drifts segment to segment.
- When a segment naturally has more than 3 speaking characters, the
  segmenter locks the 3 with the most lines in that chunk and the rest fall
  back to voice_note-only description, and **flags this for human review**
  — never silently drop the rule or silently pick for the user.

## 2a. Continuation reference (opt-in poor-man's chain)

`references[]` accepts any audio, including audio the pipeline generated
itself. Set `defaults.continuation_seconds` (recommended: 20-30s) and each
segment carries a trimmed **tail clip of the previous segment's own output**
forward as one of its references — the audio analogue of Seedance's video
chain-extend, built entirely out of the ordinary reference mechanism rather
than a dedicated API field.

- **A tail clip only carries the voice(s) that actually speak within it.**
  If the previous segment's last ~20-30s is entirely the Narrator, the
  continuation reference covers the Narrator only — it cannot bootstrap a
  character who didn't speak in that window. `segmenter.DraftSegment.
  tail_characters` computes exactly who is covered; `prompts.build_prompt`
  only ever tags a character `@AudioN, continuing from the previous segment`
  when they're actually in that set.
- The continuation reference takes one of the 3 reference slots — usually
  slot 1. This is often a net win over spending that slot on an original
  casting audition: it frees the *other* slots for characters new to this
  segment, and it can bootstrap voice consistency for a character who was
  never given a dedicated audition at all (their first line establishes the
  voice; later segments carry it forward via the tail clip).
- **This introduces a real sequencing dependency.** A continuation-linked
  segment cannot generate until its predecessor's audio exists (to trim the
  tail clip from), so consecutive continuation-linked segments run as one
  sequential chain — exactly like occ's video chain mode. Segments that
  are NOT continuation-linked to their neighbor still run concurrently.
  With `continuation_seconds: 0` (the default), nothing chains and the
  whole run stays parallel.
- `defaults.max_chain` (0 = unlimited, mirrors occ) re-anchors the chain
  after N consecutive links — repeatedly deriving a reference from a
  reference of a reference is an unvalidated compounding-drift risk, the
  same lesson occ learned the hard way for video.
- This is a new, unvalidated technique — treat the first real chained
  segment as a look-dev checkpoint (listen to it before trusting the
  pattern across a whole project), the same as any other "confirm before
  wide spend" point in this doc.

## 2b. Chain per sequence, not per script (parallelism)

Left unbounded, continuation chaining (§2a) turns a whole script into one
long serial chain — every segment waits for its predecessor, so a 90-minute
film generates one clip at a time. `project.yaml`'s `sequence_starts` fixes
this: a list of 1-based **heading occurrence numbers** marking where each
new narrative sequence begins (e.g. the classic 8-sequence structure —
Status Quo/Inciting Incident, Predicament/Plot Point 1, Rising Action,
Midpoint, Subplot/Escalation, Low Point/Plot Point 2, New Plan, Climax/
Resolution). The chain only links segments that share a `sequence_id`, so:

- Segments **within** one sequence still chain for voice continuity across
  a scene that spans multiple segments.
- Different **sequences** become independent chains and generate **in
  parallel** — an 8-sequence film runs as 8 concurrent chains instead of
  one, up to `defaults.concurrency`.
- With `sequence_starts` left empty (the default), the whole script is one
  sequence — today's behavior, unchanged.

Getting the boundaries right requires actually reading the script and
identifying where each structural beat genuinely lands (inciting incident,
midpoint reveal, low point, climax) — do not guess by page-count proportion.
Boundaries must fall on real scene-heading breaks (a sequence can't split
mid-scene); `occ_audio scan`'s `seqN` tag on every segment is how you verify
the assignment landed where intended before spending anything.

## 3. Cast before you spend

Casting is a distinct, gated phase, analogous to occ's "preview before
generate": for each character (all, or only "key" characters, per
`cast_mode` — `key` casts exactly the characters declared in `cast:`, `all`
casts every speaker the script parser finds), generate **4** short (~15s)
audition takes — text-prompt-only (no reference) — and present them to the
user. **Every take uses the identical prompt.** Seed Audio is stateless and
non-deterministic per call, so the same prompt run 4 times already produces
4 distinct voice realizations; manufacturing "faster pace"/"heightened
emotion" variants only steered performance on that one line, not the
underlying voice, so it added noise without adding real choice. The user's
choice is recorded by hand in `project.yaml` (`cast.<Name>.reference`).
**No auto-selection.**

**A character must have a `voice_note` to be cast.** With nothing to
describe the voice, Seed Audio picks an unconstrained voice and the "take"
isn't an audition of anything — `generate_auditions` refuses to run without
one. This is also why `cast_mode: key` must read the actual `cast:`
declarations rather than guess by line-count: guessing surfaces characters
with no configured `voice_note`, which either wastes the call (blank note)
or silently casts characters nobody asked to cast and pays for it.

Segment generation must not start until the cast the segment depends on has
a recorded decision (a lock, or an explicit voice-only fallback).

## 4. From source to segments

- **Dialogue and narration text is always verbatim from source.** Never
  paraphrase, summarize, or translate a line — the same rule as occ's
  "dialogue and lyrics are verbatim," for the same reason: it's not yours to
  rewrite.
- Fill genuine gaps (a screenplay with no narrator but prose needs one read
  aloud in audiobook mode) deliberately, and flag the fill — don't invent
  silently.
- A **segment** is one Seed Audio call: ≤ 2048 chars, ≤ 3 locked voices,
  targeting (not exactly hitting — TTS duration isn't predictable
  ahead of generation) `target_segment_seconds` (default 110s, headroom
  under the 120s cap).
- Scene/chapter boundaries (screenplay sluglines, chapter/section breaks in
  prose) are preferred segment breaks over splitting mid-scene.
- The segmenter's output is a **draft for human/agent review**, not an
  auto-approved plan — same discipline as occ's storyboard.

## 5. State the scene every segment

Because there is no continuation mechanism (§1), nothing about ambience,
location, or mood carries from one segment to the next automatically. Each
segment's `text_prompt` must **fully re-establish its own background/SFX/
atmosphere** — the audio analogue of occ's "state time-of-day every
segment." Never assume the model remembers the rainy street from the
previous segment; say so again if the scene is still there.

## 6. Prompt assembly

- **Audiobook mode**: the narrator reads prose verbatim, including
  attribution ("she said"); dialogue lines are voiced by the speaking
  character (locked reference or voice_note description).
- **Radioplay mode**: action lines become ambience/SFX/music direction (in
  the style Seed Audio's own demonstrated prompts use — footsteps, doors,
  weather, room tone); dialogue is performed by cast only; no narrator
  unless the source explicitly has one.
- Reference order in the assembled prompt must exactly match the order of
  `references[]` sent to the backend.
- Stay under the 2048-char limit — `occ_audio preview` reports every
  segment's character count before spend.

## 7. Workflow & guardrails

- `occ_audio scan` (free) shows the segmentation plan; `occ_audio preview`
  (free) assembles and prints every actual `text_prompt` with char counts
  and reference counts. Run both, and read them, before any paid
  `cast` or `generate`.
- A run only stitches a complete set — never publish audio with gaps.
- A failed segment retries once; if it fails again, triage the cause (don't
  blindly re-burn the same prompt).
- Unchanged segments (same script text + same voice references + same
  audio_config) are reused for free across runs — never regenerated. When
  continuation chaining is on, a segment's reuse identity folds in its
  entire chain so far (occ's `cumulative_hash` pattern): if anything
  upstream changes, every segment downstream of it in that chain loses its
  reuse hit too, even if its own inputs are unchanged.
- Verify the assembled audiobook/radioplay before calling it done: listen
  to segment boundaries for level or pacing jumps, since there is no
  built-in cross-segment loudness/timing normalization beyond what
  `stitch.py` applies.

## 8. Process discipline

Same meta-lesson as occ: **understand the model and the source material
before building or spending.** Study the two docs in `docs/` first, preview
always, spend last.

## 9. Never commit `.env`

Verify before every `git add`.
