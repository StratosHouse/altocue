"""The verdict rule, the rounds, and the review command from gates to REPORT.md."""

from __future__ import annotations

import json
import os

import pytest
from canaries import Case, Recipe, build

import altocue.review as review
from altocue import qa
from altocue.cli import main
from altocue.errors import AltocueError
from altocue.models import GateResult
from altocue.qa.gates import NOT_APPLICABLE
from altocue.review import gemini, pack, rounds, rubric

GOOD = {name: {"score": 4, "why": "fine"} for name in rubric.DIMENSIONS}
PASSING = [GateResult(id="loudness", passed=True, value=-14.0), GateResult(id="words", passed=True, value=0.99)]
FAILING = [GateResult(id="loudness", passed=False, value=-20.0, threshold="-14 LUFS +/- 1", detail="-20.0 LUFS")]


def scores(**over: int | None) -> dict:
    merged = {k: dict(v) for k, v in GOOD.items()}
    for name, value in over.items():
        merged[name]["score"] = value
    return merged


def note(**over) -> dict:
    return {
        "start": "00:02",
        "end": "00:03",
        "severity": "minor",
        "dimension": "pacing",
        "observed": "drags",
        "suggested_fix": "trim",
        **over,
    }


# Rubric


def test_nine_dimensions_weigh_one_hundred():
    assert len(rubric.DIMENSIONS) == 9
    assert sum(d.weight for d in rubric.DIMENSIONS.values()) == 100
    assert rubric.DIMENSIONS["hook"].weight == 20 and rubric.DIMENSIONS["sound_as_speech"].weight == 5


def test_weighted_score():
    assert rubric.weighted(rubric.read_scores({"scores": scores()})) == 4.0
    assert rubric.weighted(rubric.read_scores({"scores": scores(hook=3)})) == 3.8


@pytest.mark.parametrize(
    ("gates", "marks", "notes", "verdict"),
    [
        (PASSING, scores(), [], "SHIP"),
        (PASSING, scores(hook=3), [], "SHIP"),
        (PASSING, scores(hook=3, clarity=3), [], "FIX"),
        (PASSING, {**scores(), "pacing": {"score": 2, "why": "slow"}}, [], "FIX"),
        (PASSING, scores(), [note(severity="blocker")], "FIX"),
        (PASSING, scores(), [note(severity="major")], "SHIP"),
        (FAILING, scores(hook=5), [], "FIX"),
        (PASSING + [GateResult(id="words", passed=False, skipped=True, detail="no transcriber")], scores(), [], "FIX"),
        (PASSING + [GateResult(id="captions_match", passed=False, skipped=True, value=NOT_APPLICABLE)], scores(), [], "SHIP"),
    ],
)
def test_verdict_rule(gates, marks, notes, verdict):
    result = rubric.score({"scores": marks, "notes": notes}, gates, duration=10.0)
    assert result["verdict"] == verdict, result["reasons"]


def test_exact_threshold_ships_and_just_under_does_not():
    assert rubric.decide(PASSING, rubric.read_scores({"scores": scores(hook=3)}), [])[0] == "SHIP"
    assert rubric.decide(PASSING, rubric.read_scores({"scores": scores(hook=3, ending=3)}), [])[0] == "FIX"


def test_code_decides_not_the_model():
    result = rubric.score({"scores": scores(hook=1), "notes": [], "verdict": "SHIP", "weighted": 5.0}, PASSING, duration=10.0)
    assert result["verdict"] == "FIX" and result["weighted"] < 4
    assert "ignored" in " ".join(result["warnings"]).lower()


@pytest.mark.parametrize(
    "bad",
    [
        {k: v for k, v in GOOD.items() if k != "hook"},
        scores(hook=6),
        scores(hook=0),
        scores(hook=True),
        {**scores(), "hook": {"score": "4", "why": ""}},
        {**scores(), "hook": {"score": 3.5, "why": ""}},
        scores(hook=None),
    ],
)
def test_bad_scores_are_refused_not_guessed(bad):
    with pytest.raises(AltocueError):
        rubric.read_scores({"scores": bad})


