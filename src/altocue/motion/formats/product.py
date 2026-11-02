"""Product and UI motion. Every interface here is invented and unbranded, and any screen is drawn unless the film supplies its own picture."""

from __future__ import annotations

import difflib
import math
import random
import re
from typing import Any

from ...errors import AltocueError
from . import Format, Slot, kit

CURSOR_DWELL = 0.22  # a hand arrives, then presses
UNIT = re.compile(r"(?<![\w.#-])(-?\d*\.?\d+)u\b")
KEYWORDS = frozenset(
    "and as async await break case catch class const continue def default del do elif else except export extends false False finally fn for from func function if import in "
    "interface let match new nil none None not null of or pub raise return self static struct super switch this throw true True try type use var void while with yield".split()
)
TOKENS = re.compile(r"(?P<comment>#.*|//.*)|(?P<string>\"[^\"]*\"?|'[^']*'?|`[^`]*`?)|(?P<number>\b\d[\d_.]*\b)|(?P<word>[A-Za-z_]\w*)|(?P<other>\s+|.)")
TICK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path pathLength="1" d="M5 12.8l4.6 4.7L19 7.4"/></svg>'
ARROW = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V6M6.2 11.6L12 5.8l5.8 5.8"/></svg>'
POINTER = '<svg viewBox="0 0 64 64" aria-hidden="true"><path d="M13 6v45l11.2-10 7.7 17.5 9.5-4.2-7.8-17.3L48.8 36z"/></svg>'


def _scoped(fmt: str, *css: str) -> str:
    """`$` is the format's own class, and `12u` is twelve pixels at full scale."""
    body = "".join(css).replace("$", f".f-{fmt}")
    return UNIT.sub(lambda found: f"calc({found.group(1)}px * var(--scale))", body)


def _share(row: dict[str, Any], key: str, fallback: float, owner: str) -> float:
    """A place on a picture, as a share of its width or height."""
    value = row.get(key)
    if value in (None, ""):
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        raise AltocueError(f'{owner}: "{key}" must be a number from 0 to 1.', fix=f"See `altocue library show {owner}`.") from None


def _spread(count: int, begin: float, end: float) -> list[float]:
    return [begin + (end - begin) * index / max(count - 1, 1) for index in range(count)]


def _timed(rows: list[dict[str, Any]], begin: float, end: float) -> list[float]:
    """A row anchored to a spoken word keeps its time. The rest share the span evenly."""
    even = _spread(len(rows), begin, end)
    return [row["delay"] if row.get("at") is not None else even[index] for index, row in enumerate(rows)]


def _typed(text: str, start: float, span: float) -> tuple[str, float]:
    """One span per character with a caret inside. The rhythm comes from the copy, so a render never changes, and it pauses where a person would."""
    weights = []
    for index, char in enumerate(text):
        wobble = 0.8 + ((index * 7) % 5) * 0.11
        weights.append(wobble * (2.6 if char in ".,:;!?" else 1.5 if char == " " else 4.0 if char == "\n" else 1.0))
    unit = span / max(sum(weights), 0.001)
    out, clock = [], start
    for index, char in enumerate(text):
        gap = weights[index] * unit
        last = index == len(text) - 1
        shown = "\n" if char == "\n" else kit.esc(char)
        out.append(f'<span class="ch{" last" if last else ""}" style="--t:{clock:.3f}s;--n:{gap:.3f}s">{shown}<i class="car"></i></span>')
        clock += gap
    return "".join(out), clock


def _pointer(start: tuple[float, float], stops: list[tuple[float, float, float]], *, travel: float = 0.56, leave: tuple[float, float, float] | None = None) -> str:
    """The pointer rides a curve to each stop and waits a beat before it presses, the way a hand does.

    Each leg is its own offset path with a seeded bend, so a render never changes and two legs never bend alike.
    Coordinates are at full scale. The press happens at each stop's time, so the click sound can land on it.
    """
    inner = f'<b class="glyph">{POINTER}</b>'
    for _, _, at in stops:
        inner = f'<div class="press" style="--t:{at:.3f}s">{inner}</div>'
    px, py = start
    legs: list[tuple[float, float, float, float]] = [(x, y, at, CURSOR_DWELL) for x, y, at in stops]
    if leave:
        legs.append((leave[0], leave[1], leave[2], 0.0))
    for index, (x, y, at, dwell) in enumerate(legs):
        dx, dy = x - px, y - py
        distance = math.hypot(dx, dy) or 1.0
        rng = random.Random(f"{start}{index}{x:.1f}{y:.1f}")
        bend = distance * rng.uniform(0.12, 0.24) * rng.choice((-1, 1))
        mid = rng.uniform(0.42, 0.62)
        cx, cy = dx * mid - dy / distance * bend, dy * mid + dx / distance * bend
        seconds = max(min(travel * (0.7 + distance / 1500), travel * 1.4), 0.24) if index < len(stops) else 0.7
        go = max(at - seconds - dwell, 0.0) if index < len(stops) else at
        inner = f"<div class=\"leg\" style=\"offset-path:path('M0 0Q{cx:.1f} {cy:.1f} {dx:.1f} {dy:.1f}');--t:{go:.3f}s;--n:{seconds:.3f}s\">{inner}</div>"
        px, py = x, y
    return f'<div class="ptr" style="left:calc({start[0]:.1f}px * var(--scale));top:calc({start[1]:.1f}px * var(--scale))">{inner}</div>'


def _ripple(x: float, y: float, at: float) -> str:
    return f'<b class="rip" style="--x:{x:.1f};--y:{y:.1f};--t:{at:.3f}s"></b>'


POINTER_CSS = """
$ .ptr{position:absolute;width:0;height:0;margin:0;z-index:9;transform:scale(var(--scale));transform-origin:0 0}
$ .leg,$ .press{position:absolute;left:0;top:0;width:0;height:0}
$ .leg{offset-rotate:0deg;offset-anchor:0 0;offset-distance:0%;animation:pd-leg var(--n) cubic-bezier(.45,.05,.2,1) var(--t) both}
$ .press{animation:pd-press .22s ease-out calc(var(--t) - .08s) both}
$ .glyph{position:absolute;left:-14px;top:-8px;display:block;width:72px;height:72px;filter:drop-shadow(0 8px 12px rgba(0,0,0,.5))}
$ .glyph svg{display:block;width:100%;height:100%}
$ .glyph path{fill:var(--ink);stroke:var(--panel);stroke-width:3.4;stroke-linejoin:round}
$ .rip{position:absolute;left:calc(var(--x) * 1px * var(--scale));top:calc(var(--y) * 1px * var(--scale));width:0;height:0;z-index:8}
$ .rip{display:block;border-radius:50%;box-shadow:0 0 0 3u var(--accent);animation:pd-rip .62s cubic-bezier(.16,.7,.3,1) var(--t) both}
@keyframes pd-leg{from{offset-distance:0%}to{offset-distance:100%}}
@keyframes pd-press{0%,100%{transform:none}45%{transform:scale(.82)}}
@keyframes pd-rip{0%{opacity:0;width:0;height:0;margin:0}2%{opacity:1}100%{opacity:0;width:150u;height:150u;margin:-75u 0 0 -75u}}
"""

CARD_CSS = """
$ .card{position:relative;width:880u;max-width:100%;border-radius:36u;overflow:hidden;background:color-mix(in srgb,var(--surface) 86%,var(--panel));box-shadow:0 0 0 1px rgba(211,238,252,.15) inset,0 1px 0 rgba(255,255,255,.09) inset,0 44u 100u rgba(0,0,0,.52);animation:pd-card .62s var(--ease) both}
@keyframes pd-card{from{opacity:0;transform:translateY(46u) scale(.965)}to{opacity:1;transform:none}}
"""

TYPED_CSS = """
$ .ch{position:relative;opacity:0;animation:pd-on .01s linear var(--t) both}
$ .car{position:absolute;right:-.05em;top:.06em;bottom:.02em;width:.08em;border-radius:1px;background:var(--accent);opacity:0;animation:pd-car var(--n) linear var(--t) forwards}
$ .ch.last .car{animation:pd-blink 1s steps(2) var(--t) infinite}
@keyframes pd-on{from{opacity:0}to{opacity:1}}
@keyframes pd-car{0%,99%{opacity:1}100%{opacity:0}}
@keyframes pd-blink{0%{opacity:1}100%{opacity:0}}
"""

FULL_CSS = """
$ {container-type:size;overflow:hidden}
$ .bg{position:absolute;inset:0;background:radial-gradient(90% 56% at 50% 58%,color-mix(in srgb,var(--accent) 13%,var(--surface)),transparent 72%),radial-gradient(120% 100% at 50% 0%,color-mix(in srgb,var(--surface) 80%,var(--panel)),var(--panel) 70%);animation:pd-in .3s ease-out both}
$ .ttl{animation:rise .6s .12s var(--ease) both}
$ .ttl h1{margin:0;font-size:92u;font-weight:600;line-height:1;letter-spacing:-.04em;text-wrap:balance}
$ .ttl p{margin:18u 0 0;font-size:40u;line-height:1.18;letter-spacing:-.015em;color:var(--soft);text-wrap:balance}
@keyframes pd-in{from{opacity:0}to{opacity:1}}
"""

SCREEN_CSS = """
$ .scr{position:absolute;inset:0;overflow:hidden;container-type:size;color:var(--ink);background:linear-gradient(168deg,color-mix(in srgb,var(--surface) 88%,var(--ink) 7%),var(--surface) 58%,color-mix(in srgb,var(--surface) 62%,var(--panel)))}
$ .scr img{display:block;width:100%;height:100%;object-fit:cover}
$ .shell{position:absolute;inset:0;display:flex}
$ .nav{display:none;flex:none;width:17cqmin;padding:7cqmin 0;flex-direction:column;align-items:center;gap:5cqmin;background:color-mix(in srgb,var(--panel) 62%,transparent)}
$ .nav b{width:8cqmin;height:8cqmin;border-radius:2.4cqmin;background:var(--accent)}
$ .nav i{width:6.4cqmin;height:6.4cqmin;border-radius:2cqmin;background:rgba(255,255,255,.1)}
$ .main{flex:1;min-width:0;display:flex;flex-direction:column;gap:5cqmin;padding:11cqmin 7cqmin 6cqmin}
$ .top{display:flex;align-items:center;justify-content:space-between;gap:3cqmin}
$ .top strong{min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font-size:7.6cqmin;font-weight:600;letter-spacing:-.03em}
$ .top u{flex:none;width:8.4cqmin;height:8.4cqmin;border-radius:50%;background:linear-gradient(140deg,var(--accent),color-mix(in srgb,var(--accent) 30%,var(--surface)))}
$ .kpi{display:grid;grid-template-columns:1fr auto;align-items:end;gap:1cqmin 3cqmin;padding:5.4cqmin;border-radius:5cqmin;background:rgba(255,255,255,.055);box-shadow:0 0 0 1px rgba(255,255,255,.07) inset}
$ .kpi span{grid-column:1 / -1;font-size:4.6cqmin;color:var(--soft)}
$ .kpi strong{font-size:14.5cqmin;font-weight:600;line-height:1;letter-spacing:-.05em}
$ .kpi em{font-size:5cqmin;font-weight:600;padding:.8cqmin 2.6cqmin;border-radius:9cqmin;color:var(--panel);background:var(--accent)}
$ .plot{position:relative;flex:1;min-height:22cqmin;animation:pd-reveal 1.05s cubic-bezier(.5,0,.2,1) var(--s) both}
$ .plot svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
$ .plot .ar{fill:color-mix(in srgb,var(--accent) 16%,transparent)}
$ .plot .ln{fill:none;stroke:var(--accent);stroke-width:2.2;vector-effect:non-scaling-stroke;stroke-linecap:round;stroke-linejoin:round}
$ .lst{display:flex;flex-direction:column;gap:3cqmin;margin:0}
$ .lst p{display:flex;align-items:center;gap:3.6cqmin;margin:0;font-size:5.2cqmin;letter-spacing:-.015em;animation:pd-up .5s var(--ease) calc(var(--s) + var(--i) * .1s + .15s) both}
$ .lst i{flex:none;display:grid;place-items:center;width:9.4cqmin;height:9.4cqmin;border-radius:3cqmin;font-style:normal;font-size:4.2cqmin;font-weight:600;color:var(--accent);background:color-mix(in srgb,var(--accent) 15%,transparent)}
$ .lst span{flex:1;min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
$ .lst b{font-weight:500;color:var(--soft)}
$ .lst .ok i{color:var(--panel);background:var(--accent)}
$ .lst i svg{width:62%;height:62%}
$ .lst i path{fill:none;stroke:currentColor;stroke-width:3.4;stroke-linecap:round;stroke-linejoin:round}
$ .chart{flex:1;min-height:30cqmin;display:flex;align-items:flex-end;gap:2.6cqmin;padding:3cqmin 0 0}
$ .chart i{flex:1;height:var(--h);border-radius:2.4cqmin 2.4cqmin 1cqmin 1cqmin;background:rgba(255,255,255,.12);transform-origin:50% 100%;animation:pd-grow .72s cubic-bezier(.2,1.2,.35,1) calc(var(--s) + var(--i) * .06s) both}
$ .chart i.hi{background:var(--accent)}
$ .duo{display:grid;grid-template-columns:1fr 1fr;gap:3.4cqmin}
$ .duo.last{margin-top:auto}
$ .duo div{padding:4.2cqmin 4.6cqmin;border-radius:4cqmin;background:rgba(255,255,255,.055);box-shadow:0 0 0 1px rgba(255,255,255,.07) inset}
$ .duo span{display:block;font-size:4.2cqmin;color:var(--soft)}
$ .duo strong{display:block;margin-top:.6cqmin;font-size:9.4cqmin;font-weight:600;letter-spacing:-.04em}
$ .meter{height:3cqmin;border-radius:9cqmin;background:rgba(255,255,255,.1);overflow:hidden}
$ .meter i{display:block;height:100%;width:72%;border-radius:9cqmin;background:var(--accent);transform-origin:0 50%;animation:pd-bar 1.1s cubic-bezier(.4,0,.2,1) var(--s) both}
@container (min-aspect-ratio:1/1){
$ .nav{display:flex}
$ .main{padding:7cqmin 7cqmin 6cqmin;gap:4cqmin}
$ .top strong{font-size:6.4cqmin}
$ .kpi strong{font-size:11cqmin}
$ .lst{flex-direction:row;gap:5cqmin}
$ .lst p{flex:1;min-width:0}
$ .lst p:nth-child(n+3){display:none}
$ .duo.last{display:none}
$ .plot{min-height:14cqmin}
$ .chart{min-height:18cqmin}
}
@keyframes pd-reveal{from{clip-path:inset(-4% 100% -4% 0)}to{clip-path:inset(-4% -2% -4% 0)}}
@keyframes pd-up{from{opacity:0;transform:translateY(3cqmin)}to{opacity:1;transform:none}}
@keyframes pd-grow{from{transform:scaleY(0)}to{transform:none}}
@keyframes pd-bar{from{transform:scaleX(0)}to{transform:none}}
"""

