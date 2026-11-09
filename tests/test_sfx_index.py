"""The sound index: open packs listed by address and hash, each sound a catalog row, one file fetched into a project. No network."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import socket
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

from altocue import cli, ffmpeg, library
from altocue.errors import AltocueError
from altocue.generate import http
from altocue.project import Project
from altocue.stock import credits, sfxpacks

ROOT = Path(__file__).resolve().parents[1]
RATE = 48000
PAGE = "https://packs.example/assets/tiny-ui"
FILES = {
    "Tiny UI/Audio/click_001.wav": (0.12, 1800),
    "Tiny UI/Audio/whooshLong_002.wav": (0.6, 300),
    "Tiny UI/Audio/glitch_03.wav": (0.25, 5000),
    "Tiny UI/Audio/notification-ding.wav": (0.4, 1200),
    "Tiny UI/Audio/swipe_left.wav": (0.2, 700),
    "Tiny UI/Audio/riser_01.wav": (1.1, 200),
    "Tiny UI/Audio/Spotify jingle.wav": (0.3, 900),
}


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


index_sfx = load("index_sfx")
build = load("build_library")


def tone(seconds: float, hertz: float) -> np.ndarray:
    t = np.arange(round(seconds * RATE)) / RATE
    return 0.3 * np.sin(2 * np.pi * hertz * t) * np.exp(-t * 3)


@pytest.fixture
def fake_pack(tmp_path: Path) -> Path:
    """A zip laid out like a real pack: sound files in folders, a licence file, and junk a pack index must skip."""
    folder = tmp_path / "made"
    for name, (seconds, hertz) in FILES.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        ffmpeg.write_wav(folder / name, tone(seconds, hertz), RATE)
    archive = tmp_path / "upstream" / "kenney_tiny-ui.zip"
    archive.parent.mkdir()
    with zipfile.ZipFile(archive, "w") as out:
        for name in FILES:
            out.write(folder / name, name)
        out.writestr("Tiny UI/License.txt", "CC0 1.0")
        out.writestr("__MACOSX/Tiny UI/Audio/._click_001.wav", b"junk")
    return archive


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the network was touched")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


@pytest.fixture
def downloads(fake_pack, monkeypatch, no_network, tmp_path):
    """Every download is a copy of the fake pack, counted, and every page read names its link."""
    calls: list[str] = []

    def fake_download(url: str, dest: Path) -> Path:
        calls.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(fake_pack, dest)
        return dest

    def fake_call(method: str, url: str, **kwargs):
        assert url == PAGE
        return http.Response(200, b'<a class="button" href="/media/pages/assets/tiny-ui/abc-123/kenney_tiny-ui.zip">Download</a>')

    monkeypatch.setattr(sfxpacks, "download", fake_download)
    monkeypatch.setattr(http, "call", fake_call)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return calls


@pytest.fixture
def packs_file(tmp_path: Path) -> Path:
    pack = {
        "id": "kenney-tiny-ui", "title": "Tiny UI", "author": "Kenney", "source": "kenney", "page": PAGE, "licence": "CC0-1.0",
        "licence_url": "https://creativecommons.org/publicdomain/zero/1.0/", "credit": None, "files": len(FILES), "version": "1.0",
        "hints": [], "download": None, "find_download": r"""[^"'\s<>]*/kenney_[^"'\s<>/]+\.zip""", "sha256": None, "bytes": None,
        "pinned_on": None, "checked_on": "2026-11-08", "checked_from": "primary", "notes": "",
    }  # fmt: skip
    path = tmp_path / "packs.json"
    path.write_text(json.dumps({"schema": 1, "packs": [pack]}))
    return path


def indexed(packs_file: Path, tmp_path: Path) -> dict:
    return index_sfx.run([], tmp_path / "cache", repin=False, packs_path=packs_file, index_path=tmp_path / "index.json")


# Naming and sorting


@pytest.mark.parametrize(
    ("path", "category"),
    [
        ("Audio/click_001.ogg", "click"),
        ("Audio/impactPunch_heavy_002.ogg", "impact"),
        ("Audio/whooshLong_002.wav", "whoosh"),
        ("Audio/swipe_left.wav", "swipe"),
        ("Audio/glitch_03.wav", "glitch"),
        ("Audio/notification-ding.wav", "notification"),
        ("Audio/riser_01.wav", "riser"),
        ("Audio/drop_002.ogg", "pop"),
        ("Audio/typewriter_04.ogg", "typing"),
        ("Audio/camera-shutter.wav", "camera"),
        ("Audio/jingles_NES00.ogg", "jingle"),
        ("Ambience/forest_loop.ogg", "ambience"),
    ],
)
def test_each_sound_gets_a_clear_category_from_its_name(path, category):
    assert sfxpacks.categorise(path)[0] == category


