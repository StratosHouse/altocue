"""One HTML file that plays the film beside its gates, notes and captions.

It reads what `altocue qa` and `altocue review` saved and decides nothing: a verdict is copied from
`review/report.json`, and only for the exact file it was given. Text from a reviewer, a caption or a
gate is escaped here, so the small script in `page.js` never touches it. Everything travels in the
one file, the film included, so it opens from file:// and can be sent to a phone.
"""

from __future__ import annotations

import base64
import json
import re
import tempfile
import webbrowser
from html import escape
from pathlib import Path
from typing import Any

import numpy as np

from .. import ffmpeg, locks, master, qa
from ..brand import data_path
from ..errors import AltocueError
from ..project import Project
from ..qa import gates as qa_gates
from . import report as reports
from .pack import timecode

NAME = "page.html"
CSS = Path(__file__).with_name("page.css")
SCRIPT = Path(__file__).with_name("page.js")
COPY_LONG_SIDE = 960
COPY_MOST_KBPS = 900  # enough to read a caption and judge a face, in a file small enough to send
COPY_LEAST_KBPS = 180
COPY_AUDIO_KBPS = 96
COPY_BUDGET_MB = 40
WAVE_POINTS = 600
STAMP = re.compile(r"\b(\d{2}):(\d{2}\.\d{2})\b")
ORDER = {"FAIL": 0, "NOT RUN": 1, "PASS": 2, "N/A": 3}
LOGO = (
    '<svg class="logo" viewBox="0 0 100 100" aria-hidden="true"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" stop-color="#eafff5"/><stop offset=".38" stop-color="#b5f4d9"/><stop offset=".72" stop-color="#a5e7d1"/>'
    '<stop offset="1" stop-color="#8fd4e0"/></linearGradient><mask id="m" maskUnits="userSpaceOnUse" x="0" y="0" width="100" height="100">'
    '<rect width="100" height="100" fill="#000"/><path d="M20 14 L20 86 L84 50 Z" fill="#fff" stroke="#fff" stroke-width="12" stroke-linejoin="round"/>'
    '<rect x="38" y="-12" width="5.5" height="124" fill="#000" transform="rotate(10 40.75 50)"/>'
    '<rect x="62" y="-12" width="5.5" height="124" fill="#000" transform="rotate(10 64.75 50)"/></mask></defs>'
    '<rect width="100" height="100" fill="url(#g)" mask="url(#m)"/></svg>'
)


def path(project: Project) -> Path:
    return project.review_dir / NAME


def _read(target: Path) -> dict[str, Any]:
    return json.loads(target.read_text()) if target.exists() else {}


def video_rate(duration: float) -> int:
    """A short film gets the full rate. A long one thins out so the page stays a file a browser can open."""
    budget = COPY_BUDGET_MB * 8000 / max(duration, 0.001) - COPY_AUDIO_KBPS
    return int(min(COPY_MOST_KBPS, max(COPY_LEAST_KBPS, budget)))


def review_copy(file: Path, dest: Path, duration: float, fps: float) -> Path:
    """A small copy for the page. A keyframe a second, so a jump lands at once."""
    rate = video_rate(duration)
    fit = f"scale='if(gte(iw,ih),min({COPY_LONG_SIDE},iw),-2)':'if(gte(iw,ih),-2,min({COPY_LONG_SIDE},ih))'"
    args = ["-i", str(file), "-vf", fit, "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-maxrate", f"{rate}k", "-bufsize", f"{rate * 2}k"]
    args += ["-g", str(round(fps) or 30), "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", f"{COPY_AUDIO_KBPS}k", "-movflags", "+faststart", str(dest)]
    with locks.heavy("making the review copy"):
        ffmpeg.run(args)
    return dest