GRAPH = '<div class="plot"><svg viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true"><path class="ar" d="M0 40V31C8 30 12 25 20 26S32 33 42 27 56 12 66 15 80 21 88 10 96 6 100 5V40z"/><path class="ln" d="M0 31C8 30 12 25 20 26S32 33 42 27 56 12 66 15 80 21 88 10 96 6 100 5"/></svg></div>'


def _list(rows: list[tuple[str, str, str]], *, done: int = 0) -> str:
    cells = []
    for index, (mark, text, side) in enumerate(rows):
        ok = index < done
        cells.append(f'<p class="{"ok" if ok else ""}" style="--i:{index}"><i>{TICK if ok else mark}</i><span>{text}</span><b>{side}</b></p>')
    return f'<div class="lst">{"".join(cells)}</div>'


def _body(kind: str) -> str:
    if kind == "tasks":
        return (
            '<div class="kpi"><span>Done this week</span><strong>18 of 24</strong></div><div class="meter"><i></i></div>'
            + _list([("", "Draft the release", "Mon"), ("", "Check the numbers", "Tue"), ("3", "Book the review", "Wed"), ("4", "Tell the team", "Thu"), ("5", "Ship it", "Fri")], done=2)
            + '<div class="duo last"><div><span>On track</span><strong>92%</strong></div><div><span>Blocked</span><strong>1</strong></div></div>'
        )
    if kind == "bars":
        heights = (38, 62, 46, 78, 55, 96, 71)
        bars = "".join(f'<i class="{"hi" if value == 96 else ""}" style="--h:{value}%;--i:{index}"></i>' for index, value in enumerate(heights))
        return f'<div class="chart">{bars}</div><div class="duo"><div><span>Saved</span><strong>6.2h</strong></div><div><span>Streak</span><strong>14</strong></div></div>'
    if kind == "inbox":
        return _list([("AK", "Ana sent the brief", "9:41"), ("MR", "Milo left a note", "9:32"), ("NS", "Noor approved it", "8:58"), ("JT", "Jo booked a slot", "8:14"), ("EL", "Eli shared a file", "7:50")])
    return f'<div class="kpi"><span>Active today</span><strong>2,418</strong><em>+12%</em></div>{GRAPH}' + _list([("IN", "New sign-ups", "128"), ("UP", "Upgrades", "36")])


def _screen(kind: str, heading: str, start: float, image: Any = None) -> str:
    """A drawn screen, or the film's own picture when it gives one."""
    if image:
        return f'<div class="scr"><img src="{kit.esc(image)}" alt=""></div>'
    return f'<div class="scr" style="--s:{start:.3f}s"><div class="shell"><div class="nav"><b></b><i></i><i></i><i></i></div><div class="main"><div class="top"><strong>{kit.esc(heading)}</strong><u></u></div>{_body(kind)}</div></div></div>'


SCREENS = ("pulse", "tasks", "bars", "inbox")
HEADINGS = ("Today", "Tasks", "Trends", "Inbox")


# Cursor walkthrough


def _cursor_shot(item: dict[str, Any], rows: list[dict[str, Any]], hits: list[float]) -> str:
    """The same walk over the film's own screen. Each step gives `x` and `y` as shares of the picture, and its text shows beside the click."""
    head, width, height = 116.0, 880.0, 550.0
    stops, notes = [], []
    for index, row in enumerate(rows):
        fallback = (index + 1) / (len(rows) + 1)
        x, y = _share(row, "x", fallback, "cursor_walk") * width, head + _share(row, "y", fallback, "cursor_walk") * height
        stops.append((x, y, hits[index]))
        ends = hits[index + 1] if index + 1 < len(rows) else 99.0
        side = "right:calc(100% - 22px)" if x > width * 0.55 else "left:22px"
        notes.append(f'<div class="say" style="left:calc({x:.1f}px * var(--scale));top:calc({y:.1f}px * var(--scale));--t:{hits[index] + 0.06:.3f}s;--e:{ends - 0.08:.3f}s"><span style="{side}">{kit.esc(row["text"])}</span></div>')
    ripples = "".join(_ripple(x, y, at) for x, y, at in stops)
    last = hits[-1] + 0.5
    return (
        f'<div class="card" style="height:calc({head + height:.0f}px * var(--scale))"><div class="hd"><strong>{kit.emphasis(item["text"])}</strong></div>'
        f'<img class="pic" src="{kit.esc(item["image"])}" alt=""><div class="ok float" style="--t:{last:.3f}s"><span class="dot">{TICK}</span>{kit.esc(item["done"])}</div>'
        f"{''.join(notes)}{ripples}{_pointer((930.0, head + height + 40), stops)}</div>"
    )


def _cursor_walk(item: dict[str, Any]) -> str:
    rows = kit.rows(item, "steps")
    total = kit.seconds(item)
    count = len(rows)
    hits = _timed(rows, 0.95, max(1.1, total * 0.58))
    if item.get("image"):
        return _cursor_shot(item, rows, hits)
    head, row_h = 116.0, 118.0
    press = hits[-1] + (hits[-1] - hits[0]) / max(count - 1, 1) if count > 1 else hits[-1] + 0.9
    press = min(press, total * 0.74)
    stops = [(150.0, head + row_h * (index + 0.5), at) for index, at in enumerate(hits)]
    button = (880 - 96.0, head + row_h * count + 98, press)
    cells = []
    for index, row in enumerate(rows):
        cells.append(
            f'<div class="step" style="--t:{hits[index]:.3f}s"><span class="box"><span class="fillb"></span><span class="mark">{TICK}</span></span><span class="txt">{kit.esc(row["text"])}<span class="strike"></span></span></div>'
        )
    hits_html = "".join(_ripple(x, y, at) for x, y, at in [*stops, button])
    return (
        f'<div class="card" style="height:calc({head + row_h * count + 140:.0f}px * var(--scale))"><div class="hd"><strong>{kit.emphasis(item["text"])}</strong></div>{"".join(cells)}'
        f'<div class="ft"><span class="ok" style="--t:{press + 0.1:.3f}s"><span class="dot">{TICK}</span>{kit.esc(item["done"])}</span><span class="go" style="--t:{press:.3f}s">{kit.esc(item.get("button") or "Finish")}</span></div>{hits_html}'
        f"{_pointer((930.0, head + row_h * count + 180), [*stops, button])}</div>"
    )


CURSOR_CSS = """
$ .hd{display:flex;align-items:center;height:116u;padding:0 40u;border-bottom:1px solid rgba(255,255,255,.08)}
$ .hd strong{font-size:44u;font-weight:600;letter-spacing:-.028em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
$ .step{display:flex;align-items:center;gap:28u;height:118u;padding:0 40u;font-size:42u;letter-spacing:-.02em;border-bottom:1px solid rgba(255,255,255,.06)}
$ .box{position:relative;flex:none;width:58u;height:58u;margin-left:24u;border-radius:17u;box-shadow:0 0 0 3u rgba(255,255,255,.26) inset}
$ .fillb{position:absolute;inset:0;border-radius:inherit;background:var(--accent);animation:pd-fill .3s cubic-bezier(.2,1.5,.4,1) var(--t) both}
$ .mark{position:absolute;inset:8u;stroke-dashoffset:1;animation:pd-tick .26s ease-out calc(var(--t) + .08s) both}
$ .mark svg{display:block;width:100%;height:100%}
$ .mark path{fill:none;stroke:var(--panel);stroke-width:3.4;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:1}
$ .txt{position:relative;min-width:0;white-space:nowrap;animation:pd-dimtxt .3s linear calc(var(--t) + .05s) both}
$ .strike{position:absolute;left:0;right:0;top:54%;height:3u;background:var(--soft);transform-origin:0 50%;animation:pd-bar .34s cubic-bezier(.5,0,.2,1) calc(var(--t) + .06s) both}
$ .pic{display:block;width:100%;height:550u;object-fit:cover}
$ .say{position:absolute;width:0;height:0;z-index:7;animation:pd-okin .3s var(--ease) var(--t) both,pd-out .16s ease-in var(--e) forwards}
$ .say span{position:absolute;top:30u;white-space:nowrap;padding:12u 24u 14u;border-radius:16u;font-size:34u;font-weight:600;letter-spacing:-.016em;color:var(--panel);background:var(--accent);box-shadow:0 14u 34u rgba(0,0,0,.42)}
$ .ok.float{position:absolute;left:30u;bottom:30u;padding:14u 28u 14u 16u;border-radius:99u;background:color-mix(in srgb,var(--panel) 92%,transparent);box-shadow:0 14u 34u rgba(0,0,0,.42)}
$ .ft{position:relative;height:140u;display:flex;align-items:center;justify-content:space-between;padding:0 40u}
$ .ok{display:inline-flex;align-items:center;gap:16u;font-size:38u;font-weight:600;letter-spacing:-.02em;color:var(--accent);animation:pd-okin .5s var(--ease) var(--t) both}
$ .dot{display:grid;place-items:center;width:46u;height:46u;border-radius:50%;background:var(--accent)}
$ .dot svg{width:30u;height:30u}
$ .dot path{fill:none;stroke:var(--panel);stroke-width:3.4;stroke-linecap:round;stroke-linejoin:round}
$ .go{display:inline-flex;align-items:center;height:84u;padding:0 44u;border-radius:99u;font-size:38u;font-weight:600;letter-spacing:-.02em;color:var(--panel);background:var(--accent);animation:pd-tap .36s ease-out calc(var(--t) - .08s) both}
@keyframes pd-fill{from{transform:scale(0)}to{transform:none}}
@keyframes pd-tick{from{stroke-dashoffset:1}to{stroke-dashoffset:0}}
@keyframes pd-dimtxt{to{color:var(--soft)}}
@keyframes pd-bar{from{transform:scaleX(0)}to{transform:none}}
@keyframes pd-okin{from{opacity:0;transform:translateX(-18u)}to{opacity:1;transform:none}}
@keyframes pd-out{to{opacity:0}}
@keyframes pd-tap{0%,100%{transform:none}40%{transform:scale(.93);filter:brightness(1.25)}}
"""


