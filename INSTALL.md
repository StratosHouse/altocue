# Installing Altocue

These steps work for a person or for a coding agent. Ask before running anything that installs software.

1. **Check for uv.** If `uv --version` fails, install it: `curl -LsSf https://astral.sh/uv/install.sh | sh`
2. **Check for ffmpeg.** If `ffmpeg -version` fails, install it: `brew install ffmpeg` on macOS, `sudo apt install ffmpeg` on Ubuntu or Debian.
3. **Install Altocue.** `uv tool install git+https://github.com/StratosHouse/altocue`
4. **Add the optional extras.** `altocue setup` asks before installing the motion renderer (needs Node 22 or later) and the noise remover.
5. **Confirm.** `altocue doctor` lists what is ready and how to fix what is not.
6. **Get the skills.** The Claude Code plugin brings them. Anywhere else, clone the repo: `skills/altocue/SKILL.md` explains the workflow, and the tools have their own skills beside it.

The first transcription downloads a speech model, about 2.5 GB on Apple Silicon.
