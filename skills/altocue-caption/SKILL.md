---
name: altocue-caption
description: Builds word-timed captions that highlight each word as it is spoken, never cross a cut and only show words really said. Use when the user asks for captions, subtitles or animated text on speech. Part of Altocue.
license: MIT
compatibility: Requires the altocue CLI.
---

# Altocue Caption

Captions build automatically during `altocue render`. Run `altocue caption` alone to inspect `work/captions.json`.

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
- **Up to 4 words a page.** Set `max_words` under `caption` in the brand file. [source] One reel guide uses one to three for hooks. The setting covers the whole film, so a short hook-led reel suits it best.
- **Words reveal on the spoken beat and clear before the next page.** This is automatic: a page holds 0.28 seconds past its last word, never past the next page or a cut.
- **Check the safe zone.** App buttons cover the bottom and the right of a tall video. QA checks this.
- **Captions do not follow a dub.** See `altocue-deliver`.
- **Captions help people understand.** [evidence] Over 100 studies found better comprehension, attention and memory (Gernsbacher, 2015). That is about understanding. It is not a result about keeping viewers.
- **Spoken captions are not held to a reading gate.** [evidence] In BBC R&D's 2015 tests, about 175 words a minute felt right and about 230 too fast, and viewers accepted real clips far past the guideline when the subtitles matched the speech.
