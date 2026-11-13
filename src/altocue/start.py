"""Eight questions, asked once, so every project starts the way its owner works.

The answers live in one small file per user, the profile. `altocue new` reads it, and so does the agent at the start
of each job. It holds no key and waives no consent: spending, uploading and a failed gate stop for a yes whatever it says.
"""

from __future__ import annotations

import argparse
import functools
import json
import math
import os
import re
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import AltocueError
from .tomledit import toml_value

HOME_VARIABLE = "ALTOCUE_HOME"  # the user-level folder, in place of ~/.altocue
PROFILE = "profile.toml"
VERSION = 1
KINDS_FILE = Path(__file__).parent / "data" / "kinds.toml"
HOUSE = "house"
CAP = "spend_cap_usd"
LANGUAGE = re.compile(r"[a-z]{2,3}")
LANGUAGE_EXAMPLES = "en, es, fr, de, pt, hi or ja"
STOPS = ("spending money", "uploading a file", "a failed gate")
ALWAYS = "Spending money, uploading a file and a failed gate always stop for your yes, whatever you choose here."
# The shape each place plays. `vertical` is one file for Reels, TikTok and Shorts together.
SHAPES = {"vertical": "9:16", "reels": "9:16", "tiktok": "9:16", "shorts": "9:16", "youtube": "16:9", "linkedin": "4:5", "square": "1:1"}


@dataclass(frozen=True)
class Kind:
    """One kind of video and the settings a project of that kind starts with. Starting points, never measurements."""

    name: str
    label: str
    about: str
    playbook: str
    settings: dict[str, dict[str, Any]]

    @property
    def aspect(self) -> str:
        return str(self.settings["output"]["aspect"])

    @property
    def platform(self) -> str:
        return str(self.settings["output"]["platform"])


@functools.cache
def kinds() -> dict[str, Kind]:
    raw = tomllib.loads(KINDS_FILE.read_text())
    return {
        name: Kind(name, entry["label"], entry["about"], entry["playbook"], {table: dict(keys) for table, keys in entry.items() if isinstance(keys, dict)})
        for name, entry in raw.items()
    }


@dataclass(frozen=True)
class Choice:
    value: str
    label: str
    explanation: str


@dataclass(frozen=True)
class Question:
    id: str
    question: str
    choices: tuple[Choice, ...]
    default: str
    other: str = ""  # what may be typed in place of a choice


def questions() -> tuple[Question, ...]:
    return (
        Question("kind", "What do you make most?", tuple(Choice(kind.name, kind.label, kind.about) for kind in kinds().values()), "short"),
        Question(
            "platform",
            "Where is it published first?",
            (
                Choice("vertical", "All the vertical apps", "Reels, TikTok and Shorts together, as one tall 9:16 file"),
                Choice("reels", "Instagram Reels", "Tall, 9:16, with text kept clear of the Reels buttons"),
                Choice("tiktok", "TikTok", "Tall, 9:16, with text kept clear of the TikTok buttons"),
                Choice("shorts", "YouTube Shorts", "Tall, 9:16, with text kept clear of the Shorts buttons"),
                Choice("youtube", "YouTube", "Wide, 16:9, for the long form player"),
                Choice("linkedin", "LinkedIn", "A 4:5 file for the feed"),
                Choice("square", "A square feed", "A 1:1 file that is not tied to one app"),
            ),
            "vertical",
        ),
        Question(
            "brand",
            "What should your videos look like?",
            (Choice(HOUSE, "The house look", "Altocue's own fonts and colours"),),
            HOUSE,
            other="The folder that holds your brand kit: your logo, fonts and colours",
        ),
        Question(
            "captions",
            "Do you want captions?",
            (
                Choice("on", "On", "Word-timed captions in the picture"),
                Choice("file", "As a file only", "A subtitle file beside the video and nothing in the picture"),
                Choice("off", "Off", "No captions"),
            ),
            "on",
        ),
        Question(
            "sound",
            "How should it sound?",
            (
                Choice("voice", "Voice only", "No music and no effects"),
                Choice("subtle", "Subtle", "Light effects on the graphics and no music"),
                Choice("music", "Music bed", "Music under the voice"),
                Choice("full", "Full", "Music and effects"),
            ),
            "subtle",
        ),
        Question(
            "language",
            "What language do you speak in your videos?",
            (Choice("en", "English", "The code en"),),
            "en",
            other=f"Any other language as its code, such as {LANGUAGE_EXAMPLES}",
        ),
        Question(
            "online",
            "May Altocue use online services, and may it spend money?",
            (
                Choice("none", "No", "Nothing leaves this computer and nothing is spent"),
                Choice("free", "Free sources only", "Free stock and sound sources, with your own keys. Nothing is spent"),
                Choice("review", "Also an online reviewer", "Free sources, and an online reviewer may watch the final. You are still asked before each upload"),
                Choice("paid", "Also paid generation", "All of the above, and paid generation up to a cap you type. Each paid call still shows its cost and asks"),
            ),
            "none",
        ),
        Question(
            "checks",
            "How much should the agent check with you? Spending, uploading and a failed gate always stop, whatever you choose.",
            (
                Choice("every", "At every step", "The agent stops after each stage and shows you the result"),
                Choice("cut", "At the cut and before the final", "You see the cut, then a draft, before anything is final"),
                Choice("draft", "Show me a draft, then ask", "Straight through to a draft, then one question"),
                Choice("end", "Run to the end", "Through to the finished video, then a list of what was decided. Spending, uploading and a failed gate still stop"),
            ),
            "cut",
        ),
    )


