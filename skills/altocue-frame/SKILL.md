---
name: altocue-frame
description: Reframes video to any aspect, such as 9:16, 16:9, 1:1, 4:5, 4:3 or 21:9, with face-aware crops per shot, and plans punch-ins by beat so a change of shot size has a reason. Use when the user wants vertical from horizontal, a reframe, a crop, zooms between cuts, or a jump cut dealt with. Part of Altocue.
license: MIT
compatibility: Requires the altocue CLI.
---

# Altocue Frame

## Steps

1. Set the aspect with `altocue new --aspect`, or in `altocue.toml`.
2. Run `altocue frame` after `altocue cut`. It writes a crop and zoom into each segment of `edl.json`.
3. Read what it prints, then `work/frame.json`. It names the shots that follow, every jump cut left bare, and any clip too small for its crop.
4. Change any crop by hand. `crop` is `[x, y, width, height]` in display pixels. It stays through a re-cut, stops any moving crop on that shot, and `altocue frame --force` clears it.

## How it decides

- **One still crop per shot, unless it fails.** Editors reframe per shot, and constant drifting reads as AI. With `[frame] follow = "auto"`, only a shot a still crop cannot hold moves: someone walking, or two people who do not fit.
- **A moving crop trails, then eases.** It waits out a dead zone and never moves faster than a pan.
- **Turns are cuts.** With speaker labels (`altocue transcribe --speakers`), each voice goes to the face whose mouth moves with it, and the crop cuts to whoever talks. Turns under 1.2 s are ignored.
- **Check the speaker labels before trusting turn cuts.** If `altocue frame` says a conversation's crop follows the head, not the speaker, the diariser merged or split the voices, and the crop will not cut to whoever talks. Fix the labels, or set the crop by hand.
- **A camera change inside a segment cuts the crop too,** and a shot with no face takes the centre. `altocue frame` says how many seconds had no face; look at those, because the centre of a wide shot can be an empty gap. Set the crop by hand there.
- **Faces get headroom,** with a dead zone so small head moves change nothing.
- **Two people:** both stay in if they fit. Otherwise whoever speaks, or the larger face with no labels.

## Shot size

Each clip starts wide, at 1.0. The other size is `[frame] punch_in`, about 1.12.

- **The size changes by beat, not by cut.** It changes only where a new beat opens on a jump cut: the next word starts a sentence, or a retake or a pause of 1 s or more came out there. A filler cut out mid-sentence never flips the shot.
- **A size is held.** It stays for `[frame] min_hold` seconds before it may change. Leave the key out and `[cut] pace` sets it: 1.2 for tight, 2.0 for natural, 3.0 for relaxed. A beat that ends within 1.2 s gets no new size.
- **A join nobody sees is left alone.** That is a join under an insert that runs 4 frames past it on both sides, or one where the face barely moved.
- **The zoom holds the eyes still.** Each segment's `origin` is the eye point in display pixels, so the wide and tight versions of a shot keep the eyes in one place. A speaker at the edge of the picture still gets a punch-in.
- **With no transcript,** a second or more cut out counts as a new beat.

## Read the report

`work/frame.json` is where the stage says what it could not solve. `altocue frame` prints the same.

- **`bare_jumps`:** each jump cut that shows and keeps one size on both sides, with its time in the film as `stamp`, the two segments and `why`. For each one choose: cover it with an insert (see `altocue-broll`, then run `altocue frame` again), put back what was cut there (its id is in `work/cut-report.md`, and a keep ruling through `altocue cut --apply-review decisions.json` restores it), or leave it and say so. Never report a cut as clean while this list has entries you have not looked at.
- **`joins`:** every jump with its `kind`, which is toggle, covered, invisible or bare.
- **`upscale`:** how far the source's pixels are enlarged, per segment and as `worst`. Past 1.5 the picture looks soft, and the segments are listed under `soft`. Past 2.0 with the punch-in, that clip plays without one and is listed under `punch_dropped`. A 1920 by 1080 source filling 9:16 is already at 1.78. Offer the user `aspect = "4:5"` or a source with more pixels. This is a warning, not a gate.
- **`warnings`:** the same in sentences.

## When to change it

- **A line that must land:** `[frame] emphasis = ["the words as spoken"]` plays that beat tight, whatever the hold. It finds the line by its words, so it survives a re-cut, and `altocue frame` says when nobody says it.
- **Slower or quicker changes of size:** set `[frame] min_hold`.
- **A slow push:** set `[frame] push_rate`, per second, such as 0.008. One push runs over each held shot, carries across the joins inside it, eases in and out over 0.3 s, and stops 0.06 above where it began. The older `[frame] push_in` adds one amount to every segment, and applies only while `push_rate` is 0.
- **One shot by hand:** set `zoom_from` and `zoom_to` on that segment, such as 1.0 to 1.06. It stays through a re-cut.
- **A bolder jump:** raise `[frame] punch_in`, then run `altocue frame`. That solves again around your hand edits. `altocue frame --force` replaces them too.
- Keep all of it rare. These are rules of thumb, not findings.

## After every reframe

- **Recheck captions, the product and the safe area.** [source] `altocue qa` covers `safe_zone`, `face_clear` and `graphics_clear`. No gate looks for the product, so view the frames with your own file-reading tool, never by launching an app.
- **A new shape is a new layout, not a crop.** `altocue deliver --for reels,youtube` re-renders the graphics for each shape. See `altocue-deliver`.
- **A hosted reframe paints new edges.** See `altocue recipes show reframe`. A crop is free, so try it first.