# Zoom to click


def _zoom_click(item: dict[str, Any]) -> str:
    total = kit.seconds(item)
    click = min(1.15, total * 0.3)
    image = item.get("image")
    width, height = 1000.0, 660.0
    x, y = (_share(item, "x", 0.5, "zoom_click") * width, _share(item, "y", 0.5, "zoom_click") * height) if image else (548.0, 212.0)
    box = (x - 160, y - 100, 320.0, 200.0) if image else (412.7, 150.0, 270.7, 124.0)
    label = item.get("label") or "Total"
    value = item.get("value") or "41%"
    if image:
        shot = f'<img class="pic" src="{kit.esc(image)}" alt="">'
    else:
        shot = (
            '<div class="side"><b></b><i></i><i></i><i></i><i></i></div><div class="pane"><div class="head"><strong>This quarter</strong><span></span></div><div class="tiles">'
            '<div class="tile"><span>Visitors</span><strong>84k</strong></div>'
            f'<div class="tile aim"><span>{kit.esc(label)}</span><strong>{kit.esc(value)}</strong><i class="lift"></i></div>'
            '<div class="tile"><span>Refunds</span><strong>1.2%</strong></div></div>'
            f'<div class="wide">{GRAPH}</div></div>'
        )
    tip = (x + 56, y + 34)
    pointer = _pointer((980.0, 690.0), [(tip[0], tip[1], click)], travel=0.7, leave=(1120.0, 760.0, click + 0.16))
    return (
        f'<div class="bg"></div><div class="frame"><div class="zoom" style="--x:{x:.1f}px;--y:{y:.1f}px;--tx:{width / 2 - x:.1f}px;--ty:{height / 2 - y:.1f}px;--go:{click + 0.14:.3f}s;--s:.3s;--dz:{float(item.get("zoom") or 2.7):.2f}">'
        f'<div class="win" style="--s:.3s"><div class="bar"><i></i><span>app.example.com</span></div><div class="surface">{shot}</div></div>'
        f'<div class="spot" style="left:calc({box[0]:.1f}px * var(--scale));top:calc({box[1]:.1f}px * var(--scale));width:calc({box[2]:.1f}px * var(--scale));height:calc({box[3]:.1f}px * var(--scale))"></div>{_ripple(tip[0], tip[1], click)}{pointer}</div></div>'
        f'<div class="note" style="--t:{click + 0.72:.3f}s"><p>{kit.emphasis(item["text"])}</p></div>'
    )


ZOOM_CSS = """
$ .frame{position:absolute;left:50%;top:50%;width:1000u;height:660u;margin:-330u 0 0 -500u;transform:translateY(-6cqh) scale(.93)}
$ .zoom{position:absolute;inset:0;transform-origin:var(--x) var(--y);animation:pd-zoom 1.2s cubic-bezier(.72,0,.14,1) var(--go) both}
$ .win{position:absolute;inset:0;border-radius:26u;overflow:hidden;background:var(--surface);box-shadow:0 0 0 1px rgba(211,238,252,.16),0 50u 110u rgba(0,0,0,.6);animation:rise .6s var(--ease) both}
$ .bar{display:flex;align-items:center;gap:16u;height:60u;padding:0 22u;background:color-mix(in srgb,var(--panel) 66%,var(--surface));border-bottom:1px solid rgba(255,255,255,.07);font-size:22u;color:var(--soft)}
$ .bar i{width:20u;height:20u;border-radius:50%;background:rgba(255,255,255,.14)}
$ .bar span{padding:6u 24u;border-radius:99u;background:rgba(255,255,255,.07)}
$ .surface{position:absolute;left:0;right:0;top:60u;bottom:0;display:flex}
$ .pic{display:block;width:100%;height:100%;object-fit:cover}
$ .side{flex:none;width:96u;display:flex;flex-direction:column;align-items:center;gap:22u;padding-top:26u;background:color-mix(in srgb,var(--panel) 54%,transparent)}
$ .side b{width:46u;height:46u;border-radius:14u;background:var(--accent)}
$ .side i{width:38u;height:38u;border-radius:12u;background:rgba(255,255,255,.1)}
$ .pane{flex:1;min-width:0;display:flex;flex-direction:column;gap:22u;padding:26u 30u 28u}
$ .head{display:flex;justify-content:space-between;align-items:center;height:42u}
$ .head strong{font-size:34u;font-weight:600;letter-spacing:-.025em}
$ .head span{width:130u;height:34u;border-radius:99u;background:rgba(255,255,255,.08)}
$ .tiles{display:grid;grid-template-columns:repeat(3,1fr);gap:16u}
$ .tile{position:relative;height:124u;padding:16u 20u 0;border-radius:18u;background:rgba(255,255,255,.055);box-shadow:0 0 0 1px rgba(255,255,255,.07) inset}
$ .tile span{display:block;font-size:20u;color:var(--soft);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
$ .tile strong{display:block;margin-top:6u;font-size:58u;font-weight:600;line-height:1;letter-spacing:-.04em}
$ .aim strong{color:var(--accent)}
$ .wide{position:relative;flex:1;--s:.3s;display:flex}
$ .plot{position:relative;flex:1;animation:pd-reveal 1.05s cubic-bezier(.5,0,.2,1) var(--s) both}
$ .plot svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
$ .plot .ar{fill:color-mix(in srgb,var(--accent) 16%,transparent)}
$ .plot .ln{fill:none;stroke:var(--accent);stroke-width:2.2;vector-effect:non-scaling-stroke;stroke-linecap:round;stroke-linejoin:round}
$ .spot{position:absolute;border-radius:18u;box-shadow:0 0 0 2u var(--accent),0 0 0 3000u rgba(4,7,10,.74);animation:pd-in .55s ease-out calc(var(--go) + .3s) both}
$ .note{position:absolute;left:var(--gutter);right:calc(var(--gutter) + var(--rail,0px));bottom:calc(var(--top) * 1.02);animation:rise .62s var(--ease) var(--t) both}
$ .note p{margin:0;padding:30u 38u 34u;border-radius:30u;font-size:60u;font-weight:600;line-height:1.06;letter-spacing:-.034em;text-wrap:balance;background:color-mix(in srgb,var(--panel) 90%,transparent);box-shadow:0 0 0 1px rgba(211,238,252,.15) inset,0 30u 70u rgba(0,0,0,.5)}
@container (min-aspect-ratio:5/4){$ .frame{transform:translateY(-2cqh) scale(1.08)}$ .note{right:auto;max-width:900u;bottom:7cqh}}
@container (min-aspect-ratio:4/5) and (max-aspect-ratio:5/4){$ .frame{transform:translateY(-5cqh) scale(1.02)}$ .note{bottom:7cqh}}
@keyframes pd-zoom{0%{transform:none;filter:none}46%{filter:blur(1.5px)}100%{transform:translate(var(--tx),var(--ty)) scale(var(--dz));filter:none}}
@keyframes pd-reveal{from{clip-path:inset(-4% 100% -4% 0)}to{clip-path:inset(-4% -2% -4% 0)}}
"""


# Floating device


def _device(kind: str, screen: str) -> str:
    if kind == "browser":
        return f'<div class="dev br"><div class="body"><div class="bar"><i></i><span>app.example.com</span></div><div class="glass">{screen}<b class="sheen"></b></div></div></div>'
    return f'<div class="dev ph"><div class="back"></div><div class="body"><div class="glass">{screen}<b class="sheen"></b></div></div></div>'


def _floating_device(item: dict[str, Any]) -> str:
    kind = item.get("device") or "phone"
    screen = _screen("pulse" if kind == "phone" else "bars", item.get("screen") or "Overview", 0.62, item.get("image"))
    line = f"<p>{kit.esc(item['label'])}</p>" if item.get("label") else ""
    title = f'<div class="ttl"><h1>{kit.emphasis(item["text"])}</h1>{line}</div>'
    return f'<div class="bg"></div><div class="stage {kind}">{title}<div class="slot"><div class="float"><div class="enter">{_device(kind, screen)}</div></div></div></div>'


DEVICE_CSS = """
$ .stage{position:absolute;inset:0;display:grid;grid-template-rows:auto minmax(0,1fr);gap:3.6cqh;padding:var(--top) calc(var(--gutter) + var(--rail,0px)) 6cqh var(--gutter)}
$ .slot{position:relative;min-height:0;min-width:0;display:grid;place-items:center;container-type:size;perspective:2400u}
$ .float{transform-style:preserve-3d;animation:pd-float var(--dur) linear both}
$ .enter{transform-style:preserve-3d;animation:pd-enter 1.15s cubic-bezier(.16,.9,.2,1) both}
$ .dev{position:relative;transform-style:preserve-3d}
$ .dev.ph{height:min(98cqh,calc(96cqw * 2.04));aspect-ratio:9 / 18.4}
$ .dev.br{width:min(98cqw,calc(96cqh * 1.5));aspect-ratio:3 / 2}
$ .body{position:absolute;inset:0;border-radius:7.4cqh;background:#0b1117;box-shadow:0 0 0 1u rgba(255,255,255,.16),0 60u 120u rgba(0,0,0,.6)}
$ .dev.ph .body{padding:1.1cqh}
$ .back{position:absolute;inset:0;border-radius:7.4cqh;background:#1b242d;transform:translateZ(-16u)}
$ .glass{position:relative;width:100%;height:100%;overflow:hidden;border-radius:6.4cqh;background:var(--surface)}
$ .dev.br .body{border-radius:26u;display:flex;flex-direction:column;overflow:hidden;background:color-mix(in srgb,var(--panel) 66%,var(--surface))}
$ .dev.br .glass{flex:1;min-height:0;height:auto;border-radius:0}
$ .bar{flex:none;display:flex;align-items:center;gap:16u;height:64u;padding:0 22u;font-size:24u;color:var(--soft)}
$ .bar i{width:20u;height:20u;border-radius:50%;background:rgba(255,255,255,.14)}
$ .bar span{padding:6u 24u;border-radius:99u;background:rgba(255,255,255,.07)}
$ .sheen{position:absolute;inset:-20%;background:linear-gradient(112deg,transparent 42%,rgba(255,255,255,.13) 50%,transparent 58%);animation:pd-sheen 1.5s cubic-bezier(.3,0,.2,1) .7s both}
@container (min-aspect-ratio:4/5){$ .stage.phone{grid-template-rows:minmax(0,1fr);grid-template-columns:minmax(0,1.1fr) minmax(0,1fr);gap:calc(var(--gutter) * .6);padding:6cqh var(--gutter);align-items:center}$ .stage.phone .slot{height:88cqh}$ .stage.browser{padding-top:7cqh}}
@keyframes pd-float{from{transform:translateY(10u) rotateY(-3deg)}to{transform:translateY(-12u) rotateY(5deg)}}
@keyframes pd-enter{from{opacity:0;transform:translateY(24cqh) rotateX(34deg) rotateY(-34deg) rotateZ(5deg) scale(.82)}34%{opacity:1}to{opacity:1;transform:rotateX(5deg) rotateY(-13deg) rotateZ(1.2deg)}}
@keyframes pd-sheen{from{transform:translateX(-62%)}to{transform:translateX(62%)}}
"""


# Device splay


def _device_splay(item: dict[str, Any]) -> str:
    rows = kit.rows(item, "screens")
    count = len(rows)
    cards = []
    for index, row in enumerate(rows):
        spot = index - (count - 1) / 2
        begin = 0.32 + abs(spot) * 0.1
        screen = _screen(SCREENS[index % len(SCREENS)], row["text"], begin + 0.5, row.get("image"))
        cards.append(f'<div class="ph" style="--k:{spot:.2f};--a:{abs(spot):.2f};--t:{begin:.3f}s"><div class="face">{screen}</div></div>')
    title = f'<div class="ttl"><h1>{kit.emphasis(item["text"])}</h1></div>' if item.get("text") else ""
    return f'<div class="bg"></div><div class="stage">{title}<div class="slot" style="--span:{max(count - 1, 1)}"><div class="rig"><div class="yaw">{"".join(cards)}</div></div></div></div>'


