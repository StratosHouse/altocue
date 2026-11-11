# Where and why to cut

A cut moves the viewer's attention. Make one when you want their attention to move, and leave the shot alone when you do not. Lines here are [thumb] unless labelled. How to make each change is in `altocue-cut`, `altocue-frame`, `altocue-broll` and `altocue-transitions`.

## A cut needs a reason

Before any cut, name which of these it is:

1. **New information.** The next picture shows something this one cannot.
2. **Emphasis.** The moment needs to feel bigger or closer.
3. **Time.** Something between here and there is not needed.
4. **Rhythm.** The film has sat still long enough for this content.
5. **Repair.** A fault needs hiding: a stumble, a look away, a jump.

If none applies, do not cut. A cut for its own sake is noise, and enough of them make a film feel anxious rather than fast.

## What to give up last

When rules collide, an editor trades the lower ones away first. [source] Walter Murch, In the Blink of an Eye, puts them in this order: emotion, story, rhythm, eye trace, the flat composition of the frame, and the geography of the space. He weights the first far above the rest.

- **Emotion.** Does the cut keep what the viewer should be feeling at this moment?
- **Story.** Does it move the point forward?
- **Rhythm.** Does it land at a moment that feels right, like a beat in music?
- **Eye trace.** Is the thing to look at next near where the eye already is?
- **The frame and the space.** Do composition and continuity hold?

A cut that is true to the feeling and breaks continuity is usually right. A cut that keeps perfect continuity and loses the feeling is always wrong.

## Where the cut falls

- **Cut on the end of a thought, not the end of a sound.** [source] Murch's observation: people blink when a thought completes, and a cut feels natural where a listener would have blinked. The sentence finishing is the cue, not the silence after it.
- **Cut on movement.** A cut made during a gesture, a head turn or a step hides inside the motion. A cut between two still frames is as visible as it gets.
- **Never start a shot on closed eyes.** A first frame mid-blink reads as a glitch. Look at the first frame of each segment in the draft.
- **Keep the eyes where they were.** After a cut the viewer keeps looking at the same spot on the screen for a few frames. Put the next subject there. Across a punch-in that means the eyes hold their position.
- **Change something real.** Same subject, same size, same angle, with time missing in between, is a jump cut. Change the size, cover it, or own it as a style for the whole film.

## The kinds of cut and what each one says

| Cut | What happens | It says | In Altocue |
|---|---|---|---|
| Hard cut | Picture and sound change together | Next | The default at every join |
| Jump cut | Same framing, time removed | Energy, or an error | Leave it only as a consistent style |
| Punch-in | Same shot, new size | This matters, or a hidden jump | `zoom_from`, `zoom_to`, `[frame] punch_in` |
| Cutaway | Picture leaves, voice stays | Look at what I mean | An insert in `motion.json`, or `altocue broll` |
| Sound leads | The next sound starts under this picture | Something is coming | Start the insert's own sound, or a music change, a beat before the cut |
| Sound trails | This voice runs on over the next picture | Stay with the thought | Any insert over continuing speech |
| Match cut | Shape, motion or sound carries across | These two things are linked | Pick both frames by eye |
| Smash cut | Loud to quiet, or still to violent | Contrast, often a joke | A hard cut with no transition and no sound bridge |
| Cross cut | Two threads alternate | These happen together | Order of `segments` in `edl.json` |
| Dissolve | One picture melts into the next | Time passed, or a memory | `dissolve` |
| Dip to black | The picture closes and reopens | A chapter ended | `fadeblack` |

- **Split the picture from the sound.** In good work the picture and the sound rarely change on the same frame. A voice that starts under the last second of the previous shot pulls the viewer forward. A voice that continues over a new picture keeps a thought alive while showing what it means. A cutaway over a continuing voice is the simplest split edit there is, and it is why inserts make a talking head feel edited rather than chopped.
- **The hard cut is the default,** because nobody notices it. Everything else is a statement, so it has to be true: a dissolve where no time passed is a lie the viewer feels.

## Speech: the craft inside a talking take

- **In before the first sound, out after the last one dies.** A word starts with breath and ends with decay. A cut on the listed word time takes the consonant off the front or the tail off the end. The code places edges on measured quiet. Do not hand-write edges inside a line.
- **A breath belongs to the sentence after it.** Keep the breath before a line and the line sounds human. Cut it in half and it sounds like a gasp. Two breaths in a row means a pause was removed between them: take one out.
- **Pauses carry meaning, so they should not all be one length.** A pause inside a clause is a stumble. A pause after a full stop is punctuation. A pause before the key line, after a question, after a number or after a joke is doing work. Tighten the first kind hard, the second a little, and leave the third alone. A film where every gap is the same length sounds like a machine reading.
- **Tighter is not always better.** Speech with no air in it tires the listener, who stops taking in what is said. When a film feels long, remove a sentence. Do not shave every gap.
- **A filler is sometimes the thought.** "Um" before a hard admission is the feeling. Cut fillers that stall, and keep the ones that are the moment.
- **Do not build a sentence from parts of two.** The pitch at the join will not match, and the listener hears it even when the words are right.
- **Check every join by its sound picture.** `altocue qa` re-reads the words, and the review pack's timeline sheets draw the waveform against the cuts. A cut that lands on a tall part of the wave has clipped something.

## Rhythm

- **Rhythm is variation.** A run of equal shots, long or short, goes dead. Follow a quick passage with a held one. The held shot is what makes the quick ones feel quick.
- **Speed up into a peak and stop after it.** Shots shorten as tension builds, then one shot holds while the point lands. Cutting away from the payoff too soon throws it away.
- **Short for the simple, long for the dense.** The more there is to read in a picture, the longer it stays. A viewer who has not finished reading a shot feels the cut as a slap.
- **Let the content set the pace.** A calm subject cut frantically is as wrong as an urgent one cut slowly. Measure with `altocue facts`, and never chase a number.
- **Follow the speaker's own rhythm.** Good speakers have a cadence. Cut with it, on their emphasis, and the edit disappears. Cut against it and they sound interrupted.
- **Music sets a grid, speech breaks it.** Cuts in a montage can land on the beat. Cuts in speech follow the words and only sometimes meet the beat. Forcing speech cuts onto a beat clips words.

## What to take out

- **Start late.** The first sentence of most takes is the speaker warming up. If the second sentence works as an opening, the first one goes.
- **Leave early.** End on the last useful thing said. A summary of what was just said, a trailing "so yeah", a second goodbye: all out.
- **Say it once.** People repeat a point in different words. Keep the best telling and cut the rest, even when both are good.
- **Cut the side road.** A true and interesting aside that serves a different film goes into a different film.
- **One job per scene.** If you cannot say what a scene is for in a few words, it has no job yet.
- **Then check what is left still makes sense.** Read the kept words in order as a stranger would. A reference to something that was cut has to go too.
- **Never change what the speaker meant.** Moving and trimming lines can turn a hedge into a claim. If the meaning shifts, put it back or ask.

## Reading your own cut

After each pass, ask in this order: does it still feel right, does it still say the point, does it move. Then the smaller things: where the eye lands at each cut, whether any frame is a stray. [The passes](passes.md) has the full set.
