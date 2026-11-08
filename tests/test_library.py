"""The library: one searchable catalog, built from open sources and refusing everything else."""

from __future__ import annotations

import importlib.util
import json
import socket
import stat
import sys
from collections import Counter
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from altocue import library
from altocue.cli import main
from altocue.errors import AltocueError
from altocue.generate import http
from altocue.motion import compositions, formats
from altocue.project import Project
from altocue.timeline import resolve_items

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "library" / "sources"
COMMITTED = ROOT / "library" / "catalog.json"
REGISTRY_ITEMS = 448


def load_build():
    spec = importlib.util.spec_from_file_location("build_library", ROOT / "scripts" / "build_library.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_library"] = module
    spec.loader.exec_module(module)
    return module


build = load_build()


def entry(id: str, **fields) -> dict:
    base = {
        "id": id,
        "kind": "motion",
        "tier": "fetch",
        "title": id.replace("-", " ").title(),
        "summary": "A plain thing.",
        "tags": [],
        "jobs": [],
        "licence": "MIT",
        "url": "https://example.com/item",
        "ship": "Fetched into your project.",
        "source": "hyperframes",
    }
    return {**base, **fields}


def source(id: str, **fields) -> dict:
    base = {"id": id, "name": id.title(), "url": f"https://{id}.example/", "classes": ["video"], "holds": "Things.", "verdict": "DIRECTORY", "flags": [], "notes": ""}
    base["licence"] = {"scope": "source", "id": "MIT", "url": base["url"], "checked_on": "2026-11-02", "checked_from": "primary", "text_sha256": None}
    return {**base, **fields}


def catalog(*entries, sources=(), refused=(), generated="2026-11-02T10:00:00Z") -> dict:
    return {"schema": 1, "generated": generated, "pins": {"hyperframes": "0.9.5"}, "entries": list(entries), "sources": list(sources), "refused": list(refused)}


@pytest.fixture
def home(tmp_path, monkeypatch):
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setenv("HOME", str(folder))
    return folder


@pytest.fixture
def bundled(tmp_path, monkeypatch, home):
    """A small bundled catalog, so a test states exactly what is in it."""
    path = tmp_path / "bundled" / "catalog.json"
    path.parent.mkdir()
    made = catalog(
        entry(
            "headline-slam",
            title="Headline Slam",
            summary="A heavy headline lands.",
            tags=["typography"],
            jobs=["hook"],
            licence="Apache-2.0",
            gsap=True,
            seconds=3.5,
            slots=[{"name": "text", "kind": "string", "default": "Ship it today"}],
        ),
        entry("gl-wind", kind="transition", title="Wind", source="gl-transitions", jobs=["transition"]),
        entry("pixabay", kind="stock", also=["photo"], tier="search", source="pixabay", title="Pixabay", licence="per item"),
        sources=[
            source("pixabay", verdict="RUNTIME", classes=["video", "photo"]),
            source("coverr", verdict="EXCLUDE", exclude_reason="Its terms bar video editing services."),
        ],
        refused=[{"id": "tiktok-follow", "source": "hyperframes", "rule": "brand", "reason": "Its name references TikTok."}],
    )
    path.write_text(json.dumps(made))
    monkeypatch.setattr(library, "bundled_path", lambda: path)
    return made


