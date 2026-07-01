# occ-audio — one-click audiobook / radioplay creation

A config-driven pipeline that turns a novel, screenplay, or script into a
finished audiobook or radioplay: character extraction, voice casting (4
audition takes per character, user picks), segmentation into Seed-Audio-sized
chunks, BytePlus Seed Audio 1.0 generation, concurrent rendering, cross-run
reuse, and ffmpeg-concatenated final output.

Read these in order:
- **`METHODOLOGY.md`** — the source-to-audio playbook (start here for a new project).
- **`docs/seed-audio-1.0-http-api.md`** — the technical API reference.
- **`docs/seed-audio-1.0-overview.md`** — the prompting guide / use cases.

## Setup

```bash
cp .env.example .env        # fill in SEED_AUDIO_API_KEY
python -m pip install -r requirements.txt
```

`occ-audio` needs PyYAML and `ffmpeg` (system, or the bundled
`imageio-ffmpeg`). No key is needed for `scan`, `preview`, `models`, or
`--dry-run`.

## Commands

```bash
python occ_audio.py models                       # list registered models
python occ_audio.py cast "<project>"              # generate audition takes (paid)
python occ_audio.py scan "<project>"              # show the segmentation plan (free)
python occ_audio.py preview "<project>"           # assemble every prompt for review (free)
python occ_audio.py generate "<project>" --dry-run
python occ_audio.py generate "<project>"          # full run (needs SEED_AUDIO_API_KEY)
python occ_audio.py stitch "<project>"            # re-concat the latest run
```

`python -m occ_audio <command>` works too. Flags: `--max-segments N`,
`--segments ID,ID`, `--regen all|<ids>`, `--concurrency N`, `--run-name
<name>`.

**Always `scan` and `preview` — and read every prompt — before a paid
`cast` or `generate`.**

## Projects

A project is a folder with a `project.yaml`:

```yaml
name: my_audiobook
mode: audiobook                     # audiobook | radioplay
source: script.fdx                  # .fdx (Final Draft), .docx, .pdf, .md, or .txt
cast_mode: key                      # key | all
cast:
  Narrator:
    voice_note: "warm female, 40s, measured pace"
    reference: casting/Narrator/take_2.wav   # filled in after casting
  Hero:
    voice_note: "young male, energetic, slight rasp"
defaults:
  language: en
  audio_format: wav
  sample_rate: 24000
  target_segment_seconds: 110
  continuation_seconds: 0            # >0 enables chained voice continuity, see below
  max_chain: 0                       # cap on consecutive chained segments (0 = unlimited)
```

Every character carries a `voice_note` (its voice brief) whether or not it
gets a hard-locked `reference` — **at most 3 characters can be hard-locked
per segment** (a Seed Audio API limit), so characters without room in a
given segment's 3 slots fall back to their `voice_note` description. See
`METHODOLOGY.md` §2.

### Source formats

`source:` accepts `.fdx` (Final Draft — parsed from its own structured
Scene Heading/Character/Dialogue/Action types, the most reliable input),
`.docx` (Word, via `python-docx`), `.pdf` (via `pypdf`), `.md`, or plain
`.txt`. Non-FDX formats fall back to the same screenplay/prose heuristics
plain text uses. `.docx`/`.pdf` support needs their packages installed
(`pip install -r requirements.txt`).

### Cost estimate

Every `scan`, `preview`, and `cast` prints a cost estimate — clip count,
estimated total seconds, and estimated USD at $0.15/min — before any paid
call. It's an estimate off the segmenter's rough duration guess, not a
billing fact; actual cost is based on each clip's real generated duration.

### Continuation reference (optional)

Set `defaults.continuation_seconds` (recommended 20-30) to carry a tail clip
of each segment's own generated audio forward as a reference for the next
segment — a poor-man's chain built out of the ordinary reference mechanism,
since the API has no dedicated continuation field. It only covers whichever
character(s) actually spoke in that trailing window (`occ_audio scan`/
`preview` show exactly who), and it introduces a real sequencing dependency:
continuation-linked segments run in order, like occ's video chain mode.
See `METHODOLOGY.md` §2a before enabling this on a real project.

## Casting

`occ_audio cast` generates 4 short (~15s) audition takes per character (per
`cast_mode`), written to `<project>/casting/<Character>/take_{1..4}.wav`
plus a `manifest.json` recording the prompt used for each take. Listen, then
record your choice in `project.yaml` (`cast.<Name>.reference`). No
auto-selection.

Each run writes `<project>/outputs/runs/<run>/` with `segments/`,
`prompts/`, `results/`, `manifest.json`, `final.<ext>`. It publishes the
finished audio as a **numbered iteration** — `<project>/outputs/<name>_v01.wav`,
`_v02.wav`, … — never overwritten, plus a `<name>_latest.wav` pointer.

## Status

New project, scaffolded from `occ`'s proven pipeline shape and adapted for
BytePlus Seed Audio 1.0 (synchronous TTS, not async video generation — see
`METHODOLOGY.md` §1 for what that changes).
