"""Who said each word: optional, local, and off until asked for."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from test_cut import silent_project, timed

from altocue import brand, caption, cut, doctor, locks, setup, speakers
from altocue import transcribe as transcribing
from altocue.cli import main
from altocue.errors import AltocueError
from altocue.models import Clip, Edl, Segment, Word
from altocue.motion import basic, html
from altocue.project import Project
from altocue.speakers import Turn, assign


@pytest.fixture(autouse=True)
def no_waiting(request, monkeypatch):
    """A long render elsewhere can hold the real lock, and nothing here is heavy. So a real speech model fails the test."""
    if request.node.get_closest_marker("slow"):
        return
    monkeypatch.setattr(speakers.locks, "heavy", lambda label: nullcontext())
    monkeypatch.setattr(transcribing, "heavy", lambda label: nullcontext())
    monkeypatch.setattr(transcribing, "load_parakeet", lambda name: pytest.fail("a test with no lock must not load the speech model"))


def said(*spans: tuple[str, float, float]) -> list[Word]:
    return [Word(text=text, start=start, end=end) for text, start, end in spans]


class Fake:
    name = "fake"

    def __init__(self, turns: list[Turn]) -> None:
        self.found = turns
        self.asked: list[int] = []

    def available(self) -> bool:
        return True

    def install_fix(self) -> str:
        return ""

    def turns(self, wav: Path, *, count: int = 0) -> list[Turn]:
        self.asked.append(count)
        return self.found


def test_a_word_starts_with_no_speaker_and_keeps_one_through_json():
    assert Word(text="a", start=0.0, end=0.1).speaker == ""
    assert Word.from_dict(Word(text="a", start=0.0, end=0.1, speaker="S2").to_dict()).speaker == "S2"


def test_each_word_goes_to_the_turn_it_overlaps_most():
    words = said(("one", 0.0, 0.5), ("two", 0.9, 1.3), ("three", 2.0, 2.4))
    found = assign(words, [Turn(0.0, 1.0, "b"), Turn(1.0, 3.0, "a")])
    assert [word.speaker for word in found] == ["S1", "S2", "S2"]


def test_names_follow_who_spoke_first():
    words = said(("one", 0.0, 0.5), ("two", 1.0, 1.5))
    assert [w.speaker for w in assign(words, [Turn(1.0, 2.0, "x"), Turn(0.0, 0.9, "y")])] == ["S1", "S2"]


def test_a_word_between_turns_takes_the_nearest_close_one():
    words = said(("near", 1.1, 1.3), ("far", 9.0, 9.4))
    found = assign(words, [Turn(0.0, 1.0, "a"), Turn(2.0, 3.0, "b")])
    assert [w.speaker for w in found] == ["S1", ""]


def test_assigning_keeps_every_other_field():
    words = [Word(text="um", start=0.0, end=0.2, confidence=0.5, kind="filler")]
    found = assign(words, [Turn(0.0, 1.0, "a")])[0]
    assert (found.text, found.confidence, found.kind) == ("um", 0.5, "filler")


def voiced(tmp_path: Path) -> Project:
    project = Project.create(tmp_path / "video")
    wav = tmp_path / "talk.wav"
    from altocue import ffmpeg

    ffmpeg.write_wav(wav, np.zeros(16000 * 3, dtype=np.float32), 16000)
    project.save_clips([Clip(id="talk", path=str(wav), duration=3.0, fps=30, width=1080, height=1920)])
    transcribing._save_words(project, "talk", said(("one", 0.1, 0.4), ("two", 1.6, 1.9)), "engine-name")
    return project


def test_labelling_a_project_keeps_its_words_and_engine(tmp_path):
    project = voiced(tmp_path)
    fake = Fake([Turn(0.0, 1.0, "a"), Turn(1.5, 2.0, "b")])
    found = speakers.label_project(project, diariser=fake, count=2)
    assert found == {"talk": {"S1": pytest.approx(0.3), "S2": pytest.approx(0.3)}}
    assert fake.asked == [2]
    saved = json.loads((project.words_dir / "talk.json").read_text())
    assert saved["engine"] == "engine-name"
    assert [w["speaker"] for w in saved["words"]] == ["S1", "S2"]
    assert json.loads((project.work / "speakers.json").read_text())["engine"] == "fake"


def test_labelling_says_where_nothing_could_be_told(tmp_path, capsys):
    project = voiced(tmp_path)
    speakers.label_project(project, diariser=Fake([]))
    assert "no speech" in capsys.readouterr().out.lower()


def test_a_clip_with_no_words_is_skipped(tmp_path):
    project = silent_project(tmp_path, timed("one two"))
    project.save_words("a", [], "test")
    assert speakers.label_project(project, diariser=Fake([Turn(0.0, 1.0, "a")])) == {}


def test_the_missing_package_names_its_extra(monkeypatch):
    monkeypatch.setattr(speakers.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(AltocueError) as error:
        speakers.select()
    assert "altocue[speakers]" in str(error.value.fix)


def test_missing_models_name_the_setup_step(monkeypatch, tmp_path):
    monkeypatch.setattr(speakers.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setenv("ALTOCUE_SPEAKER_MODELS", str(tmp_path))
    with pytest.raises(AltocueError) as error:
        speakers.select()
    assert "altocue setup --speakers" in str(error.value.fix)


def test_transcribe_labels_when_asked(tmp_path, monkeypatch, capsys):
    project = voiced(tmp_path)
    monkeypatch.setattr(speakers, "select", lambda: Fake([Turn(0.0, 3.0, "a")]))
    assert main(["transcribe", "--project", str(project.root), "--speakers", "2"]) == 0
    assert project.words("talk")[0].speaker == "S1"
    assert "S1" in capsys.readouterr().out


def test_transcribe_alone_stays_off(tmp_path, monkeypatch):
    project = voiced(tmp_path)
    monkeypatch.setattr(speakers, "select", lambda: pytest.fail("speakers must stay off until asked for"))
    assert main(["transcribe", "--project", str(project.root)]) == 0


def test_every_model_states_its_licence_and_a_checksum():
    assert sum(model.megabytes for model in speakers.MODELS) < 40
    for model in speakers.MODELS:
        assert model.licence and len(model.sha256) == 64 and model.url.startswith("https://github.com/k2-fsa/sherpa-onnx/releases/")


def test_no_model_is_trained_on_voxceleb():
    assert not any("voxceleb" in model.url.lower() or "voxceleb" in model.file.lower() for model in speakers.MODELS)


def archive(member: str, body: bytes, licence: bytes) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:bz2") as tar:
        for name, data in ((member, body), (member.rsplit("/", 1)[0] + "/LICENSE", licence), ("evil/../../outside.txt", b"no")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return stream.getvalue()


class Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def fake_models(monkeypatch, tmp_path: Path, *, tamper: bool = False) -> None:
    bodies = {}
    rows = []
    for model in speakers.MODELS:
        data = archive(model.member, b"weights", b"MIT License") if model.member else b"weights"
        rows.append(SimpleNamespace(**{**vars(model), "sha256": hashlib.sha256(data).hexdigest()}))
        bodies[model.url] = data + (b"x" if tamper else b"")
    monkeypatch.setattr(speakers, "MODELS", tuple(rows))
    monkeypatch.setattr(setup.urllib.request, "urlopen", lambda url, timeout: Answer(bodies[url]))
    monkeypatch.setenv("ALTOCUE_SPEAKER_MODELS", str(tmp_path / "models"))


def test_setup_fetches_checks_and_keeps_the_notice(monkeypatch, tmp_path, capsys):
    fake_models(monkeypatch, tmp_path)
    setup.install_speakers(True)
    folder = tmp_path / "models"
    assert sorted(path.name for path in folder.iterdir()) == sorted(["segmentation-LICENSE", *(model.file for model in speakers.MODELS)])
    assert (folder / "segmentation-LICENSE").read_bytes() == b"MIT License"
    assert not (tmp_path / "outside.txt").exists()
    out = capsys.readouterr().out
    assert "MIT" in out and "Apache-2.0" in out


def test_a_bad_checksum_leaves_nothing_behind(monkeypatch, tmp_path):
    fake_models(monkeypatch, tmp_path, tamper=True)
    with pytest.raises(AltocueError, match="checksum"):
        setup.install_speakers(True)
    folder = tmp_path / "models"
    assert not folder.exists() or not list(folder.iterdir())


def test_setup_asks_before_it_fetches(monkeypatch, tmp_path, capsys):
    fake_models(monkeypatch, tmp_path)
    monkeypatch.setattr(setup, "consent", lambda question, yes: False)
    setup.install_speakers(False)
    assert not (tmp_path / "models").exists()


def test_doctor_reports_speakers_as_optional(monkeypatch, tmp_path):
    monkeypatch.setenv("ALTOCUE_SPEAKER_MODELS", str(tmp_path))
    check = doctor.check_speakers()
    assert not check.ok and not check.required and "speakers" in check.fix


# Captions


def turns_project(tmp_path: Path, **config) -> Project:
    words = [*timed("We plan the work", start=0.0), *timed("and we ship it", start=1.2)]
    for index, word in enumerate(words):
        word.speaker = "S1" if index < 4 else "S2"
    project = silent_project(tmp_path, words)
    project.config.setdefault("speakers", {}).update(config)
    edl = Edl(fps=30, segments=[Segment("s001", "a", 0.0, words[-1].end + 0.1)])
    project.save_edl(edl)
    return project


def test_mapped_words_carry_the_speaker(tmp_path):
    project = turns_project(tmp_path)
    mapped = cut.map_words(project, project.edl())
    assert {word["speaker"] for word in mapped} == {"S1", "S2"}


def test_a_caption_page_never_mixes_speakers(tmp_path):
    project = turns_project(tmp_path)
    pages = caption.build(project)
    who = {word["text"]: page["speaker"] for page in pages for word in page["words"]}
    assert who["work"] == "S1" and who["and"] == "S2"
    assert {page["speaker"] for page in pages} == {"S1", "S2"}
    edl = project.edl()
    words = [word for word in cut.map_words(project, edl)]
    pages = caption.build_pages(words, max_words=8, combine_ms=5000)
    assert len(pages) == 2 and [len(page["words"]) for page in pages] == [4, 4]


def test_captions_look_the_same_until_asked(tmp_path):
    project = turns_project(tmp_path)
    assert not any("colour" in page or "name" in page for page in caption.build(project))


def test_colour_marks_each_speaker(tmp_path):
    project = turns_project(tmp_path, show="colour")
    pages = caption.build(project)
    colours = {page["speaker"]: page.get("colour") for page in pages}
    assert colours["S1"] != colours["S2"]


def test_a_name_shows_when_the_speaker_changes(tmp_path):
    project = turns_project(tmp_path, show="name", S1="Ada", S2="Grace")
    pages = caption.build(project)
    named = [page["name"] for page in pages if page.get("name")]
    assert named == ["Ada", "Grace"]


def test_a_speaker_with_no_name_keeps_their_id(tmp_path):
    project = turns_project(tmp_path, show="name", S1="Ada")
    assert [page.get("name") for page in caption.build(project) if page.get("name")] == ["Ada", "S2"]


def test_a_brand_can_choose_the_colours(tmp_path):
    project = turns_project(tmp_path, show="colour")
    (project.root / "house.json").write_text('{"caption": {"speaker_colours": ["#111111", "#222222"]}}')
    project.config["brand"]["name"] = "house.json"
    assert {page["colour"] for page in caption.build(project)} == {"#111111", "#222222"}


def test_an_unknown_show_setting_is_refused(tmp_path):
    project = turns_project(tmp_path, show="glitter")
    with pytest.raises(AltocueError, match="glitter"):
        caption.build(project)


def page(**extra) -> dict:
    return {"start": 0.0, "end": 1.0, "segment": "s001", "words": [{"text": "Hello", "start": 0.0, "end": 0.5}, {"text": "there", "start": 0.5, "end": 1.0}], **extra}


def test_the_graphics_layer_draws_colour_and_name():
    out = html.caption_html(page(colour="#ffd08a", name="Ada"), "clean", 1)
    assert "#ffd08a" in out and "Ada" in out


def test_the_graphics_layer_escapes_a_name():
    assert "<script>" not in html.caption_html(page(name="<script>"), "clean", 1)


def test_the_plain_renderer_draws_colour_and_name(tmp_path):
    project = turns_project(tmp_path)
    style = brand.load(project)
    plain = np.asarray(basic.caption_frame((1080, 1920), page(), 0, style))
    coloured = np.asarray(basic.caption_frame((1080, 1920), page(colour="#ffd08a"), 0, style))
    named = np.asarray(basic.caption_frame((1080, 1920), page(name="Ada"), 0, style))
    assert (plain != coloured).any() and (plain != named).any()


@pytest.mark.slow
def test_two_voices_come_apart(tmp_path):
    """Needs the extra, the models and macOS `say`, so it stays out of the normal run. Short turns need the count given."""
    import shutil
    import subprocess

    if not shutil.which("say") or not os.environ.get("ALTOCUE_SPEAKER_MODELS"):
        pytest.skip("needs macOS say and ALTOCUE_SPEAKER_MODELS")
    from altocue import ffmpeg

    parts = []
    truth = []
    at = 0.0
    lines = [
        ("Samantha", "The first voice says a few plain things about the weather today."),
        ("Daniel", "The second voice answers with something about the trains and the road."),
    ]
    for turn in range(6):
        voice, line = lines[turn % 2]
        aiff = tmp_path / f"{turn}.aiff"
        subprocess.run(["say", "-v", voice, "-o", str(aiff), line], check=True)
        wav = ffmpeg.extract_wav(aiff, tmp_path / f"{turn}.wav", rate=16000)
        samples = ffmpeg.read_wav(wav)[0]
        parts += [samples, np.zeros(8000, dtype=np.float32)]
        truth.append((at, at + len(samples) / 16000, turn % 2))
        at += len(samples) / 16000 + 0.5
    wav = ffmpeg.write_wav(tmp_path / "both.wav", np.concatenate(parts), 16000)
    with locks.heavy("telling two voices apart"):
        found = speakers.select().turns(wav, count=2)
    assert len({turn.speaker for turn in found}) == 2
    middle = [word.speaker for word in assign([Word(text="x", start=(a + b) / 2, end=(a + b) / 2 + 0.2) for a, b, _ in truth], found)]
    assert middle[0] == middle[2] == middle[4] != middle[1] == middle[3] == middle[5]


def test_a_long_recording_is_cut_into_windows_with_no_short_tail():
    samples = np.zeros(int((speakers.WINDOW * 3 + 40) * speakers.RATE), dtype=np.float32)
    cut = speakers.windows(samples)
    assert [round(offset) for offset, _ in cut] == [0, 300, 600]
    assert sum(len(chunk) for _, chunk in cut) == len(samples)


def test_a_long_tail_keeps_its_own_window():
    samples = np.zeros(int((speakers.WINDOW * 2 + 200) * speakers.RATE), dtype=np.float32)
    assert len(speakers.windows(samples)) == 3


def voice(direction: int, noise: float, seed: int) -> np.ndarray:
    base = np.zeros(16, dtype=np.float32)
    base[direction] = 1.0
    return base + np.random.default_rng(seed).normal(0, noise, 16).astype(np.float32)


def test_the_same_voice_in_every_window_links_to_one_speaker():
    vectors = [voice(0, 0.1, 1), voice(1, 0.1, 2), voice(0, 0.1, 3), voice(1, 0.1, 4), voice(1, 0.1, 5), voice(0, 0.1, 6)]
    labels = speakers.link(vectors, [60.0] * 6, count=2)
    assert labels[0] == labels[2] == labels[5] != labels[1] == labels[3] == labels[4]


def test_without_a_count_distinct_voices_stay_apart_and_alike_ones_merge():
    vectors = [voice(0, 0.05, 1), voice(0, 0.05, 2), voice(1, 0.05, 3), voice(2, 0.05, 4)]
    labels = speakers.link(vectors, [30.0] * 4)
    assert labels[0] == labels[1] and len(set(labels)) == 3


def test_a_short_voice_joins_the_nearest_speaker_instead_of_starting_one():
    vectors = [voice(0, 0.05, 1), voice(1, 0.05, 2), voice(2, 0.05, 3), voice(1, 0.3, 4)]
    labels = speakers.link(vectors, [60.0, 60.0, 1.0, 2.0])
    assert len(set(labels)) == 2 and labels[3] == labels[1]


def test_linking_nothing_gives_nothing():
    assert speakers.link([], []) == []
