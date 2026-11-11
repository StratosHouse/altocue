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
    for name in keys_the_code_reads():
        if ok:
            monkeypatch.setenv(name, "x")
        else:
            monkeypatch.delenv(name, raising=False)


def keys_the_code_reads() -> set[str]:
    # Altocue's own modules only: the renderer's node_modules ships third-party scripts once setup has run.
    source = Path(doctor.__file__).parent
    paths = (path for path in source.rglob("*.py") if "node_modules" not in path.parts)
    return {name for path in paths for name in re.findall(r'"([A-Z][A-Z0-9_]*_(?:API_KEY|KEY|TOKEN))"', path.read_text())}


def test_every_key_the_code_reads_has_a_row_in_the_table(monkeypatch):
    """Freesound, 1of10, Spyglass and YouTube keys were read by the code and missing from the table."""
    stub_machine(monkeypatch, ok=False)
    rows = {row.how.split()[0]: row for row in doctor.capabilities()}
    wanted = keys_the_code_reads()
    assert {"FREESOUND_API_KEY", "ONEOF10_API_KEY", "SPYGLASS_API_KEY", "YOUTUBE_API_KEY"} <= wanted
    missing = sorted(name for name in wanted if name not in rows)
    assert not missing, f"Keys with no row in the doctor table: {missing}"
    assert all(rows[name].fix == f"export {name}=your-key" for name in wanted)
    checked = {check.detail.split()[0] for check in doctor.check_keys()}
    assert wanted <= checked


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
        main_args = doctor.argparse.Namespace(json=False, all=True)
        doctor.run(main_args)
    out = capsys.readouterr().out
    assert "What this machine can run" in out
    assert re.search(r"^no\s+Editor in Chief through Gemini", out, re.MULTILINE)
    assert "fix: export GEMINI_API_KEY=" in out


def working_machine(monkeypatch, *, missing: tuple[str, ...] = (), keys: tuple[str, ...] = ()) -> None:
    """A machine that can edit, with the named optional parts missing and only the named keys set."""
    stub_machine(monkeypatch, ok=True)
    results = [doctor.Check(c.id, c.id not in missing, c.required, c.detail, "Do this." if c.id in missing else "") for c in doctor.core_checks()]
    monkeypatch.setattr(doctor, "core_checks", lambda: results)
    monkeypatch.setattr(doctor, "check_disk", lambda: doctor.Check("disk", True, False, "20 GB free"))
    monkeypatch.setattr(doctor, "check_memory", lambda: doctor.Check("memory", True, False, "16 GB"))
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    for name in keys_the_code_reads():
        monkeypatch.delenv(name, raising=False)
    for name in keys:
        monkeypatch.setenv(name, "x")


def test_doctor_leads_with_the_answer(monkeypatch, capsys):
    """It printed 60 lines with no verdict, 15 of them "no" rows for paid services nobody needs to edit."""
    from altocue import __version__

    working_machine(monkeypatch, missing=("speakers",), keys=("PEXELS_API_KEY",))
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    first = out.splitlines()[0]
    assert first.startswith(f"altocue {__version__}. Ready: this machine can transcribe, cut, clean, grade, frame, caption, render and master, and run QA.")
    assert "Optional and not set up, with a fix below: speakers." in first
    assert "10 hosted services have no key or tool, which is fine: `altocue doctor --all` lists them." in first
    assert "GEMINI_API_KEY" not in out and "codex" not in out and "Higgsfield" not in out, "an unset hosted service is not listed by default"
    assert "PEXELS_API_KEY set" in out and re.search(r"^yes\s+Stock from Pexels", out, re.MULTILINE), "one that is set up still shows"
    assert re.search(r"^-- speakers", out, re.MULTILINE) and "fix: Do this." in out
    assert len(out.splitlines()) < 30


def test_all_lists_the_hosted_services_too(monkeypatch, capsys):
    working_machine(monkeypatch)
    assert main(["doctor", "--all"]) == 0
    out = capsys.readouterr().out
    assert re.search(r"^no\s+Editor in Chief through Gemini", out, re.MULTILINE) and "fix: export GEMINI_API_KEY=your-key" in out
    assert re.search(r"^-- codex", out, re.MULTILINE)


def test_a_machine_that_cannot_edit_says_so_first(monkeypatch, capsys):
    stub_machine(monkeypatch, ok=False)
    monkeypatch.setattr(doctor, "check_disk", lambda: doctor.Check("disk", True, False, "20 GB free"))
    assert main(["doctor"]) == 1
    captured = capsys.readouterr()
    assert "Not ready: ffmpeg and transcriber are missing, so nothing can be edited yet." in captured.out.splitlines()[0]
    assert re.search(r"^!! ffmpeg", captured.out, re.MULTILINE) and "Required tools are missing" in captured.err


