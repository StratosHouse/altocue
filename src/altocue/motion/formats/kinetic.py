"""Type that moves. Spoken words become the picture."""

from __future__ import annotations

import hashlib
import math
from dataclasses import replace

from . import Format, Slot, kit

GLYPHS = "ABDEFGHKLMNPRSTUVXZ0123456789#%&/"
DRUM = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789&/.,:-"
DROP = "filter:drop-shadow(0 1px 2px rgba(0,0,0,.42)) drop-shadow(0 4px 12px rgba(0,0,0,.38)) drop-shadow(0 14px 44px rgba(0,0,0,.5))"
SCRIM = (
    "position:absolute;inset:-70% -26% -76%;background:radial-gradient(closest-side,rgba(6,10,14,.36),rgba(6,10,14,.25) 46%,rgba(6,10,14,.08) 76%,rgba(6,10,14,0));"
    "animation:kscrim .36s ease-out both,kscrimoff .34s ease-in calc(var(--dur) - .36s) forwards"
)
SCRIM_KEYS = "@keyframes kscrim{from{opacity:0}to{opacity:1}}\n@keyframes kscrimoff{to{opacity:0}}\n"
PAUSE = {".": 4.0, "!": 4.0, "?": 4.0, ",": 2.6, ";": 2.6, ":": 2.6, " ": 1.3}
WALL_ROWS = 29
WORLDS = ("paper", "code", "note", "ledger", "poster", "tint", "book")
PROSE = (
    "The morning began with a short list and a long walk to the station, where the first train was already late and nobody seemed to mind",
    "Every good plan starts as a rough sketch on the back of an envelope, then survives three honest questions from someone who has done it before",
    "By the end of the week the numbers had settled, the boxes were packed, and the whole team had finally agreed on what came next",
    "Nothing about the old method was wrong, it was only slow, and slow things have a habit of quietly becoming the way it has always been done",
    "The notes from that afternoon filled two pages, mostly questions, a few drawings, and one line underlined twice in blue ink",
    "She read the letter again by the window, folded it along the same soft creases, and decided the answer could wait until the rain had stopped",
    "Small changes rarely make the front page, but they add up in the margins, one careful edit at a time, until the whole shape is different",
)
CURVE = "M-300 360C-110 360 35 120 235 120S450 440 650 440S1040 250 1240 250"
CURVE_LENGTH, CURVE_LEFT, CURVE_MIDDLE, CURVE_RIGHT = 1771, 339, 876, 1397  # distances along the path above, to the stage's left edge, middle and right edge


def _plain(text: object) -> str:
    return str(text).replace("*", "")


def _stream(text: str, count: int) -> list[int]:
    """Bytes drawn from the copy, so anything that looks random is the same in every render."""
    out, block = b"", 0
    while len(out) < count:
        out += hashlib.sha256(f"{text}/{block}".encode()).digest()
        block += 1
    return list(out[:count])


def _fit(longest: float, largest: int, width: int = 880, em: float = 0.52) -> int:
    """A size that sets the longest line across the panel, capped at `largest`. Pixels at full scale."""
    return min(largest, int(width / max(longest * em, 0.1)))


def _lines(words: list[str], budget: int) -> list[list[int]]:
    """Group word positions into lines of at most `budget` characters."""
    lines: list[list[int]] = []
    used = 0
    for index, word in enumerate(words):
        if lines and used + 1 + len(word) <= budget:
            lines[-1].append(index)
            used += 1 + len(word)
        else:
            lines.append([index])
            used = len(word)
    return lines


def _word_slam(item: dict) -> str:
    """`gap` sets the seconds between words. `lead` starts the run that far in, so a hook is on screen from the first frame."""
    count = max(len(kit.tokens(item["text"])), 1)
    gap = float(item["gap"]) if item.get("gap") is not None else min(kit.seconds(item) * 0.62 / count, 0.3)
    lead = f";--lead:{float(item['lead']):.3f}s" if item.get("lead") is not None else ""
    return f'<h1 class="lift" style="--gap:{gap:.3f}s{lead}">{kit.words(item["text"])}</h1>'


def _decode(item: dict) -> str:
    """Each letter is a strip of glyphs that steps to the real one. The glyphs come from the copy, so a render never changes."""
    seed = hashlib.sha256(item["text"].encode()).digest()
    cells, index = [], 0
    for word, hot in kit.tokens(item["text"]):
        strips = []
        for char in word:
            count = 5 + index % 4
            filler = "".join(f"<i>{GLYPHS[seed[(index + n) % len(seed)] % len(GLYPHS)]}</i>" for n in range(count))
            strips.append(f'<span class="cell"><span class="strip" style="--n:{count};--i:{index}">{filler}<i>{kit.esc(char)}</i></span></span>')
            index += 1
        cells.append(f'<span class="word{" hot" if hot else ""}">{"".join(strips)}</span>')
        index += 1
    label = f'<p>{kit.esc(item["label"])}</p>' if item.get("label") else ""
    return f'<h1 class="lift">{" ".join(cells)}</h1>{label}'


def _marker(item: dict) -> str:
    parts = str(item["text"]).split("*")
    body = "".join(f'<mark><i></i><span>{kit.esc(part)}</span></mark>' if i % 2 else kit.esc(part) for i, part in enumerate(parts))
    return f"<p>{body}</p>"


def _mask_rise(item: dict) -> str:
    """Lines stack tight unless they are timed to speech, when each one waits for its word."""
    lines = kit.rows(item, "lines")
    timed = any("at" in line for line in lines)
    size = _fit(max((len(_plain(line["text"])) for line in lines), default=1), 150)
    spans = []
    for index, line in enumerate(lines):
        delay = line["delay"] if timed else index * 0.1
        spans.append(f'<span class="line"><span class="up" style="--d:{delay:.3f}s;--i:{index}">{kit.emphasis(line["text"])}</span></span>')
    return f'<div class="lock"><i class="scrim"></i><h1 style="--size:{size}px">{"".join(spans)}</h1><i class="rule"></i></div>'


def _letter_cascade(item: dict) -> str:
    parts = kit.tokens(item["text"])
    count = sum(len(word) for word, _ in parts)
    size = _fit(max(max((len(word) for word, _ in parts), default=1), count / 2), 150, em=0.56)
    return f'<div class="lock"><i class="scrim"></i><h1 style="--size:{size}px;--gap:{min(0.034, 0.5 / max(count, 1)):.3f}s">{kit.letters(item["text"])}</h1></div>'


