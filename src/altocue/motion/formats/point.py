"""Graphics that come out of a presenter's gesture.

When someone points up or reaches toward the space beside their head, the graphic
should appear there, not in a fixed panel. A point callout starts as a spark at the
fingertip, draws a connector to its card and unfolds from that edge. Every position
is a percentage of the frame, so one recipe fits any aspect. All motion is CSS on
HTML elements, so every frame is a pure function of time.

`ax` and `ay` are the fingertip. `cx` is the card's centre and `cy` its lower edge,
both percentages of the frame. Rows can name an absolute timeline `at`, so each one
lands on the word that says it.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from . import Format, Slot, kit

# Original 24-unit line icons, drawn for these formats. Stroke only, round joins.
ICONS: dict[str, str] = {
    "moon": '<path d="M20 14.6A8.4 8.4 0 1 1 9.4 4a6.8 6.8 0 0 0 10.6 10.6z"/><path d="M16.5 3.5v3M15 5h3"/>',
    "clock": '<circle cx="12" cy="12" r="8.6"/><path d="M12 7.2V12l3.3 2.1"/>',
    "brain": (
        '<path d="M11.2 5.2a3 3 0 0 0-5.3 1.5 3 3 0 0 0-2 4.8 3.2 3.2 0 0 0 1.3 5.1 3 3 0 0 0 4.6 2.6 2.1 2.1 0 0 0 1.4-.9z"/>'
        '<path d="M12.8 5.2a3 3 0 0 1 5.3 1.5 3 3 0 0 1 2 4.8 3.2 3.2 0 0 1-1.3 5.1 3 3 0 0 1-4.6 2.6 2.1 2.1 0 0 1-1.4-.9z"/>'
        '<path d="M12 5.4v13.4M7.6 9.6c1 .2 1.8.9 2.1 1.9M16.4 9.6c-1 .2-1.8.9-2.1 1.9M7.4 15c.9-.6 2-.7 3-.3M16.6 15c-.9-.6-2-.7-3-.3"/>'
    ),
    "plate": '<circle cx="12" cy="12" r="6.2"/><path d="M3 4v5.5a2 2 0 0 0 2 2V20M5 4v5M21 4c-1.8 1.6-1.8 5.6 0 7.4V20"/>',
    "heart": '<path d="M12 20s-7.5-4.5-7.5-10.2A4.2 4.2 0 0 1 12 7.3a4.2 4.2 0 0 1 7.5 2.5C19.5 15.5 12 20 12 20z"/><path d="M6.5 12.5h3l1.3-2.4 2.4 4.6 1.3-2.2h3"/>',
    "gauge": '<path d="M4.2 16.5a8.4 8.4 0 1 1 15.6 0"/><path d="M12 15.5l3.6-4.8"/><circle cx="12" cy="15.8" r="1.4"/><path d="M6.6 11.2l1 .6M12 7v1.2M17.4 11.2l-1 .6"/>',
    "calendar": '<rect x="4" y="5.5" width="16" height="14.5" rx="2.6"/><path d="M8.2 3.5v4M15.8 3.5v4M4 10h16"/><path d="M9 15l2 2 4-4.2"/>',
    "lock": '<rect x="5.2" y="10.6" width="13.6" height="9.6" rx="2.4"/><path d="M8.2 10.6V8a3.8 3.8 0 0 1 7.6 0v2.6"/><path d="M12 14.4v2.4"/>',
    "bottle": '<rect x="6.6" y="3.4" width="10.8" height="4" rx="1.2"/><path d="M7.6 7.4h8.8v11.2a2 2 0 0 1-2 2H9.6a2 2 0 0 1-2-2z"/><path d="M10 12.2h4M12 10.2v4"/>',
    "pharmacy": '<path d="M9.4 3.8h5.2v5.6h5.6v5.2h-5.6v5.6H9.4v-5.6H3.8V9.4h5.6z"/>',
    "chat": '<path d="M5.2 5h13.6A2.2 2.2 0 0 1 21 7.2v7.6a2.2 2.2 0 0 1-2.2 2.2h-6.6l-4.8 3.6V17H5.2A2.2 2.2 0 0 1 3 14.8V7.2A2.2 2.2 0 0 1 5.2 5z"/><path d="M7.6 11h.01M12 11h.01M16.4 11h.01"/>',
    "sliders": '<path d="M4 7.5h9M17.5 7.5H20M4 16.5h3.5M12 16.5h8"/><circle cx="15.2" cy="7.5" r="2.2"/><circle cx="9.7" cy="16.5" r="2.2"/>',
    "egg": '<path d="M12 3.2c3.3 0 6.4 5.9 6.4 10.4a6.4 6.4 0 0 1-12.8 0C5.6 9.1 8.7 3.2 12 3.2z"/><path d="M9.4 14.6a2.8 2.8 0 0 0 2.6 2.2"/>',
    "cup": '<path d="M4.8 9h11.4v5.4a5 5 0 0 1-5 5h-1.4a5 5 0 0 1-5-5z"/><path d="M16.2 10.6h1.3a2.6 2.6 0 0 1 0 5.2h-1.6"/><path d="M8.4 3.4c-.8 1 .8 1.8 0 3M11.6 3.4c-.8 1 .8 1.8 0 3"/>',
    "can": '<path d="M8.4 4.4h7.2l.8 1.8v12.6l-.8 1.8H8.4l-.8-1.8V6.2z"/><path d="M7.6 7.6h8.8M7.6 17.4h8.8"/><path d="M12.6 9.6l-2.2 3h3.2l-2.2 3"/>',
    "bag": '<path d="M5.6 8.4h12.8l-1.1 11.2a1.6 1.6 0 0 1-1.6 1.4H8.3a1.6 1.6 0 0 1-1.6-1.4z"/><path d="M9 8.4V7a3 3 0 0 1 6 0v1.4"/>',
    "apple": '<path d="M12 7.6c-1.6-1.4-6.4-1.7-6.4 4.1 0 4.2 3 8.3 4.6 8.3.8 0 1.2-.5 1.8-.5s1 .5 1.8.5c1.6 0 4.6-4.1 4.6-8.3 0-5.8-4.8-5.5-6.4-4.1z"/><path d="M12 7.6c0-2 .9-3.4 2.9-4.2"/>',
    "lungs": '<path d="M12 4v7.8"/><path d="M12 9.6c-1.6 0-2.4-1.9-4.4-1.9-2.4 0-3.6 4.6-3.6 8.6 0 2.6 1 3.7 2.8 3.7 2.3 0 3.9-1.2 3.9-3.4V11.5"/><path d="M12 9.6c1.6 0 2.4-1.9 4.4-1.9 2.4 0 3.6 4.6 3.6 8.6 0 2.6-1 3.7-2.8 3.7-2.3 0-3.9-1.2-3.9-3.4V11.5"/>',
    "dizzy": '<circle cx="12" cy="12" r="8.6"/><path d="M8 9l2 2M10 9l-2 2M14 9l2 2M16 9l-2 2"/><path d="M9 16c1.6-1.2 4.4-1.2 6 0"/>',
    "pulse": '<path d="M2.8 12h3.6l2-5.4 3.2 10.8 2.2-7.6 1.6 4.2h5.8"/>',
    "phone": '<path d="M7.2 3.6h2.9l1.4 3.8-1.9 1.4a11.3 11.3 0 0 0 5.6 5.6l1.4-1.9 3.8 1.4v2.9a2 2 0 0 1-2.1 2A15.6 15.6 0 0 1 5.2 5.7a2 2 0 0 1 2-2.1z"/>',
    "alert": '<path d="M12 3.6l9.2 16H2.8z"/><path d="M12 10v4.2M12 17.2h.01"/>',
    "check": '<path d="M5 12.4l4.6 4.6L19 7.6"/>',
    "share-off": '<circle cx="6.4" cy="12" r="2.4"/><circle cx="17.6" cy="6.2" r="2.4"/><circle cx="17.6" cy="17.8" r="2.4"/><path d="M8.6 10.9l6.8-3.6M8.6 13.1l6.8 3.6M3.4 3.4l17.2 17.2"/>',
    "battery": '<rect x="2.8" y="7.6" width="16.4" height="8.8" rx="2.2"/><path d="M21.2 10.8v2.4"/><rect x="5" y="9.8" width="3" height="4.4" rx=".6"/>',
    "trend-down": '<path d="M3.6 6.8l6 6 3.4-3.4 7.4 7.4"/><path d="M20.4 11.8v5h-5"/>',
    "shield": '<path d="M12 3.2l7.4 2.8v5.6c0 4.6-3 8-7.4 9.2-4.4-1.2-7.4-4.6-7.4-9.2V6z"/><path d="M12 15.6s-3.2-1.9-3.2-4.3a1.8 1.8 0 0 1 3.2-1.1 1.8 1.8 0 0 1 3.2 1.1c0 2.4-3.2 4.3-3.2 4.3z"/>',
    "list": '<path d="M9.4 6.6H20M9.4 12H20M9.4 17.4H20"/><path d="M3.8 6.4l1.2 1.2 2.2-2.4M3.8 11.8l1.2 1.2 2.2-2.4M3.8 17.2l1.2 1.2 2.2-2.4"/>',
    "sparkle": '<path d="M12 3.4l1.9 6.7 6.7 1.9-6.7 1.9L12 20.6l-1.9-6.7L3.4 12l6.7-1.9z"/>',
}
TONES = ("calm", "alert")
SPOTS = {"ax": 30.0, "ay": 36.0, "cx": 50.0, "cy": 30.0, "w": 76.0}


def icon(name: str, cls: str = "pc-glyph") -> str:
    paths = ICONS.get(name) or ICONS["sparkle"]
    return f'<svg class="{cls}" viewBox="0 0 24 24" aria-hidden="true">{paths}</svg>'


def _spot(item: dict[str, Any], key: str) -> float:
    value = item.get(key)
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = SPOTS[key]
    return max(0.0, min(100.0, number))


def _rows(item: dict[str, Any]) -> str:
    out, first = [], None
    for index, row in enumerate(kit.rows(item, "items") if item.get("items") else []):
        delay = row["delay"] if row.get("at") is not None else 1.05 + 0.32 * index
        first = delay if first is None else min(first, delay)
        mark = icon(str(row.get("icon") or ("alert" if item.get("tone") == "alert" else "check")), "pc-mini-glyph")
        out.append(
            f'<div class="pc-row" style="--d:{delay:.4f}s"><span class="pc-mini">{mark}</span>'
            f'<b>{kit.emphasis(str(row.get("text", "")))}</b></div>'
        )
    if not out:
        return ""
    return f'<div class="pc-rows" style="--first:{max((first or 0) - 0.12, 0):.4f}s"><div class="pc-rows-in">{"".join(out)}</div></div>'


def _title(item: dict[str, Any]) -> str:
    words = kit.tokens(str(item.get("title") or "One clear point"))
    return " ".join(
        f'<span class="pc-mask"><span class="pc-word{" pc-hot" if hot else ""}" style="--n:{index}">{kit.esc(word)}</span></span>'
        for index, (word, hot) in enumerate(words)
    )


def _badge(item: dict[str, Any], family: str) -> str:
    if family == "stat":
        fill = max(0.02, min(1.0, float(item.get("fill") or 0.25)))
        unit = f'<i class="pc-unit">{kit.esc(item.get("unit", ""))}</i>' if item.get("unit") else ""
        return (
            f'<span class="pc-dial" style="--fill:{fill:.4f}"><span class="pc-dial-track"></span><span class="pc-dial-arc"></span>'
            f'<span class="pc-dial-face"><b class="pc-value">{kit.esc(item.get("value", ""))}</b>{unit}</span></span>'
        )
    legs = '<span class="pc-legs"><i class="pc-leg"></i><i class="pc-leg pc-leg-b"></i></span>' if item.get("act") == "walk" else ""
    return f'<span class="pc-badge"><span class="pc-halo"></span><span class="pc-icon">{icon(str(item.get("icon") or "sparkle"))}</span>{legs}</span>'


def _draw(item: dict[str, Any], family: str) -> str:
    ax, ay, cx, cy, width = (_spot(item, key) for key in ("ax", "ay", "cx", "cy", "w"))
    tone = item.get("tone") if item.get("tone") in TONES else "calm"
    cover = item.get("stage") == "cover"
    pointed = not cover and item.get("ax") is not None and item.get("ay") is not None
    if not pointed:
        ax, ay = cx, cy
    kicker = f'<span class="pc-kicker">{kit.esc(item["kicker"])}</span>' if item.get("kicker") else ""
    # The connector is drawn in frame percentages, from the fingertip to the card's lower edge.
    bend = (ay + cy) / 2
    ex = max(cx - width / 2 + 7, min(cx + width / 2 - 7, ax))
    link = (
        f'<span class="pc-link" style="--top:{min(ay, cy):.3f};--bottom:{max(ay, cy):.3f}"><svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">'
        f'<path class="pc-link-glow" d="M{ax:.2f} {ay:.2f}C{ax:.2f} {bend:.2f} {ex:.2f} {bend:.2f} {ex:.2f} {cy:.2f}"/>'
        f'<path class="pc-link-line" d="M{ax:.2f} {ay:.2f}C{ax:.2f} {bend:.2f} {ex:.2f} {bend:.2f} {ex:.2f} {cy:.2f}"/></svg></span>'
    )
    spark = '<span class="pc-spark"><span class="pc-ring"></span><span class="pc-ring pc-ring-late"></span><span class="pc-dot"></span></span>'
    mark = icon(str(item.get("icon") or ("alert" if tone == "alert" else "sparkle")), "pc-watermark-glyph")
    backdrop = f'<span class="pc-backdrop"><span class="pc-backdrop-light"></span><span class="pc-dots"></span><span class="pc-watermark">{mark}</span></span>' if cover else ""
    walk = item.get("act") == "walk"
    mode = " pc-cover" if cover else (" pc-pointed" if pointed else " pc-open")
    return (
        f'<div class="pc-stage pc-{tone} pc-family-{family}{mode}{" pc-walk" if walk else ""}" style="--ax:{ax:.3f};--ay:{ay:.3f};--cx:{cx:.3f};--cy:{cy:.3f};--w:{width:.3f}">'
        f'{backdrop}{link if pointed else ""}{spark if pointed else ""}'
        f'<div class="pc-card"><span class="pc-sheen"></span><span class="pc-glint"></span>'
        f'<div class="pc-head">{_badge(item, family)}<div class="pc-copy">{kicker}<h3 class="pc-title">{_title(item)}</h3></div></div>'
        f"{_rows(item)}</div></div>"
    )


CSS = """
& .pc-stage{position:absolute;inset:0;container-type:size;pointer-events:none;color:var(--ink);--pc-ease:cubic-bezier(.16,1,.3,1);--pc-tone:var(--accent);--pc-hot:var(--g-hot,var(--pc-tone));--pc-spark:var(--g-spark,var(--pc-tone));--pc-badge:var(--g-badge,var(--pc-tone));--pc-out:calc(var(--dur) - .46s)}
& .pc-alert{--pc-tone:var(--g-alert,#ff8a7a);--pc-hot:var(--g-alert-hot,var(--pc-tone));--pc-spark:var(--g-alert,#ff8a7a);--pc-badge:var(--g-alert,#ff8a7a)}
& .pc-cover{--pc-hot:var(--g-cover-hot,var(--pc-tone))}
& em{font-style:normal;color:var(--pc-hot)}
& .pc-spark{position:absolute;left:calc(var(--ax) * 1cqw);top:calc(var(--ay) * 1cqh);width:0;height:0;animation:pc-fade .55s ease-in 1.45s forwards}
& .pc-dot{position:absolute;left:calc(-13px * var(--scale));top:calc(-13px * var(--scale));width:calc(26px * var(--scale));height:calc(26px * var(--scale));border-radius:50%;background:var(--pc-spark);box-shadow:0 0 calc(26px * var(--scale)) var(--pc-spark),0 0 0 calc(5px * var(--scale)) color-mix(in srgb,var(--pc-spark) 28%,transparent);animation:pc-dot .5s var(--pc-ease) both}
& .pc-ring{position:absolute;left:calc(-75px * var(--scale));top:calc(-75px * var(--scale));width:calc(150px * var(--scale));height:calc(150px * var(--scale));border-radius:50%;border:calc(3px * var(--scale)) solid var(--pc-spark);opacity:0;animation:pc-ring .9s cubic-bezier(.2,.7,.2,1) .04s both}
& .pc-ring-late{animation-delay:.2s}
& .pc-link{position:absolute;inset:0;clip-path:inset(calc(var(--top) * 1cqh) 0 calc((100 - var(--bottom)) * 1cqh) 0);animation:pc-draw .62s var(--pc-ease) .1s both,pc-fade .6s ease-in 1.6s forwards}
& .pc-link svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
& .pc-link-line{fill:none;stroke:var(--pc-spark);stroke-width:calc(5px * var(--scale));stroke-linecap:round;vector-effect:non-scaling-stroke}
& .pc-link-glow{fill:none;stroke:color-mix(in srgb,var(--pc-spark) 40%,transparent);stroke-width:calc(20px * var(--scale));stroke-linecap:round;vector-effect:non-scaling-stroke;filter:blur(calc(7px * var(--scale)))}
& .pc-card{position:absolute;left:calc((var(--cx) - var(--w) / 2) * 1cqw);bottom:calc((100 - var(--cy)) * 1cqh);width:calc(var(--w) * 1cqw);box-sizing:border-box;padding:calc(34px * var(--scale)) calc(38px * var(--scale)) calc(36px * var(--scale));border-radius:calc(46px * var(--scale));overflow:hidden;
  background:var(--g-card,linear-gradient(162deg,color-mix(in srgb,var(--surface) 86%,#ffffff 14%) 0%,var(--panel) 46%,color-mix(in srgb,var(--panel) 84%,#071a1f 16%) 100%));color:var(--g-card-ink,var(--ink));
  border:calc(1.5px * var(--scale)) solid var(--g-card-edge,rgba(255,255,255,.2));
  box-shadow:0 calc(34px * var(--scale)) calc(90px * var(--scale)) color-mix(in srgb,var(--g-card-shadow,#06161a) 46%,transparent),0 calc(10px * var(--scale)) calc(26px * var(--scale)) color-mix(in srgb,var(--g-card-shadow,#06161a) 26%,transparent),inset 0 calc(1.5px * var(--scale)) 0 rgba(255,255,255,.24);
  transform-origin:calc(50% + (var(--ax) - var(--cx)) * 1cqw) 100%;animation:pc-card .78s var(--pc-ease) .26s both,pc-card-out .44s cubic-bezier(.5,0,.75,0) var(--pc-out) forwards}
& .pc-sheen{position:absolute;inset:0 0 auto;height:55%;background:linear-gradient(180deg,var(--g-sheen,rgba(255,255,255,.12)),rgba(255,255,255,0));pointer-events:none}
& .pc-glint{position:absolute;top:-30%;bottom:-30%;left:-40%;width:28%;background:linear-gradient(100deg,transparent,rgba(255,255,255,.16) 45%,rgba(255,255,255,.04) 60%,transparent);transform:translateX(-120%) skewX(-14deg);animation:pc-glint 1.1s cubic-bezier(.4,0,.2,1) .82s both}
& .pc-head{position:relative;display:flex;align-items:center;gap:calc(28px * var(--scale))}
& .pc-badge{position:relative;flex:none;width:calc(128px * var(--scale));height:calc(128px * var(--scale));border-radius:50%;display:grid;place-items:center;
  background:radial-gradient(circle at 34% 28%,#ffffff 0%,color-mix(in srgb,var(--pc-badge) 86%,#ffffff 14%) 34%,color-mix(in srgb,var(--pc-badge) 70%,var(--surface) 30%) 100%);
  box-shadow:0 calc(12px * var(--scale)) calc(28px * var(--scale)) rgba(6,22,26,.34),inset 0 calc(-6px * var(--scale)) calc(14px * var(--scale)) rgba(6,22,26,.14);animation:pc-badge .66s var(--pc-ease) .48s both}
& .pc-halo{position:absolute;inset:calc(-16px * var(--scale));border-radius:50%;background:radial-gradient(circle,color-mix(in srgb,var(--pc-tone) 45%,transparent),transparent 70%);animation:pc-halo 2.8s ease-in-out 1.1s infinite alternate}
& .pc-icon{position:relative;width:62%;height:62%;clip-path:circle(0% at 50% 50%);animation:pc-icon .7s var(--pc-ease) .7s both}
& .pc-glyph{width:100%;height:100%;fill:none;stroke:var(--g-glyph,var(--panel));stroke-width:1.75;stroke-linecap:round;stroke-linejoin:round}
& .pc-copy{min-width:0;display:flex;flex-direction:column;gap:calc(10px * var(--scale))}
& .pc-kicker{align-self:flex-start;display:inline-flex;align-items:center;height:calc(52px * var(--scale));padding:0 calc(22px * var(--scale));border-radius:999px;font-size:calc(30px * var(--scale));font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--g-kicker-ink,var(--panel));background:var(--g-kicker,var(--pc-tone));animation:pc-rise .5s var(--pc-ease) .62s both}
& .pc-title{margin:0;font-size:calc(64px * var(--scale));line-height:1.06;font-weight:700;letter-spacing:-.018em;text-wrap:balance}
& .pc-mask{display:inline-block;overflow:hidden;vertical-align:bottom;padding-bottom:.08em;margin-bottom:-.08em}
& .pc-word{display:inline-block;animation:pc-word .62s var(--pc-ease) both;animation-delay:calc(.66s + var(--n) * .055s)}
& .pc-hot{color:var(--pc-hot)}
& .pc-family-stat .pc-title{font-size:calc(54px * var(--scale))}
& .pc-dial{position:relative;flex:none;width:calc(176px * var(--scale));height:calc(176px * var(--scale));border-radius:50%;display:grid;place-items:center;animation:pc-badge .66s var(--pc-ease) .48s both}
& .pc-dial-track{position:absolute;inset:0;border-radius:50%;background:conic-gradient(var(--g-dial-track,rgba(255,255,255,.14)) 0turn 1turn);-webkit-mask:radial-gradient(farthest-side,transparent calc(100% - 13px * var(--scale)),#000 calc(100% - 12px * var(--scale)));mask:radial-gradient(farthest-side,transparent calc(100% - 13px * var(--scale)),#000 calc(100% - 12px * var(--scale)))}
& .pc-dial-arc{position:absolute;inset:0;border-radius:50%;background:conic-gradient(var(--pc-tone) 0turn calc(var(--fill) * 1turn),transparent calc(var(--fill) * 1turn));-webkit-mask:radial-gradient(farthest-side,transparent calc(100% - 13px * var(--scale)),#000 calc(100% - 12px * var(--scale)));mask:radial-gradient(farthest-side,transparent calc(100% - 13px * var(--scale)),#000 calc(100% - 12px * var(--scale)));clip-path:polygon(50% 50%,50% -20%,120% -20%,120% 120%,-20% 120%,-20% -20%,50% -20%);animation:pc-sweep .9s var(--pc-ease) .62s both}
& .pc-dial-face{position:relative;display:flex;flex-direction:column;align-items:center;line-height:.9}
& .pc-value{font-size:calc(74px * var(--scale));font-weight:700;letter-spacing:-.03em;animation:pc-rise .5s var(--pc-ease) .72s both}
& .pc-unit{font-style:normal;font-size:calc(24px * var(--scale));font-weight:700;letter-spacing:.14em;color:var(--pc-hot);text-transform:uppercase;margin-top:calc(6px * var(--scale));animation:pc-rise .5s var(--pc-ease) .8s both}
& .pc-rows{position:relative;display:grid;grid-template-rows:0fr;animation:pc-open .5s var(--pc-ease) var(--first) both}
& .pc-rows-in{min-height:0;overflow:hidden;display:flex;flex-wrap:wrap;gap:calc(14px * var(--scale));padding-top:calc(26px * var(--scale))}
& .pc-row{display:flex;align-items:center;gap:calc(14px * var(--scale));padding:calc(10px * var(--scale)) calc(26px * var(--scale)) calc(10px * var(--scale)) calc(10px * var(--scale));border-radius:999px;background:var(--g-row,rgba(255,255,255,.08));box-shadow:inset 0 0 0 calc(1.5px * var(--scale)) var(--g-row-edge,rgba(255,255,255,.14));opacity:0;animation:pc-row .52s var(--pc-ease) var(--d) both}
& .pc-row b{font-size:calc(44px * var(--scale));font-weight:700;line-height:1.1;letter-spacing:-.01em;white-space:nowrap}
& .pc-mini{flex:none;display:grid;place-items:center;width:calc(58px * var(--scale));height:calc(58px * var(--scale));border-radius:50%;background:color-mix(in srgb,var(--pc-tone) 22%,transparent);box-shadow:inset 0 0 0 calc(1.5px * var(--scale)) color-mix(in srgb,var(--pc-tone) 62%,transparent)}
& .pc-mini-glyph{width:60%;height:60%;fill:none;stroke:var(--g-mini,var(--pc-hot));stroke-width:2.1;stroke-linecap:round;stroke-linejoin:round}
& .pc-cover .pc-rows-in{flex-direction:column;flex-wrap:nowrap;gap:calc(16px * var(--scale));padding-top:calc(34px * var(--scale))}
& .pc-cover .pc-mini-glyph{stroke:var(--pc-hot)}& .pc-cover .pc-glyph{stroke:var(--panel)}& .pc-cover .pc-dial-track{background:conic-gradient(rgba(255,255,255,.14) 0turn 1turn)}& .pc-cover .pc-sheen{background:linear-gradient(180deg,rgba(255,255,255,.12),rgba(255,255,255,0))}
& .pc-cover .pc-row{background:none;box-shadow:none;padding:calc(12px * var(--scale)) 0 0;border-top:calc(1.5px * var(--scale)) solid rgba(255,255,255,.12);border-radius:0}
& .pc-cover .pc-row b{font-size:calc(54px * var(--scale))}
& .pc-cover .pc-mini{width:calc(70px * var(--scale));height:calc(70px * var(--scale))}
& .pc-backdrop{position:absolute;inset:0;background:var(--g-cover,radial-gradient(120% 80% at 50% 38%,color-mix(in srgb,var(--surface) 96%,#ffffff 4%) 0%,var(--panel) 58%,color-mix(in srgb,var(--panel) 80%,#04141a 20%) 100%));animation:pc-cover-in .42s var(--pc-ease) both,pc-fade .38s ease-in var(--pc-out) forwards}
& .pc-dots{position:absolute;inset:0;background-image:radial-gradient(rgba(255,255,255,.07) calc(1.6px * var(--scale)),transparent calc(1.8px * var(--scale)));background-size:calc(34px * var(--scale)) calc(34px * var(--scale));-webkit-mask:radial-gradient(70% 55% at 50% 45%,#000,transparent);mask:radial-gradient(70% 55% at 50% 45%,#000,transparent)}
& .pc-watermark{position:absolute;right:-12cqw;bottom:-6cqh;width:78cqw;height:78cqw;opacity:.07;animation:pc-turn 18s linear infinite}
& .pc-watermark-glyph{width:100%;height:100%;fill:none;stroke:#ffffff;stroke-width:.9;stroke-linecap:round;stroke-linejoin:round}
& .pc-backdrop-light{position:absolute;inset:-10%;background:radial-gradient(40% 30% at 30% 22%,color-mix(in srgb,var(--pc-tone) 16%,transparent),transparent 70%),radial-gradient(36% 26% at 74% 78%,color-mix(in srgb,var(--pc-tone) 10%,transparent),transparent 70%);animation:pc-drift 9s ease-in-out infinite alternate}
& .pc-cover .pc-card{left:calc((50 - var(--w) / 2) * 1cqw);bottom:auto;top:calc(var(--cy) * 1cqh);transform-origin:50% 50%;color:var(--ink);background:linear-gradient(162deg,rgba(255,255,255,.12),rgba(255,255,255,.04));border-color:rgba(255,255,255,.18);box-shadow:0 calc(40px * var(--scale)) calc(100px * var(--scale)) rgba(2,14,18,.42),inset 0 calc(1.5px * var(--scale)) 0 rgba(255,255,255,.2)}
& .pc-cover .pc-title{font-size:calc(76px * var(--scale))}& .pc-cover .pc-badge{width:calc(150px * var(--scale));height:calc(150px * var(--scale))}& .pc-cover .pc-card{padding:calc(46px * var(--scale)) calc(50px * var(--scale)) calc(48px * var(--scale))}
& .pc-open .pc-card{transform-origin:50% 100%}
& .pc-legs{position:absolute;left:50%;bottom:calc(-24px * var(--scale));width:calc(58px * var(--scale));height:calc(30px * var(--scale));margin-left:calc(-29px * var(--scale));display:none;opacity:0;animation:pc-cover-in .2s ease-out calc(var(--dur) - 2.2s) both}
& .pc-walk .pc-legs{display:block}
& .pc-leg{position:absolute;bottom:0;width:calc(7px * var(--scale));height:calc(30px * var(--scale));border-radius:9px;background:#ffffff;box-shadow:0 0 calc(10px * var(--scale)) color-mix(in srgb,var(--pc-tone) 70%,transparent);transform-origin:50% 0;left:calc(14px * var(--scale));animation:pc-step .17s ease-in-out calc(var(--dur) - 2.15s) infinite alternate}
& .pc-leg-b{left:auto;right:calc(14px * var(--scale));animation-name:pc-step-b}
& .pc-walk .pc-badge{animation:pc-badge .66s var(--pc-ease) .48s both,pc-walk-off 1.9s cubic-bezier(.45,0,.55,1) calc(var(--dur) - 2.15s) both}
& .pc-walk .pc-card{overflow:visible}
@keyframes pc-cover-in{from{opacity:0}to{opacity:1}}
@keyframes pc-drift{from{transform:translate(-2%,-1%)}to{transform:translate(2%,1.5%)}}
@keyframes pc-step{from{transform:rotate(22deg)}to{transform:rotate(-22deg)}}
@keyframes pc-step-b{from{transform:rotate(-22deg)}to{transform:rotate(22deg)}}
@keyframes pc-walk-off{0%{transform:none}16%{transform:translateY(calc(-186px * var(--scale)))}100%{transform:translate(calc(var(--w) * 1.12cqw),calc(-186px * var(--scale)))}}
@keyframes pc-dot{from{transform:scale(0)}to{transform:scale(1)}}
@keyframes pc-ring{0%{opacity:.9;transform:scale(.2)}100%{opacity:0;transform:scale(1.25)}}
@keyframes pc-draw{from{clip-path:inset(calc(var(--bottom) * 1cqh) 0 calc((100 - var(--bottom)) * 1cqh) 0)}to{clip-path:inset(calc((var(--top) - 3) * 1cqh) 0 calc((100 - var(--bottom) - 3) * 1cqh) 0)}}
@keyframes pc-card{from{opacity:0;filter:blur(calc(14px * var(--scale)));transform:translateY(calc(46px * var(--scale))) scale(.82)}to{opacity:1;filter:blur(0);transform:none}}
@keyframes pc-card-out{from{opacity:1;filter:blur(0);transform:none}to{opacity:0;filter:blur(calc(10px * var(--scale)));transform:translateY(calc(26px * var(--scale))) scale(.9)}}
@keyframes pc-fade{to{opacity:0}}
@keyframes pc-glint{from{transform:translateX(-120%) skewX(-14deg)}to{transform:translateX(560%) skewX(-14deg)}}
@keyframes pc-badge{from{opacity:0;transform:scale(.5) rotate(-18deg)}to{opacity:1;transform:none}}
@keyframes pc-halo{from{opacity:.5;transform:scale(.94)}to{opacity:1;transform:scale(1.06)}}
@keyframes pc-icon{from{clip-path:circle(0% at 50% 50%)}to{clip-path:circle(75% at 50% 50%)}}
@keyframes pc-rise{from{opacity:0;transform:translateY(calc(14px * var(--scale)))}to{opacity:1;transform:none}}
@keyframes pc-word{from{transform:translateY(110%)}to{transform:none}}
@keyframes pc-row{from{opacity:0;transform:translateX(calc(-22px * var(--scale)))}to{opacity:1;transform:none}}
@keyframes pc-sweep{from{clip-path:polygon(50% 50%,50% -20%,50% -20%,50% -20%,50% -20%,50% -20%,50% -20%)}to{clip-path:polygon(50% 50%,50% -20%,120% -20%,120% 120%,-20% 120%,-20% -20%,50% -20%)}}
& .pc-cover.pc-family-stat .pc-dial{width:calc(250px * var(--scale));height:calc(250px * var(--scale))}& .pc-cover .pc-value{font-size:calc(104px * var(--scale))}& .pc-cover .pc-unit{font-size:calc(32px * var(--scale))}
@keyframes pc-open{from{grid-template-rows:0fr}to{grid-template-rows:1fr}}
@keyframes pc-turn{from{transform:rotate(0)}to{transform:rotate(360deg)}}
"""

def _covers(item: dict[str, Any]) -> bool:
    """A cover fills the frame, so QA treats it like any full-frame graphic: a planned hold, not frozen video."""
    return item.get("stage") == "cover"


SLOTS = (
    Slot("title", limit=30),
    Slot("kicker", limit=14, required=False),
    Slot("tone", "choice", required=False, choices=TONES),
    Slot("items", "list", limit=4, required=False, each=24),
    Slot("stage", "choice", required=False, choices=("open", "cover")),
    Slot("act", "choice", required=False, choices=("walk",)),
    Slot("ax", "number", required=False),
    Slot("ay", "number", required=False),
    Slot("cx", "number", required=False),
    Slot("cy", "number", required=False),
    Slot("w", "number", required=False),
)
POINT = Format(
    "point_callout", "Point callout",
    "A card unfolds from where the presenter points, with a spark, a drawn connector and timed rows.",
    "explain", ("hook", "explain"),
    (*SLOTS, Slot("icon", "choice", required=False, choices=tuple(ICONS))),
    {"title": "Protect your *sleep*", "kicker": "Tip 01", "icon": "moon", "ax": 30, "ay": 36, "cx": 50, "cy": 30,
     "items": [{"text": "Keep the schedule", "icon": "clock"}, {"text": "Tell your provider", "icon": "chat"}]},
    partial(_draw, family="icon"), CSS.replace("&", ".f-point_callout"),
    seconds=5, shortest=2.5, longest=14, area="full", tall=520, sound=None, leaves=False, needs_alpha=True, under_captions=True,
    opaque_when=_covers,
)
STAT = Format(
    "point_stat", "Point stat",
    "One number on a sweeping dial unfolds from where the presenter points, with its label beside it.",
    "explain", ("hook", "explain"),
    (*SLOTS, Slot("value", limit=5), Slot("unit", limit=8, required=False), Slot("fill", "number", required=False)),
    {"title": "Hours of sleep", "value": "3", "unit": "hrs", "fill": 0.25, "ax": 34, "ay": 38, "cx": 50, "cy": 30},
    partial(_draw, family="stat"), CSS.replace("&", ".f-point_stat"),
    seconds=4, shortest=2.5, longest=12, area="full", tall=420, sound=None, leaves=False, needs_alpha=True, under_captions=True,
    opaque_when=_covers,
)
FORMATS = [POINT, STAT]
