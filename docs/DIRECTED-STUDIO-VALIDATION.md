# Directed Studio: implementation and validation

Validated on a Linux render host on November 4, 2026. Public examples contain no private footage, paid sound pack, user brand art or third-party account capture.

## What is in the product

The ordinary render command detects a project-local studio.json, validates its local assets and exact output-frame timing, and composites a native transparent stage. Its twelve recipes, prompts and example scenes come from src/altocue/studio/collection.json. Upper-panel and deliberately full-frame scenes share the same editorial palette, easing, fonts and protected heading geometry.

Typing uses the same integer frames for glyphs and trimmed sound attacks. The source-dialogue command writes a new track with a source-sample receipt and rejects attempts to overwrite original audio. Final speech rules count explicit phrases within bounded output time windows. Cover creation also writes centered 3:4, 4:5 and square proofs. Status becomes stale when the manifest or its input files change or disappear.

## Reproduction

~~~sh
altocue studio demo -p directed-demo
altocue studio validate -p directed-demo
altocue studio frame -p directed-demo --frame 68 --out directed-demo/roster.png
altocue studio layer -p directed-demo --out directed-demo/graphics.mov
~~~

The offline example creates its own source art and two synthesized sounds. The twelve-scene output is 1,900 frames at 60 fps, 720 by 1280, with alpha. Install the feature branch before using these commands; the earlier public 0.2.0 package does not include them.

For a real presenter project, use the same local manifest alongside the regular EDL, captions and picture. Follow [Directed Studio](DIRECTED-STUDIO.md) for actual source assets, voice repair, frame-relative cues and regular rendering.

## Evidence

- Installed a built wheel in an isolated directory, outside the source checkout. The installed package generated and validated the complete offline demo and encoded its 1,900-frame alpha stage.
- Rendered every catalog recipe into a playable public-safe preview from the package with original generated geometry. Checked both narrow-phone and desktop layouts, all twelve videos, actual clipboard prompt contents, scene JSON and overflow on the private and public-safe library variants: 48 combinations.
- Tested a regular project through render.build, including the Studio overlay and generated keys. The decoded final picture is compared to an independently rendered native frame, allowing for codec color conversion.
- Compared seven brand-ending frames against the retained production compositor with the same private input art. All differences are confined to the house label rectangle (94,225)-(237,240) in the 720 by 1280 space, where the portable version fits long names. Whole-image mean absolute RGB difference: 0.1741 out of 255. This comparison applies to that scene, not every private film.
- The final-phrase checker passes the repaired production file and rejects the earlier file missing the qualifier. The CTA word occurs once in its final bounded window.
- The affected renderer/status/README/delivery regression run passed 145 tests. After the final original-audio safeguard, all 9 native recipe/cue/source/example/status cases passed again, and all 332 operator-skill checks passed. See the operator handoff for exact commit and run receipts.
- Ruff and the repository publication check pass. Native code adds no required dependency, hosted model, account or paid asset.

## Deliberate limits

A rendered recipe is not an automatic directing decision. The editor still selects source takes, matches moments to spoken words, supplies a clean presenter plate, and checks the final composition and sound.

Logo-to-page docking uses registered transparent layers exported from genuine artwork and a current page capture. It does not recreate arbitrary live WebGL lighting from a flattened logo. The diagram tour uses a real tall capture; a live web interaction needs an actual screen capture. Character motion operates on supplied art. The original metal sculpture generates its own geometry and follows supplied PCM.

Phone masks are conservative design allowances, not proof of every app/account UI. Actual composer and phone-speaker review remain release checks. Master and review-proxy hashes differ; compare the right artifact. Automated frame/audio checks are not creative approval.

## Continuing the product

For every reusable improvement, update the native module, its catalog prompt and JSON, a generated-input test, and this workflow. Validate through the product CLI on the render host, compare to the accepted scene or cue, refresh the library previews and record the commit in the handoff. Keep creative exceptions explicit and private inputs out of public source.

A separately developed licensed sound-pack registry and general per-character cue extension lives on claude/sound-packs. Coordinate that feature's merge and sound regression checks with its owner; this change does not overwrite that branch. A branch push is not a PyPI release or a website deployment.
