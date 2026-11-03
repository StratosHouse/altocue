"""Every number the README shows must match the tool as it ships, the way `altocue film check` holds a film to its numbers.

A count the code can give is read from the code here, never typed into a test. A number with no count behind it, such as a
disk estimate, is listed below with its reason. Any other number in the README's prose fails, so a new claim cannot slip
in unchecked. The commands it names must exist, and its pictures must be absolute links to files in this repo.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest
from canaries import CANARIES, CONTROLS
from test_skills import OTHER_EDITORS, every_flag, problems_in

from altocue import doctor, film, learn, library, looks, project, transitions
from altocue.generate import recipes
from altocue.motion import formats, shaders
from altocue.qa import gates
from altocue.review import rounds, rubric

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text()
INSTALL = (ROOT / "INSTALL.md").read_text()
RAW = "https://raw.githubusercontent.com/StratosHouse/altocue/main/"

# What the README calls each kind of library entry, in its table.
KIND_LABELS = {
    "motion": "Motion", "transition": "Transitions", "3d": "3D", "map": "Maps", "overlay": "Overlays", "stock": "Stock video",
    "icon": "Icons", "data": "Data", "illustration": "Illustration", "music": "Music", "sfx": "Sound effects",
    "generate": "Generation", "photo": "Photos", "lut": "Looks", "font": "Fonts",
}  # fmt: skip

# Numbers with no count in the code behind them. Each says where it comes from, so a change is a decision, not a drift.
FIXED = [
    r"\b\d+:\d+\b",  # aspect ratios such as 9:16, which name a shape rather than count anything
    r"\b1of10\b|\bCC0\b|\b3D\b",  # names: a product, a licence and a kind of graphic
    r"^\d+\. ",  # the steps of a numbered list
    r"16 GB of memory",  # a comfortable machine, from running it
    r"about 3\.5 GB of disk: 2\.5 GB for the speech model",  # the speech model, the tool and its renderer, measured on a Mac
    r"730 KB demo take",  # the published demo take, pinned by hash in demo.py
    r"about 35 MB of models",  # the two speaker models fetched by setup --speakers
    r"peaks at about 3\.2 GB and refuses to start with under about 4 GB free",  # measured by the clips stream
    r"October 23, 2026",  # the date Higgsfield's terms changed
    r"ffmpeg other than 8\.1",  # the ffmpeg the looks were proven on
    r"transitions at 60 fps",  # a frame rate nobody has tried transitions at
    r"over 5 minutes has gone through, memory has not been watched past 4 minutes",  # the longest recordings tried so far
]


def bundled() -> dict:
    """The catalog that ships, read the way `altocue library stats` reads it, with any catalog downloaded to this machine set aside."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(library, "updated_path", lambda: ROOT / "no-downloaded-catalog.json")
        return library.current()


STATS = library.stats(bundled())
FORMATS = formats.load()
LAUNCH = json.loads((Path(film.__file__).parent / "data" / "films" / "launch.json").read_text())
USABLE = [recipe for recipe in recipes.load_recipes().values() if not recipe.excluded]
RENDERER = json.loads((Path(film.__file__).parent / "data" / "renderer" / "package.json").read_text())["dependencies"]["hyperframes"]


def most_priced_date() -> str:
    """The date most known recipe prices were read on, as the README writes a date."""
    found = Counter(recipe.as_of for recipe in USABLE if recipe.basis != "unknown").most_common(1)[0][0]
    day = date.fromisoformat(found)
    return f"{day:%B} {day.day}, {day.year}"


