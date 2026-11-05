"""Original, time-pure Studio graphics. No media, paid assets or external runtime.

All motion uses one continuous, non-overshooting arrival and a deliberate hold.
The portrait layout keeps essential content inside the Studio's stricter safe box.
Sound roles are suggestions, never a dependency or permission to fetch audio.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from . import Format, Slot, kit


def _rows(item: dict[str, Any]) -> list[str]:
    return [str(row.get("text", "")) if isinstance(row, dict) else str(row) for row in item.get("items", [])]


def _labels(item: dict[str, Any], cls: str) -> str:
    rows = []
    for index, row in enumerate(item.get("items", [])):
        text = str(row.get("text", "")) if isinstance(row, dict) else str(row)
        style = f"--n:{index}"
        if isinstance(row, dict) and isinstance(row.get("at"), int | float):
            delay = max(float(row["at"]) - float(item.get("start", 0)), 0)
            style += f";animation-delay:{delay:.4f}s;animation-duration:.42s"
        rows.append(
            f'<div class="s2-unit {cls}" style="{style}"><span class="s2-index">{index + 1:02d}</span>'
            f'<b>{kit.emphasis(text)}</b></div>'
        )
    return "".join(rows)


def _type(item: dict[str, Any], variant: str) -> str:
    text = item.get("text") or "One clear idea"
    words = kit.tokens(str(text))
    letters = " ".join(
        f'<span class="s2-mask" style="--n:{index}"><span class="s2-word{" s2-hot" if hot else ""}">{kit.esc(word)}</span></span>'
        for index, (word, hot) in enumerate(words)
    )
    return f'<div class="s2-type s2-{variant}"><div class="s2-type-line">{letters}</div><div class="s2-type-rule"></div></div>'


def _depth(item: dict[str, Any], variant: str) -> str:
    return f'<div class="s2-depth s2-{variant}"><div class="s2-depth-floor"></div>{_labels(item, "s2-plane")}</div>'


def _route(item: dict[str, Any], variant: str) -> str:
    route = '<div class="s2-route-line"><div class="s2-route-fill"></div></div>'
    return f'<div class="s2-route s2-{variant}">{route}{_labels(item, "s2-node")}</div>'


def _task(item: dict[str, Any], variant: str) -> str:
    if variant == "link_receipt":
        source, artifact, target = (_rows(item) + ["", "", ""])[:3]
        duration = max(float(item.get("end", 6)) - float(item.get("start", 0)), 0.1)
        receiver = (item.get("items") or [None])[-1]
        at = max(float(receiver["at"]) - float(item.get("start", 0)), 0) if isinstance(receiver, dict) and isinstance(receiver.get("at"), int | float) else duration * 0.58
        travel = min(0.8, duration * 0.2)
        chain = '<svg viewBox="0 0 48 32" aria-hidden="true"><path d="M19 8h-7a8 8 0 0 0 0 16h9m8-16h7a8 8 0 0 1 0 16h-9M16 16h16"/></svg>'
        size = 40 if len(artifact.replace("*", "")) > 12 else 54
        return (
            f'<div class="s2-receipt" style="--send:{at:.4f}s;--travel:{travel:.4f}s">'
            '<svg class="s2-receipt-route" viewBox="0 0 660 700" preserveAspectRatio="none" aria-hidden="true">'
            '<path d="M150 80C400 80 245 610 520 610"/></svg>'
            f'<div class="s2-owner s2-from"><span>01</span><b>{kit.emphasis(source)}</b></div>'
            '<div class="s2-file"><span class="s2-file-icon"><i></i><i></i><i></i></span>'
            f'<b style="font-size:calc({size}px * var(--scale))">{kit.emphasis(artifact)}</b><span class="s2-file-link">{chain}</span></div>'
            f'<div class="s2-owner s2-to"><span>02</span><b>{kit.emphasis(target)}</b></div></div>'
        )
    if variant == "calendar_lift":
        days = ["Mon", "Tue", "Wed"]
        cells = "".join(
            f'<div class="s2-day" style="--n:{index}"><span>{days[index]}</span><b>{kit.emphasis(text)}</b></div>'
            for index, text in enumerate(_rows(item))
        )
        circle = ""
        if isinstance(item.get("circle_at"), int | float):
            delay = max(float(item["circle_at"]) - float(item.get("start", 0)), 0)
            circle = (
                f'<div class="s2-calendar-circle" style="--circle-at:{delay:.4f}s" aria-hidden="true"><svg viewBox="0 0 300 160">'
                '<ellipse cx="150" cy="80" rx="135" ry="58"/></svg></div>'
            )
        return f'<div class="s2-task s2-calendar_lift"><div class="s2-calendar-grid">{cells}</div>{circle}<div class="s2-calendar-route"></div></div>'
    return f'<div class="s2-task s2-{variant}"><div class="s2-paper-edge"></div>{_labels(item, "s2-task-row")}<div class="s2-selection"></div></div>'


def _product(item: dict[str, Any], variant: str) -> str:
    rows = _rows(item)
    if variant == "sound_wave":
        # An illustrative signal, never a measured waveform or an improvement claim.
        heights = [24, 41, 63, 37, 74, 50, 88, 64, 100, 74, 48, 80, 56, 34, 61, 44, 25]
        bars = "".join(f'<span style="--n:{i};--h:{h}%"></span>' for i, h in enumerate(heights))
        content = f'<div class="s2-wave">{bars}</div><b class="s2-product-label">{kit.emphasis(rows[0] if rows else "Voice first")}</b>'
    else:
        content = _labels(item, "s2-track")
    return f'<div class="s2-product s2-{variant}"><div class="s2-window-rule"><i></i><i></i><i></i></div>{content}<div class="s2-playhead"></div></div>'


def _close(item: dict[str, Any], variant: str) -> str:
    return f'<div class="s2-close s2-{variant}">{_labels(item, "s2-close-row")}<div class="s2-close-rule"></div></div>'


DRAW = {"type": _type, "depth": _depth, "route": _route, "task": _task, "product": _product, "close": _close}


def _draw(item: dict[str, Any], family: str, variant: str) -> str:
    title = kit.emphasis(item.get("title", ""))
    note = kit.esc(item.get("note", "Illustrative sample"))
    return (
        f'<div class="s2-stage"><div class="s2-light"></div><div class="s2-safe s2-family-{family}">'
        f'<h2 class="s2-title">{title}</h2><div class="s2-art">{DRAW[family](item, variant)}</div>'
        f'<p class="s2-note">{note}</p></div></div>'
    )


CSS = """
& .s2-stage{position:absolute;inset:0;container-type:size;overflow:hidden;background:var(--panel);color:var(--ink);--s2-ease:cubic-bezier(.22,.8,.22,1)}
& .s2-light{position:absolute;inset:0;background:radial-gradient(ellipse at 34% 32%,color-mix(in srgb,var(--accent) 7%,transparent),transparent 64%)}
& .s2-safe{position:absolute;left:11.111cqw;top:15cqh;width:61.111cqw;height:50cqh;display:flex;flex-direction:column;gap:calc(32px * var(--scale))}
& .s2-title{font-size:calc(76px * var(--scale));line-height:1.04;letter-spacing:-.045em;font-weight:560;margin:0;text-wrap:balance;position:relative;z-index:3}
& em{font-style:normal;color:var(--accent)}
& .s2-art{position:relative;flex:1;min-height:0;perspective:1600px}
& .s2-note{font-size:calc(38px * var(--scale));line-height:1.15;color:var(--soft);margin:0}
& .s2-unit{box-sizing:border-box}
& .s2-unit b{font-size:calc(50px * var(--scale));line-height:1.08;font-weight:520;letter-spacing:-.025em;overflow-wrap:break-word}
& .s2-index{font-size:calc(27px * var(--scale));font-weight:450;letter-spacing:.03em;color:var(--soft)}
& .s2-type{position:absolute;inset:0;display:flex;flex-direction:column;justify-content:center;gap:calc(40px * var(--scale))}
& .s2-type-line{font-size:calc(100px * var(--scale));font-weight:550;line-height:1.02;letter-spacing:-.055em;position:relative}
& .s2-mask{display:inline-block;overflow:hidden;vertical-align:bottom;padding-bottom:.06em}
& .s2-word{display:inline-block;animation:s2-mask calc(var(--dur) * .13) var(--s2-ease) both;animation-delay:calc(var(--n) * var(--dur) * .045)}
& .s2-hot{color:var(--accent)}
& .s2-type-rule{height:calc(3px * var(--scale));width:100%;background:var(--accent);transform-origin:left;animation:s2-line calc(var(--dur) * .20) calc(var(--dur) * .16) var(--s2-ease) both}
& .s2-focus_stack .s2-mask{display:block}
& .s2-focus_stack .s2-word{animation-name:s2-focus}
& .s2-kinetic_bridge .s2-type-line{font-size:calc(86px * var(--scale))}
& .s2-kinetic_bridge .s2-word{animation-name:s2-bridge}
& .s2-signal_word .s2-type-rule{height:calc(14px * var(--scale));width:30%;border-radius:10px}
& .s2-depth{position:absolute;inset:0;transform-style:preserve-3d}
& .s2-depth-floor{position:absolute;left:5%;right:5%;bottom:4%;height:22%;background:radial-gradient(ellipse,color-mix(in srgb,var(--accent) 13%,transparent),transparent 70%);transform:rotateX(63deg)}
& .s2-plane{position:absolute;left:calc(var(--n) * 5%);right:calc((2 - var(--n)) * 5%);top:calc(7% + var(--n) * 25%);min-height:24%;padding:calc(28px * var(--scale));border:1px solid color-mix(in srgb,var(--ink) 20%,transparent);border-radius:calc(20px * var(--scale));background:linear-gradient(125deg,color-mix(in srgb,var(--surface) 90%,var(--ink)),var(--surface));box-shadow:0 16px 40px #0003;display:flex;align-items:center;gap:calc(26px * var(--scale));transform-origin:50% 80%;animation:s2-depth calc(var(--dur) * .24) var(--s2-ease) both;animation-delay:calc(var(--dur) * (.045 + var(--n) * .08))}
& .s2-plane:last-child{background:var(--accent);color:var(--panel);border-color:var(--accent)}
& .s2-plane:last-child .s2-index{color:var(--panel)}
& .s2-depth_layers .s2-plane{animation-name:s2-iso;box-shadow:0 3px 0 #22333b,0 8px 0 #1b2931,0 28px 42px #0005}
& .s2-depth_handoff .s2-plane{animation-name:s2-handoff}
& .s2-focus_deck .s2-plane{left:0;right:0;animation-name:s2-focus}
& .s2-object_orbit .s2-plane{animation-name:s2-orbit;transform-origin:0 100%;width:44%;right:auto;min-height:30%;padding:calc(22px * var(--scale));justify-content:center}
& .s2-object_orbit .s2-plane .s2-index{display:none}
& .s2-object_orbit .s2-plane:nth-of-type(2){left:0;top:4%}
& .s2-object_orbit .s2-plane:nth-of-type(3){left:55%;top:31%}
& .s2-object_orbit .s2-plane:nth-of-type(4){left:10%;top:63%}
& .s2-route{position:absolute;inset:0;display:flex;flex-direction:column;justify-content:space-between;padding-block:4%}
& .s2-route-line{position:absolute;top:12%;bottom:12%;left:calc(29px * var(--scale));width:calc(3px * var(--scale));background:color-mix(in srgb,var(--ink) 16%,transparent)}
& .s2-route-fill{position:absolute;inset:0;background:var(--accent);transform-origin:top;animation:s2-line-y calc(var(--dur) * .43) calc(var(--dur) * .07) var(--s2-ease) both}
& .s2-node{position:relative;display:flex;align-items:center;gap:calc(36px * var(--scale));animation:s2-reveal calc(var(--dur) * .15) var(--s2-ease) both;animation-delay:calc(var(--dur) * (.03 + var(--n) * .15))}
& .s2-node .s2-index{display:grid;place-content:center;width:calc(62px * var(--scale));height:calc(62px * var(--scale));border-radius:50%;border:1px solid var(--accent);color:var(--accent);background:var(--panel);flex-shrink:0}
& .s2-role_cluster .s2-node{padding:calc(16px * var(--scale));border-radius:calc(18px * var(--scale));background:var(--surface)}
& .s2-role_cluster .s2-route-line{left:calc(45px * var(--scale))}
& .s2-linked_steps .s2-node{animation-name:s2-handoff}
& .s2-workflow_lanes .s2-node{border-bottom:1px solid color-mix(in srgb,var(--ink) 15%,transparent);padding-bottom:calc(20px * var(--scale))}
& .s2-task{position:absolute;inset:0;border-radius:calc(18px * var(--scale));padding:calc(26px * var(--scale));background:#ece9e0;color:#17242a;box-shadow:0 24px 60px #0003;display:flex;flex-direction:column;justify-content:center;gap:calc(22px * var(--scale));animation:s2-paper calc(var(--dur) * .19) var(--s2-ease) both}
& .s2-paper-edge{position:absolute;inset:8px -7px -8px 7px;border:1px solid #ece9e040;border-radius:inherit;z-index:-1;transform:rotate(1.1deg)}
& .s2-task-row{position:relative;display:flex;gap:calc(22px * var(--scale));align-items:center;padding:calc(23px * var(--scale));min-height:24%;border-radius:calc(12px * var(--scale));border:1px solid #17242a20}
& .s2-task-row .s2-index{color:#53605c}
& .s2-task-row:nth-of-type(3){background:#bddfce;animation:s2-task-select calc(var(--dur) * .22) calc(var(--dur) * .18) var(--s2-ease) both}
& .s2-selection{position:absolute;inset:0;border-radius:inherit;pointer-events:none}
& .s2-select_focus .s2-task-row:nth-of-type(3){animation-name:s2-target}
& .s2-approval_gate .s2-task-row:nth-of-type(4){animation:s2-approve calc(var(--dur) * .18) calc(var(--dur) * .39) var(--s2-ease) both}
& .s2-calendar_lift .s2-task-row:nth-of-type(3){animation-name:s2-lift}
& .s2-calendar-grid{height:80%;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:calc(12px * var(--scale));align-items:start}
& .s2-day{height:100%;display:flex;flex-direction:column;gap:calc(24px * var(--scale));padding:calc(12px * var(--scale));border-radius:calc(9px * var(--scale));border:1px solid #17242a25;background:#d7dcd1}
& .s2-day span{font-size:calc(44px * var(--scale));line-height:1.1;border-bottom:1px solid #17242a30;padding-bottom:calc(18px * var(--scale))}
& .s2-day b{font-size:calc(48px * var(--scale));line-height:1.12;font-weight:500;letter-spacing:-.025em}
& .s2-day:nth-child(2){background:#bddfce;animation:s2-lift calc(var(--dur) * .22) calc(var(--dur) * .18) var(--s2-ease) both}
& .s2-calendar-route{height:3px;width:70%;background:#34634f;transform-origin:left;animation:s2-line calc(var(--dur) * .20) calc(var(--dur) * .40) var(--s2-ease) both}
& .s2-receipt{position:absolute;inset:0;perspective:1200px}
& .s2-receipt-route{position:absolute;inset:0;width:100%;height:100%;fill:none;stroke:var(--accent);stroke-width:3;opacity:.35;stroke-dasharray:1000;stroke-dashoffset:1000;animation:s2-link-route .7s .2s var(--s2-ease) both}
& .s2-owner{position:absolute;width:45%;min-height:20%;padding:calc(22px * var(--scale));border:1px solid color-mix(in srgb,var(--ink) 20%,transparent);border-radius:calc(20px * var(--scale));background:linear-gradient(130deg,var(--surface),var(--panel));box-shadow:0 16px 36px #0004;display:flex;flex-direction:column;justify-content:center;gap:calc(12px * var(--scale));animation:s2-reveal .5s var(--s2-ease) both}
& .s2-owner span{font-size:calc(25px * var(--scale));color:var(--soft)}
& .s2-owner b{font-size:calc(42px * var(--scale));font-weight:540;line-height:1.08;letter-spacing:-.025em;overflow-wrap:anywhere}
& .s2-from{left:0;top:0}
& .s2-to{right:0;bottom:0;animation:s2-reveal .5s .25s var(--s2-ease) both,s2-receive var(--travel) var(--send) var(--s2-ease) forwards}
& .s2-file{position:absolute;left:8%;top:30%;width:75%;min-height:33%;padding:calc(24px * var(--scale));border-radius:calc(20px * var(--scale));background:linear-gradient(130deg,#f1eee5,#dcded1);color:#172a26;box-shadow:0 3px 0 #9aa79c,0 9px 0 #4f615a,0 24px 48px #0005;display:flex;flex-direction:column;gap:calc(15px * var(--scale));transform-style:preserve-3d;animation:s2-file-in .55s var(--s2-ease) both,s2-file-send var(--travel) var(--send) var(--s2-ease) forwards}
& .s2-file>b{font-weight:550;line-height:1.08;letter-spacing:-.025em;overflow-wrap:anywhere}
& .s2-file-icon{display:flex;flex-direction:column;gap:calc(5px * var(--scale));width:calc(44px * var(--scale));padding:calc(10px * var(--scale));border:1px solid #172a2640;border-radius:calc(6px * var(--scale))}
& .s2-file-icon i{height:2px;background:#172a2670;width:100%}
& .s2-file-icon i:last-child{width:60%}
& .s2-file-link{display:block;width:calc(72px * var(--scale));padding:calc(7px * var(--scale)) calc(12px * var(--scale));border-radius:100px;background:#b4efce}
& .s2-file-link svg{display:block;width:100%;height:calc(24px * var(--scale));fill:none;stroke:#244c3c;stroke-width:3;stroke-linecap:round}
& .s2-product{position:absolute;inset:0;border:1px solid color-mix(in srgb,var(--ink) 24%,transparent);border-radius:calc(20px * var(--scale));background:linear-gradient(145deg,var(--surface),var(--panel));padding:calc(30px * var(--scale));overflow:hidden;display:flex;flex-direction:column;justify-content:center;gap:calc(20px * var(--scale));box-shadow:0 22px 60px #0003;animation:s2-window calc(var(--dur) * .20) var(--s2-ease) both}
& .s2-calendar-circle{position:absolute;left:35%;top:27%;width:29%;height:32%;overflow:visible;z-index:4;animation:s2-circle .55s var(--s2-ease) var(--circle-at) both}
& .s2-calendar-circle svg{width:100%;height:100%;overflow:visible;fill:none;stroke:#244c3c;stroke-width:5;stroke-linecap:round}
& .s2-window-rule{position:absolute;left:calc(30px * var(--scale));right:calc(30px * var(--scale));top:calc(24px * var(--scale));height:calc(34px * var(--scale));display:flex;gap:calc(10px * var(--scale));border-bottom:1px solid color-mix(in srgb,var(--ink) 13%,transparent)}
& .s2-window-rule i{display:block;width:calc(7px * var(--scale));height:calc(7px * var(--scale));border-radius:50%;background:var(--soft)}
& .s2-track{display:flex;align-items:center;gap:calc(20px * var(--scale));padding:calc(18px * var(--scale));border-radius:calc(10px * var(--scale));border:1px solid color-mix(in srgb,var(--ink) 15%,transparent);animation:s2-track calc(var(--dur) * .20) var(--s2-ease) both;animation-delay:calc(var(--dur) * (.08 + var(--n) * .09))}
& .s2-track:nth-last-of-type(2){background:color-mix(in srgb,var(--accent) 17%,var(--surface))}
& .s2-retake_trim .s2-track:nth-of-type(3){animation-name:s2-trim}
& .s2-caption_focus .s2-track{border:0;background:none}
& .s2-caption_focus .s2-track:nth-last-of-type(2){background:var(--accent);color:var(--panel)}
& .s2-playhead{position:absolute;top:14%;bottom:8%;left:8%;width:2px;background:var(--accent);opacity:.7;animation:s2-scan calc(var(--dur) * .45) calc(var(--dur) * .10) var(--s2-ease) both}
& .s2-caption_focus .s2-playhead,& .s2-sound_wave .s2-playhead{display:none}
& .s2-wave{height:58%;display:flex;align-items:center;justify-content:space-between;gap:calc(9px * var(--scale));padding-top:calc(25px * var(--scale))}
& .s2-wave span{display:block;flex:1;height:var(--h);border-radius:20px;background:var(--accent);transform-origin:center;animation:s2-wave calc(var(--dur) * .27) var(--s2-ease) both;animation-delay:calc(var(--dur) * (.05 + var(--n) * .011))}
& .s2-product-label{font-size:calc(56px * var(--scale));line-height:1.12;margin-top:calc(25px * var(--scale))}
& .s2-close{position:absolute;inset:0;display:flex;flex-direction:column;justify-content:center;gap:calc(24px * var(--scale))}
& .s2-close-row{display:flex;align-items:center;gap:calc(30px * var(--scale));padding:calc(25px * var(--scale)) 0;animation:s2-reveal calc(var(--dur) * .17) var(--s2-ease) both;animation-delay:calc(var(--dur) * (.02 + var(--n) * .13))}
& .s2-close-rule{height:calc(3px * var(--scale));background:var(--accent);transform-origin:left;animation:s2-line calc(var(--dur) * .23) calc(var(--dur) * .27) var(--s2-ease) both}
& .s2-chapter_fold .s2-close-row{animation-name:s2-paper}
& .s2-compare_slide .s2-close-row:nth-child(2){color:var(--accent);animation-name:s2-bridge}
& .s2-progress_path .s2-close-row .s2-index{border:1px solid var(--accent);border-radius:50%;padding:calc(12px * var(--scale));color:var(--accent)}
& .s2-save_card{border-left:calc(3px * var(--scale)) solid var(--accent);padding-left:calc(32px * var(--scale))}
& .s2-save_card .s2-close-row b{font-size:calc(70px * var(--scale));line-height:1.04}
@keyframes s2-mask{from{transform:translateY(112%)}to{transform:translateY(0)}}
@keyframes s2-circle{from{opacity:0;transform:scale(.82)}to{opacity:1;transform:scale(1)}}
@keyframes s2-focus{from{opacity:0;filter:blur(8px);transform:translateY(18px) scale(.96)}to{opacity:1;filter:blur(0);transform:none}}
@keyframes s2-bridge{from{opacity:0;transform:translateX(-44px)}to{opacity:1;transform:none}}
@keyframes s2-line{from{transform:scaleX(0)}to{transform:scaleX(1)}}
@keyframes s2-line-y{from{transform:scaleY(0)}to{transform:scaleY(1)}}
@keyframes s2-depth{from{opacity:0;transform:translate3d(0,70px,-110px) rotateX(24deg)}to{opacity:1;transform:translate3d(0,0,0) rotateX(0)}}
@keyframes s2-iso{from{opacity:0;transform:translate3d(0,100px,-120px) rotateX(67deg) rotateZ(-34deg)}to{opacity:1;transform:translate3d(0,0,0) rotateX(48deg) rotateZ(-24deg)}}
@keyframes s2-handoff{from{opacity:0;transform:translate3d(-70px,22px,-40px)}to{opacity:1;transform:translate3d(0,0,0)}}
@keyframes s2-orbit{from{opacity:0;transform:translate3d(55px,20px,-80px) rotateY(-17deg)}to{opacity:1;transform:translate3d(0,0,0) rotateY(0)}}
@keyframes s2-reveal{from{opacity:.08;transform:translateY(32px)}to{opacity:1;transform:translateY(0)}}
@keyframes s2-paper{from{opacity:.1;transform:translateY(55px) rotateX(9deg)}to{opacity:1;transform:none}}
@keyframes s2-link-route{to{stroke-dashoffset:0}}
@keyframes s2-file-in{from{opacity:0;transform:translate(-7%,9%) rotate(-7deg) rotateY(-12deg) scale(.94)}to{opacity:1;transform:none}}
@keyframes s2-file-send{from{transform:none}to{transform:translate(16%,18%) rotate(3deg) scale(.88)}}
@keyframes s2-receive{to{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent),0 18px 40px #0004}}
@keyframes s2-task-select{from{background:#ece9e0;transform:translateX(0)}to{background:#bddfce;transform:translateX(12px)}}
@keyframes s2-target{from{background:#ece9e0;box-shadow:0 0 0 0 #34634f00}to{background:#bddfce;box-shadow:0 0 0 2px #34634f}}
@keyframes s2-approve{from{opacity:.25;transform:translateY(12px)}to{opacity:1;transform:none}}
@keyframes s2-lift{from{background:#ece9e0;transform:translateY(0);box-shadow:none}to{background:#bddfce;transform:translateY(-9px);box-shadow:0 18px 35px #17242a22}}
@keyframes s2-window{from{opacity:.1;transform:translateY(42px) scale(.965)}to{opacity:1;transform:none}}
@keyframes s2-track{from{opacity:0;transform:translateX(24px)}to{opacity:1;transform:translateX(0)}}
@keyframes s2-trim{from{opacity:1;transform:scaleX(1)}to{opacity:.32;transform:scaleX(.76)}}
@keyframes s2-scan{from{left:8%}to{left:90%}}
@keyframes s2-wave{from{opacity:.2;transform:scaleY(.12)}to{opacity:1;transform:scaleY(1)}}
@container (min-aspect-ratio:4/5){& .s2-safe{left:8cqw;top:8cqh;width:84cqw;height:84cqh}}
@container (min-aspect-ratio:5/4){& .s2-safe{left:8cqw;top:14cqh;width:84cqw;height:72cqh;display:grid;grid-template-columns:40% 56%;grid-template-rows:1fr auto;gap:0 4%;align-items:center}& .s2-title{grid-column:1;grid-row:1}& .s2-art{grid-column:2;grid-row:1/3;height:100%}& .s2-note{grid-column:1;grid-row:2}}
"""


SPECS = [
    ("type", "satin_title", "Satin title", "A masked line arrives word by word and settles into a quiet underline.", "One clear thought", {"text": "Make it *clear*"}),
    ("type", "focus_stack", "Focus stack", "Short lines come into focus without bouncing or moving while they are read.", "Say one thing", {"text": "One *clear* idea"}),
    ("type", "kinetic_bridge", "Kinetic bridge", "A lateral type reveal carries a thought into its next useful point.", "Keep the meaning", {"text": "Cut with *purpose*"}),
    ("type", "signal_word", "Signal word", "One accent and a restrained rule give the important words a clean landing.", "Find the point", {"text": "Voice comes *first*"}),
    ("depth", "depth_layers", "Depth layers", "Three separated planes arrive from depth and settle with front-facing labels.", "Keep the context", {"items": ["Script", "Footage", "Edit"]}),
    ("depth", "depth_handoff", "Depth handoff", "Layers travel along one lateral path to explain the stages of a handoff.", "One linked handoff", {"items": ["Plan", "Build", "Review"]}),
    ("depth", "focus_deck", "Focus deck", "A compact deck reveals a hierarchy through focus and measured staggering.", "A clear hierarchy", {"items": ["The goal", "The action", "The result"]}),
    ("depth", "object_orbit", "Object arrival", "A restrained turn reveals depth before every label settles front-facing.", "Follow the work", {"items": ["Source", "Process", "Private draft"]}),
    ("route", "route_nodes", "Route nodes", "A continuous route connects three readable nodes in the order they are named.", "Show the route", {"items": ["Mac", "Render PC", "Review"]}),
    ("route", "role_cluster", "Role cluster", "Single-job roles arrive around one continuous line of responsibility.", "Give each a job", {"items": ["Assistant", "Director", "Finance"]}),
    ("route", "linked_steps", "Linked steps", "One shared route and lateral arrivals turn a list into a connected process.", "One next step", {"items": ["Capture", "Process", "Review"]}),
    ("route", "workflow_lanes", "Workflow lanes", "Separated lanes keep ownership readable while the route reveals sequence.", "Clear ownership", {"items": ["Claim", "Build", "Link the work"]}),
    ("task", "task_transfer", "Task transfer", "A paper task receives focus and moves with a clear selection endpoint.", "Pick one task", {"items": ["Read the list", "Select a task", "Name its owner"]}),
    ("task", "link_receipt", "Linked handoff", "A linked artifact travels from one named owner to the next, then settles beside its receiver.", "Hand off the actual work", {"items": ["Editor", "draft.mp4", "Reviewer"]}),
    ("task", "select_focus", "Selection focus", "A precise outline and a quiet colour change identify one selected row.", "Make the choice", {"items": ["First option", "Selected task", "Next step"]}),
    ("task", "approval_gate", "Approval gate", "A proposed action waits while the final approval row becomes readable.", "Ask before acting", {"items": ["Proposed action", "Wait for a yes", "Then proceed"]}),
    ("task", "calendar_lift", "Calendar lift", "One paper task lifts out of a calendar without overlapping its neighbours.", "Lift one task", {"items": ["Email", "Task", "Plan"]}),
    ("product", "edit_window", "Edit window", "An illustrative edit window opens and reveals three separate stages.", "Show the edit", {"items": ["Raw take", "Select the line", "Clean cut"]}),
    ("product", "retake_trim", "Retake trim", "One illustrative retake contracts while the continuous playhead advances.", "Remove the retake", {"items": ["First line", "Retake", "Kept line"]}),
    ("product", "caption_focus", "Caption focus", "One line becomes the caption focus with a clean, readable colour change.", "Words that land", {"items": ["Exact words", "Clear timing", "One idea"]}),
    ("product", "sound_wave", "Sound reveal", "An explicitly illustrative signal grows smoothly and settles on the voice label.", "Keep voice first", {"items": ["Voice first"]}),
    ("close", "chapter_fold", "Chapter fold", "A soft paper arrival marks a new chapter without a noisy full-screen wipe.", "The next chapter", {"items": ["A fresh point", "A clear action"]}),
    ("close", "compare_slide", "Compare slide", "Two statements arrive on a shared axis so the contrast stays easy to read.", "Make it visible", {"items": ["Before the cut", "After the cut"]}),
    ("close", "progress_path", "Progress path", "Numbered milestones and a measured line show the steps without fake progress.", "One useful flow", {"items": ["Make", "Check", "Review"]}),
    ("close", "save_card", "Save card", "A single quiet ending holds the useful action without activating a keyword flow.", "Keep this handy", {"items": ["Save this", "Try one step"]}),
]


def _format(spec: tuple) -> Format:
    family, name, title, summary, headline, example = spec
    slots = [Slot("title", limit=38), Slot("note", limit=28, required=False)]
    slots.append(Slot("text", limit=30) if family == "type" else Slot("items", "list", limit=3, each=6 if name == "calendar_lift" else 18))
    id = "studio_" + name
    return Format(
        id, title, summary, "iso" if family == "depth" else "kinetic" if family == "type" else "explain",
        ("hook", "explain", "close") if family == "type" else ("explain", "close"),
        tuple(slots), {"title": headline, "note": "Illustrative sample", **example, "held": True}, partial(_draw, family=family, variant=name),
        CSS.replace("&", ".f-" + id), seconds=6, shortest=3, longest=12, area="full", tall=920,
        sound=None, leaves=False, needs_alpha=False,
    )


FORMATS = [_format(spec) for spec in SPECS]
