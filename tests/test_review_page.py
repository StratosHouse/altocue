"""The review page: one file that plays the film beside its gates, notes and captions, and decides nothing."""

from __future__ import annotations

import base64
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from canaries import Case, Recipe, build

from altocue import ffmpeg, locks, qa
from altocue.cli import main
from altocue.errors import AltocueError
from altocue.project import write_json
from altocue.review import page, rubric

NOTE = {
    "start": "00:03",
    "end": "00:04",
    "start_seconds": 3.0,
    "end_seconds": 4.0,
    "severity": "major",
    "dimension": "pacing",
    "observed": "The thought is chopped in half here.",
    "suggested_fix": "Hold the shot.",
}


def gate(gate_id: str, passed: bool = True, skipped: bool = False, value=None, detail: str = "fine") -> dict:
    return {"id": gate_id, "passed": passed, "skipped": skipped, "value": value, "threshold": None, "detail": detail}


GATES = [
    gate("loudness", value=-14.0, detail="-14.0 LUFS"),
    gate("black_frames", passed=False, value=1, detail="black at 00:02.00-00:02.10"),
    gate("rules_ocr", passed=False, skipped=True, detail="No text reader on this machine."),
    gate("inserts", passed=False, skipped=True, value="n/a", detail="No B-roll in this edit."),
]


def report(project, file, verdict: str = "FIX", **more) -> dict:
    digest = qa.sha256(file)
    data = {
        "verdict": verdict,
        "reasons": ["Weighted score 3.20, under 3.8"],
        "weighted": 3.2,
        "scores": {name: {"score": 4, "why": ""} for name in rubric.DIMENSIONS},
        "notes": [NOTE],
        "reviewer": {"vendor": "acme", "model": "m1", "sees": "stills", "independent": False, "independent_why": "A file cannot prove that."},
        "file_sha256": digest,
        "gates": GATES,
        "limits": ["The reviewer heard nothing."],
        **more,
    }
    write_json(project.review_dir / "report.json", data)
    write_json(project.review_dir / "qa.json", {"sha256": digest, "gates": GATES})
    return data


@pytest.fixture(scope="module")
def film(tmp_path_factory):
    item = build(Case("page", "", Recipe(seconds=3.0, labels=["", "", ""], face=False)), tmp_path_factory.mktemp("page"))
    return item


@pytest.fixture
def written(film):
    report(film.project, film.file)
    return page.write(film.project, film.file).read_text()


def only_tag(html: str, name: str) -> list[str]:
    return re.findall(rf"<{name}\b[^>]*>", html)


def test_the_page_lands_in_the_project(film):
    report(film.project, film.file)
    assert page.write(film.project, film.file) == film.project.review_dir / "page.html"


def test_the_film_is_inside_the_file_and_really_plays(written, tmp_path):
    payload = re.search(r'src="data:video/mp4;base64,([^"]+)"', written).group(1)
    copy = tmp_path / "copy.mp4"
    copy.write_bytes(base64.b64decode(payload))
    clip = ffmpeg.probe_clip(copy)
    assert clip.has_audio and clip.duration == pytest.approx(3.0, abs=0.2)
    assert max(clip.width, clip.height) <= page.COPY_LONG_SIDE


def test_nothing_comes_from_the_network(written):
    assert not re.search(r"""(?:src|href)=["']https?:""", written)
    assert "@import" not in written and "url(http" not in written
    assert not only_tag(written, "link")


def test_every_script_is_inline(written):
    assert all("src=" not in tag for tag in only_tag(written, "script"))


def test_the_html_is_well_formed(written):
    class Check(HTMLParser):
        VOID = {"meta", "source", "br", "path", "input", "img"}

        def __init__(self):
            super().__init__()
            self.stack = []

        def handle_starttag(self, tag, attrs):
            if tag not in self.VOID:
                self.stack.append(tag)

        def handle_startendtag(self, tag, attrs):
            pass

        def handle_endtag(self, tag):
            assert self.stack and self.stack.pop() == tag, tag

    check = Check()
    check.feed(written)
    assert not check.stack


