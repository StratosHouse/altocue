from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from altocue import direct
from altocue.cli import main
from altocue.direct import check_clip, full_prompt, lint_prompt, load_shots, require_valid, validate
from altocue.errors import AltocueError
from altocue.models import Word
from altocue.project import Project

GOOD_PROMPT = (
    "She lifts the lantern and walks to the door, the coat swinging with each stride. "
    "Slow dolly in from medium to close-up, ending on her hands. Dust stirs in the beam."
)


def shot(**changes) -> dict:
    base = {
        "id": "s01",
        "purpose": "Show the lantern being lit",
        "kind": "video",
        "tier": "draft",
        "prompt": GOOD_PROMPT,
        "aspect": "9:16",
        "duration": 5,
        "placement": 3.5,
        "check": "motion",
    }
    return {**base, **changes}


def rules(review, level: str | None = None) -> set[str]:
    return {f.rule for f in review.findings if level is None or f.level == level}


def lint(**changes) -> set[str]:
    return {f.rule for f in lint_prompt(direct.Shot.from_dict(shot(**changes)))}


# Schema


def test_clean_shot_list_has_no_findings():
    review = validate({"shots": [shot()]})
    assert review.ok
    assert review.findings == []
    assert review.shots[0].id == "s01"
    assert review.shots[0].refs == []


def test_empty_list_is_an_error():
    assert not validate({"shots": []}).ok


@pytest.mark.parametrize("field", ["id", "purpose", "kind", "tier", "prompt", "aspect", "duration", "placement", "check"])
def test_every_required_field_is_enforced(field):
    data = shot()
    del data[field]
    review = validate({"shots": [data]})
    assert not review.ok
    assert any(field in f.message for f in review.errors)


@pytest.mark.parametrize(
    ("change", "rule"),
    [
        ({"kind": "audio"}, "kind"),
        ({"tier": "ultra"}, "tier"),
        ({"aspect": "5:3"}, "aspect"),
        ({"duration": 0}, "duration"),
        ({"duration": "five"}, "duration"),
        ({"check": "vibes"}, "check"),
        ({"placement": -1}, "placement"),
        ({"placement": {"occurrence": 2}}, "placement"),
        ({"placement": {"word": "bloom", "second": 2}}, "placement"),
        ({"refs": "hero.png"}, "refs"),
    ],
)
def test_bad_values_are_errors(change, rule):
    review = validate({"shots": [shot(**change)]})
    assert rule in rules(review, "error")


def test_placement_takes_a_second_or_a_word_anchor():
    assert validate({"shots": [shot(placement=2.5)]}).ok
    assert validate({"shots": [shot(placement={"word": "bloom", "occurrence": 2, "offset": 0.2})]}).ok


def test_duplicate_ids_are_an_error():
    assert "duplicate-id" in rules(validate({"shots": [shot(), shot()]}), "error")


def test_a_shot_may_not_name_a_model():
    review = validate({"shots": [shot(model="some-vendor/some-model")]})
    assert "no-model" in rules(review, "error")


def test_unknown_field_is_a_warning_not_a_silent_drop():
    review = validate({"shots": [shot(fisrt_frame="a.png")]})
    assert review.ok
    assert "unknown-field" in rules(review, "warning")


def test_image_rules():
    still = shot(kind="image", check="still", prompt="A brass lantern on a wet stone step, warm practical light.")
    assert validate({"shots": [still]}).ok
    assert "check" in rules(validate({"shots": [{**still, "check": "motion"}]}), "error")
    assert "first-frame" in rules(validate({"shots": [{**still, "first_frame": "a.png"}]}), "error")


def test_last_frame_needs_a_first_frame():
    assert "last-frame" in rules(validate({"shots": [shot(last_frame="end.png")]}), "error")


def test_first_frame_must_be_a_still():
    assert "first-frame" in rules(validate({"shots": [shot(first_frame="clip.mp4")]}), "error")


def test_shot_references_must_be_earlier_stills():
    still = shot(id="key", kind="image", check="still", prompt="A brass lantern on a wet stone step.")
    moving = shot(id="s02", first_frame="@key")
    assert validate({"shots": [still, moving]}).ok
    assert "shot-ref" in rules(validate({"shots": [moving, still]}), "error")
    assert "shot-ref" in rules(validate({"shots": [shot(first_frame="@ghost")]}), "error")
    assert "shot-ref" in rules(validate({"shots": [shot(id="v1"), shot(id="v2", first_frame="@v1")]}), "error")


