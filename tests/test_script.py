"""Script mode: a cut built from chosen lines, with every edge still on real speech."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from test_cut import add_clip, make_project, silent_project, spans_of, spoken, timed, voiced_project

from altocue import cut
from altocue.cli import main
from altocue.cut import expected_words, map_words, propose, validate
from altocue.cut.script import from_script, script_text, sentences, transcript_mark, write_script
from altocue.errors import AltocueError
from altocue.models import Word
from altocue.project import Project

TALK = "First we plan the work. Then we film the idea. Next we cut the pauses. Finally we ship the thing."


def lines_of(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("[L")]


def written(project: Project) -> Path:
    project.save_edl(propose(project))
    return write_script(project, project.edl())


def rewrite(path: Path, lines: list[str]) -> None:
    head = [line for line in path.read_text().splitlines() if not line.startswith("[L")]
    path.write_text("\n".join([*head, *lines]) + "\n")


def test_sentences_end_on_punctuation():
    words = timed("One two. Three four? Five six!")
    assert sentences(words) == [(0, 1), (2, 3), (4, 5)]


def test_sentences_do_not_end_on_a_title_or_a_decimal():
    words = timed("Dr. Lee said 3.5 percent. Then stopped.")
    assert sentences(words) == [(0, 4), (5, 6)]


def test_a_long_pause_ends_a_sentence_with_no_punctuation():
    words = [*timed("no punctuation here"), *timed("still none", start=5.0)]
    assert sentences(words) == [(0, 2), (3, 4)]


def test_a_quoted_ending_still_ends_the_sentence():
    assert sentences(timed('He said "stop." Then left.')) == [(0, 2), (3, 4)]


def test_the_script_is_one_readable_line_per_sentence(tmp_path):
    project = silent_project(tmp_path, timed(TALK))
    text = written(project).read_text()
    lines = lines_of(text)
    assert len(lines) == 4
    assert lines[1].endswith("Then we film the idea.")
    assert re.match(r"\[L0002 0:01\.\d\] ", lines[1])
    assert "data" in text.lower()


def test_words_already_cut_are_struck_through(tmp_path):
    project = silent_project(tmp_path, timed("Well um that works. We ship it."))
    assert "Well ~~um~~ that works." in lines_of(written(project).read_text())[0]


def test_keeping_every_line_changes_nothing(tmp_path):
    project = silent_project(tmp_path, timed(TALK))
    path = written(project)
    before = expected_words(project, project.edl())
    edl = from_script(project, path)
    assert expected_words(project, edl) == before
    assert len(edl.segments) == len(propose(project).segments)


def test_a_deleted_line_goes_and_the_record_says_why(tmp_path):
    project = silent_project(tmp_path, timed(TALK))
    path = written(project)
    lines = lines_of(path.read_text())
    rewrite(path, [lines[0], lines[2], lines[3]])
    edl = from_script(project, path)
    assert spoken(project, edl) == "First we plan the work. Next we cut the pauses. Finally we ship the thing."
    assert validate(project, edl) == []
    assert any(item.kind == "manual" and "script" in item.evidence for item in edl.removed)


def test_a_struck_line_goes_too(tmp_path):
    project = silent_project(tmp_path, timed(TALK))
    path = written(project)
    lines = lines_of(path.read_text())
    tag, body = lines[1].split("] ", 1)
    rewrite(path, [lines[0], f"{tag}] ~~{body}~~", lines[2], lines[3]])
    assert "film" not in spoken(project, from_script(project, path))


def test_moved_lines_play_in_the_new_order(tmp_path):
    project = silent_project(tmp_path, timed(TALK))
    path = written(project)
    lines = lines_of(path.read_text())
    rewrite(path, [lines[3], lines[0], lines[1], lines[2]])
    edl = from_script(project, path)
    assert spoken(project, edl) == "Finally we ship the thing. First we plan the work. Then we film the idea. Next we cut the pauses."
    assert validate(project, edl) == []
    assert [s.start for s in edl.segments] == sorted([s.start for s in edl.segments], key=lambda t: (t < 3, t))
    assert [w["text"] for w in map_words(project, edl)][:2] == ["Finally", "we"]


def test_neighbouring_sentences_split_when_they_are_moved_apart(tmp_path):
    words = timed(TALK, step=0.2)
    project = voiced_project(tmp_path, words)
    path = written(project)
    lines = lines_of(path.read_text())
    assert len(project.edl().segments) == 1
    rewrite(path, [lines[1], lines[0], lines[2], lines[3]])
    edl = from_script(project, path)
    assert len(edl.segments) >= 3
    assert validate(project, edl) == []


def test_edges_still_snap_to_speech_and_the_frame_grid(tmp_path):
    words = [*timed("We start here today.", start=1.0), *timed("Then we stop there.", start=4.0)]
    project = voiced_project(tmp_path, words, fps=30)
    path = written(project)
    lines = lines_of(path.read_text())
    rewrite(path, [lines[1], lines[0]])
    edl = from_script(project, path)
    assert validate(project, edl) == []
    for segment in edl.segments:
        assert abs(segment.start * 30 - round(segment.start * 30)) < 1e-3
        assert abs(segment.end * 30 - round(segment.end * 30)) < 1e-3
    first_spoken = next(w for w in words if w.text == "Then")
    assert edl.segments[0].start == pytest.approx(first_spoken.start - 0.1, abs=0.05)


def test_a_word_can_go_from_inside_a_line(tmp_path):
    project = silent_project(tmp_path, timed("We plan the whole work now."))
    path = written(project)
    tag = lines_of(path.read_text())[0].split("] ")[0]
    rewrite(path, [f"{tag}] We plan the work now."])
    assert spoken(project, from_script(project, path)) == "We plan the work now."


def test_taking_the_strike_off_brings_a_word_back(tmp_path):
    project = silent_project(tmp_path, timed("Well um that works."))
    path = written(project)
    line = lines_of(path.read_text())[0]
    assert "~~um~~" in line
    rewrite(path, [line.replace("~~um~~", "um")])
    edl = from_script(project, path)
    assert "um" in expected_words(project, edl)
    assert any(item.kind == "filler" and item.decision == "keep" for item in edl.removed)


def test_a_new_word_is_refused_and_named(tmp_path):
    project = silent_project(tmp_path, timed(TALK))
    path = written(project)
    lines = lines_of(path.read_text())
    rewrite(path, [lines[0].replace("plan", "invent"), *lines[1:]])
    with pytest.raises(AltocueError, match='L0001.*"invent"') as error:
        from_script(project, path)
    assert error.value.fix


def test_a_line_with_no_id_is_found_by_its_words(tmp_path):
    project = silent_project(tmp_path, timed(TALK))
    path = written(project)
    lines = lines_of(path.read_text())
    rewrite(path, [lines[0], "Next we cut the pauses."])
    assert spoken(project, from_script(project, path)) == "First we plan the work. Next we cut the pauses."


def test_a_retyped_line_may_skip_a_filler(tmp_path):
    project = silent_project(tmp_path, timed("Well um that works. We ship it."))
    path = written(project)
    rewrite(path, ["Well that works."])
    assert spoken(project, from_script(project, path)) == "Well that works."


def test_a_line_said_twice_must_be_named_by_id(tmp_path):
    project = silent_project(tmp_path, timed("We ship it. Then rest. We ship it."))
    path = written(project)
    rewrite(path, ["We ship it."])
    with pytest.raises(AltocueError, match="2 times"):
        from_script(project, path)


def test_a_line_not_in_the_recording_is_refused_with_the_closest(tmp_path):
    project = silent_project(tmp_path, timed(TALK))
    path = written(project)
    rewrite(path, ["Next we cut the pause."])
    with pytest.raises(AltocueError, match="not in the recording") as error:
        from_script(project, path)
    assert "Next we cut the pauses." in str(error.value.fix)


def test_a_line_cannot_play_twice(tmp_path):
    project = silent_project(tmp_path, timed(TALK))
    path = written(project)
    lines = lines_of(path.read_text())
    rewrite(path, [lines[0], lines[1], lines[0]])
    with pytest.raises(AltocueError, match="L0001"):
        from_script(project, path)


def test_a_line_can_be_split_and_its_halves_moved(tmp_path):
    project = silent_project(tmp_path, timed("We plan the work and we ship the thing. Then rest."))
    path = written(project)
    tag = lines_of(path.read_text())[0].split("] ")[0]
    rewrite(path, [f"{tag}] and we ship the thing.", f"{tag}] We plan the work"])
    assert spoken(project, from_script(project, path)) == "and we ship the thing. We plan the work"


def test_an_empty_script_is_refused(tmp_path):
    project = silent_project(tmp_path, timed(TALK))
    path = written(project)
    rewrite(path, [])
    with pytest.raises(AltocueError, match="keeps nothing"):
        from_script(project, path)


def test_a_script_for_another_transcript_is_refused(tmp_path):
    project = silent_project(tmp_path, timed(TALK))
    path = written(project)
    project.save_words("a", [Word(text="Changed.", start=0.0, end=0.3)], "test")
    with pytest.raises(AltocueError, match="transcript changed"):
        from_script(project, path)


def test_a_missing_script_says_how_to_make_one(tmp_path):
    project = silent_project(tmp_path, timed(TALK))
    project.save_edl(propose(project))
    with pytest.raises(AltocueError, match="No script") as error:
        from_script(project, project.script_path)
    assert "--write-script" in str(error.value.fix)


def test_a_second_script_replaces_the_first(tmp_path):
    project = silent_project(tmp_path, timed(TALK))
    path = written(project)
    lines = lines_of(path.read_text())
    rewrite(path, lines[:2])
    project.save_edl(from_script(project, path))
    rewrite(path, lines)
    assert "ship" in spoken(project, from_script(project, path))


def test_lines_move_between_clips(tmp_path):
    one, two = timed("We start here."), timed("We end there.")
    project = make_project(tmp_path, {"a": (add_clip(tmp_path, "a", 2.0, None), one), "b": (add_clip(tmp_path, "b", 2.0, None), two)})
    path = written(project)
    lines = lines_of(path.read_text())
    rewrite(path, [lines[1], lines[0]])
    edl = from_script(project, path)
    assert [s.clip for s in edl.segments] == ["b", "a"]
    assert spoken(project, edl) == "We end there. We start here."


def test_a_clip_with_no_speech_has_a_line_of_its_own(tmp_path):
    talk = timed("We start here.")
    project = make_project(tmp_path, {"a": (add_clip(tmp_path, "a", 2.0, None), talk), "quiet": (add_clip(tmp_path, "quiet", 3.0, None), [])})
    path = written(project)
    lines = lines_of(path.read_text())
    assert any("no speech" in line for line in lines)
    rewrite(path, [lines[1], lines[0]])
    assert [s.clip for s in from_script(project, path).segments] == ["quiet", "a"]
    rewrite(path, [lines[0]])
    assert [s.clip for s in from_script(project, path).segments] == ["a"]


def test_a_reordered_cut_writes_back_in_play_order(tmp_path):
    project = silent_project(tmp_path, timed(TALK))
    path = written(project)
    lines = lines_of(path.read_text())
    rewrite(path, [lines[2], lines[0]])
    edl = from_script(project, path)
    project.save_edl(edl)
    again = lines_of(script_text(project, edl))
    assert [line.split("] ", 1)[1] for line in again[:2]] == [lines[2].split("] ", 1)[1], lines[0].split("] ", 1)[1]]
    assert "~~Then we film the idea.~~" in again[2]
    write_script(project, edl)
    assert spoken(project, from_script(project, path)) == spoken(project, edl)


def test_the_mark_follows_the_words(tmp_path):
    project = silent_project(tmp_path, timed(TALK))
    mark = transcript_mark(project)
    project.save_words("a", timed("Other words."), "test")
    assert transcript_mark(project) != mark


def test_cut_writes_a_script_once_and_leaves_an_edited_one_alone(tmp_path, capsys):
    project = silent_project(tmp_path, timed(TALK))
    assert main(["cut", "--project", str(project.root)]) == 0
    assert project.script_path.exists()
    project.script_path.write_text("edited by hand")
    assert main(["cut", "--project", str(project.root)]) == 0
    assert project.script_path.read_text() == "edited by hand"
    assert "script.md" in capsys.readouterr().out


def test_write_script_replaces_and_keeps_the_old_copy(tmp_path):
    project = silent_project(tmp_path, timed(TALK))
    assert main(["cut", "--project", str(project.root)]) == 0
    project.script_path.write_text("edited by hand")
    assert main(["cut", "--project", str(project.root), "--write-script"]) == 0
    assert "[L0001" in project.script_path.read_text()
    assert (project.work / "script-previous.md").read_text() == "edited by hand"


def test_from_script_on_the_command_line(tmp_path, capsys):
    project = silent_project(tmp_path, timed(TALK))
    assert main(["cut", "--project", str(project.root)]) == 0
    lines = lines_of(project.script_path.read_text())
    rewrite(project.script_path, [lines[3], lines[1]])
    assert main(["cut", "--project", str(project.root), "--from-script"]) == 0
    assert spoken(project, project.edl()) == "Finally we ship the thing. Then we film the idea."
    assert (project.work / "edl-previous.json").exists()
    assert "Moved" in (project.work / "cut-report.md").read_text()
    assert "script" in capsys.readouterr().out


def test_a_named_script_file_works(tmp_path):
    project = silent_project(tmp_path, timed(TALK))
    assert main(["cut", "--project", str(project.root)]) == 0
    other = tmp_path / "picks.md"
    other.write_text("Next we cut the pauses.\n")
    assert main(["cut", "--project", str(project.root), "--from-script", str(other)]) == 0
    assert spoken(project, project.edl()) == "Next we cut the pauses."


def test_spans_helper_is_not_shadowed():
    assert spans_of(timed("a b")) == [(0.0, 0.3), (0.3, 0.6)]
    assert cut.propose is propose