def test_only_inserts_may_be_not_applicable():
    marks = rubric.read_scores({"scores": scores(inserts=None)})
    assert marks["inserts"]["score"] is None
    assert rubric.weighted(marks) == 4.0


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("00:07", 7.0), ("01:15", 75.0), ("1:02:03", 3723.0), ("00:07.5", 7.5), (12, 12.0), (3.25, 3.25), ("12", 12.0), ("12.5s", 12.5)],
)
def test_timecodes_parse(text, seconds):
    assert rubric.parse_time(text) == seconds


@pytest.mark.parametrize("text", ["", None, "soon", "aa:bb", -1, "99:99:99:99", True, "00:75"])
def test_bad_timecodes_are_none(text):
    assert rubric.parse_time(text) is None


def test_notes_without_a_valid_timecode_are_dropped():
    notes = [
        note(),
        note(start="00:99"),
        note(start=""),
        {k: v for k, v in note().items() if k != "start"},
        note(dimension="vibes"),
        note(severity="fatal"),
        note(observed=""),
        note(start="00:05", end="00:04"),
        note(end="01:00"),
    ]
    kept, dropped = rubric.read_notes({"notes": notes}, duration=10.0)
    assert len(kept) == 1 and len(dropped) == 8
    assert all(d["why"] for d in dropped)
    assert kept[0]["start_seconds"] == 2.0


def test_a_blocker_with_no_timecode_is_dropped_but_forces_another_look():
    result = rubric.score({"scores": scores(), "notes": [note(severity="blocker", start="nope")]}, PASSING, duration=10.0)
    assert result["verdict"] == "FIX" and len(result["dropped_notes"]) == 1


def test_low_score_without_a_note_is_flagged():
    result = rubric.score({"scores": scores(pacing=3), "notes": []}, PASSING, duration=10.0)
    assert "pacing" in result["unsupported_scores"]


def test_schema_needs_all_nine_and_is_strict():
    schema = rubric.schema()
    assert set(schema["properties"]["scores"]["required"]) == set(rubric.DIMENSIONS)
    assert schema["additionalProperties"] is False
    assert "verdict" not in schema["properties"]


# Independence


@pytest.mark.parametrize(
    ("vendor", "editor", "independent"),
    [
        ("google", "anthropic", True),
        ("openai", "anthropic", True),
        ("anthropic", "anthropic", False),
        ("Anthropic", "anthropic", False),
        ("google", None, False),
        ("", "anthropic", False),
    ],
)
def test_independence(vendor, editor, independent):
    flag, why = review.independence(vendor, editor)
    assert flag is independent and why


def test_editor_vendor_comes_from_flag_then_config_then_the_host(tmp_path, monkeypatch):
    from altocue.project import Project

    monkeypatch.delenv("CLAUDECODE", raising=False)
    project = Project.create(tmp_path / "p")
    assert review.editor_vendor(project, None) is None
    monkeypatch.setenv("CLAUDECODE", "1")
    assert review.editor_vendor(project, None) == "anthropic"
    assert review.editor_vendor(project, "openai") == "openai"
    project.config["review"]["editor_vendor"] = "google"
    assert review.editor_vendor(project, None) == "google"


# Rounds


def entry(project, digest, verdict):
    return rounds.record(project, digest, verdict, reviewer="pack", weighted=3.0, open_items=2)


def test_two_fix_rounds_then_a_human(tmp_path):
    from altocue.project import Project

    project = Project.create(tmp_path / "p")
    assert not entry(project, "a", "FIX")["handed_off"]
    assert not entry(project, "b", "FIX")["handed_off"]
    log = entry(project, "c", "FIX")
    assert log["handed_off"] and len(log["rounds"]) == 3
    assert json.loads((project.review_dir / "rounds.json").read_text())["handed_off"]
    assert rounds.blocked(project, "d")
    assert not rounds.blocked(project, "c")


