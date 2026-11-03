"""Teaching graphics. The maths happens here, so a proof, a plot or a sort on screen is the real thing and never a drawing of one.

Only HTML elements animate, for the same reason as in the data module: SVG and pseudo-element animations are seeked to film time.
"""

from __future__ import annotations

import ast
import math
import operator
import re
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from PIL import ImageFont

from ...brand import data_path
from . import FONT_FILES, Format, Slot, kit
from .data import W, _card, _fail, _foot, _keys, _mark, _num, _pct, _skin, _smooth, _spread, _strip, _svg, _ticks

GLIDE = "animation-timing-function:cubic-bezier(.62,0,.2,1)"
HOLD = "animation-timing-function:steps(1,end)"
TICK = '<svg viewBox="0 0 48 48" aria-hidden="true"><path d="M11 25 L20.5 34.5 L38 15" pathLength="1"/></svg>'


# Equation morph

TERM = re.compile(r"\d+(?:\.\d+)?|[A-Za-z]|\S")
OPERATORS = set("=+−×÷<>")
SWAPS = {"-": "−", "/": "÷"}
RAISED = 0.62


@lru_cache(maxsize=1)
def _serif() -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(data_path("fonts", FONT_FILES["serif"])), 1000)


def _em(text: str) -> float:
    """A term's width. Digits are set in the mono, a steady .6em each, because the serif's 1 reads as an l."""
    return 0.6 * len(text) if text[0].isdigit() else _serif().getlength(text) / 1000


def _terms(text: str) -> list[dict[str, Any]]:
    """A line of algebra as terms. `^` raises the next term, and `*...*` marks the accent."""
    out: list[dict[str, Any]] = []
    for index, part in enumerate(str(text).split("*")):
        lift = False
        for found in TERM.findall(part):
            if found == "^":
                lift = True
                continue
            out.append({"text": SWAPS.get(found, found), "hot": bool(index % 2), "up": lift})
            lift = False
    return out


def _set_line(terms: list[dict[str, Any]]) -> float:
    """Place each term with the serif's real widths, centred on zero. Returns the line's width in ems."""
    x, last = 0.0, None
    for term in terms:
        wide = _em(term["text"]) * (RAISED if term["up"] else 1.0)
        gap = 0.27 if term["text"] in OPERATORS and last is not None else 0.0
        after = 0.27 if term["text"] in OPERATORS and last is not None else 0.0
        term["x"], term["wide"] = x + gap, wide
        x += gap + wide + after + 0.012
        last = term
    for term in terms:
        term["x"] -= x / 2
    return x


def _pair(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> dict[int, int]:
    """Which terms carry over. Longest common run first, so a term stays itself; then any leftover twin, so a term can cross the equals sign."""
    same = [[0] * (len(after) + 1) for _ in range(len(before) + 1)]

    def key(term: dict[str, Any]) -> tuple[str, bool]:
        return term["text"], term["up"]

    for i in range(len(before) - 1, -1, -1):
        for j in range(len(after) - 1, -1, -1):
            same[i][j] = same[i + 1][j + 1] + 1 if key(before[i]) == key(after[j]) else max(same[i + 1][j], same[i][j + 1])
    link: dict[int, int] = {}
    i = j = 0
    while i < len(before) and j < len(after):
        if key(before[i]) == key(after[j]):
            link[j] = i
            i, j = i + 1, j + 1
        elif same[i + 1][j] >= same[i][j + 1]:
            i += 1
        else:
            j += 1
    free = [n for n in range(len(before)) if n not in link.values()]
    for j, term in enumerate(after):
        if j not in link:
            twin = next((n for n in free if key(before[n]) == key(term)), None)
            if twin is not None:
                link[j] = twin
                free.remove(twin)
    return link


def _beats(rows: list[dict[str, Any]], first: float, last: float) -> list[float]:
    """When each step lands: its word if it has one, or evenly spread, and always in order."""
    count = len(rows)
    out, floor = [], first
    for index, row in enumerate(rows):
        spaced = first + (last - first) * index / max(count - 1, 1)
        floor = max(row["delay"] if "at" in row else spaced, floor)
        out.append(floor)
        floor += 0.55
    return out


def _equation_morph(item: dict[str, Any]) -> str:
    rows = kit.rows(item, "steps")
    if len(rows) < 2:
        _fail("equation_morph", "needs at least two steps.")
    lines = [_terms(row["text"]) for row in rows]
    wide = max(_set_line(line) for line in lines)
    total = kit.seconds(item)
    beats = _beats(rows, 0.7, total * 0.74)
    span = beats[-1] + 0.05
    hop = min(b - a for a, b in zip(beats, beats[1:], strict=False))
    move = min(0.8, hop * 0.72)
    size = min(116, 742 / max(wide, 1))
    spans: list[dict[int, dict[str, Any]]] = []
    live: dict[int, dict[int, dict[str, Any]]] = {}
    for step, line in enumerate(lines):
        link = _pair(lines[step - 1], line) if step else {}
        now = {}
        for index, term in enumerate(line):
            track = live[link[index]] if index in link else {}
            if index not in link:
                spans.append(track)
            track[step] = term
            now[index] = track
        live = now
    mark = _mark(item)
    css, cells = [], []

    def state(track: dict[int, dict[str, Any]], step: int) -> str:
        here = track.get(step)
        if here:
            return f"transform:translate({here['x']:.3f}em,0);opacity:1;color:{'var(--accent)' if here['hot'] else 'var(--ink)'}"
        near = track[min(track, key=lambda s: abs(s - step))]
        return f"transform:translate({near['x']:.3f}em,.34em);opacity:0;color:var(--ink)"

    for number, track in enumerate(spans):
        first = state(track, 0).replace("opacity:1", "opacity:0").replace(",0)", ",.34em)")
        frames = [(0.0, first), ((beats[0] - 0.45) / span * 100, first)]
        for step in range(len(rows)):
            settled = state(track, step) + ";" + GLIDE
            frames.append((beats[step] / span * 100, settled))
            if step < len(rows) - 1:
                leaves = beats[step + 1] - move
                frames.append((leaves / span * 100, settled))
                a, b = track.get(step), track.get(step + 1)
                if a and b and abs(a["x"] - b["x"]) > 0.9:
                    middle = (a["x"] + b["x"]) / 2
                    frames.append(
                        (
                            (leaves + move / 2) / span * 100,
                            f"transform:translate({middle:.3f}em,-.72em);opacity:1;color:{'var(--accent)' if b['hot'] else 'var(--ink)'};{GLIDE}",
                        )
                    )
        css.append(_keys(f"{mark}e{number}", frames))
        sample = next(iter(track.values()))
        kind = " v" if sample["text"].isalpha() else " d" if sample["text"][0].isdigit() else ""
        body = f'<i class="up">{kit.esc(sample["text"])}</i>' if sample["up"] else kit.esc(sample["text"])
        cells.append(f'<span class="t{kind}" style="animation:{mark}e{number} {span:.3f}s linear both">{body}</span>')
    notes = list(item.get("notes") or [])
    says = []
    for step in range(len(rows)):
        text = str(notes[step]) if step < len(notes) else ""
        if not text:
            continue
        on, off = beats[step] - move * 0.4, beats[step + 1] - move if step < len(rows) - 1 else span
        frames = [
            (0.0, "opacity:0;transform:translateY(.4em)"),
            (max(on - 0.3, 0.0) / span * 100, "opacity:0;transform:translateY(.4em)"),
            (on / span * 100, "opacity:1;transform:none"),
        ]
        if step < len(rows) - 1:
            frames += [(off / span * 100, "opacity:1;transform:none"), ((off + 0.2) / span * 100, "opacity:0;transform:none")]
        css.append(_keys(f"{mark}w{step}", frames))
        says.append(f'<p style="animation:{mark}w{step} {span:.3f}s linear both">{kit.esc(text)}</p>')
    stage = f'<style>{"".join(css)}</style><div class="eq" style="font-size:calc({size:.0f}px * var(--scale))">{"".join(cells)}</div>'
    return _card(item, stage + (f'<div class="why">{"".join(says)}</div>' if says else ""))


# Function plot

BINARY = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Pow: operator.pow}
FUNCTIONS: dict[str, Callable[..., float]] = {"sin": math.sin, "cos": math.cos, "tan": math.tan, "exp": math.exp, "log": math.log, "sqrt": math.sqrt, "abs": abs}
CONSTANTS = {"pi": math.pi, "e": math.e}


