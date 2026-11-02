---
name: altocue-ugc
description: Plans UGC-style ads and generated-host films honestly: truth check, recipe choice, quote, disclosure. Use when the user wants UGC, a UGC ad, an AI actor, an AI influencer, a generated presenter, a testimonial-style video, or a film that looks shot on a phone. Part of Altocue.
license: MIT
compatibility: Requires the altocue CLI. Generating needs Higgsfield, fal.ai or your own clips.
---

# Altocue UGC

This skill decides what may be made. It holds no prompts. `altocue-direct` plans the shots, `altocue-generate` spends, and [the phone-footage notes](../altocue-generate/references/ugc.md) hold the craft.

Nothing here is legal advice. Tell the user that plainly, and tell them to have counsel check anything commercial.

## 1. Intake

Ask for what you cannot infer: the product, who it is for, where it will run, the length, and an approved list of claims. Without that list, the script describes only what can be seen.

## 2. The truth check

Do this before any money moves. The line is what is claimed, not how real it looks.

| Content | Position |
|---|---|
| A generated host shows visible features, labelled | Fine with care |
| A generated host repeats approved claims, labelled | Fine if the claims are substantiated |
| A clearly fictional skit | Fine if it is obviously fiction |
| A real customer's real words performed by an avatar, with consent, labelled | Stop and send it to counsel |
| A real creator's own footage, cleaned up in Altocue | Normal editing |
| A generated person says they bought, used or got results | **Blocked** |
| Generated before and after results | **Blocked** |
| A generated person captioned as a customer or verified buyer | **Blocked** |
| A real person's likeness or voice without consent | **Blocked** |
| Removing an AI label or provenance mark | **Blocked** |

A label does not cure a blocked line. Offer a rewrite in the host's voice, about what can be seen, or offer to film a real customer. Never reword a claim so the gate misses it.

## 3. Pick and price

1. `altocue recipes search ugc`, then `altocue recipes show <id>`. The usual picks are `ugc-video`, `marketing-studio` and `talking-head`. For a user with no Higgsfield account, the recipe names its fallback.
2. `altocue recipes quote <id> --seconds 15 --resolution 1080p`. Show the figure, its basis and its date.
3. Say where files go. Uploading recipes refuse until the project agrees: show the Higgsfield warning, and run `altocue generate --allow-upload higgsfield` only if the owner says yes.

## 4. Make it

- **Flag the shot in `shots.json`:** `"synthetic": ["person", "voice"]`, with the spoken line in `"script"`.
- **Run `altocue direct`.** It refuses a line that claims experience, before any spend.
- **Follow the MCP protocol in `altocue-generate`:** reserve, load the workflow by name, quote, spend with the user's yes, import with provenance. Typing `/ugc-video` silently loses the truth gate.
- **Treat every clip as a rush.** Generated clips go through the same cut, grade and review as footage.

## 5. Disclose

- **Put a label on screen.** Add a chip such as `AI-generated actor` in `motion.json`, in the caption-safe area, for at least 2 seconds. `altocue qa` reports the film if it is missing. Set your own words under `[disclosure] label`.
- **An exemption is a recorded decision.** Set `[disclosure] exempt` with the reason in your own words. Nobody checks it, so ask the user.
- **New York requires it in ads.** Since June 9, 2026, an advertiser who knows an ad has a synthetic performer must disclose it conspicuously (GBL 396-b). That covers honest generated hosts too. No wording is prescribed. Audio-only ads, translation-only uses and matching ads for expressive works are exempt.
- **The FTC bars fake testimonials.** That means a review or testimonial from someone who does not exist or did not use the product (16 CFR 465.2). An ad that shows actual consumers must use real ones, or say clearly that they are not (255.2).
- **The EU and UK apply too.** The AI Act's deep fake duty (Article 50(4)) likely reaches a realistic generated ad, and the ASA applies its code in full to AI ads. In Canada, a testimonial needs someone who actually gave or approved it (Competition Act 74.02).
- **Flip every platform toggle, because the file cannot do it for you.** Give the user this list:
  1. **TikTok ads:** turn on the AI disclaimer at ad creation. It locks once submitted, and duplicating a campaign resets it.
  2. **TikTok organic:** turn on the creator AI toggle.
  3. **Meta organic:** use the AI disclosure tool for photorealistic video and realistic audio. For Meta ads, check the current rule, because we could not confirm it for video.
  4. **YouTube:** declare realistic synthetic content in Studio.
- **Keep the sidecars.** Every `.provenance.json` stays with the master.

## 6. Check

- `altocue qa` runs `claims` and `disclosure` with every other gate.
- `altocue qa --check ugc` measures speech rate, the cold open and baked text. It cannot measure lip closure, so it writes `review/lip-sheet.jpg` and reports the check as skipped. Look at the sheet, or have a person look.
- A skipped check is not a pass. Say what was not checked.
