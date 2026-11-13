# Sound design: effects and music cut to picture

An effect is a promise that something happened. Music is a promise about how to feel. Break either and the viewer stops trusting the film. Lines here are [thumb] unless labelled. The voice, room tone, loudness and storytelling are in [sound](sound.md); the commands are in `altocue-sound`.

## Four questions before any effect

1. **What happens on screen, on which frame?** No event, no sound.
2. **Is anyone speaking near it?** Never in the last half second of a line, the breath after it, or the moment before the next word.
3. **What played in the last second?** At most two effects in any one second.
4. **Would silence say more?** Often yes.

## Event to sound

| On screen | Family | Length | Lands | Bundled today |
|---|---|---|---|---|
| A chip, label or card arrives | Pop | Under 0.3 s | On the settle frame | `pop` |
| A number lands | Soft pop or ding | Under 0.4 s | On the frame the value is final | `pop`, `ding` |
| A number counts up | Ticks, then a landing | One per visible step | First step, last step | `tick` |
| Steps appear one by one | Tick or rising step | Under 0.1 s each | On each arrival | `tick` |
| Text types on screen | One key tap per character | Per character | Each character's frame, stopping with the last | The typing scene in `altocue studio` |
| A button press or click | Click | 30 to 60 ms | On the press frame | `click` |
| A slide, wipe or scene change | Whoosh | Fit to the move, usually 0.5 to 1 s | Loudest point on the fastest frame of the move | `whoosh3`, `whoosh4`, `whoosh7`, `whoosh8`; `whoosh` for a short slide |
| A build to a reveal | Riser | About a second, longer for a big build | Ends on the reveal frame, hands to a hit | Nothing real. `rise` and `powerup` are short interface sounds |
| A reveal, logo or big number | Hit | 0.3 to 1 s | Loudest point on the reveal frame | `punch`, `thud`, `clang` |
| Weight under a reveal, no voice | Sub drop, with a top layer | About 1 s | Loudest point on the frame | `sub`, `boom` |
| Going back, a rewind, a suck into a cut | Reverse swell | Ends on the cut | Ends on the cut | Nothing bundled |
| Something leaves or fails | Fall or soft low hit | Under 0.4 s | As it starts to leave | `fall`, `thud` |
| Success, done, saved | Ding or chime | Under 0.8 s | On the first frame of the tick or badge | `ding`, `chime` |
| A page or paper turns | Page | Under 0.5 s | With the turn | `page` |
| A section opens, no voice | Swell in the music | A bar or two | The music's own lift | The track itself |
| A plain cut, a punch-in, a quote being read | Nothing | | | |

`altocue sfx list` gives each bundled sound's length and use; `altocue sfx search` finds more in the indexed open packs. Where nothing fits, use nothing. A wrong sound is worse than none.

## Place it to the frame

- **Find the frame the viewer feels.** A pop lands on the frame an element stops moving or first becomes readable, not the frame its entrance starts. A whoosh peaks at the fastest point of the move. A riser ends on the reveal. A faller starts as something leaves.
- **The house sounds land on a graphic's first frame.** `sfx_cues` puts the sound at the start of the item's entrance, and a wipe's at its middle. When the settle is a few frames later, or the start sits on speech, turn the house sounds off and place each cue yourself.
- **Late is safer than early.** [source] ITU-R BT.1359, as summarised by Fora Soft (June 2026): viewers notice sound 45 ms early but only 125 ms late, and accept up to 90 ms early and 185 ms late. At 30 fps an effect may land up to three frames after its event and still read as in sync, but no more than one before.
- **Put the transient on the frame.** `"align": "peak"` lays the loudest moment on the time, for hits and whooshes. `"align": "start"` suits a pop or click whose attack is its first sample.
- **Effects belong on a graphic's arrival, never on a line's tail.** A sound that starts in the last half second of a spoken line, or in the breath after it, is heard as a strange noise in the voice, not as a sound effect.
- **Never land on a consonant.** [source] MetricGate's masking notes (June 2026): a loud sound hides quieter sound for roughly 100 to 200 ms after it, and for only 5 to 20 ms before it. A pop just before a word takes its first consonant; one just after a word takes its decay. Keep 0.2 s clear before a word starts.
- **When the event sits on speech:** move the sound up to three frames later into a gap, take it down to `"under_db": 12`, or leave it out. Leaving it out is usually right.
- **A whoosh dies before the landing.** A whoosh then a hit is a designed pair; the hit needs clear air.

## Level it

