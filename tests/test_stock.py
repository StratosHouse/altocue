from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from argparse import Namespace
from dataclasses import fields
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

import numpy as np
import pytest
from canaries import draw_face
from PIL import Image, ImageDraw

from altocue import broll, cut, ffmpeg, frame, mix, render, stock
from altocue.errors import AltocueError
from altocue.generate import http
from altocue.models import Edl, Segment, Word
from altocue.project import Project, write_json
from altocue.qa import gates
from altocue.stock import archive, base, ccmixter, cleveland, credits, freesound, incompetech, met, music, nasa, pexels, pixabay, sfx, stills, wikimedia
from altocue.timeline import build as build_timeline

PNG = b"\x89PNG\r\n\x1a\n"


class Site(BaseHTTPRequestHandler):
    routes: dict[str, object] = {}
    seen: list[tuple[str, dict[str, str]]] = []

    def answer(self, send_body: bool) -> None:
        Site.seen.append((self.path, dict(self.headers)))
        parts = urlsplit(self.path)
        reply = next((value for prefix, value in sorted(Site.routes.items(), key=lambda kv: -len(kv[0])) if parts.path.startswith(prefix)), None)
        if callable(reply):
            reply = reply(parts.path, parse_qs(parts.query))
        status = 404 if reply is None else 200
        data = reply if isinstance(reply, bytes) else json.dumps({} if reply is None else reply).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if send_body:
            self.wfile.write(data)

    def do_GET(self):
        self.answer(True)

    def do_HEAD(self):
        self.answer(False)

    def log_message(self, *args):
        pass


@pytest.fixture
def site(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.delenv("FREESOUND_API_KEY", raising=False)
    Site.routes, Site.seen = {}, []
    server = HTTPServer(("127.0.0.1", 0), Site)
    Thread(target=server.serve_forever, daemon=True).start()
    root = f"http://127.0.0.1:{server.server_port}"
    for module, names in (
        (nasa, ("SEARCH", "ASSET")),
        (wikimedia, ("API",)),
        (met, ("SEARCH", "OBJECTS")),
        (cleveland, ("ARTWORKS", "WIKIDATA")),
        (pixabay, ("VIDEOS", "PHOTOS")),
        (pexels, ("VIDEOS", "PHOTOS")),
        (archive, ("SEARCH", "METADATA", "FILES")),
        (ccmixter, ("QUERY",)),
        (incompetech, ("PIECES", "FILES")),
        (freesound, ("SEARCH", "SOUND")),
    ):
        for name in names:
            monkeypatch.setattr(module, name, f"{root}/{module.__name__.rsplit('.', 1)[-1]}/{name.lower()}")
    yield SimpleNamespace(root=root, routes=Site.routes, seen=Site.seen)
    server.shutdown()


def script() -> list[Word]:
    text = (
        "So welcome back. The camera sits on a wooden desk near the window. "
        "Our ocean garden grows tomatoes and fresh basil every summer. "
        "Then the delivery truck brings boxes to the warehouse at sunrise. "
        "That is everything for today."
    ).split()
    return [Word(token, index * 0.34, index * 0.34 + 0.3) for index, token in enumerate(text)]


@pytest.fixture
def project(tmp_path, clip, monkeypatch) -> Project:
    made = Project.create(tmp_path / "video")
    (made.sources / "take.mp4").symlink_to(clip)
    probed = ffmpeg.probe_clip(made.sources / "take.mp4", clip_id="take")
    words = script()
    probed.duration = words[-1].end + 0.5
    made.save_clips([probed])
    made.save_words("take", words, "test")
    made.save_edl(Edl(fps=30, segments=[Segment(id="s1", clip="take", start=0.0, end=round(words[-1].end * 30) / 30)]))

    def mapped(project: Project, edl: Edl):
        return [{"text": w.text, "start": w.start, "end": w.end, "segment": "s1", "kind": "word"} for w in project.words("take")]

    monkeypatch.setattr(cut, "map_words", mapped)
    return made


def spot_with(project: Project, options: list[dict]) -> None:
    spots = [{"id": "b1", "start": 4.0, "end": 6.0, "phrase": "the ocean", "query": "ocean", "why": [], "options": options}]
    broll.number_options(spots)
    write_json(project.work / broll.SPOTS_FILE, {"cut": project.cut_mark(), "spots": spots})


def photo(path: Path, size: tuple[int, int] = (1600, 900), colour: str = "#225588") -> Path:
    Image.new("RGB", size, colour).save(path)
    return path


def video_file(path: Path, seconds: float = 3.0) -> Path:
    args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=30:duration={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(args, check=True)
    return path


def tone(path: Path, seconds: float = 1.0) -> Path:
    args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}", "-c:a", "libopus", "-b:a", "48k", str(path)]
    subprocess.run(args, check=True)
    return path


# The one shape


def test_a_result_has_exactly_the_fields_the_shape_promises():
    names = {field.name for field in fields(base.Result)}
    assert {"id", "title", "kind", "url", "preview", "duration", "width", "height", "licence", "licence_url", "credit", "source", "source_url"} <= names


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("CC0", "CC0-1.0"),
        ("cc-zero", "CC0-1.0"),
        ("CC0 (CC Zero)", "CC0-1.0"),
        ("http://creativecommons.org/publicdomain/zero/1.0/", "CC0-1.0"),
        ("Public domain", "Public domain"),
        ("pd", "Public domain"),
        ("https://creativecommons.org/publicdomain/mark/1.0/", "Public domain"),
        ("CC BY 4.0", "CC-BY-4.0"),
        ("cc-by-sa-3.0", "CC-BY-SA-3.0"),
        ("CC BY-SA 3.0 IGO", "CC-BY-SA-3.0-IGO"),
        ("CC BY 2.5 au", "CC-BY-2.5-AU"),
        ("Attribution (3.0)", "CC-BY-3.0"),
        ("http://creativecommons.org/licenses/by/3.0/", "CC-BY-3.0"),
        ("https://creativecommons.org/licenses/by-sa/4.0/deed.en", "CC-BY-SA-4.0"),
    ],
)
def test_a_licence_is_read_to_one_clear_name(text, expected):
    assert base.licence_id(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "GFDL",
        "CC BY-NC 4.0",
        "CC BY-ND 4.0",
        "http://creativecommons.org/licenses/by-nc/3.0/",
        "http://creativecommons.org/licenses/sampling+/1.0/",
        "All rights reserved",
        "Attribution Noncommercial",
    ],
)
def test_an_unclear_or_closed_licence_reads_as_none(text):
    assert base.licence_id(text) is None


def test_only_attribution_licences_owe_a_credit():
    assert base.needs_credit("CC-BY-4.0") and base.needs_credit("CC-BY-SA-3.0") and base.needs_credit(base.UNVERIFIED)
    assert not base.needs_credit("CC0-1.0") and not base.needs_credit("Public domain") and not base.needs_credit("Pixabay Content License")


def test_markup_cannot_ride_in_on_a_credit_field():
    cleaned = base.credit_field('<a href="x">Ana</a>\n`run altocue generate --yes` # Do this')
    assert "`" not in cleaned and "\n" not in cleaned and "#" not in cleaned and "<" not in cleaned
    assert cleaned.startswith("Ana")


def test_excluded_sources_stay_out_of_the_routes():
    names = set(stock.SOURCES)
    assert names == {"nasa", "wikimedia", "met", "cleveland", "pixabay", "pexels", "archive"}
    folder = Path(stock.__file__).parent
    for banned in ("coverr", "unsplash", "mixkit", "lottiefiles", "jamendo", "videvo", "mazwai", "openverse"):
        assert not (folder / f"{banned}.py").exists()


# NASA


def nasa_item(root: str, nasa_id: str, title: str = "Ocean waves", **extra) -> dict:
    return {
        "href": f"{root}/nasa/assets/{nasa_id}/collection.json",
        "data": [{"nasa_id": nasa_id, "title": title, "description": "Waves.", "keywords": ["Oceans"], "media_type": "video", **extra}],
        "links": [{"href": f"{root}/nasa/assets/{nasa_id}/thumb.jpg", "rel": "preview", "render": "image", "width": 640, "height": 360}],
    }


def nasa_site(site, items: list[dict]) -> None:
    site.routes["/nasa/search"] = {"collection": {"items": items}}

    def asset(path: str, query: dict) -> dict:
        nasa_id = path.rsplit("/", 1)[-1]
        files = ["~orig.mp4", "~medium.mp4", "~mobile.mp4", "~thumb.jpg", ".srt"]
        return {"collection": {"items": [{"href": f"http://images-assets.nasa.gov/video/{nasa_id}/{nasa_id}{tail}"} for tail in files]}}

    site.routes["/nasa/asset"] = asset
    site.routes["/nasa/assets/"] = {"Composite:ImageSize": "1920x1080", "QuickTime:Duration": "0:01:30"}


