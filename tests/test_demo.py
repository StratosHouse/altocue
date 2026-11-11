"""`altocue demo`: a generated sample, every free stage, a printed result, and no files left behind."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from altocue import claims, cut, demo, locks, qa
from altocue.errors import AltocueError
from altocue.models import Edl, Removed, Segment, Word


def args(**more) -> argparse.Namespace:
    return argparse.Namespace(**{"keep": None, "renderer": "auto", **more})


real_sample_stage = demo.sample_stage


def quiet_steps(monkeypatch, *, failing: str = "", gates: list | None = None) -> list[str]:
    ran: list[str] = []

    def steps(project, renderer):
        def step(name: str):
            def work() -> str:
                ran.append(name)
                if name == failing:
                    print("the log of what went wrong")
                    raise AltocueError(f"{name} broke.", fix="Do the thing.")
                return f"{name} done"

            return demo.Step(name, work)

        return [step(name) for name in ("ingest", "cut", "render", "qa")]

    monkeypatch.setattr(demo, "steps", steps)
    monkeypatch.setattr(demo, "sample_stage", lambda root: "a sample")
    monkeypatch.setattr(demo, "preflight", lambda: None)
    monkeypatch.setattr(demo, "start_project", lambda root, sample: demo.Project.create(root / "altocue-demo"))
    monkeypatch.setattr(demo, "outcome", lambda project: ("Fact gates: all clear.", True))
    return ran


def serve(monkeypatch, body: bytes = b"a take") -> list[str]:
    """Stands in for the one download, with its hash, so no test touches the network."""
    asked: list[str] = []

    def fetched(url, dest, **_):
        asked.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        return dest

    monkeypatch.setattr(demo.http, "download", fetched)
    monkeypatch.setattr(demo, "SAMPLE_SHA256", hashlib.sha256(body).hexdigest())
    return asked


def test_the_take_comes_from_our_own_site(tmp_path, monkeypatch):
    asked = serve(monkeypatch)
    take = demo.fetch_sample(tmp_path)
    assert asked == [demo.SAMPLE_URL] and take.parent == tmp_path
    assert urlsplit(demo.SAMPLE_URL).hostname == demo.SAMPLE_HOST


def test_a_failed_fetch_stops_the_demo_and_says_so(tmp_path, monkeypatch):
    def down(*_, **__):
        raise demo.http.Unreachable("no route to host")

    monkeypatch.setattr(demo.http, "download", down)
    with pytest.raises(AltocueError, match="Could not fetch the demo take, so the demo stops here") as caught:
        demo.fetch_sample(tmp_path)
    assert "no route to host" in str(caught.value) and caught.value.fix


def test_a_failed_fetch_has_no_fallback(monkeypatch, tmp_path, capsys):
    """No other sample exists, so a run with no take ends there, with nothing else tried."""

    def down(*_, **__):
        raise demo.http.Unreachable("no route to host")

    monkeypatch.setattr(demo.tempfile, "mkdtemp", lambda prefix: str(tmp_path / "x"))
    (tmp_path / "x").mkdir()
    ran = quiet_steps(monkeypatch)
    monkeypatch.setattr(demo, "sample_stage", real_sample_stage)
    monkeypatch.setattr(demo.http, "download", down)
    assert demo.run(args()) == 1
    assert "stops here" in capsys.readouterr().out and ran == []


def test_behind_a_proxy_the_fix_names_the_proxy_and_the_host(tmp_path, monkeypatch):
    def down(*_, **__):
        raise demo.http.Unreachable("Could not download the take through the proxy at proxy.test: 403 Forbidden")

    monkeypatch.setattr(demo.http, "download", down)
    monkeypatch.setattr(demo.http, "proxy_for", lambda url: "proxy.test")
    with pytest.raises(AltocueError) as caught:
        demo.fetch_sample(tmp_path)
    assert "proxy" in caught.value.fix and demo.SAMPLE_HOST in caught.value.fix
    monkeypatch.setattr(demo.http, "proxy_for", lambda url: "")
    with pytest.raises(AltocueError) as caught:
        demo.fetch_sample(tmp_path)
    assert "proxy" not in caught.value.fix


def test_a_demo_that_failed_never_names_a_film_that_is_not_there(monkeypatch, tmp_path, capsys):
    """It printed "The film is at .../out/final.mp4" under a failed download, when nothing had been made."""
    quiet_steps(monkeypatch, failing="cut")
    kept = tmp_path / "kept"
    assert demo.run(args(keep=str(kept))) == 1
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert "did not pass" in last and str(kept) in last and "there is no film" in last
    assert "final.mp4" not in last and "page.html" not in last and "Kept in" not in last


def test_a_demo_that_failed_a_gate_names_only_what_it_made(monkeypatch, tmp_path, capsys):
    quiet_steps(monkeypatch)
    monkeypatch.setattr(demo, "outcome", lambda project: ("Verdict: FIX, as code decided it.", False))
    kept = tmp_path / "kept"
    film = kept / "altocue-demo" / "out" / "final.mp4"
    film.parent.mkdir(parents=True)
    film.write_bytes(b"film")
    assert demo.run(args(keep=str(kept))) == 1
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert "did not pass" in last and str(film) in last and "page.html" not in last


def test_a_failed_demo_that_removed_its_files_offers_no_film(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(demo.tempfile, "mkdtemp", lambda prefix: str(tmp_path / "x"))
    (tmp_path / "x").mkdir()
    quiet_steps(monkeypatch, failing="cut")
    assert demo.run(args()) == 1
    assert capsys.readouterr().out.strip().splitlines()[-1] == "Removed the demo files."


def test_a_different_file_at_the_address_is_refused(tmp_path, monkeypatch):
    serve(monkeypatch)
    monkeypatch.setattr(demo, "SAMPLE_SHA256", "0" * 64)
    with pytest.raises(AltocueError, match="not the take this version expects"):
        demo.fetch_sample(tmp_path)
    assert not list(tmp_path.iterdir()), "a file nobody asked for is not left behind"


def test_the_network_use_is_stated_before_anything_runs(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(demo.tempfile, "mkdtemp", lambda prefix: str(tmp_path / "x"))
    (tmp_path / "x").mkdir()

    def first(root):
        assert demo.SAMPLE_HOST in capsys.readouterr().out, "said before the download"
        return "a take"

    quiet_steps(monkeypatch)
    monkeypatch.setattr(demo, "sample_stage", first)
    assert demo.run(args()) == 0


def test_the_opening_says_plainly_what_the_presenter_is(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(demo.tempfile, "mkdtemp", lambda prefix: str(tmp_path / "x"))
    (tmp_path / "x").mkdir()
    quiet_steps(monkeypatch)
    demo.run(args())
    assert "generated presenter" in capsys.readouterr().out


def test_the_published_take_is_the_only_source_of_picture_and_sound():
    source = Path(demo.__file__).read_text()
    assert "PIL" not in source and "subprocess" not in source


def test_the_film_carries_a_label_for_the_generated_presenter():
    """The disclosure gate wants it on screen for two seconds, in words it recognises."""
    label = next(item for item in demo.PLAN["items"] if item.get("text") == demo.LABEL)
    assert label["duration"] >= qa.gates.DISCLOSURE_SECONDS and claims.discloses(demo.LABEL, "")


def test_the_take_is_registered_as_a_generated_person(tmp_path, monkeypatch):
    """So the claims and disclosure gates run on the demo film, as they would on a customer's."""
    sample = tmp_path / "raw-a.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=64x64:r=10:d=1", str(sample)], check=True)
    project = demo.start_project(tmp_path, sample)
    assert (project.sources / demo.SAMPLE_NAME).exists()
    entry = demo.store.read_manifest(project)["presenter"]
    assert "person" in json.loads((project.root / entry["provenance"]).read_text())["synthetic"]


