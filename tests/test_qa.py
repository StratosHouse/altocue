"""The QA stage: pure rules first, then each gate's edges, then the command."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from canaries import CLAIMING, CONTROLS, HOST_PROMPT, LABEL_ITEM, SCRIPT, Case, Recipe, build, fakes, fired, freeze, planted
from conftest import make_clip
from PIL import Image

from altocue import claims, qa
from altocue.cli import main
from altocue.errors import AltocueError
from altocue.generate import import_asset
from altocue.models import Clip, GateResult, Word
from altocue.project import Project
from altocue.qa import gates, ocr, rules
from altocue.review import rubric

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


# Honesty gates. Not legal advice: these read what is said, not what is lawful.


def synthetic(tmp_path: Path, **recipe):
    return build(Case("host", "", Recipe(performer=HOST_PROMPT, **recipe)), tmp_path / "host")


def fresh(tmp_path: Path):
    """The shared clean project must stay clean, so anything that changes a project gets its own."""
    return build(CLEAN, tmp_path / "fresh")


@pytest.mark.parametrize(
    "line",
    [
        "I bought this last week",
        "I tried it for a month",
        "this changed my life",
        "my results speak for themselves",
        "Verified buyer",
        "I've been using it every morning",
        "it worked for me",
        "my skin has never looked better",
        "after two weeks of using it",
        "I can't live without it",
        "see my before and after photos",
        "real customer review",
    ],
)
def test_experience_claims_are_found(line):
    assert claims.experience(line)


@pytest.mark.parametrize(
    "line",
    [
        "This bottle holds half a litre.",
        "Twist the cap and pour.",
        "You can try it on at home.",
        "It ships in two days and returns are free within 30 days.",
        "The label lists every ingredient.",
        "Wash your hands before and after use.",
    ],
)
def test_plain_description_is_not_a_claim(line):
    assert not claims.experience(line)


def test_curly_quotes_and_capitals_do_not_hide_a_claim():
    assert claims.experience("I\u2019VE BEEN USING IT")


def test_the_honesty_gates_have_nothing_to_do_without_a_synthetic_person(clean):
    context = gates.Context.open(clean.project, clean.file)
    with planted(clean):
        assert gates.gate_claims(context).value == gates.NOT_APPLICABLE
        assert gates.gate_disclosure(context).value == gates.NOT_APPLICABLE


def test_a_failed_claim_is_a_failed_gate_so_review_says_fix(tmp_path):
    built = synthetic(tmp_path, heard=CLAIMING, expected=CLAIMING)
    with planted(built):
        results = qa.run_gates(built.project, built.file)
    gate = by_id(results, "claims")
    assert gate in qa.failed(results)
    assert "changed my life" in gate.detail
    assert rubric.gates_only(results)["verdict"] == "FIX"


def test_a_label_does_not_cure_a_claim(tmp_path):
    built = synthetic(tmp_path, heard=CLAIMING, expected=CLAIMING, items=[LABEL_ITEM])
    with planted(built):
        context = gates.Context.open(built.project, built.file)
        assert gates.gate_disclosure(context).passed
        assert not gates.gate_claims(context).passed


def test_claims_with_no_transcript_are_skipped_not_passed(tmp_path):
    built = synthetic(tmp_path)
    timeline = json.loads(built.project.timeline_path.read_text())
    built.project.timeline_path.write_text(json.dumps({**timeline, "captions": []}))
    context = gates.Context.open(built.project, built.file)
    context._heard = (None, "No transcriber here.")
    context.edl = None
    gate = gates.gate_claims(context)
    assert gate.skipped and not gate.passed


def test_a_real_speaker_over_a_silent_generated_insert_is_read_only_inside_the_insert(tmp_path):
    built = build(Case("real", "", Recipe(heard=CLAIMING, expected=CLAIMING)), tmp_path / "real")
    asset = import_asset(built.project, file=built.file, shot="cut", prompt=HOST_PROMPT, model="m", cost=0, synthetic=["person"], out=lambda _: None)
    timeline = json.loads(built.project.timeline_path.read_text())
    timeline["inserts"] = [{"file": str(asset.relative_to(built.project.root)), "start": 5.5, "end": 6.0}]
    built.project.timeline_path.write_text(json.dumps(timeline))
    built.project.save_clips([Clip(id="take", path=str(tmp_path / "elsewhere.mp4"), duration=6.0, fps=30, width=1080, height=1920)])
    with planted(built):
        assert gates.gate_claims(gates.Context.open(built.project, built.file)).passed


def test_a_flagged_take_that_is_not_in_the_cut_is_not_in_the_film(tmp_path):
    clean = fresh(tmp_path)
    take = make_clip(tmp_path / "take.mp4", seconds=1.0, audio=False)
    import_asset(clean.project, file=take, shot="unused", prompt=HOST_PROMPT, model="m", cost=0, synthetic=["person"], out=lambda _: None)
    with planted(clean):
        gate = gates.gate_disclosure(gates.Context.open(clean.project, clean.file))
    assert gate.value == gates.NOT_APPLICABLE
    assert "unused" in gate.detail


def test_with_no_edit_on_disk_a_flagged_take_counts_as_in_the_film(tmp_path):
    clean = fresh(tmp_path)
    take = make_clip(tmp_path / "take.mp4", seconds=1.0, audio=False)
    import_asset(clean.project, file=take, shot="host", prompt=HOST_PROMPT, model="m", cost=0, synthetic=["person"], out=lambda _: None)
    clean.project.edl_path.unlink()
    clean.project.timeline_path.unlink()
    with planted(clean):
        gate = gates.gate_disclosure(gates.Context.open(clean.project, clean.file))
    assert gate.skipped or not gate.passed
    assert gate.value != gates.NOT_APPLICABLE


def test_a_flag_in_the_shot_list_counts(tmp_path):
    clean = fresh(tmp_path)
    asset = import_asset(clean.project, file=clean.file, shot="host", prompt=HOST_PROMPT, model="m", cost=0, out=lambda _: None)
    clean.project.save_clips([Clip(id="take", path=str(asset), duration=6.0, fps=30, width=1080, height=1920)])
    shot = {"id": "host", "purpose": "p", "kind": "video", "tier": "draft", "prompt": HOST_PROMPT, "aspect": "9:16", "duration": 5, "placement": 0, "check": "none"}
    (clean.project.root / "shots.json").write_text(json.dumps({"shots": [{**shot, "synthetic": ["person"]}]}))
    with planted(clean):
        assert not gates.gate_disclosure(gates.Context.open(clean.project, clean.file)).passed


def test_a_script_on_the_shot_list_is_read(tmp_path):
    clean = fresh(tmp_path)
    asset = import_asset(clean.project, file=clean.file, shot="host", prompt=HOST_PROMPT, model="m", cost=0, synthetic=["person"], out=lambda _: None)
    clean.project.save_clips([Clip(id="take", path=str(asset), duration=6.0, fps=30, width=1080, height=1920)])
    shot = {"id": "host", "purpose": "p", "kind": "video", "tier": "draft", "prompt": HOST_PROMPT, "aspect": "9:16", "duration": 5, "placement": 0, "check": "none"}
    (clean.project.root / "shots.json").write_text(json.dumps({"shots": [{**shot, "script": "I tried it myself."}]}))
    with planted(clean):
        gate = gates.gate_claims(gates.Context.open(clean.project, clean.file))
    assert not gate.passed and "tried" in gate.detail


def test_disclosure_says_it_is_not_legal_advice(tmp_path):
    built = synthetic(tmp_path)
    with planted(built):
        context = gates.Context.open(built.project, built.file)
        assert "not legal advice" in gates.gate_disclosure(context).detail.lower()


def test_a_project_can_name_its_own_label(tmp_path):
    item = {"type": "chip", "text": "Played by a digital double", "start": 0.0, "end": 6.0}
    built = synthetic(tmp_path, items=[item])
    config = built.project.root / "altocue.toml"
    config.write_text(config.read_text().replace('label = "AI-generated actor"', 'label = "digital double"'))
    with planted(built):
        assert gates.gate_disclosure(gates.Context.open(Project.open(built.project.root), built.file)).passed


def test_an_exemption_is_recorded_in_the_detail(tmp_path):
    built = build(Case("exempt", "", Recipe(performer=HOST_PROMPT, exempt="Audio-only ad.")), tmp_path / "exempt")
    with planted(built):
        gate = gates.gate_disclosure(gates.Context.open(built.project, built.file))
    assert gate.passed and gate.value == "exempt" and "Audio-only ad." in gate.detail


# UGC checks


def ugc(tmp_path: Path, **recipe) -> dict[str, GateResult]:
    built = build(Case("ugc", "", Recipe(**recipe)), tmp_path / "ugc")
    with planted(built):
        results = gates.run_ugc(gates.Context.open(built.project, built.file))
    return {gate.id: gate for gate in results}


CALM = SCRIPT[:14]


def test_speech_over_the_ceiling_fails(tmp_path):
    found = ugc(tmp_path)  # the whole script in 6 s
    assert not found["speech_rate"].passed and found["speech_rate"].value > 5
    assert found["speech_rate"].threshold == gates.SPEECH_CEILING


def test_speech_under_the_ceiling_passes(tmp_path):
    assert ugc(tmp_path, heard=CALM, expected=CALM)["speech_rate"].passed


def test_a_cold_open_passes(tmp_path):
    assert ugc(tmp_path, heard=CALM, expected=CALM)["cold_open"].passed


def test_a_slow_first_word_fails_the_cold_open(tmp_path, monkeypatch):
    built = build(Case("late", "", Recipe(heard=CALM, expected=CALM)), tmp_path / "late")
    built.heard[0] = Word(text=CALM[0], start=0.9, end=1.1)
    with planted(built):
        gate = gates.gate_cold_open(gates.Context.open(built.project, built.file))
    assert not gate.passed and "0.90" in gate.detail


def test_a_static_start_fails_the_cold_open(tmp_path):
    gate = ugc(tmp_path, heard=CALM, expected=CALM, vf=freeze(0, 20))["cold_open"]
    assert not gate.passed and "static" in gate.detail


def test_baked_text_fails(tmp_path):
    gate = ugc(tmp_path, heard=CALM, expected=CALM, burn_text=[("LIMITED TIME OFFER", 0.5, 0.0, 6.0)])["baked_text"]
    assert not gate.passed and not gate.skipped, gate.detail
    assert "limited" in gate.detail.lower()


def test_planned_text_is_not_baked_text(tmp_path):
    item = {"type": "chip", "text": "Follow this plan", "start": 0.0, "end": 6.0}
    gate = ugc(tmp_path, heard=CALM, expected=CALM, items=[item], burn_text=[("Follow this plan", 0.2, 0.0, 6.0)])["baked_text"]
    assert gate.passed, gate.detail


def test_the_real_product_label_can_be_allowed(tmp_path):
    built = build(Case("label", "", Recipe(heard=CALM, expected=CALM, burn_text=[("OAK AND ASH", 0.5, 0.0, 6.0)])), tmp_path / "label")
    config = built.project.root / "altocue.toml"
    config.write_text(config.read_text().replace("label_text = []", 'label_text = ["oak and ash"]'))
    with planted(built):
        gate = gates.gate_baked_text(gates.Context.open(Project.open(built.project.root), built.file))
    assert gate.passed, gate.detail


def test_no_text_reader_skips_baked_text(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr, "reader", lambda: (None, ocr.NO_READER))
    gate = ugc(tmp_path, heard=CALM, expected=CALM)["baked_text"]
    assert gate.skipped and not gate.passed


def test_lip_closure_is_never_passed_and_leaves_a_sheet_for_a_person(tmp_path):
    words = ["maybe", "people", "buy", "more", "paper", "than", "before"]
    built = build(Case("lips", "", Recipe(heard=words, expected=words)), tmp_path / "lips")
    with planted(built):
        gate = gates.gate_lip_closure(gates.Context.open(built.project, built.file))
    assert gate.skipped and not gate.passed
    assert "person" in gate.detail
    sheet = built.project.review_dir / "lip-sheet.jpg"
    assert sheet.exists()
    with Image.open(sheet) as image:
        assert image.size[0] > 0


def test_lip_closure_with_no_such_words_has_nothing_to_look_at(tmp_path):
    words = ["the", "tiny", "dot", "sat", "still"]
    assert ugc(tmp_path, heard=words, expected=words)["lip_closure"].value == gates.NOT_APPLICABLE


def test_qa_check_ugc_runs_only_those_checks_and_saves_them(tmp_path, capsys):
    built = build(Case("ugc", "", Recipe()), tmp_path / "ugc")
    with planted(built):
        code = main(["qa", "--project", str(built.project.root), "--check", "ugc"])
    out = capsys.readouterr().out
    assert code == 1  # 5 words a second
    assert "speech_rate" in out and "lip_closure" in out and "black_frames" not in out
    saved = json.loads((built.project.review_dir / "qa-ugc.json").read_text())
    assert {g["id"] for g in saved["gates"]} == {"speech_rate", "cold_open", "baked_text", "lip_closure"}