def _compile(expr: str, param: str) -> Callable[[float, float], float]:
    """Turn `a*sin(x)` into a function of x and the parameter, by walking the parse tree. Nothing is ever passed to eval."""
    text = str(expr).replace("^", "**").replace("π", "pi").replace("−", "-").replace("×", "*")
    text = re.sub(r"^\s*y\s*=", "", text).strip()
    text = re.sub(r"(\d)\s*([a-zA-Z(])", r"\1*\2", text)
    text = re.sub(r"\)\s*([a-zA-Z(\d])", r")*\1", text)
    try:
        tree = ast.parse(text, mode="eval").body
    except SyntaxError:
        _fail("function_plot", f'cannot read the formula "{expr}".')

    def build(node: ast.AST) -> Callable[[dict[str, float]], float]:
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
            number = float(node.value)
            return lambda names: number
        if isinstance(node, ast.Name):
            name = node.id
            if name in CONSTANTS:
                number = CONSTANTS[name]
                return lambda names: number
            if name in ("x", param):
                return lambda names: names[name]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
            inner = build(node.operand)
            sign = -1.0 if isinstance(node.op, ast.USub) else 1.0
            return lambda names: sign * inner(names)
        if isinstance(node, ast.BinOp) and type(node.op) in BINARY:
            left, right, join = build(node.left), build(node.right), BINARY[type(node.op)]
            return lambda names: join(left(names), right(names))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS and len(node.args) == 1:
            inner, call = build(node.args[0]), FUNCTIONS[node.func.id]
            return lambda names: call(inner(names))
        _fail("function_plot", f'"{expr}" uses something outside x, {param}, numbers, + - * / ^ and {", ".join(FUNCTIONS)}.')
        raise AssertionError

    body = build(tree)

    def run(x: float, value: float) -> float:
        try:
            out = body({"x": x, param: value})
        except (ValueError, ZeroDivisionError, OverflowError):
            return math.nan
        return out if isinstance(out, float | int) and math.isfinite(out) else math.nan

    return run


def _curve_path(
    fn: Callable[[float, float], float],
    value: float,
    x0: float,
    x1: float,
    x_of: Callable[[float], float],
    y_of: Callable[[float], float],
    low: float,
    high: float,
    count: int,
) -> str:
    parts, pen = [], False
    reach = (high - low) * 0.6
    for k in range(count + 1):
        x = x0 + (x1 - x0) * k / count
        y = fn(x, value)
        if math.isnan(y):
            pen = False
            continue
        parts.append(f"{'L' if pen else 'M'}{x_of(x):.1f} {y_of(min(max(y, low - reach), high + reach)):.1f}")
        pen = True
    return "".join(parts)


def _pretty(expr: str) -> str:
    """The formula as a reader expects it: real minus signs, a raised dot for times, and powers as superscript."""
    text = kit.esc(re.sub(r"^\s*y\s*=\s*", "", expr)).replace("*", "·").replace("-", "−")
    return re.sub(r"\^(\w+)", r"<sup>\1</sup>", text)


def _plain(number: float) -> str:
    return f"{number:g}".replace("-", "−")


def _function_plot(item: dict[str, Any]) -> str:
    expr, param = str(item["expr"]), str(item.get("param") or "a")
    if len(expr) > 32 or len(param) != 1:
        _fail("function_plot", '"expr" holds 32 characters at most and "param" is one letter.')
    x0, x1 = _num(item["x_min"], "function_plot", "x_min"), _num(item["x_max"], "function_plot", "x_max")
    if x1 <= x0:
        _fail("function_plot", '"x_max" must be above "x_min".')
    values = [_num(v, "function_plot", "values") for v in item.get("values") or [1]]
    fn = _compile(expr, param)
    samples = [y for value in values for y in (fn(x0 + (x1 - x0) * k / 160, value) for k in range(161)) if not math.isnan(y)]
    if not samples:
        _fail("function_plot", f'"{expr}" has no real values between {x0:g} and {x1:g}.')
    given_low, given_high = item.get("y_min"), item.get("y_max")
    low = _num(given_low, "function_plot", "y_min") if given_low is not None else min(0.0, min(samples))
    high = _num(given_high, "function_plot", "y_max") if given_high is not None else max(0.0, max(samples))
    if given_low is None or given_high is None:
        ticks = _ticks(low, high)
        low, high = ticks[0], ticks[-1]
    y_ticks = [t for t in _ticks(low, high) if low <= t <= high]
    x_ticks = [t for t in _ticks(x0, x1, 6) if x0 <= t <= x1]
    height, left, right, top, bottom = 430, 62, 14, 12, 44
    wide, tall = W - left - right, height - top - bottom

    def x_of(x: float) -> float:
        return left + wide * (x - x0) / (x1 - x0)

    def y_of(y: float) -> float:
        return top + tall * (high - y) / (high - low)

    grid = []
    for tick in y_ticks:
        y = y_of(tick)
        grid.append(
            f'<line class="{"ax" if tick == 0 else "gl"}" x1="{left}" x2="{W - right}" y1="{y:.1f}" y2="{y:.1f}"/><text x="{left - 12}" y="{y + 9:.1f}" text-anchor="end">{_plain(tick)}</text>'
        )
    for tick in x_ticks:
        x = x_of(tick)
        grid.append(
            f'<line class="{"ax" if tick == 0 else "gl"}" x1="{x:.1f}" x2="{x:.1f}" y1="{top}" y2="{height - bottom}"/><text x="{x:.1f}" y="{height - 8}" text-anchor="middle">{_plain(tick)}</text>'
        )
    total = kit.seconds(item)
    begin, draw = 0.55, min(total * 0.24, 1.3)
    start = begin + draw + 0.25
    mark = _mark(item)
    shape = f'<path d="{_curve_path(fn, values[0], x0, x1, x_of, y_of, low, high, 96)}" pathLength="1"/>'
    first = f'<div class="ly curve{" ghost" if len(values) > 1 else ""}" style="--d:{draw:.3f}s;--b:{begin:.3f}s;--g:{start:.3f}s">{_svg(height, shape)}</div>'
    chip = ""
    film = ""
    css = ""
    places = max((len(f"{v:.4f}".rstrip("0").split(".")[1]) for v in values), default=0)
    if len(values) > 1:
        moves = len(values) - 1
        hop = (total * 0.84 - start) / moves
        glide = min(hop * 0.68, 1.1)
        frames_each = max(round(glide * 20), 8)
        count = moves * frames_each + 1
        span = start + hop * moves
        rail, tags, texts, panes, at = [(0.0, f"transform:translateY(0);{HOLD}")], [(0.0, f"transform:translateY(0);{HOLD}")], [], [], start
        for move in range(moves):
            leave = at + (hop - glide)
            step = f"animation-timing-function:steps({frames_each},end)"
            base = move * frames_each
            rail += [
                (at / span * 100, f"transform:translateY(0%);{HOLD}" if base == 0 else f"transform:translateY({-base / count * 100:.4f}%);{HOLD}"),
                (leave / span * 100, f"transform:translateY({-base / count * 100:.4f}%);{step}"),
            ]
            tags += [(leave / span * 100, f"transform:translateY({-base * 1.16:.2f}em);{step}")]
            at = leave + glide
            rail.append((at / span * 100, f"transform:translateY({-(base + frames_each) / count * 100:.4f}%);{HOLD}"))
            tags.append((at / span * 100, f"transform:translateY({-(base + frames_each) * 1.16:.2f}em);{HOLD}"))
            for k in range(frames_each):
                value = values[move] + (values[move + 1] - values[move]) * _smooth(k / frames_each)
                texts.append(f"{param} = {value:.{min(places, 2)}f}")
                panes.append(_svg(height, f'<path d="{_curve_path(fn, value, x0, x1, x_of, y_of, low, high, 72)}"/>'))
        texts.append(f"{param} = {values[-1]:.{min(places, 2)}f}")
        panes.append(_svg(height, f'<path d="{_curve_path(fn, values[-1], x0, x1, x_of, y_of, low, high, 72)}"/>'))
        css = f"<style>{_keys(f'{mark}r', rail)}{_keys(f'{mark}t', tags)}</style>"
        motion = f"{span:.3f}s linear both"
        film = f'<div class="win" style="--g:{start:.3f}s"><div class="film" style="height:{count * 100}%;animation:{mark}r {motion}">{"".join(panes)}</div></div>'
        cells = "".join(f"<i>{kit.esc(text)}</i>" for text in texts)
        chip = f'<span class="pval"><span class="ct"><span class="st" style="animation:{mark}t {motion}">{cells}</span></span></span>'
    formula = f'<div class="fx"><span class="sum">y = {_pretty(expr)}</span>{chip}</div>'
    plot = f'<div class="plot" style="aspect-ratio:{W}/{height}"><div class="ly grid">{_svg(height, "".join(grid))}</div>{first}{film}</div>'
    return _card(item, f"{css}{formula}{plot}")


# Geometry proofs

CAPTIONS = {
    "pythagoras": ("Four equal right triangles sit in a square. The gap is c².", "Slide them. Same triangles, same square.", "The gap is now a² and b²."),
    "triangle_sum": ("Copy each base corner by turning it half a circle.", "Each one lands at the top, against a parallel line.", "Together they make a straight line."),
}