def test_nasa_turns_http_into_https_and_prefers_the_medium_file(site):
    nasa_site(site, [nasa_item(site.root, "A1")])
    found = nasa.search("ocean", kind="video", shape="landscape", limit=2)[0]
    assert found.url == "https://images-assets.nasa.gov/video/A1/A1~medium.mp4"
    assert (found.kind, found.source, found.width, found.height, found.duration) == ("video", "nasa", 1920, 1080, 90.0)
    assert found.licence == nasa.LICENCE and found.credit == "NASA" and found.source_url == "https://images.nasa.gov/details/A1"
    assert any("logos" in warning for warning in found.warnings)


def test_nasa_drops_anything_that_names_a_third_party(site):
    nasa_site(
        site,
        [
            nasa_item(site.root, "A1", title="Rocket, courtesy of a TV network"),
            nasa_item(site.root, "A2", description="Copyright 2019 someone"),
            nasa_item(site.root, "A3", keywords=["Reuters"]),
            nasa_item(site.root, "A4"),
        ],
    )
    assert [found.id for found in nasa.search("ocean", kind="video", shape="landscape", limit=4)] == ["A4"]


def test_nasa_leaves_out_hour_long_films(site):
    nasa_site(site, [nasa_item(site.root, "A1")])
    site.routes["/nasa/assets/"] = {"Composite:ImageSize": "1920x1080", "QuickTime:Duration": "0:54:24"}
    assert nasa.search("ocean", kind="video", shape="landscape", limit=2) == []


def test_nasa_photos_come_straight_from_the_listing(site):
    item = {
        "href": f"{site.root}/nasa/assets/P1/collection.json",
        "data": [{"nasa_id": "P1", "title": "Earth", "media_type": "image", "photographer": "NASA/Bill Ingalls"}],
        "links": [
            {"href": f"{site.root}/nasa/P1~small.jpg", "rel": "preview", "render": "image", "width": 640, "height": 360},
            {"href": f"{site.root}/nasa/P1~medium.jpg", "rel": "alternate", "render": "image", "width": 1280, "height": 720},
            {"href": f"{site.root}/nasa/P1~large.jpg", "rel": "alternate", "render": "image", "width": 1920, "height": 1080},
            {"href": f"{site.root}/nasa/P1~orig.jpg", "rel": "canonical", "render": "image", "width": 6000, "height": 3376},
        ],
    }
    nasa_site(site, [item])
    found = nasa.search("earth", kind="photo", shape="landscape", limit=1)[0]
    assert found.url.endswith("P1~large.jpg") and found.preview.endswith("P1~small.jpg")
    assert (found.width, found.height) == (1920, 1080) and found.credit == "NASA/Bill Ingalls"


# Wikimedia Commons


def commons_page(
    title: str, short: str, *, artist: str = "<a href='x'>Ana</a>", mime: str = "video/webm", size: int = 9_000_000, derivatives: list | None = None, **more
) -> dict:
    info = {
        "size": size,
        "width": 1920,
        "height": 1080,
        "duration": 20.0,
        "url": f"https://upload.wikimedia.org/x/{title}.webm?utm_source=commons",
        "thumburl": "https://thumb.wikimedia.org/x.jpg?utm_source=commons",
        "descriptionurl": f"https://commons.wikimedia.org/wiki/File:{title}.webm",
        "mime": mime,
        "derivatives": derivatives or [],
        "extmetadata": {
            "Artist": {"value": artist},
            "LicenseShortName": {"value": short},
            "LicenseUrl": {"value": "http://creativecommons.org/licenses/by/4.0/"},
            **more,
        },
    }
    return {"title": f"File:{title}.webm", "index": 1, "videoinfo": [info], "imageinfo": [info]}


def test_commons_asks_the_server_for_clear_licences_and_names_itself(site):
    site.routes["/wikimedia/api"] = {"query": {"pages": []}}
    wikimedia.search("ocean", kind="video", shape="landscape", limit=2)
    path, headers = site.seen[0]
    sent = parse_qs(urlsplit(path).query)["gsrsearch"][0]
    assert 'hastemplate:"Cc-zero|PD-Layout|Cc-by-layout|Cc-by-sa-layout"' in sent and "filetype:video" in sent and sent.startswith("ocean")
    assert headers["User-Agent"] == base.AGENT and "altocue" in headers["User-Agent"].lower() and "http" in headers["User-Agent"]


def test_commons_reads_the_licence_of_every_file_and_drops_the_unclear(site):
    pages = [
        commons_page("Public", "Public domain"),
        commons_page("Zero", "CC0"),
        commons_page("Credit", "CC BY 4.0"),
        commons_page("Share", "CC BY-SA 3.0"),
        commons_page("Doc", "GFDL"),
        commons_page("Closed", "CC BY-NC 4.0"),
        commons_page("Nameless", "CC BY 4.0", artist=""),
        {"title": "File:Empty.webm", "index": 9, "videoinfo": [{"mime": "video/webm", "extmetadata": {}}]},
    ]
    site.routes["/wikimedia/api"] = {"query": {"pages": pages}}
    found = {result.title: result for result in wikimedia.search("ocean", kind="video", shape="landscape", limit=8)}
    assert set(found) == {"Public", "Zero", "Credit", "Share"}
    assert found["Public"].licence == "Public domain" and found["Public"].credit == ""
    assert found["Zero"].licence == "CC0-1.0" and found["Zero"].credit == ""
    assert found["Credit"].credit == "Credit by Ana, CC BY 4.0, https://commons.wikimedia.org/wiki/File:Credit.webm"
    assert found["Share"].licence == "CC-BY-SA-3.0" and any("hare-alike" in warning for warning in found["Share"].warnings)


def test_commons_takes_a_transcode_near_1080_and_clean_urls(site):
    derivatives = [
        {"src": "https://upload.wikimedia.org/t/240.webm", "type": 'video/webm; codecs="vp9"', "width": "426", "height": "240"},
        {"src": "https://upload.wikimedia.org/t/360.mov", "type": 'video/quicktime; codecs="mp4v"', "width": "640", "height": "360"},
        {"src": "https://upload.wikimedia.org/t/1080.webm", "type": 'video/webm; codecs="vp9"', "width": "1920", "height": "1080"},
        {"src": "https://upload.wikimedia.org/t/2160.webm", "type": 'video/webm; codecs="vp9"', "width": "3840", "height": "2160"},
    ]
    site.routes["/wikimedia/api"] = {"query": {"pages": [commons_page("Wave", "CC0", size=500_000_000, derivatives=derivatives)]}}
    found = wikimedia.search("ocean", kind="video", shape="landscape", limit=1)[0]
    assert found.url == "https://upload.wikimedia.org/t/1080.webm" and found.preview == "https://thumb.wikimedia.org/x.jpg"


def test_commons_leaves_out_feature_films(site):
    page = commons_page("Feature", "Public domain")
    page["videoinfo"][0]["duration"] = 5152.9
    site.routes["/wikimedia/api"] = {"query": {"pages": [page]}}
    assert wikimedia.search("ocean", kind="video", shape="landscape", limit=1) == []


def test_commons_photos_use_the_scaled_file(site):
    page = commons_page("Rock", "CC BY 3.0", mime="image/jpeg")
    site.routes["/wikimedia/api"] = {"query": {"pages": [page]}}
    found = wikimedia.search("rock", kind="photo", shape="landscape", limit=1)[0]
    assert found.kind == "photo" and found.url == "https://thumb.wikimedia.org/x.jpg"
    assert "filetype:bitmap" in parse_qs(urlsplit(site.seen[0][0]).query)["gsrsearch"][0]


def test_commons_passes_on_a_restriction_as_a_warning(site):
    page = commons_page("Face", "CC0", Restrictions={"value": "personality"})
    site.routes["/wikimedia/api"] = {"query": {"pages": [page]}}
    assert any("personality" in warning for warning in wikimedia.search("x", kind="video", shape="landscape", limit=1)[0].warnings)


# Museums


