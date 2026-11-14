# Sound: what the ear is told, and when

Viewers forgive a soft picture long before they forgive bad sound. Sound also does half the editing: it joins shots, sets pace and tells the viewer what matters. Lines here are [thumb] unless labelled. You cannot hear, so every decision on this page ends with a measurement and a moment for a person to listen to. How to run each step is in `altocue-clean` and `altocue-sound`. Effects and music cut to picture are in [sound design](sound-design.md).

## Three layers, one at the front

A film has a voice, music and effects. At any moment exactly one of them is at the front.

- **With a voice, the voice is the front.** Music and effects serve it or leave.
- **With no voice, music is the front,** and effects punctuate it.
- **Silence is a layer too.** A stop before the key line does more than another sound.

When two layers fight, the answer is almost never to raise one. Lower the other, move it in time, or take it out.

## The voice chain, in order

Each step should hear a signal the earlier steps already made easier. [thumb] Repair in this order: clipping, clicks, hum, broadband noise, then de-essing, and only then level, tone and compression. A later step treats an earlier fault as voice.

| Step | What Altocue does | Overdone, it sounds like |
|---|---|---|
| Declip | Counts clipping, repairs nothing | Nothing rebuilds a flat top. Say so and ask for a retake |
| Rumble and hum | High-pass at 80 Hz, notches only on hum it measured | A thin, small voice |
| Mouth clicks | Turned down, never cut | A plosive or a T taken for a click |
| Noise | Strength chosen by measured signal to noise; clean audio is left alone | Watery, chirping S and R sounds, bitten word starts, gaps that go dead beside gaps that hiss |
| Echo | `[audio] echo` takes a little off when asked | Hollow, phasey, lisping |
| Sibilance | A split-band de-esser, `light` by default | A lisp: the S turns soft, whistly or into a TH, and the voice goes dark |
| Level | Phrase by phrase, pauses left alone | Breaths and room pumping up between words |
| Tone and compression | A small cut at 300 Hz, a small lift at 3 kHz, 2:1 compression | Flat delivery, loud breaths, harsh S sounds the de-esser then fights |
| Master | Two-pass loudness and a true-peak limit on the decoded file | Squashed peaks on plosives |

- **Clean by need, never by habit.** A clean recording left alone sounds better than a clean recording denoised. Lower a noise floor. Do not delete it.
- **Back off past the artefact.** [thumb] Raise noise reduction until artefacts appear, back it off until they go, then back off a little more.
- **Never stack two denoisers.** The second works on the first one's artefacts.
- **Check the words survived.** After any strong clean-up, `altocue qa` re-reads the words from the finished file.

## De-essing: soften a harsh S, never erase it

The commonest way to ruin a good voice is too much de-essing. It gives the speaker a lisp, and a viewer hears a lisp as the person, not the processing.

- **A good wireless lavalier needs the smallest softening, or none.** On the chest it sits below the mouth and in the chin's shadow, so it already has fewer highs than a microphone above the mouth. `light`, the default, softens only an S that stands near or above the level of the voice, so natural S sounds come through it untouched: start there, not at `off`.
- **One step at a time.** `light` takes at most 2 dB, `normal` 3.5 and `strong` 5, and no strength ever takes an S under 10 dB below the voice. Go to `strong` only after a person heard `normal` and still found the S harsh.
- **Read what it took.** `work/audio/report.json` gives `s_to_voice_db` before and after, how many S sounds were softened, and `deess_held`, the ones the lisp guard stopped. The `sibilance` gate fails a film whose S sounds were taken too far.
- **Compare at matched loudness.** [source] The method published for a lispy de-esser (TryUncle, October 2026): loop a line full of S sounds, bypass, match loudness, then compare. If the lisp goes with the bypass, the de-esser did it.
- **Brightness added later brings the S back.** [thumb] Recheck sibilance after any EQ lift or compression. A harsh S after a light de-ess is often a presence lift or a bright compressor, not too little de-essing.

## Let every word finish

- **Keep the decay and a few frames of handle before a cut.** A word ends with a tail that the waveform barely shows. Cut on it and the viewer hears a lost ending. The cut restores tails it measures, and the `word_ends` gate fails a shot that closes inside a word's last sound.
- **When there is no room, split the sound under the next shot.** Altocue has no split edit yet. The nearest: lengthen the segment in `edl.json` so the word finishes, and lay an insert over that extra so the picture moves on while the voice ends.
- **Breaths belong to the line after them.** Keep them whole. Lower a loud one with `[audio] breaths`; never cut one in half.
- **Hesitations can be the point.** [source] Production Expert (May 2026): a pause after a hard admission, or a search for the word, carries meaning that text editing strips out. First passes are usually too fast.

