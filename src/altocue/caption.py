"""Word-timed caption pages. Pages never cross a cut and only show words really spoken."""

from __future__ import annotations

import argparse
import math
from typing import Any

from . import brand as brands
from . import cut
from .project import Project, write_json

LEADING = "\"'“‘("
TRAILING = ".,!?;:\"'”’)"
SENTENCE_END = ".!?"
HOLD_AFTER = 0.28


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", "-p", default=".", help="Project folder")


def strip_punctuation(text: str) -> str:
    cleaned = text.strip().lstrip(LEADING).rstrip(TRAILING)
    return cleaned if any(ch.isalnum() for ch in cleaned) else ""


def balanced(words: list[dict[str, Any]], max_words: int) -> list[list[dict[str, Any]]]:
    """Split a run into even chunks, so nothing is left as a lonely last word."""
    size = math.ceil(len(words) / math.ceil(len(words) / max_words))
    return [words[i : i + size] for i in range(0, len(words), size)]


def build_pages(words: list[dict[str, Any]], *, max_words: int = 4, combine_ms: int = 700) -> list[dict[str, Any]]:
    """Group mapped words into pages. Each word needs `text`, `start`, `end` and `segment`."""
    runs: list[list[dict[str, Any]]] = []
    for word in words:
        current = runs[-1] if runs else None
        joins = (
            current is not None
            and current[-1]["segment"] == word["segment"]
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
                pages.append({"start": shown[0]["start"], "end": shown[-1]["end"], "segment": chunk[0]["segment"], "words": shown})

    for page, following in zip(pages, pages[1:], strict=False):
        page["end"] = round(min(page["end"] + HOLD_AFTER, following["start"]), 3)
    if pages:
        pages[-1]["end"] = round(pages[-1]["end"] + HOLD_AFTER, 3)
    return pages


def build(project: Project) -> list[dict[str, Any]]:
    style = brands.load(project).caption
    pages = build_pages(
        [w for w in cut.map_words(project, project.edl()) if w.get("kind", "word") == "word"],
        max_words=int(style["max_words"]),
        combine_ms=int(style["combine_ms"]),
    )
    write_json(project.work / "captions.json", {"pages": pages})
    return pages


def run(args: argparse.Namespace) -> int:
    pages = build(Project.open(args.project))
    print(f"{len(pages)} caption pages, {sum(len(p['words']) for p in pages)} words")
    return 0