SPLAY_CSS = """
$ .stage{position:absolute;inset:0;display:grid;grid-template-rows:auto minmax(0,1fr);gap:3cqh;padding:var(--top) calc(var(--gutter) + var(--rail,0px)) 8cqh var(--gutter)}
$ .slot{position:relative;min-height:0;container-type:size;perspective:2200u;--h:min(72cqh,84cqw);--w:calc(var(--h) * .49);--gap:min(calc((100cqw - var(--w)) / var(--span)),calc(var(--w) * 1.22))}
$ .rig,$ .yaw{position:absolute;left:50%;top:50%;width:0;height:0;transform-style:preserve-3d}
$ .rig{transform:rotateX(9deg)}
$ .yaw{animation:pd-yaw var(--dur) linear both}
$ .ph{position:absolute;left:calc(var(--w) / -2);top:calc(var(--h) / -2);width:var(--w);height:var(--h);transform-style:preserve-3d;animation:pd-fan 1.05s cubic-bezier(.2,.95,.24,1) var(--t) both}
$ .face{position:absolute;inset:0;padding:calc(var(--h) * .013);border-radius:calc(var(--h) * .082);background:#0b1117;box-shadow:0 0 0 1u rgba(255,255,255,.17),0 40u 90u rgba(0,0,0,.58)}
$ .face .scr{position:relative;width:100%;height:100%;border-radius:calc(var(--h) * .07)}
@container (min-aspect-ratio:5/4){$ .stage{padding-top:7cqh}}
@container (min-aspect-ratio:4/5) and (max-aspect-ratio:5/4){$ .stage{padding-top:7cqh}}
@keyframes pd-fan{from{opacity:0;transform:translateZ(-420u) rotateX(16deg) scale(.86)}30%{opacity:1}to{opacity:1;transform:translateX(calc(var(--k) * var(--gap))) translateZ(calc(var(--a) * -130u)) rotateY(calc(var(--k) * -15deg)) rotateZ(calc(var(--k) * 1.8deg))}}
@keyframes pd-yaw{from{transform:rotateY(-4deg)}to{transform:rotateY(4deg)}}
"""


# Prompt to result


def _prompt_result(item: dict[str, Any]) -> str:
    total = kit.seconds(item)
    typed, finished = _typed(item["text"], 0.42, min(total * 0.27, 1.9))
    send = finished + 0.16
    start = send + max(0.55, total * 0.1)
    words = kit.tokens(item["result"])
    step = min(0.075, max(total * 0.9 - start, 0.2) / max(len(words), 1))
    cells = ""
    for index, (word, hot) in enumerate(words):
        gap = "" if index == 0 or not (word[0].isalnum() or word[0] in "\"'(") else " "
        cells += f'{gap}<span class="wd{" hot" if hot else ""}" style="--t:{start + index * step:.3f}s">{kit.esc(word)}</span>'
    status = kit.esc(item.get("status") or "Working")
    return (
        f'<div class="card"><div class="ask" style="--s:{send:.3f}s"><p>{typed}</p><span class="send">{ARROW}</span></div>'
        f'<div class="out"><div class="state" style="--t:{send + 0.1:.3f}s;--e:{start:.3f}s"><i></i><i></i><i></i><span>{status}</span></div><p class="ans">{cells}</p></div></div>'
    )


PROMPT_CSS = """
$ .ask{position:relative;display:flex;align-items:flex-end;gap:22u;margin:26u 26u 22u;padding:26u 26u 26u 34u;min-height:200u;border-radius:26u;background:rgba(255,255,255,.055);box-shadow:0 0 0 1px rgba(255,255,255,.1) inset;animation:pd-focus .4s ease-out .2s both,pd-blur .3s ease-out var(--s) forwards}
$ .ask p{flex:1;min-width:0;margin:0;align-self:flex-start;font-size:42u;line-height:1.2;letter-spacing:-.02em}
$ .send{flex:none;display:grid;place-items:center;width:76u;height:76u;border-radius:50%;color:var(--panel);background:var(--accent);animation:pd-tap .34s ease-out calc(var(--s) - .1s) both}
$ .send svg{width:44u;height:44u}
$ .send path{fill:none;stroke:currentColor;stroke-width:2.6;stroke-linecap:round;stroke-linejoin:round}
$ .out{position:relative;min-height:110u;padding:6u 38u 36u}
$ .state{position:absolute;left:38u;top:6u;display:flex;align-items:center;gap:9u;font-size:32u;color:var(--soft);animation:pd-show .2s linear var(--t) both,pd-hide .18s linear var(--e) forwards}
$ .state i{width:14u;height:14u;border-radius:50%;background:var(--accent);animation:pd-wave .8s ease-in-out var(--t) infinite}
$ .state i:nth-child(2){animation-delay:calc(var(--t) + .12s)}
$ .state i:nth-child(3){animation-delay:calc(var(--t) + .24s);margin-right:10u}
$ .ans{margin:0;font-size:40u;line-height:1.26;letter-spacing:-.018em}
$ .wd{display:inline-block;animation:pd-word .34s ease-out var(--t) both}
$ .wd.hot{color:var(--accent)}
@keyframes pd-focus{to{box-shadow:0 0 0 2u var(--accent) inset,0 0 0 8u color-mix(in srgb,var(--accent) 16%,transparent)}}
@keyframes pd-blur{to{box-shadow:0 0 0 1px rgba(255,255,255,.1) inset}}
@keyframes pd-tap{0%,100%{transform:none}45%{transform:scale(.84);filter:brightness(1.3)}}
@keyframes pd-show{from{opacity:0}to{opacity:1}}
@keyframes pd-hide{to{opacity:0}}
@keyframes pd-wave{0%,60%,100%{transform:none;opacity:.5}30%{transform:translateY(-9u);opacity:1}}
@keyframes pd-word{from{opacity:0;transform:translateY(10u);filter:blur(3px)}to{opacity:1;transform:none;filter:none}}
"""


# Chat exchange


def _chat_exchange(item: dict[str, Any]) -> str:
    rows = kit.rows(item, "messages")
    total = kit.seconds(item)
    times = _timed(rows, 0.3, total * 0.74)
    out = []
    for index, row in enumerate(rows):
        side = row.get("side") or ("them" if index % 2 == 0 else "me")
        at = times[index]
        if side == "them" and index:
            wait = min(0.72, max(at - times[index - 1] - 0.2, 0.24))
            out.append(f'<div class="msg them wait" style="--a:{at - wait:.3f}s;--t:{at:.3f}s"><div class="in"><div class="bub dots"><i></i><i></i><i></i></div></div></div>')
        out.append(f'<div class="msg {"me" if side == "me" else "them"}" style="--a:{at:.3f}s"><div class="in"><div class="bub">{kit.emphasis(row["text"])}</div></div></div>')
    return f'<div class="thread">{"".join(out)}</div>'


CHAT_CSS = """
$ .thread{display:flex;flex-direction:column;justify-content:flex-end;height:640u;width:100%;max-width:880u;overflow:hidden;-webkit-mask-image:linear-gradient(to bottom,transparent,#000 9%);mask-image:linear-gradient(to bottom,transparent,#000 9%)}
$ .msg{display:grid;grid-template-rows:0fr;flex:none;animation:pd-open .42s var(--ease) var(--a) both}
$ .msg.wait{animation:pd-open .3s var(--ease) var(--a) both,pd-shut .2s ease-in var(--t) forwards}
$ .in{min-height:0;overflow:hidden;display:flex;padding:7u 4u}
$ .msg.me .in{justify-content:flex-end}
$ .bub{max-width:80%;padding:22u 30u 24u;border-radius:34u;font-size:42u;line-height:1.18;letter-spacing:-.018em;box-shadow:0 14u 34u rgba(0,0,0,.3);animation:pd-bub .46s cubic-bezier(.2,1.4,.36,1) var(--a) both}
$ .them .bub{color:var(--ink);background:color-mix(in srgb,var(--surface) 92%,var(--ink));border-bottom-left-radius:10u;transform-origin:0 100%}
$ .me .bub{color:var(--panel);background:var(--accent);border-bottom-right-radius:10u;transform-origin:100% 100%;font-weight:500}
$ .me .bub em{color:inherit;text-decoration:underline;text-underline-offset:.14em}
$ .dots{display:flex;gap:11u;padding:32u 34u}
$ .dots i{width:17u;height:17u;border-radius:50%;background:var(--soft);animation:pd-wave .8s ease-in-out var(--a) infinite}
$ .dots i:nth-child(2){animation-delay:calc(var(--a) + .12s)}
$ .dots i:nth-child(3){animation-delay:calc(var(--a) + .24s)}
@keyframes pd-open{to{grid-template-rows:1fr}}
@keyframes pd-shut{to{grid-template-rows:0fr;opacity:0}}
@keyframes pd-bub{from{opacity:0;transform:scale(.72)}to{opacity:1;transform:none}}
@keyframes pd-wave{0%,60%,100%{transform:none;opacity:.5}30%{transform:translateY(-9u);opacity:1}}
"""


# Notification cascade


def _notification_stack(item: dict[str, Any]) -> str:
    rows = kit.rows(item, "alerts")
    total = kit.seconds(item)
    times = _timed(rows, 0.15, total * 0.62)
    cards = []
    for index, row in enumerate(rows):
        label = str(row.get("label") or "Update")
        card = (
            f'<div class="note" style="--t:{times[index]:.3f}s"><div class="face"><span class="mark">{kit.esc(label[:1].upper())}</span>'
            f'<span class="body"><span class="meta"><b>{kit.esc(label)}</b><i>{kit.esc(row.get("time") or "now")}</i></span><span class="text">{kit.emphasis(row["text"])}</span></span></div></div>'
        )
        for later in times[index + 1 :]:
            card = f'<div class="push" style="--t:{later:.3f}s">{card}</div>'
        cards.append(f'<div class="spot" style="z-index:{index + 1}">{card}</div>')
    return f'<div class="pile">{"".join(cards)}</div>'


NOTE_CSS = """
$ .pile{position:relative;width:100%;max-width:880u;height:700u}
$ .spot{position:absolute;left:0;right:0;top:0;height:128u}
$ .push{position:absolute;inset:0;transform-origin:50% 0;animation:pd-push .5s cubic-bezier(.2,1.05,.32,1) var(--t) both}
$ .note{position:absolute;inset:0;animation:pd-drop .58s cubic-bezier(.2,1.3,.36,1) var(--t) both}
$ .face{display:flex;align-items:center;gap:24u;height:100%;padding:0 28u;border-radius:30u;background:color-mix(in srgb,var(--surface) 92%,var(--ink) 4%);box-shadow:0 0 0 1px rgba(211,238,252,.15) inset,0 1px 0 rgba(255,255,255,.09) inset,0 20u 50u rgba(0,0,0,.42)}
$ .mark{flex:none;display:grid;place-items:center;width:78u;height:78u;border-radius:22u;font-size:40u;font-weight:700;color:var(--panel);background:var(--accent)}
$ .body{flex:1;min-width:0;display:flex;flex-direction:column;gap:2u}
$ .meta{display:flex;justify-content:space-between;gap:20u;font-size:27u;color:var(--soft)}
$ .meta b{font-weight:600;color:var(--soft);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
$ .meta i{flex:none;font-style:normal}
$ .text{font-size:35u;line-height:1.14;letter-spacing:-.016em;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden}
@keyframes pd-drop{from{opacity:0;transform:translateY(-120u) scale(.9)}to{opacity:1;transform:none}}
@keyframes pd-push{to{transform:translateY(142u) scale(.975);opacity:.86}}
"""


# Code typing


def _token_class(found: re.Match[str]) -> str:
    kind = found.lastgroup or "other"
    text = found.group()
    if kind == "word":
        rest = found.string[found.end() :]
        return "k" if text in KEYWORDS else "f" if rest.startswith("(") else ""
    return {"comment": "c", "string": "s", "number": "n"}.get(kind, "")


def _code_cells(line: str, clock: float, unit: float) -> tuple[str, float]:
    """A line as coloured characters, each with its own moment. Only the renderer's clock decides what shows."""
    cells = []
    for found in TOKENS.finditer(line):
        tone = _token_class(found)
        for char in found.group():
            gap = unit * (2.0 if char in " .,:;()" else 1.0)
            cells.append(f'<span class="ch{f" {tone}" if tone else ""}" style="--t:{clock:.3f}s;--n:{gap:.3f}s">{kit.esc(char)}<i class="car"></i></span>')
            clock += gap
    return "".join(cells), clock


