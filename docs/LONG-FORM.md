# Long recordings

Measured on November 8, 2026, on one real recording: a 27 minute video call between two people, screen shared most of the time, with a product video played partway through. The recording stays private and is not in the repo. This page records what ran, what it cost and what broke.

## The run

The machine was the studio render PC: Ubuntu 24.04 under WSL2, a 24-thread CPU, 23 GB of memory for Ubuntu and an RTX 3090 Ti. The transcriber was faster-whisper on the GPU, with the `speakers` extra. The source was 2560x1440 at a constant 25 fps, 1615.4 seconds, AAC audio.

| Stage | Wall time | Peak memory | Peak GPU memory | Peak GPU temperature |
|---|---:|---:|---:|---:|
| `new` and `ingest` | under 1 s | 0.05 GB | | |
| `transcribe --speakers 2`, first run, model fetched | 320 s | 2.34 GB | 3.7 GB | 61 C |
| `transcribe --force --speakers 2` | 269 s | 2.34 GB | 3.7 GB | 62 C |
| `clean` | 41 s | 2.51 GB | | |
| `clips find --count 8 --length 30-60` | under 1 s | 0.04 GB | | |
| `clips cut --pick 1,4,7 --aspect 9:16` | 1.2 s | 0.06 GB | | |
| `run --to qa`, one short of 50 to 57 s | 60 to 64 s | 2.51 GB | 3.6 GB | 54 C |

Speaker turns took about 215 s of the transcribe time. Memory did not grow with the length of the recording: the transcriber reads a minute at a time, and the speaker step holds the 16 kHz sound, about 100 MB here.

`transcribe` found 4685 words and 68 fillers. `clips find` listed 8 candidates, all passing its five checks.

## What broke, and the fixes

**Speakers were not told apart.** With the count given as 2, one clustering pass over the whole call gave 1419 s to one voice and 31 s to another, so every word went to one speaker. With no count it found about 30 voices. A 5 minute slice of the same call split well. Speaker turns now come from 5 minute windows, and each window's voices are matched across the recording by likeness. The same call now splits 1028 s and 416 s. Two 5 minute stretches that cross a window edge, diarised on their own, agree with the linked labels on 95% and 97% of their speech. A third, around the played product video, agrees on 67%: a third voice is in it and the count says two.

With no count, window voices this alike or closer are one speaker: a cosine distance of 0.5. On this call the same voice measured a median of 0.14 between windows and different voices 0.59, and 0.5 found the two people. That is one recording. Give the count when you know it.

**Every join opened a frame of gap.** Zoom writes a chapter track that starts at 0, while each encoded part's picture starts a frame in. The join read each part as starting a frame early, so a 51.56 s short came out 52.16 s with 15 gaps of 80 ms, 0.6 s behind its sound by the end, at 24.7 fps. Each part now holds picture only. The same short is 1289 frames, 51.56 s, a steady 25 fps.

## What still fails, and why

- **Frozen frames.** A screen share sits still for seconds at a time. In the first short's 70 s of source, ffmpeg's freeze detector finds 37 stills before Altocue touches it. The gate is right that the picture does not move. It was written for camera footage.
- **Caption speed.** Two of the three shorts peak at 26.7 and 26.8 characters a second, over the limit of 24. The speaker is fast. Trim words, or allow it for this film.
- **The vertical crop of a screen share.** The crop follows the presenter's camera tile, which Zoom puts at the top edge, so the face sits half out of frame in a 9:16 short. A screen share needs a layout of its own, such as the face tile above a crop of the screen.

## Not covered

- Nothing longer than 27 minutes.
- This Mac's transcribers (Parakeet and MLX Whisper) on a long file. The memory figures above are for faster-whisper on Linux.
- Speaker labels were checked for agreement, not by ear.
