---
name: altocue-review
description: Runs the Editor in Chief, a separate review of a finished video. Code runs the fact gates, a separate reviewer scores it against a rubric, and code decides SHIP or FIX with timecoded notes. Use after every final render, or when the user asks to review, check, QA or approve a video. Part of Altocue.
license: MIT
compatibility: Requires the altocue CLI. Gemini needs GEMINI_API_KEY. Codex needs the codex CLI.
---

# Altocue Review

The editor never signs off their own work.

## Steps

1. `altocue qa` runs the fact gates. Fix any failure before a review.
2. `altocue review` picks a reviewer.
   - **`gemini`:** sees the actual video. Requires a paid key and the user's yes before upload.
   - **`codex`:** an independent vendor working from the review pack.
   - **`pack`:** writes `review/PACK.md`. Give it to the `editor-in-chief` agent, or any fresh agent, then `altocue review --ingest review/verdict.json`.
3. Read `review/REPORT.md`.
4. Make every fix, in order. Re-render. Review again. At most two rounds, then hand the open list to the user.

## Rules

- **Never argue with a verdict in the report.** Fix it, or tell the user why you disagree.
- **Never edit `verdict.json`.**
- **Never show the reviewer your reasoning.** It gets the file, the brief, the rubric and the numbers. That keeps its read fresh.
- **If the reviewer shares a vendor with you, say so.** The report labels it.
- **Always pass on the section "What this review could not see or hear".**
