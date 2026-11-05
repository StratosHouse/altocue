"""A skill is only as good as what it names. A flag that does not exist sends an agent into a wall, so every name is checked here."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import re
import tomllib
from pathlib import Path

import pytest

from altocue import library, overlays, transitions
from altocue.cli import STAGES
from altocue.models import Segment
from altocue.motion import formats
from altocue.project import DEFAULT_CONFIG

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
PAGES = sorted(SKILLS.glob("*/SKILL.md"))
NOTES = sorted(SKILLS.glob("*/references/*.md"))
EVERY = PAGES + NOTES
OWN_SKILLS = {path.parent.name for path in PAGES}

MOST_WORDS_PAGE = 1500
MOST_WORDS_NOTE = 2000
LABELS = {"evidence", "source", "thumb"}

# A stage cli.py already registers, whose module lands from a branch still open. Named by stage only, so no flag goes unchecked:
# when the module arrives its flags are read like any other stage's. Add nothing here for a stage that has no branch.
ARRIVING_STAGES: set[str] = set()
# Skills other branches are writing. A note may point at one. Creating one here would collide with its owner.
ARRIVING_SKILLS: set[str] = set()
# Names a skill invents as an example, in place of something the user brings. Altocue reads none of them by name.
EXAMPLE_FILES = {
    "a.mov",
    "b.mov",
    "approved_claims.json",
    "bed.mp3",
    "brand.json",
    "clip.mp4",
    "desk.mp4",
    "decisions.json",
    "door.wav",
    "keyboard-01.wav",
    "soft-hit-02.wav",
    "export.csv",
    "Inter-Variable.ttf",
    "logo.png",
    "photo.jpg",
    "rows.csv",
    "show.cube",
    "street.mp4",
    "shot-1.mp4",
}
# Words an agent must never carry from a skill to a user. Set by the owner: Altocue never launches or scripts a third-party application.
OTHER_EDITORS = re.compile(r"(?i:Premiere|DaVinci|Final Cut|FCPXML|altocue export|hand(?:ing|s|ed)? (?:\w+ ){0,2}off to an editor|editor handoff)|\bResolve\b")

FENCE = re.compile(r"^```[^\n]*\n(.*?)^```", re.DOTALL | re.MULTILINE)
SPAN = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
COMMAND = re.compile(r"(?<![\w/.:-])altocue\s+([a-z][^#|;&]*)")
SETTING = re.compile(r"^\[([a-z.]+)\]\s+([a-z_]+)")
BARE_FLAG = re.compile(r"(?<![\w-])--[a-z][a-z-]*")
TYPE_VALUE = re.compile(r'"type":\s*"([\w-]+)"')
PATHISH = re.compile(r"^[\w./-]+\.(?:json|md|toml|txt|jpg|png|mp4|mov|csv|cube|srt|vtt|html|mp3|wav|ttf)$")
NAMEISH = re.compile(r"^[a-z][a-z0-9]*(?:[_-][a-z0-9]+)+$")
AMERICAN = re.compile(r"\b(?!(?:re)?sized?\b|prized?\b|seized?\b)\w+(?:ize|ized|izing|ization)\b")
SEGMENT_ID = re.compile(r"^s\d{3}$")
CUTDOWN = re.compile(r"^final-\d+x\d+\.mp4$")  # `altocue film render --cutdowns` writes final-<W>x<H>.mp4 for each aspect, built in film.py


def source_text() -> str:
    found = list((ROOT / "src" / "altocue").rglob("*.py")) + list((ROOT / "src" / "altocue").rglob("*.toml"))
    found += [ROOT / "src" / "altocue" / "data" / "sfx" / "sounds.json", ROOT / "agents" / "editor-in-chief.md"]
    found += list((ROOT / "src" / "altocue" / "data" / "films").glob("*.json"))  # film types name their brief fields, such as gauge_note
    found += list((ROOT / "src" / "altocue" / "data" / "compositions").glob("*.html"))  # bundled compositions, by name
    return "\n".join(path.read_text(errors="replace") for path in found)


SOURCE = source_text()


def code_of(text: str) -> tuple[list[str], list[str]]:
    """Fenced blocks and inline spans. Only these name things an agent would type."""
    return FENCE.findall(text), SPAN.findall(FENCE.sub("", text))


def commands(text: str) -> list[list[str]]:
    """Every `altocue ...`, a shell continuation joined, as words after `altocue`."""
    blocks, spans = code_of(text)
    lines = [line for block in blocks for line in block.replace("\\\n", " ").splitlines()] + spans
    return [found.group(1).split() for line in lines for found in COMMAND.finditer(line)]


def parser_for(stage: str) -> argparse.ArgumentParser | None:
    """A stage's real arguments, or None while its module is on another branch."""
    module = STAGES[stage][0]
    if importlib.util.find_spec(module) is None:
        return None
    parser = argparse.ArgumentParser(prog=stage)
    importlib.import_module(module).add_arguments(stage, parser)
    return parser


def subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    return next((dict(action.choices) for action in parser._actions if isinstance(action, argparse._SubParsersAction)), {})


def actions_named(parser: argparse.ArgumentParser) -> tuple[str, ...]:
    """Some stages take their action as a plain positional with choices, such as `altocue recipes list`."""
    plain = [action for action in parser._actions if not action.option_strings and action.choices]
    return next((tuple(action.choices) for action in plain if not isinstance(action, argparse._SubParsersAction)), ())


def options_of(parser: argparse.ArgumentParser) -> dict[str, argparse.Action]:
    return dict(parser._option_string_actions)


def every_flag() -> set[str]:
    flags: set[str] = set()
    for stage in STAGES:
        parser = parser_for(stage)
        if parser is None:
            continue
        flags |= set(options_of(parser))
        for sub in subcommands(parser).values():
            flags |= set(options_of(sub))
    return flags


def placeholder(word: str) -> bool:
    return not word or word[0] in "<\"'[" or word[-1] in ">:"


def check_command(words: list[str], stages: dict[str, argparse.ArgumentParser | None]) -> list[str]:
    """What is wrong with one command, in plain words. Empty means an agent can type it."""
    stage = words[0]
    if stage not in STAGES:
        return [f"altocue {stage}: no such stage"]
    parser = stages.setdefault(stage, parser_for(stage))
    if parser is None:
        return [] if stage in ARRIVING_STAGES else [f"altocue {stage}: registered in cli.py, but its module is not in this checkout"]
    rest, problems = words[1:], []
    branches, actions = subcommands(parser), actions_named(parser)
    known = options_of(parser)
    leading = next((word for word in rest if not word.startswith("-")), "")
    if branches and leading and not placeholder(leading):
        if leading not in branches:
            problems.append(f"altocue {stage} {leading}: no such action")
        else:
            known = {**known, **options_of(branches[leading])}
    if actions and leading and not placeholder(leading) and leading not in actions:
        problems.append(f"altocue {stage} {leading}: no such action")
    for index, word in enumerate(rest):
        if not word.startswith("-") or len(word) < 2 or word[1].isdigit():
            continue
        flag = word.split("=")[0]
        action = known.get(flag)
        if action is None:
            problems.append(f"altocue {stage} {flag}: no such flag")
            continue
        following = rest[index + 1] if index + 1 < len(rest) else ""
        if action.choices and following and not following.startswith("-") and not placeholder(following) and following not in action.choices:
            problems.append(f"altocue {stage} {flag} {following}: not a choice")
    return problems


def known_types() -> set[str]:
    # `footage` is the storyboard's own item for a real clip in a film. storyboard.py and motion/scenes.py read it by name.
    return set(formats.load()) | formats.CORE_TYPES | set(transitions.NAMES) | set(overlays.KINDS) | set(shader_names()) | {"footage"}