def _code_typing(item: dict[str, Any]) -> str:
    total = kit.seconds(item)
    lines = [str(row["text"]) for row in kit.rows(item, "lines")]
    size = sum(len(line) for line in lines) + len(lines) * 3
    unit = min(total * 0.74 / max(size * 1.2, 1), 0.05)
    clock, rows = 0.42, []
    for number, line in enumerate(lines, 1):
        begin = clock
        cells, clock = _code_cells(line, clock, unit)
        clock += unit * 3
        rows.append(f'<div class="row" style="--t:{begin:.3f}s;--e:{clock:.3f}s"><i class="hl"></i><span class="no">{number}</span><code>{cells or "&nbsp;"}</code></div>')
    name = kit.esc(item.get("name") or "main.py")
    return f'<div class="card"><div class="tab"><span>{name}</span></div><div class="ed">{"".join(rows)}</div></div>'


CODE_CSS = """
$ .card{font-family:var(--mono)}
$ .tab{display:flex;align-items:flex-end;height:78u;padding:0 22u;background:color-mix(in srgb,var(--panel) 72%,var(--surface))}
$ .tab span{padding:14u 28u 16u;border-radius:18u 18u 0 0;font-size:28u;font-weight:500;color:var(--ink);background:color-mix(in srgb,var(--surface) 86%,var(--panel))}
$ .ed{padding:22u 26u 30u 0}
$ .row{position:relative;display:flex;gap:26u;font-size:33u;line-height:1.46;animation:pd-show .01s linear var(--t) both}
$ .hl{position:absolute;inset:0 -26u 0 0;background:color-mix(in srgb,var(--accent) 8%,transparent);box-shadow:3u 0 0 var(--accent) inset;animation:pd-off .01s linear var(--e) forwards}
$ .no{position:relative;flex:none;width:68u;text-align:right;color:color-mix(in srgb,var(--soft) 52%,transparent)}
$ code{position:relative;flex:1;min-width:0;font-family:inherit;white-space:pre-wrap;word-break:break-all}
$ .k{color:var(--accent)}
$ .s{color:color-mix(in srgb,var(--accent) 34%,#ffc792)}
$ .n{color:color-mix(in srgb,var(--accent) 24%,#9fcbff)}
$ .f{color:color-mix(in srgb,var(--ink) 62%,#9fcbff)}
$ .c{color:color-mix(in srgb,var(--soft) 76%,transparent)}
@keyframes pd-show{from{opacity:0}to{opacity:1}}
@keyframes pd-off{to{opacity:0}}
"""


# Code diff


def _word_marks(old: str, new: str) -> tuple[str, str]:
    """Mark the words that changed between two versions of one line."""
    left, right = re.findall(r"\s+|\w+|[^\w\s]", old), re.findall(r"\s+|\w+|[^\w\s]", new)
    a, b = [], []
    for tag, i0, i1, j0, j1 in difflib.SequenceMatcher(None, left, right, autojunk=False).get_opcodes():
        before, after = kit.esc("".join(left[i0:i1])), kit.esc("".join(right[j0:j1]))
        a.append(before if tag == "equal" or not before else f"<mark>{before}</mark>")
        b.append(after if tag == "equal" or not after else f"<mark>{after}</mark>")
    return "".join(a), "".join(b)


