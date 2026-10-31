---
name: altocue-cut
description: Cuts video from word-timed transcripts. Removes silence, fillers, stutters, false starts and retakes, snaps edges to real speech, and refuses any cut through a word. Use when the user asks to tighten a recording, remove ums, cut pauses, keep the best take, or shorten a video. Part of Altocue.
license: MIT
compatibility: Requires the altocue CLI.
---

# Altocue Cut

The cut is the edit. Everything else is finish.

## Steps

1. `altocue transcribe` writes one word file per clip with fillers kept.
2. `altocue cut` writes `edl.json`, `work/cut-report.md` and `work/cut-review.md`.
3. Read `work/cut-review.md`. It holds a packed transcript and every decision the code would not make alone.
4. Rule on each `review` item. Write `decisions.json`, then run `altocue cut --apply-review decisions.json`.
5. Check the result reads as a whole. Read the kept words aloud in your head.

## How to rule

- **Keep the last complete take.** People improve as they repeat.
- **Keep the best delivery if the last take trails off.**
- **Never join two halves of different sentences** to save a second, unless the join reads naturally.
- **Keep a breath before a key line.** Pacing is not the same as speed.
- **Cut a filler only when the sentence survives.** "So" that starts a story can stay.
- **Ask the user** when two takes say different things. That is a choice about the message.

## What the code guarantees

- Edges sit on measured speech, not on transcript guesses.
- No cut lands inside a word. `altocue cut` refuses that edit.
- Every removal is listed with its reason in `work/cut-report.md`.
- After the render, QA re-transcribes the output and compares it with the expected words.

## Tips to give the user before they film

Saying "redo" or "start again" after a mistake is the cheapest edit there is. Altocue treats those as cues and removes the failed take.
