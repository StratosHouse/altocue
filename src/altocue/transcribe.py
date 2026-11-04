"""Word-timed transcripts with fillers kept. Engines sit behind one small interface."""

from __future__ import annotations

import argparse
import contextvars
import dataclasses
import functools
import gc
import importlib.util
import json
import math
import os
import platform
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
import wave
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from . import ffmpeg
from .errors import AltocueError
from .locks import free_memory_gb, heavy
from .models import Clip, Word
from .project import Project

RATE = 16000
HOP = 0.02  # envelope frame, seconds
MIN_CONTRAST = 10.0  # dB between floor and loud level before the envelope can be trusted
MIN_WORD = 0.04
LONG_WORD = 1.0  # words past this usually swallowed the pause after them
COLLAPSED = 0.02  # a run of words this short has lost its times
COLLAPSED_RUN = 3
COLLAPSED_LIMIT = 0.25  # past this share of a clip, the engine's times cannot be used
SPREAD_MAX = 0.4
GHOST_MARGIN = 0.3  # a word with no speech this close to it was never said
LOOP_FILLER, LOOP_WORD, LOOP_PHRASE = 3, 6, 4
STOCK_CONFIDENCE = 0.5
PIECE = 60.0  # speech goes to a local engine in pieces this long, so its memory follows the piece and not the file
REACH = 10.0  # how far either side of a mark to look for the quietest moment
READING_BACK = contextvars.ContextVar("reading_back", default=False)  # a finished render, read back for its checks
READBACK_PIECE = 30.0  # a finished render has music under the speech, and parakeet drops whole lines from a long stretch of it
MLX_CACHE_BYTES = 256 * 2**20  # MLX keeps freed buffers, and by default that cache may grow past the machine's memory
MLX_LIMIT_BYTES = 5 * 2**30  # a 60 second piece peaks near 3 GB
MIN_FREE_DISK_GB = 5.0  # swap lives on this disk too, and a full disk with no swap took a 16 GB Mac down
WAV_BYTES_PER_SECOND = 2 * RATE
QUIET_WINDOW = 25  # envelope frames that must be quiet together, so a split lands in a pause and not between syllables
ENVELOPE_BLOCK = 5000  # envelope frames read from disk at once

FILLERS = frozenset("um umm uh uhh uhm er erm hm hmm mm mmm mhm ah ahh eh".split())
FILLER_PROMPT = "Um, uh, so, like, you know, okay, hmm, I mean, right."

NUMBERS = "one two three four five six seven eight nine ten 1 2 3 4 5 6 7 8 9 10".split()
STRONG_CUES = [
    "redo",
    "redo that",
    "redo it",
    "cut that",
    "cut it",
    "scratch that",
    "strike that",
    "start again",
    "starting again",
    "let me start again",
    "let me start over",
    "let me redo that",
    "let me redo it",
    "let me try again",
    "let me try that again",
    "let me say that again",
    "let me do that again",
    "let's start again",
    "let's start over",
    "let's try again",
    "let's try that again",
    "let's go again",
    "let's do that again",
    *(f"{slate} {n}" for slate in ("take", "scene") for n in NUMBERS),
]
WEAK_CUES = [
    "cut",
    "start over",
    "try again",
    "try that again",
    "go again",
    "do that again",
    "do it again",
    "one more time",
    "from the top",
]
SOFTENERS = frozenset("okay ok all right alright sorry no wait yeah so oh oops ugh actually um uh".split())
CUE_OPEN, CUE_CLOSE = 0.25, 0.2

STOCK_PHRASES = [
    "thanks for watching",
    "thank you for watching",
    "please subscribe",
    "like and subscribe",
    "subtitles by",
    "transcribed by",
    "amara org",
]


def core(text: str) -> str:
    """A word with its case and punctuation gone, for matching."""
    return re.sub(r"[^\w']", "", text.lower().replace("’", "'"))


