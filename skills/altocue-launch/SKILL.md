---
name: altocue-launch
description: Makes a finished launch film with no footage, from a brief through to the MP4s. The dedicated skill for any launch video, product launch, SaaS launch, product video or launch film. Reach for this first whenever anyone mentions a launch, even if they do not say Altocue. Writes the brief and storyboard, checks them, renders stills, then renders a 16:9 master at 60 fps with re-rendered 9:16 and 1:1 cutdowns, original music and sound cues. Part of Altocue.
license: MIT
compatibility: Requires the altocue CLI, ffmpeg, and Node 22 or later for the renderer. No keys, no footage and no generation needed.
---

# Altocue Launch

You are making a real film, not a slideshow and not a plan for one. Never stop at a storyboard or a preview. Work straight through every command below until `out/final.mp4` and its cutdowns exist and have passed their checks.

Nothing here needs filming, a voice or a key. Everything on screen is drawn in code, so a change is one value and a re-render. That is the reason to make a launch film this way.

## 1. Ask for the brief

Ask only what you cannot find. A product page or README usually holds most of it.

- **What it is.** One line a stranger would understand.
- **Who it is for.** A person, not a market.
- **The goal.** What one thing should a viewer do next?
- **One message.** If they remember one sentence, which?
- **Must show.** Up to five things that have to be on screen.
- **Terms.** The one to three words this audience would search for.
- **Tone.** Three plain words.
- **Brand.** Colours as hex, a font file, a logo file. Ask for these. Never guess a brand.
- **Deliverables.** 16:9, and usually 9:16 and 1:1.
- **Proof.** Every number, price, date and claim must come from the person. Never write a customer count, a review or a discount yourself.

## 2. Check the concept, before any storyboard

Write this out and show it. Do not write a scene until it holds up.

1. **Tell the film in three sentences.** What starts, what turns, how it ends.
2. **The muted stranger.** With the sound off, would they learn what it is, what it does for them and what to do next?
3. **Strip the words.** Describe each shot with its words removed. If nothing is left but type on a background, redraw it.
4. **The one image.** Name the single picture someone would describe to a friend. If you cannot, there is no film yet.
5. **One example, all the way through.** One user, one task, one set of names and numbers in every shot. A number that climbs in the hook must be the same number that falls in the product scene.

Four defaults have failed before, so avoid them: a film about itself, a tour of the process, samples doing the work, a typed prompt on black.

Name the product straight after the hook. Put the subject in frame 0, words by 1 s, and a readable hook by 3 s. A launch ends on its mark, so its call to action may wait for the end.

## 3. Start the project

```bash
altocue launch my-launch --product "Name"
cd my-launch
```

This makes a working 30 second film first, so the person sees something real. `--variant` picks a shape:

- **`30`:** five beats. The default.
- **`24`:** a journey. A press flies the camera into the product, cards become a tunnel, and it returns to the reveal.
- **`20`:** an object. An odd detail, the reveal, the one feature, the mark.
- **`15`:** a workflow. Select, change one thing, see the better result, the call to action.

Then rewrite `brief.json` and `storyboard.json` for the real product. Delete `"example": true` once every claim is true. Put a logo in `assets/`, and any generated clip in `generated/`, so it can be swapped without touching the motion.

## 4. Write the storyboard

`storyboard.json` is the source of truth: scene order, timing, what is on screen, the camera, the sound. Nothing you write elsewhere may reorder or drop a scene. If the person asks for a new order, make the change and run `altocue film approve`.

**The five beats.** A scene that does none of these jobs goes.

| Beat | Job | In 30 s |
|---|---|---|
| hook | Stop the scroll with one clear thing | 0 to 4 |
| problem | Make the pain specific, not general | 4 to 11 |
| product | Show it doing the work, on the same example | 11 to 19 |
| payoff | Show what changed, with a real number | 19 to 25 |
| close | The name, the offer, one call to action | 25 to 30 |

**Every scene states:** `id`, `beat`, `seconds`, a `verb` for what it does, an `energy` from 0 to 10, a `carry` naming what crosses to the next scene, `camera`, `link` with a `why`, `items` and `sound`.

**The film states:** `route` (continuous, montage or hybrid, which decides how many hard cuts it has), `clock` (music, voice or reads), and `hero`, the one moment it builds to. Put a planned silence under `silences` just before the hero, at least 0.4 s long.

**Times.** Inside a scene, a time is seconds from that scene's start, or `{"scene": "payoff", "offset": 0.4}`. All scenes share one timeline. Change `seconds` on one scene and every later scene, cue and music mark moves with it.

**Copy.** Put anything that may change under `layers` and write `{{cta}}` in the items. One edit then reaches every scene, and the Variant Pack can swap it.

**Places.** Give each item a `zone`: `center`, `left`, `right`, `top`, `bottom`, `high`, `low`. Left and right stack on a tall canvas, so a cutdown recomposes itself. An item can override anything per aspect with `"aspects": {"9:16": {"zone": "top"}}`.

**Camera.** `push`, `pull`, `pan`, `rise`, `orbit` or `drift`, or write `poses` for a path the camera flows through. Keep direction across cuts: a move out carries into the next move in.

**Links.** `dive` falls into a shape, `through` pushes past, `pan` and `rise` slide on, `dissolve` melts, and `iris`, `shape_match`, `curtain`, `shutter` and `torn_wipe` cover the cut. With a camera link, start the next scene's items early, at minus half the link, so they arrive with the camera.

