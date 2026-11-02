"""Pieces made for films with no footage: a board that does the work, a gauge that carries the plot, and small things that keep a frame alive."""

from __future__ import annotations

import math
import random
from typing import Any

from . import Format, Slot, kit
from .product import _scoped as scoped

TICK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path pathLength="1" d="M5 12.8l4.6 4.7L19 7.4"/></svg>'
COUNT_STEPS = 18
TRACK_SAMPLES = 24
SILHOUETTE_POSES = ("walk", "wave", "point")


def _count(start: int, end: int, begin: float, finish: float) -> str:
    """A counter as a stack of values, each shown for its slice of time. It eases down, fast then slow, and lands exactly."""
    cells = []
    span = max(finish - begin, 0.1)
    last = 0
    values = []
    for step in range(COUNT_STEPS + 1):
        share = step / COUNT_STEPS
        eased = 1 - (1 - share) ** 2.4
        values.append((round(start + (end - start) * eased), begin + span * share))
    for index, (value, at) in enumerate(values):
        leave = values[index + 1][1] if index + 1 < len(values) else None
        if value == last and index:
            continue
        last = value
        out = f";--o:{leave:.3f}s" if leave is not None else ";--o:9999s"
        cells.append(f'<b class="n" style="--t:{at if index else 0:.3f}s{out}">{value:,}</b>')
    return f'<span class="ct">{"".join(cells)}</span>'


def _work_cascade(item: dict[str, Any]) -> str:
    rows = kit.rows(item, "rows")
    seconds = kit.seconds(item)
    count = max(len(rows), 1)
    begin, finish = 0.5, seconds * 0.72
    gap = (finish - begin) / (count + 0.6)
    each = gap * 1.6
    start = int(float(item.get("from", 412)))
    end = int(float(item.get("to", 0)))
    cells = []
    for index, row in enumerate(rows):
        at = begin + index * gap
        chip = f'<em class="tag">{kit.esc(row.get("label", ""))}</em>' if row.get("label") else ""
        cells.append(
            f'<div class="row" style="--i:{index};--t:{at:.3f}s;--n:{at + each:.3f}s"><i class="st"><u></u><s>{TICK}</s></i><span class="txt">{kit.emphasis(str(row.get("text", "")))}</span>{chip}<b class="bar"></b></div>'
        )
    title = kit.emphasis(str(item.get("title", "")))
    done = f'<p class="clear" style="--t:{finish + 0.25:.3f}s">{kit.emphasis(str(item["done"]))}</p>' if item.get("done") else ""
    return (
        f'<div class="board"><header><strong>{title}</strong><span class="pill" style="--t:{finish:.3f}s"><i></i>{_count(start, end, 0.4, finish)}<small>{kit.esc(item.get("label", ""))}</small></span></header>'
        f'<div class="list">{"".join(cells)}</div>{done}</div>'
    )


def _state_gauge(item: dict[str, Any]) -> str:
    """Rows of values that change on cue. Write the table of states first, and the film follows it."""
    seconds = kit.seconds(item)
    made = []
    for index, row in enumerate(item.get("rows") or []):
        states = row.get("states") or [{"value": row.get("value", ""), "level": row.get("level", 0)}]
        total = max(len(states), 1)
        cells, fills = [], []
        previous = float(states[0].get("level", 0))
        for number, state in enumerate(states):
            at = state.get("at")
            start = float(at) - float(item["start"]) if at is not None else seconds * number / total
            after = states[number + 1] if number + 1 < total else None
            stop = (float(after["at"]) - float(item["start"]) if after.get("at") is not None else seconds * (number + 1) / total) if after else 9999.0
            level = float(state.get("level", previous))
            cells.append(f'<b class="v" style="--t:{start:.3f}s;--o:{stop:.3f}s">{kit.esc(state.get("value", ""))}</b>')
            fills.append(f'<i class="fill{" hi" if state.get("hot") else ""}" style="--t:{start:.3f}s;--o:{stop:.3f}s;--a:{previous:.3f};--b:{level:.3f}"></i>')
            previous = level
        made.append(f'<div class="rw" style="--i:{index}"><span class="lb">{kit.esc(row.get("text", ""))}</span><span class="vs">{"".join(cells)}</span><div class="mt">{"".join(fills)}</div></div>')
    title = f'<h3>{kit.esc(item["title"])}</h3>' if item.get("title") else ""
    note = f'<p class="nt">{kit.esc(item["note"])}</p>' if item.get("note") else ""
    return f'<div class="gauge">{title}{"".join(made)}{note}</div>'