def frame_levels(samples: np.ndarray, size: int) -> np.ndarray:
    frames = len(samples) // size
    rms = np.sqrt((samples[: frames * size].reshape(frames, size) ** 2).mean(axis=1)) + 1e-9
    return 20 * np.log10(rms)


@dataclass
class Envelope:
    """A 20 ms RMS envelope. Speech is measured against the clip's own floor, so outdoor ambience stays below it."""

    db: np.ndarray
    floor: float
    loud: float
    threshold: float

    @classmethod
    def measure(cls, samples: np.ndarray, rate: int) -> Envelope:
        return cls.of_levels(frame_levels(samples, int(rate * HOP)))

    @classmethod
    def of_levels(cls, db: np.ndarray) -> Envelope:
        if not len(db):
            return cls(np.zeros(0), 0.0, 0.0, 0.0)
        floor, loud = float(np.percentile(db, 10)), float(np.percentile(db, 90))
        return cls(db, floor, loud, max(floor + 6.0, loud - 26.0, -52.0))

    @classmethod
    def from_wav(cls, path: str | Path) -> Envelope:
        """Reads the file a block at a time, so hours of sound never sit in memory whole."""
        with wave.open(str(path), "rb") as handle:
            rate, channels, width = handle.getframerate(), handle.getnchannels(), handle.getsampwidth()
            if width != 2:
                raise AltocueError(f"{path} is not 16-bit PCM.")
            size = int(rate * HOP)
            levels: list[np.ndarray] = []
            while raw := handle.readframes(size * ENVELOPE_BLOCK):
                samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
                levels.append(frame_levels(samples.reshape(-1, channels).mean(axis=1) if channels > 1 else samples, size))
        return cls.of_levels(np.concatenate(levels) if levels else np.zeros(0))

    @property
    def speech(self) -> np.ndarray:
        return self.db > self.threshold

    @property
    def reliable(self) -> bool:
        """Under steady noise or wall-to-wall speech, speech and floor cannot be told apart."""
        return self.loud - self.floor >= MIN_CONTRAST

    def has_speech(self, start: float, end: float, *, sustain: int = 3) -> bool:
        """True when `sustain` frames in a row sit above the threshold."""
        mask = self.speech[max(0, int(start / HOP)) : max(0, math.ceil(end / HOP))]
        if len(mask) < sustain:
            return bool(len(mask)) and bool(mask.all())
        return bool((np.convolve(mask.astype(int), np.ones(sustain, dtype=int), "valid") == sustain).any())

    def silence_after(self, start: float, end: float, *, frames: int = 10) -> float | None:
        """When the first 200 ms of quiet between two times begins, if there is any."""
        first = max(0, int(start / HOP))
        quiet = ~self.speech[first : math.ceil(end / HOP)]
        if len(quiet) < frames:
            return None
        hits = np.flatnonzero(np.convolve(quiet.astype(int), np.ones(frames, dtype=int), "valid") == frames)
        return float((first + hits[0]) * HOP) if len(hits) else None

    def speech_seconds(self, start: float, end: float) -> float:
        return float(self.speech[max(0, int(start / HOP)) : max(0, math.ceil(end / HOP))].sum()) * HOP


class Transcriber(Protocol):
    name: str
    hosted: bool

    def available(self) -> bool: ...

    def install_fix(self) -> str: ...

    def notice(self, seconds: float) -> str: ...

    def transcribe(self, wav: Path, *, language: str, prompt: str) -> list[Word]: ...


@dataclass
class LocalEngine:
    name: str
    module: str
    extra: str
    hosted: bool = False
    download: str = ""
    model: str = ""
    needs_gb: float = 3.0

    def available(self) -> bool:
        return importlib.util.find_spec(self.module) is not None

    def install_fix(self) -> str:
        return f'Run `uv tool install --force "altocue[{self.extra}]"`.'

    def notice(self, seconds: float) -> str:
        return f"{self.name}: runs on this machine. The model is {self.download}, downloaded on first use."


