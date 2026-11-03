"""What a service says it ran, held against what was asked, and why a take was kept.

A job record can carry defaults nobody asked for, such as 16:9 or sound on (b015), and a service can
rewrite a prompt before running it. Both are read leniently, because every service shapes its record
its own way. Four records stay apart: the brief, the prompt sent, the settings used and the review (c335).
"""

from __future__ import annotations

import re
from typing import Any

from ..errors import AltocueError

MODEL_KEYS = ("job_type", "model", "model_id")
PROMPT_KEYS = ("actual_prompt", "final_prompt", "enhanced_prompt", "revised_prompt", "rewritten_prompt", "prompt")
TASTE = {
    "amazing", "awesome", "beautiful", "best", "better", "cinematic", "cool", "epic", "feel", "feels", "fine", "good", "gorgeous", "great",
    "like", "liked", "looks", "look", "love", "loved", "nice", "perfect", "pretty", "stunning", "vibe", "vibes", "works", "really", "very",
}  # fmt: skip
SMALL = {"a", "an", "and", "are", "but", "for", "in", "is", "it", "its", "of", "on", "or", "so", "the", "this", "to", "was", "with"}
CONCRETE_WORDS = 3
NUMBER = re.compile(r"(-?\d+(?:\.\d+)?)\s*s?")


def params_of(record: dict[str, Any]) -> dict[str, Any]:
    """The settings a record says were used: its params block, or the record itself when it has none."""
    for key in ("params", "input", "arguments"):
        if isinstance(record.get(key), dict):
            return record[key]
    return record


def norm(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return f"{float(value):g}"
    text = str(value).strip().lower()
    if text in ("on", "yes", "true"):
        return "true"
    if text in ("off", "no", "false"):
        return "false"
    number = NUMBER.fullmatch(text)
    return f"{float(number.group(1)):g}" if number else text


def ran_prompt(record: dict[str, Any]) -> str | None:
    """The prompt the service says it ran, when its record carries one."""
    for source in (params_of(record), record):
        for key in PROMPT_KEYS:
            if isinstance(source.get(key), str) and source[key].strip():
                return source[key]
    return None


def kept_because(reason: str) -> str:
    """Why a take was kept, in words a later reader can check: the eyeline lands on the cup, the room matches s02.

    "Looks cinematic" explains nothing a later choice can rest on, so a reason made of taste words is refused.
    """
    words = re.findall(r"[a-z0-9']+", reason.lower())
    concrete = [word for word in words if word not in TASTE and word not in SMALL]
    if len(concrete) < CONCRETE_WORDS:
        raise AltocueError(
            f'"{reason.strip()}" does not say what in the take was right.',
            fix='Name what can be checked in the picture, such as "eyeline lands on the cup, the room matches s02, hands clear of the label".',
        )
    return reason.strip()


def mismatches(record: dict[str, Any], sent: dict[str, Any], *, model: str = "") -> list[str]:
    """Every setting the record states that differs from the request. A setting the record leaves out is not checked."""
    used = params_of(record)
    found = []
    named = next((str(source[key]) for source in (record, used) for key in MODEL_KEYS if source.get(key)), "")
    if model and named and re.sub(r"[\W_]+", "", named.lower()) != re.sub(r"[\W_]+", "", model.lower()):
        found.append(f"model {named}, asked for {model}")
    for key, value in sent.items():
        if key in used and not isinstance(used[key], dict | list) and norm(used[key]) != norm(value):
            found.append(f"{key} {used[key]}, asked for {value}")
    return found