def _corner_stamp(item: dict[str, Any]) -> str:
    corner = item.get("corner", "tl")
    label = f'<span class="lb">{kit.esc(item["label"])}</span>' if item.get("label") else ""
    return f'<div class="stamp {kit.esc(corner)}"><i></i><span class="tx">{kit.letters(str(item["text"]))}</span>{label}</div>'


def _tracking_marks(item: dict[str, Any]) -> str:
    """Small squares fixed to moving points, joined by thin lines. Every position and angle comes from the same paths, so a line never leaves its points."""
    rng = random.Random(int(item.get("seed", 5)))
    labels = list(item.get("labels") or [])
    count = max(len(labels), 3)
    points = []
    for index in range(count):
        points.append((rng.uniform(16, 84), rng.uniform(18, 82), rng.uniform(3, 7), rng.uniform(2, 6), rng.uniform(0, 6.28), rng.uniform(0.6, 1.2)))
    name = f"tm{int(item.get('seed', 5))}"
    span = max(kit.seconds(item), 0.5)

    def spot(point: tuple[float, ...], share: float) -> tuple[float, float]:
        x, y, reach_x, reach_y, phase, speed = point
        turn = phase + share * span * speed
        return x + reach_x * math.sin(turn), y + reach_y * math.cos(turn * 0.8)

    rules, marks = [], []
    for index, point in enumerate(points):
        steps = "".join(f"{100 * s / TRACK_SAMPLES:.1f}%{{left:{spot(point, s / TRACK_SAMPLES)[0]:.3f}%;top:{spot(point, s / TRACK_SAMPLES)[1]:.3f}%}}" for s in range(TRACK_SAMPLES + 1))
        rules.append(f"@keyframes {name}p{index}{{{steps}}}")
        text = f'<em>{kit.esc(labels[index])}</em>' if index < len(labels) else ""
        marks.append(f'<div class="mk" style="animation:{name}p{index} {span:.3f}s linear both"><i></i>{text}</div>')
    lines = []
    for index in range(count - 1):
        a, b = points[index], points[index + 1]
        steps = []
        for s in range(TRACK_SAMPLES + 1):
            ax, ay = spot(a, s / TRACK_SAMPLES)
            bx, by = spot(b, s / TRACK_SAMPLES)
            steps.append(f"{100 * s / TRACK_SAMPLES:.1f}%{{clip-path:polygon({ax:.3f}% {ay:.3f}%,{bx:.3f}% {by:.3f}%,{bx:.3f}% {by + 0.14:.3f}%,{ax:.3f}% {ay + 0.14:.3f}%)}}")
        rules.append(f"@keyframes {name}l{index}{{{''.join(steps)}}}")
        lines.append(f'<div class="ln" style="animation:{name}l{index} {span:.3f}s linear both"></div>')
    return f"<style>{''.join(rules)}</style>" + "".join(lines) + "".join(marks)


def _silhouette_act(item: dict[str, Any]) -> str:
    pose = item.get("pose", "walk") if item.get("pose") in SILHOUETTE_POSES else "walk"
    caption = f'<p class="line">{kit.emphasis(str(item["text"]))}</p>' if item.get("text") else ""
    body = (
        '<div class="fig"><div class="hips"><div class="trunk"><div class="head"></div>'
        '<div class="arm a1"><div class="fore"></div></div><div class="arm a2"><div class="fore"></div></div></div>'
        '<div class="leg l1"><div class="shin"></div></div><div class="leg l2"><div class="shin"></div></div></div></div>'
    )
    return f'<div class="stagefield {pose}"><div class="floor"></div>{body}{caption}</div>'