def _typewriter(item: dict) -> str:
    """Each key lands on its own beat, with pauses after punctuation. The beats come from the copy."""
    text = str(item["text"])
    chars = [(char, bool(part % 2)) for part, piece in enumerate(text.split("*")) for char in piece]
    seed = _stream(text, len(chars))
    lead = 0.36
    beats = [(0.7 + seed[i] / 255 * 0.8) * PAUSE.get(chars[i - 1][0] if i else "", 1.0) for i in range(len(chars))]
    step = min(max(kit.seconds(item) * 0.66 - lead, 0.3) / max(sum(beats), 0.1), 0.065)
    times, at = [], lead
    for beat in beats:
        times.append(at)
        at += beat * step
    cells = []
    for index, (char, hot) in enumerate(chars):
        last = index == len(chars) - 1
        hold = times[index + 1] - times[index] if not last else 0
        cls = "k" + (" hot" if hot else "") + (" fin" if last else "")
        cells.append(f'<span class="{cls}" style="--t:{times[index]:.3f}s;--l:{hold:.3f}s">{kit.esc(char)}<i class="car"></i></span>')
    size = 92 if len(chars) <= 20 else 78 if len(chars) <= 42 else 66
    return f'<p style="--size:{size}px"><span class="all"><i class="car boot" style="--t:0s;--l:{lead:.3f}s"></i>{"".join(cells)}</span></p>'


def _text_match(item: dict) -> str:
    """The word never moves. Seven generated pages cut around it, faster and faster, and the last one holds."""
    word = kit.esc(_plain(item["text"]))
    seed = _stream(_plain(item["text"]), len(WORLDS))
    gaps = [0.8**n for n in range(len(WORLDS) - 1)]
    stretch = kit.seconds(item) * 0.62 / sum(gaps)
    pages, at = [], 0.0
    for index, world in enumerate(WORLDS):
        turn = seed[0] + index
        said = PROSE[turn % len(PROSE)].split()
        cut = 6 + seed[index] % 6
        left, right = kit.esc(" ".join(said[cut - 5 : cut])), kit.esc(" ".join(said[cut : cut + 5]))
        above = kit.esc(". ".join(PROSE[(turn + n) % len(PROSE)] for n in range(1, 8)) + ".")
        below = kit.esc(". ".join(PROSE[(turn + n) % len(PROSE)] for n in range(4, 11)) + ".")
        pages.append(
            f'<div class="page {world}" style="--t:{at:.3f}s"><div class="sheet"><div class="above"><p>{above}</p></div>'
            f'<div class="row"><span class="l"><span>{left}</span></span><span class="gap">{word}</span><span class="r"><span>{right}</span></span></div>'
            f'<div class="below"><p>{below}</p></div></div></div>'
        )
        at += gaps[index] * stretch if index < len(gaps) else 0
    size = _fit(len(_plain(item["text"])), 150, width=760, em=0.58)
    return f'<div class="scene" style="--key:{size}px">{"".join(pages)}<b class="key">{word}</b></div>'


def _weight_flex(item: dict) -> str:
    """Each word swells from hairline to heavy on its own moment, so it can follow the voice."""
    rows = kit.rows(item, "words")
    plain = [_plain(row["text"]) for row in rows]
    groups = _lines(plain, 11)
    size = _fit(max((sum(len(plain[i]) + 1 for i in group) - 1 for group in groups), default=1), 170, em=0.62)
    lines = []
    for group in groups:
        spans = []
        for index in group:
            gap = min(0.05, 0.4 / max(len(plain[index]), 1))
            spans.append(f'<span class="flex" style="--d:{rows[index]["delay"]:.3f}s;--gap:{gap:.3f}s">{kit.letters(rows[index]["text"])}</span>')
        lines.append(f'<span class="line">{" ".join(spans)}</span>')
    return f'<div class="lock"><i class="scrim"></i><h1 style="--size:{size}px">{"".join(lines)}</h1></div>'


def _text_curve(item: dict) -> str:
    """Letters are HTML, each a fixed advance apart on the same path, so the whole line can travel it. Mono keeps that advance true."""
    chars = [(char, bool(part % 2)) for part, piece in enumerate(str(item["text"]).split("*")) for char in piece]
    size = min(118, int(960 / max(len(chars) * 0.56, 1)))
    advance = size * 0.56
    length = advance * len(chars)
    rest = CURVE_MIDDLE - length / 2
    cells = []
    for index, (char, hot) in enumerate(chars):
        if char != " ":
            cells.append(f'<span class="c{" hot" if hot else ""}" style="--x:{(index + 0.5) * advance:.1f}">{kit.esc(char)}</span>')
    return (
        f'<div class="ride"><i class="scrim"></i><div class="stage" style="--size:{size}px;--from:{CURVE_LEFT - length - 60:.1f};--rest:{rest:.1f};--drift:{rest + 22:.1f};--to:{CURVE_RIGHT + 60:.1f}">'
        f'<div class="line"><svg viewBox="0 0 890 470" aria-hidden="true"><path d="{CURVE}"/></svg></div>'
        f'{"".join(cells)}<i class="tip" style="--x:{length + size * 0.34:.1f}"></i></div></div>'
    )


def _word_swap(item: dict) -> str:
    rows = kit.rows(item, "words")
    size = _fit(max((len(_plain(row["text"])) for row in rows), default=1), 150, em=0.55)
    cells = []
    for index, row in enumerate(rows):
        last = index == len(rows) - 1
        leaves = "calc(var(--dur) - .48s)" if last else f'{rows[index + 1]["delay"]:.3f}s'
        cells.append(f'<span class="w{" last" if last else ""}" style="--in:{row["delay"]:.3f}s;--out:{leaves}">{kit.esc(_plain(row["text"]))}</span>')
    lead = f'<span class="lead"><span>{kit.emphasis(item["lead"])}</span></span>' if item.get("lead") else ""
    return f'<div class="lock"><i class="scrim"></i><h1 style="--size:{size}px">{lead}<span class="slot">{"".join(cells)}</span></h1></div>'


def _strike_replace(item: dict) -> str:
    """The old words get struck, the frame holds a beat on the mistake, then the better words land."""
    rows = kit.rows(item, "swap")
    old = _plain(rows[0]["text"]) if rows else ""
    new = _plain(rows[1]["text"]) if len(rows) > 1 else ""
    timed = len(rows) > 1 and "at" in rows[1]
    land = max(rows[1]["delay"], 1.0) if timed else max(kit.seconds(item) * 0.44, 1.0)
    size = _fit(max(len(old), len(new), 1), 136, em=0.55)
    lead = f'<span class="m lead"><span class="t" style="--i:0">{kit.esc(item["lead"])}</span></span>' if item.get("lead") else ""
    fresh = f'<span class="m new"><span class="t" style="--i:2">{kit.esc(new)}</span></span>' if new else ""
    return (
        f'<div class="lock"><i class="scrim"></i><h1 style="--size:{size}px;--hit:{land - 0.56:.3f}s;--land:{land:.3f}s">{lead}'
        f'<span class="m old"><span class="t" style="--i:1"><span class="hit">{kit.esc(old)}<i class="bar"></i></span></span></span>{fresh}</h1></div>'
    )


