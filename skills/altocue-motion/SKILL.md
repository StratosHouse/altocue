---
name: altocue-motion
description: Adds motion graphics to a video from a JSON file: hook, chips, lower thirds, stats, quotes, steps, title cards and end cards, timed to spoken words and styled from a brand file. Use when the user asks for titles, graphics, lower thirds, a hook, an end card or branded overlays. Part of Altocue.
license: MIT
compatibility: Requires the altocue CLI. Node 22 or later for full motion. A basic renderer works without it.
---

# Altocue Motion

Write `motion.json` in the project root. Times are seconds in the finished video, or follow a word.

```json
{
  "items": [
    { "type": "hook", "eyebrow": "For first-time founders", "text": "Three numbers\n*every founder* gets wrong", "start": 0, "duration": 3.0 },
    { "type": "lower_third", "title": "Sam Rivera", "subtitle": "Founder, Northlight", "start": { "word": "hello" } },
    { "type": "chip", "text": "Number one", "start": { "word": "first" } },
    { "type": "stat", "value": "41%", "label": "of runway goes to tools nobody uses", "start": { "word": "runway" } },
    { "type": "steps", "title": "The check", "items": [
      { "text": "Pull the invoices", "at": { "word": "invoices" } },
      { "text": "Cancel what is idle", "at": { "word": "cancel" } }
    ], "start": { "word": "check" }, "duration": 6 },
    { "type": "endcard", "headline": "Run the *check* this week", "cta": "yoursite.com", "duration": 2.6 }
  ],
  "music": { "path": "music/bed.mp3" },
  "sfx": [{ "name": "whoosh", "at": { "word": "next" } }]
}
```

`sfx` is `true` for the house sounds, `false` for none, or a list of cues to add to them. See `altocue-clean` for cues and for how music ends. Name only a sound `altocue sfx list` shows, or a file in the project. Never invent one. No sound is a fine answer.

## Types

`hook`, `chip`, `lower_third`, `stat`, `quote`, `steps`, `title`, `endcard`, and `composition` for a fetched or hand-written HyperFrames page. In copy, `*word*` takes the accent colour. In a hook, a new line is a new line of the headline.

## Formats

Every built-in format goes in `"type"`, with its own slots and copy limits, and copy that cannot fit is an error before the render. Search first with `altocue library search <what you want> --kind motion`, then `altocue library show <id>`. Places to start, by job:

- **Hook:** `word_slam`, `mask_rise`, `typewriter`, `word_swap`, `spotlight`.
- **Prove:** `count_up`, `bar_build`, `waffle`, `receipt_print`, `doc_highlight`.
- **Explain:** `stepped_list`, `flowchart`, `split_compare`, `whiteboard`, `cursor_walk`.
- **Close:** `recap_check`, `logo_sting`, `follow_prompt`.
- **Move between sections:** `chapter_card`, `iris`, `torn_wipe`.
- **Real proof only:** `testimonial_card`, `star_fill`, `logo_wall`, `press_strip`, `comment_card`, `avatar_cloud`, `before_after`. Fill these from what the user gave you. A placeholder never ships.
- **`white_flash` is a flash.** See the limit in `altocue-transitions`.

## Follow the speech

- `{ "word": "runway" }` starts on that word. Add `"occurrence": 2` for the second time it is said, `"offset": 0.2` to nudge.
- A word that was cut is an error, which is what you want.

## Rules of taste

Rules of thumb, not findings, apart from reading time.

- **The hook lands in the first frame.** No logo and no intro. Open on the peak. About 7 words, eyebrow included, in the default 2.6 seconds. Longer copy needs a longer `duration`.
- **On-screen points quote the speaker's words, in the order spoken.** Never get ahead of them.
- **One graphic at a time** in the same area. Graphics under 40% of runtime.
- **Hold text long enough to read.** QA fails a graphic that shows more than 3 words for each second on screen, the top of what Ofcom recommends for subtitles. That is a subtitle guideline, not a phone study. Lists and charts count only their longest row, because they are scanned. A format's default length does not always fit its copy, so set `duration`. Set `reading_wps` under `[qa]` for a slower audience.
- **Keep the picture moving.** `altocue facts` reports the longest static stretch. There is no target: no study shows viewers leave if nothing changes for a set time, so pace to the content.
- **End on the last useful point.** Keep the end card short.

## Timing

These hold in any look. Formats mostly follow them already, so they matter most in a composition you write.

- **Anything important stays up about 1.5 seconds,** or its reading time if that is longer. [source]
- **A count-up lasts about a second.** [source]
- **Arrivals take about half a second, and departures go faster.** [thumb]
- **Stagger related items by a few frames,** and let an item land before its check appears. [thumb]

## What belongs to the look

Sources disagree on these because they are choices, not craft. Pick per film and hold to it.

- **Overshoot.** A clean, glassy look settles with no bounce. A playful look may overshoot.
- **Movement at rest.** The camera may keep a slow drift, but held text stays sharp and still, with no shimmer. A static shot is fully still, not almost still.

## Something drawn that should feel alive

For a hand-written composition. These come from one builder's method, so treat them as [source].

- **Never teleport.** Everything travels. Nothing snaps from one drawing to another.
- **Parts turn on real pivots,** and trailing parts lag behind what leads them.
- **One main action, plus two or three small idle loops.** Keep amplitudes small, and halve them when unsure.
- **A cursor travels on arcs.** Each click gets a small press and a visible result.
- **Every frame is a pure function of time.** No clocks and no unseeded randomness, so a frame reads the same forwards and backwards.

## Check it by eye

No gate measures these yet. Look at frames from `altocue render --draft`.

- **Frame 0 is not empty,** and the opening is not flat.
- **No single-frame pops,** and no stretch that dies by accident.
- **Read it at phone size.** Scale frames to 360 pixels wide and read them without zooming. [source] One builder fails text under about 6.7 pixels at that width.
- **Cover the words.** If a plain ground is all that is left, the shot is a slide. [source]

## Brand

Point `[brand] name` at a JSON file. It only needs what differs:

```json
{
  "name": "Northlight",
  "font": { "family": "Inter", "file": "Inter-Variable.ttf" },
  "colors": { "accent": "#ffd166", "panel": "#101418", "surface": "#1a2027" },
  "caption": { "style": "bold" },
  "logo": "logo.png"
}
```

## Renderers

`hyperframes` (default, Apache-2.0) gives full motion. `basic` needs only Pillow and ffmpeg: it draws the same content with simple panels and no entrance motion. Use `--renderer basic` when Node is not available.
