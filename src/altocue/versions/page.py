"""One HTML file to watch any version, play two side by side in step, and leave timed notes with drawings.

It works from file:// and asks the network for nothing. The films are named by relative path, such as
v003/film.mp4, never embedded, so the page stays small however many versions there are. Everything a person or a
model wrote (summaries, prompts, change lines) is escaped here. Notes travel as JSON in a data block, and the
script in `page.js` puts them on the page as text, never as markup.

A page read from a file cannot write to disk, so its notes wait in the browser until the person copies or
downloads them, as altocue-notes.json. Served by `altocue versions serve`, the same page posts them instead.
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

from ..project import Project
from ..review import page as review_page
from . import FILM, diff, folder, megabytes, notes, records, when

NAME = "index.html"
EXPORT_NAME = "altocue-notes.json"
CSS = Path(__file__).with_name("page.css")
SCRIPT = Path(__file__).with_name("page.js")
SERVE_MARK = "altocue-serve"
# What the page may load: its own inline script and style, its own films beside it, and, when served, its own notes address.
# The browser holds the page to this, so even text that slipped past the escaping could not fetch or send anything.
POLICY = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; media-src 'self' file:; font-src data:; img-src data:; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'"
)


def path(project: Project) -> Path:
    return folder(project) / NAME


def as_data(value: Any) -> str:
    """JSON that is safe inside a script element: no `<`, so nothing in it can close the element or open another."""
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def film_of(project: Project, record: dict[str, Any]) -> str | None:
    """The version's film as the page names it. None when the version kept none, which the page says plainly."""
    return f"{record['id']}/{FILM}" if record.get("render") and (folder(project) / record["id"] / FILM).is_file() else None


def page_data(project: Project, saved: list[dict[str, Any]]) -> dict[str, Any]:
    versions = []
    for record in saved:
        film = record.get("render") or {}
        versions.append(
            {
                "id": record["id"],
                "film": film_of(project, record),
                "duration": film.get("duration") or 0,
                "shape": round(film["width"] / film["height"], 4) if film.get("width") and film.get("height") else 0.5625,
                "previous": record.get("previous") or "",
            }
        )
    playable = [version["id"] for version in versions if version["film"]]
    return {"project": project.root.name, "versions": versions, "first": playable[-1] if playable else "", "notes": notes.load(project)}


def change_html(version: str, row: dict[str, Any]) -> str:
    """One code-made change. One with a time is a button that takes the player there."""
    at, was = row["at"], row["was_at"]
    when: float | None = was if at is None else at
    if when is None:
        return f'<li><span class="notime"></span><span>{escape(row["text"])}</span></li>'
    places = (f' data-at="{at}"' if at is not None else "") + (f' data-was="{was}"' if was is not None else "")
    label, text = diff.stamp(when), escape(row["text"])
    return f'<li><button type="button" class="jump go" data-version="{escape(version)}"{places} data-say="{text}">{label}</button><span>{text}</span></li>'


def version_html(record: dict[str, Any], playable: bool) -> str:
    film = record.get("render")
    facts = [f"saved {when(record)}"]
    facts.append(f"{diff.stamp(film['duration'])} long, {film['stage']}, review copy {megabytes(film.get('film_bytes'))}" if film else "no film kept")
    if record.get("model"):
        facts.append(f"made with {record['model']}")
    if record.get("resolves"):
        facts.append(f"resolves {', '.join(record['resolves'])}")
    name, off = escape(record["id"]), "" if playable else " disabled"
    parts = [
        f'<li class="version" id="ver-{name}"><div class="top"><button type="button" class="jump go" data-version="{name}" data-say="{name}"{off}>{name}</button>'
        f'<div><p class="summary">{escape(str(record.get("summary", "")))}</p><p class="meta">{escape(", ".join(facts))}</p></div></div>'
    ]
    if record.get("prompts"):
        parts.append("<h3>Asked for, in the user's own words</h3>" + "".join(f"<blockquote>{escape(str(prompt))}</blockquote>" for prompt in record["prompts"]))
    if record.get("changes"):
        parts.append('<h3>What the editor says it changed</h3><ul class="told">' + "".join(f"<li>{escape(str(change))}</li>" for change in record["changes"]) + "</ul>")
    found = record.get("diff")
    if found:
        listed = "".join(change_html(record["id"], row) for row in diff.rows(found))
        body = f'<ul class="changes">{listed}</ul>' if listed else '<p class="empty">Nothing that the files show.</p>'
        parts.append(f"<h3>Changed since {escape(str(found['from']))}, worked out by code</h3>{body}")
    else:
        parts.append('<p class="meta first">The first version, so there is nothing to compare it with.</p>')
    return "".join(parts) + "</li>"


