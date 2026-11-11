---
name: altocue-review
description: Runs the Editor in Chief, a separate review of a finished video. Code runs the fact gates, a separate reviewer scores it against a rubric, and code decides SHIP or FIX with timecoded notes. Use after every final render, or when the user asks to review, check, QA or approve a video. Part of Altocue.
license: MIT
compatibility: Requires the altocue CLI. Gemini needs GEMINI_API_KEY. Codex needs the codex CLI.
---

# Altocue Review

The editor never signs off their own work.

## Steps

1. `altocue qa` runs the fact gates. Fix any failure before a review.
2. `altocue review` picks a reviewer.
   - **`gemini`:** sees the actual video. Requires a paid key and the user's yes before upload.
   - **`codex`:** an independent vendor working from the review pack.
   - **`pack`:** writes `review/PACK.md`. Give it to the `editor-in-chief` agent, or any fresh agent, then `altocue review --ingest review/verdict.json`.
3. Read `review/REPORT.md`.
4. Make every fix, in order. Re-render. Review again. At most two rounds, then hand the open list to the user.
5. Offer `altocue review --page`. It writes `review/page.html`: the film beside each gate, note and caption, with a click on a note jumping to that moment. It is one file with no server and no network. It prints its path and opens nothing, so give the user the path.

## Tell the gates what the film must say

A brief can list lines the film has to say, under a heading of its own in `brief.md`:

```markdown
## Must say

- the free plan never expires
- thirty day refund | 30 day refund
```

The `brief_coverage` gate checks each line against the transcript of the render. It reads words, not meaning, so a `|` offers other wordings. Whether the point lands is the reviewer's call. With no such list the gate is not applicable.

## Rules

- **Never argue with a verdict in the report.** Fix it, or tell the user why you disagree.
- **Never edit `verdict.json`.**
- **Never show the reviewer your reasoning.** It gets the file, the brief, the rubric and the numbers. That keeps its read fresh. `review/page.html` holds the verdict and the notes, so it never goes to a reviewer.
- **Never show the reviewer facts, benchmarks or results.** `review/facts.json`, `bench/`, `results/` and `director-notes.md` stay out of its sight, and out of the brief. Numbers like these would anchor its hook and pacing scores. Facts reach the report only after scoring.
- **If the reviewer shares a vendor with you, say so.** The report labels it.
- **Always pass on the section "What this review could not see or hear".**

## Before you call it done

The gates catch what code can measure. These are yours. Most are [source], from working editors, so weigh them.

- **Look at key frames before a full render.** `altocue render --draft`, then every cut, graphic and insert.
- **Measure as well as look:** every cut time, the size of an effect in pixels, and stray frames.
- **Say what you saw before you change anything.** Timecode, what is in the frame, then the fix. Sampled frames mislead.
- **Check stability in order:** first, middle and last frames, then whole, with and without sound. You work from frames, so tell the user they still need to watch it at normal speed, once with the sound off. That is a design check, not a claim about how people watch.
- **Pull the exact first and last frames of each insert.** The `stray_frames` gate compares the frames at each hard edge, and leaves an edge with a transition alone. Those are still yours to check.
- **Flashes are measured.** The `flash_rate` gate holds a film to three in any one second. See `altocue-transitions`.
- **Reject a beautiful shot that breaks the scene.** [thumb]
- **Check every name, place, claim, phone number, likeness and brand.** Every factual claim holds up, and a hypothetical reads as one.
- **When matching a reference, compare the same timecodes,** not similar moments.
- **Keep the last good version.** Copy `out/final.mp4` aside before a polish pass, because the next master overwrites it.

## Fix vocabulary

Turn each note into one named fix: one sentence, one change, everything else left alone. [source] The note's dimension says where to look. After any change to `edl.json`, run `altocue render --draft` and look again. A changed cut also needs `altocue broll` if the film has B-roll. A cut fix keeps any order, crop, zoom or gain set by hand. Read its `Could not keep` lines, because an edit on words that left the film goes with them.

| Say | For a note on | Change |
|---|---|---|
| Tighter cut | `pacing` | `altocue cut --remove s007 --reason "drags"` for one stretch. For the whole film, set `[cut] pace` to `tight`, then `altocue cut` and rule again |
| Harder hook | `hook` | Move the strongest self-contained line to the top of `segments` in `edl.json`, then set the `hook` item's `text` in `motion.json` to its promise, with `start` at 0 |
| Match pacing | `pacing` | Measure the reference in its own project: `altocue new ref --input reference.mp4`, then `altocue facts -p ref --file <that file>`. Compare `scene_changes_per_10s` and `longest_static_s` with this film's, then use the rows above and below. Never aim at the number |
| Hide the jump | `pacing`, `picture` | `altocue broll --pick b1=c2`, or raise `[frame] punch_in` and run `altocue frame` |
| Land the line | `clarity` | `edl.json` `zoom_from` and `zoom_to` on that segment, such as 1.0 to 1.06 |
| Fix the level | `sound_as_speech` | `edl.json` `gain` on that segment |
| Hold it longer | `captions`, `clarity` | `duration` on that item in `motion.json`, or fewer words |
| Cleaner captions | `captions` | `style` to `boxed` and a lower `max_words`, under `caption` in the brand file |
| Fewer effects | `sound_as_speech` | `"sfx": false` in `motion.json`, or `under_db` on one cue |
| Softer look | `picture` | `[grade] look_strength`, such as 0.6, then `altocue grade` |
| Hard cut | `pacing` | Delete that row from `transitions` in `motion.json` |
| Shorter ending | `ending` | `duration` on the `endcard`, or `altocue cut --remove` on the last segment |

The same vocabulary for a generated shot is in `altocue-direct`.

## Directed presenter parity

For a presenter-led software or workflow reel, use the [directed Studio workflow](../../docs/DIRECTED-STUDIO.md). The standard render reads studio.json. Every reusable Studio improvement needs a portable implementation, example, render-PC proof and a versioned handoff. Keep private footage, brand marks and licensed sounds outside the product repo.
