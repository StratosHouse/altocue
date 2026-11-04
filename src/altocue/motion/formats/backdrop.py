"""Full-frame backdrops for films with no footage. Each is pure CSS, seeded where it needs scatter, and never settles."""

from __future__ import annotations

import base64
import random
import re
import struct
import zlib
from typing import Any

from . import Format, Slot

GLYPHS = ("·", ":", "+", "#")
ASCII_COLUMNS = 96
ASCII_ROWS = 44
GRAIN_TILE = 160
HEX = re.compile(r"#[0-9a-fA-F]{6}")


def _seeded(item: dict[str, Any]) -> random.Random:
    return random.Random(int(item.get("seed", 7)))


def _dot_grid(item: dict[str, Any]) -> str:
    size = max(min(float(item.get("size", 42)), 120.0), 18.0)
    return f'<div class="field" style="--gap:{size:g}"><i class="dim"></i><i class="lit"></i></div>'


def _soft_gradient(item: dict[str, Any]) -> str:
    places = ((14, 20, 66, 0), (86, 30, 72, 1), (56, 92, 80, 2))
    rng = _seeded(item)
    tones = [tone for tone in str(item.get("tones", "")).split(",") if HEX.fullmatch(tone.strip())]
    paint = "".join(f"--tone{index}:{tone.strip()};" for index, tone in enumerate(tones[:3]))
    blobs = "".join(
        f'<i class="b{tone}" style="--x:{x + rng.uniform(-6, 6):.1f}%;--y:{y + rng.uniform(-6, 6):.1f}%;--r:{r}%;--t:{-rng.uniform(0, 9):.2f}s"></i>'
        for x, y, r, tone in places
    )
    return f'<div class="wash" style="{paint}">{blobs}</div>'


def _png_noise(seed: int) -> str:
    """A tile of grain as a small PNG, from a seed, so every render of a film has the same grain."""
    rng = random.Random(seed)
    rows = b"".join(b"\x00" + bytes(rng.randrange(255) for _ in range(GRAIN_TILE)) for _ in range(GRAIN_TILE))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    header = struct.pack(">IIBBBBB", GRAIN_TILE, GRAIN_TILE, 8, 0, 0, 0, 0)
    raw = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"")
    return base64.b64encode(raw).decode()


def _grain_field(item: dict[str, Any]) -> str:
    strength = max(min(float(item.get("strength", 0.08)), 0.3), 0.01)
    tile = _png_noise(int(item.get("seed", 7)))
    return f'<i class="grain" style="--o:{strength:g};background-image:url(data:image/png;base64,{tile})"></i>'


def _particle_field(item: dict[str, Any]) -> str:
    rng = _seeded(item)
    count = max(min(int(item.get("count", 90)), 160), 12)
    dots = []
    for index in range(count):
        depth = rng.random()
        size = 2 + depth * depth * 9
        dots.append(
            f'<i class="{"hot" if index % 7 == 0 else "p"}" style="left:{rng.uniform(-4, 104):.2f}%;top:{rng.uniform(-4, 104):.2f}%;'
            f"--s:{size:.1f};--a:{0.14 + depth * 0.5:.2f};--dx:{-30 - depth * 90:.1f};--dy:{rng.uniform(-40, 40):.1f};"
            f'--t:{7 + (1 - depth) * 9:.2f}s;--o:{-rng.uniform(0, 16):.2f}s"></i>'
        )
    return f'<div class="dust">{"".join(dots)}</div>'


def _ascii_field(item: dict[str, Any]) -> str:
    """Brightness as glyph density on a fixed grid. A soft ring of each density travels through, so still areas never flicker."""
    rng = _seeded(item)
    layers = []
    for band, glyph in enumerate(GLYPHS):
        rows = []
        for _ in range(ASCII_ROWS):
            rows.append("".join(glyph if rng.random() > 0.1 else " " for _ in range(ASCII_COLUMNS)))
        layers.append(f'<pre class="lay l{band}">{chr(10).join(rows)}</pre>')
    return f'<div class="grid">{"".join(layers)}</div>'


