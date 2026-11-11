---
name: altocue-grade
description: Corrects colour from code and applies looks. Converts iPhone HDR to SDR, fixes white balance and exposure anchored on the face, matches shots to each other, and bakes a film look or the user's own .cube LUT with skin protected. Use when footage looks washed out, too dark, too warm, inconsistent between shots, or the user asks for colour correction, a look, a LUT or a film feel. Part of Altocue.
license: MIT
compatibility: Requires the altocue CLI.
---

# Altocue Grade

## Steps

1. `altocue grade` measures each clip and writes one `.cube` per clip to `work/luts/` plus `report.json`.
2. Read the report: what moved, and by how much. Corrections are capped on purpose. Read any `warnings` too.
3. Render a draft and look at frames with skin in them.

## After a re-cut

The grade stands through a re-cut. Each clip is read at fixed times over all of it, and the frames the cut keeps count for more. Trimming a word changes no colour, so the plate keeps the shots it has already encoded.

- **A clip cut very differently is measured again by itself,** once its kept share has moved by more than 30 percent. `altocue status` shows the grade as stale when that is due.
- **`altocue grade --resample` measures again on request.** A result that barely moved keeps the LUT it had, byte for byte, and the report lists that clip under `held`.
- **`--force` solves afresh and keeps nothing.** Use it only when you want new colour.

## Looks

Start with `natural`. Reach for a look only when the brief calls for one, and see them first:

```bash
altocue grade --compare                        # every look on one real frame: work/luts/looks.jpg, and each alone in work/luts/looks/
altocue grade --look teal_orange --look-strength 0.6
altocue grade --lut ~/luts/show.cube
```

| Look | What it does |
|---|---|
| `natural` | Correction only. The default |
| `warm`, `cool`, `punchy`, `filmic` | A small taste on the correction |
| `warm_film` | Soft shoulder, warm mids and highlights, greenish shadows |
| `cool_film` | Steel shadows, cooler mids, neutral highlights |
| `faded_matte` | Lifted blacks, capped whites, less colour |
| `mono_contrast` | Black and white with a hard curve |
| `bleach_bypass` | Hard contrast, drained colour |
| `teal_orange` | Warm hues lean orange, the others teal, cool shadows |
| `soft_pastel` | Open mids, low contrast, gentle colour |
| `neutral_show` | One quiet curve for a series, with no colour opinion |

- **Flags are for trying.** To keep a choice through `altocue run`, set `look`, `look_strength` or `lut` under `[grade]` in `altocue.toml`. A relative `lut` path starts at the project.
- **A LUT replaces the look.** Asking for both is refused.
- **Strength runs from 0 to 1.** Start around 0.6 for anything strong. Most films want less than the full recipe.
- **Bring your own LUT as a `.cube`,** 1D or 3D. Altocue bundles none, and these looks are generated in code, so nothing here is a third party's LUT.
- **A LUT made for log footage will look wrong here.** The correction hands it display-referred BT.709.
- **The look reaches inserts.** The grade also writes `work/luts/look.cube`: the look alone, with no correction and no skin fit. B-roll, stills and generated shots picked with `altocue broll --pick` take it, after the HDR conversion where one is needed. Change the look and the next `altocue render` prepares them again. `natural` writes no such file and leaves inserts as they are. A screen recording keeps its own colour: see `altocue-broll`.

## Rules

- **Grade in order, and demonstrate it in order.** Balance first: white balance, then exposure and contrast. Then secondary work, such as skin. Then the look, and a last check. A before and after on screen goes the same way, from a flat camera file with its cast, through each correction, to the look. Never the finished frame first and a filter after it.

- **Skin comes first.** If a correction moves skin hue too far, it backs off by itself. Do not fight that.
- **Looks protect skin too.** Colours near this clip's skin take a quarter of the look. If skin hue still moves past 3 degrees or the face past 10% in level, the guard tightens, and only then does the whole look back off, to no less than a quarter of the strength you asked for. The report says what each clip took: `look_strength` and `look_skin_moved`.
- **A look that removes colour is not guarded,** so black and white stays black and white.
- **Two people with very different skin share one clip's guard.** Check both faces in the draft.
- **One correction per clip.** Per-frame correction flickers.
- **If nothing changed,** the clip probably needed nothing, or there was no face to anchor on. Check the report.
- **A face left dark is said out loud.** Exposure moves by 8 percent at most. When a face is still well outside the band after that, the report and the stage output carry a warning that names the clip. The fix is light or exposure at the shoot. Tell the user, and never work around the limit with a look.
- **HDR phone footage is handled.** Never ask the user to convert it first.