def _pythagoras(total: float, says: list[str]) -> str:
    u, o, a, b = 52.0, 28.0, 3.0, 4.0
    s = a + b

    def pts(*corners: tuple[float, float]) -> str:
        return " ".join(f"{o + x * u:.1f},{o + y * u:.1f}" for x, y in corners)

    pieces = [
        (((0, 0), (a, 0), (0, b)), (0, a)),
        (((s, 0), (s, a), (a, 0)), (0, 0)),
        (((s, s), (b, s), (s, a)), (-b, 0)),
        (((0, s), (0, b), (b, s)), (a, -b)),
    ]
    move = total * 0.44
    drawn = []
    for index, (corners, (dx, dy)) in enumerate(pieces):
        shift = f"--x:{dx * u / 420 * 100:.3f}%;--y:{dy * u / 420 * 100:.3f}%"
        drawn.append(
            f'<div class="ly pc" style="{shift};--t:{0.5 + index * 0.14:.2f}s;--m:{move + index * 0.1:.2f}s"><svg viewBox="0 0 420 420" aria-hidden="true"><polygon points="{pts(*corners)}"/></svg></div>'
        )
    tilted = pts((a, 0), (s, a), (b, s), (0, b))
    gap_c = f'<div class="ly gap outc" style="--t:1.2s;--m:{move - 0.3:.2f}s"><svg viewBox="0 0 420 420" aria-hidden="true"><polygon points="{tilted}"/></svg><span class="nm" style="left:50%;top:50%">c<sup>2</sup></span></div>'
    side_a = f'<polygon points="{pts((0, 0), (a, 0), (a, a), (0, a))}"/>'
    side_b = f'<polygon points="{pts((a, a), (s, a), (s, s), (a, s))}"/>'
    land = move + 1.5
    gap_ab = f'<div class="ly gap inab" style="--t:{land:.2f}s"><svg viewBox="0 0 420 420" aria-hidden="true">{side_a}{side_b}</svg><span class="nm" style="left:{_pct(o + a * u / 2, 420)};top:{_pct(o + a * u / 2, 420)}">a<sup>2</sup></span><span class="nm" style="left:{_pct(o + (a + b / 2) * u, 420)};top:{_pct(o + (a + b / 2) * u, 420)}">b<sup>2</sup></span></div>'
    frame = f'<rect x="{o}" y="{o}" width="{s * u}" height="{s * u}"/>'
    edges = f'<text x="{o + a * u / 2}" y="{o - 8}" text-anchor="middle">a</text><text x="{o + (a + b / 2) * u}" y="{o - 8}" text-anchor="middle">b</text>'
    marks = f'<line x1="{o + a * u}" x2="{o + a * u}" y1="{o - 16}" y2="{o - 2}"/>'
    stage = f'<div class="stage"><div class="ly base">{_board(frame + edges + marks)}</div>{gap_ab}{gap_c}{"".join(drawn)}</div>'
    lines = _says(says, (1.4, move - 0.2, land))
    sum_line = f'<p class="sum" style="--t:{land + 0.5:.2f}s">a<sup>2</sup> + b<sup>2</sup> = c<sup>2</sup></p>'
    return f'<div class="row">{stage}<div class="say">{lines}{sum_line}</div></div>'


def _board(inner: str) -> str:
    return f'<svg viewBox="0 0 420 420" aria-hidden="true">{inner}</svg>'


def _says(says: list[str], times: tuple[float, ...]) -> str:
    return "".join(f'<p class="beat" style="--t:{at:.2f}s">{kit.esc(text)}</p>' for text, at in zip(says, times, strict=False))


def _wedge(at: tuple[float, float], to_a: tuple[float, float], to_b: tuple[float, float], radius: float, fill: str) -> str:
    def ray(point: tuple[float, float]) -> tuple[float, float]:
        dx, dy = point[0] - at[0], point[1] - at[1]
        size = math.hypot(dx, dy)
        return at[0] + dx / size * radius, at[1] + dy / size * radius

    p, q = ray(to_a), ray(to_b)
    cross = (p[0] - at[0]) * (q[1] - at[1]) - (p[1] - at[1]) * (q[0] - at[0])
    return f'<path class="{fill}" d="M{at[0]:.1f} {at[1]:.1f} L{p[0]:.1f} {p[1]:.1f} A{radius} {radius} 0 0 {1 if cross > 0 else 0} {q[0]:.1f} {q[1]:.1f} Z"/>'


def _triangle_sum(total: float, says: list[str]) -> str:
    a, b, c = (34.0, 340.0), (392.0, 340.0), (146.0, 118.0)
    radius = 62.0
    turn_a, turn_b = total * 0.34, total * 0.52
    hull = f'<polygon class="hull" points="{a[0]},{a[1]} {b[0]},{b[1]} {c[0]},{c[1]}"/>'
    names = f'<text x="{a[0] - 10}" y="{a[1] + 34}">A</text><text x="{b[0] - 8}" y="{b[1] + 34}">B</text><text x="{c[0] - 8}" y="{c[1] - 22}" class="hide">C</text>'
    line = f'<line class="par" x1="0" x2="420" y1="{c[1]}" y2="{c[1]}" pathLength="1"/>'
    wedge_c = _wedge(c, a, b, radius, "tc")

    def swing(point: tuple[float, float], kind: str, at: float) -> str:
        mid_x, mid_y = (point[0] + c[0]) / 2, (point[1] + c[1]) / 2
        other = b if point == a else a
        shape = _wedge(point, c, other, radius, kind)
        rest = f'<div class="ly stay" style="--t:0.9s">{_board(shape)}</div>'
        moving = f'<div class="ly turn" style="transform-origin:{_pct(mid_x, 420)} {_pct(mid_y, 420)};--t:0.9s;--m:{at:.2f}s">{_board(shape)}</div>'
        return rest + moving

    stage = (
        f'<div class="stage"><div class="ly base">{_board(hull + names)}</div><div class="ly stay" style="--t:0.9s">{_board(wedge_c)}</div>'
        f'<div class="ly paral" style="--t:{turn_a - 0.8:.2f}s">{_board(line)}</div>{swing(a, "ta", turn_a)}{swing(b, "tb", turn_b)}</div>'
    )
    land = turn_b + 1.4
    lines = _says(says, (0.9, turn_a, land - 0.4))
    sum_line = f'<p class="sum" style="--t:{land:.2f}s"><i class="ka">A</i> + <i class="kc">C</i> + <i class="kb">B</i> = 180°</p>'
    return f'<div class="row">{stage}<div class="say">{lines}{sum_line}</div></div>'


def _geometry_proof(item: dict[str, Any]) -> str:
    proof = str(item.get("proof") or "pythagoras")
    says = [str(s) for s in item.get("captions") or CAPTIONS[proof]]
    body = {"pythagoras": _pythagoras, "triangle_sum": _triangle_sum}[proof](kit.seconds(item), says)
    return _card(item, body)


# Code walkthrough

KEYWORDS = set(
    "def return if else elif for while in not and or import from class const let var function async await try except finally with as new yield None True False null true false".split()
)
CODE = re.compile(r"(?P<note>#.*|//.*)|(?P<text>\"[^\"]*\"?|'[^']*'?|`[^`]*`?)|(?P<num>\b\d+(?:\.\d+)?\b)|(?P<word>[A-Za-z_][A-Za-z0-9_]*)")


def _paint(line: str) -> str:
    """Light syntax colour from a small tokeniser. Enough to read by, and the same for any language with these shapes."""
    out, at = [], 0
    for found in CODE.finditer(line):
        out.append(kit.esc(line[at : found.start()]))
        text = kit.esc(found.group())
        kind = found.lastgroup
        if kind == "word":
            kind = "key" if found.group() in KEYWORDS else "fn" if line[found.end() : found.end() + 1] == "(" else None
        out.append(f'<span class="{kind}">{text}</span>' if kind else text)
        at = found.end()
    out.append(kit.esc(line[at:]))
    return "".join(out)


def _code_walk(item: dict[str, Any]) -> str:
    lines = [str(line) for line in item["code"]]
    rows = kit.rows(item, "steps")
    total = kit.seconds(item)
    beats = _beats(rows, 1.1, total * 0.76)
    span = total
    mark = _mark(item)
    spots = []
    for row in rows:
        raw = row.get("line", 1)
        first, last = (raw, raw) if not isinstance(raw, list) else (raw[0], raw[-1])
        first, last = int(_num(first, "code_walk", "line")), int(_num(last, "code_walk", "line"))
        if not 1 <= first <= last <= len(lines):
            _fail("code_walk", f"a step points at line {raw}, and the code has {len(lines)} lines.")
        spots.append((first - 1, last))
    glide = 0.32
    band = [(0.0, f"opacity:0;transform:translateY({spots[0][0] * 100:.2f}%) scaleY({spots[0][1] - spots[0][0]})")]
    band.append((max(beats[0] - 0.2, 0.0) / span * 100, band[0][1]))
    for index, (first, last) in enumerate(spots):
        where = f"opacity:1;transform:translateY({first * 100:.2f}%) scaleY({last - first});{GLIDE}"
        band.append((beats[index] / span * 100, where))
        if index < len(spots) - 1:
            band.append(((beats[index + 1] - glide) / span * 100, where))
    css = [_keys(f"{mark}b", band)]
    cells = []
    for number, line in enumerate(lines):
        reveal = 0.35 + number * 0.05
        frames = [(0.0, "opacity:0"), (reveal / span * 100, "opacity:0"), ((reveal + 0.26) / span * 100, "opacity:1")]
        for index, (first, last) in enumerate(spots):
            state = "opacity:1" if first <= number < last else "opacity:.34"
            frames.append(((beats[index] - 0.24) / span * 100, frames[-1][1]))
            frames.append((beats[index] / span * 100, state))
        css.append(_keys(f"{mark}l{number}", frames))
        cells.append(
            f'<div class="ln" style="animation:{mark}l{number} {span:.3f}s linear both"><span class="no">{number + 1}</span><code>{_paint(line) or " "}</code></div>'
        )
    says = []
    for index, row in enumerate(rows):
        off = beats[index + 1] - 0.3 if index < len(rows) - 1 else None
        frames = [
            (0.0, "opacity:0;transform:translateY(.3em)"),
            (max(beats[index] - 0.3, 0.0) / span * 100, "opacity:0;transform:translateY(.3em)"),
            (beats[index] / span * 100, "opacity:1;transform:none"),
        ]
        if off is not None:
            frames += [(off / span * 100, "opacity:1;transform:none"), ((off + 0.18) / span * 100, "opacity:0;transform:none")]
        css.append(_keys(f"{mark}s{index}", frames))
        says.append(f'<p style="animation:{mark}s{index} {span:.3f}s linear both">{kit.emphasis(row["text"])}</p>')
    sheet = f'<div class="sheet" style="--rows:{len(lines)}"><i class="band" style="animation:{mark}b {span:.3f}s linear both"></i>{"".join(cells)}</div>'
    return _card(item, f'<style>{"".join(css)}</style>{sheet}<div class="why">{"".join(says)}</div>')