def test_a_shot_cannot_reach_a_file_outside_the_project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "a.png").write_bytes(b"x")
    (tmp_path / "secret.png").write_bytes(b"x")
    assert "outside-project" in rules(validate({"shots": [shot(refs=["../secret.png"], first_frame="a.png")]}, root=root), "error")


def test_only_images_can_be_uploaded_as_references(tmp_path):
    (tmp_path / "a.png").write_bytes(b"x")
    (tmp_path / "keys.txt").write_bytes(b"x")
    assert "not-an-image" in rules(validate({"shots": [shot(refs=["keys.txt"], first_frame="a.png")]}, root=tmp_path), "error")


def test_missing_files_are_checked_against_the_project(tmp_path):
    (tmp_path / "a.png").write_bytes(b"x")
    assert validate({"shots": [shot(first_frame="a.png")]}, root=tmp_path).ok
    assert "missing-file" in rules(validate({"shots": [shot(first_frame="b.png")]}, root=tmp_path), "error")
    assert "missing-file" in rules(validate({"shots": [shot(refs=["c.png"], first_frame="a.png")]}, root=tmp_path), "error")


def test_style_is_joined_byte_identical_onto_every_prompt():
    review = validate({"style": "Felt and wool. Warm paper light.", "shots": [shot()]})
    assert full_prompt(review.shots[0], review.style) == f"{GOOD_PROMPT}\n\nFelt and wool. Warm paper light."
    assert full_prompt(review.shots[0], "") == GOOD_PROMPT


def test_load_shots_explains_bad_json_and_missing_file(tmp_path):
    with pytest.raises(AltocueError) as missing:
        load_shots(tmp_path / "shots.json")
    assert missing.value.fix
    (tmp_path / "shots.json").write_text("{nope")
    with pytest.raises(AltocueError) as bad:
        load_shots(tmp_path / "shots.json")
    assert bad.value.fix


def test_require_valid_lists_every_error():
    review = validate({"shots": [shot(kind="audio", tier="ultra")]})
    with pytest.raises(AltocueError) as caught:
        require_valid(review)
    assert "kind" in str(caught.value) and "tier" in str(caught.value)


# Craft rules


def test_two_camera_moves_warn():
    assert "one-move" in lint(prompt="Slow orbit around her while the camera pans left and pushes in, ending on her hands.")


def test_sequential_moves_with_timing_are_fine():
    prompt = "The camera pans left to follow her to the door, then pushes in when she stops, ending on the handle."
    assert "one-move" not in lint(prompt=prompt)


def test_a_head_tilt_is_not_a_camera_move():
    prompt = "She tilts her head and laughs while a slow push in ends on her eyes as the light settles."
    assert "one-move" not in lint(prompt=prompt)


def test_no_named_move_warns():
    assert "no-move" in lint(prompt="She walks to the door and opens it while the rain falls on the step behind her.")


def test_move_without_endpoint_warns():
    assert "endpoint" in lint(prompt="Slow dolly in as she walks along the wet street past the shop windows at dusk.")


def test_stated_endpoints_pass():
    for ending in ("ending on her hands", "settling at the doorway", "until the cottage fills the frame"):
        assert "endpoint" not in lint(prompt=f"Slow dolly in as she crosses the wet street at dusk, {ending}.")


def test_locked_off_camera_needs_no_endpoint():
    assert "endpoint" not in lint(prompt="Locked-off static frame as she crosses the wet street at dusk and rain falls.")


def test_prompt_that_redescribes_the_start_frame_warns():
    prompt = f"A young woman with long dark hair wearing a red coat. {GOOD_PROMPT}"
    assert "reference-conflict" in lint(first_frame="key.png", prompt=prompt)
    assert "reference-conflict" not in lint(prompt=prompt)


def test_style_words_arguing_with_references_warn():
    assert "reference-conflict" in lint(refs=["hero.png"], prompt=f"{GOOD_PROMPT} Photorealistic skin.")


def test_secondary_motion_of_hair_is_not_redescribing():
    prompt = f"{GOOD_PROMPT} Her hair lifts in the draught from the door."
    assert "reference-conflict" not in lint(first_frame="key.png", prompt=prompt)


def test_empty_style_words_warn():
    assert "empty-words" in lint(prompt=f"{GOOD_PROMPT} Cinematic and stunning.")


def test_emotion_named_instead_of_shown_warns():
    assert "emotion-as-physics" in lint(prompt=f"{GOOD_PROMPT} She looks happy.")


def test_wide_shot_of_a_character_warns():
    assert "wide-character" in lint(characters=["hero"], prompt=f"Wide shot of the valley. {GOOD_PROMPT}")


def test_over_the_shoulder_without_a_character_warns():
    assert "ots-no-character" in lint(prompt=f"Over-the-shoulder view of the valley. {GOOD_PROMPT}")