class MlxWhisper(LocalEngine):
    english = "mlx-community/whisper-small.en-mlx"
    multilingual = "mlx-community/whisper-large-v3-turbo"

    def transcribe(self, wav: Path, *, language: str, prompt: str) -> list[Word]:
        import mlx_whisper  # pyright: ignore[reportMissingImports]

        repo = self.model or (self.english if language == "en" else self.multilingual)
        limit_mlx()
        result = mlx_whisper.transcribe(
            str(wav),
            path_or_hf_repo=repo,
            word_timestamps=True,
            initial_prompt=prompt or None,
            condition_on_previous_text=False,
            temperature=0.0,
            language=language,
        )
        return [
            Word(text=w["word"].strip(), start=float(w["start"]), end=float(w["end"]), confidence=float(w.get("probability", 1.0)))
            for segment in result["segments"]
            for w in segment.get("words", [])
        ]


class FasterWhisper(LocalEngine):
    english = "small.en"
    multilingual = "large-v3-turbo"

    def device(self) -> str:
        """An Nvidia card when there is one, unless ALTOCUE_WHISPER_DEVICE=cpu says otherwise."""
        if os.environ.get("ALTOCUE_WHISPER_DEVICE", "auto") == "cpu":
            return "cpu"
        try:
            import ctranslate2  # pyright: ignore[reportMissingImports]

            return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            return "cpu"

    def transcribe(self, wav: Path, *, language: str, prompt: str) -> list[Word]:
        # Decoded by ffmpeg first: some PyAV builds reject faster-whisper's open arguments.
        samples, _ = ffmpeg.read_wav(wav)
        if self.device() == "cuda":
            try:
                return self._run(samples, self.model or self.multilingual, "cuda", "float16", language=language, prompt=prompt)
            except RuntimeError as error:  # the card is there but cuBLAS or cuDNN is not
                print(f"faster-whisper could not use the Nvidia card ({error}). Transcribing on the CPU instead.", file=sys.stderr)
        return self._run(samples, self.model or (self.english if language == "en" else self.multilingual), "cpu", "int8", language=language, prompt=prompt)

    def _run(self, samples: Any, name: str, device: str, compute: str, *, language: str, prompt: str) -> list[Word]:
        from faster_whisper import WhisperModel  # pyright: ignore[reportMissingImports]

        segments, _ = WhisperModel(name, device=device, compute_type=compute).transcribe(
            samples,
            vad_filter=READING_BACK.get(),  # music alone read as speech: "We'll be right back." in a film with no voice
            word_timestamps=True,
            language=language,
            initial_prompt=prompt or None,
            condition_on_previous_text=False,
            temperature=0.0,
        )
        return [Word(text=w.word.strip(), start=float(w.start), end=float(w.end), confidence=float(w.probability)) for segment in segments for w in segment.words or []]


class Parakeet(LocalEngine):
    repo = "mlx-community/parakeet-tdt-0.6b-v3"
    chunk, overlap = 120.0, 15.0

    def transcribe(self, wav: Path, *, language: str, prompt: str) -> list[Word]:
        try:
            result = load_parakeet(self.model or self.repo).transcribe(str(wav), chunk_duration=self.chunk, overlap_duration=self.overlap)
        except RuntimeError as error:
            if "alloc" not in str(error).lower() and "memory" not in str(error).lower():
                raise
            raise AltocueError(f"{self.name} ran out of memory.", fix="Close other apps, or wait for other jobs to finish, then run this again.") from error
        return words_from_tokens(result.tokens)


@functools.lru_cache(maxsize=1)
def load_parakeet(name: str) -> Any:
    """The model takes gigabytes, so one load serves every clip in a run."""
    os.environ.setdefault("HF_HUB_VERBOSITY", "error")
    from parakeet_mlx import from_pretrained  # pyright: ignore[reportMissingImports]

    limit_mlx()
    model = from_pretrained(name)
    release_memory()
    return model


