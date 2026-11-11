---
name: altocue-cut
description: Cuts video from word-timed transcripts. Removes silence, fillers, stutters, false starts and retakes, builds a cut from chosen lines in a readable script, snaps edges to real speech, and refuses any cut through a word. Use when the user asks to tighten a recording, remove ums, cut pauses, keep the best take, shorten a video, pick lines, reorder what was said, or edit by transcript. Part of Altocue.
license: MIT
compatibility: Requires the altocue CLI.
---

# Altocue Cut

The cut is the edit. Everything else is finish.

## Steps

1. `altocue transcribe` writes one word file per clip with fillers kept.
2. `altocue cut` writes `edl.json`, `script.md`, `work/cut-report.md` and `work/cut-review.md`.
3. Read `work/cut-review.md`. It holds a packed transcript and every decision the code would not make alone.
4. Rule on each `review` item. Write `decisions.json`, then run `altocue cut --apply-review decisions.json`.
5. Check the result reads as a whole. Read the kept words aloud in your head.

## Script mode: build the cut from lines

Removal is the wrong tool when the job is choosing. A long talk that must become a short film, a story told in a better order, or a paper edit from a client: edit `script.md` instead. It holds every sentence, one to a line.

```
[L0012 0:41.2] The hardest part is saying ~~um~~ no to good work.
[L0013 0:44.9] ~~Wait, redo.~~
```

- **Move a line** to change the order. Neighbouring sentences split cleanly.
- **Delete a line**, or put `~~` around it, to leave it out.
- **Delete words**, or put `~~` around them, to trim inside a line. Take the `~~` off to bring back anything the cut removed.
- **Then run `altocue cut --from-script`.** Your last cut is saved to `work/edl-previous.json`.

The lines choose the words. The edges come from the same code as every other cut, so they still land on real speech and never inside a word: the in point backs off to the quiet before a word's first sound, and the out point runs on to the quiet after its last, since recognisers end soft words early.

- **Keep whole lines.** When a viewer heard a clipped or strange sound at a join, the cut was built from pieces of lines. Choose whole sentences, from their first word to their last, and let the script place the edges. Never hand-write segment edges a few frames inside a line in `edl.json`. `~~struck~~` means out, whether the cut took it or you did.

- **You cannot add a word.** A word the recording does not hold is refused, by line number. Fix a spelling in `[transcribe.fix]`, then `altocue cut --write-script`.
- **A word plays once.** A line in the script twice is refused.
- **A line with no `[L number]`** is found by its words. If it was said twice, you need the number.
- **Make it fresh with `altocue cut --write-script`.** It writes the current cut, in play order, and saves your old file to `work/script-previous.md`.

## How to rule

- **Keep the last complete take.** People improve as they repeat.
- **Keep the best delivery if the last take trails off.**
- **Never join two halves of different sentences** to save a second, unless the join reads naturally.
- **Keep a breath before a key line.** Pacing is not the same as speed.
- **Let a big picture run.** [source] A large sequence can hold several seconds with no narration.
- **In dialogue, cut on the last syllable,** or about 0.3 seconds after it in a fast scene. [source] One author.
- **Interleave shot types** rather than grouping them. [source]
- **Cut a filler only when the sentence survives.** "So" that starts a story can stay.
- **Moving a line can change what it means.** Read the new order aloud. Keep the speaker's meaning intact, and ask the user if a move changes it.
- **Ask the user** when two takes say different things. That is a choice about the message.

## Changing the cut

- **One stretch:** `altocue cut --remove s007 --reason "drags"`. Segment ids are in `work/cut-report.md`.
- **The whole film tighter:** lower `[cut] max_pause` from its 0.4 default. [source] One guide cuts pauses over about a third of a second. Then run `altocue cut` and rule again.
- **`altocue cut` with no flags starts over.** Your earlier cut and rulings go to `work/edl-previous.json`.
- **New order:** move lines in `script.md`, or segments in `edl.json`. Either way the order stays through a re-cut.
- **Hand edits stay.** `--remove`, `--apply-review` and `--from-script` keep `crop`, `zoom_from`, `zoom_to`, `gain` and order. See below.
- **Every cut change moves B-roll.** See `altocue-broll`.

Long video into shorts belongs to `altocue-clips`.

## Whose footage

Own footage by default. [evidence] YouTube treats clips of someone else's show, or shorts compiled from other sites, as reused content even with permission. Substantive editing with commentary or story is allowed. A permission note does not clear it, and copyright applies separately. See [the platform rules](../altocue/references/platform-rules.md).

## What the code guarantees

- Edges sit on measured speech, not on transcript guesses.
- No cut lands inside a word. `altocue cut` refuses that edit.
- Every removal is listed with its reason in `work/cut-report.md`.
- After the render, QA re-transcribes the output and compares it with the expected words.

## Hand edits survive a re-cut

Set `gain`, `crop`, `zoom_from` or `zoom_to` on a segment in `edl.json` and it stays through `--remove`, `--apply-review` and `--from-script`. So does any order you moved.

- **Edits follow the words.** A segment that splits gives its edit to both halves. Where two merge, the one with more speech wins.
- **Anything lost is reported.** The command prints `Could not keep`, and `work/cut-report.md` lists it under Hand edits. Read that list after every re-cut.
- **`altocue frame` leaves them alone.** `held` on a segment lists what was set by hand. `altocue frame --force` clears crops and zooms.
- **A fresh `altocue cut` starts clean.** It says how many edits stay behind in `work/edl-previous.json`.

## Tips to give the user before they film

Saying "redo" or "start again" after a mistake is the cheapest edit there is. Altocue treats those as cues and removes the failed take.
