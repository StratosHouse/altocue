# The weekly sweep

Altocue stands on tools, models, sound packs, motion blocks and platform rules that change every week. The sweep is how it keeps up without anyone having to remember to look. It is one routine, run once a week by an agent or a person, and it ends in one pull request that a person reads before it merges.

A sweep never releases anything. A merge is not a release, and a release is a separate decision by the maintainer.

## What a sweep produces

- **One pull request**, titled `Weekly sweep, <date>`, with small commits by area.
- **A short report** at the top of that pull request: what moved, what was adopted, what was held and why, and what a person must listen to or watch before merging.
- **An updated backlog** at the foot of this file.

If nothing worth changing was found, the sweep says so in one line and opens nothing.

## The rules a sweep works under

These are the same rules as the rest of the repository. `AGENTS.md` has them in full.

- **Open licences only.** Read the licence file itself, not the package page. Nothing GPL or AGPL is linked into the package. No non-commercial, research-only or "source available" weights, sounds or code. A licence that changed for the worse is a reason to hold a version or drop a source.
- **ffmpeg stays enough.** A new tool is optional, found by `altocue doctor`, and has a fallback.
- **No media in git.** Sounds, music and footage are fetched by the user, with a checksum and a notice.
- **No hosted call without the user opting in.**
- **Nothing is claimed that was not measured.** A new engine is adopted on a comparison, with the numbers in the docs. A sweep cannot hear or watch, so anything judged by ear or eye is listed for a person.
- **No private information, no client names, no invented numbers.** Figures about platforms and audiences come from sources dated in the current year, with the link.
- **One heavy job at a time.** Targeted tests while working, and the full suite once in CI.

## The steps

### 1. Start clean

Start from the newest `main` on a branch named for the week. Read the backlog at the foot of this file and the last sweep's pull request.

### 2. See what moved

```bash
uv run python scripts/sweep.py
```

It reads `library/watch.toml` and reports each watched package's newest release and stated licence against the pin or against the release a person last read. It changes nothing. The Monday library job prints the same report in its run summary.

For every row that moved:

