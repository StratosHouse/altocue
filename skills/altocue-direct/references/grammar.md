# Motion grammar: how to write shots that models obey

Sources: Higgsfield official Seedance guides, ByteDance formula, fal.ai
guides, the OSideMedia community skill (battle-tested failure catalog),
Higgsfield's own faceless pipeline. Verified against our own renders.

## Prompt anatomy (the seven slots, in this order)

1. **Camera**: named move + endpoint ("slow dolly in from medium to medium
   close-up, ending on her hands"). Community finding: camera lands best in
   3rd position overall; the working order is subject, action, camera,
   setting/style, lighting, audio, constraints. Missing 3+ slots correlates
   with filter rejection; all-slots-present almost never gets flagged.
2. **Subject**: identity, position, frame occupancy. FOR IMAGE-TO-VIDEO,
   THE MOST IMPORTANT RULE IN THIS FILE: do not re-describe what the start
   frame already shows. Long appearance description fights the image and
   degrades it. The prompt's job is the NEW motion. (Exception: restate
   critical small details like a label's text, because the model can drop
   them.)
3. **Action**: present tense, unidirectional chains (each action flows one
   way into the next; reversals appear when the model fills empty seconds).
4. **Setting** only as it changes or matters to motion.
5. **Style/lens**: named referents beat adjectives ("35mm film texture,
   shallow depth of field", "macro tilt-shift"), never the empty word
   "cinematic".
6. **Lighting**: practical sources, color temperature, shadow quality.
7. **Constraints**: "keep the same character, same materials, no extra
   people, no new furniture; the set contains only what the reference
   shows." Seedance has no negative field on Higgsfield; bake constraints
   into this slot, most critical first.

Length: 50-80 words for a single shot; structured multi-shot production
prompts legitimately run 500+ words. Sometimes the shortest prompt hits
hardest.

## Camera vocabulary, by reliability

- **Near-certain**: push in / dolly in · pull back wide · static/locked-off
  · pan · tilt · orbit ("slow orbit around...") · crane up/down · handheld
  follow · aerial glide. Single-vector moves execute almost every time.
- **Reliable with care**: tracking ("tracks alongside at chest height,
  matching pace"; always name WHO is followed and the start/end points) ·
  dolly zoom · whip pan · dutch angle · FPV.
- **Translate to observables**: rack focus = "the foreground leaves
  gradually blur while her face changes from soft to sharp". Describe what
  the viewer SEES, not the jargon.
- **ONE primary move per shot.** Stacking conflicting vectors (orbit + zoom
  + pan) warps geometry. Sequential moves must state timing ("pan to follow
  her to the door, then push in when she stops"). State the move ONCE.
- **Endpoint always.** A move without a destination drifts: "ending on",
  "settling at", "until the cottage fills frame".
- To force one unbroken take: "single continuous shot, no cuts". Without
  it, Seedance inserts its own angle cuts.
- Handheld only reads if aggressive: "completely unstabilized, constant
  micro-jitters". Default output is smooth.

## Motion detail: how deep to go

- **Concrete verbs with physics beat adjectives.** "Dropping into a low
  spin, the skirt flaring, then snapping upright" beats "dancing
  beautifully". Vague mood words give the model nothing to point a camera
  at.
- **Secondary motion, one level deep, as REACTIONS.** Arms swing with the
  stride; fur ruffles as the breeze passes; dust motes drift through the
  beam the movement stirred; grass bows under the step; the blanket
  settles after the turn. Name cloth, hair, particles, wind as consequences
  of the primary action, never as separate simultaneous events.
- **Emotion as physics.** "Eyes squeeze shut, cheeks lift, a little bounce
  on the spot", never "looks happy".
- **One primary state change per beat**: initial state, one event, explicit
  end state. Time budgets not frame math ("0-3s... 3-7s..."); never demand
  multiple actions within one second.
- **Impact beats.** Every clip lands at least one physical accent (a stamp,
  a catch, a door closing, a lantern lighting) named in the shot text and
  echoed in the audio line. Reference-grade work lands an accent roughly
  every 3 seconds.
- **What over-specification breaks**: several simultaneous actions = mush;
  frame-accurate timing = ignored; choreographing every limb = anatomy
  errors; long dialogue = lip-sync drift.
- **Staggered entrances**: "A, then B, then C", with landing verbs (snaps
  into place, drops with a bounce, settles). Never "everything at once".
- **End settled, not frozen**: the final shot eases into a stable, still
  micro-moving frame.

## The multi-shot block (for hard-cut sequences in one prompt)

Header first: "A single {N}-second scene of {K} hard-cut shots", then
numbered shots with time ranges and HARD CUT separators:

```
SHOT 1 - 0.0s to 2.0s - {SIZE+ANGLE}: {beat}.
HARD CUT.
SHOT 2 - 2.0s to 4.0s - {DIFFERENT SIZE+ANGLE}: {beat}.
...
{K} hard-cut shots, no dissolves, no fades. Continuous motion within each
shot, never freezes. Motion starts on frame 1 (no opening freeze).
```

Rules that make it work: no shot longer than 2.5s (a frame hanging 3-5s
reads as a slideshow); vary shot SIZE and ANGLE on every cut; only the
first visit to a location opens on an establishing wide; OTS only when a
named character's shoulder is deliberately in foreground (OTS in a
characterless shot makes the model invent a person); max ~2 consecutive
blocks per location. The model under-delivers cuts; if it merges shots,
regenerate once with the cuts spelled out, then accept one fewer cut.

For our single-move story reels (the default), the alternative is ONE
continuous directed take: one confident move, parallax layers, secondary
motion everywhere. Choose per story shape: hard-cut blocks for pace and
information; the continuous take for tenderness and immersion.

## Parallax (the anti-"AI-generated" weapon)

Flat frames read as AI. Build three depth layers into the KEYFRAME first
(foreground occluders, midground subject, textured background), then write
their differential motion into the prompt: "the foreground dandelions
sweep past faster than the hills; the cottages drift slowly; the sky
holds." Camera moves perpendicular to layered depth (lateral track, orbit,
push through foreground) maximize it. A push-in that starts BEHIND
foreground elements and passes them is the cheapest big-production shot
that exists.

## Audio line

Diegetic SFX follows choreography 1:1: every named motion gets at most one
cue, 2-4 cues per clip plus one room-tone bed. Nothing sounds that did not
move. Dialogue goes in double quotes with a delivery note; keep lines
short. If music is unwanted (it usually is; add at edit): "no music, only
soft diegetic sound". An open prompt tends to come back scored like a car
advert.

## Failure modes -> fixes

| Symptom | Fix |
|---|---|
| Unwanted angle cuts | "single continuous shot, no cuts" |
| Camera ignored | use the named vocabulary; endpoint; worst case attach an @motion video reference |
| Identity drift mid-clip | shorter clip, gentler motion, cleaner reference, constraint line |
| Invented furniture/rooms | "the set contains only what the reference shows" |
| Character shrinks to a dot | "stays the human-sized anchor, never a tiny distant figure" |
| Morph/extra limbs | constraint slot: "no warped face, no extra limbs, no melting"; calm the action density |
| Text warps | bigger, centered, restated in words, "text remains sharp" |
| Unwanted score | "no music" explicitly |
| Instant filter rejection | rewrite the scene framing (complete all slots); a complete cinematic scene passes where a "safe"-worded fragment fails |
| Action reversal | unidirectional chains; fill every second with forward motion |

## Iteration economics

Prototype at 720p silent; rerun the identical winning prompt at 1080p+ with
audio for the final. One well-written prompt at 720p beats five vague
prompts at 1080p. Duration sweet spots: under 8s while iterating a single
shot; 10-15s for finals and montages; identity drift compounds with
duration and motion magnitude.
