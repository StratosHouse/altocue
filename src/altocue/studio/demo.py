"""Create original geometric inputs so a clean install can exercise the real renderer offline."""

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .. import ffmpeg
from ..project import write_json
from .canvas import font
from .plan import need


def create(root: Path):
    need(not root.exists() or (root.is_dir() and not any(root.iterdir())), "Choose a new or empty demo directory.")
    root.mkdir(parents=True, exist_ok=True)
    folder = root / "assets"
    folder.mkdir()
    paths = {}

    def save(key, im):
        im.save(folder / (key + ".png"))
        paths[key] = "assets/" + key + ".png"

    colors = ["#b6eccd", "#efa97f", "#93cfd7"]
    for i, name in enumerate(("tool-a", "tool-b", "tool-c")):
        im = Image.new("RGBA", (180, 180))
        d = ImageDraw.Draw(im)
        if i == 0:
            d.regular_polygon((90, 90, 78), 3, rotation=0, fill=colors[i])
        if i == 1:
            d.rounded_rectangle((24, 24, 156, 156), radius=33, fill=colors[i])
        if i == 2:
            d.ellipse((19, 19, 161, 161), outline=colors[i], width=26)
        save(name, im)
    mark = Image.new("RGBA", (190, 190))
    for i, key in enumerate(("part-a", "part-b", "part-c")):
        part = Image.new("RGBA", mark.size)
        d = ImageDraw.Draw(part)
        y = 26 + i * 48
        d.polygon([(28, y + 27), (120, y), (166, y + 15), (75, y + 42)], fill=colors[i])
        save(key, part)
        mark.alpha_composite(part)
    save("house", mark)
    save("character", mark)
    page = Image.new("RGB", (680, 1520), "#e7eee4")
    d = ImageDraw.Draw(page)
    for j, (title, sub) in enumerate(
        [("Studio / source", "A local demonstration"), ("First, collect.", "01 / Inputs"), ("Then, transform.", "02 / Build"), ("Finally, inspect.", "03 / Review")]
    ):
        y = 60 + j * 365
        d.rounded_rectangle((35, y, 645, y + 303), 32, fill="#244648")
        d.text((67, y + 38), sub, font=font(20, "m"), fill="#c9efcc")
        d.text((67, y + 104), title, font=font(48, "s"), fill="white")
        for k in range(3):
            d.rounded_rectangle((72 + k * 181, y + 207, 171 + k * 181, y + 251), 17, fill=colors[k])
    save("repo", page)
    save("diagram", page.crop((35, 790, 645, 1093)))
    save("landing", page.crop((0, 0, 680, 550)))
    rate = 48000
    t = np.arange(3 * rate) / rate
    tap = np.sin(2 * np.pi * 1150 * t[:3800]) * np.exp(-t[:3800] * 140) * 0.23
    ffmpeg.write_wav(folder / "key.wav", tap, rate)
    paths["key"] = "assets/key.wav"
    v = (0.17 * np.sin(2 * np.pi * 180 * t) * (0.15 + 0.85 * np.sin(2 * np.pi * 1.33 * t) ** 2)).astype(np.float32)
    ffmpeg.write_wav(folder / "voice.wav", v, rate)
    paths["voice"] = "assets/voice.wav"
    data = json.loads(Path(__file__).with_name("collection.json").read_text())
    scenes = []
    pos = 0
    for entry in data["entries"]:
        sc = entry["scene"].copy()
        sc["start"], sc["end"] = pos, pos + sc["end"]
        pos = sc["end"]
        scenes.append(sc)
    write_json(root / "studio.json", dict(schema=1, fps=data["fps"], house="Studio", product="Fieldwork", mark="house", assets=paths, scenes=scenes))
    return root / "studio.json"
