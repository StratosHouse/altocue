# Transcription

Altocue cuts on word times, so it needs a transcript that keeps fillers and places words near the real speech. This page records how the default was chosen.

## Result

- **Apple Silicon default: `parakeet`.** It heard about six times as many spliced fillers as the best Whisper setup, invented none, and ran 1.7 times faster.
- **Fallback on Apple Silicon: `mlx-whisper`.** A fifth of the download. Use it when you need a custom spelling, because Parakeet takes no prompt.
- **Everywhere else: `faster-whisper`.** Whisper family, so expect the same weakness on fillers.
- **`elevenlabs` is opt-in only.** It runs when named with `--engine` or in `altocue.toml`, prints where the audio is going and what it costs, and is never auto-selected.

## Test set

- **Speech.** 16 phone clips, 301 s in total. Two main speakers plus off-camera talk, in rooms and outdoors, scripted and unscripted, English. The footage is private, so it is not in this repo and nothing from it is quoted here.
- **Machine.** Apple M2 Pro, 16 GB, 2026-10-30. Other work was running, so speed is the best of three rounds per clip.
- **Engines.** All at `temperature=0`, word timestamps on, `condition_on_previous_text=False`.

| Engine | Package | Model | Prompt |
|---|---|---|---|
| Parakeet | `parakeet-mlx` 0.5.3 | `mlx-community/parakeet-tdt-0.6b-v3` | Not supported |
| Whisper, prompted | `mlx-whisper` 0.4.3 | `mlx-community/whisper-small.en-mlx` | Filler list |
| Whisper, plain | `mlx-whisper` 0.4.3 | `mlx-community/whisper-small.en-mlx` | None |

## Numbers

| Measure | Parakeet | Whisper, prompted | Whisper, plain |
|---|---:|---:|---:|
| Model download | 2.5 GB | 460 MB | 460 MB |
| Speed, times real time | 41.7x | 25.1x | 23.7x |
| Natural fillers found, of 5 places any engine heard one | 3 | 1 | 1 |
| Spliced fillers heard, of 43 | 30 | 5 | 1 |
| Filler tokens with no spliced filler nearby | 0 | 0 | 0 |
| Words | 536 | 554 | 521 |
| Words dropped by the guards | 0 | 2 | 1 |
| Words the other model family did not hear | 7.8% | 13.5% | 9.6% |
| Words over 1 s before the guards | 8 | 9 | 11 |
| Word start error, median | 140 ms | 140 ms | 60 ms |
| Word start error, 90th percentile | 242 ms | 250 ms | 232 ms |
| Word start bias | -78 ms | -48 ms | +13 ms |
| Word end error, median | 120 ms | 100 ms | 100 ms |
| Word end error, 90th percentile | 240 ms | 240 ms | 208 ms |
| Word end bias | -17 ms | -65 ms | -64 ms |
| Phrase starts matched to an RMS onset | 60 | 36 | 35 |

## How each number was measured

- **Natural fillers.** Non-lexical tokens such as "um" and "uh", clustered by time across engines. The set is small and has no human labels: every place had speech energy, nothing more.
- **Spliced fillers.** Three real fillers were lifted from the clips and spliced into five other clips at 43 known word boundaries. A hit is a filler token within 0.5 s. Splices are artificial, but the audio is real human speech and the positions are known.
- **Word edges.** Each word next to a pause of 150 ms or more, against the nearest RMS onset or offset in the 20 ms envelope, within 300 ms. Negative bias means the word time is earlier than the energy.
- **Other family.** A word with no matching word within 0.6 s in the other family's transcript. This shows disagreement, not which side is wrong.

## What the numbers say

- **Fillers decide it.** A prompt helps Whisper, from 1 to 5 of 43, but Parakeet still heard six times as many.
- **No engine's edges are good enough on their own.** Errors of 100 to 250 ms are why every cut edge snaps to the RMS envelope, whichever engine ran. Parakeet's times also step in 80 ms units.
- **Whisper keeps its prompt.** With it, Whisper heard five spliced fillers instead of one, at the cost of a little more text only it heard.

## Engine faults found by this test

- **Late punctuation.** Parakeet emitted a full stop up to 15 s after its word. Merging that piece stretched one word across a long pause. Punctuation now adds to the text only.
- **Stretched words.** All three runs had words that swallowed the pause after them. A word over 1 s now ends at the first 200 ms of quiet.
- **Empty pieces.** Parakeet can emit a bare space. It now just starts the next word.

## What this does not prove

- **Five natural fillers is a small sample.** The spliced test exists because of that, and it reused three filler samples.
- **Whisper was tested at `small.en` only.** `large-v3-turbo` was not run.
- **`faster-whisper` had one smoke test** on generated speech, not this bake-off.
- **Scribe was not measured.** This footage cannot be uploaded.
- **No Linux Parakeet.** `onnx-asr` serves the same weights there and is not built yet.
- **RMS onsets are a yardstick, not truth.** Off-camera talk creates onsets no transcript should match.

## Settings

```toml
[transcribe]
engine = "auto"          # auto, parakeet, mlx-whisper, faster-whisper or elevenlabs
language = "en"          # Whisper and ElevenLabs. Parakeet takes no language setting
words = ["Altocue"]      # spellings for Whisper's prompt
```

Install an engine with `pip install "altocue[parakeet]"`, `"altocue[mlx-whisper]"` or `"altocue[faster-whisper]"`.
