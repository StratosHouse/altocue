"""The Studio canvas. Geometry is expressed in a 720 by 1280 design space."""

from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image as I
from PIL import ImageDraw as D
from PIL import ImageFont as F

T = Path(__file__).resolve().parents[1] / "data/fonts"


def smooth(t):
    t = max(0, min(1, t))
    return t * t * t * (10 + t * (-15 + 6 * t))


def rgba(s):
    return tuple(bytes.fromhex(s.lstrip("#")))


@lru_cache(200)
def font(n, k):
    f = F.truetype(str(T / {"s": "InstrumentSerif-Regular.ttf", "m": "GeistMono-Variable.ttf", "b": "Geist-Variable.ttf", "g": "Geist-Variable.ttf"}[k]), n)
    if k == "b":
        f.set_variation_by_axes([660])
    return f


def txt(im, xy, text, n, k="g", color="#f2f4ed", anchor="lm", limit=None):
    if not text:
        return
    while limit and font(n, k).getlength(text) > limit and n > 10:
        n -= 1
    D.Draw(im).text(xy, text, font=font(n, k), fill=color, anchor=anchor, stroke_width=0)


def rr(im, box, r, fill, edge=None, width=1):
    D.Draw(im).rounded_rectangle(tuple(map(round, box)), r, fill=fill, outline=edge, width=width)


def ln(im, points, colour, width=2):
    D.Draw(im).line(points, fill=colour, width=width, joint="curve")


@lru_cache(128)
def thumbnail(path, n, stamp):
    im = I.open(path).convert("RGBA")
    box = im.getbbox()
    if box:
        im = im.crop(box)
    im.thumbnail((n, n), I.Resampling.LANCZOS)
    return im


def center(im, ic, x, y):
    pos = (round(x - ic.width / 2), round(y - ic.height / 2))
    im.paste(ic, pos, ic.getchannel("A") if ic.mode == "RGBA" else None)


def ell(im, xy, r, co):
    x, y = xy
    D.Draw(im).ellipse((x - r, y - r, x + r, y + r), fill=co)


@lru_cache(15)
def wash(w, h, scheme="ink"):
    y, x = np.mgrid[0:h, 0:w]
    x = x / w
    y = y / h
    if scheme == "ink":
        lo = (9, 24, 31)
        hi = (44, 82, 82)
        ac = (18, 56, 50)
    elif scheme == "light":
        lo = (233, 242, 231)
        hi = (209, 224, 215)
        ac = (245, 242, 225)
    else:
        lo = (15, 26, 33)
        hi = (36, 78, 91)
        ac = (74, 94, 76)
    a = np.exp(-(((x - 0.75) / 0.58) ** 2) - ((y - 0.16) / 0.58) ** 2)
    b = np.exp(-(((x - 0.07) / 0.55) ** 2) - ((y - 0.9) / 0.47) ** 2)
    ga = np.random.default_rng(124).normal(0, 0.62, (h, w, 1))
    ar = np.asarray(lo) + (np.asarray(hi) - lo) * a[:, :, None] + (np.asarray(ac) - lo) * b[:, :, None] + ga
    return I.fromarray(np.uint8(np.clip(ar, 0, 255)))


@lru_cache(32)
def backing(theme, full=False):
    h = 1280 if full else 522
    im = wash(720, h, theme).copy()
    d = D.Draw(im)
    for j in range(4):
        xy = (275 - j * 34, -450 + j * 12, 1040 + j * 34, 465 + j * 23)
        d.arc(xy, 20, 170, fill=(45 + j, 74 + j, 75 + j), width=1)
    return im


def glass(im, box, fill="#25464a", r=19):
    x, y, xx, yy = box
    rr(im, (x + 1, y + 5, xx + 1, yy + 6), r, "#102a2e")
    rr(im, box, r, fill, "#648580")
    ln(im, [(x + r, y + 1), (xx - r, y + 1)], "#8aa99b", 1)


def folio(im, x, y, z=1):
    w, h = round(61 * z), round(76 * z)
    rr(im, (x - w / 2, y - h / 2, x + w / 2, y + h / 2), round(8 * z), "#d7e9df", "#f0f6ed")
    ln(im, [(x + w * 0.12, y - h * 0.49), (x + w * 0.12, y - h * 0.18), (x + w * 0.46, y - h * 0.18)], "#7a9d91", 2)
    for j in range(3):
        ln(im, [(x - w * 0.29, y + j * 7 * z), (x + w * (0.12 if j == 2 else 0.27), y + j * 7 * z)], "#779a8f", 2)


def tray(im, x, y, bin=False):
    if bin:
        rr(im, (x - 24, y - 21, x + 24, y + 26), 7, "#366264", "#9dbcae", 2)
        rr(im, (x - 29, y - 28, x + 29, y - 22), 3, "#a1bfb4")
        rr(im, (x - 10, y - 32, x + 10, y - 29), 2, "#b1c8bc")
        for j in (-9, 0, 9):
            ln(im, [(x + j, y - 14), (x + j, y + 18)], "#a7c4b7", 2)
    else:
        rr(im, (x - 33, y - 16, x + 33, y + 24), 6, "#477470", "#aac7b5", 2)
        rr(im, (x - 30, y - 23, x - 5, y - 10), 4, "#6c9888")
