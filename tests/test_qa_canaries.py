"""Every planted fault must fire its gate, and clean controls must fire nothing."""

from __future__ import annotations

import shutil

import pytest
from canaries import CANARIES, CONTROLS, build, fired, planted, score

from altocue.qa import gates, ocr, run_gates

OCR_GATES = {"face_clear", "safe_zone", "rules_ocr"}


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    results = {}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(gates, "FACE_SAMPLES", 3)  # the face model is the slow part of a run
        for index, case in enumerate([*CANARIES, *CONTROLS]):
            built = build(case, tmp_path_factory.mktemp(f"case{index}"))
            with planted(built):
                results[case.name] = run_gates(built.project, built.file)
    return results


@pytest.mark.parametrize("case", CANARIES, ids=lambda c: c.name)
def test_gate_catches_planted_fault(case, runs):
    assert case.gate in fired(runs[case.name]), [(g.id, g.passed, g.skipped, g.value, g.detail) for g in runs[case.name]]


@pytest.mark.parametrize("case", CANARIES, ids=lambda c: c.name)
def test_fault_fires_nothing_unrelated(case, runs):
    assert fired(runs[case.name]) <= {case.gate, *case.also}, [
        (g.id, g.value, g.detail) for g in runs[case.name] if not g.passed and not g.skipped
    ]


@pytest.mark.parametrize("case", CONTROLS, ids=lambda c: c.name)
def test_clean_control_fires_nothing(case, runs):
    assert not fired(runs[case.name]), [(g.id, g.value, g.detail) for g in runs[case.name] if not g.passed and not g.skipped]


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract is not installed")
def test_tesseract_route_catches_what_vision_catches(tmp_path, monkeypatch, capsys):
    """Linux has no Vision, so the same text canaries must fire through tesseract alone."""
    monkeypatch.setattr(ocr, "vision_binary", lambda: None)
    monkeypatch.setattr(gates, "FACE_SAMPLES", 3)
    chosen = [c for c in [*CANARIES, *CONTROLS] if c.gate in OCR_GATES or c.recipe.rules or c.name == "clean control"]
    results = {}
    for index, case in enumerate(chosen):
        built = build(case, tmp_path / str(index))
        with planted(built):
            results[case.name] = run_gates(built.project, built.file)
    result = score(results)
    with capsys.disabled():
        print(f"\ntesseract route: {result['caught']}/{result['planted']} caught; {len(result['false_alarms'])} false alarms")
    assert result["recall"] == 1.0, result["missed"]
    assert not result["false_alarms"], result["false_alarms"]


def test_recall_and_false_alarms(runs, capsys):
    result = score(runs)
    with capsys.disabled():
        print(
            f"\ncanaries: {result['caught']}/{result['planted']} caught, recall {result['recall']:.0%}; "
            f"{len(result['false_alarms'])} false alarms on {result['controls']} controls"
        )
    assert result["recall"] == 1.0, result["missed"]
    assert not result["false_alarms"]