# Algorithm stepper


def _sort_trace(values: list[float], algo: str) -> list[dict[str, Any]]:
    """Run the sort for real and keep every state: where each value sits, what is being looked at, and what is finished."""
    order = list(range(len(values)))
    states: list[dict[str, Any]] = []
    done: set[int] = set()

    def show(index: int) -> str:
        return f"{values[index]:g}"

    def keep(look: tuple[int, ...], say: str) -> None:
        states.append({"order": list(order), "look": set(look), "done": set(done), "out": set(), "say": say})

    count = len(values)
    if algo == "bubble":
        for end in range(count - 1, 0, -1):
            moved = False
            for j in range(end):
                a, b = order[j], order[j + 1]
                keep((a, b), f"Compare {show(a)} and {show(b)}")
                if values[a] > values[b]:
                    order[j], order[j + 1] = b, a
                    moved = True
                    keep((a, b), f"{show(a)} is larger, so swap")
            done.add(order[end])
            if not moved:
                done.update(order)
                break
    elif algo == "insertion":
        done.add(order[0])
        for i in range(1, count):
            j = i
            while j > 0:
                a, b = order[j - 1], order[j]
                keep((a, b), f"Compare {show(b)} with {show(a)}")
                if values[a] <= values[b]:
                    break
                order[j - 1], order[j] = b, a
                keep((a, b), f"{show(b)} moves left")
                j -= 1
            done.update(order[: i + 1])
    else:
        for i in range(count - 1):
            least = i
            for j in range(i + 1, count):
                keep((order[least], order[j]), f"Is {show(order[j])} below {show(order[least])}?")
                if values[order[j]] < values[order[least]]:
                    least = j
            if least != i:
                a, b = order[i], order[least]
                order[i], order[least] = b, a
                keep((a, b), f"Put {show(b)} in place")
            done.add(order[i])
    done.update(order)
    keep(tuple(order), "Sorted")
    return states


def _search_trace(values: list[float], target: float) -> list[dict[str, Any]]:
    """Binary search on sorted values, one halving per state."""
    low, high = 0, len(values) - 1
    out: set[int] = set()
    states: list[dict[str, Any]] = []
    while low <= high:
        mid = (low + high) // 2
        states.append({"order": list(range(len(values))), "look": {mid}, "done": set(), "out": set(out), "say": f"Middle is {values[mid]:g}"})
        if values[mid] == target:
            states.append({"order": list(range(len(values))), "look": {mid}, "done": set(), "out": set(out), "say": f"Found {target:g}"})
            return states
        if values[mid] < target:
            out.update(range(low, mid + 1))
            low = mid + 1
            say = f"{values[mid]:g} is too low, drop the left"
        else:
            out.update(range(mid, high + 1))
            high = mid - 1
            say = f"{values[mid]:g} is too high, drop the right"
        states.append({"order": list(range(len(values))), "look": set(), "done": set(), "out": set(out), "say": say})
    states.append({"order": list(range(len(values))), "look": set(), "done": set(), "out": set(out), "say": f"{target:g} is not in the list"})
    return states


def _algo_step(item: dict[str, Any]) -> str:
    algo = str(item.get("algo") or "bubble")
    values = [_num(v, "algo_step", "values") for v in item["values"]]
    if len(values) < 2 or min(values) < 0 or max(values) <= 0:
        _fail("algo_step", '"values" needs at least two amounts of zero or more.')
    if algo == "binary":
        if item.get("target") is None:
            _fail("algo_step", 'binary search needs a "target".')
        values = sorted(values)
        states = _search_trace(values, _num(item["target"], "algo_step", "target"))
    else:
        states = _sort_trace(values, algo)
    total = kit.seconds(item)
    begin = 0.8
    run = total - begin - max(total * 0.14, 0.8)
    pace = run / len(states)
    if pace < 0.07:
        _fail("algo_step", f"{len(states)} steps cannot be followed in {total:g} seconds.")
    span = begin + run
    mark = _mark(item)
    top = max(values)
    css, bars = [], []
    glide = min(pace * 0.7, 0.3)
    for index, value in enumerate(values):
        move, paint = [], []
        start = states[0]["order"].index(index)
        move.append((0.0, f"transform:translateX({start * 100}%);{GLIDE}"))
        paint.append((0.0, f"background:var(--mute);{HOLD}"))
        place = start
        for step, state in enumerate(states):
            at = begin + step * pace
            now = state["order"].index(index)
            if now != place:
                move.append((at / span * 100, f"transform:translateX({place * 100}%);{GLIDE}"))
                move.append(((at + glide) / span * 100, f"transform:translateX({now * 100}%);{GLIDE}"))
                place = now
            tint = (
                "var(--accent)"
                if index in state["look"]
                else "color-mix(in srgb,var(--accent) 42%,var(--surface))"
                if index in state["done"]
                else "color-mix(in srgb,var(--ink) 12%,transparent)"
                if index in state["out"]
                else "var(--mute)"
            )
            paint.append((at / span * 100, f"background:{tint};{HOLD}"))
        css += [_keys(f"{mark}m{index}", move), _keys(f"{mark}p{index}", paint)]
        motion = f"{span:.3f}s linear both"
        bars.append(
            f'<div class="el" style="width:{100 / len(values):.3f}%;animation:{mark}m{index} {motion}"><div class="in" style="--f:{value / top:.4f}"><span>{value:g}</span><b style="animation:{mark}p{index} {motion}"></b></div></div>'
        )
    status = _strip([s["say"] for s in states], pace * (len(states) - 1), begin)
    return _card(item, f'<style>{"".join(css)}</style><p class="now">{status}</p><div class="bars">{"".join(bars)}</div>')


# Cards


def _vocab_card(item: dict[str, Any]) -> str:
    term = str(item["term"])
    size = min(138, 1240 / max(len(term), 1))
    tag = "".join(f"<span>{kit.esc(item[name])}</span>" for name in ("kind", "sound") if item.get(name))
    word = f'<div class="term" style="font-size:calc({size:.0f}px * var(--scale))">{kit.letters(term)}</div>'
    example = f'<p class="eg">{kit.emphasis(item["example"])}</p>' if item.get("example") else ""
    return (
        f'<div class="sheet">{f"<div class=tags>{tag}</div>" if tag else ""}{word}<i class="rule"></i><p class="mean">{kit.emphasis(item["meaning"])}</p>{example}</div>'
    )


def _quiz_reveal(item: dict[str, Any]) -> str:
    rows = kit.rows(item, "options")
    answer = int(_num(item["answer"], "quiz_reveal", "answer"))
    if not 1 <= answer <= len(rows):
        _fail("quiz_reveal", f'"answer" is the number of the right option, from 1 to {len(rows)}.')
    total = kit.seconds(item)
    right = rows[answer - 1]
    show = 0.5 + 0.12 * len(rows)
    reveal = max(right["delay"] if "at" in right else total * 0.6, show + 0.4)
    cells = []
    for index, row in enumerate(rows):
        won = index == answer - 1
        wait = 0.5 + 0.12 * index
        state = f"dkrise .42s var(--ease) {wait:.2f}s both,{'qzwin' if won else 'qzlose'} .32s ease-out {reveal:.2f}s forwards"
        tick = f'<span class="tk" style="animation:dkdraw .36s var(--ease) {reveal + 0.16:.2f}s both">{TICK}</span>' if won else ""
        cells.append(f'<div class="opt" style="animation:{state}"><b>{"ABCD"[index]}</b><span class="tx">{kit.esc(row["text"])}</span>{tick}</div>')
    timer = f'<div class="timer"><i style="animation:qzrun {reveal - show:.2f}s linear {show:.2f}s both"></i></div>'
    return f'<div class="card"><i class="tab"></i><h2>{kit.emphasis(item["question"])}</h2>{timer}<div class="opts">{"".join(cells)}</div>{_foot(item)}</div>'


def _recap_check(item: dict[str, Any]) -> str:
    rows = kit.rows(item, "points")
    total = kit.seconds(item)
    beats = _beats(rows, 0.7, total * 0.66)
    cells = []
    for row, at in zip(rows, beats, strict=True):
        cells.append(
            f'<div class="pt" style="animation-delay:{at - 0.28:.2f}s"><span class="box"><u style="animation-delay:{at:.2f}s"></u><span class="tk" style="animation:dkdraw .34s var(--ease) {at + 0.06:.2f}s both">{TICK}</span></span>'
            f'<span class="tx">{kit.emphasis(row["text"])}</span></div>'
        )
    return _card(item, f'<div class="list">{"".join(cells)}</div>')