def wave_path(context: qa_gates.Context) -> str:
    """The sound as one filled shape, from peaks the page never has to compute."""
    if context.audio is None:
        return ""
    samples, _ = context.samples()
    if not samples.size:
        return ""
    edges = np.linspace(0, samples.size, WAVE_POINTS + 1).astype(int)
    peaks = [float(np.abs(samples[a:b]).max()) if b > a else 0.0 for a, b in zip(edges, edges[1:], strict=False)]
    top = " ".join(f"L{x} {50 - peak * 50:.1f}" for x, peak in enumerate(peaks))
    bottom = " ".join(f"L{x} {50 + peak * 50:.1f}" for x, peak in reversed(list(enumerate(peaks))))
    return f"M0 50 {top} {bottom} Z"


def verdict_of(project: Project, digest: str) -> dict[str, Any]:
    """Only code's saved verdict for this exact file counts. Everything else is said plainly as no verdict."""
    saved = _read(project.review_dir / "report.json")
    if saved.get("file_sha256") == digest and saved.get("verdict"):
        return saved
    if saved:
        return {"waiting": "The last verdict was for an earlier render, not this film.", "next": "altocue review"}
    if (project.review_dir / "PACK.md").exists():
        return {"waiting": "The pack is ready and no reviewer has scored it.", "next": "altocue review --ingest review/verdict.json"}
    return {"waiting": "No reviewer has looked at this film.", "next": "altocue review"}


def gates_of(project: Project, digest: str, verdict: dict[str, Any]) -> list[dict[str, Any]] | None:
    """The gates the verdict stands on, else QA's for this file. A result for another render is not shown."""
    if verdict.get("gates"):
        return verdict["gates"]
    saved = _read(project.review_dir / "qa.json")
    return saved.get("gates") if saved.get("sha256") == digest else None


def state_of(gate: dict[str, Any]) -> str:
    if gate.get("value") == qa_gates.NOT_APPLICABLE:
        return "N/A"
    if gate.get("skipped"):
        return "NOT RUN"
    return "PASS" if gate.get("passed") else "FAIL"


def moments(detail: str) -> list[float]:
    """Every time written in a gate's detail, in the MM:SS.ss every gate uses."""
    return [int(minutes) * 60 + float(seconds) for minutes, seconds in STAMP.findall(detail)]


def jump(at: float, text: str, *, extra: str = "") -> str:
    return f'<button type="button" class="jump{extra}" data-at="{round(at, 3)}" data-say="{escape(text)}">{escape(timecode(at, precise=True))}</button>'


def verdict_html(verdict: dict[str, Any]) -> str:
    if "waiting" in verdict:
        return (
            '<div class="verdict none"><p class="word">No verdict yet</p>'
            f'<p>{escape(verdict["waiting"])} Next: <code>{escape(verdict["next"])}</code></p></div>'
        )
    word = str(verdict["verdict"])
    reviewer = verdict.get("reviewer") or {}
    lines = []
    if verdict.get("weighted") is not None:
        lines.append(f"Weighted score {float(verdict['weighted']):.2f}")
    if reviewer.get("vendor") == "none":
        lines.append("No reviewer, because a fact gate failed")
    elif reviewer.get("vendor"):
        lines.append(f"Reviewer {reviewer['vendor']}, {reviewer.get('model', 'unknown')}, sees {reviewer.get('sees', 'unknown')}")
    if "independent" in reviewer:
        lines.append(f"independent: {str(reviewer['independent']).lower()}. {reviewer.get('independent_why', '')}")
    reasons = "".join(f"<li>{escape(str(reason))}</li>" for reason in verdict.get("reasons") or [])
    return (
        f'<div class="verdict {escape(word.lower())}"><p class="word">{escape(word)}</p>'
        f'<p class="who">{"<br>".join(escape(line) for line in lines)}</p>'
        + (f"<ul>{reasons}</ul>" if reasons else "")
        + "</div>"
    )


def note_html(note: dict[str, Any], index: int) -> str:
    start = float(note["start_seconds"])
    spoken = f"Note {index}, {note['severity']}"
    fix = str(note.get("suggested_fix") or "")
    return (
        f'<li class="note {escape(str(note["severity"]))}">{jump(start, spoken, extra=" note-jump")}'
        f'<div><p class="meta">{escape(str(note["severity"]))}, {escape(str(note["dimension"]))}</p>'
        f'<p>{escape(str(note["observed"]))}</p>' + (f'<p class="fix">Fix: {escape(fix)}</p>' if fix else "") + "</div></li>"
    )


