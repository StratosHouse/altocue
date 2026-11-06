# Altocue design

Altocue turns raw footage, a long recording or a brief into a finished video from inside a coding agent. It is a command line tool plus a set of agent skills. Every step is a plain file you can read, change and re-run.

## Why it exists

- **Editing with an agent should not mean guessing.** Agents cannot watch video. They can read word-timed transcripts, measurements and frames. Altocue gives them those.
- **Most AI editors stop at cuts and captions.** A finished video also needs clean sound, correct colour, framing, direction, inserts, graphics, music, deliverables and someone checking the work.
- **Not every film starts with footage.** A launch film or an explainer can be drawn in code from a storyboard, and checked the same way.
- **Nothing ships on the editor's own word.** A separate Editor in Chief reviews every final, and code decides the verdict.

## Goals

- **Plug and play.** One line to install. `ffmpeg` is the only hard requirement.
- **Local first.** Footage stays on the machine unless the user turns on a hosted provider.
- **Works in any agent.** Claude Code first, then Codex, Cursor and Gemini CLI. The CLI works alone.
- **Reproducible.** Same inputs, same output. Every decision is written to a file.
- **Provable.** `altocue demo` takes our published demo take through every free stage on this machine, in about 3 minutes.
- **Honest.** Every report says what was measured, what was judged and what could not be checked.

## Non-goals

- **Not a timeline editor.** `altocue timeline` shows the edit in the HyperFrames studio as a view. `edl.json` and `motion.json` stay the truth. `altocue review --page` writes one file to watch and check a film in. Nothing in it edits.
- **Not a posting tool.** Altocue delivers files.
- **Not a hosted service.** Everything runs where the user runs it.

## Anti-goals

- **Never invent speech.** Captions and cuts come from what was actually said.
- **Never spend money silently.** Every paid generation shows its cost first and needs a yes.
- **Never upload silently.** Every hosted step names where the footage is going.
- **Never claim a check that did not run.**
- **Never open a window.** Altocue writes files and prints their paths. It never opens an application window, an editor or a browser on its own. The one exception is `altocue timeline`, which starts the HyperFrames studio at a local address it prints. Background command-line tools with no window are fine: ffmpeg, headless Chrome inside a render, npm during setup, and the codex and higgsfield CLIs when chosen.
- **Never predict.** The data stages measure, compare and learn. There is no virality score.

## The pipeline

```text
sources
  -> ingest      probe clips, rotation, frame rate, HDR
  -> transcribe  word times, fillers kept, speakers on request
  -> clips       optional: complete thoughts in a long recording, each cut into its own project
  -> cut         remove silence, fillers, stutters, retakes, or build from an edited script (EDL)
  -> clean       denoise, hum, tone, de-ess, level each phrase, room tone in the holes
  -> grade       HDR to SDR, white balance, exposure, shot match, looks and LUTs
  -> frame       any aspect up to 4:1, face-aware crop, punch-ins
  -> broll       spots that want a cutaway, filled from your clips, stock or generation
  -> direct      shot list for anything that needs inserts
  -> generate    images and video from model providers, behind a spend gate
  -> plate       one picture and sound edit, with transitions and freezes, no graphics
  -> caption     word-timed caption pages
  -> motion      formats from the library as transparent layers
  -> render      draft or final composite, with texture overlays under the captions
  -> master      two-pass loudness, true-peak limit
  -> qa          fact gates in code
  -> review      Editor in Chief verdict, SHIP or FIX
  -> deliver     subtitles, chapters, thumbnails, stems and a version for each platform
  -> facts       measured facts, never a score
```

A film with no footage takes a shorter road to the same checks:

```text
brief and storyboard
  -> film check  beats, holds, reading time, the hook, flashes, every number read from the tool
  -> stills      one still per scene and a contact sheet, before any full render
  -> score       original music fitted to the scene marks
  -> render      a master, then each cutdown re-rendered from the storyboard
  -> film qa     seeking, pops, dead air, frame 0, planned silences, hook and phone sheets
  -> qa, review, deliver and facts, as above
```

