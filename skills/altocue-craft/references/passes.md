# The passes: testing a cut before anyone else sees it

Nobody catches everything in one viewing, so editors review in passes, each looking for one kind of thing. You cannot play the film, so each pass below names the evidence to look at and the command that makes it. Do them in order: a later pass is wasted on a cut that fails an earlier one. This is your own check. It never replaces `altocue qa` or the Editor in Chief.

Lines here are [thumb] unless labelled.

## How to look

```bash
altocue watch --sources          # before the cut: every line of the recording as three frames, with its words
altocue render --draft
altocue watch                    # after a render: the opening, each cut, each graphic, each insert, the ending
altocue watch --at 12.4 31.0     # one moment at full size, when a sheet shows something to check
```

Each run writes sheets and an `index.md` under `work/watch/`. View the sheets with your own file-reading tool, never by launching an app.

- **Look at every sheet, not a sample.** The fault is always on the sheet you skipped.
- **Say what you see before you change anything:** the time, what is in the frame, then the fix.
- **Change one thing, then look at that thing again.** `--at` is for this.
- **Stills are not playback.** A frame cannot show a stutter, a click or lip sync. Say so in your report, every time.

## Pass 0: the selects, before any cut

Evidence: the `altocue watch --sources` sheets, `work/cut-review.md`.

- **Read each line and look at its three frames.** Eyes on the lens or off? Energy up or flat? Anything wrong in the frame: a hand over the face, a glance at notes, a slump?
- **Between two takes of one line,** pick by delivery, then confirm the first and last frame are clean.
- **Mark the opening line now.** The strongest self-contained line in the whole recording, wherever it sits.
- **Mark what needs covering:** a look away, a stumble that cannot be cut. That list is the insert and graphic plan.
- **`sources.json` ranks nothing.** Use its numbers to find lines worth a closer look: a face that is small, a level far under its neighbours, a long pause before a line.

## Pass 1: the words

Evidence: `script.md`, fresh from `altocue cut --write-script`.

- **Read the kept lines top to bottom as a stranger.** No footage in your head, only the words.
- **Does the first line make someone want the second?**
- **Does every "this", "that" and "as I said" point at something still in the film?**
- **Is anything said twice?** Keep the better telling.
- **Could it end one line earlier?**
- **Does it still mean what the speaker meant?** A trimmed hedge or a moved line can change a claim.

If this pass fails, go back to the cut. No picture fixes words.

## Pass 2: the stranger's first seconds

Evidence: the opening sheet.

- **Frame 0 shows the subject,** not black, not a logo, not an empty room.
- **Words arrive inside the first second,** heard or on screen.
- **By three seconds a stranger could say what this is and who it is for.**
- **The picture, the spoken line and the text agree.**

## Pass 3: the picture alone

Evidence: every sheet from `altocue watch`, read without the words.

- **Could someone follow the film with no sound?** Not every word, but what it is, how it develops and what to do next. This is a design check for clarity and for people who cannot hear it, not a claim about how people watch.
- **Does the picture change when the idea changes?** Find the longest stretch where nothing on screen changes and ask whether the content there earns the stillness. `altocue facts` reports `longest_static_s` with its start and end.
- **Does anything change with no idea behind it?** A zoom, a transition or a graphic with no reason is a twitch. Remove it.
- **Skin, exposure and framing hold from shot to shot.** A face that changes colour between two cuts of the same take is a grading fault.
- **Headroom and eye height hold across punch-ins.**

## Pass 4: the joins

Evidence: the cuts sheets, one pair of frames per cut. For the sound, the timeline sheets in `review/pack/` after `altocue review --reviewer pack`.

- **No first frame with closed eyes or a half-turned head.**
- **The eyes stay near the same place on screen across a punch-in.**
- **A real change at every cut:** size, subject or angle. Two nearly identical frames either side of a cut is a jump that needs a punch-in, an insert or a different cut point.
- **No stray frame** of another shot at an insert's edge. The `stray_frames` gate measures hard edges. Edges inside a transition are yours.
- **The sound at each join:** on the timeline sheet, a cut that lands on a tall part of the waveform has clipped a word. The `word_ends` gate measures the same thing. A gap in the waveform at a join is dead air.
- **The last word of the film finishes,** with a beat after it.

## Pass 5: the text

Evidence: the graphics sheets, the caption pages in `work/captions.json`.

- **Spelling, names and numbers,** each against the transcript. A number on screen matches the number said.
- **Read every graphic at phone size.** Scale the frame to about 360 pixels wide and read it without zooming.
- **Nothing covers the face, and nothing sits under the app's buttons.** The `face_clear`, `graphics_clear` and `safe_zone` gates measure this. Look anyway.
- **Each graphic holds long enough to read twice,** and leaves before the speaker has moved on.
- **One idea on screen at a time.**
- **Text says what the speaker says, when they say it.** Never ahead of them, never a different claim.

## Pass 6: the sound, by measurement

Evidence: `altocue qa`, the timeline sheets, `altocue facts`.

- **You cannot hear. Say so,** and tell the user what a person must listen for: tone, noise, the music, lip sync.
- **Loudness and true peak pass their gates.**
- **The voice sits level from line to line** on the loudness trace, with no line dropping away.
- **Every effect sits on something visible,** and none lands on a word.
- **Music is under the voice, and it ends on purpose** on the last frame, not cut off.
- **The opening is not quieter than the rest.** `altocue facts` reports `opening_minus_integrated`.

## Pass 7: the pace

Evidence: `altocue facts`, the cuts list in `work/watch/render/index.json`.

- **Is there variation?** List the shot lengths. A run of equal lengths is a metronome. Break it.
- **Where is the longest shot, and does it deserve to be?** It should be the moment that needs to land.
- **Is there a rest after the densest passage?**
- **Does it move fastest where the content is simplest and slow where it is hardest?** The reverse is the usual fault.
- **Never chase a number.** The measures describe a film. They do not say what it should be.

## Pass 8: the ending

Evidence: the ending sheet, the last lines of `script.md`.

- **The last useful line is the last line.**
- **One action, said once.**
- **The final frame is worth leaving on:** the speaker's settled face or the end card, never a frame mid-word or mid-gesture.
- **The end card is short.** It holds long enough to read and no longer.

## Pass 9: the truth

Evidence: the transcript of the render, every graphic, the brief.

- **Every claim is one the speaker made,** in a form they would sign.
- **Every number, name, logo, quote and review came from the user or the speech.**
- **A generated person or place is labelled as the rules require.** See `altocue-ugc`.
- **Every credit owed is in `out/credits.txt`.**

## Then hand it over

1. `altocue qa`. A failed gate is a fix, with no exceptions.
2. `altocue review`. The Editor in Chief sees the film without your reasons. Never brief it.
3. Report to the user: the path, the verdict, what you changed, and a plain list of what could not be checked from stills and measurements.

## When a pass fails

- **Fix the earliest failing pass first.** A structure problem dressed with graphics is still a structure problem.
- **One named change at a time.** The fix vocabulary is in `altocue-review`.
- **Keep the last good version** before a polish pass.
- **Two rounds, then ask.** If the same note survives two fixes, the footage may not hold the answer. Say what is missing and offer a pickup line to film.