KEYS = ("kind", "platform", "brand", "captions", "sound", "language", "online", CAP, "checks")
CAP_QUESTION = "What is the most one project may spend, in US dollars? Leave it out and nothing can spend."


def home() -> Path:
    chosen = os.environ.get(HOME_VARIABLE)
    return Path(chosen).expanduser() if chosen else Path.home() / ".altocue"


def profile_path() -> Path:
    return home() / PROFILE


def defaults() -> dict[str, Any]:
    return {**{question.id: question.default for question in questions()}, CAP: 0.0}


def checked(key: str, value: Any) -> Any:
    """One answer, as the profile stores it. Anything else says which answers exist."""
    by_id = {question.id: question for question in questions()}
    if key not in KEYS:
        raise AltocueError(f"There is no question called {key}.", fix=f"The questions are {', '.join(KEYS)}. See them with `altocue start --questions`.")
    if key == CAP:
        try:
            cap = float(value)
        except (TypeError, ValueError):
            cap = math.nan
        if isinstance(value, bool) or not math.isfinite(cap) or cap < 0:
            raise AltocueError(f"{value} is not a spend cap.", fix="Give a plain number of US dollars, 0 or more, such as 20. 0 blocks all spending.")
        return cap
    text = str(value).strip()
    if key == "brand":
        if text.lower() in ("", HOUSE):
            return HOUSE
        kit = Path(text).expanduser()
        if not kit.exists():
            raise AltocueError(f"No brand kit at {kit}.", fix=f"Give the folder that holds your logo, fonts and colours, or `{HOUSE}` for Altocue's own look.")
        return str(kit.resolve())
    if key == "language":
        if not LANGUAGE.fullmatch(text.lower()):
            raise AltocueError(f"{text or 'Nothing'} is not a language code.", fix=f"Give the short code of the language you speak, such as {LANGUAGE_EXAMPLES}.")
        return text.lower()
    values = [choice.value for choice in by_id[key].choices]
    if text.lower() not in values:
        raise AltocueError(f"{text or 'Nothing'} is not an answer to {key}.", fix=f"Use one of {', '.join(values)}.")
    return text.lower()


def settled(answers: dict[str, Any]) -> dict[str, Any]:
    """A cap only means something beside `online = paid`. Any other answer blocks spending."""
    return {**answers, CAP: float(answers[CAP]) if answers["online"] == "paid" else 0.0}


