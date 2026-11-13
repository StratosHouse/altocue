---
name: altocue-setup
description: Learns how the user works, once. Asks eight questions, saves the answers as a profile and reads it before every job. Use when Altocue is new on this machine, when no profile exists, at the start of each editing job, or when the user says "set me up" or "change my defaults". Part of Altocue.
license: MIT
compatibility: Requires the altocue CLI.
---

# Altocue Setup

The profile is eight answers, saved once in `profile.toml`. `altocue new` starts every project from it, and you work the way it says. It holds no key and waives no consent.

## At the start of every job

```bash
altocue start --show --json
```

- **`answered` is true:** ask nothing. Read `answers` and follow the table.
- **`answered` is false:** hold the interview once, before the first project.

With no shell, use the `altocue_profile` MCP tool.

## The interview

1. `altocue start --questions` prints the questions as JSON: an `id`, choices with a label and an explanation, a `default`, and any saved `answer`.
2. Ask in chat, in two batches of four: `kind`, `platform`, `brand` and `captions`, then `sound`, `language`, `online` and `checks`. Use your own question tool if you have one, with the labels as written. If it shows fewer options than a question has, show the likeliest and name the rest.
3. `platform` starts from the kind just chosen: read `default_by_kind`.
4. `brand` takes a folder, `language` a code. If `online` is `paid`, ask for a cap in US dollars and pass it as `spend_cap_usd`. Never pick a cap yourself: with no number nothing can spend.
5. Save them all in one command. A wrong value fails and names the valid ones.
   ```bash
   altocue start --answer kind=short --answer platform=vertical --answer brand=house --answer captions=on --answer sound=subtle --answer language=en --answer online=none --answer checks=cut
   ```
6. Read the result back in one line.

- **Never ask twice.** Once a profile exists, change an answer only when the user asks: `altocue start --answer sound=music`.
- **Never block.** If nobody answers, run `altocue start --yes`, which saves the defaults. Say that defaults were used and that `altocue start` changes them.
- **Never answer for the user.** A default is the only answer you may give.

## Honour the answers

| Answer | What you do |
|---|---|
| `kind` | `altocue new` writes it as `[format] kind` in `altocue.toml`, with its starting pace, framing and shape. Read its playbook in `altocue-craft` before the first cut. For one job of another kind, pass `--kind`. |
| `platform` | The shape is already set. Other versions come from `altocue deliver`. |
| `brand` | `house` is the house look, chosen on purpose: do not ask for a kit. `altocue new` reads a folder. If it has moved, ask. |
| `captions` | `on`: in the picture. `file`: subtitle files from `altocue deliver`. `off`: none. The render has no caption switch yet, so for `file` and `off` say so once: `altocue deliver --textless` gives the picture with no captions and no graphics. |
| `sound` | In `motion.json`. `voice`: `"sfx": false` and no music. `subtle`: the house effects, no music. `music`: add a bed with `altocue-sound`. `full`: a bed, and effects on real events. |
| `language` | Already set for `altocue transcribe`. |
| `online` | `none`: no stock search, hosted reviewer or generation. `free`: free stock and sound sources with the user's own keys. `review`: also a hosted reviewer for the final. `paid`: also generation, inside the project's `spend_cap_usd`. |
| `checks` | `every`: stop after each stage. `cut`: show the cut and a draft, then wait. `draft`: straight to a draft, then ask. `end`: run to the finished video, then list every judgement call. |

A flag beats the profile. Once a project exists, its own `altocue.toml` beats both.

## Three stops nothing switches off

Whatever the profile says, stop and wait for a yes before:

1. **Spending money.** Name the provider and the cost, every time.
2. **Uploading a file.** Name where it goes, every time. `review` and `paid` are not a yes.
3. **Going past a failed gate.** Fix it or hand it to the user. Never waive it.
