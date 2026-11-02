---
name: altocue-clips
description: Finds short clips in a long recording and cuts each into its own project, reusing the transcript. Checks that each candidate is a complete thought with a clear start and end, a usable first line, a useful length and nothing pointing outside the clip. Use when the user wants shorts, reels or clips from a podcast, talk, webinar, interview or long take. For the user's own recordings or footage they have the rights to. Part of Altocue.
license: MIT
compatibility: Requires the altocue CLI and a transcript. `--speaker` needs the optional speakers extra.
---

# Altocue Clips

One statement from a long recording, cut so it makes sense alone.

## Rights come first

Long to shorts is for the user's own recordings, or footage they have the rights to. Ask before you start if it is not clear.

- **Permission is not the whole answer.** [evidence] YouTube's reused content rule applies even with the creator's permission. Named examples include show clips edited together with little narrative and shorts compiled from other sites. Substantive editing with added commentary or story is allowed. See [the platform rules](../altocue/references/platform-rules.md).
- **Copyright still applies separately.** A clip does not become fair to use by being short.

## Two films, not one

- **A long episode and its vertical cut are different films.** [source] If both are wanted, plan both from the start, in the brief and on the shoot.
- **A clip must work for someone who never saw the episode.** That is what the checks below test. A clip that only makes sense as a trailer needs a line that says so, which is the hook's job.
- **Treat what this makes as a first cut of a new film.** Frame, caption, sound and review it as its own project.

## Steps

1. `altocue new talk --input talk.mp4 --aspect 16:9`, then `altocue transcribe -p talk`. A long file goes to the engine in pieces.
2. `altocue clips find -p talk --count 8 --length 30-60`. This reads the transcript and nothing else. It writes `clips/candidates.md` and `clips/candidates.json`.
3. Read every candidate in full, the transcript as well as the checks. Pick the ones a person would finish watching.
4. `altocue clips cut -p talk --pick 1,3,5`. Each pick becomes a project in `clips/`, on the shape you ask for with `--aspect`, 9:16 by default.
5. Write each clip's hook into its `motion.json`. Then treat it as any other project: `altocue run -p talk/clips/01-... --draft`.

Run `clips find` on the raw talk. It needs no cut, no clean and no grade.

## What `find` checks

Each candidate carries five checks in plain words. None of them forecasts how a clip will do, and there is no score to rank by.

- **Opens:** it starts a thought. A pause or a change of subject comes before it. It does not lean on the line before with "And" or "That is why", answer a question with "Yeah" or "Sure", or say "it" or "those" before naming anything.
- **Closes:** it ends on a full stop with a pause after. It does not stop on a question, on "let me show you", or just before "But".
- **Hook:** the first line is short and has a question, a number, a direct "you" or a claim in it.
- **Stands alone:** nothing points outside it, such as "as I said", "this slide" or "second" with no "first".
- **Stays on topic:** a word of substance from the first half carries into the second. A word said twice in one place is a retake, not a theme.

A candidate that passes all five can still be dull. One that fails a check can still be the best moment in the talk. Read them. The checks are there so you do not have to find these faults by ear.

## Choosing

- **Keep the speaker's meaning intact.** A strong line that means something else without its context is the wrong pick. Read the sentences either side in the full transcript.
- **Prefer a statement to a topic.** "Why we stopped hiring in March" beats "Some thoughts on hiring".
- **Different moments, not the same point twice.** A batch of clips that say one thing is a channel problem as well as an editing one.
- **Tell the user what you passed over and why.** Report the candidates, with the hook and the reason, before you render anything.

## Writing the hook

You write it. The code will not. It goes in the clip's `motion.json`:

```json
{ "items": [{ "type": "hook", "text": "Why your first price\nis *always* too low", "start": 0, "duration": 2.6 }] }
```

- **Promise what the clip delivers.** Nothing it does not say.
- **Six to eight words.** See `altocue-motion`.
- **Never put words in the speaker's mouth.** The hook is on-screen text from the editor, not a quote.

## Fixing a clip

Each clip is a normal project, so every other skill works on it. Its transcript came from the long recording, and nothing is transcribed twice.

- **Trim a soft opening.** A clip that fails Opens on "So" often stands up without it. Strike the word in the clip's `script.md`, then `altocue cut --from-script`. See `altocue-cut`.
- **Reach a little past the edge.** Each clip holds a second or so of what came before and after, struck out in `script.md`. Take the `~~` off to bring a word back.
- **Each clip stands alone.** Clean, grade, frame, captions and QA all run on the clip's own short file, so the length of the source costs nothing here.

## Interviews

With speakers on the words (`altocue transcribe --speakers`), `clips find --speaker S2` keeps only clips that person holds. Each candidate lists who speaks and for how much of it. See `altocue-caption` for naming or colouring speakers. Listen before you trust a label: speaker models make mistakes on short turns and cross-talk.

## What is not checked

Delivery, energy, jokes and whether the point is any good. Those need a person, or the Editor in Chief on the finished clip. Say so when you hand candidates over.