def test_a_name_that_says_nothing_falls_back_to_its_folder_then_the_pack_and_never_guesses():
    assert sfxpacks.categorise("Whoosh/0001.wav")[0] == "whoosh"
    assert sfxpacks.categorise("Audio/round_1.ogg", ["voice"])[0] == "voice"
    assert sfxpacks.categorise("Audio/qwerty.ogg")[0] == "other"


def test_a_sound_at_the_top_of_the_archive_with_no_folder_is_still_sorted():
    assert sfxpacks.categorise("laser_04.wav")[0] == "laser"
    assert sfxpacks.categorise("qwerty.ogg", ["click"])[0] == "click"


def test_ui_sounds_carry_a_ui_tag_and_their_own_words():
    category, tags = sfxpacks.categorise("Interface/click_001.ogg")
    assert category == "click" and {"ui", "click", "interface"} <= set(tags)


def test_a_title_says_what_the_file_is_and_which_take():
    assert sfxpacks.title_of("Audio/impactPunch_heavy_002.ogg") == "Impact punch heavy 002"


# Indexing a pack


def test_the_index_reads_the_link_from_the_page_pins_its_hash_and_lists_every_sound(downloads, packs_file, tmp_path, fake_pack):
    body = indexed(packs_file, tmp_path)
    pack = json.loads(packs_file.read_text())["packs"][0]
    assert pack["download"] == "https://packs.example/media/pages/assets/tiny-ui/abc-123/kenney_tiny-ui.zip"
    assert pack["sha256"] == hashlib.sha256(fake_pack.read_bytes()).hexdigest() and pack["bytes"] == fake_pack.stat().st_size
    rows = {row["path"].rsplit("/", 1)[-1]: row for row in body["rows"]}
    assert set(rows) == {Path(name).name for name in FILES} - {"Spotify jingle.wav"}, "licence files, junk and branded names stay out"
    assert body["left_out"]["kenney-tiny-ui"][0]["rule"] == "brand"
    click = rows["click_001.wav"]
    assert click["category"] == "click" and click["seconds"] == pytest.approx(0.12, abs=0.01) and click["licence"] == "CC0-1.0"
    assert click["id"] == "kenney-tiny-ui-click-001" and click["pack"] == "kenney-tiny-ui" and click["path"] == "Tiny UI/Audio/click_001.wav"
    with zipfile.ZipFile(fake_pack) as opened:
        assert click["sha256"] == hashlib.sha256(opened.read(click["path"])).hexdigest()
    assert json.loads((tmp_path / "index.json").read_text())["sounds"] == len(rows)


def test_a_pinned_pack_that_changes_upstream_is_reported_and_its_pin_kept(downloads, packs_file, tmp_path):
    data = json.loads(packs_file.read_text())
    data["packs"][0].update(sha256="0" * 64, download="https://packs.example/kenney_tiny-ui.zip")
    packs_file.write_text(json.dumps(data))
    body = indexed(packs_file, tmp_path)
    [failure] = body["failed"]
    assert failure["pack"] == "kenney-tiny-ui" and "does not match" in failure["error"] and "--repin" in failure["fix"]
    assert json.loads(packs_file.read_text())["packs"][0]["sha256"] == "0" * 64, "a mismatch never rewrites the pin"


def test_one_dead_pack_is_reported_and_the_rest_are_still_indexed(downloads, packs_file, tmp_path, monkeypatch, capsys):
    data = json.loads(packs_file.read_text())
    dead = {**data["packs"][0], "id": "dead-pack", "page": "https://gone.example/pack"}
    data["packs"].insert(0, dead)
    packs_file.write_text(json.dumps(data))
    real_call = http.call
    monkeypatch.setattr(http, "call", lambda method, url, **kw: http.Response(404, b"") if url == dead["page"] else real_call(method, url, **kw))
    (tmp_path / "index.json").write_text(json.dumps({"rows": [{"id": "dead-pack-old", "pack": "dead-pack", "category": "click"}], "left_out": {}}))
    body = indexed(packs_file, tmp_path)
    assert [item["pack"] for item in body["failed"]] == ["dead-pack"] and "404" in body["failed"][0]["error"]
    packs = {row["pack"] for row in body["rows"]}
    assert packs == {"dead-pack", "kenney-tiny-ui"}, "the live pack is indexed and the dead one keeps its last rows"
    assert "dead-pack: NOT INDEXED" in capsys.readouterr().err


