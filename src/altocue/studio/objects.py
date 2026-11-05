"""Original voice-reactive metal surface and source-art character motion; no external service."""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from .. import ffmpeg
from .canvas import center, ell, smooth


@lru_cache(6)
def signal(path, stamp):
    raw = ffmpeg.run_bytes(["-i", str(path), "-vn", "-ac", "1", "-ar", "12000", "-f", "f32le", "-"])
    return np.frombuffer(raw, np.float32)


@lru_cache(1)
def mesh():
    lons, lats = 86, 23
    u, v = np.meshgrid(np.arange(lons) * 2 * np.pi / lons, np.arange(lats) * 2 * np.pi / lats, indexing="ij")
    i, j = np.meshgrid(np.arange(lons), np.arange(lats), indexing="ij")
    quad = np.stack([i * lats + j, (i + 1) % lons * lats + j, (i + 1) % lons * lats + (j + 1) % lats, i * lats + (j + 1) % lats], axis=-1).reshape(-1, 4)
    return u, v, quad


def sculpture(out, plan, scene, pos):
    path = Path(plan["_assets"][scene["voice"]])
    a = signal(path, path.stat().st_mtime_ns)
    at = (scene.get("voice_start", scene["start"]) + pos) / plan["fps"]
    t = pos / plan["fps"]
    # A finite, deterministic release envelope supports random frame access.
    levels = []
    for j in reversed(range(16)):
        n = round((at - j / plan["fps"]) * 12000)
        v = a[max(0, n - 420) : max(0, n + 420)]
        levels.append(min(1, 6 * float(np.sqrt(np.mean(v * v)) if len(v) else 0)))
    env = 0
    for v in levels:
        env = 0.76 * env + 0.24 * v
    u, v, quad = mesh()
    th = 2 * np.pi * t / 5.8
    twist = 0.76 + 0.25 * np.sin(3 * u - th) + 0.07 * env
    rad = 1.08 + 0.23 * np.cos(3 * u + th)
    tube = 0.29 + 0.055 * np.sin(2 * u - th) + 0.08 * env
    pts = np.stack([(rad + tube * np.cos(v)) * np.cos(u), (rad + tube * np.cos(v)) * np.sin(u), 0.39 * np.sin(3 * u - th) + tube * np.sin(v) * twist], -1)
    ay = 0.42 + 0.51 * t
    ax = 0.62 + 0.26 * np.sin(0.74 * t)
    rot = np.array([[np.cos(ay), 0, np.sin(ay)], [0, 1, 0], [-np.sin(ay), 0, np.cos(ay)]]) @ np.array(
        [[1, 0, 0], [0, np.cos(ax), -np.sin(ax)], [0, np.sin(ax), np.cos(ax)]]
    )
    verts = (pts @ rot.T).reshape(-1, 3)
    faces = verts[quad]
    norm = np.cross(faces[:, 1] - faces[:, 0], faces[:, 3] - faces[:, 0])
    norm /= np.maximum(0.00001, np.linalg.norm(norm, axis=1, keepdims=True))
    norm *= np.where(norm[:, 2:3] < 0, -1, 1)
    shade = np.clip(norm @ np.array([-0.52, -0.42, 0.75]), 0, 1)
    stripe = np.exp(-(((norm[:, 0] * 0.77 + norm[:, 1] * 0.22 + norm[:, 2] * 0.54 - 0.41) / 0.115) ** 2))
    rim = (1 - norm[:, 2]) ** 2
    copper = np.clip(0.52 + 0.7 * norm[:, 1] - 0.28 * norm[:, 0], 0, 1)
    color = (
        (np.array([114, 184, 170]) * (1 - copper[:, None]) + np.array([218, 155, 113]) * copper[:, None]) * (0.26 + 0.66 * shade[:, None])
        + stripe[:, None] * np.array([125, 118, 104])
        + rim[:, None] * np.array([52, 57, 51])
    )
    color = np.uint8(np.clip(color, 0, 254))
    z = 3.9 / (3.9 - 0.35 * verts[:, 2])
    sc = 58 + 7 * smooth(t / 0.87) + 3 * env
    xy = np.int32(np.rint(np.stack([375 + sc * z * verts[:, 0], 363 - sc * z * verts[:, 1]], 1) * 16))
    raster = np.array(out)
    for k in np.argsort(faces[:, :, 2].mean(axis=1)):
        cv2.fillConvexPoly(raster, xy[quad[k]], tuple(map(int, color[k])), lineType=cv2.LINE_AA, shift=4)
    out.paste(Image.fromarray(raster))
    last = 360.0
    for j, x in enumerate(range(69, 252, 4)):
        n = round(at * 12000) - 750 + j * 29
        v = float(a[n]) if 0 <= n < len(a) else 0
        if j:
            ImageDraw.Draw(out).line([(x - 4, last), (x, 360 + v * 65)], fill="#b6e6c9", width=2)
        last = 360 + v * 65


def character(out, ic, t):
    ic = ic.copy()
    d = ImageDraw.Draw(ic)
    for frac in (0.43, 0.59):
        x, y = round(ic.width * frac), round(ic.height * 0.51)
        d.ellipse((x - 5, y - 7, x + 5, y + 7), fill="#f2f5ec")
        d.ellipse((x - 1, y - 3, x + 4, y + 4), fill="#152f32")
    d.arc((ic.width * 0.43, ic.height * 0.53, ic.width * 0.64, ic.height * 0.62), 5, 150, fill="#152f32", width=2)
    wave = math.sin((t - 0.06) * 6)
    ic = ic.rotate(wave * 8, resample=Image.Resampling.BICUBIC, expand=True)
    cx = 355 + 18 * wave - 20 * (1 - smooth(t / 0.32))
    cy = 364 - 7 * abs(wave)
    center(out, ic, cx, cy)
    for j in range(7):
        ell(out, (cx - 25 - j * 9 + wave * 4, cy + 215 * 0.45 - j % 3 * 4), 1 + j % 2, "#9eb9a0")
