# Directed presenter reels

A cut can pass a decoder and still repeat a word, hide its title behind a profile, or leave keyboard sounds playing after an edit. Use a local Studio plan when a presenter needs to demonstrate software, a comparison or a workflow. This is an opt-in addition to the ordinary renderer; a bare input clip does not contain the product logos, repository capture or your editorial decisions.

1. **Cut for meaning first.** Select complete source words with consonant handles. When an ASR transcript collapses a repeated noun, audition the isolated CTA and use one positively identified instance. For a missing qualifier, recover the speaker's original take; keep picture, captions and sound on the same revised timeline. Never invent a pickup. Preserve the originals.
2. **Gather real inputs.** Add your mark, genuine tool marks and approved sound to the project. For a repo tour supply a tall local image captured from the real README, the diagram, and its title/source. For a logo-to-site match cut, capture the actual page and provide transparent components from the real model. The logo-dock recipe animates those components into the measured target. True live WebGL lighting or a site-specific camera still needs the original page/model capture. The voice-sculpture recipe generates its own moving geometry from a local voice track. Prepare the presenter plate, matte and digital backdrop when the shot requires it.
3. **Author the proof.** Run `altocue studio catalog` for twelve reusable scenes, their copyable prompts and actual JSON. `altocue studio example > studio.json` provides a starter; replace example local assets, times and text. Times are integer output frames, starts inclusive, ends exclusive. Logo focus times and character keys are relative to their scene. Gaps leave the normal picture unchanged. The project and manifest must have the same fps and a 9:16 canvas. Animate only what explains the line.
4. **Render normally.** `altocue studio validate -p PROJECT` checks assets and timing. `altocue render -p PROJECT` composites studio.json on the ordinary presenter/graphics/caption timeline. The upper compositions leave the lower picture transparent, and whole-screen demonstrations deliberately cover it. Other motion, explicitly requested effects, grading and mastering retain their normal paths. For a standalone alpha stage use `altocue studio layer -p PROJECT --out stage.mov`; use `altocue studio frame -p PROJECT --frame 72 --out frame.png` for one image.
5. **Sound and crops.** A typing scene takes one local tap and an ordered frame per character. Altocue trims every attack to the minimum of 41 ms, the next key or the scene end, with a tail fade. Omit sound for silence. Disable other automatic sounds in motion.json if this edit calls for voice and these effects only. The title and paired house/product label sit inside the upper proof area, below the house profile-mask allowance, instead of in the top corners. Check right-side controls, lower caption tray and a real phone: overlays vary. `altocue studio cover -p PROJECT --portrait face.jpg --scene 0 --out cover.jpg` accepts a deliberately selected portrait crop and also produces 3:4, 4:5 and square previews.
6. **Verify the delivered file.** Decode the final audio and video; check every frame for corrupt/black frames or isolated flashes. Compare the delivered AAC and the effect stems at the actual visible events. Re-transcribe the finished master, configure bounded `speech_checks` in studio.json, and run `altocue studio check-speech -p PROJECT --words final-words.json`. The expected word file is `{"words":[{"text":"word","start":1.2}]}` in final-output seconds. A frame decoder and a speech recognizer cannot approve directing or phone-speaker balance. Recheck whenever the master hash changes. Keep separate SHA-256 for the master and each lower-resolution review copy.

## From a clean install

Run these from any directory with the new Altocue version and FFmpeg installed. The new directory gets original geometric artwork and two generated sounds. Nothing is fetched. This is a standalone graphics demonstration, not a simulated presenter reel.

```sh
altocue studio demo -p directed-demo
altocue studio validate -p directed-demo
altocue studio frame -p directed-demo --frame 68 --out directed-demo/roster.png
altocue studio layer -p directed-demo --out directed-demo/graphics.mov
```

On an actual 9:16 project, copy or author studio.json and its local assets, fit the scene ranges to the cut, then use the regular `altocue render -p PROJECT`.

## Replace spoken words from true source audio

`altocue studio dialogue -p PROJECT --out replacement.wav` applies an explicit studio-dialogue.json to a conformed base voice track. All named files are local. Source positions are 48 kHz sample offsets after decoding; output positions are integer frames. Each piece must fit an ordered replacement range and may not overlap the next one. Example: `{"fps":60,"frames":180,"base":"voice.wav","replace":[[60,120]],"pieces":[{"source":"original.wav","in_sample":96000,"out_sample":120000,"at_frame":65,"fade_samples":432}]}`. Keep room tone as a sourced piece when needed. The command writes a new WAV and a source receipt; it does not silently retime video or change the EDL. For added dialogue, edit the picture and caption timing too.

## Feature parity is a release requirement

A reusable improvement is complete only when its implementation, a local-input example, copyable creation guidance, and a render-PC witness all exist in the product branch. Compare the *rendered* result with an accepted reference at the relevant frame or cue; document differences. User footage, third-party marks, sound packs, raw account captures and brand-specific copy remain project inputs. Do not add them to the public repository. Record the native CLI command, commit, source/asset requirements, QA evidence, any creative-only exception, and whether the change is released or only on a branch. Hand that receipt to the next operator.
