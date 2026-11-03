# Notes by type

The craft notes for each film type, and the checks each one carries for itself. A type's own checks run inside `altocue film check` before anything renders, and inside `altocue film qa` on the master. They are not gates.

## The code-only types

- **`explainer`:** a familiar situation, a surprising fact, a simple explanation, the question the viewer now has, a one-line twist. Every number on screen must match a fact in the brief, with its source.
- **`demo`:** say how it was made near the start. Show each thing as it is named, and hold long enough to inspect. Keep estimates apart from what was measured.
- **`ad`:** one change per card. Never write a review, a result, a discount or a quote. Use only ones the person gives you. Render every shape with `--cutdowns`, and use `altocue film variants` for the offers.
- **`gauge`:** write the table of states first, then the lines. Use `state_gauge` with `"carry": true` so one panel stays on screen across scenes. If a number is exaggerated for reading speed, the brief's `gauge_note` must say so, and so must the screen.
- **`case`:** flat, dated sentences, and `corner_stamp` for the log. Anything reconstructed needs a label on screen. Never fake a real death, a disaster or a real person's words.
- **`line`:** one picture per spoken line. Real narration and a distinct idea in each video are what keep this format on the right side of YouTube's rules.
- **`music`:** beats come from the track. Add `"snap": true` to move cuts to them. Use your own words on screen, and confirm the rights to any track. With no track, Altocue writes a score, and a person must listen to it.

## Your own footage inside a film

- **`plate`, Plate Edit.** One change to your own clip, such as a pillar that turns to glass and back. Make the edit pass from the plate with every frame, cut and camera move locked, then put it under `comp` on the footage item: the pass, its `region` as shares of the frame, and the window `from` and `to` in plate seconds. Altocue lays the pass over the plate only inside that region and window, so the pass is never the shot. Start the change on a visible cue and end it before the plate does. `film check` wants one change, a still camera (`"camera": {"move": "drift", "amount": 0}`), nothing drawn over the plate, a pass as long as the plate, and a plate at the film's aspect. `film qa` holds every pixel outside the region, and the whole frame outside the window, to the plate.
- **`song`, Song Cut.** Your own clips cut to a song you have rights to, set as `music.path`. Altocue has no stem separator or onset detector, and a beat grid can miss the real crash, so pin each hit on the drums and bass by ear and write it under `hits` with `how`. `film check` fails a hit no cut lands on, more than one word on screen or a title off the first hit, and any shot under the shortest the post found readable. It warns on snapping to a beat grid and on sound effects over the song. `film qa` looks for a cut in the picture on every hit and checks the master is bright enough for a phone. Direction across cuts and text in the edge band are not measured: watch for both.

## Facts from an intake sheet

- **`invite`, Invitation.** Fill `intake` in `brief.json` with the facts exactly as the hosts gave them: `names`, `date` written as 2031-05-17, `venue` and `rsvp`. The film types them from there, so the storyboard writes `{{names}}`, `{{date_text}}`, `{{day}}`, `{{venue}}` and `{{rsvp}}` and never retypes a fact. Every fact must reach the screen exactly, the date must be ahead, and any weekday shown must be the date's own. Use the hosts' own photos, and have them approve the card before anything is shared.
- **`heritage`, Heritage Film.** The founding facts go in `intake` and the periods, in order, in `eras`. Each scene names its `era`. A scene that shows a period nobody filmed at the time is a reconstruction: mark it `"reconstructed": true` and label it on screen, such as a `corner_stamp` with `"label": "Reconstruction"`. Going back to an earlier era needs `"flashback": true`. Real faces and real records are the anchor. Never invent a person's words.

## One take

Any film can declare `"one_take": {"frames": 720}`: one unbroken move made of segments, at an exact frame count. Every scene but the last names a `seam`, `black` or `white`: what fills the frame as it ends, such as a mouth closing over the lens or a flash of sun. Seams are plain cuts, and each scene's camera stays still. `film check` fails a missing seam, a link at a seam and a length off by a frame. `altocue qa` holds every seam in its colour for ten frames each side, and fails any cut it finds outside a hold. Light seams hold across takes, and object seams often do not. If a segment fails in its last second, move the seam earlier to the nearest black or white.

## Split screen

The zones `half_top` and `half_bottom` are the two halves of the frame, edge to edge. Footage in one is bled to its edges, holds still under every camera, and gets a thin accent line on the seam. Set `"focus": 0.3` to keep a face in the crop, as a share down the picture. Write `"captions": {"place": "seam"}` to centre the captions on that line, large, and clear of the face. Put the graphics in `top` or `bottom`, inside the other half. A presenter who talks across several scenes is one footage item with `"end": {"scene": "last", "from": "end"}` and `"carry": true`. Bring the take in as `altocue deliver --textless` made it. If the footage starts part way in with `"from"`, give its scene the same `"voice_from"`, so the sound starts there too.
