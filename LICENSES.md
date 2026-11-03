# Licences

Altocue's own code is MIT. Nothing it needs charges a licence fee, so a team of any size can use it without buying anything. Two things `altocue setup` installs are free to use but not open source: GSAP, inside HyperFrames, and headless Chrome for Testing, under Google's terms. Both are noted below.

## Required

| Dependency | Licence | Use |
|---|---|---|
| ffmpeg | LGPL or GPL, depending on your build. You install it | Every video and audio operation |
| NumPy | BSD-3-Clause | Maths |
| Pillow | MIT-CMU | Text and contact sheets |
| OpenCV (headless) | Apache-2.0 | Face detection |

## Bundled in this repo

| Asset | Licence | Source |
|---|---|---|
| Geist and Geist Mono fonts | SIL OFL 1.1, text in `src/altocue/data/fonts/OFL.txt` | The Geist Project Authors |
| Caveat font | SIL OFL 1.1, text in `src/altocue/data/fonts/OFL-Caveat.txt` | The Caveat Project Authors |
| Instrument Serif font | SIL OFL 1.1, text in `src/altocue/data/fonts/OFL-InstrumentSerif.txt` | The Instrument Serif Project Authors |
| YuNet face model | MIT, text in `src/altocue/data/models/` | OpenCV Zoo |
| Sound effects: Interface Sounds, UI Audio, Impact Sounds, Digital Audio, Sci-fi Sounds, RPG Audio | CC0 1.0 | Kenney, in `src/altocue/data/sfx/`. See its `README.md` |
| Music stings: Music Jingles | CC0 1.0 | Kenney, in `src/altocue/data/music/`. See its `README.md` |
| Whooshes 1795, 1796, 1800 and 1801 | CC0 1.0 | BigSoundBank, by Joseph Sardin, in `src/altocue/data/sfx/` |
| 12 shader transitions | MIT, text in `src/altocue/data/transitions/LICENSE` | gl-transitions contributors, in `src/altocue/data/transitions/`, each file's header kept |
| Tabler Icons, 16 outline icons for feature badges | MIT, text in `src/altocue/data/icons/LICENSE` | Copyright 2020-2025 Paweł Kuna, version 3.35.0, in `src/altocue/data/icons/`. See its `README.md` |
| Country and land outlines | Public domain shapes. The world-atlas packaging is ISC, text in `src/altocue/data/maps/LICENSE` | Natural Earth 1:110m, packaged by world-atlas, copyright 2013-2019 Michael Bostock, in `src/altocue/data/maps/` |

- **Kenney is credited by name only, never the logo.** Credit is optional under CC0, and we give it.
- **BigSoundBank warns that third parties can file wrong Content ID claims on its CC0 sounds.** Its page gives a dispute link.
- **The shader notice.** Copyright (c) 2017-present gl-transitions contributors. Named authors in the twelve headers: rectalogic, mikolalysenko, Anastasia Dunbar, gre, Gunnar Roth, 0gust1, Sergey Kosarevsky, pschroen and Ben Zhang. `gl-luminance-melt` also carries code by Ian McEwan and Ashima Arts, under MIT.
- **Looks, overlays, film backdrops, formats, the film score and the two bundled compositions are original code.** The compositions are `ascii-tunnel` and `particle-flow`, in `src/altocue/data/compositions/`. Nothing third party is bundled for any of them. The badge icons are Tabler's, as above, apart from `waveform` and `clip-cut`, which are drawn in our own code.
- **The review page embeds Geist,** which is already bundled above.
- **The demo take is ours.** `altocue demo` downloads it at run time. It is not in the repo.

## Adapted code

| Code | Licence | Where |
|---|---|---|
| Plan checks from `check_timeline.py` in win4r/video-director | MIT, copyright 2026 win4r | `src/altocue/storyboard.py`, notice kept in the file |
| Film QA checks from `qa_video.py` in win4r/video-director | MIT, copyright 2026 win4r | `src/altocue/qa/film.py`, notice kept in the file |

## Installed by `altocue setup`, with your consent

