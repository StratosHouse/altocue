# Installing Altocue

These steps work for a person or for a coding agent. Ask before running anything that installs software.

1. **Check for uv.** If `uv --version` fails, install it: `curl -LsSf https://astral.sh/uv/install.sh | sh`
2. **Check for ffmpeg.** If `ffmpeg -version` fails, install it: `brew install ffmpeg` on macOS, `sudo apt install ffmpeg` on Ubuntu or Debian. On a Mac, QA reads text in the picture through the Xcode command line tools, and Apple voice isolation needs them. If `xcode-select -p` fails, install them:
   ```bash
   xcode-select --install
   ```
   On Linux, or on a Mac without them, add `tesseract-ocr` so QA can read text in the picture.
3. **Install Altocue from PyPI.** `uv tool install altocue`
4. **Add the optional extras.** `altocue setup` asks before installing the motion renderer, which needs Node 22 or later, and a better noise remover where the Mac has no voice isolation built in.
5. **Confirm.** `altocue doctor` lists what is ready and how to fix what is not.
6. **Prove it.** `altocue demo` downloads our 730 KB demo take, which shows a generated presenter, and edits it through every free stage on this machine, with no keys and no spending. It takes about 3 minutes once the speech model is on disk, and removes its files. `--keep` keeps them.
7. **Get the skills.** The Claude Code plugin brings them. Anywhere else, clone the repo: `skills/altocue/SKILL.md` explains the workflow, and the tools have their own skills beside it.

**Making a launch video?** There is a dedicated feature for it, and it needs no footage: `altocue launch my-launch --product "Name"`, guided by the `altocue-launch` skill. It takes a brief through to a finished 16:9 film with its 9:16 and 1:1 cutdowns. Other films with no footage, such as explainers, demos and ad cards, start with `altocue film types` and the `altocue-film` skill.

**Telling speakers apart** is optional. Run `uv tool install --force "altocue[speakers]"`, then `altocue setup --speakers`, which fetches about 35 MB of models once.

The first transcription downloads a speech model, about 2.5 GB on Apple Silicon. Keep 5 GB of disk free: `altocue doctor` warns under that, and transcription will not start.

**Network.** Renders work offline, with two exceptions. A font a fetched library item names and `library add` could not embed is fetched at render time when the cache does not have it yet. If `setup` could not fetch headless Chrome, the first render fetches it.
