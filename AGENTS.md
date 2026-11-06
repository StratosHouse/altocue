# Working on Altocue

Altocue is a command line tool plus agent skills that edit raw footage into finished video. Read `docs/DESIGN.md` first.

**Installing or using Altocue, not changing it?** Follow `INSTALL.md`, then `skills/altocue/SKILL.md`.

## Layout

- `src/altocue/`: the CLI. One module per stage.
- `skills/`: portable agent skills. `.agents/skills` links here.
- `agents/`: Claude Code subagents. Skills must still work without them.
- `tests/`: `uv run pytest`. Fixtures are generated at test time.
- `scripts/`: repo tooling.

## Rules

- **ffmpeg must stay enough.** Never require `drawtext`, `subtitles`, `libass` or `zscale`.
- **Every required dependency stays under an open licence.** No paid fonts, sounds or LUTs. No AGPL or GPL code linked into the package. Check `LICENSES.md` before adding anything.
- **No media in git.** Tests generate what they need.
- **No generation or review model ids in code.** They live in `src/altocue/generate/models.toml` and `src/altocue/review/models.toml`. The one exception is `transcribe.py`, which names each speech engine's default model beside the code that loads it.
- **No hosted call without explicit opt-in.** Name the destination before sending anything.
- **A skipped check is never a pass.**
- **Never open an application window, editor or browser.** Altocue writes files and prints paths. The one exception is the HyperFrames studio that `altocue timeline` starts. Background command-line tools with no window, such as ffmpeg, headless Chrome in a render, npm in setup and the codex and higgsfield CLIs, are fine.
- **Numbers in the README come from the code.** `tests/test_readme.py` fails when a count moves. Change the README, never the count in the test.
- **Errors tell the user what to do.** Raise `AltocueError` with `fix`.

## Go easy on the machine

- **Never run heavy work in parallel.** Transcribing takes about 4 GB. Renders and the full test suite use every core. Parallel runs have crashed a 16 GB Mac.
- **Let CI run the full suite.** Locally, run only the tests for what you changed.

## Before a commit

```bash
uv run ruff check src tests scripts
uv run pytest tests/test_<what you changed>.py
uv run python scripts/check_repo.py
```

## Directed presenter parity

For a presenter-led software or workflow reel, use the [directed Studio workflow](docs/DIRECTED-STUDIO.md). The standard render reads studio.json. Every reusable Studio improvement needs a portable implementation, example, render-PC proof and a versioned handoff. Keep private footage, brand marks and licensed sounds outside the product repo.
