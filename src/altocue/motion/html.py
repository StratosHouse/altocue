"""Turn a timeline into one HTML composition. Motion is plain CSS, seeked per clip."""

from __future__ import annotations

from html import escape
from typing import Any

from ..brand import Brand

CSS = """
@font-face{font-family:"Brand";src:url("%(font)s");font-weight:100 900}
:root{--ink:%(ink)s;--soft:%(soft)s;--accent:%(accent)s;--panel:%(panel)s;--surface:%(surface)s;
--ease:cubic-bezier(.16,1,.3,1);--scale:%(scale)s;--gutter:%(gutter)spx}
*{box-sizing:border-box}
html,body{margin:0;padding:0;background:transparent}
#root{position:relative;width:%(width)spx;height:%(height)spx;overflow:hidden;background:transparent;
font-family:"Brand",Arial,sans-serif;color:var(--ink);-webkit-font-smoothing:antialiased;text-rendering:geometricPrecision}
.clip{position:absolute}
em{font-style:normal;color:var(--accent)}

/* Captions */
.cap{left:var(--gutter);right:calc(var(--gutter) + var(--rail,0px));top:var(--cap-y);display:flex;flex-wrap:wrap;justify-content:center;
align-items:baseline;gap:.08em .26em;font-size:calc(var(--cap-size) * 1px);font-weight:700;line-height:1.12;letter-spacing:-.018em;text-align:center;
text-wrap:balance;text-shadow:0 0 3px rgba(0,0,0,.5),0 2px 4px rgba(0,0,0,.62),0 4px 22px rgba(0,0,0,.62),0 0 44px rgba(0,0,0,.34)}
.cap.enter{animation:capin .13s var(--ease) both}
.cap b{font-weight:inherit;display:inline-block}
.cap b.on{color:var(--accent);transform:scale(1.07)}
.cap.bold{text-transform:uppercase;font-weight:800;letter-spacing:-.01em;line-height:1.04;
text-shadow:0 0 2px #000,0 4px 0 rgba(0,0,0,.65),0 8px 30px rgba(0,0,0,.5)}
.cap.boxed{text-shadow:none;left:auto;right:auto;width:100%%;padding:0 var(--gutter)}
.cap.boxed .plate{display:inline-flex;flex-wrap:wrap;justify-content:center;gap:.08em .24em;padding:.32em .6em .36em;border-radius:.42em;
background:color-mix(in srgb,var(--panel) 84%%,transparent);box-shadow:0 0 0 1px rgba(255,255,255,.1) inset,0 18px 48px rgba(0,0,0,.34);max-width:100%%}
.cap.rail{font-weight:600;font-size:calc(var(--cap-size) * .76px);letter-spacing:-.008em}
.cap.rail b.on{transform:none}
@keyframes capin{from{transform:scale(.94)}to{transform:none}}

/* Shared surfaces */
.glass{background:color-mix(in srgb,var(--surface) 78%%,transparent);box-shadow:0 0 0 1px rgba(211,238,252,.13) inset,0 1px 0 rgba(255,255,255,.07) inset,0 24px 64px rgba(0,0,0,.34);
backdrop-filter:blur(26px) saturate(1.2);-webkit-backdrop-filter:blur(26px) saturate(1.2)}
.rise{animation:rise .42s var(--ease) both}
@keyframes rise{from{transform:translateY(calc(26px * var(--scale)));opacity:0}to{transform:none;opacity:1}}
.leave{animation:leave .2s ease-in forwards;animation-delay:var(--leave)}
@keyframes leave{to{opacity:0;transform:translateY(calc(-8px * var(--scale)))}}

/* Hook */
.hook{left:var(--gutter);right:calc(var(--gutter) + var(--rail,0px));top:var(--top);text-align:left}
.hook::before{content:"";position:absolute;z-index:-1;inset:-34%% -14%% -38%% -14%%;
background:radial-gradient(closest-side,rgba(6,10,14,.66),rgba(6,10,14,.5) 46%%,rgba(6,10,14,0));pointer-events:none}
.hook .eyebrow{display:inline-flex;align-items:center;gap:.6em;font-size:calc(30px * var(--scale));font-weight:500;letter-spacing:.015em;color:var(--accent);margin-bottom:.8em}
.hook .eyebrow i{width:.5em;height:.5em;border-radius:50%%;background:var(--accent);box-shadow:0 0 18px var(--accent)}
.hook h1{margin:0;font-size:calc(104px * var(--scale));font-weight:600;line-height:.98;letter-spacing:-.04em;text-wrap:balance;text-shadow:0 2px 6px rgba(0,0,0,.34),0 10px 50px rgba(0,0,0,.5)}
.hook .line{display:block;overflow:hidden;padding-bottom:.08em;margin-bottom:-.08em}
.hook .line span{display:block;animation:lineup .6s var(--ease) both}
@keyframes lineup{from{transform:translateY(105%%)}to{transform:none}}

/* Chip */
.chip{left:var(--gutter);top:var(--top);display:inline-flex;align-items:center;gap:calc(14px * var(--scale));height:calc(70px * var(--scale));
padding:0 calc(30px * var(--scale)) 0 calc(24px * var(--scale));border-radius:999px;font-size:calc(34px * var(--scale));font-weight:500;letter-spacing:-.01em}
.chip i{width:calc(13px * var(--scale));height:calc(13px * var(--scale));border-radius:50%%;background:var(--accent);box-shadow:0 0 14px var(--accent)}

/* Lower third */
.lt{left:var(--gutter);top:var(--lt-y);display:flex;flex-direction:column;gap:calc(5px * var(--scale));padding:calc(24px * var(--scale)) calc(38px * var(--scale)) calc(26px * var(--scale));border-radius:calc(26px * var(--scale));max-width:calc(100%% - var(--gutter) * 2 - var(--rail,0px))}
.lt strong{font-size:calc(42px * var(--scale));font-weight:600;letter-spacing:-.022em;line-height:1.1}
.lt span{font-size:calc(31px * var(--scale));font-weight:400;color:var(--soft);letter-spacing:-.005em}
.lt::before{content:"";position:absolute;left:calc(14px * var(--scale));top:26%%;bottom:26%%;width:calc(3px * var(--scale));border-radius:9px;background:var(--accent);animation:growy .5s .12s var(--ease) both;transform-origin:50%% 50%%}
@keyframes growy{from{transform:scaleY(0)}to{transform:none}}

/* Stat */
.stat{left:var(--gutter);right:calc(var(--gutter) + var(--rail,0px));max-width:calc(940px * var(--scale));top:var(--top);padding:calc(38px * var(--scale)) calc(44px * var(--scale));border-radius:calc(34px * var(--scale))}
.stat .num{display:block;font-size:calc(168px * var(--scale));font-weight:600;line-height:.96;letter-spacing:-.055em;color:var(--accent)}
.stat .lab{display:block;margin-top:calc(14px * var(--scale));font-size:calc(38px * var(--scale));line-height:1.18;letter-spacing:-.015em;color:var(--ink)}

/* Quote */
.quote{left:var(--gutter);right:calc(var(--gutter) + var(--rail,0px));max-width:calc(940px * var(--scale));top:var(--top);padding:calc(42px * var(--scale)) calc(46px * var(--scale)) calc(40px * var(--scale));border-radius:calc(34px * var(--scale));font-size:calc(52px * var(--scale));font-weight:500;line-height:1.14;letter-spacing:-.025em}
.quote cite{display:block;margin-top:calc(20px * var(--scale));font-size:calc(28px * var(--scale));font-style:normal;font-weight:400;color:var(--soft);letter-spacing:0}

/* Steps */
.steps{left:var(--gutter);right:calc(var(--gutter) + var(--rail,0px));max-width:calc(940px * var(--scale));top:var(--top);padding:calc(30px * var(--scale)) calc(34px * var(--scale));border-radius:calc(32px * var(--scale))}
.steps h2{margin:0 0 calc(18px * var(--scale));font-size:calc(30px * var(--scale));font-weight:500;color:var(--soft);letter-spacing:-.005em}
.steps .row{position:relative;display:flex;align-items:center;gap:calc(20px * var(--scale));padding:calc(15px * var(--scale)) 0;font-size:calc(42px * var(--scale));font-weight:500;letter-spacing:-.02em;color:var(--ink);animation:stepon .36s var(--ease) both;animation-delay:var(--at)}
.steps .row+.row{border-top:1px solid rgba(255,255,255,.08)}
.steps .row i{flex:none;display:grid;place-items:center;width:calc(46px * var(--scale));height:calc(46px * var(--scale));border-radius:50%%;font-style:normal;font-size:calc(24px * var(--scale));font-weight:600;color:var(--panel);background:var(--accent)}
@keyframes stepon{from{opacity:.16;transform:translateX(calc(-10px * var(--scale)))}to{opacity:1;transform:none}}

/* Title */
.title{inset:0;display:flex;flex-direction:column;justify-content:center;padding:0 calc(var(--gutter) * 1.4);background:var(--panel)}
.title h1{margin:0;font-size:calc(112px * var(--scale));font-weight:600;line-height:1;letter-spacing:-.04em}
.title p{margin:calc(26px * var(--scale)) 0 0;font-size:calc(40px * var(--scale));color:var(--soft);letter-spacing:-.015em}

/* End card */
.end{inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:0 var(--gutter);
background:radial-gradient(90%% 60%% at 50%% 42%%,color-mix(in srgb,var(--surface) 92%%,var(--accent) 8%%),var(--panel) 72%%);animation:endin .4s ease-out both}
@keyframes endin{from{opacity:0}to{opacity:1}}
.end img{max-width:calc(420px * var(--scale));max-height:calc(190px * var(--scale));margin-bottom:calc(46px * var(--scale));animation:rise .6s .08s var(--ease) both}
.end h1{margin:0;font-size:calc(112px * var(--scale));font-weight:600;line-height:1.02;letter-spacing:-.04em;text-wrap:balance;animation:rise .6s .14s var(--ease) both}
.end p{margin:calc(30px * var(--scale)) 0 0;display:inline-flex;align-items:center;height:calc(84px * var(--scale));padding:0 calc(42px * var(--scale));border-radius:999px;font-size:calc(38px * var(--scale));font-weight:500;color:var(--panel);background:var(--accent);animation:rise .6s .22s var(--ease) both}
"""