CASCADE_CSS = """
$ .board{position:relative;width:880u;max-width:100%;padding:34u 34u 30u;border-radius:40u;background:color-mix(in srgb,var(--surface) 88%,var(--panel));box-shadow:0 0 0 1.5px color-mix(in srgb,var(--ink) 12%,transparent) inset,0 2u 0 color-mix(in srgb,var(--ink) 9%,transparent) inset,0 60u 140u rgba(0,0,0,.55);animation:wcin .62s cubic-bezier(.16,1,.3,1) both}
$ header{display:flex;align-items:center;justify-content:space-between;gap:20u;padding:4u 8u 26u}
$ strong{font-size:40u;font-weight:600;letter-spacing:-.025em}
$ .pill{position:relative;display:inline-flex;align-items:center;gap:12u;padding:12u 24u 12u 20u;border-radius:99u;font-size:34u;font-weight:600;letter-spacing:-.02em;background:color-mix(in srgb,var(--ink) 8%,transparent);animation:wcpill .5s cubic-bezier(.2,1.5,.4,1) var(--t) both}
$ .pill>i{width:14u;height:14u;border-radius:50%;background:var(--accent);box-shadow:0 0 14u var(--accent);animation:wcbeat 1s ease-in-out infinite alternate}
$ .pill small{font-size:24u;font-weight:500;color:var(--soft);animation:wcsoft .4s linear var(--t) both}
$ .ct{position:relative;display:inline-block;min-width:2.4ch;height:1.15em;line-height:1.15em;text-align:right;font-variant-numeric:tabular-nums}
$ .n{position:absolute;right:0;top:0;font-weight:inherit;opacity:0;animation:wcon .01s linear var(--t) forwards,wcoff .01s linear var(--o) forwards}
$ .list{display:flex;flex-direction:column;gap:12u}
$ .row{position:relative;display:flex;align-items:center;gap:20u;min-height:84u;padding:0 24u;border-radius:22u;overflow:hidden;background:color-mix(in srgb,var(--ink) 5%,transparent);animation:wcrow .5s cubic-bezier(.16,1,.3,1) calc(.08s + var(--i) * .06s) both,wcglow .9s ease-out var(--t) both,wcdone .5s ease-out var(--n) both}
$ .txt{flex:1;min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font-size:32u;font-weight:500;letter-spacing:-.015em}
$ .tag{flex:none;padding:6u 16u;border-radius:99u;font-size:23u;font-style:normal;font-weight:500;color:var(--soft);background:color-mix(in srgb,var(--ink) 7%,transparent)}
$ .st{position:relative;flex:none;width:40u;height:40u;border-radius:50%;box-shadow:0 0 0 2.5u color-mix(in srgb,var(--ink) 22%,transparent) inset}
$ .st u{position:absolute;inset:0;border-radius:50%;background:conic-gradient(var(--accent) 0 28%,transparent 0);-webkit-mask-image:radial-gradient(closest-side,transparent 66%,#000 68%);opacity:0;animation:wcspin .7s linear var(--t) infinite,wcshow .15s linear var(--t) both,wchide .1s linear var(--n) forwards}
$ .st s{position:absolute;inset:-2u;display:grid;place-items:center;border-radius:50%;text-decoration:none;color:var(--panel);background:var(--accent);transform:scale(0);animation:wctick .46s cubic-bezier(.2,1.6,.4,1) var(--n) forwards}
$ .st svg{width:68%;height:68%;display:block}
$ .st path{fill:none;stroke:currentColor;stroke-width:3.6;stroke-linecap:round;stroke-linejoin:round}
$ .bar{position:absolute;left:0;right:0;bottom:0;height:4u;background:var(--accent);transform:scaleX(0);transform-origin:0 50%;animation:wcfill calc(var(--n) - var(--t)) cubic-bezier(.5,0,.2,1) var(--t) both,wchide .2s linear var(--n) forwards}
$ .clear{position:absolute;left:0;right:0;bottom:-84u;margin:0;text-align:center;font-size:34u;font-weight:600;letter-spacing:-.02em;animation:wcup .5s cubic-bezier(.16,1,.3,1) var(--t) both}
@keyframes wcin{from{opacity:0;transform:translateY(60u) scale(.96)}to{opacity:1;transform:none}}
@keyframes wcrow{from{opacity:0;transform:translateY(26u)}to{opacity:1;transform:none}}
@keyframes wcglow{0%{box-shadow:0 0 0 0 color-mix(in srgb,var(--accent) 0%,transparent) inset}24%{box-shadow:0 0 0 2u var(--accent) inset}100%{box-shadow:0 0 0 2u color-mix(in srgb,var(--accent) 40%,transparent) inset}}
@keyframes wcdone{to{background:color-mix(in srgb,var(--accent) 12%,transparent);box-shadow:0 0 0 0 transparent inset}}
@keyframes wcspin{to{transform:rotate(360deg)}}
@keyframes wcshow{to{opacity:1}}
@keyframes wchide{to{opacity:0}}
@keyframes wctick{0%{transform:scale(0)}100%{transform:none}}
@keyframes wcfill{to{transform:none}}
@keyframes wcon{to{opacity:1}}
@keyframes wcoff{to{opacity:0}}
@keyframes wcbeat{from{opacity:.5}to{opacity:1}}
@keyframes wcpill{0%{background:color-mix(in srgb,var(--ink) 8%,transparent)}40%{transform:scale(1.14)}100%{color:var(--panel);background:var(--accent);transform:none}}
@keyframes wcsoft{to{color:var(--panel)}}
@keyframes wcup{from{opacity:0;transform:translateY(24u)}to{opacity:1;transform:none}}
"""

