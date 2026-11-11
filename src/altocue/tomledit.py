"""Set keys in a TOML file's text and leave every other line as it was written.

`altocue.toml` is a file people edit by hand, with a comment beside almost every value. Reading it and writing it back
would lose them all, so a setting is changed where it stands: its value is replaced, a commented-out `# key = value`
line becomes live, a missing key goes at the end of its table, and a missing table goes at the end of the file.
"""

from __future__ import annotations

import json
import math
import re
import tomllib
from collections.abc import Mapping
from typing import Any

from .errors import AltocueError

HEADER = re.compile(r"\s*\[([^\[\]]+)\]\s*(?:#.*)?")
OTHER_HEADER = re.compile(r"\s*\[\[")
BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
BY_HAND = "Change that line by hand in the file."


def toml_value(value: Any) -> str:
    """One value as TOML writes it. Tables are not values here: name the table and set its keys."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AltocueError(f"{value} is not a number a settings file can hold.")
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")
    if isinstance(value, list | tuple):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    raise AltocueError(f"A setting cannot hold {type(value).__name__}.")


def toml_key(key: str) -> str:
    return key if BARE_KEY.fullmatch(key) else json.dumps(key, ensure_ascii=False)


def split_comment(rest: str) -> tuple[str, str]:
    """`"a # b"   # note` as the value and the comment, with the gap kept on the value. A hash inside quotes is text."""
    quote = ""
    index = 0
    while index < len(rest):
        char = rest[index]
        if quote:
            if char == "\\" and quote == '"':
                index += 1
            elif char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "#":
            return rest[:index], rest[index:]
        index += 1
    return rest, ""


def is_value(text: str) -> bool:
    try:
        tomllib.loads(f"probe = {text}")
    except tomllib.TOMLDecodeError:
        return False
    return True


def key_line(line: str, key: str) -> tuple[str, str, str, bool] | None:
    """A line that sets `key`, as its indent, the value with its gap, the comment, and whether it is commented out."""
    body = line.rstrip("\r\n")
    name = re.escape(key) if BARE_KEY.fullmatch(key) else rf"""(?:"{re.escape(key)}"|'{re.escape(key)}')"""
    found = re.match(rf"(\s*)(#\s*)?{name}\s*=\s*", body)
    if not found:
        return None
    value, comment = split_comment(body[found.end() :])
    off = bool(found.group(2))
    if off and not is_value(value):
        return None  # a sentence in a comment that happens to start with the key
    return found.group(1), value, comment, off


def set_line(line: str, key: str, value: Any) -> str:
    indent, old, comment, _ = key_line(line, key)  # type: ignore[misc]
    ending = line[len(line.rstrip("\r\n")) :]
    made = f"{indent}{toml_key(key)} = {toml_value(value)}"
    if comment:
        column = len(line.rstrip("\r\n")) - len(comment)  # the comment stays in its column while the value fits
        made = made.ljust(column) if len(made) < column else made + "  "
    return made + comment + ending


def table_span(lines: list[str], table: str) -> tuple[int, int] | None:
    """The lines of `[table]`, from its header up to the next header."""
    start = next((index for index, line in enumerate(lines) if (found := HEADER.fullmatch(line.rstrip("\r\n"))) and found.group(1).strip() == table), None)
    if start is None:
        return None
    end = next((index for index in range(start + 1, len(lines)) if HEADER.fullmatch(lines[index].rstrip("\r\n")) or OTHER_HEADER.match(lines[index])), len(lines))
    return start, end


def end_of_table(lines: list[str], start: int, end: int) -> int:
    """Where a new key goes: after the table's last line, before the blank lines and any comment block that leads into the next table."""

    def skip_blank(index: int) -> int:
        while index > start + 1 and not lines[index - 1].strip():
            index -= 1
        return index

    last = skip_blank(end)
    probe = last
    while probe > start + 1 and lines[probe - 1].lstrip().startswith("#"):
        probe -= 1
    if probe < last and probe > start + 1 and not lines[probe - 1].strip():
        last = skip_blank(probe)
    return last


def set_key(text: str, table: str, key: str, value: Any) -> str:
    """`text` with `[table] key` set to `value`, and nothing else touched."""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    span = table_span(lines, table)
    fresh = f"{toml_key(key)} = {toml_value(value)}{newline}"
    if span is None:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += newline
        gap = [newline] if lines and lines[-1].strip() else []
        return "".join([*lines, *gap, f"[{table}]{newline}", fresh])
    start, end = span
    inside = range(start + 1, end)
    found = [(index, key_line(lines[index], key)) for index in inside]
    live = next((index for index, parts in found if parts and not parts[3]), None)
    off = next((index for index, parts in found if parts and parts[3]), None)
    place = live if live is not None else off
    if place is not None:
        lines[place] = set_line(lines[place], key, value)
    else:
        at = end_of_table(lines, start, end)
        if at > 0 and not lines[at - 1].endswith(("\n", "\r")):
            lines[at - 1] += newline
        lines.insert(at, fresh)
    return "".join(lines)


def set_keys(text: str, values: Mapping[str, Mapping[str, Any]]) -> str:
    """`text` with every `values[table][key]` set. The result is checked: it parses, and it differs from `text` only in those keys."""
    try:
        wanted = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise AltocueError(f"The settings file does not parse: {error}.", fix="Mend that line by hand, then run this again.") from error
    made = text
    for table, keys in values.items():
        group = wanted
        for part in table.split("."):
            group = group.setdefault(part, {})
            if not isinstance(group, dict):
                raise AltocueError(f"[{table}] is not a table in this file, so nothing was changed.", fix=BY_HAND)
        for key, value in keys.items():
            made = set_key(made, table, key, value)
            group[key] = list(value) if isinstance(value, tuple) else value
    try:
        same = tomllib.loads(made) == wanted
    except tomllib.TOMLDecodeError:
        same = False
    if not same:
        named = ", ".join(f"[{table}] {key}" for table, keys in values.items() for key in keys)
        raise AltocueError(f"Could not set {named} without disturbing the rest of the file, so nothing was changed.", fix=BY_HAND)
    return made