## Three layers, one composite

- **Plate.** The picture and sound edit, made with `ffmpeg`. No graphics. Fast, deterministic, no browser.
- **Layers.** Captions and motion graphics rendered as transparent video from a neutral JSON spec.
- **Composite.** `ffmpeg` lays the layers over the plate. Changing a caption never re-encodes the edit.

The core never depends on a rendering engine. A `Renderer` turns a spec into layers.

- **`hyperframes` (default for full motion).** Apache-2.0. Pinned exactly. Telemetry off by default.
- **`basic` (fallback).** Pillow and `ffmpeg` only. No Node, no browser. Captions and cards with simple motion.

Remotion is deliberately not a dependency. It is source-available, and organisations of 4 or more people need a paid licence. Altocue keeps every required dependency under an open licence so a team of any size can use it. An adapter can be added later through the same interface.

## Avoiding the Homebrew `ffmpeg` trap

Stock Homebrew `ffmpeg` has no `drawtext`, `subtitles` or `zscale`. Altocue uses none of them.

- **Text** is drawn by Pillow or the browser.
- **HDR to SDR** is a 3D LUT computed in Python and applied with `lut3d`.
- **Timecodes on review sheets** are drawn with Pillow.

## Files in a project

```text
my-video/
  altocue.toml        project settings: aspect, fps, brand, providers, limits
  brief.md            the goal and the audience, in the user's words
  consent.json        this project's yes to uploading footage to a named provider, if given
  sources/            links to the original files, never modified
  work/
    media.json        probe results
    words/            one word file per clip
    audio/            cleaned audio per clip
    luts/             one .cube per clip
    layers/           transparent layers
  edl.json            the cut: every kept range, with a reason for every removal
  script.md           every sentence, to edit and cut from
  motion.json         the graphics, transitions, freezes and overlays
  broll.json          the B-roll picked for each spot
  shots.json          inserts to generate, if any
  timeline.json       resolved output timeline
  library.lock.json   every item fetched from the library, with its licence
  motion/fetched/     fetched items and their licence files
  clips/              candidates, and one project per clip cut
  out/
    draft.mp4
    final.mp4
    credits.txt       what the film must credit
  deliver/            subtitles, chapters, thumbnails, stems and platform versions
  review/
    pack/             contact sheets, cut frames, timeline sheets, numbers
    report.json
    REPORT.md
    page.html         the film beside its gates, notes and captions, in one file
```

## Key contracts

- **`Clip`:** path, duration, fps, size, rotation, HDR transfer, has audio.
- **`Word`:** text, start, end, confidence, kind (`word`, `filler`, `cue`).
- **`Edl`:** ordered segments of `clip, start, end, zoom_from, zoom_to, gain, crop, label`, plus `removed` entries with `kind`, `evidence` and `decision`. A segment's `held` list names what was set by hand, so `frame` leaves it and a re-cut carries it.
- **`Shot`:** a shot to generate. `synthetic` flags a generated person or place, and `script` holds any words it says, so the honesty gates can read them.
- **`Timeline`:** the EDL resolved to output frames, with mapped words and inserts.
- **`OverlaySpec`:** engine-neutral list of `type, start, end, text, style`.
- **`GateResult`:** `id, passed, value, threshold, detail`.

## Cutting

Three layers, each with evidence.

1. **Certain.** Word gaps, filler tokens, spoken cues such as "redo" and "start again".
2. **Candidates.** Repeat runs, false starts and stutters found by fuzzy word-run matching. Each carries a span, evidence, score, decision and reason.
3. **Judgement.** The agent reads a packed transcript and rules on anything marked `review`. With no agent, those stay in.

Edges snap to real speech with a 20 ms RMS envelope. Code refuses an edit that cuts through a word. Every render is re-transcribed, and the result must match the expected words at 95% or more.

## Audio

