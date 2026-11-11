"""Facts measure the finished film. They never score it and they never guess."""

from __future__ import annotations

import json
import re
import subprocess

import numpy as np
import pytest
from canaries import Case, Recipe, build, fakes, planted

from altocue import facts, qa
from altocue.cli import main
from altocue.models import Word
from altocue.project import write_json
from altocue.qa import gates, ocr

CHIP = {"type": "chip", "text": "Follow this plan", "start": 1.0, "end": 3.0}


@pytest.fixture(scope="module")
def film(tmp_path_factory):
    recipe = Recipe(items=[CHIP], burn_text=[("Follow this plan", 0.12, 1.0, 3.0)])
    return build(Case("facts", "", recipe), tmp_path_factory.mktemp("facts"))


@pytest.fixture(scope="module")
def measured(film):
    with planted(film):
        return facts.measure(film.project, film.file)


def value(data, name):
    return data["facts"][name]["value"]


# What is measured


def test_the_record_says_what_was_measured_who_measured_it_and_when(measured, film):
    assert measured["sha256"] == qa.sha256(film.file)
    assert measured["measured_by"].startswith("altocue ")
    assert re.match(r"\d{4}-\d{2}-\d{2}T", measured["measured_at"])
    assert all("how" in entry or "why" in entry for entry in measured["facts"].values())


def test_first_word(measured):
    assert value(measured, "first_word_s") == 0.0


def test_hook_words_end_inside_three_seconds(measured):
    """The canary script is 19 words over 6 seconds, so 10 of them end by 3 seconds."""
    assert value(measured, "hook_word_count") == 10
    assert value(measured, "hook_wps") == pytest.approx(3.33, abs=0.01)


def test_cuts_come_from_the_edit(measured):
    assert value(measured, "first_cut_s") == pytest.approx(2.0)
    assert value(measured, "cuts_per_10s") == pytest.approx(3.33, abs=0.01)


def test_longest_static_runs_from_the_last_event_to_the_end(measured):
    entry = measured["facts"]["longest_static_s"]
    assert entry["value"] == pytest.approx(2.0, abs=0.05)
    assert entry["from"] == pytest.approx(4.0, abs=0.05) and entry["to"] == pytest.approx(6.0, abs=0.05)


def test_captions_cover_the_speech(measured):
    assert value(measured, "caption_coverage") == pytest.approx(1.0, abs=0.01)


def test_first_text_is_seen_in_the_picture_and_states_its_precision(measured):
    entry = measured["facts"]["first_text_s"]
    assert entry["value"] == pytest.approx(0.0, abs=0.26)
    assert entry["confirmed"] is True and entry["precision_s"] == 0.25 and entry["planned_s"] == 0.0


def test_opening_loudness(measured):
    assert value(measured, "opening_lufs") == pytest.approx(-14, abs=2)
    assert value(measured, "opening_minus_integrated") == pytest.approx(0, abs=2)


def test_reading_time_is_reported_for_graphics_and_captions(measured):
    assert value(measured, "text_under_reading_time") == 0
    assert value(measured, "text_reading_margin_s") == pytest.approx(1.0, abs=0.01)
    assert value(measured, "caption_wps_peak") > 3


def test_safe_zones_use_more_than_one_profile(measured):
    assert 0 <= value(measured, "safe_zone_project") <= 1
    assert 0 <= value(measured, "safe_zone_meta_reels_ad") <= 1


def test_loop_is_measured_from_the_first_and_last_frames(measured):
    assert 0 <= value(measured, "loop_ssim") <= 1
    assert abs(value(measured, "loop_audio_delta_db")) < 12


def test_length_bucket_matches_the_market_bands(measured):
    assert measured["duration_bucket"] == "0-10"


# Helpers


@pytest.mark.parametrize(("seconds", "bucket"), [(0.0, "0-10"), (9.9, "0-10"), (10.0, "10-30"), (45.0, "30-60"), (60.0, "60+"), (300.0, "60+")])
def test_length_buckets(seconds, bucket):
    assert facts.duration_bucket(seconds) == bucket