def geometry(width: int, height: int) -> dict[str, float]:
    """Layout per canvas. Tall video keeps clear of the app's own buttons."""
    tall = height > width
    scale = 1.0 if tall else 0.9 if width > height else 0.86
    return {
        "scale": scale,
        "gutter": 72 if tall else 96,
        "rail": 56 if tall else 0,
        "top": round(height * 0.17) if tall else round(height * 0.12),
        "cap_y": round(height * 0.675) if tall else round(height * 0.74),
        "lt_y": round(height * 0.565) if tall else round(height * 0.68),
    }


def _attrs(start: float, end: float, track: int) -> str:
    return f'data-start="{start:.3f}" data-duration="{max(end - start, 0.04):.3f}" data-track-index="{track}"'


def _style(item: dict[str, Any], *, leave: bool = False) -> str:
    """Inline settings for one graphic: where it sits, and when it starts to leave."""
    parts = []
    if "y" in item:
        parts.append(f"top:{float(item['y']):g}px")
    if leave:
        parts.append(f"--leave:{max(item['end'] - item['start'] - 0.2, 0):.3f}s")
    return f'style="{";".join(parts)}"' if parts else ""


def caption_html(page: dict[str, Any], style: str, track: int) -> str:
    """One clip per spoken word. Only the first one plays the entrance."""
    out = []
    words = page["words"]
    for index, word in enumerate(words):
        start = page["start"] if index == 0 else word["start"]
        end = page["end"] if index == len(words) - 1 else words[index + 1]["start"]
        if end - start < 0.02:
            continue
        spans = "".join(f'<b class="on">{escape(w["text"])}</b>' if i == index else f"<b>{escape(w['text'])}</b>" for i, w in enumerate(words))
        body = f'<span class="plate">{spans}</span>' if style == "boxed" else spans
        enter = " enter" if index == 0 and style != "rail" else ""
        out.append(f'<div class="clip cap {style}{enter}" {_attrs(start, end, track)}>{body}</div>')
    return "\n".join(out)