def limit_mlx() -> None:
    """Without this, a 61 minute recording held 10 GB on a 16 GB Mac, because nothing gave the cache back."""
    import mlx.core as mx  # pyright: ignore[reportMissingImports]

    mx.set_cache_limit(MLX_CACHE_BYTES)
    mx.set_memory_limit(MLX_LIMIT_BYTES)


def release_memory() -> None:
    """Hand back what the last piece held. Only an engine that has loaded MLX has anything to clear."""
    gc.collect()
    core = sys.modules.get("mlx.core")
    if core is not None:
        core.clear_cache()


def words_from_tokens(tokens: list[Any]) -> list[Word]:
    """Parakeet emits word pieces. A piece that starts with a space starts a word.

    Punctuation can arrive seconds after its word, so it adds to the text and not to the time.
    """
    words: list[Word] = []
    fresh = True
    for token in tokens:
        if not token.text.strip():
            fresh = True
            continue
        if fresh or token.text.startswith(" "):
            words.append(Word(text=token.text.strip(), start=float(token.start), end=float(token.end), confidence=float(token.confidence)))
        elif re.search(r"\w", token.text):
            last = words[-1]
            last.text += token.text
            last.end = float(token.end)
            last.confidence = min(last.confidence, float(token.confidence))
        else:
            words[-1].text += token.text
        fresh = False
    return words


class ElevenLabs:
    """Hosted Scribe. Opt-in only: never chosen by auto-selection."""

    name = "elevenlabs"
    hosted = True
    host = "api.elevenlabs.io"
    endpoint = f"https://{host}/v1/speech-to-text"
    model = "scribe_v2"
    usd_per_hour = 0.22

    def available(self) -> bool:
        return bool(os.environ.get("ELEVENLABS_API_KEY"))

    def install_fix(self) -> str:
        return "Set ELEVENLABS_API_KEY, or use a local engine with `--engine auto`."

    def notice(self, seconds: float) -> str:
        cost = seconds / 3600 * self.usd_per_hour
        return f"{self.name}: uploading audio to {self.host}, about ${cost:.2f} at ${self.usd_per_hour:.2f} per hour."

    def transcribe(self, wav: Path, *, language: str, prompt: str) -> list[Word]:
        key = os.environ.get("ELEVENLABS_API_KEY", "")
        fields = {
            "model_id": self.model,
            "timestamps_granularity": "word",
            "tag_audio_events": "false",
            "diarize": "false",
            "language_code": language,
        }
        body, content_type = multipart(fields, wav)
        request = urllib.request.Request(self.endpoint, data=body, headers={"xi-api-key": key, "Content-Type": content_type}, method="POST")
        from .generate import http

        try:
            with http.open_keyed(request, timeout=1800) as response:
                return scribe_words(json.load(response))
        except urllib.error.HTTPError as error:
            raise AltocueError(f"ElevenLabs refused the audio (HTTP {error.code}).", fix="Check ELEVENLABS_API_KEY and your ElevenLabs plan.") from error
        except urllib.error.URLError as error:
            raise AltocueError(f"Could not reach {self.host}: {error.reason}.", fix="Check your connection, or use a local engine.") from error


def multipart(fields: dict[str, str], path: Path) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode() for name, value in fields.items()]
    head = f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\nContent-Type: audio/wav\r\n\r\n'.encode()
    return b"".join([*parts, head, path.read_bytes(), f"\r\n--{boundary}--\r\n".encode()]), f"multipart/form-data; boundary={boundary}"


def scribe_words(data: dict[str, Any]) -> list[Word]:
    """Scribe mixes words, spacing and audio events in one list."""
    return [
        Word(text=w["text"].strip(), start=float(w["start"]), end=float(w["end"]), confidence=math.exp(float(w.get("logprob", 0.0))))
        for w in data.get("words", [])
        if w.get("type", "word") == "word"
    ]


