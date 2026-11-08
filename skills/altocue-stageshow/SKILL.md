---
name: altocue-stageshow
description: Films faux product UI built with Stageshow (browser and app windows, phones, dashboards, cursor walkthroughs) into a frame-exact video insert for a reel or film. Use when a video needs to show a product, an app or an agent doing something on screen and there is no real screen recording, or the real one is private, cluttered or slow. Part of Altocue.
license: MIT
compatibility: Requires the altocue CLI, ffmpeg, Node 20.19 or later, Chrome or Chromium, and the Stageshow skill from github.com/benjitaylor/skills with npm install run in its folder.
---

# Altocue Stageshow

Stageshow, by Benji Taylor (MIT), is a skill and React kit for believable product UI: a real-looking screen, simplified to what the viewer needs, framed with intent, where every motion has a cause. Altocue films a stage one fixed frame at a time, so the same stage gives the same frames everywhere and a beat lands on a known frame of the edit.

## Rules

- **It is an illustration, so label it.** Every Stageshow insert is a mockup. Put a small "Illustrative mockup" label on screen while it shows. Never present it as a screen recording of a real account.
- **No private data.** Names, emails, balances and logos on the stage are made up or approved by their owner. Check the poster frame before anything is shared.
- **The client's look, not Stageshow's.** Map the product's own type, colour and icons to the kit's tokens, as its looks reference says. A reel for a brand uses that brand.
- **Build it by Stageshow's own rules.** Read its `SKILL.md`, copy the closest gold example, and pass its rehearsal gate (its own `scripts/film.mjs`, in gate mode) before filming the insert. Altocue films; Stageshow's checks decide whether the stage is good.
- **Design for the frame it lands in.** A full-screen insert in a 9:16 reel wants a stage built at phone width (its `compact` design, filmed with `--width 390`). A window beside the speaker wants the stage at the size it shows.
- **Keep it short.** Two to five seconds on screen in a reel. Film a little more than you place, so a cut can breathe.

## Steps

1. Install Stageshow once with the skills CLI (`npx skills add benjitaylor/skills`, choosing stageshow), then `npm install` in the installed folder. Run `altocue stageshow doctor` until it says ready. Set `ALTOCUE_STAGESHOW` if the folder is somewhere unusual.
2. Build the stage with Stageshow's workflow: a plan, the closest gold example, the brief's product, then its rehearsal gate.
3. Film it into the project:
   ```bash
   altocue stageshow record --example hero-demo --project . --seconds 6
   altocue stageshow record --url http://127.0.0.1:5173/?capture=my-demo --project . --width 390 --dark
   ```
   `--example` serves Stageshow's own preview for the run. `--url` films any page you already serve. `--start` skips into the loop, `--still` writes the poster frame as a PNG, and `--fps` should match the project.
4. View a few frames of the result with your own file-reading tool. The sidecar JSON beside it records the page, frames and size, and marks it illustrative.
5. Place it with an insert in `motion.json`, timed to the spoken word, as `altocue-motion` describes. The command prints the entry to start from.

## When to use something else

- A real recording of the user's own screen exists and is clean: use it, cut and punch in with `altocue-frame`.
- A graphic, not a product: `altocue-motion` formats.
- A generated scene or person: `altocue-generate`.