def test_coverage_counts_time_not_words():
    assert facts.coverage([(0.0, 2.0), (4.0, 6.0)], [(1.0, 5.0)]) == pytest.approx(0.5)
    assert facts.coverage([(0.0, 1.0), (0.5, 1.5)], [(0.0, 1.5)]) == pytest.approx(1.0)
    assert facts.coverage([(0.0, 2.0)], []) == 0.0


def test_longest_gap_includes_both_ends():
    assert facts.longest_gap([3.0, 4.0], 10.0) == (6.0, 4.0, 10.0)
    assert facts.longest_gap([], 10.0) == (10.0, 0.0, 10.0)
    assert facts.longest_gap([-1.0, 20.0, 5.0], 10.0) == (5.0, 0.0, 5.0)


def test_sentence_ending_needs_punctuation_to_mean_anything():
    assert facts.ends_mid_sentence([{"text": "We stop.", "end": 5.9}, {"text": "and then", "end": 6.0}], 6.0) is True
    assert facts.ends_mid_sentence([{"text": "and", "end": 3.0}, {"text": "stop.", "end": 5.9}], 6.0) is False
    assert facts.ends_mid_sentence([{"text": "no marks at all", "end": 5.9}], 6.0) is None


def test_scene_changes_come_from_the_picture(tmp_path):
    path = tmp_path / "two.mp4"
    source = "color=c=red:s=320x568:r=30:d=1[a];color=c=blue:s=320x568:r=30:d=1[b];[a][b]concat=n=2:v=1"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", source, "-pix_fmt", "yuv420p", str(path)], check=True)
    found = facts.scene_changes(path, 2.0)
    assert len(found) == 1 and found[0] == pytest.approx(1.0, abs=0.05)


def test_a_silent_end_stays_a_readable_number():
    assert facts.rms_db(np.zeros(100)) == -99.0
    assert facts.rms_db(np.ones(100)) == 0.0


# What it will not claim


def test_nothing_measured_is_never_a_zero(tmp_path):
    built = build(Case("bare", "", Recipe(burn_captions=False)), tmp_path)
    built.project.timeline_path.unlink()
    built.project.edl_path.unlink()
    with fakes([], [], None):
        data = facts.measure(built.project, built.file)
    for name in ("cuts_per_10s", "first_cut_s", "caption_coverage", "text_under_reading_time", "safe_zone_project"):
        assert data["facts"][name]["value"] is None and data["facts"][name]["why"], name
    assert value(data, "scene_changes_per_10s") is not None


def test_a_film_with_no_captions_has_zero_coverage_not_an_unknown(tmp_path):
    built = build(Case("none", "", Recipe(burn_captions=False)), tmp_path)
    timeline = json.loads(built.project.timeline_path.read_text())
    write_json(built.project.timeline_path, {**timeline, "captions": []})
    with planted(built):
        assert value(facts.measure(built.project, built.file), "caption_coverage") == 0.0


def test_no_transcript_leaves_speech_facts_unknown(tmp_path, monkeypatch):
    built = build(Case("deaf", "", Recipe(burn_captions=False)), tmp_path)
    monkeypatch.setattr(gates, "transcribe", lambda *_, **__: (None, "No transcriber."))
    built.project.edl_path.unlink()
    data = facts.measure(built.project, built.file)
    assert value(data, "first_word_s") is None and "No transcriber" in data["facts"]["first_word_s"]["why"]
    assert value(data, "hook_word_count") is None and value(data, "ends_cold_s") is None


def test_silent_film_reports_no_loudness_rather_than_failing(tmp_path):
    built = build(Case("quiet", "", Recipe(gaps=[(0.0, 6.0)], burn_captions=False)), tmp_path)
    with planted(built):
        data = facts.measure(built.project, built.file)
    assert value(data, "opening_lufs") is None and data["facts"]["opening_lufs"]["why"]


