---
name: altocue-transitions
description: Adds transitions between shots, shader transitions, freeze frames, and texture overlays such as film grain, light leaks, dust, vignette and paper. Use when the user asks for a dissolve, a wipe, a whip, a dip to black, a freeze on a word, or a film texture, or when a cut needs to mark a jump in time or place. Part of Altocue.
license: MIT
compatibility: Requires the altocue CLI and ffmpeg. Shaders need Node 22 or later. Without it a shader becomes a dissolve and the film still finishes.
---

# Altocue Transitions

## When to use one at all

Most cuts stay hard. A hard cut is invisible, and a transition is a statement that something changed.

- **Talking head, same take, same thought:** hard cut. Hide a jump with a punch-in (`altocue-frame`) or B-roll (`altocue-broll`). A dissolve across a jump cut shows two mouths at once.
- **A real change of time, place or topic:** a transition earns its place. `dissolve` says time passed. `fadeblack` closes a chapter.
- **One style per film.** Two kinds at most. The same one each time reads as grammar, and a new one each time reads as a demo.
- **Short.** 0.2 to 0.6 seconds, as a rule of thumb. Under speech, keep it near a pause.
- **If you are unsure, leave the cut hard.**

## Transitions

Add to `motion.json`:

```json
{
  "transitions": [
    { "at": { "word": "next" }, "type": "dissolve", "duration": 0.4 },
    { "at": 12.5, "type": "gl-cross-zoom", "duration": 0.5 }
  ],
  "inserts": [
    { "file": "broll/street.mp4", "start": { "word": "city" }, "duration": 2.5, "in": "dissolve", "out": { "type": "wipeleft", "duration": 0.3 } }
  ]
}
```

- **`at` finds the nearest cut,** within 0.75 seconds. A transition with no cut near it is refused, and the error lists the cuts.
- **An insert's `in` and `out` sit inside its span,** so it covers no more of the film than it was given.
- **Built-in names** are ffmpeg's 58 xfade transitions. `dissolve` here is a smooth mix. ffmpeg's own pixel stipple is called `stipple`.
- **Useful ones:** `dissolve`, `fadeblack`, `fadewhite`, `wipeleft`, `slideleft`, `coverleft`, `smoothleft`, `zoomin`, `circleopen`, `radial`, `hblur`, `pixelize`.
- **An older ffmpeg may lack the newest names.** The error says so.

## Shader transitions

Shaders from gl-transitions run in WebGL on the HyperFrames stage and come back as a clip spliced into the picture. Each frame is rendered, so they cost seconds per transition.

These twelve ship with Altocue, each with its licence header: `gl-cross-zoom`, `gl-cube`, `gl-directional-warp`, `gl-dreamy`, `gl-film-burn`, `gl-glitch-memories`, `gl-linear-blur`, `gl-luminance-melt`, `gl-overexposure`, `gl-ripple`, `gl-swirl`, `gl-whip-pan`.

For anything else, find it and fetch it once. It stays in the project, so renders need no network:

```bash
altocue library search shader wipe --kind transition
altocue library add gl-wind
```

- **Settings are the shader's own defaults.** Altocue does not pass custom values.
- **A shader that needs a texture input is refused** by `altocue library add`.
- **With no renderer installed,** a shader cut becomes a `dissolve`, and the run says so.

## Flashes are a safety limit

- **[evidence] Three flashes or fewer in any one second.** WCAG 2.2, criterion 2.3.1, Level A. It is a web standard, and it is the floor here because a film can reach someone with photosensitive epilepsy.
- **A flash is a quick swing from bright to dark and back** over a large part of the frame. Saturated red has a stricter limit, so avoid it.
- **Watch these:** `fadewhite`, `white_flash`, `gl-film-burn`, `gl-overexposure`, `gl-glitch-memories`, a run of very short cuts, and any hosted effect that strobes.
- **One dip to white is one flash.** The danger is a run of them.
- **No gate measures this yet.** Look at frames across every fast run, and say in the delivery that the check was by eye.

## Timing: what moves and what does not

Nothing that QA measures moves. A transition or a freeze changes picture only.

- **The film keeps its exact frame count.** Sound is not touched, so word times, captions and graphics stay true.
- **A transition sits across the cut,** half before and half after. The outgoing shot plays on past its out point and the incoming shot starts before its in point, from the source footage. Where the source runs out, its last or first frame holds.
- **So picture from outside the cut shows during the overlap,** and lips there do not match the words. In a dissolve it stays under half strength, but look at it. If the handle shows a flub, shorten the transition.
- **A freeze overwrites frames.** The picture holds while the sound carries on, then returns exactly in step. Nothing after it shifts, and the film does not get longer.

## Freezes

```json
{ "freezes": [{ "at": { "word": "stop" }, "duration": 0.6 }] }
```

- **It holds the frame on that word.** Add `"offset": 0.1` to the `at` to pick a better frame. Mouths mid-word look wrong, so look at the frame.
- **Speech carries on over it,** so a long freeze feels like a stall. Stay under about a second, or land it on a pause.
- **A freeze cannot sit inside a transition,** and an insert laid over one hides it.

## Overlays

Texture made in code, drawn on the picture and under the captions and graphics. Nothing is bundled.

```json
{ "overlay": [
    { "type": "light_leak", "amount": 0.35 },
    { "type": "grain", "amount": 0.2 },
    { "type": "vignette", "amount": 0.3 }
] }
```

- **Types:** `grain`, `light_leak`, `dust`, `vignette`, `paper`. One overlay, or a list that stacks in order.
- **`amount` runs from 0 to 1.** As a rule of thumb, most films want 0.1 to 0.3. If a viewer notices it, it is too much.
- **Add `start` and `end`,** in seconds or on a word, to run one for part of the film. A timed light leak fades in and out.
- **Grain costs file size.** Heavy grain under a low bitrate on a platform's re-encode turns to mush, so stay low for social.

## Check it

1. `altocue render --draft`, then look at the frames across every transition and freeze.
2. `altocue qa`. A dip to black or a freeze you asked for is not a fault. Anything else still is.
