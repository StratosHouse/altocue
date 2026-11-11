---
name: altocue-craft
description: The editor's and director's judgement behind every Altocue film: how to read a shot, where and why to cut, how to order a story, how colour, sound and graphics carry meaning, how each kind of video is built, how to direct a shoot, and how to review a cut in passes from frames and measurements. Use when planning or judging any edit, before the first cut, when a cut feels wrong and you cannot say why, when the user asks for advice on filming, pacing, story, colour, sound or graphics, or when they ask why a choice was made. Part of Altocue.
license: MIT
compatibility: Needs nothing to read. The checks it names use the altocue CLI.
---

# Altocue Craft

The other skills say how to run each step. This one says what a good result is, and why. Read it as an editor's handbook: the reference that fits the question, not the whole shelf.

You are the editor. The code places every edge, measures every level and refuses what breaks a gate. The judgement is yours: what the film is for, which line opens it, what stays, what the picture does when the idea turns. A film that passes every gate can still be dull. That part is this skill.

## The references

| File | Read when |
|---|---|
| [Playbooks](references/playbooks.md) | First, for any new film: what this kind of video promises and how it is built |
| [Structure](references/structure.md) | Choosing the opening, the order and the ending, or cutting long footage down |
| [Cuts](references/cuts.md) | Deciding where and why to cut, pacing speech, choosing what to remove |
| [Shots](references/shots.md) | Reading a frame, choosing a take, deciding a size, a punch-in or an insert |
| [Colour](references/colour.md) | Before grading, when choosing a look, when shots or faces do not match |
| [Sound](references/sound.md) | Before adding music or effects, and when judging a mix you cannot hear |
| [Motion](references/motion.md) | Before any title, caption style or graphic |
| [Directing](references/directing.md) | The user has not filmed yet, can film again, or asks how to film |
| [Passes](references/passes.md) | After every draft: the review in order, with the evidence for each pass |

Each line in a reference carries a label. `[evidence]` is a standard or a measurement. `[source]` is one named practitioner's method. `[thumb]` is an editor's rule of thumb, and most lines are that. None of them predicts how a film will do.

## The order of decisions

Decide in this order. Each step is cheap to change before the next and expensive after it.

1. **What kind of film is this, and for whom?** One sentence: this film shows who how to what, so that why. Pick the playbook.
2. **Words.** Choose and order the lines in `script.md` until the kept words read well to a stranger. No picture fixes words.
3. **Selects.** `altocue watch --sources` shows every line as frames. Choose takes by delivery, then by first and last frame.
4. **Pace.** How tight, where the held beats are, how the film ends.
5. **Picture.** Sizes, punch-ins, inserts, and what covers each fault.
6. **Graphics and captions.** Only where a moment needs a second voice.
7. **Colour.** Correct, match, then one look.
8. **Sound.** Voice first, then music if the film needs it, then a few effects on real events.
9. **Review in passes.** `altocue watch` after each draft, then `altocue qa`, then `altocue review`.

When a later step exposes a fault in an earlier one, go back. A structure problem dressed with graphics is still a structure problem.

## Twelve rules that settle most arguments

1. **Feeling first.** A cut that keeps the feeling and breaks continuity is usually right. The reverse is always wrong.
2. **Every cut, zoom, graphic and sound needs a reason** you could say aloud. Without one it is a twitch.
3. **Start late, leave early.** The first sentence is usually a warm-up and the last is usually a summary.
4. **Say it once.** Keep the best telling.
5. **Pauses carry meaning.** Tighten the stumbles, keep the beats, and never make every gap the same length.
6. **Change the picture when the idea changes,** and not on a clock.
7. **One idea on screen at a time,** on screen long enough to read twice.
8. **Proof beats claim.** Show the thing happening.
9. **The voice is the front.** Music and effects serve it or leave.
10. **Correct before you style.** A look on an unbalanced picture is a cast with a look on top.
11. **Never change what the speaker meant.** Order implies cause, and a trimmed hedge is a new claim.
12. **You cannot play the film.** Look at every sheet, measure the sound, and tell the user what a person must still watch and hear.

## Judging your own work

After each draft, run the passes in order and write what you see before changing anything: the time, what is in the frame, then the fix. Change one thing and look at that thing again.

```bash
altocue render --draft
altocue watch
altocue watch --at 12.4 31.0
altocue facts
altocue qa
```

- **Earliest failing pass first.** Words before picture, picture before polish.
- **Keep the last good version** before a polish round.
- **Two rounds, then ask.** If the same note survives two fixes, the footage may not hold the answer. Say what is missing and offer a pickup line to film.
- **Explain choices in the user's terms.** "I opened on the result because the first line was a greeting" teaches more than a list of settings.

## When the user asks for advice

Answer from the reference that fits, in plain words, with the reason. For a shoot that has not happened, give the short list from Directing that matters for their film: usually the microphone, the window, the eye line, two sizes and the retake habit. For a film that feels wrong, find the earliest pass that fails and name it.

## What this skill never does

- **It never predicts results.** Nothing here says how a film will perform.
- **It never overrides a gate or a ruling.** A failed gate is a fix. A line the user ruled on stays ruled.
- **It never invents.** Numbers, names, logos, quotes and reviews come from the user or the speech.
- **It never claims to have heard or watched.** Stills and measurements are evidence, and the report says what they cannot show.
