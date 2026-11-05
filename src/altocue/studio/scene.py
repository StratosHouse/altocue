"""Directed product visuals: local source art, spoken triggers and stable safe-area labels."""

from __future__ import annotations

import bisect
import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from .canvas import backing, center, ell, folio, glass, ln, rr, smooth, thumbnail, tray, txt, wash

INK = "#152f32"
PAPER = "#f2f5ec"
MINT = "#c9f2d1"


@lru_cache(24)
def registered(path, stamp):
    with Image.open(path) as original:
        return original.convert("RGBA")


def image(plan, key, size):
    path = Path(plan["_assets"][key])
    return thumbnail(path, size, path.stat().st_mtime_ns)


def header(out, plan, light=False):
    # The labels belong to the evidence area, below the profile band on an organic Reel.
    x, y = 51, 233
    fg = "#28463c" if light else "#dceee3"
    center(out, image(plan, plan["mark"], 33), x + 15, y)
    txt(out, (x + 43, y), plan["house"], 21, "b", fg, limit=143)
    ln(out, [(x + 195, y - 9), (x + 195, y + 9)], "#78968a", 1)
    txt(out, (x + 211, y), plan["product"], 21, "b", fg, limit=275)


def title(out, scene):
    txt(out, (48, 119), scene.get("eyebrow", ""), 14, "m", "#a4c6bc", limit=530)
    txt(out, (46, 180), scene.get("title", ""), 51, "s", PAPER, limit=565)


def opened(out, plan, scene, t):
    v = smooth(t / 0.72)
    d = ImageDraw.Draw(out)
    for j in range(3):
        rx, ry = 115 + j * 28 + round(6 * math.sin(v * math.pi)), 21 + j * 6
        d.ellipse((348 - rx, 429 - ry, 348 + rx, 429 + ry), outline=(54 + 3 * j, 89 + j, 88 + j), width=1)
    logo = image(plan, scene.get("asset", plan["mark"]), 177)
    w, h = logo.size
    for j in range(3):
        a, b = round(j * h / 3), round((j + 1) * h / 3)
        crop = logo.crop((0, a, w, b))
        e = smooth((t - 0.055 * (2 - j)) / 0.65)
        tile = crop.resize((round(w * (0.84 + 0.16 * e)), max(1, round(crop.height * (0.14 + 0.86 * e)))), Image.Resampling.LANCZOS)
        center(out, tile, 348, 337 + (a + (b - a) / 2 - h / 2) * (0.16 + 0.84 * e) + 24 * (1 - e))
    y = round(18 * (1 - smooth((t - 0.15) / 0.58)))
    txt(out, (348, 524 + y), scene.get("verb", "Comment"), 62, "s", anchor="mm", limit=550)
    txt(out, (348, 625 + y), scene["keyword"], 104, "b", "#d0f6d9", "mm", 545)
    ln(out, [(261, 714), (435, 714)], "#60857c", 1)
    txt(out, (348, 753), scene.get("subtitle", ""), 23, "g", "#ccddd2", "mm", 530)


def badge(out, plan, item, x, y, size):
    if item.get("dark_tile"):
        rr(out, (x - size * 0.62, y - size * 0.62, x + size * 0.62, y + size * 0.62), round(size * 0.28), "#172627", "#81a4a0")
    center(out, image(plan, item["asset"], size), x, y)