def test_met_keeps_only_objects_marked_public_domain(site):
    site.routes["/met/search"] = {"total": 3, "objectIDs": [1, 2, 3]}

    def item(path: str, query: dict) -> dict:
        number = int(path.rsplit("/", 1)[-1])
        return {
            "objectID": number,
            "isPublicDomain": number != 1,
            "primaryImage": "" if number == 2 else "https://images.metmuseum.org/full.jpg",
            "primaryImageSmall": "https://images.metmuseum.org/small.jpg",
            "title": "The Sea",
            "artistDisplayName": "A Painter",
            "objectURL": f"https://www.metmuseum.org/art/collection/search/{number}",
        }

    site.routes["/met/objects"] = item
    found = met.search("ocean", kind="photo", shape="portrait", limit=4)
    assert [result.id for result in found] == ["3"]
    assert found[0].licence == "CC0-1.0" and found[0].credit == "" and found[0].url == "https://images.metmuseum.org/full.jpg"
    sent = parse_qs(urlsplit(site.seen[0][0]).query)
    assert sent["isPublicDomain"] == ["true"] and sent["hasImages"] == ["true"]


def cleveland_art(number: int, status: str = "CC0") -> dict:
    return {
        "id": number,
        "accession_number": f"1915.{number}",
        "title": "Sea Cliffs",
        "url": f"https://clevelandart.org/art/1915.{number}",
        "share_license_status": status,
        "images": {
            "web": {"url": "https://openaccess-cdn.clevelandart.org/w.jpg", "width": "748", "height": "893"},
            "print": {"url": "https://openaccess-cdn.clevelandart.org/p.jpg", "width": "2849", "height": "3400"},
            "full": {"url": "https://openaccess-cdn.clevelandart.org/f.tif", "width": "4609", "height": "5500"},
        },
        "creators": [{"description": "A Painter (American, 1800-1900)", "role": "artist"}],
    }


def test_cleveland_keeps_only_cc0_and_avoids_the_tiff(site):
    site.routes["/cleveland/artworks"] = {"info": {"total": 2}, "data": [cleveland_art(1), cleveland_art(2, "Copyrighted"), {**cleveland_art(3), "images": None}]}
    found = cleveland.search("ocean", kind="photo", shape="portrait", limit=4)
    assert [result.id for result in found] == ["1915.1"]
    assert found[0].url.endswith("p.jpg") and found[0].preview.endswith("w.jpg") and found[0].licence == "CC0-1.0" and found[0].credit == ""
    assert (found[0].width, found[0].height) == (2849, 3400) and found[0].author == "A Painter (American, 1800-1900)"
    sent = parse_qs(urlsplit(site.seen[0][0]).query)
    assert sent["cc0"] == ["1"] and sent["has_image"] == ["1"] and sent["q"] == ["ocean"]


def test_cleveland_finds_works_through_wikidata_when_its_own_search_is_empty(site):
    def artworks(path: str, query: dict) -> dict:
        if path.endswith("/1976.13"):
            return {"data": cleveland_art(13)}
        if path.endswith("/1980.1"):
            return {"data": cleveland_art(1, "Copyrighted")}
        return {"info": {"total": 0}, "data": []}

    site.routes["/cleveland/artworks"] = artworks
    site.routes["/cleveland/wikidata"] = {"results": {"bindings": [{"acc": {"value": "1976.13"}}, {"acc": {"value": "1980.1"}}]}}
    found = cleveland.search('ocean" } #', kind="photo", shape="landscape", limit=4)
    assert [result.id for result in found] == ["1915.13"]
    asked = next(unquote(path) for path, _ in site.seen if path.startswith("/cleveland/wikidata"))
    assert 'CONTAINS(LCASE(?label),+"ocean"))' in asked and "#" not in asked


# Pixabay and Pexels


def pixabay_hit(number: int, wide: int, tall: int, *, large: bool = True) -> dict:
    videos = {
        "large": {
            "url": f"https://cdn.pixabay.com/{number}_large.mp4" if large else "",
            "width": wide * 2 if large else 0,
            "height": tall * 2 if large else 0,
            "thumbnail": "https://cdn.pixabay.com/t.jpg",
        },
        "medium": {"url": f"https://cdn.pixabay.com/{number}_medium.mp4", "width": wide, "height": tall, "thumbnail": "https://cdn.pixabay.com/t.jpg"},
    }
    return {"id": number, "pageURL": f"https://pixabay.com/videos/{number}", "user": "Ben", "duration": 12, "videos": videos}


def test_pixabay_ranks_tall_clips_first_for_a_portrait_project(site):
    site.routes["/pixabay/videos"] = {"hits": [pixabay_hit(1, 1920, 1080), pixabay_hit(2, 1080, 1920)]}
    found = pixabay.search("truck", kind="video", shape="portrait", limit=4, key="k")
    assert [result.id for result in found] == ["2", "1"]
    assert parse_qs(urlsplit(site.seen[0][0]).query)["min_height"]


def test_pixabay_ranks_wide_clips_first_for_a_landscape_project(site):
    site.routes["/pixabay/videos"] = {"hits": [pixabay_hit(2, 1080, 1920), pixabay_hit(1, 1920, 1080)]}
    assert [result.id for result in pixabay.search("truck", kind="video", shape="landscape", limit=4, key="k")] == ["1", "2"]


def test_pixabay_takes_the_smallest_file_that_fills_the_canvas(site):
    site.routes["/pixabay/videos"] = {"hits": [pixabay_hit(1, 1920, 1080), pixabay_hit(2, 1080, 1920), pixabay_hit(3, 1920, 1080, large=False)]}
    found = {result.id: result for result in pixabay.search("truck", kind="video", shape="portrait", limit=4, key="k")}
    assert found["1"].url.endswith("1_large.mp4")  # 1080 tall cannot fill 1920 without an upscale
    assert found["2"].url.endswith("2_medium.mp4")
    assert found["3"].url.endswith("3_medium.mp4")  # the best there is


def test_a_pixabay_search_stays_cached_for_a_day_per_shape(site):
    site.routes["/pixabay/videos"] = {"hits": [pixabay_hit(1, 1920, 1080)]}
    pixabay.search("truck", kind="video", shape="portrait", limit=4, key="k")
    pixabay.search("truck", kind="video", shape="portrait", limit=2, key="k")
    assert len(site.seen) == 1


def test_pixabay_photos_ask_for_the_shape(site):
    site.routes["/pixabay/photos"] = {
        "hits": [
            {
                "id": 5,
                "pageURL": "https://pixabay.com/p/5",
                "user": "Ben",
                "largeImageURL": "https://cdn.pixabay.com/l.jpg",
                "webformatURL": "https://cdn.pixabay.com/w.jpg",
                "imageWidth": 4000,
                "imageHeight": 6000,
            }
        ]
    }
    found = pixabay.search("truck", kind="photo", shape="portrait", limit=1, key="k")[0]
    assert found.kind == "photo" and found.url.endswith("l.jpg") and found.licence == pixabay.LICENCE and found.credit == ""
    assert parse_qs(urlsplit(site.seen[0][0]).query)["orientation"] == ["vertical"]


def test_pexels_keeps_its_licence_and_photos_work(site):
    site.routes["/pexels/photos"] = {
        "photos": [
            {
                "id": 4,
                "url": "https://www.pexels.com/photo/4",
                "photographer": "Ana",
                "width": 4000,
                "height": 6000,
                "src": {"large2x": "https://images.pexels.com/l.jpg", "medium": "https://images.pexels.com/m.jpg"},
            }
        ]
    }
    found = pexels.search("desk", kind="photo", shape="portrait", limit=1, key="secret")[0]
    assert found.url.endswith("l.jpg") and found.licence == pexels.LICENCE and found.author == "Ana"
    assert site.seen[0][1]["Authorization"] == "secret"


# The Internet Archive stays behind a warning


def archive_site(site) -> None:
    site.routes["/archive/search"] = {
        "response": {
            "docs": [
                {"identifier": "good", "title": "Harbour", "creator": "Ana", "licenseurl": "http://creativecommons.org/publicdomain/mark/1.0/"},
                {"identifier": "closed", "title": "Closed", "licenseurl": "http://creativecommons.org/licenses/by-nc/3.0/"},
                {"identifier": "none", "title": "Nothing"},
            ]
        }
    }
    site.routes["/archive/metadata/good"] = {
        "files": [
            {"name": "h.mpg", "format": "MPEG2", "size": "90000000", "length": "60.0", "source": "original"},
            {"name": "h.mp4", "format": "h.264", "size": "9000000", "length": "60.0", "width": "640", "height": "480", "source": "derivative"},
        ]
    }


def test_archive_results_never_carry_a_trusted_licence(site):
    archive_site(site)
    found = archive.search("harbour", kind="video", shape="landscape", limit=4)
    assert r"licenseurl:*licenses\/by-sa\/*" in parse_qs(urlsplit(site.seen[0][0]).query)["q"][0]
    assert [result.id for result in found] == ["good"]
    assert found[0].licence == base.UNVERIFIED and found[0].credit and archive.WARNING in found[0].warnings
    assert found[0].url.endswith("/good/h.mp4")


