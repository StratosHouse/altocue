# The consistency system (asset lock)

Character drift is never fixed downstream. It is prevented upstream. The rules here are [thumb] unless marked, and the labels are defined in [story.md](story.md). In order of leverage:

## 1. Reference images are the identity

Never describe a recurring character from memory. Every generation that contains a character attaches that character's canonical image.

- **In `shots.json`:** name the character in `characters`, the approved keyframe in `first_frame`, and each canonical image in `refs`. The start frame carries composition. The references hold identity across the clip.
- **For an image,** open the prompt with "The exact character from the reference image, identical in every way:", then name its concrete features, so the text and the image agree.
- **[source] Give each reference one job, and name it.** Identity, path only, mood only, wardrobe. Say what must not render, and which one wins when they disagree.
- **[source] Order: location first, then characters, then props.** Trim props first, never a location or an on-screen character. Retries keep the exact same ordered list.

### How many references

- **`altocue direct` warns past 7.** That is a craft limit: past it they fight. It is a warning, so a shot may go over with a reason.
- **What a model accepts is a separate number, and it varies.** **[source]** Posts report 9 on one model and up to 50 on another. Higgsfield's live model pages, read 2026-11-02, list the reference roles and give no count. So check the route before you plan around a figure, and treat a higher limit as room, not a target.

## 2. The style formula, byte-identical

- **Write one style formula for the project:** surface treatment, palette, background, light, render discipline.
- **Put it in `style` in `shots.json`.** Altocue adds it to every prompt byte-identical, so no shot can restyle by accident.
- **Never restyle per shot,** and never let scene adjectives argue with it.
- **Lock the palette:** only these colours, no foreign colours. Pick one working accent, on one or two objects a shot, and let some shots breathe with none.

## 3. Character sheets multiply grip

- **For a character across many shots, build a locked sheet once:** front, three-quarter, side and back, evenly spaced on a flat neutral ground, in the project style. `altocue recipes show character-sheet` has a hosted route.
- **Split-panel sheets, full body plus tight close-up, lock faces hardest.**
- **Outfit or state variants get their own pre-baked sheet,** so a state change never invents a new face.
- **Light the sheet for its job.** **[source]** Use ordinary room light if it must blend with real footage, and flat 18% grey if it must carry no light at all. Pick per project.

## 4. Framing protects identity

- **Characters live in medium and close shots. Wides are world-only.** A character small in frame loses its reference and drifts. `altocue direct` warns.
- **If a beat needs scale and a character, shoot two frames:** a world-only wide and a character medium, then cut between them.
- **Design around a drifting face.** **[source]** Tight frames or masks hide it. Never use these to hide who is shown in a real case.

## 5. Words that fight the reference are bugs

If renders drift toward a texture or proportion the reference does not have, the fix is usually a phrase quietly arguing with it. Hunt it and delete it. A hard material block is a last resort for group shots and interiors, and only enforces what the reference already shows.

## 6. Verify against the source, not against taste

- **Put the render next to the reference.** Same material? Same proportions? Same face?
- **For video, check first, middle and last frames.** `altocue direct --check <file>` pulls them to `work/checks/`. Drift compounds, so the last frame lies most.
- **"It looks nice" is not the test.** "It is the same character" is.

## Known traps

- **Multi-character shots and cosy interiors drift hardest.** Use fewer references per call.
- **Stored platform assets carry their own descriptions** that can argue with the current style. Prefer direct image references you control.
- **Analogue clock faces, mirrored text and specific hand poses are unreliable.** Use digital displays, or crop them out. `altocue direct` warns.
- **A render chosen by taste can itself be off-model.** The reference art is the benchmark, and a render only becomes a reference by an explicit decision.
