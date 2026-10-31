# Altocue design

Altocue turns raw footage into a finished video from inside a coding agent. It is a command line tool plus a set of agent skills. Every step is a plain file you can read, change and re-run.

## Why it exists

- **Editing with an agent should not mean guessing.** Agents cannot watch video. They can read word-timed transcripts, measurements and frames. Altocue gives them those.
- **Most AI editors stop at cuts and captions.** A finished video also needs clean sound, correct colour, framing, direction, inserts and someone checking the work.
- **Nothing ships on the editor's own word.** A separate Editor in Chief reviews every final, and code decides the verdict.

## Goals

- **Plug and play.** One line to install. `ffmpeg` is the only hard requirement.
- **Local first.** Footage stays on the machine unless the user turns on a hosted provider.
- **Works in any agent.** Claude Code first, then Codex, Cursor and Gemini CLI. The CLI works alone.
- **Reproducible.** Same inputs, same output. Every decision is written to a file.
- **Honest.** Every report says what was measured, what was judged and what could not be checked.

## Non-goals for v0.1

- **Not a timeline app.** There is no GUI editor.
- **Not a posting tool.** Altocue delivers files.
- **Not a hosted service.** Everything runs where the user runs it.

## Anti-goals

- **Never invent speech.** Captions and cuts come from what was actually said.
- **Never spend money silently.** Every paid generation shows its cost first and needs a yes.
- **Never upload silently.** Every hosted step names where the footage is going.
- **Never claim a check that did not run.**

## The pipeline

```text
sources
  -> ingest      probe clips, rotation, frame rate, HDR
  -> transcribe  word times, fillers kept
  -> cut         remove silence, fillers, stutters, retakes (EDL)
  -> clean       denoise, hum, tone, de-ess, level
  -> grade       HDR to SDR, white balance, exposure, shot match
  -> frame       aspect ratio, face-aware crop, punch-ins
  -> broll       spots that want a cutaway, filled from your clips, stock or generation
  -> direct      shot list for anything that needs inserts
  -> generate    images and video from model providers, behind a spend gate
  -> plate       one picture and sound edit, no graphics
  -> caption     word-timed caption pages
  -> motion      hook, chips, lower third, end card as transparent layers
  -> render      draft or final composite
  -> master      two-pass loudness, true-peak limit
  -> qa          fact gates in code
  -> review      Editor in Chief verdict, SHIP or FIX
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
  sources/            links to the original files, never modified
  work/
    media.json        probe results
    words/            one word file per clip
    audio/            cleaned audio per clip
    luts/             one .cube per clip
    layers/           transparent layers
  edl.json            the cut: every kept range, with a reason for every removal
  shots.json          inserts to generate, if any
  timeline.json       resolved output timeline
  out/
    draft.mp4
    final.mp4
  review/
    pack/             contact sheets, cut frames, numbers
    report.json
    REPORT.md
```

## Key contracts

- **`Clip`:** path, duration, fps, size, rotation, HDR transfer, has audio.
- **`Word`:** text, start, end, confidence, kind (`word`, `filler`, `cue`).
- **`Edl`:** ordered segments of `clip, start, end, zoom, label`, plus `removed` entries with `kind`, `evidence` and `decision`.
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

- **Denoise:** `deep-filter` when present, otherwise `afftdn`.
- **Hum:** measured by FFT, notched only when found.
- **Tone:** high-pass, gentle EQ, de-esser, compressor.
- **Cuts:** short fades at every join so no clicks.
- **Master:** two-pass `loudnorm`, then a true-peak limiter. Default -14 LUFS and -1.5 dBTP. The QA gate allows 0.5 dB above that, because AAC can overshoot a limiter.

## Colour

- **Bounded and per clip.** One correction per clip never flickers. Changes are capped.
- **Face-anchored.** Exposure reads the face, not a bright window.
- **Skin wins.** If a correction moves skin hue too far, it backs off.
- **Baked to a LUT.** The maths is testable Python. The render is one `lut3d` filter.

## Framing

- **YuNet** finds faces. Its model is MIT and ships with Altocue.
- **Per-shot crop with a dead zone.** Editors reframe per shot. Constant drifting looks like AI.
- **Punch-ins** alternate on jump cuts to hide them.

## Generation

- **Provider interface,** with model ids in data, never in code. Two vendors retired video APIs in the last six weeks, so model ids must be able to change without a release.
- **Tiers, not models.** A shot asks for `draft`, `standard` or `hero`.
- **Adapters:** fal.ai by key, Higgsfield by CLI or through its MCP server.
- **Spend gate.** Estimate, show, wait for a yes, enforce a per-run cap.
- **Provenance.** A sidecar next to every asset records prompt, model, seed, cost and date.

## B-roll

- **Code proposes, a person or agent decides.** `find_spots` scores runs of words for concrete nouns, a jump cut to hide and a long static stretch. It keeps out of the hook, the end and any graphic that carries meaning.
- **Three sources, nearest first.** The user's clips, stock from Pexels or Pixabay, then generated shots.
- **Spots belong to one cut.** `spots.json` and `broll.json` record a hash of the EDL. A changed cut stops the render rather than land a picture under the wrong words.
- **Search sends words, never footage.** Stock needs a free key. Pixabay results are cached for 24 hours, as its API asks.

## Editor in Chief

A reviewer that did not make the edit.

- **Stage 1, fact gates in code.** Loudness, true peak, black or frozen frames, duration, word match, caption match, format, silence. Any failure means FIX with no model call.
- **Stage 2, judgement.** A reviewer scores nine dimensions from the file, the brief and the rubric. It never sees the plan or the edit reasons.
- **Code decides.** SHIP needs all gates passing, nothing below 3, a weighted score of 3.8 or more, and no blocker.
- **Every note needs a timecode.** Notes that point past the end are dropped.
- **Independent when you opt in.** Gemini sees the actual video. Without a key, a fresh-context agent reads the review pack and the report is labelled accordingly.
- **Canaries.** Clips with planted faults measure whether the reviewer catches them.
- **At most two fix rounds,** then it hands the open list to the human.

## Agent packaging

- **One repo, plugin at the root.** `.claude-plugin/`, `plugin.json`, `gemini-extension.json`, `skills/`, `agents/`.
- **Skills use the portable frontmatter fields.** The agent calls `altocue` on PATH.
- **No hooks, no `bin/`, no MCP server in v0.1.** Each one narrows where the plugin can be used.

## Safety rails for a public repo

- **Nothing from client work ships.** Fresh git history, sample media only, and a CI blocklist.
- **No paid or unclear assets.** Fonts are OFL. Sound effects are CC0.
- **Keep media out of git.** The repo stays small enough to clone in seconds.
