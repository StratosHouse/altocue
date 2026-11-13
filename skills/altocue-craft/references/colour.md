# Colour: what a grade is for

A grade does three jobs, in this order: make the picture correct, make the shots agree, and then give the film a feeling. Most of what reads as professional is the first two. Lines here are [thumb] unless labelled. You cannot judge colour by eye from a still alone, so each rule names a number to read. How to run each step is in `altocue-grade`.

## The order of work

1. **Know the source.** Phone video, a log recording, HDR and a screen recording are four different things. Convert HDR and log to the working picture before measuring anything. A reading taken from an unconverted signal is a number about the encoding, not the scene.
2. **Balance.** Make neutral things neutral: shadows, then highlights, then the middle.
3. **Exposure, then contrast.** Place the subject, then set the black and white points.
4. **Match the shots** of a scene to one reference shot.
5. **Fix single things:** skin that drifted, one colour that shouts.
6. **The look.** One look for the film, at one strength.
7. **Texture.** Glow first, grain last.
8. **Check the finished file,** not the working picture.

Each step makes the next measurable. Rules that follow:

- **Never put a look on unbalanced footage.** The cast is still there, now with a look on top.
- **Anything that clips goes last.** A hard curve or a LUT throws information away and nothing after it gets that back.
- **Keep it simple.** One correction and one look per clip. Stacked fixes fight each other.
- **Demonstrate in the same order.** A before and after on screen runs from the flat camera file, through each correction, to the look.

## Reading the picture as numbers

- **Brightness.** The 1st percentile is the black point and the 99th the white point. The eye is far more sensitive at the bottom: a small shift in the shadows shows, while the highlights can move several times as far unnoticed. So match black levels first.
- **Clipping.** Count pixels at the limits and compare with the source. Only what the grade added is a grading fault. [evidence] EBU R 103 counts an out-of-range error only when more than 1% of the picture is outside.
- **Balance.** Read red, green and blue separately on pixels that are already near neutral. A gap between the three at the bottom is a cast in the shadows. A gap at the top is a cast in the highlights. A cast at one end only is common in mixed light, and one global correction will not fix it.
- **Saturation.** Large areas at the ceiling are a fault. Strong reds break first under compression.
- **Skin.** The grade report gives `skin_hue` and `face_luma` before and after. Read them on every clip.

## Skin, for every skin tone

- **All skin sits near one hue, at different brightness and strength.** That is why skin hue is a reliable check across people, and why brightness is not.
- **There is no one correct face level.** [evidence] ITU-R BT.2408 gives separate reference ranges for light, medium and dark skin and says variations are to be expected. A dark face sitting low in a well exposed room is correct. Lifting it toward a light face's level changes how the person looks. Never do it.
- **Judge exposure from the scene first.** If the room's whites and mids are where they should be, the face is where the light put it. Lift a face only when the whole scene is under.
- **Dark skin shows its shape in reflections.** Protect the small highlights on forehead, cheek and nose. A heavy roll-off flattens them and the face goes grey.
- **To brighten a face, lift the middle, not the top.** Raising everything and pulling the blacks down blows the highlights.
- **Contrast pivots where the face is.** A contrast curve darkens what is below its pivot and brightens what is above, so the same curve pushes a light face lighter and a dark face darker.
- **Tints hit low-saturation skin hardest.** The same shadow tint that barely moves one face can turn another. The report's `look_skin_moved` says what each clip took. Read every clip, not the first.
- **Two people, two skin tones.** Never pull one person's skin toward the other's. Keep each where the scene put them and check both faces in the draft.
- **Skin is a check, not a method.** It tells you a correction went wrong. It does not tell you what the light was.

## White balance

- **A colourful scene is not a cast.** A green wall is not green light. Estimate only from pixels that are already close to neutral, in more than one brightness zone.
- **Real light is amber or blue.** A cast that is mostly green or magenta is usually an object, a screen or a tube light. Correct that axis more gently.
- **Mixed light has no single answer.** A window and a lamp give two whites. Correct neither, or the larger one gently, and say so.
- **A warm room is not a fault.** Candlelight, sunset and a lamp-lit evening should stay warm. Correct casts, not moods.

