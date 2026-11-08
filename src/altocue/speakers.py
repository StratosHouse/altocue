"""Who said each word. Optional, on this machine, and off until asked for.

Words stay as they were transcribed. This only adds a speaker to each one, from turns found by
any `Diariser`. The one here uses sherpa-onnx, with two models fetched once by `altocue setup --speakers`.

The speaker embedding model is the one 3D-Speaker trained on its own Chinese set of about 200,000
speakers. The English CAM++ was left out on purpose: its weights are Apache-2.0, but they were trained on
VoxCeleb, whose page offers the data "for research purposes" and leaves the videos' copyright with their owners.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import os
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from . import ffmpeg, locks
from .errors import AltocueError
from .models import Word
from .project import Project, write_json

RATE = 16000
NEAREST = 0.5  # a word this close to a turn, and overlapping none, belongs to it
CLUSTER_THRESHOLD = 0.6  # with the speaker count unknown. Right on four real recordings; 0.5 split one voice in two, 0.7 merged two. Give the count when known
MIN_TURN, MIN_GAP = 0.3, 0.5
WINDOW = 300.0  # seconds one clustering pass holds steady. On a 27 minute call one pass gave 1419 s to one voice and 31 s to the other
MIN_SEED = 5.0  # seconds a window's voice needs before it can start a speaker of its own; shorter ones join the nearest
LIKENESS_AUDIO = 60.0  # seconds of a window's voice used to describe it
LINK_THRESHOLD = 0.5  # with no count, window voices this alike are one speaker. One 27 minute call: same voice median 0.14, different 0.59
DOWNLOADS = "https://github.com/k2-fsa/sherpa-onnx/releases/download"


@dataclass(frozen=True)
class Turn:
    start: float
    end: float
    speaker: str


@dataclass(frozen=True)
class SpeakerModel:
    name: str
    file: str
    url: str
    sha256: str
    megabytes: float
    licence: str
    member: str = ""  # where the file sits in the archive, if it comes in one


MODELS = (
    SpeakerModel(
        "voice activity",
        "segmentation.onnx",
        f"{DOWNLOADS}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
        "24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488",
        7.0,
        "MIT, pyannote segmentation-3.0, copyright CNRS",
        "sherpa-onnx-pyannote-segmentation-3-0/model.onnx",
    ),
    SpeakerModel(
        "voice likeness",
        "3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx",
        f"{DOWNLOADS}/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx",
        "f682b514c05d947ee3fa91cd6ec6c5c7543479a128373fa29b1faedccd21fd11",
        28.3,
        "Apache-2.0, 3D-Speaker CAM++",
    ),
)


class Diariser(Protocol):
    name: str

    def turns(self, wav: Path, *, count: int = 0) -> list[Turn]: ...


def model_folder() -> Path:
    chosen = os.environ.get("ALTOCUE_SPEAKER_MODELS")
    return Path(chosen) if chosen else Path.home() / ".altocue" / "models" / "speakers"


def models_ready() -> bool:
    return all((model_folder() / model.file).exists() for model in MODELS)


@dataclass
class Sherpa:
    name: str = "sherpa-onnx"

    def turns(self, wav: Path, *, count: int = 0) -> list[Turn]:
        samples, _ = ffmpeg.read_wav(wav)  # clustering needs every voice at once, so this one step holds the sound in memory
        if len(samples) <= WINDOW * 1.5 * RATE:
            return self.diarise(samples, count)
        return self.linked(samples, count)

    def diarise(self, samples, count: int) -> list[Turn]:
        import sherpa_onnx  # pyright: ignore[reportMissingImports]

        segmentation, likeness = (str(model_folder() / model.file) for model in MODELS)
        pyannote = sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=segmentation)
        config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(pyannote=pyannote),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=likeness),
            clustering=sherpa_onnx.FastClusteringConfig(num_clusters=count or -1, threshold=CLUSTER_THRESHOLD),
            min_duration_on=MIN_TURN,
            min_duration_off=MIN_GAP,
        )
        result = sherpa_onnx.OfflineSpeakerDiarization(config).process(samples).sort_by_start_time()
        return [Turn(float(row.start), float(row.end), str(row.speaker)) for row in result]

    def linked(self, samples, count: int) -> list[Turn]:
        """A long recording in windows short enough to cluster well, then each window's voices matched across windows."""
        import sherpa_onnx  # pyright: ignore[reportMissingImports]

        likeness = sherpa_onnx.SpeakerEmbeddingExtractor(sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(model_folder() / MODELS[1].file)))
        voices: list[list[Turn]] = []
        vectors: list[np.ndarray] = []
        for offset, chunk in windows(samples):
            found: dict[str, list[Turn]] = {}
            for turn in self.diarise(chunk, count):
                found.setdefault(turn.speaker, []).append(turn)
            for turns in found.values():
                stream = likeness.create_stream()
                stream.accept_waveform(RATE, voice_sample(chunk, turns))
                stream.input_finished()
                vectors.append(np.asarray(likeness.compute(stream), dtype=np.float32))
                voices.append([Turn(offset + turn.start, offset + turn.end, "") for turn in turns])
        labels = link(vectors, [sum(turn.end - turn.start for turn in turns) for turns in voices], count)
        return sorted((dataclasses.replace(turn, speaker=str(label)) for turns, label in zip(voices, labels, strict=True) for turn in turns), key=lambda turn: turn.start)