def test_handheld_needs_to_be_aggressive():
    assert "handheld" in lint(prompt="Handheld follow as she runs down the alley, ending on the gate at the far end.")
    assert "handheld" not in lint(prompt="Handheld follow, completely unstabilized, as she runs, ending on the gate at the far end.")


def test_audio_without_no_music_warns():
    assert "no-music" in lint(audio=True)
    assert "no-music" not in lint(audio=True, prompt=f"{GOOD_PROMPT} No music, only soft diegetic sound.")


def test_too_many_references_warn():
    assert "too-many-refs" in lint(refs=[f"{n}.png" for n in range(8)])


def test_unreliable_subject_warns():
    assert "unreliable-subject" in lint(prompt=f"{GOOD_PROMPT} An analog clock on the wall.")


def test_long_prompt_warns_short_block_exempt():
    assert "prompt-length" in lint(prompt=" ".join(["word"] * 130))
    assert "prompt-length" in lint(prompt="She walks in.")


def test_multi_shot_block_is_checked_as_a_block():
    good = "SHOT 1 - 0.0s to 2.0s - WIDE: she enters.\nHARD CUT.\nSHOT 2 - 2.0s to 4.0s - CLOSE: she turns.\n" * 12
    found = lint(prompt=good)
    assert not found & {"one-move", "no-move", "endpoint", "prompt-length"}
    assert "block-length" in lint(prompt="SHOT 1 - 0.0s to 4.0s - WIDE: she enters.\nHARD CUT.\nSHOT 2 - 4.0s to 5.0s - CLOSE: she turns.")


def test_image_prompts_skip_camera_rules():
    found = lint(kind="image", check="still", prompt="A brass lantern on a wet stone step, warm practical light from the left.")
    assert not found & {"no-move", "endpoint", "one-move"}


def test_recurring_character_video_needs_a_still_first():
    one = shot(id="a", characters=["hero"], first_frame="key.png")
    two = shot(id="b", characters=["hero"])
    review = validate({"shots": [one, two]})
    assert "still-first" in rules(review, "error")
    assert any(f.shot == "b" for f in review.errors)


def test_one_off_character_is_only_a_warning():
    review = validate({"shots": [shot(characters=["passer-by"])]})
    assert review.ok
    assert "still-first" in rules(review, "warning")


def test_character_without_any_reference_warns():
    still = shot(kind="image", check="still", characters=["hero"], prompt="Her face in soft window light, a calm half smile.")
    assert "identity-ref" in rules(validate({"shots": [still]}), "warning")
    assert "identity-ref" not in rules(validate({"shots": [{**still, "refs": ["hero.png"]}]}), "warning")
    assert "identity-ref" not in rules(validate({"shots": [shot(characters=["hero"], first_frame="key.png")]}), "warning")


def test_long_character_clip_warns():
    assert "drift" in lint(characters=["hero"], first_frame="k.png", refs=["hero.png"], duration=14)


# check_clip


def moving(path: Path, seconds: float = 2.0) -> Path:
    """Film grain changes every frame, so it clears the motion gate on purpose."""
    source = f"color=c=gray:size=320x568:rate=30:duration={seconds},noise=alls=40:allf=t"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", source, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True
    )
    return path


