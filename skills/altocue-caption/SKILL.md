---
name: altocue-caption
description: Builds word-timed captions that highlight each word as it is spoken, never cross a cut and only show words really said. Use when the user asks for captions, subtitles or animated text on speech. Part of Altocue.
license: MIT
compatibility: Requires the altocue CLI.
---

# Altocue Caption

Captions build automatically during `altocue render`. Run `altocue caption` alone to inspect `work/captions.json`.

## How pages break

A page breaks where a phrase does, not on a clock.

- **Always:** at a cut, a change of speaker, the end of a sentence, and a silence over 0.6 seconds between two words.
- **Between those, the cheapest breaks win.** A page does not end on an article, a preposition or a possessive. A number stays with its unit, a title with its name, and a run of capitalised words stays whole. A comma is a good place to break. One stray word on a page costs, and so does a page longer than the character limit.
- **`?` and `!` stay on screen.** Commas, full stops and quotes do not.
- **The word lists are English.** Any other `[transcribe] language` still breaks on punctuation, pauses, length and balance.
- **Two lines are balanced.** Each page in `work/captions.json` carries `lines`, the count of words on each line, and both renderers draw exactly those. No line is left with one short word when the page could be split evenly.

## Settings

In `altocue.toml`:

```toml
[caption]
max_words = 0    # 0 follows the shape: 3 on a tall canvas, 4 otherwise
max_chars = 0    # 0 follows the shape: 22 tall or square, 32 wide
min_show = 0.4   # seconds a page stays up at least. 0 turns it off
```

- **`[caption] max_words` and `[caption] max_chars` win.** Left at 0, `max_words` or `max_chars` under `caption` in the brand file is used, then the shape of the canvas.
- **`combine_ms` in the brand file is still read, and its meaning changed.** It is now the longest silence between two neighbouring words on one page, 600 by default. It used to be the time from a page's first word to its last, which broke pages by how fast someone spoke.
- **Every page gets time to be seen:** `[caption] min_show`, or a twentieth of a second per character when that is longer. A page that falls short joins a neighbour in the same phrase, where the two fit in one word more than `max_words`. Otherwise it holds into the silence after it, then starts up to 0.12 seconds early. It never crosses a cut.
- **Read `short_pages` after a build.** A page that still falls short is listed there with `shown`, `needed` and `short_by`, in seconds, and `altocue caption` prints the count. No gate reads it yet, so check it yourself. A page under `min_show` is usually against a cut: keep a little more after the word, or drop the word. A longer page that is listed was simply spoken fast.

## Safe zone

- **A tall project with no platform keeps to `vertical`.** That zone is the overlap of the reels, tiktok and shorts zones, worked out from the platform table. It ends 65% of the way down, so captions sit higher than they once did. `altocue caption` and `altocue render` say so once.
- **`altocue new` writes `platform = "vertical"`** under `[output]` for any canvas taller than 4:5. Set `[output] platform` to one app to use its own zone.
- **A 4:5, square or wide canvas is unchanged,** and so is a film from a storyboard, which lays its text out in zones of its own.

## Styles

Set `caption.style` in the brand file: `clean` (default), `bold`, `boxed`, `rail`.

- **`clean`:** white with a soft shadow. Most talking heads.
- **`bold`:** uppercase and heavy. Energetic short form.
- **`boxed`:** a dark plate behind the words. Busy backgrounds.
- **`rail`:** small and quiet. Interviews and long form.

## Speakers

Run `altocue transcribe --speakers`, or give the number with `--speakers 2` when you know it, which is more reliable. It needs `uv tool install --force "altocue[speakers]"` and `altocue setup --speakers`. Then, in `altocue.toml`:

```toml
[speakers]
show = "both"    # colour, name or both
S1 = "Ada"
S2 = "Grace"
```

- **A page never mixes two speakers.** Each voice gets its own page.
- **A name shows when the voice changes,** not on every page.
- **Colours come from `caption.speaker_colours` in the brand file,** or a built-in set.
- **Check the labels before you name anyone.** A wrong name on a real person is worse than none.

## Rules

- **Fix spellings in the transcript, not the caption.** Names and product terms come from `work/words/`.
- **Never add a word that was not said.** Use a `chip` or `quote` graphic for added text.
- **Up to 3 words a page on a tall canvas, 4 on any other.** [source] One reel guide uses one to three for hooks. `[caption] max_words` covers the whole film, so a short hook-led reel suits a low number best.
- **Words reveal on the spoken beat and clear before the next page.** This is automatic: a page holds 0.28 seconds past its last word, never past the next page or a cut.
- **Check the safe zone.** App buttons cover the bottom and the right of a tall video. The `safe_zone` gate checks the same zone the layout uses.
- **Captions do not follow a dub.** See `altocue-deliver`.
- **Captions help people understand.** [evidence] Over 100 studies found better comprehension, attention and memory (Gernsbacher, 2015). That is about understanding. It is not a result about keeping viewers.
- **Spoken captions are not held to a reading gate.** [evidence] In BBC R&D's 2015 tests, about 175 words a minute felt right and about 230 too fast, and viewers accepted real clips far past the guideline when the subtitles matched the speech.
