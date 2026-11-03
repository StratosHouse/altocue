# Installing Altocue

These steps work for a person or for a coding agent. Ask before running anything that installs software.

1. **Check for uv.** If `uv --version` fails, install it: `curl -LsSf https://astral.sh/uv/install.sh | sh`
2. **Check for ffmpeg.** If `ffmpeg -version` fails, install it: `brew install ffmpeg` on macOS, `sudo apt install ffmpeg` on Ubuntu or Debian.
3. **Install Altocue.** `uv tool install git+https://github.com/StratosHouse/altocue`
4. **Add the optional extras.** `altocue setup` asks before installing the motion renderer (needs Node 22 or later) and the noise remover.
5. **Confirm.** `altocue doctor` lists what is ready and how to fix what is not.
6. **Prove it.** `altocue demo` downloads our 730 KB demo take, which shows a generated presenter, and edits it through every free stage on this machine, with no keys and no spending. It takes about 3 minutes once the speech model is on disk, and removes its files. `--keep` keeps them.
7. **Get the skills.** The Claude Code plugin brings them. Anywhere else, clone the repo: `skills/altocue/SKILL.md` explains the workflow, and the tools have their own skills beside it.

**Making a launch video?** There is a dedicated feature for it, and it needs no footage: `altocue launch my-launch --product "Name"`, guided by the `altocue-launch` skill. It takes a brief through to a finished film with its 9:16 and 1:1 cutdowns.

The first transcription downloads a speech model, about 2.5 GB on Apple Silicon.
