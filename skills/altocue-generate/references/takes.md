# Takes and their records

What to do when a take fails, what Altocue records about every take, and which routes take a motion video or a music track.

## When a take fails

- **Say what you saw first.** Name the frame and the fault before changing a prompt. Sampled frames mislead. [source]
- **Read the failure.** A problem that changes every run is the prompt. One that is wrong the same way each time is a setting. [source]
- **Change one thing per pass,** and fix one visible problem at a time. More adjectives rarely help. [source]
- **Patch or regenerate.** [source] Regenerate when the performance, length, shot structure, object scale or contact changes. Otherwise edit. One failed check is a patch, and two or more is a new take.
- **Retry from the original clip,** never from the damaged one. [source]
- **Keep it ordinary if it sits beside real footage.** Over-polished generated scenes stand out, and slightly ordinary references match better. [source]

`altocue-direct` has the named fixes.

## No blind retries

- **A failure label is not evidence.** [source] Before another take, compare what was planned with what arrived, and read why the take failed.
- **Three failed takes of one prompt stop the next.** A take that failed, was cancelled, was moderated or failed its check counts. `altocue generate` refuses another take of the same words before any spend. Change the one thing at fault, the prompt or a reference, never both [source].

## What a take's record holds

- **Four records stay apart.** [source] The brief, the prompt sent, the settings the service used, and the review. A clear prompt can still miss, and a happy accident does not prove the prompt worked.
- **The prompt the service ran.** When a job record carries the prompt the service actually ran, provenance keeps it as `prompt_ran` beside the prompt that was sent, and the run says so when it was rewritten.
- **The record against the request.** A job record can carry defaults nobody asked for, such as 16:9 or sound on [source]. Each setting the record states is held against what was sent: model, duration, aspect, seed when one was sent, resolution, mode and sound. A take that differs is paid for and kept, and never placed. `altocue generate --import --record` does the same for a job made elsewhere.
- **Why it was kept.** `altocue generate --keep <shot> --because "..."` records `kept_because` in the take's provenance and the manifest. Name what can be checked in the picture: the eyeline lands on the cup, the room matches the wide, hands clear of the label. "Looks cinematic" explains nothing, and it is refused.

## Motion videos and music tracks

- **Wired only where the provider's own docs list them.** fal's Seedance 2.0 and 2.0 Mini reference routes take a motion video and a track, and the Higgsfield CLI's `seedance_2_0` and its Mini take both. The Higgsfield MCP routes are not wired, because their listing was not checked. Any other route refuses the shot with a fix.
- **What fal accepts.** A motion video of 2 to 15 seconds, MP4 or MOV, between about 480p and 720p. A track of 15 seconds at most, MP3 or WAV, beside at least one image or video reference.
- **What it costs on fal.** With a motion video, fal bills a lower token rate on the motion video's seconds and the output's together, so `altocue direct` prices both. A motion video it cannot read is priced at the longest a route takes.
- **Both are project files.** They are named before a run and need the upload yes, like any still.
