# Motion grammar: how to write shots that models obey

Sources: vendor guides for video models, their published prompt formulas, and creator posts. Everything here is **[source]**, a vendor's or community's claim, or **[thumb]**, a rule of thumb, unless marked. None of it was tested at scale. The labels are defined in [story.md](story.md). Model behaviour changes, so test the one you are on.

## Prompt anatomy

The working order, in seven slots. **[source]**

1. **Subject.** Identity, position, frame occupancy. For image-to-video, do not re-describe what the start frame already shows. Long appearance text fights the image and degrades it. The prompt's job is the new motion. Restate only critical small details, such as a label's text, because the model can drop them.
2. **Action.** Present tense, as a chain that runs one way. Reversals appear when the model fills empty seconds.
3. **Camera.** One named move with an endpoint: "slow dolly in from medium to medium close-up, ending on her hands".
4. **Setting and style.** Setting only as it changes or matters to motion. Named referents beat adjectives: "35mm film texture, shallow depth of field", never the empty word "cinematic".
5. **Lighting.** Practical sources, colour temperature, shadow quality.
6. **Audio.** See below.
7. **Constraints.** "Keep the same character, same materials, no new furniture; the set contains only what the reference shows." State exact counts of people and objects, and rule out extras.

- **Length.** About 50 to 80 words for a single shot. `altocue direct` warns outside 8 to 120. Structured multi-shot prompts can run far longer.
- **Long prompts.** **[source]** One author says text near 4,000 characters loses its last blocks. Another ships prompts of thousands of words. Untested, so put what must survive first, and test once at draft tier before relying on a long one.

## Constraints and failure lists

- **A negative field, if the model has one, takes the no-list.** One author reports that naming an unwanted thing in the prompt invites it.
- **With no such field, keep constraints positive where you can:** say what stays, not what must not appear.
- **After a rejected take, add one lock line:** name the last failure and its fix. Do not let the list grow past what you have seen go wrong.

## Camera vocabulary, by reliability

- **Most reliable:** push in, dolly in, pull back wide, static, locked-off, pan, tilt, slow orbit, crane up or down, handheld follow, aerial glide. Single-vector moves are reported to land most often.
- **Reliable with care:** tracking ("tracks alongside at chest height, matching pace"; always name who is followed and the start and end points), dolly zoom, whip pan, dutch angle, FPV.
- **Translate to what is seen.** Rack focus becomes "the foreground leaves gradually blur while her face changes from soft to sharp".
- **One primary move per shot.** Stacking conflicting vectors warps geometry. Sequential moves state their timing: "pan to follow her to the door, then push in when she stops". State the move once.
- **Endpoint always.** A move with no destination drifts: "ending on", "settling at", "until the cottage fills frame".
- **Name dolly or zoom, and rule out the other.** One changes parallax, the other focal length.
- **Keep one lens across a scene,** and change only distance.
- **A move should change what the viewer learns.** **[thumb]**
- **To force one unbroken take:** "single continuous shot, no cuts". Without it, some models insert their own angle cuts.
- **Handheld needs asking for.** Default output is smooth. For a film look, ask plainly: "completely unstabilised, constant micro-jitters". For footage that reads as a phone, ask for a small constant shake instead. See [the phone-footage notes](../../altocue-generate/references/ugc.md).

## Motion detail: how deep to go

- **Concrete verbs with physics beat adjectives.** "Dropping into a low spin, the skirt flaring, then snapping upright" beats "dancing beautifully".
- **Secondary motion, one level deep, as reactions.** Cloth, hair, dust and grass move as consequences of the main action, never as separate events.
- **Emotion as physics.** "Eyes squeeze shut, cheeks lift", never "looks happy".
- **One state change per beat:** starting state, one event, end state. Use time budgets ("0-3s, 3-7s"), and never demand several actions inside one second.
- **Impact beats.** A clip lands a physical accent, such as a stamp, a catch or a door closing, named in the shot and echoed in the audio line. **[thumb]**
- **Treat a power or effect as a material:** force, contact, result, aftermath. Glow words give the model nothing.
- **What over-specification breaks:** simultaneous actions go to mush, frame-accurate timing is ignored, every limb choreographed gives anatomy errors, long dialogue drifts off the lips.
- **Staggered entrances:** "A, then B, then C", with landing verbs. Never everything at once.
- **End settled, not frozen.** The final shot eases into a stable, still micro-moving frame.

