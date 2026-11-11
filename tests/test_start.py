"""`altocue start` asks eight questions once, and `altocue new` starts every project from the answers.

No test here needs a terminal: the questions are asked through a stand-in for `input`.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

from altocue import brand, platforms, project, start
from altocue.cli import STAGES, main
from altocue.errors import AltocueError
from altocue.project import CONFIG, Project
from altocue.tomledit import set_key, set_keys, split_comment, toml_value

ROOT = Path(__file__).resolve().parents[1]
ENTER_EIGHT_TIMES = [""] * 8
DEFAULTS = {
    "kind": "short", "platform": "vertical", "brand": "house", "captions": "on", "sound": "subtle", "language": "en", "online": "none", "spend_cap_usd": 0.0,
    "checks": "cut",
}  # fmt: skip
# The starting values the kinds were given, as kind: aspect, platform, pace, punch-in, B-roll share, caption words, music under.
STARTING_VALUES = {
    "short": ("9:16", "vertical", "tight", 1.15, 0.3, 3, 15),
    "long": ("16:9", "youtube", "natural", 1.12, 0.3, 4, 18),
    "tutorial": ("16:9", "youtube", "natural", 1.0, 0.5, 4, 22),
    "interview": ("16:9", "youtube", "relaxed", 1.0, 0.15, 4, 22),
    "clip": ("9:16", "vertical", "natural", 1.12, 0.1, 3, 18),
    "launch": ("16:9", "youtube", "tight", 1.12, 0.5, 4, 15),
    "ad": ("9:16", "vertical", "tight", 1.15, 0.3, 3, 15),
    "testimonial": ("9:16", "vertical", "natural", 1.12, 0.2, 4, 20),
    "event": ("9:16", "vertical", "tight", 1.0, 0.5, 3, 15),
    "story": ("16:9", "youtube", "relaxed", 1.0, 0.3, 4, 20),
}


def typed(monkeypatch: pytest.MonkeyPatch, lines: list[str]) -> list[str]:
    """Stand in for a person at a terminal. Returns the prompts they were shown. Running out of lines is the end of input."""
    waiting, prompts = iter(lines), []

    def answer(prompt: str = "") -> str:
        prompts.append(prompt)
        try:
            return next(waiting)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr(start, "is_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", answer)
    return prompts


def nobody_there(monkeypatch: pytest.MonkeyPatch) -> None:
    def never(prompt: str = "") -> str:
        raise AssertionError(f"asked with no terminal: {prompt}")

    monkeypatch.setattr(start, "is_terminal", lambda: False)
    monkeypatch.setattr("builtins.input", never)


def default_file(aspect: str = "9:16") -> str:
    return project.DEFAULT_CONFIG.format(aspect=aspect)


def settings_of(folder: Path) -> dict:
    return tomllib.loads((folder / CONFIG).read_text())


# The questions


def test_start_is_a_stage_right_after_doctor():
    names = list(STAGES)
    assert names[names.index("doctor") + 1] == "start" and STAGES["start"][0] == "altocue.start"


def test_there_are_eight_questions_and_each_has_a_default_among_its_choices():
    asked = start.questions()
    assert [question.id for question in asked] == ["kind", "platform", "brand", "captions", "sound", "language", "online", "checks"]
    for question in asked:
        assert question.default in [choice.value for choice in question.choices]
        assert all(choice.label and choice.explanation for choice in question.choices)
    assert start.defaults() == DEFAULTS


def test_the_checks_question_says_what_always_stops():
    checks = next(question for question in start.questions() if question.id == "checks")
    for words in ("Spending", "uploading", "failed gate"):
        assert words in checks.question
    assert "still stop" in next(choice for choice in checks.choices if choice.value == "end").explanation


def test_enter_eight_times_gives_the_defaults(monkeypatch, capsys):
    prompts = typed(monkeypatch, ENTER_EIGHT_TIMES)
    assert main(["start"]) == 0
    assert len(prompts) == 8
    assert start.saved() == DEFAULTS
    out = capsys.readouterr().out
    assert "1 of 8. What do you make most?" in out and "8 of 8." in out
    assert "altocue demo" in out and "altocue new" in out, "it ends on the next command"


def test_every_choice_is_numbered_with_one_line_of_explanation(monkeypatch, capsys):
    typed(monkeypatch, ENTER_EIGHT_TIMES)
    main(["start"])
    out = capsys.readouterr().out
    for kind in start.kinds().values():
        assert re.search(rf"^  \d+\. {re.escape(kind.label)}: {re.escape(kind.about)}", out, re.MULTILINE)
    assert "  2. Subtle: Light effects on the graphics and no music  (default)" in out


def test_answers_are_typed_as_a_number_a_name_or_a_value(monkeypatch, tmp_path):
    kit = tmp_path / "kit"
    kit.mkdir()
    typed(monkeypatch, ["2", "youtube", str(kit), "3", "music", "DE", "2", "end"])
    assert main(["start"]) == 0
    assert start.saved() == {
        "kind": "long", "platform": "youtube", "brand": str(kit.resolve()), "captions": "off", "sound": "music", "language": "de", "online": "free",
        "spend_cap_usd": 0.0, "checks": "end",
    }  # fmt: skip


def test_saved_answers_are_the_next_runs_defaults(monkeypatch, tmp_path, capsys):
    kit = tmp_path / "kit"
    kit.mkdir()
    typed(monkeypatch, ["4", "", "2", str(kit), "2", "1", "fr", "4", "25", "3"])
    assert main(["start"]) == 0
    first = start.saved()
    assert first["kind"] == "interview" and first["platform"] == "youtube", "Enter on the place takes the one the new kind plays in"
    assert first["brand"] == str(kit.resolve()) and first["online"] == "paid" and first["spend_cap_usd"] == 25.0
    capsys.readouterr()
    prompts = typed(monkeypatch, [""] * 9)  # eight questions and the cap
    assert main(["start"]) == 0
    assert start.saved() == first
    assert "press Enter for 4: " in prompts[0] and "Press Enter for 25: " in prompts[7]
    assert f"My brand kit: {kit.resolve()}  (default)" in capsys.readouterr().out


def test_a_wrong_answer_is_asked_again(monkeypatch, capsys):
    typed(monkeypatch, ["99", "vlog", "3", "", "", "", "", "english", "es", "", ""])
    assert main(["start"]) == 0
    assert start.saved()["kind"] == "tutorial" and start.saved()["language"] == "es"
    out = capsys.readouterr().out
    assert "Type a number from 1 to 10." in out and "is not a language code" in out


def test_answers_that_stop_early_save_nothing(monkeypatch, capsys):
    typed(monkeypatch, ["2", "", ""])
    assert main(["start"]) == 1
    assert start.saved() is None and not start.profile_path().exists()
    assert "nothing was saved" in capsys.readouterr().err


def test_no_terminal_means_no_prompt_and_no_file(monkeypatch, capsys):
    nobody_there(monkeypatch)
    assert main(["start"]) == 0
    out = capsys.readouterr().out
    assert len(out.strip().splitlines()) == 1 and "--answer" in out and "--questions" in out and "--yes" in out
    assert not start.profile_path().exists()


def test_the_questions_print_as_json_and_ask_nothing(monkeypatch, capsys):
    nobody_there(monkeypatch)
    assert main(["start", "--questions"]) == 0
    found = json.loads(capsys.readouterr().out)
    assert found["answered"] is False and len(found["questions"]) == 8
    for entry in found["questions"]:
        assert {"id", "question", "choices", "default", "answer"} <= set(entry) and entry["answer"] is None
        assert all(set(choice) == {"value", "label", "explanation"} for choice in entry["choices"])
    by_id = {entry["id"]: entry for entry in found["questions"]}
    assert [choice["value"] for choice in by_id["kind"]["choices"]] == list(start.kinds())
    assert by_id["platform"]["default_by_kind"]["long"] == "youtube"
    assert by_id["brand"]["other"] and by_id["language"]["other"]
    assert by_id["online"]["follow_up"]["id"] == "spend_cap_usd" and by_id["online"]["follow_up"]["default"] == 0
    assert found["always_stops"] == ["spending money", "uploading a file", "a failed gate"]
    assert not start.profile_path().exists(), "listing the questions saves nothing"

    assert main(["start", "--answer", "kind=tutorial", "--answer", "checks=end"]) == 0
    capsys.readouterr()
    assert main(["start", "--questions"]) == 0
    found = json.loads(capsys.readouterr().out)
    by_id = {entry["id"]: entry for entry in found["questions"]}
    assert found["answered"] is True
    assert by_id["kind"]["answer"] == "tutorial" and by_id["platform"]["answer"] == "youtube" and by_id["checks"]["answer"] == "end"


def test_answers_are_set_without_a_prompt(monkeypatch, capsys):
    nobody_there(monkeypatch)
    assert main(["start", "--answer", "kind=ad", "--answer", "sound=full", "--answer", "language=pt"]) == 0
    assert start.saved() == {**DEFAULTS, "kind": "ad", "sound": "full", "language": "pt"}
    assert main(["start", "--answer", "captions=file"]) == 0
    assert start.saved() == {**DEFAULTS, "kind": "ad", "sound": "full", "language": "pt", "captions": "file"}, "one answer changes and the rest stay"


@pytest.mark.parametrize(
    ("answer", "named"),
    [
        ("colour=red", "kind, platform, brand, captions, sound, language, online, spend_cap_usd, checks"),
        ("kind=vlog", "short, long, tutorial, interview, clip, launch, ad, testimonial, event, story"),
        ("platform=myspace", "vertical, reels, tiktok, shorts, youtube, linkedin, square"),
        ("captions=maybe", "on, file, off"),
        ("sound=loud", "voice, subtle, music, full"),
        ("online=everything", "none, free, review, paid"),
        ("checks=never", "every, cut, draft, end"),
        ("language=english", "en, es, fr"),
        ("spend_cap_usd=lots", "plain number of US dollars"),
        ("spend_cap_usd=-5", "plain number of US dollars"),
        ("spend_cap_usd=inf", "plain number of US dollars"),
        ("brand=/no/such/kit", "house"),
        ("kind", "key=value"),
    ],
)
def test_a_bad_answer_names_the_good_ones_and_saves_nothing(answer, named, capsys):
    assert main(["start", "--answer", "sound=music", "--answer", answer]) == 1
    assert named in capsys.readouterr().err
    assert not start.profile_path().exists()


def test_yes_takes_the_defaults_then_keeps_what_is_saved(monkeypatch, capsys):
    nobody_there(monkeypatch)
    assert main(["start", "--yes"]) == 0
    assert start.saved() == DEFAULTS
    main(["start", "--answer", "kind=story"])
    assert main(["start", "--yes"]) == 0
    assert start.saved()["kind"] == "story" and start.saved()["platform"] == "vertical", "a saved place is an answer, and a later kind does not move it"


def test_the_profile_is_one_small_file_that_can_be_edited_by_hand():
    main(["start", "--yes"])
    path = start.profile_path()
    assert path.name == "profile.toml" and path.parent == start.home()
    assert tomllib.loads(path.read_text()) == {"version": 1, **DEFAULTS}
    lines = [line for line in path.read_text().splitlines() if line and not line.startswith("#")]
    assert [line.split(" = ")[0] for line in lines] == ["version", *start.KEYS]
    assert all("#" in line for line in lines[1:]), "each answer says what else it may be"
    path.write_text(path.read_text().replace('sound = "subtle"', 'sound = "voice"'))
    assert start.saved()["sound"] == "voice"


def test_the_home_folder_can_be_moved(monkeypatch, tmp_path):
    monkeypatch.setenv("ALTOCUE_HOME", str(tmp_path / "elsewhere"))
    assert start.profile_path() == tmp_path / "elsewhere" / "profile.toml"
    monkeypatch.delenv("ALTOCUE_HOME")
    assert start.profile_path() == Path.home() / ".altocue" / "profile.toml"


def test_show_says_it_in_plain_words_or_as_json(capsys):
    assert main(["start", "--show"]) == 0
    out = capsys.readouterr().out
    assert "Nobody has answered yet" in out and "Short talking video" in out and "always stop" in out
    assert main(["start", "--show", "--json"]) == 0
    found = json.loads(capsys.readouterr().out)
    assert found["answered"] is False and found["answers"] == DEFAULTS
    assert not start.profile_path().exists(), "showing saves nothing"

    main(["start", "--answer", "kind=tutorial", "--answer", "online=review", "--answer", "checks=draft"])
    capsys.readouterr()
    assert main(["start", "--show", "--json"]) == 0
    found = json.loads(capsys.readouterr().out)
    assert found["answered"] is True and found["answers"]["checks"] == "draft" and found["aspect"] == "16:9"
    assert found["playbook"] == "Tutorial and screen recording"
    assert found["always_stops"] == ["spending money", "uploading a file", "a failed gate"]
    assert set(found["in_words"]) == {question.id for question in start.questions()}
    assert main(["start", "--show"]) == 0
    out = capsys.readouterr().out
    assert str(start.profile_path()) in out and "Tutorial or screen recording" in out and "Show me a draft, then ask" in out


def test_saving_can_answer_in_json(capsys):
    assert main(["start", "--json", "--answer", "kind=clip"]) == 0
    found = json.loads(capsys.readouterr().out)
    assert found["answered"] is True and found["answers"]["kind"] == "clip"


def test_reset_removes_the_profile(capsys):
    main(["start", "--yes"])
    assert start.profile_path().exists()
    assert main(["start", "--reset"]) == 0
    assert not start.profile_path().exists() and start.saved() is None
    assert main(["start", "--reset"]) == 0, "nothing to remove is not a failure"
    assert "nothing to remove" in capsys.readouterr().out


def test_the_kinds_are_listed_with_one_line_each(capsys):
    assert main(["start", "--kinds"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert [line.split()[0] for line in lines] == list(start.kinds())
    assert all(kind.label in line and kind.about in line for kind, line in zip(start.kinds().values(), lines, strict=True))


def test_a_profile_broken_by_hand_says_how_to_mend_it_and_start_still_runs(monkeypatch, capsys):
    main(["start", "--yes"])
    path = start.profile_path()
    path.write_text(path.read_text().replace('checks = "cut"', 'checks = "sometimes"'))
    with pytest.raises(AltocueError, match="bad answer") as caught:
        start.saved()
    assert "every, cut, draft, end" in caught.value.fix and "altocue start" in caught.value.fix
    assert start.saved(strict=False)["checks"] == "cut"
    path.write_text("kind = [unfinished")
    with pytest.raises(AltocueError, match="does not parse"):
        start.saved()
    typed(monkeypatch, ENTER_EIGHT_TIMES)
    assert main(["start"]) == 0
    assert start.saved() == DEFAULTS


# Spending


def test_a_spend_cap_is_never_raised_without_a_typed_number(monkeypatch):
    main(["start", "--answer", "online=paid"])
    assert start.saved()["online"] == "paid" and start.saved()["spend_cap_usd"] == 0.0, "paid with no number still blocks spending"
    main(["start", "--reset"])
    main(["start", "--answer", "spend_cap_usd=40"])
    assert start.saved()["spend_cap_usd"] == 0.0, "a cap means nothing unless paid generation was chosen"
    main(["start", "--answer", "online=paid", "--answer", "spend_cap_usd=40"])
    assert start.saved()["spend_cap_usd"] == 40.0
    main(["start", "--yes"])
    assert start.saved()["spend_cap_usd"] == 40.0, "a number the user typed is kept"
    main(["start", "--answer", "online=review"])
    assert start.saved()["spend_cap_usd"] == 0.0, "leaving paid generation takes the cap away"
    main(["start", "--answer", "online=paid"])
    assert start.saved()["spend_cap_usd"] == 0.0, "and coming back does not bring it back"


def test_enter_at_the_cap_keeps_spending_blocked(monkeypatch, capsys):
    typed(monkeypatch, ["", "", "", "", "", "", "4", "", ""])
    assert main(["start"]) == 0
    assert start.saved()["online"] == "paid" and start.saved()["spend_cap_usd"] == 0.0
    assert "nothing can spend until you type a number" in capsys.readouterr().out


def test_a_cap_written_by_hand_beside_another_answer_is_not_read():
    main(["start", "--yes"])
    path = start.profile_path()
    path.write_text(path.read_text().replace("spend_cap_usd = 0.0", "spend_cap_usd = 500.0"))
    assert start.saved()["online"] == "none" and start.saved()["spend_cap_usd"] == 0.0


@pytest.mark.parametrize("online", ["none", "free", "review", "paid"])
def test_a_new_project_can_only_spend_a_number_the_user_typed(online):
    profile = {**DEFAULTS, "online": online}
    _, changes = start.starting_point(kind=None, aspect=None, profile=profile)
    assert "generate" not in changes, f"{online} with no cap leaves spend_cap_usd at 0"
    made = tomllib.loads(set_keys(default_file(), changes))
    assert made["generate"]["spend_cap_usd"] == 0.0 and made["generate"]["provider"] == ""
    assert "allow_upload" not in made["review"], "an answer is never a yes to an upload"
    _, changes = start.starting_point(kind=None, aspect=None, profile={**profile, "spend_cap_usd": 30.0})
    assert changes.get("generate") == ({"spend_cap_usd": 30.0} if online == "paid" else None)


# Kinds of video


def test_every_kind_is_complete_and_names_a_real_shape():
    found = start.kinds()
    assert list(found) == list(STARTING_VALUES)
    playbooks = (ROOT / "skills" / "altocue-craft" / "references" / "playbooks.md").read_text()
    for name, kind in found.items():
        assert kind.name == name and kind.label and kind.about
        assert f"## {kind.playbook}\n" in playbooks, f"{name}: no playbook called {kind.playbook}"
        assert {table: sorted(keys) for table, keys in kind.settings.items()} == {
            "output": ["aspect", "platform"], "cut": ["pace"], "frame": ["punch_in"], "broll": ["share"], "caption": ["max_words"], "audio": ["music_under"],
        }  # fmt: skip
        assert kind.aspect in project.SIZES and project.size_for(kind.aspect)
        assert start.SHAPES[kind.platform] == kind.aspect, f"{name}: {kind.platform} does not play {kind.aspect}"
        got = (kind.aspect, kind.platform, kind.settings["cut"]["pace"], kind.settings["frame"]["punch_in"], kind.settings["broll"]["share"])
        assert (*got, kind.settings["caption"]["max_words"], kind.settings["audio"]["music_under"]) == STARTING_VALUES[name]
        assert kind.settings["cut"]["pace"] in ("tight", "natural", "relaxed")
        assert 1.0 <= kind.settings["frame"]["punch_in"] <= 1.3 and 0 <= kind.settings["broll"]["share"] <= 1


def test_the_kinds_file_says_its_values_are_starting_points():
    text = start.KINDS_FILE.read_text()
    assert "starting point" in text and "None of them is measured" in text


def test_every_place_to_publish_is_a_real_platform_with_its_own_shape():
    offered = [choice.value for choice in next(question for question in start.questions() if question.id == "platform").choices]
    assert offered == list(start.SHAPES)
    assert set(platforms.PLATFORMS) <= set(offered), "a platform Altocue knows is missing from the question"
    for name, record in platforms.PLATFORMS.items():
        assert start.SHAPES[name] == record.aspect
    assert set(offered) - set(platforms.PLATFORMS) <= {"vertical"}, "only `vertical` may be ahead of platforms.py"


@pytest.mark.parametrize("name", list(STARTING_VALUES))
def test_every_kind_lays_over_the_default_file_and_disturbs_nothing_else(name, monkeypatch):
    monkeypatch.setitem(platforms.PLATFORMS, "vertical", platforms.PLATFORMS["reels"])
    aspect, changes = start.starting_point(kind=name, aspect=None, profile=None)
    kind = start.kinds()[name]
    assert aspect == kind.aspect
    before = default_file(aspect)
    after = set_keys(before, changes)
    made = tomllib.loads(after)
    assert made["format"] == {"kind": name}
    for table, keys in kind.settings.items():
        assert {key: made[table][key] for key in keys} == keys
    wanted = tomllib.loads(before)
    for table, keys in changes.items():
        wanted.setdefault(table, {}).update(keys)
    assert made == wanted, "no other setting moved"

    # Written against whatever the default file holds, so a key that another change adds to it is still covered.
    names = {key for keys in changes.values() for key in keys}

    def sets_one(line: str) -> bool:
        return line.lstrip("# ").split(" ")[0] in names

    old, new = before.splitlines(), after.splitlines()
    rest = iter(new)
    assert all(line in rest for line in old if not sets_one(line)), "every other line is kept byte for byte, in order"
    assert all(sets_one(line) or line.strip("[]") in changes for line in new if line and line not in old), "nothing is added but the keys and their tables"
    for line in old:
        if sets_one(line) and "#" in line.lstrip("# "):
            assert "#" + line.lstrip("# ").split("#", 1)[1] in after, "a changed line keeps its comment"
    assert "# keep text inside that app's safe zone" in after and "# platform" not in after


# Setting keys in place

SAMPLE = """\
# A project file.