def test_fix_is_shown_as_code_decided_it(written):
    assert 'class="verdict fix"' in written and ">FIX<" in written and ">SHIP<" not in written
    assert "Weighted score 3.20, under 3.8" in written


def test_ship_is_shown_as_code_decided_it(film):
    report(film.project, film.file, verdict="SHIP", reasons=[])
    html = page.write(film.project, film.file).read_text()
    assert 'class="verdict ship"' in html and ">SHIP<" in html and ">FIX<" not in html


def test_the_page_copies_the_verdict_and_never_works_one_out(film):
    """A failed gate beside a saved SHIP is shown as saved. Only code in rubric.py decides."""
    report(film.project, film.file, verdict="SHIP", reasons=[], gates=[gate("loudness", passed=False)])
    assert ">SHIP<" in page.write(film.project, film.file).read_text()


def test_no_review_is_no_verdict(film):
    (film.project.review_dir / "report.json").unlink(missing_ok=True)
    (film.project.review_dir / "PACK.md").unlink(missing_ok=True)
    html = page.write(film.project, film.file).read_text()
    assert ">SHIP<" not in html and ">FIX<" not in html and "No verdict yet" in html and "altocue review" in html


def test_a_pack_waiting_says_so(film):
    (film.project.review_dir / "report.json").unlink(missing_ok=True)
    (film.project.review_dir / "PACK.md").write_text("pack")
    html = page.write(film.project, film.file).read_text()
    (film.project.review_dir / "PACK.md").unlink()
    assert "--ingest review/verdict.json" in html and ">SHIP<" not in html


def test_a_verdict_on_another_render_is_not_shown_as_this_films(film):
    report(film.project, film.file, verdict="SHIP", file_sha256="0" * 64)
    html = page.write(film.project, film.file).read_text()
    assert ">SHIP<" not in html and "earlier render" in html


def test_gates_from_another_render_are_not_shown(film):
    (film.project.review_dir / "report.json").unlink(missing_ok=True)
    write_json(film.project.review_dir / "qa.json", {"sha256": "0" * 64, "gates": GATES})
    html = page.write(film.project, film.file).read_text()
    assert "black_frames" not in html and "altocue qa" in html


def test_every_gate_is_shown_and_a_skip_is_never_a_pass(written):
    for name in ("loudness", "black_frames", "rules_ocr", "inserts"):
        assert name in written
    row = re.search(r'<li class="gate[^>]*>(?:(?!</li>).)*rules_ocr(?:(?!</li>).)*</li>', written, re.DOTALL).group(0)
    assert "NOT RUN" in row and "PASS" not in row
    row = re.search(r'<li class="gate[^>]*>(?:(?!</li>).)*inserts(?:(?!</li>).)*</li>', written, re.DOTALL).group(0)
    assert "N/A" in row and "PASS" not in row


def test_failed_gates_come_first(written):
    assert written.index("black_frames") < written.index("rules_ocr") < written.index("loudness") < written.index("No B-roll")


def test_a_gate_with_a_moment_can_be_jumped_to(written):
    assert re.search(r'<button[^>]*data-at="2\.0"[^>]*>[^<]*00:02\.00', written)


def test_clicking_a_note_jumps_to_its_moment(written):
    button = re.search(r'<button[^>]*class="[^"]*note-jump[^"]*"[^>]*>', written).group(0)
    assert 'data-at="3.0"' in button
    assert "The thought is chopped in half here." in written and "Hold the shot." in written


def test_every_caption_is_listed_with_its_time(film):
    report(film.project, film.file)
    html = page.write(film.project, film.file).read_text()
    pages = json.loads(film.project.timeline_path.read_text())["captions"]
    assert pages
    for caption in pages:
        assert f'data-at="{caption["start"]}"' in html
        assert " ".join(word["text"] for word in caption["words"]) in html


