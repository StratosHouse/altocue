"""The glass kit in examples/: an optional house look, never the default. It must build on a plain Altocue run and
leave a composition that `altocue shorts check` passes for covers and the house rules."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from altocue import shorts

KIT = Path(__file__).resolve().parents[1] / "examples" / "shorts-kits" / "glass"
BASE = '<html><head><style>body{margin:0}</style></head><body><div id="root">\n<!-- reels:base:end -->\n</div></body></html>\n'


def words(text: str, start: float, step: float = 0.4) -> list[dict]:
    return [{"word": w, "start": round(start + i * step, 3), "end": round(start + i * step + 0.3, 3)} for i, w in enumerate(text.split())]


def glass_run(tmp_path: Path) -> Path:
    """A run as `shorts prepare` and Shorts Studio's measure leave it: two takes, one look-away in the second."""
    run = tmp_path / "run"
    (run / "composition").mkdir(parents=True)
    (run / "composition" / "index.html").write_text(BASE)
    shorts.copy_kit(KIT, run)
    record = {
        "take": "media/takes/take-01.mp4",
        "takes": [{"id": "take-01", "from": 0.0, "to": 4.0}, {"id": "take-02", "from": 4.0, "to": 9.0}],
        "covers": [{"start": 5.6, "end": 6.4, "reason": "eyes down"}],
        "sfx_only": True,
        "sounds": [{"file": "media/sfx/keyboard-01.wav", "name": "keyboard-01", "sha256": "x"}],
        "kit": [],
        "project": "",
    }
    (run / "altocue-shorts.json").write_text(json.dumps(record))
    timeline = {
        "fps": 30,
        "master_frames": 270,
        "master_duration": 9.0,
        "caption_words": words("one idea per card and then a full graphic over the look away so it never shows the end", 0.2, 0.5),
        "takes": [
            {"id": "take-01", "source_from": 0.0, "timeline_start": 0.0, "timeline_duration": 4.0},
            {"id": "take-02", "source_from": 0.0, "timeline_start": 4.0, "timeline_duration": 5.0},
        ],
    }
    (run / "timeline.json").write_text(json.dumps(timeline))
    (run / "build.py").write_text(
        "from glass_kit import Reel\n"
        "r = Reel('Glass test', 'KIT')\n"
        "r.require()\n"
        "r.beat('b1', 0.2, 3.8, '<div class=\"hl\">One idea per card</div>' + '<div class=\"band\">' + r.tiles([('Cut', 'scissors'), ('Check', 'check')]) + '</div>')\n"
        "r.typing(1.0, 'altocue shorts check')\n"
        "r.end_card(7.4)\n"
        "r.write()\n"
    )
    return run


def build(run: Path, lint: bool = False) -> subprocess.CompletedProcess:
    env = {**os.environ, "ALTOCUE_KIT_LINT": "1"} if lint else {k: v for k, v in os.environ.items() if k != "ALTOCUE_KIT_LINT"}
    return subprocess.run([sys.executable, "build.py"], cwd=run, env=env, capture_output=True, text=True, check=False)


def test_the_kit_copies_its_objects_and_logo_and_leaves_its_readme(tmp_path):
    run = tmp_path / "run"
    copied = shorts.copy_kit(KIT, run)
    assert "glass_kit.py" in copied and "house.json" in copied
    assert (run / "media" / "glass" / "check.webp").exists() and (run / "media" / "brand" / "stratos-logo.png").exists()
    assert not (run / "media" / "README.md").exists() and not (run / "README.md").exists()


def test_every_object_the_kit_ships_is_named_in_its_readme():
    readme = (KIT / "README.md").read_text()
    for path in (KIT / "glass").glob("*.webp"):
        assert f"`{path.stem}`" in readme


def test_the_kit_builds_and_covers_the_look_away(tmp_path):
    run = glass_run(tmp_path)
    done = build(run)
    assert done.returncode == 0, done.stdout + done.stderr
    html = (run / "composition" / "index.html").read_text()
    assert "/* glass kit */" in html and "--green:#267052" in html
    assert 'data-video="media/takes/take-02.mp4"' in html  # no cutout, so the take fills the aperture
    assert 'src="media/sfx/keyboard-01.wav"' in html
    assert "const LINT = false" in html
    record = json.loads((run / "altocue-shorts.json").read_text())
    assert record["takes"][1]["id"] == "take-02" and record["covers"]
    timeline = json.loads((run / "timeline.json").read_text())
    assert shorts.check_covers(html, timeline, record) == []


def test_a_second_build_replaces_the_kit_instead_of_stacking_it(tmp_path):
    run = glass_run(tmp_path)
    assert build(run).returncode == 0
    assert build(run, lint=True).returncode == 0
    html = (run / "composition" / "index.html").read_text()
    assert html.count("/* glass kit */") == 1 and "const LINT = true" in html


def test_a_sound_the_pack_lacks_stops_the_build(tmp_path):
    run = glass_run(tmp_path)
    (run / "build.py").write_text((run / "build.py").read_text().replace("r.typing(1.0, 'altocue shorts check')", "r.sound(1.0, 'whoosh')"))
    done = build(run)
    assert done.returncode != 0 and "whoosh" in done.stderr


def test_the_example_names_no_local_paths():
    """scripts/check_repo.py holds the whole repo to the blocklist and the dash rule; a kit must also name no builder paths."""
    for path in [KIT / "glass_kit.py", KIT / "house.json", KIT / "README.md"]:
        assert "~/" not in path.read_text(), path.name
