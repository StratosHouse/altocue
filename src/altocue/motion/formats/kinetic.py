"""Type that moves. Spoken words become the picture."""

from __future__ import annotations

import hashlib

from . import Format, Slot, kit

GLYPHS = "ABDEFGHKLMNPRSTUVXZ0123456789#%&/"


def _word_slam(item: dict) -> str:
    count = max(len(kit.tokens(item["text"])), 1)
    gap = min(kit.seconds(item) * 0.62 / count, 0.3)
    return f'<h1 class="lift" style="--gap:{gap:.3f}s">{kit.words(item["text"])}</h1>'


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
    body = "".join(f'<mark><span>{kit.esc(part)}</span></mark>' if i % 2 else kit.esc(part) for i, part in enumerate(parts))
    return f"<p>{body}</p>"


FORMATS = (
    Format(
        id="word_slam",
        title="Word slam",
        summary="Big words hit one at a time, each landing with weight.",
        group="kinetic",
        jobs=("hook",),
        slots=(Slot("text", limit=48),),
        example={"text": "Stop *editing* by hand"},
        draw=_word_slam,
        seconds=2.4,
        tall=460,
        sound="thud",
        css="""
.f-word_slam h1{margin:0;font-size:calc(132px * var(--scale));font-weight:800;line-height:.96;letter-spacing:-.045em;text-wrap:balance}
.f-word_slam .w{display:inline-block;animation:slam .36s cubic-bezier(.18,1.35,.32,1) both;animation-delay:calc(var(--i) * var(--gap))}
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
.f-marker mark::before{content:"";position:absolute;z-index:-1;inset:.06em -.12em .02em;border-radius:.1em;background:var(--accent);transform:scaleX(0);transform-origin:0 50%;animation:sweep .46s .36s cubic-bezier(.65,0,.2,1) both}
.f-marker mark span{animation:inkflip .1s .5s linear both}
@keyframes sweep{to{transform:none}}
@keyframes inkflip{to{color:var(--panel);text-shadow:none}}
""",
    ),
)
