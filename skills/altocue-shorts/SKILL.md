---
name: altocue-shorts
description: Gives a real talking-head edit the motion design of Higgsfield Shorts Studio (captions, graphics, cutouts and sound on the beat) while keeping the real person, then checks what comes back. Use when a Reel, Short or TikTok made from your own footage should look like a Shorts Studio preset, or when someone types /shorts about their own footage. Part of Altocue.
license: MIT
compatibility: Requires the altocue CLI, ffmpeg, and the Higgsfield MCP server, whose cloud sandbox runs Shorts Studio's own scripts.
---

# Altocue Shorts

Shorts Studio is the `/shorts` command of the Higgsfield MCP server. It designs a vertical reel around a talking take: hook-first motion graphics, captions, cutouts and sound, composed in HTML and rendered by its own engine in Higgsfield's cloud sandbox. By default it casts an AI presenter. Here the presenter is the real person: the take is the cut Altocue already made.

## Rules

- **The person on screen is real.** The take is supplied. Never generate, recast, lip-sync or redraw the presenter. `altocue shorts check` fails any run whose `reel.json` holds a generated take.
- **Uploading sends footage to Higgsfield.** Its terms let it train on uploads. Upload only footage the owner has agreed to send. Never a client's face without that client's yes.
- **Altocue's stages come first and last.** Cut, clean, grade and frame run before. Master, review and deliver run after. Shorts Studio designs the motion; it does not replace the edit.
- **Looking away gets covered.** `altocue shorts gaze -p .` finds where the eyes leave the lens (it needs MediaPipe; `altocue doctor` says how to add it) and writes `gaze.json`; pass it with `--cover`. The eyeline is judged against the lens, so eyes that only cancel a head turn are on it, and a blink is not a look-away. If the camera sits off to one side, set `[shorts] lens` to the head plus eye turn in degrees when looking down it. Each becomes a cover beat. The composition hides the presenter for the whole moment: put it inside a `data-layout="hidden"` scene, or under a full-screen graphic marked `data-cover`.
- **One sound pack, when the house has one.** `[shorts] sfx_dir` names it. With `sfx_only` on, only those files may play. Shorts Studio's library sounds fail the check.
- **House look, every Reel.** `[shorts] kit` names a folder of build helpers, such as a shared stylesheet and motion module. Each run gets them beside its `build.py`, and any image or subfolder in the kit goes into `media/`. `examples/shorts-kits/glass/` in the Altocue repo is a complete example kit, a light glass look with a layout lint. Use it only when asked: the person's own brand kit always comes first.
- **One take per recorded line.** From a project, each stretch between the cut's own joins is its own take, cut on those joins. Nothing re-trims the take inside a line: a second trim doubles the jump cuts and makes the delivery robotic. A line under 0.6 s, such as one word, joins the next one. Keep the takes in order.
- **The take is an image tag.** Show it with `<img data-video>`. A `<video>` tag fails the render, and the check fails it too.
- **House style on screen.** No em dashes or en dashes in any text, caption or card.
- **Generated objects look like one set.** When a Reel needs a made object (an icon-like 3D object, never a person), every prompt in the house opens with the same sentence describing material, light, angle, margin and a transparent background, then names one object, and ends with no text, letters, numbers, logos or people. Keep the sentence in the kit's README. Reserve each image in the Reel's project first (`altocue generate --reserve`), and register it after with `altocue generate --import` and its shot, prompt and model, so the spend and the source are on record.
- **A preset is a style reference, not a script.** `/shorts/<slug>` brings a published look. Keep the speaker's own words.

## Steps

1. Edit the reel as usual through `altocue plate`, so the cut, clean sound and grade are done.
2. Prepare the run:
   ```bash
   altocue shorts prepare --project . --cover gaze.json --sfx-dir ~/sounds/house-pack
   ```
   It writes `shorts/<slug>/` with `reel.json` (one supplied take per line of the cut, no job id), `script.md`, an `edit-plan.md` with a beat and cue per sentence and per cover, `HOUSE.md` with these rules, the pack in `media/sfx/`, the kit if one is set, and a zip of the folder.
3. Upload the zip once the owner has agreed: its media upload tool, PUT the bytes, confirm the upload, then download it into the sandbox and unzip it under `/home/user/shorts-studio/`.
4. Load `/shorts` (or `/shorts/<slug>` for a preset) with `get_preset_instructions` and follow its steps from measure onward on the supplied take: style reference, motion plan, measure, scaffold, composition, snapshots, render and its own check. Skip casting, take generation and voice. Place sounds from `media/sfx/` as audio tags. Read `HOUSE.md` first. Right after measure, run `altocue shorts fix-words <run>`: a word the recognizer missed at the start of a take is otherwise trimmed off. If it changed anything, measure again.
5. Pack the project with Shorts Studio's pack step, then add the run's `timeline.json` to the archive, since the check maps covers through it: `zip renders/composition.zip timeline.json reel.json`. Bring back `renders/final.mp4` and `renders/composition.zip`, and unzip the archive into the run folder.
6. Check it:
   ```bash
   altocue shorts check shorts/<slug>
   ```
   Add `--gaze` to scan the finished render too: it fails any look-away left on screen. Fix every failure in the composition and render again. A skipped check is not a pass. Then `altocue shorts frames shorts/<slug>` writes timestamped contact sheets into `review/`: look at every frame yourself before anyone else sees the Reel. A passing check is not a review.
7. Master, review and deliver the render with the usual stages.

## When to use something else

- No footage at all: `altocue film`.
- A generated host by choice: `altocue-ugc`.
- Graphics Altocue renders on its own machine: `altocue-motion`.
