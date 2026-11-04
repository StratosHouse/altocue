"""A generic rules file: words that must never appear, lines that must, and spellings.

Every finding goes to a human. QA never blurs, cuts or rewrites anything.

    {
      "banned": ["guaranteed", "risk free"],
      "required": [{"id": "free-terms", "when_spoken": "free", "on_screen": "terms apply"}],
      "spellings": ["Altocue"],
      "allow_repeats": ["again and again"],
      "safe_zone": {"left": 0.05, "right": 0.92, "top": 0.09, "bottom": 0.82}
    }
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import AltocueError
from ..motion import formats

TEXT_FIELDS = ("eyebrow", "text", "title", "subtitle", "value", "label", "by", "headline", "cta")
FUZZY_FROM = 6  # shorter words differ by one letter too often to fuzzy match
MUST_SAY = re.compile(r"^\s*(?:#{1,6}\s*)?must\s+(?:say|cover|mention|include)\s*:?\s*$", re.IGNORECASE)
LIST_MARK = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
SMALL_WORDS = {"a", "an", "and", "are", "for", "in", "is", "it", "of", "on", "or", "that", "the", "this", "to", "we", "with", "you", "your"}
SAID_SHARE = 0.8
SAID_SLACK = 2  # a line may be said with this many extra words for each of its own
READING_WPS = 3.0  # 180 words a minute, the top of the 160 to 180 Ofcom recommends for subtitles. A subtitle figure, not a phone study


@dataclass
class Rules:
    banned: list[str] = field(default_factory=list)
    required: list[dict[str, Any]] = field(default_factory=list)
    spellings: list[str] = field(default_factory=list)
    allow_repeats: list[str] = field(default_factory=list)
    safe_zone: dict[str, float] | None = None


def load(path: str | Path) -> Rules | None:
    """Read a rules file. A key we do not know is an error, because an ignored rule is a false pass."""
    target = Path(path)
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text())
    except json.JSONDecodeError as error:
        raise AltocueError(f"{target} is not valid JSON: {error}", fix="Fix the file, or remove it.") from error
    unknown = set(data) - set(Rules.__dataclass_fields__)
    if unknown:
        raise AltocueError(f"Unknown keys in {target.name}: {', '.join(sorted(unknown))}.", fix=f"Use only {', '.join(Rules.__dataclass_fields__)}.")
    for rule in data.get("required", []):
        if not {"when_spoken", "on_screen"} <= set(rule):
            raise AltocueError(f"A required rule in {target.name} is incomplete: {rule}.", fix="Each needs `when_spoken` and `on_screen`.")
    return Rules(**data)


def edit_distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, left in enumerate(a, 1):
        current = [i]
        for j, right in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (left != right)))
        previous = current
    return previous[-1]


def tokens(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", text.lower())


def near(token: str, term: str) -> bool:
    return token == term or (len(term) >= FUZZY_FROM and abs(len(token) - len(term)) <= 1 and edit_distance(token, term) <= 1)


def match_terms(text: str, terms: list[str]) -> list[str]:
    """Terms in one string: exact words, one letter off for long words, phrases as a run of words."""
    found = tokens(text)
    joined = f" {' '.join(found)} "
    hits = []
    for term in terms:
        wanted = tokens(term)
        if len(wanted) > 1:
            if f" {' '.join(wanted)} " in joined:
                hits.append(term)
        elif wanted and any(near(token, wanted[0]) for token in found):
            hits.append(term)
    return hits


def misspelled(text: str, spellings: list[str]) -> list[str]:
    """The approved spellings this string gets wrong. All capitals is a style, not a mistake."""
    wrong = []
    for term in spellings:
        if " " in term:
            for match in re.finditer(re.escape(term), text, re.IGNORECASE):
                if match.group(0) != term and not match.group(0).isupper():
                    wrong.append(term)
                    break
            continue
        low = term.lower()
        for raw in re.findall(r"[A-Za-z0-9]+", text):
            same = raw.lower() == low
            if raw == term or (same and raw.isupper()):
                continue
            if near(raw.lower(), low):
                wrong.append(term)
                break
    return wrong


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value).replace("*", "").replace("|", " ")).strip()


def on_screen(timeline: dict[str, Any]) -> list[dict[str, Any]]:
    """Every string the render puts on screen, with its layer and time."""
    strings = []
    for page in timeline.get("captions", []):
        strings.append({"layer": "captions", "text": " ".join(w["text"] for w in page["words"]), "start": page["start"], "end": page["end"]})
        if page.get("name"):  # a speaker named on the page is on screen with it
            strings.append({"layer": "captions", "text": str(page["name"]), "start": page["start"], "end": page["end"]})
    for item in timeline.get("items", []):
        if item.get("type") == "composition":
            texts = [value for value in item.get("vars", {}).values() if isinstance(value, str) and " " in value]
        else:
            texts = formats.strings(item) if formats.find(item.get("type")) else [item[name] for name in TEXT_FIELDS if item.get(name) not in (None, "")]
        strings += [{"layer": item["type"], "text": _clean(text), "start": item["start"], "end": item["end"]} for text in texts]
        for step in item.get("items", []) if item.get("type") == "steps" else []:
            strings.append({"layer": "steps", "text": _clean(step["text"]), "start": step.get("at", item["start"]), "end": item["end"]})
    return strings


def _word_count(text: str) -> int:
    return sum(1 for word in text.split() if any(ch.isalnum() for ch in word))


def _read_through(item: dict[str, Any]) -> list[str]:
    """A format's text is read in full. Its lists and charts are scanned, so only the longest row counts."""
    fmt = formats.find(item["type"])
    texts = [_clean(item[slot.name]) for slot in fmt.slots if slot.kind == "text" and item.get(slot.name)] if fmt else []
    rows = [text for text in map(_clean, formats.strings(item)) if text not in texts]
    return texts + ([max(rows, key=_word_count)] if rows else [])