def _hook_lines(text: str) -> str:
    lines = [line for line in text.split("\n") if line.strip()]
    return "".join(f'<span class="line"><span style="animation-delay:{i * 0.07:.2f}s">{_emphasis(line)}</span></span>' for i, line in enumerate(lines))


def _emphasis(text: str) -> str:
    """`*word*` in copy becomes the accent colour."""
    parts = escape(text).split("*")
    return "".join(f"<em>{part}</em>" if i % 2 else part for i, part in enumerate(parts))


def item_html(item: dict[str, Any], brand: Brand, track: int) -> str:
    kind, attrs = item["type"], _attrs(item["start"], item["end"], track)
    if kind == "hook":
        eyebrow = f'<div class="eyebrow rise"><i></i>{escape(item["eyebrow"])}</div>' if item.get("eyebrow") else ""
        return f'<div class="clip hook leave" {attrs} {_style(item, leave=True)}>{eyebrow}<h1>{_hook_lines(item["text"])}</h1></div>'
    if kind == "chip":
        return f'<div class="clip chip glass rise" {attrs} {_style(item)}><i></i>{escape(item["text"])}</div>'
    if kind == "lower_third":
        sub = f"<span>{escape(item['subtitle'])}</span>" if item.get("subtitle") else ""
        return f'<div class="clip lt glass rise" {attrs} {_style(item)}><strong>{escape(item["title"])}</strong>{sub}</div>'
    if kind == "stat":
        return f'<div class="clip stat glass rise" {attrs} {_style(item)}><span class="num">{escape(str(item["value"]))}</span><span class="lab">{escape(item.get("label", ""))}</span></div>'
    if kind == "quote":
        cite = f"<cite>{escape(item['by'])}</cite>" if item.get("by") else ""
        return f'<div class="clip quote glass rise" {attrs} {_style(item)}>{_emphasis(item["text"])}{cite}</div>'
    if kind == "steps":
        rows = "".join(
            f'<div class="row" style="--at:{max(step["at"] - item["start"], 0):.3f}s"><i>{n}</i>{escape(step["text"])}</div>' for n, step in enumerate(item["items"], 1)
        )
        title = f"<h2>{escape(item['title'])}</h2>" if item.get("title") else ""
        return f'<div class="clip steps glass rise" {attrs} {_style(item)}>{title}{rows}</div>'
    if kind == "title":
        sub = f"<p>{escape(item['subtitle'])}</p>" if item.get("subtitle") else ""
        return f'<div class="clip title" {attrs}><h1>{_emphasis(item["text"])}</h1>{sub}</div>'
    if kind == "endcard":
        logo = f'<img src="{escape(brand.logo.name)}" alt="">' if brand.logo else ""
        cta = f"<p>{escape(item['cta'])}</p>" if item.get("cta") else ""
        return f'<div class="clip end" {attrs}>{logo}<h1>{_emphasis(item.get("headline", ""))}</h1>{cta}</div>'
    raise ValueError(kind)