- **Denoise:** Apple voice isolation where the Mac has it, otherwise `deep-filter`, otherwise `afftdn`. `audio.denoiser` overrides. A guard rejects any engine that takes speech.
- **Hum:** measured by FFT, notched only when found.
- **Phrases:** gain moves only under speech, up to +9 and -6 dB. Pauses stay at unity, so room tone and breaths are not pumped.
- **Tone:** high-pass, gentle EQ, de-esser, compressor.
- **Cuts:** short fades at every join so no clicks, and the clip's own room tone in any hole the voice leaves.
- **Music:** ducks on a smooth ramp that starts before the word. Its own end lands on the last frame.
- **No voice:** the music leads, effects are levelled against it, and fades are tight.
- **Master:** two-pass `loudnorm`, then a true-peak limiter. Default -14 LUFS and -1.5 dBTP. The QA gate allows 0.5 dB above that, because AAC can overshoot a limiter.

## Colour

- **Bounded and per clip.** One correction per clip never flickers. Changes are capped.
- **Face-anchored.** Exposure reads the face, not a bright window.
- **Skin wins.** If a correction moves skin hue too far, it backs off. A look backs off at skin too, but never to nothing.
- **Looks are code.** Eight film looks are written as maths, or the user brings a `.cube`. Strength is one number.
- **Baked to a LUT.** The maths is testable Python. The render is one `lut3d` filter.

## Framing

- **YuNet** finds faces. Its model is MIT and ships with Altocue.
- **Per-shot crop with a dead zone.** Editors reframe per shot. Constant drifting looks like AI.
- **Punch-ins** alternate on jump cuts to hide them.
- **Any aspect.** Any W:H up to 4:1 each way. Past that there is no room for a face, a caption or a safe zone.

## Motion and the library

- **Formats are original CSS.** Each lives in `src/altocue/motion/formats/`, is pure in time so any frame can be sought, and declares its slots and copy limits. Copy longer than a slot fits is an error before the render.
- **One catalog, four tiers.** Built in ships with Altocue. Fetch is open items from the HyperFrames registry and GL shaders, copied into a project with their licence files. Search is a live service. Link is a pointer only, with no items indexed from a closed site.
- **Built-ins are read live.** The catalog lists formats from this install, so a new format is in the library without a rebuild.
- **A licence gate and a deny list.** An item without an allowed licence, or one that rebuilds a brand, is refused, and the reason is kept. Excluded sources stay listed with their reason.
- **Offline by default.** Search, show, sources and stats never use the network. `altocue library update` fetches a newer catalog, and a weekly job rebuilds it as a pull request that a person reviews before it reaches main.
- **A render works offline.** Formats use only the fonts Altocue ships. Fetched items, the libraries they load and the fonts they name are saved in the project and listed in `library.lock.json`. Two exceptions: a face `library add` could not embed is fetched at render time when the cache lacks it, and headless Chrome is fetched on the first render if setup missed it.

## Films with no footage

- **The storyboard is the source of truth.** Scene order, timing, what is on screen, the camera and the sound live in `storyboard.json`. Nothing writes to it after `new`, and a new order needs `altocue film approve`.
- **One timeline.** Inside a scene, a time is seconds from its start. Change one scene's length and every later scene, cue and music mark moves with it.
- **Checked before it renders.** `film check` reads the brief, lengths, formats, copy limits, beats, holds, reading time, the hook by 3 s and the flash limit. `film stills` renders one still per scene before any full render.
- **Re-rendered, never cropped.** The master renders at its own shape. Each cutdown is rendered again from the same storyboard, and `--only-cutdowns` proves the master did not change.
- **Original music.** `film score` writes music in code, fitted to the scene marks. No samples, no model, nothing to license. Nobody at Altocue's end can hear it, so every report says a person must.
- **Live numbers.** A film that shows a count from the tool writes `{live:...}` tokens. `film check` reads each number again and fails a film whose numbers have moved.
- **A type carries its own checks.** A film type's record names them under `checks`. `kinds.py` runs them inside `film check` and `film qa`, never as gates, so the gate count is the same for every film.
- **Your footage inside a film.** Plate Edit lays a generated pass over your clip only inside a region and a window, and holds every other pixel to the plate. Song Cut holds each cut to a hit a person pinned by ear: Altocue has no stem or onset tool, and a beat grid can miss the real hit.
- **Facts from an intake sheet.** Invitation and Heritage read names, dates and places from `intake` in `brief.json`, as layers, so no fact is ever retyped. Their checks confirm each one reaches the screen exactly.
- **One take.** A storyboard's `one_take` names an exact frame count and a black or white seam on every scene but the last. The loop seam gate checks the holds and scans the whole film for a cut outside them.

