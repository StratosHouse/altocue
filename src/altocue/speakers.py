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

from . import ffmpeg, locks
from .errors import AltocueError
from .models import Word
from .project import Project, write_json

RATE = 16000
NEAREST = 0.5  # a word this close to a turn, and overlapping none, belongs to it
CLUSTER_THRESHOLD = 0.6  # with the speaker count unknown. Right on four real recordings; 0.5 split one voice in two, 0.7 merged two. Give the count when known
MIN_TURN, MIN_GAP = 0.3, 0.5
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
        import sherpa_onnx  # pyright: ignore[reportMissingImports]

        folder = model_folder()
        segmentation, likeness = (str(folder / model.file) for model in MODELS)
        pyannote = sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=segmentation)
        config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(pyannote=pyannote),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=likeness),
            clustering=sherpa_onnx.FastClusteringConfig(num_clusters=count or -1, threshold=CLUSTER_THRESHOLD),
            min_duration_on=MIN_TURN,
            min_duration_off=MIN_GAP,
        )
        samples, _ = ffmpeg.read_wav(wav)  # clustering needs every voice at once, so this one step holds the sound in memory
        result = sherpa_onnx.OfflineSpeakerDiarization(config).process(samples).sort_by_start_time()
        return [Turn(float(row.start), float(row.end), str(row.speaker)) for row in result]


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
