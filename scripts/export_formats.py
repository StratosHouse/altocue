#!/usr/bin/env python3
"""Export every built-in graphic's example as a page any browser can play, plus formats.json. For the website. Dev tool.

Each page is the graphic as a render composes it: `motion.html.compose` with the default brand, the format's own CSS and
the fonts it names. A backdrop sits under graphics that are transparent, and a few lines of script fit the frame to the
window and loop it. Nothing on the page uses the network. A page that holds it can pause or play it with
`postMessage({altocue: "pause"})` and `postMessage({altocue: "play"})`. Under reduced motion it holds one still frame.

    python scripts/export_formats.py <folder> [--aspect square] [--fonts-url ../fonts/] [--id bar_build ...]

Square by default, where a panel spans most of the frame, so a small tile stays readable. Fonts are copied beside the
pages with their licences, unless `--fonts-url` says where a site already serves them.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from html import escape
from pathlib import Path
from typing import Any

from altocue import __version__, library
from altocue import brand as brands
from altocue.brand import data_path
from altocue.motion import formats
from altocue.motion.html import compose

SIZES = {"wide": (1920, 1080), "tall": (1080, 1920), "square": (1080, 1080)}
GAP = 0.6  # seconds of backdrop between one play and the next
STILL = 0.62  # share of the way through a graphic where reduced motion holds it, as the format sheet's middle frame
BACKDROP = (
    '<div aria-hidden="true" style="position:absolute;inset:0;background:radial-gradient(120% 95% at 72% 18%,#25414b 0%,#152530 42%,#0b131a 78%,#080d13 100%)"></div>'
)
FONT_LICENCES = ("OFL.txt", "OFL-InstrumentSerif.txt", "OFL-Caveat.txt")

# Fits the frame to the window, loops it, and answers pause and play. A replayed copy of the root restarts every
# CSS animation, including the ones that have finished and no longer show in getAnimations().
PLAYER = """<script>
(() => {
  const LOOP = %(loop)s, STILL = %(still)s;
  let root = document.getElementById("root"), timer = 0, playing = false;
  const width = +root.dataset.width, height = +root.dataset.height;
  const calm = matchMedia("(prefers-reduced-motion: reduce)");
  const fit = () => {
    const scale = Math.min(innerWidth / width, innerHeight / height);
    root.style.transform = `translate(${(innerWidth - width * scale) / 2}px, ${(innerHeight - height * scale) / 2}px) scale(${scale})`;
  };
  const fresh = () => { const copy = root.cloneNode(true); root.replaceWith(copy); root = copy; fit(); };
  const hold = () => { for (const a of root.getAnimations({ subtree: true })) { a.pause(); a.currentTime = STILL * 1000; } };
  const stop = () => { clearInterval(timer); timer = 0; playing = false; for (const a of root.getAnimations({ subtree: true })) a.pause(); };
  const play = () => {
    clearInterval(timer);
    fresh();
    if (calm.matches) { hold(); playing = false; return; }
    playing = true;
    timer = setInterval(fresh, LOOP * 1000);
  };
  root.style.transformOrigin = "0 0";
  addEventListener("resize", fit);
  addEventListener("message", (event) => {
    const asked = event.data && event.data.altocue;
    if (asked === "pause" && (playing || timer)) stop();
    if (asked === "play" && !playing) play();
  });
  calm.addEventListener("change", play);
  play();
})();
</script>"""

PAGE_CSS = "html,body{width:100%;height:100%;overflow:hidden;background:#080d13}#root{position:absolute;left:0;top:0}"


def entries() -> list[dict[str, Any]]:
    """Every built-in the library lists, in its order: the core graphics, then the formats."""
    return library.builtins()


def timeline_for(item: dict[str, Any], seconds: float, size: tuple[int, int]) -> dict[str, Any]:
    return {"width": size[0], "height": size[1], "fps": 30, "duration": seconds, "tail": 0, "total": seconds, "captions": [], "items": [item]}


def page_for(row: dict[str, Any], brand: brands.Brand, size: tuple[int, int], fonts_url: str) -> str:
    seconds = float(row.get("seconds") or 3.0)
    item = {**row["example"], "type": row["id"], "start": 0.0, "end": seconds}
    formats.check(item)
    if any(key in json.dumps(row["example"]) for key in ('"image"', '"src"')):
        raise SystemExit(f"{row['id']}: its example names an image, which this export cannot carry.")
    page = compose(timeline_for(item, seconds, size), brand)
    if fonts_url:
        for name in {brand.font_file.name, *formats.FONT_FILES.values()}:
            page = page.replace(f'url("{name}")', f'url("{fonts_url}{name}")')
    head = (
        f'<meta name="viewport" content="width=device-width, initial-scale=1">\n<meta name="robots" content="noindex">\n<link rel="icon" href="data:,">\n'
        f"<title>{escape(row['title'])}, an Altocue format</title>\n"
    )
    page = page.replace('<meta charset="utf-8">\n', f'<meta charset="utf-8">\n{head}', 1)
    page = page.replace("</style></head>", f"{PAGE_CSS}</style></head>", 1)
    opening = page.index('<div id="root"')
    opening = page.index(">\n", opening) + 2
    page = page[:opening] + BACKDROP + "\n" + page[opening:]
    player = PLAYER % {"loop": f"{seconds + GAP:.3f}", "still": f"{seconds * STILL:.3f}"}
    return page.replace("</body>", f"{player}\n</body>", 1)


def index_row(row: dict[str, Any]) -> dict[str, Any]:
    """The library's own entry for this graphic, so a page can list it like any other, plus what the gallery needs."""
    fmt = formats.find(row["id"])
    return {
        **row,
        "group": fmt.group if fmt else "core",
        "core": fmt is None,
        "area": fmt.area if fmt else ("full" if row["id"] in ("title", "endcard") else "panel"),
        "fonts": list(fmt.fonts) if fmt else [],
        "file": f"{row['id']}.html",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out", help="Folder for the pages and formats.json")
    parser.add_argument("--aspect", choices=SIZES, default="square")
    parser.add_argument("--fonts-url", default="", help="Load fonts from this address prefix instead of copying them beside the pages")
    parser.add_argument("--id", nargs="+", help="Only these graphics")
    args = parser.parse_args()
    if args.fonts_url and not args.fonts_url.endswith("/"):
        raise SystemExit("--fonts-url must end with a slash.")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    brand = brands.Brand(json.loads(data_path("brands", "default.json").read_text()), data_path("brands"))
    size = SIZES[args.aspect]
    picked = [row for row in entries() if not args.id or row["id"] in args.id]
    if not picked:
        raise SystemExit("No graphics matched.")
    for old in out.glob("*.html"):
        if re.fullmatch(r"[a-z0-9_]+\.html", old.name):
            old.unlink()
    for row in picked:
        (out / f"{row['id']}.html").write_text(page_for(row, brand, size, args.fonts_url))
    if not args.fonts_url:
        for name in {brand.font_file.name, *formats.FONT_FILES.values(), *FONT_LICENCES}:
            shutil.copy(data_path("fonts", name), out / name)
    rows = [index_row(row) for row in picked]
    index = {
        "altocue": __version__,
        "brand": "default",
        "width": size[0],
        "height": size[1],
        "formats": sum(not row["core"] for row in rows),
        "groups": {group: title for group, title in formats.GROUPS.items() if any(row["group"] == group for row in rows)},
        "entries": rows,
    }
    (out / "formats.json").write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n")
    print(f"{len(rows)} pages, {index['formats']} of them formats, in {out}")


if __name__ == "__main__":
    main()