GAUGE_CSS = """
$ .gauge{width:560u;max-width:100%;padding:26u 30u;border-radius:30u;background:color-mix(in srgb,var(--surface) 82%,transparent);box-shadow:0 0 0 1.5px color-mix(in srgb,var(--ink) 13%,transparent) inset,0 30u 80u rgba(0,0,0,.42);animation:sgin .5s cubic-bezier(.16,1,.3,1) both}
$ h3{margin:0 0 18u;font-size:25u;font-weight:500;letter-spacing:.01em;color:var(--soft)}
$ .rw{display:grid;grid-template-columns:1fr auto;align-items:baseline;gap:6u 18u;padding:10u 0;animation:sgin .5s cubic-bezier(.16,1,.3,1) calc(.1s + var(--i) * .08s) both}
$ .lb{font-size:30u;font-weight:500;letter-spacing:-.01em}
$ .vs{position:relative;display:block;min-width:4ch;height:1.2em;font-size:40u;font-weight:600;letter-spacing:-.03em;text-align:right;font-variant-numeric:tabular-nums}
$ .v{position:absolute;right:0;top:0;white-space:nowrap;font-weight:inherit;opacity:0;animation:sgup .32s cubic-bezier(.2,1.2,.4,1) var(--t) both,sgout .14s linear var(--o) forwards}
$ .mt{position:relative;grid-column:1 / -1;height:9u;border-radius:9u;overflow:hidden;background:color-mix(in srgb,var(--ink) 10%,transparent)}
$ .fill{position:absolute;inset:0;border-radius:9u;background:var(--ink);transform:scaleX(var(--a));transform-origin:0 50%;opacity:0;animation:sgfill .7s cubic-bezier(.3,0,.1,1) var(--t) both,sgout .01s linear var(--o) forwards}
$ .fill.hi{background:var(--accent)}
$ .nt{margin:16u 0 0;font-size:22u;line-height:1.25;color:var(--soft)}
@keyframes sgin{from{opacity:0;transform:translateY(22u)}to{opacity:1;transform:none}}
@keyframes sgup{from{opacity:0;transform:translateY(.5em)}to{opacity:1;transform:none}}
@keyframes sgout{to{opacity:0}}
@keyframes sgfill{from{opacity:1;transform:scaleX(var(--a))}to{opacity:1;transform:scaleX(var(--b))}}
"""

STAMP_CSS = """
$ .stamp{position:absolute;display:flex;flex-direction:column;gap:6u;font-family:var(--mono);color:var(--ink);text-shadow:0 1u 10u rgba(0,0,0,.6)}
$ .tl{left:var(--gutter);top:var(--top)}
$ .tr{right:var(--gutter);top:var(--top);align-items:flex-end}
$ .bl{left:var(--gutter);bottom:calc(var(--top) * .6)}
$ .br{right:var(--gutter);bottom:calc(var(--top) * .6);align-items:flex-end}
$ .stamp>i{width:14u;height:14u;background:var(--accent);animation:csblink 1s steps(2) infinite}
$ .tx{font-size:30u;font-weight:500;letter-spacing:.04em}
$ .word{display:inline-block;white-space:nowrap}
$ .c{display:inline-block;opacity:0;animation:cson .01s linear calc(var(--i) * .035s) forwards}
$ .lb{font-size:22u;letter-spacing:.06em;color:var(--soft)}
@keyframes csblink{0%{opacity:1}100%{opacity:.15}}
@keyframes cson{to{opacity:1}}
"""

TRACK_CSS = """
$ {overflow:hidden}
$ .ln{position:absolute;inset:0;background:color-mix(in srgb,var(--accent) 70%,transparent)}
$ .mk{position:absolute;width:0;height:0}
$ .mk i{position:absolute;left:-9u;top:-9u;width:18u;height:18u;box-shadow:0 0 0 2u var(--accent);animation:tmpop .3s cubic-bezier(.2,1.6,.4,1) both}
$ .mk em{position:absolute;left:18u;top:-13u;white-space:nowrap;font-family:var(--mono);font-style:normal;font-size:22u;letter-spacing:.03em;color:var(--ink);text-shadow:0 1u 8u rgba(0,0,0,.7)}
@keyframes tmpop{from{transform:scale(2.2);opacity:0}to{transform:none;opacity:1}}
"""

