---
name: altocue-broll
description: Finds the moments in an edit that want B-roll and fills them from the user's own clips, stock video and photos (NASA, Wikimedia Commons, The Met and Cleveland with no key, Pixabay or Pexels with one), or generated shots. Use when the user wants cutaways or overlays on a talking head, wants to hide a jump cut, or says a stretch of a reel feels static.
license: MIT
compatibility: Requires the altocue CLI. Stock works with no key through NASA, Wikimedia Commons, The Met and Cleveland. PIXABAY_API_KEY is free and is the route for generic footage. Pexels has paused new keys.
---

# Altocue B-roll

B-roll covers the picture while the voice keeps playing. Use it to show what is being said, hide a jump cut, or wake up a long static stretch. It comes after the cut, because it sits on words.

## Rules

- **Show what is said.** An insert illustrates its phrase. Never use one as decoration. Show a detail as the narrator names it, and hold it long enough to inspect. [source] Weak support: 61% of sampled interaction peaks in online course videos sat at visual transitions (Kim et al., 2014). Learners, not scrollers.
- **Meet the speaker first.** Spots start after 3 seconds. QA fails anything before 2 seconds.
- **Keep the speaker on screen.** `[broll] share` defaults to 0.3 of the runtime. QA fails anything over 0.4.
- **Real footage beats stock.** Search the user's own clips first.
- **Own footage by default.** Stock and the user's clips sit inside an edit they made. [evidence] Someone else's show, or shorts from other sites, stay reused content on YouTube even with permission. See [the platform rules](../altocue/references/platform-rules.md).
- **Look before you pick.** Open `work/broll/sheet.jpg`. Never choose from a title.
- **Get a yes before searching stock.** The search words go to the stock site, and they come from the transcript. Footage never leaves the machine.
- **Read the licence before you pick.** `work/broll-review.md` shows each option's licence and whether a credit is owed.

## Steps

1. Find the spots. Add `--library` for the user's own clips, or set `[broll] library` in `altocue.toml`. A clip matches on its file name, its folder and a `.txt` note beside it with the same name, so a folder of camera file names needs notes:
   ```bash
   altocue broll --library ~/clips/brand
   ```
2. Read `work/broll-review.md`. Each spot has its phrase, why it was chosen and what was found. Drop any that would hurt the edit.
3. Rewrite the search words. The `query` in `work/broll/spots.json` is a draft picked by word length. Change it to something a camera could film: `hands typing on a laptop`, never `pauses reads word`.
4. Search stock if the user's clips fall short:
   ```bash
   altocue broll --search auto
   ```
   `auto` tries Pixabay when `PIXABAY_API_KEY` is set, then NASA and Wikimedia Commons, then photos from The Met, Cleveland, NASA and Commons. It works on a fresh install with no key. A source that fails is skipped and named. Name one source to go straight to it, such as `--search nasa`. Add `--kind photo` or `--kind video` to narrow it. Use `--search pexels` only if the user already has a Pexels key.
5. Open `work/broll/sheet.jpg`. Every option is labelled, such as `b1 c2`.
6. Pick. Each spot takes one option, a generated shot, any file, or a skip:
   ```bash
   altocue broll --pick b1=c2 b2=skip b3=shot:shot-1 b4=file:clips/desk.mp4
   ```
7. `altocue render --draft`, then look at the frames inside each insert.

A clip must be at least as long as its spot. Altocue takes the middle of it, removes its sound, converts HDR to SDR and fills the frame. Text in a clip can sit under your captions, so check it.

A photo fills a spot too. Altocue crops it to the canvas around any faces, then makes a slow push-in of exactly the right length. It never stretches. Your own stills work the same way, with `b1=file:photo.jpg`.

## Which source suits which subject

No-key sources are strong on nature, space, science, history and art, and weak on modern people and business.

| The phrase is about | Search |
|---|---|
| Space, Earth, rockets, weather, oceans from above | `--search nasa` |
| Nature, places, animals, cities, history, old film | `--search wikimedia` |
| Paintings, prints, sculpture, the past | `--search met` or `--search cleveland` |
| Office work, hands on a laptop, people in meetings, shops, generic business | `--search pixabay` with the free key from <https://pixabay.com/api/docs/> |

Commons has 213 hits for "people walking" and NASA has 100 for "city", so if the phrase is modern and everyday, tell the user a free Pixabay key is the fix rather than forcing an odd clip.

## If you re-cut

B-roll sits on words, so a new cut invalidates it. `altocue render` stops and says so. Run `altocue broll` and pick again. Your library and any search words you wrote carry over.

## If nothing fits

Write a shot in `shots.json` (see `altocue-direct`), run it through `altocue-generate`, then pick it with `b1=shot:<id>`. Say the cost first.

## Using stock safely

A licence covers the file. It does not clear what is in the picture.

- **Check for people and brands.** The user is responsible for consent, trademarks and logos, most of all in an ad.
- **Never imply an endorsement.** A person in a stock clip has not backed the product, and NASA may never be shown as backing one.
- **Never show someone in a bad light.** Avoid sensitive subjects with identifiable people.
- **Never resell a clip on its own.** Using it inside an edit is fine.
- **Label AI-generated shots** where the platform asks.

What each source asks:

| Source | Licence | Credit |
|---|---|---|
| Pixabay, Pexels | Their own licences | None needed. Kind to give |
| The Met, Cleveland | CC0, read per object | None |
| NASA | NASA media guidelines | NASA should be acknowledged, so it lands in the credits |
| Wikimedia Commons | CC0, public domain, CC BY or CC BY-SA, read per file | CC BY and BY-SA owe one |
| Internet Archive | Unverified | Always listed |

- **Share-alike needs care.** A CC BY-SA file can require the film to carry the same licence. Prefer another option for anything sold, and tell the user if there is none.
- **The Internet Archive is opt-in.** Its licence field is set by uploaders and cannot be trusted. Its top public-domain results have included feature films still under copyright. Only use `--search archive` if the user asks, and pick from it only with `--rights-checked` after the user has checked the rights.
- **Credits are automatic.** Every download writes a record to `credits.json`, render writes `out/credits.txt`, and QA fails a film that owes a credit without that file. `altocue credits` prints the block to paste into a description. See `altocue-sound`.