def saved(*, strict: bool = True) -> dict[str, Any] | None:
    """The profile on this machine, or None when nobody has answered yet.

    `strict` refuses a file somebody broke by hand. Without it a bad value falls back to its default, so `altocue start`
    can always run and mend the file.
    """
    path = profile_path()
    if not path.is_file():
        return None
    try:
        raw = tomllib.loads(path.read_text())
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        if strict:
            fix = "Answer again with `altocue start`, or remove it with `altocue start --reset`."
            raise AltocueError(f"The profile at {path} does not parse: {error}.", fix=fix) from error
        raw = {}
    answers = defaults()
    if raw.get("kind") in kinds():
        answers["platform"] = kinds()[raw["kind"]].platform
    for key in KEYS:
        if key not in raw:
            continue
        if key == "brand" and str(raw[key]).strip().lower() not in ("", HOUSE):
            answers[key] = str(raw[key]).strip()  # a kit folder is not looked for here: it may sit on a disk that is unplugged today
            continue
        try:
            answers[key] = checked(key, raw[key])
        except AltocueError as error:
            if strict:
                raise AltocueError(f"The profile at {path} has a bad answer. {error.args[0]}", fix=f"{error.fix} Or answer again with `altocue start`.") from error
    return settled(answers)


def starting_point(*, kind: str | None, aspect: str | None, profile: dict[str, Any] | None) -> tuple[str | None, dict[str, dict[str, Any]]]:
    """The aspect a new project is made with and the settings laid over the default file.

    A flag beats the profile, and the profile beats the built-in defaults. With no kind and no profile there is nothing
    to set, and the project file stays as `Project.create` wrote it.
    """
    from . import platforms

    chosen = kind or (profile["kind"] if profile else None)
    if not chosen:
        return aspect, {}
    preset = kinds()[chosen]
    changes = {table: dict(keys) for table, keys in preset.settings.items()}
    changes["format"] = {"kind": chosen}
    place = preset.platform
    if profile:
        if chosen == profile["kind"]:
            place = profile["platform"]  # answered for the kind they make most. Another kind brings its own shape
        changes.setdefault("transcribe", {})["language"] = profile["language"]
        if profile["online"] == "paid" and float(profile[CAP]) > 0:
            changes.setdefault("generate", {})[CAP] = float(profile[CAP])  # a number the user typed. Every other answer leaves spending blocked
    shape = aspect or SHAPES[place]
    del changes["output"]["aspect"]  # `Project.create` writes it
    # A safe zone belongs to one shape, and a name this checkout's platforms do not know would stop every later stage.
    if SHAPES[place] == shape and place in platforms.PLATFORMS:
        changes["output"]["platform"] = place
    else:
        del changes["output"]["platform"]
    return shape, changes


def kind_of(project: Any) -> Kind | None:
    """The kind a project was started as, which names the playbook to read."""
    return kinds().get(str(project.setting("format", "kind", "")))


COMMENTS = {
    "kind": "what you make most. `altocue start --kinds` lists them",
    "platform": "where it is published first",
    "brand": f"your brand kit folder, or {HOUSE} for Altocue's own look",
    "captions": "on, file (a subtitle file only) or off",
    "sound": "voice, subtle, music or full",
    "language": "the language you speak, as a code",
    "online": "none, free, review or paid",
    CAP: "the most one project may spend. Only read when online is paid. 0 blocks all spending",
    "checks": "how often the agent stops for you: every, cut, draft or end",
}


def write(answers: dict[str, Any]) -> Path:
    answers = settled(answers)
    lines = [
        "# How you work, as answered in `altocue start`. Every new project starts from this.",
        "# Safe to change by hand, or run `altocue start` again. No key or password is ever kept here.",
        f"version = {VERSION}",
        "",
        *(f"{f'{key} = {toml_value(answers[key])}':<26}  # {COMMENTS[key]}" for key in KEYS),
    ]
    path = profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    return path


def is_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def ask_choice(question: Question, default: str, ask: Callable[[str], str], say: Callable[[str], None]) -> str:
    values = [choice.value for choice in question.choices]
    for number, choice in enumerate(question.choices, 1):
        say(f"  {number}. {choice.label}: {choice.explanation}{'  (default)' if choice.value == default else ''}")
    while True:
        typed = ask(f"Type a number, or press Enter for {values.index(default) + 1}: ").strip().lower()
        if not typed:
            return default
        if typed.isdigit() and 1 <= int(typed) <= len(values):
            return values[int(typed) - 1]
        if typed in values:
            return typed
        say(f"Type a number from 1 to {len(values)}.")


