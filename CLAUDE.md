# occ-audio — agent instructions

## MANDATORY FIRST STEP

**Before doing any work on this project — reading code, casting voices,
segmenting a script, running a generation — you MUST read `METHODOLOGY.md`
in full.** It is the source-to-audio playbook. Every rule in it comes
directly from the BytePlus Seed Audio 1.0 API's real, tested limits; skipping
it means re-deriving (or blowing past) those limits at the user's expense.
This applies to every new session and every subagent spawned for project
work.

Then read `README.md` for the commands and project layout.

## What occ-audio is

A config-driven audiobook / radioplay pipeline: a novel, screenplay, or
script → character extraction and voice casting → segmentation into
Seed-Audio-sized chunks → BytePlus Seed Audio 1.0 generation → concurrent
rendering → stitched final audio file. The CLI is `python occ_audio.py
<command>` (`cast` / `scan` / `preview` / `generate` / `stitch` / `models`).
See `README.md`.

## Non-negotiable rules (full detail and reasoning in METHODOLOGY.md)

- **Never guess the API.** `docs/seed-audio-1.0-http-api.md` is the source
  of truth for the request/response shape; `docs/seed-audio-1.0-overview.md`
  is the prompting guide. Re-read both before changing `backends/seed_audio.py`
  or `prompts.py`.
- **At most 3 hard-locked (reference-audio) voices per generation call.**
  This is an API limit, not a design choice — it drives segmentation.
- **Cast before you spend.** Run the casting phase (4 auditions per
  character) and get explicit user sign-off on the chosen take before any
  segment generation.
- **Dialogue and narration are verbatim.** Never paraphrase source text.
- **`occ_audio preview` and read every assembled prompt before any paid
  `generate`.**
- A run only stitches a complete set; a failed segment retries once, then is
  triaged — never re-burned blindly.

## Working on the pipeline itself

- After changing `occ_audio/`, run the tests in `tests/` (`python
  tests/test_*.py`).
- Never commit `.env`. Verify before any `git add`.