def test_no_text_reader_is_part_of_the_answer_because_it_blocks_ship(monkeypatch):
    working_machine(monkeypatch, missing=("ocr",))
    ready, said = doctor.verdict(doctor.checks(doctor.core_checks()), doctor.capabilities(doctor.core_checks()))
    assert ready and "and run QA." not in said
    assert "without ocr some gates skip, and a skipped gate blocks SHIP" in said


def test_json_says_the_version_and_whether_the_machine_is_ready(monkeypatch, capsys):
    """commands/setup.md says "if doctor reports an older Altocue", and the report had no version to read."""
    from altocue import __version__

    working_machine(monkeypatch, missing=("renderer",))
    assert main(["doctor", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["version"] == __version__ and data["ready"] is True and data["ok"] is True
    assert data["verdict"].startswith("Ready:") and "renderer" in data["verdict"]
    assert sum(1 for row in data["capabilities"] if row["hosted"]) == 11, "the report itself leaves nothing out"
    assert {check["id"] for check in data["checks"] if check["hosted"]} >= {"gemini", "codex"}
    stub_machine(monkeypatch, ok=False)
    assert main(["doctor", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ready"] is False and data["verdict"].startswith("Not ready:")


def test_the_speakers_row_keeps_the_second_step_of_its_fix(monkeypatch):
    working_machine(monkeypatch)
    check = doctor.Check("speakers", False, False, "not installed: words will have no speakers", "x")
    assert "altocue setup --speakers" in doctor.speakers_how(check)
    fetched = doctor.Check("speakers", False, False, "models not fetched: words will have no speakers", "x")
    assert doctor.speakers_how(fetched) == fetched.detail and doctor.speakers_fix(fetched) == "altocue setup --speakers"


# `altocue setup`


def setup_args(**given) -> doctor.argparse.Namespace:
    return doctor.argparse.Namespace(**{"yes": False, "skip_denoiser": False, "deep_filter": False, "skip_renderer": False, "speakers": False, "plugin": False, **given})


def bare_machine(monkeypatch, tmp_path, *, terminal: bool, answer: str = "n"):
    """Nothing installed yet, npm present, and no network: a download would fail the test."""
    from altocue import clean, setup

    monkeypatch.setattr(setup, "HOME", tmp_path / "home")
    monkeypatch.setattr(setup.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(doctor, "check_ffmpeg", lambda: doctor.Check("ffmpeg", True, True, "ffmpeg"))
    monkeypatch.setattr(clean, "find_isolator", lambda: None)
    monkeypatch.setattr(clean, "find_deep_filter", lambda: None)
    monkeypatch.setattr(setup.platform, "system", lambda: "Linux")
    monkeypatch.setattr(setup.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(setup.sys.stdin, "isatty", lambda: terminal, raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt: answer)
    monkeypatch.setattr(setup.subprocess, "run", lambda *a, **k: pytest.fail("installed without a yes"))
    monkeypatch.setattr(setup.urllib.request, "urlopen", lambda *a, **k: pytest.fail("downloaded without a yes"))
    return setup


def test_setup_that_could_not_ask_says_nothing_was_installed(monkeypatch, tmp_path, capsys):
    """With no terminal it printed two Skipped lines, then "Run `altocue doctor` to confirm", and an agent read that as done."""
    setup = bare_machine(monkeypatch, tmp_path, terminal=False)
    assert setup.run(setup_args()) == 0, "the extras are optional, and the skills run a plain `altocue setup` after the install"
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.startswith("Nothing was installed: there is no terminal to ask at") and "altocue setup --yes" in last and "Ask the user" in last
    assert "confirm" not in last


def test_setup_that_was_told_no_says_so_without_calling_it_a_failure(monkeypatch, tmp_path, capsys):
    setup = bare_machine(monkeypatch, tmp_path, terminal=True, answer="n")
    assert setup.run(setup_args()) == 0
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.startswith("Nothing was installed, as you chose.")


def test_setup_with_nothing_left_to_install_says_that(monkeypatch, tmp_path, capsys):
    from altocue import clean

    setup = bare_machine(monkeypatch, tmp_path, terminal=False)
    monkeypatch.setattr(clean, "find_deep_filter", lambda: tmp_path / "deep-filter")
    (tmp_path / "home" / "renderer" / "node_modules" / ".bin").mkdir(parents=True)
    (tmp_path / "home" / "renderer" / "node_modules" / ".bin" / "hyperframes").write_text("")
    assert setup.run(setup_args()) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1].startswith("Nothing was installed, because nothing was missing")


def test_setup_that_installed_something_points_at_doctor(monkeypatch, tmp_path, capsys):
    setup = bare_machine(monkeypatch, tmp_path, terminal=False)
    monkeypatch.setattr(setup, "install_renderer", lambda yes: setup.INSTALLED)
    monkeypatch.setattr(setup, "install_denoiser", lambda yes, even_on_a_mac=False: setup.UNAVAILABLE)
    assert setup.run(setup_args(yes=True)) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "Run `altocue doctor` to confirm."


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
    assert 'uv tool install --force "altocue[parakeet]"' in fix
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