def ask_brand(question: Question, default: str, ask: Callable[[str], str], say: Callable[[str], None]) -> str:
    house = question.choices[0]
    kept = default != HOUSE
    say(f"  1. {house.label}: {house.explanation}{'' if kept else '  (default)'}")
    say(f"  2. My brand kit: {default}  (default)" if kept else "  2. My brand kit: Type the folder that holds your logo, fonts and colours")
    while True:
        typed = ask(f"Type 1, or the folder of your brand kit, or press Enter for {2 if kept else 1}: ").strip()
        if typed == "2" and not kept:
            typed = ask("The folder: ").strip()
        if not typed or (typed == "2" and kept):
            return default
        try:
            return checked("brand", HOUSE if typed == "1" else typed)
        except AltocueError as error:
            say(str(error))


def ask_typed(key: str, prompt: str, default: Any, ask: Callable[[str], str], say: Callable[[str], None]) -> Any:
    while True:
        typed = ask(prompt).strip()
        if not typed:
            return default
        try:
            return checked(key, typed)
        except AltocueError as error:
            say(str(error))


def interview(current: dict[str, Any], ask: Callable[[str], str] | None = None, say: Callable[[str], None] = print) -> dict[str, Any]:
    """Ask every question once. Enter keeps `current`, which is the saved answer or the default."""
    ask = ask or input
    answers = dict(current)
    asked = questions()
    say("Eight questions, asked once. Press Enter to keep the default. Change any answer later with `altocue start`.")
    for number, question in enumerate(asked, 1):
        say(f"\n{number} of {len(asked)}. {question.question}")
        default = answers[question.id]
        if question.id == "platform" and answers["kind"] != current["kind"]:
            default = kinds()[answers["kind"]].platform  # another kind of video brings its own place to play
        if question.id == "brand":
            answers["brand"] = ask_brand(question, default, ask, say)
        elif question.id == "language":
            answers["language"] = ask_typed("language", f"Type its code, such as {LANGUAGE_EXAMPLES}, or press Enter for {default}: ", default, ask, say)
        else:
            answers[question.id] = ask_choice(question, default, ask, say)
        if question.id == "online" and answers["online"] == "paid":
            kept = float(current[CAP]) if current["online"] == "paid" else 0.0
            answers[CAP] = ask_typed(CAP, f"The most one project may spend, in US dollars. Press Enter for {kept:g}: ", kept, ask, say)
            if not answers[CAP]:
                say("The cap stays at 0, so nothing can spend until you type a number.")
    return settled(answers)


def in_words(answers: dict[str, Any]) -> dict[str, str]:
    """Each answer as a sentence a person would say."""
    found = {}
    for question in questions():
        value = answers[question.id]
        choice = next((choice for choice in question.choices if choice.value == value), None)
        found[question.id] = f"{choice.label}. {choice.explanation}" if choice else str(value)
    if answers["brand"] != HOUSE:
        found["brand"] = f"Your brand kit at {answers['brand']}"
    found["language"] = "English" if answers["language"] == "en" else f"The language with the code {answers['language']}"
    if answers["online"] == "paid":
        cap = float(answers[CAP])
        found["online"] += f". The cap is {cap:g} US dollars a project" if cap else ". No cap was typed, so nothing can spend yet"
    return found


TITLES = {
    "kind": "You make", "platform": "Published first", "brand": "Look", "captions": "Captions", "sound": "Sound", "language": "Language", "online": "Online",
    "checks": "Checks",
}  # fmt: skip


NOT_ANSWERED = "Nobody has answered yet, so these are the defaults. Ask with `altocue start --questions`, then save with `altocue start --answer`."
NO_TERMINAL = (
    "No terminal here, so nothing was asked and nothing was saved. List the questions with `altocue start --questions`, "
    "then answer with `altocue start --answer kind=short`, or take the defaults with `altocue start --yes`."
)
STOPPED = "The answers stopped before the last question, so nothing was saved."


def report(answers: dict[str, Any], *, answered: bool) -> dict[str, Any]:
    """What `--show --json` prints, and what an agent reads at the start of each job."""
    kind = kinds()[answers["kind"]]
    return {
        "profile": str(profile_path()),
        "answered": answered,
        "answers": {key: answers[key] for key in KEYS},
        "in_words": in_words(answers),
        "aspect": SHAPES[answers["platform"]],
        "playbook": kind.playbook,
        "always_stops": list(STOPS),
        "next": "Nothing to ask." if answered else NOT_ANSWERED,
    }