ENGINES: dict[str, Transcriber] = {
    "parakeet": Parakeet("parakeet", "parakeet_mlx", "parakeet", download="about 2.5 GB", needs_gb=4.0),
    "mlx-whisper": MlxWhisper("mlx-whisper", "mlx_whisper", "mlx-whisper", download="about 460 MB"),
    "faster-whisper": FasterWhisper("faster-whisper", "faster_whisper", "faster-whisper", download="about 480 MB"),
    "elevenlabs": ElevenLabs(),
}


def apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def auto_order() -> list[str]:
    """Measured order, see docs/TRANSCRIPTION.md. Hosted engines are never here."""
    return ["parakeet", "mlx-whisper", "faster-whisper"] if apple_silicon() else ["faster-whisper"]


def select(engine: str | None = None) -> Transcriber:
    """Pick an engine by name, or the best local one installed."""
    if engine and engine != "auto":
        if engine not in ENGINES:
            raise AltocueError(f"Unknown engine {engine}.", fix=f"Use auto or one of {', '.join(ENGINES)}.")
        chosen = ENGINES[engine]
        if not chosen.available():
            raise AltocueError(f"{engine} is not ready on this machine.", fix=chosen.install_fix())
        return chosen
    for name in auto_order():
        if ENGINES[name].available():
            return ENGINES[name]
    raise AltocueError("No local transcription engine is installed.", fix=ENGINES[auto_order()[0]].install_fix())


def classify(words: list[Word], *, cues: list[str] | None = None) -> list[Word]:
    """Mark each word as `word`, `filler` or `cue`. A cue only counts when it stands alone."""
    tokens = [core(w.text) for w in words]
    kinds = ["filler" if token in FILLERS else "word" for token in tokens]
    strong = [phrase.split() for phrase in [*STRONG_CUES, *(cues or [])]]
    phrases = sorted([(p, False) for p in strong] + [(p.split(), True) for p in WEAK_CUES], key=lambda item: len(item[0]), reverse=True)
    index = 0
    while index < len(tokens):
        for phrase, weak in phrases:
            stop = index + len(phrase)
            if tokens[index:stop] != phrase:
                continue
            begin = index
            while begin and tokens[begin - 1] in SOFTENERS:
                begin -= 1
            if begin < index or stands_alone(words, begin, stop, weak=weak):
                kinds[begin:stop] = ["cue"] * (stop - begin)
                index = stop - 1
                break
        index += 1
    return [dataclasses.replace(word, kind=kind) for word, kind in zip(words, kinds, strict=True)]  # type: ignore[arg-type]


def stands_alone(words: list[Word], begin: int, stop: int, *, weak: bool) -> bool:
    """Weak cues read like ordinary sentences, so punctuation alone does not clear them."""
    before = words[begin].start - words[begin - 1].end if begin else math.inf
    after = words[stop].start - words[stop - 1].end if stop < len(words) else math.inf
    opens = before >= CUE_OPEN or (not weak and words[begin - 1].text.rstrip()[-1:] in ".?!")
    closes = after >= CUE_CLOSE or (not weak and words[stop - 1].text.rstrip()[-1:] in ".?!")
    return opens and closes


def clean(words: list[Word], *, duration: float, envelope: Envelope | None = None) -> list[Word]:
    """Repair what engines get wrong before anything trusts these times."""
    found = sorted((w for w in words if is_speech(w.text) and w.start < duration + 0.02), key=lambda w: w.start)
    found = spread_collapsed(found, duration)
    found = drop_loops(found)
    found = drop_stock_phrases(found)
    found = [shorten(word, envelope) for word in found]
    if envelope is not None and envelope.reliable:
        found = [w for w in found if envelope.speech[max(0, int((w.start - GHOST_MARGIN) / HOP)) : math.ceil((w.end + GHOST_MARGIN) / HOP)].any()]
    return separate(found, duration)


def is_speech(text: str) -> bool:
    return bool(re.search(r"\w", text)) and not re.fullmatch(r"\s*[\[(*♪♫].*[\])*♪♫]\s*", text)


