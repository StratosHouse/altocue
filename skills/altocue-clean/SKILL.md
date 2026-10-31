---
name: altocue-clean
description: Cleans and masters speech audio. Removes noise and hum, shapes tone, tames sibilance, levels clips against each other, ducks music under the voice, and masters to a measured loudness and true peak. Use when the user mentions bad audio, noise, hum, wind, echo, quiet speech, loudness, LUFS, or music too loud. Part of Altocue.
license: MIT
compatibility: Requires the altocue CLI. `deep-filter` gives the best result and `altocue setup` installs it.
---

# Altocue Clean

## Steps

1. `altocue clean` writes one cleaned file per clip and `work/audio/report.json`.
2. Read the report: noise floor before and after, hum found, engine used, any warning.
3. After the render, `altocue master` applies two-pass loudness and a true-peak limit, and reports what it measured.

## Reading the report

- **A warning that denoising removed too much speech** means the original was kept. Trust it. Tell the user the source needs a better recording.
- **No hum found** is normal. Notches are only applied when hum is measured.
- **Quiet speaker next to a loud one:** set `gain` on those segments in `edl.json`.

## Targets

- Default master: -14 LUFS, -1.5 dBTP. Change them in `altocue.toml`.
- No major platform publishes a loudness number. -14 is the safest single file.

## Be honest

Clean-up cannot rescue clipped audio, heavy room echo or a voice under loud music. Say so instead of pushing the denoiser harder.
