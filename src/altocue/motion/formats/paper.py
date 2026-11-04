"""Paper and stop-motion: torn sheets, cut letters, tape and ink, moved by hand at eight frames a second.

Every wobble is seeded from the copy, so a render never changes. Motion lives on HTML elements only:
the renderer seeks those per clip, and it would seek SVG children and pseudo-elements to film time.
"""

from __future__ import annotations

import hashlib
import math
from urllib.parse import quote

from . import Format, Slot, kit

TICK = 0.125  # one frame of stop-motion, in seconds


class _Dice:
    """Numbers from the copy. Same words, same tear."""

    def __init__(self, *seed: object) -> None:
        self.key, self.count = "|".join(str(part) for part in seed), 0

    def unit(self) -> float:
        self.count += 1
        return int.from_bytes(hashlib.sha256(f"{self.key}#{self.count}".encode()).digest()[:6], "big") / 2**48

    def span(self, low: float, high: float) -> float:
        return low + (high - low) * self.unit()

    def pick(self, options: tuple | list):
        return options[min(int(self.unit() * len(options)), len(options) - 1)]

    def tag(self) -> str:
        return hashlib.sha256(self.key.encode()).hexdigest()[:7]


def _tick(seconds: float) -> float:
    """Snap a time to the stop-motion grid, so everything on the table moves on the same frame."""
    return round(seconds / TICK) * TICK


def _keys(name: str, *poses: str) -> str:
    """Evenly spaced poses. Used with step-end, each one holds until the next: posed by hand, not eased."""
    last = len(poses) - 1
    return f"@keyframes {name}{{" + "".join(f"{index * 100 / last:.4g}%{{{pose}}}" for index, pose in enumerate(poses)) + "}\n"


def _fly(name: str, *shares: float, extra: dict[int, str] | None = None) -> str:
    """A move from an offset to rest in hand-spaced poses. `--x`, `--y` and `--r` set the offset per element."""
    extra = extra or {}
    poses = [f"transform:translate(calc(var(--x) * {s}),calc(var(--y) * {s})) rotate(calc(var(--r) * {s}));{extra.get(i, 'opacity:1')}" for i, s in enumerate(shares)]
    return _keys(name, *poses)


def _grain(dice: _Dice, depth: float = 0.3) -> str:
    """A tile of paper tooth and long fibres, drawn once and repeated."""
    one, two = int(dice.span(1, 900)), int(dice.span(1, 900))
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='260' height='260'>"
        f"<filter id='a' x='0' y='0' width='100%' height='100%'><feTurbulence type='fractalNoise' baseFrequency='.74' numOctaves='3' seed='{one}' stitchTiles='stitch'/>"
        f"<feColorMatrix values='0 0 0 0 .3 0 0 0 0 .22 0 0 0 0 .13 0 0 0 {depth * 2.5:.2f} {-depth:.2f}'/></filter>"
        f"<filter id='b' x='0' y='0' width='100%' height='100%'><feTurbulence type='fractalNoise' baseFrequency='.03 .42' numOctaves='2' seed='{two}' stitchTiles='stitch'/>"
        f"<feColorMatrix values='0 0 0 0 .34 0 0 0 0 .25 0 0 0 0 .14 0 0 0 {depth * 1.5:.2f} {-depth * 0.66:.2f}'/></filter>"
        "<rect width='260' height='260' filter='url(#a)'/><rect width='260' height='260' filter='url(#b)'/></svg>"
    )
    return "url('data:image/svg+xml," + quote(svg, safe="") + "')"


