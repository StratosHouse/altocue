---
name: altocue-broll
description: Finds the moments in an edit that want B-roll and fills them from the user's own clips, free stock from Pixabay or Pexels, or generated shots. Use when the user wants cutaways or overlays on a talking head, wants to hide a jump cut, or says a stretch of a reel feels static.
license: MIT
compatibility: Requires the altocue CLI. PIXABAY_API_KEY or PEXELS_API_KEY for stock. Both are free, and Pexels has paused new keys.
---

# Altocue B-roll

B-roll covers the picture while the voice keeps playing. Use it to show what is being said, hide a jump cut, or wake up a long static stretch. It comes after the cut, because it sits on words.

## Rules

- **Show what is said.** An insert illustrates its phrase. Never use one as decoration.
- **Meet the speaker first.** Spots start after 3 seconds. QA fails anything before 2 seconds.
- **Keep the speaker on screen.** `[broll] share` defaults to 0.3 of the runtime. QA fails anything over 0.4.
- **Real footage beats stock.** Search the user's own clips first.
- **Look before you pick.** Open `work/broll/sheet.jpg`. Never choose from a title.
- **Get a yes before searching stock.** The search words go to Pexels or Pixabay, and they come from the transcript. Footage never leaves the machine.

## Steps

1. Find the spots. Add `--library` for the user's own clips, or set `[broll] library` in `altocue.toml`. A clip matches on its file name, its folder and a `.txt` note beside it with the same name, so a folder of camera file names needs notes:
   ```bash
   altocue broll --library ~/clips/brand
   ```
2. Read `work/broll-review.md`. Each spot has its phrase, why it was chosen and what was found. Drop any that would hurt the edit.
3. Rewrite the search words. The `query` in `work/broll/spots.json` is a draft picked by word length. Change it to something a camera could film: `hands typing on a laptop`, never `pauses reads word`.
4. Search stock if the user's clips fall short:
   ```bash
   altocue broll --search pixabay
   ```
   Use `--search pexels` only if the user already has a Pexels key. Pexels has paused new ones.
5. Open `work/broll/sheet.jpg`. Every option is labelled, such as `b1 c2`.
6. Pick. Each spot takes one option, a generated shot, any file, or a skip:
   ```bash
   altocue broll --pick b1=c2 b2=skip b3=shot:shot-1 b4=file:clips/desk.mp4
   ```
7. `altocue render --draft`, then look at the frames inside each insert.

A clip must be at least as long as its spot. Altocue takes the middle of it, removes its sound, converts HDR to SDR and fills the frame. Text in a clip can sit under your captions, so check it.

## If you re-cut

B-roll sits on words, so a new cut invalidates it. `altocue render` stops and says so. Run `altocue broll` and pick again. Your library and any search words you wrote carry over.

## If nothing fits

Write a shot in `shots.json` (see `altocue-direct`), run it through `altocue-generate`, then pick it with `b1=shot:<id>`. Say the cost first.

## Using stock safely

Pexels and Pixabay clips are free for commercial use, can be edited, and need no credit. The licence does not clear what is in the picture.

- **Check for people and brands.** The user is responsible for consent, trademarks and logos, most of all in an ad.
- **Never imply an endorsement.** A person in a stock clip has not backed the product.
- **Never show someone in a bad light.** Avoid sensitive subjects with identifiable people.
- **Never resell a clip on its own.** Using it inside an edit is fine.

Credits land in `out/broll-credits.md`. Giving them is optional. Label AI-generated shots where the platform asks.