def test_the_timeline_carries_cuts_waveform_and_markers(written):
    assert written.count('class="tick cut"') == 2
    assert re.search(r'<path d="M0[^"]+"', written)
    assert 'class="mark note' in written and 'class="mark gate' in written


def test_what_the_review_could_not_see_is_on_the_page(written):
    assert "The reviewer heard nothing." in written


def test_a_note_cannot_inject_anything(film):
    hostile = {**NOTE, "observed": '<script>alert("x")</script>', "suggested_fix": '"><img src=x onerror=alert(1)>'}
    report(film.project, film.file, notes=[hostile])
    html = page.write(film.project, film.file).read_text()
    assert "<script>alert" not in html and "<img src=x" not in html


def test_a_caption_cannot_inject_anything(film):
    timeline = json.loads(film.project.timeline_path.read_text())
    saved = film.project.timeline_path.read_text()
    timeline["captions"][0]["words"][0]["text"] = "</script><b>x"
    write_json(film.project.timeline_path, timeline)
    report(film.project, film.file)
    html = page.write(film.project, film.file).read_text()
    film.project.timeline_path.write_text(saved)
    assert "</script><b>" not in html


def test_the_page_has_what_a_screen_reader_needs(written):
    assert re.search(r'<html lang="en', written)
    assert re.search(r"<title>[^<]+</title>", written)
    assert "<main" in written and "<h1" in written and "<h2" in written
    assert 'aria-live="polite"' in written
    video = only_tag(written, "video")[0]
    assert "controls" in video and "aria-label" in video
    assert 'name="viewport"' in written


def test_a_motion_preference_is_respected():
    assert "prefers-reduced-motion" in page.CSS.read_text()


def test_the_copy_runs_under_the_heavy_lock(film, monkeypatch):
    used = []
    real = locks.heavy

    def spy(label):
        used.append(label)
        return real(label)

    monkeypatch.setattr(page.locks, "heavy", spy)
    report(film.project, film.file)
    page.write(film.project, film.file)
    assert used


def test_a_long_film_gets_a_thinner_copy():
    assert page.video_rate(30.0) == page.COPY_MOST_KBPS
    assert page.video_rate(3600.0) == page.COPY_LEAST_KBPS
    assert page.COPY_LEAST_KBPS < page.video_rate(900.0) < page.COPY_MOST_KBPS


def test_no_film_says_how_to_make_one(film, tmp_path):
    with pytest.raises(AltocueError, match="altocue render"):
        page.write(film.project, tmp_path / "missing.mp4")


def test_the_command_writes_prints_the_path_and_opens_nothing(film, monkeypatch, capsys):
    """Altocue never opens an application window, editor or browser on its own."""
    monkeypatch.setattr(qa, "run_gates", lambda *_, **__: pytest.fail("the page only reads what was saved"))
    report(film.project, film.file)
    assert main(["review", "--page", "-p", str(film.project.root)]) == 0
    assert str(film.project.review_dir / "page.html") in capsys.readouterr().out


def test_there_is_no_flag_that_opens_a_browser(film, capsys):
    report(film.project, film.file)
    with pytest.raises(SystemExit) as stopped:
        main(["review", "--page", "--open", "-p", str(film.project.root)])
    assert stopped.value.code == 2
    assert not hasattr(page, "show")


def test_no_code_in_altocue_can_start_a_browser():
    source = Path(page.__file__).parents[1]
    found = [str(path.relative_to(source)) for path in source.rglob("*.py") if re.search(r"\bwebbrowser\b", path.read_text())]
    assert found == []


def test_the_page_stays_off_the_reviewers_list():
    from altocue.review import pack

    assert "review/page.html" in pack.FORBIDDEN


def test_the_page_file_never_lives_outside_review(film):
    assert Path(page.path(film.project)).parent == film.project.review_dir