def show(answers: dict[str, Any], *, answered: bool) -> None:
    print(f"Your profile, at {profile_path()}:" if answered else "Nobody has answered yet. These are the defaults a project starts from:")
    for key, sentence in in_words(answers).items():
        print(f"  {TITLES[key] + ':':<17}{sentence}")
    print(ALWAYS)


def as_questions(found: dict[str, Any] | None) -> dict[str, Any]:
    """The questions as data, so an agent can ask them in chat with its own question tool."""
    current = found or defaults()
    listed = []
    for question in questions():
        entry: dict[str, Any] = {
            "id": question.id,
            "question": question.question,
            "choices": [{"value": choice.value, "label": choice.label, "explanation": choice.explanation} for choice in question.choices],
            "default": kinds()[current["kind"]].platform if question.id == "platform" else question.default,
            "answer": current[question.id] if found else None,
        }
        if question.other:
            entry["other"] = question.other
        if question.id == "platform":
            entry["default_by_kind"] = {kind.name: kind.platform for kind in kinds().values()}
        if question.id == "online":
            entry["follow_up"] = {"id": CAP, "when": "paid", "question": CAP_QUESTION, "default": 0, "answer": current[CAP] if found else None}
        listed.append(entry)
    return {
        "profile": str(profile_path()),
        "answered": found is not None,
        "questions": listed,
        "always_stops": list(STOPS),
        "save": "altocue start " + " ".join(f"--answer {question.id}=<value>" for question in questions()),
    }


def given_answers(pairs: list[str]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for pair in pairs:
        key, equals, value = pair.partition("=")
        if not equals:
            raise AltocueError(f"{pair} is not an answer.", fix="Write each answer as key=value, such as `altocue start --answer kind=short`.")
        found[key.strip()] = checked(key.strip(), value)
    return found


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--questions", action="store_true", help="Print the questions as JSON and ask nothing, so an agent can ask them in chat")
    parser.add_argument("--answer", action="append", default=[], metavar="KEY=VALUE", help="Set one answer without being asked, such as kind=long. Repeat it for more")
    parser.add_argument("--yes", "-y", action="store_true", help="Keep the saved answers, or take the defaults, without being asked")
    parser.add_argument("--show", action="store_true", help="Print the profile in plain words")
    parser.add_argument("--json", action="store_true", help="With --show, --answer or --yes: print the profile as JSON")
    parser.add_argument("--reset", action="store_true", help="Remove the profile. New projects go back to the built-in defaults")
    parser.add_argument("--kinds", action="store_true", help="List the kinds of video a project can start as")


def run(args: argparse.Namespace) -> int:
    if args.kinds:
        for kind in kinds().values():
            print(f"{kind.name:<13}{kind.label}. {kind.about}. Starts at {kind.aspect}.")
        return 0
    path = profile_path()
    if args.reset:
        if path.is_file():
            path.unlink()
            print(f"Removed {path}. New projects start from the built-in defaults again.")
        else:
            print(f"No profile at {path}, so there was nothing to remove.")
        return 0
    if args.questions:
        print(json.dumps(as_questions(saved(strict=False)), indent=2, ensure_ascii=False))
        return 0
    if args.show:
        found = saved()
        if args.json:
            print(json.dumps(report(found or defaults(), answered=found is not None), indent=2, ensure_ascii=False))
        else:
            show(found or defaults(), answered=found is not None)
        return 0
    given = given_answers(args.answer)
    found = saved(strict=False)
    current = found or defaults()
    if given or args.yes:
        if "kind" in given and "platform" not in given and not found:
            given["platform"] = kinds()[given["kind"]].platform
        answers = settled({**current, **given})
    elif is_terminal():
        try:
            answers = interview(current)
        except (EOFError, KeyboardInterrupt):
            raise AltocueError(STOPPED, fix="Run `altocue start` again, or `altocue start --yes` to take the defaults.") from None
    else:
        print(NO_TERMINAL)
        return 0
    write(answers)
    if args.json:
        print(json.dumps(report(answers, answered=True), indent=2, ensure_ascii=False))
        return 0
    print()
    show(answers, answered=True)
    print("\nNext: `altocue demo` proves a new machine on a sample clip. Or start a project with `altocue new <folder> --input <clips>`.")
    return 0
