"""Paints the studio that the chrome mark reflects: dark room, soft coloured light panels."""

import sys

import numpy as np
from PIL import Image

W, H = 2048, 1024
x, y = np.meshgrid(np.linspace(0, 1, W), np.linspace(0, 1, H))
image = np.zeros((H, W, 3), dtype=np.float32) + np.array([0.018, 0.028, 0.04])
PANELS = (
    # centre x, centre y, width, height, tilt, colour
    (0.08, 0.34, 0.035, 0.34, 0.5, (0.93, 1.00, 0.96)),
    (0.20, 0.62, 0.02, 0.40, -0.4, (0.40, 0.95, 0.72)),
    (0.33, 0.30, 0.05, 0.09, 0.2, (0.62, 0.54, 1.00)),
    (0.46, 0.68, 0.03, 0.30, 0.6, (1.00, 0.56, 0.68)),
    (0.60, 0.36, 0.018, 0.42, 0.4, (1.00, 1.00, 1.00)),
    (0.74, 0.60, 0.05, 0.14, -0.5, (0.44, 0.80, 1.00)),
    (0.90, 0.30, 0.025, 0.30, 0.1, (1.00, 0.82, 0.52)),
)
# A soft pearl wash behind the camera, so the front faces read as metal and not as black glass.
for cx in (0.25, 0.75):
    dx, dy = ((x - cx + 0.5) % 1 - 0.5), y - 0.5
    wash = np.exp(-((dx / 0.2) ** 2 + (dy / 0.34) ** 2))
    tint = np.stack((0.46 + 0.34 * (y - 0.2), 0.62 - 0.1 * y, 0.60 + 0.3 * (1 - y)), axis=-1)
    image += wash[..., None] * tint * 0.62
for cx, cy, w, h, tilt, colour in PANELS:
    dx, dy = ((x - cx + 0.5) % 1 - 0.5), y - cy
    u = dx * np.cos(tilt) + dy * np.sin(tilt)
    v = -dx * np.sin(tilt) + dy * np.cos(tilt)
    glow = np.exp(-((u / w) ** 4 + (v / h) ** 4))
    image += glow[..., None] * np.array(colour, dtype=np.float32)
Image.fromarray((np.clip(image, 0, 1) * 255).astype(np.uint8)).save(sys.argv[1])
