"""Graphic wipes. Clear at both ends and solid at the midpoint, so the cut underneath is never seen."""

from __future__ import annotations

from . import Format, Slot, kit

SLATS = 7
CLOSE = "cubic-bezier(.55,0,.8,.28)"
OPEN = "cubic-bezier(.2,.72,.45,1)"


def _label(item: dict) -> str:
    return f'<b class="lab">{kit.esc(item["label"])}</b>' if item.get("label") else ""


def _label_css(name: str, ink: str = "var(--ink)") -> str:
    """The label only ever shows while the frame is covered."""
    return (
        f".f-{name} .lab{{position:absolute;inset:0;display:grid;place-items:center;padding:0 var(--gutter);font-size:calc(112px * var(--scale));font-weight:600;line-height:1;letter-spacing:-.035em;text-align:center;color:{ink};animation:wp{name} var(--dur) linear both}}\n"
        f"@keyframes wp{name}{{0%,38%{{opacity:0}}43%,57%{{opacity:1}}62%,100%{{opacity:0}}}}\n"
    )


def _iris(item: dict) -> str:
    return f'<i class="rim"></i><i class="fill"></i>{_label(item)}'


def _white_flash(item: dict) -> str:
    return f'<i class="core"></i><i class="fill"></i><i class="tail"></i>{_label(item)}'


def _shape_match(item: dict) -> str:
    """The shape locks onto a spot in the outgoing shot, swallows the frame, and hands back to the same spot in the next."""
    size = max(min(float(item.get("size", 30)), 80.0), 6.0)
    place = f'--cx:{float(item.get("cx", 50)):g}%;--cy:{float(item.get("cy", 44)):g}%;--size:{size:g}%;--s0:{size / 420:.4f}'
    shape = item.get("shape", "circle")
    return f'<div class="scene {shape}" style="{place}"><span class="ring"><i></i></span><span class="grow"><i></i></span></div>{_label(item)}'


def _shutter(item: dict) -> str:
    slats = "".join(f'<span class="slat" style="--i:{index};--s:{1 if index % 2 else -1}"><i class="l"></i><i class="r"></i></span>' for index in range(SLATS))
    return f"{slats}{_label(item)}"


def _curtain(item: dict) -> str:
    return f'<i class="sheet a"></i><i class="sheet b"></i><i class="sheet c"></i>{_label(item)}'