def _ragged(dice: _Dice, count: int) -> list[float]:
    """A torn line from 0 to 1: a slow drift, a middle wander, and fine fibres on top."""
    slow = [dice.unit() for _ in range(max(count // 11, 2) + 2)]
    mid = [dice.unit() for _ in range(max(count // 4, 2) + 2)]

    def at(table: list[float], where: float) -> float:
        place = where * (len(table) - 1)
        low = min(int(place), len(table) - 2)
        return table[low] + (table[low + 1] - table[low]) * (place - low)

    return [0.5 * at(slow, i / max(count - 1, 1)) + 0.32 * at(mid, i / max(count - 1, 1)) + 0.18 * dice.unit() for i in range(count)]


def _tear(dice: _Dice, top: float = 0, right: float = 0, bottom: float = 0, left: float = 0, along: int = 60, across: int = 18) -> str:
    """A clip-path polygon. Each number is how deep that side bites, in em, so tears keep their scale on any size of sheet."""
    points: list[str] = []

    def side(depth: float, count: int, place) -> None:
        if not depth:
            return
        line = _ragged(dice, count)
        points.extend(place(index / (count - 1), line[index] * depth) for index in range(count))

    if not top:
        points.append("0 0")
    side(top, along, lambda at, bite: f"{at * 100:.2f}% {bite:.3f}em")
    if not (top or right):
        points.append("100% 0")
    side(right, across, lambda at, bite: f"calc(100% - {bite:.3f}em) {at * 100:.2f}%")
    if not (right or bottom):
        points.append("100% 100%")
    side(bottom, along, lambda at, bite: f"{(1 - at) * 100:.2f}% calc(100% - {bite:.3f}em)")
    if not (bottom or left):
        points.append("0 100%")
    side(left, across, lambda at, bite: f"{bite:.3f}em {(1 - at) * 100:.2f}%")
    return "polygon(" + ",".join(points) + ")"


def _rip(dice: _Dice, body: str, kind: str = "", top: float = 0, right: float = 0, bottom: float = 0, left: float = 0, attrs: str = "", along: int = 60) -> str:
    """A torn sheet: the white core shows where the coloured face tore further back, which is what makes a tear read as paper."""
    core = _tear(dice, top * 0.55, right * 0.55, bottom * 0.55, left * 0.55, along=along)
    face = _tear(dice, top * 1.5, right * 1.5, bottom * 1.5, left * 1.5, along=along)
    return (
        f'<div class="rip {kind}" {attrs}><i class="core" style="clip-path:{core}"></i><i class="face" style="clip-path:{face}"></i><div class="ink">{body}</div></div>'
    )


def _snip(dice: _Dice) -> str:
    """Four scissor cuts with a slight miss at each corner."""
    d = dice.span
    pairs = [
        (d(0, 7), d(0, 9)),
        (d(40, 60), d(0, 4)),
        (d(93, 100), d(0, 10)),
        (d(96, 100), d(40, 60)),
        (d(92, 100), d(91, 100)),
        (d(40, 60), d(96, 100)),
        (d(0, 8), d(90, 100)),
        (d(0, 4), d(40, 60)),
    ]
    return "polygon(" + ",".join(f"{x:.1f}% {y:.1f}%" for x, y in pairs) + ")"


def _rough(dice: _Dice, bite: float, grain: float = 0.035, count: int = 3) -> tuple[str, str]:
    """Filters that shake an edge, plus the variables that name them. Swapping them on the beat makes drawn lines boil."""
    tag = "b" + dice.tag()
    body = "".join(
        f'<filter id="{tag}{i}" x="-6%" y="-6%" width="112%" height="112%"><feTurbulence type="fractalNoise" baseFrequency="{grain}" numOctaves="2" seed="{int(dice.span(1, 900))}"/>'
        f'<feDisplacementMap in="SourceGraphic" scale="{bite}" xChannelSelector="R" yChannelSelector="G"/></filter>'
        for i in range(count)
    )
    return f'<svg class="defs" aria-hidden="true">{body}</svg>', ";".join(f"--f{i}:url(#{tag}{i})" for i in range(count))


def _tokens(text: str) -> list[tuple[str, bool]]:
    """Words with their accent flag. Punctuation after a `*marked*` word stays on that word."""
    out: list[tuple[str, bool]] = []
    for word, hot in kit.tokens(text):
        if out and not word[0].isalnum() and word[0] not in "\"'(":
            out[-1] = (out[-1][0] + word, out[-1][1])
        else:
            out.append((word, hot))
    return out


def _lines(text: str, count: int = 2) -> list[list[tuple[str, bool]]]:
    """Split copy into balanced lines of words, for sheets that hold one line each."""
    words = _tokens(text)
    if len(words) <= 1 or count <= 1:
        return [words]
    target = sum(len(word) + 1 for word, _ in words) / count
    out: list[list[tuple[str, bool]]] = []
    line: list[tuple[str, bool]] = []
    used = 0
    for word, hot in words:
        if line and used + len(word) / 2 > target and len(out) < count - 1:
            out.append(line)
            line, used = [], 0
        line.append((word, hot))
        used += len(word) + 1
    return [*out, line]


def _set_words(words: list[tuple[str, bool]]) -> str:
    return " ".join(f"<em>{kit.esc(word)}</em>" if hot else kit.esc(word) for word, hot in words)


def _beats(item: dict, name: str, share: float) -> list[dict]:
    """Rows with a time on the grid. Spoken rows keep their word; the rest spread across `share` of the graphic."""
    rows = kit.rows(item, name)
    span = kit.seconds(item) * share
    for index, row in enumerate(rows):
        row["delay"] = _tick(row["delay"] if row.get("at") is not None else span * index / max(len(rows), 1))
    return rows


def _stock(fid: str) -> str:
    return (
        f""".f-{fid}{{--cream:#f3ebdc;--white:#fdfaf3;--kraft:#c8a577;--news:#e0d9c9;--brick:#d4412a;--gold:#efbd3f;--pen:#1c1813;
--thick:drop-shadow(0 calc(2px * var(--scale)) calc(1.5px * var(--scale)) rgba(20,12,4,.44)) drop-shadow(0 calc(13px * var(--scale)) calc(15px * var(--scale)) rgba(20,12,4,.32))}}
.f-{fid} .defs{{position:absolute;width:0;height:0}}
.f-{fid} .set{{animation:p-off .2s step-end var(--leave) forwards}}
.f-{fid} .rip{{position:relative;font-size:calc(100px * var(--scale));filter:var(--thick)}}
.f-{fid} .rip>i{{position:absolute;inset:0;display:block}}
.f-{fid} .core{{background:var(--white)}}
.f-{fid} .face{{background-color:var(--cream);background-image:var(--grain)}}
.f-{fid} .ink{{position:relative;color:var(--pen)}}
.f-{fid} .ink em{{color:var(--brick)}}
"""
        + _keys("p-off", "transform:none;opacity:1", "transform:translateY(calc(12px * var(--scale))) rotate(-.6deg);opacity:1", "opacity:0", "opacity:0")
        + _keys(
            "p-jit",
            "translate:0 0;rotate:0deg",
            "translate:.7px -.9px;rotate:.22deg",
            "translate:-.8px .4px;rotate:-.18deg",
            "translate:.5px .8px;rotate:.1deg",
            "translate:-.4px -.6px;rotate:-.26deg",
            "translate:.9px .2px;rotate:.16deg",
            "translate:0 0;rotate:0deg",
        )
    )


def _ransom(item: dict) -> str:
    dice = _Dice("ransom", item["text"])
    words = kit.tokens(item["text"])
    total = sum(len(word) for word, _ in words)
    per_tick = max(1, math.ceil(total * TICK / max(kit.seconds(item) * 0.55, TICK)))
    looks = (
        "news sans",
        "ink serif",
        "lit sans",
        "white mono",
        "kraft italic",
        "brick sans",
        "gold mono",
        "cream hand",
        "news italic",
        "ink mono",
        "white serif",
        "kraft sans",
    )
    out, index, last = [], 0, ""
    for word, hot in words:
        cells = []
        for char in word:
            look = dice.pick(looks)
            while look == last:
                look = dice.pick(looks)
            last = look
            at = (index // per_tick) * TICK
            spin, drop = dice.span(-6.5, 6.5), dice.span(-0.09, 0.09)
            size = dice.span(0.94, 1.08)
            cells.append(
                f'<span class="slot" style="--at:{at:.3f}s;--k:{int(dice.span(0, 6))};rotate:{spin:.1f}deg;translate:0 {drop:.3f}em">'
                f'<span class="tile {"lit" if hot and "ink" not in look else ""} {look}" style="clip-path:{_snip(dice)};font-size:{size:.2f}em">{kit.esc(char)}</span></span>'
            )
            index += 1
        out.append(f'<span class="word">{"".join(cells)}</span>')
    return f'<div class="set" style="--grain:{_grain(dice, 0.26)}"><div class="note">{"".join(out)}</div></div>'


def _torn_wipe(item: dict) -> str:
    dice = _Dice("wipe", item.get("text", ""), item.get("label", ""))

    def edge(lean: float, bite: float) -> str:
        """Slanted, ragged leading and trailing edges, in percent of a very wide sheet."""
        count = 56
        lead, trail = _ragged(dice, count), _ragged(dice, count)
        fore = [f"{4 + lead[i] * bite + lean * (1 - i / (count - 1)):.2f}% {i * 100 / (count - 1):.2f}%" for i in range(count)]
        back = [f"{96 - trail[i] * bite - lean * (i / (count - 1)):.2f}% {i * 100 / (count - 1):.2f}%" for i in range(count)]
        return "polygon(" + ",".join(fore + back[::-1]) + ")"

    def sheet(cls: str, body: str = "") -> str:
        return f'<div class="sheet {cls}"><i class="core" style="clip-path:{edge(5, 2.4)}"></i><i class="face" style="clip-path:{edge(5, 3.2)}"></i>{body}</div>'

    label = f"<p>{kit.esc(item['label'])}</p>" if item.get("label") else ""
    words = f'<div class="ink"><h2>{kit.emphasis(item["text"])}</h2>{label}</div>'
    return f'<div class="wipe" style="--grain:{_grain(dice, 0.34)}">{sheet("under")}{sheet("over", words)}</div>'


def _shards(dice: _Dice, cols: int, rows: int, wobble: int = 7) -> list[tuple[str, float, float]]:
    """Tear a sheet along shared ragged seams. Neighbours keep the same edge, so the pieces fit back exactly."""
    joint = {}
    for x in range(cols + 1):
        for y in range(rows + 1):
            joint[x, y] = (x * 100 / cols + (dice.span(-5, 5) if 0 < x < cols else 0), y * 100 / rows + (dice.span(-9, 9) if 0 < y < rows else 0))

    def seam(a: tuple[float, float], b: tuple[float, float], outer: bool) -> list[tuple[float, float]]:
        line = _ragged(dice, wobble)
        dx, dy = b[0] - a[0], b[1] - a[1]
        normal = (-dy, dx)
        size = math.hypot(*normal) or 1
        reach = 0.9 if outer else 1.7
        out = []
        for i in range(wobble):
            at = (i + 1) / (wobble + 1)
            push = (line[i] - 0.5) * reach * 2 * (1 if abs(dy) < abs(dx) else 0.5)
            out.append((a[0] + dx * at + normal[0] / size * push, a[1] + dy * at + normal[1] / size * push))
        return out

    across = {(x, y): seam(joint[x, y], joint[x + 1, y], y in (0, rows)) for x in range(cols) for y in range(rows + 1)}
    down = {(x, y): seam(joint[x, y], joint[x, y + 1], x in (0, cols)) for x in range(cols + 1) for y in range(rows)}
    pieces = []
    for y in range(rows):
        for x in range(cols):
            ring = [joint[x, y], *across[x, y], joint[x + 1, y], *down[x + 1, y], joint[x + 1, y + 1], *across[x, y + 1][::-1], joint[x, y + 1], *down[x, y][::-1]]
            middle = (sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring))
            pieces.append(("polygon(" + ",".join(f"{px:.2f}% {py:.2f}%" for px, py in ring) + ")", *middle))
    return pieces


def _torn_scraps(item: dict) -> str:
    dice = _Dice("scraps", item["text"])
    page = f"<h1>{kit.emphasis(item['text'])}</h1>"
    pieces = _shards(dice, 4, 2)
    order = sorted(range(len(pieces)), key=lambda _: dice.unit())
    gap = min(_tick(kit.seconds(item) * 0.05) or TICK, TICK)
    bits = []
    for turn, index in enumerate(order):
        shape, mx, my = pieces[index]
        x, y = (mx - 50) * dice.span(0.36, 0.66), (my - 50) * dice.span(1.3, 2.3) + dice.span(-34, 34)
        bits.append(
            f'<div class="bit" style="--x:{x:.1f}%;--y:{y:.1f}%;--r:{dice.span(-34, 34):.1f}deg;--at:{turn * gap:.3f}s;transform-origin:{mx:.1f}% {my:.1f}%">'
            f'<div class="cut" style="clip-path:{shape}"><div class="page" aria-hidden="true">{page}</div></div></div>'
        )
    label = f'<p class="tag">{kit.esc(item["label"])}</p>' if item.get("label") else ""
    return f'<div class="set" style="--grain:{_grain(dice)}"><div class="sheet"><div class="page ghost">{page}</div>{"".join(bits)}</div>{label}</div>'


def _collage(item: dict) -> str:
    dice = _Dice("collage", item["text"], item.get("label", ""))

    def layer(body: str, tick: int, x: float, y: float, r: float, style: str, cls: str = "") -> str:
        return f'<div class="lay {cls}" style="--at:{tick * TICK:.3f}s;--x:{x}%;--y:{y}%;--r:{r}deg;--k:{int(dice.span(0, 6))};{style}">{body}</div>'

    hills = [
        ("back", "0 100%,0 62%,16% 50%,33% 60%,52% 40%,70% 54%,100% 42%,100% 100%"),
        ("mid", "0 100%,0 76%,24% 64%,46% 74%,68% 60%,100% 72%,100% 100%"),
        ("low", "0 100%,0 90%,30% 80%,62% 90%,100% 82%,100% 100%"),
    ]
    scene = '<i class="sun"></i>' + "".join(f'<i class="hill {name}" style="clip-path:polygon({_jog(dice, points)})"></i>' for name, points in hills)

    def tape(spin: int, left: int) -> str:
        return f'<i class="tape" style="rotate:{spin}deg;left:{left}%;clip-path:{_tear(dice, left=0.05, right=0.05, across=9)}"></i>'

    strips = []
    for index, words in enumerate(_lines(item["text"], 2)):
        strips.append(
            layer(
                _rip(dice, f"<h1>{_set_words(words)}</h1>", top=0.06, bottom=0.08, left=0.05, right=0.05),
                8 + index * 2,
                -130 if index % 2 else 130,
                12,
                -9 if index % 2 else 8,
                f"rotate:{dice.span(-2.2, 2.2):.1f}deg",
                "strip",
            )
        )
    label = layer(f"<span>{kit.esc(item['label'])}</span>", 13, 30, 90, 14, "rotate:-3deg", "tag") if item.get("label") else ""
    return (
        f'<div class="set" style="--grain:{_grain(dice)}"><div class="stage">'
        + layer(_rip(dice, "", "kraft", 0.14, 0.14, 0.14, 0.14), 0, -90, 16, -16, "left:0;top:5%;width:64%;height:74%;rotate:-5deg", "sheet")
        + layer(_rip(dice, "<b></b><u></u><u></u><u></u>", "press", 0.1, 0.08, 0.12, 0.1), 2, 90, -24, 14, "left:45%;top:0;width:52%;height:60%;rotate:4deg", "sheet")
        + layer("", 4, 60, 70, 30, "left:66%;top:34%;width:27%;aspect-ratio:1;", "disc")
        + layer(f'<div class="print">{scene}</div>{tape(-6, 8)}{tape(5, 58)}', 6, -40, -90, 12, "left:9%;top:12%;width:50%;height:50%;rotate:2.4deg", "photo")
        + '<div class="strips">'
        + "".join(strips)
        + "</div>"
        + label
        + "</div></div>"
    )


def _jog(dice: _Dice, points: str) -> str:
    """Nudge a shape's cut points a little, so no two scenes share a horizon."""
    out = []
    for pair in points.split(","):
        x, y = (float(value.rstrip("%")) for value in pair.split())
        if 0 < y < 100:
            y += dice.span(-5, 5)
        out.append(f"{x:.1f}% {y:.1f}%")
    return ",".join(out)


def _stop_motion(item: dict) -> str:
    dice = _Dice("stop", item["text"])
    defs, names = _rough(dice, 5, 0.05)
    out, index = [], 0
    total = sum(len(word) for word, _ in kit.tokens(item["text"]))
    per_tick = max(1, math.ceil(total * TICK / max(kit.seconds(item) * 0.5, TICK)))
    for word, hot in kit.tokens(item["text"]):
        cells = []
        for char in word:
            at = (index // per_tick) * TICK
            cells.append(
                f'<span class="c" style="--at:{at:.3f}s;--x:{dice.span(-30, 30):.0f}%;--r:{dice.span(-22, 22):.0f}deg;--k:{int(dice.span(0, 6))}">{kit.esc(char)}</span>'
            )
            index += 1
        out.append(f'<span class="word{" hot" if hot else ""}">{"".join(cells)}</span>')
    label = f'<p style="--at:{_tick(index / per_tick * TICK) + TICK * 2:.3f}s">{kit.esc(item["label"])}</p>' if item.get("label") else ""
    return f'{defs}<div class="set" style="--grain:{_grain(dice, 0.42)}"><h1 style="{names}">{" ".join(out)}</h1>{label}</div>'


def _bars(dice: _Dice, count: int = 46) -> str:
    """A barcode pattern as a gradient. Decoration from the copy, with no meaning to scan."""
    runs = [(dice.pick((0.9, 0.9, 1.6, 2.4, 3.2)), dice.pick((0.9, 1.4, 2.2))) for _ in range(count)]
    fit = 100 / sum(wide + gap for wide, gap in runs)
    at, stops = 0.0, []
    for wide, gap in runs:
        stops.append(f"var(--pen) {at:.2f}% {at + wide * fit:.2f}%,transparent {at + wide * fit:.2f}% {at + (wide + gap) * fit:.2f}%")
        at += (wide + gap) * fit
    return "linear-gradient(90deg," + ",".join(stops) + ")"


def _receipt(item: dict) -> str:
    dice = _Dice("receipt", item.get("title", ""), item.get("total", ""))
    rows = _beats(item, "items", 0.52)
    first = 0.25
    head = f'<div class="ln head" style="--at:0s"><b>{kit.esc(item["title"])}</b></div><div class="ln meta" style="--at:.125s"><span>{kit.esc(item.get("label", "Receipt"))}</span><span>No {int(dice.span(1000, 9999))}</span></div>'
    body = []
    for row in rows:
        at = first + row["delay"]
        body.append(f'<div class="ln row" style="--at:{at:.3f}s"><span>{kit.esc(row["text"])}</span><span>{kit.esc(row.get("value", ""))}</span></div>')
    done = _tick(first + (rows[-1]["delay"] if rows else 0) + 0.375)
    total = f'<div class="ln total" style="--at:{done:.3f}s"><span>{kit.esc(item.get("total_label", "Total"))}</span><b>{kit.esc(item["total"])}</b></div>'
    foot = f'<div class="ln code" style="--at:{done + 0.25:.3f}s"><i style="background-image:{_bars(dice)}"></i></div>'
    cut = done + 0.625
    return (
        f'<div class="set" style="--grain:{_grain(dice, 0.16)};--cut:{cut:.3f}s"><div class="mouth"><div class="strip">'
        f'<i class="zig top"></i>{head}<i class="ln dash" style="--at:.25s"></i>{"".join(body)}<i class="ln dash" style="--at:{done - 0.125:.3f}s"></i>{total}{foot}<i class="zig close"></i>'
        '</div></div><div class="printer"><i class="led"></i><i class="lip"></i></div></div>'
    )


_ZIG = "polygon(" + ",".join(f"{i * 2.5:.1f}% {100 if i % 2 else 0}%" for i in range(41)) + ",100% 0,0 0)"
_ZIG_TOP = "polygon(0 100%," + ",".join(f"{i * 2.5:.1f}% {0 if i % 2 else 100}%" for i in range(41)) + ",100% 100%)"


def _ticket(item: dict) -> str:
    dice = _Dice("ticket", item["title"])
    label = f'<p class="over">{kit.esc(item["label"])}</p>' if item.get("label") else ""
    detail = f'<p class="under">{kit.esc(item["detail"])}</p>' if item.get("detail") else ""
    serial = f"No {int(dice.span(100000, 999999))}"
    stamp = f'<div class="mark" style="rotate:{dice.span(-13, -6):.1f}deg"><span>{kit.esc(item["stamp"])}</span></div>' if item.get("stamp") else ""
    defs, names = _rough(dice, 9, 0.09, 1)
    return (
        f'{defs}<div class="set" style="--grain:{_grain(dice)};{names}"><div class="pass">'
        f'<div class="main"><div class="edge"></div>{label}<h1>{kit.emphasis(item["title"])}</h1>{detail}</div>'
        f'<div class="stub"><div class="edge"></div><span class="serial">{serial}</span><i class="code" style="background-image:{_bars(dice)}"></i></div>'
        f"{stamp}</div></div>"
    )


def _scallop(width: int, height: int, tooth: int) -> str:
    """A stamp's perforated edge as an SVG path, by walking each side with half-circle bites."""
    cols, rows = round(width / (tooth * 2)), round(height / (tooth * 2))
    wide, tall = width / cols, height / rows
    r = wide * 0.27
    d = "M0 0"
    for i in range(cols):
        d += f"H{wide * i + wide / 2 - r:.1f}a{r:.1f} {r:.1f} 0 0 0 {2 * r:.1f} 0"
    d += f"H{width}"
    for i in range(rows):
        d += f"V{tall * i + tall / 2 - r:.1f}a{r:.1f} {r:.1f} 0 0 0 0 {2 * r:.1f}"
    d += f"V{height}"
    for i in range(cols):
        d += f"H{width - wide * i - wide / 2 + r:.1f}a{r:.1f} {r:.1f} 0 0 0 {-2 * r:.1f} 0"
    d += "H0"
    for i in range(rows):
        d += f"V{height - tall * i - tall / 2 + r:.1f}a{r:.1f} {r:.1f} 0 0 0 0 {-2 * r:.1f}"
    return d + "Z"


def _stamp(item: dict) -> str:
    dice = _Dice("stamp", item["text"], item.get("value", ""))
    defs, names = _rough(dice, 8, 0.11, 1)
    edge = _scallop(560, 680, 20)
    paper = f'<svg class="perf" viewBox="0 0 560 680" aria-hidden="true"><path d="{edge}"/></svg>'
    value = f'<b class="value">{kit.esc(item["value"])}</b>' if item.get("value") else ""
    over = f'<span class="over">{kit.esc(item["label"])}</span>' if item.get("label") else ""
    post = kit.esc(item.get("mark", ""))
    waves = "".join(f'<path d="M0 {18 + i * 28}c60 -22 120 22 180 0s120 -22 180 0s120 22 180 0"/>' for i in range(4))
    mark = (
        f'<div class="post" style="rotate:{dice.span(-16, -7):.1f}deg"><div class="ring"><span>{post}</span></div><svg class="bars" viewBox="0 0 540 120" aria-hidden="true">{waves}</svg></div>'
        if item.get("mark")
        else ""
    )
    return (
        f'{defs}<div class="set" style="--grain:{_grain(dice)};{names}"><div class="stamp">{paper}<div class="plate"><div class="burin"></div>'
        f'<div class="top">{over}{value}</div><h1>{kit.emphasis(item["text"])}</h1></div>{mark}</div></div>'
    )


def _scribble(dice: _Dice, kind: str) -> str:
    """Hand-drawn strokes in a 400 by 100 box. Two passes of a ring, as a pen really goes round twice."""
    j = dice.span
    if kind == "ring":
        return (
            f'<path pathLength="1" d="M{j(20, 50):.0f} {j(16, 30):.0f}C{j(90, 130):.0f} {j(-8, 4):.0f} {j(320, 370):.0f} {j(-6, 6):.0f} {j(386, 402):.0f} {j(34, 52):.0f}'
            f'S{j(280, 330):.0f} {j(100, 112):.0f} {j(150, 200):.0f} {j(100, 108):.0f}S{j(-10, 6):.0f} {j(84, 96):.0f} {j(0, 12):.0f} {j(46, 58):.0f}S{j(60, 110):.0f} {j(2, 10):.0f} {j(170, 230):.0f} {j(-2, 6):.0f}"/>'
        )
    if kind == "under":
        return f'<path pathLength="1" d="M{j(0, 10):.0f} {j(60, 70):.0f}C{j(90, 130):.0f} {j(46, 58):.0f} {j(260, 320):.0f} {j(52, 66):.0f} {j(392, 400):.0f} {j(50, 58):.0f}C{j(300, 340):.0f} {j(66, 74):.0f} {j(140, 180):.0f} {j(72, 80):.0f} {j(70, 100):.0f} {j(84, 92):.0f}"/>'
    return f'<path pathLength="1" d="M{j(4, 14):.0f} {j(8, 16):.0f}L{j(380, 396):.0f} {j(4, 14):.0f}L{j(386, 398):.0f} {j(84, 96):.0f}L{j(6, 16):.0f} {j(86, 96):.0f}L{j(2, 10):.0f} {j(2, 12):.0f}L{j(120, 160):.0f} {j(0, 8):.0f}"/>'


def _boil(item: dict) -> str:
    dice = _Dice("boil", item["text"], item.get("note", ""))
    defs, names = _rough(dice, 7, 0.028)
    kind = item.get("style", "ring")
    parts = str(item["text"]).split("*")
    words = []
    for index, part in enumerate(parts):
        if index % 2:
            words.append(
                f'<span class="hit">{kit.esc(part)}<span class="pen" aria-hidden="true"><svg viewBox="0 0 400 100" preserveAspectRatio="none">{_scribble(dice, kind)}</svg></span></span>'
            )
        else:
            words.append(kit.esc(part))
    note = ""
    if item.get("note"):
        arrow = f'<path pathLength="1" d="M8 {dice.span(70, 84):.0f}C{dice.span(40, 60):.0f} {dice.span(20, 34):.0f} {dice.span(90, 120):.0f} {dice.span(10, 22):.0f} 150 36"/><path pathLength="1" d="M122 16L152 38L118 54"/>'
        note = f'<div class="note"><span class="draw way" aria-hidden="true"><svg viewBox="0 0 160 92">{arrow}</svg></span><span>{kit.esc(item["note"])}</span></div>'
    return f'{defs}<div class="set"><div class="sketch" style="{names}"><h1>{"".join(words)}</h1>{note}</div></div>'


def _pop_size(text: str) -> float:
    """Comic words fill the burst, so the type shrinks with the length."""
    return min(208.0, 1120 / max(len(text), 1))


def _burst(dice: _Dice, spikes: int = 15, inner: float = 0.66) -> str:
    points = []
    for index in range(spikes * 2):
        turn = math.tau * index / (spikes * 2) + dice.span(-0.05, 0.05)
        reach = (dice.span(0.9, 1.0) if index % 2 == 0 else dice.span(inner - 0.05, inner + 0.03)) * 50
        points.append(f"{50 + reach * math.cos(turn):.1f}% {50 + reach * math.sin(turn):.1f}%")
    return "polygon(" + ",".join(points) + ")"


def _comic(item: dict) -> str:
    dice = _Dice("comic", item["text"])
    shape = _burst(dice)
    word = str(item["text"]).replace("*", "")
    label = f'<div class="strap"><span>{kit.esc(item["label"])}</span></div>' if item.get("label") else ""
    return (
        f'<div class="set"><div class="stage"><div class="hit"><div class="shade" style="clip-path:{shape}"></div><div class="fill" style="clip-path:{shape}"><i></i></div></div>'
        f'<h1 style="--size:{_pop_size(word):.0f}px">{kit.esc(word)}</h1></div>{label}</div>'
    )


def _figure(turn: float, walking: bool) -> str:
    """One pencil pose of a figure holding a board overhead. Joints come from a walk cycle, and the low foot always sits on the floor."""
    thigh, shin = 66.0, 62.0

    def leg(phase: float) -> tuple[tuple[float, float], tuple[float, float]]:
        at = (turn + phase) * math.tau
        upper = 0.52 * math.sin(at) if walking else (0.1 if phase else -0.1)
        lower = upper - (0.95 * max(math.cos(at), 0) if walking else 0.05)
        knee = (thigh * math.sin(upper), thigh * math.cos(upper))
        return knee, (knee[0] + shin * math.sin(lower), knee[1] + shin * math.cos(lower))

    legs = [leg(0.0), leg(0.5)]
    hip = -max(foot[1] for _, foot in legs)
    shoulder, head = hip - 96, hip - 142
    strokes = f"M0 {hip:.1f}L0 {shoulder:.1f}"
    for knee, foot in legs:
        strokes += f"M0 {hip:.1f}L{knee[0]:.1f} {hip + knee[1]:.1f}L{foot[0]:.1f} {hip + foot[1]:.1f}L{foot[0] + 17:.1f} {hip + foot[1]:.1f}"
    hand = -292
    strokes += f"M0 {shoulder:.1f}L-58 {shoulder - 26:.1f}L-46 {hand}M0 {shoulder:.1f}L58 {shoulder - 26:.1f}L46 {hand}"
    return (
        f'<svg viewBox="-110 -300 220 300" aria-hidden="true"><path d="{strokes}"/><circle class="head" cx="0" cy="{head:.1f}" r="27"/>'
        f'<circle class="eye" cx="8" cy="{head - 4:.1f}" r="3.6"/><circle class="eye" cx="20" cy="{head - 4:.1f}" r="3.6"/></svg>'
    )


def _crew(item: dict) -> str:
    dice = _Dice("crew", item["text"])
    defs, names = _rough(dice, 3, 0.04)
    pair = []
    for who, phase in (("rear", 0.0), ("lead", 0.34)):
        walk = "".join(f'<div class="fr" style="--n:{n}">{_figure(n / 6 + phase, True)}</div>' for n in range(6))
        pair.append(f'<div class="man {who}"><div class="pose go">{walk}</div><div class="pose rest">{_figure(0.0, False)}</div></div>')
    return (
        f'{defs}<div class="set" style="--grain:{_grain(dice)}"><div class="board">'
        + _rip(dice, "", "", 0.1, 0.07, 0.12, 0.07)
        + f'<div class="slit"><i class="floor"></i><div class="team"><div class="block"><span>{kit.emphasis(item["text"])}</span></div><div class="crew" style="{names}">{"".join(pair)}</div></div></div></div></div>'
    )


def _scrapbook(item: dict) -> str:
    dice = _Dice("scrapbook", *[row["text"] if isinstance(row, dict) else row for row in item["items"]])
    rows = _beats(item, "items", 0.55)
    kinds = ("sticky", "card", "tag", "leaf")
    offset = int(dice.span(0, 4))
    notes = []
    for index, row in enumerate(rows):
        kind = kinds[(index + offset) % 4]
        at = row["delay"]
        tape = f'<i class="tape" style="--at:{at + TICK * 2:.3f}s;rotate:{dice.span(-9, 9):.1f}deg;left:{dice.span(28, 46):.0f}%;clip-path:{_tear(dice, left=0.06, right=0.06, across=10)}"></i>'
        notes.append(
            f'<div class="hit" style="--at:{at:.3f}s;--k:{int(dice.span(0, 6))};rotate:{dice.span(-4.5, 4.5):.1f}deg;translate:{dice.span(-1.5, 1.5):.1f}% {dice.span(-3, 3):.1f}%">'
            f'<div class="note {kind}"><p>{kit.emphasis(row["text"])}</p></div>{tape}</div>'
        )
    title = f'<h2 style="--at:0s">{kit.esc(item["title"])}</h2>' if item.get("title") else ""
    return f'<div class="set" style="--grain:{_grain(dice)}">{title}<div class="wall">{"".join(notes)}</div></div>'


def _pen_trail(item: dict) -> str:
    dice = _Dice("trail", *[row["text"] if isinstance(row, dict) else row for row in item["items"]])
    rows = kit.rows(item, "items")
    total = kit.seconds(item)
    closing = min(0.9, total * 0.18)
    span = total - closing - min(0.75, total * 0.15)
    times = [row["delay"] if row.get("at") is not None else span * i / max(len(rows), 1) for i, row in enumerate(rows)]
    times.append(span)
    size, gap_y = 176, 318
    placed, y = [], 0.0
    for row in rows:
        width = max(len(row["text"]) * size * 0.4, 120)
        x = dice.span(-120, 120) if width < 760 else 0.0
        placed.append((x - width / 2, y + dice.span(-14, 14), width))
        y += gap_y
    top, bottom = -size, y - gap_y + size * 0.6
    high = bottom - top
    fit = min(0.8, 1120 / max(high, 1), 860 / max(max(x + w for x, _, w in placed) - min(x for x, _, _ in placed) + 420, 1))
    words, links, pens = [], [], []
    for index, (x, base, width) in enumerate(placed):
        begin, finish = times[index], times[index + 1]
        room = max(finish - begin, 0.3)
        write = min(room * 0.62, max(0.45, len(rows[index]["text"]) * 0.055))
        words.append(
            f'<div class="word" style="left:{x:.0f}px;top:{base - size:.0f}px;--at:{begin:.3f}s;--for:{write:.3f}s;--n:{max(int(write / TICK), 2) * 2}"><span>{kit.esc(rows[index]["text"])}</span></div>'
        )
        tip = base + 22
        run = f"M{x:.0f} {tip:.0f}Q{x + width / 2:.0f} {tip + dice.span(-16, 16):.0f} {x + width:.0f} {tip:.0f}"
        pens.append(f'<div class="pen" style="offset-path:path(\'{run}\');--at:{begin:.3f}s;--for:{write:.3f}s">{_PEN}</div>')
        if index + 1 >= len(placed):
            continue
        nx, nbase, _ = placed[index + 1]
        ex, ey = x + width + 14, base - 14
        sx, sy = nx - 12, nbase - 36
        loop = dice.span(70, 110)
        lane = base + gap_y * 0.36
        back = sx - 92
        link = (
            f"M{ex:.0f} {ey:.0f}C{ex + loop:.0f} {ey - loop:.0f} {ex + loop * 1.7:.0f} {lane - loop * 0.5:.0f} {ex + loop * 0.2:.0f} {lane:.0f}"
            f"C{ex - 200:.0f} {lane + dice.span(-16, 16):.0f} {back + 240:.0f} {lane + dice.span(-16, 16):.0f} {back:.0f} {lane + 26:.0f}"
            f"C{back - 62:.0f} {lane + 44:.0f} {back - 40:.0f} {sy + 14:.0f} {sx:.0f} {sy:.0f}"
        )
        link_at, link_for = begin + write, max(room - write - 0.06, 0.12)
        links.append(
            f'<div class="link" style="--at:{link_at:.3f}s;--for:{link_for:.3f}s;--n:{max(int(link_for / TICK) * 2, 2)}"><svg viewBox="-2000 -2000 4000 5000" aria-hidden="true"><path pathLength="1" d="{link}"/></svg></div>'
        )
        pens.append(f'<div class="pen" style="offset-path:path(\'{link}\');--at:{link_at:.3f}s;--for:{link_for:.3f}s">{_PEN}</div>')
    centres = [(x + w / 2, base - size * 0.3) for x, base, w in placed]
    middle = ((min(x for x, _, _ in placed) + max(x + w for x, _, w in placed)) / 2, (top + bottom) / 2)
    stops = [*centres, middle]
    world = '<div class="sheet"></div>' + "".join(links) + "".join(words) + "".join(pens)
    inner = f'<div class="world" style="transform:translate({-stops[-1][0]:.0f}px,{-stops[-1][1]:.0f}px)">{world}</div>'
    for index in range(len(stops) - 1, 0, -1):
        dx, dy = stops[index][0] - stops[index - 1][0], stops[index][1] - stops[index - 1][1]
        begin = times[index] - 0.2 if index < len(stops) - 1 else span
        inner = (
            f'<div class="cam" style="--dx:{dx:.0f}px;--dy:{dy:.0f}px;--at:{max(begin, 0):.3f}s;--for:{0.75 if index < len(stops) - 1 else closing:.3f}s">{inner}</div>'
        )
    return f'<div class="room" style="--grain:{_grain(dice, 0.24)}"><div class="lens"><div class="pull" style="--fit:{fit:.3f};--at:{span:.3f}s;--for:{closing:.3f}s">{inner}</div></div><div class="vignette"></div></div>'


_PEN = (
    '<svg viewBox="0 0 90 240" aria-hidden="true"><path class="wood" d="M45 236L28 186H62Z"/><path class="tip" d="M45 236L39 218H51Z"/>'
    '<path class="body" d="M28 186V34a6 6 0 0 1 6-6h22a6 6 0 0 1 6 6V186Z"/><path class="band" d="M28 34V14h34V34Z"/><path class="rub" d="M28 14V8a8 8 0 0 1 8-8h18a8 8 0 0 1 8 8V14Z"/></svg>'
)


def _fold_out(item: dict) -> str:
    dice = _Dice("fold", item["text"])
    total = kit.seconds(item)
    gap = _tick(min(0.75, total * 0.21))
    label = f"<p>{kit.esc(item['label'])}</p>" if item.get("label") else ""
    page = f"<h1>{kit.emphasis(item['text'])}</h1>{label}"

    def leaf(number: int) -> str:
        return f'<div class="slice" style="--n:{number}" aria-hidden="{"false" if number == 0 else "true"}"><div class="page">{page}</div></div>'

    nest = ""
    for number in range(3, 0, -1):
        at = 0.375 + (number - 1) * gap
        nest = f'<div class="hinge" style="--at:{at:.3f}s"><div class="front">{leaf(number)}<i class="crease"></i><i class="dim"></i></div><div class="back"><i class="dim"></i></div>{nest}</div>'
    return f'<div class="set" style="--grain:{_grain(dice)}"><div class="pack"><div class="flat">{leaf(0)}</div>{nest}</div></div>'


_SNAP = _keys(
    "p-snap",
    "opacity:0;transform:scale(1.36) rotate(4deg)",
    "opacity:1;transform:scale(1.14) rotate(-1deg)",
    "opacity:1;transform:scale(.96)",
    "opacity:1;transform:none",
    "opacity:1;transform:none",
)
_HOP = _keys(
    "p-hop",
    "opacity:0;transform:translate(var(--x),150%) rotate(var(--r)) scale(.92,1.1)",
    "opacity:1;transform:translate(calc(var(--x) * .4),-26%) rotate(calc(var(--r) * .4)) scale(.95,1.08)",
    "opacity:1;transform:translateY(5%) rotate(calc(var(--r) * -.12)) scale(1.08,.9)",
    "opacity:1;transform:translateY(-3%) scale(.99,1.02)",
    "opacity:1;transform:none",
)
_PLACE = _fly("p-place", 1, 0.56, 0.24, 0.06, -0.03, 0, extra={0: "opacity:0"})
_BIT = _fly("p-bit", 1, 0.74, 0.5, 0.3, 0.16, 0.07, 0.02, 0, extra={0: "opacity:0"})


FORMATS = (
    Format(
        id="ransom_note",
        title="Ransom letters",
        summary="Every letter is its own clipping, slapped down one at a time.",
        group="paper",
        jobs=("hook",),
        slots=(Slot("text", limit=26),),
        example={"text": "Nobody *reads* the manual"},
        draw=_ransom,
        seconds=3.0,
        longest=8.0,
        tall=480,
        sound="tick",
        fonts=("mono", "serif", "hand"),
        leaves=False,
        css=_stock("ransom_note")
        + """
.f-ransom_note .note{display:flex;flex-wrap:wrap;justify-content:center;gap:calc(16px * var(--scale)) calc(40px * var(--scale));margin:0 auto;max-width:calc(1120px * var(--scale));font-size:calc(106px * var(--scale))}
.f-ransom_note .word{display:inline-flex;flex-wrap:wrap;justify-content:center;gap:calc(3px * var(--scale))}
.f-ransom_note .slot{display:inline-block;filter:var(--thick)}
.f-ransom_note .tile{display:grid;place-items:center;min-width:calc(86px * var(--scale));height:calc(136px * var(--scale));padding:0 .13em;font-weight:800;line-height:1;text-transform:uppercase;color:var(--pen);background-color:var(--cream);background-image:var(--grain);
animation:p-snap .5s step-end var(--at) both,p-jit .75s step-end calc(var(--k) * -.125s) infinite}
.f-ransom_note .sans{font-weight:900;letter-spacing:-.02em}
.f-ransom_note .serif{font-family:var(--serif);font-weight:400;font-size:1.22em;text-transform:none}
.f-ransom_note .italic{font-family:var(--serif);font-style:italic;font-weight:400;font-size:1.22em;text-transform:lowercase}
.f-ransom_note .mono{font-family:var(--mono);font-weight:600}
.f-ransom_note .hand{font-family:var(--hand);font-weight:700;font-size:1.34em;text-transform:lowercase}
.f-ransom_note .news{background-color:var(--news);background-image:var(--grain),repeating-linear-gradient(0deg,rgba(28,24,19,.13) 0 1px,transparent 1px 6px)}
.f-ransom_note .ink{background-color:var(--pen);color:var(--cream)}
.f-ransom_note .white{background-color:var(--white);color:var(--brick)}
.f-ransom_note .kraft{background-color:var(--kraft)}
.f-ransom_note .brick{background-color:var(--brick);color:var(--white)}
.f-ransom_note .gold{background-color:var(--gold)}
.f-ransom_note .lit{background-color:var(--accent);color:var(--panel)}
"""
        + _SNAP,
    ),
    Format(
        id="torn_wipe",
        title="Torn paper wipe",
        summary="A ragged sheet rips across the frame, covers the cut, and rips away.",
        group="paper",
        jobs=("transition",),
        slots=(Slot("text", limit=40), Slot("label", limit=36, required=False)),
        example={"text": "Part *two*", "label": "What happened next"},
        draw=_torn_wipe,
        seconds=2.2,
        shortest=1.5,
        longest=6.0,
        area="full",
        tall=1920,
        sound="whoosh",
        fonts=("serif", "mono"),
        leaves=False,
        css=_stock("torn_wipe")
        + """
.f-torn_wipe .wipe{position:absolute;inset:0;overflow:hidden}
.f-torn_wipe .sheet{position:absolute;top:-1%;bottom:-1%;left:-30%;width:160%;filter:var(--thick)}
.f-torn_wipe .sheet>i{position:absolute;inset:0;display:block}
.f-torn_wipe .under{animation:torn-in .625s step-end both,torn-out .625s step-end calc(var(--dur) - .625s) forwards}
.f-torn_wipe .over{animation:torn-in .625s step-end .125s both,torn-out .625s step-end calc(var(--dur) - .75s) forwards}
.f-torn_wipe .under .face{background-color:var(--accent)}
.f-torn_wipe .ink{position:absolute;left:18.75%;width:62.5%;top:0;bottom:0;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:0 var(--gutter)}
.f-torn_wipe h2{margin:0;font-family:var(--serif);font-weight:400;font-size:calc(184px * var(--scale));line-height:1;letter-spacing:-.02em;text-wrap:balance}
.f-torn_wipe p{margin:.9em 0 0;font-family:var(--mono);font-size:calc(34px * var(--scale));font-weight:500;letter-spacing:.02em;color:rgba(28,24,19,.74)}
"""
        + _keys("torn-in", "translate:-104% 0", "translate:-62% 0", "translate:-30% 0", "translate:-12% 0", "translate:-3% 0", "translate:0 0")
        + _keys(
            "torn-out",
            "transform:none",
            "transform:translateX(4%)",
            "transform:translateX(16%)",
            "transform:translateX(38%)",
            "transform:translateX(72%)",
            "transform:translateX(106%)",
        ),
    ),
    Format(
        id="torn_scraps",
        title="Torn pieces assemble",
        summary="Scraps of a torn page fly in and fit together into the headline.",
        group="paper",
        jobs=("hook", "close"),
        slots=(Slot("text", limit=44), Slot("label", limit=36, required=False)),
        example={"text": "Put the *pieces* back together", "label": "Issue four, out Friday"},
        draw=_torn_scraps,
        seconds=3.2,
        shortest=2.4,
        longest=8.0,
        tall=560,
        sound="fall",
        fonts=("serif", "mono"),
        leaves=False,
        css=_stock("torn_scraps")
        + """
.f-torn_scraps .set{margin:0 auto;width:min(100%,calc(980px * var(--scale)))}
.f-torn_scraps .sheet{position:relative}
.f-torn_scraps .page{display:grid;place-items:center;padding:calc(58px * var(--scale)) calc(46px * var(--scale));text-align:center;color:var(--pen);background-color:var(--cream);background-image:var(--grain),repeating-linear-gradient(0deg,rgba(28,24,19,.07) 0 1px,transparent 1px 9px)}
.f-torn_scraps .ghost{visibility:hidden}
.f-torn_scraps h1{margin:0;font-family:var(--serif);font-weight:400;font-size:calc(116px * var(--scale));line-height:1.02;letter-spacing:-.022em;text-wrap:balance}
.f-torn_scraps h1 em{color:var(--brick)}
.f-torn_scraps .bit{position:absolute;inset:0;filter:drop-shadow(calc(1.6px * var(--scale)) calc(1.4px * var(--scale)) 0 var(--white)) drop-shadow(calc(-1.2px * var(--scale)) calc(-1.4px * var(--scale)) 0 var(--white)) var(--thick);animation:p-bit .875s step-end var(--at) both}
.f-torn_scraps .cut{position:absolute;inset:0}
.f-torn_scraps .cut .page{position:absolute;inset:0}
.f-torn_scraps .tag{display:table;margin:calc(26px * var(--scale)) auto 0;padding:.34em .7em;font-family:var(--mono);font-size:calc(34px * var(--scale));font-weight:500;color:var(--panel);background:var(--accent);rotate:-1.2deg;animation:p-snap .5s step-end 1.625s both}
"""
        + _BIT
        + _SNAP,
    ),
    Format(
        id="collage_stack",
        title="Collage stack",
        summary="Sheets, prints and tape are shuffled into a layout under the headline.",
        group="paper",
        jobs=("hook", "explain"),
        slots=(Slot("text", limit=40), Slot("label", limit=30, required=False)),
        example={"text": "Made by *hand*, one frame at a time", "label": "Studio notes, volume two"},
        draw=_collage,
        seconds=4.2,
        shortest=2.4,
        longest=8.0,
        tall=730,
        sound="fall",
        fonts=("serif", "mono"),
        leaves=False,
        css=_stock("collage_stack")
        + """
.f-collage_stack .set{margin:0 auto;width:min(100%,calc(900px * var(--scale)))}
.f-collage_stack .stage{position:relative;height:calc(690px * var(--scale))}
.f-collage_stack .lay{position:absolute;animation:p-place .625s step-end var(--at) both,p-jit .75s step-end calc(var(--k) * -.125s) infinite}
.f-collage_stack .sheet .rip{position:absolute;inset:0}
.f-collage_stack .kraft .face{background-color:var(--kraft)}
.f-collage_stack .press .face{background-color:var(--news)}
.f-collage_stack .press .ink{position:absolute;inset:7%;display:grid;grid-template-columns:repeat(3,1fr);grid-template-rows:auto 1fr;gap:calc(14px * var(--scale))}
.f-collage_stack .press b{grid-column:1/-1;height:calc(44px * var(--scale));background:var(--pen)}
.f-collage_stack .press u{display:block;background:repeating-linear-gradient(rgba(28,24,19,.62) 0 calc(4px * var(--scale)),transparent calc(4px * var(--scale)) calc(11px * var(--scale)))}
.f-collage_stack .disc{border-radius:50%;background-color:var(--accent);background-image:var(--grain);filter:var(--thick)}
.f-collage_stack .photo{filter:var(--thick)}
.f-collage_stack .print,.f-collage_stack .photo .print{position:absolute;inset:0;overflow:hidden;background:#f6d9a8;border:calc(14px * var(--scale)) solid var(--white)}
.f-collage_stack .sun{position:absolute;left:58%;top:12%;width:26%;aspect-ratio:1;border-radius:50%;background:var(--brick)}
.f-collage_stack .hill{position:absolute;inset:0}
.f-collage_stack .hill.back{background:#3f7c70}
.f-collage_stack .hill.mid{background:var(--accent)}
.f-collage_stack .hill.low{background:var(--pen)}
.f-collage_stack .tape{position:absolute;top:-5%;width:34%;height:11%;font-size:calc(100px * var(--scale));background:rgba(241,226,180,.72)}
.f-collage_stack .strips{position:absolute;left:0;right:0;top:56%;display:flex;flex-direction:column;align-items:center;gap:calc(4px * var(--scale))}
.f-collage_stack .strip{position:relative}
.f-collage_stack .strip .ink{padding:calc(18px * var(--scale)) calc(38px * var(--scale)) calc(24px * var(--scale))}
.f-collage_stack h1{margin:0;font-family:var(--serif);font-weight:400;font-size:calc(82px * var(--scale));line-height:1;letter-spacing:-.018em;white-space:nowrap}
.f-collage_stack .tag{left:6%;top:calc(100% - 46px * var(--scale));display:inline-block;padding:.3em .7em;font-family:var(--mono);font-size:calc(30px * var(--scale));font-weight:500;color:var(--panel);background:var(--accent);filter:var(--thick)}
"""
        + _PLACE,
    ),
    Format(
        id="stop_motion",
        title="Stop-motion letters",
        summary="Cut-paper letters hop into place on an eight-frame cadence and never quite hold still.",
        group="paper",
        jobs=("hook",),
        slots=(Slot("text", limit=26), Slot("label", limit=40, required=False)),
        example={"text": "Moved by *hand*", "label": "Twelve thousand frames, one table"},
        draw=_stop_motion,
        seconds=3.0,
        longest=8.0,
        tall=570,
        sound="tick",
        fonts=("mono",),
        leaves=False,
        css=_stock("stop_motion")
        + """
.f-stop_motion .set{text-align:center}
.f-stop_motion h1{display:flex;flex-wrap:wrap;justify-content:center;gap:0 .3em;margin:0;font-size:calc(148px * var(--scale));font-weight:800;line-height:1.02;letter-spacing:-.02em;animation:stop-boil .375s step-end infinite}
.f-stop_motion .word{display:inline-flex}
.f-stop_motion .c{display:inline-block;color:transparent;background-color:var(--cream);background-image:var(--grain);-webkit-background-clip:text;background-clip:text;padding:0 .015em;animation:p-hop .625s step-end var(--at) both,p-jit .75s step-end calc(var(--k) * -.125s) infinite}
.f-stop_motion .hot .c{background-color:var(--accent)}
.f-stop_motion p{display:inline-block;margin:calc(26px * var(--scale)) 0 0;padding:.3em .62em;font-family:var(--mono);font-size:calc(32px * var(--scale));font-weight:500;color:var(--pen);background-color:var(--cream);background-image:var(--grain);rotate:-.8deg;filter:var(--thick);animation:p-snap .5s step-end var(--at) both}
"""
        + _keys("stop-boil", "filter:var(--f0) var(--thick)", "filter:var(--f1) var(--thick)", "filter:var(--f2) var(--thick)", "filter:var(--f0) var(--thick)")
        + _HOP
        + _SNAP,
    ),
    Format(
        id="receipt_print",
        title="Receipt print",
        summary="A strip of thermal paper feeds out of a printer, line by line as they are said.",
        group="paper",
        jobs=("prove", "explain"),
        slots=(
            Slot("title", limit=18),
            Slot("label", limit=12, required=False),
            Slot("items", kind="list", limit=5, each=17),
            Slot("total_label", limit=8, required=False),
            Slot("total", limit=8),
        ),
        example={
            "title": "Edit week 12",
            "label": "Saved",
            "items": [
                {"text": "Rough cut", "value": "3.5 h"},
                {"text": "Captions", "value": "1.0 h"},
                {"text": "Colour pass", "value": "2.0 h"},
                {"text": "Sound clean", "value": "1.5 h"},
            ],
            "total": "8.0 h",
        },
        draw=_receipt,
        seconds=4.0,
        shortest=2.4,
        longest=10.0,
        tall=720,
        sound="tick",
        fonts=("mono",),
        leaves=False,
        css=_stock("receipt_print")
        + """
.f-receipt_print .set{margin:0 auto;width:min(100%,calc(680px * var(--scale)))}
.f-receipt_print .mouth{position:relative;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;height:calc(624px * var(--scale));overflow:hidden}
.f-receipt_print .strip{position:relative;display:flex;flex-direction:column;width:calc(590px * var(--scale));color:var(--pen);font-family:var(--mono);background-color:var(--white);background-image:linear-gradient(90deg,rgba(40,30,20,.13),transparent 7%,transparent 93%,rgba(40,30,20,.13)),var(--grain);animation:tear .25s step-end var(--cut) forwards}
.f-receipt_print .zig{display:block;width:100%;height:calc(16px * var(--scale));background:var(--white)}
.f-receipt_print .zig.top{position:absolute;left:0;bottom:100%;clip-path:"""
        + _ZIG_TOP
        + """}
.f-receipt_print .zig.close{position:absolute;left:0;top:100%;clip-path:"""
        + _ZIG
        + """}
.f-receipt_print .ln{display:flex;justify-content:space-between;align-items:flex-start;flex:none;width:100%;height:0;overflow:hidden;padding:0 calc(36px * var(--scale));white-space:nowrap;animation:feed .25s steps(2,end) var(--at) forwards}
.f-receipt_print .head{--h:calc(84px * var(--scale));justify-content:center;font-size:calc(44px * var(--scale));font-weight:700;letter-spacing:-.01em}
.f-receipt_print .head b{padding-top:calc(24px * var(--scale))}
.f-receipt_print .meta{--h:calc(46px * var(--scale));font-size:calc(28px * var(--scale));color:rgba(28,24,19,.66)}
.f-receipt_print .dash{--h:calc(22px * var(--scale));display:block;background:radial-gradient(circle,var(--pen) 0 38%,transparent 42%) 0 50%/calc(13px * var(--scale)) calc(13px * var(--scale)) repeat-x;background-origin:content-box;background-clip:content-box;opacity:.5}
.f-receipt_print .row{--h:calc(54px * var(--scale));gap:.6em;font-size:calc(35px * var(--scale));font-weight:500;line-height:calc(54px * var(--scale))}
.f-receipt_print .row span:first-child{min-width:0;overflow:hidden}
.f-receipt_print .row span:last-child{flex:none}
.f-receipt_print .total{--h:calc(80px * var(--scale));font-size:calc(46px * var(--scale));font-weight:700;line-height:calc(80px * var(--scale))}
.f-receipt_print .total b{color:inherit;background:var(--accent);padding:0 .28em;line-height:1.2;margin-top:.26em;border-radius:.08em}
.f-receipt_print .code{--h:calc(80px * var(--scale))}
.f-receipt_print .code i{display:block;width:100%;height:calc(58px * var(--scale));margin-top:calc(6px * var(--scale))}
.f-receipt_print .printer{position:relative;height:calc(64px * var(--scale));border-radius:calc(10px * var(--scale)) calc(10px * var(--scale)) calc(18px * var(--scale)) calc(18px * var(--scale));background:linear-gradient(#2a3036,#14181c 60%,#0d1013);box-shadow:0 calc(14px * var(--scale)) calc(30px * var(--scale)) rgba(0,0,0,.42),inset 0 1px 0 rgba(255,255,255,.14)}
.f-receipt_print .lip{position:absolute;left:calc(32px * var(--scale));right:calc(32px * var(--scale));top:0;height:calc(7px * var(--scale));background:#05070a;border-radius:0 0 4px 4px}
.f-receipt_print .led{position:absolute;right:calc(26px * var(--scale));bottom:calc(16px * var(--scale));width:calc(12px * var(--scale));height:calc(12px * var(--scale));border-radius:50%;background:var(--accent);animation:led .25s step-end 8;animation-fill-mode:forwards}
@keyframes feed{to{height:var(--h)}}
@keyframes led{0%{opacity:.25}50%{opacity:1}}
"""
        + _keys("tear", "transform:none", "transform:translateY(calc(-9px * var(--scale)))", "transform:translateY(calc(-16px * var(--scale))) rotate(.5deg)"),
    ),
    Format(
        id="ticket_stub",
        title="Ticket stub",
        summary="A perforated ticket slides in, takes a rubber stamp, and its stub tears away.",
        group="paper",
        jobs=("hook", "close"),
        slots=(Slot("title", limit=26), Slot("label", limit=20, required=False), Slot("detail", limit=26, required=False), Slot("stamp", limit=8, required=False)),
        example={"title": "Opening *night*", "label": "Admit one", "detail": "Studio four, row F", "stamp": "Valid"},
        draw=_ticket,
        seconds=3.4,
        shortest=2.0,
        longest=8.0,
        tall=480,
        sound="thud",
        fonts=("mono", "serif"),
        leaves=False,
        css=_stock("ticket_stub")
        + """
.f-ticket_stub .set{margin:0 auto;width:min(100%,calc(980px * var(--scale)))}
.f-ticket_stub .pass{position:relative;display:flex;height:calc(420px * var(--scale));rotate:-1.6deg;filter:var(--thick);animation:p-place .625s step-end both;--x:-110%;--y:6%;--r:-7deg}
.f-ticket_stub .main,.f-ticket_stub .stub{position:relative;background-color:var(--gold);background-image:var(--grain),repeating-radial-gradient(circle at 12% 120%,rgba(28,24,19,.09) 0 1px,transparent 1px 9px);
-webkit-mask:var(--bite) 0 0/100% 51% no-repeat,var(--bite2) 0 100%/100% 51% no-repeat;mask:var(--bite) 0 0/100% 51% no-repeat,var(--bite2) 0 100%/100% 51% no-repeat}
.f-ticket_stub .main{--r2:calc(24px * var(--scale));--bite:radial-gradient(circle var(--r2) at 100% 0,transparent 98%,#000);--bite2:radial-gradient(circle var(--r2) at 100% 100%,transparent 98%,#000);flex:1;min-width:0;display:flex;flex-direction:column;justify-content:center;padding:calc(30px * var(--scale)) calc(46px * var(--scale))}
.f-ticket_stub .stub{--r2:calc(24px * var(--scale));--bite:radial-gradient(circle var(--r2) at 0 0,transparent 98%,#000);--bite2:radial-gradient(circle var(--r2) at 0 100%,transparent 98%,#000);flex:none;width:calc(172px * var(--scale));display:flex;flex-direction:column;align-items:center;justify-content:space-between;padding:calc(34px * var(--scale)) 0;transform-origin:0 100%;animation:stub .5s step-end calc(var(--dur) - .875s) forwards}
.f-ticket_stub .edge{position:absolute;inset:calc(14px * var(--scale));border:calc(2px * var(--scale)) solid rgba(28,24,19,.5);border-radius:calc(6px * var(--scale))}
.f-ticket_stub .stub .edge{left:calc(18px * var(--scale));border-left:0;border-right-width:calc(2px * var(--scale))}
.f-ticket_stub .main .edge{right:calc(18px * var(--scale))}
.f-ticket_stub .stub{border-left:calc(5px * var(--scale)) dotted rgba(28,24,19,.72);background-clip:padding-box}
.f-ticket_stub .over,.f-ticket_stub .under{position:relative;margin:0;font-family:var(--mono);font-size:calc(32px * var(--scale));font-weight:600;letter-spacing:.01em;color:var(--pen)}
.f-ticket_stub .over{text-transform:uppercase;letter-spacing:.14em;font-size:calc(28px * var(--scale))}
.f-ticket_stub h1{position:relative;margin:.14em 0 .18em;font-family:var(--serif);font-weight:400;font-size:calc(112px * var(--scale));line-height:.98;letter-spacing:-.02em;color:var(--pen);text-wrap:balance}
.f-ticket_stub h1 em{color:var(--brick)}
.f-ticket_stub .serial{position:relative;writing-mode:vertical-rl;font-family:var(--mono);font-size:calc(30px * var(--scale));font-weight:600;color:var(--pen);letter-spacing:.06em}
.f-ticket_stub .code{position:relative;width:calc(100px * var(--scale));height:calc(92px * var(--scale))}
.f-ticket_stub .mark{position:absolute;right:calc(196px * var(--scale));top:calc(24px * var(--scale));padding:.08em .34em;font-family:var(--mono);font-size:calc(54px * var(--scale));font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:var(--brick);border:.08em solid var(--brick);border-radius:.12em;filter:var(--f0);mix-blend-mode:multiply;animation:thump .5s step-end calc(var(--dur) * .36) both}
"""
        + _keys(
            "thump",
            "opacity:0;transform:scale(1.9)",
            "opacity:1;transform:scale(1.12)",
            "opacity:1;transform:scale(.98)",
            "opacity:1;transform:none",
            "opacity:1;transform:none",
        )
        + _keys(
            "stub",
            "transform:none",
            "transform:translate(calc(4px * var(--scale)),calc(2px * var(--scale))) rotate(1.4deg)",
            "transform:translate(calc(12px * var(--scale)),calc(8px * var(--scale))) rotate(4deg)",
            "transform:translate(calc(18px * var(--scale)),calc(20px * var(--scale))) rotate(6.4deg)",
            "transform:translate(calc(20px * var(--scale)),calc(24px * var(--scale))) rotate(6deg)",
        )
        + _PLACE,
    ),
    Format(
        id="postage_stamp",
        title="Postage stamp",
        summary="A perforated stamp lands, and a postmark thuds across it.",
        group="paper",
        jobs=("prove", "close"),
        slots=(Slot("text", limit=20), Slot("value", limit=5, required=False), Slot("label", limit=16, required=False), Slot("mark", limit=12, required=False)),
        example={"text": "Sent on *time*", "value": "42", "label": "First class", "mark": "Delivered"},
        draw=_stamp,
        seconds=3.2,
        shortest=2.0,
        longest=8.0,
        tall=715,
        sound="thud",
        fonts=("mono", "serif"),
        leaves=False,
        css=_stock("postage_stamp")
        + """
.f-postage_stamp .set{margin:0 auto;width:calc(560px * var(--scale))}
.f-postage_stamp .stamp{position:relative;width:calc(560px * var(--scale));height:calc(680px * var(--scale));rotate:2deg;filter:var(--thick);animation:p-snap .5s step-end both}
.f-postage_stamp .perf{position:absolute;inset:0;width:100%;height:100%;fill:var(--white)}
.f-postage_stamp .plate{position:absolute;inset:calc(34px * var(--scale));display:flex;flex-direction:column;justify-content:space-between;overflow:hidden;padding:calc(30px * var(--scale));color:var(--pen);background-color:var(--accent);background-image:var(--grain)}
.f-postage_stamp .burin{position:absolute;inset:0;background:repeating-radial-gradient(ellipse at 74% 112%,rgba(8,13,19,.34) 0 calc(2px * var(--scale)),transparent calc(2px * var(--scale)) calc(9px * var(--scale)))}
.f-postage_stamp .top{position:relative;display:flex;justify-content:space-between;align-items:flex-start;gap:.4em}
.f-postage_stamp .over{font-family:var(--mono);font-size:calc(28px * var(--scale));font-weight:600;line-height:1.1;text-transform:uppercase;letter-spacing:.06em;color:var(--panel)}
.f-postage_stamp .value{font-family:var(--serif);font-weight:400;font-size:calc(120px * var(--scale));line-height:.8;letter-spacing:-.03em;color:var(--panel)}
.f-postage_stamp h1{position:relative;margin:0;font-family:var(--serif);font-weight:400;font-size:calc(96px * var(--scale));line-height:.98;letter-spacing:-.02em;color:var(--panel);overflow-wrap:anywhere}
.f-postage_stamp h1 em{color:inherit;text-decoration:underline;text-decoration-thickness:.05em;text-underline-offset:.1em}
.f-postage_stamp .post{position:absolute;left:calc(-34px * var(--scale));top:calc(116px * var(--scale));width:calc(640px * var(--scale));height:calc(300px * var(--scale));color:#141a24;filter:var(--f0);animation:thump .5s step-end calc(var(--dur) * .42) both}
.f-postage_stamp .ring{position:absolute;left:calc(30px * var(--scale));top:0;width:calc(292px * var(--scale));height:calc(292px * var(--scale));display:grid;place-items:center;border-radius:50%;border:calc(10px * var(--scale)) solid currentColor;background:rgba(255,255,255,.1)}
.f-postage_stamp .ring span{font-family:var(--mono);font-size:calc(40px * var(--scale));font-weight:700;letter-spacing:.01em;text-transform:uppercase;text-align:center;line-height:1.06;padding:0 .3em}
.f-postage_stamp .bars{position:absolute;left:calc(320px * var(--scale));top:calc(78px * var(--scale));width:calc(320px * var(--scale));height:calc(130px * var(--scale));overflow:visible;fill:none;stroke:currentColor;stroke-width:13}
"""
        + _keys(
            "thump",
            "opacity:0;transform:scale(1.7)",
            "opacity:1;transform:scale(1.08)",
            "opacity:1;transform:scale(.98)",
            "opacity:1;transform:none",
            "opacity:1;transform:none",
        )
        + _SNAP,
    ),
    Format(
        id="line_boil",
        title="Line boil",
        summary="A hand-drawn line rings the key word, then shivers on purpose, redrawn every other frame.",
        group="paper",
        jobs=("hook", "prove", "explain"),
        slots=(Slot("text", limit=40), Slot("note", limit=34, required=False), Slot("style", kind="choice", required=False, choices=("ring", "under", "box"))),
        example={"text": "This is the *one number* that counts", "note": "write it on the wall"},
        draw=_boil,
        seconds=4.2,
        longest=10.0,
        tall=590,
        sound=None,
        fonts=("hand",),
        leaves=False,
        css=_stock("line_boil")
        + """
.f-line_boil .sketch{max-width:calc(1240px * var(--scale));margin:0 auto;animation:boil-a .375s step-end infinite;color:var(--ink)}
.f-line_boil h1{margin:0;font-family:var(--hand);font-size:calc(150px * var(--scale));font-weight:700;line-height:.98;letter-spacing:-.01em;text-wrap:balance;text-shadow:0 calc(3px * var(--scale)) calc(10px * var(--scale)) rgba(0,0,0,.5)}
.f-line_boil .hit{position:relative;display:inline-block;color:var(--accent);white-space:nowrap}
.f-line_boil .pen{position:absolute;left:-.22em;right:-.22em;top:-.1em;bottom:-.14em;stroke-dashoffset:1;animation:boil-draw .625s steps(5,end) .25s both}
.f-line_boil .pen svg,.f-line_boil .way svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible;fill:none;stroke:var(--accent);stroke-width:calc(8px * var(--scale));stroke-linecap:round;stroke-linejoin:round}
.f-line_boil path{stroke-dasharray:1 1;vector-effect:non-scaling-stroke}
.f-line_boil .note{display:flex;align-items:center;gap:.3em;margin-top:calc(18px * var(--scale));font-family:var(--hand);font-size:calc(70px * var(--scale));font-weight:600;line-height:1;text-shadow:0 calc(2px * var(--scale)) calc(8px * var(--scale)) rgba(0,0,0,.5)}
.f-line_boil .way{position:relative;flex:none;width:calc(130px * var(--scale));height:calc(76px * var(--scale));rotate:-160deg;stroke-dashoffset:1;animation:boil-draw .375s steps(3,end) 1s both}
.f-line_boil .note span:last-child{animation:boil-show .125s step-end 1.25s both}
@keyframes boil-draw{to{stroke-dashoffset:0}}
@keyframes boil-show{from{opacity:0}to{opacity:1}}
"""
        + _keys("boil-a", "filter:var(--f0)", "filter:var(--f1)", "filter:var(--f2)", "filter:var(--f0)"),
    ),
    Format(
        id="comic_pop",
        title="Comic pop",
        summary="A burst of halftone colour explodes behind one elastic word.",
        group="paper",
        jobs=("hook", "transition"),
        slots=(Slot("text", limit=11), Slot("label", limit=36, required=False)),
        example={"text": "Boom!", "label": "Meanwhile, in the edit suite"},
        draw=_comic,
        seconds=2.2,
        shortest=1.2,
        longest=6.0,
        tall=720,
        sound="pop",
        leaves=False,
        css=_stock("comic_pop")
        + """
.f-comic_pop .set{margin:0 auto;width:min(100%,calc(900px * var(--scale)))}
.f-comic_pop .stage{position:relative;height:calc(600px * var(--scale));display:grid;place-items:center}
.f-comic_pop .hit{position:absolute;inset:0;animation:pop-hit .625s step-end both,p-jit .5s step-end infinite}
.f-comic_pop .shade,.f-comic_pop .fill{position:absolute;inset:0}
.f-comic_pop .shade{background:#141210;transform:translate(calc(15px * var(--scale)),calc(17px * var(--scale)))}
.f-comic_pop .fill{background:var(--accent)}
.f-comic_pop .fill i{position:absolute;inset:0;background:radial-gradient(circle,rgba(20,18,16,.92) 0 42%,transparent 44%) 0 0/calc(17px * var(--scale)) calc(17px * var(--scale));-webkit-mask:radial-gradient(closest-side,transparent 38%,#000 96%);mask:radial-gradient(closest-side,transparent 38%,#000 96%)}
.f-comic_pop h1{position:relative;margin:0;font-size:calc(var(--size) * var(--scale));font-weight:900;font-style:italic;line-height:1;letter-spacing:-.03em;text-transform:uppercase;color:var(--white);-webkit-text-stroke:.055em #141210;paint-order:stroke fill;text-shadow:.04em .05em 0 var(--brick),.055em .065em 0 #141210;animation:pop-word .75s step-end .125s both}
.f-comic_pop .strap{display:flex;justify-content:center;margin-top:calc(8px * var(--scale));animation:p-snap .5s step-end .75s both}
.f-comic_pop .strap span{padding:.34em .62em;font-size:calc(38px * var(--scale));font-weight:800;font-style:italic;letter-spacing:-.005em;text-transform:uppercase;color:#141210;background:var(--gold);border:calc(5px * var(--scale)) solid #141210;box-shadow:calc(8px * var(--scale)) calc(8px * var(--scale)) 0 #141210;text-align:center}
"""
        + _keys(
            "pop-hit",
            "transform:scale(0) rotate(-20deg)",
            "transform:scale(1.22) rotate(5deg)",
            "transform:scale(.9) rotate(-3deg)",
            "transform:scale(1.05) rotate(1deg)",
            "transform:scale(.99)",
            "transform:none",
        )
        + _keys(
            "pop-word",
            "opacity:0;transform:scale(.2) rotate(-12deg)",
            "opacity:1;transform:scale(1.34,1.1) rotate(4deg)",
            "opacity:1;transform:scale(.88,1.14) rotate(-3deg)",
            "opacity:1;transform:scale(1.1,.94) rotate(1deg)",
            "opacity:1;transform:scale(.98,1.03) rotate(-.5deg)",
            "opacity:1;transform:rotate(-2deg)",
            "opacity:1;transform:rotate(-2deg)",
        )
        + _SNAP,
    ),
    Format(
        id="scrapbook",
        title="Scrapbook slap",
        summary="Notes, cards and tags slap onto the frame and get taped down as each is said.",
        group="paper",
        jobs=("explain", "prove"),
        slots=(Slot("items", kind="list", limit=4, each=38), Slot("title", limit=30, required=False)),
        example={
            "title": "This week",
            "items": [{"text": "Shoot on *Monday*"}, {"text": "Cut in one sitting"}, {"text": "Captions before coffee"}, {"text": "Post by *noon*"}],
        },
        draw=_scrapbook,
        seconds=4.0,
        shortest=2.0,
        longest=12.0,
        tall=720,
        sound="pop",
        fonts=("hand", "mono"),
        leaves=False,
        css=_stock("scrapbook")
        + """
.f-scrapbook .set{margin:0 auto;width:min(100%,calc(1500px * var(--scale)))}
.f-scrapbook h2{display:table;margin:0 0 calc(22px * var(--scale));padding:.2em .6em;font-family:var(--mono);font-size:calc(34px * var(--scale));font-weight:600;color:var(--panel);background:var(--accent);rotate:-1deg;filter:var(--thick);animation:p-snap .5s step-end both}
.f-scrapbook .wall{display:grid;grid-template-columns:repeat(auto-fit,minmax(calc(340px * var(--scale)),1fr));gap:calc(24px * var(--scale))}
.f-scrapbook .hit{position:relative;filter:var(--thick);animation:p-slap .5s step-end var(--at) both,p-jit .75s step-end calc(var(--k) * -.125s) infinite}
.f-scrapbook .note{display:grid;align-items:center;min-height:calc(290px * var(--scale));padding:calc(40px * var(--scale)) calc(34px * var(--scale)) calc(30px * var(--scale));color:var(--pen);background-image:var(--grain)}
.f-scrapbook p{margin:0;font-family:var(--hand);font-size:calc(70px * var(--scale));font-weight:700;line-height:1;overflow-wrap:anywhere}
.f-scrapbook p em{color:var(--brick)}
.f-scrapbook .sticky{background-color:#f6dc6b;background-image:var(--grain),linear-gradient(180deg,transparent 80%,rgba(120,84,10,.16));border-bottom-right-radius:60% 7%}
.f-scrapbook .card{background-color:var(--white);background-image:var(--grain),linear-gradient(transparent calc(58px * var(--scale)),rgba(212,65,42,.6) calc(58px * var(--scale)) calc(60px * var(--scale)),transparent calc(60px * var(--scale))),repeating-linear-gradient(transparent 0 calc(66px * var(--scale)),rgba(60,110,150,.3) calc(66px * var(--scale)) calc(68px * var(--scale)))}
.f-scrapbook .tag{background-color:var(--kraft);clip-path:polygon(9% 0,100% 0,100% 100%,9% 100%,0 86%,0 14%)}
.f-scrapbook .tag p{padding-left:.5em}
.f-scrapbook .leaf{background-color:var(--cream);background-image:var(--grain),repeating-linear-gradient(transparent 0 calc(58px * var(--scale)),rgba(28,24,19,.14) calc(58px * var(--scale)) calc(60px * var(--scale)))}
.f-scrapbook .tape{position:absolute;top:calc(-20px * var(--scale));width:calc(170px * var(--scale));height:calc(46px * var(--scale));font-size:calc(100px * var(--scale));background:rgba(240,226,184,.74);animation:tape-on .25s step-end var(--at) both}
@keyframes tape-on{0%{opacity:0;transform:translateY(-30%) scale(1.2)}50%{opacity:1;transform:scale(1.06)}100%{opacity:1;transform:none}}
"""
        + _keys(
            "p-slap",
            "opacity:0;transform:scale(1.5) rotate(7deg)",
            "opacity:1;transform:scale(1.12) rotate(-2deg)",
            "opacity:1;transform:scale(.97) rotate(.4deg)",
            "opacity:1;transform:none",
            "opacity:1;transform:none",
        )
        + _SNAP,
    ),
    Format(
        id="pen_trail",
        title="Pen trail",
        summary="A pencil writes the words across a page and links them, the camera following its tip.",
        group="paper",
        jobs=("explain", "hook"),
        slots=(Slot("items", kind="list", limit=5, each=14),),
        example={"items": [{"text": "Idea"}, {"text": "First draft"}, {"text": "Rewrite"}, {"text": "Finished"}]},
        draw=_pen_trail,
        seconds=5.0,
        shortest=3.0,
        longest=14.0,
        area="full",
        tall=1920,
        sound=None,
        fonts=("hand",),
        needs_alpha=False,
        css=_stock("pen_trail")
        + """
.f-pen_trail .room{position:absolute;inset:0;overflow:hidden;background-color:var(--cream);background-image:var(--grain)}
.f-pen_trail .lens{position:absolute;left:50%;top:50%;width:0;height:0;transform:scale(var(--scale))}
.f-pen_trail .pull{animation:trail-pull var(--for) steps(9,end) var(--at) both}
.f-pen_trail .cam{animation:trail-cam var(--for) steps(8,end) var(--at) both}
.f-pen_trail .sheet{position:absolute;left:-3000px;top:-3000px;width:6000px;height:7000px;background-image:radial-gradient(circle,rgba(28,24,19,.2) 0 2px,transparent 2.6px);background-size:52px 52px}
.f-pen_trail .world{position:absolute;left:0;top:0}
.f-pen_trail .word{position:absolute;font-family:var(--hand);font-size:176px;font-weight:700;line-height:1;white-space:nowrap;color:var(--pen);clip-path:inset(-30% 100% -30% -5%);animation:trail-write var(--for) steps(var(--n),end) var(--at) both}
.f-pen_trail .link{position:absolute;left:0;top:0;stroke-dashoffset:1;animation:trail-link var(--for) steps(var(--n),end) var(--at) both}
.f-pen_trail .link svg{position:absolute;left:-2000px;top:-2000px;width:4000px;height:5000px;overflow:visible;fill:none;stroke:var(--brick);stroke-width:7;stroke-linecap:round}
.f-pen_trail .link path{stroke-dasharray:1 1}
.f-pen_trail .pen{position:absolute;left:0;top:0;width:90px;height:240px;offset-rotate:0deg;offset-anchor:50% 100%;opacity:0;rotate:26deg;transform-origin:50% 100%;filter:drop-shadow(14px 18px 12px rgba(30,20,8,.32));animation:trail-pen var(--for) linear var(--at) forwards,trail-hide .05s step-end calc(var(--at) + var(--for)) forwards}
.f-pen_trail .pen svg{width:100%;height:100%;display:block}
.f-pen_trail .wood{fill:#e6c08f}
.f-pen_trail .tip{fill:var(--pen)}
.f-pen_trail .body{fill:var(--accent)}
.f-pen_trail .band{fill:#b9b4a6}
.f-pen_trail .rub{fill:var(--brick)}
.f-pen_trail .vignette{position:absolute;inset:0;background:radial-gradient(90% 80% at 50% 50%,transparent 60%,rgba(70,48,20,.22))}
@keyframes trail-pull{to{transform:scale(var(--fit))}}
@keyframes trail-cam{from{transform:translate(var(--dx),var(--dy))}to{transform:none}}
@keyframes trail-write{to{clip-path:inset(-30% -5% -30% -5%)}}
@keyframes trail-link{to{stroke-dashoffset:0}}
@keyframes trail-pen{from{offset-distance:0%;opacity:1}to{offset-distance:100%;opacity:1}}
@keyframes trail-hide{to{visibility:hidden}}
""",
    ),
    Format(
        id="fold_out",
        title="Fold-out",
        summary="A folded sheet opens along its creases, panel by panel, to show the line.",
        group="paper",
        jobs=("hook", "explain"),
        slots=(Slot("text", limit=56), Slot("label", limit=40, required=False)),
        example={"text": "The whole plan fits on *one sheet*", "label": "Fold, unfold, get to work"},
        draw=_fold_out,
        seconds=4.2,
        shortest=2.4,
        longest=10.0,
        tall=725,
        sound="whoosh",
        fonts=("serif", "mono"),
        leaves=False,
        css=_stock("fold_out")
        + """
.f-fold_out .set{margin:0 auto;width:min(100%,calc(860px * var(--scale)));filter:var(--thick)}
.f-fold_out .pack{--ph:calc(172px * var(--scale));position:relative;height:var(--ph);perspective:calc(2400px * var(--scale));perspective-origin:50% 0;transform-style:preserve-3d;rotate:-1deg;--x:0%;--y:-24%;--r:-5deg;animation:p-place .375s step-end both}
.f-fold_out .flat{position:absolute;inset:0;overflow:hidden}
.f-fold_out .hinge{position:absolute;left:0;right:0;top:100%;height:100%;transform-origin:50% 0;transform-style:preserve-3d;animation:fold-open .75s steps(6,end) var(--at) both}
.f-fold_out .front,.f-fold_out .back{position:absolute;inset:0;overflow:hidden;backface-visibility:hidden;-webkit-backface-visibility:hidden}
.f-fold_out .back{transform:rotateX(180deg);background-color:var(--kraft);background-image:var(--grain)}
.f-fold_out .slice{position:absolute;inset:0;overflow:hidden;background-color:var(--cream);background-image:var(--grain)}
.f-fold_out .page{position:absolute;left:0;right:0;top:calc(var(--n) * -100%);height:400%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:calc(24px * var(--scale));padding:0 calc(56px * var(--scale));text-align:center;color:var(--pen)}
.f-fold_out h1{margin:0;font-family:var(--serif);font-weight:400;font-size:calc(118px * var(--scale));line-height:.98;letter-spacing:-.022em;text-wrap:balance}
.f-fold_out h1 em{color:var(--brick)}
.f-fold_out p{margin:0;font-family:var(--mono);font-size:calc(32px * var(--scale));font-weight:500;color:rgba(28,24,19,.72)}
.f-fold_out .crease{position:absolute;left:0;right:0;top:0;height:calc(26px * var(--scale));background:linear-gradient(rgba(70,48,20,.26),rgba(255,255,255,.32) 12%,transparent)}
.f-fold_out .dim{position:absolute;inset:0;background:linear-gradient(rgba(36,24,10,.62),rgba(36,24,10,.3));animation:fold-dim .75s steps(6,end) var(--at) both}
@keyframes fold-open{from{transform:rotateX(176deg) translateZ(1px)}to{transform:none}}
@keyframes fold-dim{to{opacity:0}}
"""
        + _PLACE,
    ),
    Format(
        id="stick_crew",
        title="Stick crew",
        summary="Two pencil figures march the words in overhead, plant their feet and give a little hop.",
        group="paper",
        jobs=("hook", "explain", "close"),
        slots=(Slot("text", limit=30),),
        example={"text": "Delivered by *hand*"},
        draw=_crew,
        seconds=3.6,
        shortest=2.8,
        longest=8.0,
        tall=615,
        sound=None,
        leaves=False,
        css=_stock("stick_crew")
        + """
.f-stick_crew .set{margin:0 auto;width:min(100%,calc(1040px * var(--scale)))}
.f-stick_crew .board{position:relative;animation:p-snap .5s step-end both}
.f-stick_crew .board>.rip{position:absolute;inset:0}
.f-stick_crew .slit{position:relative;overflow:hidden;margin:0 calc(12px * var(--scale));padding:calc(40px * var(--scale)) 0 calc(54px * var(--scale));--walk:1.75s}
.f-stick_crew .floor{position:absolute;left:0;right:0;bottom:calc(52px * var(--scale));height:calc(5px * var(--scale));border-radius:9px;background:var(--pen)}
.f-stick_crew .team{position:relative;display:flex;flex-direction:column;align-items:center;padding-bottom:calc(290px * var(--scale));animation:march var(--walk) steps(14,end) both,hop .75s step-end calc(var(--walk) + .25s) both}
.f-stick_crew .block{position:relative;z-index:1;max-width:calc(760px * var(--scale));padding:calc(20px * var(--scale)) calc(38px * var(--scale)) calc(24px * var(--scale));text-align:center;background-color:var(--accent);background-image:var(--grain);filter:var(--thick)}
.f-stick_crew .block span{display:block;font-size:calc(84px * var(--scale));font-weight:800;letter-spacing:-.035em;line-height:1;color:var(--panel);text-wrap:balance}
.f-stick_crew .block em{color:inherit;font-style:italic}
.f-stick_crew .crew{position:absolute;left:0;right:0;bottom:0;height:calc(290px * var(--scale));animation:crew-boil .375s step-end infinite}
.f-stick_crew .man{position:absolute;bottom:0;width:calc(212px * var(--scale));height:calc(290px * var(--scale));margin-left:calc(-106px * var(--scale))}
.f-stick_crew .man.rear{left:calc(50% - 230px * var(--scale))}
.f-stick_crew .man.lead{left:calc(50% + 230px * var(--scale))}
.f-stick_crew .pose,.f-stick_crew .fr{position:absolute;inset:0}
.f-stick_crew .fr{visibility:hidden;animation:walk-frame .75s step-end calc((var(--n) - 6) * .125s) infinite}
.f-stick_crew .go{animation:go-off .05s step-end var(--walk) forwards}
.f-stick_crew .rest{visibility:hidden;animation:rest-on .05s step-end var(--walk) forwards}
.f-stick_crew svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
.f-stick_crew path{fill:none;stroke:var(--pen);stroke-width:9;stroke-linecap:round;stroke-linejoin:round}
.f-stick_crew .head{fill:var(--white);stroke:var(--pen);stroke-width:8}
.f-stick_crew .eye{fill:var(--pen)}
@keyframes march{from{translate:-62% 0}to{translate:0 0}}
@keyframes go-off{to{opacity:0}}
@keyframes rest-on{to{visibility:visible}}
@keyframes walk-frame{0%{visibility:visible}16.6%,100%{visibility:hidden}}
"""
        + _keys(
            "hop",
            "transform:none",
            "transform:translateY(calc(-34px * var(--scale)))",
            "transform:translateY(calc(-46px * var(--scale)))",
            "transform:translateY(calc(-18px * var(--scale)))",
            "transform:none",
            "transform:translateY(calc(-20px * var(--scale)))",
            "transform:none",
        )
        + _keys("crew-boil", "filter:var(--f0)", "filter:var(--f1)", "filter:var(--f2)", "filter:var(--f0)")
        + _SNAP,
    ),
)