def reading_time(timeline: dict[str, Any], wps: float = READING_WPS) -> list[dict[str, Any]]:
    """How long each graphic's words stay up, against how long they take to read.

    Strings shown together share their time. Captions are left out, because they echo what is
    being said, and BBC R&D found viewers keep up with accurate subtitles well past guideline speed.
    """
    groups: dict[tuple[str, float, float], list[str]] = {}
    plain = [item for item in timeline.get("items", []) if not formats.find(item.get("type"))]
    for item in on_screen({"items": plain}):
        groups.setdefault((item["layer"], item["start"], item["end"]), []).append(item["text"])
    for item in timeline.get("items", []):
        if formats.find(item.get("type")):
            groups[(item["type"], item["start"], item["end"])] = _read_through(item)
    close = timeline.get("total")
    rows = []
    for (layer, start, end), texts in sorted(groups.items(), key=lambda entry: entry[0][1]):
        words = sum(_word_count(text) for text in texts)
        if not words:
            continue
        shown = round((min(end, close) if close else end) - start, 3)
        needed = round(words / wps, 3)
        short = round(max(needed - shown, 0), 3)
        rows.append({"layer": layer, "text": " ".join(texts), "start": start, "words": words, "shown": shown, "needed": needed, "short_by": short})
    return rows


def _spoken_at(rule: dict[str, Any], spoken: list[dict[str, Any]]) -> list[tuple[float, float]]:
    wanted = tokens(rule["when_spoken"])
    said = [(token, w) for w in spoken for token in tokens(w["text"])]
    return [
        (said[i][1]["start"], said[i + len(wanted) - 1][1]["end"])
        for i in range(len(said) - len(wanted) + 1)
        if [token for token, _ in said[i : i + len(wanted)]] == wanted
    ]