@pytest.fixture
def project(tmp_path):
    return Project.create(tmp_path / "film")


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the network was touched")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def fake_renderer(
    tmp_path: Path, monkeypatch, *, html: str, files: dict[str, bytes] | None = None, target: str = "compositions/components/headline-slam.html", fail: bool = False
) -> Path:
    """Stands in for `hyperframes`: it records what it was asked and lays files out the way the real one does."""
    folder = tmp_path / "fake"
    bin_dir = folder / "renderer" / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    payload = folder / "payload"
    payload.mkdir()
    (payload / "main.html").write_text(html)
    for number, data in enumerate((files or {}).values()):
        (payload / str(number)).write_bytes(data)
    script = bin_dir / "hyperframes"
    script.write_text(
        f"""#!{sys.executable}
import json, pathlib, shutil, sys
args = sys.argv[1:]
if args[:1] == ["--version"]:
    print("0.9.5")
    raise SystemExit(0)
pathlib.Path({str(folder / "calls.json")!r}).write_text(json.dumps(args))
if {fail!r}:
    print("registry unreachable", file=sys.stderr)
    raise SystemExit(1)
root = pathlib.Path(args[args.index("--dir") + 1])
pathlib.Path({str(folder / "config.json")!r}).write_text((root / "hyperframes.json").read_text())
written = []
target = root / {target!r}
target.parent.mkdir(parents=True, exist_ok=True)
shutil.copy({str(payload / "main.html")!r}, target)
written.append(str(target))
for number, name in enumerate({list(files or {})!r}):
    out = root / name
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy({str(payload)!r} + "/" + str(number), out)
    written.append(str(out))
(root / "hyperframes.lock.json").write_text("{{}}")
print(json.dumps({{"ok": True, "name": args[1], "written": written}}))
"""
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("ALTOCUE_RENDERER_DIR", str(folder / "renderer"))
    return folder


COMPOSITION = '<html><body><template><div id="root" data-composition-id="headline-slam" data-duration="3.5"><script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script></div></template></body></html>'


# Ranking


def test_a_title_match_beats_a_tag_and_a_tag_beats_the_summary():
    items = [entry("a", title="Nothing", summary="A map of things."), entry("b", title="Nothing", tags=["map"]), entry("c", title="World Map")]
    assert [row["id"] for row in library.rank(items, "map")] == ["c", "b", "a"]


def test_every_word_matching_beats_one_word_matching_twice_as_well():
    items = [entry("a", title="Bar"), entry("b", title="Chart", tags=["bar"])]
    assert library.rank(items, "bar chart")[0]["id"] == "b"


def test_a_tie_goes_to_what_needs_nothing_fetched():
    items = [entry("a", title="Wipe"), entry("b", title="Wipe", tier="built-in"), entry("c", title="Wipe", tier="link", source="c")]
    assert [row["id"] for row in library.rank(items, "wipe")] == ["b", "a", "c"]


def test_plurals_match_and_nothing_matching_is_left_out():
    items = [entry("a", title="Lower thirds"), entry("b", title="Other")]
    assert [row["id"] for row in library.rank(items, "lower third")] == ["a"]


# Filters


def test_kind_filter_finds_a_source_under_any_of_its_kinds(bundled):
    assert [row["id"] for row in library.search(library.current(), "pixabay", kind="photo")] == ["pixabay"]
    assert library.search(library.current(), "pixabay", kind="music") == []


def test_tier_filter(bundled):
    found = library.search(library.current(), "headline", tier="link")
    assert found == []
    assert [row["id"] for row in library.search(library.current(), "headline", tier="fetch")] == ["headline-slam"]


def test_job_filter(bundled):
    assert [row["id"] for row in library.search(library.current(), "wind", job="transition")] == ["gl-wind"]
    assert library.search(library.current(), "wind", job="hook") == []


def test_the_job_words_are_the_ones_formats_use():
    assert library.JOBS == formats.JOBS


# The command


def test_search_prints_json_for_an_agent(bundled, capsys):
    assert main(["library", "search", "headline", "--json"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert "headline-slam" in [row["id"] for row in printed["results"]] and "note" in printed


def test_search_says_when_a_word_names_an_excluded_source(bundled, capsys):
    assert main(["library", "search", "coverr"]) == 0
    assert "Its terms bar video editing services" in capsys.readouterr().out


def test_search_finds_nothing_without_failing(bundled, capsys):
    assert main(["library", "search", "zzzz"]) == 0
    assert "Nothing matched" in capsys.readouterr().out


def test_show_a_format_gives_a_line_that_really_works(capsys):
    fmt = next(iter(formats.load().values()))
    assert main(["library", "show", fmt.id]) == 0
    out = capsys.readouterr().out
    line = json.loads(out[out.index('{"type"') :].splitlines()[0])
    items, _ = resolve_items([line], [], 30.0)
    assert items[0]["type"] == fmt.id and "MIT" in out and fmt.slots[0].name in out


@pytest.mark.parametrize("name", sorted(library.CORE))
def test_show_a_core_type_gives_a_line_that_really_works(name, capsys):
    assert main(["library", "show", name]) == 0
    out = capsys.readouterr().out
    line = json.loads(out[out.index('{"type"') :].splitlines()[0])
    assert resolve_items([line], [{"text": "hello", "start": 1.0, "end": 1.4}], 30.0)[0][0]["type"] == name


def test_show_a_fetched_item_says_how_to_get_it_and_what_it_runs(bundled, capsys):
    assert main(["library", "show", "headline-slam"]) == 0
    out = capsys.readouterr().out
    assert "altocue library add headline-slam" in out and "GSAP" in out and "Apache-2.0" in out
    assert '"type": "composition"' in out and '"Ship it today"' in out and library.UNTRUSTED_NOTE in out


def test_show_a_refused_item_gives_the_reason(bundled, capsys):
    assert main(["library", "show", "tiktok-follow"]) == 0
    assert "Its name references TikTok" in capsys.readouterr().out


def test_show_an_excluded_source_gives_the_reason(bundled, capsys):
    assert main(["library", "show", "coverr"]) == 0
    assert "Its terms bar video editing services" in capsys.readouterr().out


def test_show_something_unknown_is_an_error_with_a_fix(bundled, capsys):
    assert main(["library", "show", "nope"]) == 1
    assert "altocue library search" in capsys.readouterr().err


def test_sources_filter_by_verdict_and_keep_the_reason(bundled, capsys):
    assert main(["library", "sources", "--verdict", "EXCLUDE"]) == 0
    out = capsys.readouterr().out
    assert "coverr" in out and "Its terms bar" in out and "pixabay" not in out


def test_sources_filter_by_kind(bundled, capsys):
    assert main(["library", "sources", "--kind", "photo"]) == 0
    out = capsys.readouterr().out
    assert "pixabay" in out and "coverr" not in out


def test_stats_are_counted_not_typed(bundled, capsys):
    assert main(["library", "stats", "--json"]) == 0
    stats = json.loads(capsys.readouterr().out)
    shown = library.current()["entries"]
    assert stats["entries"] == len(shown) == sum(stats["tiers"].values()) == sum(stats["kinds"].values())
    assert stats["tiers"]["built-in"] == len(formats.load()) + len(library.CORE) and stats["sources"] == 2 and stats["refused"] == 1


def test_a_format_added_after_the_last_build_is_in_the_library(bundled, monkeypatch):
    formats.load()
    slots = (formats.Slot("text"),)
    fresh = formats.Format(id="brand_new", title="Brand new", summary="New today.", group="kinetic", jobs=("hook",), slots=slots, example={"text": "H"}, draw=str, css="")
    monkeypatch.setitem(formats._loaded, "brand_new", fresh)
    assert library.search(library.current(), "brand new")[0]["id"] == "brand_new"
    made = build.format_entries()
    assert "brand_new" in {row["id"] for row in made}


def test_a_built_in_keeps_its_name_when_a_catalog_lists_the_same_one(bundled, home):
    name = build.format_entries()[-1]["id"]
    path = library.updated_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(catalog(entry(name), generated="2099-01-01T00:00:00Z")))
    rows = [row for row in library.current()["entries"] if row["id"] == name]
    assert [row["tier"] for row in rows] == ["built-in"]


def test_a_newer_catalog_never_claims_a_format_this_install_lacks(bundled, home):
    path = library.updated_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(catalog(entry("from_the_future", tier="built-in", source="altocue"), generated="2099-01-01T00:00:00Z")))
    assert "from_the_future" not in {row["id"] for row in library.current()["entries"]}


# The deny list


@pytest.mark.parametrize(
    ("name", "title"),
    [
        ("instagram-follow", "Instagram Follow"),
        ("spotify-card", "Spotify Now Playing"),
        ("chatgpt-exchange", "ChatGPT Exchange"),
        ("yt-comment-card", "Comment Cards"),
        ("x-post", "X Post Card"),
        ("heygen-avatar-promo-card", "HeyGen Avatar Promo Card"),
        ("vfx-iphone-device", "iPhone & MacBook 3D Showcase"),
        ("lower-third-bild", "Lower Third, BILD Style"),
    ],
)
def test_a_real_brand_in_a_name_or_title_is_refused(name, title):
    assert library.refusal(name, title)["rule"] == "brand"


ORDINARY = ["chrome-orb", "stripes", "liquid-metal", "cursor-annotate", "zoom-through-transition", "arc-motion-path", "vfx-text-cursor", "camera-rig-depth-stack"]


@pytest.mark.parametrize("name", ORDINARY)
def test_an_ordinary_word_is_not_a_brand(name):
    assert library.refusal(name, name.replace("-", " ").title()) is None


def test_x_the_letter_is_not_x_the_brand():
    assert library.refusal("x-ray-wipe", "X-Ray Wipe") is None


def test_a_transitions_dev_port_is_refused():
    assert library.refusal("badge-pop", "Badge Pop", tags=["transitions-dev-port"])["rule"] == "transitions-dev-port"


def test_an_item_that_paints_a_real_product_is_refused_even_with_a_plain_name():
    assert library.refusal("store-badge-lockup", "Store Badge Lockup", "Equal-size App Store and Google Play badges.")["rule"] == "brand-description"


def test_a_long_dash_from_upstream_becomes_a_comma():
    assert build.plain_summary("Lower Third \u2014 Bold Block") == "Lower Third, Bold Block"


def test_a_passing_style_word_in_a_description_is_dropped_not_refused():
    description = "Red background sweep behind each active word, TikTok-style"
    assert library.refusal("caption-highlight", "Highlight", description) is None
    assert build.plain_summary(description) == "Red background sweep behind each active word"
    assert build.plain_summary("Apple-style realistic map animation with a plane.") == "Realistic map animation with a plane."


def test_real_musicians_in_the_default_images_are_refused():
    found = library.refusal("carousel-circle-1", "Carousel", files=["assets/carousel-images/artist-tool.jpg"])
    assert found["rule"] == "real-people-media"


def test_a_brand_mark_among_the_assets_is_refused():
    assert library.refusal("some-card", "Some Card", files=["assets/img/slack-mark.svg"])["rule"] == "brand-asset"


def test_the_committed_deny_counts_are_what_was_computed():
    data = json.loads(COMMITTED.read_text())
    rules = Counter(row["rule"] for row in data["refused"] if row["source"] == "hyperframes")
    assert rules == {"transitions-dev-port": 16, "brand": 30, "brand-description": 7, "real-people-media": 25}
    kept = [row for row in data["entries"] if row["source"] == "hyperframes" and row["id"] != "hyperframes"]
    shadowed = json.loads((ROOT / "library" / "report.json").read_text())["named_by_a_built_in"]
    assert len(kept) + sum(rules.values()) + len(shadowed) == REGISTRY_ITEMS


def test_nothing_in_the_catalog_trips_the_deny_list():
    data = json.loads(COMMITTED.read_text())
    for row in data["entries"]:
        if row["tier"] == "fetch":
            assert library.refusal(row["id"], row["title"], row["summary"], row["tags"]) is None, row["id"]
    assert not {row["id"] for row in data["refused"]} & {row["id"] for row in data["entries"]}


# The licence gate


@pytest.mark.parametrize("expression", ["MIT", "Apache-2.0", "Apache-2.0 AND MIT", "MIT AND OFL-1.1", "BSD-3-Clause", "CC0-1.0", "MIT OR GPL-3.0-only"])
def test_an_open_licence_passes(expression):
    assert library.licence_allowed(expression)


@pytest.mark.parametrize("expression", ["GPL-3.0-only", "Apache-2.0 AND GPL-3.0-only", "CC-BY-NC-4.0", "CC-BY-4.0", "custom", "", None, "per item"])
def test_anything_else_fails(expression):
    assert not library.licence_allowed(expression)


def test_a_registry_item_outside_the_allow_list_is_refused_with_a_reason():
    rows = [{"name": "bad-licence", "type": "block", "title": "Bad", "description": "A thing.", "tags": [], "license": "GPL-3.0-only"}]
    entries, refused = build.registry_entries(rows, "0.9.5")
    assert entries == [] and refused[0]["rule"] == "licence" and "GPL-3.0-only" in refused[0]["reason"]


def test_a_registry_item_with_no_licence_of_its_own_inherits_apache():
    entries, refused = build.registry_entries([{"name": "fine", "type": "block", "title": "Fine", "description": "A thing.", "tags": []}], "0.9.5")
    assert refused == [] and entries[0]["licence"] == "Apache-2.0"


def test_a_snippet_with_no_timeline_is_a_link_not_something_to_fetch():
    rows = [{"name": "vignette", "type": "component", "title": "Vignette", "description": "A thing.", "tags": [], "mountable": False}]
    entries, refused = build.registry_entries(rows, "0.9.5")
    assert refused == [] and entries[0]["tier"] == "link" and "cannot mount" in entries[0]["ship"]


def test_a_registry_item_named_like_a_built_in_stays_out_of_the_build():
    built_in = entry("flowchart", tier="built-in", source="altocue")
    kept, shadowed = build.unclaimed([entry("flowchart"), entry("headline-slam")], [built_in])
    assert [row["id"] for row in kept] == ["headline-slam"] and shadowed == ["flowchart"]
    library.validate(build.assemble([built_in, *kept], [], [], {"hyperframes": "0.9.5"}, previous=None))


def test_a_shader_outside_mit_and_bsd_is_refused():
    rows = [
        {"name": "Good", "author": "a", "license": "MIT", "description": "One."},
        {"name": "AlsoGood", "author": "b", "license": "BSD 3 Clause", "description": "Two."},
        {"name": "Bad", "author": "c", "license": "Apache-2.0", "description": "Three."},
    ]
    entries, refused = build.transition_entries(rows, "2.2.0")
    assert [row["licence"] for row in entries] == ["MIT", "BSD-3-Clause"] and [row["id"] for row in refused] == ["gl-bad"]


def test_gl_transitions_is_read_at_its_pin_never_at_latest(monkeypatch):
    """Whatever upstream publishes next reaches users only through a reviewed change to the pin."""
    asked = []
    monkeypatch.setattr(build, "_get_json", lambda url: asked.append(url) or [])
    rows, version = build.fetch_transitions()
    assert version == build.SHADER_VERSION == json.loads(COMMITTED.read_text())["pins"]["gl-transitions"]
    assert asked == [build.SHADER_INDEX.format(version=build.SHADER_VERSION)]


def test_the_sync_bot_opens_a_pull_request_and_never_pushes_to_main():
    workflow = (ROOT / ".github" / "workflows" / "library-sync.yml").read_text()
    assert "HEAD:main" not in workflow and "gh pr create" in workflow
    assert "pull-requests: write" in workflow


# What the catalog may never hold


def test_a_built_in_with_no_licence_fails():
    with pytest.raises(AltocueError, match="no licence"):
        library.validate(catalog(entry("x", tier="built-in", licence="")))


def test_a_fetch_entry_outside_the_allow_list_fails():
    with pytest.raises(AltocueError, match="not on the allow-list"):
        library.validate(catalog(entry("x", licence="GPL-3.0-only")))


def test_an_excluded_source_cannot_leak_items():
    with pytest.raises(AltocueError, match="excluded source"):
        library.validate(catalog(entry("a", source="coverr"), sources=[source("coverr", verdict="EXCLUDE", exclude_reason="No.")]))


def test_a_link_only_source_gets_one_record_and_no_item_index():
    with pytest.raises(AltocueError, match="more than one"):
        library.validate(catalog(entry("twenty-one", tier="link", source="21st-dev"), entry("some-item", tier="link", source="21st-dev"), sources=[source("21st-dev")]))


def test_an_id_cannot_climb_out_of_a_project():
    with pytest.raises(AltocueError, match="file name"):
        library.validate(catalog(entry("../../evil")))


def test_an_excluded_record_needs_its_reason():
    with pytest.raises(AltocueError, match="reason"):
        library.validate(catalog(sources=[source("coverr", verdict="EXCLUDE")]))


def test_an_unverified_licence_cannot_be_used_at_runtime():
    record = source("x", verdict="RUNTIME")
    record["licence"]["checked_from"] = "not_verified"
    with pytest.raises(AltocueError, match="not verified"):
        library.validate(catalog(sources=[record]))


def test_refused_means_refused_even_if_a_catalog_lists_it():
    with pytest.raises(AltocueError, match="refused"):
        library.validate(catalog(entry("a"), refused=[{"id": "a", "source": "hyperframes", "rule": "brand", "reason": "No."}]))


def test_a_brand_cannot_arrive_in_a_downloaded_catalog():
    with pytest.raises(AltocueError, match="TikTok"):
        library.validate(catalog(entry("tiktok-follow", title="TikTok Follow")))


def test_the_build_fails_on_a_bad_source_record(tmp_path):
    (tmp_path / "x.json").write_text(json.dumps(source("y")))
    with pytest.raises(AltocueError, match="does not match its file"):
        build.read_sources(tmp_path)


def test_an_excluded_source_never_becomes_an_entry():
    rows = build.source_entries([source("coverr", verdict="EXCLUDE", exclude_reason="No."), source("pixabay", verdict="RUNTIME", classes=["video", "photo"])])
    assert [(row["id"], row["tier"], row["kind"], row["also"]) for row in rows] == [("pixabay", "search", "stock", ["photo"])]


def test_the_sync_says_how_far_the_pin_is_behind(monkeypatch):
    monkeypatch.setattr(build, "_get_json", lambda url: {"version": "0.9.6"})
    assert build.release_drift("0.9.5") == "HyperFrames 0.9.6 is out. The pin is 0.9.5."
    assert build.release_drift("0.9.6") == "The pinned HyperFrames 0.9.6 is the latest release."

    def down(url):
        raise AltocueError("offline")

    monkeypatch.setattr(build, "_get_json", down)
    assert "Could not read" in build.release_drift("0.9.5")


def test_the_build_keeps_its_date_when_nothing_changed(tmp_path):
    first = build.assemble([entry("a")], [], [], {"hyperframes": "0.9.5"}, previous=None)
    again = build.assemble([entry("a")], [], [], {"hyperframes": "0.9.5"}, previous={**first, "generated": "2020-01-01T00:00:00Z"})
    assert again["generated"] == "2020-01-01T00:00:00Z"
    changed = build.assemble([entry("b")], [], [], {"hyperframes": "0.9.5"}, previous={**first, "generated": "2020-01-01T00:00:00Z"})
    assert changed["generated"] != "2020-01-01T00:00:00Z"


def test_a_dead_link_is_reported_and_a_shy_site_never_fails_the_run(monkeypatch):
    answers = {"https://gone.example/": 404, "https://shy.example/": 403, "https://fine.example/": 200}

    def call(method, url, **kwargs):
        if url == "https://down.example/":
            raise http.Unreachable("no route")
        return http.Response(answers[url])

    monkeypatch.setattr(build.http, "call", call)
    records = [{"url": url} for url in [*answers, "https://down.example/", "http://plain.example/"]]
    written = build.check_links(records)
    assert "1 answered, 1 dead, 2 would not say" in written and "Dead (404): https://gone.example/" in written


def test_the_two_committed_catalogs_are_the_same_file():
    assert COMMITTED.read_bytes() == (ROOT / "src/altocue/data/library/catalog.json").read_bytes()


def test_the_committed_catalog_passes_its_own_checks():
    library.validate(json.loads(COMMITTED.read_text()))


def test_the_committed_catalog_names_nothing_it_must_not():
    text = (COMMITTED.read_text() + "".join(path.read_text() for path in SOURCES.glob("*.json"))).lower()
    assert "worldclaw" not in text and "scrapling" not in text and chr(0x2014) not in text


def test_every_source_record_follows_the_schema():
    records = {path.stem: json.loads(path.read_text()) for path in SOURCES.glob("*.json")}
    assert len(records) >= 150
    for id, record in records.items():
        assert record["id"] == id and record["url"].startswith(("https://", "http://ccmixter.org")) and record["holds"].endswith(".")
        assert record["verdict"] in library.VERDICTS and set(record["classes"]) <= set(library.CLASS_KINDS)
        assert record["licence"]["scope"] in ("source", "per_set", "per_item") and record["licence"]["checked_from"] in ("primary", "secondary", "not_verified")
        assert record["access"]["kind"] in ("bundle", "api", "cdn", "npm", "git", "local", "code", "manual")
        assert record["access"]["key"] in ("none", "free", "paused", "paid")
        assert set(record["rights"]) == {"commercial", "bundle", "tool_use", "agent_download", "ai_training"}
        assert set(record["flags"]) <= library.FLAGS
        if record["verdict"] == "EXCLUDE":
            assert len(record["exclude_reason"]) > 12


def test_the_owners_own_links_carry_the_agreed_verdicts():
    verdicts = {path.stem: json.loads(path.read_text())["verdict"] for path in SOURCES.glob("*.json")}
    expected = {
        "motionsites": "EXCLUDE",
        "bklit": "BUNDLE",
        "kokonutui": "BUNDLE",
        "21st-dev": "DIRECTORY",
        "prompt-motion": "DIRECTORY",
        "paperanimator": "DIRECTORY",
        "dqnamo": "DIRECTORY",
        "play-mint-gg": "RUNTIME",
        "buildcool": "DIRECTORY",
        "1of10": "RUNTIME",
        "spyglass": "RUNTIME",
        "lemo-opuscar": "DIRECTORY",
    }
    assert {key: verdicts[key] for key in expected} == expected


PAID_SFX_STORES = {
    "epidemic-sound", "artlist", "soundly", "splice", "boom-library", "a-sound-effect", "pro-sound-effects", "soundsnap", "envato-elements",
    "motion-array", "sonniss-shop",
}  # fmt: skip


def paid_store(id: str = "shop", **fields) -> dict:
    return source(id, **{"access": {"kind": "manual", "docs": "https://shop.example/", "key": "paid"}, "affiliate_url": None, **fields})


def test_paid_sound_stores_are_links_with_their_plain_address_and_an_empty_affiliate_slot():
    records = {path.stem: json.loads(path.read_text()) for path in SOURCES.glob("*.json")}
    for id in PAID_SFX_STORES:
        record = records[id]
        assert record["verdict"] == "DIRECTORY" and record["access"]["key"] == "paid" and "sfx" in record["classes"]
        assert record["affiliate_url"] is None, "a code goes in only when one is agreed, never invented"
        assert "carries no affiliate or referral code" in record["notes"] and not library.TRACKING.search(record["url"])
    assert not [id for id, record in records.items() if "affiliate_url" in record and record["access"]["key"] != "paid"]


@pytest.mark.parametrize("url", ["https://shop.example/?ref=abc", "https://shop.example/sfx?utm_source=altocue", "https://shop.example/?aff_id=9", "https://shop.example/#via=me"])
def test_a_referral_or_tracking_code_in_a_source_url_is_refused(url):
    with pytest.raises(AltocueError, match="tracking code") as caught:
        library.validate(catalog(sources=[paid_store(url=url)]))
    assert "affiliate_url" in caught.value.fix


def test_the_affiliate_slot_takes_only_an_https_link_and_only_on_a_paid_store():
    library.validate(catalog(sources=[paid_store(), paid_store(affiliate_url="https://shop.example/?ref=altocue")]))
    with pytest.raises(AltocueError, match="not an https"):
        library.validate(catalog(sources=[paid_store(affiliate_url="http://shop.example/?ref=altocue")]))
    with pytest.raises(AltocueError, match="only a paid store"):
        library.validate(catalog(sources=[source("free", affiliate_url="https://free.example/?ref=x")]))


def test_an_affiliate_link_reaches_the_catalog_only_when_set_and_show_names_it(bundled, monkeypatch, tmp_path, capsys):
    plain, coded = paid_store(), paid_store(id="coded", affiliate_url="https://shop.example/?ref=altocue")
    rows = build.source_entries([plain, coded])
    assert "affiliate_url" not in rows[0] and "no affiliate code" in rows[0]["ship"] and "paid" in rows[0]["tags"]
    assert rows[1]["affiliate_url"] == "https://shop.example/?ref=altocue" and rows[1]["url"] == coded["url"]
    path = tmp_path / "stores.json"
    path.write_text(json.dumps(catalog(*rows, sources=[plain, coded])))
    monkeypatch.setattr(library, "bundled_path", lambda: path)
    assert main(["library", "show", "coded"]) == 0
    assert "carries an affiliate code" in capsys.readouterr().out
    assert main(["library", "show", "shop"]) == 0
    assert "affiliate" not in capsys.readouterr().out.lower().replace("no affiliate code", "")


def test_an_offline_build_reuses_the_fetched_rows_and_moves_nothing_else():
    previous = json.loads(COMMITTED.read_text())
    fetched, refused, pins, _ = build.reused(previous)
    assert {row["source"] for row in fetched} == {"hyperframes", "gl-transitions"} and refused == previous["refused"] and pins == previous["pins"]
    records = {"hyperframes", "gl-transitions"} & {record["id"] for record in previous["sources"]}  # their own source rows are rebuilt, not reused
    assert len(fetched) == sum(row.get("source") in ("hyperframes", "gl-transitions") for row in previous["entries"]) - len(records)


# Fetching an item


def test_add_fetches_into_the_project_and_records_it(bundled, project, tmp_path, monkeypatch, capsys):
    folder = fake_renderer(tmp_path, monkeypatch, html=COMPOSITION)
    assert main(["library", "add", "headline-slam", "-p", str(project.root)]) == 0
    fetched = project.root / "motion/fetched/headline-slam.html"
    assert fetched.exists() and not (project.root / "hyperframes.lock.json").exists() and not (project.root / "compositions").exists()
    lock = json.loads((project.root / "library.lock.json").read_text())["items"]["headline-slam"]
    assert lock["licence"] == "Apache-2.0" and lock["version"] == "0.9.5" and lock["source"] == "https://example.com/item" and lock["gsap"] is True
    out = capsys.readouterr().out
    line = json.loads(out[out.index('{"type"') :].splitlines()[0])
    assert compositions.resolve(project, line)["own_id"] == "headline-slam"
    assert "GreenSock" in out and "standard licence" in out
    assert json.loads((folder / "config.json").read_text())["registry"].endswith("/v0.9.5/registry")


def fake_font_tool(folder: Path, css: str, fail: str = "") -> None:
    """Stands in for `hyperframes-localize-fonts`: HTML in, the same HTML with a deterministic font block out."""
    tool = folder / "renderer" / "node_modules" / ".bin" / "hyperframes-localize-fonts"
    reply = f'<style data-hyperframes-deterministic-fonts="true">{css}</style>'
    keep = f"pathlib.Path({str(folder / 'probe.html')!r}).write_text(page)"
    body = f"print({fail!r}, file=sys.stderr)\nraise SystemExit(1)" if fail else f"{keep}\nprint(page.replace('</head>', {reply!r} + '</head>'))"
    tool.write_text(f"#!{sys.executable}\nimport pathlib, sys\npage = sys.stdin.read()\n{body}\n")
    tool.chmod(tool.stat().st_mode | stat.S_IEXEC)


STYLED = COMPOSITION.replace("<template>", "<template><style>.w{font-family:var(--font-display, Inter, sans-serif)}</style>")
FACE = '@font-face { font-family: "Inter"; src: url("data:font/woff2;base64,AAAA") format("woff2"); }'


def test_add_embeds_the_faces_an_item_names_so_a_render_needs_no_font_from_the_web(bundled, project, tmp_path, monkeypatch, capsys):
    folder = fake_renderer(tmp_path, monkeypatch, html=STYLED)
    fake_font_tool(folder, FACE)
    assert main(["library", "add", "headline-slam", "-p", str(project.root)]) == 0
    page = (project.root / "motion" / "fetched" / "headline-slam.html").read_text()
    assert page.index("<template>") < page.index("data-altocue-fonts") < page.index(".w{font-family") and FACE in page
    probe = (folder / "probe.html").read_text()
    assert "font-family:var(--font-display, Inter" in probe and "Q" in probe and "\u00e9" in probe  # the item's styles, and Latin text to cut the faces to
    lock = json.loads((project.root / library.LOCK_FILE).read_text())["items"]["headline-slam"]
    assert lock["fonts_embedded"] == ["Inter"] and "no font from the web" in capsys.readouterr().out


def test_add_says_when_a_face_cannot_be_embedded(bundled, project, tmp_path, monkeypatch, capsys):
    folder = fake_renderer(tmp_path, monkeypatch, html=STYLED)
    fake_font_tool(folder, FACE, fail='font localization failed (FontFetchError): unresolved font families: "Odd Face"')
    assert main(["library", "add", "headline-slam", "-p", str(project.root)]) == 0
    lock = json.loads((project.root / library.LOCK_FILE).read_text())["items"]["headline-slam"]
    assert lock["fonts_embedded"] == [] and "Odd Face" in lock["fonts_left_to_the_web"] and "Odd Face" in capsys.readouterr().out


def test_add_never_runs_the_renderer_for_a_refused_name(bundled, project, tmp_path, monkeypatch, capsys):
    folder = fake_renderer(tmp_path, monkeypatch, html=COMPOSITION)
    assert main(["library", "add", "tiktok-follow", "-p", str(project.root)]) == 1
    assert "Its name references TikTok" in capsys.readouterr().err and not (folder / "calls.json").exists()


def test_add_says_when_a_name_is_not_in_the_library(bundled, project, tmp_path, monkeypatch, capsys):
    fake_renderer(tmp_path, monkeypatch, html=COMPOSITION)
    assert main(["library", "add", "nope", "-p", str(project.root)]) == 1
    assert "altocue library update" in capsys.readouterr().err


def test_add_takes_a_shader_as_text_and_needs_no_renderer(bundled, project, monkeypatch, capsys):
    from altocue.generate import http

    shader = b"// License: MIT\nvec4 transition(vec2 uv) { return getToColor(uv); }\n"
    monkeypatch.setattr(http, "call", lambda method, url, **kwargs: http.Response(200, shader))
    assert main(["library", "add", "gl-wind", "-p", str(project.root)]) == 0
    assert (project.root / "motion" / "fetched" / "gl-wind.glsl").read_bytes() == shader
    assert '"type": "gl-wind"' in capsys.readouterr().out


def test_add_refuses_a_snippet_and_says_why(bundled, project, tmp_path, monkeypatch, capsys):
    data = {**bundled, "entries": [*bundled["entries"], entry("vignette", tier="link")]}
    library.bundled_path().write_text(json.dumps(data))
    folder = fake_renderer(tmp_path, monkeypatch, html=COMPOSITION)
    assert main(["library", "add", "vignette", "-p", str(project.root)]) == 1
    assert "not a composition" in capsys.readouterr().err and not (folder / "calls.json").exists()


def test_add_needs_the_renderer(bundled, project, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ALTOCUE_RENDERER_DIR", str(tmp_path / "missing"))
    assert main(["library", "add", "headline-slam", "-p", str(project.root)]) == 1
    assert "altocue setup" in capsys.readouterr().err


def test_a_failed_fetch_leaves_nothing_behind(bundled, project, tmp_path, monkeypatch, capsys):
    fake_renderer(tmp_path, monkeypatch, html=COMPOSITION, fail=True)
    assert main(["library", "add", "headline-slam", "-p", str(project.root)]) == 1
    assert "registry unreachable" in capsys.readouterr().err
    assert not (project.root / "motion/fetched").exists() and not (project.root / "library.lock.json").exists()


def test_a_second_add_keeps_the_first_items_record(bundled, project, tmp_path, monkeypatch):
    fake_renderer(tmp_path, monkeypatch, html=COMPOSITION)
    (project.root / "library.lock.json").write_text(json.dumps({"schema": 1, "items": {"older": {"name": "older"}}}))
    assert main(["library", "add", "headline-slam", "-p", str(project.root)]) == 0
    assert set(json.loads((project.root / "library.lock.json").read_text())["items"]) == {"older", "headline-slam"}


def test_a_sample_id_in_a_comment_is_not_a_composition(bundled, project, tmp_path, monkeypatch, capsys):
    fake_renderer(tmp_path, monkeypatch, html='<!-- <div data-composition-id="sample"> --><div class="plain"></div>')
    assert main(["library", "add", "headline-slam", "-p", str(project.root)]) == 1
    assert "not a composition" in capsys.readouterr().err


def test_items_all_called_main_cannot_collide_on_the_stage(bundled, project, tmp_path, monkeypatch):
    html = """<template><div id="root" data-composition-id="main"><script>window.__timelines["main"] = tl; getElementById("main")</script></div></template>"""
    fake_renderer(tmp_path, monkeypatch, html=html)
    assert main(["library", "add", "headline-slam", "-p", str(project.root)]) == 0
    text = (project.root / "motion/fetched/headline-slam.html").read_text()
    assert 'data-composition-id="headline-slam"' in text and 'window.__timelines["headline-slam"]' in text and 'getElementById("main")' in text


def test_an_items_files_travel_with_it_and_their_paths_follow(bundled, project, tmp_path, monkeypatch):
    html = (
        '<html><body><template><div id="root" data-composition-id="headline-slam"><img src="assets/map.png">'
        "<script>new Image().src = './assets/map.png'; const base = 'assets/'; load('models/thing.glb')</script>"
        "<style>.a{background:url(assets/map.png)}</style></div></template></body></html>"
    )
    fake_renderer(tmp_path, monkeypatch, html=html, files={"assets/map.png": b"png", "models/thing.glb": b"glb", "assets/NOTICE.txt": b"notice"})
    assert main(["library", "add", "headline-slam", "-p", str(project.root)]) == 0
    text = (project.root / "motion/fetched/headline-slam.html").read_text()
    assert 'src="/compositions/headline-slam/assets/map.png"' in text and "url(/compositions/headline-slam/assets/map.png)" in text
    assert "'/compositions/headline-slam/assets/'" in text and "'/compositions/headline-slam/models/thing.glb'" in text
    assert (project.root / "motion/fetched/headline-slam/assets/NOTICE.txt").read_text() == "notice"
    item = compositions.resolve(project, {"type": "composition", "name": "headline-slam", "start": 0, "end": 1})
    stage = tmp_path / "stage"
    stage.mkdir()
    compositions.install([item], stage, tmp_path / "renderer")
    assert (stage / "compositions/headline-slam/assets/map.png").read_bytes() == b"png"


def test_an_item_in_its_own_folder_keeps_its_sibling_paths(bundled, project, tmp_path, monkeypatch):
    html = '<html><body><template><div id="root" data-composition-id="headline-slam"><script src="./assets/scene.js"></script></div></template></body></html>'
    fake_renderer(tmp_path, monkeypatch, html=html, target="compositions/headline-slam/headline-slam.html", files={"compositions/headline-slam/assets/scene.js": b"//"})
    assert main(["library", "add", "headline-slam", "-p", str(project.root)]) == 0
    assert 'src="/compositions/headline-slam/assets/scene.js"' in (project.root / "motion/fetched/headline-slam.html").read_text()
    assert (project.root / "motion/fetched/headline-slam/assets/scene.js").exists()


def test_files_beside_a_page_in_the_shared_folder_are_found_too(bundled, project, tmp_path, monkeypatch):
    html = """<template><div id="root" data-composition-id="headline-slam"><script>assetUrl("assets/fonts/a.json")</script></div></template>"""
    fake_renderer(tmp_path, monkeypatch, html=html, target="compositions/headline-slam.html", files={"compositions/assets/fonts/a.json": b"{}"})
    assert main(["library", "add", "headline-slam", "-p", str(project.root)]) == 0
    assert '"/compositions/headline-slam/assets/fonts/a.json"' in (project.root / "motion/fetched/headline-slam.html").read_text()
    assert (project.root / "motion/fetched/headline-slam/assets/fonts/a.json").exists()


def fake_web(pages: dict[str, bytes], seen: list[str]):
    def download(url: str, dest: Path, timeout: float = 300, limit: int = 0) -> Path:
        seen.append(url)
        if url not in pages:
            raise AltocueError(f"Could not download {url}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(pages[url])
        return dest

    return download


def test_what_an_item_loads_from_the_web_is_kept_locally_so_a_render_needs_no_network(project, tmp_path, monkeypatch):
    three = "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.min.js"
    esm = "https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/geometries/RoundedBoxGeometry.js/+esm"
    font = "https://fonts.googleapis.com/css2?family=Inter:wght@400&amp;display=swap"
    atlas = "https://cdn.jsdelivr.net/npm/us-atlas@3/states-10m.json"
    gsap = "https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"
    html = (
        f'<head><link rel="preconnect" href="https://fonts.gstatic.com"><link href="{font}" rel="stylesheet">'
        f'<script src="{gsap}"></script><script src="{three}"></script></head>'
        f'<svg xmlns="http://www.w3.org/2000/svg"></svg><script type="module">import x from "{esm}"; d3.json("{atlas}")</script>'
    )
    seen: list[str] = []
    pages = {
        three: b"// three",
        esm: b'import"/npm/three@0.160.0/+esm";',
        "https://cdn.jsdelivr.net/npm/three@0.160.0/+esm": b"// core",
        "https://fonts.googleapis.com/css2?family=Inter:wght@400&display=swap": b"@font-face{src:url(https://fonts.gstatic.com/s/inter/v20/a.ttf)}",
        "https://fonts.gstatic.com/s/inter/v20/a.ttf": b"ttf",
        atlas: b"{}",
    }
    monkeypatch.setattr(http, "download", fake_web(pages, seen))
    text, kept = compositions.localise(html, "card", project.root / "motion/fetched/card")
    assert "jsdelivr.net/npm/three" not in text and "us-atlas" not in text and "fonts.googleapis" not in text.replace('href="https://fonts.gstatic.com"', "")
    assert gsap in text, "GSAP is left for the renderer's own copy"
    assert gsap not in seen and "fonts.gstatic.com/s/inter" not in text and "<style>@font-face" in text
    assert set(pages) >= set(seen) and len(kept) == len(pages)
    nested = next(path for path in (project.root / "motion/fetched/card/vendor").iterdir() if b"import" in path.read_bytes())
    assert b"/npm/" not in nested.read_bytes() and b'"./' in nested.read_bytes()


def test_an_item_that_loads_from_an_unknown_host_is_refused_not_left_to_fail_offline(project):
    html = '<script src="https://tracker.example/x.js"></script>'
    with pytest.raises(AltocueError, match="tracker.example"):
        compositions.localise(html, "card", project.root / "motion/fetched/card")


def test_a_download_that_fails_mid_add_leaves_nothing_behind(bundled, project, tmp_path, monkeypatch, capsys):
    three = "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.min.js"
    html = f'<html><body><template><div id="root" data-composition-id="headline-slam"><script src="{three}"></script></div></template></body></html>'
    fake_renderer(tmp_path, monkeypatch, html=html)
    monkeypatch.setattr(http, "download", fake_web({}, []))
    assert main(["library", "add", "headline-slam", "-p", str(project.root)]) == 1
    assert not (project.root / "motion/fetched/headline-slam.html").exists() and not (project.root / "motion/fetched/headline-slam").exists()


# Updating


@pytest.fixture
def web(tmp_path, monkeypatch):
    """A real local server, so the download path runs for real."""
    folder = tmp_path / "web"
    folder.mkdir()

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(folder)))
    Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(library, "CATALOG_URL", f"http://127.0.0.1:{server.server_port}/catalog.json")
    yield folder / "catalog.json"
    server.shutdown()


def test_update_saves_a_good_catalog_and_search_prefers_it(bundled, web, capsys):
    web.write_text(json.dumps(catalog(entry("only-in-the-update", title="Update Wonder"), generated="2026-12-01T00:00:00Z")))
    assert main(["library", "update"]) == 0
    assert library.updated_path().exists()
    assert library.search(library.current(), "wonder")[0]["id"] == "only-in-the-update"


def test_an_older_download_loses_to_a_newer_bundled_catalog(bundled, home):
    path = library.updated_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(catalog(entry("stale-thing", title="Stale"), generated="2020-01-01T00:00:00Z")))
    assert library.search(library.current(), "stale") == []


