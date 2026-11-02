# Sound effects

Twenty short effects, named by what they are for. Mono, 48 kHz Ogg Opus, each under 40 KB. `sounds.json` holds the same table for `altocue sfx`.

Nobody has listened to these. The newer twelve were chosen by file name and measured length, so hear them before you ship anything.

| File | Use | Length | Source file | Pack |
|---|---|---:|---|---|
| `pop.ogg` | An element appears | 0.09 s | `drop_002.ogg` | [Interface Sounds](https://kenney.nl/assets/interface-sounds) |
| `click.ogg` | A small, dry hit | 0.06 s | `click3.ogg` | [UI Audio](https://kenney.nl/assets/ui-audio) |
| `tick.ogg` | A counter or a list step | 0.03 s | `tick_002.ogg` | [Interface Sounds](https://kenney.nl/assets/interface-sounds) |
| `whoosh.ogg` | A noise sweep for a transition | 0.31 s | `open_003.ogg` | [Interface Sounds](https://kenney.nl/assets/interface-sounds) |
| `rise.ogg` | A short pitch rise | 0.26 s | `maximize_001.ogg` | [Interface Sounds](https://kenney.nl/assets/interface-sounds) |
| `fall.ogg` | A short pitch fall | 0.26 s | `minimize_001.ogg` | [Interface Sounds](https://kenney.nl/assets/interface-sounds) |
| `thud.ogg` | A low hit | 0.14 s | `error_005.ogg` | [Interface Sounds](https://kenney.nl/assets/interface-sounds) |
| `ding.ogg` | A bright single note | 0.19 s | `glass_001.ogg` | [Interface Sounds](https://kenney.nl/assets/interface-sounds) |
| `punch.ogg` | A heavy body hit for a big reveal | 0.46 s | `impactPunch_heavy_002.ogg` | [Impact Sounds](https://kenney.nl/assets/impact-sounds) |
| `clang.ogg` | A metal hit | 0.36 s | `impactMetal_heavy_001.ogg` | [Impact Sounds](https://kenney.nl/assets/impact-sounds) |
| `bell.ogg` | A struck bell | 0.66 s | `impactBell_heavy_003.ogg` | [Impact Sounds](https://kenney.nl/assets/impact-sounds) |
| `chime.ogg` | A three-note digital sign | 0.77 s | `threeTone1.ogg` | [Digital Audio](https://kenney.nl/assets/digital-audio) |
| `powerup.ogg` | A short rising digital sweep | 0.37 s | `powerUp5.ogg` | [Digital Audio](https://kenney.nl/assets/digital-audio) |
| `boom.ogg` | A crunching explosion | 0.78 s | `explosionCrunch_000.ogg` | [Sci-fi Sounds](https://kenney.nl/assets/sci-fi-sounds) |
| `sub.ogg` | A low-frequency drop | 1.01 s | `lowFrequency_explosion_001.ogg` | [Sci-fi Sounds](https://kenney.nl/assets/sci-fi-sounds) |
| `page.ogg` | A page turning | 0.44 s | `bookFlip2.ogg` | [RPG Audio](https://kenney.nl/assets/rpg-audio) |
| `whoosh3.ogg` | A recorded whoosh for a transition | 0.83 s | `1795.ogg` | [BigSoundBank](https://bigsoundbank.com/whoosh-3-s1795.html) |
| `whoosh4.ogg` | A recorded whoosh for a transition | 0.89 s | `1796.ogg` | [BigSoundBank](https://bigsoundbank.com/whoosh-4-s1796.html) |
| `whoosh7.ogg` | A recorded whoosh for a transition | 0.73 s | `1800.ogg` | [BigSoundBank](https://bigsoundbank.com/whoosh-7-s1800.html) |
| `whoosh8.ogg` | A recorded whoosh for a transition | 0.63 s | `1801.ogg` | [BigSoundBank](https://bigsoundbank.com/whoosh-8-s1801.html) |

## Source and licence

- **Kenney:** [kenney.nl](https://kenney.nl). Packs: [Interface Sounds](https://kenney.nl/assets/interface-sounds) 1.0, [UI Audio](https://kenney.nl/assets/ui-audio), [Impact Sounds](https://kenney.nl/assets/impact-sounds), [Digital Audio](https://kenney.nl/assets/digital-audio), [Sci-fi Sounds](https://kenney.nl/assets/sci-fi-sounds) and [RPG Audio](https://kenney.nl/assets/rpg-audio).
- **Kenney licence:** Creative Commons Zero, CC0 1.0. See [the licence text](https://creativecommons.org/publicdomain/zero/1.0/). Each pack states this in its `License.txt`, and each pack page says so too. Credit is optional, and we give it gladly. The name only, never the logo.
- **BigSoundBank:** Joseph Sardin, [bigsoundbank.com](https://bigsoundbank.com). Each of the four whoosh pages states "CC0 (public domain)", and the [licences page](https://bigsoundbank.com/licenses.html) covers CC0, WTFPL and a public domain dedication.
- **A Content ID warning:** BigSoundBank warns that third parties can file wrong Content ID claims on its CC0 sounds, and gives a dispute link on that page.
- **Downloaded:** the first eight on 2026-10-30, the other twelve on 2026-11-02.

## What was changed

- **Decoded** and mixed to mono at 48 kHz, then encoded as Ogg Opus at 96 kb/s. Every `ffmpeg` can decode it.
- **Trimmed** so the sound starts on the first sample, which keeps placement exact.
- **Levelled** to a -3 dBFS peak, with a 5 ms fade at the end. `altocue.mix` sets the real level against the voice.
