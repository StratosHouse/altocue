"""Pace: how much of each pause a film keeps, and how long it holds at the end.

`[cut] pace` is one of `tight`, `natural` or `relaxed`. A join between two kept runs has a class
(clause, comma, sentence or topic) and each class keeps its own pause. Nothing here reads sound or
makes a cut: these are the numbers, and the cut and the framing both read them from here.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

from .errors import AltocueError

PACES = ("tight", "natural", "relaxed")
DEFAULT_PACE = "natural"
CLASSES = ("clause", "comma", "sentence", "topic")

MAX_PAUSE = 0.4  # the default `[cut] max_pause`: a pause inside a clause is left whole up to this
SLOPE = 0.15  # past the knee, each further second of pause keeps this much
# Where each class starts to be shortened. The clause knee is `max_pause`, and no class starts below it.
KNEE = {"comma": 0.25, "sentence": 0.35, "topic": 0.50}
# The most each class keeps. A plain pause never keeps less than its knee, so the curve has no step in it.
CAP = {
    "tight": {"clause": 0.20, "comma": 0.26, "sentence": 0.36, "topic": 0.50},
    "natural": {"clause": 0.25, "comma": 0.32, "sentence": 0.50, "topic": 0.80},
    "relaxed": {"clause": 0.30, "comma": 0.40, "sentence": 0.65, "topic": 1.00},
}
START = {"tight": 0.20}  # a tight film starts on clause pauses here, however high `max_pause` is set
END_HOLD = {"tight": 0.25, "natural": 0.5, "relaxed": 0.8}  # seconds of picture after the last word
END_HOLD_MOST = 2.0

SENTENCE_GAP = 1.2  # with no punctuation to go on, a pause this long ends a sentence
TOPIC_GAP = 2.5  # and one this long starts something new
TAIL_MOST = 0.14  # the pad after a word that the snap was proven at. More than this goes before the next word
TAIL_SHARE = 0.6  # of a pause too short for both pads
LEAD_LEAST = 0.06
LEAD_MOST = 0.40  # a shot that opens on more quiet than this reads as dead air, and the cut_sound gate hears it

SENTENCE_END = ".?!。？！…؟।"  # Latin, CJK, the ellipsis, the Arabic question mark and the Devanagari danda
CLAUSE_END = ",;:，、；：،؛"
CLOSERS = "\"'”’)"
TITLES = frozenset("dr mr mrs ms prof mt st vs".split())
INNER_DOT = re.compile(r"\w\.\w")

# Words that open a new subject. Short on purpose: "so", "and" and "but" open half of all spoken sentences.
MARKERS = {
    "en": frozenset("now next anyway anyways okay ok alright first firstly second secondly third thirdly finally lastly meanwhile".split()),
    "es": frozenset("ahora luego después bueno primero segundo finalmente".split()),
    "fr": frozenset("maintenant ensuite bon d'abord premièrement deuxièmement enfin bref".split()),
    "de": frozenset("jetzt nun zuerst erstens zweitens schließlich übrigens".split()),
    "pt": frozenset("agora depois bom primeiro segundo finalmente enfim".split()),
}


def _cut_settings(config: Any) -> Mapping[str, Any]:
    """The `[cut]` table of a project's settings. A `Project` is accepted too."""
    settings = getattr(config, "config", config) or {}
    found = settings.get("cut", {}) if isinstance(settings, Mapping) else {}
    return found if isinstance(found, Mapping) else {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return float(value)


def pace_of(config: Any) -> str:
    """The project's pace. An old project with no `pace` key is `natural`."""
    chosen = _cut_settings(config).get("pace", DEFAULT_PACE)
    if chosen not in PACES:
        raise AltocueError(f"Unknown pace {chosen!r}.", fix=f"Set pace under [cut] in altocue.toml to one of {', '.join(PACES)}.")
    return str(chosen)


def max_pause_of(config: Any) -> float:
    """`[cut] max_pause`: the longest pause inside a clause that is left whole."""
    chosen = _number(_cut_settings(config).get("max_pause", MAX_PAUSE))
    if chosen is None or chosen <= 0:
        raise AltocueError("max_pause must be a number of seconds above 0.", fix=f"Set max_pause under [cut] in altocue.toml, such as {MAX_PAUSE:g}.")
    return chosen


def end_hold_of(config: Any) -> float:
    """Seconds of picture after the last word. With no `end_hold` key it follows the pace. 0 turns the hold off."""
    settings = _cut_settings(config)
    if "end_hold" not in settings:
        return END_HOLD[pace_of(config)]
    chosen = _number(settings["end_hold"])
    if chosen is None or not 0 <= chosen <= END_HOLD_MOST:
        raise AltocueError(
            f"end_hold must be a number of seconds from 0 to {END_HOLD_MOST:g}.",
            fix="Set end_hold under [cut] in altocue.toml, or remove the key so it follows the pace.",
        )
    return chosen


def curve(kind: str, pace: str = DEFAULT_PACE, max_pause: float = MAX_PAUSE) -> tuple[float, float]:
    """The knee and the cap of one class: a pause is whole up to the knee and never keeps more than the cap."""
    start = min(max_pause, START.get(pace, math.inf))
    knee = start if kind == "clause" else max(KNEE[kind], start)
    return knee, max(CAP[pace][kind], knee)


def kept_pause(gap: float, kind: str, pace: str = DEFAULT_PACE, max_pause: float = MAX_PAUSE) -> float:
    """How much of a pause of `gap` seconds stays. It never grows a pause, and a longer pause never keeps less than a shorter one."""
    knee, cap = curve(kind, pace, max_pause)
    return gap if gap <= knee else min(knee + (gap - knee) * SLOPE, cap)


def join_pause(kind: str, pace: str = DEFAULT_PACE) -> float:
    """The pause left where words were taken out or two clips meet. There is no source pause to follow, so the class sets it."""
    return CAP[pace][kind]


def split(keep: float) -> tuple[float, float]:
    """A kept pause as (tail, lead): what stays after the word before, and what stays before the next one.

    The tail is the short side, because a breath sits just before the line it belongs to.
    """
    tail = max(min(TAIL_SHARE * keep, TAIL_MOST), keep - LEAD_MOST)
    lead = keep - tail
    if lead < LEAD_LEAST:
        lead = min(LEAD_LEAST, keep)
        tail = keep - lead
    return tail, lead


def bare(text: str) -> str:
    """A word with its case and punctuation gone."""
    return re.sub(r"[^\w']", "", text.lower().replace("’", "'"))


def ends_sentence(text: str) -> bool:
    """A full stop after a title or inside an abbreviation does not end a sentence."""
    word = text.rstrip().rstrip(CLOSERS)
    if not word or word[-1] not in SENTENCE_END:
        return False
    return word[-1] != "." or (bare(word) not in TITLES and not INNER_DOT.search(word))


def ends_clause(text: str) -> bool:
    word = text.rstrip().rstrip(CLOSERS)
    return bool(word) and word[-1] in CLAUSE_END


def classify(left: str, right: str = "", gap: float | None = None, *, new_clip: bool = False, language: str = "en") -> str:
    """The class of the join after `left` and before `right`. `gap` is the source pause, when the two words were neighbours."""
    ended = new_clip or ends_sentence(left) or (gap is not None and gap >= SENTENCE_GAP)
    if (gap is not None and gap >= TOPIC_GAP) or (ended and bare(right) in MARKERS.get(str(language)[:2].lower(), ())):
        return "topic"
    if ended:
        return "sentence"
    return "comma" if ends_clause(left) else "clause"
