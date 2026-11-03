# Security

## Reporting

Please report security issues privately through GitHub's "Report a vulnerability" on this repo. Do not open a public issue.

## What Altocue does with your footage

- **Altocue runs locally.** Its own stages send nothing anywhere unless you run a command that names a service below.
- **Your coding agent is a hosted provider.** It reads transcripts and frames to do the edit, so those reach whichever vendor runs it. If footage must stay private, use a local model or run the commands yourself.
- **Hosted steps name their destination first.** Transcription, generation and review providers are opt-in.
- **Keys stay in your environment.** Altocue reads them from environment variables and never writes them to project files.
- **Telemetry:** Altocue has none, and switches off HyperFrames telemetry when it runs it.

## Every service that can receive data

Each one is reached only by the command named, and only when you run it.

**Your files, your words or your prompts, with your own key or account:**

- **fal.ai:** prompts, and the project files a shot names, for `altocue generate` with fal.
- **Higgsfield, through its CLI:** prompts, and the project files a shot names, for `altocue generate` with Higgsfield. Project files wait for `altocue generate --allow-upload higgsfield`.
- **Google Gemini:** the film, picture and sound, for `altocue review` with Gemini and `--upload`.
- **OpenAI, through the Codex CLI:** frames from the review pack, the transcript and the measurements, for `altocue review --reviewer codex` with `--upload`. The video itself stays here.
- **ElevenLabs:** a clip's sound, for hosted transcription, which you choose by name.
- **1of10, Spyglass and the YouTube Data API:** search words, tracker, account and channel ids, for `altocue benchmark`.

**Search words only:**

- **Stock video and photos, no key:** NASA, Wikimedia Commons, The Met, the Cleveland Museum of Art, and Wikidata, which finds Cleveland's works by up to three words of the search. The Internet Archive only when you opt in.
- **Stock with your own key:** Pexels and Pixabay.
- **Music and sound:** ccMixter and Incompetech with no key, and Freesound with your own key.
- Search words come from your transcript, so they can carry what was said. Footage never goes to a stock or music source.

**Downloads only, which see your address and what was asked for:**

- **stratosagency.ai:** the demo take, for `altocue demo`.
- **GitHub (raw files and releases):** the library catalog for `altocue library update`, registry items for `altocue library add`, the noise remover and the speaker models for `altocue setup`.
- **jsDelivr and cdnjs:** shaders, and the libraries a fetched item loads, for `altocue library add`.
- **Google Fonts:** faces a fetched library item names, for `altocue library add`, and at render time for a face it could not embed.
- **The npm registry:** the renderer for `altocue setup`.
- **Google's Chrome for Testing storage:** headless Chrome, for `altocue setup`, or the first render if setup missed it.
- **Hugging Face:** the speech model, on the first transcription.
- **The stock and music sources above:** the file you picked.