def draw_scene(plan, scene, pos):
    fps = plan["fps"]
    t = pos / fps
    mode = scene["recipe"]
    full = mode in {"brand-open", "repo-tour", "before-after", "parallel-jobs"}
    out = backing(plan.get("theme", "ink"), full).copy()
    if mode == "brand-open":
        header(out, plan)
        opened(out, plan, scene, t)
        return out
    if mode == "repo-tour":
        out = wash(720, 1280, "light").copy()
        header(out, plan, True)
        txt(out, (50, 286), scene.get("eyebrow", "PROJECT README"), 14, "m", "#43655e")
        txt(out, (48, 333), scene["title"], 51, "s", INK, limit=568)
        x, y, w, h = 43, 388, 574, 459
        rr(out, (x + 3, y + 7, x + w + 4, y + h + 8), 22, "#bbccc1")
        rr(out, (x, y, x + w, y + h), 22, "#f8faf5", "#94aea1", 2)
        for j, col in enumerate(("#d79182", "#dfbf71", "#86ae8c")):
            ell(out, (x + 23 + j * 13, y + 23), 4, col)
        txt(out, (x + 91, y + 23), scene["source_label"], 16, "m", INK, limit=444)
        # Local tall capture, not a fabricated browser or a network fetch.
        page = image(plan, scene["asset"], 12000)
        view = round((h - 55) / w * page.width)
        a, b, c = scene["scroll_frames"]
        offsets = scene["scroll_y"]
        limit = max(0, page.height - view)
        at = offsets[0] + (offsets[1] - offsets[0]) * smooth(pos / a) + (offsets[2] - offsets[1]) * smooth((pos - b) / (c - b))
        top = round(limit * at)
        crop = page.crop((0, top, page.width, min(page.height, top + view))).resize((w - 12, h - 55), Image.Resampling.LANCZOS)
        out.paste(crop, (x + 6, y + 45))
        if scene.get("detail") and pos >= a:
            tile = image(plan, scene["detail"], 2000)
            box = scene.get("detail_box", [0, 0, 1, 1])
            tile = tile.crop(tuple(round(v * (tile.width if j % 2 == 0 else tile.height)) for j, v in enumerate(box)))
            txt(out, (50, 890), scene.get("detail_label", "Closer look"), 26, "s", INK, limit=550)
            out.paste(ImageOps.contain(tile, (544, 168)), (52, 931))
        return out
    if mode == "before-after":
        header(out, plan)
        txt(out, (51, 285), scene.get("eyebrow", "COMPARISON"), 16, "m", MINT)
        txt(out, (48, 355), scene["title"], 65, "s", limit=552)
        for j, item in enumerate(scene["items"]):
            x = 49 + j * 279
            glass(out, (x, 466, x + 252, 704))
            tray(out, x + 120, 535, j == 0)
            txt(out, (x + 22, 604), item["name"], 37, "b", limit=213)
            txt(out, (x + 22, 659), item["detail"], 22, "g", "#b7d0c8", limit=212)
        p = smooth((t - 0.38) / 1.36)
        folio(out, 167 + 279 * p, 507 - 58 * math.sin(math.pi * p), 0.72)
        return out
    if mode == "parallel-jobs":
        header(out, plan)
        txt(out, (50, 334), scene["title"], 63, "s", limit=555)
        txt(out, (50, 410), scene.get("subtitle", ""), 66, "s", MINT, limit=559)
        for k, item in enumerate(scene["items"]):
            y = 508 + 112 * k
            glass(out, (50, y, 594, y + 91), "#264748", 20)
            badge(out, plan, item, 100, y + 45, 48)
            txt(out, (151, y + 45), item["name"], 23, "b", limit=146)
            txt(out, (512, y + 45), item["detail"], 24, "s", MINT, "rm", 80)
            ln(out, [(306, y + 46), (427, y + 46)], "#678c82", 2)
            ell(out, (310 + 110 * smooth(t / 0.98), y + 46), 5, "#b5e4c4")
        return out
    if mode == "voice-sculpture":
        from .objects import sculpture

        sculpture(out, plan, scene, pos)
    elif mode == "character-sweep":
        from .objects import character

        character(out, image(plan, scene["asset"], 215), t)
    elif mode == "logo-dock":
        # Bring transparent component images exported from the real model, and a matching page plate.
        # Each image can have a different silhouette; no guessed vector replacement.
        page = image(plan, scene["page"], 1800)
        pg = ImageOps.fit(page, (553, 207), centering=(0.5, 0))
        out.paste(pg, (48, 264))
        dock = smooth((t - 0.22) / 0.87)
        opening = smooth((t - 1.09) / 0.41)
        spread = 1.35 * (1 - dock) + (0.10 + 0.90 * opening) * dock
        tx, ty, tw = scene["target"]
        cx = 326 * (1 - dock) + (48 + 553 * tx) * dock
        cy = 361 * (1 - dock) + (264 + 207 * ty) * dock
        width = 226 * (1 - dock) + 553 * tw * dock
        for key in scene["parts"]:
            # All layers retain registration from a common original canvas. At rest their union is the original mark.
            path = Path(plan["_assets"][key])
            im = registered(str(path), path.stat().st_mtime_ns)
            box = im.getbbox()
            if not box:
                continue
            scale = width / im.width
            left, top, right, bottom = box
            tile = im.crop(box).resize((max(1, round((right - left) * scale)), max(1, round((bottom - top) * scale * min(1, spread)))), Image.Resampling.LANCZOS)
            center(out, tile, cx + ((right + left) / 2 - im.width / 2) * scale, cy + ((top + bottom) / 2 - im.height / 2) * scale * spread)
    elif mode == "logo-roster":
        for j, item in enumerate(scene["items"]):
            hit = item["at"] / fps
            e = smooth((t - hit + 0.025) / 0.19) * (1 - smooth((t - hit - item.get("hold", 0.8)) / 0.20))
            x = 50 + j * 191
            y = round(269 + (1 - smooth((t - 0.04 * j) / 0.45)) * 14 - 10 * e)
            glass(out, (x, y, x + 169, y + 166), "#426c65" if e > 0.28 else "#355856", 22)
            if e > 0.03:
                r = round(29 + 26 * smooth((t - hit + 0.02) / 0.23))
                col = tuple(round(a + (b - a) * e) for a, b in zip((71, 106, 100), (166, 211, 185), strict=True))
                ImageDraw.Draw(out).ellipse((x + 84 - r, y + 61 - r, x + 84 + r, y + 61 + r), outline=col, width=2)
            badge(out, plan, item, x + 84, y + 60, round(76 + 8 * e))
            txt(out, (x + 84, y + 127), item["name"], 21, "b", PAPER, "mm", 156)
    elif mode == "typing":
        from .canvas import font

        glass(out, (48, 274, 611, 405))
        txt(out, (72, 337), "›", 38, "m", MINT)
        text = scene["text"][: bisect.bisect_right(scene["keys"], pos)]
        txt(out, (112, 338), text, 33, "m", limit=468)
        x = min(585, 115 + font(33, "m").getlength(text))
        rr(out, (x, 318, x + 3, 351), 1, MINT)
    elif mode == "file-restore":
        glass(out, (48, 271, 611, 452))
        tray(out, 151, 373)
        tray(out, 507, 373, True)
        for x, label in zip((151, 507), scene["labels"], strict=True):
            txt(out, (x, 420), label, 18, "g", "#b7d0c8", "mm", 155)
        times = scene["travel"]
        v = smooth((pos - times[0]) / (times[1] - times[0])) - smooth((pos - times[2]) / (times[3] - times[2]))
        folio(out, 151 + 356 * v, 340 - 46 * math.sin(math.pi * v), 0.72)
    elif mode == "asset-reveal":
        u = smooth(t / 0.42)
        art = image(plan, scene["asset"], round(120 + 62 * u))
        center(out, art, 325, 357 + 18 * (1 - u))
    elif mode == "file-handoff":
        a, b = scene["items"]
        glass(out, (48, 266, 609, 446), "#203f46", 25)
        badge(out, plan, a, 106, 352, 64)
        badge(out, plan, b, 551, 352, 60)
        ln(out, [(154, 349), (499, 349)], "#739b91", 2)
        x = 247 + 70 * smooth((t - 0.02) / 1.15)
        rr(out, (x - 65, 302, x + 87, 383), 15, "#d9eae1", "#f2f6ed")
        folio(out, x - 39, 340, 0.42)
        txt(out, (x - 16, 341), scene["file_label"], 15, "b", INK, limit=95)
        txt(out, (329, 416), scene.get("subtitle", ""), 18, "m", MINT, "mm", 340)
    # Keep the headline, not just the small labels, clear of the Instagram profile area.
    safe = backing(plan.get("theme", "ink"), True).crop((0, 0, 720, 536))
    safe.paste(out.crop((0, 250, 720, 486)), (0, 300))
    header(safe, plan)
    txt(safe, (51, 178), scene.get("eyebrow", ""), 13, "m", "#8bb0a5", limit=526)
    txt(safe, (49, 278), scene.get("title", ""), 39, "s", PAPER, limit=546)
    return safe