| Dependency | Licence | Use |
|---|---|---|
| HyperFrames | Apache-2.0 | Motion graphics layers, films and the timeline view. Telemetry and update checks are switched off by Altocue. Its undici is held at a patched release (MIT) |
| GSAP | [GreenSock standard licence](https://gsap.com/standard-license): free to use, not open source | Bundled inside HyperFrames. Altocue's own formats use CSS and never call it. Items fetched with `altocue library add` usually do, and the command says so |
| Headless Chrome | Chrome for Testing, Google's terms | Downloaded by HyperFrames to draw the graphics |
| DeepFilterNet (`deep-filter`) | MIT or Apache-2.0 | Noise removal where the Mac has no voice isolation, or on request |

## Part of your system, not bundled

| Component | Licence | Use |
|---|---|---|
| Apple voice isolation (AUSoundIsolation) | macOS system audio unit | Noise removal on a Mac. Our own Swift helper calls it |

## Optional, only when you ask

| Dependency | Licence | Use |
|---|---|---|
| sherpa-onnx | Apache-2.0 | Tells speakers apart. Install with `uv tool install --force "altocue[speakers]"` |
| pyannote segmentation-3.0 model | MIT, copyright CNRS, licence text kept beside the model | Fetched by `altocue setup --speakers` |
| 3D-Speaker CAM++ model, zh-cn 16k common | Apache-2.0, per its model card. Trained on about 200,000 Chinese speakers, in a set the card does not name | Fetched by `altocue setup --speakers` |

The English CAM++ model is left out on purpose: its training set, VoxCeleb, is for research only.

## Fetched into a project by `altocue library add`

| Items | Licence | How |
|---|---|---|
| HyperFrames registry items | Apache-2.0, some also MIT or OFL-1.1 | Fetched per project into `motion/fetched/` with their licence files. Never shipped |
| Libraries those items load | Their own: three.js MIT, D3 ISC and others | Saved per project so a render needs no network. Listed in `library.lock.json` |
| Web fonts an item names | Each under its own open font licence, mostly SIL OFL 1.1 | Embedded in the fetched item, cut to Latin text, so its renders need no network |
| GL Transitions shaders | MIT, BSD-2-Clause or BSD-3-Clause, per file | Twelve ship with Altocue, as above. Any other comes from the pinned gl-transitions package with its header. A shader that needs a texture input is refused |

The library catalog lists names, authors, licences and links. Excluded sources stay listed with their reason, and nothing from them is indexed or fetched.

## Transcribers

| Engine | When | Licence |
|---|---|---|
| parakeet-mlx | Installed on Apple Silicon | Apache-2.0 |
| Parakeet TDT 0.6b v3 model | Downloaded on first use | By NVIDIA, CC-BY-4.0 |
| faster-whisper | Installed everywhere else | MIT |
| mlx-whisper | Optional | MIT |

## Hosted services

Hosted providers are off until you turn them on. Each has its own terms, and you bring your own key: fal.ai, Higgsfield, Google Gemini, ElevenLabs, 1of10, Spyglass and the YouTube Data API.

- **Recipe descriptions in `src/altocue/data/recipes.toml` are our own words.** No Higgsfield prompt, script or skill text is included.

## Stock video and photos

Altocue ships no stock footage. It searches, shows the licence and credit, and you pick what comes down. Every download is recorded with its source and licence.

- **NASA:** no key. NASA's media guidelines. Its API gives no licence field, so third-party material is screened by its words.
- **Wikimedia Commons:** no key. Each file's own licence is read. Only CC0, public domain, CC BY and CC BY-SA files come through.
- **The Met and the Cleveland Museum of Art:** no key. Only works each museum marks CC0.
- **Pixabay:** a free key. The [Pixabay licence](https://pixabay.com/service/license-summary/).
- **Pexels:** an existing key, because Pexels has paused new ones. The [Pexels licence](https://www.pexels.com/license/).
- **Internet Archive:** opt-in only. Uploaders set its licence field, so nothing from it counts as cleared until you check the rights yourself.

Pixabay and Pexels allow commercial use and editing without credit. No source clears the people or brands in a clip, and neither Pixabay nor Pexels allows selling a clip on its own. That part is yours to check.

## Music and sound effects

- **Bundled:** the CC0 effects and stings above.
- **ccMixter:** no key. Public-domain and CC BY tracks only, read per track.
- **Incompetech:** no key. Kevin MacLeod's library under CC BY 4.0, with the credit he asks for.
- **Freesound:** your own key. CC0 and CC BY sounds only.

Render writes `out/credits.txt`, and QA fails a film that owes a credit without it.

## Video you make

ffmpeg's H.264 encoder is covered by this repo's licence only as far as the code goes. Codec patents are a separate matter.

## Names and logos

The MIT licence covers the code. It does not cover the Altocue and Stratos House names or logos, including `assets/stratos-mark.png`.
