---
name: altocue-frame
description: Reframes video to 9:16, 16:9, 1:1 or 4:5 with face-aware crops per shot, and adds punch-ins to hide jump cuts. Use when the user wants vertical from horizontal, a reframe, a crop, or zooms between cuts. Part of Altocue.
license: MIT
compatibility: Requires the altocue CLI.
---

# Altocue Frame

## Steps

1. Set the aspect with `altocue new --aspect`, or in `altocue.toml`.
2. Run `altocue frame` after `altocue cut`. It writes a crop and zoom into each segment of `edl.json`.
3. Change any crop by hand. `crop` is `[x, y, width, height]` in display pixels.

## How it decides

- **One static crop per shot.** Editors reframe per shot. Constant drifting reads as AI.
- **Faces get headroom,** with a dead zone so small head moves change nothing.
- **Two people:** both stay in if they fit. Otherwise the larger speaker.
- **Jump cuts inside one clip alternate zoom,** 1.0 then about 1.12, so the cut reads as a choice.

## When to change it

- A slow push-in on a key line: set `zoom_from` and `zoom_to` on that segment, such as 1.0 to 1.06.
- Keep it rare. One or two a minute.
