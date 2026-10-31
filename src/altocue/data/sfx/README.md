# Sound effects

Eight short effects, named by what they are for. Mono, 48 kHz Ogg Opus, each under 40 KB.

| File | Use | Source file | Pack |
|---|---|---|---|
| `pop.ogg` | An element appears | `drop_002.ogg` | Interface Sounds |
| `click.ogg` | A small, dry hit | `click3.ogg` | UI Audio |
| `tick.ogg` | A counter or a list step | `tick_002.ogg` | Interface Sounds |
| `whoosh.ogg` | A noise sweep for a transition | `open_003.ogg` | Interface Sounds |
| `rise.ogg` | A short pitch rise | `maximize_001.ogg` | Interface Sounds |
| `fall.ogg` | A short pitch fall | `minimize_001.ogg` | Interface Sounds |
| `thud.ogg` | A low hit | `error_005.ogg` | Interface Sounds |
| `ding.ogg` | A bright single note | `glass_001.ogg` | Interface Sounds |

## Source and licence

- **Author:** Kenney, [kenney.nl](https://kenney.nl).
- **Packs:** [Interface Sounds](https://kenney.nl/assets/interface-sounds) 1.0 and [UI Audio](https://kenney.nl/assets/ui-audio).
- **Licence:** Creative Commons Zero, CC0 1.0. See [the licence text](https://creativecommons.org/publicdomain/zero/1.0/). Both packs state this in their `License.txt`. Credit is optional, and we give it gladly.
- **Downloaded:** 2026-10-30, from kenney.nl.

## What was changed

- **Decoded** from Ogg Vorbis and mixed to mono at 48 kHz, then encoded as Ogg Opus at 96 kb/s. Every `ffmpeg` can decode it.
- **Trimmed** so the sound starts on the first sample, which keeps placement exact.
- **Levelled** to a -3 dBFS peak, with a 5 ms fade at the end. `altocue.mix` sets the real level against the voice.
