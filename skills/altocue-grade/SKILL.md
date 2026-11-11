---
name: altocue-grade
description: Corrects colour from code and applies looks. Converts iPhone HDR to SDR, fixes white balance and exposure anchored on the face, matches shots to each other, and bakes a film look or the user's own .cube LUT with skin protected. Use when footage looks washed out, too dark, too warm, inconsistent between shots, or the user asks for colour correction, a look, a LUT or a film feel. Part of Altocue.
license: MIT
compatibility: Requires the altocue CLI.
---

# Altocue Grade

## Steps

1. `altocue grade` measures each clip and writes one `.cube` per clip to `work/luts/` plus `report.json`.
2. Read the report: what moved, and by how much. Corrections are capped on purpose.
3. Render a draft and look at frames with skin in them.

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
| `warm`, `cool`, `punchy`, `filmic` | A small taste on the correction. `punchy` lifts weak colour most and holds strong colour |
| `neutral_show` | One quiet curve for a series, with no colour opinion |
| `clean_commercial` | Crisp and neutral: true whites, clean blacks, weak colour lifted, strong colour held |
| `product_white` | A white background goes white, greys stay grey, the product keeps its colour |
| `warm_lifestyle` | Bright and golden, a soft top, greens toward olive |
| `airy_bright` | Lifted shadows, gentle colour, a little warmth |
| `soft_pastel` | Open mids, low contrast, gentle colour |
| `earthy_doc` | Natural contrast, olive greens, slate blues |
| `warm_film` | Soft shoulder, warm mids and highlights, greenish shadows |
| `cool_film` | Steel shadows, cooler mids, neutral highlights |
| `print_film` | Toe and shoulder, dense colour, cool shadows, a warm top |
| `vintage_print` | An old print: cream whites, blue in the blacks, reds toward orange |
| `teal_orange` | Cool hues lean teal and warm hues keep their own. Greys, whites and lips are left alone |
| `moody_low` | Dark mids, capped whites, quiet colour, cool green shadows |
| `faded_matte` | Lifted blacks, capped whites, less colour |
| `bleach_bypass` | Hard contrast, drained colour |
| `night_blue` | Darker mids and cool shadows. Lamps and skin stay warm |
| `neon_night` | Violet shadows, magenta and cyan pushed, warm skin kept |
| `mono_contrast` | Black and white with a hard curve. It takes a dark face down a long way, so look at faces |
| `mono_silver` | A softer black and white that keeps the whole tonal range |

- **Match the look to the job.** Product and software: `clean_commercial` or `product_white`. Lifestyle: `warm_lifestyle` or `airy_bright`. Brand film: `print_film` or `teal_orange`. Documentary: `earthy_doc`. Night: `night_blue` or `neon_night`.
- **Brand colours beat looks.** If a logo or product colour must be exact, use `natural`, `neutral_show`, `clean_commercial` or `product_white`.
- **Flags are for trying.** To keep a choice through `altocue run`, set `look`, `look_strength` or `lut` under `[grade]` in `altocue.toml`. A relative `lut` path starts at the project.
- **A LUT replaces the look.** Asking for both is refused.
- **Strength runs from 0 to 1.** Start around 0.6 for anything strong. Most films want less than the full recipe.
- **Bring your own LUT as a `.cube`,** 1D or 3D. Altocue bundles none, and these looks are generated in code, so nothing here is a third party's LUT.
- **A LUT made for log footage will look wrong here.** The correction hands it display-referred BT.709, and log footage is converted first.

## Log footage

Log footage looks flat and grey until its curve is undone, and no tag in the file proves which curve it is. So Altocue never guesses one.

```toml
[grade]
input = "slog3"            # every clip the tags do not settle
input = { a = "vlog" }     # or by clip id, for a mixed shoot
```

- **`auto` is the default.** It converts only what the file's own tags prove, which is HDR.
- **Name the curve:** `apple_log`, `slog3`, `vlog`, `clog3`, `flog2` or `dlog`. Each assumes the camera's usual wide gamut. The conversion is baked ahead of the correction, in the clip's one cube.
- **Read the summary.** `may be log footage` means a 10 bit clip looks flat or is tagged the way Apple Log is. Ask the user which camera and picture profile, then set `input`. If it is not log, `input = "video"` ends the note.
- **Do not name a curve on a guess.** The wrong curve gives wrong colour that no correction repairs.
- **Look at the draft.** A log clip should come out with ordinary contrast and believable skin. If it is still flat, or harsh, the curve is the wrong one.

## Rules

- **Grade in order, and demonstrate it in order.** Balance first: white balance, then exposure and contrast. Then secondary work, such as skin. Then the look, and a last check. A before and after on screen goes the same way, from a flat camera file with its cast, through each correction, to the look. Never the finished frame first and a filter after it.

- **Skin comes first.** If a correction moves skin hue too far, it backs off by itself. Do not fight that.
- **Looks protect skin too.** Colours near this clip's skin take a quarter of the look. If skin hue still moves past 3 degrees or the face past 10% in level, the guard tightens, and only then does the whole look back off, to no less than a quarter of the strength you asked for. The report says what each clip took: `look_strength` and `look_skin_moved`.
- **A look that removes colour is not guarded,** so black and white stays black and white.
- **Two people with very different skin share one clip's guard.** Check both faces in the draft.
- **One correction per clip.** Per-frame correction flickers.
- **If nothing changed,** the clip probably needed nothing, or there was no face to anchor on. Check the report.
- **HDR phone footage is handled.** Never ask the user to convert it first.
