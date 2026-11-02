"""Word-timed caption pages. Pages never cross a cut and only show words really spoken."""

from __future__ import annotations

import argparse
import math
from typing import Any

from . import brand as brands
from . import cut
from .errors import AltocueError
from .project import Project, write_json

LEADING = "\"'“‘("
TRAILING = ".,!?;:\"'”’)"
SENTENCE_END = ".!?"
HOLD_AFTER = 0.28
SPEAKER_COLOURS = ("#f3f7fa", "#ffd08a", "#a8d8ff", "#ffb3c7", "#d2c2ff")
SPEAKER_DISPLAYS = {"": (), "colour": ("colour",), "name": ("name",), "both": ("colour", "name")}


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", "-p", default=".", help="Project folder")


def strip_punctuation(text: str) -> str:
    cleaned = text.strip().lstrip(LEADING).rstrip(TRAILING)
    return cleaned if any(ch.isalnum() for ch in cleaned) else ""


def balanced(words: list[dict[str, Any]], max_words: int) -> list[list[dict[str, Any]]]:
    """Split a run into even chunks, so nothing is left as a lonely last word."""
    size = math.ceil(len(words) / math.ceil(len(words) / max_words))
    return [words[i : i + size] for i in range(0, len(words), size)]


def build_pages(
    words: list[dict[str, Any]], *, max_words: int = 4, combine_ms: int = 700, ends: dict[str, float] | None = None
) -> list[dict[str, Any]]:
    """Group mapped words into pages. Each word needs `text`, `start`, `end` and `segment`.

    `ends` gives each segment's last moment in output seconds, and a page's hold stops there.
    """
    runs: list[list[dict[str, Any]]] = []
    for word in words:
        current = runs[-1] if runs else None
        joins = (
            current is not None
            and current[-1]["segment"] == word["segment"]
            and current[-1].get("speaker", "") == word.get("speaker", "")
            and word["start"] - current[0]["start"] <= combine_ms / 1000
            and current[-1]["text"].rstrip()[-1:] not in SENTENCE_END
            and not current[-1]["text"].rstrip().endswith(",")
        )
        if joins:
            current.append(word)  # type: ignore[union-attr]
        else:
            runs.append([word])

    pages: list[dict[str, Any]] = []
    for run in runs:
        for chunk in balanced(run, max_words):
            shown = [
                {"text": strip_punctuation(w["text"]), "start": round(w["start"], 3), "end": round(w["end"], 3)}
                for w in chunk
                if strip_punctuation(w["text"])
            ]
            if shown:
                page = {"start": shown[0]["start"], "end": shown[-1]["end"], "segment": chunk[0]["segment"], "words": shown}
                if chunk[0].get("speaker"):
                    page |= {"speaker": chunk[0]["speaker"], "said_by": [{"speaker": w.get("speaker", "")} for w in chunk]}
                pages.append(page)

    for page, following in zip(pages, pages[1:], strict=False):
        page["end"] = round(min(page["end"] + HOLD_AFTER, following["start"]), 3)
    if pages:
        pages[-1]["end"] = round(pages[-1]["end"] + HOLD_AFTER, 3)
    for page in pages:
        page["end"] = min(page["end"], (ends or {}).get(page["segment"], page["end"]))
    return pages


def mark_speakers(pages: list[dict[str, Any]], project: Project, colours: list[str]) -> None:
    """Colour a page by who says it, or name them when the voice changes. Off unless `[speakers] show` asks."""
    show = str(project.setting("speakers", "show", ""))
    if show not in SPEAKER_DISPLAYS:
        raise AltocueError(f"Unknown [speakers] show value {show}.", fix="Use colour, name, both, or leave it empty.")
    seen: dict[str, int] = {}
    latest = ""
    for page in pages:
        who = page.get("speaker", "")
        if not who:
            continue
        place = seen.setdefault(who, len(seen))
        if "colour" in SPEAKER_DISPLAYS[show]:
            page["colour"] = colours[place % len(colours)]
        if "name" in SPEAKER_DISPLAYS[show] and who != latest:
            page["name"] = str(project.setting("speakers", who, who))
        latest = who


def build(project: Project) -> list[dict[str, Any]]:
    style = brands.load(project).caption
    edl = project.edl()
    pages = build_pages(
        [w for w in cut.map_words(project, edl) if w.get("kind", "word") == "word"],
        max_words=int(style["max_words"]),
        combine_ms=int(style["combine_ms"]),
        ends=cut.segment_ends(edl),
    )
    mark_speakers(pages, project, list(style.get("speaker_colours") or SPEAKER_COLOURS))
    write_json(project.work / "captions.json", {"pages": pages})
    return pages


def run(args: argparse.Namespace) -> int:
    pages = build(Project.open(args.project))
    print(f"{len(pages)} caption pages, {sum(len(p['words']) for p in pages)} words")
    return 0