def gate_html(gate: dict[str, Any]) -> str:
    state = state_of(gate)
    value = gate.get("value")
    shown = "" if value is None or isinstance(value, dict) or value == qa_gates.NOT_APPLICABLE else f'<span class="value">{escape(str(value))}</span>'
    detail = str(gate.get("detail") or "")
    times = "".join(jump(at, f"Gate {gate['id']}") for at in moments(detail)[:4])
    return (
        f'<li class="gate {state.lower().replace(" ", "-").replace("/", "")}"><span class="state">{state}</span>'
        f'<div><p class="meta"><span class="name">{escape(str(gate["id"]))}</span>{shown}</p><p>{escape(detail)}</p>'
        + (f'<p class="times">{times}</p>' if times else "")
        + "</div></li>"
    )


def caption_html(page: dict[str, Any]) -> str:
    text = " ".join(str(word["text"]) for word in page["words"])
    start = float(page["start"])
    return f'<li data-end="{float(page["end"])}">{jump(start, f"Caption at {timecode(start)}")}<span>{escape(text)}</span></li>'


def _left(at: float, duration: float) -> str:
    return f"{min(at / max(duration, 0.001), 1.0) * 100:.3f}"


def timeline_html(
    duration: float, wave: str, cuts: list[float], captions: list[dict[str, Any]], gates: list[dict[str, Any]], notes: list[dict[str, Any]]
) -> str:
    """Mouse and touch only. Every moment here is also a button in a list below, for a keyboard or a screen reader."""
    ticks = "".join(f'<i class="tick cut" style="left:{_left(at, duration)}%"></i>' for at in cuts)
    spans = "".join(
        f'<i class="span" data-at="{float(c["start"])}" style="left:{_left(float(c["start"]), duration)}%;'
        f'width:{_left(float(c["end"]) - float(c["start"]), duration)}%"></i>'
        for c in captions
    )
    trouble = [(at, state_of(g)) for g in gates if state_of(g) in ("FAIL", "NOT RUN") for at in moments(str(g.get("detail") or ""))[:4]]
    marks = "".join(f'<i class="mark gate" data-at="{at}" style="left:{_left(at, duration)}%"></i>' for at, _ in trouble)
    placed = "".join(
        f'<i class="mark note {escape(str(n["severity"]))}" data-at="{float(n["start_seconds"])}" style="left:{_left(float(n["start_seconds"]), duration)}%"></i>'
        for n in notes
    )
    drawn = f'<svg viewBox="0 0 {WAVE_POINTS} 100" preserveAspectRatio="none" aria-hidden="true"><path d="{wave}"/></svg>'
    sound = drawn if wave else '<p class="empty">No sound</p>'
    lanes = (
        f'<div class="lane sound"><b>Sound</b>{sound}</div><div class="lane cuts"><b>Cuts</b>{ticks}</div>'
        f'<div class="lane captions"><b>Captions</b>{spans}</div><div class="lane gates"><b>Gates</b>{marks}</div>'
        f'<div class="lane notes"><b>Notes</b>{placed}</div>'
    )
    head = '<i class="playhead" id="playhead"></i>'
    return f'<div class="timeline" id="timeline" data-duration="{duration}" aria-hidden="true"><div class="lanes">{lanes}{head}</div></div>'


def summary_of(gates: list[dict[str, Any]]) -> str:
    states = [state_of(gate) for gate in gates]
    return f"{states.count('PASS')} passed, {states.count('FAIL')} failed, {states.count('NOT RUN')} not run, {states.count('N/A')} not applicable"


def font_css() -> str:
    data = base64.b64encode(data_path("fonts", "Geist-Variable.ttf").read_bytes()).decode()
    return f'@font-face{{font-family:Geist;font-weight:100 900;font-display:swap;src:url(data:font/ttf;base64,{data}) format("truetype")}}'


