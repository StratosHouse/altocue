"""Social proof: what the user passes in, set well.

Nothing here invents a review, a rating or a name, and no card imitates a real platform. A star row shows the
number it was given. Examples are plainly fictional. Motion lives on HTML elements, which the renderer seeks per clip.
"""

from __future__ import annotations

import hashlib
import math

from . import Format, Slot, kit

CARD = """background:color-mix(in srgb,var(--surface) 95%,transparent);box-shadow:0 0 0 1px rgba(255,255,255,.1) inset,0 1px 0 rgba(255,255,255,.08) inset,0 calc(30px * var(--scale)) calc(80px * var(--scale)) rgba(0,0,0,.42)"""


def _shade(*seed: object, n: int = 0) -> int:
    """A small number from the copy, for choices that must not change between renders."""
    return hashlib.sha256("|".join(str(part) for part in (*seed, n)).encode()).digest()[0]


def _words(text: str) -> str:
    """One span per word, with punctuation after a `*marked*` word kept on that word."""
    out: list[tuple[str, bool]] = []
    for word, hot in kit.tokens(text):
        if out and not word[0].isalnum() and word[0] not in "\"'(":
            out[-1] = (out[-1][0] + word, out[-1][1])
        else:
            out.append((word, hot))
    return " ".join(f'<span class="w{" hot" if hot else ""}" style="--i:{index}">{kit.esc(word)}</span>' for index, (word, hot) in enumerate(out))


def _initials(name: str) -> str:
    parts = [part for part in str(name).replace(",", " ").split() if part[0].isalnum()]
    return "".join(part[0] for part in parts[:2]).upper() or "?"


def _disc(name: str) -> str:
    """Initials on a tint from the brand. There is no stock face to put here, and no real one to borrow."""
    mix = 26 + _shade(name) % 40
    return f'<span class="disc" style="background:color-mix(in srgb,var(--accent) {mix}%,var(--surface))">{kit.esc(_initials(name))}</span>'


def _wheel(value: str, at: float = 0.1, pace: float = 1.0) -> str:
    """Digits roll up to the value given. Commas, signs and units stay as typed, so the number on screen is exactly the number passed in."""
    places = [index for index, char in enumerate(value) if char.isdigit()]
    out = []
    for index, char in enumerate(value):
        if not char.isdigit():
            out.append(f'<span class="still" style="--at:{at + pace * 0.72:.3f}s">{kit.esc(char)}</span>')
            continue
        place = places.index(index)
        steps = 6 + place * 3
        run = [(int(char) - steps + n) % 10 for n in range(steps + 1)]
        cells = "".join(f"<i>{digit}</i>" for digit in run)
        out.append(f'<span class="col"><span class="roll" style="--n:{steps};--at:{at + place * 0.05:.3f}s;--for:{pace:.3f}s">{cells}</span></span>')
    return f'<span class="wheel" aria-label="{kit.esc(value)}">{"".join(out)}</span>'


WHEEL = """
.wheel{display:inline-flex;line-height:1;font-variant-numeric:tabular-nums}
.col{display:inline-block;height:1em;overflow:hidden;-webkit-mask:linear-gradient(transparent,#000 12%,#000 88%,transparent);mask:linear-gradient(transparent,#000 12%,#000 88%,transparent)}
.roll{display:block;animation:q-roll var(--for) cubic-bezier(.2,.8,.16,1) var(--at) both}
.roll i{display:block;height:1em;font-style:normal;text-align:center}
.still{display:inline-block;animation:q-fade .3s ease-out var(--at) both}
@keyframes q-roll{to{transform:translateY(calc(var(--n) * -1em))}}
@keyframes q-fade{from{opacity:0}to{opacity:1}}
"""


def _scoped(fid: str, css: str) -> str:
    """Prefix a shared block so it stays under one format."""
    lines = []
    for line in css.strip().splitlines():
        lines.append(line if line.startswith("@keyframes") else f".f-{fid} {line}")
    return "\n".join(lines) + "\n"


def _testimonial(item: dict) -> str:
    count = max(len(kit.tokens(item["text"])), 1)
    gap = min(0.034, 0.95 / count)
    late = 0.2 + count * gap
    role = f'<span class="role">{kit.esc(item["role"])}</span>' if item.get("role") else ""
    return (
        f'<div class="card"><span class="mark" aria-hidden="true">“</span><blockquote style="--gap:{gap:.3f}s">{_words(item["text"])}</blockquote>'
        f'<i class="rule" style="--late:{late:.3f}s"></i><div class="who" style="--late:{late + 0.08:.3f}s">{_disc(item["name"])}<div class="id"><span class="name">{kit.esc(item["name"])}</span>{role}</div></div></div>'
    )


_STAR = '<svg viewBox="0 0 100 100" aria-hidden="true"><path d="M50 9l12.4 26.5 29 3.6-21.4 19.8 5.7 28.6L50 72.8 24.3 86.5 30 57.9 8.6 39.1l29-3.6z"/></svg>'


def _stars(item: dict) -> str:
    best = float(item.get("best") or 5)
    shown = str(item["rating"])
    share = max(min(float(item["rating"]) / best, 1.0), 0.0) * 5
    cells, at = [], 0.34
    for index in range(5):
        fill = max(min(share - index, 1.0), 0.0)
        pace = 0.2 * max(fill, 0.3)
        whole = " whole" if fill >= 1 else ""
        cells.append(f'<span class="star{whole}" style="--at:{at:.3f}s;--for:{pace:.3f}s;--f:{fill:.3f}"><span class="off">{_STAR}</span><span class="on">{_STAR}</span></span>')
        at += pace + 0.03 if fill else 0
    of = f"{best:g}"
    return (
        f'<div class="card"><div class="score"><span class="num">{_wheel(shown, 0.12, 1.0)}</span><span class="of">out of {kit.esc(of)}</span></div>'
        f'<div class="row">{"".join(cells)}</div><p>{kit.esc(item["label"])}</p></div>'
    )


