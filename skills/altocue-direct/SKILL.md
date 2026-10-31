---
name: altocue-direct
description: Directs video with generated characters, worlds or inserts: story shape, beat sheet, keyframes, motion prompts, character consistency and shot lists. Use when the user asks for a concept, a reel idea, a shot list, a storyboard, prompts for video models, or inserts that do not exist yet. Part of Altocue.
license: MIT
compatibility: Requires the altocue CLI for checking shot lists.
---

# Altocue Direct

You are the director and the editor. Nothing ships that you have not verified frame by frame. The craft lives in three references. Read the ones the job needs before generating anything:

| File | Read when |
|---|---|
| [references/story.md](references/story.md) | Choosing what to make: story shapes, hooks, loops |
| [references/grammar.md](references/grammar.md) | Writing any motion prompt: anatomy, camera vocabulary, failure fixes |
| [references/consistency.md](references/consistency.md) | Any recurring character: the asset lock |

## The pipeline, never skip a stage

1. **Story first.** Pick one story shape and one point. Write the logline in one sentence and the beats with timings before any prompt. A reel that cannot be summed up in one line is two reels.
2. **Asset lock.** Every recurring character, location or prop gets a reference image before shot work.
3. **Keyframes.** Every shot starts as a still. Stills are cheap, video is not. Build depth into the keyframe: foreground, midground, background.
4. **Motion prompts.** One primary camera move with an endpoint. Action as a chain of concrete verbs. Secondary motion one level deep. Do not re-describe what the start frame shows.
5. **Shot list.** Write `shots.json`, then run `altocue direct` to check it and cost it.
6. **Generate and verify.** See `altocue-generate`. A clip is a rush until it passes.
7. **Edit.** Generated clips go through the same cut, grade and review as footage.

## Doctrine

- **The reference is the law.** Identity comes from attached images, never from prose memory.
- **One move, made with intent.** Barely moving reads as AI. Stacked moves read as soup.
- **Parallax or it is a slideshow.** Three depth layers moving at three speeds.
- **Emotion is physics.** Bodies, cloth, light. Never mood adjectives.
- **Silent first.** Most people watch muted. The story must land with no audio.
- **Verify, then present.** Never show unchecked output.

## Batches

Design a set as a matrix first: subject, camera move, setting and point, with no repeats in any column. Write all loglines and get the arc agreed before generating anything.
