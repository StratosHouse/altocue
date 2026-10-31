# Security

## Reporting

Please report security issues privately through GitHub's "Report a vulnerability" on this repo. Do not open a public issue.

## What Altocue does with your footage

- **Altocue runs locally.** Its own stages send nothing anywhere unless you turn on a hosted provider.
- **Your coding agent is a hosted provider.** It reads transcripts and frames to do the edit, so those reach whichever vendor runs it. If footage must stay private, use a local model or run the commands yourself.
- **Setup downloads with your consent.** npm packages, headless Chrome for the renderer and the noise remover from GitHub. The first transcription downloads its model from Hugging Face.
- **Hosted steps name their destination first.** Transcription, generation and review providers are opt-in.
- **Stock search sends words, never footage.** Search words go to Pexels or Pixabay only when you ask, and they come from your transcript.
- **Keys stay in your environment.** Altocue reads them from environment variables and never writes them to project files.
- **Telemetry:** Altocue has none, and switches off HyperFrames telemetry when it runs it.