def test_a_corrupt_download_keeps_the_old_catalog(bundled, web, capsys):
    good = catalog(entry("old-good", title="Old Good"), generated="2026-12-01T00:00:00Z")
    library.updated_path().parent.mkdir(parents=True)
    library.updated_path().write_text(json.dumps(good))
    web.write_text('{"schema": 1, "entries": [')
    assert main(["library", "update"]) == 1
    assert "still works" in capsys.readouterr().err
    assert json.loads(library.updated_path().read_text()) == good
    assert list(library.updated_path().parent.iterdir()) == [library.updated_path()]


def test_a_download_that_breaks_the_rules_keeps_the_old_catalog(bundled, web, capsys):
    web.write_text(json.dumps(catalog(entry("x", licence="GPL-3.0-only"))))
    assert main(["library", "update"]) == 1
    assert not library.updated_path().exists()
    assert library.search(library.current(), "headline")


def test_an_oversized_download_is_stopped(bundled, web, monkeypatch, capsys):
    monkeypatch.setattr(library, "MAX_CATALOG", 100)
    web.write_text(json.dumps(catalog(entry("big", summary="x" * 500))))
    assert main(["library", "update"]) == 1
    assert not library.updated_path().exists()


def test_an_unreachable_host_keeps_the_old_catalog(bundled, monkeypatch, capsys):
    monkeypatch.setattr(library, "CATALOG_URL", "http://127.0.0.1:9/catalog.json")
    assert main(["library", "update"]) == 1
    assert "still works" in capsys.readouterr().err


