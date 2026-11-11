from __future__ import annotations

import argparse
import json
import os
import tomllib
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from conftest import make_clip

from altocue import cli, doctor, ffmpeg, ingest, transcribe
from altocue.cli import main
from altocue.errors import AltocueError
from altocue.models import Edl, Removed, Segment
from altocue.project import Project, nearest_rate


def test_new_project_probes_clip(tmp_path, clip, capsys):
    folder = tmp_path / "video"
    assert main(["new", str(folder), "--input", str(clip), "--brief", "A test"]) == 0
    project = Project.open(folder)
    found = project.clips()
    assert [c.id for c in found] == ["take-one"]
    assert found[0].has_audio and found[0].fps == 30
    assert project.fps == 30
    assert project.size == (1080, 1920)
    assert "A test" in (folder / "brief.md").read_text()
    assert "Project ready" in capsys.readouterr().out


def test_new_refuses_existing_project(tmp_path, clip):
    folder = tmp_path / "video"
    main(["new", str(folder), "--input", str(clip)])
    assert main(["new", str(folder), "--input", str(clip)]) == 1


def test_missing_input_is_reported(tmp_path, capsys):
    assert main(["new", str(tmp_path / "v"), "--input", str(tmp_path / "nope.mp4")]) == 1
    assert "Not found" in capsys.readouterr().err


def test_edl_round_trips():
    edl = Edl(fps=30, segments=[Segment(id="s1", clip="a", start=1, end=2.5)], removed=[Removed(clip="a", start=0, end=1, kind="silence", evidence="gap")])
    again = Edl.from_dict(json.loads(json.dumps(edl.to_dict())))
    assert again == edl and again.duration == 1.5


def test_nearest_rate_snaps_camera_rates():
    assert nearest_rate(23.976) == 24 and nearest_rate(29.97) == 30 and nearest_rate(59.94) == 60


def test_wav_round_trip(tmp_path):
    samples = np.sin(np.linspace(0, 100, 48000)).astype(np.float32) * 0.5
    path = ffmpeg.write_wav(tmp_path / "a.wav", samples, 48000)
    back, rate = ffmpeg.read_wav(path)
    assert rate == 48000 and np.allclose(back, samples, atol=1e-3)


def test_loudness_measures(tmp_path, clip):
    wav = ffmpeg.extract_wav(clip, tmp_path / "a.wav")
    measured = ffmpeg.loudness(wav)
    assert set(measured) == {"integrated", "range", "true_peak"}


