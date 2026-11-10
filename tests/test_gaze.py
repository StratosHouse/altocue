"""The eyeline against the lens: what counts as off the lens, what is a blink, and what a cover must hold."""

from __future__ import annotations

import pytest

from altocue import gaze, shorts
from altocue.errors import AltocueError

STEP = 1 / 15


def rows(seconds: float, at=lambda t: {}, start: float = 0.0) -> list[dict]:
    """Readings every 1/15 s: on the lens unless `at(t)` says otherwise."""
    out = []
    for i in range(round(seconds / STEP)):
        t = round(start + i * STEP, 3)
        out.append({"t": t, "face": True, "hs": 0.0, "up": 0.1, "down": 0.1, "yaw": 2.5, "pitch": 0.0, **at(t)})
    return out


def test_eyes_that_only_cancel_a_head_turn_are_on_the_lens():
    turned = rows(3.0, lambda t: {"yaw": 10.0, "hs": -0.36})  # 10 degrees round, eyes counter-turned back to the lens
    assert gaze.away_spans(turned)[0] == []


def test_reading_beside_the_lens_is_off_it_even_with_the_head_still():
    reading = rows(4.0, lambda t: {"hs": 0.6} if 1.0 <= t < 1.6 else {})
    spans, _ = gaze.away_spans(reading)
    assert len(spans) == 1 and "eyes off" in spans[0]["reason"]
    assert spans[0]["start"] == pytest.approx(1.0, abs=0.07) and spans[0]["end"] == pytest.approx(1.6, abs=0.07)


def test_a_blink_is_not_a_look_away_but_a_turned_head_is():
    blink = rows(3.0, lambda t: {"down": 0.6} if 1.0 <= t < 1.2 else {})
    spans, blinks = gaze.away_spans(blink)
    assert spans == [] and len(blinks) == 1
    turned = rows(3.0, lambda t: {"yaw": 25.0, "hs": 0.0} if 1.0 <= t < 1.3 else {})
    assert len(gaze.away_spans(turned)[0]) == 1


def test_a_blink_at_the_edge_of_a_look_away_is_held_under_the_cover():
    def at(t):
        if 1.0 <= t < 1.6:
            return {"hs": 0.6}  # reads beside the lens
        if 1.8 <= t < 2.0:
            return {"down": 0.6}  # comes back with the eyes still down
        return {}

    spans, _ = gaze.away_spans(rows(4.0, at))
    assert spans[0]["end"] >= 2.0


def test_no_face_counts_on_a_plate_but_not_on_a_render():
    missing = rows(3.0, lambda t: {"face": False} if 1.0 <= t < 1.5 else {})
    assert len(gaze.away_spans(missing)[0]) == 1
    assert gaze.away_spans(missing, no_face=False)[0] == []


def test_the_lens_can_be_set_for_another_camera():
    beside = rows(3.0, lambda t: {"yaw": 12.0})
    assert gaze.away_spans(beside)[0] and gaze.away_spans(beside, lens=12.0)[0] == []


def test_a_render_with_a_look_away_on_screen_fails_and_one_under_a_graphic_passes(tmp_path, monkeypatch):
    render = tmp_path / "final.mp4"
    render.write_bytes(b"stand-in")
    monkeypatch.setattr(gaze, "scan", lambda video, fps=gaze.SAMPLE_FPS: rows(4.0, lambda t: {"hs": 0.6} if 1.0 <= t < 1.6 else {}))
    shown = '<section data-layout="base" data-start="0" data-duration="4"></section>'
    assert "1.00 to 1.60 s" in shorts.check_gaze(render, shown, gaze.LENS)[0]
    covered = shown + '<section data-layout="hidden" data-start="0.9" data-duration="0.9"></section>'
    assert shorts.check_gaze(render, covered, gaze.LENS) == []


def test_without_mediapipe_the_fix_names_the_install(monkeypatch, tmp_path):
    import builtins

    real = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name.startswith("mediapipe"):
            raise ImportError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    with pytest.raises(AltocueError) as caught:
        gaze.scan(tmp_path / "take.mp4")
    assert "--with mediapipe" in caught.value.fix
