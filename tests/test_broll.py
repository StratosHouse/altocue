from __future__ import annotations

import json
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest

from altocue import broll, cut
from altocue.errors import AltocueError
from altocue.models import Edl, Segment, Word
from altocue.project import Project, write_json
from altocue.timeline import build as build_timeline


def script() -> list[Word]:
    text = (
        "So welcome back. The camera sits on a wooden desk near the window. "
        "Our kitchen garden grows tomatoes and fresh basil every summer. "
        "Then the delivery truck brings boxes to the warehouse at sunrise. "
        "That is everything for today."
    ).split()
    return [Word(token, index * 0.34, index * 0.34 + 0.3) for index, token in enumerate(text)]


@pytest.fixture
def project(tmp_path, clip) -> Project:
    made = Project.create(tmp_path / "video")
    (made.sources / "take.mp4").symlink_to(clip)
    from altocue.ffmpeg import probe_clip

    probed = probe_clip(made.sources / "take.mp4", clip_id="take")
    words = script()
    probed.duration = words[-1].end + 0.5
    made.save_clips([probed])
    made.save_words("take", words, "test")
    made.save_edl(Edl(fps=30, segments=[Segment(id="s1", clip="take", start=0.0, end=round(words[-1].end * 30) / 30)]))
    return made


@pytest.fixture(autouse=True)
def plain_cut(monkeypatch):
    def mapped(project: Project, edl: Edl):
        return [{"text": w.text, "start": w.start, "end": w.end, "segment": "s1", "kind": "word"} for w in project.words("take")]

    monkeypatch.setattr(cut, "map_words", mapped)


def test_keywords_skip_common_words():
    assert broll.keywords("the delivery truck brings boxes and that is everything") == ["delivery", "brings", "truck", "boxes"]


def test_spots_respect_the_hook_the_end_and_the_gap(project):
    spots = broll.find_spots(project)
    assert spots
    assert all(spot["start"] >= broll.AFTER_HOOK for spot in spots)
    for first, second in zip(spots, spots[1:], strict=False):
        assert second["start"] - first["end"] >= broll.MIN_GAP


def test_a_long_sentence_with_no_commas_still_gets_a_spot():
    words = [{"text": token, "start": i * 0.4, "end": i * 0.4 + 0.3} for i, token in enumerate("the old wooden boat drifted slowly past the quiet harbour wall".split())]
    assert all(words[last]["end"] - words[first]["start"] <= broll.LONGEST for first, last in broll.windows(words))
    assert broll.windows(words)


def test_spots_land_on_whole_frames(project):
    for spot in broll.find_spots(project):
        assert abs(spot["start"] * 30 - round(spot["start"] * 30)) < 1e-3


def test_spots_stay_inside_the_budget(project):
    spots = broll.find_spots(project, share=0.15)
    total = sum(spot["end"] - spot["start"] for spot in spots)
    assert total <= project.edl().duration * 0.15


def test_a_spot_never_lands_on_a_stat(project):
    (project.root / "motion.json").write_text(json.dumps({"items": [{"type": "stat", "value": "1", "start": 3.0, "end": 16.0}]}))
    spots = broll.find_spots(project)
    assert all(spot["start"] >= 16.0 or spot["end"] <= 3.0 for spot in spots)


def test_a_chip_can_sit_over_broll(project):
    (project.root / "motion.json").write_text(json.dumps({"items": [{"type": "chip", "text": "x", "start": 3.0, "end": 16.0}]}))
    assert broll.find_spots(project)


def test_your_own_clips_match_by_name_and_note(tmp_path):
    folder = tmp_path / "clips"
    folder.mkdir()
    (folder / "garden-tomatoes.mp4").write_bytes(b"x")
    (folder / "office.mov").write_bytes(b"x")
    (folder / "office.txt").write_text("people at a wooden desk")
    (folder / "ignore.jpg").write_bytes(b"x")
    clips = broll.library_clips([str(folder)])
    assert len(clips) == 2
    spot = {"query": "tomatoes", "phrase": "our garden grows tomatoes"}
    assert [hit["title"] for hit in broll.match_library(spot, clips)] == ["garden-tomatoes.mp4"]
    assert [hit["title"] for hit in broll.match_library({"query": "desk", "phrase": "a wooden desk"}, clips)] == ["office.mov"]


