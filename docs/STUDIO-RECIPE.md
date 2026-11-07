# The Studio recipe

This is how Stratos House films and edits its own Reels with Altocue. It is written so you or your agent can run the same process on your own footage, with your own sounds and your own machines. Nothing here needs a paid service. For the scenes themselves, the [Directed Studio workflow](DIRECTED-STUDIO.md) has the commands and JSON.

## Film

- **One complete thought per take.** Put the script by the lens. Pause between paragraphs. If you glance at the script, pause, look back into the lens and say the whole sentence again. Altocue picks the clean one.
- **Leave room at both ends.** Two seconds of stillness before you speak and after you stop. Do not rush the first consonant or swallow the last word.
- **Capture the real proof separately.** When the video explains a product, record the actual screen doing the actual thing. A diagram never stands in for evidence.

## Cut for meaning first

- **Keep whole words.** Every cut keeps the complete first and last word of a thought, and its subject and verb. `altocue cut` works from word timings, so check the joins at normal speed.
- **Remove whole thoughts.** Cut redundant sentences and true false starts. Do not trim inside a sentence to save half a second.
- **Hold the frame steady.** Pick one deliberate crop. Change it only when the change supports what is being said, never as a tic.
- **Make a pickup list.** A glance or stumble that cannot be cut cleanly is a note for the next shoot, not something to hide.

## Motion explains, it does not decorate

- **Proof first, then one idea, then the speaker.** Show the real thing, add a graphic for one complete idea, and come back to the face.
- **Land changes on the words.** A row, label or highlight arrives on the clause that names it. Copy a prompt or native JSON from `altocue library search` and keep to its slot limits and reading time.
- **Prefer movement to lists.** A before and after, a selection or a linked move reads faster than a long list on screen.
- **No B-roll for its own sake.** Generic stock and generated filler weaken a Reel. Use footage only when it shows the point.

## Sound follows an event

- **Every effect marks something the viewer sees.** An arrival, a contact, a file moving, a selection, a key pressed. If nothing happened on screen, there is no sound.
- **Sit under the voice.** Effects are levelled against the voice, 8 to 12 dB under it, and never cover a word.
- **Type one key at a time.** When text types on screen, use the `typing` scene from the [Directed Studio workflow](DIRECTED-STUDIO.md). It gives each character its own key tap, so the sound stops when the typing stops.
- **Use your own pack if you have one.** `altocue sfx pack` registers a folder of sounds you hold a licence for. `altocue sfx get` copies one into the project's private folder, which git ignores. Altocue never uploads or bundles a pack. Whether a pack's sounds may appear in a published video depends on its licence, so read it.
- **Silence is a choice.** Many Reels need no music at all. Voice plus a few precise effects is often the cleanest mix.

## Heavy work goes where there is room

- **Transcription, renders, QA and test suites are heavy.** On a laptop, run one at a time. Altocue's own job lock already waits for room.
- **A second machine helps.** On a desktop with more memory, set `ALTOCUE_HEAVY_JOBS` to run more jobs at once and `ALTOCUE_RENDER_WORKERS` for more render workers. Edit and review on the laptop, render on the desktop.
- **A desktop with an NVIDIA GPU encodes there.** Set `ALTOCUE_ENCODER=auto` (or `nvenc` to insist). Encodes run about three times faster than x264 at the same quality level. Transcription already uses the GPU when one is there.

## Check the file you will actually ship

- **Run the gates on the final file.** `altocue qa` checks loudness, frozen frames, captions, safe zones, credits and more. A failure is a FIX, with no exceptions.
- **Watch it at normal speed, to the end.** Sampled frames are not playback.
- **Holds are for stills.** A planned reading hold belongs on a graphic or a still scene. It never excuses frozen video or missing speech.

## Keep the review honest

- **The maker never grades its own work.** `altocue review` sends the film to a separate reviewer, Gemini or Codex when you turn one on, that never sees why each cut was made. Without one, the report says the review was not independent.
- **Two rounds of fixes, then a person.** After two scored rounds the open notes go to a human. Do not reset the count to get a better score.
- **Say what was not checked.** A reviewer that saw stills did not hear the sound. Automated checks are not taste. A person listens before anything goes public.

## Publish only with a yes

- **Drafts stay private.** Share a private link for review. Posting, emailing, keyword flows and spending each need their owner's explicit yes, every time.
