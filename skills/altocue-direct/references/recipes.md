# Recipes: hosted commands, and what to try first

A recipe is a hosted command with its cost, what leaves the machine and a fallback for anyone without Higgsfield. See them with `altocue recipes list`, read one with `altocue recipes show <id>`, and price it with `altocue recipes quote <id>`. `altocue-generate` has the protocol for running one. Reach for the free local route first.

| The film needs | Recipe | Try first |
|---|---|---|
| A flat talking head to hit harder | `text-behind`, `freeze-frame`, `over-the-shoulder` | Local motion graphics |
| A spoken ranking or a number climbing | `tier-list`, `glass-graph` | A native format, with no upload |
| Colour shown as the story | `grade-wipe` | `altocue-grade` |
| A cutaway on a phrase | `seedance-insert`, `product-motion`, `product-stills` | Stock, through `altocue-broll` |
| A demo with no filmed presenter | `ugc-video`, `marketing-studio`, `ai-influencer` | Film a real person. Read `altocue-ugc` first |
| Real words in a presenter's own recorded voice | `talking-head` | Film them |
| An explainer section with no person | `faceless-video` | Native explainer formats |
| Hook tests on a finished ad | `ad-multiplier`, swapping one thing | Cut a new hook by hand |
| Another language | `dubbing`, before caption and master. Text does not follow, so read `altocue-deliver` | Nothing local yet |
| A crop that cuts the subject | `reframe` | `altocue-frame`, because a crop is free |
| A sharper copy of an approved clip | `upscale` | Ship it if it reads on a phone. A higher tier is a new take, not a sharper one |
| A performance moved onto another body | `motion-transfer`, with consent | Nothing local |
| New angles of one location | `3d-scene` | Shoot them |

Reel effects cover the first 60 seconds of one vertical clip and add their own grain, grade and sound. Run them on the graded, uncaptioned picture with the effect's own finishing pass off, stacked in this order: grade-wipe, text-behind, over-the-shoulder, tier-list, glass-graph, captions, freeze-frame. Import the result as a new source, so Altocue still captions and masters it. On-screen text mirrors the speech. Never invent a number, a name or a rank.
