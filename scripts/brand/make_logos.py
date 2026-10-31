"""Writes the Altocue mark and the ten tool logos. Every glyph carries the same slanted cut."""

from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "assets" / "logos"

TOOLS = {
    "cut": ("#b5f4d9", "#7fdcbd"),
    "clean": ("#a8efe9", "#6fd3d6"),
    "grade": ("#abdffa", "#74b8ea"),
    "frame": ("#b8cbfb", "#8aa2f0"),
    "caption": ("#cfc0f9", "#a690ee"),
    "motion": ("#edc2f1", "#d093e0"),
    "direct": ("#f8c6d6", "#ee96b4"),
    "generate": ("#fad5b1", "#f2b27c"),
    "broll": ("#f3e4a4", "#ddc26a"),
    "review": ("#d7f1a9", "#a9d873"),
}


def slash(x: float, w: float = 5.5) -> str:
    return f'<rect x="{x}" y="-12" width="{w}" height="124" fill="#000" transform="rotate(10 {x + w / 2} 50)"/>'


def rounded_triangle() -> str:
    return '<path d="M20 14 L20 86 L84 50 Z" fill="#fff" stroke="#fff" stroke-width="12" stroke-linejoin="round"/>'


GLYPHS = {
    "cut": '<rect x="8" y="33" width="84" height="34" rx="12" fill="#fff"/>' + slash(33) + slash(63),
    "clean": "".join(f'<rect x="{x}" y="{50 - h / 2}" width="8.5" height="{h}" rx="4.25" fill="#fff"/>' for x, h in ((9, 14), (21, 30), (33, 56), (45.5, 78), (58, 56), (70, 30), (82, 14))) + slash(51, 4.5),
    "grade": '<circle cx="50" cy="50" r="40" fill="#fff"/>' + slash(47, 5) + '<path d="M0 0 H50 L36 100 H0 Z" fill="#000" fill-opacity=".52"/>',
    "frame": "".join(f'<path d="{d}" fill="none" stroke="#fff" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/>' for d in ("M12 32 V21 Q12 12 21 12 H32", "M68 12 H79 Q88 12 88 21 V32", "M88 68 V79 Q88 88 79 88 H68", "M32 88 H21 Q12 88 12 79 V68"))
    + '<circle cx="50" cy="50" r="21" fill="#fff"/>' + '<rect x="47.8" y="24" width="4.5" height="52" fill="#000" transform="rotate(10 50 50)"/>',
    "caption": '<g transform="skewX(-10) translate(9 0)"><rect x="8" y="27" width="28" height="20" rx="7" fill="#fff" fill-opacity=".46"/><rect x="41" y="27" width="46" height="20" rx="7" fill="#fff"/><rect x="8" y="54" width="48" height="20" rx="7" fill="#fff" fill-opacity=".46"/><rect x="61" y="54" width="26" height="20" rx="7" fill="#fff" fill-opacity=".46"/></g>',
    "motion": '<g transform="skewX(-10) translate(9 0)"><rect x="10" y="20" width="16" height="60" rx="6" fill="#fff" fill-opacity=".3"/><rect x="33" y="20" width="16" height="60" rx="6" fill="#fff" fill-opacity=".62"/><path d="M56 26 Q56 20 62 21 Q84 33 90 45 Q93 50 90 55 Q84 67 62 79 Q56 80 56 74 Z" fill="#fff"/></g>',
    "direct": '<rect x="11" y="43" width="78" height="44" rx="11" fill="#fff"/><g transform="rotate(-10 12 37)"><rect x="11" y="16" width="78" height="20" rx="7" fill="#fff"/><g fill="#000"><rect x="29" y="12" width="7" height="28" transform="skewX(-22)" /><rect x="50" y="12" width="7" height="28" transform="skewX(-22)"/><rect x="71" y="12" width="7" height="28" transform="skewX(-22)"/></g></g>',
    "generate": '<path d="M50 6 C53 32 68 47 94 50 C68 53 53 68 50 94 C47 68 32 53 6 50 C32 47 47 32 50 6 Z" fill="#fff"/>' + slash(47.6, 4.8),
    "broll": '<rect x="7" y="16" width="86" height="68" rx="14" fill="#fff" fill-opacity=".52"/><rect x="37" y="37" width="52" height="41" rx="11" fill="#000"/>'
    + '<rect x="42" y="42" width="42" height="31" rx="7.5" fill="#fff"/>'
    + slash(25.5, 4.8),
    "review": '<path d="' + "M50 7 L61 15 L74 12 L80 24 L92 30 L89 43 L97 54 L88 64 L88 78 L75 81 L67 92 L54 88 L42 93 L33 83 L20 81 L18 67 L8 58 L14 46 L10 33 L22 26 L27 14 L40 15 Z" + '" fill="#fff" stroke="#fff" stroke-width="6" stroke-linejoin="round"/><path d="M33 51 L45 63 L68 37" fill="none" stroke="#000" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/>',
}


def glyph_svg(name: str, top: str, bottom: str, size: int = 100) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 100 100">'
        f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{top}"/><stop offset="1" stop-color="{bottom}"/></linearGradient>'
        f'<mask id="m" maskUnits="userSpaceOnUse" x="0" y="0" width="100" height="100"><rect width="100" height="100" fill="#000"/>{GLYPHS[name]}</mask></defs>'
        f'<rect width="100" height="100" fill="url(#g)" mask="url(#m)"/></svg>'
    )


def tile_svg(name: str, top: str, bottom: str) -> str:
    inner = glyph_svg(name, top, bottom)
    body = inner[inner.index("<defs>") : inner.rindex("</svg>")]
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160" viewBox="0 0 160 160">'
        '<defs><linearGradient id="t" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#1b2c35"/><stop offset=".62" stop-color="#101b24"/></linearGradient></defs>'
        '<rect x="1" y="1" width="158" height="158" rx="42" fill="url(#t)" stroke="#d3eefc" stroke-opacity=".16" stroke-width="2"/>'
        f'<svg x="38" y="38" width="84" height="84" viewBox="0 0 100 100">{body}</svg></svg>'
    )


def mark_svg(size: int = 100) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 100 100">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#eafff5"/><stop offset=".38" stop-color="#b5f4d9"/><stop offset=".72" stop-color="#a5e7d1"/><stop offset="1" stop-color="#8fd4e0"/></linearGradient>'
        f'<mask id="m" maskUnits="userSpaceOnUse" x="0" y="0" width="100" height="100"><rect width="100" height="100" fill="#000"/>{rounded_triangle()}{slash(38)}{slash(62)}</mask></defs>'
        '<rect width="100" height="100" fill="url(#g)" mask="url(#m)"/></svg>'
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "altocue.svg").write_text(mark_svg())
    for name, (top, bottom) in TOOLS.items():
        (OUT / f"{name}.svg").write_text(glyph_svg(name, top, bottom))
        (OUT / f"{name}-tile.svg").write_text(tile_svg(name, top, bottom))


if __name__ == "__main__":
    main()