def test_archive_never_joins_a_default_route():
    plan = stock.plan("auto", None, {"PIXABAY_API_KEY": "k", "PEXELS_API_KEY": "k"})
    assert all(name != "archive" for name, _ in plan)


def test_an_archive_pick_needs_a_person_to_have_checked_the_rights(project, tmp_path, site):
    clip = video_file(tmp_path / "clip.mp4")
    site.routes["/file.mp4"] = clip.read_bytes()
    option = {
        "source": "archive",
        "kind": "video",
        "url": f"{site.root}/file.mp4",
        "title": "Harbour",
        "licence": base.UNVERIFIED,
        "licence_url": "",
        "credit": "Harbour by Ana",
        "page": "https://archive.org/details/good",
    }
    spot_with(project, [option])
    with pytest.raises(AltocueError, match="cannot be trusted") as caught:
        broll.apply_picks(project, ["b1=c1"])
    assert "--rights-checked" in caught.value.fix
    assert broll.apply_picks(project, ["b1=c1"], rights_checked=True)[0]["licence"] == base.UNVERIFIED


# Routes through broll


def test_auto_with_no_keys_tries_the_no_key_sources():
    plan = stock.plan("auto", None, {})
    assert plan[:2] == [("nasa", "video"), ("wikimedia", "video")]
    assert ("met", "photo") in plan and ("cleveland", "photo") in plan and all(name not in {"pixabay", "pexels"} for name, _ in plan)


def test_auto_leads_with_pixabay_when_there_is_a_key():
    assert stock.plan("auto", None, {"PIXABAY_API_KEY": "k"})[0] == ("pixabay", "video")


def test_one_named_source_fills_with_its_photos_after_its_video():
    assert stock.plan("nasa", None, {}) == [("nasa", "video"), ("nasa", "photo")]
    assert stock.plan("nasa", "photo", {}) == [("nasa", "photo")]
    assert stock.plan("met", None, {}) == [("met", "photo")]


def found(name: str, number: int, kind: str = "video", licence: str = "CC0-1.0") -> base.Result:
    return base.Result(
        str(number),
        f"{name} {number}",
        kind,
        f"https://x.test/{name}{number}.mp4",
        "",
        9.0,
        1920,
        1080,
        licence,
        "https://x.test/l",
        "",
        name,
        f"https://x.test/{number}",
    )


def test_auto_moves_past_a_source_that_fails(project, monkeypatch, capsys):
    def seek(name, query, **_):
        if name == "nasa":
            raise http.Unreachable("Could not reach nasa")
        return [found(name, 1)]

    monkeypatch.setattr(stock, "search", seek)
    spots = [{"query": "ocean", "options": []}]
    broll.search(project, spots, "auto")
    assert [option["source"] for option in spots[0]["options"]][:1] == ["wikimedia"]
    assert "nasa" in capsys.readouterr().out


def test_auto_says_so_when_nothing_answers(project, monkeypatch):
    def seek(name, query, **_):
        raise http.Unreachable("down")

    monkeypatch.setattr(stock, "search", seek)
    with pytest.raises(AltocueError, match="No stock source answered"):
        broll.search(project, [{"query": "ocean", "options": []}], "auto")


def test_a_named_source_that_fails_says_so(project, monkeypatch):
    def seek(name, query, **_):
        raise AltocueError("NASA answered 500.")

    monkeypatch.setattr(stock, "search", seek)
    with pytest.raises(AltocueError, match="NASA answered 500"):
        broll.search(project, [{"query": "ocean", "options": []}], "nasa")


def test_a_query_with_no_hits_narrows_to_fewer_words(project, monkeypatch):
    asked = []

    def seek(name, query, **_):
        asked.append(query)
        return [found(name, 1)] if query == "delivery" else []

    monkeypatch.setattr(stock, "search", seek)
    spots = [{"query": "delivery brings truck", "options": []}]
    broll.search(project, spots, "nasa")
    assert asked[0] == "delivery brings truck" and asked[-1] == "delivery" and spots[0]["options"]


def test_a_search_replaces_only_what_that_source_found_before(project, monkeypatch):
    monkeypatch.setattr(stock, "search", lambda name, query, **_: [found(name, 1)])
    spots = [{"query": "ocean", "options": [{"source": "library", "file": "x.mp4", "title": "x"}, {"source": "nasa", "url": "old", "title": "old"}]}]
    broll.search(project, spots, "nasa")
    assert spots[0]["options"][0]["source"] == "library" and "old" not in [option["title"] for option in spots[0]["options"]]


def test_a_fresh_install_can_fill_spots_with_no_key(project, site, capsys):
    nasa_site(site, [nasa_item(site.root, "A1")])
    site.routes["/nasa/assets/A1/thumb.jpg"] = b"not a picture"
    args = Namespace(project=str(project.root), library=None, search="auto", pick=None, share=None)
    assert broll.run(args) == 0
    spots = json.loads((project.work / broll.SPOTS_FILE).read_text())["spots"]
    assert spots and all(spot["options"][0]["source"] == "nasa" for spot in spots if spot["query"])
    review = (project.work / "broll-review.md").read_text()
    assert broll.UNTRUSTED_NOTE in review and nasa.LICENCE in review and "credit" in review.lower()


def test_the_review_line_shows_kind_licence_and_credit():
    line = broll.option_line(
        {
            "id": "c1",
            "source": "wikimedia",
            "kind": "photo",
            "title": "Rock",
            "author": "Ana",
            "licence": "CC-BY-4.0",
            "credit": "Rock by Ana, CC BY 4.0",
            "page": "https://x.test/a",
        }
    )
    assert "photo" in line and "CC-BY-4.0" in line and "credit owed" in line.lower()


def test_a_stock_option_with_no_clear_licence_never_reaches_the_project(project, tmp_path, site):
    clip = video_file(tmp_path / "clip.mp4")
    site.routes["/file.mp4"] = clip.read_bytes()
    spot_with(project, [{"source": "wikimedia", "kind": "video", "url": f"{site.root}/file.mp4", "title": "Unclear", "licence": ""}])
    with pytest.raises(AltocueError, match="no clear licence"):
        broll.apply_picks(project, ["b1=c1"])
    assert not list((project.work / "broll").rglob("*.mp4")) and not (project.root / credits.FILE).exists()


def test_an_excluded_source_written_into_spots_is_refused(project, tmp_path, site):
    clip = video_file(tmp_path / "clip.mp4")
    site.routes["/file.mp4"] = clip.read_bytes()
    spot_with(project, [{"source": "coverr", "kind": "video", "url": f"{site.root}/file.mp4", "title": "X", "licence": "CC0-1.0"}])
    with pytest.raises(AltocueError, match="no clear licence"):
        broll.apply_picks(project, ["b1=c1"])


def test_a_picked_webm_is_kept_as_webm_and_accepted(project, tmp_path, site):
    args = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=640x360:rate=30:duration=3",
        "-c:v",
        "libvpx-vp9",
        "-b:v",
        "200k",
        "-deadline",
        "realtime",
        "-cpu-used",
        "8",
        str(tmp_path / "c.webm"),
    ]
    subprocess.run(args, check=True)
    site.routes["/wave.webm"] = (tmp_path / "c.webm").read_bytes()
    spot_with(
        project,
        [
            {
                "source": "wikimedia",
                "kind": "video",
                "url": f"{site.root}/wave.webm",
                "title": "Wave",
                "licence": "CC0-1.0",
                "licence_url": "https://x.test",
                "credit": "",
                "page": "https://x.test/p",
            }
        ],
    )
    inserts = broll.apply_picks(project, ["b1=c1"])
    assert inserts[0]["source_file"].endswith(".webm")
    assert (project.root / inserts[0]["file"]).exists()


def test_an_ogg_video_is_a_video():
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "old.ogv"
        path.write_bytes(b"OggS" + bytes(20))
        broll.require_video(path)
        assert path.exists()


def test_pixabay_stays_callable_from_broll_with_the_old_shape(site):
    site.routes["/pixabay/videos"] = {"hits": [pixabay_hit(1, 1920, 1080)]}
    options = pixabay.search("truck", kind="video", shape="landscape", limit=4, key="k")
    assert options[0].source == "pixabay"
    assert stock.option(options[0])["page"] == "https://pixabay.com/videos/1"


# Stills as B-roll


def frames_of(path: Path, folder: Path) -> list[Path]:
    folder.mkdir(exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), str(folder / "%03d.png")], check=True)
    return sorted(folder.glob("*.png"))


