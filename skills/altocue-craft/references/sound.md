# Sound: what the ear is told, and when

Viewers forgive a soft picture long before they forgive bad sound. Sound also does half the editing: it joins shots, sets pace and tells the viewer what matters. Lines here are [thumb] unless labelled. You cannot hear, so every decision on this page ends with a measurement and a moment for a person to listen to. How to do each step is in `altocue-clean` and `altocue-sound`.

## Three layers, one at the front

A film has a voice, music and effects. At any moment exactly one of them is at the front.

- **With a voice, the voice is the front.** Music and effects serve it or leave.
- **With no voice, music is the front,** and effects punctuate it.
- **Silence is a layer too.** A stop before the key line does more than another sound.

When two layers fight, the answer is almost never to raise one. Lower the other, move it in time, or take it out.

## The voice

- **Repair in order.** Clipping first, then rumble, clicks, hum, broadband noise, harsh S sounds, and only then level, tone and compression. [source] The repair order is the one published by iZotope for restoration work. A later step treats an earlier fault as voice.
- **Clean by need, never by habit.** A clean recording left alone sounds better than a clean recording denoised. Lower a noise floor. Do not delete it.
- **Keep the room.** A gap at digital silence beside a gap with room in it is heard as a cut. The room tone fill exists for this. Leave it on.
- **Level by phrase.** A quiet aside and a loud line should sit together, and the pauses between them should not pump.
- **One voice is not every voice.** A thin phone recording, a boomy close microphone and a bright clip-on each need different tone. A fixed presence boost on a bright microphone makes S sounds harsh.
- **Breaths are part of speech.** Turn them down when the pace is tight. Never cut them out, and never cut one in half.
- **Check the words survived.** After any strong clean-up, `altocue qa` re-reads the words from the finished file.

## Music

- **Ask whether the film needs it.** Many talking films are better with none, or with music only at the open, the turn and the end. Music as a constant bed flattens everything it sits under.
- **Choose by job.** Tempo sets pace, register sets weight, and density decides whether a voice can sit on top. A busy melody in the range of the voice competes with every word. Sparse, steady and low or high is easier to speak over.
- **Hold it well under the voice.** [source] Torcoli and others (2019) found listeners prefer speech at least 10 LU over music and 15 over ambience, and non-experts want more. Treat that as the floor, and go further for teaching.
- **Duck before the word, hold through breaths, return slowly.** A bed that rises in every short pause and falls again is the classic sign of an automatic mix. The dip should follow sentences, not syllables.
- **Do not duck what is already quiet.** A forced dip on a soft passage makes the music vanish.
- **Edit music by bars.** Remove or repeat whole four or eight bar phrases. A join that lands off the bar is heard by people who could not say why.
- **Start on a downbeat.** `music_from` sets the sample the track begins on.
- **End on purpose.** In order of preference: the track's own ending timed to the last frame, which is what `music_end` set to land does; the last downbeat on the last cut with the tail left to ring; a hard stop into silence, once, for emphasis. Never a long fade from an arbitrary point.
- **Let the edit meet the music at chosen moments.** Where nobody is speaking, a cut may land on a beat. [source] ITU-R BT.1359 puts the limits of detectable sync error at about 45 ms for sound early and 125 ms for sound late, so a cut a little before the beat reads as on it, and a cut after the beat reads as late.

## Effects

- **A sound needs an event.** Something arrives, lands, moves, changes, is pressed, is typed or is revealed. No event, no sound.
- **Choose by what the thing is, then by how big.** A small label gets a short light sound. A full-screen title can carry weight.
- **Match the length of the move.** The whoosh for a quick slide is not the whoosh for a slow push.
- **Find the right frame.** A pop lands on the frame the element settles or becomes readable, not the frame its animation starts. A whoosh peaks at the fastest point of the move. A riser ends on the reveal and hands over to a hit.
- **Stay off words.** Move the sound into the gap before or after, or bring it down. An effect on the key word costs the word.
- **Budget them.** Frequent sounds are short and quiet. Rare sounds can be big. A whoosh on every cut and a pop on every caption is noise.
- **Vary repeats.** The same click five times is a machine.
- **Low hits need a top.** A phone speaker plays almost nothing under a few hundred hertz. Without an upper layer a deep hit is silent where most people will hear it.
- **Keep tuned sounds in key with the music,** or use untuned ones.
- **Some moments get nothing.** A punch-in that hides a cut, a plain cut between two shots of the speaker, a quote the viewer is reading. A sound there points at what you wanted hidden.

## What sound for what event

| On screen | Sound | Lands |
|---|---|---|
| A title or hook arrives | One accent, or none | On the first readable frame |
| A small label or chip | A small pop, skipped if one just played | On the settle |
| A number appears | A soft landing | On the frame the value is final |
| A number counts up | A run, then a landing | Starts with the count, lands with the last step |
| Steps appear one by one | A tick or a rising step each | On each arrival |
| A scene or chapter change | A whoosh | Peak at the middle of the move, in a gap between sentences |
| A reveal after a build | A riser into a hit | Riser ends on the reveal frame |
| A button press or cursor click | A click | On the press frame |
| Typing | Key taps | One per character, stopping with the last |
| A cutaway to a place | A quiet bed of that place | Fades in and out with the picture |
| A visible contact: a door, a cup, keys | The real sound | On the contact frame |
| The end card | A sting or the music's own ending | After the last word |

`altocue sfx list` shows what is bundled, and `altocue sfx search` what the open packs hold. Where nothing fits, use nothing. A wrong sound is worse than none.

## Levels and delivery

- **Master to the destination, not to a habit.** The loudness setting takes a number or a name. No short-video app publishes a loudness target, so the default is a house choice. Say so.
- **Leave room under full scale.** Lossy encoding overshoots. The true peak gate measures the decoded file.
- **The opening must not be quieter than the rest.** A quiet first word loses the viewer. `altocue facts` reports `opening_minus_integrated`.
- **Voice in the centre, effects in the centre, lows in mono.** Width belongs to music and to tails.
- **Think of the phone.** Most short video is heard on a small speaker or cheap earphones. A mix that relies on bass, or on a very quiet passage followed by a very loud one, will not arrive.

## Sound as editing

- **Sound joins what picture separates.** A voice that runs on over a new picture keeps a thought alive. A sound that starts before its picture pulls the viewer forward. See [cuts](cuts.md).
- **A cutaway with no sound of its own feels pasted on.** A little of the place under the voice makes it part of the film.
- **Room tone is continuity.** Two shots recorded in different rooms are joined by the ear before the eye.
- **A change of sound marks a change of section** as clearly as a title does: music entering, music leaving, a new room.

## The tells of an automatic mix

- The bed dips and recovers on every pause.
- The same dip depth whether the music is loud or quiet.
- Every sound of one kind is the same file at the same level.
- Every effect sits exactly on the frame, with no lead or lag by type.
- Music stops with the picture, in the middle of a bar.
- The same tone and compression on every voice, whatever the microphone.
- Noise reduction on a recording that had no noise.

## What to hand a person

You cannot hear, so name the moments. After `altocue qa`, list for the user: the first two seconds, every place music enters or leaves, each effect with its time, any join the timeline sheet shows landing on a tall part of the wave, and the last second. Say plainly that tone, noise, the music and lip sync were checked by measurement only.
