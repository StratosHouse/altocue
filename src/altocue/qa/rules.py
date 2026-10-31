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
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import AltocueError

TEXT_FIELDS = ("eyebrow", "text", "title", "subtitle", "value", "label", "by", "headline", "cta")
FUZZY_FROM = 6  # shorter words differ by one letter too often to fuzzy match


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
        raise AltocueError(
            f"Unknown keys in {target.name}: {', '.join(sorted(unknown))}.", fix=f"Use only {', '.join(Rules.__dataclass_fields__)}."
        )
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
        strings.append(
            {"layer": "captions", "text": " ".join(w["text"] for w in page["words"]), "start": page["start"], "end": page["end"]}
        )
    for item in timeline.get("items", []):
        for name in TEXT_FIELDS:
            if item.get(name) not in (None, ""):
                strings.append({"layer": item["type"], "text": _clean(item[name]), "start": item["start"], "end": item["end"]})
        for step in item.get("items", []) if item.get("type") == "steps" else []:
            strings.append({"layer": "steps", "text": _clean(step["text"]), "start": step.get("at", item["start"]), "end": item["end"]})
    return strings


def _spoken_at(rule: dict[str, Any], spoken: list[dict[str, Any]]) -> list[tuple[float, float]]:
    wanted = tokens(rule["when_spoken"])
    said = [(token, w) for w in spoken for token in tokens(w["text"])]
    return [
        (said[i][1]["start"], said[i + len(wanted) - 1][1]["end"])
        for i in range(len(said) - len(wanted) + 1)
        if [token for token, _ in said[i : i + len(wanted)]] == wanted
    ]


def missing_required(
    rule: dict[str, Any], strings: list[dict[str, Any]], spoken: list[dict[str, Any]], *, overlap: bool = False
) -> dict[str, Any] | None:
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


def check(strings: list[dict[str, Any]], ruleset: Rules, spoken: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings = []
    for item in strings:
        findings += [{**item, "kind": "banned", "term": term} for term in match_terms(item["text"], ruleset.banned)]
        findings += [{**item, "kind": "spelling", "term": term} for term in misspelled(item["text"], ruleset.spellings)]
    for rule in ruleset.required:
        missing = missing_required(rule, strings, spoken)
        if missing:
            findings.append(missing)
    return findings
