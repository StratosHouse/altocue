# Source-clock audio conformance

Some camera files have AAC packet times shorter than the samples returned by the
decoder. The file reports the expected duration, but a plain PCM decode drifts
away from the picture during the take. Trimming the decoded tail to the reported
duration does not fix words in the middle.

Altocue's shared `source_clock_filter` follows incoming timestamps with
`aresample=<rate>:async=1:min_hard_comp=0.001:first_pts=0`. It fills gaps and trims
overlap samples; it does not change the playback speed of the whole recording.
Transcription and cleaning use the same policy. Cleaning then pads/trims to the
exact source-length sample count. Original media and video timing are unchanged.

## Existing projects

New transcriptions and cleaning reports record `source_clock: source-pts-v1`.
Unverified cached word times are refused when transcribing or when cleaning
would use them. They are not silently overwritten. A changed cleaning policy
invalidates the cleaned-audio cache. Existing verified word metadata survives
text or speaker-label changes; manually imported words are not stamped as verified.

Before migrating a project:

1. Back up its words, cleaned audio and approved EDL.
2. Run `altocue transcribe --force` using the intended engine.
3. Run `altocue clean --force`.
4. Review existing cut boundaries against the original source, then rebuild
   captions, the plate and the final render. Re-transcribing does not rewrite the
   EDL or certify that its old boundaries are still right.

These checks are at the transcription and cleaning entry points. This change
does not retrofit a project-wide migration gate into every downstream command.
Do not skip migration by rendering an old cache directly.

## Regression coverage

`tests/test_source_clock.py` generates all media during the test. It encodes
separated pulses to AAC, remuxes with shortened packet timestamps while keeping
the compressed payload, and first proves that plain decoding drifts. Acceptance
then checks pulse positions at the beginning, middle and end, at 16 and 48 kHz,
plus cleaned sample count, source range extraction, normal PCM and a delayed
audio track. Cache tests verify refusal without overwriting, metadata retention,
and cleaned-audio rebuilds. No private media or model download is required.

Implementation reference: [FFmpeg resampler documentation](https://ffmpeg.org/ffmpeg-resampler.html).

## Release

Shipped in 0.4.0. The full suite runs on Linux and macOS in CI. The real-model
transcription tests still need a machine with the speech models installed.