def shader_names() -> set[str]:
    return {row["id"] for row in library.current()["entries"] if row["id"].startswith("gl-")}


def catalog_ids() -> set[str]:
    return {row["id"] for row in library.current()["entries"]}


def removed_segments(words: list[str]) -> list[str]:
    """The ids after `altocue cut --remove`, up to the next flag."""
    if words[0] != "cut" or "--remove" not in words:
        return []
    named = words[words.index("--remove") + 1 :]
    return [word for word in named[: next((i for i, w in enumerate(named) if w.startswith("-")), len(named))] if not placeholder(word)]


def problems_in(text: str, *, flags: set[str], stages: dict[str, argparse.ArgumentParser | None]) -> list[str]:
    """Every name in one page that this checkout does not have."""
    problems: list[str] = []
    blocks, spans = code_of(text)
    runs = commands(text)
    for words in runs:
        problems += check_command(words, stages)
        if words[:2] in (["library", "show"], ["library", "add"]) and len(words) > 2 and not placeholder(words[2]) and words[2] not in catalog_ids():
            problems.append(f"altocue {words[0]} {words[1]} {words[2]}: not in the catalog")
        problems += [f"altocue {words[0]} --remove {word}: segment ids look like s007" for word in removed_segments(words) if not SEGMENT_ID.fullmatch(word)]
    for span in spans:
        if COMMAND.search(span):
            continue  # already read against its own stage, which is the stricter check
        for flag in BARE_FLAG.findall(span):
            if flag not in flags:
                problems.append(f"{flag}: no stage has this flag")
    for kind in TYPE_VALUE.findall("\n".join(blocks) + "\n" + "\n".join(spans)):
        if kind not in known_types():
            problems.append(f'"type": "{kind}": no such format, transition or overlay')
    return problems


def formats_section(text: str) -> list[str]:
    """The ids listed under a page's `## Formats` heading."""
    found = re.search(r"^## Formats.*?(?=^## |\Z)", text, re.DOTALL | re.MULTILINE)
    return [span for span in SPAN.findall(found.group(0)) if re.fullmatch(r"[a-z][a-z0-9_]*", span)] if found else []


def settings_named(text: str) -> list[tuple[str, str]]:
    return [(found.group(1), found.group(2)) for span in code_of(text)[1] if (found := SETTING.match(span))]


def default_settings() -> dict[str, dict[str, object]]:
    return tomllib.loads(DEFAULT_CONFIG.format(aspect="9:16"))


def test_every_skill_was_found():
    assert len(PAGES) >= 16 and {"altocue", "altocue-direct", "altocue-review"} <= OWN_SKILLS


@pytest.mark.parametrize("path", EVERY, ids=lambda p: str(p.relative_to(SKILLS)))
def test_a_skill_only_names_what_exists(path):
    problems = problems_in(path.read_text(), flags=every_flag(), stages={})
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("path", EVERY, ids=lambda p: str(p.relative_to(SKILLS)))
def test_a_skill_names_real_files(path):
    blocks, spans = code_of(path.read_text())
    named = [span for span in spans if PATHISH.fullmatch(span)]
    named += [word.strip("\"',") for block in blocks for word in block.split() if PATHISH.fullmatch(word.strip("\"',"))]
    missing = []
    for name in named:
        here = path.parent / name
        beside = SKILLS / name
        base = Path(name).name
        if not (here.exists() or beside.exists() or base in EXAMPLE_FILES or base in SOURCE or CUTDOWN.fullmatch(base)):
            missing.append(name)
    assert not missing, f"Nothing in this checkout makes or reads: {', '.join(sorted(set(missing)))}"


@pytest.mark.parametrize("path", EVERY, ids=lambda p: str(p.relative_to(SKILLS)))
def test_a_skill_links_real_pages(path):
    links = re.findall(r"\]\((?!https?:|#)([^)#]+)", path.read_text())
    assert not [link for link in links if not (path.parent / link).exists()]