# Each claim: a pattern in the README, and what each of its groups must read now.
CLAIMS: dict[str, tuple[str, Callable[[], list[object]]]] = {
    "formats": (r"\b(\d+) original formats", lambda: [len(FORMATS)]),
    "groups": (r"formats in (\d+) groups", lambda: [len({fmt.group for fmt in FORMATS.values()})]),
    "film looks": (r"\b(\d+) film looks", lambda: [len(looks.IDS)]),
    "widest aspect": (r"up to (\d+):1 either way", lambda: [int(project.WIDEST)]),
    "node": (r"Node (\d+) or later", lambda: [doctor.NODE_MINIMUM]),
    "renderer": (r"HyperFrames (\d+\.\d+\.\d+)", lambda: [RENDERER]),
    "library": (
        r"holds (\d+) entries: (\d+) built in, (\d+) to fetch, (\d+) live searches and (\d+) links",
        lambda: [STATS["entries"], *(STATS["tiers"].get(tier, 0) for tier in ("built-in", "fetch", "search", "link"))],
    ),
    "sources": (r"judges (\d+) sources and refuses (\d+) items", lambda: [STATS["sources"], STATS["refused"]]),
    **{f"kind {kind}": (rf"^\| {re.escape(label)} \| (\d+) \|", lambda kind=kind: [STATS["kinds"].get(kind, 0)]) for kind, label in KIND_LABELS.items()},
    "transitions": (r"\b(\d+) built-in transitions", lambda: [len(transitions.XFADE)]),
    "shaders": (r"\b(\d+) shader transitions bundled", lambda: [len(shaders.bundled())]),
    "launch master": (r"a (\d+:\d+) master at (\d+) fps", lambda: [LAUNCH["aspect"], LAUNCH["fps"]]),
    "launch shapes": (r"picks a (\d+), (\d+), (\d+) or (\d+) second shape", lambda: sorted(LAUNCH["variants"], key=int, reverse=True)),
    "film types": (r"There are (\d+) film types", lambda: [len(film.ORDER)]),
    "gates": (r"\*\*Fact gates in code\.\*\* (\d+) of them", lambda: [len(gates.gate_ids())]),
    "floor": (r"nothing under (\d+),", lambda: [rubric.FLOOR]),
    "ship at": (r"weighted score of ([\d.]+) or more", lambda: [rubric.SHIP_AT]),
    "rounds": (r"After (\d+) rounds of fixes", lambda: [rounds.MAX_FIX_ROUNDS]),
    "canaries": (r"caught (\d+) of (\d+) planted faults", lambda: [len(CANARIES), len(CANARIES)]),
    "controls": (r"no false alarms on (\d+) clean controls", lambda: [len(CONTROLS)]),
    "learn": (r"no pattern under (\d+) posts and suggests nothing under (\d+)", lambda: [learn.PATTERN_FROM, learn.SUGGEST_FROM]),
    "recipe prices": (r"\b(\d+) of the (\d+) recipes have no known price", lambda: [sum(r.basis == "unknown" for r in USABLE), len(USABLE)]),
    "price date": (r"Most known prices date from (\w+ \d+, \d{4})", lambda: [most_priced_date()]),
}


def prose(text: str) -> str:
    """The text a reader sees as words. Code, HTML tags and link targets become spaces, so positions still line up."""

    def blank(found: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", found.group(0))

    for pattern in (r"^```.*?^```", r"`[^`\n]+`", r"<[^>]+>", r"\]\([^)]*\)"):
        text = re.sub(pattern, blank, text, flags=re.DOTALL | re.MULTILINE)
    return text


SEEN = prose(README)


def same(shown: str, wanted: object) -> bool:
    if isinstance(wanted, float):
        return float(shown) == wanted
    return shown == str(wanted)


@pytest.mark.parametrize("name", CLAIMS)
def test_every_number_matches_the_code(name):
    pattern, live = CLAIMS[name]
    found = list(re.finditer(pattern, SEEN, re.MULTILINE))
    assert found, f"The README no longer says {name}. Put the claim back, or take it out of CLAIMS."
    wanted = live()
    for match in found:
        shown = list(match.groups())
        assert len(shown) == len(wanted) and all(same(s, w) for s, w in zip(shown, wanted, strict=True)), (
            f"README says {match.group(0)!r}, and the code says {wanted} now. Change the README."
        )


def test_no_number_goes_unchecked():
    covered: list[tuple[int, int]] = []
    for pattern in [pattern for pattern, _ in CLAIMS.values()] + FIXED:
        covered += [found.span() for found in re.finditer(pattern, SEEN, re.MULTILINE)]
    loose = [
        (found.group(0), SEEN[max(0, found.start() - 30) : found.end() + 30].strip())
        for found in re.finditer(r"\d+(?:\.\d+)?", SEEN)
        if not any(start <= found.start() and found.end() <= end for start, end in covered)
    ]
    assert not loose, f"Numbers with nothing behind them. Read each from the code in CLAIMS, or list it in FIXED with a reason: {loose}"


def test_every_kind_in_the_library_has_a_row():
    assert set(STATS["kinds"]) <= set(KIND_LABELS), f"A new kind needs a label and a row: {set(STATS['kinds']) - set(KIND_LABELS)}"


def test_the_film_types_table_lists_every_type_in_order():
    listed = re.findall(r"^\| `([a-z]+)` \|", README, re.MULTILINE)
    assert listed == list(film.ORDER), f"README lists {listed}, and `altocue film types` gives {list(film.ORDER)}."


@pytest.mark.parametrize("page", [README, INSTALL], ids=["README.md", "INSTALL.md"])
def test_every_command_exists(page):
    assert not problems_in(page, flags=every_flag() | {"--version", "--help"}, stages={})  # the top-level flags are real too


@pytest.mark.parametrize("page", [README, INSTALL], ids=["README.md", "INSTALL.md"])
def test_no_third_party_editor_is_named(page):
    assert not OTHER_EDITORS.search(page), "Altocue never launches or hands off to a third-party editor. The timeline view is the HyperFrames studio."


def test_every_picture_is_an_absolute_link_to_a_real_file():
    """PyPI renders the README too, and a relative path is a broken picture there."""
    pictures = re.findall(r'<img src="([^"]+)"', README)
    assert pictures
    for url in pictures:
        if url.startswith("https://img.shields.io/"):
            continue
        assert url.startswith(RAW), f"{url} must start with {RAW}"
        assert (ROOT / url.removeprefix(RAW)).is_file(), f"{url} points at a file this repo does not have"
