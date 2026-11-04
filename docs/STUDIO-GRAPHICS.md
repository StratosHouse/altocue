# Studio graphics and reusable prompts

The Studio collection ships in Altocue 0.3.0 and newer. Update an older tool installation with `uv tool upgrade altocue`. It is original, offline HTML/CSS/SVG. It uses Altocue's bundled open fonts and native timeline, renderer and QA. No subscription, generated footage, music or purchased sound pack is required.

Browse the [full live library](https://www.stratosagency.ai/altocue/library/), preview a format and copy either its agent prompt or native JSON. The same recipe is available offline through `altocue library show <id>` or its `--json` output. Prompts include the actual sample values and slot limits from your installed version.

## Choose by the idea

| Format | Use it for |
| --- | --- |
| `studio_satin_title` | One masked headline with a quiet underline |
| `studio_focus_stack` | A short statement arriving into focus |
| `studio_kinetic_bridge` | Moving from one thought to the next |
| `studio_signal_word` | Landing one important phrase |
| `studio_depth_layers` | Separating three layers of an idea |
| `studio_depth_handoff` | Showing stages of responsibility |
| `studio_focus_deck` | Revealing a hierarchy |
| `studio_object_orbit` | A restrained turn into a readable object stack |
| `studio_route_nodes` | Connecting stages along a route |
| `studio_role_cluster` | Giving each role a distinct job |
| `studio_linked_steps` | Explaining a connected process |
| `studio_workflow_lanes` | Keeping stages and ownership separate |
| `studio_task_transfer` | Selecting a task and naming an owner |
| `studio_link_receipt` | Passing a linked file from an owner to a receiver |
| `studio_select_focus` | Showing a choice among alternatives |
| `studio_approval_gate` | Explaining an approval rule, without claiming a real approval |
| `studio_calendar_lift` | Lifting one task from an illustrative calendar |
| `studio_edit_window` | Explaining the stages of an edit |
| `studio_retake_trim` | Illustrating a removed retake |
| `studio_caption_focus` | Highlighting the words that matter |
| `studio_sound_wave` | An illustrative signal, not a measured audio result |
| `studio_chapter_fold` | Beginning a chapter |
| `studio_compare_slide` | Comparing two short statements |
| `studio_progress_path` | Showing milestones, without simulated job progress |
| `studio_save_card` | Holding a clear final action |

## Native example

Add this to `items` in the project's `motion.json`:

```json
{
  "type": "studio_link_receipt",
  "title": "Hand off the actual work",
  "items": [
    {"text": "Editor"},
    {"text": "draft.mp4"},
    {"text": "Reviewer", "at": 4.48}
  ],
  "start": 1.0,
  "duration": 6.0
}
```

This draws an original paper file and chain symbol, then moves the file toward its receiver. It is a visual explanation; it does not send a file, operate Slack or imply that an agent service is running. Replace sample filenames and roles with real ones when presenting an actual workflow.

The row-based depth, route, task and close formats accept an optional absolute timeline `at` on a row. Its arrival settles over 0.42 seconds. A linked handoff uses its receiver's `at` as the send time. Without row times, the format provides its own stagger. The calendar, illustrative signal and type formats use their built-in timing.

`studio_calendar_lift` also accepts an absolute `circle_at` time for a smooth circle around its middle task. Use this for an actual instruction such as choosing an illustrative calendar task, with a cue as the circle closes.

## Direct the edit

Use one graphic to explain one complete thought, then return to the speaker or real product proof. Do not cover every sentence with a text card. Keep factual demonstrations tied to the actual source, and label illustrative diagrams. Preserve complete first and last words and full closing thoughts.

Studio formats default to six seconds, accept three to twelve seconds, and paint the whole frame. Portrait copy fits a stricter content box, leaving room for social controls and separately placed captions. If a caption band is needed, choose it deliberately in the project layout and inspect the final composite. The format's own slot limits fail early when copy is too long.

Starter examples explicitly declare `held: true`: their settled full-frame graphic is a planned reading hold. In a film storyboard, a graphics/still-only scene can additionally declare `reading_hold: true`. Film QA reports these declared holds separately from unplanned dead air. It never excuses a frozen video under a card, including video in the backdrop. Quiet while reading is planned only when that scene has no declared voice; missing speech is still checked. Keep these declarations tied to the actual design rather than using them to conceal a stopped video.

Choose an aspect ratio and use `altocue motion`, `altocue timeline` or a native film storyboard. Render with Altocue and run `altocue qa`. Technical checks establish measured file properties; an automated frame reviewer does not establish sound taste or human approval.

## Sound follows the visible action

Sound is optional. Use a quiet contact when a card settles, a short travel cue as an artifact moves and a restrained click when a selection changes. Use typing only while text visibly types. Calculate absolute cue times against the actual graphic or row arrival, rather than placing a sound arbitrarily at each cut. Duck cues under speech and inspect the delivered mix.

Public previews are silent. No paid audio is included in Altocue, its recipes or these graphics. Users supply their own licensed sounds through native motion SFX cues. A written cue suggestion is not permission to download, redistribute or purchase sound.