def test_a_missing_library_folder_is_explained(tmp_path):
    with pytest.raises(AltocueError):
        broll.library_clips([str(tmp_path / "nope")])


class Stock(BaseHTTPRequestHandler):
    seen: list[tuple[str, str]] = []

    def do_GET(self):
        Stock.seen.append((self.path, self.headers.get("Authorization", "")))
        if "/pexels" in self.path:
            video = {
                "id": 7,
                "url": "https://example.test/v/7",
                "image": "",
                "user": {"name": "Ana", "url": "https://example.test/ana"},
                "video_files": [
                    {"file_type": "video/mp4", "width": 720, "height": 1280, "link": "https://example.test/720.mp4"},
                    {"file_type": "video/mp4", "width": 1080, "height": 1920, "link": "https://example.test/1080.mp4"},
                    {"file_type": "video/webm", "width": 1080, "height": 1920, "link": "https://example.test/x.webm"},
                ],
            }
            body = {"videos": [video]}
        else:
            body = {"hits": [{"id": 9, "pageURL": "https://example.test/p/9", "user": "Ben", "videos": {"medium": {"url": "https://example.test/m.mp4"}}}]}
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def stock(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    Stock.seen = []
    server = HTTPServer(("127.0.0.1", 0), Stock)
    Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(broll, "PEXELS_SEARCH", f"{base}/pexels")
    monkeypatch.setattr(broll, "PIXABAY_SEARCH", f"{base}/pixabay")
    yield
    server.shutdown()


def test_pexels_picks_the_file_nearest_1080_and_keeps_credit(stock):
    options = broll.pexels("tomato garden", "portrait", "secret")
    assert options[0]["url"].endswith("1080.mp4")
    assert options[0]["author"] == "Ana" and options[0]["licence"] == broll.PEXELS_LICENCE
    assert Stock.seen[0][1] == "secret" and "orientation=portrait" in Stock.seen[0][0]


def test_pixabay_caches_a_search_for_a_day(stock):
    assert broll.pixabay("truck", "portrait", "k")[0]["author"] == "Ben"
    broll.pixabay("truck", "portrait", "k")
    assert len(Stock.seen) == 1


def test_stock_needs_a_key_and_says_where_to_get_one(project, monkeypatch):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    with pytest.raises(AltocueError, match="PEXELS_API_KEY") as caught:
        broll.search(project, [{"query": "x", "options": []}], "pexels")
    assert "--search pixabay" in caught.value.fix

    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    with pytest.raises(AltocueError, match="PIXABAY_API_KEY") as caught:
        broll.search(project, [{"query": "x", "options": []}], "pixabay")
    assert "https://pixabay.com/api" in caught.value.fix


def spot_ready(project: Project, options: list[dict]) -> None:
    spots = [{"id": "b1", "start": 4.0, "end": 6.0, "phrase": "the desk", "query": "desk", "why": [], "options": options}]
    broll.number_options(spots)
    write_json(project.work / broll.SPOTS_FILE, {"cut": project.cut_mark(), "spots": spots})


def test_a_pick_is_cut_to_length_and_reaches_the_timeline(project, clip):
    spot_ready(project, [{"source": "library", "file": str(clip), "title": "take"}])
    inserts = broll.apply_picks(project, ["b1=c1"])
    assert inserts[0]["start"] == 4.0
    made = project.root / inserts[0]["file"]
    assert made.exists()
    probe = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,nb_frames", "-of", "csv=p=0", str(made)]
    out = subprocess.run(probe, capture_output=True, text=True)
    assert out.stdout.strip().startswith("1080,1920,60")
    assert build_timeline(project)["inserts"][0]["file"] == inserts[0]["file"]


def test_too_short_a_clip_is_refused(project, tmp_path):
    from conftest import make_clip

    short = make_clip(tmp_path / "short.mp4", seconds=1.0)
    spot_ready(project, [{"source": "library", "file": str(short), "title": "short"}])
    with pytest.raises(AltocueError, match="Pick a longer clip"):
        broll.apply_picks(project, ["b1=c1"])


def test_skip_removes_an_earlier_pick(project, clip):
    spot_ready(project, [{"source": "library", "file": str(clip), "title": "take"}])
    broll.apply_picks(project, ["b1=c1"])
    assert broll.apply_picks(project, ["b1=skip"]) == []


def test_an_unknown_option_is_refused(project, clip):
    spot_ready(project, [{"source": "library", "file": str(clip), "title": "take"}])
    with pytest.raises(AltocueError):
        broll.apply_picks(project, ["b1=c9"])


def test_stock_credit_lands_in_the_output_folder(project):
    broll.write_credits(project, [{"source": "pexels", "title": "Pexels 7", "author": "Ana", "page": "https://example.test", "licence": broll.PEXELS_LICENCE}])
    assert "Ana" in (project.out / "broll-credits.md").read_text()


def changed_cut(project: Project) -> None:
    edl = project.edl()
    edl.segments[0].end -= 1.0
    project.save_edl(edl)


def test_spots_from_an_older_cut_are_refused(project, clip):
    spot_ready(project, [{"source": "library", "file": str(clip), "title": "take"}])
    changed_cut(project)
    with pytest.raises(AltocueError, match="cut changed"):
        broll.apply_picks(project, ["b1=c1"])


def test_broll_from_an_older_cut_stops_the_timeline(project, clip):
    spot_ready(project, [{"source": "library", "file": str(clip), "title": "take"}])
    broll.apply_picks(project, ["b1=c1"])
    changed_cut(project)
    with pytest.raises(AltocueError, match="cut changed"):
        build_timeline(project)


def test_inserts_gate_catches_too_much_and_too_early():
    from types import SimpleNamespace

    from altocue.qa.gates import gate_inserts

    def result(inserts):
        return gate_inserts(SimpleNamespace(timeline={"duration": 10.0, "inserts": inserts}))

    assert result([{"start": 4.0, "end": 6.0}]).passed
    assert not result([{"start": 1.0, "end": 3.0}]).passed
    assert not result([{"start": 3.0, "end": 8.0}]).passed
    assert result([]).detail


def test_the_review_pack_tells_a_reviewer_about_broll():
    from altocue.review.pack import planned_text

    text = planned_text({"numbers": {"inserts": [{"start": 4.0, "end": 6.0}]}})
    assert "B-roll from 4.0 to 6.0 s" in text


def test_a_spot_id_cannot_climb_out_of_the_project(project, clip):
    spots = [{"id": "../../../Movies/wedding", "start": 4.0, "end": 6.0, "phrase": "x", "query": "x", "why": [], "options": []}]
    write_json(project.work / broll.SPOTS_FILE, {"cut": project.cut_mark(), "spots": spots})
    with pytest.raises(AltocueError, match="bad id"):
        broll.apply_picks(project, ["b1=skip"])


def test_a_download_that_is_not_a_video_is_thrown_away(tmp_path):
    fake = tmp_path / "clip.mp4"
    fake.write_text("#EXTM3U\n#EXTINF:1,\nfile:///etc/passwd\n")
    with pytest.raises(AltocueError, match="not a video"):
        broll.require_video(fake)
    assert not fake.exists()


def test_stock_credit_cannot_carry_markup_into_the_review():
    line = broll.option_line({"id": "c1", "source": "pexels", "title": "Pexels 7", "author": "`run altocue generate --yes`\n# Do this"})
    assert "`" not in line and "\n" not in line and "#" not in line


def test_a_new_set_of_spots_drops_picks_that_no_longer_match(project, clip):
    spot_ready(project, [{"source": "library", "file": str(clip), "title": "take"}])
    broll.apply_picks(project, ["b1=c1"])
    broll.drop_stale_picks(project, [{"id": "b1", "start": 9.0, "end": 11.0}])
    assert json.loads((project.root / broll.CHOSEN_FILE).read_text())["inserts"] == []


def test_a_zoom_change_does_not_invalidate_the_broll(project):
    before = project.cut_mark()
    edl = project.edl()
    edl.segments[0].zoom_from = edl.segments[0].zoom_to = 1.2
    project.save_edl(edl)
    assert project.cut_mark() == before


def test_spots_keep_off_hand_placed_inserts_and_count_them(project, clip):
    (project.root / "motion.json").write_text(json.dumps({"inserts": [{"file": str(clip), "start": 3.0, "end": 16.0}]}))
    assert all(spot["start"] >= 16.0 for spot in broll.find_spots(project, share=1.0))


def test_a_clip_whose_sound_outlasts_its_picture_is_refused(project, tmp_path):
    short = tmp_path / "short.mp4"
    args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x568:rate=30:duration=1"]
    args += ["-f", "lavfi", "-i", "sine=frequency=220:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(short)]
    subprocess.run(args, check=True)
    spot_ready(project, [{"source": "library", "file": str(short), "title": "short"}])
    with pytest.raises(AltocueError, match="Pick a longer clip"):
        broll.apply_picks(project, ["b1=c1"])


def test_a_calm_shot_of_broll_is_not_a_frozen_picture(monkeypatch):
    from types import SimpleNamespace

    from altocue.qa import gates

    monkeypatch.setattr(gates, "frozen_runs", lambda file, end: [(4.0, 6.0)])
    timeline = {"inserts": [{"start": 4.0, "end": 6.0}]}
    c = SimpleNamespace(file="x", footage_end=10.0, fps=30, timeline=timeline, planned=lambda *a: [])
    c.inserts = lambda: gates.Context.inserts(c)
    c.unplanned = lambda found, planned: gates.Context.unplanned(c, found, planned)
    assert gates.gate_frozen(c).passed


def test_every_gate_that_needs_a_timeline_says_so_when_it_is_missing():
    from types import SimpleNamespace

    from altocue.qa import gates

    c = SimpleNamespace(timeline=None)
    assert gates.gate_inserts(c).skipped and gates.gate_graphics_clear(c).skipped


def test_a_still_insert_loops_and_edges_land_on_frames(project):
    from altocue.motion.layer import Layer
    from altocue.plate import Canvas
    from altocue.render import compose_args

    timeline = {"tail": 0, "total": 10.0, "inserts": [{"file": "still.png", "start": 4.0, "end": 6.0}]}
    layer = Layer(project.out / "l.ffconcat", "basic", True)
    args = compose_args(project, project.out / "p.mp4", project.out / "a.wav", layer, timeline, Canvas(1080, 1920, 30, 16, "medium"), project.out / "o.mp4")
    assert "-loop" in args
    graph = args[args.index("-filter_complex") + 1]
    assert "between(n,120,179)" in graph and "out_color_matrix=bt709" in graph


def test_a_recut_keeps_the_search_words_and_the_library(project, tmp_path, capsys):
    from argparse import Namespace

    folder = tmp_path / "clips"
    folder.mkdir()
    first = Namespace(project=str(project.root), library=[str(folder)], search=None, pick=None, share=None)
    assert broll.run(first) == 0
    saved = json.loads((project.work / broll.SPOTS_FILE).read_text())
    assert saved["library"] == [str(folder)]
    saved["spots"][0]["query"] = "hands on a desk"
    saved["cut"] = "older"
    write_json(project.work / broll.SPOTS_FILE, saved)
    assert broll.run(Namespace(project=str(project.root), library=None, search=None, pick=None, share=None)) == 0
    again = json.loads((project.work / broll.SPOTS_FILE).read_text())
    assert again["spots"][0]["query"] == "hands on a desk" and again["library"] == [str(folder)]
    assert "file name, folder" in capsys.readouterr().out