def _logo_wall(item: dict) -> str:
    rows = kit.rows(item, "items")
    total = len(rows)
    wide = {1: 1, 2: 2, 3: 3, 4: 4, 5: 3, 6: 3, 7: 4, 8: 4}.get(total, 4)
    order = sorted(range(total), key=lambda i: _shade(rows[i]["text"], n=i))
    cells = []
    for index, row in enumerate(rows):
        look = _shade(row["text"], item.get("title", "")) % 5
        cells.append(f'<div class="cell"><span class="mk k{look}" style="--at:{0.26 + order.index(index) * 0.09:.3f}s">{kit.esc(row["text"])}</span></div>')
    title = f'<p class="over">{kit.esc(item["title"])}</p>' if item.get("title") else ""
    return f'<div class="set"><div class="card">{title}<div class="wall" style="--wide:{wide}">{"".join(cells)}</div></div></div>'


_TICK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.8l4.6 4.4L19 7.4"/></svg>'


def _trust(item: dict) -> str:
    rows = [row["text"] for row in kit.rows(item, "items")]
    run = "".join(f'<span class="pt"><i>{_TICK}</i>{kit.esc(text)}</span>' for text in rows)
    length = sum(len(text) for text in rows) + 5 * len(rows)
    return f'<div class="band" style="--loop:{max(length * 0.24, 7):.2f}s"><div class="track">{(f"<div class=run>{run}</div>") * 6}</div></div>'


def _milestone(item: dict) -> str:
    value = str(item["value"])
    size = min(250.0, 1400 / max(len(value), 1))
    note = f'<span class="note">{kit.esc(item["note"])}</span>' if item.get("note") else ""
    ticks = "".join(f'<i style="--i:{index}"></i>' for index in range(21))
    return (
        f'<div class="set"><div class="glow"></div><div class="num" style="--size:{size:.0f}px">{_wheel(value, 0.1, 1.15)}</div><div class="ruler">{ticks}</div>'
        f'<p class="lab">{kit.esc(item["label"])}</p>{note}</div>'
    )


def _comment(item: dict) -> str:
    count = max(len(kit.tokens(item["text"])), 1)
    gap = min(0.03, 0.6 / count)
    note = f'<span class="note" style="--late:{0.32 + count * gap:.3f}s">{kit.esc(item["note"])}</span>' if item.get("note") else ""
    return (
        f'<div class="set"><div class="card"><div class="head">{_disc(item["name"])}<span class="name">{kit.esc(item["name"])}</span></div>'
        f'<p style="--gap:{gap:.3f}s">{_words(item["text"])}</p>{note}</div></div>'
    )


def _match_cut(item: dict) -> str:
    rows = kit.rows(item, "words")
    total = kit.seconds(item)
    times = [row["delay"] if row.get("at") is not None else total * 0.74 * index / max(len(rows), 1) for index, row in enumerate(rows)]
    looks = ("dark", "light", "lit", "dark alt", "light alt", "lit alt")
    letters = sum(len(row["text"]) + 1 for row in rows)
    size = min(96.0, math.sqrt(790 * 320 / (0.56 * max(letters, 1))))
    cards = []
    seed = "".join(row["text"] for row in rows)
    for index in range(len(rows)):
        words = " ".join(f'<span class="{"" if place <= index else "off"}">{kit.esc(row["text"])}</span>' for place, row in enumerate(rows))
        look = looks[(_shade(seed) + index) % len(looks)]
        off = f";--to:{times[index + 1]:.3f}s" if index + 1 < len(rows) else ""
        one = 34 + _shade(seed, n=index * 3 + 1) % 34
        two = 20 + _shade(seed, n=index * 3 + 2) % 26
        three = 44 + _shade(seed, n=index * 3 + 3) % 40
        side = " flip" if _shade(seed, n=index * 7) % 2 else ""
        ending = "" if index + 1 < len(rows) else " last"
        cards.append(
            f'<div class="card {look}{ending}" style="--at:{times[index]:.3f}s{off}"><div class="top{side}"><i class="ava"></i><i class="bar" style="width:{one}%"></i></div>'
            f'<p>{words}</p><div class="foot{side}"><i class="bar" style="width:{three}%"></i><i class="bar" style="width:{two}%"></i></div></div>'
        )
    return f'<div class="set"><div class="stage" style="--size:{size:.0f}px">{"".join(cards)}</div></div>'


_ARROW = '<svg viewBox="0 0 64 80" aria-hidden="true"><path d="M6 4l50 44H33l13 26-11 5-13-27L6 66z"/></svg>'


def _follow(item: dict) -> str:
    sub = f'<span class="sub">{kit.esc(item["label"])}</span>' if item.get("label") else ""
    return (
        f'<div class="set"><div class="card">{_disc(item["name"])}<div class="id"><span class="name">{kit.esc(item["name"])}</span>{sub}</div>'
        f'<div class="spot"><i class="ring"></i><span class="btn"><span class="a">{kit.esc(item["text"])}</span><span class="b"><i>{_TICK}</i>{kit.esc(item["done"])}</span></span>'
        f'<div class="fly-x"><div class="fly-y"><div class="tap">{_ARROW}</div></div></div></div></div></div>'
    )


def _fit(value: str, room: float, top: float) -> float:
    return min(top, room / max(len(value) * 0.56, 1))


