"""`altocue doctor`: the checks, the disk warning and what this machine can run."""

from __future__ import annotations

import json
import re
from collections import namedtuple
from pathlib import Path

import pytest

from altocue import doctor, transcribe
from altocue.cli import main

Usage = namedtuple("Usage", "total used free")
GB = 1024**3
COMMAND = re.compile(r"^(altocue|brew|sudo|export|uv|npm|curl) \S")


def test_plenty_of_disk_is_fine(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "disk_usage", lambda path: Usage(100 * GB, 80 * GB, 20 * GB))
    check = doctor.check_disk()
    assert check.ok and "20" in check.detail and not check.fix


def test_under_five_gb_warns_and_says_what_needs_the_room(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "disk_usage", lambda path: Usage(100 * GB, 96 * GB, int(4.5 * GB)))
    check = doctor.check_disk()
    assert not check.ok and not check.required
    assert "4.5" in check.detail and "2.5 GB" in check.detail and check.fix


def test_exactly_five_gb_is_not_a_warning(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "disk_usage", lambda path: Usage(100 * GB, 95 * GB, 5 * GB))
    assert doctor.check_disk().ok


def test_a_low_disk_warning_does_not_stop_doctor(monkeypatch, capsys):
    monkeypatch.setattr(doctor.shutil, "disk_usage", lambda path: Usage(100 * GB, 99 * GB, GB))
    assert main(["doctor"]) == 0
    assert "disk" in capsys.readouterr().out


def stub_machine(monkeypatch, *, ok: bool) -> None:
    fixes = {
        "ffmpeg": doctor.ffmpeg.INSTALL_FIX,
        "node": "Install Node 22 or later for full motion graphics.",
        "renderer": "Run `altocue setup`.",
        "denoiser": "Run `altocue setup` for better noise removal.",
        "speakers": "Run `altocue setup --speakers`.",
        "ocr": "Install tesseract.",
        "transcriber": "Reinstall.",
    }
    detail = {"transcriber": "parakeet"}
    results = [doctor.Check(name, ok, name in ("ffmpeg", "transcriber"), detail.get(name, "detail"), "" if ok else fix) for name, fix in fixes.items()]
    monkeypatch.setattr(doctor, "core_checks", lambda: results)
    monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/bin/{name}" if ok else None)
    monkeypatch.setattr(doctor, "face_model", lambda: ok)
    for name in ("FAL_KEY", "PEXELS_API_KEY", "PIXABAY_API_KEY", "GEMINI_API_KEY", "ELEVENLABS_API_KEY"):
        if ok:
            monkeypatch.setenv(name, "x")
        else:
            monkeypatch.delenv(name, raising=False)


def test_every_gap_comes_with_one_command(monkeypatch):
    stub_machine(monkeypatch, ok=False)
    rows = doctor.capabilities()
    gaps = [row for row in rows if not row.ok]
    assert len(gaps) >= 8
    for row in gaps:
        assert COMMAND.match(row.fix), (row.feature, row.fix)
        assert ". " not in row.fix and "`" not in row.fix, row.fix


def test_a_full_machine_has_no_gaps_and_no_fixes(monkeypatch):
    stub_machine(monkeypatch, ok=True)
    rows = doctor.capabilities()
    assert all(row.ok and not row.fix for row in rows), [r for r in rows if not r.ok]


def test_the_free_paths_never_depend_on_a_key(monkeypatch):
    stub_machine(monkeypatch, ok=False)
    free = {row.feature: row for row in doctor.capabilities()}
    assert free["Review pack for a fresh agent"].ok and free["Review page"].ok and free["Captions and cards, basic renderer"].ok


def test_each_feature_is_named_once(monkeypatch):
    stub_machine(monkeypatch, ok=True)
    names = [row.feature for row in doctor.capabilities()]
    assert len(names) == len(set(names))


def test_a_mac_and_linux_get_their_own_install_command(monkeypatch):
    monkeypatch.setattr(doctor.platform, "system", lambda: "Darwin")
    assert doctor.install("tesseract", "tesseract-ocr") == "brew install tesseract"
    assert doctor.install_node() == "brew install node"
    monkeypatch.setattr(doctor.platform, "system", lambda: "Linux")
    assert doctor.install("tesseract", "tesseract-ocr") == "sudo apt install tesseract-ocr"
    assert "apt" not in doctor.install_node(), "apt's Node is too old for the renderer"


def test_the_printout_carries_the_matrix(monkeypatch, capsys):
    stub_machine(monkeypatch, ok=False)
    monkeypatch.setattr(doctor, "check_disk", lambda: doctor.Check("disk", True, False, "20 GB free"))
    with pytest.raises(doctor.AltocueError):
        main_args = doctor.argparse.Namespace(json=False)
        doctor.run(main_args)
    out = capsys.readouterr().out
    assert "What this machine can run" in out
    assert re.search(r"^no\s+Editor in Chief through Gemini", out, re.MULTILINE)
    assert "fix: export GEMINI_API_KEY=" in out


def test_json_carries_the_matrix(monkeypatch, capsys):
    stub_machine(monkeypatch, ok=True)
    monkeypatch.setattr(doctor, "check_disk", lambda: doctor.Check("disk", True, False, "20 GB free"))
    assert doctor.run(doctor.argparse.Namespace(json=True)) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert {"feature", "ok", "how", "fix"} <= set(data["capabilities"][0])
    assert any(check["id"] == "disk" for check in data["checks"])


def test_a_missing_engine_gets_a_fix_that_works_for_this_install():
    """`pip install` reaches neither a uv tool nor anything on PyPI, because Altocue is not published there."""
    fix = transcribe.ENGINES["parakeet"].install_fix()
    assert 'uv tool install --force "altocue[parakeet] @ git+https://github.com/StratosHouse/altocue"' in fix
    assert "pip install" not in fix


def test_doctor_gives_the_same_fix_when_nothing_is_installed(monkeypatch):
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: None)
    assert "uv tool install" in doctor.check_transcriber().fix


def test_the_docs_do_not_print_the_old_fix():
    assert 'pip install "altocue[' not in Path("docs/TRANSCRIPTION.md").read_text()


def test_ocr_is_judged_by_a_reader_that_really_works(monkeypatch):
    monkeypatch.setattr(doctor, "ocr_reader", lambda: (None, "no reader"))
    check = doctor.check_ocr()
    assert not check.ok and "tesseract" in check.fix
    monkeypatch.setattr(doctor, "ocr_reader", lambda: ("vision", ""))
    assert doctor.check_ocr().ok


def test_a_cached_model_is_told_apart_from_a_download(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    engine = transcribe.ENGINES["parakeet"]
    assert doctor.model_cached(engine) is False
    (tmp_path / "models--mlx-community--parakeet-tdt-0.6b-v3").mkdir()
    assert doctor.model_cached(engine) is True
