---
description: Install Altocue on this machine, check what it can run, and prove it on the demo take. Asks before anything installs.
---

Set up Altocue for the user. Ask before any step that installs software, and name what each step fetches and from where.

1. **Check.** Call the `altocue_doctor` tool if you have it. Otherwise run `altocue doctor --json`.
2. **Install Altocue if it is missing.** Ask: "Altocue installs from PyPI with uv, as a command line tool on this machine. Shall I install it?" On a yes, call `altocue_install` with `confirm` true, or run `uv tool install altocue`.
   - If uv is missing too, say it comes from Astral's official installer and ask again. On a yes, add `install_uv` true, or run `curl -LsSf https://astral.sh/uv/install.sh | sh`.
   - If doctor reports an older Altocue, offer the update: `altocue_install` with `upgrade` true, or `uv tool upgrade altocue`.
3. **Close the gaps doctor names.** Show each `fix` line and ask before running it. ffmpeg comes from Homebrew on a Mac or apt on Ubuntu. On a Mac without the Xcode command line tools, the user runs `xcode-select --install` themselves, because it opens Apple's installer.
4. **Offer the extras.** `altocue setup --yes` adds the motion renderer, which needs Node 22 or later, and a better noise remover where the Mac has no voice isolation. Run it only on a yes, with `altocue_run` and `["setup", "--yes"]` or in the shell.
5. **Offer the demo.** `altocue demo` downloads our small demo take, a generated presenter, and edits it through every free stage on this machine, with no keys and no spending. It takes a few minutes, longer on the first run while the speech model downloads.
6. **Ask how they work, once.** Call `altocue_profile`, or run `altocue start --show --json`. If nobody has answered, follow the `altocue-setup` skill: ask the eight questions in two batches of four and save the answers. If nobody replies, take the defaults with `altocue start --yes` and say so.
7. **Finish** with what is ready, what is not and its fix, and how to start: "Ask me to edit a video, for example: edit take.mov into a 45 second reel. Or: make a launch video for my product."