def option_html(record: dict[str, Any], playable: bool) -> str:
    film = record.get("render")
    length = diff.stamp(film["duration"]) if film and playable else "no film"
    return f'<option value="{escape(record["id"])}">{escape(record["id"])}, {length}: {escape(diff.clipped(str(record.get("summary", "")), 60))}</option>'


def compose(project: Project, token: str = "") -> str:
    """The whole page as text. `token` is set only by the local server, and is what lets the page post notes to it."""
    saved = records(project)
    data = page_data(project, saved)
    playable = {version["id"] for version in data["versions"] if version["film"]}
    newest_first = list(reversed(saved))
    options = "".join(option_html(record, record["id"] in playable) for record in newest_first)
    waiting = len(notes.still_open(data["notes"]))
    counts = f"{len(saved)} version{'' if len(saved) == 1 else 's'}, {waiting} open note{'' if waiting == 1 else 's'}"
    facts = f"{counts}. Everything here stays on this machine: the page asks the network for nothing."
    mark = f'\n<meta name="{SERVE_MARK}" content="{escape(token)}">' if token else ""
    title = escape(project.root.name)
    return f"""<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{POLICY}">{mark}
<title>{title}: versions</title>
<style>{review_page.font_css()}{review_page.CSS.read_text()}{CSS.read_text()}</style>
</head>
<body>
<a class="skip" href="#notes">Skip to the notes</a>
<header>{review_page.LOGO}<p class="brand">Altocue</p><h1>{title}</h1><p class="facts">{escape(facts)}</p></header>
<main>
<section class="stage" aria-label="The films">
<div class="pickers">
<label for="pick-a">Version<select id="pick-a">{options}</select></label>
<label for="pick-b">Compare with<select id="pick-b"><option value="">Nothing</option>{options}</select></label>
</div>
<div class="screens">
<figure class="screen" id="screen-a">
<div class="frame"><video id="film-a" playsinline preload="metadata" aria-label="The chosen version"></video><canvas id="ink" aria-hidden="true"></canvas></div>
<figcaption id="cap-a"></figcaption>
</figure>
<figure class="screen" id="screen-b" hidden>
<div class="frame"><video id="film-b" playsinline muted preload="metadata" aria-label="The version it is compared with"></video></div>
<figcaption id="cap-b"></figcaption>
</figure>
</div>
<div class="transport">
<button type="button" class="act main" id="play">Play</button>
<input type="range" id="scrub" min="0" max="0" step="0.05" value="0" aria-label="Place in the film, for both versions">
<output id="clock" for="scrub">0:00.0</output>
<button type="button" class="act" id="sound" hidden>Sound</button>
</div>
<p class="said" id="said" role="status" aria-live="polite"></p>
<form class="composer" id="composer">
<label for="note-text">Note at <span id="note-time">0:00.0</span> on <span id="note-version"></span></label>
<textarea id="note-text" rows="2" maxlength="{notes.MOST_TEXT}" placeholder="What should change here?"></textarea>
<div class="buttons">
<button type="button" class="act" id="draw" aria-pressed="false">Draw on the frame</button>
<button type="button" class="act" id="undo" hidden>Undo the last line</button>
<button type="submit" class="act main">Add note</button>
</div>
</form>
</section>
<div class="panels">
<section id="notes" aria-labelledby="h-notes" tabindex="-1">
<h2 id="h-notes">Notes</h2>
<ol class="rows" id="note-list"></ol>
<p class="empty" id="no-notes">No notes yet. Pause on a moment, type what should change, and add it.</p>
<div class="buttons" id="hand-on">
<button type="button" class="act" id="copy">Copy notes</button>
<button type="button" class="act" id="download">Download notes</button>
</div>
<textarea id="export" readonly hidden rows="6" aria-label="Your notes as text, to copy by hand"></textarea>
<p class="facts" id="how">A page read from a file cannot save into the project. Your notes stay in this browser until you copy or download them.
Give {EXPORT_NAME} to your agent, or run <code>altocue versions notes --ingest {EXPORT_NAME}</code>.</p>
</section>
<section aria-labelledby="h-versions">
<h2 id="h-versions">Versions</h2>
<ol class="rows">{"".join(version_html(record, record["id"] in playable) for record in newest_first)}</ol>
</section>
</div>
</main>
<script type="application/json" id="data">{as_data(data)}</script>
<script>{SCRIPT.read_text()}</script>
</body>
</html>
"""


def write(project: Project) -> Path:
    target = path(project)
    target.write_text(compose(project))
    return target