def test_the_name_is_spelled_right_in_captions(tmp_path):
    project = demo.Project.create(tmp_path / "altocue-demo")
    demo.spell_the_name(project)
    project = demo.Project.open(project.root)
    assert project.fix_text("AltaQ.") == "Altocue." and project.fix_text("Altoque") == "Altocue"
    split = [Word("is", 0.0, 0.2), Word("Alta", 0.2, 0.5), Word("Q.", 0.5, 0.8), Word("It's", 1.0, 1.2)]
    assert [(w.text, w.end) for w in project.corrected_all(split)] == [("is", 0.2), ("Altocue.", 0.8), ("It's", 1.2)]


def test_the_files_are_removed_unless_told_to_keep_them(monkeypatch, tmp_path, capsys):
    made = tmp_path / "altocue-demo-x"
    monkeypatch.setattr(demo.tempfile, "mkdtemp", lambda prefix: str(made.mkdir() or made))
    quiet_steps(monkeypatch)
    assert demo.run(args()) == 0
    assert not made.exists()
    assert "Removed the demo files" in capsys.readouterr().out


def test_keep_leaves_the_folder_and_says_where(monkeypatch, tmp_path, capsys):
    quiet_steps(monkeypatch)
    assert demo.run(args(keep=str(tmp_path / "kept"))) == 0
    assert (tmp_path / "kept").is_dir()
    out = capsys.readouterr().out
    assert str(tmp_path / "kept") in out and "Removed" not in out