def _chapter_card(item: dict[str, Any]) -> str:
    number = item.get("number")
    label = f"Chapter {number:g}" if isinstance(number, int | float) else str(number or "")
    head = f'<p class="no">{kit.esc(label)}</p>' if label else ""
    pips = ""
    if item.get("of") and isinstance(number, int | float):
        count = min(int(_num(item["of"], "chapter_card", "of")), 12)
        pips = '<div class="pips">' + "".join(f'<i class="{"on" if n < number else ""}" style="--i:{n}"></i>' for n in range(count)) + "</div>"
    words = "".join(
        f'<span class="m"><span class="w{" hot" if hot else ""}" style="--i:{i}">{kit.esc(word)}</span></span> ' for i, (word, hot) in enumerate(kit.tokens(item["text"]))
    )
    sub = f'<p class="sub">{kit.esc(item["subtitle"])}</p>' if item.get("subtitle") else ""
    return f'<i class="bg"></i><div class="in">{head}<i class="rule"></i><h1>{words}</h1>{sub}{pips}</div>'


# Timeline


def _timeline_scroll(item: dict[str, Any]) -> str:
    rows = kit.rows(item, "events")
    total = kit.seconds(item)
    count = len(rows)
    gap = 640.0
    even = [gap * i for i in range(count)]
    dates = []
    for row in rows:
        try:
            dates.append(float(str(row.get("date", "")).strip()))
        except ValueError:
            dates = []
            break
    xs = even
    if len(dates) == count and count > 1 and all(a < b for a, b in zip(dates, dates[1:], strict=False)):
        scaled = [(d - dates[0]) / (dates[-1] - dates[0]) * gap * (count - 1) for d in dates]
        if min(b - a for a, b in zip(scaled, scaled[1:], strict=False)) >= 520:
            xs = scaled
    beats = _beats(rows, 0.6, total * 0.8)
    hop = min((b - a for a, b in zip(beats, beats[1:], strict=False)), default=1.0)
    move = min(0.8, hop * 0.6)
    span = total
    mark = _mark(item)
    camera = [(0.0, f"transform:translateX(0);{GLIDE}")]
    reach = xs[-1] + 8
    for index, x in enumerate(xs):
        if index:
            camera.append(((beats[index] - move) / span * 100, f"transform:translateX(calc({-xs[index - 1]:.1f}px * var(--scale)));{GLIDE}"))
        camera.append((beats[index] / span * 100, f"transform:translateX(calc({-x:.1f}px * var(--scale)));{GLIDE}"))
    cells = []
    for index, (row, x) in enumerate(zip(rows, xs, strict=True)):
        on = beats[index] - move * 0.4
        off = beats[index + 1] - move * 0.8 if index < count - 1 else None
        states = f"tlon .3s ease-out {on:.2f}s both" + (f",tloff .3s ease-out {off:.2f}s forwards" if off else "")
        dot = f"tlon .3s ease-out {on:.2f}s both" + (f",tloff .3s ease-out {off:.2f}s forwards" if off else "")
        cells.append(
            f'<div class="ev" style="left:calc({x:.1f}px * var(--scale));animation:{states}"><span class="dt">{kit.esc(row.get("date", ""))}</span><i class="dot" style="animation:{dot}"></i><p>{kit.esc(row["text"])}</p></div>'
        )
    motion = f"{span:.3f}s linear both"
    lane = '<i class="lane"></i>'
    track = f'<div class="track" style="--far:{reach + 1800:.1f}px;animation:{mark}c {motion}">{lane}{"".join(cells)}</div>'
    return _card(item, f'<style>{_keys(f"{mark}c", camera)}</style><div class="vp">{track}</div>')


# Labelled diagram

CELL = (
    "M50 6 C72 5 92 20 94 44 C96 68 80 92 54 94 C28 96 8 80 6 54 C4 30 24 7 50 6 Z",
    "M28 50 a18 18 0 1 0 36 0 a18 18 0 1 0 -36 0",
    "M41 45 a6 6 0 1 0 12 0 a6 6 0 1 0 -12 0",
    "M63 24 C67 18 80 18 84 25 C87 32 77 37 71 36 C64 36 59 30 63 24 Z",
    "M66 28 q3 -5 5 0 t5 0 t5 -1",
    "M20 72 C25 66 37 68 39 74 C41 81 32 85 26 84 C19 83 16 77 20 72 Z",
    "M23 75 q3 -4 4 0 t5 0 t4 0",
    "M24 28 C32 19 44 17 54 21",
    "M19 35 C28 24 44 21 58 27",
    "M67 55 q9 5 18 0",
    "M67 61 q9 5 18 0",
    "M68 67 q8 5 16 0",
    "M58 80 a3 3 0 1 0 6 0 a3 3 0 1 0 -6 0",
    "M69 79 a2 2 0 1 0 4 0 a2 2 0 1 0 -4 0",
)
SAFE_PATH = re.compile(r"[MmLlHhVvCcSsQqTtAaZz0-9 ,.\-]+")


def _diagram_labels(item: dict[str, Any]) -> str:
    rows = kit.rows(item, "labels")
    paths = [str(p.get("d") if isinstance(p, dict) else p) for p in item.get("paths") or []] or list(CELL)
    if not all(SAFE_PATH.fullmatch(path) for path in paths):
        _fail("diagram_labels", '"paths" holds SVG path data only.')
    total = kit.seconds(item)
    height, side, x0, y0 = 450, 410, 199.0, 20.0
    beats = _beats(rows, 1.5, total * 0.74)
    picks = []
    for row, at in zip(rows, beats, strict=True):
        x = _num(row.get("x"), "diagram_labels", "x")
        y = _num(row.get("y"), "diagram_labels", "y")
        picks.append({"text": row["text"], "x": x0 + side * x / 100, "y": y0 + side * y / 100, "left": x < 50, "at": at})
    for group in (True, False):
        chosen = [p for p in picks if p["left"] == group]
        ys = _spread([p["y"] for p in chosen], 66)
        shift = max(30 - min(ys, default=30), 0) - max(max(ys, default=0) - (height - 30), 0)
        for pick, y in zip(chosen, ys, strict=True):
            pick["ly"] = y + shift
    leaders, tags = [], []
    for pick in picks:
        end = 182.0 if pick["left"] else W - 182.0
        line = f'<line x1="{end:.1f}" y1="{pick["ly"]:.1f}" x2="{pick["x"]:.1f}" y2="{pick["y"]:.1f}" pathLength="1"/>'
        leaders.append(f'<div class="ly lead" style="animation:dkdraw .42s var(--ease) {pick["at"]:.2f}s both">{_svg(height, line)}</div>')
        tags.append(f'<i class="pin" style="left:{_pct(pick["x"], W)};top:{_pct(pick["y"], height)};animation-delay:{pick["at"] + 0.3:.2f}s"></i>')
        edge = f"right:{_pct(W - end + 10, W)};text-align:right" if pick["left"] else f"left:{_pct(end + 10, W)}"
        tags.append(f'<span class="tag" style="{edge};top:{_pct(pick["ly"], height)};animation-delay:{pick["at"] + 0.04:.2f}s">{kit.esc(pick["text"])}</span>')
    art = "".join(f'<path d="{path}" pathLength="1"/>' for path in paths)
    tint = "".join(f'<path d="{path}"/>' for path in paths[:2])
    box = f"left:{_pct(x0, W)};top:{_pct(y0, height)};width:{_pct(side, W)};height:{_pct(side, height)}"
    figure = f'<div class="wash" style="{box}"><svg viewBox="0 0 100 100" aria-hidden="true">{tint}</svg></div><div class="fig" style="{box}"><svg viewBox="0 0 100 100" aria-hidden="true">{art}</svg></div>'
    return _card(item, f'<div class="plot" style="aspect-ratio:{W}/{height}">{figure}{"".join(leaders)}{"".join(tags)}</div>')