def compose(
    title: str, digest: str, facts: str, film: str, verdict: dict[str, Any], gates: list[dict[str, Any]] | None, captions: list[dict[str, Any]], timeline: str
) -> str:
    notes = verdict.get("notes") or []
    limits = verdict.get("limits") or ["No reviewer has looked at this film.", *reports.ALWAYS]
    fallback = "../out/final.mp4"
    gate_rows = "".join(gate_html(g) for g in sorted(gates or [], key=lambda g: ORDER[state_of(g)]))
    no_gates = '<p class="empty">QA has not run on this film. Run <code>altocue qa</code>.</p>'
    caption_rows = "".join(caption_html(c) for c in captions)
    listed = f'<ol class="captions-list" id="captions" tabindex="0" aria-label="Every caption">{caption_rows}</ol>'
    captions_list = listed if captions else '<p class="empty">No captions in this film.</p>'
    return f"""<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="altocue-film" content="{digest}">
<title>{escape(title)}: review</title>
<style>{font_css()}{CSS.read_text()}</style>
</head>
<body>
<a class="skip" href="#notes">Skip to the notes</a>
<header>{LOGO}<p class="brand">Altocue</p><h1>{escape(title)}</h1><p class="facts">{escape(facts)}</p></header>
<main>
<section class="stage" aria-label="The film">
<video id="film" controls playsinline preload="auto" aria-label="The finished film">
<source src="data:video/mp4;base64,{film}" type="video/mp4">
<source src="{fallback}" type="video/mp4">
<p>This browser cannot play the film. It is at out/final.mp4.</p>
</video>
{timeline}
<p class="said" id="said" role="status" aria-live="polite"></p>
</section>
<div class="panels">
<section aria-labelledby="h-verdict">
<h2 id="h-verdict">Verdict</h2>
{verdict_html(verdict)}
</section>
<section id="notes" aria-labelledby="h-notes" tabindex="-1">
<h2 id="h-notes">Reviewer notes</h2>
{f'<ol class="rows">{"".join(note_html(n, i) for i, n in enumerate(notes, 1))}</ol>' if notes else '<p class="empty">No notes.</p>'}
</section>
<section aria-labelledby="h-gates">
<h2 id="h-gates">Fact gates</h2>
{f'<p class="facts">{escape(summary_of(gates))}</p><ul class="rows">{gate_rows}</ul>' if gates else no_gates}
</section>
<section aria-labelledby="h-captions">
<h2 id="h-captions">Captions</h2>
{captions_list}
</section>
<section aria-labelledby="h-limits">
<h2 id="h-limits">What this review could not see or hear</h2>
<ul class="limits">{"".join(f"<li>{escape(str(item))}</li>" for item in limits)}</ul>
</section>
</div>
</main>
<script>{SCRIPT.read_text()}</script>
</body>
</html>
"""


def write(project: Project, file: Path) -> Path:
    if not Path(file).is_file():
        raise AltocueError(f"No film at {file}.", fix="Run `altocue render`, then `altocue master`, or pass --file.")
    context = qa_gates.Context.open(project, file)
    digest = qa.sha256(file)
    verdict = verdict_of(project, digest)
    gates = gates_of(project, digest, verdict)
    captions = (context.timeline or {}).get("captions") or []
    duration = context.clip.duration
    cuts = [round(shot.start, 3) for shot in context.shots[1:]]
    timeline = timeline_html(duration, wave_path(context), cuts, captions, gates or [], verdict.get("notes") or [])
    with tempfile.TemporaryDirectory(prefix="altocue-page-") as folder:
        copy = review_copy(Path(file), Path(folder) / "copy.mp4", duration, context.clip.fps)
        film = base64.b64encode(copy.read_bytes()).decode()
    size = f"{context.clip.width}x{context.clip.height}"
    target = master.project_target(project)[0]
    facts = f"{timecode(duration)} long, {size}, aiming for {target:g} LUFS. Film sha256 {digest[:12]}. The page plays a smaller copy of this film."
    target_path = path(project)
    target_path.write_text(compose(project.root.name, digest, facts, film, verdict, gates, captions, timeline))
    return target_path


def show(target: Path) -> bool:
    """Only ever called for `--open`. Altocue starts no other application by itself."""
    return bool(webbrowser.open(target.as_uri()))
