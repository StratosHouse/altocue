"""Long to shorts: candidates from the transcript alone, each cut into a normal project."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from test_cut import timed

from altocue import caption, clips, cut, ffmpeg
from altocue import transcribe as transcribing
from altocue.cli import main
from altocue.errors import AltocueError
from altocue.frame import frame_project
from altocue.models import Clip, Word
from altocue.project import Project
from altocue.transcribe import classify

THOUGHT = [
    "Most people get pricing wrong on their first offer.",
    "They set the price from cost instead of value to the buyer.",
    "A buyer does not care what the work cost you to make.",
    "A buyer cares what the work is worth to the business.",
    "So price the outcome and never the hours you spent.",
    "That one change doubled our pricing in a single year.",
]
RUN_ON = [
    "And that is why it matters so much to the team.",
    "As I said earlier we covered the numbers on the last slide.",
    "It keeps going from there and then the second one follows.",
    "Let me show you how that works.",
]


def spoken_lines(lines: list[str], *, start: float = 0.5, step: float = 0.32, gap: float = 0.1, pause: float = 0.6) -> list[Word]:
    words: list[Word] = []
    at = start
    for line in lines:
        here = timed(line, start=at, step=step, gap=gap)
        words += here
        at = here[-1].end + pause
    return words


def talk_project(tmp_path: Path, lines: list[str] | dict[str, list[str]], **options) -> Project:
    project = Project.create(tmp_path / "talk", aspect="16:9")
    takes = lines if isinstance(lines, dict) else {"talk": lines}
    found = []
    for name, said in takes.items():
        words = spoken_lines(said, **options)
        found.append(Clip(id=name, path=str(tmp_path / f"{name}-gone.mp4"), duration=words[-1].end + 2, fps=30, width=1920, height=1080))
        project.save_clips(found)
        project.save_words(name, classify(words), "test")
    return project


def checks_of(candidate: dict) -> dict[str, bool]:
    return {name: row["passed"] for name, row in candidate["checks"].items()}


def test_the_length_range_reads_plainly():
    assert clips.parse_length("30-60") == (30.0, 60.0)
    assert clips.parse_length("15-20.5") == (15.0, 20.5)


@pytest.mark.parametrize("text", ["60-30", "abc", "30", "-5-10", "0-10"])
def test_a_bad_length_range_is_refused(text):
    with pytest.raises(AltocueError):
        clips.parse_length(text)


def test_candidates_are_whole_sentences(tmp_path):
    project = talk_project(tmp_path, THOUGHT)
    found = clips.find_candidates(project, count=3, length=(10, 40))
    assert found
    for candidate in found:
        assert candidate["transcript"].split()[0][0].isupper()
        assert candidate["transcript"].rstrip().endswith(".")


def test_a_complete_thought_wins(tmp_path):
    project = talk_project(tmp_path, THOUGHT)
    best = clips.find_candidates(project, count=1, length=(25, 40))[0]
    assert best["transcript"].startswith("Most people get pricing wrong")
    assert all(checks_of(best).values()), best["checks"]


def test_length_counts_what_plays_not_the_pauses(tmp_path):
    project = talk_project(tmp_path, THOUGHT[:3], pause=4.0)
    found = clips.find_candidates(project, count=1, length=(12, 60))[0]
    pauses = found["transcript"].count(".") - 1
    assert pauses >= 1
    assert found["seconds"] == pytest.approx(found["end"] - found["start"] - pauses * (4.0 - 0.4), abs=0.5)


def test_nothing_outside_the_range_is_offered(tmp_path):
    project = talk_project(tmp_path, THOUGHT)
    for candidate in clips.find_candidates(project, count=8, length=(10, 15)):
        assert 10 <= candidate["seconds"] <= 15


def test_candidates_do_not_overlap(tmp_path):
    project = talk_project(tmp_path, [*THOUGHT, *RUN_ON, *THOUGHT])
    found = sorted(clips.find_candidates(project, count=8, length=(10, 30)), key=lambda c: c["start"])
    assert len(found) >= 2
    for a, b in zip(found, found[1:], strict=False):
        assert a["end"] <= b["start"]


def test_a_dangling_reference_is_named(tmp_path):
    project = talk_project(tmp_path, [THOUGHT[0], RUN_ON[1], THOUGHT[4]])
    candidate = clips.find_candidates(project, count=1, length=(5, 60))[0]
    assert not candidate["checks"]["stands_alone"]["passed"]
    assert "as i said" in candidate["checks"]["stands_alone"]["detail"].lower()


def test_an_opening_that_needs_what_came_before_fails(tmp_path):
    project = talk_project(tmp_path, [RUN_ON[0], *THOUGHT[1:3]])
    candidate = clips.find_candidates(project, count=1, length=(5, 60))[0]
    assert not candidate["checks"]["opens"]["passed"]


def first_of(text: str) -> clips.Said:
    return clips.read_lines(spoken_lines([text]), 0.4)[0]


@pytest.mark.parametrize(
    "line",
    [
        "What is it and why is it important?",
        "Now in those days the thing was huge.",
        "Sure.",
        "Yeah, we tried that for a year.",
        "That one change doubled our pricing.",
        "Why does that matter to anyone?",
        "This is a problem for the team.",
    ],
)
def test_a_first_line_that_answers_or_points_back_fails(line):
    assert not clips.opening(first_of(line), False).passed


@pytest.mark.parametrize(
    "line",
    [
        "Most people get it wrong.",
        "This year we doubled our prices.",
        "The Navy sent me to the first big computer.",
        "Why do most launches fail?",
        "No one tells you about hiring.",
        "There are three rules here.",
        "This is Altocue.",
    ],
)
def test_a_first_line_that_stands_up_passes(line):
    assert clips.opening(first_of(line), False).passed


def test_a_retake_is_not_a_theme():
    lines = [
        "What is it and why is it important?",
        "So here is the best part.",
        "A completely separate editor has to sign off.",
        "A completely separate editor has to agree.",
    ]
    assert not clips.focus(clips.read_lines(spoken_lines(lines), 0.4))[0].passed


def test_a_one_word_line_is_no_hook():
    assert not clips.first_line(first_of("Stop.")).passed


def test_a_later_ordinal_needs_its_first(tmp_path):
    lone = clips.dangling(["Second you need a plan for the launch."])
    paired = clips.dangling(["First you need a list of buyers.", "Second you need a plan for the launch."])
    assert lone and not paired


def test_an_open_promise_does_not_end_a_clip():
    line = clips.read_lines(spoken_lines([RUN_ON[3]]), 0.4)[0]
    assert not clips.closing(line, None, False).passed


def test_an_ending_the_next_line_argues_with_fails():
    lines = clips.read_lines(spoken_lines([THOUGHT[0], "But that is only half of the story here."]), 0.4)
    check = clips.closing(lines[0], lines[1], False)
    assert not check.passed and "But" in check.detail


def test_a_question_left_open_does_not_end_a_clip():
    assert not clips.closing(clips.read_lines(spoken_lines(["So what should you do next?"]), 0.4)[0], None, False).passed


@pytest.mark.parametrize(
    "line, form",
    [
        ("Why do most launches fail?", "question"),
        ("We lost 40 percent in a week.", "number"),
        ("Three things changed that year.", "number"),
        ("Your first offer is too cheap.", "speaks to you"),
        ("Nobody tells you this about hiring.", "claim"),
        ("The biggest mistake was waiting.", "claim"),
    ],
)
def test_a_hook_is_named_by_its_form(line, form):
    assert form in clips.hook_forms(line)


def test_a_plain_line_has_no_hook():
    assert clips.hook_forms("We went to the office on the bus.") == []


def test_a_long_first_line_is_not_a_hook():
    long_line = "Most people get pricing wrong because they never sat down with a buyer and asked what the work was really worth to them at all."
    assert not clips.first_line(clips.read_lines(spoken_lines([long_line]), 0.4)[0]).passed


def test_a_clip_that_wanders_is_marked():
    wandering = ["Boats float on calm water.", "Bread needs flour and salt.", "Trains leave from platform nine.", "Cats sleep through winter days."]
    assert not clips.focus(clips.read_lines(spoken_lines(wandering), 0.4))[0].passed


def test_the_output_has_no_score(tmp_path):
    project = talk_project(tmp_path, THOUGHT)
    assert main(["clips", "find", "-p", str(project.root), "--length", "10-40"]) == 0
    saved = json.loads((project.root / "clips" / "candidates.json").read_text())
    text = json.dumps(saved).lower()
    assert "score" not in text and "viral" not in text
    row = saved["candidates"][0]
    assert {"number", "clip", "start", "end", "transcript", "why"} <= set(row)
    assert row["why"]


def test_find_writes_a_readable_list_and_names_the_rule(tmp_path, capsys):
    project = talk_project(tmp_path, THOUGHT)
    assert main(["clips", "find", "-p", str(project.root), "--length", "10-40"]) == 0
    text = (project.root / "clips" / "candidates.md").read_text()
    assert "data" in text and "rights" in text
    assert "--pick" in capsys.readouterr().out


def test_count_is_a_ceiling_and_a_short_list_says_so(tmp_path, capsys):
    project = talk_project(tmp_path, THOUGHT)
    assert main(["clips", "find", "-p", str(project.root), "--count", "9", "--length", "25-40"]) == 0
    assert "asked for 9" in capsys.readouterr().out


def test_nothing_fits_is_explained(tmp_path):
    project = talk_project(tmp_path, THOUGHT[:1])
    with pytest.raises(AltocueError, match="No stretch") as error:
        clips.find_candidates(project, count=3, length=(30, 60))
    assert "--length" in str(error.value.fix)


def test_a_candidate_stays_in_one_clip(tmp_path):
    project = talk_project(tmp_path, {"one": THOUGHT[:3], "two": THOUGHT[3:]})
    for candidate in clips.find_candidates(project, count=8, length=(5, 60)):
        words = project.words(candidate["clip"])
        assert words[candidate["first_word"]].start == candidate["start"]
        assert words[candidate["last_word"]].end == candidate["end"]


def test_find_never_opens_the_media(tmp_path, monkeypatch):
    project = talk_project(tmp_path, THOUGHT)
    monkeypatch.setattr(ffmpeg, "read_wav", lambda *a, **k: pytest.fail("find read the audio"))
    monkeypatch.setattr(ffmpeg, "extract_wav", lambda *a, **k: pytest.fail("find read the audio"))
    assert not Path(project.clips()[0].path).exists()
    assert clips.find_candidates(project, count=1, length=(10, 40))


def test_no_transcript_says_what_to_run(tmp_path):
    project = Project.create(tmp_path / "talk")
    project.save_clips([Clip(id="a", path="a.mp4", duration=9.0, fps=30, width=1920, height=1080)])
    with pytest.raises(AltocueError, match="transcribe"):
        clips.find_candidates(project, count=1, length=(10, 40))


def test_following_one_speaker(tmp_path):
    project = talk_project(tmp_path, THOUGHT)
    words = project.words("talk")
    middle = len(words) // 2
    for index, word in enumerate(words):
        word.speaker = "S1" if index < middle else "S2"
    project.save_words("talk", words, "test")
    found = clips.find_candidates(project, count=8, length=(5, 60), speaker="S2")
    assert found
    for candidate in found:
        assert candidate["start"] >= words[middle].start
        assert candidate["speakers"] == {"S2": 1.0}


def test_following_a_speaker_needs_speakers(tmp_path):
    project = talk_project(tmp_path, THOUGHT)
    with pytest.raises(AltocueError, match="--speakers"):
        clips.find_candidates(project, count=3, length=(5, 60), speaker="S2")


# Real media from here down. Each clip is generated, so nothing needs a model.


def beeping_clip(path: Path, seconds: float, beeps: list[float], *, gop: int = 30, rate: int = 30) -> Path:
    """A tone burst per word, on a moving picture, so a shifted transcript would show."""
    samples = np.zeros(int(16000 * seconds), dtype=np.float32)
    for at in beeps:
        first = int(at * 16000)
        samples[first : first + 3200] = 0.4 * np.sin(2 * np.pi * 440 * np.arange(len(samples[first : first + 3200])) / 16000)
    wav = ffmpeg.write_wav(path.with_suffix(".wav"), samples, 16000)
    args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate={rate}:duration={seconds}", "-i", str(wav)]
    args += ["-c:v", "libx264", "-g", str(gop), "-keyint_min", str(gop), "-bf", "2", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)]
    subprocess.run(args, check=True)
    return path


def media_project(tmp_path: Path, *, gop: int = 30) -> Project:
    words = spoken_lines([*THOUGHT, *RUN_ON, *THOUGHT], start=2.0)
    seconds = words[-1].end + 3
    source = beeping_clip(tmp_path / "talk.mp4", seconds, [w.start for w in words], gop=gop)
    assert main(["new", str(tmp_path / "talk"), "--input", str(source), "--aspect", "16:9"]) == 0
    project = Project.open(tmp_path / "talk")
    transcribing._save_words(project, "talk", classify(words), "test")
    return project


def onset(path: Path, after: float) -> float:
    wav = ffmpeg.extract_wav(path, path.with_name("onset.wav"), rate=16000)
    samples, rate = ffmpeg.read_wav(wav)
    loud = np.flatnonzero(np.abs(samples[int(after * rate) :]) > 0.1)
    return after + loud[0] / rate


def cut_one(project: Project, length: str = "20-40") -> Project:
    assert main(["clips", "find", "-p", str(project.root), "--length", length]) == 0
    assert main(["clips", "cut", "-p", str(project.root), "--pick", "1"]) == 0
    return Project.open(next((project.root / "clips").glob("01-*")))


def test_cut_makes_a_normal_project_without_transcribing(tmp_path, monkeypatch):
    project = media_project(tmp_path)
    monkeypatch.setattr(transcribing, "select", lambda *a, **k: pytest.fail("a clip must not transcribe again"))
    child = cut_one(project)
    assert child.size == (1080, 1920)
    assert (child.root / "brief.md").exists() and child.script_path.exists()
    edl = child.edl()
    assert cut.validate(child, edl) == []
    saved = json.loads((project.root / "clips" / "candidates.json").read_text())["candidates"][0]
    assert " ".join(cut.expected_words(child, edl)) == saved["transcript"]
    assert transcribing.done(child, child.clips()[0].id)


def test_a_clip_keeps_the_parent_clock_only_when_it_was_verified(tmp_path):
    project = media_project(tmp_path)
    path = project.words_dir / "talk.json"
    record = json.loads(path.read_text())
    assert record["source_clock"] == ffmpeg.SOURCE_CLOCK
    del record["source_clock"]
    path.write_text(json.dumps(record))
    child = cut_one(project)
    assert "source_clock" not in json.loads((child.words_dir / f"{child.clips()[0].id}.json").read_text())
    with pytest.raises(AltocueError):
        transcribing.done(child, child.clips()[0].id)


def test_the_words_still_land_on_the_sound(tmp_path):
    project = media_project(tmp_path, gop=75)
    child = cut_one(project)
    clip = child.clips()[0]
    words = child.words(clip.id)
    assert onset(Path(clip.path), words[10].start - 0.05) == pytest.approx(words[10].start, abs=0.03)


def test_the_cut_starts_on_a_keyframe_so_the_picture_is_never_touched(tmp_path):
    project = media_project(tmp_path, gop=75)
    child = cut_one(project)
    meta = json.loads((child.work / "clip.json").read_text())
    assert meta["offset"] == pytest.approx(round(meta["offset"] * 30 / 75) * 75 / 30, abs=0.04)
    assert meta["offset"] <= meta["start"]
    packets = ffmpeg.probe_json(child.clips()[0].path)["streams"][0]
    assert packets["codec_name"] == "h264"


def test_rotation_metadata_survives_the_copy(tmp_path):
    project = media_project(tmp_path)
    source = Path(project.clips()[0].path).resolve()
    rotated = tmp_path / "turned.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-display_rotation", "90", "-i", str(source), "-c", "copy", str(rotated)], check=True)
    assert main(["new", str(tmp_path / "turned"), "--input", str(rotated), "--aspect", "16:9"]) == 0
    other = Project.open(tmp_path / "turned")
    other.save_words("turned", project.words("talk"), "test")
    child = cut_one(other)
    assert abs(child.clips()[0].rotation) == 90


def test_settings_follow_the_clip(tmp_path):
    project = media_project(tmp_path)
    (project.root / "house.json").write_text('{"caption": {"style": "boxed"}}')
    text = (project.root / "altocue.toml").read_text()
    text = text.replace('name = "default"', 'name = "house.json"').replace("max_pause = 0.4", "max_pause = 0.3")
    text = text.replace("[transcribe.fix]        # names the transcript gets wrong, heard = written\n", '[transcribe.fix]\n"Pricing" = "Prices"\n')
    (project.root / "altocue.toml").write_text(text)
    child = cut_one(Project.open(project.root))
    assert child.setting("cut", "max_pause") == 0.3
    assert child.setting("output", "aspect") == "9:16"
    assert child.config["transcribe"]["fix"] == {"Pricing": "Prices"}
    from altocue import brand

    assert brand.load(child).caption["style"] == "boxed"


def test_aspect_can_be_chosen(tmp_path):
    project = media_project(tmp_path)
    assert main(["clips", "find", "-p", str(project.root), "--length", "20-40"]) == 0
    assert main(["clips", "cut", "-p", str(project.root), "--pick", "1", "--aspect", "1:1"]) == 0
    assert Project.open(next((project.root / "clips").glob("01-*"))).size == (1080, 1080)


def test_captions_and_framing_run_on_a_clip(tmp_path):
    project = media_project(tmp_path)
    child = cut_one(project)
    assert caption.build(child)
    framed = frame_project(child)
    assert all(segment.crop is not None for segment in framed.segments)


def test_a_clip_can_be_recut_from_its_own_script(tmp_path):
    project = media_project(tmp_path)
    child = cut_one(project)
    lines = [line for line in child.script_path.read_text().splitlines() if line.startswith("[L") and "~~" not in line]
    child.script_path.write_text("\n".join(reversed(lines[:2])))
    assert main(["cut", "-p", str(child.root), "--from-script"]) == 0
    assert cut.validate(child, child.edl()) == []


def test_only_the_stretch_is_copied(tmp_path, monkeypatch):
    project = media_project(tmp_path)
    source = Path(project.clips()[0].path)
    seen: list[str] = []
    real = ffmpeg.extract_wav
    monkeypatch.setattr(ffmpeg, "extract_wav", lambda path, *a, **k: seen.append(str(path)) or real(path, *a, **k))
    child = cut_one(project)
    assert seen and all(Path(path).resolve() != source.resolve() for path in seen)
    assert child.clips()[0].duration < project.clips()[0].duration / 2


def test_picking_again_keeps_the_clip_you_worked_on(tmp_path, capsys):
    project = media_project(tmp_path)
    child = cut_one(project)
    (child.root / "motion.json").write_text('{"items": []}')
    capsys.readouterr()
    assert main(["clips", "cut", "-p", str(project.root), "--pick", "1"]) == 0
    assert (child.root / "motion.json").exists()
    assert "Keeping" in capsys.readouterr().out


def test_a_pick_that_is_not_on_the_list_is_refused(tmp_path, capsys):
    project = media_project(tmp_path)
    assert main(["clips", "find", "-p", str(project.root), "--length", "20-40"]) == 0
    assert main(["clips", "cut", "-p", str(project.root), "--pick", "99"]) == 1
    assert "99" in capsys.readouterr().err


@pytest.mark.parametrize("pick", ["", "a", "1,,3", "0"])
def test_a_bad_pick_is_refused(pick):
    with pytest.raises(AltocueError):
        clips.parse_picks(pick, 8)


def test_cut_before_find_says_what_to_run(tmp_path, capsys):
    project = talk_project(tmp_path, THOUGHT)
    assert main(["clips", "cut", "-p", str(project.root), "--pick", "1"]) == 1
    assert "clips find" in capsys.readouterr().err


def test_candidates_from_an_older_transcript_are_refused(tmp_path, capsys):
    project = talk_project(tmp_path, THOUGHT)
    assert main(["clips", "find", "-p", str(project.root), "--length", "10-40"]) == 0
    project.save_words("talk", timed("Different words now."), "test")
    assert main(["clips", "cut", "-p", str(project.root), "--pick", "1"]) == 1
    assert "transcript changed" in capsys.readouterr().err


def test_a_codec_mov_cannot_hold_goes_in_mkv(tmp_path):
    webm = tmp_path / "talk.webm"
    args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=15:duration=3", "-f", "lavfi", "-i", "sine=frequency=220:duration=3"]
    made = subprocess.run([*args, "-c:v", "libvpx-vp9", "-b:v", "200k", "-c:a", "libopus", str(webm)], capture_output=True)
    if made.returncode:
        pytest.skip("this ffmpeg has no VP9 or Opus encoder")
    clip = ffmpeg.probe_clip(webm, clip_id="talk")
    dest, offset = clips.copy_stretch(clip, 1.0, 2.0, tmp_path)
    assert dest.suffix == ".mkv" and offset >= 0
    assert ffmpeg.probe_clip(dest).duration > 1.0