def spread_collapsed(words: list[Word], duration: float) -> list[Word]:
    """Short runs stacked on one instant are spread over the room they have and marked unsure."""
    words = [dataclasses.replace(w) for w in words]
    collapsed = 0
    index = 0
    while index < len(words):
        stop = index
        while stop < len(words) and words[stop].end - words[stop].start < COLLAPSED:
            stop += 1
        count = stop - index
        if count >= COLLAPSED_RUN:
            collapsed += count
            limit = words[stop].start if stop < len(words) else duration
            start = words[index].start
            if limit - start < count * MIN_WORD:
                start = words[index - 1].end if index else 0.0
            step = min((limit - start) / count, SPREAD_MAX)
            for offset, word in enumerate(words[index:stop]):
                word.start, word.end = start + offset * step, start + (offset + 1) * step
                word.confidence = min(word.confidence, 0.1)
        index = max(stop, index + 1)
    if collapsed and collapsed / len(words) > COLLAPSED_LIMIT:
        raise AltocueError(
            f"{collapsed} of {len(words)} words came back with collapsed times.",
            fix="Run again with another engine, such as `--engine mlx-whisper`.",
        )
    return words


def drop_loops(words: list[Word]) -> list[Word]:
    """Whisper can repeat itself over silence or a prompt. Keep one copy of the loop."""
    tokens = [core(w.text) for w in words]
    keep = [True] * len(words)
    index = 0
    while index < len(tokens):
        size, repeats = 0, 0
        for candidate in range(1, 7):
            count = 1
            while tokens[index : index + candidate] == tokens[index + count * candidate : index + (count + 1) * candidate]:
                count += 1
            limit = LOOP_PHRASE if candidate > 1 else LOOP_FILLER if tokens[index] in FILLERS else LOOP_WORD
            if count >= limit and count * candidate > size * repeats:
                size, repeats = candidate, count
        if repeats:
            keep[index + size : index + size * repeats] = [False] * (size * (repeats - 1))
            index += size * repeats
        else:
            index += 1
    return [w for w, kept in zip(words, keep, strict=True) if kept]


def drop_stock_phrases(words: list[Word]) -> list[Word]:
    """Sign-offs Whisper learned from subtitles. Only unsure copies go: people do say these."""
    tokens = [core(w.text) for w in words]
    keep = [True] * len(words)
    for phrase in map(str.split, STOCK_PHRASES):
        for index in range(len(tokens) - len(phrase) + 1):
            stop = index + len(phrase)
            if tokens[index:stop] == phrase and sum(w.confidence for w in words[index:stop]) / len(phrase) < STOCK_CONFIDENCE:
                keep[index:stop] = [False] * len(phrase)
    return [w for w, kept in zip(words, keep, strict=True) if kept]


def shorten(word: Word, envelope: Envelope | None) -> Word:
    """Engines stretch a word across the pause after it. Audio says where it stopped."""
    if word.end - word.start <= LONG_WORD:
        return word
    if envelope is None or not envelope.reliable:
        return dataclasses.replace(word, end=word.start + LONG_WORD)
    stop = envelope.silence_after(word.start, word.end)
    return dataclasses.replace(word, end=max(stop, word.start + MIN_WORD)) if stop is not None else word


def separate(words: list[Word], duration: float) -> list[Word]:
    """No zero-length words, no overlaps, nothing past the end, times to the millisecond."""
    out: list[Word] = []
    previous = 0.0
    for word in words:
        start = min(max(word.start, previous), max(duration - MIN_WORD, 0.0))
        end = min(max(word.end, start + MIN_WORD), duration)
        out.append(dataclasses.replace(word, text=word.text.strip(), start=round(start, 3), end=round(end, 3), confidence=round(word.confidence, 3)))
        previous = end
    return out


def done(project: Project, clip_id: str) -> bool:
    path = project.words_dir / f"{clip_id}.json"
    return path.exists() and path.stat().st_size > 10


