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

`sfx` is `true` for the house sounds, `false` for none, or a list of cues to add to them. See `altocue-clean` for cues and for how music ends.

## Types

`hook`, `chip`, `lower_third`, `stat`, `quote`, `steps`, `title`, `endcard`. In copy, `*word*` takes the accent colour. In a hook, a new line is a new line of the headline.

## Follow the speech

- `{ "word": "runway" }` starts on that word. Add `"occurrence": 2` for the second time it is said, `"offset": 0.2` to nudge.
- A word that was cut is an error, which is what you want.

## Rules of taste

Rules of thumb, not findings, apart from reading time.

- **The hook lands in the first frame.** About 7 words, eyebrow included, in the default 2.6 seconds. Longer copy needs a longer `duration`.
- **On-screen points quote the speaker's words, in the order spoken.** Never get ahead of them.
- **One graphic at a time** in the same area. Graphics under 40% of runtime.
- **Hold text long enough to read.** QA fails a graphic that shows more than 3 words for each second on screen, the top of what Ofcom recommends for subtitles. Lists and charts count only their longest row, because they are scanned. A format's default length does not always fit its copy, so set `duration`. Set `reading_wps` under `[qa]` for a slower audience.
- **Keep the picture moving, about every 2 to 4 seconds after the hook,** and a cut counts. No study shows viewers leave if nothing changes that often, so pace to the content.
- **End on the last useful point.** Keep the end card short.

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