def test_a_photo_becomes_a_push_in_of_the_exact_length_and_size(tmp_path):
    source = photo(tmp_path / "wide.jpg")
    canvas = Image.open(source)
    ImageDraw.Draw(canvas).rectangle((700, 350, 900, 550), fill="#ffffff")
    canvas.save(source)
    dest = stills.push_in(source, tmp_path / "out.mp4", (540, 960), 30, 60)
    info = ffmpeg.probe_clip(dest)
    assert (info.width, info.height) == (540, 960)
    video = next(s for s in ffmpeg.probe_json(dest)["streams"] if s["codec_type"] == "video")
    assert int(video["nb_frames"]) == 60 and video["pix_fmt"] == "yuv420p" and video.get("color_space") == "bt709"
    shots = frames_of(dest, tmp_path / "frames")
    first, last = np.asarray(Image.open(shots[0]).convert("L"), float), np.asarray(Image.open(shots[-1]).convert("L"), float)
    assert (last > 200).sum() > (first > 200).sum() * 1.05  # it moves, and it moves inward
    assert not np.allclose(first, last, atol=2)


@pytest.mark.parametrize("canvas", [(540, 960), (960, 540), (540, 540)])
def test_a_photo_keeps_its_aspect(tmp_path, canvas):
    source = tmp_path / "ring.png"
    picture = Image.new("RGB", (2400, 1600), "#101820")
    ImageDraw.Draw(picture).ellipse((1000, 600, 1400, 1000), fill="#ffffff")
    picture.save(source)
    dest = stills.push_in(source, tmp_path / "out.mp4", canvas, 30, 30)
    for shot in frames_of(dest, tmp_path / "frames")[::29]:
        pixels = np.asarray(Image.open(shot).convert("L")) > 128
        wide, tall = np.flatnonzero(pixels.any(axis=0)), np.flatnonzero(pixels.any(axis=1))
        assert (wide[-1] - wide[0]) / (tall[-1] - tall[0]) == pytest.approx(1.0, abs=0.03)


def face_photo(path: Path, width: int = 3000, height: int = 1920, x: int = 1900) -> Path:
    plate = Image.new("RGBA", (1080, 1920), (40, 50, 60, 255))
    draw_face(plate)
    picture = Image.new("RGB", (width, height), (40, 50, 60))
    picture.paste(plate.convert("RGB"), (x, height - 1920))
    picture.save(path)
    return path


def test_a_photo_crops_to_the_face_and_keeps_it_through_the_push(tmp_path):
    source = face_photo(tmp_path / "group.png")
    assert frame.detect_faces(np.asarray(Image.open(source).convert("RGB")))
    dest = stills.push_in(source, tmp_path / "out.mp4", (540, 960), 30, 45)
    for shot in frames_of(dest, tmp_path / "frames")[::44]:
        pixels = np.asarray(Image.open(shot).convert("RGB"))
        faces = frame.detect_faces(pixels)
        assert faces, "the face left the frame"
        assert faces[0].x > 0 and faces[0].x + faces[0].w < pixels.shape[1] and faces[0].y > 0