def prompt_for(project: Project, *, engine: str) -> str:
    """Whisper hears these spellings better when told. Parakeet takes no prompt."""
    if engine not in {"mlx-whisper", "faster-whisper"}:
        return ""
    words = [str(w).rstrip(".") + "." for w in project.setting("transcribe", "words", [])]
    return " ".join([FILLER_PROMPT, *words])


def quiet_marks(envelope: Envelope, duration: float, piece: float | None = None) -> list[float]:
    """Where to split a recording: the quietest pause near each mark, so no word is cut in two.

    The marks follow the clip's length and the envelope stops where the sound does, so past the sound there is nothing to split.
    """
    length = piece or PIECE
    reach = min(REACH, length / 3)
    if duration <= length * 1.5 or not len(envelope.db):
        return []
    held = np.convolve(envelope.db, np.ones(QUIET_WINDOW) / QUIET_WINDOW, "same")
    marks: list[float] = []
    place = length
    while place < duration - length / 2:
        first, last = int((place - reach) / HOP), int((place + reach) / HOP)
        if first >= len(held):
            break
        marks.append(round((first + int(np.argmin(held[first:last]))) * HOP, 3))
        place = marks[-1] + length
    return marks


def in_pieces(engine: Transcriber, wav: Path, envelope: Envelope, duration: float, *, language: str, prompt: str, piece: float | None = None) -> list[Word]:
    """Hand a local engine the recording a piece at a time, clearing up after each, so memory stays flat however long it runs."""
    marks = [] if engine.hosted else quiet_marks(envelope, duration, piece)
    words: list[Word] = []
    edges = [0.0, *marks, duration]
    for start, end in zip(edges, edges[1:], strict=False):
        piece = ffmpeg.extract_wav(wav, wav.with_name("piece.wav"), rate=RATE, start=start, end=end) if marks else wav
        heard = engine.transcribe(piece, language=language, prompt=prompt)
        words += [dataclasses.replace(word, start=word.start + start, end=word.end + start) for word in heard]
        release_memory()
    return words


def check_room(engine: Transcriber, seconds: float) -> None:
    """Refuse before anything loads, because running out halfway can take the whole machine down."""
    needs = float(getattr(engine, "needs_gb", 0.0))
    free = free_memory_gb() if needs and not engine.hosted else 0.0
    if free is None:
        print("Could not read free memory here, so this runs without that check.")
    elif needs and not engine.hosted and free < needs:
        raise AltocueError(
            f"Only {free:.1f} GB of memory is free, and {engine.name} needs about {needs:g} GB.",
            fix="Close other apps, or wait for other jobs to finish, then run this again.",
        )
    disk = shutil.disk_usage(tempfile.gettempdir()).free / 2**30
    wanted = MIN_FREE_DISK_GB + seconds * WAV_BYTES_PER_SECOND / 2**30
    if disk < wanted:
        raise AltocueError(
            f"Only {disk:.1f} GB of disk is free, and this needs about {wanted:.1f} GB, part of it held back for swap.",
            fix="Free some disk space, then run this again.",
        )


def transcribe_clip(clip: Clip, engine: Transcriber, *, language: str, prompt: str, cues: list[str], piece: float | None = None) -> list[Word]:
    check_room(engine, clip.duration)
    with tempfile.TemporaryDirectory() as folder:
        wav = ffmpeg.extract_wav(clip.path, Path(folder) / "audio.wav", rate=RATE)
        envelope = Envelope.from_wav(wav)
        raw = in_pieces(engine, wav, envelope, clip.duration, language=language, prompt=prompt, piece=piece)
    return classify(clean(raw, duration=clip.duration, envelope=envelope), cues=cues)


@contextmanager
def reading_back() -> Iterator[None]:
    """Mark a transcription as a finished render read back for its checks. Engines that can skip non-speech then do.

    A raw take is never read this way: speech detection drops the fillers the cut has to find.
    """
    token = READING_BACK.set(True)
    try:
        yield
    finally:
        READING_BACK.reset(token)