def _diff_rows(before: list[str], after: list[str]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for tag, i0, i1, j0, j1 in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if tag == "equal":
            rows += [("same", kit.esc(line)) for line in before[i0:i1]]
            continue
        gone, come = before[i0:i1], after[j0:j1]
        paired = [_word_marks(a, b) if difflib.SequenceMatcher(None, a, b).ratio() > 0.55 else (kit.esc(a), kit.esc(b)) for a, b in zip(gone, come, strict=False)]
        rows += [("del", old) for old, _ in paired] + [("del", kit.esc(line)) for line in gone[len(paired) :]]
        rows += [("add", new) for _, new in paired] + [("add", kit.esc(line)) for line in come[len(paired) :]]
    return rows


def _code_diff(item: dict[str, Any]) -> str:
    total = kit.seconds(item)
    before, after = [str(row["text"]) for row in kit.rows(item, "before")], [str(row["text"]) for row in kit.rows(item, "after")]
    rows = _diff_rows(before, after)
    removed = sum(1 for kind, _ in rows if kind == "del")
    added = sum(1 for kind, _ in rows if kind == "add")
    start, span = total * 0.26, total * 0.3
    changed = max(removed + added, 1)
    index, cells = 0, []
    number = 0
    for kind, text in rows:
        at = start + span * index / changed
        number += 0 if kind == "del" else 1
        sign = {"del": "-", "add": "+"}.get(kind, " ")
        cells.append(f'<div class="ln {kind}" style="--t:{at:.3f}s"><div class="in"><span class="sg">{sign}</span><code>{text or "&nbsp;"}</code></div></div>')
        index += kind != "same"
    name = kit.esc(item.get("name") or "main.py")
    tallest = max(len(before), len(after))
    return f'<div class="card"><div class="hd"><span>{name}</span><span class="tally" style="--t:{start:.3f}s"><b>+{added}</b> <i>-{removed}</i></span></div><div class="lines" style="--r:{tallest}">{"".join(cells)}</div></div>'


DIFF_CSS = """
$ .card{font-family:var(--mono);--red:color-mix(in srgb,#ff7a6b 82%,var(--ink))}
$ .hd{display:flex;justify-content:space-between;align-items:center;height:84u;padding:0 34u;font-size:29u;color:var(--soft);border-bottom:1px solid rgba(255,255,255,.08)}
$ .tally{animation:pd-show .3s linear var(--t) both}
$ .tally b{font-weight:600;color:var(--accent)}
$ .tally i{font-style:normal;font-weight:600;color:var(--red)}
$ .lines{box-sizing:content-box;padding:16u 0 24u;min-height:calc(var(--r) * 47u)}
$ .ln{display:grid;grid-template-rows:1fr}
$ .in{min-height:0;overflow:hidden;display:flex;gap:18u;padding:0 30u;font-size:32u;line-height:1.46}
$ .sg{flex:none;width:22u;color:var(--soft)}
$ code{flex:1;min-width:0;font-family:inherit;white-space:pre-wrap;word-break:break-all}
$ .ln.del{animation:pd-del-a .28s linear var(--t) both,pd-shut .46s cubic-bezier(.6,0,.2,1) calc(var(--t) + .6s) forwards}
$ .ln.del code{animation:pd-del-b .28s linear var(--t) both}
$ .ln.add{grid-template-rows:0fr;animation:pd-open .5s cubic-bezier(.2,.9,.25,1) calc(var(--t) + .55s) both,pd-add .5s ease-out calc(var(--t) + .55s) both}
$ mark{background:none;color:inherit;border-radius:6u}
$ .del mark{background:color-mix(in srgb,#ff7a6b 34%,transparent)}
$ .add mark{background:color-mix(in srgb,var(--accent) 30%,transparent)}
$ .del .sg{color:var(--red)}
$ .add .sg{color:var(--accent)}
@keyframes pd-show{from{opacity:0}to{opacity:1}}
@keyframes pd-del-a{to{background:color-mix(in srgb,#ff7a6b 15%,transparent)}}
@keyframes pd-del-b{to{color:var(--red)}}
@keyframes pd-shut{to{grid-template-rows:0fr;opacity:0}}
@keyframes pd-open{to{grid-template-rows:1fr}}
@keyframes pd-add{from{background:color-mix(in srgb,var(--accent) 34%,transparent)}to{background:color-mix(in srgb,var(--accent) 11%,transparent)}}
"""


# Terminal run


def _terminal_run(item: dict[str, Any]) -> str:
    total = kit.seconds(item)
    typed, finished = _typed(str(item["text"]), 0.42, min(total * 0.24, 1.5))
    rows = kit.rows(item, "output")
    times = _timed(rows, finished + 0.28, total * 0.8)
    cells = [f'<div class="ln cmd"><span class="ps">$</span><span class="tx">{typed}</span></div>']
    for index, row in enumerate(rows):
        kind = row.get("kind") or ""
        mark = f'<span class="tk">{TICK}</span>' if kind == "ok" else ""
        cells.append(f'<div class="ln {kind}" style="--t:{times[index]:.3f}s">{mark}<span class="tx">{kit.emphasis(row["text"])}</span></div>')
    end = times[-1] + 0.24 if times else finished + 0.3
    cells.append(f'<div class="ln" style="--t:{end:.3f}s"><span class="ps">$</span><span class="blink"></span></div>')
    late = max(len(cells) - WINDOW, 0)
    moments = [*([0.0] + times), end]
    body = "".join(cells)
    for at in moments[len(moments) - late :]:
        body = f'<div class="sc" style="--t:{at:.3f}s">{body}</div>'
    name = kit.esc(item.get("name") or "~/project")
    return f'<div class="card"><div class="hd"><span class="tiny"></span><span>{name}</span></div><div class="term"><div class="clipb"><div class="col">{body}</div></div></div></div>'


WINDOW = 7

TERM_CSS = """
$ .card{font-family:var(--mono);background:color-mix(in srgb,var(--panel) 92%,#000)}
$ .hd{display:flex;align-items:center;gap:16u;height:70u;padding:0 30u;font-size:27u;color:var(--soft);background:rgba(255,255,255,.04)}
$ .tiny{width:18u;height:18u;border-radius:50%;background:var(--accent)}
$ .term{padding:22u 30u}
$ .clipb{height:calc(58u * 7);overflow:hidden}
$ .col{position:relative}
$ .sc{animation:pd-sc .01s linear var(--t) both}
$ .ln{display:flex;align-items:center;gap:16u;height:58u;font-size:33u;white-space:nowrap;color:color-mix(in srgb,var(--ink) 76%,var(--soft));animation:pd-show .01s linear var(--t,0s) both}
$ .ps{color:var(--accent);font-weight:600}
$ .ch{white-space:pre}
$ .cmd{color:var(--ink);font-weight:500}
$ .ok{color:var(--accent);font-weight:500}
$ .warn{color:color-mix(in srgb,#ffc792 76%,var(--ink))}
$ .dim{color:var(--soft)}
$ .tk{flex:none;width:36u;height:36u}
$ .tk svg{display:block;width:100%;height:100%}
$ .tk path{fill:none;stroke:currentColor;stroke-width:3;stroke-linecap:round;stroke-linejoin:round}
$ .blink{width:20u;height:38u;background:var(--ink);animation:pd-blink 1s steps(2) var(--t,0s) infinite}
@keyframes pd-show{from{opacity:0}to{opacity:1}}
@keyframes pd-sc{to{transform:translateY(-58u)}}
"""


# Feature carousel


def _feature_carousel(item: dict[str, Any]) -> str:
    rows = kit.rows(item, "screens")
    total = kit.seconds(item)
    count = len(rows)
    anchored = any(row.get("at") is not None for row in rows)
    times = [row["delay"] for row in rows] if anchored else _spread(count, 0.0, total * (1 - 1 / (count + 0.6)))
    cards, caps, ticks = [], [], []
    for index, row in enumerate(rows):
        arrive, leave = (times[index] if index else 0.0), (times[index + 1] if index + 1 < count else None)
        screen = _screen(SCREENS[index % len(SCREENS)], HEADINGS[index % len(HEADINGS)], arrive + 0.42, row.get("image"))
        card = f'<div class="pane">{screen}</div>'
        if leave is not None:
            card = f'<div class="off" style="--t:{leave:.3f}s">{card}</div>'
        if index:
            card = f'<div class="on" style="--t:{arrive:.3f}s">{card}</div>'
        else:
            card = f'<div class="first">{card}</div>'
        cards.append(f'<div class="slide" style="--i:{index}">{card}</div>')
        out = f";--e:{leave:.3f}s" if leave is not None else ""
        caps.append(f'<p class="{"last" if leave is None else ""}" style="--t:{arrive + (0.3 if index else 0.25):.3f}s{out}">{kit.emphasis(row["text"])}</p>')
        ticks.append(f'<i style="--t:{arrive:.3f}s;--e:{(leave if leave is not None else total + 1):.3f}s"></i>')
    track = "".join(cards)
    for at in times[1:]:
        track = f'<div class="step" style="--t:{at:.3f}s">{track}</div>'
    return f'<div class="bg"></div><div class="rail"><div class="mid">{track}</div></div><div class="caps">{"".join(caps)}<div class="dots">{"".join(ticks)}</div></div>'


CAROUSEL_CSS = """
$ {--h:min(56cqh,86cqw);--w:calc(var(--h) * .6);--pitch:calc(var(--w) * .93);--cy:calc((100cqh - var(--h) - 4cqh - 230px * var(--scale)) / 2)}
$ .rail{position:absolute;left:0;right:0;top:0;bottom:0;-webkit-mask-image:linear-gradient(to right,transparent 2%,#000 16%,#000 84%,transparent 98%);mask-image:linear-gradient(to right,transparent 2%,#000 16%,#000 84%,transparent 98%)}
$ .mid{position:absolute;left:50%;top:calc(var(--cy) + var(--h) / 2);width:0;height:0}
$ .step{position:absolute;left:0;top:0;width:0;height:0;animation:pd-step .72s cubic-bezier(.76,0,.16,1) var(--t) both}
$ .slide{position:absolute;left:calc(var(--i) * var(--pitch) - var(--w) / 2);top:calc(var(--h) / -2);width:var(--w);height:var(--h)}
$ .on,$ .off,$ .first{position:absolute;inset:0}
$ .first{animation:pd-first .7s var(--ease) both}
$ .on{transform:scale(.8);opacity:.42;animation:pd-to .72s cubic-bezier(.76,0,.16,1) var(--t) both}
$ .off{animation:pd-from .72s cubic-bezier(.76,0,.16,1) var(--t) both}
$ .pane{position:absolute;inset:0;border-radius:calc(var(--h) * .055);overflow:hidden;box-shadow:0 0 0 1px rgba(211,238,252,.16),0 40u 90u rgba(0,0,0,.55)}
$ .caps{position:absolute;left:var(--gutter);right:calc(var(--gutter) + var(--rail,0px));top:calc(var(--cy) + var(--h) + 4cqh);text-align:center}
$ .caps p{position:absolute;left:0;right:0;top:0;margin:0;font-size:72u;font-weight:600;line-height:1.04;letter-spacing:-.036em;text-wrap:balance;animation:pd-capin .5s var(--ease) var(--t) both,pd-capout .28s ease-in var(--e) forwards}
$ .caps p.last{animation:pd-capin .5s var(--ease) var(--t) both}
$ .dots{position:absolute;left:0;right:0;top:calc(170u);display:flex;justify-content:center;gap:16u}
$ .dots i{width:44u;height:8u;border-radius:9u;background:rgba(255,255,255,.18);animation:pd-doton .2s linear var(--t) both,pd-dotoff .2s linear var(--e) forwards}
@keyframes pd-step{to{transform:translateX(calc(var(--pitch) * -1))}}
@keyframes pd-first{from{opacity:0;transform:translateY(5cqh) scale(.92)}to{opacity:1;transform:none}}
@keyframes pd-to{to{transform:none;opacity:1}}
@keyframes pd-from{to{transform:scale(.8);opacity:.42}}
@keyframes pd-capin{from{opacity:0;transform:translateY(28u)}to{opacity:1;transform:none}}
@keyframes pd-capout{to{opacity:0;transform:translateY(-18u)}}
@keyframes pd-doton{to{background:var(--accent)}}
@keyframes pd-dotoff{to{background:rgba(255,255,255,.18)}}
"""


# Bento assemble


BENTO = {
    3: ("1 / 1 / 3 / 5", "1 / 5 / 2 / 7", "2 / 5 / 3 / 7"),
    4: ("1 / 1 / 3 / 5", "1 / 5 / 2 / 7", "2 / 5 / 3 / 7", "3 / 1 / 4 / 7"),
    5: ("1 / 1 / 3 / 5", "1 / 5 / 2 / 7", "2 / 5 / 3 / 7", "3 / 1 / 4 / 4", "3 / 4 / 4 / 7"),
    6: ("1 / 1 / 3 / 5", "1 / 5 / 2 / 7", "2 / 5 / 3 / 7", "3 / 1 / 4 / 3", "3 / 3 / 4 / 5", "3 / 5 / 4 / 7"),
}


def _bento_viz(kind: int, delay: float) -> str:
    if kind == 0:
        return f'<div class="viz area" style="--s:{delay:.3f}s">{GRAPH}</div>'
    if kind == 1:
        return f'<div class="viz ringv" style="--s:{delay:.3f}s"><svg viewBox="0 0 100 100" aria-hidden="true"><circle class="trk" cx="50" cy="50" r="40"/><circle class="arc" cx="50" cy="50" r="40" pathLength="1"/></svg></div>'
    if kind == 2:
        bars = "".join(f'<i style="--h:{value}%;--i:{index}"></i>' for index, value in enumerate((42, 66, 54, 88, 72)))
        return f'<div class="viz barsv" style="--s:{delay:.3f}s">{bars}</div>'
    if kind == 3:
        faces = "".join(f'<i style="--i:{index}">{letter}</i>' for index, letter in enumerate("ANMJ"))
        return f'<div class="viz facesv" style="--s:{delay:.3f}s">{faces}</div>'
    if kind == 4:
        return f'<div class="viz sw" style="--s:{delay:.3f}s"><i></i></div>'
    return f'<div class="viz dotsv" style="--s:{delay:.3f}s">{"".join(f"<i style=--i:{index}></i>" for index in range(12))}</div>'


def _bento_grid(item: dict[str, Any]) -> str:
    rows = kit.rows(item, "tiles")[:6]
    count = max(len(rows), 3)
    layout = BENTO[count]
    times = _timed(rows, 0.12, 0.12 + 0.11 * (len(rows) - 1))
    middle = (3.5, 2.5 if count > 3 else 2.0)
    cells = []
    for index, row in enumerate(rows):
        area = layout[index]
        r0, c0, r1, c1 = (int(part) for part in area.split(" / "))
        dx, dy = (c0 + c1) / 2 - middle[0], (r0 + r1) / 2 - middle[1]
        reach = math.hypot(dx, dy) or 1.0
        value = f"<strong>{kit.esc(row['value'])}</strong>" if row.get("value") else ""
        viz = _bento_viz(index if index < 6 else index % 6, times[index] + 0.34)
        cells.append(
            f'<div class="tile t{index}" style="grid-area:{area};--t:{times[index]:.3f}s;--fx:{dx / reach * 340:.0f};--fy:{dy / reach * 340:.0f};--fr:{dx * 3.4:.1f}deg">'
            f'{viz}<div class="copy">{value}<span>{kit.emphasis(row["text"])}</span></div><b class="glint"></b></div>'
        )
    return f'<div class="grid r{3 if count > 3 else 2}">{"".join(cells)}</div>'


BENTO_CSS = """
$ .grid{display:grid;grid-template-columns:repeat(6,1fr);gap:16u;width:100%;max-width:880u;height:640u}
$ .grid.r3{grid-template-rows:repeat(3,1fr)}
$ .grid.r2{grid-template-rows:repeat(2,1fr);height:440u}
$ .tile{position:relative;overflow:hidden;min-width:0;min-height:0;display:flex;flex-direction:column;justify-content:flex-end;padding:22u 26u 22u;border-radius:30u;background:color-mix(in srgb,var(--surface) 92%,var(--ink) 3%);box-shadow:0 0 0 1px rgba(211,238,252,.15) inset,0 1px 0 rgba(255,255,255,.09) inset,0 24u 60u rgba(0,0,0,.44);animation:pd-snap .68s cubic-bezier(.2,1.26,.36,1) var(--t) both}
$ .t0{background:linear-gradient(165deg,color-mix(in srgb,var(--accent) 22%,var(--surface)),var(--surface) 70%)}
$ .copy{position:relative;display:flex;flex-direction:column;gap:2u}
$ .copy strong{font-size:58u;font-weight:600;line-height:1;letter-spacing:-.045em}
$ .t0 .copy strong{font-size:112u}
$ .copy span{font-size:29u;line-height:1.12;letter-spacing:-.012em;color:var(--soft)}
$ .t0 .copy span{font-size:34u;color:var(--ink)}
$ .viz{position:absolute;--s:0s}
$ .glint{position:absolute;inset:-30%;background:linear-gradient(115deg,transparent 44%,rgba(255,255,255,.1) 50%,transparent 56%);animation:pd-gl 1.1s cubic-bezier(.3,0,.2,1) calc(var(--t) + .9s) both}
$ .area{left:0;right:0;top:0;bottom:44%}
$ .plot{position:absolute;inset:18u 0 0;animation:pd-reveal 1.05s cubic-bezier(.5,0,.2,1) var(--s) both}
$ .plot svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
$ .plot .ar{fill:color-mix(in srgb,var(--accent) 18%,transparent)}
$ .plot .ln{fill:none;stroke:var(--accent);stroke-width:2.6;vector-effect:non-scaling-stroke;stroke-linecap:round;stroke-linejoin:round}
$ .ringv{right:18u;top:18u;width:76u;height:76u;stroke-dashoffset:1;animation:pd-arc .9s cubic-bezier(.4,0,.2,1) var(--s) both}
$ .ringv svg{display:block;width:100%;height:100%;transform:rotate(-90deg)}
$ .ringv circle{fill:none;stroke-width:14}
$ .trk{stroke:rgba(255,255,255,.1)}
$ .arc{stroke:var(--accent);stroke-linecap:round;stroke-dasharray:1 1}
$ .barsv{right:22u;top:20u;left:22u;height:56u;display:flex;align-items:flex-end;gap:8u}
$ .barsv i{flex:1;height:var(--h);border-radius:6u 6u 2u 2u;background:var(--accent);transform-origin:50% 100%;animation:pd-grow .6s cubic-bezier(.2,1.25,.35,1) calc(var(--s) + var(--i) * .06s) both}
$ .facesv{left:22u;top:20u;display:flex}
$ .facesv i{display:grid;place-items:center;width:54u;height:54u;margin-right:-14u;border-radius:50%;font-style:normal;font-size:26u;font-weight:700;color:var(--panel);background:color-mix(in srgb,var(--accent) calc(100% - var(--i) * 20%),var(--ink));box-shadow:0 0 0 4u var(--surface);animation:pd-pop .46s cubic-bezier(.2,1.5,.36,1) calc(var(--s) + var(--i) * .08s) both}
$ .sw{right:22u;top:22u;width:96u;height:54u;border-radius:99u;background:rgba(255,255,255,.14);animation:pd-swbg .3s linear calc(var(--s) + .2s) both}
$ .sw i{position:absolute;left:6u;top:6u;width:42u;height:42u;border-radius:50%;background:var(--ink);animation:pd-swgo .34s cubic-bezier(.3,1.4,.5,1) calc(var(--s) + .2s) both}
$ .dotsv{left:22u;top:20u;right:64u;display:grid;grid-template-columns:repeat(6,1fr);gap:7u}
$ .dotsv i{aspect-ratio:1;border-radius:50%;background:var(--accent);animation:pd-pop .34s ease-out calc(var(--s) + var(--i) * .05s) both}
@keyframes pd-snap{from{opacity:0;transform:translate(calc(var(--fx) * 1u),calc(var(--fy) * 1u)) rotate(var(--fr)) scale(.76)}38%{opacity:1}to{opacity:1;transform:none}}
@keyframes pd-gl{from{transform:translateX(-70%)}to{transform:translateX(70%)}}
@keyframes pd-reveal{from{clip-path:inset(-4% 100% -4% 0)}to{clip-path:inset(-4% -2% -4% 0)}}
@keyframes pd-arc{from{stroke-dashoffset:1}to{stroke-dashoffset:.26}}
@keyframes pd-grow{from{transform:scaleY(0)}to{transform:none}}
@keyframes pd-pop{from{opacity:0;transform:scale(0)}to{opacity:1;transform:none}}
@keyframes pd-swbg{to{background:var(--accent)}}
@keyframes pd-swgo{to{transform:translateX(42u);background:var(--panel)}}
"""


# State morph


def _state_morph(item: dict[str, Any]) -> str:
    rows = kit.rows(item, "states")[:4]
    total = kit.seconds(item)
    count = len(rows)
    if count < 2:
        raise AltocueError('state_morph needs at least two rows in "states".', fix="See `altocue library show state_morph`.")
    kinds = {2: ("pill", "done"), 3: ("pill", "bar", "done"), 4: ("pill", "bar", "dot", "done")}.get(count, ("pill", "done"))
    times = _timed(rows, 0.0, total * 0.62)
    width = 2 * 62 + 27 * len(str(rows[0]["text"]))
    done_w = min(2 * 52 + 76 + 25 * len(str(rows[-1]["text"])), 840)
    sizes = {"pill": (width, 128, 64), "bar": (780, 30, 15), "dot": (156, 156, 78), "done": (done_w, 128, 64)}
    box = ";".join(f"--w{i}:calc({sizes[kind][0]}px * var(--scale));--h{i}:calc({sizes[kind][1]}px * var(--scale));--r{i}:calc({sizes[kind][2]}px * var(--scale))" for i, kind in enumerate(kinds))
    moments = ";".join(f"--m{i}:{times[i] - 0.1:.3f}s" for i in range(1, count))
    layers = []
    for index, kind in enumerate(kinds):
        text = rows[index]["text"]
        arrive = times[index] + 0.22 if index else 0.0
        leave = times[index + 1] - 0.14 if index + 1 < count else None
        off = f";--e:{leave:.3f}s" if leave is not None else ""
        cls = f"lay {kind}{' stay' if leave is None else ''}"
        if kind == "pill":
            layers.append(f'<div class="{cls}" style="--t:{arrive:.3f}s{off}"><span>{kit.esc(text)}</span></div>')
        elif kind == "bar":
            span = max((times[index + 1] if index + 1 < count else total) - times[index] - 0.2, 0.2)
            layers.append(f'<div class="{cls}" style="--t:{arrive:.3f}s{off};--n:{span:.3f}s"><i></i></div>')
        elif kind == "dot":
            layers.append(f'<div class="{cls}" style="--t:{arrive:.3f}s{off}"><span class="spin"><i></i></span></div>')
        else:
            layers.append(f'<div class="{cls}" style="--t:{arrive:.3f}s{off}"><span class="seal">{TICK}</span><span>{kit.esc(text)}</span></div>')
    side = []
    for index, kind in enumerate(kinds):
        if kind in ("bar", "dot"):
            arrive, leave = times[index] + 0.2, times[index + 1] - 0.12
            lift = 150 if kind == "dot" else 104
            side.append(f'<p style="--t:{arrive:.3f}s;--e:{leave:.3f}s;bottom:calc(50% + {lift - 44}px * var(--scale))">{kit.esc(rows[index]["text"])}</p>')
    press = max(times[1] - 0.34, 0.2)
    return f'<div class="wrap"><div class="say">{"".join(side)}</div><div class="dip" style="--p:{press:.3f}s"><div class="box n{count}" style="{box};{moments}">{"".join(layers)}</div></div></div>'


MORPH_CSS = """
$ .wrap{position:relative;width:100%;max-width:880u;height:340u;display:flex;align-items:center;justify-content:center;animation:rise .5s var(--ease) both}
$ .say{position:absolute;inset:0;text-align:center}
$ .say p{position:absolute;left:0;right:0;margin:0;font-size:42u;font-weight:500;letter-spacing:-.02em;animation:pd-in .24s ease-out var(--t) both,pd-outp .16s ease-in var(--e) forwards}
$ .dip{display:grid;place-items:center;animation:pd-dip .3s ease-out var(--p) both}
$ .box.n2{animation:pd-s1 .58s cubic-bezier(.7,0,.16,1) var(--m1) forwards}
$ .box.n3{animation:pd-s1 .58s cubic-bezier(.7,0,.16,1) var(--m1) forwards,pd-s2 .58s cubic-bezier(.7,0,.16,1) var(--m2) forwards}
$ .box.n4{animation:pd-s1 .58s cubic-bezier(.7,0,.16,1) var(--m1) forwards,pd-s2 .58s cubic-bezier(.7,0,.16,1) var(--m2) forwards,pd-s3 .58s cubic-bezier(.7,0,.16,1) var(--m3) forwards}
$ .box{position:relative;width:var(--w0);height:var(--h0);border-radius:var(--r0);background:var(--accent);box-shadow:0 26u 60u rgba(0,0,0,.45),0 0 0 1px rgba(255,255,255,.14) inset}
$ .lay{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;gap:20u;white-space:nowrap;overflow:hidden;border-radius:inherit;font-size:46u;font-weight:600;letter-spacing:-.024em;color:var(--panel);animation:pd-in .22s ease-out var(--t) both,pd-outp .14s ease-in var(--e) forwards}
$ .lay.stay{animation:pd-in .26s ease-out var(--t) both}
$ .lay.bar{background:color-mix(in srgb,var(--surface) 70%,var(--ink) 12%);justify-content:flex-start}
$ .lay.bar i{display:block;width:100%;height:100%;background:var(--accent);transform-origin:0 50%;animation:pd-load var(--n) cubic-bezier(.4,0,.3,1) var(--t) both}
$ .lay.dot{background:var(--surface)}
$ .spin{width:72u;height:72u;border-radius:50%;box-shadow:0 0 0 9u rgba(255,255,255,.14) inset;position:relative;animation:pd-spin .8s linear var(--t) infinite}
$ .spin i{position:absolute;inset:0;border-radius:50%;border:9u solid transparent;border-top-color:var(--accent)}
$ .seal{display:grid;place-items:center;flex:none;width:68u;height:68u;border-radius:50%;background:var(--panel);stroke-dashoffset:1;animation:pd-tick .34s ease-out calc(var(--t) + .1s) both}
$ .seal svg{width:44u;height:44u}
$ .seal path{fill:none;stroke:var(--accent);stroke-width:3.2;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:1}
@keyframes pd-s1{to{width:var(--w1);height:var(--h1);border-radius:var(--r1)}}
@keyframes pd-s2{to{width:var(--w2);height:var(--h2);border-radius:var(--r2)}}
@keyframes pd-s3{to{width:var(--w3);height:var(--h3);border-radius:var(--r3)}}
@keyframes pd-dip{0%,100%{transform:none}50%{transform:scale(.95)}}
@keyframes pd-in{from{opacity:0}to{opacity:1}}
@keyframes pd-outp{to{opacity:0}}
@keyframes pd-load{from{transform:scaleX(0)}to{transform:none}}
@keyframes pd-spin{to{transform:rotate(360deg)}}
@keyframes pd-tick{from{stroke-dashoffset:1}to{stroke-dashoffset:0}}
"""


# Form success


def _form_success(item: dict[str, Any]) -> str:
    rows = kit.rows(item, "fields")
    total = kit.seconds(item)
    clock = 0.42
    share = total * 0.36 / max(len(rows), 1)
    cells = []
    for row in rows:
        typed, finished = _typed(str(row["text"]), clock + 0.12, share - 0.26)
        cells.append(f'<label style="--t:{clock:.3f}s;--e:{finished + 0.1:.3f}s"><span>{kit.esc(row.get("label") or "Field")}</span><div class="input">{typed}</div></label>')
        clock = finished + 0.14
    press = clock + 0.18
    burst = press + 0.14
    done = f"<p>{kit.esc(item['label'])}</p>" if item.get("label") else ""
    return (
        f'<div class="card"><div class="form"><h2>{kit.emphasis(item["text"])}</h2>{"".join(cells)}<div class="send" style="--t:{press:.3f}s">{kit.esc(item.get("button") or "Continue")}</div></div>'
        f'<div class="ok" style="--t:{burst:.3f}s"><div class="seal"><span class="tickw">{TICK}</span></div><h3>{kit.esc(item["done"])}</h3>{done}</div></div>'
    )


FORM_CSS = """
$ .form{display:flex;flex-direction:column;gap:20u;padding:38u 40u 40u}
$ .form h2{margin:0 0 6u;font-size:52u;font-weight:600;line-height:1.04;letter-spacing:-.034em}
$ label{display:block}
$ label>span{display:block;margin-bottom:9u;font-size:29u;color:var(--soft)}
$ .input{height:90u;padding:0 28u;display:flex;align-items:center;white-space:nowrap;overflow:hidden;border-radius:20u;font-size:40u;letter-spacing:-.016em;background:rgba(255,255,255,.055);box-shadow:0 0 0 1px rgba(255,255,255,.11) inset;animation:pd-foc .2s ease-out var(--t) both,pd-unf .2s ease-out var(--e) forwards}
$ .input .ch{white-space:pre}
$ .send{display:grid;place-items:center;height:98u;margin-top:10u;border-radius:22u;font-size:40u;font-weight:600;letter-spacing:-.02em;color:var(--panel);background:var(--accent);animation:pd-tap .32s ease-out calc(var(--t) - .1s) both}
$ .ok{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:20u;padding:40u;text-align:center;color:var(--panel);background:var(--accent);animation:pd-burst .62s cubic-bezier(.7,0,.16,1) var(--t) both}
$ .ok h3{margin:0;font-size:68u;font-weight:600;line-height:1.02;letter-spacing:-.04em;animation:pd-up .5s var(--ease) calc(var(--t) + .42s) both}
$ .ok p{margin:0;font-size:38u;line-height:1.18;letter-spacing:-.016em;animation:pd-up .5s var(--ease) calc(var(--t) + .5s) both}
$ .seal{display:grid;place-items:center;width:164u;height:164u;border-radius:50%;background:var(--panel);animation:pd-seal .6s cubic-bezier(.2,1.6,.36,1) calc(var(--t) + .28s) both}
$ .tickw{display:block;width:104u;height:104u;stroke-dashoffset:1;animation:pd-tick .36s ease-out calc(var(--t) + .5s) both}
$ .tickw svg{display:block;width:100%;height:100%}
$ .tickw path{fill:none;stroke:var(--accent);stroke-width:3;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:1}
@keyframes pd-foc{to{box-shadow:0 0 0 2u var(--accent) inset,0 0 0 7u color-mix(in srgb,var(--accent) 16%,transparent)}}
@keyframes pd-unf{to{box-shadow:0 0 0 1px rgba(255,255,255,.11) inset}}
@keyframes pd-tap{0%,100%{transform:none}45%{transform:scale(.97);filter:brightness(1.2)}}
@keyframes pd-burst{from{clip-path:circle(0% at 50% 86%)}to{clip-path:circle(120% at 50% 86%)}}
@keyframes pd-seal{from{transform:scale(0)}to{transform:none}}
@keyframes pd-tick{from{stroke-dashoffset:1}to{stroke-dashoffset:0}}
@keyframes pd-up{from{opacity:0;transform:translateY(22u)}to{opacity:1;transform:none}}
"""


# Logo sting


def _logo_sting(item: dict[str, Any]) -> str:
    image = item.get("image")
    arcs = "".join(
        f'<div class="arc" style="--i:{index};--r:{turn}deg"><svg viewBox="0 0 200 200" aria-hidden="true"><circle class="a{index}" cx="100" cy="100" r="{radius}" pathLength="1"/></svg></div>'
        for index, (radius, turn) in enumerate(((84, 0), (56, 120), (28, 240)))
    )
    mark = f'<div class="pic"><img src="{kit.esc(image)}" alt=""></div>' if image else f'<div class="turn">{arcs}</div>'
    letters = kit.letters(str(item["text"]))
    line = f'<p>{kit.esc(item["label"])}</p>' if item.get("label") else ""
    call = f'<div class="call"><span>{kit.esc(item["cta"])}<i class="shine"></i></span></div>' if item.get("cta") else ""
    return f'<div class="bg"></div><div class="lock"><div class="mark">{mark}</div><div class="name"><h1>{letters}</h1>{line}</div>{call}</div>'


STING_CSS = """
$ .lock{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:34u;padding:0 var(--gutter)}
$ .mark{position:relative;flex:none;width:236u;height:236u;animation:pd-in .12s linear both}
$ .turn{position:absolute;inset:0;animation:pd-turn 1s cubic-bezier(.22,1,.3,1) both}
$ .arc{position:absolute;inset:0;stroke-dashoffset:.74;animation:pd-arc .62s cubic-bezier(.5,0,.1,1) calc(var(--i) * .07s + .04s) both}
$ .arc svg{display:block;width:100%;height:100%;transform:rotate(var(--r))}
$ .arc circle{fill:none;stroke:var(--ink);stroke-width:21;stroke-linecap:round;stroke-dasharray:.74 1}
$ .arc .a0{stroke:var(--accent)}
$ .pic{position:absolute;inset:0;animation:pd-pop .6s cubic-bezier(.2,1.4,.36,1) both}
$ .pic img{display:block;width:100%;height:100%;object-fit:contain}
$ .name{text-align:center;overflow:hidden;padding:0 .1em .08em}
$ .name h1{margin:0;font-size:124u;font-weight:600;line-height:1.02;letter-spacing:-.045em;text-wrap:balance}
$ .c{display:inline-block;animation:pd-letter .52s var(--ease) calc(.42s + var(--i) * .026s) both}
$ .word{display:inline-block;white-space:nowrap}
$ .name p{margin:12u 0 0;font-size:42u;letter-spacing:-.016em;color:var(--soft);animation:rise .5s var(--ease) .72s both}
$ .call{flex:none;display:flex;justify-content:center;animation:rise .55s var(--ease) 1.05s both}
$ .call span{position:relative;display:inline-block;overflow:hidden;padding:26u 62u 28u;border-radius:99u;font-size:56u;font-weight:600;letter-spacing:-.025em;color:var(--panel);background:var(--accent)}
$ .shine{position:absolute;top:-20%;bottom:-20%;left:-30%;width:26%;background:linear-gradient(100deg,transparent,rgba(255,255,255,.55),transparent);transform:translateX(0) skewX(-18deg);animation:pd-shine 2.6s cubic-bezier(.5,0,.3,1) 1.5s infinite}
@keyframes pd-shine{0%{transform:translateX(0) skewX(-18deg)}46%,100%{transform:translateX(620%) skewX(-18deg)}}
@container (min-aspect-ratio:5/4){$ .lock{flex-direction:row;flex-wrap:wrap;gap:34u 50u;align-content:center}$ .name{text-align:left}$ .call{flex:0 0 100%}}
@keyframes pd-turn{from{transform:rotate(-150deg) scale(.7)}to{transform:none}}
@keyframes pd-arc{from{stroke-dashoffset:.74}to{stroke-dashoffset:0}}
@keyframes pd-pop{from{opacity:0;transform:scale(.6)}to{opacity:1;transform:none}}
@keyframes pd-letter{from{opacity:0;transform:translateY(62%)}to{opacity:1;transform:none}}
"""


IMAGE = Slot("image", kind="path", required=False)

FORMATS = (
    Format(
        id="cursor_walk",
        title="Cursor walkthrough",
        summary="A pointer clicks through a checklist and finishes the task.",
        group="product",
        jobs=("explain", "prove"),
        slots=(
            Slot("text", limit=30),
            Slot("steps", kind="list", limit=4, each=30),
            Slot("done", limit=20),
            Slot("button", limit=12, required=False),
            IMAGE,
        ),
        example={"text": "Launch *checklist*", "steps": ["Pick the audience", "Write the subject line", "Book the send"], "done": "Scheduled", "button": "Confirm"},
        draw=_cursor_walk,
        seconds=5.2,
        shortest=3.2,
        longest=12.0,
        area="panel",
        tall=740,
        sound="click",
        css=_scoped("cursor_walk", CARD_CSS, POINTER_CSS, CURSOR_CSS),
    ),
    Format(
        id="zoom_click",
        title="Zoom to click",
        summary="A pointer clicks and the camera pushes into the part that matters.",
        group="product",
        jobs=("prove", "explain"),
        slots=(
            Slot("text", limit=56),
            Slot("label", limit=14, required=False),
            Slot("value", limit=6, required=False),
            IMAGE,
            Slot("x", kind="number", required=False),
            Slot("y", kind="number", required=False),
            Slot("zoom", kind="number", required=False),
        ),
        example={"text": "This one number *changed the plan*", "label": "Repeat buyers", "value": "41%"},
        draw=_zoom_click,
        seconds=4.2,
        shortest=2.8,
        longest=9.0,
        area="full",
        tall=1080,
        sound="click",
        needs_alpha=False,
        css=_scoped("zoom_click", FULL_CSS, POINTER_CSS, ZOOM_CSS),
    ),
    Format(
        id="floating_device",
        title="Floating device",
        summary="A screen in a neutral phone or browser turns in space and settles.",
        group="product",
        jobs=("prove", "explain"),
        slots=(
            Slot("text", limit=40),
            Slot("label", limit=60, required=False),
            Slot("device", kind="choice", choices=("phone", "browser"), required=False),
            Slot("screen", limit=16, required=False),
            IMAGE,
        ),
        example={"text": "Your whole week, *one screen*", "label": "Everything in one calm view", "device": "phone", "screen": "Overview"},
        draw=_floating_device,
        seconds=3.8,
        shortest=2.2,
        longest=10.0,
        area="full",
        tall=1080,
        sound="whoosh",
        needs_alpha=False,
        css=_scoped("floating_device", FULL_CSS, SCREEN_CSS, DEVICE_CSS),
    ),
    Format(
        id="device_splay",
        title="Device splay",
        summary="Several neutral phone screens fan out in space from one stack.",
        group="product",
        jobs=("prove", "hook"),
        slots=(Slot("screens", kind="list", limit=5, each=14), Slot("text", limit=40, required=False)),
        example={"text": "One account, *every view*", "screens": ["Today", "Tasks", "Trends"]},
        draw=_device_splay,
        seconds=3.6,
        shortest=2.2,
        longest=10.0,
        area="full",
        tall=1080,
        sound="whoosh",
        needs_alpha=False,
        css=_scoped("device_splay", FULL_CSS, SCREEN_CSS, SPLAY_CSS),
    ),
    Format(
        id="prompt_result",
        title="Prompt to result",
        summary="A prompt types itself, sends, and the answer streams in word by word.",
        group="product",
        jobs=("prove", "explain"),
        slots=(Slot("text", limit=80), Slot("result", limit=190), Slot("status", limit=20, required=False)),
        example={"text": "Summarise last week's feedback in one line", "result": "People love the new export, but *setup takes too long*. Shortening the first step would help most."},
        draw=_prompt_result,
        seconds=8.2,
        shortest=3.2,
        longest=12.0,
        area="panel",
        tall=640,
        sound="tick",
        css=_scoped("prompt_result", CARD_CSS, TYPED_CSS, PROMPT_CSS),
    ),
    Format(
        id="chat_exchange",
        title="Chat exchange",
        summary="Messages arrive in a neutral thread, with typing dots before each reply.",
        group="product",
        jobs=("explain", "prove"),
        slots=(Slot("messages", kind="list", limit=6, each=64),),
        example={
            "messages": [
                {"text": "Is the new plan live yet?", "side": "them"},
                {"text": "Going out today", "side": "me"},
                {"text": "Can it wait for *the numbers*?", "side": "them"},
                {"text": "They came in an hour ago", "side": "me"},
            ]
        },
        draw=_chat_exchange,
        seconds=5.0,
        shortest=2.6,
        longest=14.0,
        tall=640,
        sound="pop",
        css=_scoped("chat_exchange", CHAT_CSS),
    ),
    Format(
        id="notification_stack",
        title="Notification cascade",
        summary="Neutral alerts land one after another and push the older ones down.",
        group="product",
        jobs=("prove", "hook"),
        slots=(Slot("alerts", kind="list", limit=5, each=44),),
        example={
            "alerts": [
                {"text": "New order from a returning customer", "label": "Orders"},
                {"text": "Your invoice was paid", "label": "Billing"},
                {"text": "Three people joined your team", "label": "Team"},
                {"text": "Weekly report is ready", "label": "Reports"},
            ]
        },
        draw=_notification_stack,
        seconds=4.0,
        shortest=2.2,
        longest=10.0,
        tall=700,
        sound="ding",
        css=_scoped("notification_stack", NOTE_CSS),
    ),
    Format(
        id="code_typing",
        title="Code typing",
        summary="Code writes itself line by line with syntax colour.",
        group="product",
        jobs=("explain", "prove"),
        slots=(Slot("lines", kind="list", limit=9, each=38), Slot("name", limit=24, required=False)),
        example={"lines": ["def welcome(name):", "    # say hello first", '    note = f"Hi {name}"', "    return send(note, retries=3)"], "name": "welcome.py"},
        draw=_code_typing,
        seconds=4.6,
        shortest=2.4,
        longest=12.0,
        tall=570,
        sound="tick",
        fonts=("mono",),
        css=_scoped("code_typing", CARD_CSS, TYPED_CSS, CODE_CSS),
    ),
    Format(
        id="code_diff",
        title="Code diff",
        summary="Removed lines flag and close, new lines open between them.",
        group="product",
        jobs=("explain", "prove"),
        slots=(Slot("before", kind="list", limit=8, each=38), Slot("after", kind="list", limit=8, each=38), Slot("name", limit=24, required=False)),
        example={
            "before": ["def total(cart):", "    sum = 0", "    for item in cart:", "        sum += item.price", "    return sum"],
            "after": ["def total(cart):", "    prices = [i.price for i in cart]", "    return round(sum(prices), 2)"],
            "name": "cart.py",
        },
        draw=_code_diff,
        seconds=4.2,
        shortest=2.6,
        longest=10.0,
        tall=520,
        sound="tick",
        fonts=("mono",),
        css=_scoped("code_diff", CARD_CSS, DIFF_CSS),
    ),
    Format(
        id="terminal_run",
        title="Terminal run",
        summary="A command types, runs, and its output scrolls up to a pass.",
        group="product",
        jobs=("prove", "explain"),
        slots=(Slot("text", limit=36), Slot("output", kind="list", limit=7, each=38), Slot("name", limit=24, required=False)),
        example={
            "text": "make release",
            "name": "~/shop",
            "output": [
                {"text": "Reading 214 files", "kind": "dim"},
                {"text": "Building the site", "kind": "dim"},
                {"text": "Running 86 checks", "kind": "dim"},
                {"text": "All 86 passed", "kind": "ok"},
                {"text": "Live in *38 seconds*", "kind": "ok"},
            ],
        },
        draw=_terminal_run,
        seconds=4.4,
        shortest=2.6,
        longest=10.0,
        tall=520,
        sound="tick",
        fonts=("mono",),
        css=_scoped("terminal_run", CARD_CSS, TYPED_CSS, TERM_CSS),
    ),
    Format(
        id="feature_carousel",
        title="Feature carousel",
        summary="Screens slide past one at a time, each with its own line.",
        group="product",
        jobs=("explain", "prove"),
        slots=(Slot("screens", kind="list", limit=5, each=28),),
        example={"screens": ["See the whole week", "Clear tasks fast", "Spot the trend", "Reply in one tap"]},
        draw=_feature_carousel,
        seconds=6.0,
        shortest=3.2,
        longest=14.0,
        area="full",
        tall=1080,
        sound="whoosh",
        needs_alpha=False,
        css=_scoped("feature_carousel", FULL_CSS, SCREEN_CSS, CAROUSEL_CSS),
    ),
    Format(
        id="bento_grid",
        title="Bento assemble",
        summary="A grid of feature cards flies in and snaps together.",
        group="product",
        jobs=("prove", "explain"),
        slots=(Slot("tiles", kind="list", limit=6, each=16),),
        example={
            "tiles": [
                {"text": "Hours saved", "value": "12"},
                {"text": "Uptime", "value": "99.9%"},
                {"text": "Faster reports", "value": "3x"},
                {"text": "Team seats", "value": "48"},
                {"text": "Auto-sync", "value": "On"},
                {"text": "Connections", "value": "120"},
            ]
        },
        draw=_bento_grid,
        seconds=3.4,
        shortest=2.0,
        longest=10.0,
        tall=640,
        sound="pop",
        css=_scoped("bento_grid", BENTO_CSS),
    ),
    Format(
        id="state_morph",
        title="State morph",
        summary="One shape travels from a button through progress to a finished state.",
        group="product",
        jobs=("explain", "transition"),
        slots=(Slot("states", kind="list", limit=4, each=24),),
        example={"states": ["Send invoice", "Sending to Ana", "Checking", "Paid in two days"]},
        draw=_state_morph,
        seconds=3.6,
        shortest=2.2,
        longest=9.0,
        tall=340,
        sound="tick",
        css=_scoped("state_morph", MORPH_CSS),
    ),
    Format(
        id="form_success",
        title="Form success",
        summary="Fields fill, the button goes, and a check lands.",
        group="product",
        jobs=("explain", "close"),
        slots=(
            Slot("text", limit=26),
            Slot("fields", kind="list", limit=3, each=26),
            Slot("done", limit=20),
            Slot("label", limit=44, required=False),
            Slot("button", limit=14, required=False),
        ),
        example={
            "text": "Start your *free trial*",
            "fields": [{"text": "Ana Reis", "label": "Name"}, {"text": "ana@example.com", "label": "Work email"}],
            "done": "You are in",
            "label": "Check your inbox for the first step",
            "button": "Create account",
        },
        draw=_form_success,
        seconds=6.2,
        shortest=3.0,
        longest=10.0,
        tall=720,
        sound="ding",
        css=_scoped("form_success", CARD_CSS, TYPED_CSS, FORM_CSS),
    ),
    Format(
        id="logo_sting",
        title="Logo sting",
        summary="A mark draws itself, turns into place and the name resolves.",
        group="product",
        jobs=("close", "hook"),
        slots=(Slot("text", limit=18), Slot("label", limit=40, required=False), IMAGE, Slot("cta", limit=34, required=False)),
        example={"text": "Tenvale", "label": "Tools for small teams"},
        draw=_logo_sting,
        seconds=2.2,
        shortest=1.4,
        longest=8.0,
        area="full",
        tall=1080,
        sound="whoosh",
        needs_alpha=False,
        css=_scoped("logo_sting", FULL_CSS, STING_CSS),
    ),
)
