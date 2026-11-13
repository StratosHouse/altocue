# Changelog

## Unreleased

- **S sounds softened, never into a lisp.** The de-esser measured each clip's S sounds against each other and always turned the louder ones down, so a good microphone was treated like a harsh one and a voice could come out with a lisp. It now measures every S against the voiced speech around it and softens only one that stands near or above the voice: by at most 2 dB on `light`, the default, 3.5 on `normal` and 5 on `strong`, and never under 10 dB below the voice on any strength. It works in a bell around the speaker's own S centre, leaves the air above it, and leaves every sample away from an S untouched. An old project that says `strong` gets the new limits, and audio cleaned by the old de-esser is cleaned again. The clean report gives the S to voice level before and after, how many S sounds were softened and how many the guard held back.
- **Clipped peaks redrawn before the denoiser.** `altocue clean` finds runs held flat at the file's own extreme and redraws them with a curve fitted either side, then makes room under -1 dBFS. A clip with no clipping passes through untouched. The clean report gives the runs, samples and longest run.
- **Two sound gates, 35 in all.** `sibilance` fails a film whose S sounds were taken more than 5 dB or under 10 dB below the voice, and names the fix. `music_under_voice` fails music that sits closer under the voice than `[audio] music_under` asks, by more than 3 dB.
- **Effects kept off words, and on their frame.** The mix checks every effect against the words. One tied to a graphic (`"sync": "picture"`, which the house sounds are) keeps its frame and is ducked 6 dB, or shortened, where it would cover a word's first sound or the last half second of a line. One that is not may slide up to 0.2 s into clear air, and `motion.json` cues are now free by default. `align` takes `end` as well as `start` and `peak`, `start` now means the sound's onset rather than its first sample, and the hit sounds now start on their event while `rise` ends on it. A sound used again steps a little in level and pitch unless `"vary": false`. The mix report gives, for each effect, where it was asked for, where it landed, the frame, its zone and what was done.
- **Music that ends on a bar.** With `"end": "fade"` and a tempo that reads clearly, the mix moves the track up to a second so a bar line meets the last frame. The beat grid is found with NumPy alone, and the mix report gives the tempo, how sure it is, the beats and downbeats in film seconds, and where the ending landed or why it kept the old one.
- **Effects found by what they are for.** `altocue sfx for card arrives` ranks the bundled and catalogued sounds by purpose and gives a ready cue, with each sound's family, alignment and measured onset, peak and end. `--json` for agents.
- **No sound effect on the tail of a line.** `altocue shorts check` fails an effect that starts in the last half second of a spoken line or the breath after it, where it is heard as a stray noise on the voice, unless it starts with a scene.
- **Sound design in the skills.** A new craft reference, sound design, gives an event to sound table, placement to the frame, level and density rules, music cut to picture and a checklist. The sound reference now holds the voice chain in order, what each step sounds like overdone, the de-essing lesson, music under speech and delivery. `altocue-sound` now picks, places and levels.
- **Eight questions, asked once.** `altocue start` asks what kind of video you make, where it goes, your brand kit, captions, sound, language, whether anything may go online and how often to stop for a check, then never asks again. The answers live in `~/.altocue/profile.toml`, every new project starts from them, and `altocue start --show` prints them. With no terminal it prints one line and writes nothing, so an agent asks the questions in the chat with `--questions` and `--answer`. A new `altocue-setup` skill and a `/setup` command.
- **Ten kinds of video.** `altocue new --kind` starts a project with the settings that kind needs: short talking, long talking, tutorial, interview, clip, product demo, advert, testimonial, event or story. `altocue start --kinds` lists them, and each has a playbook in the craft skill: its structure, pace, graphics, sound and the mistakes that kind makes.
- **The agent watches its own film.** `altocue watch` turns the recordings or a render into sheets of stamped frames: every spoken line, both sides of every cut, every graphic at its start, middle and end, and the last seconds. `--at` looks at named seconds. An agent can now check a first frame for a blink, a graphic for a typo and an ending for a cold stop before a person does.
- **A craft skill: how an editor decides.** `altocue-craft` holds nine references an agent reads before it chooses anything: playbooks for the ten kinds, structure, where and why to cut, shots, colour, sound, motion, directing a shoot, and the passes to run on a finished cut. Every line is labelled `[evidence]`, `[source]` or `[thumb]`, so a rule of thumb is never dressed as a fact. It lists the tells of a machine edit, the tells of an automatic mix and starting numbers for motion.
- **Punch-ins by beat, not at every jump cut.** `altocue frame` now changes the shot size where a new beat opens and holds it for `[frame] min_hold` seconds, so the size means something. A join under an insert or one the eye cannot see never changes size. A zoom holds the eyes where they were. `[frame] push_rate` gives one eased slow push per held shot, capped at 0.06. `work/frame.json` lists every join with its kind and every bare jump with its time in the film. A source enlarged past 1.5 times is named as soft, and past 2.0 that clip plays without its punch-in. A replaced source file makes the framing stale.
- **Eleven new looks, 19 in all.** clean_commercial, product_white, warm_lifestyle, airy_bright, earthy_doc, moody_low, print_film, vintage_print, mono_silver, night_blue and neon_night. `teal_orange` now leaves greys, whites and lips alone. The skin guard is tested on five skin tones, and `bleach_bypass` keeps its guard on deep skin.
- **Log footage.** `input` under `[grade]` names the camera curve (apple_log, slog3, vlog, clog3, flog2 or dlog), and the clip is brought to a normal picture before the correction. It is never guessed: the grade summary says when a clip may be log and leaves the choice to you.
- **Grain that sits in the midtones.** Grain now fades in pure black and pure white, where film has none, and no longer lifts the blacks.
- **Faster, and it says how far.** `altocue run` records the time of each stage in `work/timings.json` and prints the slowest first. An unchanged graphics layer is not rendered again, and a render builds the timeline once. The faster-whisper model loads once per run. QA samples frames for text in one decode. Long encodes print a percentage and write `work/progress.json`. Setup downloads say their size and progress, and `altocue setup --speech-model` fetches the speech model ahead of the first run. `altocue status --help` no longer loads NumPy or OpenCV.
- **Pauses shaped by what they do.** `[cut] pace` is `tight`, `natural` or `relaxed`. Each pause is classed as inside a clause, at a comma, at the end of a sentence or at a change of topic, and keeps a length to match, so a film no longer has every gap the same. `max_pause` is now the longest pause inside a clause that is left whole. Breaths stay whole or go whole. `work/cut-review.md` lists the pauses and `work/cut-report.md` the shots under 0.5 s. Default cuts keep slightly longer pauses than before.
- **Fillers that belong stay.** `[cut] fillers` is `all`, `clean` or `none`. `clean`, the default, cuts a filler only where it lifts out cleanly, and leaves reactions and repeated content words for you to rule on. `remove_fillers` is still read.
- **An ending that holds.** The film holds after its last word for `[cut] end_hold` seconds (0.25, 0.5 or 0.8 by pace) on real frames where the take has them, then fades. `altocue facts` adds `ends_cold_s`.
- **Music lower under the voice.** `[audio] music_under` sets how far music sits under speech, 18 dB by default. It stays down across pauses under 1.2 s, so it no longer pumps between sentences, and a small dip near 2.5 kHz leaves room for consonants.
- **Captions that break where a reader would.** A page ends at a sentence, a new speaker, a cut or a pause, and inside a run it never ends on a word that leans forward, such as "the" or "in". `?` and `!` stay. A page stays up long enough to read: it joins its neighbour, holds into the silence after it or starts a little early, never across a cut, and the ones that still cannot are listed under `short_pages` in `work/captions.json`. Two-line captions are balanced, the same in both renderers. New `[caption]` settings: `max_words`, `max_chars` and `min_show`. `combine_ms` in a brand file now means the longest gap between two words on a page.
- **One safe zone for every vertical app.** A tall project that names no platform keeps its text inside a built-in `vertical` zone, the overlap of the Reels, TikTok and Shorts zones, so one file clears all three. `altocue new` writes `platform = "vertical"` for a tall project, and `altocue deliver --for vertical` works. Speaker names and boxed plates no longer push captions out of the zone.
- **A first run without the traps.** The brand kit is asked for before any slow work, and every stage that stops names both ways out. An unexpected failure prints three lines and keeps a log, and `ALTOCUE_TRACEBACK=1` shows the traceback. `altocue demo` no longer reports success without a film, and downloads work behind a proxy. `altocue new` checks everything before it writes, leaves nothing behind when it fails, and takes `--platform`, plain aspect words (`vertical`, `portrait`, `square`, `wide`) and `--brand house`. Commands find the project from inside it. `altocue doctor` leads with a verdict, and `altocue qa` shows failures first (`--all` lists every gate). `altocue status --json` gives every stage and the one command to run next, which is now the shortest route. Over MCP, quick commands run beside a render.
- **One rule turns a shot into frames.** Every stage now counts a shot's frames the same way, a segment under one frame is refused, and the duration gate allows one frame (two with blends) however many cuts there are. It also fails sound that outlasts the picture.
- **The look reaches B-roll, stills and generated shots.** Inserts take the project's look, with no skin correction, after any HDR conversion. `"look": false` keeps a spot's own colour.
- **A grade that holds.** The grade is measured once and stands through a re-cut, so colour does not drift between review rounds and a trimmed word no longer re-encodes every shot. `altocue grade --resample` measures again. A face still too dark or too bright after the 8 percent limit is named in plain words.
- **Smaller fixes.** No insert before 3 seconds, in the placer and in QA. `altocue run` refuses a range that covers no stage. Sound is cleaned again when word times change. The plate holds the last frame where the picture ends, not where the file does. 16 bit sound is rounded, not truncated.
- **A weekly sweep.** `scripts/sweep.py` reads `library/watch.toml` and reports each watched tool's newest release and licence against the pin, and the Monday library job prints it. `docs/SWEEP.md` is the routine: what to read, what may be adopted, what a person must hear or watch, and one pull request at the end.
- **Versions, compare, restore and timed notes.** `altocue versions save` keeps every decision file, a small review copy of the render, the user's own words and a change list worked out by code from the files: cuts removed, added, moved or trimmed with the words in them, graphics, inserts, transitions, music, sound effects, settings and script lines. `altocue versions diff` prints it as plain lines with timecodes. `altocue versions restore` puts an older version's files back, saving the current edit first when no version holds it, and never touches `sources/`, `work/` or `out/`. `altocue versions page` writes one file with no network: any version to watch, two side by side under one scrubber, each change a button that jumps there, and timed notes with freehand drawing. `altocue versions notes` reads the notes back, draws each drawing onto its frame, and a save with `--resolves` closes the ones it answered. `altocue versions serve` is an optional server on this machine only, standard library only, that saves notes as they are written. A save refuses a summary, prompt or change line that holds a key. Nothing is uploaded and nothing opens. `altocue status` shows the latest version and the open notes. A new `altocue-versions` skill.
- **Craft notes from a Reel's review.** The cut, motion, grade and transitions skills now say: let the last word's decay finish before a cut; show a feature on material made for it rather than the speaker's face, and choose any still of the speaker; match an on-screen number to the spoken one and show scale; grade and demonstrate a grade in order; and what a light leak and grain really look like.
- **Skin read against where skin usually sits.** `altocue grade` now notes a clip whose graded skin hue (CIELAB) lands outside 30 to 70 degrees, the same idea as a vectorscope's skin line, and says which way it leans. It is a note to look again, never a correction, since the room's light can be the reason.
- **Long graphics split, not just failed.** `altocue shorts check` fails a full-screen graphic over 10 s (`max_scene` in a kit's house.json) and says where to split it: on a spoken word near its middle, each half keeping only its own parts. Scenes that show the speaker are left alone.
- **No shot closes inside a word.** The mirror of the in point guard: recognisers end soft words early ("dollars" played as "doll" in a Reel), so `altocue cut` now reads the sound after every out point, and when 0.03 to 0.45 s of speech runs straight on from it, the edge moves on to the quiet after it, never past the next kept word. More than that is the next line, which stays cut. A new fact gate, `word_ends`, holds hand edits to the same rule; a shot that carries on from the same frame in the next one is left alone. The `altocue-cut` skill now says to choose whole lines in `script.md` rather than hand-write edges inside a line.
- **A take's edge words follow the sound.** `altocue shorts fix-words` now also moves a take's first word back to where its sound starts (up to 0.6 s) and its last word on to where the sound stops (up to 1.2 s), read at a softer level than speech, since the take is trimmed to those two words and a short edge is a lost sound. Run it again after every re-cut.
- **Stale cues named.** `altocue shorts check` fails any beat in `edit-plan.md` whose cue phrase its take no longer says, word for word. After a re-cut or a corrected transcript such a beat used to move or vanish without a word.
- **No shot opens inside a word.** A cut a few frames into a word loses its first sound, and the transcript cannot see it, because the recogniser's start is late in exactly those words. `altocue cut` now reads the sound before every in point: when 0.03 to 0.30 s of speech runs straight into it, the edge moves back to the quiet before it. More than that is running speech the edit removed on purpose, and stays cut. A new fact gate, `word_starts`, holds hand edits to the same rule, since nothing snaps them. Found on six Reels, where 17 line starts were clipped.
- **Mouth clicks out, and S sounds that do not cut.** `altocue clean` now turns mouth clicks and lip smacks down by 34 dB under short fades: a burst under 60 ms standing alone in quiet, with quiet before and after it, which a word's onset never is. With a transcript, a burst just after a word or just before one stays, since it may be the word's release or plosive. Nothing is cut and the length never changes. `--no-declick` or `[audio] declick = false` leaves them in. The de-esser no longer uses ffmpeg's `deesser` (see the next entry for how it works now). ffmpeg's `deesser` did nothing at gentle settings and took 12 dB a step up on a real voice, so it is no longer used. The clean report gives the clicks found, the most taken off the S sounds and the S band's peak before and after.
- **Shorts takes follow the cut's own joins.** `altocue shorts prepare --project` used to hand Shorts Studio the whole cut as one take, and its measure step then trimmed pauses the edit had already shaped, which doubled the jump cuts and made the delivery robotic. Each stretch between the cut's joins is now its own take, cut on exact frames, so only the dead air at a join is trimmed and nothing inside a line is touched. A line under 0.6 s, or one with no words, joins its neighbour, since a single word is too short to time or measure. The edit plan cues each sentence in its own take, covers are mapped through the take they sit in, and `altocue shorts check` reads runs made before this as one take.
- **Full graphics land on the footage's joins.** `altocue shorts check` fails a full-screen graphic that starts or ends within 0.7 s of a join in the footage without landing on it, since the picture would change twice in a second: a new line shows for a few frames before the graphic lands, or the speaker comes back mid-line. A join inside another look-away is left alone, since moving the edge there would cut that look-away in two. `join_reach` in a kit's house.json changes the reach.
- **Word times checked by the sound.** `altocue shorts check` used to call any gap between word times a silent face, but the recognizer sometimes ends a word early, so a gap can be on paper only. With the reel's voice in the composition, each gap is now heard, and one with no quiet stretch of 0.25 s is speech. New `altocue shorts fix-words <run>`, run after Shorts Studio's measure: a word the recognizer did not hear gets a time from the sound (from where the sound starts, to where it stops, or across the gap between its neighbours), so the measure no longer trims a missed first word off the take, and a long gap with no quiet in the sound is closed. It rewrites the take's transcript and deletes its measured file, so the next measure trims it again. `--dry` says what it would change.
- **Look-aways found against the lens.** New `altocue shorts gaze` reads a plate (or any video) with MediaPipe's face landmarker and writes the spans where the eyes leave the lens, ready for `shorts prepare --cover`. The eyeline is head turn plus eye turn, judged against the lens rather than against the take, so eyes that only cancel a head turn count as on the lens, and a take read from notes beside the lens is not turned around. A blink (0.3 s or less of the eyes only dropping) is not a look-away, but one at the edge of a look-away grows its cover until the eyes are up. `[shorts] lens` sets the eyeline for a camera off to one side. `altocue shorts check --gaze` scans the finished render as well and fails any look-away left on screen outside a hidden scene. MediaPipe is Apache 2.0 and optional: `uv tool install --force altocue --with mediapipe`; its 3.7 MB model is downloaded once and checked against a pinned hash.
- **Contact sheets of a Reel.** New `altocue shorts frames <run>` puts a frame of the render every 1.36 s (`--every`) on sheets of 8 by 3, each stamped with its time, in the run's `review/` folder, so a reviewer looks at every beat and can name the second a problem happens. The stamps are drawn with Pillow, so it works on ffmpeg builds without drawtext.
- **A glass kit to start from.** `examples/shorts-kits/glass/` is a complete `[shorts] kit`, Stratos's light Reel look: a brand row, frosted glass cards, 28 matching glass objects, the speaker in a rounded aperture, a full graphic over every look-away and a comment-keyword end card. It is an example and never the default, since your own brand kit always comes first: copy it and make its `house.json` yours. Its build stops if a look-away from `altocue shorts gaze` is left without a graphic, and writes the covers back so `altocue shorts check` reads them. `ALTOCUE_KIT_LINT=1` turns on a layout lint while you build, for text outside the safe area, a card off the edge, or an object over text. The objects are AI-generated with Higgsfield, labelled as such, with a prompt per file in `provenance.json`. `[shorts] kit` now copies a kit's subfolders into `media/` too.
- **Generated objects in a Reel look like one set.** The `altocue-shorts` skill now says how: one shared opening sentence for every object prompt (material, light, angle, margin, transparent background), one object each, no text or people, reserved in the Reel's project before it is made and registered after with `generate --import`.

## 0.5.1

Your brand kit first, Higgsfield Shorts Studio motion design on real footage with a check that holds a draft to the house look, and fixes found by a fresh install: the demo now runs on ffmpeg 5.1, deliver no longer fails on a subtitle cue a cut squeezed to nothing, and a cover laid on a carried take shows.

- **A cover laid on a carried take shows.** A full-frame take carried across scenes used to be drawn over every scene's camera, so a full-frame still, cover or card laid on it in a film never appeared. It now sits under the scenes. A take with a zone or its own box stays on top as before, and `"over": true` keeps a full-frame one on top on purpose.
- **Shorts Studio motion design on real footage.** `altocue shorts prepare` turns a finished cut into a run folder for Higgsfield Shorts Studio with the real take in the presenter's place, so it designs the captions, graphics and sound without generating a person. Look-away spans become cover beats that a full-screen graphic must hide, and a sound folder named in `[shorts] sfx_dir` becomes the only sounds the run may play. `altocue shorts check` reads what comes back and fails a generated presenter, a sound from outside the pack, an uncovered look-away, an em or en dash on screen, a `<video>` tag (which fails the Shorts Studio render), or a render of the wrong length. A look-away counts as covered inside a `data-layout="hidden"` scene too. `[shorts] kit` (or `--kit`) copies a folder of house build helpers into every run, so each Reel's build starts from the same look. The check also holds a first draft to what a reviewer would catch: the face on screen with nothing said, a flash of the face between two graphics, scenes under half a second, grain that is too strong or jumps every frame, a graphic cut before its words can be read, too many scene changes in a few seconds or sounds in one, caption words of no length or subtitle cues too short to read, and, with a `house.json` in the kit, the house end card, layout shares and faces. Nothing in Altocue talks to Higgsfield, and none of its scripts are copied. New skill: `altocue-shorts`.
- **No subtitle cue too short to read.** Where a cut left a cue almost no room, `altocue deliver` wrote a cue a few thousandths of a second long, read at tens of thousands of characters a second, and the muxer refused the film with "Invalid argument". A cue under 0.3 s now joins its neighbour in the same shot when the two fit, and one under a frame with nowhere to go is dropped.
- **Your brand kit comes first.** A new project makes no caption, graphic or render until it has read the owner's brand kit. `altocue new --brand <kit>` reads it as the project starts, and `altocue brand ingest <kit>` does it later: a folder with a logo, font files and colours written anywhere in it (brand.json, text, CSS or SVG), or a brand.json on its own. The kit is copied into `brand/`, its colours are given the jobs a brand needs (panel, ink, accent, surface, soft), and anything the kit leaves open is named, so nobody ships the house look by accident. `altocue brand ingest --house` chooses the house look on purpose, and `altocue brand show` prints what the project uses. `altocue run` stops before any stage when there is no brand. Projects made before this keep the brand they named.
- **Clean works on ffmpeg 5.1.** The loudness measure asked ebur128 for `framelog=quiet`, which needs ffmpeg 6 or later, so on Debian 12's ffmpeg 5.1 every `altocue clean`, and so `altocue demo`, stopped with "Invalid argument". It now asks for `framelog=verbose`, which every version takes and which keeps the log just as quiet.
- **Master works on ffmpeg 5.1.** There, `loudnorm` hands on audio with no channel layout and the limiter refused it, so every master failed. The chain now names the usual layouts first, and ffmpeg keeps the one with the same number of channels. With both fixes `altocue demo` runs clean to deliver on Debian 12's ffmpeg 5.1.

## 0.5.0

A moving crop that follows people, motion tracking, music off a voice, a sound effect library of single CC0 sounds, a catalog grown to 1231 entries, and long recordings proven end to end.

- **The crop follows when a still one fails.** `altocue frame` used to give every shot one fixed crop, so a 9:16 crop lost anyone who walked, and a wide shot of two people kept only one. With `[frame] follow = "auto"` (the new default) those shots get a moving crop. One person moving: the crop trails the head with a dead zone and eases after it, never faster than a pan. People taking turns: with speaker labels, each voice is matched to the face whose mouth moves while it talks, and the crop cuts to whoever is speaking, ignoring turns under 1.2 s. It uses the YuNet faces and sherpa-onnx speakers Altocue already has; no new model. The plate renders it with a sub-pixel slide on the film's own clock. A camera change inside a segment, common in an already-edited source, is found by the picture's tones and cuts the crop with it, and a shot with no face takes the centre instead of keeping the last shot's place; `altocue frame` names the seconds with no face so you can look. A segment with no face of its own, or a single stray detection, no longer gets a moving crop. `follow = "on"` moves every cropped shot, `"off"` restores the old behaviour, and a crop you set by hand always wins.
- **Motion tracking.** A new `tracked` list in `motion.json` puts effects and graphics on something as it moves: `blur` and `pixelate` for privacy, `spotlight` to dim everything else, a brand-styled `label`, or any PNG `sticker`. A target is a face (`"face"`, or the left or right one) or a box drawn at one time and followed both ways with optical flow. A face blur reaches below the detector's box to cover the chin. Tracking runs on the finished picture, after every crop and punch-in, and each item is baked once into a lossless layer and reused until the picture or the item changes. Nothing is uploaded.
- **Music off a voice.** `altocue clean` measures music under speech (the gaps between words stay loud and tonal) and, with Demucs installed, takes the vocal stem before the denoiser. Demucs is MIT and runs on your machine; it stays out of Altocue's own dependencies because it brings PyTorch: `uv tool install --force altocue --with "demucs>=4.1,<5"`. A split that takes more than 6 dB off the words is undone. `[audio] music = "auto"`, `"on"` or `"off"`. `altocue doctor` says whether it is installed.
- **Room echo, measured and, if you ask, reduced a little.** Every clean measures how long words ring on and warns when a room rings. `[audio] echo = "light"` or `"strong"` runs single-channel weighted prediction error dereverberation, in NumPy, in chunks so an hour stays light on memory. It is modest, under 1 dB off the tail of a synthetic 0.6 s room, so it is off by default. On a Mac the apple denoiser removes more.
- **A sound effect database.** 28 new sound sources. 22 were read on their own licence page on 2026-11-08; the other six say their terms were not read, and stay links. Free ones include 99Sounds, Sound Jay, Free Sounds Library, freeSFX, Shapeforms, NASA's mission sounds and the CC0 ChipTone generator, each with what it bars: most forbid redistribution, and Sound Jay forbids AI use. GameSounds.xyz is excluded, because it re-hosts bundles whose licence bars that. Paid stores and subscriptions (Epidemic Sound, Artlist, Soundly, Splice, BOOM Library, A Sound Effect, Pro Sound Effects, Soundsnap, Envato Elements, Motion Array and the Sonniss shop) are links by their plain address, with no affiliate code. Each has an empty `affiliate_url`, which validation keeps https-only and paid-only, and a tracking code in any source url fails the build.
- **Single sounds from open packs.** `library/sfx/packs.json` lists four CC0 packs on OpenGameArt: two from Kenney (Interface Sounds and Sci-Fi Sounds, Kenney's own uploads, which may be older than kenney.nl's) and two from rubberduck. kenney.nl now serves its archives only through short-lived signed links, so its other packs stay a link you visit yourself. `scripts/index_sfx.py` reads each page for the archive link, pins its sha256, measures every file with ffprobe and sorts it into a category such as click, pop, whoosh, swipe, riser, impact, glitch, typing, notification, ambience or jingle. `build_library.py` makes each sound a catalog entry of kind sfx to fetch, and `altocue library add <sound>` or `altocue sfx get <sound>` downloads the pack once into a cache, checks it, and copies only that file into the project with its licence notice. `altocue sfx search` lists them too. The index holds 323 sounds: 51 swipe, 39 impact, 32 ambience, 32 click, 24 notification, 21 laser, 17 foley, 10 glitch, 9 riser, 6 pop and 82 other. A pack it cannot fetch is reported and keeps its last rows, and the run exits non-zero, so one dead link cannot empty the index. No audio enters git.
- **`build_library.py --offline`** rebuilds the sources and the sound index while reusing the committed registry and shader rows, so a source change needs no network.
- **The library judges 87 more sources, checked on 2026-11-08.** With the sound sources above, the catalog grows from 801 entries to 908 and from 171 sources to 283. New open icon sets (Heroicons, Iconoir, Material Symbols, Bootstrap Icons, Feather, Ionicons, Radix, Fluent UI System Icons), OFL foundries (The League of Moveable Type, Velvetyne, Collletttivo), CC0 and public domain collections (Rijksmuseum, National Gallery of Art, Paris Musées, NOAA, USGS, Openclipart), charting libraries (Chart.js, ECharts, Vega-Lite, Plotly.js, Observable Plot), open models and tools (Wan 2.2, FLUX.1 schnell, Real-ESRGAN, Practical-RIFE), map layers (Overture Maps, deck.gl, geoBoundaries, Natural Earth III), code animation engines (Motion Canvas, Manim, Theatre.js core) and overlays (tsParticles, Vanta.js, grained.js, CC0 textures). Well-known paid libraries (Epidemic Sound, Artlist, Musicbed, Soundstripe, PremiumBeat, Storyblocks, Envato Elements, Shutterstock, Artgrid) are plain links with their terms summarised. Four are refused with their reason: Simple Icons (every icon is a brand mark), mobygratis (non-commercial only), HunyuanVideo (its licence excludes the EU, UK and South Korea) and ManyPixels (it bars compiling its art into a service or an app). Sound effects are unchanged.
- **Speakers are told apart on long recordings.** One clustering pass over a 27 minute call put every word on one speaker. Speaker turns now come from 5 minute windows, matched across the recording by voice likeness. The same call now splits 1028 s and 416 s. See [docs/LONG-FORM.md](docs/LONG-FORM.md).
- **Joins no longer open a gap on files with a chapter track.** Zoom recordings carry one, and every join added a frame: a 51.56 s short came out 52.16 s, 0.6 s behind its sound. Each encoded part now holds picture only.
- **Long recordings measured.** A 27 minute call went through transcript, speakers, clean, clip finding and three finished shorts, with wall time, memory and GPU for each stage in [docs/LONG-FORM.md](docs/LONG-FORM.md).
- **Product UI inserts with Stageshow.** `altocue stageshow record` films a [Stageshow](https://github.com/benjitaylor/skills) stage (windows, phones, dashboards, cursor walkthroughs) into an MP4 or a poster PNG. It injects Stageshow's own virtual clock before the page loads and steps it one frame at a time, so every render gives the same frames and a beat lands on a known frame of the edit. `--example` serves Stageshow's preview for the run; `--url` films any page that renders a stage. A sidecar marks the file illustrative, and the command prints the `motion.json` insert to place it. `altocue stageshow doctor` checks Node, the install, ffmpeg and a browser. New skill: `altocue-stageshow`.
- **Higgsfield catalog rechecked on 2026-11-07.** `/shorts` is now a full presenter-reel workflow (script, takes, captions, motion, music and a reviewed render), so the `shorts-studio` recipe describes it, with its synthetic-person truth gate and a per-second planning cost. Its text says to keep going without waiting; the recipe says to reserve first and hand it the reserved credits as its budget. New recipes: `shorts-presets` (the Shorts Studio style gallery), `color-grade` (20 movie looks or reference stills, LUT export, no paid generation) and `microdrama-ads`.
- **Seedance 2.5 at 1080p costs 16 credits a second, not 12.** Live quotes on 2026-11-07: 18, 42 and 96 credits for 6 s at 480p, 720p and 1080p. The hero routes and the `seedance-insert` recipe now say so.

## 0.4.0

Altocue installs as a plugin in any agent, not just from a terminal.

- One plugin, branded with the Altocue mark, for Claude Code, Claude Desktop and Cowork, Codex, Cursor and Gemini CLI. In Claude Code: `/plugin marketplace add StratosHouse/altocue`, then `/plugin install altocue@altocue`. In Codex: `codex plugin marketplace add StratosHouse/altocue`, then `codex plugin add altocue@altocue`. In Gemini CLI: `gemini extensions install https://github.com/StratosHouse/altocue`.
- An MCP server for agents with no shell, such as Cowork. Six tools: `altocue_doctor` checks the machine, `altocue_install` installs Altocue from PyPI only when the call says the user agreed, `altocue_run` runs any stage, `altocue_job` and `altocue_stop` follow and stop long renders, and `altocue_guide` serves the skills. Each skill is also a prompt. It uses only the standard library, so the plugin starts it before Altocue is installed. `altocue mcp` serves it from PyPI for any other MCP client.
- `/altocue:setup` walks the agent through install, extras and the demo, asking before each step that installs.
- The README and INSTALL.md lead with the plugin. The terminal steps stay, unchanged.
- A privacy page, and the support and privacy links a plugin directory asks for.
- Audio now follows the source clock. Some camera files carry AAC packet times that drift from the picture during a take, so word times and cuts slid away from the face. Extraction, cleaning and transcription now conform the audio to the packet timestamps without changing its speed. See [docs/SOURCE-CLOCK.md](docs/SOURCE-CLOCK.md).
- **Upgrading a project made before 0.4.0:** Altocue now refuses cached word times it cannot vouch for, instead of using them silently. Back up the project, run `altocue transcribe --force` and `altocue clean --force`, then check the cut against the original before you render. New projects need nothing.

## 0.3.0

- Add twelve directed presenter scenes through studio.json and the normal render command: authentic logo rosters, registered logo-to-page docking, voice-reactive sculpture, character motion, repo tours, reversible documents, comparisons, handoffs, parallel work and opening-brand endings.
- Add an offline geometric demo, copyable scene catalog, per-character sound timing, portable portrait covers, exact-source dialogue conforming and bounded final-transcript checks for repeated or missing phrases.
- Keep scene headings and product labels inside the house profile-mask allowance; invalidate a render when the Studio manifest or local source artwork changes.
- Require reusable Studio improvements to ship with product code, a reproducible example, render evidence and operator documentation.

- Add the original Studio collection: smooth type, depth, routes, linked-file handoffs, task selection, approval rules, edit diagrams and clear endings. All use bundled open fonts and require no purchased media.
- Include a portable agent prompt, real native JSON, use cases and optional sound notes for every built-in graphic, available through the CLI and exported live library.
- Tighten uncertain leading transcript silence only when the audio envelope provides sustained speech evidence. Preserve word ends and uncertain quiet/noisy attacks.
- Preserve explicit film graphic placement, including heading dimensions, instead of overriding it with a default zone.
- Keep adjacent clips and held captions on exact frame boundaries, preventing one-frame title overlaps and caption carryover onto an incoming face.
- Include settled-ending frames within the reviewer image budget. A short end card can no longer fall entirely between regular contact-sheet samples.
- Recognize explicitly declared graphic/still reading holds in film QA, while retaining frozen-video and missing-speech checks.
- Check visible composite faces under opaque graphics, and distinguish explicitly planned full-frame graphic holds from unintended freezes. Visible-face overlap and unplanned holds still fail.
- Promote native motion recipes in place of the B-roll feature tile. Existing CLI compatibility remains available.
- Public previews remain silent. No paid sound pack, private film or customer footage ships in the package.
- Use a sound pack you hold a licence for: `altocue sfx pack` registers its folder, `altocue sfx search` finds its sounds, and `altocue sfx get pack:sound` copies one into the project's git-ignored `sfx/private/` with its licence recorded, ready as a cue or a studio typing tap. Altocue never uploads or bundles a pack.
- Add the Studio recipe, the house process for filming, cutting, sound and review, linked from the README and the main skill.

## 0.2.0

Altocue becomes a full editing suite: a library, films with no footage, long video to shorts, deliverables and data checks.

### Films with no footage

- `altocue launch`: a dedicated launch film from a brief, with the `altocue-launch` skill. A 16:9 master at 60 fps, with 9:16 and 1:1 cutdowns re-rendered from the storyboard, never cropped
- `altocue film`: twelve film types, with `types`, `new`, `check`, `approve`, `stills`, `score`, `render`, `variants`, `study` and `qa`, and the `altocue-film` skill
- Plate Edit: one change to your own clip, comped back inside a region and a window, and diffed against the plate so untouched pixels stay as shot
- Song Cut: your own footage cut to a song, the cut and one title word on each hit you pinned by ear
- Invitation and Heritage: no generation, every name, date and place typed from an intake sheet in `brief.json`, and every reconstructed period labelled
- A film type can carry its own checks, run by `film check` and `film qa`, never as gates
- One-take films: a storyboard's `one_take` seams are held in black or white, the frame count is exact, and no cut falls outside a hold
- A storyboard is the source of truth, checked for beats, holds, reading time, the hook and flashes before anything renders
- Original music written in code and fitted to the scenes, with no samples and nothing to license
- Numbers a film shows are read from the tool, and `altocue film check` fails a film whose numbers have moved

### Motion and the library

- 126 original motion formats in 12 groups: kinetic type, products, paper, data, explainers, proof, teaching, film, maps, backdrops, isometric scenes and wipes
- `timeline_cut` lays a take's real words on a track, and each removal strikes and folds away as the playhead passes. `cut.track` reads them from a project's own words and `edl.json`
- `library_scroll` types a search, then scrolls a wall of real stock results, each with its licence on it
- `feature_badge` pops a feature's icon and name in a corner on the cut. The icons are Tabler's, MIT, with the notice kept, or drawn in code
- A film can play part of a clip's sound, take a picture from a frame of a clip, caption a raw take word for word with each cut tagged, read captions from the clip that carries the sound, scale its caption track, change one picture's shape in place, and start a chosen track on a downbeat so cuts snap to heard beats. Film render writes `out/credits.txt`
- `altocue broll --wall` keeps a frame and the licence of each stock result for a search shown on screen, and `grade --compare` keeps each look alone
- `altocue deliver` works on a film from a storyboard, and `--no-music` writes the film with voice and effects only, for an app's own music library
- `altocue library`: one searchable catalog with built-in, fetched, searched and linked tiers, a licence gate, a brand deny list and a weekly sync, and the `altocue-library` skill
- Any HyperFrames composition can be mounted as a graphic, and fetched items are kept self-contained, so a render needs the network only for a font that could not be embedded
- Copy longer than a format allows fails before the render

### Renderer

- Graphics render through HyperFrames 0.9.8, up from 0.8.222, with undici held at 7.30.1, which clears 29 security advisories
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

- `altocue deliver`: subtitles, chapters, thumbnails, loudness targets and a version for each platform, with the `altocue-deliver` skill. It delivers only a film with a SHIP verdict for that exact file. `--override REASON` is printed and recorded, and the claims and disclosure gates have no override
- `altocue deliver --stems`: voice, music and effects as WAV files
- `altocue timeline`: the edit in the HyperFrames studio, as a view. The studio is the one thing Altocue starts, and it opens no window

### Data

- `altocue facts`: measured facts about the finished film, never a score
- `altocue benchmark`: winners beside non-winners, from a CSV or your own key
- `altocue learn`: lessons from your own results, with paired hook tests
- Skills label each claim as evidence, a source's claim or a rule of thumb

### Generation

- `altocue generate --reserve`, `--release`, `--not-billed`, `--report` and `--allow-upload`: one spend cap per project. A run holds its whole estimate while it works, a cancelled job stays on the cap until billing shows no charge, and on the MCP route the cap holds when the agent reserves first
- `altocue recipes`: 37 hosted commands with what each costs before anything runs
- Fuller provenance on every import
- Claims and disclosure gates for a generated person, and `altocue qa --check ugc`
- A shot can take a motion video, such as a blockout, and a music track as a timing input, each wired only to the routes whose own docs list it
- `altocue direct` warns on a written timeline with gaps, settings in a prompt that differ from the shot's, a reference in the wrong state for the continuity ledger, and long shots in Higgsfield's own block formats
- A finished job's record is held against the request, and a take that differs is kept but never placed. `--import --record` does the same for a job made elsewhere
- `altocue generate --keep` records why a take was kept, provenance records the prompt the service actually ran, and three failed takes of one prompt stop a blind retry
- The `altocue-ugc` skill
- Higgsfield's terms checked to October 23, 2026, and Seedance 2.5 MCP routes

### The Editor in Chief and checks

- New gates: caption speed, brief coverage, flash rate, stray frames at insert edges, text readable, credits, claims and disclosure
- Banned words are checked in speech as well as on screen, and each finding says where it came from
- `altocue qa --check proof`: Product Proof Spot's label against a truth list, the product in frame, and hands clear at the end
- `altocue review --page`: one file with the film beside every gate, note and caption. It prints its path and opens nothing
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