def windows(samples) -> list[tuple[float, object]]:
    """Cut into WINDOW pieces, the last one taking any short remainder."""
    size = int(WINDOW * RATE)
    starts = list(range(0, len(samples), size))
    if len(starts) > 1 and len(samples) - starts[-1] < size // 2:
        starts.pop()
    ends = starts[1:] + [len(samples)]
    return [(start / RATE, samples[start:end]) for start, end in zip(starts, ends, strict=True)]


def voice_sample(chunk, turns: list[Turn]):
    """Up to LIKENESS_AUDIO seconds of one voice, longest turns first."""
    pieces, held = [], 0.0
    for turn in sorted(turns, key=lambda turn: turn.start - turn.end):
        if held >= LIKENESS_AUDIO:
            break
        pieces.append(chunk[int(turn.start * RATE) : int(turn.end * RATE)])
        held += turn.end - turn.start
    return np.concatenate(pieces).astype(np.float32)


def link(vectors: list[np.ndarray], seconds: list[float], count: int = 0) -> list[int]:
    """Match voices found window by window. Each label is a speaker across the whole recording.

    Voices with at least MIN_SEED seconds are merged, closest first, until `count` remain or, with no count, until
    the closest pair is further apart than LINK_THRESHOLD. Shorter voices then join the nearest speaker."""
    if not vectors:
        return []
    unit = [vector / (np.linalg.norm(vector) or 1.0) for vector in vectors]
    seeds = [index for index, held in enumerate(seconds) if held >= MIN_SEED] or list(range(len(unit)))
    groups = [[index] for index in seeds]

    def centre(group: list[int]) -> np.ndarray:
        mean = sum(unit[index] * seconds[index] for index in group) / max(sum(seconds[index] for index in group), 1e-9)
        return mean / (np.linalg.norm(mean) or 1.0)

    while len(groups) > max(count, 1):
        centres = [centre(group) for group in groups]
        distance, a, b = min((1.0 - float(centres[a] @ centres[b]), a, b) for a in range(len(groups)) for b in range(a + 1, len(groups)))
        if not count and distance > LINK_THRESHOLD:
            break
        groups[a] += groups.pop(b)
    centres = [centre(group) for group in groups]
    labels = [0] * len(unit)
    for label, group in enumerate(groups):
        for index in group:
            labels[index] = label
    for index in range(len(unit)):
        if index not in seeds:
            labels[index] = max(range(len(centres)), key=lambda label: float(centres[label] @ unit[index]))
    return labels


def select() -> Diariser:
    if importlib.util.find_spec("sherpa_onnx") is None:
        fix = 'Run `uv tool install --force "altocue[speakers]"`, then `altocue setup --speakers`.'
        raise AltocueError("Telling speakers apart needs sherpa-onnx.", fix=fix)
    if not models_ready():
        raise AltocueError("The speaker models are not on this machine.", fix="Run `altocue setup --speakers`. It fetches about 35 MB, once.")
    return Sherpa()


def owner(word: Word, turns: list[Turn]) -> str:
    """The turn this word overlaps most, or the nearest one close enough."""
    best = max(turns, key=lambda turn: min(word.end, turn.end) - max(word.start, turn.start), default=None)
    if best is None:
        return ""
    if min(word.end, best.end) - max(word.start, best.start) > 0:
        return best.speaker
    near = min(turns, key=lambda turn: max(turn.start - word.end, word.start - turn.end))
    return near.speaker if max(near.start - word.end, word.start - near.end) <= NEAREST else ""


def assign(words: list[Word], turns: list[Turn]) -> list[Word]:
    """Give each word a speaker. S1 is whoever speaks first."""
    labels: dict[str, str] = {}
    found: list[Word] = []
    for word in words:
        raw = owner(word, turns)
        if raw:
            labels.setdefault(raw, f"S{len(labels) + 1}")
        found.append(dataclasses.replace(word, speaker=labels.get(raw, "")))
    return found


def talk_time(words: list[Word]) -> dict[str, float]:
    seconds: Counter[str] = Counter()
    for word in words:
        if word.speaker:
            seconds[word.speaker] += word.end - word.start
    return {name: round(value, 3) for name, value in seconds.most_common()}


def label_project(project: Project, *, diariser: Diariser | None = None, count: int = 0) -> dict[str, dict[str, float]]:
    """Put a speaker on every word. The transcript's text and times stay exactly as they were."""
    chosen = diariser or select()
    found: dict[str, dict[str, float]] = {}
    with locks.heavy("telling speakers apart"):
        for clip in project.clips():
            words = project.words(clip.id)
            if not words or not clip.has_audio:
                continue
            with tempfile.TemporaryDirectory() as folder:
                turns = chosen.turns(ffmpeg.extract_wav(clip.path, Path(folder) / "audio.wav", rate=RATE), count=count)
            if not turns:
                print(f"{clip.id}: no speech the speaker models could use, so its words stay unlabelled.")
                continue
            labelled = assign(words, turns)
            project.save_words(clip.id, labelled, str(project.engine_of(clip.id)))
            found[clip.id] = talk_time(labelled)
    write_json(project.work / "speakers.json", {"engine": chosen.name, "clips": found})
    return found