## Room tone

- **Keep the room.** A gap at digital silence beside a gap with room in it is heard as a cut. `[audio] room_tone` fills holes with the clip's own room. Leave it on.
- **Room tone is continuity.** Two shots recorded in different rooms are joined by the ear before the eye.
- **A cutaway with no sound of its own feels pasted on.** A little of the place under the voice makes it part of the film.

## Music under speech

- **Ask whether the film needs it.** Many talking films are better with none, or with music only at the open, the turn and the end.
- **Choose what leaves the voice room.** [source] Audio Network's guidance (July 2026): the voice lives in the mid range, where lead piano, guitar and synth melodies also live; speed is not the problem, constant builds, drops and swells are. Sparse, steady, and low or high in register.
- **No lyrics under speech.** Two sets of words at once and the viewer follows neither.
- **Hold it well under the voice.** [thumb] Listeners who are not sound people want more room between voice and music than an editor does, so err lower. Altocue's `[audio] music_under` is 18 dB by default.
- **Duck before the word, hold through breaths, return slowly.** A bed that rises in every short pause is the classic automatic mix. The mix already rides this way.
- **Let music step up in the gaps.** A pause, a section change, the end: that is where music may lift.
- **Pull it out for the line that matters.** Take music away under the key sentence, or stop it just before. A sudden absence is louder than any swell.

## Loudness and delivery in 2026

- **No short-video app publishes a loudness number.** [source] Checked on 13 November 2026: YouTube's help page names none and says "stable volume", on by default except for music, narrows the range between quiet and loud. Meta wrote in 2023 that its players set the level per device and per room from loudness data in the file, and no newer page gives a number. TikTok and LinkedIn publish nothing found. Treat any platform figure as unconfirmed.
- **Published targets exist elsewhere.** [source] Apple Podcasts: around -16 LKFS, within 1 dB, true peak no higher than -1 dBFS, and WAV or FLAC must be two channels. Spotify music: -14 LUFS, true peak under -1 dB, under -2 dB if louder. `altocue master --loudness podcast` names the first.
- **Louder buys nothing where the player normalises.** The file is turned down and keeps only the damage to its peaks. The -14 LUFS default is a house choice. Say so.
- **The opening must not be quieter than the rest.** A quiet first word loses the viewer. `altocue facts` reports `opening_minus_integrated`.
- **Think of the phone.** It is close to mono and plays little low end. The voice must read with the lows gone, a deep hit needs a top, and nothing may depend on stereo width. Voice and effects in the centre; width belongs to music and tails.
- **Trust the decoded file.** Lossy encoding overshoots peaks. The `true_peak` gate measures what the viewer receives.

## Sound as storytelling

- **Contrast.** Quiet before loud. A hit lands harder after a moment with less in it, and a film that is loud everywhere has no loud moment.
- **Rhythm.** Speech has its own pulse. Music, cuts and effects either follow it or step into its gaps.
- **Anticipation.** A sound that starts before its picture pulls the viewer forward. A riser ends on the reveal, never starts on it.
- **The hook is heard before it is watched.** Open on the voice or one strong sound on the first frame. Never on a fade in, a breath or room tone.
- **A signature.** One sound for one recurring idea, and the same ending sting across a series, make a channel recognisable. Change it and the link is lost.
- **Restraint.** Every sound spends the viewer's attention. Spend it where the film turns.
- **A change of sound marks a change of section** as clearly as a title does.

## The tells of an automatic mix

- The bed dips and recovers on every pause.
- The same dip depth whether the music is loud or quiet.
- Every sound of one kind is the same file at the same level.
- Effects on the tail of a spoken line, heard as odd mouth noises.
- Music stops with the picture, in the middle of a bar.
- A lisp from a de-esser on a voice that was fine.
- Noise reduction on a recording that had no noise.

## What to hand a person

You cannot hear, so name the moments. After `altocue qa`, list for the user: the first two seconds, every place music enters or leaves, each effect with its time, every S-heavy line, any join the timeline sheet shows landing on a tall part of the wave, and the last second. Say plainly that tone, noise, sibilance, the music and lip sync were checked by measurement only.
