"""User-observable contracts for directed reels; all media are generated in the temporary project."""

import json

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw

from altocue import ffmpeg
from altocue.errors import AltocueError
from altocue.studio import frame, key_audio, layer
from altocue.studio.dialogue import conform
from altocue.studio.plan import check_speech, load


def setup_board(root):
    img = Image.new("RGBA", (90, 90))
    d = ImageDraw.Draw(img)
    d.polygon([(5, 80), (45, 10), (85, 80)], fill="#42d398")
    img.save(root / "tool.png")
    a = np.zeros(48000, np.float32)
    a[400:1000] = np.sin(np.arange(600) * 0.11) * 0.4
    a[3000:4000] = 0.35
    ffmpeg.write_wav(root / "key.wav", a, 48000)
    plan = {
        "schema": 1,
        "fps": 30,
        "mark": "mark",
        "house": "House",
        "product": "Editor",
        "assets": {"mark": "tool.png", "key": "key.wav"},
        "scenes": [
            {"recipe": "typing", "start": 0, "end": 15, "title": "Try it.", "text": "Hi.", "keys": [1, 3, 14], "sound": "key"},
            {"recipe": "brand-open", "start": 15, "end": 45, "keyword": "EDIT"},
        ],
    }
    (root / "studio.json").write_text(json.dumps(plan))
    return plan


def test_frame_and_keystroke_share_a_clock(tmp_path):
    setup_board(tmp_path)
    p = load(tmp_path, total=45, fps=30)
    cues = key_audio(p, tmp_path / "work")
    assert [x["at"] for x in cues] == [1 / 30, 3 / 30, 14 / 30]
    a = np.asarray(frame(p, 2))
    b = np.asarray(frame(p, 3))
    assert np.any(a[355:415, 111:400] != b[355:415, 111:400])
    assert a[900, 300, 3] == 0 and np.asarray(frame(p, 44))[900, 300, 3] == 255
    for cue in cues:
        tap, rate = ffmpeg.read_wav(cue["file"])
        assert np.abs(tap[:480]).max() > 0.1
        assert cue["at"] + len(tap) / rate <= 15 / 30 + 1e-9
    # The original WAV includes a distinct later sound. It must not leak into the next shot.
    assert abs(tap[-1]) < 0.001
    layer(p, tmp_path / "layer.mov", 45, (180, 320))
    cap = cv2.VideoCapture(str(tmp_path / "layer.mov"))
    assert cap.isOpened() and int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) == 45
    cap.release()


def test_safe_header_and_settled_final_are_visible(tmp_path):
    setup_board(tmp_path)
    p = load(tmp_path)
    actual = np.asarray(frame(p, 40))
    from altocue.studio.canvas import backing

    bg = np.asarray(backing("ink", True))
    diff = np.max(abs(actual[:254, :, :3].astype(float) - bg[:254]), axis=2)
    assert not np.any(diff[: int(0.167 * 1280)] > 8)
    assert np.count_nonzero(diff[215:252, 40:570] > 40) > 140
    assert np.array_equal(actual, np.asarray(frame(p, 44)))


def test_escape_or_out_of_shot_key_is_rejected(tmp_path):
    plan = setup_board(tmp_path)
    plan["scenes"][0]["keys"][-1] = 15
    (tmp_path / "studio.json").write_text(json.dumps(plan))
    with pytest.raises(AltocueError, match="inside the scene"):
        load(tmp_path)
    plan["assets"]["mark"] = "../outside.png"
    (tmp_path / "studio.json").write_text(json.dumps(plan))
    with pytest.raises(AltocueError, match="outside-project"):
        load(tmp_path)


def test_duplicate_and_missing_phrase_in_final_window():
    words = [{"text": s, "start": j * 0.2} for j, s in enumerate("Tool earlier. Comment tool tool.".split())]
    rules = [{"start": 10, "end": 50, "phrase": "tool", "count": 1}, {"start": 0, "end": 50, "phrase": "all at the same time", "count": 1}]
    assert [r["observed"] for r in check_speech(words, rules, 30)] == [2, 0]
    assert not any(r["passed"] for r in check_speech(words, rules, 30))


def test_dialogue_uses_requested_source_once(tmp_path):
    src = np.r_[np.zeros(24000), np.full(24000, 0.25)].astype("float32")
    ffmpeg.write_wav(tmp_path / "source.wav", src, 48000)
    spec = {
        "fps": 30,
        "frames": 30,
        "base": "source.wav",
        "replace": [[15, 30]],
        "pieces": [{"source": "source.wav", "in_sample": 35000, "out_sample": 40000, "at_frame": 16}],
    }
    (tmp_path / "studio-dialogue.json").write_text(json.dumps(spec))
    out = tmp_path / "new.wav"
    conform(tmp_path, out)
    pcm, rate = ffmpeg.read_wav(out)
    assert rate == 48000 and len(pcm) == 48000
    assert pcm[26080] > 0.2 and not np.any(pcm[30600:])
    before = (tmp_path / "source.wav").read_bytes()
    with pytest.raises(AltocueError, match="original source"):
        conform(tmp_path, tmp_path / "source.wav")
    assert (tmp_path / "source.wav").read_bytes() == before