def _flap_cell(char: str, order: int, delay: float, turns: int) -> str:
    """Four halves, each a strip of the drum. The strips step while the two flaps swing, so one cell runs on four elements."""
    if char == " ":
        return '<span class="cell"><span class="half top"></span><span class="half bot"></span></span>'
    place = DRUM.find(char) if char in DRUM else order % len(DRUM)
    glyphs = [" "] + [DRUM[(place - back) % len(DRUM)] for back in range(turns - 1, 0, -1)] + [char, char]
    strip = kit.esc("\n".join(glyphs))
    return (
        f'<span class="cell" style="--n:{turns};--d:{delay:.3f}s">'
        f'<span class="half top"><span class="s next">{strip}</span></span><span class="half bot"><span class="s">{strip}</span></span>'
        f'<span class="half top fa"><span class="s">{strip}</span></span><span class="half bot fb"><span class="s next">{strip}</span></span></span>'
    )


def _split_flap(item: dict) -> str:
    rows = kit.rows(item, "lines")
    texts = [_plain(row["text"]).upper() for row in rows]
    columns = max(min(max((len(text) for text in texts), default=8), 13), 8)
    gap, pad = 5, 18
    width = min(96, (896 - 2 * pad - gap * (columns - 1)) // columns)
    body = []
    for line, (row, text) in enumerate(zip(rows, texts, strict=True)):
        seed = _stream(f"{text}/{line}", columns)
        cells = [_flap_cell(text[col] if col < len(text) else " ", col, 0.26 + row["delay"] + col * 0.034, 4 + seed[col] % 5) for col in range(columns)]
        body.append(f'<span class="row">{"".join(cells)}</span>')
    return f'<div class="board" style="--w:{width}px;--h:{round(width * 1.46)}px">{"".join(body)}</div>'


def _type_wall(item: dict) -> str:
    """An odd number of repeats puts one whole phrase dead centre mid-clip, and the bright row barely drifts off it."""
    plain = _plain(item["text"])
    size = min(150, int(820 / max(len(plain) * 0.66, 1)))
    count = math.ceil(5200 / ((len(plain) * 0.66 + 1.2) * size)) | 1
    run = "<i></i>".join([kit.esc(plain)] * count)
    rows = []
    for index in range(WALL_ROWS):
        side = 1 if index % 2 else -1
        hot = index == WALL_ROWS // 2
        speed = 34 if hot else 130 + index % 3 * 50
        shift = 0 if hot else (index * 233) % 700 - 350
        rows.append(f'<div class="row{" hot" if hot else ""}" style="--i:{index};--s:{side};--g:{speed * side};--o:{shift}"><span class="run">{run}</span></div>')
    return f'<div class="veil"></div><div class="view"><div class="plane" style="--size:{size}px">{"".join(rows)}</div></div>'


def _ticker(item: dict) -> str:
    rows = kit.rows(item, "items")
    travel = 190 * kit.seconds(item)
    unit = max(sum(len(_plain(row["text"])) * 31 + 110 for row in rows), 200)
    run = "".join(f'<span class="item">{kit.emphasis(row["text"])}</span><i class="pip"></i>' for row in rows) * (math.ceil((1200 + travel) / unit) + 1)
    tag = f'<b class="tag">{kit.esc(item["label"])}</b>' if item.get("label") else ""
    return f'<div class="band">{tag}<div class="win"><div class="run" style="--go:{travel:.0f}">{run}</div></div></div>'


def _liquid_type(item: dict) -> str:
    """Letters pull out of one drop and run back into it. Filtered copy for the flow, a clean copy for the hold."""
    chars = [(char, bool(part % 2)) for part, piece in enumerate(str(item["text"]).split("*")) for char in piece]
    middle = (len(chars) - 1) / 2
    spell = "".join(
        f'<span class="c{" hot" if hot else ""}" style="--o:{(middle - index) * 0.58:.2f}em;--r:{abs(index - middle) / max(middle, 1):.2f}">{kit.esc(char) if char != " " else "&nbsp;"}</span>'
        for index, (char, hot) in enumerate(chars)
    )
    size = _fit(len(chars), 250, width=840, em=0.6)
    goo = (
        '<svg class="defs" width="0" height="0" aria-hidden="true"><filter id="liquid-type" x="-30%" y="-70%" width="160%" height="240%" color-interpolation-filters="sRGB">'
        '<feGaussianBlur stdDeviation="10"/><feColorMatrix values="1 0 0 0 0 0 1 0 0 0 0 0 1 0 0 0 0 0 26 -11"/></filter></svg>'
    )
    return f'{goo}<div class="stage" style="--size:{size}px"><i class="scrim"></i><h1 class="goo"><i class="drop"></i>{spell}</h1><h1 class="ink">{spell}</h1></div>'


def _spotlight(item: dict) -> str:
    size = _fit(max(len(_plain(item["text"])) / 1.8, 6), 180, width=860, em=0.54)
    return f'<div class="dark"></div><h1 class="lit" style="--size:{size}px"><span>{kit.emphasis(item["text"])}</span></h1>'


FORMATS = (
    Format(
        id="word_slam",
        title="Word slam",
        summary="Big words hit one at a time, each landing with weight.",
        group="kinetic",
        jobs=("hook",),
        slots=(Slot("text", limit=48), Slot("gap", kind="number", required=False), Slot("lead", kind="number", required=False)),
        example={"text": "Stop *editing* by hand"},
        draw=_word_slam,
        seconds=2.4,
        tall=460,
        sound="thud",
        css="""
.f-word_slam h1{margin:0;font-size:calc(132px * var(--scale));font-weight:800;line-height:.96;letter-spacing:-.045em;text-wrap:balance}
.f-word_slam .w{display:inline-block;animation:slam .36s cubic-bezier(.18,1.35,.32,1) both;animation-delay:calc(var(--i) * var(--gap) - var(--lead, 0s))}
.f-word_slam .w.hot{color:var(--accent)}
@keyframes slam{from{opacity:0;transform:scale(1.7);filter:blur(10px)}55%{opacity:1;filter:blur(0)}to{opacity:1;transform:none;filter:blur(0)}}
""",
    ),
    Format(
        id="decode",
        title="Decode",
        summary="Random glyphs settle letter by letter into the real word.",
        group="kinetic",
        jobs=("hook", "prove"),
        slots=(Slot("text", limit=28), Slot("label", limit=60, required=False)),
        example={"text": "*SIGNAL* FOUND", "label": "Every word checked against the audio"},
        draw=_decode,
        seconds=2.8,
        tall=360,
        sound="tick",
        fonts=("mono",),
        css="""
.f-decode h1{margin:0;display:flex;flex-wrap:wrap;gap:0 .5em;font-family:var(--mono);font-size:calc(104px * var(--scale));font-weight:600;line-height:1.1;letter-spacing:-.01em}
.f-decode .word{display:inline-flex}
.f-decode .word.hot{color:var(--accent)}
.f-decode .cell{display:inline-block;height:1.1em;overflow:hidden}
.f-decode .strip{display:block;animation:decode .62s steps(var(--n)) both;animation-delay:calc(var(--i) * .028s)}
.f-decode .strip i{display:block;height:1.1em;font-style:normal}
.f-decode p{margin:.5em 0 0;font-size:calc(36px * var(--scale));color:var(--soft);animation:rise .42s .5s var(--ease) both}
@keyframes decode{from{transform:translateY(0)}to{transform:translateY(calc(var(--n) * -1.1em))}}
""",
    ),
    Format(
        id="marker",
        title="Marker sweep",
        summary="A highlighter wipes behind the phrase that matters.",
        group="kinetic",
        jobs=("prove", "explain"),
        slots=(Slot("text", limit=90),),
        example={"text": "Most of your runway goes to *tools nobody uses*"},
        draw=_marker,
        seconds=3.0,
        tall=340,
        css="""
.f-marker p{margin:0;font-size:calc(74px * var(--scale));font-weight:600;line-height:1.16;letter-spacing:-.03em;color:var(--ink);text-shadow:0 2px 6px rgba(0,0,0,.34),0 10px 44px rgba(0,0,0,.5);animation:rise .42s var(--ease) both}
.f-marker mark{position:relative;background:none;color:inherit;white-space:nowrap}
.f-marker mark i{position:absolute;z-index:-1;inset:.06em -.12em .02em;border-radius:.1em;background:var(--accent);transform:scaleX(0);transform-origin:0 50%;animation:sweep .46s .36s cubic-bezier(.65,0,.2,1) both}
.f-marker mark span{animation:inkflip .1s .5s linear both}
@keyframes sweep{to{transform:none}}
@keyframes inkflip{to{color:var(--panel);text-shadow:none}}
""",
    ),
    Format(
        id="mask_rise",
        title="Mask rise",
        summary="Lines climb out from behind a hidden baseline and leave the same way.",
        group="kinetic",
        jobs=("hook", "explain"),
        slots=(Slot("lines", kind="list", limit=4, each=16),),
        example={"lines": ["Small teams", "ship *faster*"]},
        draw=_mask_rise,
        seconds=2.8,
        shortest=1.8,
        tall=500,
        sound="rise",
        leaves=False,
        css="""
.f-mask_rise .lock{position:relative;display:inline-block;max-width:100%}
.f-mask_rise .scrim{$scrim}
.f-mask_rise h1{margin:0;font-size:calc(var(--size) * var(--scale));font-weight:700;line-height:1;letter-spacing:-.048em;$drop}
.f-mask_rise .line{display:block;overflow:hidden;padding:.05em .06em .15em 0;margin-bottom:-.13em}
.f-mask_rise .up{display:block;transform-origin:0 100%;animation:mrin .92s var(--ease) var(--d) both,mrout .42s cubic-bezier(.7,0,.84,0) calc(var(--dur) - .5s + var(--i) * .04s) forwards}
.f-mask_rise .rule{display:block;height:calc(7px * var(--scale));margin-top:calc(30px * var(--scale));background:var(--accent);clip-path:inset(0 100% 0 0);animation:mrrule .84s var(--ease) both,mrruleout .42s cubic-bezier(.7,0,.84,0) calc(var(--dur) - .46s) forwards}
@keyframes mrin{from{transform:translateY(132%) rotate(4deg)}to{transform:none}}
@keyframes mrout{to{transform:translateY(-132%)}}
@keyframes mrrule{to{clip-path:inset(0)}}
@keyframes mrruleout{from{clip-path:inset(0)}to{clip-path:inset(0 0 0 100%)}}
$scrimkeys""",
    ),
    Format(
        id="letter_cascade",
        title="Letter cascade",
        summary="Letters tip upright from the baseline one after another, then fold away.",
        group="kinetic",
        jobs=("hook", "close"),
        slots=(Slot("text", limit=30),),
        example={"text": "Built to *last*"},
        draw=_letter_cascade,
        seconds=2.6,
        shortest=1.8,
        tall=470,
        sound="whoosh",
        leaves=False,
        css="""
.f-letter_cascade .lock{position:relative;display:inline-block;max-width:100%}
.f-letter_cascade .scrim{$scrim}
.f-letter_cascade h1{margin:0;font-size:calc(var(--size) * var(--scale));font-weight:700;line-height:1.04;letter-spacing:-.04em;perspective:calc(1100px * var(--scale));$drop}
.f-letter_cascade .word{display:inline-block;white-space:nowrap}
.f-letter_cascade .word.hot{color:var(--accent)}
.f-letter_cascade .c{display:inline-block;transform-origin:50% 86%;animation:lcin .78s var(--ease) calc(var(--i) * var(--gap)) both,lcout .36s cubic-bezier(.7,0,.84,0) calc(var(--dur) - .44s + var(--i) * .007s) forwards}
@keyframes lcin{from{opacity:0;transform:translateY(.46em) rotateX(-98deg)}40%{opacity:1}to{opacity:1;transform:none}}
@keyframes lcout{to{opacity:0;transform:translateY(-.3em) rotateX(74deg)}}
$scrimkeys""",
    ),
    Format(
        id="typewriter",
        title="Typewriter",
        summary="Text lands key by key behind a caret, then gets selected and deleted.",
        group="kinetic",
        jobs=("hook", "explain"),
        slots=(Slot("text", limit=64),),
        example={"text": "Start with one *honest* sentence."},
        draw=_typewriter,
        seconds=3.4,
        shortest=1.8,
        tall=440,
        sound="click",
        fonts=("mono",),
        leaves=False,
        css="""
.f-typewriter p{position:relative;width:fit-content;margin:0;padding:.42em 1.02em .46em .54em;border-radius:.3em;background:color-mix(in srgb,var(--panel) 90%,transparent);box-shadow:0 0 0 1px rgba(255,255,255,.09) inset,0 24px 64px rgba(0,0,0,.4);font-family:var(--mono);font-size:calc(var(--size) * var(--scale));font-weight:500;line-height:1.24;letter-spacing:-.03em;white-space:pre-wrap;color:var(--ink);animation:twplate .42s var(--ease) both,twgone .001s step-end calc(var(--dur) - .06s) both}
.f-typewriter .all{--hot:var(--accent);--caret:var(--accent);position:relative;animation:twsel .001s step-end calc(var(--dur) - .36s) both}
.f-typewriter .k{position:relative;animation:twkey .001s step-end var(--t) both}
.f-typewriter .k.hot{color:var(--hot)}
.f-typewriter .car{position:absolute;left:100%;top:.1em;bottom:.1em;width:.54em;background:var(--caret);opacity:0;animation:twcar var(--l) linear var(--t) 1}
.f-typewriter .car.boot{left:0;top:.1em;bottom:auto;height:1.04em;animation:twcar var(--l) linear 0s 1}
.f-typewriter .k.fin .car{animation:twblink .84s step-end var(--t) infinite}
@keyframes twkey{from{opacity:0}to{opacity:1}}
@keyframes twcar{from{opacity:1}to{opacity:1}}
@keyframes twblink{0%{opacity:1}56%{opacity:0}100%{opacity:0}}
@keyframes twsel{from{background:transparent}to{background:var(--accent);color:var(--panel);--hot:var(--panel);--caret:transparent}}
@keyframes twplate{from{opacity:0;transform:translateY(calc(18px * var(--scale)))}to{opacity:1;transform:none}}
@keyframes twgone{from{visibility:visible}to{visibility:hidden}}
""",
    ),
    Format(
        id="text_match",
        title="Text match cut",
        summary="One word stays pinned while whole pages of the world cut around it.",
        group="kinetic",
        jobs=("hook", "transition"),
        slots=(Slot("text", limit=14),),
        example={"text": "everything"},
        draw=_text_match,
        seconds=2.8,
        shortest=1.6,
        longest=6.0,
        area="full",
        tall=1920,
        sound="tick",
        fonts=("mono", "serif"),
        leaves=False,
        needs_alpha=False,
        css="""
.f-text_match .scene{position:absolute;inset:0;overflow:hidden;background:var(--panel)}
.f-text_match .page{position:absolute;inset:0;animation:tmcut .001s step-end var(--t) both}
.f-text_match .sheet{position:absolute;inset:0 var(--gutter);display:grid;grid-template-rows:minmax(0,1fr) auto minmax(0,1fr);animation:tmpush .7s var(--ease) var(--t) both}
.f-text_match .above{display:flex;flex-direction:column;justify-content:flex-end;overflow:hidden;-webkit-mask-image:linear-gradient(transparent 7%,#000 34%)}
.f-text_match .below{overflow:hidden;-webkit-mask-image:linear-gradient(#000 66%,transparent 93%)}
.f-text_match p{margin:0;flex:none}
.f-text_match .row{display:grid;grid-template-columns:minmax(0,1fr) auto minmax(0,1fr);align-items:center;white-space:nowrap;-webkit-mask-image:linear-gradient(90deg,transparent,#000 9%,#000 91%,transparent)}
.f-text_match .l{display:flex;justify-content:flex-end;overflow:hidden}
.f-text_match .l span,.f-text_match .r span{flex:none}
.f-text_match .r{overflow:hidden}
.f-text_match .gap,.f-text_match .key{padding:.05em .22em .09em;font-family:"Brand",Arial,sans-serif;font-size:calc(var(--key) * var(--scale));font-weight:700;line-height:1;letter-spacing:-.035em;white-space:nowrap}
.f-text_match .gap{visibility:hidden}
.f-text_match .key{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);border-radius:.08em;background:var(--accent);color:var(--panel)}
.f-text_match .paper{background:#f2ecdf;color:#231e16;font-family:var(--serif);font-size:calc(66px * var(--scale));line-height:1.12;letter-spacing:-.01em}
.f-text_match .code{background:var(--panel);color:var(--soft);font-family:var(--mono);font-size:calc(40px * var(--scale));line-height:1.5}
.f-text_match .note{background:var(--surface);color:var(--ink);font-size:calc(56px * var(--scale));font-weight:500;line-height:1.2;letter-spacing:-.025em}
.f-text_match .ledger{background:#fbfaf6;color:#30373d;font-family:var(--mono);font-size:calc(36px * var(--scale));line-height:1.7}
.f-text_match .poster{background:#050505;color:#f5f5f5;font-size:calc(84px * var(--scale));font-weight:800;line-height:1;letter-spacing:-.04em;text-transform:uppercase}
.f-text_match .tint{background:color-mix(in srgb,var(--accent) 16%,var(--panel));color:var(--accent);font-family:var(--serif);font-size:calc(78px * var(--scale));line-height:1.08}
.f-text_match .book{background:#e9dfca;color:#3a2f22;font-family:var(--serif);font-size:calc(52px * var(--scale));line-height:1.26;text-align:justify}
@keyframes tmcut{from{visibility:hidden}to{visibility:visible}}
@keyframes tmpush{from{transform:scale(1.07)}to{transform:none}}
""",
    ),
    Format(
        id="weight_flex",
        title="Weight flex",
        summary="Each word swells from hairline to heavy on its beat. Anchor rows to speech.",
        group="kinetic",
        jobs=("hook", "prove"),
        slots=(Slot("words", kind="list", limit=4, each=10),),
        example={"words": ["Say", "it", "*louder*"]},
        draw=_weight_flex,
        seconds=3.0,
        shortest=2.0,
        tall=540,
        sound="rise",
        leaves=False,
        css="""
.f-weight_flex .lock{position:relative;display:inline-block;max-width:100%}
.f-weight_flex .scrim{$scrim}
.f-weight_flex h1{margin:0;font-size:calc(var(--size) * var(--scale));font-weight:100;line-height:1.02;letter-spacing:-.03em;$drop}
.f-weight_flex .line{display:block;white-space:nowrap}
.f-weight_flex .word.hot{color:var(--accent)}
.f-weight_flex .c{display:inline-block;animation:wfin 1.15s cubic-bezier(.3,0,.1,1) calc(var(--d) + var(--i) * var(--gap)) both,wfout .44s cubic-bezier(.6,0,.84,0) calc(var(--dur) - .52s + var(--i) * .012s) forwards}
@keyframes wfin{0%{opacity:0;font-weight:100;transform:translateY(.14em)}14%{opacity:1}54%{font-weight:900;transform:translateY(-.03em)}100%{opacity:1;font-weight:780;transform:none}}
@keyframes wfout{from{font-weight:780}to{opacity:0;font-weight:100}}
$scrimkeys""",
    ),
    Format(
        id="text_curve",
        title="Text on a curve",
        summary="A line of type rides a drawn curve in, settles, and runs off the far end.",
        group="kinetic",
        jobs=("hook", "explain"),
        slots=(Slot("text", limit=22),),
        example={"text": "The *long way* round"},
        draw=_text_curve,
        seconds=3.6,
        shortest=2.4,
        longest=8.0,
        tall=480,
        sound="whoosh",
        fonts=("mono",),
        leaves=False,
        css="""
@property --tc-ride{syntax:"<number>";inherits:true;initial-value:0}
.f-text_curve .ride{position:relative;width:890px;height:470px;transform:scale(calc(var(--scale) * var(--k,1)));transform-origin:0 0}
.f-text_curve .scrim{$scrim}
.f-text_curve .stage{position:absolute;inset:0;-webkit-mask-image:linear-gradient(90deg,transparent,#000 4%,#000 96%,transparent);animation:tcride var(--dur) linear both;$drop}
@media (min-aspect-ratio:3/2){.f-text_curve{--k:1.45}}
.f-text_curve .c{position:absolute;left:0;top:0;offset-path:path("$curve");offset-distance:calc((var(--tc-ride) + var(--x)) * 1px);offset-anchor:50% 76%;font-family:var(--mono);font-size:var(--size);font-weight:800;line-height:1;color:var(--ink)}
.f-text_curve .c.hot{color:var(--accent)}
.f-text_curve .tip{position:absolute;left:0;top:0;width:22px;height:22px;border-radius:50%;background:var(--accent);offset-path:path("$curve");offset-distance:calc((var(--tc-ride) + var(--x)) * 1px);offset-rotate:0deg}
.f-text_curve .line{position:absolute;inset:0;stroke-dashoffset:$start;animation:tcline 1.15s cubic-bezier(.3,0,.1,1) both,tcleave .62s cubic-bezier(.6,0,.9,.5) calc(var(--dur) - .62s) forwards}
.f-text_curve svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
.f-text_curve path{fill:none;stroke:var(--accent);stroke-opacity:.5;stroke-width:4;stroke-linecap:round;stroke-dasharray:$length;transform:translateY(12px)}
@keyframes tcride{0%{--tc-ride:var(--from);animation-timing-function:cubic-bezier(.3,.5,.2,1)}46%{--tc-ride:var(--rest);animation-timing-function:linear}82%{--tc-ride:var(--drift);animation-timing-function:cubic-bezier(.6,0,.9,.5)}100%{--tc-ride:var(--to)}}
@keyframes tcline{to{stroke-dashoffset:0}}
@keyframes tcleave{from{stroke-dashoffset:0}to{stroke-dashoffset:-$right}}
$scrimkeys""",
    ),
    Format(
        id="word_swap",
        title="Word swap",
        summary="One slot rolls through alternatives while the line around it holds still.",
        group="kinetic",
        jobs=("hook", "explain"),
        slots=(Slot("lead", limit=22, required=False), Slot("words", kind="list", limit=5, each=14)),
        example={"lead": "Made for", "words": ["makers", "teachers", "night owls", "everyone"]},
        draw=_word_swap,
        seconds=3.4,
        shortest=2.0,
        longest=10.0,
        tall=320,
        sound="tick",
        leaves=False,
        css="""
.f-word_swap .lock{position:relative;display:inline-block;max-width:100%}
.f-word_swap .scrim{$scrim}
.f-word_swap h1{margin:0;font-size:calc(var(--size) * var(--scale));font-weight:700;line-height:1;letter-spacing:-.048em;$drop}
.f-word_swap .lead{display:block;overflow:hidden;padding-bottom:.14em;font-size:.62em;font-weight:500;letter-spacing:-.035em}
.f-word_swap .lead span{display:block;animation:wsin .8s var(--ease) both,wsout .44s cubic-bezier(.7,0,.84,0) calc(var(--dur) - .52s) forwards}
.f-word_swap .slot{display:grid;justify-items:start;overflow:hidden;padding:.04em .06em .16em 0;margin-bottom:-.14em;color:var(--accent)}
.f-word_swap .w{grid-area:1/1;white-space:nowrap;animation:wsin .66s var(--ease) var(--in) both,wsup .66s var(--ease) var(--out) forwards}
.f-word_swap .w.last{animation:wsin .66s var(--ease) var(--in) both,wsout .44s cubic-bezier(.7,0,.84,0) var(--out) forwards}
@keyframes wsin{from{transform:translateY(132%)}to{transform:none}}
@keyframes wsup{to{transform:translateY(-132%)}}
@keyframes wsout{to{transform:translateY(-132%)}}
$scrimkeys""",
    ),
    Format(
        id="strike_replace",
        title="Strike and replace",
        summary="A rule crosses out the old words, then the better ones land beneath.",
        group="kinetic",
        jobs=("prove", "explain"),
        slots=(Slot("lead", limit=24, required=False), Slot("swap", kind="list", limit=2, each=16)),
        example={"lead": "Editing takes", "swap": ["three days", "nine minutes"]},
        draw=_strike_replace,
        seconds=3.4,
        shortest=2.4,
        tall=470,
        sound="thud",
        leaves=False,
        css="""
.f-strike_replace .lock{position:relative;display:inline-block;max-width:100%}
.f-strike_replace .scrim{$scrim}
.f-strike_replace h1{margin:0;font-size:calc(var(--size) * var(--scale));font-weight:700;line-height:1;letter-spacing:-.048em;$drop}
.f-strike_replace .m{display:block;overflow:hidden;padding:.04em .14em .16em;margin:0 -.14em -.12em}
.f-strike_replace .lead{font-size:.62em;font-weight:500;letter-spacing:-.035em}
.f-strike_replace .t{display:block;animation:srin .86s var(--ease) calc(var(--i) * .09s) both,srout .42s cubic-bezier(.7,0,.84,0) calc(var(--dur) - .52s + var(--i) * .04s) forwards}
.f-strike_replace .new{color:var(--accent)}
.f-strike_replace .new .t{animation:srin .86s var(--ease) var(--land) both,srout .42s cubic-bezier(.7,0,.84,0) calc(var(--dur) - .44s) forwards}
.f-strike_replace .hit{position:relative;display:inline-block;animation:srknock .3s linear calc(var(--hit) + .17s) both,srdim .34s ease-out calc(var(--hit) + .2s) both}
.f-strike_replace .bar{position:absolute;left:-.09em;right:-.09em;top:52%;height:.095em;background:var(--accent);transform:rotate(-2.2deg);clip-path:inset(0 100% 0 0);animation:srbar .25s cubic-bezier(.8,0,.2,1) var(--hit) both}
@keyframes srin{from{transform:translateY(132%)}to{transform:none}}
@keyframes srout{to{transform:translateY(-132%)}}
@keyframes srbar{to{clip-path:inset(-20% -2% -20% -2%)}}
@keyframes srknock{0%{transform:none}28%{transform:translateX(.05em)}62%{transform:translateX(-.02em)}100%{transform:none}}
@keyframes srdim{to{opacity:.46}}
$scrimkeys""",
    ),
    Format(
        id="split_flap",
        title="Split-flap board",
        summary="Letters clatter through the drum and settle on a departures board.",
        group="kinetic",
        jobs=("hook", "prove"),
        slots=(Slot("lines", kind="list", limit=3, each=13),),
        example={"lines": ["NEXT STOP", "NEW IDEAS"]},
        draw=_split_flap,
        seconds=3.6,
        shortest=2.2,
        longest=10.0,
        tall=480,
        sound="tick",
        fonts=("mono",),
        leaves=False,
        css="""
.f-split_flap{--u:.058s;--g:calc(5px * var(--scale))}
.f-split_flap .board{--cw:calc(var(--w) * var(--scale));--ch:calc(var(--h) * var(--scale));display:inline-flex;flex-direction:column;gap:var(--g);padding:calc(18px * var(--scale));border-radius:calc(20px * var(--scale));background:color-mix(in srgb,var(--panel) 94%,transparent);box-shadow:0 0 0 1px rgba(255,255,255,.08) inset,0 26px 70px rgba(0,0,0,.42);animation:sfin .5s var(--ease) both,sfout .36s cubic-bezier(.7,0,.84,0) calc(var(--dur) - .36s) forwards}
.f-split_flap .row{display:flex;gap:var(--g)}
.f-split_flap .cell{position:relative;flex:none;width:var(--cw);height:var(--ch);perspective:calc(var(--ch) * 3.4);font-family:var(--mono);font-size:calc(var(--ch) * .72);font-weight:600;line-height:var(--ch);text-align:center;color:var(--ink)}
.f-split_flap .half{position:absolute;left:0;right:0;height:50%;overflow:hidden;background:var(--surface)}
.f-split_flap .top{top:0;border-radius:calc(7px * var(--scale)) calc(7px * var(--scale)) 0 0;border-bottom:calc(1px * var(--scale)) solid rgba(0,0,0,.6)}
.f-split_flap .bot{bottom:0;border-radius:0 0 calc(7px * var(--scale)) calc(7px * var(--scale));background:color-mix(in srgb,var(--surface) 84%,#000)}
.f-split_flap .s{display:block;white-space:pre;animation:sfstep calc(var(--n) * var(--u)) steps(var(--n),end) var(--d) both}
.f-split_flap .bot .s{margin-top:calc(var(--ch) * -.5)}
.f-split_flap .top .s.next{margin-top:calc(var(--ch) * -1)}
.f-split_flap .bot .s.next{margin-top:calc(var(--ch) * -1.5)}
.f-split_flap .fa{transform-origin:50% 100%;backface-visibility:hidden;animation:sffa var(--u) linear var(--d) var(--n) both}
.f-split_flap .fb{transform-origin:50% 0;backface-visibility:hidden;animation:sffb var(--u) linear var(--d) var(--n) both}
@keyframes sfstep{to{transform:translateY(calc(var(--n) * var(--ch) * -1))}}
@keyframes sffa{0%{transform:rotateX(0)}50%,100%{transform:rotateX(-90deg)}}
@keyframes sffb{0%,50%{transform:rotateX(90deg)}100%{transform:rotateX(0)}}
@keyframes sfin{from{opacity:0;transform:translateY(calc(30px * var(--scale)))}to{opacity:1;transform:none}}
@keyframes sfout{to{opacity:0;transform:translateY(calc(-22px * var(--scale)))}}
""",
    ),
    Format(
        id="type_wall",
        title="Type wall",
        summary="A dense wall of repeated type slides past in depth behind one bright row.",
        group="kinetic",
        jobs=("hook", "close"),
        slots=(Slot("text", limit=16),),
        example={"text": "Keep going"},
        draw=_type_wall,
        seconds=3.2,
        shortest=1.8,
        area="full",
        tall=1920,
        sound="whoosh",
        leaves=False,
        css="""
.f-type_wall .veil{position:absolute;inset:0;background:color-mix(in srgb,var(--panel) 90%,transparent);animation:twvin .3s ease-out both,twvout .34s ease-in calc(var(--dur) - .34s) forwards}
.f-type_wall .view{position:absolute;inset:0;overflow:hidden;-webkit-mask-image:linear-gradient(90deg,transparent 3%,#000 14%,#000 86%,transparent 97%),linear-gradient(transparent 3%,#000 14%,#000 86%,transparent 97%);-webkit-mask-composite:source-in;mask-composite:intersect}
.f-type_wall .plane{position:absolute;left:50%;top:50%;width:calc(3600px * var(--scale));transform:translate(-50%,-50%) perspective(calc(1500px * var(--scale))) rotateX(46deg) rotateZ(-15deg);animation:twpush var(--dur) cubic-bezier(.3,0,.7,1) both}
.f-type_wall .row{display:flex;justify-content:center;white-space:nowrap;font-size:calc(var(--size) * var(--scale));font-weight:800;line-height:1.04;letter-spacing:-.035em;text-transform:uppercase;color:transparent;-webkit-text-stroke:calc(3px * var(--scale)) var(--soft);animation:twin .92s var(--ease) calc(var(--i) * .017s) both,twout .46s cubic-bezier(.7,0,.84,0) calc(var(--dur) - .54s + var(--i) * .007s) forwards}
.f-type_wall .row.hot{color:var(--accent);-webkit-text-stroke:0}
.f-type_wall .run{flex:none;animation:twrun var(--dur) linear both}
.f-type_wall i{display:inline-block;width:.34em;height:.34em;margin:0 .42em;vertical-align:.2em;border-radius:50%;background:currentColor}
@keyframes twvin{from{opacity:0}to{opacity:1}}
@keyframes twvout{to{opacity:0}}
@keyframes twin{from{opacity:0;transform:translateX(calc(var(--s) * 34%))}to{opacity:1;transform:none}}
@keyframes twout{to{opacity:0;transform:translateX(calc(var(--s) * -44%))}}
@keyframes twrun{from{transform:translateX(calc((var(--o) - var(--g) * 1.6) * 1px * var(--scale)))}to{transform:translateX(calc((var(--o) + var(--g) * 1.6) * 1px * var(--scale)))}}
@keyframes twpush{from{scale:.94}to{scale:1.06}}
""",
    ),
    Format(
        id="ticker",
        title="Headline ticker",
        summary="A slim band unrolls and headlines run through it at a steady reading pace.",
        group="kinetic",
        jobs=("prove", "explain"),
        slots=(Slot("label", limit=8, required=False), Slot("items", kind="list", limit=4, each=48)),
        example={"label": "Now", "items": ["Rain clears by noon", "Library opens late on Fridays", "Harbour ferry back on schedule"]},
        draw=_ticker,
        seconds=5.0,
        shortest=3.0,
        longest=20.0,
        tall=130,
        sound="pop",
        leaves=False,
        css="""
.f-ticker{top:calc(var(--cap-y) - 164px * var(--scale))}
.f-ticker .band{display:flex;align-items:center;height:calc(124px * var(--scale));overflow:hidden;border-radius:calc(22px * var(--scale));background:color-mix(in srgb,var(--panel) 92%,transparent);box-shadow:0 0 0 1px rgba(255,255,255,.09) inset,0 20px 56px rgba(0,0,0,.36);clip-path:inset(0 100% 0 0 round calc(22px * var(--scale)));animation:tkin .7s var(--ease) both,tkout .36s cubic-bezier(.7,0,.84,0) calc(var(--dur) - .36s) forwards}
.f-ticker .tag{flex:none;align-self:stretch;display:grid;place-items:center;padding:0 calc(34px * var(--scale));background:var(--accent);color:var(--panel);font-size:calc(44px * var(--scale));font-weight:700;letter-spacing:-.02em}
.f-ticker .win{flex:1;min-width:0;overflow:hidden;-webkit-mask-image:linear-gradient(90deg,transparent,#000 5%,#000 92%,transparent)}
.f-ticker .run{display:inline-flex;align-items:center;white-space:nowrap;font-size:calc(58px * var(--scale));font-weight:600;letter-spacing:-.025em;animation:tkrun var(--dur) linear both}
.f-ticker .pip{flex:none;width:calc(14px * var(--scale));height:calc(14px * var(--scale));margin:0 calc(42px * var(--scale));background:var(--accent);transform:rotate(45deg)}
@keyframes tkin{to{clip-path:inset(0 0 0 0 round calc(22px * var(--scale)))}}
@keyframes tkout{from{clip-path:inset(0 0 0 0 round calc(22px * var(--scale)))}to{clip-path:inset(0 100% 0 0 round calc(22px * var(--scale)))}}
@keyframes tkrun{from{transform:translateX(calc(46px * var(--scale)))}to{transform:translateX(calc(var(--go) * -1px * var(--scale)))}}
""",
    ),
    Format(
        id="liquid_type",
        title="Liquid type",
        summary="A drop stretches into letters, holds sharp, then runs back into itself.",
        group="kinetic",
        jobs=("hook", "close"),
        slots=(Slot("text", limit=12),),
        example={"text": "Fluid"},
        draw=_liquid_type,
        seconds=3.6,
        shortest=2.8,
        tall=300,
        sound="pop",
        leaves=False,
        css="""
.f-liquid_type .defs{position:absolute}
.f-liquid_type .stage{position:relative;width:fit-content;margin:0 auto;font-size:calc(var(--size) * var(--scale));text-align:center}
.f-liquid_type .scrim{$scrim}
.f-liquid_type h1{margin:0;font-size:1em;font-weight:800;line-height:1.1;letter-spacing:-.03em;white-space:nowrap}
.f-liquid_type .goo{position:relative;filter:url(#liquid-type) drop-shadow(0 4px 14px rgba(0,0,0,.42));animation:lthide .001s step-end 1.56s both,ltback .001s step-end calc(var(--dur) - .98s) forwards}
.f-liquid_type .ink{position:absolute;inset:0;opacity:0;$drop;animation:ltshow .2s linear 1.36s both,ltoff .14s linear calc(var(--dur) - .98s) forwards}
.f-liquid_type .c{display:inline-block}
.f-liquid_type .c.hot{color:var(--accent)}
.f-liquid_type .goo .c{animation:ltspread .96s cubic-bezier(.62,0,.22,1.16) calc(.3s + var(--r) * .1s) both,ltgather .72s cubic-bezier(.62,0,.78,.4) calc(var(--dur) - .92s + (1 - var(--r)) * .08s) forwards}
.f-liquid_type .drop{position:absolute;left:50%;top:50%;width:.92em;height:.92em;margin:-.46em 0 0 -.46em;border-radius:50%;background:var(--ink);animation:ltland .34s cubic-bezier(.3,1.4,.5,1) both,ltshrink .72s cubic-bezier(.5,0,.6,.8) .42s forwards,ltregrow .5s cubic-bezier(.5,0,.5,1) calc(var(--dur) - .7s) forwards,ltdry .2s ease-in calc(var(--dur) - .2s) forwards}
@keyframes ltland{from{transform:scale(0)}to{transform:none}}
@keyframes ltshrink{to{transform:scale(0)}}
@keyframes ltregrow{from{transform:scale(0)}to{transform:none}}
@keyframes ltdry{from{transform:none}to{transform:scale(0)}}
@keyframes lthide{from{visibility:visible}to{visibility:hidden}}
@keyframes ltback{from{visibility:hidden}to{visibility:visible}}
@keyframes ltspread{from{transform:translateX(var(--o)) scale(.42)}to{transform:none}}
@keyframes ltgather{to{transform:translateX(var(--o)) scale(.42)}}
@keyframes ltoff{from{opacity:1}to{opacity:0}}
@keyframes ltshow{from{opacity:0}to{opacity:1}}
$scrimkeys""",
    ),
    Format(
        id="spotlight",
        title="Spotlight reveal",
        summary="A light hunts across a dark frame, finds the words, then opens up on them.",
        group="kinetic",
        jobs=("hook", "close"),
        slots=(Slot("text", limit=48),),
        example={"text": "Look *closer*"},
        draw=_spotlight,
        seconds=3.6,
        shortest=2.6,
        longest=8.0,
        area="full",
        tall=1920,
        sound="whoosh",
        leaves=False,
        css="""
@property --sp-x{syntax:"<percentage>";inherits:true;initial-value:10%}
@property --sp-y{syntax:"<percentage>";inherits:true;initial-value:44%}
@property --sp-r{syntax:"<number>";inherits:true;initial-value:200}
.f-spotlight{animation:sppath var(--dur) both}
.f-spotlight .dark{position:absolute;inset:0;background:radial-gradient(circle calc(var(--sp-r) * 1.5px * var(--scale)) at var(--sp-x) var(--sp-y),color-mix(in srgb,var(--panel) 30%,transparent) 0,color-mix(in srgb,var(--panel) 42%,transparent) 54%,color-mix(in srgb,var(--panel) 95%,transparent) 100%);animation:spin .3s ease-out both,spout .3s ease-in calc(var(--dur) - .3s) forwards}
.f-spotlight .lit{position:absolute;inset:0;margin:0;display:grid;place-items:center;padding:0 var(--gutter);font-size:calc(var(--size) * var(--scale));font-weight:700;line-height:1.02;letter-spacing:-.045em;text-align:center;text-wrap:balance;-webkit-mask-image:radial-gradient(circle calc(var(--sp-r) * 1px * var(--scale)) at var(--sp-x) var(--sp-y),#000 56%,transparent 100%);animation:spout .22s ease-in calc(var(--dur) - .26s) forwards}
.f-spotlight .lit span{max-width:100%}
@keyframes spin{from{opacity:0}to{opacity:1}}
@keyframes spout{to{opacity:0}}
@keyframes sppath{0%{--sp-x:8%;--sp-y:34%;--sp-r:170;animation-timing-function:cubic-bezier(.45,0,.25,1)}26%{--sp-x:72%;--sp-y:58%;--sp-r:220;animation-timing-function:cubic-bezier(.45,0,.25,1)}44%{--sp-x:30%;--sp-y:42%;--sp-r:220;animation-timing-function:cubic-bezier(.45,0,.25,1)}56%{--sp-x:50%;--sp-y:50%;--sp-r:240;animation-timing-function:cubic-bezier(.16,1,.3,1)}72%{--sp-x:50%;--sp-y:50%;--sp-r:1300}86%{--sp-x:50%;--sp-y:50%;--sp-r:1300;animation-timing-function:cubic-bezier(.7,0,.84,0)}100%{--sp-x:50%;--sp-y:50%;--sp-r:0}}
""",
    ),
)


MACROS = {"$scrimkeys": SCRIM_KEYS, "$scrim": SCRIM, "$drop": DROP, "$curve": CURVE, "$length": str(CURVE_LENGTH), "$start": str(CURVE_LENGTH - CURVE_LEFT), "$right": str(CURVE_RIGHT)}


def _expand(css: str) -> str:
    for name, value in MACROS.items():
        css = css.replace(name, value)
    return css


FORMATS = tuple(replace(fmt, css=_expand(fmt.css)) for fmt in FORMATS)
