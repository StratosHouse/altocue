---
name: altocue-clean
description: Cleans and masters speech audio. Removes noise and hum, shapes tone, tames sibilance, levels each phrase, turns breaths down, fills holes with room tone, ducks music under the voice, and masters to a measured loudness and true peak. Use when the user mentions bad audio, noise, hum, wind, echo, quiet speech, a line that drops away, breaths, sibilance, dead air at a cut, loudness, LUFS, music too loud, or an effect that needs placing. Part of Altocue.
license: MIT
compatibility: Requires the altocue CLI. On macOS 13 or later with the Xcode command line tools, voice isolation needs no download. Elsewhere `altocue setup` installs `deep-filter`.
---

# Altocue Clean

## Steps

1. `altocue doctor` names the denoiser this machine will use.
2. `altocue clean` writes one cleaned file per clip and `work/audio/report.json`.
3. Read the report: noise floor before and after, hum found, engine used, phrases, loudness range before and after, any warning.
4. After the render, `altocue master` applies two-pass loudness and a true-peak limit, and reports what it measured.

## Which denoiser

- **`apple`, the default on a Mac that has it.** Apple's voice isolation, built into macOS. No download.
- **`deep-filter`, the default elsewhere.** `altocue setup` installs it. On a Mac, `altocue setup --deep-filter` fetches it too.
- **`afftdn`, when neither exists.** Stock ffmpeg, and the plainest of the three.
- **Why Apple leads.** On the demo speech mixed with synthetic noise and hum, it left more of the voice against the original in five cases of six, and took the noise further in all six. deep-filter moved the colour of the voice a little less. Synthetic noise is not a real room, and nobody has listened. If a voice sounds wrong, try `altocue clean --force --denoiser deep-filter`, and let a person choose.

## Reading the report

- **A warning that denoising removed too much speech** means the original was kept. Trust it. Tell the user the source needs a better recording.
- **Apple's unit removes what it does not hear as a voice.** A synthetic voice can vanish. The guard catches that and hands over to the next engine, and the warning says so.
- **No hum found** is normal. Notches are only applied when hum is measured.
- **`range_before` and `range_after`** are the loudness range in LU. Long pauses count, so an uncut take can read wider after levelling. Judge it on the cut film.

## Phrases, breaths and sibilance

- **Phrase levelling is on.** Each phrase is brought toward the clip's level, by at most 9 dB up and 6 dB down. A noisy room gets less boost, because the room rises with the voice.
- **Pauses are left exactly alone,** so room tone and breaths are not pumped.
- **Only one part of a clip wrong, or a quiet speaker beside a loud one:** set `gain` on those segments in `edl.json`, after the cut is final.
- **Breaths stay unless asked.** `altocue clean --breaths 9` turns them down 9 dB. They are never cut. The finder has not been checked against labelled breaths, so listen.
- **De-essing has four settings:** `off`, `light`, `normal`, `strong`, through `--deess`. Light is the default. Too much makes a lisp, so go up one step at a time.

Set these in `altocue.toml` under `[audio]`: `denoiser`, `level_phrases`, `deess`, `breaths`, `room_tone`.

## Room tone

- **On by default.** Anywhere the voice falls under the clip's own room, its room fills the hole. A clip with no sound, such as a screen recording, borrows from its neighbour, and the room runs on under an end card.
- **Speech is untouched.** Nothing is added where the voice holds up.
- **A clip with no quiet stretch has no room to give.** The report's `room` lists what was found.

## Effects

- **Every bundled effect can be called.** `altocue sfx list` names them. In `motion.json`:

```json
"sfx": [{ "name": "whoosh", "at": { "word": "next" } }]
```

- **`at` takes seconds or a word,** with `occurrence` and `offset` as for graphics.
- **A list adds to the house sounds.** `"sfx": false` turns them off. For only your own, use `"sfx": { "house": false, "cues": [...] }`.
- **`"file": "sounds/door.wav"`** in place of `name` uses a project file.
- **Each sound has a row in `sounds.json`:** `align`, `offset_ms`, `min_gap_ms`, `bed_db`, `dip_db`. A cue can override any of them. `"align": "peak"` puts the loudest moment on the time. A sound repeated too soon is dropped, and the mix report lists it under `skipped`.
- **Wipes sound at their midpoint,** because that is where the cut lands.
- **Accent three to five moments that matter,** and let the music cover the other cuts. [source] One set of measured films had a hit on each cut, another found sound on cuts no better than chance, so a hit on every cut is not the rule.

## Music

- **Ducking is automatic.** The music is down before the first word lands, stays down through a breath, and comes back slowly.
- **`"music": { "path": "...", "end": "land" }` is the default.** The track is backtimed so its own end falls on the last frame. Silence at the end of the file does not count.
- **`"end": "fade"`** starts at the top of the track and fades out.
- **`"end": "own"`** is for a score that ends itself: no loop, no backtiming, no fade.
- **A track shorter than the film loops,** blended at each join. The joins are not on a bar, so listen to them.
- **Check each cue against the speech.** [source] The whole track is levelled once, so a quiet passage can vanish under the voice. You cannot hear this, so name the moments for the user to check.

## No voice

- **A film with no voice has nothing to level against,** so the music leads. A silent voice track is enough to trigger this.
- **Effects sit over the music.** A cue's `over_db` sets how far, 0 to 14. `under_db` is refused here, because it means nothing without a voice.
- **A big hit gets a dip.** `dip_db` draws the music down 6 to 12 dB in the 0.4 s before it. boom, punch, sub and clang have one in the table.
- **Fades are tight:** 0.03 s in and 0.27 s out.
- **These numbers are starting points,** taken from one project's measurements of its own films. Nobody has listened to them here.

## Targets

- **Default master: -14 LUFS, -1.5 dBTP.** Change them in `altocue.toml`, or name a target: `altocue master --loudness podcast`.
- **-14 is a house default.** No short-video platform publishes a loudness number. Spotify, Apple Podcasts and EBU R 128 publish theirs, and naming one prints its source.

## Be honest

Clean-up cannot rescue clipped audio, heavy room echo or a voice under loud music. Say so instead of pushing the denoiser harder. Numbers show what changed, not whether it sounds better. A person has to listen.
