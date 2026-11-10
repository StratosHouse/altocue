# Glass kit (optional example)

A light, glassy Shorts look, shipped as an example of a `[shorts] kit`. It is one studio's house look: Stratos's
light Reels. It is never the default. Altocue reads your own brand kit first (`altocue brand ingest`), and this kit
is only used when you point `[shorts] kit` at it.

What it draws: a light canvas with your brand row (logo, name, Reel label), a Geist headline top-left, frosted glass
cards and glass objects in the top band, the caption centred between graphics and presenter, the speaker in a
rounded aperture in the bottom half, a full light-canvas graphic over every moment the speaker looks off the lens, and
an end card that asks viewers to comment a keyword.

It builds on Shorts Studio's composition runtime (`window.kit`), so it runs inside a Shorts Studio run after the
measure step and a first scaffold, the same place `altocue shorts prepare` hands over to.

## Use it

1. Copy this folder somewhere of your own, such as `kits/glass/` in your project.
2. Make `house.json` yours: `brand.name`, `brand.logo` (put your logo in `brand/`), the six `colors`, and the
   `typing_sounds` names from your sound pack. `layouts`, `silent_face` and `grain_opacity` are house rules that
   `altocue shorts check` holds the run to.
3. Point the project at it: `[shorts] kit = "kits/glass"` in `altocue.toml`, or pass `--kit` to
   `altocue shorts prepare`. The run gets `glass_kit.py` and `house.json` beside its `build.py`, and `glass/` and
   `brand/` in `media/`.
4. Write `build.py` in the run folder (the docstring at the top of `glass_kit.py` has a short example) and run it.
   `r.require()` reads the look-away covers from `altocue-shorts.json`, which come from `altocue shorts gaze`, and the
   build stops if any look-away is left without a full graphic over it.
5. While you build, set `ALTOCUE_KIT_LINT=1`. The composition then reports layout problems in the console: text
   outside x 56 to 1000, text below y 1500 in a full graphic, a card outside x 40 to 1010, or a glass object covering
   more than 15% of any text. Unset it for the final render, then run `altocue shorts check`.

With a cutout of the speaker (`media/<take>-cutout.webm` with alpha, from your own matting step), the head breaks above
the aperture. Without one, the take fills the aperture.

## Glass objects

Place one with `r.ob(name, cls, style)`, or several with `r.tiles([...])` and `r.deck([...])`.

| Name | Object | Name | Object |
|---|---|---|---|
| `bell` | reception desk bell | `key` | key |
| `button` | button | `laptop` | open laptop |
| `cable` | cable with two plugs | `megaphone` | megaphone |
| `calendar` | desk calendar block | `mic` | microphone with sound bars |
| `camera` | cinema camera | `phone` | smartphone |
| `chat` | chat bubble | `prism` | prism splitting light |
| `check` | check mark | `scissors` | scissors |
| `clapper` | clapperboard | `search` | search |
| `cloud` | cloud with a server inside | `shield` | shield with check mark |
| `coins` | stack of plain coins | `system` | system |
| `connect` | connection | `timeline` | editing timeline |
| `docs` | documents | `toggle` | toggle switch |
| `gavel` | gavel | `tower` | desktop computer tower |
| `headset` | call centre headset | `id` | ID card |

To add one that matches the set, open every prompt with the same sentence and name one object:

> Premium 3D glass object (*object*), frosted and clear glass, mint glow, emerald accents, transparent background. No text, logos or people.

Reserve it in the Reel's project first with `altocue generate --reserve`, and register it after with
`altocue generate --import`, so the spend and the source are on record.

## Provenance and licence

Every image in `glass/` is AI-generated: made with Higgsfield (model `seedream_5_5`) on 10 November 2026, then scaled
and saved as WebP for this kit. `provenance.json` lists each file with its SHA-256, prompt, model and date. Keep the
AI label wherever you use them, and follow
[Higgsfield's terms of use](https://higgsfield.ai/terms-of-use-agreement), which among other things bar using the
images to train or fine-tune AI models.

`brand/stratos-logo.png` is the Stratos mark. It is here so the example renders as shipped. Use your own logo in your
copy, and do not use the Stratos mark as yours.

The code in this folder is MIT, like the rest of Altocue.
