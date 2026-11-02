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
- **Let a big picture run.** [source] A large sequence can hold several seconds with no narration.
- **In dialogue, cut on the last syllable,** or about 0.3 seconds after it in a fast scene. [source] One author.
- **Interleave shot types** rather than grouping them. [source]
- **Cut a filler only when the sentence survives.** "So" that starts a story can stay.
- **Ask the user** when two takes say different things. That is a choice about the message.

## Changing the cut

- **One stretch:** `altocue cut --remove s007 --reason "drags"`. Segment ids are in `work/cut-report.md`.
- **The whole film tighter:** lower `[cut] max_pause` from its 0.4 default. [source] One guide cuts pauses over about a third of a second. Then run `altocue cut` and rule again.
- **`altocue cut` with no flags starts over.** Your earlier cut and rulings go to `work/edl-previous.json`.
- **New order:** segments play in the order they sit in `edl.json`, so move them by hand.
- **Hand edits come last.** `--remove`, `--apply-review` and a fresh proposal rebuild the segments. That resets their order and clears `crop`, `zoom_from`, `zoom_to` and `gain`.
- **Every cut change moves B-roll.** See `altocue-broll`.

Long video into shorts belongs to `altocue-clips` when it is installed.

## Whose footage

Own footage by default. [evidence] YouTube treats clips of someone else's show, or shorts compiled from other sites, as reused content even with permission. Substantive editing with commentary or story is allowed. A permission note does not clear it, and copyright applies separately. See [the platform rules](../altocue/references/platform-rules.md).

## What the code guarantees

- Edges sit on measured speech, not on transcript guesses.
- No cut lands inside a word. `altocue cut` refuses that edit.
- Every removal is listed with its reason in `work/cut-report.md`.
- After the render, QA re-transcribes the output and compares it with the expected words.

## Tips to give the user before they film

Saying "redo" or "start again" after a mistake is the cheapest edit there is. Altocue treats those as cues and removes the failed take.
