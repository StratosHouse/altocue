# Licences

Altocue's own code is MIT. Nothing it needs charges a licence fee, so a team of any size can use it without buying anything. The one part that is not open source is GSAP, noted below.

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
| Geist font | SIL OFL 1.1, text in `src/altocue/data/fonts/OFL.txt` | The Geist Project Authors |
| YuNet face model | MIT, text in `src/altocue/data/models/` | OpenCV Zoo |
| Sound effects | CC0 | Kenney, see `src/altocue/data/sfx/README.md` |

## Installed by `altocue setup`, with your consent

| Dependency | Licence | Use |
|---|---|---|
| HyperFrames | Apache-2.0 | Motion graphics layers. Telemetry and update checks are switched off by Altocue |
| GSAP | [GreenSock standard licence](https://gsap.com/standard-license): free to use, not open source | Bundled inside HyperFrames. Altocue's graphics use CSS and never call it |
| Headless Chrome | Chrome for Testing, Google's terms | Downloaded by HyperFrames to draw the graphics |
| DeepFilterNet (`deep-filter`) | MIT or Apache-2.0 | Noise removal |

## Transcribers

| Engine | When | Licence |
|---|---|---|
| parakeet-mlx | Installed on Apple Silicon | Apache-2.0 |
| Parakeet TDT 0.6b v3 model | Downloaded on first use | By NVIDIA, CC-BY-4.0 |
| faster-whisper | Installed everywhere else | MIT |
| mlx-whisper | Optional | MIT |

## Hosted services

Hosted providers are off until you turn them on. Each has its own terms, and you bring your own key: fal.ai, Higgsfield, Google Gemini, ElevenLabs.

## Stock B-roll

Altocue ships no stock footage. With your own free key it can search [Pexels](https://www.pexels.com) and [Pixabay](https://pixabay.com), and you pick what comes down. Those clips stay under the [Pexels licence](https://www.pexels.com/license/) and the [Pixabay licence](https://pixabay.com/service/license-summary/). Both allow commercial use and editing without credit. Neither clears the people or brands in a clip, and neither allows selling a clip on its own. That part is yours to check.

## Video you make

ffmpeg's H.264 encoder is covered by this repo's licence only as far as the code goes. Codec patents are a separate matter.

## Names and logos

The MIT licence covers the code. It does not cover the Altocue and Stratos House names or logos, including `assets/stratos-mark.png`.
