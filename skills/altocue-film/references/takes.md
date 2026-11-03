# Real takes and music in a film

What a film from a storyboard can do with real takes and a chosen track.

## Real takes

- **Part of a clip's sound.** `"voice_from"` and `"voice_to"` on a scene play that stretch of its `"voice"`, in the file's own seconds, faded at the edges.
- **A clean picture with its sound elsewhere.** `"captions": {"file": "generated/take-sound.mp4"}` reads the words from the clip the scene plays, when the picture has no sound of its own.
- **The mess, as said.** `"captions": {"verbatim": true, "tags": [...]}` keeps every um and spoken cue, and each word inside a removal from `edl.json` shows its kind, popping as it is said.
- **Bigger captions.** `"scale": 1.5` in `"captions"` sets the one track across the frame larger. Set it per shape under `"aspects"`.
- **A frame of a clip as a picture.** In any graphic, `{"image": "assets/take.mp4", "frame": 5.0}` shows the frame at 5 s. The key is `frame`, because a row's `at` is a time in the film.
- **One picture through several shapes.** `"shapes": ["16:9", "9:16", "1:1"]` on footage holds each aspect in turn inside its zone, named beneath it. `"focus": [0.5, 0.3]` keeps a face in frame.
- **A generated person is held to the disclosure gates.** Footage in a film counts as played, so import a generated take with `altocue generate --import` and label it on screen.

## Music on the beat

A chosen track from `altocue music get` plays backtimed to the film's end by default. To cut to it, start it on a downbeat with `"music": {"from": 100.69}` and set `"snap": true`. The beat grid moves with it, so every cut lands on a beat that is heard, and the track fades out at the end. Make each scene a whole number of beats. Render writes `out/credits.txt` for a track that owes a credit.