def test_normal_render_composites_studio_manifest_and_exports_voice_plus_keys(tmp_path):
    from conftest import make_clip

    from altocue import render
    from altocue.ingest import ingest
    from altocue.models import Edl, Segment, Word
    from altocue.project import Project

    src = make_clip(tmp_path / "take.mp4", seconds=2, size="320x568", rate=30)
    project = Project.create(tmp_path / "project", aspect="9:16")
    (project.sources / "take.mp4").symlink_to(src)
    clip = ingest(project)[0]
    project.save_words(clip.id, [Word("Hello", 0.1, 0.35), Word("there", 0.45, 0.8)], "test")
    ffmpeg.extract_wav(src, project.audio_dir / "take.wav")
    project.save_edl(Edl(fps=30, segments=[Segment(id="first", clip=clip.id, start=0, end=1.7)]))
    setup_board(project.root)
    media = render.build(project, draft=True, renderer="basic")
    receipt = json.loads((project.work / "studio/receipt.json").read_text())
    assert len(receipt["keys"]) == 3 and receipt["frames"] >= 45
    cap = cv2.VideoCapture(str(media))
    cap.set(cv2.CAP_PROP_POS_FRAMES, 40)
    ok, decoded = cap.read()
    cap.release()
    assert ok
    # Independent native frame vs. normal final compositor after BT.709/YUV subsampling.
    expected = np.asarray(frame(load(project.root), 40).convert("RGB").resize((180, 320)))
    actual = cv2.cvtColor(cv2.resize(decoded, (180, 320), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
    assert np.mean(np.abs(expected.astype(float) - actual.astype(float))) < 7


def test_every_shipped_recipe_renders_and_its_example_is_valid(tmp_path):
    from importlib.resources import files

    from altocue.studio import cover
    from altocue.studio.plan import RECIPES

    setup_board(tmp_path)
    doc = json.loads(files("altocue.studio").joinpath("collection.json").read_text())
    assert {e["id"] for e in doc["entries"]} == set(RECIPES)
    names = "tool-a tool-b tool-c repo diagram character landing part-a part-b part-c mark".split()
    assets = {k: "tool.png" for k in names}
    assets.update(voice="key.wav", key="key.wav")
    plan = dict(schema=1, fps=doc["fps"], house="Studio", product="Project", mark="mark", assets=assets)
    for item in doc["entries"]:
        sc = item["scene"]
        assert item["prompt"] and item["id"] == sc["recipe"]
        (tmp_path / "studio.json").write_text(json.dumps({**plan, "scenes": [sc]}))
        loaded = load(tmp_path)
        first = np.asarray(frame(loaded, 0))
        later = np.asarray(frame(loaded, int(sc["end"] * 0.55)))
        assert first.shape == (1280, 720, 4)
        assert np.any(first != later), item["id"]
    cover(loaded, 0, tmp_path / "tool.png", tmp_path / "cover.jpg")
    assert Image.open(tmp_path / "cover.jpg").size == (1080, 1920)
    assert Image.open(tmp_path / "cover-square.jpg").size == (720, 720)


def test_status_invalidates_changed_or_missing_studio_inputs(tmp_path):
    import os

    from altocue.project import Project
    from altocue.status import DONE, STALE, check_render

    project = Project.create(tmp_path / "status")
    setup_board(project.root)
    project.out.mkdir(exist_ok=True)
    target = project.out / "render.mp4"
    target.write_bytes(b"existing render")
    os.utime(target, (2000000000, 2000000000))
    assert check_render(project).state == DONE
    os.utime(project.root / "tool.png", (2000000001, 2000000001))
    assert check_render(project).state == STALE
    (project.root / "tool.png").unlink()
    assert check_render(project).state == STALE
    project.work.mkdir(exist_ok=True)
    (project.work / "render.json").write_text('{"studio":true}')
    (project.root / "studio.json").unlink()
    assert check_render(project).state == STALE


def test_clean_demo_is_runnable_and_does_not_overwrite_a_project(tmp_path):
    from altocue.cli import main

    root = tmp_path / "demo"
    assert main(["studio", "demo", "-p", str(root)]) == 0
    assert main(["studio", "validate", "-p", str(root)]) == 0
    assert main(["studio", "frame", "-p", str(root), "--frame", "68", "--out", str(root / "first.png")]) == 0
    plan = load(root)
    assert len(plan["scenes"]) == 12 and Image.open(root / "first.png").size == (720, 1280)
    prior = (root / "studio.json").read_bytes()
    assert main(["studio", "demo", "-p", str(root)]) == 1
    assert (root / "studio.json").read_bytes() == prior