## The multi-shot block

For hard-cut sequences in one prompt. Open with the shot count, the total length and the ratio, then number the shots:

```
A single {N}-second {RATIO} scene of {K} hard-cut shots.
SHOT 1 - 0.0s to 2.0s - {SIZE+ANGLE}: {beat}.
HARD CUT.
SHOT 2 - 2.0s to 4.0s - {DIFFERENT SIZE+ANGLE}: {beat}.
{K} hard-cut shots, no dissolves, no fades. Continuous motion within each shot.
Motion starts on frame 1.
```

- **No shot over 2.5 seconds.** A frame hanging longer reads as a slideshow. `altocue direct` warns.
- **Vary size and angle on every cut.**
- **Only the first visit to a location opens wide.**
- **Over the shoulder only with a named character in the foreground.** Without one, the model invents a person.
- **If the model merges shots,** regenerate once with the cuts spelled out, then accept one fewer cut.
- **The written cut times are approximate.** See `altocue-generate`.

The alternative is one continuous directed take: one confident move, parallax layers, secondary motion. Use blocks for pace and information, and the continuous take for tenderness and immersion.

## Parallax

Flat frames read as generated. **[thumb]** Build three depth layers into the keyframe first, then write their different speeds into the prompt: "the foreground leaves sweep past faster than the hills; the sky holds." A lateral track, an orbit or a push through foreground shows it best.

## Audio line

- **No soundtrack in a generated clip.** Scene sound only, music in the edit. An open prompt tends to come back scored, so say "no music, only soft diegetic sound".
- **Sound follows the action.** Each named motion gets at most one cue, plus one room tone. Nothing sounds that did not move.
- **Dialogue goes in double quotes,** with a delivery note. Keep lines short.
- **Keep a generated line inside one shot.** One author runs a line across a picture cut, but lip-sync authors keep it inside one, so that is the default.
- **Say how a brand name is pronounced.**

## Stating size

Sources disagree, and it may vary by model. Show both subjects together in the keyframe first, because an image carries scale better than words. In text, give size in real units against the body, such as centimetres against the hand. Test before relying on it.

## Failure modes

| Symptom | Fix |
|---|---|
| Unwanted angle cuts | "single continuous shot, no cuts" |
| Camera ignored | Use the named vocabulary and an endpoint. At worst, attach a motion video reference |
| Identity drift mid-clip | Shorter clip, gentler motion, cleaner reference, a lock line |
| Invented furniture or rooms | "the set contains only what the reference shows" |
| Character shrinks to a dot | "stays the human-sized anchor, never a tiny distant figure" |
| Morph or extra limbs | Calm the action, one lock line, fewer people in frame |
| Extra people or objects appear | State exact counts in the constraints |
| Text warps | Take it out of the shot. Text goes in the edit |
| Unwanted score | "no music" |
| Action reversal | One-way chains, with every second filled by forward motion |
| Close-up loses the location | Say what is behind the person |
| A shared prop teleports between hands | Show it in one person's close-up only |
| A screen changes by itself | Give it a cause, say what it shows, or insert the screen in the edit |
| Tint or glow appears on a time freeze | Say what does not change |
| A transformation jumps | Anchor it on the same spot, and hide the change in a cut |
| A trick looks stuck on | Build it into a real part of the approved prop |
| An outfit changes on screen | Hide the change inside a full occlusion |

A refused prompt is not a failure mode. Never reword one to get past moderation. See `altocue-generate`.

## Iteration economics

- **Prototype small, then keep the take.** Work out the shot at draft tier with no sound. **[source]** A fresh high-resolution run is a different video, so upscale the approved draft. Several authors agree, and it follows from a rerun being a new sample.
- **One action per clip, about 5 seconds.** **[source]** Short shots are easier to replace.
- **Identity drift compounds with length and motion.** `altocue direct` warns past 12 seconds on a character.
