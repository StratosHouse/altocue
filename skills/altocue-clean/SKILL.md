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
3. Read the report: noise floor, hum, engine, phrases, loudness range, S sounds, any warning.
4. After the render, `altocue master` applies two-pass loudness and a true-peak limit, and reports what it measured.

## Which denoiser

- **`apple`, the default on a Mac that has it.** Apple's voice isolation, built into macOS. No download.
- **`deep-filter`, the default elsewhere.** `altocue setup` installs it. On a Mac, `altocue setup --deep-filter` fetches it too.
- **`afftdn`, when neither exists.** Stock ffmpeg, the plainest.
- **Why Apple leads.** On demo speech with synthetic noise it kept more of the voice in five cases of six and took more noise in all six. Nobody has listened. If a voice sounds wrong, try `altocue clean --force --denoiser deep-filter`, and let a person choose.

## Reading the report

- **A warning that denoising removed too much speech** means the original was kept. Trust it. Tell the user the source needs a better recording.
- **Apple's unit removes what it does not hear as a voice.** A synthetic voice can vanish. The guard then hands over to the next engine, and warns.
- **No hum found** is normal. Notches go in only where hum is measured.
- **`range_before` and `range_after`** are the loudness range in LU. Pauses count, so judge it on the cut film.

## Phrases, breaths and clicks

- **Phrase levelling is on.** Each phrase is brought toward the clip's level, by at most 9 dB up and 6 dB down. A noisy room gets less boost, because the room rises with the voice.
- **Pauses are left alone,** so room tone and breaths do not pump.
- **Only one part of a clip wrong, or a quiet speaker beside a loud one:** set `gain` on those segments in `edl.json`, after the cut is final.
- **Breaths stay unless asked.** `altocue clean --breaths 9` turns them down 9 dB. They are never cut. The finder has not been checked against labelled breaths, so listen.
- **Mouth clicks and lip smacks** are turned down by default, never cut. `--no-declick` or `declick = false` leaves them in.

Set these in `altocue.toml` under `[audio]`: `denoiser`, `level_phrases`, `deess`, `declick`, `breaths`, `room_tone`.

## S sounds

- **De-essing is the smallest softening.** Each S is measured against the body of the same voice, its S-to-voice. Only an S over the strength's ceiling comes down, toward it: at most 2 dB on `light`, the default, 3.5 on `normal`, 5 on `strong`. No S is ever taken under 10 dB below the voice.
- **A good microphone needs little or none.** A good wireless lavalier with natural S sounds comes through `light` untouched, and the clean line says so.
- **Go up one step at a time from the default.** Use `strong` only after someone has heard `normal` and still found it harsh. `--deess off` is for a voice where any softening shows.
- **Read `mouth` in the report.** `s_to_voice_db` is before and after: about 10 dB under the voice is natural, level is harsh. [thumb] `sibilants_softened` counts S sounds touched, `deess_held` those the guard held, `deess_lowest_db` the softest left, `s_to_voice_final_db` the finished clip.
- **A lisp in numbers:** S-to-voice well under -10 dB, or more than 5 dB taken. The `sibilance` gate fails on either, as on an old report that took 7 or 11 dB. Fix it with `altocue clean --deess light`.
- **Never raise the strength for harshness from a too-bright EQ or a too-hot limiter.** If `s_to_voice_final_db` sits well above the de-essed figure, the brightness came later. Fix it there.

## Room tone

- **On by default.** Where the voice falls under the clip's own room, its room fills the hole. A clip with no sound, such as a screen recording, borrows its neighbour's, and the room runs on under an end card.
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

- **Ducking is automatic.** The music is down before the first word lands and stays down through any pause under 1.2 s. In a pause up to 2.5 s it rises 4 dB at most. In a longer one it comes back slowly. Under speech it also cedes a little of the consonant band.
- **`[audio] music_under` is how far under the voice the music sits while someone speaks:** 18 dB by default, from 6 to 30. About 12 suits a montage with a line over it. The mix report beside `work/mix.wav` gives the measured figure as `under_voice_db`, and the `music_under_voice` gate fails when it sits more than 3 dB short of the target.
- **`"music": { "path": "...", "end": "land" }` is the default.** The track is backtimed so its own end falls on the last frame. Silence at the end of the file does not count.
- **`"end": "fade"`** starts at the top of the track and fades out.
- **`"end": "own"`** is for a score that ends itself: no loop, no backtiming, no fade.
- **A track shorter than the film loops,** blended at each join. The joins are not on a bar, so listen to them.
- **Check each cue against the speech.** [source] The whole track is levelled once, so a quiet passage can vanish under the voice. You cannot hear this, so name the moments for the user to check.

## No voice

- **A film with no voice has nothing to level against,** so the music leads. A silent voice track counts.
- **Effects sit over the music.** A cue's `over_db` sets how far, 0 to 14. `under_db` is refused here, because it means nothing without a voice.
- **A big hit gets a dip.** `dip_db` draws the music down 6 to 12 dB in the 0.4 s before it. boom, punch, sub and clang have one in the table.
- **Fades are tight:** 0.03 s in and 0.27 s out.
- **These are starting points** from one project's measurements of its own films. Nobody has listened here.

## Targets

- **Default master: -14 LUFS, -1.5 dBTP.** Change them in `altocue.toml`, or name a target: `altocue master --loudness podcast`.
- **-14 is a house default.** No short-video platform publishes a loudness number. Spotify, Apple Podcasts and EBU R 128 publish theirs, and naming one prints its source.

## Be honest

- **Music under the voice** is measured on every clean. With Demucs installed (`altocue doctor` says), `[audio] music = "auto"` takes the vocal stem first, and undoes it if the words lose more than 6 dB.
- **Echo** is measured too, and a room that rings is named. `[audio] echo = "light"` takes a little off. On a Mac the apple denoiser removes more.

Short clipped peaks are redrawn before denoising, and `declip` in the report counts them. Heavy clipping cannot be rescued, and a bare room is only softened. Say so instead of pushing the denoiser harder. Numbers show what changed, not whether it sounds better. A person has to listen.