def transcribe_file(path: str | Path, engine: str | None = None, language: str = "en", spans: list[tuple[float, float]] | None = None) -> list[Word]:
    """Read any media file back to words, for checks on a finished render. A render has music under it, so it goes in shorter pieces.

    `spans` are the stretches, in seconds, where the render has a voice. Each is read on its own, so a line on one side of a
    stretch of music is never run together with a line on the other, and music alone is never heard as words.
    """
    with heavy("reading the render back"), reading_back():
        clip, chosen = ffmpeg.probe_clip(path), select(engine)
        if not spans:
            return transcribe_clip(clip, chosen, language=language, prompt="", cues=[], piece=READBACK_PIECE)
        check_room(chosen, clip.duration)
        with tempfile.TemporaryDirectory() as folder:
            wav = ffmpeg.extract_wav(clip.path, Path(folder) / "audio.wav", rate=RATE)
            envelope = Envelope.from_wav(wav)
            raw: list[Word] = []
            for start, end in spans:
                piece = ffmpeg.extract_wav(wav, Path(folder) / "span.wav", rate=RATE, start=start, end=end)
                raw += [dataclasses.replace(word, start=word.start + start, end=word.end + start) for word in chosen.transcribe(piece, language=language, prompt="")]
                release_memory()
        return classify(clean(raw, duration=clip.duration, envelope=envelope), cues=[])


def transcribe_project(project: Project, engine: str | Transcriber | None = None, force: bool = False) -> dict[str, int]:
    """Transcribe every clip not done yet. Returns the word count of each clip it worked on."""
    named = engine if isinstance(engine, str) or engine is None else None
    chosen = select(named or project.setting("transcribe", "engine") or None) if isinstance(engine, str) or engine is None else engine
    clips = project.clips()
    pending = [clip for clip in clips if force or not done(project, clip.id)]
    for clip in clips:
        if clip not in pending:
            print(f"{clip.id}: already transcribed")
    speaking = [clip for clip in pending if clip.has_audio]
    if speaking:
        print(chosen.notice(sum(clip.duration for clip in speaking)))
    counts: dict[str, int] = {}
    language = project.setting("transcribe", "language", "en")
    prompt = prompt_for(project, engine=chosen.name)
    cues = list(project.setting("cut", "cues", []))
    with heavy("transcribing"):
        for clip in pending:
            if not clip.has_audio:
                project.save_words(clip.id, [], chosen.name)
                counts[clip.id] = 0
                print(f"{clip.id}: no audio")
                continue
            words = transcribe_clip(clip, chosen, language=language, prompt=prompt, cues=cues)
            project.save_words(clip.id, words, chosen.name)
            counts[clip.id] = len(words)
            fillers, spoken_cues = sum(w.kind == "filler" for w in words), sum(w.kind == "cue" for w in words)
            print(f"{clip.id}: {len(words)} words, {fillers} fillers, {spoken_cues} cue words ({chosen.name})")
    return counts


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", "-p", default=".", help="Project folder")
    parser.add_argument("--engine", default=None, help=f"auto or one of {', '.join(ENGINES)}. Hosted engines run only when named here or in altocue.toml")
    parser.add_argument("--force", action="store_true", help="Transcribe clips that already have a word file")
    parser.add_argument("--speakers", nargs="?", type=int, const=0, default=None, metavar="N", help="Also work out who said each word. Give N if you know how many spoke")


def run(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    counts = transcribe_project(project, engine=args.engine, force=args.force)
    print(f"Transcribed {len(counts)} clips, {sum(counts.values())} words")
    if getattr(args, "speakers", None) is not None:
        from . import speakers

        for clip, seconds in speakers.label_project(project, count=args.speakers).items():
            print(f"{clip}: " + ", ".join(f"{name} {value:.0f} s" for name, value in seconds.items()))
    return 0
