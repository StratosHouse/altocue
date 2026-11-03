# Changelog

## 0.2.0

Altocue becomes a full editing suite: a library, films with no footage, long video to shorts, deliverables and data checks.

### Films with no footage

- `altocue launch`: a dedicated launch film from a brief, with the `altocue-launch` skill. A 16:9 master at 60 fps, with 9:16 and 1:1 cutdowns re-rendered from the storyboard, never cropped
- `altocue film`: eight film types, with `types`, `new`, `check`, `approve`, `stills`, `score`, `render`, `variants`, `study` and `qa`, and the `altocue-film` skill
- A storyboard is the source of truth, checked for beats, holds, reading time, the hook and flashes before anything renders
- Original music written in code and fitted to the scenes, with no samples and nothing to license
- Numbers a film shows are read from the tool, and `altocue film check` fails a film whose numbers have moved

### Motion and the library

- 123 original motion formats in 12 groups: kinetic type, products, paper, data, explainers, proof, teaching, film, maps, backdrops, isometric scenes and wipes
- `altocue library`: one searchable catalog with built-in, fetched, searched and linked tiers, a licence gate, a brand deny list and a weekly sync, and the `altocue-library` skill
- Any HyperFrames composition can be mounted as a graphic, and fetched items are kept self-contained, so a render needs the network only for a font that could not be embedded
- Copy longer than a format allows fails before the render

### Renderer

- Graphics render through HyperFrames 0.9.5, up from 0.8.222, with undici held at 7.30.1, which clears 29 security advisories
- Renders work offline: formats use only the fonts Altocue ships, and `altocue library add` embeds the fonts a fetched item names. A face it could not embed is fetched at render time, and headless Chrome on the first render if setup missed it
- Serif formats render in Instrument Serif, as designed. Renders had been drawing them in Inter
- The graphics page is marked `data-no-timeline`
- The weekly library sync says how far the HyperFrames pin is behind the latest release
- A registry item that shares a name with a built-in format stays out of the catalog

### Stock, music and effects

- Stock with no key: NASA, Wikimedia Commons, The Met and Cleveland, with Pixabay first when you have its key
- Stills as a slow push-in, and an opt-in Internet Archive source
- `altocue music`, `altocue sfx` and `altocue credits`, with the `altocue-sound` skill
- 20 CC0 effects bundled, 12 of them new, and 4 CC0 stings
- `out/credits.txt` on every render, and a credits QA gate
- Pixabay honours the shape you ask for. Slow zooms fixed on ffmpeg 8.1

### Cutting and clips

- Script mode: `altocue cut --from-script` builds a frame-safe cut from an edited `script.md`
- Hand-set gain, crop, zoom and order survive a re-cut, and anything lost is reported
- `altocue clips`: finds complete thoughts in a long recording and cuts each into its own project, with the `altocue-clips` skill
- Optional speakers: words can carry who said them, and captions can name or colour each voice
- Transcribing holds memory flat across file lengths and refuses to start when memory or disk is short

### Sound

- Apple voice isolation as a clean backend on a Mac
- Phrase-by-phrase levelling, room tone in the holes, a working de-esser, and breaths turned down on request
- Every bundled effect callable from `motion.json`, with wipes sounded at their midpoint
- Music ducks on a smooth ramp, and its end lands on the last frame
- A no-voice mode for films with no speech

### Colour, transitions and texture

- Eight film looks written as code, your own `.cube` LUT, look strength, and a comparison sheet
- 58 built-in transitions, 12 bundled shader transitions, and more shaders fetched through the library
- Freeze on any word while the sound carries on
- Film grain, light leaks, dust, vignette and paper, made in code, under the captions
- Any W:H aspect, up to 4:1 either way

### Deliverables

- `altocue deliver`: subtitles, chapters, thumbnails, loudness targets and a version for each platform, with the `altocue-deliver` skill
- `altocue deliver --stems`: voice, music and effects as WAV files
- `altocue timeline`: the edit in the HyperFrames studio, as a view. It launches nothing else

### Data

- `altocue facts`: measured facts about the finished film, never a score
- `altocue benchmark`: winners beside non-winners, from a CSV or your own key
- `altocue learn`: lessons from your own results, with paired hook tests
- Skills label each claim as evidence, a source's claim or a rule of thumb

### Generation

- `altocue generate --reserve`, `--release`, `--report` and `--allow-upload`: one spend cap per project, covering the MCP route
- `altocue recipes`: 37 hosted commands with what each costs before anything runs
- Fuller provenance on every import
- Claims and disclosure gates for a generated person, and `altocue qa --check ugc`
- The `altocue-ugc` skill
- Higgsfield's terms checked to October 23, 2026, and Seedance 2.5 MCP routes

### The Editor in Chief and checks

- New gates: caption speed, brief coverage, flash rate, stray frames at insert edges, text readable, credits, claims and disclosure
- `altocue review --page`: one file with the film beside every gate, note and caption
- Timeline sheets and pacing numbers in the review pack, so a reviewer given stills can judge rhythm
- Reviewer canaries on the demo take
- `altocue demo`: proves a machine works on our published demo take
- `altocue doctor` shows a capability table and checks disk. `altocue status` is wider

### Skills

- Craft rules sorted into the skill that uses them, each labelled evidence, a source's claim or a rule of thumb
- Safety and platform rules, and named fixes in `altocue-review` and `altocue-direct`
- `tests/test_skills.py` fails if a skill names a stage, flag, format, file or setting that does not exist
- `tests/test_readme.py` fails if a number in the README differs from the code

### Fixed

- A caption no longer holds past a cut
- Clips stay in the order given
- The heavy lock holds through the plate and the composite
- The parakeet extra installs
- Install commands, the OCR check, and two-word name fixes in `[transcribe.fix]`
- A segment that started exactly on a frame no longer starts one frame late

## 0.1.1

- Stock search leads with Pixabay, because Pexels has paused new API keys
- Published to PyPI, so `uvx altocue` works

## 0.1.0

First release.

- Word-timed cutting with silence, filler, stutter and retake detection
- Voice clean-up, mixing and mastering
- Automatic colour correction, including iPhone HDR to SDR
- Face-aware reframing and punch-ins
- Word-by-word captions and motion graphics from a JSON file
- B-roll: finds the spots that want a cutaway, then fills them from your clips, free stock or generation
- Directing skills and generation through fal.ai and Higgsfield, behind a spend gate
- QA fact gates and an independent Editor in Chief review