## Clips

- **Checked, not forecast.** `clips find` reads only the transcript. Each candidate carries five plain checks: it opens a thought, closes one, has a hook, stands alone and stays on topic. There is no score to rank by.
- **Each clip is its own project.** `clips cut` writes one project per pick, on the shape asked for, reusing the transcript.
- **Flat memory.** Local engines take a recording a minute at a time, and transcribing refuses to start when memory or disk is short.

## Sound sources

- **Licence before download.** `altocue music` and `altocue sfx` show the licence and credit before anything comes down. Every download is recorded.
- **Credits are owed, not optional.** Render writes `out/credits.txt`, and the `credits` gate fails a film that owes a credit without it.
- **Bundled sounds are CC0.** Each is named by what it is for, with placement values in `sounds.json`.

## Deliver

- **The master is only ever read.** Everything lands in `deliver/`, so `out/` and `edl.json` never change.
- **A new shape is rendered, not cropped.** Each platform version gets its own framing and graphics, and is checked for level, length, safe zone, faces, graphics and format.
- **Stems add up to the mix.** `--stems` writes voice, music and effects as WAV files at their levels in the mix, before mastering.

## Data

- **Facts, never a score.** `altocue facts` measures the finished film: time to first word and text, hook words, cuts, the longest static stretch, caption coverage, opening loudness, reading time, safe zones and loop match.
- **Winners beside non-winners.** `altocue benchmark` compares against both, so a hook that is simply common does not look like a cause.
- **Learning has floors.** `altocue learn` shows no pattern under 20 posts and suggests nothing under 30. Paired hook tests are the only route past association.

## Generation

- **Provider interface,** with model ids in data, never in code. Two vendors retired video APIs in the last six weeks, so model ids must be able to change without a release.
- **Tiers, not models.** A shot asks for `draft`, `standard` or `hero`.
- **Adapters:** fal.ai by key, Higgsfield by CLI or through its MCP server.
- **Spend gate.** Estimate, show, wait for a yes. `spend_cap_usd` is one budget per project: runs, reservations and imports all count.
- **Reservations for the MCP route.** An agent spends through the MCP server, not Altocue, so it books money against the cap first. An import with no reservation is named in the report.
- **Consent for uploads.** `consent.json` at the project root records a yes to send footage to a named provider. Without it, an uploading recipe is refused.
- **Recipes in data.** `src/altocue/data/recipes.toml` describes each hosted command in our own words, with what it costs and when the price was read.
- **Provenance.** A sidecar next to every asset records prompt, model, seed, cost and date, the prompt the service says it ran, and why a take was kept.
- **Inputs only where a route takes them.** A motion video and a music track reach a model only through a route in `models.toml` that names a field for them, read from that provider's own docs. Everywhere else the shot is refused with a fix.
- **The record against the request.** A finished job's record is compared with what was sent. A take that differs is paid for and kept, and never placed.
- **No blind retries.** Three failed takes of one prompt stop the next one before any spend.

## B-roll

- **Code proposes, a person or agent decides.** `find_spots` scores runs of words for concrete nouns, a jump cut to hide and a long static stretch. It keeps out of the hook, the end and any graphic that carries meaning.
- **Three sources, nearest first.** The user's clips, stock, then generated shots.
- **Stock with no key.** NASA, Wikimedia Commons, The Met and Cleveland answer with no key. Pixabay comes first when the user has its free key. Each result's own licence decides, and a result with no clear licence never reaches a project.
- **Spots belong to one cut.** `spots.json` and `broll.json` record a hash of the EDL. A changed cut stops the render rather than land a picture under the wrong words.
- **Search sends words, never footage.** Pixabay results are cached for 24 hours, as its API asks.