SILHOUETTE_CSS = """
$ .stagefield{position:absolute;inset:0;overflow:hidden}
$ .floor{position:absolute;left:0;right:0;top:72%;bottom:0;background:color-mix(in srgb,var(--accent) 16%,transparent)}
$ .line{position:absolute;left:var(--gutter);right:var(--gutter);top:12%;margin:0;text-align:center;font-size:60u;font-weight:600;line-height:1.1;letter-spacing:-.025em;text-wrap:balance}
$ .fig{position:absolute;left:50%;top:72%;width:0;height:0;color:var(--ink);--u:calc(13u);animation:safig var(--dur) linear both}
$ .hips{position:absolute;left:0;top:calc(var(--u) * -11);width:0;height:0;animation:sabob .6s ease-in-out infinite alternate}
$ .trunk{position:absolute;left:calc(var(--u) * -1.6);bottom:0;width:calc(var(--u) * 3.2);height:calc(var(--u) * 7.4);border-radius:calc(var(--u) * 1.6);background:currentColor;transform-origin:50% 100%;animation:salean 1.2s ease-in-out infinite alternate}
$ .head{position:absolute;left:50%;top:calc(var(--u) * -3.2);width:calc(var(--u) * 2.9);height:calc(var(--u) * 2.9);margin-left:calc(var(--u) * -1.45);border-radius:50%;background:currentColor;transform-origin:50% 100%;animation:sahead 1.5s ease-in-out infinite alternate}
$ .arm,$ .leg{position:absolute;width:calc(var(--u) * 1.15);border-radius:calc(var(--u) * .6);background:currentColor;transform-origin:50% calc(var(--u) * .6)}
$ .arm{left:calc(50% - var(--u) * .58);top:calc(var(--u) * .5);height:calc(var(--u) * 3.6)}
$ .fore,$ .shin{position:absolute;left:0;top:calc(100% - var(--u) * .6);width:100%;border-radius:calc(var(--u) * .6);background:currentColor;transform-origin:50% calc(var(--u) * .6)}
$ .fore{height:calc(var(--u) * 3.4)}
$ .leg{left:calc(var(--u) * -.58);top:calc(var(--u) * -.5);height:calc(var(--u) * 5.8)}
$ .shin{height:calc(var(--u) * 5.8)}
$ .a1{animation:saarm 1.2s ease-in-out infinite alternate}
$ .a2{animation:saarm 1.2s ease-in-out -1.2s infinite alternate}
$ .a1 .fore{animation:safore 1.2s ease-in-out -.1s infinite alternate}
$ .a2 .fore{animation:safore 1.2s ease-in-out -1.3s infinite alternate}
$ .l1{animation:saleg 1.2s ease-in-out infinite alternate}
$ .l2{animation:saleg 1.2s ease-in-out -1.2s infinite alternate}
$ .l1 .shin{animation:sashin 1.2s ease-in-out -.14s infinite alternate}
$ .l2 .shin{animation:sashin 1.2s ease-in-out -1.34s infinite alternate}
$ .wave .a1{animation:sawave .7s ease-in-out infinite alternate}
$ .wave .a1 .fore{animation:sawavef .7s ease-in-out -.1s infinite alternate}
$ .wave .l1,$ .wave .l2,$ .point .l1,$ .point .l2,$ .wave .shin,$ .point .shin{animation:none}
$ .wave .l1,$ .point .l1{transform:rotate(7deg)}
$ .wave .l2,$ .point .l2{transform:rotate(-7deg)}
$ .wave .fig,$ .point .fig{animation:none}
$ .point .a1{animation:sapoint .9s cubic-bezier(.2,1.2,.4,1) both}
$ .point .a1 .fore{animation:none;transform:rotate(-8deg)}
@keyframes safig{from{transform:translateX(-26vmin)}to{transform:translateX(26vmin)}}
@keyframes sabob{from{transform:translateY(calc(var(--u) * .3))}to{transform:translateY(calc(var(--u) * -.2))}}
@keyframes salean{from{transform:rotate(-2deg)}to{transform:rotate(3deg)}}
@keyframes sahead{from{transform:rotate(-4deg)}to{transform:rotate(4deg)}}
@keyframes saarm{from{transform:rotate(30deg)}to{transform:rotate(-30deg)}}
@keyframes safore{from{transform:rotate(-34deg)}to{transform:rotate(-6deg)}}
@keyframes saleg{from{transform:rotate(26deg)}to{transform:rotate(-26deg)}}
@keyframes sashin{from{transform:rotate(4deg)}to{transform:rotate(48deg)}}
@keyframes sawave{from{transform:rotate(-150deg)}to{transform:rotate(-112deg)}}
@keyframes sawavef{from{transform:rotate(-24deg)}to{transform:rotate(24deg)}}
@keyframes sapoint{from{transform:rotate(20deg)}to{transform:rotate(-98deg)}}
"""

