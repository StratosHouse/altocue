# Launch film notes

Detail for the `altocue-launch` skill: how to write each part of the storyboard, the motion rules, and fixes by symptom.

## Storyboard detail

**Every scene states:** `id`, `beat`, `seconds`, a `verb` for what it does, an `energy` from 0 to 10, a `carry` naming what crosses to the next scene, `camera`, `link` with a `why`, `items` and `sound`.

**The film states:** `route` (continuous, montage or hybrid, which decides how many hard cuts it has), `clock` (music, voice or reads), and `hero`, the one moment it builds to. Put a planned silence under `silences` just before the hero, at least 0.4 s long.

**Times.** Inside a scene, a time is seconds from that scene's start, or `{"scene": "payoff", "offset": 0.4}`. All scenes share one timeline. Change `seconds` on one scene and every later scene, cue and music mark moves with it.

**Copy.** Put anything that may change under `layers` and write `{{cta}}` in the items. One edit then reaches every scene, and the Variant Pack can swap it.

**Places.** Give each item a `zone`: `center`, `left`, `right`, `top`, `bottom`, `high`, `low`. Left and right stack on a tall canvas, so a cutdown recomposes itself. An item can override anything per aspect with `"aspects": {"9:16": {"zone": "top"}}`.

**Camera.** `push`, `pull`, `pan`, `rise`, `orbit` or `drift`, or write `poses` for a path the camera flows through. Keep direction across cuts: a move out carries into the next move in.

**Links.** `dive` falls into a shape, `through` pushes past, `pan` and `rise` slide on, `dissolve` melts, and `iris`, `shape_match`, `curtain`, `shutter` and `torn_wipe` cover the cut. With a camera link, start the next scene's items early, at minus half the link, so they arrive with the camera.

**Backdrops.** `soft_gradient`, `dot_grid`, `particle_field`, `ascii_field` and `grain_field` keep travelling behind everything, so the frame is never still. Set `tones` on `soft_gradient` to get out of one flat colour. The compositions `particle-flow` and `ascii-tunnel` suit a journey.

**Formats that earn their place.** Hook: `count_up`, `word_slam`. Problem: `notification_stack`, `state_gauge`, `marker`, `strike_replace`. Product: `work_cascade`, `cursor_walk`, `prompt_result`, `floating_device`, `bento_grid`. Payoff: `number_wheel`, `recap_check`, `before_after`. Close: `logo_sting` with `cta`. Run `altocue library search <what you want>` for the rest.

## A real track

With `"snap": true`, cuts move to the track's beats, counted from the top of the file. The film plays the track from its top, or from `"from"` seconds into it, as in `"music": {"path": "assets/music/track.mp3", "from": 12.4}`, so the beats it cuts on are the beats you hear. Pick `from` on a downbeat where the track is already moving. The music fades at the end. Without `snap`, a track is backtimed so its own ending lands on the film's last frame.

## Motion rules

- **Important text holds about 1.5 s** after it lands, and every graphic gets its reading time at 3 words a second. Count-ups last about a second: set `"run": 1`.
- **Nothing moves in a straight line at a steady speed.** In over about 0.5 s, out faster than in. Overshoot belongs to a playful look, not a calm one.
- **Nothing fully stops.** The next move starts before the last one settles, and the camera always drifts. Held text itself stays sharp and steady.
- **Cause and effect.** A click gets a small press and a visible result. A pointer curves, waits a beat, then presses. A card pulses when it starts work. Things land before their tick, and ticks cascade. A counter hitting zero pulls the camera back.
- **Scenes connect.** Use a visible action, a matching shape or a moving camera. Say why in `link.why`.
- **Same names, same numbers.** Nothing changes from shot to shot unless the story changes it.
- **Build screens in code, never as screenshots,** so they stay sharp under a zoom. Example data must look like example data, and labels must match the real product exactly.

## Fix by the symptom

Change one thing, in one sentence, and leave the rest alone.

- **Looks like a slideshow.** Items are all arriving and stopping. Overlap the moves, add a camera `pose`, and link scenes through motion.
- **Dead patch.** Something ended before the next thing started. Start it earlier, or hold the last item longer.
- **Blank frame at a cut.** The next scene's items start too late. Give them a negative `start`.
- **Too flat.** Add `tones`, or a second backdrop, or tilt the camera with `orbit`.
- **Text too small on a phone.** Raise the item's `scale`, cut words, or take a wider zone.
- **Hook is slow.** Put the number or the claim in frame 0.
- **Rushed.** Cut a line rather than shortening the holds.
- **Reads as an ad for nothing.** Go back to the one image and the one example.

## Variants

```bash
altocue film variants variants.json
```

```json
{"variants": [{"id": "demo", "set": {"cta": "Book a demo"}}, {"id": "price", "set": {"price": "$9 a seat"}}]}
```

A Variant Pack swaps text layers: hook, offer, call to action, price, date or language. Change one layer per variant, or results cannot be compared. Altocue writes a copy table to `review/variants.md` before rendering anything.