## Editor in Chief

A reviewer that did not make the edit.

- **Stage 1, fact gates in code.** Loudness, true peak, black or frozen frames, duration, word match, caption match, caption reading speed, the lines the brief requires, text held long enough to read, safe zones, faces and graphics, flashes a second, stray frames at insert edges, credits owed, claims and disclosure for a generated person, format, silence, and the film checks: frame 0, dead air, pops, planned silences, cuts to plan and the loop seam, which also holds a one-take's seams. Banned words are read in speech as well as on screen. Any failure means FIX with no model call.
- **Named check sets.** `altocue qa --check ugc` and `--check proof` run extra sets asked for by name, never in a normal run. Proof holds a product's label to `product.json`, keeps it in frame, and looks for hands in the final hold.
- **Stage 2, judgement.** A reviewer scores nine dimensions from the file, the brief and the rubric. It never sees the plan or the edit reasons.
- **Code decides.** SHIP needs all gates passing, nothing below 3, a weighted score of 3.8 or more, and no blocker.
- **Every note needs a timecode.** Notes that point past the end are dropped.
- **Independent when you opt in.** Gemini sees the actual video. Without a key, a fresh-context agent reads the review pack and the report is labelled accordingly.
- **It sees sound as a picture.** A pack reviewer cannot hear or watch. Timeline sheets draw frames, captions, speech, waveform, loudness and cuts on one time axis, so level, silence and rhythm can be judged from a still. Tone, noise, music and lip sync still cannot.
- **Gate canaries.** Clips with planted faults prove each fact gate fires, and clean controls prove it stays quiet. They run in the test suite, on generated clips with a faked transcriber. They say nothing about any reviewer.
- **Reviewer canaries.** Our demo take edited by the real stages, so every film passes every gate, each with one fault only judgement can find, plus one clean film. A fresh reviewer gets only the pack, and a note counts if it names the right dimension at the right moment. A run proves what that one reviewer did on those films. It does not prove anything about real footage, another model or Gemini, and five faults are a smoke test, not a rate. `tests/reviewer_canaries.py` says how to run it, and the same limits.
- **At most two fix rounds,** then it hands the open list to the human.

## Agent packaging

- **One repo, plugin at the root.** `.claude-plugin/` for Claude Code, Claude Desktop and Cowork, `plugin.json`, `mcp.json` and `.agents/plugins/` for Codex, `.cursor-plugin/` for Cursor, `gemini-extension.json` for Gemini CLI. They share `skills/`, `agents/`, `commands/` and `assets/icon.png`, and every manifest carries the package version.
- **Skills use the portable frontmatter fields.** With a shell, the agent calls `altocue` on PATH.
- **One small MCP server, for agents with no shell.** `src/altocue/mcp.py` uses only the standard library and runs on Python 3.8 or later, so the plugin starts it from its own folder before Altocue is installed. It wraps the `altocue` command and adds no power of its own: every edit still goes through the command and its consent rules, it installs only when the call says the user agreed, and heavy jobs run one at a time. From PyPI the same server is `altocue mcp`, and the wheel carries the skills so its guides match.
- **No hooks and no `bin/`.** A hook runs on every session, and a top-level `bin/` keeps a plugin out of claude.ai and Cowork. A client that cannot start a local server ignores it, so the server never blocks a plugin from loading.

## Safety rails for a public repo

- **Nothing from client work ships.** Fresh git history, sample media only, and a CI blocklist.
- **No paid or unclear assets.** Fonts are OFL. Sound effects and music stings are CC0. Bundled shaders are MIT with their headers kept. Fetched items stay in the project that fetched them.
- **Keep media out of git.** The repo stays small enough to clone in seconds.