def test_reviewing_the_same_file_twice_is_not_a_new_round(tmp_path):
    from altocue.project import Project

    project = Project.create(tmp_path / "p")
    entry(project, "a", "FIX")
    log = entry(project, "a", "FIX")
    assert len(log["rounds"]) == 1


def test_ship_ends_the_rounds(tmp_path):
    from altocue.project import Project

    project = Project.create(tmp_path / "p")
    entry(project, "a", "FIX")
    assert not entry(project, "b", "SHIP")["handed_off"]


def test_reset_starts_again(tmp_path):
    from altocue.project import Project

    project = Project.create(tmp_path / "p")
    for name in "abc":
        entry(project, name, "FIX")
    rounds.reset(project)
    assert not rounds.blocked(project, "d")


# The command


@pytest.fixture
def clip(tmp_path):
    return build(Case("t", "", Recipe(burn_captions=False, seconds=3.0)), tmp_path)


@pytest.fixture
def stub(monkeypatch):
    """Stand in for the slow gates and the pack, so these tests read the flow alone."""
    calls = {"gates": PASSING}
    monkeypatch.setattr(review.qa, "run_gates", lambda project, file, stage="final": calls["gates"])
    monkeypatch.setattr(
        pack,
        "build",
        lambda project, file, gates: {"numbers": {"file": {"sha256": qa.sha256(file)}}, "transcript": [], "images": [], "brief": "b"},
    )
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return calls


def verdict_file(built, **over):
    data = {
        "reviewer": {"vendor": "anthropic", "model": "any"},
        "file_sha256": qa.sha256(built.file),
        "scores": scores(),
        "notes": [],
        "could_not_judge": [],
        **over,
    }
    path = built.project.review_dir / "verdict.json"
    path.write_text(json.dumps(data))
    return path


def argv(built, *more):
    return ["review", "--project", str(built.project.root), "--file", str(built.file), *more]


def test_failed_gate_is_fix_with_no_model_call(clip, stub, monkeypatch, capsys):
    stub["gates"] = FAILING
    monkeypatch.setattr(gemini, "review", lambda *a, **k: pytest.fail("no model call on a failed gate"))
    assert main(argv(clip, "--reviewer", "gemini", "--upload", "--paid-key")) == 1
    saved = json.loads((clip.project.review_dir / "report.json").read_text())
    assert saved["verdict"] == "FIX" and saved["reviewer"]["vendor"] == "none"
    text = (clip.project.review_dir / "REPORT.md").read_text()
    assert "FIX" in text and "loudness" in text and "What this review could not see or hear" in text


def test_pack_reviewer_stops_for_an_agent(clip, stub, capsys):
    assert main(argv(clip, "--reviewer", "pack")) == 0
    assert (clip.project.review_dir / "PACK.md").exists()
    assert not (clip.project.review_dir / "report.json").exists()
    assert "--ingest" in capsys.readouterr().out


def test_auto_without_a_key_uses_the_pack(clip, stub):
    assert main(argv(clip, "--reviewer", "auto")) == 0
    assert (clip.project.review_dir / "PACK.md").exists()