def missing_required(rule: dict[str, Any], strings: list[dict[str, Any]], spoken: list[dict[str, Any]], *, overlap: bool = False) -> dict[str, Any] | None:
    """The first place a trigger word is said without its line on screen."""
    lines = rule["on_screen"] if isinstance(rule["on_screen"], list) else [rule["on_screen"]]
    for start, end in _spoken_at(rule, spoken):
        if overlap:
            visible = [s for s in strings if s["start"] < end and s["end"] > start]
        else:
            visible = [s for s in strings if s["start"] <= start + 0.05 and s["end"] >= end - 0.05]
        joined = f" {' '.join(t for s in visible for t in tokens(s['text']))} "
        absent = [line for line in lines if f" {' '.join(tokens(line))} " not in joined]
        if absent:
            term = rule.get("id", rule["when_spoken"])
            return {
                "layer": "required",
                "text": rule["when_spoken"],
                "start": start,
                "end": end,
                "kind": "required",
                "term": term,
                "why": f"not on screen: {', '.join(absent)}",
            }
    return None


def must_say(brief: str) -> list[str]:
    """The lines listed under a `## Must say` heading in brief.md. Prose elsewhere asks for nothing a test can hold."""
    lines: list[str] = []
    reading = False
    for raw in brief.splitlines():
        if MUST_SAY.match(raw):
            reading = True
        elif reading and raw.lstrip().startswith("#"):
            reading = False
        elif reading and LIST_MARK.match(raw):
            lines.append(LIST_MARK.sub("", raw).strip())
        elif reading and lines and raw.strip() and not raw[0].isspace():
            reading = False
    return lines


def _covered(wanted: list[str], heard: list[str]) -> bool:
    """Most of the line's words within one short run of speech, so the words of a sentence cannot be collected from all over a film."""
    content = [token for token in wanted if token not in SMALL_WORDS] or wanted
    needed = math.ceil(len(content) * SAID_SHARE)
    span = max(len(wanted) * SAID_SLACK, len(wanted) + 2)
    for start in range(max(len(heard) - span + 1, 1)):
        window = heard[start : start + span]
        if sum(any(near(token, term) for token in window) for term in content) >= needed:
            return True
    return False


def said(line: str, heard: list[str]) -> bool:
    """Whether the words of a line were heard. A `|` offers other wordings. This reads words, not meaning."""
    return any(_covered(tokens(option), heard) for option in line.split("|") if tokens(option))


def said_terms(spoken: list[dict[str, Any]], terms: list[str]) -> list[dict[str, Any]]:
    """Terms heard in the speech, each where it is first said. Matched as on screen: one letter off for a long word, a phrase as a run of words."""
    said = [(token, word) for word in spoken for token in tokens(word["text"])]
    hits = []
    for term in terms:
        wanted = tokens(term)
        for index in range(len(said) - len(wanted) + 1 if wanted else 0):
            run = said[index : index + len(wanted)]
            if near(run[0][0], wanted[0]) if len(wanted) == 1 else [token for token, _ in run] == wanted:
                hits.append({"layer": "speech", "source": "speech", "text": term, "start": run[0][1]["start"], "end": run[-1][1]["end"], "kind": "banned", "term": term})
                break
    return hits


def check(strings: list[dict[str, Any]], ruleset: Rules, spoken: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """On-screen strings and speech against the rules. A banned word said aloud is as much a claim as one on screen (c123)."""
    findings = []
    for item in strings:
        findings += [{**item, "source": "screen", "kind": "banned", "term": term} for term in match_terms(item["text"], ruleset.banned)]
        findings += [{**item, "source": "screen", "kind": "spelling", "term": term} for term in misspelled(item["text"], ruleset.spellings)]
    findings += said_terms(spoken, ruleset.banned)
    for rule in ruleset.required:
        missing = missing_required(rule, strings, spoken)
        if missing:
            findings.append(missing)
    return findings
