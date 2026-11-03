---
name: altocue-film
description: Makes films from a brief and a storyboard: explainers, demo episodes, ad cards, gauge stories, case files, line-by-line reels, music cuts, plate edits, song cuts, invitations and heritage films. Use when the user wants a video made entirely in code, has no footage, or says explainer video, faceless video, demo video, ad cards, case file, or asks what kinds of film Altocue can make. For a launch video use altocue-launch instead. Part of Altocue.
license: MIT
compatibility: Requires the altocue CLI, ffmpeg, and Node 22 or later for the renderer. Generation is always optional.
---

# Altocue Film

A film here is a project driven by `brief.json` and `storyboard.json`, with no source clip, or with your own clips placed in it as Plate Edit and Song Cut do. Any type starts from a working example, so a person sees a real film first.

```bash
altocue film types
altocue film new explainer my-film
cd my-film
altocue film check
altocue film stills
altocue film render
altocue film qa
altocue qa
```

The storyboard format, scene links, camera moves and motion rules are the same for every type. `altocue-launch` explains them in full, and its concept check applies here too. Drive through to the finished film. Never stop at a storyboard.

## Which film

| The person wants | Type | Length | Generation |
|---|---|---|---|
| A product launch | `altocue-launch`, use that skill | 15 to 30 s | Optional |
| To explain something, with sources | `explainer`, Paper Explainer | 45 to 90 s | Optional |
| A walkthrough of a product or tool | `demo`, Demo Episode | 40 s to 6 min | None |
| Ads in several shapes from one offer | `ad`, Ad Card Set | 15 to 20 s, plus stills | Optional |
| A mechanism told by one changing number | `gauge`, Gauge Story | 30 to 90 s | Optional |
| A real or invented event, told like a log | `case`, Case File | 30 s to 3 min | Optional |
| A voiced story with a picture per line | `line`, Line-by-Line Reel | 30 s to 3 min | Optional |
| A film cut to a song | `music`, Music Cut | 20 to 60 s | None |
| One change to your own clip, comped back | `plate`, Plate Edit | The plate's length | Optional |
| Your own footage cut to a song | `song`, Song Cut | 30 to 60 s | None |
| An event film and its card | `invite`, Invitation | 25 to 30 s | None |
| An anniversary told across its eras | `heritage`, Heritage Film | 30 s to 3 min | None |

## Real footage inside a film

A film can hold real pictures. Add `{"type": "footage", "file": "assets/take.mp4", "zone": "phone_a", "label": "Raw"}` to a scene. The zones `phone`, `phone_a` and `phone_b` keep a vertical clip upright at every aspect. Pictures stay muted. To hear a take, set `"voice"` on the scene, and `"voice_offset"` if the picture starts early.

Add `"captions": true` to a footage item to caption what it says. Altocue reads the clip's own words once, keeps them until the file changes, and sets word-timed captions across the foot of that picture, sized to its width so they read on a phone. `film stills` measures them with everything else. Read the words a clip's captions will show: if the transcriber mishears a name, write `"captions": {"spell": {"Heard": "Name"}}` so the caption spells it as written. A person still has to listen, because a misheard name can mean a voice that says it unclearly.

For a split screen, presenter in one half and graphics in the other, see [the notes by type](references/types.md#split-screen).

Keep your own clips in `assets/` and generated ones in `generated/`, so either can be swapped without touching the motion.

## A hard length

Set `"cap_seconds": 90` in `brief.json` when a length is a limit and not a guide. `film check` fails past it, and the render refuses any file that runs over.

## Numbers on screen

A claim on screen must be true of what shipped. Read numbers from the tool when the storyboard is built, such as `altocue library stats`, and never type them from memory.

When a script builds the storyboard, write each number as a token such as `{live:formats}` and fill it with `altocue.live.fill`. That writes `live.json`, which records where every number landed and what it was read from: a count of what ships (`formats`, `groups.<id>`, `library`, `gates`, `looks`), another film's QA or review, or a measurement of a file. `film check` reads them all again and fails a film whose numbers have moved, so a stale number can never render.

## The sound off

Most people watch with the sound off. Once the voice stops, carry the story with a `say_line` in the `foot` zone of each scene: the line a narrator would say, in under ten words, true of the picture. Keep the cards above the foot, with `scale` if a card reaches it. For a hook, set `"gap"` and `"lead"` on a `word_slam` so the first word is on screen in the first frame and the line has landed by about two seconds.

## Voice comes first when there is voice

For `explainer` and `line`, record or generate the voice before animating. Put each take in `assets/voice/<scene>.wav`, and set `"seconds": "voice"` with `"voice"` and `"says"` on the scene. The timeline is built from the real durations, then you animate to it. Until a take exists, `film check` estimates from the word count, and that estimate is only for planning.

Narration runs about 2.5 to 2.8 words a second, so 30 seconds holds about 80 words.

## Notes by type

Read [the notes by type](references/types.md) before writing a storyboard. They cover each type's craft, the checks a type carries for itself, the footage and intake types, and one-take films.

## These need real footage

They are not code-only, so do not pretend they are. They run on the normal editing stages.

- **Reference Reel.** Your clips in the style of a reel you admire. Measure the reel with `altocue film study reference.mp4`, which writes a style card of pacing, palette, loudness and on-screen text. Then edit with `altocue new`, `cut`, `caption` and `motion`, following the card. See `altocue`.
- **Clip Cut.** One statement from a long recording as a short. Use `altocue clips`, then `frame`, `caption` and `master`. Stick to the user's own recordings.
- **Product Proof Spot.** An ad cut from real product footage, with generated shots only where filming is impossible. See `altocue-direct` and `altocue-generate`. Write the real label's lines in `product.json`, such as `{"name": "Hedgerow jam", "label": ["Blackberry 55%", "300 g"]}`, and run `altocue qa --check proof`: it fails a label line misread or changed, the product's words cut by the frame's edge, and a hand in the final hold. A generated angle or stunt is a claim, never proof.

## Style cards

`altocue film study <video>` measures a reference: cut points, average shot length, palette, loudness and on-screen text. Point the storyboard at it with `"style_card": "style-card.json"`, and `film check` compares your pacing against it.

Learn pacing and structure from references. Only recreate work you own or have rights to. A style card holds numbers, never pixels.

## What Altocue will not make, and why

- **AI host channels and AI persona experts.** YouTube's monetisation policy says channels using an AI persona as a human expert on health, legal issues, finances or politics cannot earn from it. It is also a poor thing to do to viewers.
- **Mass-produced channels.** YouTube's rules on repetitive and mass-produced content are judged across the channel. A format is fine, but videos that feel interchangeable, or characters repeating the same situation and outcome, are not. Give each video a distinct idea. No gate can certify a channel, so warn the person.
- **Compilations of other people's clips.** YouTube's reused content rule applies even with permission, and copyright applies separately.
- **Faked reviews, proof or results.** A generated person may not claim experience. See `altocue-ugc`.
- **Generated-only story films.** Story trailers, pitch cartoons and mechanism shorts need paid generation throughout. See `altocue-direct` and `altocue-generate`, and quote the cost first.

Ambient loops and one-take multi-cam films are also not film types here yet. One-take seams are checked, as the notes by type say, and the angle schedule has not been built.