def test_a_photo_stored_on_its_side_stands_up(tmp_path):
    source = tmp_path / "side.jpg"
    picture = Image.new("RGB", (800, 400), "#000000")
    ImageDraw.Draw(picture).rectangle((0, 0, 400, 400), fill="#ffffff")
    exif = Image.Exif()
    exif[0x0112] = 6  # stored rotated, displayed a quarter turn clockwise
    picture.save(source, exif=exif, quality=95)
    dest = stills.push_in(source, tmp_path / "out.mp4", (400, 800), 30, 2)
    top = np.asarray(Image.open(frames_of(dest, tmp_path / "frames")[0]).convert("L"), float)
    assert top[: len(top) // 3].mean() > 200 and top[-len(top) // 3 :].mean() < 60


def test_a_download_that_is_not_a_picture_is_thrown_away(tmp_path):
    fake = tmp_path / "x.jpg"
    fake.write_text("#EXTM3U\nfile:///etc/passwd\n")
    with pytest.raises(AltocueError, match="not a picture"):
        stills.require_picture(fake)
    assert not fake.exists()


def test_a_picked_photo_fills_a_spot_and_reaches_the_timeline(project, tmp_path, site):
    site.routes["/photo.jpg"] = photo(tmp_path / "p.jpg").read_bytes()
    option = {
        "source": "met",
        "kind": "photo",
        "url": f"{site.root}/photo.jpg",
        "title": "The Sea",
        "licence": "CC0-1.0",
        "licence_url": "https://x.test",
        "credit": "",
        "page": "https://x.test/1",
    }
    spot_with(project, [option])
    inserts = broll.apply_picks(project, ["b1=c1"])
    made = ffmpeg.probe_clip(project.root / inserts[0]["file"])
    assert (made.width, made.height) == (1080, 1920)
    video = next(s for s in ffmpeg.probe_json(project.root / inserts[0]["file"])["streams"] if s["codec_type"] == "video")
    assert int(video["nb_frames"]) == 60
    assert build_timeline(project)["inserts"][0]["file"] == inserts[0]["file"]


def test_your_own_photo_can_fill_a_spot_too(project, tmp_path):
    source = photo(tmp_path / "mine.png")
    spot_with(project, [])
    inserts = broll.apply_picks(project, [f"b1=file:{source}"])
    assert (project.root / inserts[0]["file"]).suffix == ".mp4" and ffmpeg.probe_clip(project.root / inserts[0]["file"]).width == 1080


# Music


def ccmixter_row(number: int, url: str, name: str = "Attribution (3.0)", **extra) -> dict:
    return {
        "upload_id": number,
        "upload_name": "Night Tide",
        "user_name": "ana",
        "user_real_name": "Ana",
        "file_page_url": f"https://ccmixter.org/files/ana/{number}",
        "license_name": name,
        "license_url": url,
        "upload_extra": {"featuring": "Ben", "nsfw": False, **extra},
        "files": [
            {"file_format_info": {"format-name": "audio-flac", "ps": "2:45"}, "download_url": "https://ccmixter.org/content/a.flac", "file_rawsize": 40000000},
            {"file_format_info": {"format-name": "audio-mp3-mp3", "ps": "2:45"}, "download_url": "https://ccmixter.org/content/a.mp3", "file_rawsize": 4000000},
        ],
    }


def test_ccmixter_keeps_cc_by_and_public_domain_only(site):
    def query(path: str, sent: dict) -> list:
        if sent["lic"] == ["by"]:
            return [
                ccmixter_row(1, "http://creativecommons.org/licenses/by/3.0/"),
                ccmixter_row(2, "http://creativecommons.org/licenses/by-nc/3.0/", "Attribution Noncommercial (3.0)"),
                ccmixter_row(3, "http://creativecommons.org/licenses/sampling+/1.0/", "Sampling Plus"),
                ccmixter_row(4, "http://creativecommons.org/licenses/by/3.0/", nsfw=True),
            ]
        return [ccmixter_row(5, "http://creativecommons.org/publicdomain/zero/1.0/", "CC0 (CC Zero)")]

    site.routes["/ccmixter/query"] = query
    results = {result.id: result for result in ccmixter.search("night", limit=8)}
    assert set(results) == {"1", "5"}
    assert results["1"].licence == "CC-BY-3.0" and results["1"].url.endswith("a.mp3") and results["1"].duration == 165.0
    assert results["1"].credit == "Night Tide by Ana, feat. Ben, CC BY 3.0, https://ccmixter.org/files/ana/1"
    assert results["5"].licence == "CC0-1.0" and results["5"].credit == ""
    assert results["1"].kind == "audio"


PIECES = [
    {
        "title": "Ocean Drift",
        "filename": "Ocean Drift.mp3",
        "length": "00:02:10",
        "instruments": "Piano",
        "genre": "Ambient",
        "description": "Calm water.",
        "feel": "Calm, Relaxed",
        "bpm": "60",
    },
    {
        "title": "City Run",
        "filename": "City Run.mp3",
        "length": "00:01:00",
        "instruments": "Drums",
        "genre": "Rock",
        "description": "Fast.",
        "feel": "Driving",
        "bpm": "140",
    },
]


def test_incompetech_carries_its_fixed_credit(site):
    site.routes["/incompetech/pieces"] = PIECES
    results = incompetech.search("calm ocean", limit=4)
    assert [result.title for result in results] == ["Ocean Drift"]
    assert results[0].licence == "CC-BY-4.0" and results[0].duration == 130.0
    assert results[0].url.endswith("/Ocean%20Drift.mp3")
    assert (
        results[0].credit
        == "Ocean Drift Kevin MacLeod (incompetech.com)\nLicensed under Creative Commons: By Attribution 4.0\nhttps://creativecommons.org/licenses/by/4.0/"
    )


def test_incompetech_reads_its_list_once_a_day(site):
    site.routes["/incompetech/pieces"] = PIECES
    incompetech.search("calm", limit=1)
    incompetech.search("fast", limit=1)
    assert len(site.seen) == 1


def run_music(capsys, *argv: str) -> str:
    from altocue import cli

    assert cli.main(["music", *argv]) == 0
    return capsys.readouterr().out


def test_music_search_shows_licence_and_credit_before_anything_downloads(site, capsys):
    site.routes["/ccmixter/query"] = lambda path, sent: [ccmixter_row(1, "http://creativecommons.org/licenses/by/3.0/")] if sent["lic"] == ["by"] else []
    site.routes["/incompetech/pieces"] = PIECES
    out = run_music(capsys, "search", "calm", "night")
    assert base.UNTRUSTED_NOTE in out
    assert "ccmixter:1" in out and "CC-BY-3.0" in out and "Night Tide by Ana, feat. Ben, CC BY 3.0" in out
    assert "incompetech:ocean-drift" in out and "Kevin MacLeod (incompetech.com)" in out
    assert not any(path.endswith((".mp3", ".ogg")) for path, _ in site.seen)


def test_music_search_finds_the_bundled_stings_with_no_network(site, capsys):
    site.routes["/ccmixter/query"] = lambda path, sent: []
    site.routes["/incompetech/pieces"] = []
    out = run_music(capsys, "search", "sax")
    assert "kenney:jingle-sax" in out and "CC0-1.0" in out


def test_music_search_survives_a_source_that_fails(site, capsys, monkeypatch):
    def down(*args, **kwargs):
        raise http.Unreachable("Could not reach ccmixter.org")

    monkeypatch.setattr(ccmixter, "search", down)
    site.routes["/incompetech/pieces"] = PIECES
    out = run_music(capsys, "search", "calm")
    assert "incompetech:ocean-drift" in out and "ccmixter" in out


def test_music_get_lands_in_the_music_folder_and_motion_points_at_it(project, site, tmp_path, capsys):
    site.routes["/incompetech/pieces"] = PIECES
    site.routes["/incompetech/files"] = tone(tmp_path / "x.ogg").read_bytes()
    (project.root / "motion.json").write_text(json.dumps({"items": [{"type": "chip", "text": "x", "start": 1.0}], "sfx": True}))
    run_music(capsys, "get", "incompetech:ocean-drift", "-p", str(project.root))
    motion = json.loads((project.root / "motion.json").read_text())
    assert motion["music"]["path"].startswith("music/") and motion["items"] and motion["sfx"] is True
    assert (project.root / motion["music"]["path"]).exists()
    record = credits.records(project)[0]
    assert record["file"] == motion["music"]["path"] and record["licence"] == "CC-BY-4.0" and "Kevin MacLeod" in record["credit"]


def test_a_ccmixter_download_names_the_page_it_came_from(project, site, tmp_path):
    row = ccmixter_row(1, "http://creativecommons.org/licenses/by/3.0/")
    row["files"][1]["download_url"] = f"{site.root}/content/a.mp3"
    site.routes["/ccmixter/query"] = [row]
    site.routes["/content/"] = tone(tmp_path / "x.ogg").read_bytes()
    music.fetch(project, "ccmixter:1")
    asked = next(headers for path, headers in site.seen if path == "/content/a.mp3")
    assert asked["Referer"] == "https://ccmixter.org/files/ana/1"
    assert (project.root / "music" / "ccmixter-1.mp3").exists()


def test_music_get_of_a_bundled_sting_warns_that_music_loops(project, capsys):
    out = run_music(capsys, "get", "kenney:jingle-sax", "-p", str(project.root))
    motion = json.loads((project.root / "motion.json").read_text())
    assert motion["music"]["path"] == "music/kenney-jingle-sax.ogg" and (project.root / motion["music"]["path"]).exists()
    assert "loops" in out
    assert credits.records(project)[0]["licence"] == "CC0-1.0"


def test_music_get_can_leave_the_bed_alone(project, capsys):
    (project.root / "motion.json").write_text(json.dumps({"music": {"path": "music/mine.mp3"}}))
    run_music(capsys, "get", "kenney:jingle-sax", "-p", str(project.root), "--no-bed")
    assert json.loads((project.root / "motion.json").read_text())["music"]["path"] == "music/mine.mp3"
    assert (project.root / "music" / "kenney-jingle-sax.ogg").exists()


def test_music_get_refuses_a_file_that_is_not_audio(project, site):
    site.routes["/incompetech/pieces"] = PIECES
    site.routes["/incompetech/files"] = b"#EXTM3U\nfile:///etc/passwd\n"
    with pytest.raises(AltocueError, match="not an audio file"):
        music.fetch(project, "incompetech:ocean-drift")
    assert not list((project.root / "music").glob("*")) and not credits.records(project)


def test_music_get_says_what_to_do_with_an_unknown_id(project):
    with pytest.raises(AltocueError) as caught:
        music.fetch(project, "jamendo:1")
    assert "altocue music search" in caught.value.fix


def test_a_chosen_track_reaches_the_timeline_the_way_render_reads_it(project, capsys):
    run_music(capsys, "get", "kenney:jingle-steel", "-p", str(project.root))
    assert build_timeline(project)["music"]["path"] == "music/kenney-jingle-steel.ogg"


# Sound effects


def test_every_bundled_sound_is_catalogued_with_its_source_and_licence():
    folder = Path(mix.SFX_DIR)
    names = {path.stem for path in folder.glob("*.ogg")}
    assert names == {row["name"] for row in sfx.bundled()}
    readme = (folder / "README.md").read_text()
    for row in sfx.bundled():
        assert row["licence"] == "CC0-1.0" and row["url"].startswith("https://") and row["seconds"] > 0
        assert f"`{row['name']}.ogg`" in readme and row["source_file"] in readme
    assert "BigSoundBank" in readme and "listened" in readme


def test_every_bundled_track_is_catalogued():
    folder = Path(mix.SFX_DIR).parent / "music"
    assert {path.stem for path in folder.glob("*.ogg")} == {row["name"] for row in music.bundled()}
    readme = (folder / "README.md").read_text()
    assert all(row["licence"] == "CC0-1.0" and row["source_file"] in readme for row in music.bundled())
    assert "listened" in readme


def test_the_bundled_sound_stays_small():
    data = Path(mix.SFX_DIR).parent
    sounds = [*(data / "sfx").glob("*.ogg"), *(data / "music").glob("*.ogg")]
    assert all(path.stat().st_size < 40_000 for path in sounds)
    assert sum(path.stat().st_size for path in sounds) < 300_000


def test_new_bundled_sounds_mix_like_the_old_ones(tmp_path):
    for name in ("punch", "whoosh3", "sub"):
        wav = ffmpeg.extract_wav(mix.sfx_path(name), tmp_path / f"{name}.wav")
        samples, rate = ffmpeg.read_wav(wav)
        assert rate == 48000 and 0.2 < len(samples) / rate < 1.5 and np.abs(samples).max() > 0.5


def run_sfx(capsys, *argv: str) -> str:
    from altocue import cli

    assert cli.main(["sfx", *argv]) == 0
    return capsys.readouterr().out


def test_sfx_list_shows_every_bundled_sound_with_length_and_licence(capsys):
    out = run_sfx(capsys, "list")
    assert "whoosh3" in out and "pop" in out and "CC0-1.0" in out and "0.83" in out


def test_sfx_search_shows_bundled_first_and_needs_no_key(site, capsys):
    out = run_sfx(capsys, "search", "transition")
    assert "whoosh3" in out and "FREESOUND_API_KEY" in out
    assert not [path for path, _ in site.seen]


def freesound_sound(number: int, licence: str) -> dict:
    return {
        "id": number,
        "name": "Door creak",
        "username": "ana",
        "license": licence,
        "duration": 1.2,
        "url": f"https://freesound.org/people/ana/sounds/{number}/",
        "previews": {"preview-hq-mp3": f"https://cdn.freesound.org/previews/{number}-hq.mp3"},
    }


def test_freesound_asks_for_cc0_and_cc_by_only_and_keeps_the_key_out_of_the_url(site):
    site.routes["/freesound/search"] = {
        "results": [
            freesound_sound(1, "https://creativecommons.org/publicdomain/zero/1.0/"),
            freesound_sound(2, "https://creativecommons.org/licenses/by/4.0/"),
            freesound_sound(3, "https://creativecommons.org/licenses/by-nc/4.0/"),
            freesound_sound(4, "http://creativecommons.org/licenses/sampling+/1.0/"),
        ]
    }
    results = freesound.search("door", limit=4, key="secret")
    assert [result.id for result in results] == ["1", "2"]
    assert results[0].credit == "" and results[1].credit == "Door creak by ana, CC BY 4.0, https://freesound.org/people/ana/sounds/2/"
    path, headers = site.seen[0]
    assert "secret" not in path and headers["Authorization"] == "Token secret"
    wanted = parse_qs(urlsplit(path).query)["filter"][0]
    assert '"Creative Commons 0"' in wanted and '"Attribution"' in wanted


def test_sfx_get_of_a_freesound_sound_saves_it_and_its_credit(project, site, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FREESOUND_API_KEY", "secret")
    sound = freesound_sound(2, "https://creativecommons.org/licenses/by/4.0/")
    sound["previews"]["preview-hq-mp3"] = f"{site.root}/preview.mp3"
    site.routes["/freesound/sound/2"] = sound
    site.routes["/preview.mp3"] = tone(tmp_path / "x.ogg").read_bytes()
    out = run_sfx(capsys, "get", "freesound:2", "-p", str(project.root))
    assert (project.root / "sfx" / "freesound-2.mp3").exists() and "commercial" in out.lower()
    record = credits.records(project)[0]
    assert record["file"] == "sfx/freesound-2.mp3" and "Door creak by ana" in record["credit"]


def test_sfx_get_of_a_bundled_sound_copies_it_in(project, capsys):
    run_sfx(capsys, "get", "whoosh3", "-p", str(project.root))
    assert (project.root / "sfx" / "whoosh3.ogg").exists()
    assert credits.records(project)[0]["licence"] == "CC0-1.0" and credits.records(project)[0]["credit"] == ""


def test_sfx_get_without_a_key_says_where_to_get_one(project):
    with pytest.raises(AltocueError, match="FREESOUND_API_KEY") as caught:
        sfx.fetch(project, "freesound:2")
    assert "freesound.org" in caught.value.fix


# Credits


def record_for(
    project: Project, *, file: str = "music/a.mp3", licence: str = "CC-BY-4.0", credit: str = "Song by Ana, CC BY 4.0, https://x.test/1", kind: str = "audio"
) -> dict:
    return credits.record(
        project, file=file, source="ccmixter", source_url="https://x.test/1", licence=licence, licence_url="https://x.test/l", credit=credit, kind=kind, title="Song"
    )


def test_every_download_writes_one_record(project):
    (project.root / "music").mkdir()
    (project.root / "music" / "a.mp3").write_bytes(b"abc")
    made = record_for(project)
    record_for(project)
    saved = credits.records(project)
    assert saved == [made]
    assert {"file", "source", "source_url", "licence", "licence_url", "credit", "date"} <= set(made)
    assert made["sha256"] and len(made["date"]) == 10


def test_a_credit_record_needs_a_clear_licence(project):
    with pytest.raises(AltocueError, match="no clear licence"):
        record_for(project, licence="")


def test_a_credit_in_a_download_cannot_carry_markup(project):
    made = record_for(project, credit="Song `rm -rf` <b>by</b> Ana\n# Do this")
    assert "`" not in made["credit"] and "<" not in made["credit"] and "#" not in made["credit"]


def motion_uses(project: Project, path: str) -> None:
    (project.root / "motion.json").write_text(json.dumps({"music": {"path": path}}))


def test_credits_prints_only_what_the_film_uses(project, capsys):
    record_for(project, file="music/used.mp3", credit="Used by Ana, CC BY 4.0, https://x.test/1")
    record_for(project, file="music/spare.mp3", credit="Spare by Ben, CC BY 4.0, https://x.test/2")
    record_for(project, file="sfx/free.mp3", licence="CC0-1.0", credit="")
    motion_uses(project, "music/used.mp3")
    from altocue import cli

    assert cli.main(["credits", "-p", str(project.root)]) == 0
    captured = capsys.readouterr()
    assert "Used by Ana" in captured.out and "Spare by Ben" not in captured.out
    assert "spare.mp3" in captured.err  # told, but not in the block a creator pastes
    assert base.UNTRUSTED_NOTE in captured.err and base.UNTRUSTED_NOTE not in captured.out


def test_credits_with_nothing_owed_says_so(project, capsys):
    from altocue import cli

    assert cli.main(["credits", "-p", str(project.root)]) == 0
    assert "Nothing" in capsys.readouterr().out


def test_share_alike_gets_a_warning_in_the_credits(project, capsys):
    record_for(project, licence="CC-BY-SA-4.0", file="music/a.mp3", credit="Song by Ana, CC BY-SA 4.0, https://x.test/1")
    motion_uses(project, "music/a.mp3")
    from altocue import cli

    assert cli.main(["credits", "-p", str(project.root)]) == 0
    assert "hare-alike" in capsys.readouterr().err


def test_a_multi_line_credit_stays_whole(project):
    text = "Ocean Drift Kevin MacLeod (incompetech.com)\nLicensed under Creative Commons: By Attribution 4.0\nhttps://creativecommons.org/licenses/by/4.0/"
    record_for(project, credit=text, licence="CC-BY-4.0")
    motion_uses(project, "music/a.mp3")
    assert text in credits.block(credits.used(project))


def test_render_writes_the_credits_file_when_something_is_owed(project):
    record_for(project)
    motion_uses(project, "music/a.mp3")
    path = credits.write_txt(project)
    assert path == project.out / "credits.txt" and "Song by Ana" in path.read_text()


def test_render_clears_a_stale_credits_file(project):
    (project.out / "credits.txt").write_text("Old")
    assert credits.write_txt(project) is None and not (project.out / "credits.txt").exists()


def test_a_credit_with_no_kind_still_lands_in_the_block():
    assert "Song by Ana" in credits.block([{"credit": "Song by Ana", "kind": ""}])


def test_render_writes_the_credits_file_itself(project, monkeypatch):
    record_for(project)
    motion_uses(project, "music/a.mp3")
    monkeypatch.setattr(render.frame, "frame_project", lambda project: None)
    monkeypatch.setattr(render.plate, "build", lambda project, draft: (Path("p.mp4"), Path("v.wav")))
    monkeypatch.setattr(render.caption, "build", lambda project: None)
    monkeypatch.setattr(render.motion, "render_layers", lambda *args, **kwargs: None)
    monkeypatch.setattr(render.mix, "mix_bed", lambda *args, **kwargs: None)
    monkeypatch.setattr(render, "compose_args", lambda *args: [])
    monkeypatch.setattr(render.ffmpeg, "run", lambda args: None)
    render.build(project)
    assert "Song by Ana" in (project.out / "credits.txt").read_text()


def stock_insert(project: Project, tmp_path: Path, site, *, credit: str, licence: str = "CC-BY-4.0") -> list[dict]:
    site.routes["/file.mp4"] = video_file(tmp_path / "clip.mp4").read_bytes()
    option = {
        "source": "wikimedia",
        "kind": "video",
        "url": f"{site.root}/file.mp4",
        "title": "Wave",
        "licence": licence,
        "licence_url": "https://x.test/l",
        "credit": credit,
        "page": "https://x.test/p",
        "author": "Ana",
    }
    spot_with(project, [option])
    return broll.apply_picks(project, ["b1=c1"])


def test_a_picked_stock_clip_writes_one_credit_record_and_keeps_its_licence(project, tmp_path, site):
    inserts = stock_insert(project, tmp_path, site, credit="Wave by Ana, CC BY 4.0, https://x.test/p")
    assert inserts[0]["licence"] == "CC-BY-4.0" and inserts[0]["credit"].startswith("Wave by Ana") and inserts[0]["licence_url"]
    record = credits.records(project)[0]
    assert record["file"] == inserts[0]["source_file"] and record["source_url"] == "https://x.test/p"
    assert [row["title"] for row in credits.used(project)] == ["Wave"]


def test_a_skipped_clip_stops_being_owed(project, tmp_path, site):
    stock_insert(project, tmp_path, site, credit="Wave by Ana, CC BY 4.0, https://x.test/p")
    broll.apply_picks(project, ["b1=skip"])
    build_timeline(project)
    assert credits.used(project) == []


def gate_on(project: Project):
    timeline = json.loads(project.timeline_path.read_text()) if project.timeline_path.exists() else None
    return gates.gate_credits(SimpleNamespace(project=project, timeline=timeline))


def test_the_credits_gate_fails_a_cc_by_asset_with_no_credits_file(project, tmp_path, site):
    stock_insert(project, tmp_path, site, credit="Wave by Ana, CC BY 4.0, https://x.test/p")
    build_timeline(project)
    result = gate_on(project)
    assert not result.passed and not result.skipped and "Wave" in result.detail and "credits.txt" in result.detail


def test_the_credits_gate_passes_once_the_file_carries_the_line(project, tmp_path, site):
    stock_insert(project, tmp_path, site, credit="Wave by Ana, CC BY 4.0, https://x.test/p")
    build_timeline(project)
    credits.write_txt(project)
    assert gate_on(project).passed


def test_the_credits_gate_catches_a_credits_file_that_went_stale(project, tmp_path, site):
    stock_insert(project, tmp_path, site, credit="Wave by Ana, CC BY 4.0, https://x.test/p")
    build_timeline(project)
    (project.out / "credits.txt").write_text("Somebody else\n")
    assert not gate_on(project).passed


def test_the_credits_gate_catches_a_lost_record(project, tmp_path, site):
    stock_insert(project, tmp_path, site, credit="Wave by Ana, CC BY 4.0, https://x.test/p")
    build_timeline(project)
    (project.root / credits.FILE).unlink()
    result = gate_on(project)
    assert not result.passed and "Wave" in result.detail


def test_a_pick_saved_by_an_older_altocue_owes_nothing(project):
    timeline = {"inserts": [{"file": "work/broll/b1.mp4", "source": "pexels", "title": "Pexels 7", "licence": "https://www.pexels.com/license/"}]}
    project.timeline_path.write_text(json.dumps(timeline))
    assert gate_on(project).passed


def test_a_stock_insert_with_no_licence_is_a_finding(project):
    project.timeline_path.write_text(json.dumps({"inserts": [{"file": "work/broll/b1.mp4", "source": "wikimedia", "title": "Wave"}]}))
    assert not gate_on(project).passed


def test_the_credits_gate_ignores_a_film_with_no_downloads(project):
    build_timeline(project)
    result = gate_on(project)
    assert result.skipped and result.value == gates.NOT_APPLICABLE


def test_the_credits_gate_passes_licences_that_owe_nothing(project, tmp_path, site):
    stock_insert(project, tmp_path, site, credit="", licence="CC0-1.0")
    build_timeline(project)
    assert gate_on(project).passed


def test_the_credits_gate_says_it_could_not_run_without_a_timeline(project):
    result = gate_on(project)
    assert result.skipped and result.value != gates.NOT_APPLICABLE


# Real requests. Run with: uv run pytest tests/test_stock.py -m network -q


@pytest.fixture
def scratch(monkeypatch):
    """Everything fetched lives here and is gone when the test ends, cache included."""
    folder = tempfile.mkdtemp(prefix="altocue-live-")
    monkeypatch.setenv("HOME", folder)
    yield Path(folder)
    shutil.rmtree(folder, ignore_errors=True)


@pytest.fixture
def live(scratch, monkeypatch) -> Project:
    from conftest import make_clip

    clip_file = make_clip(scratch / "take.mp4")
    made = Project.create(scratch / "video")
    (made.sources / "take.mp4").symlink_to(clip_file)
    probed = ffmpeg.probe_clip(made.sources / "take.mp4", clip_id="take")
    words = script()
    probed.duration = words[-1].end + 0.5
    made.save_clips([probed])
    made.save_words("take", words, "test")
    made.save_edl(Edl(fps=30, segments=[Segment(id="s1", clip="take", start=0.0, end=round(words[-1].end * 30) / 30)]))
    monkeypatch.setattr(
        cut, "map_words", lambda project, edl: [{"text": w.text, "start": w.start, "end": w.end, "segment": "s1", "kind": "word"} for w in project.words("take")]
    )
    return made


def bytes_at(url: str) -> int:
    reply = http.call("HEAD", url, headers={"User-Agent": base.AGENT}, attempts=2)
    return int(reply.headers.get("Content-Length") or 10**12)


def smallest(options: list[dict]) -> dict:
    """The option that costs the least to fetch. NASA's `~mobile` file is the smallest real rendition."""
    for option in options:
        if option["source"] == "nasa" and option.get("kind") == "video":
            option["url"] = option["url"].replace("~medium.mp4", "~mobile.mp4")
        option["bytes"] = option.get("size") or bytes_at(option["url"])
    return min(options, key=lambda option: option["bytes"])


def live_pick(project: Project, name: str, query: str, kind: str) -> None:
    results = stock.search(name, query, kind=kind, shape="portrait", limit=4)
    assert results, f"{name} found nothing for {query}"
    assert all(result.url.startswith("https://") and result.licence and result.licence_url and result.source == name for result in results)
    chosen = smallest([stock.option(result) for result in results if kind == "photo" or result.duration >= 2.0])
    print(f"\n{name}: {len(results)} results for {query!r}. Picked {chosen['title']!r}, {chosen['licence']}, {chosen['bytes']} bytes, {chosen['url']}")
    spot_with(project, [chosen])
    inserts = broll.apply_picks(project, ["b1=c1"])
    made = project.root / inserts[0]["file"]
    picture = ffmpeg.probe_clip(made)
    assert (picture.width, picture.height) == (1080, 1920) and picture.duration == pytest.approx(2.0, abs=0.1)
    assert build_timeline(project)["inserts"][0]["file"] == inserts[0]["file"]
    record = credits.records(project)[0]
    assert record["source_url"].startswith("https://") and record["licence"] == chosen["licence"] and record["sha256"]
    credits.write_txt(project)
    print(f"{name}: record {json.dumps({key: record[key] for key in ('file', 'licence', 'credit', 'source_url', 'date')})}")
    print(f"{name}: credit line {record['credit'] or '(none owed)'}")


@pytest.mark.network
@pytest.mark.parametrize(
    ("name", "query", "kind"),
    [
        ("nasa", "ocean", "video"),
        ("nasa", "ocean", "photo"),
        ("wikimedia", "ocean", "video"),
        ("wikimedia", "ocean", "photo"),
        ("met", "ocean", "photo"),
        ("cleveland", "ocean", "photo"),
    ],
)
def test_live_source_end_to_end(live, name, query, kind):
    live_pick(live, name, query, kind)


@pytest.mark.network
def test_live_commons_owes_a_real_credit_and_the_gate_sees_it(live):
    results = [result for result in stock.search("wikimedia", "ocean waves", kind="photo", shape="portrait", limit=12) if base.needs_credit(result.licence)]
    assert results, "no attribution-licensed file in this search"
    chosen = smallest([stock.option(result) for result in results])
    print(f"\ncommons: {chosen['licence']}: {chosen['credit']}")
    spot_with(live, [chosen])
    broll.apply_picks(live, ["b1=c1"])
    build_timeline(live)
    assert not gate_on(live).passed
    credits.write_txt(live)
    assert gate_on(live).passed and chosen["credit"] in (live.out / "credits.txt").read_text()


@pytest.mark.network
def test_live_archive_stays_opt_in(live):
    results = stock.search("archive", "ocean", kind="video", shape="landscape", limit=4)
    assert results and all(result.licence == base.UNVERIFIED and archive.WARNING in result.warnings for result in results)
    chosen = smallest([stock.option(result) for result in results])
    print(f"\narchive: {chosen['title']!r}, {chosen['bytes']} bytes, {chosen['url']}")
    spot_with(live, [chosen])
    with pytest.raises(AltocueError, match="cannot be trusted"):
        broll.apply_picks(live, ["b1=c1"])


def live_track(project: Project, source: str, query: str, keep) -> None:
    results = [result for result in music.search(query, limit=8) if result.source == source and keep(result)]
    assert results, f"{source} found nothing for {query}"
    shortest = min(results, key=lambda result: result.size or result.duration or 1e9)
    print(f"\n{source}: {len(results)} tracks. Picked {shortest.id}, {shortest.licence}, {shortest.duration} s, {shortest.url}")
    music.fetch(project, f"{source}:{shortest.id}")
    record = credits.records(project)[0]
    motion = json.loads((project.root / "motion.json").read_text())
    assert (project.root / motion["music"]["path"]).stat().st_size > 10_000
    assert build_timeline(project)["music"]["path"] == motion["music"]["path"] == record["file"]
    print(f"{source}: credit line {record['credit'] or '(none owed)'}")


@pytest.mark.network
def test_live_ccmixter_track(live):
    live_track(live, "ccmixter", "ambient", lambda result: base.needs_credit(result.licence))


@pytest.mark.network
def test_live_incompetech_track(live):
    live_track(live, "incompetech", "calm piano", lambda result: True)