FORMATS = (
    Format(
        id="iris",
        title="Iris",
        summary="A circle closes down to a point and opens again on the next shot.",
        group="wipe",
        jobs=("transition",),
        slots=(Slot("label", limit=18, required=False),),
        example={"label": "Part two"},
        draw=_iris,
        seconds=1.0,
        shortest=0.5,
        longest=1.2,
        area="full",
        tall=1920,
        sound="whoosh",
        leaves=False,
        css=f"""
@property --wp-iris{{syntax:"<percentage>";inherits:false;initial-value:102%}}
.f-iris i{{position:absolute;inset:0}}
.f-iris .fill{{background:var(--panel);-webkit-mask-image:radial-gradient(circle farthest-corner at 50% 50%,transparent var(--wp-iris),#000 calc(var(--wp-iris) + .12%));animation:wpirisa var(--dur) both}}
.f-iris .rim{{background:var(--accent);-webkit-mask-image:radial-gradient(circle farthest-corner at 50% 50%,transparent var(--wp-iris),#000 calc(var(--wp-iris) + .12%));animation:wpirisb var(--dur) both}}
@keyframes wpirisa{{0%{{--wp-iris:102%;animation-timing-function:{CLOSE}}}42%,58%{{--wp-iris:0%;animation-timing-function:{OPEN}}}100%{{--wp-iris:102%}}}}
@keyframes wpirisb{{0%{{--wp-iris:102%;animation-timing-function:{CLOSE}}}36%,64%{{--wp-iris:0%;animation-timing-function:{OPEN}}}100%{{--wp-iris:102%}}}}
"""
        + _label_css("iris"),
    ),
    Format(
        id="white_flash",
        title="Flash through white",
        summary="Light blooms from the centre, blows out the frame, and burns back off.",
        group="wipe",
        jobs=("transition",),
        slots=(Slot("label", limit=18, required=False),),
        example={"label": "Later"},
        draw=_white_flash,
        seconds=0.8,
        shortest=0.4,
        longest=1.1,
        area="full",
        tall=1920,
        sound="whoosh",
        leaves=False,
        css="""
.f-white_flash i{position:absolute;inset:0}
.f-white_flash .core{background:radial-gradient(circle closest-side,#fff 0,#fff 34%,rgba(255,255,255,.72) 58%,rgba(255,255,255,0) 100%);transform:scale(.2);animation:wfcore var(--dur) both}
.f-white_flash .fill{background:#fff;animation:wffill var(--dur) both}
.f-white_flash .tail{background:color-mix(in srgb,var(--accent) 34%,#fff);animation:wftail var(--dur) both}
@keyframes wfcore{0%{opacity:0;transform:scale(.2);animation-timing-function:cubic-bezier(.5,0,.9,.5)}12%{opacity:1}44%{opacity:1;transform:scale(3.4)}46%,100%{opacity:0;transform:scale(3.4)}}
@keyframes wffill{0%,16%{opacity:0;animation-timing-function:cubic-bezier(.7,0,.9,.4)}44%,56%{opacity:1;animation-timing-function:cubic-bezier(.1,.7,.3,1)}92%,100%{opacity:0}}
@keyframes wftail{0%,50%{opacity:0}56%{opacity:1;animation-timing-function:cubic-bezier(.3,.3,.3,1)}100%{opacity:0}}
"""
        + _label_css("white_flash", "var(--panel)"),
    ),
    Format(
        id="shape_match",
        title="Shape match",
        summary="A shape locks onto a spot, swallows the frame, and hands back to the same spot.",
        group="wipe",
        jobs=("transition",),
        slots=(
            Slot("label", limit=18, required=False),
            Slot("shape", kind="choice", required=False, choices=("circle", "square", "diamond")),
            Slot("cx", kind="number", required=False),
            Slot("cy", kind="number", required=False),
            Slot("size", kind="number", required=False),
        ),
        example={"label": "Meanwhile", "shape": "circle", "cx": 50, "cy": 44, "size": 30},
        draw=_shape_match,
        seconds=1.1,
        shortest=0.8,
        longest=1.2,
        area="full",
        tall=1920,
        sound="pop",
        leaves=False,
        css=f"""
.f-shape_match .scene{{position:absolute;inset:0;overflow:hidden}}
.f-shape_match .grow,.f-shape_match .ring{{position:absolute;left:var(--cx);top:var(--cy);translate:-50% -50%;aspect-ratio:1}}
.f-shape_match .grow{{width:420%;transform:scale(0);animation:smgrow var(--dur) both}}
.f-shape_match .ring{{width:var(--size);animation:smring var(--dur) both}}
.f-shape_match i{{position:absolute;inset:0;display:block}}
.f-shape_match .grow i{{background:var(--panel)}}
.f-shape_match .ring i{{box-shadow:0 0 0 calc(9px * var(--scale)) var(--accent)}}
.f-shape_match .circle i{{border-radius:50%}}
.f-shape_match .square i{{border-radius:6%}}
.f-shape_match .diamond i{{transform:rotate(45deg)}}
@keyframes smring{{0%{{opacity:0;transform:scale(1.5);animation-timing-function:{OPEN}}}13%,88%{{opacity:1;transform:none;animation-timing-function:{CLOSE}}}100%{{opacity:0;transform:scale(.3)}}}}
@keyframes smgrow{{0%,16%{{transform:scale(0);animation-timing-function:{OPEN}}}21%{{transform:scale(var(--s0));animation-timing-function:cubic-bezier(.8,0,.96,.3)}}43%,57%{{transform:none;animation-timing-function:cubic-bezier(.04,.7,.2,1)}}79%{{transform:scale(var(--s0));animation-timing-function:{CLOSE}}}90%,100%{{transform:scale(0)}}}}
"""
        + _label_css("shape_match"),
    ),
    Format(
        id="shutter",
        title="Shutter slam",
        summary="Slats slam shut from alternating sides and fly on to clear the next shot.",
        group="wipe",
        jobs=("transition",),
        slots=(Slot("label", limit=18, required=False),),
        example={"label": "Round two"},
        draw=_shutter,
        seconds=0.9,
        shortest=0.5,
        longest=1.2,
        area="full",
        tall=1920,
        sound="thud",
        leaves=False,
        css=f"""
.f-shutter{{overflow:hidden}}
.f-shutter .slat{{position:absolute;left:0;right:0;top:calc(var(--i) * 100% / {SLATS});height:calc(100% / {SLATS} + 1.5px);background:var(--panel);animation:wpslat calc(var(--dur) * .92) calc(var(--i) * var(--dur) * .0134) both}}
.f-shutter .slat:nth-child(even){{background:var(--surface)}}
.f-shutter .slat i{{position:absolute;top:0;bottom:0;width:calc(18px * var(--scale));background:var(--accent)}}
.f-shutter .slat .l{{right:100%}}
.f-shutter .slat .r{{left:100%}}
@keyframes wpslat{{0%{{transform:translateX(calc(var(--s) * 104%));animation-timing-function:{CLOSE}}}40%,60%{{transform:none;animation-timing-function:{OPEN}}}100%{{transform:translateX(calc(var(--s) * -104%))}}}}
"""
        + _label_css("shutter"),
    ),
    Format(
        id="curtain",
        title="Curtain sweep",
        summary="Three angled sheets sweep across in step, hold, and pull away the same way.",
        group="wipe",
        jobs=("transition",),
        slots=(Slot("label", limit=18, required=False),),
        example={"label": "Chapter three"},
        draw=_curtain,
        seconds=1.0,
        shortest=0.5,
        longest=1.2,
        area="full",
        tall=1920,
        sound="whoosh",
        leaves=False,
        css=f"""
.f-curtain{{overflow:hidden}}
.f-curtain .sheet{{position:absolute;top:-4%;bottom:-4%;left:-40%;right:-40%}}
.f-curtain .a{{background:var(--accent);animation:wpcura var(--dur) both}}
.f-curtain .b{{background:var(--surface);animation:wpcurb var(--dur) both}}
.f-curtain .c{{background:var(--panel);animation:wpcurc var(--dur) both}}
@keyframes wpcura{{0%{{transform:translateX(104%) skewX(-13deg);animation-timing-function:{CLOSE}}}34%,66%{{transform:skewX(-13deg);animation-timing-function:{OPEN}}}100%{{transform:translateX(-104%) skewX(-13deg)}}}}
@keyframes wpcurb{{0%,4%{{transform:translateX(104%) skewX(-13deg);animation-timing-function:{CLOSE}}}38%,62%{{transform:skewX(-13deg);animation-timing-function:{OPEN}}}96%,100%{{transform:translateX(-104%) skewX(-13deg)}}}}
@keyframes wpcurc{{0%,8%{{transform:translateX(104%) skewX(-13deg);animation-timing-function:{CLOSE}}}42%,58%{{transform:skewX(-13deg);animation-timing-function:{OPEN}}}92%,100%{{transform:translateX(-104%) skewX(-13deg)}}}}
"""
        + _label_css("curtain"),
    ),
)