def _before_after(item: dict) -> str:
    was, now = str(item["before"]), str(item["after"])
    size = min(_fit(was, 346, 124), _fit(now, 346, 124))
    title = f'<p class="over">{kit.esc(item["label"])}</p>' if item.get("label") else ""
    return (
        f'<div class="set"><div class="card">{title}<div class="split" style="--size:{size:.0f}px">'
        f'<div class="pane was"><span class="tag">{kit.esc(item.get("before_label", "Before"))}</span><b>{kit.esc(was)}</b></div>'
        f'<div class="pane now"><span class="tag">{kit.esc(item.get("after_label", "After"))}</span><b>{kit.esc(now)}</b></div>'
        '<div class="bar"><i><svg viewBox="0 0 48 48" aria-hidden="true"><path d="M19 15l-9 9 9 9M29 15l9 9-9 9"/></svg></i></div></div></div></div>'
    )


def _cloud(item: dict) -> str:
    names = [row["text"] for row in kit.rows(item, "names")]
    seed = str(item["value"])
    rows, across = 5, 9
    discs, place = [], 0
    for row in range(rows):
        count = across - row % 2
        for col in range(count):
            x = (col + 0.5 + (row % 2) * 0.5) / across * 100
            y = (row + 0.5) / rows * 100
            tint = 14 + _shade(seed, n=place) % 58
            inner = kit.esc(_initials(names[place])) if place < len(names) else ""
            pull = _shade(seed, n=place + 300) / 255
            turn = _shade(seed, n=place + 600) / 255 * math.tau
            reach = 90 + pull * 170
            delay = 0.1 + (_shade(seed, n=place + 900) % 60) / 100
            discs.append(
                f'<span class="face{"" if inner else " plain"}" style="left:{x:.2f}%;top:{y:.2f}%;--dx:{math.cos(turn) * reach:.0f}%;--dy:{math.sin(turn) * reach:.0f}%;--at:{delay:.3f}s;background-color:color-mix(in srgb,var(--accent) {tint}%,var(--surface))">{inner}</span>'
            )
            place += 1
    return f'<div class="set"><div class="head"><span class="num">{_wheel(seed, 0.3, 1.0)}</span><span class="lab">{kit.esc(item["label"])}</span></div><div class="crowd">{"".join(discs)}</div></div>'


def _press(item: dict) -> str:
    rows = kit.rows(item, "items")
    names = "".join(f'<span class="name"><span style="--at:{0.28 + index * 0.11:.3f}s">{kit.esc(row["text"])}</span></span>' for index, row in enumerate(rows))
    return f'<div class="set"><p class="over"><i></i><span>{kit.esc(item["label"])}</span><i></i></p><div class="row">{names}</div></div>'


