from __future__ import annotations

from altocue.motion.placement import best_top, overlap_area

SIZE = (1080, 1920)


def hook(**over):
    return {"type": "hook", "start": 0, "end": 2, **over}


def test_a_clear_frame_keeps_the_normal_place():
    assert best_top(hook(), 300, [], SIZE, 1250) is None


def test_a_face_under_the_title_moves_it():
    face = [(300.0, 280.0, 400.0, 400.0)]
    top = best_top(hook(), 260, face, SIZE, 1250)
    assert top is not None
    assert overlap_area(top, top + 260, 72, 950, face) == 0


def test_never_lands_on_the_captions():
    face = [(100.0, 200.0, 800.0, 700.0)]
    top = best_top(hook(), 420, face, SIZE, 1250)
    assert top is None or top + 420 <= 1250


def test_overlap_counts_a_margin_round_the_face():
    assert overlap_area(100, 195, 0, 1000, [(100.0, 200.0, 100.0, 100.0)]) > 0
