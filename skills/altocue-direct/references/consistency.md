# The consistency system (asset lock)

Character drift is never fixed downstream; it is prevented upstream. The
whole system, in order of leverage:

## 1. Reference images are the identity (nothing else is)

Never describe a recurring character from memory. Every generation that
contains a character attaches that character's canonical image:

- **Images** (nano_banana_pro / seedream): `medias` role `image`, one entry
  per character in the shot. Open the prompt with "The exact plush toy
  character from the reference image, IDENTICAL in every way:" then name its
  concrete features (material, eyes, limbs, props) so the text and the image
  agree.
- **Video** (seedance_2_0): the approved keyframe as `start_image` PLUS each
  character's canonical PNG as `image_references`. The start frame carries
  composition; the references hold identity across the clip's duration.
- Reference order (Higgsfield house rule): location first, then characters,
  then props. Hard cap ~7 references per call; trim props first, never a
  location or an on-screen character. Retries keep the exact same ordered
  medias.

## 2. The style formula, byte-identical

Write ONE 80-100 word style formula for the project (surface treatment,
palette, background treatment, light, render discipline, "non-photorealistic
handcrafted materials only"). Paste it byte-identical into every prompt.
Never restyle per shot; never let scene adjectives argue with it. Include a
PALETTE LOCK line ("only these colors, no foreign colors, no gradient
backgrounds") and pick ONE working accent color; 1-2 accent objects per shot
max, and let some shots breathe with none.

## 3. Character sheets multiply grip

For a character that will appear across many shots, build a locked sheet
once (turnaround: front / 3-4 / side / back, even spacing, flat neutral
background, in the project style) and use it alongside the canonical art.
Split-panel sheets (full body + tight close-up) lock faces hardest. Outfit
or state variants (wet, sleepy, dressed) get their OWN pre-baked sheet so a
state change never invents a new face. Higgsfield's character-sheet workflow
(`get_workflow_instructions({workflow:"character-sheet"})`) has the slot
architecture.

## 4. Framing protects identity

Characters live in medium and close shots; wides are world-only. A character
small in frame loses reference grip and drifts (fur appears or vanishes,
faces melt). If a beat needs scale AND a character, shoot two frames: a
world-only wide plus a character medium, and cut between them.

## 5. Words that fight the reference are bugs

If renders drift toward a texture or proportion the reference does not have,
the fix is usually a phrase in the prompt (or in a stored element
description) quietly arguing with the reference. Hunt it and delete it.
Hard blocks ("CRITICAL MATERIAL RULE... absolutely NO fur" or its inverse)
are a last resort for group shots and interiors, where drift pressure is
highest; they work, but only use them to enforce what the reference already
shows.

## 6. Verify against the source, not against taste

Before presenting: put the render next to the reference PNG. Same material?
Same proportions? Same face? For video: extract first, middle, and last
frames and check all three (drift compounds over duration; the last frame
lies most). "It looks nice" is not the test; "it is the same toy" is.

## Known traps

- Multi-character shots and cozy interiors drift hardest; use the hard
  material block and fewer references per call.
- Stored platform assets (Higgsfield Elements) carry their own descriptions
  that inject text into renders; if they predate the current style contract,
  they fight it invisibly. Prefer direct image references you control.
- Analog clock faces, mirrored text, and specific hand-poses are unreliable;
  use digital displays, embroidered labels, or crop them out.
- A "benchmark" render chosen by taste can itself be off-model. The
  reference art is the benchmark; renders never get promoted to reference
  without an explicit decision.