FORMATS = (
    Format(
        id="equation_morph",
        title="Equation morph",
        summary="Algebra rewrites itself term by term, each term sliding to its new place.",
        group="teach",
        jobs=("explain",),
        slots=(Slot("steps", kind="list", limit=5, each=24), Slot("notes", kind="list", limit=5, each=44, required=False), Slot("title", limit=48, required=False)),
        example={
            "title": "Solve for x",
            "steps": [{"text": "2x + 3 = 11"}, {"text": "2x = 11 - 3"}, {"text": "2x = 8"}, {"text": "*x = 4*"}],
            "notes": ["Start with what we know", "Move the 3 across the equals sign", "Tidy up the right side", "Halve both sides"],
        },
        draw=_equation_morph,
        seconds=7.0,
        shortest=3.5,
        longest=16.0,
        tall=390,
        sound=None,
        fonts=("mono", "serif"),
        css=_skin(
            "equation_morph",
            """
.f-equation_morph .eq{position:relative;height:1.5em;margin-top:calc(16px * var(--scale));font-family:var(--serif);line-height:1.5}
.f-equation_morph .t{position:absolute;left:50%;top:0;white-space:pre;color:var(--ink)}
.f-equation_morph .t.v{font-style:italic}
.f-equation_morph .t.d{top:-.015em;font-family:var(--mono);font-weight:300;letter-spacing:0}
.f-equation_morph .up{position:relative;top:-.62em;font-size:.62em;font-style:inherit}
.f-equation_morph .why{position:relative;height:calc(52px * var(--scale));margin-top:calc(8px * var(--scale));text-align:center}
.f-equation_morph .why p{position:absolute;left:0;right:0;top:0;margin:0;font-size:calc(38px * var(--scale));line-height:1.2;letter-spacing:-.012em;color:var(--soft)}
""",
        ),
    ),
    Format(
        id="function_plot",
        title="Function plot",
        summary="A real curve draws on, then reshapes itself as a parameter changes.",
        group="teach",
        jobs=("explain",),
        slots=(
            Slot("title", limit=48),
            Slot("expr"),
            Slot("x_min", kind="number"),
            Slot("x_max", kind="number"),
            Slot("param", required=False),
            Slot("values", kind="list", limit=5, required=False),
            Slot("y_min", kind="number", required=False),
            Slot("y_max", kind="number", required=False),
        ),
        example={"title": "Bigger *a*, taller wave", "expr": "a*sin(x)", "param": "a", "values": [1, 2.5, 0.5], "x_min": -6.3, "x_max": 6.3, "y_min": -3, "y_max": 3},
        draw=_function_plot,
        seconds=7.0,
        shortest=2.5,
        longest=14.0,
        tall=730,
        sound="rise",
        fonts=("serif",),
        css=_skin(
            "function_plot",
            """
.f-function_plot .gl{stroke:var(--hair);stroke-width:1.5}
.f-function_plot .ax{stroke:color-mix(in srgb,var(--ink) 50%,transparent);stroke-width:2.5}
.f-function_plot .grid{animation:dkfade .5s .25s ease-out both}
.f-function_plot .fx{display:flex;align-items:center;justify-content:space-between;gap:calc(20px * var(--scale));margin-top:calc(10px * var(--scale));font-size:calc(46px * var(--scale))}
.f-function_plot .sum{font-family:var(--serif);color:var(--ink);white-space:nowrap}
.f-function_plot sup{font-size:.6em;line-height:0;vertical-align:.6em}
.f-function_plot .pval{flex:none;padding:calc(2px * var(--scale)) calc(16px * var(--scale));border-radius:calc(10px * var(--scale));background:var(--accent);color:var(--panel);font-size:calc(34px * var(--scale));font-weight:700;animation:dkfade .3s ease-out .5s both}
.f-function_plot .curve path{fill:none;stroke:var(--accent);stroke-width:7;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:1}
.f-function_plot .curve{animation:dkdraw var(--d) cubic-bezier(.5,0,.2,1) var(--b) both}
.f-function_plot .curve.ghost{animation:dkdraw var(--d) cubic-bezier(.5,0,.2,1) var(--b) both,fpghost .3s linear var(--g) forwards}
.f-function_plot .win{position:absolute;inset:0;overflow:hidden;animation:fpshow .01s linear var(--g) both}
.f-function_plot .film{position:absolute;left:0;top:0;width:100%;display:flex;flex-direction:column}
.f-function_plot .film svg{position:static;flex:1 1 0;min-height:0;width:100%;height:auto;overflow:hidden}
.f-function_plot .film path{fill:none;stroke:var(--accent);stroke-width:7;stroke-linecap:round;stroke-linejoin:round}
@keyframes fpghost{to{opacity:.3}}
@keyframes fpshow{from{opacity:0}to{opacity:1}}
""",
        ),
    ),
    Format(
        id="geometry_proof",
        title="Geometry proof",
        summary="Shapes slide or turn to show why a theorem is true, with nothing hidden.",
        group="teach",
        jobs=("explain",),
        slots=(
            Slot("title", limit=48),
            Slot("proof", kind="choice", choices=("pythagoras", "triangle_sum"), required=False),
            Slot("captions", kind="list", limit=3, each=62, required=False),
        ),
        example={"title": "Why *a² + b² = c²*", "proof": "pythagoras"},
        draw=_geometry_proof,
        seconds=9.0,
        shortest=6.0,
        longest=16.0,
        tall=630,
        sound=None,
        fonts=("serif",),
        css=_skin(
            "geometry_proof",
            """
.f-geometry_proof .row{display:flex;align-items:center;gap:calc(26px * var(--scale));margin-top:calc(16px * var(--scale))}
.f-geometry_proof .stage{position:relative;flex:none;width:calc(440px * var(--scale));height:calc(440px * var(--scale))}
.f-geometry_proof .stage svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
.f-geometry_proof .base rect,.f-geometry_proof .base line{fill:none;stroke:var(--mute);stroke-width:2}
.f-geometry_proof text{fill:var(--soft);font-family:var(--serif);font-size:30px;font-style:italic}
.f-geometry_proof .pc{animation:gpin .4s var(--ease) var(--t) both,gpmove 1s cubic-bezier(.62,0,.2,1) var(--m) forwards}
.f-geometry_proof .pc polygon{fill:color-mix(in srgb,var(--ink) 26%,var(--panel));stroke:var(--panel);stroke-width:2}
.f-geometry_proof .gap polygon{fill:var(--accent)}
.f-geometry_proof .gap.outc{animation:dkfade .4s ease-out var(--t) both,gpout .3s ease-in var(--m) forwards}
.f-geometry_proof .gap.inab{animation:dkfade .45s ease-out var(--t) both}
.f-geometry_proof .nm{position:absolute;transform:translate(-50%,-50%);font-family:var(--serif);font-size:calc(58px * var(--scale));font-style:italic;color:var(--panel)}
.f-geometry_proof .say{min-width:0;flex:1;position:relative;align-self:stretch;display:flex;flex-direction:column;justify-content:center;gap:calc(14px * var(--scale))}
.f-geometry_proof .beat{margin:0;font-size:calc(31px * var(--scale));line-height:1.18;letter-spacing:-.01em;color:var(--soft);animation:dkrise .42s var(--ease) var(--t) both}
.f-geometry_proof .sum{margin:calc(6px * var(--scale)) 0 0;font-family:var(--serif);font-size:calc(52px * var(--scale));line-height:1.1;color:var(--accent);white-space:nowrap;animation:dkrise .5s var(--ease) var(--t) both}
.f-geometry_proof .sum i{font-style:normal}
.f-geometry_proof sup{font-size:.58em;line-height:0;vertical-align:.62em}
.f-geometry_proof .ka{color:var(--ink)}
.f-geometry_proof .kb{color:var(--soft)}
.f-geometry_proof .kc{color:var(--accent)}
.f-geometry_proof .hull{fill:color-mix(in srgb,var(--ink) 9%,transparent);stroke:var(--ink);stroke-width:3;stroke-linejoin:round}
.f-geometry_proof .ta{fill:var(--ink)}
.f-geometry_proof .tb{fill:var(--soft)}
.f-geometry_proof .tc{fill:var(--accent)}
.f-geometry_proof .par{stroke:var(--mute);stroke-width:3;stroke-dasharray:1}
.f-geometry_proof .paral{animation:dkdraw .7s cubic-bezier(.5,0,.2,1) var(--t) both}
.f-geometry_proof .stay{animation:dkfade .4s ease-out var(--t) both}
.f-geometry_proof .turn{animation:dkfade .01s linear var(--t) both,gpturn 1.05s cubic-bezier(.62,0,.2,1) var(--m) forwards}
@keyframes gpin{from{opacity:0;transform:scale(.86)}to{opacity:1;transform:none}}
@keyframes gpmove{from{transform:none}to{transform:translate(var(--x),var(--y))}}
@keyframes gpout{to{opacity:0}}
@keyframes gpturn{from{transform:rotate(0)}to{transform:rotate(180deg)}}
""",
        ),
    ),
    Format(
        id="code_walk",
        title="Code walkthrough",
        summary="Code sits still while a highlight moves line by line with a plain caption.",
        group="teach",
        jobs=("explain",),
        slots=(Slot("code", kind="list", limit=10, each=38), Slot("steps", kind="list", limit=5, each=60), Slot("title", limit=48, required=False)),
        example={
            "title": "Reading a sample file",
            "code": ["def total(prices):", "    # start from nothing", "    sum = 0", "    for price in prices:", "        sum += price", "    return sum"],
            "steps": [
                {"text": "Keep a running total, *starting at zero*", "line": 3},
                {"text": "Visit every price *once*", "line": 4},
                {"text": "Add it on, then *hand back* the result", "line": [5, 6]},
            ],
        },
        draw=_code_walk,
        seconds=7.5,
        shortest=3.5,
        longest=16.0,
        tall=740,
        sound="tick",
        fonts=("mono",),
        css=_skin(
            "code_walk",
            """
.f-code_walk .sheet{position:relative;margin-top:calc(18px * var(--scale));font-family:var(--mono);font-size:calc(30px * var(--scale))}
.f-code_walk .ln{position:relative;display:flex;height:1.46em;line-height:1.46em;white-space:pre}
.f-code_walk .no{flex:none;width:2.1em;padding-right:.7em;text-align:right;color:var(--soft);opacity:.7}
.f-code_walk code{font:inherit;color:var(--ink)}
.f-code_walk .band{position:absolute;left:calc(-14px * var(--scale));right:calc(-14px * var(--scale));top:0;height:1.46em;border-radius:calc(8px * var(--scale));background:color-mix(in srgb,var(--accent) 17%,transparent);transform-origin:0 0}
.f-code_walk .key{color:var(--accent)}
.f-code_walk .text{color:color-mix(in srgb,var(--accent) 56%,var(--ink))}
.f-code_walk .note{color:var(--soft)}
.f-code_walk .num{color:color-mix(in srgb,var(--accent) 56%,var(--ink))}
.f-code_walk .fn{color:var(--ink);font-weight:600}
.f-code_walk .why{position:relative;height:calc(92px * var(--scale));margin-top:calc(16px * var(--scale))}
.f-code_walk .why p{position:absolute;left:0;right:0;top:0;margin:0;font-size:calc(36px * var(--scale));font-weight:500;line-height:1.2;letter-spacing:-.016em}
""",
        ),
    ),
    Format(
        id="algo_step",
        title="Algorithm stepper",
        summary="A real sort or search runs on your numbers, one visible step at a time.",
        group="teach",
        jobs=("explain",),
        slots=(
            Slot("title", limit=48),
            Slot("values", kind="list", limit=9),
            Slot("algo", kind="choice", choices=("bubble", "insertion", "selection", "binary"), required=False),
            Slot("target", kind="number", required=False),
        ),
        example={"title": "Bubble sort, step by step", "algo": "bubble", "values": [5, 2, 8, 3, 6, 1]},
        draw=_algo_step,
        seconds=9.0,
        shortest=4.0,
        longest=20.0,
        tall=620,
        sound="tick",
        css=_skin(
            "algo_step",
            """
.f-algo_step .now{margin:calc(10px * var(--scale)) 0 0;font-size:calc(40px * var(--scale));font-weight:500;letter-spacing:-.016em;color:var(--accent);line-height:1.16}
.f-algo_step .bars{position:relative;height:calc(370px * var(--scale));margin-top:calc(18px * var(--scale));border-bottom:calc(3px * var(--scale)) solid color-mix(in srgb,var(--ink) 50%,transparent)}
.f-algo_step .el{position:absolute;left:0;bottom:0;height:100%;padding:0 calc(6px * var(--scale))}
.f-algo_step .in{--lab:calc(50px * var(--scale));position:absolute;left:calc(6px * var(--scale));right:calc(6px * var(--scale));bottom:0;height:calc(var(--lab) + (100% - var(--lab)) * var(--f));display:flex;flex-direction:column;align-items:center}
.f-algo_step .in span{flex:none;height:var(--lab);font-size:calc(34px * var(--scale));font-weight:600}
.f-algo_step .in b{flex:1;width:100%}
""",
        ),
    ),
    Format(
        id="vocab_card",
        title="Vocabulary card",
        summary="A term, what it means and one example, set like a page from a dictionary.",
        group="teach",
        jobs=("explain",),
        slots=(
            Slot("term", limit=20),
            Slot("meaning", limit=110),
            Slot("kind", limit=12, required=False),
            Slot("sound", limit=22, required=False),
            Slot("example", limit=90, required=False),
        ),
        example={
            "term": "Compound",
            "kind": "verb",
            "sound": "kom-POWND",
            "meaning": "To grow by adding each gain to the total, so later gains *build on earlier ones*.",
            "example": "Small savings *compound* over the years.",
        },
        draw=_vocab_card,
        seconds=8.6,
        shortest=3.0,
        longest=12.0,
        tall=520,
        sound="pop",
        fonts=("serif",),
        css="""
.f-vocab_card{max-width:calc(940px * var(--scale))}
.f-vocab_card .sheet{padding:calc(44px * var(--scale)) calc(46px * var(--scale));border-radius:calc(20px * var(--scale));background:var(--ink);color:var(--panel);box-shadow:0 28px 80px rgba(0,0,0,.42);animation:vcin .5s var(--ease) both}
.f-vocab_card .tags{display:flex;gap:calc(20px * var(--scale));font-size:calc(31px * var(--scale));letter-spacing:.005em;opacity:.72;animation:vcfade .4s .3s ease-out both}
.f-vocab_card .tags span:first-child{font-style:italic;font-family:var(--serif);font-size:1.16em;line-height:1}
.f-vocab_card .term{font-family:var(--serif);line-height:1.08;letter-spacing:-.018em;white-space:nowrap}
.f-vocab_card .term .word{display:inline-block;overflow:hidden;padding-bottom:.12em;margin-bottom:-.12em}
.f-vocab_card .term .c{display:inline-block;animation:vcup .56s var(--ease) both;animation-delay:calc(.14s + var(--i) * .022s)}
.f-vocab_card .rule{display:block;height:calc(3px * var(--scale));margin:calc(16px * var(--scale)) 0 calc(22px * var(--scale));background:var(--panel);transform-origin:0 50%;animation:vcline .6s .38s var(--ease) both}
.f-vocab_card .mean{margin:0;font-size:calc(40px * var(--scale));font-weight:500;line-height:1.22;letter-spacing:-.016em;text-wrap:pretty;animation:vcrise .46s .56s var(--ease) both}
.f-vocab_card .mean em{color:inherit;background:linear-gradient(0deg,var(--accent) 42%,transparent 42%)}
.f-vocab_card .eg{margin:calc(24px * var(--scale)) 0 0;font-family:var(--serif);font-size:calc(40px * var(--scale));font-style:italic;line-height:1.2;animation:vcrise .46s .84s var(--ease) both}
.f-vocab_card .eg em{color:inherit;background:linear-gradient(0deg,var(--accent) 42%,transparent 42%)}
@keyframes vcin{from{opacity:0;transform:translateY(calc(24px * var(--scale)))}to{opacity:1;transform:none}}
@keyframes vcfade{from{opacity:0}to{opacity:.72}}
@keyframes vcup{from{transform:translateY(110%)}to{transform:none}}
@keyframes vcline{from{transform:scaleX(0)}to{transform:none}}
@keyframes vcrise{from{opacity:0;transform:translateY(calc(14px * var(--scale)))}to{opacity:1;transform:none}}
""",
    ),
    Format(
        id="quiz_reveal",
        title="Quiz reveal",
        summary="A question holds while a timer runs down, then the right answer lands.",
        group="teach",
        jobs=("hook", "explain"),
        slots=(Slot("question", limit=80), Slot("options", kind="list", limit=4, each=30), Slot("answer", kind="number"), Slot("note", limit=48, required=False)),
        example={"question": "Which shape has the *most* sides?", "options": ["Hexagon", "Octagon", "Pentagon", "Decagon"], "answer": 4},
        draw=_quiz_reveal,
        seconds=6.0,
        shortest=3.0,
        longest=14.0,
        tall=715,
        sound="tick",
        css=_skin(
            "quiz_reveal",
            """
.f-quiz_reveal h2{font-size:calc(52px * var(--scale))}
.f-quiz_reveal .timer{height:calc(6px * var(--scale));margin:calc(22px * var(--scale)) 0;border-radius:9px;overflow:hidden;background:var(--hair)}
.f-quiz_reveal .timer i{display:block;height:100%;background:var(--accent);transform-origin:0 50%}
.f-quiz_reveal .opts{display:flex;flex-direction:column;gap:calc(12px * var(--scale))}
.f-quiz_reveal .opt{display:flex;align-items:center;gap:calc(20px * var(--scale));min-height:calc(84px * var(--scale));padding:calc(10px * var(--scale)) calc(20px * var(--scale));border-radius:calc(16px * var(--scale));background:var(--surface);box-shadow:0 0 0 1px var(--hair) inset;font-size:calc(40px * var(--scale));font-weight:500;letter-spacing:-.016em}
.f-quiz_reveal .opt b{flex:none;display:grid;place-items:center;width:calc(52px * var(--scale));height:calc(52px * var(--scale));border-radius:calc(12px * var(--scale));background:color-mix(in srgb,var(--ink) 13%,transparent);font-size:calc(30px * var(--scale));font-weight:600}
.f-quiz_reveal .tx{flex:1;min-width:0}
.f-quiz_reveal .tk{flex:none;width:calc(52px * var(--scale));height:calc(52px * var(--scale))}
.f-quiz_reveal .tk svg{display:block;width:100%;height:100%}
.f-quiz_reveal .tk path{fill:none;stroke:var(--panel);stroke-width:6;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:1}
@keyframes qzrun{from{transform:none}to{transform:scaleX(0)}}
@keyframes qzwin{to{background:var(--accent);color:var(--panel)}}
@keyframes qzlose{to{opacity:.38}}
""",
        ),
    ),
    Format(
        id="timeline_scroll",
        title="Timeline scroller",
        summary="Dated events pass along a line, spaced by real time when the dates are years.",
        group="teach",
        jobs=("explain",),
        slots=(Slot("title", limit=48), Slot("events", kind="list", limit=6, each=56), Slot("note", limit=48, required=False)),
        example={
            "title": "A sample town's *railway*",
            "events": [
                {"date": "1868", "text": "The first line reaches the sample town"},
                {"date": "1902", "text": "A second platform opens for freight"},
                {"date": "1951", "text": "Steam gives way to diesel engines"},
                {"date": "1994", "text": "The station is rebuilt as it stands today"},
            ],
            "note": "Invented history for illustration",
        },
        draw=_timeline_scroll,
        seconds=8.0,
        shortest=3.5,
        longest=18.0,
        tall=540,
        sound="whoosh",
        css=_skin(
            "timeline_scroll",
            """
.f-timeline_scroll .vp{position:relative;height:calc(300px * var(--scale));margin-top:calc(18px * var(--scale));overflow:hidden;-webkit-mask-image:linear-gradient(90deg,#000 86%,transparent);mask-image:linear-gradient(90deg,#000 86%,transparent)}
.f-timeline_scroll .track{position:absolute;left:0;top:0;bottom:0;width:0}
.f-timeline_scroll .lane{position:absolute;left:calc(-900px * var(--scale));top:calc(114px * var(--scale));width:calc(var(--far) * var(--scale));height:calc(4px * var(--scale));background:var(--hair)}
.f-timeline_scroll .ev{position:absolute;top:0;width:calc(580px * var(--scale))}
.f-timeline_scroll .dt{display:block;height:calc(96px * var(--scale));font-size:calc(76px * var(--scale));font-weight:700;line-height:calc(96px * var(--scale));letter-spacing:-.03em;white-space:nowrap;font-variant-numeric:tabular-nums}
.f-timeline_scroll .dot{display:block;width:calc(30px * var(--scale));height:calc(30px * var(--scale));margin-top:calc(6px * var(--scale));border-radius:50%;background:var(--accent);box-shadow:0 0 0 calc(7px * var(--scale)) var(--panel)}
.f-timeline_scroll .ev p{margin:calc(18px * var(--scale)) 0 0;font-size:calc(40px * var(--scale));font-weight:500;line-height:1.18;letter-spacing:-.018em;text-wrap:balance}
@keyframes tlon{from{opacity:.32}to{opacity:1}}
@keyframes tloff{from{opacity:1}to{opacity:.32}}
""",
        ),
    ),
    Format(
        id="diagram_labels",
        title="Labelled diagram",
        summary="A figure draws itself, then each part gets its name on a clean leader line.",
        group="teach",
        jobs=("explain",),
        slots=(
            Slot("title", limit=48),
            Slot("labels", kind="list", limit=6, each=12),
            Slot("paths", kind="list", required=False),
            Slot("note", limit=48, required=False),
        ),
        example={
            "title": "Inside an animal *cell*",
            "labels": [
                {"text": "Membrane", "x": 12, "y": 30},
                {"text": "Nucleus", "x": 38, "y": 58},
                {"text": "Vesicle", "x": 28, "y": 82},
                {"text": "Mitochondria", "x": 78, "y": 24},
                {"text": "Golgi body", "x": 80, "y": 60},
            ],
            "note": "Simplified drawing, not to scale",
        },
        draw=_diagram_labels,
        seconds=7.0,
        shortest=4.0,
        longest=14.0,
        tall=725,
        sound="pop",
        css=_skin(
            "diagram_labels",
            """
.f-diagram_labels .fig{position:absolute;animation:dkdraw 1.25s cubic-bezier(.45,0,.2,1) .3s both}
.f-diagram_labels .fig svg,.f-diagram_labels .wash svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
.f-diagram_labels .wash{position:absolute;animation:dkfade .7s ease-out 1.1s both}
.f-diagram_labels .wash path{fill:color-mix(in srgb,var(--accent) 11%,transparent)}
.f-diagram_labels .fig path{fill:none;stroke:var(--ink);stroke-width:.78;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:1;opacity:.92}
.f-diagram_labels .lead line{stroke:var(--accent);stroke-width:2.5;stroke-dasharray:1}
.f-diagram_labels .pin{position:absolute;width:calc(16px * var(--scale));height:calc(16px * var(--scale));margin:calc(-8px * var(--scale));border-radius:50%;background:var(--accent);box-shadow:0 0 0 calc(4px * var(--scale)) var(--panel);animation:dgpop .3s var(--ease) both}
.f-diagram_labels .tag{position:absolute;transform:translateY(-50%);font-size:calc(30px * var(--scale));font-weight:600;letter-spacing:-.012em;white-space:nowrap;animation:dkfade .3s ease-out both}
@keyframes dgpop{from{opacity:0;transform:scale(.3)}to{opacity:1;transform:none}}
""",
        ),
    ),
    Format(
        id="chapter_card",
        title="Chapter card",
        summary="A calm full-screen title that marks a new section and where it falls.",
        group="teach",
        jobs=("transition",),
        slots=(
            Slot("text", limit=44),
            Slot("number", kind="number", required=False),
            Slot("of", kind="number", required=False),
            Slot("subtitle", limit=70, required=False),
        ),
        example={"text": "How the *tide* turns", "number": 2, "of": 5, "subtitle": "What changes when the moon does the work"},
        draw=_chapter_card,
        seconds=4.2,
        shortest=2.0,
        longest=6.0,
        area="full",
        tall=1080,
        sound="whoosh",
        fonts=("mono", "serif"),
        needs_alpha=False,
        css="""
.f-chapter_card .bg{position:absolute;inset:0;background:radial-gradient(120% 80% at 12% 18%,var(--surface),var(--panel) 62%);animation:chfade .36s ease-out both}
.f-chapter_card .in{position:absolute;left:calc(var(--gutter) * 1.25);right:calc(var(--gutter) * 1.25);top:50%;transform:translateY(-50%)}
.f-chapter_card .no{margin:0;font-family:var(--mono);font-size:calc(34px * var(--scale));font-weight:500;letter-spacing:.02em;color:var(--accent);animation:chfade .5s .22s ease-out both}
.f-chapter_card .rule{display:block;width:calc(180px * var(--scale));height:calc(3px * var(--scale));margin:calc(26px * var(--scale)) 0 calc(30px * var(--scale));background:var(--accent);transform-origin:0 50%;animation:chline .8s .26s var(--ease) both}
.f-chapter_card h1{margin:0;font-family:var(--serif);font-size:calc(152px * var(--scale));font-weight:400;line-height:1.04;letter-spacing:-.018em}
.f-chapter_card .m{display:inline-block;overflow:hidden;padding-bottom:.14em;margin-bottom:-.14em;vertical-align:top}
.f-chapter_card .w{display:inline-block;animation:chup .8s var(--ease) both;animation-delay:calc(.36s + var(--i) * .07s)}
.f-chapter_card .w.hot{color:var(--accent)}
.f-chapter_card .sub{margin:calc(34px * var(--scale)) 0 0;font-size:calc(42px * var(--scale));line-height:1.2;letter-spacing:-.016em;color:var(--soft);text-wrap:balance;animation:chrise .7s .76s var(--ease) both}
.f-chapter_card .pips{display:flex;gap:calc(10px * var(--scale));margin-top:calc(44px * var(--scale));animation:chfade .5s .9s ease-out both}
.f-chapter_card .pips i{width:calc(44px * var(--scale));height:calc(5px * var(--scale));background:color-mix(in srgb,var(--ink) 20%,transparent)}
.f-chapter_card .pips i.on{background:var(--accent)}
@keyframes chfade{from{opacity:0}to{opacity:1}}
@keyframes chline{from{transform:scaleX(0)}to{transform:none}}
@keyframes chup{from{transform:translateY(112%)}to{transform:none}}
@keyframes chrise{from{opacity:0;transform:translateY(calc(16px * var(--scale)))}to{opacity:1;transform:none}}
""",
    ),
    Format(
        id="recap_check",
        title="Recap checklist",
        summary="Key points return one by one and each earns a drawn tick.",
        group="teach",
        jobs=("close", "explain"),
        slots=(Slot("title", limit=48), Slot("points", kind="list", limit=5, each=40)),
        example={
            "title": "What to *remember*",
            "points": ["Split the work into small steps", "Check each step before the next", "Write down what you changed", "Stop when the test passes"],
        },
        draw=_recap_check,
        seconds=5.5,
        shortest=2.8,
        longest=12.0,
        tall=760,
        sound="ding",
        css=_skin(
            "recap_check",
            """
.f-recap_check .list{margin-top:calc(14px * var(--scale))}
.f-recap_check .pt{display:flex;align-items:center;gap:calc(22px * var(--scale));min-height:calc(92px * var(--scale));padding:calc(10px * var(--scale)) 0;border-top:1px solid var(--hair);animation:rcon .34s ease-out both}
.f-recap_check .box{position:relative;flex:none;width:calc(54px * var(--scale));height:calc(54px * var(--scale));border-radius:calc(14px * var(--scale));box-shadow:0 0 0 calc(3px * var(--scale)) var(--mute) inset}
.f-recap_check .box u{position:absolute;inset:0;border-radius:inherit;background:var(--accent);animation:rcpop .3s var(--ease) both}
.f-recap_check .tk{position:absolute;inset:0}
.f-recap_check .tk svg{display:block;width:100%;height:100%}
.f-recap_check .tk path{fill:none;stroke:var(--panel);stroke-width:6;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:1}
.f-recap_check .tx{min-width:0;font-size:calc(40px * var(--scale));font-weight:500;line-height:1.16;letter-spacing:-.018em;text-wrap:balance}
@keyframes rcon{from{opacity:.24}to{opacity:1}}
@keyframes rcpop{from{opacity:0;transform:scale(.4)}to{opacity:1;transform:none}}
""",
        ),
    ),
)
