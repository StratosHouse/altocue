"""Any HyperFrames composition as a graphic: one you wrote, or one fetched from the registry."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from html import escape, unescape
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from ..errors import AltocueError
from ..project import Project

FOLDER = "motion/fetched"
STAGE_FOLDER = "compositions"
GSAP_CDN = re.compile(r"https://cdn\.jsdelivr\.net/npm/gsap@[\d.]+/dist/gsap\.min\.js")
OWN_ID = re.compile(r'<div[^>]*\bdata-composition-id="([^"]+)"')
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

LOCAL_HOSTS = {"cdn.jsdelivr.net", "cdnjs.cloudflare.com", "fonts.googleapis.com", "fonts.gstatic.com"}  # the places registry items load from
KEPT_SUFFIXES = {".js", ".mjs", ".css", ".json", ".woff2", ".woff", ".ttf", ".otf", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".glb", ".gltf", ".hdr", ".wasm", ".bin"}
QUOTED_URL = re.compile(r"""(?<=["'`(])https?://[^\s"'`<>()\\${}]+(?=["'`)])""")
LOADED_URL = re.compile(
    r"""(?:<(?:script|img|video|audio|source|iframe|link)\b[^>]*?\b(?:src|href)=["']|url\(\s*["']?|\bfrom\s+["']|\bimport\s*\(\s*["']|\bfetch\s*\(\s*["'])(https?://[^"'`)\s]+)"""
)
FONT_CSS = re.compile(r"""<link\b[^>]*?href=["'](https://fonts\.googleapis\.com/css[^"']*)["'][^>]*>|@import\s+url\(\s*["']?(https://fonts\.googleapis\.com/css[^"')]*)["']?\s*\)\s*;?""")
STYLE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.DOTALL | re.IGNORECASE)
EMBEDDED = re.compile(r'<style data-hyperframes-deterministic-fonts="true">(.*?)</style>', re.DOTALL)
FONT_TEXT = "".join(map(chr, range(0x20, 0x7F))) + "".join(map(chr, range(0xA0, 0x100))) + "\u2018\u2019\u201c\u201d\u2013\u2014\u2026\u2022\u20ac\u2122"
JS_IMPORT = re.compile(r"""(\b(?:from|import)\s*\(?\s*["'])((?:/npm/|/gh/|https://cdn\.jsdelivr\.net/|\.{1,2}/)[^"']+)(["'])""")


def own_id(text: str) -> str | None:
    """The id a page declares. Registry files often show a sample one in a leading comment, which is not it."""
    found = OWN_ID.search(COMMENT.sub("", text))
    return found.group(1) if found else None


def resolve(project: Project, item: dict[str, Any]) -> dict[str, Any]:
    """Find the file, and read the id the renderer needs to mount it."""
    named = item.get("name")
    relative = item.get("file") or (f"{FOLDER}/{named}.html" if named else None)
    if not relative:
        raise AltocueError('A composition needs "name" or "file".', fix='Use {"type": "composition", "name": "..."} after `altocue library add <name>`.')
    path = (project.root / relative).resolve()
    if not path.is_file() or not path.is_relative_to(project.root.resolve()):
        fix = f"Run `altocue library add {named}`." if named else "Point `file` at an HTML file inside the project."
        raise AltocueError(f"The composition {relative} is not in this project.", fix=fix)
    found = own_id(path.read_text(errors="replace"))
    if not found:
        raise AltocueError(f"{relative} is not a HyperFrames composition.", fix='It needs a <template> holding a div with data-composition-id="...".')
    return {**item, "file": relative, "path": str(path), "own_id": found}


def mount(item: dict[str, Any], attrs: str) -> str:
    values = escape(json.dumps(item.get("vars", {})), quote=True)
    source = f"{STAGE_FOLDER}/{item['own_id']}.html"
    size = f'data-width="{item.get("width", 1080)}" data-height="{item.get("height", 1920)}"'
    own = escape(item["own_id"])
    return f'<div class="clip" style="inset:0" data-composition-id="{own}" data-composition-src="{source}" {attrs} {size} data-variable-values="{values}"></div>'


def install(items: list[dict[str, Any]], stage: Path, renderer_dir: Path) -> None:
    """Copy each file beside the page. GSAP comes from the renderer's own copy, so a render never needs the network."""
    chosen = [item for item in items if item["type"] == "composition"]
    if not chosen:
        return
    folder = stage / STAGE_FOLDER
    folder.mkdir(exist_ok=True)
    local = next(iter(sorted(renderer_dir.glob("node_modules/hyperframes/dist/vendor/npm/gsap@*/dist/gsap.min.js"))), None)
    if local:
        shutil.copy(local, folder / "gsap.min.js")
    for item in chosen:
        source = Path(item["path"])
        text = source.read_text(errors="replace")
        if local:
            text = GSAP_CDN.sub(f"{STAGE_FOLDER}/gsap.min.js", text)
        (folder / f"{item['own_id']}.html").write_text(text)
        if source.with_suffix("").is_dir():
            shutil.copytree(source.with_suffix(""), folder / source.stem, dirs_exist_ok=True)


def rename(text: str, own: str, name: str) -> str:
    """Dozens of registry items are all called `main`, and two sharing a film would overwrite each other on the stage."""
    text = re.sub(rf"""(data-composition-id=["']){re.escape(own)}(["'])""", rf"\g<1>{name}\g<2>", text)
    return re.sub(rf"""(__timelines\s*\[\s*["'`]){re.escape(own)}(["'`]\s*\])""", rf"\g<1>{name}\g<2>", text)


def rebase(text: str, name: str, tops: set[str]) -> str:
    """An item's own files sit in a folder on the stage. Paths from the stage root work in markup, CSS, scripts and the runtime's assetUrl alike."""
    for top in sorted(tops):
        text = re.sub(rf"""(?<=["'`(])(?:\.{{0,2}}/)*{re.escape(top)}(?=[/"'`)])""", f"/{STAGE_FOLDER}/{name}/{top}", text)
    return text


def embed_fonts(text: str, tool: Path) -> tuple[str, list[str], str]:
    """Faces an item names without carrying. HyperFrames would fetch them from Google Fonts on every render, so they come down once, here.

    HyperFrames reads the item's own styles and embeds each face it would fetch, cut to Latin text, so any
    wordmark in a Latin alphabet draws in the face the item was designed in. Returns the page, the families
    and, when a family cannot be embedded, why.
    """
    sheets = "\n".join(STYLE.findall(text))
    if not sheets or not tool.exists():
        return text, [], ""
    from .hyperframes import quiet_env

    probe = f"<!doctype html><html><head><style>{sheets}</style></head><body>{escape(FONT_TEXT)}</body></html>"
    done = subprocess.run([str(tool)], input=probe, capture_output=True, text=True, env=quiet_env(), timeout=300, check=False)
    if done.returncode != 0:
        return text, [], done.stderr.strip().splitlines()[-1] if done.stderr.strip() else "the font localiser failed"
    found = EMBEDDED.search(done.stdout)
    if not found:
        return text, [], ""
    css = found.group(1).strip()
    families = sorted(set(re.findall(r'font-family:\s*"([^"]+)"', css)))
    at = text.find("<style", max(text.find("<template"), 0))
    at = at if at >= 0 else text.find("<style")
    block = f"<style data-altocue-fonts>\n{css}\n</style>\n"
    return (text[:at] + block + text[at:] if at >= 0 else text.replace("</head>", block + "</head>", 1)), families, ""


def localise(text: str, name: str, folder: Path) -> tuple[str, list[str]]:
    """Save what an item loads from the web beside it, so a render needs no network. GSAP is left to `install`, which uses the renderer's own copy."""
    from ..generate import http

    stage_path = f"/{STAGE_FOLDER}/{name}/vendor"
    saved: dict[str, str] = {}

    def keep(url: str) -> str:
        plain = unescape(url)
        if plain in saved:
            return saved[plain]
        suffix = Path(urlsplit(plain).path).suffix.lower()
        file = hashlib.sha256(plain.encode()).hexdigest()[:16] + (suffix if suffix in KEPT_SUFFIXES else ".js")
        saved[plain] = file
        dest = http.download(plain, folder / "vendor" / file, timeout=120)
        if file.endswith((".js", ".mjs")):
            body = dest.read_text(errors="replace")
            pointed = JS_IMPORT.sub(lambda found: f"{found[1]}./{keep(urljoin(plain, found[2]))}{found[3]}", body)
            if pointed != body:
                dest.write_text(pointed)
        return file

    def fonts(found: re.Match[str]) -> str:
        plain = unescape(found[1] or found[2])
        sheet = http.download(plain, folder / "vendor" / "fonts.css.part", timeout=60)
        css = sheet.read_text(errors="replace")
        sheet.unlink()
        saved[plain] = ""
        css = re.sub(r"https://fonts\.gstatic\.com/[^\s)\"']+", lambda url: f"{stage_path}/{keep(url[0])}", css)
        return f"<style>{css}</style>" if found[1] else css

    def swap(found: re.Match[str]) -> str:
        parts = urlsplit(found[0])
        built_later = found[0].endswith(("=", "/"))  # a prefix the item finishes at run time, for a font someone else may choose
        if parts.hostname not in LOCAL_HOSTS or parts.path in ("", "/") or built_later or GSAP_CDN.fullmatch(found[0]):
            return found[0]
        return f"{stage_path}/{keep(found[0])}"

    text = QUOTED_URL.sub(swap, FONT_CSS.sub(fonts, text))
    left = [url for url in LOADED_URL.findall(text) if urlsplit(url).path not in ("", "/") and not GSAP_CDN.fullmatch(url)]
    if left:
        raise AltocueError(f"{name} loads {left[0]} while rendering, so it could not render offline.", fix="Pick another item with `altocue library search`.")
    return text, list(saved)