def test_a_damaged_download_on_disk_falls_back_to_the_bundled_copy(bundled):
    library.updated_path().parent.mkdir(parents=True)
    library.updated_path().write_text("not json")
    assert library.search(library.current(), "headline")


def test_a_catalog_from_a_newer_altocue_is_not_trusted():
    with pytest.raises(AltocueError, match="newer"):
        library.validate({**catalog(), "schema": 99})


def test_http_download_honours_a_limit(tmp_path, web):
    web.write_text("x" * 500)
    with pytest.raises(AltocueError, match="over"):
        http.download(library.CATALOG_URL, tmp_path / "out.json", limit=100)
    assert not list(tmp_path.glob("out.json*"))


# Offline


@pytest.mark.parametrize(
    "args",
    [["search", "headline"], ["search", "headline", "--json"], ["show", "headline-slam"], ["show", "tiktok-follow"], ["sources"], ["stats"]],
    ids=lambda a: " ".join(a),
)
def test_nothing_but_add_and_update_touches_the_network(bundled, no_network, args):
    assert main(["library", *args]) == 0


def test_search_on_the_real_catalog_finds_real_things(no_network, home, capsys):
    assert main(["library", "search", "lower", "third", "--tier", "fetch"]) == 0
    assert "lt-" in capsys.readouterr().out