FORMATS = (
    Format(
        id="dot_grid",
        title="Moving dot grid",
        summary="A fine grid of dots slides past, lit by a slow pool of light.",
        group="backdrop",
        jobs=("explain",),
        slots=(Slot("size", kind="number", required=False),),
        example={"size": 42},
        draw=_dot_grid,
        seconds=8.0,
        shortest=1.0,
        longest=600.0,
        area="full",
        tall=1920,
        sound=None,
        leaves=False,
        css="""
.f-dot_grid{overflow:hidden}
.f-dot_grid .field{position:absolute;inset:-12%;--px:calc(var(--gap) * 1px * var(--scale));animation:dgslide 24s linear infinite}
.f-dot_grid i{position:absolute;inset:0;display:block;background-size:var(--px) var(--px);background-image:radial-gradient(circle,currentColor 0,currentColor calc(1.6px * var(--scale)),transparent calc(2.2px * var(--scale)))}
.f-dot_grid .dim{color:color-mix(in srgb,var(--ink) 15%,transparent)}
.f-dot_grid .lit{color:color-mix(in srgb,var(--accent) 80%,var(--ink));-webkit-mask-image:radial-gradient(closest-side,#000,rgba(0,0,0,.5) 42%,transparent);-webkit-mask-repeat:no-repeat;-webkit-mask-size:70% 110%;animation:dglight 19s ease-in-out infinite alternate}
@keyframes dgslide{from{transform:translate3d(0,0,0)}to{transform:translate3d(calc(var(--px) * -24),calc(var(--px) * -12),0)}}
@keyframes dglight{0%{-webkit-mask-position:12% 18%}50%{-webkit-mask-position:74% 62%}100%{-webkit-mask-position:30% 92%}}
""",
    ),
    Format(
        id="soft_gradient",
        title="Soft gradient",
        summary="Slow pools of brand colour drift and blend behind everything.",
        group="backdrop",
        jobs=("explain",),
        slots=(Slot("seed", kind="number", required=False), Slot("tones", kind="path", required=False)),
        example={"seed": 3, "tones": "#5b4bff,#ff8a5c"},
        draw=_soft_gradient,
        seconds=8.0,
        shortest=1.0,
        longest=600.0,
        area="full",
        tall=1920,
        sound=None,
        leaves=False,
        needs_alpha=False,
        css="""
.f-soft_gradient{overflow:hidden;background:linear-gradient(170deg,color-mix(in srgb,var(--surface) 72%,var(--panel)),var(--panel) 62%)}
.f-soft_gradient .wash{position:absolute;inset:-10%}
.f-soft_gradient i{position:absolute;display:block;left:var(--x);top:var(--y);width:calc(var(--r) * 1.5);aspect-ratio:1;translate:-50% -50%;border-radius:50%;animation:sgdrift 17s ease-in-out var(--t) infinite alternate}
.f-soft_gradient .b0{background:radial-gradient(closest-side,color-mix(in srgb,var(--tone0,var(--accent)) 46%,transparent),transparent)}
.f-soft_gradient .b1{background:radial-gradient(closest-side,color-mix(in srgb,var(--tone1,var(--surface)) 34%,transparent),transparent);animation-duration:23s}
.f-soft_gradient .b2{background:radial-gradient(closest-side,color-mix(in srgb,var(--tone2,var(--ink)) 11%,transparent),transparent);animation-duration:29s}
@keyframes sgdrift{from{transform:translate3d(-7%,-4%,0) scale(.94)}to{transform:translate3d(9%,6%,0) scale(1.1)}}
""",
    ),
    Format(
        id="grain_field",
        title="Film grain",
        summary="A fine seeded grain shimmers over the frame, so flat colour stops banding.",
        group="backdrop",
        jobs=("explain",),
        slots=(Slot("strength", kind="number", required=False), Slot("seed", kind="number", required=False)),
        example={"strength": 0.07},
        draw=_grain_field,
        seconds=8.0,
        shortest=1.0,
        longest=600.0,
        area="full",
        tall=1920,
        sound=None,
        leaves=False,
        css="""
.f-grain_field{overflow:hidden;pointer-events:none}
.f-grain_field .grain{position:absolute;inset:-160px;display:block;background-size:320px 320px;opacity:var(--o);mix-blend-mode:overlay;animation:gfjump .5s steps(1) infinite}
@keyframes gfjump{0%{transform:translate(0,0)}12%{transform:translate(-61px,37px)}25%{transform:translate(47px,-83px)}37%{transform:translate(-23px,-41px)}50%{transform:translate(89px,53px)}62%{transform:translate(-107px,19px)}75%{transform:translate(31px,97px)}87%{transform:translate(-71px,-113px)}}
""",
    ),
    Format(
        id="particle_field",
        title="Particle field",
        summary="Motes of light at several depths drift across, the near ones faster.",
        group="backdrop",
        jobs=("explain",),
        slots=(Slot("count", kind="number", required=False), Slot("seed", kind="number", required=False)),
        example={"count": 90},
        draw=_particle_field,
        seconds=8.0,
        shortest=1.0,
        longest=600.0,
        area="full",
        tall=1920,
        sound=None,
        leaves=False,
        css="""
.f-particle_field{overflow:hidden}
.f-particle_field .dust{position:absolute;inset:0}
.f-particle_field i{position:absolute;display:block;width:calc(var(--s) * 1px * var(--scale));aspect-ratio:1;border-radius:50%;background:var(--ink);opacity:var(--a);animation:pfdrift var(--t) linear var(--o) infinite}
.f-particle_field .hot{background:var(--accent);box-shadow:0 0 calc(var(--s) * 2px) var(--accent)}
@keyframes pfdrift{0%{transform:translate3d(calc(var(--dx) * -1px * var(--scale)),calc(var(--dy) * -1px * var(--scale)),0);opacity:0}12%,88%{opacity:var(--a)}100%{transform:translate3d(calc(var(--dx) * 1px * var(--scale)),calc(var(--dy) * 1px * var(--scale)),0);opacity:0}}
""",
    ),
    Format(
        id="ascii_field",
        title="ASCII field",
        summary="Glyphs on a fixed grid brighten by density as a soft wave passes through.",
        group="backdrop",
        jobs=("explain", "hook"),
        slots=(Slot("seed", kind="number", required=False),),
        example={"seed": 11},
        draw=_ascii_field,
        seconds=8.0,
        shortest=1.0,
        longest=600.0,
        area="full",
        tall=1920,
        sound=None,
        fonts=("mono",),
        leaves=False,
        css="""
.f-ascii_field{overflow:hidden;display:grid;place-items:center}
.f-ascii_field .grid{position:absolute;inset:-4%}
.f-ascii_field .lay{position:absolute;inset:0;margin:0;font-family:var(--mono);font-size:calc(100vmax / 66);line-height:1.5;letter-spacing:.1em;white-space:pre;-webkit-mask-repeat:no-repeat;-webkit-mask-size:190% 240%;animation:afwave 15s ease-in-out infinite alternate}
.f-ascii_field .l0{color:color-mix(in srgb,var(--ink) 16%,transparent);-webkit-mask-image:radial-gradient(closest-side,#000 96%,transparent)}
.f-ascii_field .l1{color:color-mix(in srgb,var(--ink) 30%,transparent);-webkit-mask-image:radial-gradient(closest-side,#000 62%,transparent 62%)}
.f-ascii_field .l2{color:color-mix(in srgb,var(--accent) 56%,var(--ink));-webkit-mask-image:radial-gradient(closest-side,#000 36%,transparent 36%)}
.f-ascii_field .l3{color:var(--accent);-webkit-mask-image:radial-gradient(closest-side,#000 15%,transparent 15%)}
@keyframes afwave{0%{-webkit-mask-position:-30% -40%}50%{-webkit-mask-position:60% 20%}100%{-webkit-mask-position:130% 120%}}
""",
    ),
)
