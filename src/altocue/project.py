"""A project is a folder. Everything Altocue knows lives in it as plain files."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .errors import AltocueError
from .models import Clip, Edl, Word

CONFIG = "altocue.toml"

SIZES = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080), "4:5": (1080, 1350)}
OUTPUT_RATES = (24, 25, 30, 50, 60)

DEFAULT_CONFIG = """\
# Altocue project settings. Every value here can be changed and re-run.

[output]
aspect = "{aspect}"     # 9:16, 16:9, 1:1 or 4:5
fps = 0                 # 0 follows the sources: 24, 25, 30, 50 or 60

[brand]
name = "default"        # a preset name or a path to a brand.json

[transcribe]
engine = ""             # empty picks the best one installed
language = "en"

[transcribe.fix]        # names the transcript gets wrong, heard = written
# "Altoque" = "Altocue"

[cut]
max_pause = 0.4         # seconds of silence kept between words at most
remove_fillers = true

[audio]
denoise = true
loudness = -14.0        # integrated LUFS
true_peak = -1.5        # dBTP

[grade]
enabled = true          # off still converts HDR, so nothing goes out washed out
look = "natural"        # natural, warm, cool, punchy or filmic

[frame]
enabled = true
punch_in = 1.12         # zoom used to hide jump cuts
push_in = 0.0           # slow zoom added per shot, such as 0.04

[broll]
share = 0.3             # the most of the runtime B-roll may cover
library = []            # folders of your own clips, searched first

[generate]
provider = ""           # fal or higgsfield. Empty means nothing generates
spend_cap_usd = 0.0     # the most one run may spend. 0 blocks all paid calls
usd_per_credit = 0.08   # Higgsfield only: your plan's dollars per credit

[review]
reviewer = "auto"       # auto, gemini, codex or pack
"""


def nearest_rate(fps: float) -> int:
    return min(OUTPUT_RATES, key=lambda rate: abs(rate - fps))


@dataclass
class Project:
    root: Path
    config: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def open(cls, path: str | Path = ".") -> Project:
        root = Path(path).resolve()
        target = root / CONFIG
        if not target.exists():
            raise AltocueError(f"No {CONFIG} in {root}.", fix="Run `altocue new <folder> --input <video>` first.")
        return cls(root=root, config=tomllib.loads(target.read_text()))

    @classmethod
    def create(cls, path: str | Path, *, aspect: str = "9:16") -> Project:
        if aspect not in SIZES:
            raise AltocueError(f"Unknown aspect {aspect}.", fix=f"Use one of {', '.join(SIZES)}.")
        root = Path(path).resolve()
        if (root / CONFIG).exists():
            raise AltocueError(f"{root} is already an Altocue project.")
        root.mkdir(parents=True, exist_ok=True)
        (root / CONFIG).write_text(DEFAULT_CONFIG.format(aspect=aspect))
        project = cls.open(root)
        for folder in (project.sources, project.words_dir, project.audio_dir, project.lut_dir, project.layers_dir, project.out, project.review_dir):
            folder.mkdir(parents=True, exist_ok=True)
        return project

    @property
    def sources(self) -> Path:
        return self.root / "sources"

    @property
    def work(self) -> Path:
        return self.root / "work"

    @property
    def words_dir(self) -> Path:
        return self.work / "words"

    @property
    def audio_dir(self) -> Path:
        return self.work / "audio"

    @property
    def lut_dir(self) -> Path:
        return self.work / "luts"

    @property
    def layers_dir(self) -> Path:
        return self.work / "layers"

    @property
    def out(self) -> Path:
        return self.root / "out"

    @property
    def review_dir(self) -> Path:
        return self.root / "review"

    @property
    def edl_path(self) -> Path:
        return self.root / "edl.json"

    @property
    def timeline_path(self) -> Path:
        return self.root / "timeline.json"

    @property
    def media_path(self) -> Path:
        return self.work / "media.json"

    @property
    def size(self) -> tuple[int, int]:
        return SIZES[self.config["output"]["aspect"]]

    def setting(self, section: str, key: str, default: Any = None) -> Any:
        return self.config.get(section, {}).get(key, default)

    def clips(self) -> list[Clip]:
        if not self.media_path.exists():
            raise AltocueError("This project has not been ingested.", fix="Run `altocue ingest`.")
        return [Clip.from_dict(c) for c in json.loads(self.media_path.read_text())["clips"]]

    def clip(self, clip_id: str) -> Clip:
        for clip in self.clips():
            if clip.id == clip_id:
                return clip
        raise AltocueError(f"No clip named {clip_id}.")

    def save_clips(self, clips: list[Clip]) -> None:
        write_json(self.media_path, {"clips": [c.to_dict() for c in clips]})

    @property
    def fps(self) -> int:
        chosen = int(self.setting("output", "fps", 0) or 0)
        if chosen:
            return chosen
        clips = self.clips()
        longest = max(clips, key=lambda c: c.duration)
        return nearest_rate(longest.fps)

    def words(self, clip_id: str) -> list[Word]:
        path = self.words_dir / f"{clip_id}.json"
        if not path.exists():
            raise AltocueError(f"No transcript for {clip_id}.", fix="Run `altocue transcribe`.")
        return [self.corrected(Word.from_dict(w)) for w in json.loads(path.read_text())["words"]]

    def fix_text(self, text: str) -> str:
        """Apply `[transcribe.fix]`, keeping any punctuation around the word."""
        fixes = {str(k).lower(): str(v) for k, v in self.config.get("transcribe", {}).get("fix", {}).items()}
        if not fixes:
            return text
        match = re.fullmatch(r"(\W*)(.*?)(\W*)", text, re.DOTALL)
        head, core, tail = match.groups() if match else ("", text, "")
        return f"{head}{fixes.get(core.lower(), core)}{tail}"

    def corrected(self, word: Word) -> Word:
        fixed = self.fix_text(word.text)
        return word if fixed == word.text else replace(word, text=fixed)

    def save_words(self, clip_id: str, words: list[Word], engine: str) -> None:
        write_json(self.words_dir / f"{clip_id}.json", {"engine": engine, "words": [w.to_dict() for w in words]})

    def edl(self) -> Edl:
        if not self.edl_path.exists():
            raise AltocueError("This project has no cut yet.", fix="Run `altocue cut`.")
        return Edl.from_dict(json.loads(self.edl_path.read_text()))

    def save_edl(self, edl: Edl) -> None:
        write_json(self.edl_path, edl.to_dict())

    def cut_mark(self) -> str:
        """Changes when the cut does, because anything placed on words is only true for one cut. Zooms do not count."""
        cuts = [[s.clip, s.start, s.end] for s in self.edl().segments]
        return hashlib.sha256(json.dumps(cuts).encode()).hexdigest()[:16]


def write_json(path: str | Path, data: Any) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return target