def frozen(path: Path, seconds: float = 2.0) -> Path:
    args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=gray:size=320x568:rate=30:duration={seconds}"]
    subprocess.run([*args, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def test_check_clip_passes_moving_footage_and_extracts_three_frames(tmp_path):
    result = check_clip(moving(tmp_path / "moving.mp4"), out_dir=tmp_path / "frames")
    assert result.passed
    assert result.subject_mean >= 4 and result.background_mean >= 2
    assert set(result.frames) == {"start", "mid", "end"}
    assert all(Path(p).stat().st_size > 0 for p in result.frames.values())
    assert len(result.per_second) == 2


def test_check_clip_fails_frozen_footage(tmp_path):
    result = check_clip(frozen(tmp_path / "still.mp4"))
    assert not result.passed
    assert result.subject_mean < 1
    assert "frozen" in result.detail


def test_check_clip_thresholds_are_settable(tmp_path):
    result = check_clip(frozen(tmp_path / "still.mp4"), min_subject=0, min_background=0)
    assert result.passed


def test_check_clip_reports_as_a_gate(tmp_path):
    gate = check_clip(frozen(tmp_path / "still.mp4")).gate()
    assert gate.id == "clip-motion"
    assert gate.passed is False
    assert gate.threshold == {"subject": 4.0, "background": 2.0}


def test_check_clip_missing_file(tmp_path):
    with pytest.raises(AltocueError):
        check_clip(tmp_path / "nothing.mp4")


def test_check_clip_on_a_single_frame_is_not_a_crash(tmp_path):
    one = tmp_path / "one.mp4"
    args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:size=320x568:rate=30:duration=0.034"]
    subprocess.run([*args, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(one)], check=True)
    result = check_clip(one)
    assert not result.passed


def test_a_file_with_no_picture_fails_cleanly(tmp_path):
    sound = tmp_path / "sound.mp4"
    source = "sine=frequency=220:sample_rate=48000:duration=1"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", source, "-c:a", "aac", str(sound)], check=True)
    with pytest.raises(AltocueError):
        check_clip(sound)
    assert direct.run_check("motion", sound, tmp_path / "out")["passed"] is False


def test_run_check_routes_by_shot_check(tmp_path):
    clip = moving(tmp_path / "moving.mp4")
    assert direct.run_check("motion", clip, tmp_path / "a")["passed"] is True
    frames = direct.run_check("frames", clip, tmp_path / "b")
    assert frames["passed"] is None and len(frames["frames"]) == 3
    assert direct.run_check("none", clip, tmp_path / "c")["passed"] is None
    from PIL import Image

    still = tmp_path / "still.png"
    Image.new("RGB", (32, 32), "white").save(still)
    assert direct.run_check("still", still, tmp_path / "d")["passed"] is True
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not an image")
    assert direct.run_check("still", broken, tmp_path / "e")["passed"] is False


# The command


@pytest.fixture
def project(tmp_path: Path) -> Project:
    made = Project.create(tmp_path / "video")
    text = (made.root / "altocue.toml").read_text()
    (made.root / "altocue.toml").write_text(text.replace('provider = ""', 'provider = "fal"'))
    return Project.open(made.root)


def write_shots(project: Project, *shots: dict, **top) -> None:
    (project.root / "shots.json").write_text(json.dumps({"shots": list(shots), **top}))


def test_direct_prints_cost_and_warnings(project, capsys):
    write_shots(project, shot(), shot(id="s02", prompt="She runs to the gate and shakes the bars until the lock gives way."))
    assert main(["direct", "--project", str(project.root)]) == 0
    out = capsys.readouterr().out
    assert "$1.55" in out
    assert "no-move" in out
    assert "2 shots" in out


def test_direct_exits_nonzero_on_errors(project, capsys):
    write_shots(project, shot(kind="audio"))
    assert main(["direct", "--project", str(project.root)]) == 1
    assert "kind" in capsys.readouterr().out


def test_direct_without_a_provider_still_validates(tmp_path, capsys):
    bare = Project.create(tmp_path / "bare")
    write_shots(bare, shot())
    assert main(["direct", "--project", str(bare.root)]) == 0
    assert "provider" in capsys.readouterr().out


def test_direct_reports_missing_capability_as_an_error(project, capsys):
    (project.root / "a.png").write_bytes(b"x")
    (project.root / "b.png").write_bytes(b"x")
    write_shots(project, shot(first_frame="a.png", refs=["b.png"]))
    assert main(["direct", "--project", str(project.root)]) == 1
    assert "higgsfield" in capsys.readouterr().err


def test_direct_flags_anchor_words_that_were_not_said(project, capsys):
    project.save_words("take", [Word(text="hello", start=0, end=1)], "test")
    write_shots(project, shot(placement={"word": "bloom"}), shot(id="s02", placement={"word": "Hello,"}))
    assert main(["direct", "--project", str(project.root)]) == 0
    out = capsys.readouterr().out
    assert "bloom" in out
    assert "Hello" not in out


def test_direct_says_when_anchor_words_could_not_be_checked(project, capsys):
    write_shots(project, shot(placement={"word": "bloom"}))
    assert main(["direct", "--project", str(project.root)]) == 0
    assert "not checked" in capsys.readouterr().out


def test_direct_check_flag_measures_a_clip(project, capsys, tmp_path):
    clip = moving(tmp_path / "moving.mp4")
    assert main(["direct", "--project", str(project.root), "--check", str(clip)]) == 0
    assert "subject" in capsys.readouterr().out
    assert (project.work / "checks" / "moving" / "mid.png").exists()
    still = frozen(tmp_path / "still.mp4")
    assert main(["direct", "--project", str(project.root), "--check", str(still)]) == 1


def test_direct_json_output_is_machine_readable(project, capsys):
    write_shots(project, shot())
    assert main(["direct", "--project", str(project.root), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert report["cost"]["usd"] == pytest.approx(0.7735)
