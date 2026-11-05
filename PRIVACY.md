# Privacy

Altocue is open source software that runs on your own machine. Stratos House runs no Altocue service, and Altocue collects nothing about you.

- **No telemetry.** Altocue sends no usage data, and it switches off HyperFrames telemetry when it runs HyperFrames.
- **Your footage stays on your machine** unless you run a command that names a hosted service and agree to it. Each one is opt-in, and [SECURITY.md](SECURITY.md) lists every service the code can reach and what each one receives.
- **Keys stay in your environment.** Altocue reads them from environment variables and never writes them to project files.
- **Your coding agent is a separate service.** It reads transcripts and frames to do the edit, so those reach whichever company runs the agent, under that company's terms.
- **The plugin and its MCP server** talk only to the agent app that started them, on this machine. The server runs the `altocue` command here. Its install tool downloads uv from Astral and Altocue from PyPI, and only after you agree.
- **Downloads** such as the demo take, the speech model and the motion renderer see your address and what was asked for, like any download.

Questions go to [GitHub issues](https://github.com/StratosHouse/altocue/issues). Security reports go through GitHub's private "Report a vulnerability" on this repo.
