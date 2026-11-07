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
LONG_SIDE, SHORT_SIDE = 1920, 1080
WIDEST = 4.0  # Past 4:1 each way there is no room for a face, a caption or a safe zone.
ASPECT = re.compile(r"(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)")

DEFAULT_CONFIG = """\
# Altocue project settings. Every value here can be changed and re-run.

[output]
aspect = "{aspect}"     # any W:H, such as 9:16, 16:9, 1:1, 4:5, 4:3 or 21:9
fps = 0                 # 0 follows the sources: 24, 25, 30, 50 or 60
# platform = "reels"    # keep text inside that app's safe zone: reels, tiktok, shorts, youtube, linkedin or square

[brand]
name = "default"        # a preset name or a path to a brand.json

[transcribe]
engine = ""             # empty picks the best one installed
language = "en"

[transcribe.fix]        # names the transcript gets wrong, heard = written
# "Altoque" = "Altocue"

[speakers]              # needs `altocue transcribe --speakers`
show = ""               # on captions: colour, name or both. Empty shows nothing
# S1 = "Ada"            # the name to show

[cut]
max_pause = 0.4         # seconds of silence kept between words at most
remove_fillers = true

[audio]
denoise = true
denoiser = "auto"       # auto, apple, deep-filter or afftdn
level_phrases = true    # level phrase by phrase, so a quiet aside and a loud line sit together
deess = "light"         # off, light, normal or strong
breaths = 0             # dB to turn breaths down. They are never cut
room_tone = true        # fill what cuts leave with the clip's own room, so nothing drops to digital silence
loudness = -14.0        # integrated LUFS, or a name: streaming, podcast, broadcast
true_peak = -1.5        # dBTP

[grade]
enabled = true          # off still converts HDR, so nothing goes out washed out
look = "natural"        # natural, warm, cool, punchy, filmic, or a film look: `altocue grade --compare`
look_strength = 1.0     # how much of the look or LUT, from 0 to 1
lut = ""                # your own .cube file, in place of a look

[frame]
enabled = true
punch_in = 1.12         # zoom used to hide jump cuts
push_in = 0.0           # slow zoom added per shot, such as 0.04

[broll]
share = 0.3             # the most of the runtime B-roll may cover
library = []            # folders of your own clips, searched first

[generate]
provider = ""           # fal or higgsfield. Empty means nothing generates
spend_cap_usd = 0.0     # the most this project may spend, on every route. 0 blocks all paid calls
usd_per_credit = 0.08   # Higgsfield only: your plan's dollars per credit

[disclosure]            # for a film with a generated person. A check, not legal advice
label = "AI-generated actor"   # the words that must stay on screen. Put them in motion.json
exempt = ""             # why no label is needed, in your words. Recorded, never judged

[ugc]
label_text = []         # words printed on the real product, so `qa --check ugc` does not call them baked in
[qa]
reading_wps = 3.0       # words a second for the text_readable gate. Slower for children or second-language viewers

[review]
reviewer = "auto"       # auto, gemini, codex or pack
"""


def size_for(aspect: str) -> tuple[int, int]:
    """The four house sizes, or any `W:H` fitted inside them: 1920 on the long side, 1080 on the short, on even numbers."""
    if aspect in SIZES:
        return SIZES[aspect]
    found = ASPECT.fullmatch(str(aspect).strip())
    wide, tall = (float(found.group(1)), float(found.group(2))) if found else (0.0, 0.0)
    if not wide or not tall or not 1 / WIDEST <= wide / tall <= WIDEST:
        raise AltocueError(f"Unknown aspect {aspect}.", fix=f"Use W:H between 1:{WIDEST:g} and {WIDEST:g}:1, such as {', '.join(SIZES)}, 4:3 or 21:9.")
    scale = min(LONG_SIDE / max(wide, tall), SHORT_SIDE / min(wide, tall))
    return (round(wide * scale / 2) * 2, round(tall * scale / 2) * 2)


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
        size_for(aspect)
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
    def script_path(self) -> Path:
        return self.root / "script.md"

    @property
    def timeline_path(self) -> Path:
        return self.root / "timeline.json"

    @property
    def media_path(self) -> Path:
        return self.work / "media.json"

    @property
    def size(self) -> tuple[int, int]:
        return size_for(self.config["output"]["aspect"])

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
        return self.corrected_all([Word.from_dict(w) for w in json.loads(path.read_text())["words"]])

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

    def corrected_all(self, words: list[Word]) -> list[Word]:
        """`[transcribe.fix]` over a run of words. A key of two or three words joins them, because a made-up name often comes back split."""
        fixes = {str(k).lower(): str(v) for k, v in self.config.get("transcribe", {}).get("fix", {}).items()}
        joined = {key: value for key, value in fixes.items() if " " in key}
        fixed: list[Word] = []
        index = 0
        while index < len(words):
            for size in (3, 2):
                run = words[index : index + size]
                parts = [re.fullmatch(r"(\W*)(.*?)(\W*)", word.text, re.DOTALL) for word in run]
                said = " ".join(part.group(2).lower() for part in parts if part)
                if len(run) == size and said in joined:
                    fixed.append(replace(run[0], text=f"{parts[0].group(1)}{joined[said]}{parts[-1].group(3)}", end=run[-1].end))  # type: ignore[union-attr]
                    index += size
                    break
            else:
                fixed.append(self.corrected(words[index]))
                index += 1
        return fixed

    def engine_of(self, clip_id: str) -> str:
        return json.loads((self.words_dir / f"{clip_id}.json").read_text()).get("engine", "")

    def save_words(self, clip_id: str, words: list[Word], engine: str) -> None:
        path = self.words_dir / f"{clip_id}.json"
        previous = json.loads(path.read_text()) if path.exists() else {}
        record = {"engine": engine, "words": [w.to_dict() for w in words]}
        # Text fixes and speaker labels do not change the source clock. New or
        # imported words have no verified clock, so never manufacture this field.
        if "source_clock" in previous:
            record["source_clock"] = previous["source_clock"]
        write_json(path, record)

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