FORMATS = (
    Format(
        id="work_cascade",
        title="Work cascade",
        summary="Cards pulse as they start work, tick in a cascade, and a counter lands on zero.",
        group="film",
        jobs=("prove", "explain"),
        slots=(
            Slot("title", limit=30),
            Slot("rows", kind="list", limit=6, each=40),
            Slot("from", kind="number"),
            Slot("to", kind="number", required=False),
            Slot("label", limit=12, required=False),
            Slot("done", limit=34, required=False),
        ),
        example={"title": "Support *queue*", "from": 412, "to": 0, "label": "open", "done": "Queue *clear*", "rows": [{"text": "Refund for a sample order", "label": "Billing"}, {"text": "Reset a password", "label": "Account"}, {"text": "Where is my parcel", "label": "Orders"}, {"text": "Change the plan", "label": "Billing"}]},
        draw=_work_cascade,
        seconds=7.0,
        shortest=3.5,
        longest=14.0,
        tall=660,
        sound="tick",
        css=scoped("work_cascade", CASCADE_CSS),
    ),
    Format(
        id="state_gauge",
        title="State gauge",
        summary="A small panel of rows that change value on cue, so one number can carry a story.",
        group="film",
        jobs=("prove", "explain"),
        slots=(Slot("title", limit=26, required=False), Slot("rows", kind="list", limit=4, each=20), Slot("note", limit=48, required=False)),
        example={"title": "Sample battery", "rows": [{"text": "Charge", "states": [{"value": "12%", "level": 0.12}, {"value": "64%", "level": 0.64, "hot": True}]}, {"text": "Range", "states": [{"value": "40 km", "level": 0.2}, {"value": "210 km", "level": 0.7}]}], "note": "Invented figures for illustration"},
        draw=_state_gauge,
        seconds=5.0,
        shortest=2.0,
        longest=30.0,
        tall=360,
        sound="tick",
        css=scoped("state_gauge", GAUGE_CSS),
    ),
    Format(
        id="corner_stamp",
        title="Corner stamp",
        summary="A small typed date and time sits in a corner, like a line in an evidence log.",
        group="film",
        jobs=("explain",),
        slots=(Slot("text", limit=30), Slot("label", limit=30, required=False), Slot("corner", kind="choice", required=False, choices=("tl", "tr", "bl", "br"))),
        example={"text": "MON 09:02:41", "label": "Sample log", "corner": "tl"},
        draw=_corner_stamp,
        seconds=4.0,
        shortest=1.0,
        longest=60.0,
        area="full",
        tall=120,
        sound=None,
        fonts=("mono",),
        css=scoped("corner_stamp", STAMP_CSS),
    ),
    Format(
        id="tracking_marks",
        title="Tracking marks",
        summary="Small squares ride moving points with mono labels, like a machine reading the frame.",
        group="film",
        jobs=("explain", "hook"),
        slots=(Slot("labels", kind="list", limit=6, each=18), Slot("seed", kind="number", required=False)),
        example={"labels": ["Sample A", "Sample B", "Sample C", "Sample D"], "seed": 5},
        draw=_tracking_marks,
        seconds=4.0,
        shortest=1.5,
        longest=20.0,
        area="full",
        tall=1920,
        sound=None,
        fonts=("mono",),
        css=scoped("tracking_marks", TRACK_CSS),
    ),
    Format(
        id="silhouette_act",
        title="Silhouette act",
        summary="A flat figure on a colour field acts out the line on real joints.",
        group="film",
        jobs=("explain", "hook"),
        slots=(Slot("text", limit=60), Slot("pose", kind="choice", required=False, choices=SILHOUETTE_POSES)),
        example={"text": "He walks the *long way* round", "pose": "walk"},
        draw=_silhouette_act,
        seconds=4.0,
        shortest=2.0,
        longest=12.0,
        area="full",
        tall=1920,
        sound=None,
        css=scoped("silhouette_act", SILHOUETTE_CSS),
    ),
)
