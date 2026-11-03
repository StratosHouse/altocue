---
name: altocue-deliver
description: Turns a finished film into what a release needs: subtitles, chapters, thumbnail candidates and a version for each app, re-rendered for its shape. Use when the user wants SRT or VTT files, YouTube chapters, a thumbnail, versions for Reels, TikTok, Shorts, YouTube, LinkedIn or square, a dub, ad variants or a release pack. Part of Altocue.
license: MIT
compatibility: Requires the altocue CLI and a finished film at out/final.mp4.
---

# Altocue Deliver

The master is only ever read. Everything lands in `deliver/`, so `out/` and `edl.json` never change.

## Steps

1. Finish the film first: QA and a SHIP verdict for this exact `out/final.mp4`. See `altocue-review`. Without one, deliver refuses and writes nothing.
   - **`--override "reason"`** delivers anyway. Use it only when the user asks. The reason is printed and written into `deliver.json`.
   - **The claims and disclosure gates have no override.** Deliver measures them again, and a film that fails either one is never delivered.
2. Run it:
   ```bash
   altocue deliver --for reels,youtube
   ```
3. Read what it printed. A version that failed a gate says so, and its workspace is kept.
4. View the thumbnail sheet and each version's frames with your own file-reading tool, never by launching an app, before calling it done.

## What it makes

- **`subtitles.srt` and `subtitles.vtt`,** from the same kept words as the captions.
- **`film.mp4`,** the master with a soft subtitle track and chapter marks.
- **`chapters.txt`,** to paste into a YouTube description, from `chapters.json` or `beats.json`. YouTube needs at least three chapters of 10 seconds each, and the run says why if it writes none.
- **`thumbnails/`,** candidates from real frames, sharp and with open eyes where there is a face.
- **`deliver.json`,** the record of all of it.

## Versions for each app

- **`--for` takes:** `reels`, `tiktok`, `shorts`, `youtube`, `linkedin`, `square`. Add `--loudness` for another target.
- **A new shape is rendered, not cropped.** Each version gets its own framing and graphics. Same shape with the same layout reuses the master's picture.
- **Each version is checked** for level, length, safe zone, faces, graphics and format. Reading time and words are not rechecked. After any reframe, also look at captions and whether the product is still seen. [source]
- **Safe zones are estimates.** Most come from ad guidance, because the apps publish little for organic posts. A project can override one in `rules.json`.
- **To cut for one app from the start,** set `[output] platform`.
- **A tall take makes a tight wide version.** Captions can land on the face, and the gates will say so. A wider source is the fix, not a setting.
- **B-roll in a version gets a centre crop,** with no face awareness. Look at each insert in a new shape.

## Thumbnails and titles

Rules from single authors, so treat them as [source].

- **Use the most extreme frame.** One author found text-only covers did worst on their own channel.
- **Keep any words to two to four.** Altocue picks frames and adds no text, so words are a separate step.
- **A title never promises what the settings break.** Claim no mid-rolls only with mid-rolls off.

## Dubs

- **Text does not follow a dub.** [source] Captions, prices, currency, dates, calls to action and legal lines stay in the old language until you replace them. Replace them before reframing.
- **Translate to time, not word for word.** [source] Longer languages break the pacing.
- **Dub before caption and master.** The `dubbing` recipe needs consent from whoever is speaking. See `altocue-direct`, and read the `dubbing` entry with `altocue recipes show dubbing`.
- **Altocue does not swap text for you.** Make the change in `motion.json`, transcribe the dubbed clip, then render.

## Variants

- **Change one thing per variant,** or results cannot be compared. [source]
- **Log the test.** `altocue learn pair` is the only route past association. See `altocue-data`.

## Credits and labels

- **Give the user the credit block.** `altocue credits` prints it for the description.
- **A generated person needs its label and platform toggles** on every version. See `altocue-ugc`.
- **Publishing is the user's step.** Altocue never posts.