def test_no_reader_keeps_the_planned_time_and_says_so(film, monkeypatch):
    monkeypatch.setattr(ocr, "reader", lambda: (None, "No text reader."))
    with planted(film):
        entry = facts.first_text(gates.Context.open(film.project, film.file), 4.0)
    assert entry["value"] == 0.0 and entry["confirmed"] is False and "No text reader" in entry["how"]


def test_saved_transcript_is_reused_and_a_stale_one_is_not(film, monkeypatch):
    digest = qa.sha256(film.file)
    heard = [{"text": "late", "start": 1.5, "end": 1.9}]
    write_json(film.project.review_dir / "transcript.json", {"sha256": digest, "available": True, "reason": "", "words": heard})
    monkeypatch.setattr(gates, "transcribe", lambda *_, **__: pytest.fail("transcribed again"))
    assert value(facts.measure(film.project, film.file), "first_word_s") == 1.5
    write_json(film.project.review_dir / "transcript.json", {"sha256": "other", "available": True, "reason": "", "words": heard})
    monkeypatch.setattr(gates, "transcribe", lambda *_, **__: ([Word(text="now", start=0.5, end=0.9)], ""))
    assert value(facts.measure(film.project, film.file), "first_word_s") == 0.5
    (film.project.review_dir / "transcript.json").unlink()


def test_the_ending_is_timed_from_the_last_word_to_the_last_frame(film, monkeypatch):
    heard = [{"text": "late", "start": 1.5, "end": 1.9}]
    write_json(film.project.review_dir / "transcript.json", {"sha256": qa.sha256(film.file), "available": True, "reason": "", "words": heard})
    monkeypatch.setattr(gates, "transcribe", lambda *_, **__: pytest.fail("transcribed again"))
    data = facts.measure(film.project, film.file)
    length = gates.Context.open(film.project, film.file).clip.duration
    assert value(data, "ends_cold_s") == pytest.approx(length - 1.9, abs=0.001)
    assert facts.NAMES["ends_cold_s"] in facts.report_text(data)
    (film.project.review_dir / "transcript.json").unlink()


def test_the_report_step_never_starts_a_transcriber(tmp_path, monkeypatch):
    """It runs at the end of every review, so it must not cost another 4 GB of memory."""
    built = build(Case("quiet", "", Recipe(burn_captions=False, seconds=3.0)), tmp_path)
    monkeypatch.setattr(gates, "transcribe", lambda *_, **__: pytest.fail("transcribed during a report"))
    built.project.edl_path.unlink()
    data = facts.after_review(built.project, built.file)
    assert value(data, "first_word_s") is None and (built.project.review_dir / "facts.json").exists()


def test_the_report_step_survives_a_broken_file(tmp_path):
    built = build(Case("broken", "", Recipe(burn_captions=False, seconds=3.0)), tmp_path)
    built.file.write_bytes(b"not a film")
    assert "error" in facts.after_review(built.project, built.file)


@pytest.mark.parametrize("banned", ["score", "viral", "predict", "retention", "good", "bad", "verified"])
def test_the_report_holds_no_score_and_no_judgement(measured, banned):
    text = facts.report_text(measured).lower().replace("none is a score", "").replace("none says how a film will do", "")
    assert banned not in text, banned
    assert not any(banned in name for name in measured["facts"])


def test_the_report_carries_its_caution(measured):
    assert "None is a score" in facts.report_text(measured)


# The command


def test_command_writes_facts_and_a_short_report(film, capsys):
    with planted(film):
        assert main(["facts", "-p", str(film.project.root)]) == 0
    saved = json.loads((film.project.review_dir / "facts.json").read_text())
    assert saved["sha256"] == qa.sha256(film.file)
    report = (film.project.review_dir / "facts.md").read_text()
    assert "Time to first word" in report and report.count("\n") < 40
    assert "Time to first word" in capsys.readouterr().out
