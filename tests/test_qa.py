"""The QA stage: pure rules first, then each gate's edges, then the command."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from canaries import CONTROLS, SCRIPT, Case, Recipe, build, fakes, fired, planted

from altocue import qa
from altocue.cli import main
from altocue.errors import AltocueError
from altocue.models import GateResult
from altocue.qa import gates, ocr, rules

CLEAN = CONTROLS[0]


@pytest.fixture(scope="module")
def clean(tmp_path_factory):
    return build(CLEAN, tmp_path_factory.mktemp("clean"))


def by_id(results: list[GateResult], gate: str) -> GateResult:
    return next(g for g in results if g.id == gate)


# Rules


def test_banned_terms_match_exact_fuzzy_and_phrases():
    assert rules.match_terms("Results GUARANTEED today", ["guaranteed"]) == ["guaranteed"]
    assert rules.match_terms("guaranted", ["guaranteed"]) == ["guaranteed"]
    assert rules.match_terms("a cure", ["cures"]) == []
    assert rules.match_terms("lose weight fast now", ["weight fast"]) == ["weight fast"]
    assert rules.match_terms("insecure", ["cure"]) == []


def test_rules_file_rejects_a_typo_rather_than_ignoring_it(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps({"bannned": ["x"]}))
    with pytest.raises(AltocueError) as error:
        rules.load(path)
    assert "bannned" in str(error.value) and error.value.fix


def test_rules_file_is_optional(tmp_path):
    assert rules.load(tmp_path / "rules.json") is None


def test_every_on_screen_string_is_collected():
    timeline = {
        "captions": [{"start": 0, "end": 1, "words": [{"text": "hello", "start": 0, "end": 1}, {"text": "there", "start": 0, "end": 1}]}],
        "items": [
            {"type": "hook", "start": 0, "end": 2, "text": "Hook *line*", "eyebrow": "Eyebrow"},
            {"type": "stat", "start": 1, "end": 2, "value": 90, "label": "Percent"},
            {"type": "steps", "start": 2, "end": 3, "title": "Steps", "items": [{"text": "First", "at": 2.1}]},
            {"type": "quote", "start": 2, "end": 3, "text": "Said", "by": "Someone"},
            {"type": "endcard", "start": 3, "end": 4, "headline": "Bye", "cta": "Visit"},
        ],
    }
    texts = {s["text"] for s in rules.on_screen(timeline)}
    assert {"hello there", "Hook line", "Eyebrow", "90", "Percent", "Steps", "First", "Said", "Someone", "Bye", "Visit"} <= texts


def test_banned_spelling_and_required_findings():
    ruleset = rules.Rules(
        banned=["guaranteed"],
        spellings=["Altocue"],
        required=[{"id": "terms", "when_spoken": "free", "on_screen": "terms apply"}],
    )
    strings = [
        {"layer": "chip", "text": "Guaranteed wins", "start": 0, "end": 1},
        {"layer": "chip", "text": "altoCue rocks", "start": 0, "end": 1},
        {"layer": "chip", "text": "Altocu rocks", "start": 0, "end": 1},
        {"layer": "chip", "text": "Altocue is right", "start": 0, "end": 1},
    ]
    spoken = [{"text": "free", "start": 5.0, "end": 5.3}]
    found = rules.check(strings, ruleset, spoken)
    assert sorted(f["kind"] for f in found) == ["banned", "required", "spelling", "spelling"]


def test_required_line_must_be_up_while_the_word_is_said():
    ruleset = rules.Rules(required=[{"id": "terms", "when_spoken": "free", "on_screen": ["terms", "apply"]}])
    spoken = [{"text": "Free,", "start": 5.0, "end": 5.3}]
    assert rules.check([{"layer": "c", "text": "Terms apply", "start": 4.0, "end": 6.0}], ruleset, spoken) == []
    assert rules.check([{"layer": "c", "text": "Terms apply", "start": 0.0, "end": 1.0}], ruleset, spoken)
    assert rules.check([], ruleset, [{"text": "paid", "start": 1.0, "end": 1.2}]) == []


# Words


def test_match_rate_normalises_and_scores():
    assert gates.match_rate(["Hello,", "world!"], ["hello", "WORLD"]) == 1.0
    assert gates.match_rate(["a", "b", "c", "d"], ["a", "b", "c", "x"]) == 0.75
    assert gates.match_rate(["a", "b"], ["c", "d", "e", "f", "g"]) == 0.0
    assert gates.match_rate(["well-known"], ["well", "known"]) == 1.0


def test_doubled_phrases_found_and_allowed():
    tokens = "the plan is the plan is good".split()
    assert gates.doubled_phrases(tokens) == ["the plan is"]
    assert gates.doubled_phrases("no no no go".split()) == []
    assert gates.doubled_phrases(tokens, allow=["the plan is"]) == []
    assert gates.doubled_phrases("one two three four".split()) == []


def test_missing_transcriber_skips_the_word_gate_never_passes(clean):
    with planted(clean):
        sys.modules["altocue.transcribe"] = None  # type: ignore[assignment]
        results = gates.word_gates(gates.Context.open(clean.project, clean.file))
    words = by_id(results, "words")
    assert words.skipped and not words.passed and "transcri" in words.detail.lower()


def test_transcriber_failure_skips_with_its_reason(clean, monkeypatch):
    def boom(path, **_):
        raise AltocueError("No engine installed.", fix="altocue setup")

    with planted(clean):
        monkeypatch.setattr(sys.modules["altocue.transcribe"], "transcribe_file", boom)
        results = gates.word_gates(gates.Context.open(clean.project, clean.file))
    assert by_id(results, "words").skipped and "No engine installed" in by_id(results, "words").detail


def test_transcriber_without_any_entry_point_skips(clean, monkeypatch):
    monkeypatch.setitem(sys.modules, "altocue.transcribe", SimpleNamespace())
    words, why = gates.transcribe(clean.file)
    assert words is None and "rendered file" in why


def test_no_edl_means_edit_gates_skip(clean, tmp_path):
    from altocue.project import Project

    bare = Project.create(tmp_path / "bare")
    with fakes([], [], []):
        results = qa.run_gates(bare, clean.file)
    assert by_id(results, "duration").skipped and not by_id(results, "duration").passed
    assert by_id(results, "cut_sound").skipped
    assert by_id(results, "loudness").passed and not by_id(results, "loudness").skipped


# Format


def test_format_names_what_is_wrong(tmp_path):
    built = build(Case("t", "format", Recipe(tags=False, pix_fmt="yuv444p", burn_captions=False)), tmp_path)
    with planted(built):
        gate = gates.gate_format(gates.Context.open(built.project, built.file))
    assert not gate.passed and "bt709" in gate.detail and "yuv444p" in gate.detail


def test_draft_stage_does_not_gate_mastering(tmp_path):
    built = build(Case("t", "", Recipe(gain_db=-6.0, burn_captions=False)), tmp_path)
    with planted(built):
        results = qa.run_gates(built.project, built.file, stage="draft")
    assert by_id(results, "loudness").skipped and "draft" in by_id(results, "loudness").detail.lower()


def test_silent_file_fails_loudness_rather_than_crashing(tmp_path):
    built = build(Case("t", "", Recipe(gaps=[(0, 6)], burn_captions=False)), tmp_path)
    with planted(built):
        gate = gates.gate_loudness(gates.Context.open(built.project, built.file))
    assert not by_id(gate, "loudness").passed and not by_id(gate, "loudness").skipped


def test_file_without_audio_is_a_failure_when_clips_had_sound(clean, tmp_path):
    mute = tmp_path / "mute.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(clean.file), "-an", "-c:v", "copy", str(mute)], check=True)
    with planted(clean):
        gate = gates.gate_loudness(gates.Context.open(clean.project, mute))
    assert not by_id(gate, "loudness").passed and not by_id(gate, "loudness").skipped


# OCR


def test_ocr_script_drops_custom_words():
    assert ".customWords" not in (Path(ocr.__file__).parent / "ocr.swift").read_text()


def test_no_reader_skips_with_a_stated_reason(clean, monkeypatch):
    monkeypatch.setattr(ocr, "reader", lambda: (None, "No text reader on this machine."))
    context = gates.Context.open(clean.project, clean.file)
    context.rules = rules.Rules(banned=["guaranteed"])
    with planted(clean):
        gate = gates.gate_rules_ocr(context)
        zone = gates.gate_safe_zone(context)
    assert gate.skipped and not gate.passed and "No text reader" in gate.detail
    assert zone.skipped and "No text reader" in zone.detail


def test_no_rules_means_nothing_to_check_not_a_pass(clean):
    gate = gates.gate_rules_ocr(gates.Context.open(clean.project, clean.file))
    assert gate.skipped and not gate.passed and gate.value == gates.NOT_APPLICABLE


def test_one_reader_one_sample_goes_to_a_human():
    grade = ocr.confidence([{"time": 1.0, "reader": "vision"}])
    assert grade == "review"
    assert ocr.confidence([{"time": 1.0, "reader": "vision"}, {"time": 1.25, "reader": "vision"}]) == "high"
    assert ocr.confidence([{"time": 1.0, "reader": "vision"}, {"time": 1.0, "reader": "tesseract"}]) == "high"


def test_ranges_merge_across_small_gaps():
    assert ocr.merge_ranges([1.0, 1.25, 1.5, 4.0], 0.25) == [[1.0, 1.75], [4.0, 4.25]]


@pytest.mark.skipif(ocr.reader()[0] is None, reason="no text reader on this machine")
def test_reader_returns_text_and_a_top_left_box(tmp_path):
    from canaries import text_plate
    from PIL import Image

    image = tmp_path / "frame.png"
    base = Image.new("RGBA", (1080, 1920), (127, 140, 153, 255))
    base.alpha_composite(text_plate("HELLO WORLD", 0.1))
    base.convert("RGB").save(image)
    found = ocr.read_images([image])
    hit = next(r for r in found if "HELLO" in r["text"].upper())
    assert 0.05 < hit["box"][1] < 0.15, hit


def test_tesseract_runs_on_one_thread_because_we_already_run_one_per_core(tmp_path, monkeypatch):
    seen = {}

    def fake(command, **kwargs):
        seen.update(kwargs["env"])
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(ocr.subprocess, "run", fake)
    ocr._tesseract_lines(tmp_path / "frame.png", tmp_path / "frame.png", (100, 100))
    assert seen["OMP_THREAD_LIMIT"] == "1"


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract is not installed")
def test_tesseract_reads_text_and_a_top_left_box_where_there_is_no_vision(tmp_path, monkeypatch):
    from canaries import text_plate
    from PIL import Image

    monkeypatch.setattr(ocr, "vision_binary", lambda: None)
    assert ocr.reader()[0] == "tesseract"
    image = tmp_path / "frame.png"
    base = Image.new("RGBA", (1080, 1920), (127, 140, 153, 255))
    base.alpha_composite(text_plate("HELLO WORLD", 0.1))
    base.convert("RGB").save(image)
    hit = next(r for r in ocr.read_images([image]) if "HELLO" in r["text"].upper())
    assert hit["reader"] == "tesseract" and 0.05 < hit["box"][1] < 0.15, hit


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract is not installed")
def test_tesseract_reads_a_light_caption_on_mid_grey(tmp_path, monkeypatch):
    from canaries import font
    from PIL import Image, ImageDraw

    monkeypatch.setattr(ocr, "vision_binary", lambda: None)
    image = tmp_path / "frame.png"
    base = Image.new("RGB", (1080, 1920), (127, 140, 153))
    ImageDraw.Draw(base).text((300, 1200), "Most people think", font=font(70), fill=(243, 247, 250))
    base.save(image)
    found = ocr.read_images([image])
    assert any("people" in r["text"] for r in found), found
    assert len([r for r in found if "people" in r["text"]]) == 1, "two reads of one line must merge"


# Faces


def test_face_finder_finds_the_drawn_face(tmp_path):
    from canaries import draw_face
    from PIL import Image

    if qa.faces.model_path() is None:
        pytest.skip("YuNet model not shipped yet")
    base = Image.new("RGBA", (1080, 1920), (127, 140, 153, 255))
    draw_face(base)
    image = tmp_path / "face.png"
    base.convert("RGB").save(image)
    boxes = qa.faces.find(image)
    assert boxes and 0.2 < boxes[0][1] < 0.4


# Output


def test_results_are_saved_and_printed(clean, capsys):
    with planted(clean):
        results = qa.run_gates(clean.project, clean.file)
    saved = json.loads((clean.project.review_dir / "qa.json").read_text())
    assert [g["id"] for g in saved["gates"]] == [g.id for g in results]
    assert saved["stage"] == "final" and len(saved["sha256"]) == 64
    qa.print_table(results)
    out = capsys.readouterr().out
    assert "PASS" in out and "n/a" in out and "loudness" in out


def test_skipped_prints_skip_never_pass(capsys):
    qa.print_table([GateResult(id="words", passed=False, skipped=True, detail="no transcriber")])
    out = capsys.readouterr().out
    assert "SKIP" in out and "PASS" not in out and "1 skipped" in out


def test_a_label_quoting_speech_is_not_a_planned_hold():
    assert gates.Shot(0, 1, "hold").marked(gates.HOLD_LABELS)
    assert gates.Shot(0, 1, " B-Roll ").marked(gates.SILENT_LABELS)
    assert not gates.Shot(0, 1, "still not sure about this").marked(gates.HOLD_LABELS)
    assert not gates.Shot(0, 1, "a silent room").marked(gates.SILENT_LABELS)


def test_gate_details_never_quote_edit_labels(tmp_path):
    built = build(Case("t", "cut_sound", Recipe(gaps=[(2.0, 2.7)], labels=["", "SECRETLABEL", ""], burn_captions=False)), tmp_path)
    with planted(built):
        results = qa.run_gates(built.project, built.file)
    assert "SECRETLABEL" not in json.dumps([g.to_dict() for g in results])


def test_command_exits_one_on_failure_and_zero_when_clean(tmp_path, capsys):
    built = build(Case("t", "", Recipe(gain_db=-6.0, burn_captions=False)), tmp_path / "bad")
    with planted(built):
        assert main(["qa", "--project", str(built.project.root), "--file", str(built.file)]) == 1
    assert "FAIL" in capsys.readouterr().out
    good = build(Case("t", "", Recipe(burn_captions=False)), tmp_path / "good")
    with planted(good):
        assert main(["qa", "--project", str(good.project.root)]) == 0


def test_command_reports_a_missing_file(tmp_path, clean, capsys):
    assert main(["qa", "--project", str(clean.project.root), "--file", str(tmp_path / "nope.mp4")]) == 1
    assert "nope.mp4" in capsys.readouterr().err


def test_picture_shorter_than_the_sound_is_reported_not_a_crash(clean, tmp_path):
    picture = tmp_path / "picture.mp4"
    short = tmp_path / "short.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-t", "3", "-i", str(clean.file), "-map", "0:v", "-c", "copy", str(picture)], check=True)
    joined = ["-i", str(picture), "-i", str(clean.file), "-map", "0:v", "-map", "1:a", "-c", "copy", str(short)]
    subprocess.run(["ffmpeg", "-v", "error", "-y", *joined], check=True)
    with planted(clean):
        results = qa.run_gates(clean.project, short)
    assert not by_id(results, "duration").passed and not by_id(results, "duration").skipped
    assert "safe_zone" in {g.id for g in results}


def test_a_gate_that_blows_up_is_skipped_not_passed_and_the_rest_still_run(clean, monkeypatch):
    def boom(context):
        raise AltocueError("ffmpeg failed: the file is damaged.")

    monkeypatch.setattr(gates, "gate_black", boom)
    with planted(clean):
        results = qa.run_gates(clean.project, clean.file)
    broken = by_id(results, "black_frames")
    assert broken.skipped and not broken.passed and "damaged" in broken.detail
    assert by_id(results, "format").passed
    assert "black_frames" in {g.id for g in qa.not_run(results)}


def test_grab_steps_back_from_the_end_of_the_picture(clean, tmp_path):
    assert gates.grab(clean.file, 5.99, tmp_path / "end.jpg") == tmp_path / "end.jpg"
    assert gates.grab(clean.file, 999.0, tmp_path / "never.jpg") is None


def test_qa_never_changes_the_file(clean):
    before = clean.file.read_bytes()
    with planted(clean):
        qa.run_gates(clean.project, clean.file)
    assert clean.file.read_bytes() == before


def test_no_drawtext_anywhere():
    root = Path(qa.__file__).parent
    assert not any("drawtext" in p.read_text() for p in root.rglob("*.py"))
    assert fired([GateResult(id="a", passed=False)]) == {"a"} and SCRIPT


def test_word_checks_work_in_any_alphabet():
    from altocue.qa.gates import match_rate

    said, heard = ["\u043f\u0440\u0438\u0432\u0435\u0442", "\u043c\u0438\u0440"], ["\u0441\u043e\u0432\u0441\u0435\u043c", "\u0434\u0440\u0443\u0433\u043e\u0435"]
    assert match_rate(said, heard) == 0.0
    assert match_rate(["\u0633\u0644\u0627\u0645"], ["\u0633\u0644\u0627\u0645"]) == 1.0