@pytest.mark.parametrize("path", EVERY, ids=lambda p: str(p.relative_to(SKILLS)))
def test_a_skill_names_real_ids(path):
    """A skill name, gate, setting, look, recipe or field: anything hyphenated or snake case must be something the code knows."""
    spans = code_of(path.read_text())[1]
    stray = [span for span in spans if NAMEISH.fullmatch(span) and span not in OWN_SKILLS | ARRIVING_SKILLS | catalog_ids() and span not in SOURCE]
    assert not stray, f"Nothing in this checkout knows: {', '.join(sorted(set(stray)))}"


@pytest.mark.parametrize("path", EVERY, ids=lambda p: str(p.relative_to(SKILLS)))
def test_a_skill_names_real_settings(path):
    defaults = default_settings()
    stray = []
    for section, key in settings_named(path.read_text()):
        group: object = defaults
        for part in section.split("."):
            group = group.get(part, {}) if isinstance(group, dict) else {}
        if key not in (group or {}) and f'"{section}", "{key}"' not in SOURCE:
            stray.append(f"[{section}] {key}")
    assert not stray, f"No such setting: {', '.join(stray)}"


def test_a_format_list_only_names_formats():
    page = (SKILLS / "altocue-motion" / "SKILL.md").read_text()
    listed = formats_section(page)
    assert listed, "altocue-motion lost its format list"
    assert not [name for name in listed if name not in formats.load() and name not in formats.CORE_TYPES]


def test_the_fix_tables_name_edit_fields_that_exist():
    fields = set(Segment.__dataclass_fields__)
    for name in ("altocue-review", "altocue-direct"):
        page = (SKILLS / name / "SKILL.md").read_text()
        found = re.search(r"^## (?:Fix|Revision).*?(?=^## |\Z)", page, re.DOTALL | re.MULTILINE)
        assert found, f"{name} has no fix vocabulary"
        rows = [line for line in found.group(0).splitlines() if line.startswith("| ") and "---" not in line][1:]
        assert len(rows) >= 6, f"{name}: a vocabulary this short will not get used"
        for row in rows:
            assert "`" in row, f"{name}: a fix must name the command or field that makes it: {row}"
        for word in re.findall(r"`edl\.json` `(\w+)`", found.group(0)):
            assert word in fields, f"{name}: edl.json has no {word}"


@pytest.mark.parametrize("path", EVERY, ids=lambda p: str(p.relative_to(SKILLS)))
def test_nothing_points_at_a_third_party_editor(path):
    found = OTHER_EDITORS.search(path.read_text())
    assert not found, f"{path}: names {found.group(0)}"


@pytest.mark.parametrize("path", EVERY, ids=lambda p: str(p.relative_to(SKILLS)))
def test_house_style(path):
    text = path.read_text()
    assert "\u2014" not in text and "\u2013" not in text, f"{path}: em or en dash"
    prose = SPAN.sub("", FENCE.sub("", text))
    assert not AMERICAN.search(prose), f"{path}: American spelling: {AMERICAN.search(prose)}"
    labels = set(re.findall(r"\[([a-z]+)\](?!\()", prose))
    assert labels <= LABELS, f"{path}: unknown evidence label {labels - LABELS}"


@pytest.mark.parametrize("path", PAGES, ids=lambda p: p.parent.name)
def test_a_skill_loads_quickly(path):
    assert len(path.read_text().split()) <= MOST_WORDS_PAGE, f"{path}: move detail into references/"


@pytest.mark.parametrize("path", NOTES, ids=lambda p: p.name)
def test_a_reference_stays_a_reference(path):
    assert len(path.read_text().split()) <= MOST_WORDS_NOTE, f"{path}: split it"