1. Read the release notes between the old version and the new one.
2. Read the licence file at the new version.
3. Decide: **adopt** (raise the pin, run that area's tests, note the change), **hold** (say why in the backlog) or **drop**.
4. Set `seen` to the version you read, whatever you decided.

A pin is raised only with its tests passing. The renderer and the speech engines are raised one at a time, each in its own commit, so one can be reverted alone.

### 3. Refresh the library

```bash
uv run python scripts/build_library.py
uv run python scripts/build_library.py --check-links
```

This rebuilds the catalog of motion blocks, transitions, looks, sound sources and stock sources from their upstream registries. Read the diff: new items arrive, dead addresses are reported, and nothing is bundled that was not cleared item by item.

### 4. Scout for what is new

Look for things the watch list does not know yet. For each area, check the places named and add at most a few candidates a week, each with its licence read.

| Area | Look for | Where |
|---|---|---|
| Speech and timing | Engines with word times, aligners, new languages | Model hubs' speech leaderboards and the release pages of the engines in the watch list |
| Voice clean-up | Noise, echo and click removal with open weights | The same hubs, and the speech enhancement challenge results of the year |
| Picture | Subject tracking, matting, stabilising, shot detection | Release pages of the tools in the watch list, new ffmpeg filters in its changelog |
| Motion graphics | New blocks, components and transitions | The renderer's registry, the shader collection, open motion libraries |
| Sound and music | CC0 and CC BY packs, open instruments, beat tools | The sources under `library/sources`, and new open packs with a manifest and checksums |
| Looks | Openly licensed film looks and colour tools | Colour science projects with permissive licences |
| Platforms | Safe zones, upload specs, loudness, length limits, labelling rules | Each platform's own help pages, dated |
| Agent hosts | Plugin, skill and MCP changes in the hosts Altocue installs into | Each host's own documentation |
| Other editors | What comparable tools shipped | Their changelogs. Take ideas, never code under an unfit licence |

A candidate enters `library/watch.toml` with `role = "candidate"`, its licence, the version read and one line on why. It becomes `used` only through a normal change with tests.

### 5. Refresh the craft

The judgement in `skills/altocue-craft` and the per-step skills is part of the product. Each week:

- Add what was learned from real edits since the last sweep: review notes that came up more than once become a rule, with its reason.
- Check that every platform fact in the skills is still true, and fix or remove what is not.
- Keep each line's label honest: `[evidence]`, `[source]` or `[thumb]`.

### 6. Check Altocue on itself

```bash
altocue demo
altocue watch -p <the demo project>
```

Run the passes in `skills/altocue-craft/references/passes.md` on the result. Anything that fails a pass is a finding. Then take the top item of the backlog that fits in a day, build it with tests, and stop there. A sweep that tries to do everything ships nothing.

### 7. Check and send

```bash
uv run ruff check src tests scripts
uv run pytest tests/test_<each area touched>.py
uv run python scripts/check_repo.py
```

Update `CHANGELOG.md` under Unreleased. Open the pull request with the report on top, let CI run the full suite, fix what it finds, and ask the maintainer for a yes to merge. Never ask for a release in the same breath.

## The report, in the pull request

```text
Moved: <packages that moved, each with adopt, hold or drop>
Added: <new candidates, sources, sounds, blocks, rules>
Built: <the backlog item done this week>
Held: <what was found and not done, and why>
For a person: <what must be heard or watched before merging>
```

## Backlog

Ordered by how much each would improve a finished film. A sweep takes from the top, and re-orders when something changes.

<!-- backlog:start -->
1. **Gates that read the new facts.** The cut, frame, caption and mix stages now write what they found, and no gate reads it yet: shots under half a second, bare jump cuts in `work/frame.json`, a source enlarged past 1.5 times, an ending that stops cold (`ends_cold_s`), a face left dark after the limit. Add them as gates with canaries, and a `music_and_mix` line in the reviewer's rubric.
2. **Captions with an active word.** A highlight that follows the spoken word, entrances that differ by caption style, and the hook on the first frame, using the motion numbers in the craft skill.
3. **Sound, the next layer.** House sounds on the frame a graphic settles rather than where it starts; at most two effects a second counted across different sounds; a typing sound on every format that types; loops joined on phrases; a mono fold-down check and a warning for a low hit with nothing above 300 Hz. Then check the de-esser's ceilings and the effect ducking by ear on real voices.
4. **Raise the renderer.** HyperFrames is pinned well behind its newest release, and the shader collection has a new major version. Each needs a proof render on a real machine, one at a time, each in its own commit.
5. **Log everywhere.** The follow crop, B-roll inserts and QA still read log clips as they are. Each needs `grade.to_sdr`. Then more curves: S-Gamut3, Apple Log 2 and D-Log M.
6. **Split edits.** Let the voice lead or trail the picture at a join between two different expressions, so the cut does not jar, as a field in `edl.json` so a word can finish under the next picture.
7. **Faster drafts.** The basic renderer for a draft that has only captions, hardware encoders where the machine has them, and plate pieces in parallel.
8. **A changed `[cut]` setting makes the cut stale.** Today it is ignored until the cut is run again.
9. **Exposure that knows the skin.** The face band is one range for every skin tone. Read the tone first, then place it.
10. **Speed ramps.** A shot that speeds up into a cut and settles after it, with sound that follows.
11. **Language packs.** Fillers, sentence ends and titles for languages beyond English.
12. **Fractional frame rates.** 23.976 and 29.97 as exact fractions end to end.
13. **A timeline to take elsewhere.** Export the cut as OpenTimelineIO.
14. **Hand-placed inserts take the look.** An insert written straight into `motion.json` bypasses the B-roll step and its look.
15. **3D inserts.** A skill that writes a scene for a 3D tool the user has installed, renders it, and looks at the result with `altocue watch`.
16. **From the studio's own Reels, not yet in Altocue.** A refine pass for a green screen matte (temporal smoothing, outline smoothing, a guided edge); a warning when a still or object was used in a recent film; delivery of a batch to one folder with numbered files; cover frames scored for a closed mouth and eyes on the lens as well as open eyes.

For a person, until someone has done it: the versions page in Safari and Firefox, a note round trip by a real reviewer, every new look on real faces from light to deep, each log curve on real camera files, and timings of the speed work on a real machine.
<!-- backlog:end -->
