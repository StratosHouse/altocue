---
name: altocue-generate
description: Generates images and video inserts through model providers such as fal.ai and Higgsfield, behind a spend gate, with provenance recorded next to every file. Use when the user wants B-roll, an insert, a generated shot or an image for a video. Part of Altocue.
license: MIT
compatibility: Requires the altocue CLI, plus FAL_KEY for fal.ai, or the Higgsfield CLI or MCP server.
---

# Altocue Generate

## Rules

- **Always show the cost first.** `altocue direct` prints it. Wait for a yes before anything paid runs.
- **Set a cap.** `[generate] spend_cap_usd` in `altocue.toml`. At 0, every paid call is blocked.
- **Draft first.** Tier `draft`, then finalise only the keepers.
- **Ask for a tier, not a model.** `draft`, `standard` or `hero`. Model ids live in `models.toml` and change often.
- **A refused prompt stays refused.** Do not reword it to get around moderation. Ask the user.

## Steps

1. Write `shots.json` (see `altocue-direct`), then `altocue direct` to check it.
2. Pick a provider: `[generate] provider = "fal"` or `"higgsfield"`.
3. `altocue generate --yes` runs the approved shots. Jobs resume after a crash without paying twice.
4. Check every result: `altocue direct --check <file>` extracts frames and scores motion. Open the frames, edges included. Generated phone footage can carry a strip of fake interface along one edge. Punch in past it with `zoom_from` and `zoom_to` in `edl.json`.
5. Place it. Over a B-roll spot, use `altocue broll --pick b1=shot:shot-1` (see `altocue-broll`). Anywhere else, use `motion.json`:
   ```json
   { "inserts": [{ "file": "work/generated/shot-1.mp4", "start": 12.4, "end": 15.0 }] }
   ```

## Using the Higgsfield MCP server

If your agent has it connected, generate there and register the result:

```bash
altocue generate --import clip.mp4 --shot shot-1 --prompt "..." --model "..." --cost 0.32 --provider higgsfield
```

`--cost` is in US dollars. Higgsfield bills credits, so multiply them by `usd_per_credit` (0.08 by default). Price it first with the tool's cost check, and pick the cheapest model that fits, because the default video model can cost seven times a budget one.

## Inserts in an edit

- Land on the exact phrase. 2 to 3 seconds each. Under 40% of runtime.
- Voice keeps playing underneath.
- Label AI-generated footage where the platform asks for it.
