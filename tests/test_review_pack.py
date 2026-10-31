"""The review pack: what a reviewer gets, and everything it must never get."""

from __future__ import annotations

import json

import numpy as np
import pytest
from canaries import Case, Recipe, build, planted
from PIL import Image

from altocue import qa
from altocue.models import Removed
from altocue.project import write_json
from altocue.review import pack, rubric

SECRET = "SECRETREASONXYZ"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    recipe = Recipe(
        vf="drawbox=color=black:t=fill:enable='eq(n,59)',drawbox=color=white:t=fill:enable='eq(n,60)'",
        labels=[f"hook {SECRET}", "", "payoff"],
        burn_captions=False,
    )
    item = build(Case("pack", "", recipe), tmp_path_factory.mktemp("pack"))
    project = item.project
    edl = project.edl()
    edl.removed.append(Removed(clip="take", start=9, end=10, kind="retake", evidence=f"{SECRET} better take later"))
    project.save_edl(edl)
    (project.root / "brief.md").write_text("# Brief\n\nExplain the plan to busy founders.\n")
    (project.root / "shots.json").write_text(json.dumps({"why": SECRET}))
    (project.root / "motion.json").write_text(json.dumps({"why": SECRET}))
    write_json(project.review_dir / "rounds.json", {"rounds": [{"note": SECRET}]})
    (project.review_dir / "REPORT.md").write_text(SECRET)
    (project.out / "draft.mp4").write_bytes(b"x")
    with planted(item):
        gates = qa.run_gates(project, item.file)
        pack.build(project, item.file, gates)
    return item


def folder(item):
    return item.project.review_dir / "pack"


def test_contact_sheets_one_frame_per_two_seconds(built):
    sheets = sorted((folder(built) / "sheets").glob("*.jpg"))
    assert len(sheets) == 1
    manifest = json.loads((folder(built) / "manifest.json").read_text())
    assert manifest["sheet_times"] == [0.0, 2.0, 4.0]


def test_contact_sheet_time_is_really_at_two_seconds(tmp_path):
    item = build(Case("t", "", Recipe(vf="drawbox=color=black:t=fill:enable='eq(n,60)'", burn_captions=False, face=False)), tmp_path)
    frames = pack.sample_frames(item.file, 2.0, item.project.work, fps=30.0)
    assert len(frames) == 3
    middle = np.asarray(Image.open(frames[1]).convert("L"))
    assert middle.mean() < 60


def test_timecodes_are_drawn_on_the_sheet(built):
    sheet = next((folder(built) / "sheets").glob("*.jpg"))
    assert qa.ocr.reader()[0] is None or any("00:0" in r["text"] for r in qa.ocr.read_images([sheet]))


def test_cut_frames_are_the_last_and_first_frame_around_each_cut(built):
    pairs = sorted((folder(built) / "cuts").glob("*.jpg"))
    assert len(pairs) == 2
    grey = np.asarray(Image.open(pairs[0]).convert("L"))
    width = grey.shape[1]
    assert grey[:, : width // 2].mean() < 40, "left half should be the last frame, planted black"
    assert grey[:, width // 2 :].mean() > 215, "right half should be the first frame, planted white"


def test_hook_frames_four_per_second_for_three_seconds(built):
    frames = sorted((folder(built) / "hook").glob("*.jpg"))
    assert len(frames) == 12
    assert Image.open(frames[0]).size[0] == 1080


def test_transcript_comes_from_the_rendered_file(built):
    data = json.loads((folder(built) / "transcript.json").read_text())
    assert data["available"] and data["source"] == "re-transcribed from the rendered file"
    assert [w["text"] for w in data["words"]] == [w.text for w in built.heard]


def test_missing_transcript_is_said_out_loud(tmp_path):
    item = build(Case("t", "", Recipe(burn_captions=False, seconds=3.0)), tmp_path)
    write_json(
        item.project.review_dir / "transcript.json",
        {"sha256": qa.sha256(item.file), "available": False, "reason": "no engine", "words": []},
    )
    with planted(item):
        info = pack.build(item.project, item.file, [])
    assert not info["transcript_available"]
    assert "no engine" in pack.prompt_text(info)


def test_numbers_carry_every_measurement(built):
    numbers = json.loads((folder(built) / "numbers.json").read_text())
    assert {"file", "loudness", "silence", "black", "frozen", "astats", "cuts", "gates"} <= set(numbers)
    assert len(numbers["file"]["sha256"]) == 64 and numbers["cuts"] == [2.0, 4.0]
    assert numbers["black"], "the planted black frame must show in the numbers"
    assert "rms_db" in numbers["astats"]


def test_numbers_hold_no_file_path(built):
    assert str(built.project.root) not in (folder(built) / "numbers.json").read_text()


def test_blind_by_construction(built):
    review = built.project.review_dir
    texts = [p for p in folder(built).rglob("*") if p.suffix in {".json", ".txt", ".md"}]
    assert texts
    pack.write_prompt(built.project, pack.load(built.project))
    for path in [*texts, review / "PACK.md"]:
        assert SECRET not in path.read_text(), path
    assert not (folder(built) / "edl.json").exists()


def test_pack_prompt_is_self_contained(built):
    pack.write_prompt(built.project, pack.load(built.project))
    text = (built.project.review_dir / "PACK.md").read_text()
    assert "Explain the plan to busy founders" in text
    assert "review/verdict.json" in text and "--ingest" in text
    for name, dimension in rubric.DIMENSIONS.items():
        assert name in text and dimension.ask in text
    assert "edl.json" in text and "Do not open" in text
    assert "sheets/" in text and "cuts/" in text and "hook/" in text
    assert qa.sha256(built.file) in text
    assert "the plan" in text, "the transcript must be inline"


def test_pack_says_how_many_cuts_there_are(built):
    info = pack.load(built.project)
    pack.write_prompt(built.project, info)
    assert "has 2 cuts" in (built.project.review_dir / "PACK.md").read_text()
    pack.write_prompt(built.project, {**info, "numbers": {**info["numbers"], "cuts": []}})
    assert "one continuous shot" in (built.project.review_dir / "PACK.md").read_text()


def test_prompt_tells_the_reviewer_it_does_not_decide(built):
    text = pack.prompt_text(pack.load(built.project))
    assert "code decides" in text.lower() and "timecode" in text.lower()


def test_rebuild_clears_stale_files(built, tmp_path):
    item = build(Case("t", "", Recipe(burn_captions=False, seconds=3.0)), tmp_path)
    stale = item.project.review_dir / "pack" / "sheets" / "old-draft.jpg"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"x")
    with planted(item):
        pack.build(item.project, item.file, [])
    assert not stale.exists()


def test_rebuild_moves_the_last_verdict_out_of_sight(tmp_path):
    item = build(Case("t", "", Recipe(burn_captions=False, seconds=3.0)), tmp_path)
    old = item.project.review_dir / "verdict.json"
    old.write_text("{}")
    with planted(item):
        pack.build(item.project, item.file, [])
    assert not old.exists() and list((item.project.review_dir / "history").glob("verdict-*.json"))


def test_no_edl_falls_back_to_a_pack_without_cuts(tmp_path):
    item = build(Case("t", "", Recipe(burn_captions=False, seconds=3.0)), tmp_path)
    item.project.edl_path.unlink()
    with planted(item):
        info = pack.build(item.project, item.file, [])
    assert info["numbers"]["cuts"] == []


def test_pack_never_uses_drawtext():
    assert "drawtext" not in open(pack.__file__).read()