FORMATS = (
    Format(
        id="testimonial_card",
        title="Testimonial card",
        summary="A customer's words set like a pull quote, with their name and role beneath.",
        group="proof",
        jobs=("prove",),
        slots=(Slot("text", limit=140), Slot("name", limit=30), Slot("role", limit=38, required=False)),
        example={"text": "We cut our edit time in *half*, and nobody on the team misses the old way.", "name": "Sam Rivera", "role": "Head of video, Northlight"},
        draw=_testimonial,
        seconds=7.6,
        shortest=2.5,
        longest=12.0,
        tall=680,
        fonts=("serif",),
        css=f"""
.f-testimonial_card .card{{position:relative;overflow:hidden;max-width:calc(1120px * var(--scale));margin:0 auto;padding:calc(56px * var(--scale)) calc(56px * var(--scale)) calc(46px * var(--scale));border-radius:calc(30px * var(--scale));{CARD};animation:tq-card .7s var(--ease) both}}
.f-testimonial_card .mark{{position:absolute;right:calc(26px * var(--scale));top:calc(-24px * var(--scale));font-family:var(--serif);font-size:calc(380px * var(--scale));line-height:1;color:var(--accent);opacity:.15}}
.f-testimonial_card blockquote{{position:relative;margin:0;font-family:var(--serif);font-size:calc(72px * var(--scale));line-height:1.06;letter-spacing:-.012em;color:var(--ink)}}
.f-testimonial_card .w{{display:inline-block;animation:tq-word .55s var(--ease) calc(.2s + var(--i) * var(--gap)) both}}
.f-testimonial_card .w.hot{{color:var(--accent)}}
.f-testimonial_card .rule{{display:block;height:1px;margin:calc(36px * var(--scale)) 0 calc(28px * var(--scale));background:rgba(255,255,255,.16);transform-origin:0 50%;animation:tq-rule .8s var(--ease) var(--late) both}}
.f-testimonial_card .who{{display:flex;align-items:center;gap:calc(22px * var(--scale));animation:tq-word .55s var(--ease) var(--late) both}}
.f-testimonial_card .disc{{flex:none;display:grid;place-items:center;width:calc(84px * var(--scale));height:calc(84px * var(--scale));border-radius:50%;font-size:calc(32px * var(--scale));font-weight:600;letter-spacing:.01em;color:var(--ink)}}
.f-testimonial_card .id{{display:flex;flex-direction:column;gap:calc(4px * var(--scale));min-width:0}}
.f-testimonial_card .name{{font-size:calc(38px * var(--scale));font-weight:600;letter-spacing:-.015em;line-height:1.1}}
.f-testimonial_card .role{{font-size:calc(31px * var(--scale));color:var(--soft);line-height:1.15}}
@keyframes tq-card{{from{{opacity:0;transform:translateY(calc(40px * var(--scale))) scale(.975)}}to{{opacity:1;transform:none}}}}
@keyframes tq-word{{from{{opacity:0;transform:translateY(.32em)}}to{{opacity:1;transform:none}}}}
@keyframes tq-rule{{from{{transform:scaleX(0)}}to{{transform:none}}}}
""",
    ),
    Format(
        id="star_fill",
        title="Star fill",
        summary="Five stars fill to the rating you give, with the number rolling up above them.",
        group="proof",
        jobs=("prove",),
        slots=(Slot("rating", kind="number"), Slot("label", limit=46), Slot("best", kind="number", required=False)),
        example={"rating": 4.8, "label": "Average of 1,204 reviews, sample data"},
        draw=_stars,
        seconds=3.2,
        shortest=2.0,
        longest=10.0,
        tall=550,
        sound="rise",
        css=f"""
.f-star_fill .card{{max-width:calc(940px * var(--scale));margin:0 auto;padding:calc(44px * var(--scale)) calc(40px * var(--scale)) calc(42px * var(--scale));border-radius:calc(30px * var(--scale));text-align:center;{CARD};animation:sr-card .6s var(--ease) both}}
.f-star_fill .score{{display:flex;justify-content:center;align-items:baseline;gap:calc(20px * var(--scale))}}
.f-star_fill .num{{font-size:calc(170px * var(--scale));font-weight:600;letter-spacing:-.04em;line-height:1;color:var(--ink)}}
.f-star_fill .of{{font-size:calc(38px * var(--scale));color:var(--soft)}}
.f-star_fill .row{{display:flex;justify-content:center;gap:calc(10px * var(--scale));margin:calc(18px * var(--scale)) 0 calc(20px * var(--scale))}}
.f-star_fill .star{{position:relative;width:calc(150px * var(--scale));height:calc(150px * var(--scale))}}
.f-star_fill .star.whole{{animation:sr-pop .42s var(--ease) calc(var(--at) + var(--for) - .04s) both}}
.f-star_fill .off,.f-star_fill .on{{position:absolute;inset:0}}
.f-star_fill svg{{width:100%;height:100%;display:block;overflow:visible}}
.f-star_fill path{{stroke-width:7;stroke-linejoin:round}}
.f-star_fill .off path{{fill:rgba(255,255,255,.1);stroke:rgba(255,255,255,.1)}}
.f-star_fill .on path{{fill:var(--accent);stroke:var(--accent)}}
.f-star_fill .on{{clip-path:inset(-8% 108% -8% -8%);animation:sr-fill var(--for) linear var(--at) forwards}}
.f-star_fill p{{margin:0;font-size:calc(36px * var(--scale));color:var(--soft);line-height:1.2;animation:sr-card .5s var(--ease) 1.1s both}}
@keyframes sr-card{{from{{opacity:0;transform:translateY(calc(26px * var(--scale)))}}to{{opacity:1;transform:none}}}}
@keyframes sr-fill{{to{{clip-path:inset(-8% calc((1 - var(--f)) * 100%) -8% -8%)}}}}
@keyframes sr-pop{{0%{{transform:none}}40%{{transform:scale(1.13)}}100%{{transform:none}}}}
"""
        + _scoped("star_fill", WHEEL),
    ),
    Format(
        id="logo_wall",
        title="Name wall",
        summary="Client names settle into a clean grid, each set in its own type.",
        group="proof",
        jobs=("prove",),
        slots=(Slot("items", kind="list", limit=8, each=14), Slot("title", limit=44, required=False)),
        example={"title": "Teams on the sample plan", "items": [{"text": "Northlight"}, {"text": "Quillhaven"}, {"text": "Marrowfield"}, {"text": "Larkmoor"}, {"text": "Oatfield Co"}, {"text": "Brasswick"}]},
        draw=_logo_wall,
        seconds=3.6,
        shortest=2.0,
        longest=10.0,
        tall=745,
        sound=None,
        fonts=("serif", "mono"),
        css=f"""
.f-logo_wall .set{{container-type:inline-size}}
.f-logo_wall .card{{max-width:calc(1320px * var(--scale));margin:0 auto;padding:calc(34px * var(--scale));border-radius:calc(30px * var(--scale));{CARD};animation:lw-card .6s var(--ease) both}}
.f-logo_wall .over{{margin:calc(4px * var(--scale)) 0 calc(26px * var(--scale));font-size:calc(34px * var(--scale));font-weight:500;color:var(--soft);text-align:center;line-height:1.15}}
.f-logo_wall .wall{{display:grid;grid-template-columns:repeat(2,1fr)}}
@container (min-width:1200px){{.f-logo_wall .wall{{grid-template-columns:repeat(var(--wide),1fr)}}}}
.f-logo_wall .cell{{display:grid;place-items:center;height:calc(136px * var(--scale));overflow:hidden;box-shadow:0 0 0 .5px rgba(255,255,255,.11)}}
.f-logo_wall .mk{{font-size:calc(50px * var(--scale));line-height:1.1;color:var(--ink);white-space:nowrap;animation:lw-mark .7s var(--ease) var(--at) both}}
.f-logo_wall .k0{{font-weight:800;letter-spacing:-.045em}}
.f-logo_wall .k1{{font-family:var(--serif);font-size:calc(60px * var(--scale));letter-spacing:-.01em}}
.f-logo_wall .k2{{font-family:var(--mono);font-weight:500;font-size:calc(40px * var(--scale));letter-spacing:-.03em;text-transform:lowercase}}
.f-logo_wall .k3{{font-weight:500;font-size:calc(35px * var(--scale));letter-spacing:.13em;text-transform:uppercase}}
.f-logo_wall .k4{{font-family:var(--serif);font-style:italic;font-size:calc(62px * var(--scale))}}
@keyframes lw-card{{from{{opacity:0;transform:translateY(calc(30px * var(--scale)))}}to{{opacity:1;transform:none}}}}
@keyframes lw-mark{{from{{opacity:0;transform:translateY(62%)}}to{{opacity:1;transform:none}}}}
""",
    ),
    Format(
        id="trust_strip",
        title="Trust strip",
        summary="A slim band of proof points drifts past, out of the way of the shot.",
        group="proof",
        jobs=("prove",),
        slots=(Slot("items", kind="list", limit=5, each=26),),
        example={"items": [{"text": "No card needed"}, {"text": "Open source"}, {"text": "Works offline"}, {"text": "Export anywhere"}]},
        draw=_trust,
        seconds=5.0,
        shortest=2.5,
        longest=20.0,
        area="edge",
        tall=160,
        sound=None,
        css=f"""
.f-trust_strip{{top:calc(var(--cap-y) - 150px * var(--scale))}}
.f-trust_strip .band{{position:relative;overflow:hidden;height:calc(100px * var(--scale));margin:0 calc(var(--gutter) + var(--rail,0px)) 0 var(--gutter);border-radius:999px;{CARD};animation:ts-in .6s var(--ease) both}}
.f-trust_strip .track{{position:absolute;left:0;top:0;bottom:0;display:flex;width:max-content;animation:ts-run var(--loop) linear both}}
.f-trust_strip .run{{display:flex;flex:none;align-items:center}}
.f-trust_strip .pt{{display:inline-flex;align-items:center;gap:calc(14px * var(--scale));padding-right:calc(52px * var(--scale));font-size:calc(40px * var(--scale));font-weight:500;letter-spacing:-.015em;white-space:nowrap;color:var(--ink)}}
.f-trust_strip .pt i{{flex:none;display:grid;place-items:center;width:calc(40px * var(--scale));height:calc(40px * var(--scale));border-radius:50%;background:var(--accent)}}
.f-trust_strip svg{{width:66%;height:66%;fill:none;stroke:var(--panel);stroke-width:3.2;stroke-linecap:round;stroke-linejoin:round}}
@keyframes ts-in{{from{{opacity:0;transform:translateY(calc(20px * var(--scale)))}}to{{opacity:1;transform:none}}}}
@keyframes ts-run{{from{{transform:translateX(calc(40px * var(--scale)))}}to{{transform:translateX(calc(100% / -6 + 40px * var(--scale)))}}}}
""",
    ),
    Format(
        id="milestone",
        title="Milestone counter",
        summary="A big total rolls up digit by digit over a ruler, with its label beneath.",
        group="proof",
        jobs=("prove", "close"),
        slots=(Slot("value", limit=11), Slot("label", limit=50), Slot("note", limit=40, required=False)),
        example={"value": "12,480", "label": "films cut this month", "note": "Sample figure"},
        draw=_milestone,
        seconds=3.2,
        shortest=1.8,
        longest=10.0,
        tall=500,
        sound="rise",
        css="""
.f-milestone .set{position:relative;max-width:calc(1120px * var(--scale));margin:0 auto;text-align:center}
.f-milestone .glow{position:absolute;z-index:-1;inset:-22% -8%;background:radial-gradient(closest-side,rgba(6,10,14,.5),rgba(6,10,14,.3) 54%,rgba(6,10,14,0))}
.f-milestone .num{font-size:calc(var(--size) * var(--scale));font-weight:700;letter-spacing:-.05em;line-height:1;color:var(--ink);text-shadow:0 calc(8px * var(--scale)) calc(44px * var(--scale)) rgba(0,0,0,.42)}
.f-milestone .ruler{display:flex;justify-content:space-between;align-items:flex-end;width:min(100%,calc(880px * var(--scale)));height:calc(30px * var(--scale));margin:calc(18px * var(--scale)) auto calc(20px * var(--scale))}
.f-milestone .ruler i{display:block;width:calc(4px * var(--scale));height:46%;border-radius:9px;background:var(--accent);transform-origin:50% 100%;animation:ms-tick .4s var(--ease) calc(.14s + var(--i) * .045s) both}
.f-milestone .ruler i:nth-child(5n+1){height:100%}
.f-milestone .lab{margin:0;font-size:calc(54px * var(--scale));font-weight:500;letter-spacing:-.02em;line-height:1.14;color:var(--ink);text-shadow:0 calc(3px * var(--scale)) calc(18px * var(--scale)) rgba(0,0,0,.5);animation:ms-up .6s var(--ease) .7s both}
.f-milestone .note{display:block;margin-top:calc(10px * var(--scale));font-size:calc(32px * var(--scale));color:var(--soft);text-shadow:0 calc(3px * var(--scale)) calc(18px * var(--scale)) rgba(0,0,0,.5);animation:ms-up .6s var(--ease) .86s both}
@keyframes ms-tick{from{transform:scaleY(0)}to{transform:none}}
@keyframes ms-up{from{opacity:0;transform:translateY(calc(22px * var(--scale)))}to{opacity:1;transform:none}}
"""
        + _scoped("milestone", WHEEL),
    ),
    Format(
        id="comment_card",
        title="Comment card",
        summary="A plain card holds one comment, with a name and initials and nothing borrowed.",
        group="proof",
        jobs=("prove", "hook"),
        slots=(Slot("text", limit=110), Slot("name", limit=28), Slot("note", limit=34, required=False)),
        example={"text": "Finally a workflow that doesn't fight me. I shipped three cuts before *lunch*.", "name": "Priya Lindqvist", "note": "Sample comment"},
        draw=_comment,
        seconds=6,
        shortest=1.8,
        longest=10.0,
        tall=520,
        css=f"""
.f-comment_card .card{{position:relative;max-width:calc(880px * var(--scale));margin:0 auto;padding:calc(34px * var(--scale)) calc(40px * var(--scale)) calc(36px * var(--scale));border-radius:calc(34px * var(--scale)) calc(34px * var(--scale)) calc(34px * var(--scale)) calc(8px * var(--scale));{CARD};transform-origin:6% 100%;animation:cc-pop .62s both}}
.f-comment_card .head{{display:flex;align-items:center;gap:calc(18px * var(--scale));margin-bottom:calc(20px * var(--scale))}}
.f-comment_card .disc{{flex:none;display:grid;place-items:center;width:calc(68px * var(--scale));height:calc(68px * var(--scale));border-radius:50%;font-size:calc(27px * var(--scale));font-weight:600;color:var(--ink)}}
.f-comment_card .name{{font-size:calc(35px * var(--scale));font-weight:600;letter-spacing:-.015em;color:var(--ink);white-space:nowrap}}
.f-comment_card p{{margin:0;font-size:calc(52px * var(--scale));font-weight:500;line-height:1.16;letter-spacing:-.022em;color:var(--ink)}}
.f-comment_card .w{{display:inline-block;animation:cc-word .34s var(--ease) calc(.22s + var(--i) * var(--gap)) both}}
.f-comment_card .w.hot{{color:var(--accent)}}
.f-comment_card .note{{display:block;margin-top:calc(16px * var(--scale));font-size:calc(30px * var(--scale));color:var(--soft);animation:cc-word .34s var(--ease) var(--late) both}}
@keyframes cc-pop{{0%{{opacity:0;transform:scale(.8) translateY(calc(28px * var(--scale)))}}46%{{opacity:1;transform:scale(1.025)}}72%{{transform:scale(.994)}}100%{{opacity:1;transform:none}}}}
@keyframes cc-word{{from{{opacity:0;transform:translateY(.24em)}}to{{opacity:1;transform:none}}}}
""",
    ),
    Format(
        id="comment_match_cut",
        title="Comment match cut",
        summary="A sentence builds word by word while the card around it cuts on every beat.",
        group="proof",
        jobs=("hook", "prove"),
        slots=(Slot("words", kind="list", limit=8, each=12),),
        example={"words": [{"text": "Honestly"}, {"text": "this"}, {"text": "changed"}, {"text": "how"}, {"text": "we"}, {"text": "edit"}]},
        draw=_match_cut,
        seconds=3.0,
        shortest=1.5,
        longest=8.0,
        tall=645,
        sound="tick",
        css="""
.f-comment_match_cut .stage{position:relative;height:calc(580px * var(--scale));max-width:calc(880px * var(--scale));margin:0 auto}
.f-comment_match_cut .card{position:absolute;inset:0;display:flex;flex-direction:column;justify-content:space-between;padding:calc(40px * var(--scale)) calc(44px * var(--scale));border-radius:calc(32px * var(--scale));visibility:hidden;color:var(--ink);background:var(--surface);box-shadow:0 0 0 1px rgba(255,255,255,.1) inset,0 calc(30px * var(--scale)) calc(80px * var(--scale)) rgba(0,0,0,.42);animation:mc-on .01s step-end var(--at) forwards,mc-off .01s step-end var(--to,999s) forwards,mc-punch .24s var(--ease) var(--at) both}
.f-comment_match_cut .card.alt{border-radius:calc(10px * var(--scale))}
.f-comment_match_cut .card.light{color:var(--panel);background:var(--ink)}
.f-comment_match_cut .card.lit{color:var(--panel);background:var(--accent)}
.f-comment_match_cut .top,.f-comment_match_cut .foot{display:flex;align-items:center;gap:calc(18px * var(--scale));min-height:calc(66px * var(--scale))}
.f-comment_match_cut .flip{flex-direction:row-reverse}
.f-comment_match_cut .ava{flex:none;width:calc(66px * var(--scale));height:calc(66px * var(--scale));border-radius:50%;background:currentColor;opacity:.22}
.f-comment_match_cut .card.alt .ava{border-radius:calc(14px * var(--scale))}
.f-comment_match_cut .bar{display:block;height:calc(18px * var(--scale));border-radius:9px;background:currentColor;opacity:.16}
.f-comment_match_cut p{margin:0;font-size:calc(var(--size) * var(--scale));font-weight:700;line-height:1.04;letter-spacing:-.035em}
.f-comment_match_cut .off{visibility:hidden}
@keyframes mc-on{to{visibility:visible}}
@keyframes mc-off{to{visibility:hidden}}
@keyframes mc-punch{from{transform:scale(1.035)}to{transform:none}}
""",
    ),
    Format(
        id="follow_prompt",
        title="Follow prompt",
        summary="A cursor glides in and presses a neutral follow button, which ticks over.",
        group="proof",
        jobs=("close",),
        slots=(Slot("name", limit=22), Slot("text", limit=10), Slot("done", limit=10), Slot("label", limit=30, required=False)),
        example={"name": "Northlight Films", "text": "Follow", "done": "Following", "label": "New cuts every Tuesday"},
        draw=_follow,
        seconds=3.4,
        shortest=2.4,
        longest=8.0,
        tall=335,
        sound="click",
        css=f"""
.f-follow_prompt .set{{display:flex;justify-content:center}}
.f-follow_prompt .card{{position:relative;display:flex;align-items:center;gap:calc(22px * var(--scale));width:100%;max-width:calc(900px * var(--scale));padding:calc(26px * var(--scale)) calc(28px * var(--scale));border-radius:calc(30px * var(--scale));{CARD};animation:fp-card .6s var(--ease) both}}
.f-follow_prompt .disc{{flex:none;display:grid;place-items:center;width:calc(96px * var(--scale));height:calc(96px * var(--scale));border-radius:50%;font-size:calc(36px * var(--scale));font-weight:600;color:var(--ink)}}
.f-follow_prompt .id{{flex:1;min-width:0;display:flex;flex-direction:column;gap:calc(3px * var(--scale))}}
.f-follow_prompt .name{{font-size:calc(40px * var(--scale));font-weight:600;letter-spacing:-.02em;line-height:1.1;color:var(--ink)}}
.f-follow_prompt .sub{{font-size:calc(30px * var(--scale));line-height:1.14;color:var(--soft)}}
.f-follow_prompt .spot{{position:relative;flex:none}}
.f-follow_prompt .btn{{position:relative;display:grid;place-items:center;height:calc(84px * var(--scale));min-width:calc(236px * var(--scale));padding:0 calc(32px * var(--scale));border-radius:999px;font-size:calc(36px * var(--scale));font-weight:600;letter-spacing:-.015em;color:var(--panel);background:var(--accent);animation:fp-press .38s var(--ease) 1.38s both,fp-flip .01s step-end 1.42s forwards}}
.f-follow_prompt .a,.f-follow_prompt .b{{grid-area:1/1;display:inline-flex;align-items:center;gap:calc(10px * var(--scale));white-space:nowrap}}
.f-follow_prompt .a{{animation:fp-out .01s step-end 1.42s forwards}}
.f-follow_prompt .b{{opacity:0;color:var(--ink);animation:fp-in .3s var(--ease) 1.42s forwards}}
.f-follow_prompt .b i{{display:block;width:calc(38px * var(--scale));height:calc(38px * var(--scale))}}
.f-follow_prompt .b svg{{display:block;width:100%;height:100%;fill:none;stroke:var(--accent);stroke-width:3.4;stroke-linecap:round;stroke-linejoin:round}}
.f-follow_prompt .ring{{position:absolute;left:50%;top:50%;width:calc(84px * var(--scale));height:calc(84px * var(--scale));margin:calc(-42px * var(--scale));border-radius:50%;border:calc(3px * var(--scale)) solid var(--accent);opacity:0;animation:fp-ring .7s var(--ease) 1.42s both}}
.f-follow_prompt .fly-x{{position:absolute;left:58%;top:58%;width:calc(70px * var(--scale));height:calc(88px * var(--scale));animation:fp-x 1.1s cubic-bezier(.5,0,.14,1) .28s both,fp-leave .5s cubic-bezier(.5,0,.8,.4) calc(var(--dur) - .7s) forwards}}
.f-follow_prompt .fly-y{{width:100%;height:100%;animation:fp-y 1.1s cubic-bezier(.3,.4,.2,1) .28s both}}
.f-follow_prompt .tap{{width:100%;height:100%;transform-origin:12% 8%;animation:fp-tap .34s var(--ease) 1.34s both}}
.f-follow_prompt .tap svg{{display:block;width:100%;height:100%;fill:var(--ink);stroke:var(--panel);stroke-width:3.5;stroke-linejoin:round;filter:drop-shadow(0 calc(6px * var(--scale)) calc(10px * var(--scale)) rgba(0,0,0,.42))}}
@keyframes fp-card{{from{{opacity:0;transform:translateY(calc(30px * var(--scale))) scale(.97)}}to{{opacity:1;transform:none}}}}
@keyframes fp-x{{from{{transform:translateX(calc(-300px * var(--scale)));opacity:0}}12%{{opacity:1}}to{{transform:none;opacity:1}}}}
@keyframes fp-y{{from{{transform:translateY(calc(250px * var(--scale)))}}to{{transform:none}}}}
@keyframes fp-tap{{0%{{transform:none}}36%{{transform:scale(.8)}}100%{{transform:none}}}}
@keyframes fp-press{{0%{{transform:scale(.93)}}100%{{transform:none}}}}
@keyframes fp-flip{{to{{background:rgba(255,255,255,.1)}}}}
@keyframes fp-out{{to{{opacity:0}}}}
@keyframes fp-in{{from{{opacity:0;transform:translateY(.3em)}}to{{opacity:1;transform:none}}}}
@keyframes fp-ring{{0%{{opacity:.9;transform:scale(.8)}}100%{{opacity:0;transform:scale(2.5)}}}}
@keyframes fp-leave{{to{{translate:calc(240px * var(--scale)) calc(200px * var(--scale));opacity:0}}}}
""",
    ),
    Format(
        id="before_after",
        title="Before and after",
        summary="A divider wipes across and leaves two results side by side.",
        group="proof",
        jobs=("prove",),
        slots=(Slot("before", limit=10), Slot("after", limit=10), Slot("label", limit=46, required=False), Slot("before_label", limit=16, required=False), Slot("after_label", limit=16, required=False)),
        example={"before": "14 hours", "after": "3 hours", "label": "Time to a first cut, sample project"},
        draw=_before_after,
        seconds=4,
        shortest=2.2,
        longest=10.0,
        tall=515,
        sound="whoosh",
        css=f"""
.f-before_after .card{{max-width:calc(1120px * var(--scale));margin:0 auto;padding:calc(20px * var(--scale));border-radius:calc(34px * var(--scale));{CARD};animation:ba-card .6s var(--ease) both}}
.f-before_after .over{{margin:calc(10px * var(--scale)) calc(10px * var(--scale)) calc(24px * var(--scale));font-size:calc(36px * var(--scale));font-weight:500;letter-spacing:-.012em;line-height:1.15;color:var(--ink);text-align:center}}
.f-before_after .split{{position:relative;height:calc(330px * var(--scale));overflow:hidden;border-radius:calc(20px * var(--scale));background:rgba(255,255,255,.05)}}
.f-before_after .pane{{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:calc(10px * var(--scale));text-align:center}}
.f-before_after .was{{padding-right:50%;color:var(--soft)}}
.f-before_after .now{{padding-left:50%;color:var(--panel);background:var(--accent);clip-path:inset(0 0 0 100%);animation:ba-wipe 1.05s cubic-bezier(.7,0,.16,1) .62s both}}
.f-before_after .tag{{font-size:calc(32px * var(--scale));font-weight:500}}
.f-before_after b{{font-size:calc(var(--size) * var(--scale));font-weight:700;letter-spacing:-.04em;line-height:1;white-space:nowrap}}
.f-before_after .now b{{animation:ba-lift .5s var(--ease) 1.36s both}}
.f-before_after .bar{{position:absolute;top:0;bottom:0;left:100%;width:calc(4px * var(--scale));margin-left:calc(-2px * var(--scale));background:var(--ink);animation:ba-bar 1.05s cubic-bezier(.7,0,.16,1) .62s both}}
.f-before_after .bar i{{position:absolute;left:50%;top:50%;display:grid;place-items:center;width:calc(72px * var(--scale));height:calc(72px * var(--scale));margin:calc(-36px * var(--scale));border-radius:50%;background:var(--ink);box-shadow:0 calc(8px * var(--scale)) calc(22px * var(--scale)) rgba(0,0,0,.34)}}
.f-before_after .bar svg{{width:66%;height:66%;fill:none;stroke:var(--panel);stroke-width:4;stroke-linecap:round;stroke-linejoin:round}}
@keyframes ba-card{{from{{opacity:0;transform:translateY(calc(28px * var(--scale)))}}to{{opacity:1;transform:none}}}}
@keyframes ba-wipe{{0%{{clip-path:inset(0 0 0 100%)}}78%{{clip-path:inset(0 0 0 46%)}}100%{{clip-path:inset(0 0 0 50%)}}}}
@keyframes ba-bar{{0%{{left:100%}}78%{{left:46%}}100%{{left:50%}}}}
@keyframes ba-lift{{from{{transform:translateY(calc(18px * var(--scale)));opacity:.3}}to{{transform:none;opacity:1}}}}
""",
    ),
    Format(
        id="avatar_cloud",
        title="Avatar cloud",
        summary="A crowd of discs gathers around the number, to show the scale behind it.",
        group="proof",
        jobs=("prove", "close"),
        slots=(Slot("value", limit=9), Slot("label", limit=40), Slot("names", kind="list", limit=43, required=False)),
        example={"value": "2,400+", "label": "people on the sample list"},
        draw=_cloud,
        seconds=3.4,
        shortest=2.0,
        longest=10.0,
        tall=640,
        sound="rise",
        css="""
.f-avatar_cloud .set{max-width:calc(880px * var(--scale));margin:0 auto}
.f-avatar_cloud .head{display:flex;flex-direction:column;align-items:center;margin-bottom:calc(30px * var(--scale));text-align:center;text-shadow:0 calc(4px * var(--scale)) calc(26px * var(--scale)) rgba(0,0,0,.5)}
.f-avatar_cloud .num{font-size:calc(150px * var(--scale));font-weight:700;letter-spacing:-.045em;line-height:1;color:var(--ink)}
.f-avatar_cloud .lab{margin-top:calc(8px * var(--scale));font-size:calc(42px * var(--scale));font-weight:500;letter-spacing:-.015em;line-height:1.14;color:var(--ink)}
.f-avatar_cloud .crowd{position:relative;height:calc(390px * var(--scale))}
.f-avatar_cloud .face{--s:calc(90px * var(--scale));position:absolute;display:grid;place-items:center;width:var(--s);height:var(--s);margin:calc(var(--s) / -2);border-radius:50%;font-size:calc(var(--s) * .36);font-weight:600;color:var(--ink);box-shadow:0 0 0 calc(3px * var(--scale)) color-mix(in srgb,var(--panel) 86%,transparent),0 calc(6px * var(--scale)) calc(16px * var(--scale)) rgba(0,0,0,.3);animation:ac-in .85s var(--ease) var(--at) both}
.f-avatar_cloud .face.plain{background-image:radial-gradient(circle at 50% 38%,rgba(255,255,255,.4) 0 19%,transparent 20%),radial-gradient(ellipse 36% 30% at 50% 94%,rgba(255,255,255,.4) 0 98%,transparent 100%)}
@keyframes ac-in{from{opacity:0;transform:translate(var(--dx),var(--dy)) scale(.4)}to{opacity:1;transform:none}}
"""
        + _scoped("avatar_cloud", WHEEL),
    ),
    Format(
        id="press_strip",
        title="Press strip",
        summary="A quiet row of mastheads for the outlets that covered it.",
        group="proof",
        jobs=("prove",),
        slots=(Slot("label", limit=30), Slot("items", kind="list", limit=4, each=20)),
        example={"label": "As covered in", "items": [{"text": "The Larkmoor Post"}, {"text": "Harbour Ledger"}, {"text": "Quarterly Almanack"}]},
        draw=_press,
        seconds=3.2,
        shortest=2.0,
        longest=10.0,
        tall=500,
        sound=None,
        fonts=("serif",),
        css=f"""
.f-press_strip .set{{max-width:calc(1500px * var(--scale));margin:0 auto;padding:calc(28px * var(--scale)) calc(36px * var(--scale)) calc(32px * var(--scale));border-radius:calc(30px * var(--scale));{CARD};animation:ps-card .6s var(--ease) both}}
.f-press_strip .over{{display:flex;align-items:center;gap:calc(20px * var(--scale));margin:0 0 calc(16px * var(--scale));font-size:calc(31px * var(--scale));color:var(--soft);white-space:nowrap}}
.f-press_strip .over i{{flex:1;height:1px;background:rgba(255,255,255,.17)}}
.f-press_strip .row{{display:flex;flex-wrap:wrap;justify-content:center;gap:calc(4px * var(--scale)) calc(48px * var(--scale))}}
.f-press_strip .name{{display:block;overflow:hidden;font-family:var(--serif);font-size:calc(66px * var(--scale));line-height:1.18;letter-spacing:-.012em;color:var(--ink);white-space:nowrap}}
.f-press_strip .name span{{display:block;animation:ps-up .75s var(--ease) var(--at) both}}
@keyframes ps-card{{from{{opacity:0;transform:translateY(calc(26px * var(--scale)))}}to{{opacity:1;transform:none}}}}
@keyframes ps-up{{from{{transform:translateY(102%)}}to{{transform:none}}}}
""",
    ),
)