[output]
aspect = "9:16"     # any W:H
fps = 0
# platform = "reels"    # keep text inside the safe zone

[cut]
max_pause = 0.4         # seconds
remove_fillers = true

# Sound comes next.
[audio]
loudness = -14.0        # integrated LUFS, or a name
"""


def test_a_value_is_replaced_and_its_comment_stays_in_its_column():
    made = set_key(SAMPLE, "cut", "max_pause", 0.6)
    assert "max_pause = 0.6         # seconds\n" in made
    assert made.replace("0.6", "0.4") == SAMPLE, "nothing else moved"
    assert 'aspect = "4:5"      # any W:H\n' in set_key(SAMPLE, "output", "aspect", "4:5")
    longer = set_key(SAMPLE, "output", "aspect", "1920:1080")
    assert 'aspect = "1920:1080"  # any W:H\n' in longer, "a longer value still leaves room before its comment"
    assert set_key(SAMPLE, "cut", "remove_fillers", False).count("remove_fillers = false\n") == 1


def test_a_commented_out_key_becomes_live():
    made = set_key(SAMPLE, "output", "platform", "vertical")
    assert 'platform = "vertical"   # keep text inside the safe zone\n' in made
    assert "# platform" not in made
    assert tomllib.loads(made)["output"]["platform"] == "vertical"
    again = set_key(made, "output", "platform", "youtube")
    assert again.count("platform") == 1 and tomllib.loads(again)["output"]["platform"] == "youtube", "the live line is the one changed after that"


def test_a_sentence_in_a_comment_is_not_taken_for_a_key():
    text = "[cut]\n# pace = how much air stays between phrases\nmax_pause = 0.4\n"
    made = set_key(text, "cut", "pace", "tight")
    assert made == '[cut]\n# pace = how much air stays between phrases\nmax_pause = 0.4\npace = "tight"\n'


def test_a_missing_key_goes_at_the_end_of_its_table():
    made = set_key(SAMPLE, "cut", "pace", "tight")
    assert 'remove_fillers = true\npace = "tight"\n\n# Sound comes next.\n[audio]\n' in made, "before the blank line and the comment that leads into the next table"
    assert made.replace('pace = "tight"\n', "") == SAMPLE
    last = set_key(SAMPLE, "audio", "music_under", 18)
    assert last == SAMPLE + "music_under = 18\n"
    assert set_key(SAMPLE.rstrip("\n"), "audio", "music_under", 18) == last, "a file with no final newline gets one"
    middle = set_key(SAMPLE, "output", "codec", "h264")
    assert '# platform = "reels"    # keep text inside the safe zone\ncodec = "h264"\n\n[cut]\n' in middle, "a commented-out key is part of its table"


def test_a_missing_table_goes_at_the_end_of_the_file():
    made = set_key(SAMPLE, "format", "kind", "short")
    assert made == SAMPLE + '\n[format]\nkind = "short"\n'
    assert set_key("", "format", "kind", "short") == '[format]\nkind = "short"\n'
    assert set_key("a = 1", "format", "kind", "short") == 'a = 1\n\n[format]\nkind = "short"\n'
    dotted = set_key(SAMPLE, "transcribe.fix", "Altoque", "Altocue")
    assert tomllib.loads(dotted)["transcribe"]["fix"] == {"Altoque": "Altocue"}


def test_several_keys_at_once_and_the_result_parses():
    changes = {"output": {"platform": "youtube", "fps": 30}, "cut": {"pace": "relaxed"}, "caption": {"max_words": 4}, "format": {"kind": "long"}}
    made = set_keys(SAMPLE, changes)
    parsed = tomllib.loads(made)
    wanted = tomllib.loads(SAMPLE)
    for table, keys in changes.items():
        wanted.setdefault(table, {}).update(keys)
    assert parsed == wanted
    for comment in ("# A project file.", "# any W:H", "# keep text inside the safe zone", "# seconds", "# Sound comes next.", "# integrated LUFS, or a name"):
        assert comment in made
    assert set_keys(SAMPLE, {}) == SAMPLE and set_keys(made, changes) == made, "setting the same values again changes nothing"


def test_a_hash_inside_a_value_is_not_a_comment():
    assert split_comment('"a # b"   # note') == ('"a # b"   ', "# note")
    assert split_comment("'a # b' # note") == ("'a # b' ", "# note")
    assert split_comment("[1, 2]") == ("[1, 2]", "")
    assert split_comment(r'"say \"hi\" # there" # real') == (r'"say \"hi\" # there" ', "# real")
    text = '[brand]\nname = "A # B"   # the name\n'
    assert set_key(text, "brand", "name", "C # D") == '[brand]\nname = "C # D"   # the name\n'


def test_values_are_written_as_toml():
    assert [toml_value(value) for value in (True, False, 3, 1.0, 0.15, "9:16", ["a", "b"], [])] == ["true", "false", "3", "1.0", "0.15", '"9:16"', '["a", "b"]', "[]"]
    tricky = 'She said "hi"\\ and\tleft\n\x7f'
    assert tomllib.loads(f"k = {toml_value(tricky)}")["k"] == tricky
    assert tomllib.loads(set_key("", "t", "a key", "v"))["t"] == {"a key": "v"}
    with pytest.raises(AltocueError):
        toml_value(float("nan"))
    with pytest.raises(AltocueError):
        toml_value({"a": 1})


def test_windows_line_endings_are_kept():
    text = SAMPLE.replace("\n", "\r\n")
    made = set_keys(text, {"cut": {"pace": "tight", "max_pause": 0.5}, "format": {"kind": "ad"}})
    assert "\n" not in made.replace("\r\n", "") and tomllib.loads(made)["cut"] == {"max_pause": 0.5, "remove_fillers": True, "pace": "tight"}


@pytest.mark.parametrize(
    ("text", "changes"),
    [
        ("[cut\nmax_pause = 0.4\n", {"cut": {"pace": "tight"}}),  # the file is already broken
        ('[broll]\nlibrary = [\n  "a",\n]\n', {"broll": {"library": []}}),  # a value over several lines
        ('output.aspect = "9:16"\n', {"output": {"platform": "reels"}}),  # the table is written as dotted keys
        ('cut = "fast"\n', {"cut": {"pace": "tight"}}),  # not a table at all
    ],
)
def test_a_file_it_cannot_change_safely_is_refused_with_a_fix(text, changes):
    with pytest.raises(AltocueError) as caught:
        set_keys(text, changes)
    assert caught.value.fix and "by hand" in caught.value.fix


# `altocue new`


def test_new_with_no_profile_writes_todays_file_byte_for_byte(clip, tmp_path, capsys):
    assert start.saved() is None
    assert main(["new", str(tmp_path / "plain"), "--input", str(clip)]) == 0
    # a tall film with no platform named starts in the built-in vertical safe zone, and nothing else differs
    made = project.Project.create(tmp_path / "made", aspect="9:16", platform="vertical")
    assert (tmp_path / "plain" / CONFIG).read_bytes() == (made.root / CONFIG).read_bytes()
    assert settings_of(tmp_path / "plain")["output"]["platform"] == "vertical"
    assert main(["new", str(tmp_path / "wide"), "--input", str(clip), "--aspect", "16:9"]) == 0
    assert (tmp_path / "wide" / CONFIG).read_bytes() == default_file("16:9").encode()
    out = capsys.readouterr().out
    assert "Kind:" not in out and "altocue start" not in out, "the pointer at `altocue start` is for a terminal only"


def test_new_points_at_start_once_on_a_terminal(clip, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    assert main(["new", str(tmp_path / "plain"), "--input", str(clip)]) == 0
    assert capsys.readouterr().out.count("altocue start") == 1
    main(["start", "--yes"])
    capsys.readouterr()
    assert main(["new", str(tmp_path / "second"), "--input", str(clip)]) == 0
    assert "Tip:" not in capsys.readouterr().out


def test_new_with_a_kind_writes_the_preset_and_the_kind(clip, tmp_path, capsys):
    assert main(["new", str(tmp_path / "talk"), "--input", str(clip), "--kind", "interview"]) == 0
    made = settings_of(tmp_path / "talk")
    assert made["format"] == {"kind": "interview"}
    assert made["output"]["aspect"] == "16:9" and made["output"]["platform"] == "youtube"
    assert made["cut"]["pace"] == "relaxed" and made["frame"]["punch_in"] == 1.0 and made["broll"]["share"] == 0.15
    assert made["caption"]["max_words"] == 4 and made["audio"]["music_under"] == 22
    assert made["transcribe"]["language"] == "en" and made["generate"]["spend_cap_usd"] == 0.0
    opened = Project.open(tmp_path / "talk")
    assert opened.size == (1920, 1080) and start.kind_of(opened).name == "interview" and platforms.zone_of(opened)
    out = capsys.readouterr().out
    assert 'Kind: interview, at 16:9. Its playbook is "Interview and conversation"' in out


def test_new_starts_from_the_profile_and_never_prompts(clip, tmp_path, monkeypatch, capsys):
    main(["start", "--answer", "kind=tutorial", "--answer", "language=de", "--answer", "online=paid", "--answer", "spend_cap_usd=20"])
    nobody_there(monkeypatch)
    calls = []
    monkeypatch.setattr(brand, "ingest", lambda project, kit, house=False: calls.append((kit, house)) or {"brand": "default", "kept": []})
    capsys.readouterr()
    assert main(["new", str(tmp_path / "lesson"), "--input", str(clip)]) == 0
    made = settings_of(tmp_path / "lesson")
    assert made["format"] == {"kind": "tutorial"} and made["output"] == {"aspect": "16:9", "fps": 0, "platform": "youtube"}
    assert made["transcribe"]["language"] == "de" and made["generate"]["spend_cap_usd"] == 20.0 and made["generate"]["provider"] == ""
    assert made["frame"]["punch_in"] == 1.0 and made["broll"]["share"] == 0.5
    assert calls == [(None, True)], "the house look in the profile is chosen on purpose"
    assert "Kind: tutorial, at 16:9, from your profile." in capsys.readouterr().out


def test_the_place_in_the_profile_shapes_the_kind_in_the_profile(clip, tmp_path):
    main(["start", "--answer", "kind=long", "--answer", "platform=linkedin"])
    assert main(["new", str(tmp_path / "feed"), "--input", str(clip)]) == 0
    made = settings_of(tmp_path / "feed")
    assert made["output"]["aspect"] == "4:5" and made["output"]["platform"] == "linkedin" and made["format"]["kind"] == "long"
    assert made["cut"]["pace"] == "natural", "the rest of the kind still applies"


def test_a_flag_beats_the_profile(clip, tmp_path, monkeypatch):
    kit = tmp_path / "kit"
    kit.mkdir()
    main(["start", "--answer", "kind=long", "--answer", "platform=linkedin", "--answer", "language=fr", "--answer", f"brand={kit}"])
    calls = []
    monkeypatch.setattr(brand, "ingest", lambda project, kit, house=False: calls.append((kit, house)) or {"brand": "default", "kept": []})

    assert main(["new", str(tmp_path / "square"), "--input", str(clip), "--aspect", "1:1"]) == 0
    made = settings_of(tmp_path / "square")
    assert made["output"]["aspect"] == "1:1" and "platform" not in made["output"], "LinkedIn's safe zone is not a square's"
    assert made["format"]["kind"] == "long" and made["transcribe"]["language"] == "fr"
    assert calls == [(kit.resolve(), False)], "with no flag the kit in the profile is read"

    assert main(["new", str(tmp_path / "advert"), "--input", str(clip), "--kind", "ad", "--brand", "house"]) == 0
    made = settings_of(tmp_path / "advert")
    assert made["format"]["kind"] == "ad" and made["output"]["aspect"] == "9:16", "another kind brings its own shape"
    assert made["cut"]["pace"] == "tight" and made["transcribe"]["language"] == "fr", "the language is still the user's"
    assert calls[-1] == (None, True)

    assert main(["new", str(tmp_path / "both"), "--input", str(clip), "--kind", "story", "--aspect", "4:3"]) == 0
    made = settings_of(tmp_path / "both")
    assert made["output"]["aspect"] == "4:3" and "platform" not in made["output"] and made["format"]["kind"] == "story"


def test_vertical_is_written_only_once_platforms_knows_it(clip, tmp_path, monkeypatch):
    main(["start", "--yes"])
    if "vertical" not in platforms.PLATFORMS:
        assert main(["new", str(tmp_path / "before"), "--input", str(clip)]) == 0
        made = settings_of(tmp_path / "before")
        assert made["output"] == {"aspect": "9:16", "fps": 0} and made["format"]["kind"] == "short"
        assert platforms.zone_of(Project.open(tmp_path / "before")) is None, "a name platforms.py does not know would stop every later stage"
        monkeypatch.setitem(platforms.PLATFORMS, "vertical", platforms.PLATFORMS["reels"])
    assert main(["new", str(tmp_path / "after"), "--input", str(clip)]) == 0
    assert settings_of(tmp_path / "after")["output"]["platform"] == "vertical"
    assert platforms.zone_of(Project.open(tmp_path / "after"))


def test_a_brand_kit_that_has_gone_does_not_stop_a_project(clip, tmp_path, capsys):
    kit = tmp_path / "kit"
    kit.mkdir()
    main(["start", "--answer", f"brand={kit}"])
    kit.rmdir()
    capsys.readouterr()
    assert main(["new", str(tmp_path / "video"), "--input", str(clip)]) == 0
    assert "is not there now, so no brand was read" in capsys.readouterr().out


def test_a_broken_profile_stops_new_before_anything_is_made(clip, tmp_path, capsys):
    start.home().mkdir(parents=True, exist_ok=True)
    start.profile_path().write_text('kind = "vlog"\n')
    assert main(["new", str(tmp_path / "video"), "--input", str(clip)]) == 1
    assert not (tmp_path / "video").exists()
    err = capsys.readouterr().err
    assert "bad answer" in err and "altocue start" in err


# The skill


def test_the_setup_skill_is_short_and_says_the_three_stops():
    page = (ROOT / "skills" / "altocue-setup" / "SKILL.md").read_text()
    head = page.split("---")[1]
    assert re.search(r"^name: altocue-setup$", head, re.MULTILINE) and "Use when" in head
    assert re.search(r"^license: MIT$", head, re.MULTILINE) and re.search(r"^compatibility: ", head, re.MULTILINE)
    assert len(page.split()) <= 700
    for needed in ("altocue start --questions", "altocue start --show --json", "altocue start --yes", "two batches of four", "Never ask twice", "Never block"):
        assert needed in page
    for answer in ("`checks`", "`captions`", "`sound`", "`brand`", "`online`"):
        assert answer in page
    for stop in ("Spending money", "Uploading a file", "failed gate"):
        assert stop in page
    saved = re.search(r"altocue start((?: --answer \w+=\w+)+)", page).group(1)
    answers = dict(pair.split("=") for pair in saved.replace("--answer", "").split())
    assert list(answers) == [question.id for question in start.questions()]
    assert {key: start.checked(key, value) for key, value in answers.items()} == {key: value for key, value in DEFAULTS.items() if key != "spend_cap_usd"}