def test_probe_rejects_non_media(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_text("not video")
    with pytest.raises(AltocueError):
        ffmpeg.probe_clip(bad)


def test_filters_list_has_what_we_rely_on():
    for name in ("lut3d", "loudnorm", "overlay", "afftdn", "alimiter", "sidechaincompress"):
        assert ffmpeg.has_filter(name), name


def options(stage: str) -> set[str]:
    """Every option the stage's own command line defines."""
    return set(vars(cli.stage_args(stage)))


def test_stage_args_carry_every_default_and_refuse_an_unknown_option():
    """The demo built deliver's options by hand and crashed once deliver grew --stems."""
    args = cli.stage_args("deliver", project="video", override="why")
    assert args.stems is False and args.renderer == "auto" and args.project == "video" and args.override == "why"
    with pytest.raises(TypeError, match="no option colour"):
        cli.stage_args("deliver", colour="red")


def test_run_can_reach_the_review_stage(tmp_path, monkeypatch):
    from altocue import review, run

    project = Project.create(tmp_path / "video")
    seen = {}
    monkeypatch.setattr(review, "run", lambda args: seen.update(vars(args)) or 0)
    run.stages(project, argparse.Namespace())["review"]()
    assert options("review") <= set(seen)
    assert seen["reviewer"] is None and seen["upload"] is False and seen["page"] is False


def test_run_hands_master_every_option_it_has(tmp_path, monkeypatch):
    from altocue import master, run

    project = Project.create(tmp_path / "video")
    seen = {}
    monkeypatch.setattr(master, "run", lambda args: seen.update(vars(args)) or 0)
    run.stages(project, argparse.Namespace())["master"]()
    assert options("master") <= set(seen)


def test_run_stops_at_a_stage_that_fails(tmp_path, monkeypatch):
    from altocue import run

    project = Project.create(tmp_path / "video")
    ran = []
    table = {name: (lambda n=name: ran.append(n) or (1 if n == "qa" else None)) for name in run.ORDER}
    monkeypatch.setattr(run, "stages", lambda project, args: table)
    args = argparse.Namespace(project=str(project.root), start="master", end="review", draft=False)
    assert run.run(args) == 1
    assert ran == ["master", "qa"]


def test_run_keeps_an_existing_cut_and_its_rulings(tmp_path, capsys):
    from altocue import run

    project = Project.create(tmp_path / "video")
    project.save_edl(Edl(fps=30, segments=[Segment(id="s1", clip="a", start=0, end=1)]))
    run.stages(project, argparse.Namespace(start="ingest"))["cut"]()
    assert "Keeping the cut" in capsys.readouterr().out
    assert project.edl().segments[0].id == "s1"


def test_a_fresh_cut_saves_the_old_one(tmp_path, monkeypatch, capsys):
    from altocue import cut

    project = Project.create(tmp_path / "video")
    project.save_edl(Edl(fps=30, segments=[Segment(id="s1", clip="a", start=0, end=1)]))
    monkeypatch.setattr(cut, "propose", lambda project: project.edl())
    monkeypatch.setattr(cut, "refuse", lambda project, edl: None)
    monkeypatch.setattr(cut, "write_report", lambda project, edl: "r")
    monkeypatch.setattr(cut, "write_review", lambda project, edl: "v")
    monkeypatch.setattr(cut, "write_script", lambda project, edl: "s")
    assert cut.run(argparse.Namespace(project=str(project.root), remove=None, apply_review=None, reason="")) == 0
    assert (project.work / "edl-previous.json").exists()
    assert "earlier cut" in capsys.readouterr().out


def test_clips_keep_the_order_given(tmp_path):
    later = make_clip(tmp_path / "b.mp4", seconds=0.5)
    earlier = make_clip(tmp_path / "a.mp4", seconds=0.5)
    assert main(["new", str(tmp_path / "video"), "--input", str(later), str(earlier)]) == 0
    assert [c.id for c in Project.open(tmp_path / "video").clips()] == ["b", "a"]


def test_a_second_ingest_keeps_that_order_and_adds_new_clips_last(tmp_path):
    later = make_clip(tmp_path / "b.mp4", seconds=0.5)
    earlier = make_clip(tmp_path / "a.mp4", seconds=0.5)
    main(["new", str(tmp_path / "video"), "--input", str(later), str(earlier)])
    project = Project.open(tmp_path / "video")
    (project.sources / "0-extra.mp4").symlink_to(make_clip(tmp_path / "extra.mp4", seconds=0.5))
    assert [c.id for c in ingest.ingest(project)] == ["b", "a", "0-extra"]


def test_every_engine_fix_names_a_real_extra():
    extras = tomllib.loads(Path("pyproject.toml").read_text())["project"]["optional-dependencies"]
    named = {engine.extra for engine in transcribe.ENGINES.values() if isinstance(engine, transcribe.LocalEngine)}
    assert named and named <= set(extras)


def test_doctor_checks_the_filters_the_required_stages_use():
    needed = {"highpass", "bandreject", "equalizer", "afftdn", "asendcmd", "aresample", "apad", "atrim", "silencedetect", "astats"}
    assert needed <= set(doctor.REQUIRED_FILTERS)


def test_no_command_takes_an_abbreviated_option(capsys):
    """On Ubuntu's Python 3.12.3, `learn pair --a` read as an abbreviation of learn's own --age or --as-of."""
    with pytest.raises(SystemExit) as stopped:
        main(["status", "--proj", "."])
    assert stopped.value.code == 2
    assert "unrecognized arguments: --proj" in capsys.readouterr().err


def test_loudness_asks_nothing_ffmpeg_five_lacks(monkeypatch):
    """ffmpeg 5.1 refuses ebur128's framelog=quiet, and every clean stopped there on Debian 12."""
    seen = []

    def fake(args, **_):
        seen.append(args)
        return SimpleNamespace(stderr="I: -20.0 LUFS\nLRA: 3.0 LU\nPeak: -1.0 dBFS\n")

    monkeypatch.setattr(ffmpeg, "run", fake)
    assert ffmpeg.loudness("x.wav")["integrated"] == -20.0
    assert "framelog=quiet" not in " ".join(seen[0])


def broken_stage(monkeypatch, error: BaseException) -> None:
    from altocue import status

    def boom(args):
        raise error

    monkeypatch.setattr(status, "run", boom)


def test_a_failure_nobody_planned_for_is_three_lines_and_a_log(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ALTOCUE_TRACEBACK")
    broken_stage(monkeypatch, KeyError("foo"))
    assert main(["status", "-p", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    lines = captured.err.splitlines()
    assert len(lines) == 3 and "Traceback" not in captured.err + captured.out
    assert lines[0].startswith("altocue: `altocue status` stopped") and "KeyError" in lines[0] and "foo" in lines[0]
    assert lines[1].startswith("Fix: ") and cli.ISSUES in lines[1] and "ALTOCUE_TRACEBACK=1" in lines[1]
    log = Path(lines[2].removeprefix("Log: "))
    assert log.parent == Path(os.environ["ALTOCUE_HOME"]) / "logs"
    written = log.read_text()
    assert "Traceback (most recent call last)" in written and "KeyError: 'foo'" in written and f"Command: altocue status -p {tmp_path}" in written


def test_only_the_newest_crash_logs_are_kept(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ALTOCUE_TRACEBACK")
    folder = Path(os.environ["ALTOCUE_HOME"]) / "logs"
    folder.mkdir(parents=True)
    for number in range(cli.LOGS_KEPT + 5):
        (folder / f"20200101-0000{number:02d}-old.log").write_text("old")
    broken_stage(monkeypatch, RuntimeError("no"))
    assert main(["status"]) == 1
    kept = sorted(path.name for path in folder.glob("*.log"))
    assert len(kept) == cli.LOGS_KEPT and kept[-1].endswith("-status.log") and "20200101-000000-old.log" not in kept


def test_a_developer_can_ask_for_the_traceback(monkeypatch):
    broken_stage(monkeypatch, KeyError("foo"))  # ALTOCUE_TRACEBACK=1 is set for every test, in conftest
    with pytest.raises(KeyError):
        main(["status"])


def test_an_interrupt_is_quiet_and_an_exit_is_left_alone(monkeypatch, capsys):
    monkeypatch.delenv("ALTOCUE_TRACEBACK")
    broken_stage(monkeypatch, KeyboardInterrupt())
    assert main(["status"]) == 130
    assert capsys.readouterr().err.strip() == "altocue: stopped."
    broken_stage(monkeypatch, SystemExit(7))
    with pytest.raises(SystemExit) as left:
        main(["status"])
    assert left.value.code == 7


def test_an_error_with_a_fix_still_prints_as_it_did(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ALTOCUE_TRACEBACK")
    broken_stage(monkeypatch, AltocueError("No film here.", fix="Render one."))
    assert main(["status"]) == 1
    assert capsys.readouterr().err == "altocue: No film here.\nFix: Render one.\n"
    assert not (Path(os.environ["ALTOCUE_HOME"]) / "logs").exists()


def test_run_takes_the_same_renderers_as_render(capsys):
    from altocue import motion

    def choices(command: str) -> list[str]:
        parser = cli.Parser(prog=command)
        __import__(cli.STAGES[command][0], fromlist=["add_arguments"]).add_arguments(command, parser)
        return next(action.choices for action in parser._actions if action.dest == "renderer")

    assert choices("run") == choices("render") == [*motion.RENDERERS, "auto"]
    with pytest.raises(SystemExit):
        main(["run", "--renderer", "foo"])
    assert "invalid choice: 'foo'" in capsys.readouterr().err


# `altocue new` is forgiving


def test_a_failed_new_leaves_nothing_so_the_retry_works(tmp_path, clip, capsys):
    folder = tmp_path / "x4"
    notes = tmp_path / "truth.json"
    notes.write_text("{}")
    for wrong in (["--brand", str(tmp_path / "nonexistent")], ["--aspect", "9:99"], ["--aspect", "wide", "--platform", "reels"]):
        assert main(["new", str(folder), "--input", str(clip), *wrong]) == 1
        assert "Fix: " in capsys.readouterr().err and not folder.exists()
    assert main(["new", str(folder), "--input", str(notes)]) == 1
    err = capsys.readouterr().err
    assert "Fix: Give video files" in err and "can run again" in err and not folder.exists()
    assert main(["new", str(folder), "--input", str(clip), "--brand", "house"]) == 0
    assert Project.open(folder).clips()


def test_every_refusal_from_new_says_what_to_do(tmp_path, clip, capsys):
    assert main(["new", str(tmp_path / "v"), "--input", str(tmp_path / "nope.mp4")]) == 1
    assert "Not found" in (err := capsys.readouterr().err) and "Fix: Check the path" in err
    assert main(["new", str(tmp_path / "v"), "--input", str(clip)]) == 0
    assert main(["new", str(tmp_path / "v"), "--input", str(clip)]) == 1
    assert "already an Altocue project" in (err := capsys.readouterr().err) and "Fix: Pick another folder name, or delete" in err
    assert main(["new", str(clip), "--input", str(clip)]) == 1
    assert "is a file" in (err := capsys.readouterr().err) and "Fix: " in err
    assert main(["new", str(tmp_path / "w"), "--input", str(clip), "--aspect", "tall"]) == 1
    assert "Unknown aspect tall" in (err := capsys.readouterr().err) and "vertical, portrait, square, wide" in err


def test_a_new_that_fails_part_way_takes_its_folder_back(tmp_path, clip, monkeypatch):
    from altocue import brand

    def broken(project, kit, house=False):
        raise AltocueError("The brand's font cannot be used.", fix="Use a .ttf file.")

    monkeypatch.setattr(brand, "ingest", broken)
    assert main(["new", str(tmp_path / "gone"), "--input", str(clip), "--brand", "house"]) == 1
    assert not (tmp_path / "gone").exists()
    mine = tmp_path / "footage"
    (mine / "sources").mkdir(parents=True)
    (mine / "notes.txt").write_text("keep me")
    (mine / "sources" / "old.txt").write_text("and me")
    assert main(["new", str(mine), "--input", str(clip), "--brand", "house"]) == 1
    assert sorted(path.relative_to(mine).as_posix() for path in mine.rglob("*")) == ["notes.txt", "sources", "sources/old.txt"]


def test_new_takes_a_platform_and_sets_the_shape_and_the_safe_zone_together(tmp_path, clip, capsys):
    from altocue import platforms

    wide = make_clip(tmp_path / "wide.mp4", seconds=1.0, size="640x360")
    assert main(["new", str(tmp_path / "yt"), "--input", str(wide), "--platform", "youtube"]) == 0
    made = Project.open(tmp_path / "yt")
    assert made.config["output"]["aspect"] == "16:9" and made.config["output"]["platform"] == "youtube" and platforms.zone_of(made)
    assert "format" not in made.config, "a platform is not a kind"
    assert main(["new", str(tmp_path / "feed"), "--input", str(clip), "--platform", "linkedin", "--kind", "interview"]) == 0
    made = Project.open(tmp_path / "feed")
    assert (made.config["output"]["aspect"], made.config["output"]["platform"], made.config["format"]["kind"]) == ("4:5", "linkedin", "interview")
    assert main(["new", str(tmp_path / "same"), "--input", str(clip), "--platform", "reels", "--aspect", "vertical"]) == 0
    capsys.readouterr()
    assert main(["new", str(tmp_path / "clash"), "--input", str(clip), "--platform", "reels", "--aspect", "16:9"]) == 1
    assert "--platform reels plays 9:16, and --aspect asks for 16:9" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["new", str(tmp_path / "bad"), "--input", str(clip), "--platform", "myspace"])


@pytest.mark.parametrize(("word", "aspect"), [("vertical", "9:16"), ("portrait", "9:16"), ("square", "1:1"), ("wide", "16:9"), ("Wide", "16:9")])
def test_new_takes_a_word_for_the_shape(tmp_path, clip, word, aspect):
    assert main(["new", str(tmp_path / "v"), "--input", str(clip), "--aspect", word]) == 0
    assert Project.open(tmp_path / "v").config["output"]["aspect"] == aspect


def test_the_help_for_new_states_the_default_shape(capsys):
    with pytest.raises(SystemExit):
        main(["new", "--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "The default is 9:16 (vertical)" in out and "vertical, portrait, square, wide" in out and "--platform {reels,tiktok,shorts,youtube,linkedin,square}" in out


def test_new_says_when_the_film_is_not_the_shape_of_the_footage(tmp_path, clip, capsys):
    wide = make_clip(tmp_path / "wide.mp4", seconds=1.0, size="640x360")
    assert main(["new", str(tmp_path / "a"), "--input", str(wide)]) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("Shape:")]
    assert len(lines) == 1
    assert "the footage is 16:9 and this film is 9:16, the default" in lines[0] and "`--aspect 16:9`" in lines[0] and "cropped" in lines[0]
    assert main(["new", str(tmp_path / "b"), "--input", str(wide), "--platform", "reels"]) == 0
    assert "this film is 9:16, from --platform reels" in capsys.readouterr().out
    assert main(["new", str(tmp_path / "c"), "--input", str(wide), "--kind", "short"]) == 0
    assert "this film is 9:16, from the kind" in capsys.readouterr().out
    for quiet in (["--input", str(wide), "--aspect", "9:16"], ["--input", str(wide), "--aspect", "wide"], ["--input", str(clip)]):
        assert main(["new", str(tmp_path / f"q{len(quiet)}{quiet[-1][-4:]}"), *quiet]) == 0
        assert "Shape:" not in capsys.readouterr().out, "asked for by name, or already the same shape"


def test_a_shape_is_named_as_people_name_it():
    assert [ingest.shape_of(*size) for size in ((1920, 1080), (1080, 1920), (3840, 2160), (1080, 1350), (1920, 1088), (720, 720))] == [
        "16:9", "9:16", "16:9", "4:5", "16:9", "1:1",
    ]  # fmt: skip
    assert ingest.shape_of(1000, 700) == "10:7"


# The project is found from inside it


def test_a_command_run_from_inside_the_project_finds_it(tmp_path, monkeypatch, capsys):
    project = Project.create(tmp_path / "first")
    assert Project.open(project.work).root == project.root
    assert Project.open(project.words_dir).root == project.root
    monkeypatch.chdir(project.work)
    assert Project.open().root == project.root
    assert main(["status"]) == 0
    assert "ingest" in capsys.readouterr().out


def test_the_nearest_project_above_wins(tmp_path):
    outer = Project.create(tmp_path / "outer")
    inner = Project.create(outer.root / "clips" / "one")
    assert Project.open(inner.work).root == inner.root
    assert Project.open(outer.root / "clips").root == outer.root


def test_a_folder_that_holds_projects_lists_them(tmp_path, monkeypatch, capsys):
    for name in ("second", "first", "my video"):
        Project.create(tmp_path / "work" / name)
    (tmp_path / "work" / "notes").mkdir()
    with pytest.raises(AltocueError) as caught:
        Project.open(tmp_path / "work")
    assert "It holds projects: first, my video, second." in str(caught.value) and "`-p first`" in caught.value.fix
    monkeypatch.chdir(tmp_path / "work")
    assert main(["status"]) == 1
    err = capsys.readouterr().err
    assert "first, my video, second" in err and "Fix: Name one with `-p first`" in err


def test_a_folder_with_no_project_near_it_says_how_to_start_or_where_to_go(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(AltocueError) as caught:
        Project.open(tmp_path / "empty")
    assert "or any folder above it" in str(caught.value) and "-p <folder>" in caught.value.fix and "altocue new" in caught.value.fix


def test_a_path_that_is_not_there_never_opens_the_project_around_it(tmp_path):
    """`-p typo` from inside a project must not quietly act on that project."""
    project = Project.create(tmp_path / "first")
    with pytest.raises(AltocueError, match="No folder at") as caught:
        Project.open(project.root / "typo")
    assert "Check the path" in caught.value.fix