def test_the_script_exits_non_zero_when_any_pack_failed(monkeypatch, tmp_path):
    monkeypatch.setattr(index_sfx, "run", lambda *args, **kwargs: {"sounds": 3, "categories": {"click": 3}, "failed": [{"pack": "dead-pack", "error": "x", "fix": None}]})
    assert index_sfx.main(["--cache", str(tmp_path)]) == 1
    monkeypatch.setattr(index_sfx, "run", lambda *args, **kwargs: {"sounds": 3, "categories": {"click": 3}, "failed": []})
    assert index_sfx.main(["--cache", str(tmp_path)]) == 0


def test_a_pack_outside_the_allow_list_is_never_indexed(packs_file, tmp_path):
    data = json.loads(packs_file.read_text())
    data["packs"][0]["licence"] = "CC-BY-NC-4.0"
    packs_file.write_text(json.dumps(data))
    with pytest.raises(AltocueError, match="allow-list"):
        indexed(packs_file, tmp_path)


def test_a_page_with_no_archive_link_says_how_to_fix_it(packs_file, tmp_path, monkeypatch, no_network):
    monkeypatch.setattr(http, "call", lambda *args, **kwargs: http.Response(200, b"<p>No link here</p>"))
    [failure] = indexed(packs_file, tmp_path)["failed"]
    assert "download" in failure["fix"]


def test_the_committed_pack_list_is_open_and_holds_no_audio():
    packs = index_sfx.read_packs()
    assert len(packs["packs"]) >= 4 and all(pack["licence"] == "CC0-1.0" for pack in packs["packs"])
    assert not list((ROOT / "library" / "sfx").glob("*.[ow][ga][gv]")), "no media in git"


# The catalog


def rows_for(packs_file: Path, tmp_path: Path) -> list[dict]:
    indexed(packs_file, tmp_path)
    return build.sound_entries(*build.read_sound_index(packs_file, tmp_path / "index.json"))


def catalog_with(rows: list[dict]) -> dict:
    return {"schema": 1, "generated": "2026-11-08T10:00:00Z", "pins": {}, "entries": rows, "sources": [], "refused": []}


def test_each_sound_becomes_a_fetchable_catalog_row_that_passes_the_checks(downloads, packs_file, tmp_path):
    rows = rows_for(packs_file, tmp_path)
    assert len(rows) == len(FILES) - 1 and {row["kind"] for row in rows} == {"sfx"} and {row["tier"] for row in rows} == {"fetch"}
    library.validate(catalog_with(rows))
    whoosh = next(row for row in rows if row["category"] == "whoosh")
    assert whoosh["jobs"] == ["transition"] and whoosh["archive_sha256"] == json.loads(packs_file.read_text())["packs"][0]["sha256"]


def test_a_pack_with_no_pin_offers_no_rows(packs_file, tmp_path):
    (tmp_path / "index.json").write_text(json.dumps({"rows": [{"id": "kenney-tiny-ui-a", "pack": "kenney-tiny-ui", "licence": "CC0-1.0"}]}))
    assert build.sound_entries(*build.read_sound_index(packs_file, tmp_path / "index.json")) == []


def test_no_index_yet_means_no_rows_and_no_error(tmp_path):
    assert build.sound_entries(*build.read_sound_index(tmp_path / "none.json", tmp_path / "none.json")) == []


def test_search_finds_sounds_by_category_and_by_tag(downloads, packs_file, tmp_path):
    catalog = catalog_with(rows_for(packs_file, tmp_path))
    assert library.search(catalog, "glitch", kind="sfx")[0]["category"] == "glitch"
    assert {row["category"] for row in library.search(catalog, "ui", kind="sfx")} >= {"click", "notification", "swipe"}
    assert library.search(catalog, "whoosh long")[0]["id"] == "kenney-tiny-ui-whooshlong-002"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sha256", "abc"),
        ("archive_sha256", None),
        ("archive", "http://packs.example/a.zip"),
        ("path", "../../etc/passwd"),
        ("path", "/abs.wav"),
        ("path", "a\\..\\b.wav"),
    ],
)
def test_a_sound_row_that_could_not_be_checked_or_could_escape_is_refused(downloads, packs_file, tmp_path, field, value):
    rows = rows_for(packs_file, tmp_path)
    rows[0][field] = value
    with pytest.raises(AltocueError):
        library.validate(catalog_with(rows))