- **With a voice, effects sit under it.** `under_db` is 8 to 12 dB under the voice, 10 by default, and the code never lets an effect's peak pass 7 dB over the voice's loudness.
- **The more often a sound plays, the quieter and shorter it is.** Ticks, clicks and keys at 12; rare accents at 8 or 9.
- **With no voice, effects sit over the music.** `over_db` from 0 to 14; a big hit gets `dip_db`, which draws the music down 6 to 12 dB in the 0.4 s before it.
- **Low hits need a top.** A phone plays almost nothing under a few hundred hertz. A `sub` or `boom` alone is silent where most people will hear it; pair it with a click or a short bright hit on the same frame.
- **Mono and centred.** Whooshes, pops, clicks and keys sit in the middle. Altocue lays every effect in the centre.

## Density, variation and silence

- **At most two effects in any one second.** Three is clutter on a phone.
- **A typing sound plays whenever text types on screen,** one tap per character, stopping with the last one. Never a long typing recording under typed text, and never typing sound with no typing.
- **Accent the few moments that matter.** Let the music and the voice carry every other cut. A whoosh on every cut and a pop on every caption is noise.
- **Vary repeats.** Rotate `whoosh3`, `whoosh4`, `whoosh7` and `whoosh8`. The same file three times in ten seconds is a machine. `min_gap_ms` drops a repeat that comes too soon.
- **Layer only designed pairs:** whoosh then landing, riser then hit, low hit plus top.
- **Keep tuned sounds in key with the music,** or use untuned ones. `ding` and `chime` are tuned.
- **Silence is an effect.** Take everything out for one beat before the key line, and the line arrives louder than any hit could make it.

## Music cut to picture

- **Choose a track longer than the film, with an ending of its own.** `"end": "land"` backtimes it so its own ending falls on the last frame. That is the best ending Altocue makes today.
- **A loop is a compromise.** A track shorter than the film loops with a blend that does not know the bar. Pick a longer track first.
- **Cut music by whole phrases.** Remove or repeat whole four or eight bar phrases. [source] Film Editing Pro's advice: cut on the backbeat, mid-measure to mid-measure, just before a transient, so a fill or a reverb tail is not chopped.
- **End on purpose, never on an arbitrary fade.** [source] Frame.io's music editing advice: join the track's real final hit to where the picture ends, matching the waveform, rather than slapping a crossfade on. In order: the track's own ending on the last frame; the last downbeat on the last cut with its tail left to ring; a hard stop into silence, once, for emphasis.
- **A hit on the last frame.** The music's final hit on the end card's first frame closes the film; a chord that rings under the card lets it breathe.
- **Start on purpose.** The first bar sets the pace. Music that fades in under the first word competes with the hook; start it with the voice or after the first line.
- **Under talk, sparse and steady.** A melody or lyric in the voice's range fights every word. A track that builds and drops makes the speaker sound like they are racing it.
- **Pull it out to make a point.** Music stopping is a cut the ear hears.

## Check it

By the numbers, after `altocue render --draft`:

- The mix report beside `work/mix.wav`: `under_voice_db` for the music, each effect's `gain_db`, whether it was `capped`, and what was `skipped`.
- Each effect's time against the word times in `work/captions.json`: none in the last half second of a line or the breath after it, none in the 0.2 s before a word, no more than two in any second.
- `altocue facts`: `opening_minus_integrated` near zero or above.
- `altocue qa`: `loudness`, `true_peak`, `cut_sound`, `word_ends` and `silence` pass.

By ear, for the person, named with times:

- Every effect: does it land on the thing, and is it clear of every word?
- Every S-heavy line: harsh, or lisping?
- Where music enters, ducks, lifts and ends: on a phrase, under the voice, ending on purpose?
- The first two seconds and the last second, on a phone speaker.

## Mistakes

- **A lisp from the de-esser.** A good lavalier de-essed at full strength. Start at `off` or `light`.
- **A stray sound on a line's tail.** An effect started in the last half second of a sentence and was heard as part of the voice.
- **A sound on the animation's first frame** instead of the frame the thing arrives.
- **Typing that does not match the text:** keys after the last character, or text typing in silence.
- **A whoosh on every cut and a pop on every caption.**
- **Three sounds in a second,** or two unrelated sounds on one frame.
- **The same file, same level, over and over.**
- **A deep boom with no top,** silent on a phone.
- **A riser that starts on the reveal** instead of ending on it.
- **Music with a lead melody or lyrics under speech.**
- **Music that fades out mid-bar,** or a loop join that lands off the beat.
- **An effect louder than the voice.**
- **A tuned ding out of key** with the music.
