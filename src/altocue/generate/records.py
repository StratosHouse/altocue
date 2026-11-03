"""What a service says it ran, held against what was asked, before a take is imported into the edit.

A job record can carry defaults nobody asked for, such as 16:9 or sound on (b015). It is read
leniently, because every service shapes its record its own way.
"""

from __future__ import annotations

import re
from typing import Any

MODEL_KEYS = ("job_type", "model", "model_id")
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
