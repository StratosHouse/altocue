---
name: altocue-frame
description: Reframes video to any aspect, such as 9:16, 16:9, 1:1, 4:5, 4:3 or 21:9, with face-aware crops per shot, and adds punch-ins to hide jump cuts. Use when the user wants vertical from horizontal, a reframe, a crop, or zooms between cuts. Part of Altocue.
license: MIT
compatibility: Requires the altocue CLI.
---

# Altocue Frame

## Steps

1. Set the aspect with `altocue new --aspect`, or in `altocue.toml`.
2. Run `altocue frame` after `altocue cut`. It writes a crop and zoom into each segment of `edl.json`.
3. `altocue frame` prints which shots follow. Watch those first.
4. Change any crop by hand. `crop` is `[x, y, width, height]` in display pixels. It stays through a re-cut, stops any moving crop on that shot, and `altocue frame --force` clears it.

## How it decides

- **One still crop per shot, unless it fails.** Editors reframe per shot, and constant drifting reads as AI. With `[frame] follow = "auto"`, only a shot a still crop cannot hold moves: someone walking, or two people who do not fit.
- **A moving crop trails, then eases.** It waits out a dead zone and never moves faster than a pan.
- **Turns are cuts.** With speaker labels (`altocue transcribe --speakers`), each voice goes to the face whose mouth moves with it, and the crop cuts to whoever talks. Turns under 1.2 s are ignored.
- **Faces get headroom,** with a dead zone so small head moves change nothing.
- **Two people:** both stay in if they fit. Otherwise whoever speaks, or the larger face with no labels.
- **Jump cuts inside one clip alternate zoom,** 1.0 then about 1.12, so the cut reads as a choice.

## When to change it

- A slow push-in on a key line: set `zoom_from` and `zoom_to` on that segment, such as 1.0 to 1.06. It stays through a re-cut.
- Keep it rare. These are rules of thumb, not findings.
- A bolder jump: raise `[frame] punch_in`, then run `altocue frame`. That solves again around your hand edits. `altocue frame --force` replaces them too.

## After every reframe

- **Recheck captions, the product and the safe area.** [source] `altocue qa` covers `safe_zone`, `face_clear` and `graphics_clear`. No gate looks for the product, so view the frames with your own file-reading tool, never by launching an app.
- **A new shape is a new layout, not a crop.** `altocue deliver --for reels,youtube` re-renders the graphics for each shape. See `altocue-deliver`.
- **A hosted reframe paints new edges.** See `altocue recipes show reframe`. A crop is free, so try it first.
