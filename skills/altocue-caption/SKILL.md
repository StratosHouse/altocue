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

Run `altocue transcribe --speakers`, or give the number with `--speakers 2` when you know it, which is more reliable. It needs `pip install "altocue[speakers]"` and `altocue setup --speakers`. Then, in `altocue.toml`:

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
- **Up to 4 words a page.** Fewer at moments that matter.
- **Check the safe zone.** App buttons cover the bottom and the right of a tall video. QA checks this.
