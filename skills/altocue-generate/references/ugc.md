# Footage that reads as filmed on a phone

Craft notes for generated clips that should look shot on a phone. They are in our own words, drawn from public guidance on AI-made creator footage and from platform ad advice. Treat the numbers as starting points. Only the speech rate, the cold open and baked text are measured, by `altocue qa --check ugc`.

## Honesty comes first

- **Realism is a craft goal, never a way to hide what this is.**
- **A generated person is a host, never a customer.** A host shows a product and says what can be seen. A host never bought it, used it or got results.
- **Use only claims on an approved list.** With no list, describe only what is visible.
- **Label it.** See `altocue-ugc` for the line and the platform steps.

## The idea

Real footage is full of small failures, and those failures are how a viewer decides something was filmed. Ask for the flaws. Do not ask for perfection, and do not stack every flaw at once, because too much texture is its own tell.

Avoid retoucher words such as flawless, airbrushed or pristine. Photographer words such as documentary are fine. Name the cheap device first, and delete cinematic words.

## Camera

- **Front camera at arm's length.** About a 24mm look.
- **Deep focus.** The room stays nearly as sharp as the face. A creamy background is the loudest tell, and it survives every other fix.
- **A small constant shake, never a glide.** Say which hand holds the phone. This is smaller than the film handheld in `altocue-direct`, and "shake" in the prompt also clears its warning.
- **One focus snap and one exposure shift per clip,** on selfie cuts only.
- **A locked shot stays locked.** No breathing wobble.
- **No fisheye, wide-angle warp, dolly or gimbal.**
- **Never show the phone.** The camera is the phone.
- **Keep mirrors and reflections out.** They grow extra limbs.

## Skin and face

- **Two or three flaws only:** pores, a little oil, uneven tone, loose hairs.
- **Texture needs raking light.** Flat frontal light hides what you asked for.
- **Repeat the skin line in the motion prompt.** Animation smooths skin away.
- **Catch the face mid-action.** No smile at the lens. The energy is in the face and the body stays calm.
- **Keep catchlights muted.** No glowing eyes.
- **Clean up a still before it becomes video.** A pass for skin and sensor noise is cheap, and video is not.

## Hands

- **Count two hands, and give each one a job.** A selfie cut leaves one free hand. A locked cut leaves two.
- **One action and one change of state per cut.**
- **Nothing thrust at the lens.** Reaching limbs warp.
- **Give size against the hand, in centimetres.** Comparisons to other objects drift.

## Light

- **One practical source, or what is already in the room.** No ring light and no studio.
- **Raking light for faces, neutral light when product colour must be true.** A mild colour cast is fine on a talking head. Golden hour is not a default.
- **Let a window clip with a thin halo,** then settle over about half a second.

## Sound

- **Room tone under everything.** A hum, a fan, far traffic.
- **The voice sits close on a phone mic,** a little boxy, with one audible breath and uneven stress.
- **A flat read gives a flat face,** because the model builds the face from the voice.
- **Prefer the model's own speech to pasted text-to-speech.**
- **No music unless someone asked.**
- **Never time-stretch a line to fit.** Rewrite it.

## Script

- **Aim for 2.5 words a second. 2.9 is the ceiling.** That is about 20 words in 8 seconds.
- **The first word is content.** Never Okay, So, Wait or Hey guys.
- **Spell numbers out. Keep sentences short.**
- **Concrete detail beats praise.** Ban the stock phrases: game changer, holy grail, obsessed, 10 out of 10.
- **One twist per film, one human-sized reaction.** No staged screaming.
- **In a story, the product arrives between 40 and 60 percent of the way in.** The story should survive without it.

## Pacing

- **Frame one is already moving, and the first word lands within 0.4 seconds.**
- **Say what this is within 3 seconds, and hook inside 6.**
- **Generate short and cut between clips.** Under about 10 seconds holds a face best.
- **For variants, hold actor, room, light and lens.** Change only the line and the first three seconds. Six to ten is enough to learn something.

## Same person across clips

- **Approve one still and use it as the first frame everywhere.**
- **Repeat one lock block word for word.** Reuse the seed where it is offered. Never describe the face again.

## Phone artefacts, in small doses

Allowed: noise in the shadows, flattened highlights, a smudge, slight skew on a quick move, 1/60 shutter smear at 30 fps. Film grain and a cinematic grade read as filmed, not phoned.

The room is lived in. One object out of place, never a tidied set.

## Leave out

- **Burned-in captions.** Add captions in the edit. Writing the spoken line after a colon is reported to stop some models burning in their own, and `altocue qa --check ugc` reads the picture to be sure.
- **Legible text on props,** except the real product label.
- **Duplicate products.** Say exactly one in the prompt.
- **A fake phone interface along an edge.**
- **Negatives in the prompt on a model with no negative field.** Naming a thing can summon it, so keep constraints positive where you can.
- **Beautiful as a casting goal.** It fights everything above. Casting is a person's choice, and an agent never automates it.

## When a clip fails

| You see | Do this |
|---|---|
| Plastic skin | Repeat the skin line in the motion prompt |
| A glide | Make the shake constant and name the hand |
| A soft background | Put the deep-focus line back |
| The face drifts | Keep clips under about 10 seconds and cut between them |
| It looks grimy | Use fewer flaws |
| Lips miss p, b and m | Scrub those words at quarter speed. `altocue qa --check ugc` writes `review/lip-sheet.jpg` so a person can look |

Each failed generation adds one lock line that names exactly what went wrong.