def test_auto_with_a_key_but_no_consent_still_uses_the_pack(clip, stub, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(gemini, "review", lambda *a, **k: pytest.fail("auto must not upload without opt-in"))
    assert main(argv(clip, "--reviewer", "auto")) == 0
    assert (clip.project.review_dir / "PACK.md").exists()


def test_auto_with_key_and_consent_uses_gemini(clip, stub, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    info = {"vendor": "google", "model": "m", "sees": "video", "sampled_fps": 4}
    monkeypatch.setattr(gemini, "review", lambda *a, **k: ({"scores": scores(), "notes": []}, info))
    assert main(argv(clip, "--reviewer", "auto", "--upload", "--paid-key", "--editor-vendor", "anthropic")) == 0
    saved = json.loads((clip.project.review_dir / "report.json").read_text())
    assert saved["reviewer"]["independent"] is True and saved["verdict"] == "SHIP"


def test_ingest_scores_and_writes_the_report(clip, stub, capsys):
    path = verdict_file(clip)
    assert main(argv(clip, "--ingest", str(path), "--editor-vendor", "anthropic")) == 0
    saved = json.loads((clip.project.review_dir / "report.json").read_text())
    assert saved["verdict"] == "SHIP"
    assert saved["reviewer"]["independent"] is False
    text = (clip.project.review_dir / "REPORT.md").read_text()
    assert "independent: false" in text.lower() and "What this review could not see or hear" in text


def test_ingest_relative_to_the_project(clip, stub):
    verdict_file(clip)
    assert main(argv(clip, "--ingest", "review/verdict.json")) == 0


def test_ingest_cannot_ship_past_a_failed_gate(clip, stub):
    stub["gates"] = FAILING
    assert main(argv(clip, "--ingest", str(verdict_file(clip)))) == 1
    assert json.loads((clip.project.review_dir / "report.json").read_text())["verdict"] == "FIX"


def test_ingest_measures_the_gates_itself_and_ignores_a_saved_result(clip, stub):
    (clip.project.review_dir / "qa.json").write_text(json.dumps({"sha256": qa.sha256(clip.file), "gates": [g.to_dict() for g in PASSING]}))
    stub["gates"] = FAILING
    assert main(argv(clip, "--ingest", str(verdict_file(clip)))) == 1


def test_a_verdict_file_cannot_claim_independence(clip, stub):
    path = verdict_file(clip, reviewer={"vendor": "openai", "model": "any"})
    assert main(argv(clip, "--ingest", str(path), "--editor-vendor", "anthropic")) == 0
    assert json.loads((clip.project.review_dir / "report.json").read_text())["reviewer"]["independent"] is False


def test_ingest_stops_once_the_fix_rounds_are_used(clip, stub, monkeypatch):
    monkeypatch.setattr(review.rounds, "blocked", lambda project, digest: True)
    assert main(argv(clip, "--ingest", str(verdict_file(clip)))) == 1
    assert not (clip.project.review_dir / "report.json").exists()


def test_ingest_refuses_a_verdict_for_another_render(clip, stub, capsys):
    assert main(argv(clip, "--ingest", str(verdict_file(clip, file_sha256="0" * 64)))) == 1
    assert "another render" in capsys.readouterr().err


def test_ingest_needs_a_vendor(clip, stub, capsys):
    assert main(argv(clip, "--ingest", str(verdict_file(clip, reviewer={})))) == 1
    assert "vendor" in capsys.readouterr().err


def test_ingest_bad_json_is_reported(clip, stub, capsys):
    path = clip.project.review_dir / "verdict.json"
    path.write_text("{nope")
    assert main(argv(clip, "--ingest", str(path))) == 1
    assert "JSON" in capsys.readouterr().err


def test_report_orders_fixes_and_gives_limits(clip, stub):
    bad = [
        note(start="00:02", severity="minor", observed="small thing"),
        note(start="00:01", severity="blocker", observed="big thing"),
        note(start="00:01", severity="major", observed="middle thing"),
    ]
    assert main(argv(clip, "--ingest", str(verdict_file(clip, notes=bad, could_not_judge=["the audio mix"])))) == 1
    text = (clip.project.review_dir / "REPORT.md").read_text()
    assert text.index("big thing") < text.index("middle thing") < text.index("small thing")
    assert "the audio mix" in text
    assert "00:01" in text
    assert list((clip.project.review_dir / "frames").glob("*.jpg"))


def test_report_shows_dropped_notes(clip, stub):
    assert main(argv(clip, "--ingest", str(verdict_file(clip, notes=[note(start="09:00", observed="imaginary fault")])))) == 0
    assert "imaginary fault" in (clip.project.review_dir / "REPORT.md").read_text()


def test_skipped_gate_is_named_in_the_limits(clip, stub):
    stub["gates"] = [GateResult(id="words", passed=False, skipped=True, detail="No transcriber installed.")]
    assert main(argv(clip, "--ingest", str(verdict_file(clip)))) == 1
    text = (clip.project.review_dir / "REPORT.md").read_text()
    limits = text[text.index("What this review could not see or hear") :]
    assert "words" in limits and "No transcriber installed." in limits


def test_handed_off_project_makes_no_model_call(clip, stub, monkeypatch, capsys):
    for name in "abc":
        rounds.record(clip.project, name, "FIX", reviewer="pack", weighted=3.0, open_items=1)
    monkeypatch.setattr(gemini, "review", lambda *a, **k: pytest.fail("the rounds are used"))
    assert main(argv(clip, "--reviewer", "gemini", "--upload", "--paid-key")) == 1
    assert "human" in capsys.readouterr().out.lower()


def test_third_fix_prints_the_hand_off(clip, stub, capsys):
    for name in "ab":
        rounds.record(clip.project, name, "FIX", reviewer="pack", weighted=3.0, open_items=1)
    assert main(argv(clip, "--ingest", str(verdict_file(clip, scores=scores(hook=1))))) == 1
    assert "human" in capsys.readouterr().out.lower()
    assert "human" in (clip.project.review_dir / "REPORT.md").read_text().lower()


def test_unknown_reviewer_in_the_config_is_refused_not_guessed(clip, stub, monkeypatch, capsys):
    text = (clip.project.root / "altocue.toml").read_text().replace('reviewer = "auto"', 'reviewer = "codx"')
    (clip.project.root / "altocue.toml").write_text(text)
    monkeypatch.setattr(review.codex, "review", lambda *a, **k: pytest.fail("frames must not leave on a typo"))
    assert main(argv(clip)) == 1
    assert "Unknown reviewer" in capsys.readouterr().err


def test_gemini_reviewer_without_consent_is_refused(clip, stub, capsys):
    assert main(argv(clip, "--reviewer", "gemini")) == 1
    assert "--upload" in capsys.readouterr().err


def test_end_to_end_with_real_gates(tmp_path, monkeypatch):
    from canaries import planted

    built = build(Case("t", "", Recipe(plate=True, seconds=6.0)), tmp_path)
    with planted(built):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        assert main(argv(built, "--reviewer", "pack")) == 0
        assert (built.project.review_dir / "pack" / "numbers.json").exists()
        assert main(argv(built, "--ingest", str(verdict_file(built)))) == 0


def test_a_blocker_in_capitals_still_counts(clip, stub):
    bad = [note(severity="Blocker")]
    assert main(argv(clip, "--ingest", str(verdict_file(clip, notes=bad)))) == 1


def test_a_final_older_than_the_cut_is_refused_without_using_a_round(clip, stub, capsys):
    final = clip.project.out / "final.mp4"
    final.write_bytes(clip.file.read_bytes())
    os.utime(final, (1, 1))
    clip.project.save_edl(clip.project.edl())
    assert main(["review", "--project", str(clip.project.root)]) == 1
    assert "older than the cut" in capsys.readouterr().err
    assert not (clip.project.review_dir / "rounds.json").exists()


def test_the_verdict_line_says_when_a_review_is_not_independent(clip, stub, capsys):
    assert main(argv(clip, "--ingest", str(verdict_file(clip)), "--editor-vendor", "anthropic")) == 0
    assert "Not an independent review" in capsys.readouterr().out


def test_a_new_pack_clears_the_old_report(clip, stub):
    (clip.project.review_dir / "REPORT.md").write_text("old")
    assert main(argv(clip, "--reviewer", "pack")) == 0
    assert not (clip.project.review_dir / "REPORT.md").exists()