## Matching shots

- **One reference per scene.** The longest shot with a face, or the widest. Everything else moves to it.
- **Match brightness before colour:** black point, white point, midtone, then balance, then saturation. Colour judgments change with brightness, so a colour match on a brightness mismatch does not hold.
- **Compare like with like.** Background to background, skin to skin. A close shot holds more skin than a wide one, so whole-frame averages differ even when the shots match.
- **Only match what is the same.** Same person in the same room: match. A different room or a different person: do not.
- **Do not match all the way.** Two angles differ for real reasons, such as a window in one. A full statistical match drains both.
- **Cuts expose mismatch.** Look at the pairs on the cuts sheet from `altocue watch`. A face that changes colour between two cuts of one take is a grading fault.
- **Inserts are part of the film.** A cutaway in a different colour world reads as a different film. Screen recordings are the exception: their colours are exact values someone chose.

## Looks

A look is built from four things:

- **A curve.** A soft toe so the darkest tones do not slam to black, a straight middle that sets contrast, and a shoulder that rolls highlights into white.
- **A colour split by brightness.** Shadows lean one way, highlights another, and the middle stays near neutral so skin survives.
- **A saturation behaviour.** Strong colours should go deeper, not brighter, and stop at a ceiling.
- **Small hue moves.** Greens toward olive, blues toward cyan. A few degrees.

Why warm skin against cool surroundings works: skin of every tone sits near orange, and the opposite side of the colour wheel is teal, so leaning what is not skin toward the opposite hue makes people stand off the background. It fails when neutrals are pushed too. Grey walls go blue-green and white shirts go cream.

Choosing:

- **Start with `natural`.** A balanced, matched picture is already most of a professional grade.
- **Match the look to the job.** Product and software: clean, neutral whites, true brand colours. Lifestyle: warm and soft. Brand film: contrast with a colour split. Documentary: natural contrast, quiet greens and blues. Night: cool shadows with warm lights kept warm.
- **Brand colours beat looks.** If a logo or product colour must be exact, stay with `natural` or `neutral_show`.
- **One look per film, one strength.** Turn a strong look down with `look_strength`. Most films want less than the full recipe.
- **See before choosing.** `altocue grade --compare` puts every look on one real frame.

Texture:

- **Grain goes last and stays low.** It belongs in the midtones, not in pure black or white, and it must change every frame. Heavy grain turns to blocks after a platform re-encodes.
- **Glow is for bright things on dark.** A lamp, a window, a highlight. Glow on everything is a filter, not a film.

## Delivery

- **Standard range for social.** Platforms convert HDR their own way, and an HDR master can arrive washed out. Altocue converts HDR itself so the result can be checked.
- **The same file looks different on different screens.** Players and displays disagree about gamma, so a file can look lighter and flatter on one and darker on another. That is the player, not the grade. Do not darken a file to suit one screen.
- **Phones are bright, small and seen in daylight.** The deepest shadow detail is the first thing lost to glare and compression. Hide nothing that matters at the very bottom.
- **Protect gradients.** Skies and plain walls band when stretched hard.

## The tells of an automatic or amateur grade

- Crushed blacks and clipped whites for punch. Hair and dark clothes become one black shape.
- Orange skin from pushed saturation, or grey skin from a look that drains it.
- Everyone the same brightness: dark skin lifted and light skin pulled down until faces match each other and not the scene.
- Teal in the neutrals.
- A look laid over a cast.
- Flicker from a correction that changes frame to frame.
- Shots that do not match because each was fixed alone.
- Milky lifted blacks with noise in them.
- Glow on everything.

## What to hand a person

Colour is judged on a real screen. Say which clips the report moved and by how much, which faces to look at, and that the grade was checked on frames and numbers, not on a calibrated display.