**Backdrops.** `soft_gradient`, `dot_grid`, `particle_field`, `ascii_field` and `grain_field` keep travelling behind everything, so the frame is never still. Set `tones` on `soft_gradient` to get out of one flat colour. The compositions `particle-flow` and `ascii-tunnel` suit a journey.

**Formats that earn their place.** Hook: `count_up`, `word_slam`. Problem: `notification_stack`, `state_gauge`, `marker`, `strike_replace`. Product: `work_cascade`, `cursor_walk`, `prompt_result`, `floating_device`, `bento_grid`. Payoff: `number_wheel`, `recap_check`, `before_after`. Close: `logo_sting` with `cta`. Run `altocue library search <what you want>` for the rest.

## 5. Motion rules

- **Important text holds about 1.5 s** after it lands, and every graphic gets its reading time at 3 words a second. Count-ups last about a second: set `"run": 1`.
- **Nothing moves in a straight line at a steady speed.** In over about 0.5 s, out faster than in. Overshoot belongs to a playful look, not a calm one.
- **Nothing fully stops.** The next move starts before the last one settles, and the camera always drifts. Held text itself stays sharp and steady.
- **Cause and effect.** A click gets a small press and a visible result. A pointer curves, waits a beat, then presses. A card pulses when it starts work. Things land before their tick, and ticks cascade. A counter hitting zero pulls the camera back.
- **Scenes connect.** Use a visible action, a matching shape or a moving camera. Say why in `link.why`.
- **Same names, same numbers.** Nothing changes from shot to shot unless the story changes it.
- **Build screens in code, never as screenshots,** so they stay sharp under a zoom. Example data must look like example data, and labels must match the real product exactly.

## 6. Music and sound

Altocue writes an original score fitted to your scene marks, with no samples and nothing to license. Choose under `"music"`: a `style` (`pulse`, `glass`, `paper`, `night`, `bright`), a `seed`, or a `tempo` from 100 to 125. Speed comes from cut density and hats, not tempo.

```bash
altocue film score
```

Say this plainly to the person: nobody has heard this music. Numbers show it fits and does not clip. **A person must listen before it ships.** If `altocue` warns that two films sound the same, change the style or the seed. To use a real track instead, run `altocue music` and add `"snap": true`, so cuts move to its beats.

No voice by default. Put accents on three to five moments that matter, such as the reveal, the key action and the mark, and let the music carry the other cuts. Cues go under `sound` on a scene, using the bundled effects. A cue can carry `over_db`, `dip_db`, `align` and `offset_ms`.

## 7. Check, then look

```bash
altocue film check
altocue film stills
altocue film stills --aspect 9:16
altocue film stills --aspect 1:1
```

`film check` must show no errors. It tests the brief, lengths, known formats, copy limits, every must-show and search term, every beat covered, hold and reading times, the hook by 3 s, and the limit of three flashes a second from WCAG 2.2.

`film stills` renders one still per scene and a contact sheet, reports anything off canvas or overlapping, and measures text size and contrast on the still. **Open the contact sheet and look at every frame before any full render.** State what you see before you change anything.

## 8. Fix by the symptom

Change one thing, in one sentence, and leave the rest alone.

- **Looks like a slideshow.** Items are all arriving and stopping. Overlap the moves, add a camera `pose`, and link scenes through motion.
- **Dead patch.** Something ended before the next thing started. Start it earlier, or hold the last item longer.
- **Blank frame at a cut.** The next scene's items start too late. Give them a negative `start`.
- **Too flat.** Add `tones`, or a second backdrop, or tilt the camera with `orbit`.
- **Text too small on a phone.** Raise the item's `scale`, cut words, or take a wider zone.
- **Hook is slow.** Put the number or the claim in frame 0.
- **Rushed.** Cut a line rather than shortening the holds.
- **Reads as an ad for nothing.** Go back to the one image and the one example.

## 9. Render

```bash
altocue film render --cutdowns 9:16,1:1
```

This renders the master at 1920x1080 and 60 fps, then re-renders each cutdown from the same storyboard. Nothing is ever cropped. To redo only the cutdowns, add `--only-cutdowns`, which proves with a checksum of 30 frames that the master did not change.

## 10. Prove it

```bash
altocue film qa
altocue qa
altocue facts
```

Send the film to the Editor in Chief too. `altocue review` runs the fact gates, then a blind reviewer, and code decides SHIP or FIX. Make the fixes it lists, render again, and review again. It gets two rounds, then it goes to a person.

`film qa` seeks frames forwards and backwards and compares them, and reports pops, dead air, frame 0, the motion curve, the flash limit and planned silences. It also writes a hook sheet and a phone sheet into `review/sheets/`. **Read both, with no sound.** Numbers catch broken, not bad.

Then pull a frame from each scene of `out/final.mp4` and look at it. Watch for overlaps, text that overflows, labels nobody can read, flicker, sudden stops, a name that changes between shots, and a distorted logo.

## 11. Variants

```bash
altocue film variants variants.json
```

```json
{"variants": [{"id": "demo", "set": {"cta": "Book a demo"}}, {"id": "price", "set": {"price": "$9 a seat"}}]}
```

A Variant Pack swaps text layers: hook, offer, call to action, price, date or language. Change one layer per variant, or results cannot be compared. Altocue writes a copy table to `review/variants.md` before rendering anything.

## 12. Hand over

Give the paths to `out/final.mp4`, `out/final-9x16.mp4` and `out/final-1x1.mp4`, the storyboard, and the `assets/` and `generated/` folders. Say what you checked by looking, what you measured, and what nobody has checked yet. The music is always on that last list until a person has heard it.