def compose(timeline: dict[str, Any], brand: Brand) -> str:
    width, height = timeline["width"], timeline["height"]
    geo = geometry(width, height)
    colors = brand.colors
    css = CSS % {
        "font": brand.font_file.name,
        "ink": colors["ink"],
        "soft": colors["soft"],
        "accent": colors["accent"],
        "panel": colors["panel"],
        "surface": colors["surface"],
        "scale": geo["scale"],
        "gutter": geo["gutter"],
        "width": width,
        "height": height,
    }
    size = int(brand.caption["size"]) * (geo["scale"] if width > height else 1)
    tokens = f'--cap-y:{geo["cap_y"]}px;--cap-size:{size};--top:{geo["top"]}px;--lt-y:{geo["lt_y"]}px;--rail:{geo["rail"]}px'
    style = brand.caption["style"]
    upper = brand.caption.get("uppercase")
    captions = "\n".join(caption_html({**p, "words": [{**w, "text": w["text"].upper() if upper else w["text"]} for w in p["words"]]}, style, 1) for p in timeline["captions"])
    items = "\n".join(item_html(item, brand, 2 + index) for index, item in enumerate(timeline["items"]))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:">
<style>{css}</style></head>
<body>
<div id="root" data-composition-id="root" data-start="0" data-duration="{timeline['total']:.3f}" data-width="{width}" data-height="{height}" style="{tokens}">
{captions}
{items}
</div>
</body></html>
"""