# Fetching one sound


@pytest.fixture
def catalogued(downloads, packs_file, tmp_path, monkeypatch):
    rows = rows_for(packs_file, tmp_path)
    downloads.clear()
    path = tmp_path / "bundled.json"
    path.write_text(json.dumps(catalog_with(rows)))
    monkeypatch.setattr(library, "bundled_path", lambda: path)
    monkeypatch.setattr(library, "updated_path", lambda: tmp_path / "no-update.json")
    return rows


def test_library_add_downloads_the_pack_once_and_extracts_one_sound_with_its_notice(catalogued, downloads, tmp_path, capsys):
    project = Project.create(tmp_path / "film")
    assert cli.main(["library", "add", "kenney-tiny-ui-click-001", "-p", str(project.root)]) == 0
    assert cli.main(["library", "add", "kenney-tiny-ui-glitch-03", "-p", str(project.root)]) == 0
    assert len(downloads) == 1, "the pack comes down once and is reused from the cache"
    sound = project.root / "sfx" / "kenney-tiny-ui-click-001.wav"
    assert ffmpeg.probe_json(sound)["format"]["duration"]
    assert sorted(path.name for path in (project.root / "sfx").iterdir()) == [
        "kenney-tiny-ui-click-001.LICENSE.txt", "kenney-tiny-ui-click-001.wav", "kenney-tiny-ui-glitch-03.LICENSE.txt", "kenney-tiny-ui-glitch-03.wav",
    ]  # fmt: skip
    notice = (project.root / "sfx" / "kenney-tiny-ui-click-001.LICENSE.txt").read_text()
    assert "CC0-1.0" in notice and "creativecommons.org/publicdomain/zero" in notice and PAGE in notice
    row = next(row for row in credits.records(project) if row["file"] == "sfx/kenney-tiny-ui-click-001.wav")
    assert row["licence"] == "CC0-1.0" and row["sha256"] == hashlib.sha256(sound.read_bytes()).hexdigest() and not credits.owes(row)
    lock = json.loads((project.root / library.LOCK_FILE).read_text())["items"]
    assert lock["kenney-tiny-ui-click-001"]["path"] == "Tiny UI/Audio/click_001.wav"
    assert '"file": "sfx/kenney-tiny-ui-click-001.wav"' in capsys.readouterr().out


def test_sfx_get_and_search_reach_the_same_sounds(catalogued, downloads, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("FREESOUND_TOKEN", raising=False)
    assert cli.main(["sfx", "search", "riser"]) == 0
    assert "kenney-tiny-ui-riser-01" in capsys.readouterr().out
    project = Project.create(tmp_path / "film")
    assert cli.main(["sfx", "get", "kenney-tiny-ui-riser-01", "-p", str(project.root)]) == 0
    assert (project.root / "sfx" / "kenney-tiny-ui-riser-01.wav").is_file()


def test_a_tampered_pack_is_thrown_away_and_nothing_reaches_the_project(catalogued, downloads, tmp_path, fake_pack, capsys):
    with zipfile.ZipFile(fake_pack, "a") as out:
        out.writestr("extra.txt", "changed upstream")
    project = Project.create(tmp_path / "film")
    assert cli.main(["library", "add", "kenney-tiny-ui-click-001", "-p", str(project.root)]) != 0
    assert "does not match its recorded sha256" in capsys.readouterr().err
    assert not (project.root / "sfx").exists() and not list(sfxpacks.cache_dir().glob("*.zip"))


def test_a_changed_file_inside_a_good_pack_is_refused(catalogued, tmp_path, monkeypatch):
    row = dict(next(row for row in catalogued if row["category"] == "click"), sha256="f" * 64)
    with pytest.raises(AltocueError, match="does not match"):
        sfxpacks.fetch(Project.create(tmp_path / "film"), row)


def test_show_says_how_to_fetch_a_sound(catalogued, capsys):
    assert cli.main(["library", "show", "kenney-tiny-ui-click-001"]) == 0
    out = capsys.readouterr().out
    assert "altocue library add kenney-tiny-ui-click-001" in out and "sha256" in out