def test_a_failed_stage_stops_the_run_shows_why_and_still_cleans_up(monkeypatch, tmp_path, capsys):
    made = tmp_path / "altocue-demo-x"
    monkeypatch.setattr(demo.tempfile, "mkdtemp", lambda prefix: str(made.mkdir() or made))
    ran = quiet_steps(monkeypatch, failing="cut")
    assert demo.run(args()) == 1
    out = capsys.readouterr().out
    assert ran == ["ingest", "cut"]
    assert "cut broke" in out and "Do the thing" in out and "the log of what went wrong" in out
    assert not made.exists()


def test_a_stage_that_crashes_is_reported_like_any_other_failure(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(demo.tempfile, "mkdtemp", lambda prefix: str(tmp_path / "x"))
    (tmp_path / "x").mkdir()
    quiet_steps(monkeypatch)

    def broken():
        raise FileNotFoundError("no such tool")

    monkeypatch.setattr(demo, "steps", lambda project, renderer: [demo.Step("clean", broken)])
    assert demo.run(args()) == 1
    assert "clean failed" in capsys.readouterr().out and not (tmp_path / "x").exists()


def test_every_stage_prints_how_long_it_took(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(demo.tempfile, "mkdtemp", lambda prefix: str(tmp_path / "x"))
    (tmp_path / "x").mkdir()
    quiet_steps(monkeypatch)
    demo.run(args())
    out = capsys.readouterr().out
    for name in ("ingest", "cut", "render", "qa"):
        assert f"{name} done" in out
    assert "s  " in out and "Took " in out


def test_a_stage_keeps_its_chatter_off_the_screen(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(demo.tempfile, "mkdtemp", lambda prefix: str(tmp_path / "x"))
    (tmp_path / "x").mkdir()
    quiet_steps(monkeypatch)
    monkeypatch.setattr(demo, "steps", lambda project, renderer: [demo.Step("ingest", lambda: print("noise") or "fine")])
    demo.run(args())
    assert "noise" not in capsys.readouterr().out


def test_the_whole_run_holds_the_heavy_lock(monkeypatch, tmp_path):
    held = []
    real = locks.heavy

    def spy(label):
        held.append(label)
        return real(label)

    monkeypatch.setattr(demo.locks, "heavy", spy)
    monkeypatch.setattr(demo.tempfile, "mkdtemp", lambda prefix: str(tmp_path / "x"))
    (tmp_path / "x").mkdir()
    quiet_steps(monkeypatch)
    demo.run(args())
    assert held


def test_a_gate_that_fails_makes_the_demo_fail(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(demo.tempfile, "mkdtemp", lambda prefix: str(tmp_path / "x"))
    (tmp_path / "x").mkdir()
    quiet_steps(monkeypatch)
    monkeypatch.setattr(demo, "outcome", lambda project: ("FIX. Gate failed: loudness.", False))
    assert demo.run(args()) == 1
    assert "FIX. Gate failed: loudness." in capsys.readouterr().out


def write_qa(project, gates) -> None:
    final = project.out / "final.mp4"
    final.write_bytes(b"film")
    from altocue.project import write_json

    write_json(project.review_dir / "qa.json", {"sha256": qa.sha256(final), "gates": gates})


def test_clear_gates_with_no_reviewer_is_never_called_a_ship(tmp_path):
    project = demo.Project.create(tmp_path / "altocue-demo")
    write_qa(project, [{"id": "words", "passed": True, "skipped": False}])
    said, ok = demo.outcome(project)
    assert ok and "SHIP" in said and "needs a reviewer" in said and "1 of 1" in said


def test_a_code_decided_fix_is_reported_as_it_stands(tmp_path):
    from altocue.project import write_json

    project = demo.Project.create(tmp_path / "altocue-demo")
    write_qa(project, [{"id": "words", "passed": False, "skipped": False, "detail": "85% of 20 words"}])
    write_json(project.review_dir / "report.json", {"verdict": "FIX", "reasons": ["Gate failed: words (85% of 20 words)"]})
    said, ok = demo.outcome(project)
    assert not ok and "FIX" in said and "85% of 20 words" in said


def test_a_gate_that_did_not_run_is_named_and_is_not_a_pass(tmp_path):
    project = demo.Project.create(tmp_path / "altocue-demo")
    write_qa(project, [{"id": "rules_ocr", "passed": False, "skipped": True, "detail": "no reader"}])
    said, ok = demo.outcome(project)
    assert ok and "rules_ocr" in said and "blocks SHIP" in said


def test_the_film_opens_on_a_hook_and_ends_on_a_card():
    assert {"hook", "endcard"} <= {item["type"] for item in demo.PLAN["items"]}


def test_every_graphic_stays_up_long_enough_to_read():
    items = [{**item, "start": 0.0, "end": item["duration"]} for item in demo.PLAN["items"]]
    rows = qa.rules.reading_time({"items": items}, 3.0)
    assert rows and all(not row["short_by"] for row in rows)


def test_the_brief_says_what_must_be_said():
    assert qa.rules.must_say(demo.BRIEF)


def test_a_waiting_retake_is_ruled_on_so_the_film_can_pass(tmp_path, monkeypatch):
    project = demo.Project.create(tmp_path / "altocue-demo")
    edl = Edl(fps=24, segments=[Segment(id="s1", clip="take", start=0.0, end=1.0)], removed=[Removed("take", 0.0, 0.5, "retake", "a false start", "review")])
    project.save_edl(edl)
    ruled = []

    def apply_review(_, path):
        ruled.append(json.loads(Path(path).read_text()))
        return edl

    monkeypatch.setattr(cut, "apply_review", apply_review)
    monkeypatch.setattr(cut, "write_report", lambda *_: None)
    monkeypatch.setattr(cut, "write_review", lambda *_: None)
    demo.rule_on_the_cut(project)
    assert ruled == [{cut.review.removal_id(edl.removed[0]): "cut"}]


def test_nothing_in_the_demo_or_doctor_starts_another_application():
    """Altocue never opens an application window, editor or browser on its own."""
    for module in (demo, demo.doctor):
        source = Path(module.__file__).read_text()
        assert "webbrowser" not in source and '"open"' not in source


def test_the_demo_never_asks_for_a_paid_reviewer(tmp_path, monkeypatch):
    seen = {}

    def spy(namespace):
        seen.update(vars(namespace))
        return 0

    monkeypatch.setenv("GEMINI_API_KEY", "a-key")
    monkeypatch.setattr(demo, "review_run", spy)
    project = demo.Project.create(tmp_path / "altocue-demo")
    write_qa(project, [])
    assert "0 gates passed" in demo.review_stage(project)
    assert seen["reviewer"] == "pack" and seen["upload"] is False and seen["paid_key"] is False


def test_too_little_disk_for_a_first_download_stops_before_anything_runs(monkeypatch):
    monkeypatch.setattr(demo.doctor, "check_ffmpeg", lambda: demo.doctor.Check("ffmpeg", True, True, "ok"))
    monkeypatch.setattr(demo.doctor, "model_cached", lambda engine: False)
    monkeypatch.setattr(demo.doctor, "check_disk", lambda: demo.doctor.Check("disk", False, False, "1.0 GB free", "Free some space."))
    with pytest.raises(AltocueError, match="1.0 GB free"):
        demo.preflight()


class Stop(Exception):
    pass


def test_the_demo_hands_deliver_every_option_deliver_has(tmp_path, monkeypatch):
    """The real run crashed at deliver with "no attribute 'stems'": the options were typed by hand."""
    from altocue import cli, deliver
    from altocue.project import Project

    seen = {}

    def capture(args):
        seen.update(vars(args))
        raise Stop

    monkeypatch.setattr(deliver, "run", capture)
    with pytest.raises(Stop):
        demo.deliver_stage(Project.create(tmp_path / "film"), "basic")
    assert set(vars(cli.stage_args("deliver"))) <= set(seen)
    assert seen["stems"] is False and seen["renderer"] == "basic" and "no reviewer" in seen["override"]


@pytest.mark.slow
def test_the_real_thing(tmp_path, capsys):
    assert demo.run(args(keep=str(tmp_path / "kept"), renderer="basic")) == 0
    assert (tmp_path / "kept" / "altocue-demo" / "out" / "final.mp4").exists()
    assert (tmp_path / "kept" / "altocue-demo" / "review" / "page.html").exists()


def test_a_made_up_name_split_into_three_words_is_joined_back(tmp_path):
    """Whisper hears Altocue under music as "all to cue", three words where a fix of two could not reach."""
    from altocue.models import Word
    from altocue.project import Project

    project = Project.create(tmp_path / "video")
    path = project.root / "altocue.toml"
    heading = "[transcribe.fix]        # names the transcript gets wrong, heard = written"
    path.write_text(path.read_text().replace(heading, '[transcribe.fix]\n"All to cue" = "Altocue"\n"Alta Q" = "Altocue"', 1))
    project = Project.open(project.root)
    said = [Word("is", 0.0, 0.2), Word("all", 0.3, 0.4), Word("to", 0.4, 0.5), Word("cue.", 0.5, 0.8), Word("Alta", 1.0, 1.2), Word("Q", 1.2, 1.3)]
    assert [(w.text, w.end) for w in project.corrected_all(said)] == [("is", 0.2), ("Altocue.", 0.8), ("Altocue", 1.3)]