@pytest.mark.parametrize("path", PAGES, ids=lambda p: p.parent.name)
def test_a_skill_says_when_to_use_it(path):
    head = path.read_text().split("---")[1]
    assert re.search(rf"^name: {path.parent.name}$", head, re.MULTILINE)
    assert re.search(r"^description: .*\bUse (when|after|before)\b", head, re.MULTILINE)


# The checks above are only worth anything if they catch a planted fault.


def planted(text: str) -> list[str]:
    return problems_in(text, flags=every_flag(), stages={})


OPEN_A_FILE = re.compile(r"\b[Oo]pen (?:`[^`]+\.(?:jpe?g|png|gif|html|mp4|mov)`|the (?:frames|contact sheet|thumbnail sheet)|a frame)")


@pytest.mark.parametrize("path", EVERY, ids=lambda p: str(p.relative_to(SKILLS)))
def test_a_skill_never_tells_the_agent_to_open_an_image(path):
    """On a Mac an agent told to open a file may run `open`, which starts Preview. Altocue never opens a window."""
    found = OPEN_A_FILE.search(path.read_text())
    assert not found, f"{path}: {found.group(0) if found else ''}. Say view it with your own file-reading tool."


def test_a_missing_stage_is_caught():
    assert planted("Run `altocue export --to fcpxml`.") == ["altocue export: no such stage"]


def test_a_stage_whose_module_is_gone_is_caught(monkeypatch):
    monkeypatch.setitem(STAGES, "ghost", ("altocue.ghost", "Not here"))
    assert planted("Run `altocue ghost`.") == ["altocue ghost: registered in cli.py, but its module is not in this checkout"]


def test_a_missing_flag_is_caught_on_its_own_stage():
    assert planted("Run `altocue cut --tighter`.") == ["altocue cut --tighter: no such flag"]
    assert planted("Run `altocue cut --look warm`.") == ["altocue cut --look: no such flag"]


def test_a_wrong_segment_id_is_caught():
    assert planted('`altocue cut --remove s7 --reason "drags"`') == ["altocue cut --remove s7: segment ids look like s007"]
    assert planted('`altocue cut --remove s007 s012 --reason "drags"`') == []


def test_a_real_spelling_is_not_called_american():
    assert not AMERICAN.search("a human-sized reaction, resized once") and AMERICAN.search("it will normalize the level")


def test_a_bad_choice_is_caught():
    assert planted("`altocue clean --deess loud`") == ["altocue clean --deess loud: not a choice"]


def test_a_missing_action_is_caught():
    assert planted("`altocue library find bar`") == ["altocue library find: no such action"]
    assert planted("`altocue recipes price upscale`") == ["altocue recipes price: no such action"]


def test_a_flag_in_a_shell_continuation_is_caught():
    block = "```bash\naltocue generate --import a.mp4 \\\n  --bogus 1\n```\n"
    assert planted(block) == ["altocue generate --bogus: no such flag"]


def test_a_bare_flag_is_caught():
    assert planted("Add `--no-such-thing` to it.") == ["--no-such-thing: no stage has this flag"]


def test_a_missing_format_is_caught():
    block = '```json\n{ "items": [{ "type": "word_slamm", "text": "Hi" }] }\n```\n'
    assert planted(block) == ['"type": "word_slamm": no such format, transition or overlay']
    assert planted("`altocue library show word_slamm`") == ["altocue library show word_slamm: not in the catalog"]


def test_real_names_pass():
    block = '```json\n{ "items": [{ "type": "word_slam", "text": "Hi" }], "transitions": [{ "type": "dissolve" }], "overlay": [{ "type": "grain" }] }\n```\n'
    text = block + "Run `altocue library show word_slam`, then `altocue clean --deess strong -p .`, `altocue learn notes -p .` and `altocue recipes list`."
    assert planted(text) == []


def test_a_format_list_catches_a_stranger():
    assert formats_section("## Formats\n\n- `word_slam`, `nonsense_format`\n\n## Next\n\n- `other`\n") == ["word_slam", "nonsense_format"]
