---
name: altocue-grade
description: Corrects colour from code. Converts iPhone HDR to SDR, fixes white balance and exposure anchored on the face, matches shots to each other, and applies named looks. Use when footage looks washed out, too dark, too warm, inconsistent between shots, or the user asks for colour correction or a look. Part of Altocue.
license: MIT
compatibility: Requires the altocue CLI.
---

# Altocue Grade

## Steps

1. `altocue grade` measures each clip and writes one `.cube` per clip to `work/luts/` plus `report.json`.
2. Read the report: what moved, and by how much. Corrections are capped on purpose.
3. Render a draft and look at frames with skin in them.

## Looks

Set `[grade] look` in `altocue.toml`: `natural` (default), `warm`, `cool`, `punchy`, `filmic`. Start with `natural`. Reach for a look only when the brief calls for one.

## Rules

- **Skin comes first.** If a correction moves skin hue too far, it backs off by itself. Do not fight that.
- **One correction per clip.** Per-frame correction flickers.
- **If nothing changed,** the clip probably needed nothing, or there was no face to anchor on. Check the report.
- **HDR phone footage is handled.** Never ask the user to convert it first.
