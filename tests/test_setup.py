"""Setup asks before it fetches, says what a download is and how big, and a slow one says how far it is."""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
from types import SimpleNamespace

import pytest

from altocue import clean, gaze, setup, transcribe
from altocue.errors import AltocueError


class Reply(io.BytesIO):
    """A download that arrives in pieces, with its length up front when the server gives one."""

    def __init__(self, body: bytes, *, length: bool = True):
        super().__init__(body)
        if length:
            self.headers = {"Content-Length": str(len(body))}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def serve(monkeypatch, body: bytes, **options) -> list[str]:
    asked: list[str] = []
    monkeypatch.setattr(setup.urllib.request, "urlopen", lambda url, timeout: asked.append(url) or Reply(body, **options))
    return asked


def clock(monkeypatch, step: float) -> None:
    """Every look at the clock is `step` seconds after the last."""
    now = iter(index * step for index in range(10_000))
    monkeypatch.setattr(setup.time, "monotonic", lambda: next(now))


def test_a_download_says_its_size_first_and_a_quick_one_says_no_more(monkeypatch, capsys):
    body = bytes(range(256)) * 1024  # four pieces
    serve(monkeypatch, body)
    assert setup.download("https://example.com/tool", "deep-filter", timeout=5) == body
    said = capsys.readouterr()
    assert said.out == "Downloading deep-filter, 0.3 MB.\n" and said.err == ""


def test_a_slow_download_says_how_far_it_is_every_few_seconds(monkeypatch, capsys):
    body = b"x" * (10 << 16)  # ten pieces
    serve(monkeypatch, body)
    clock(monkeypatch, 2.0)  # two seconds a piece
    assert setup.download("https://example.com/model", "the speaker model", timeout=5) == body
    said = capsys.readouterr()
    assert said.out == "Downloading the speaker model, 0.7 MB.\n"
    assert said.err.splitlines() == [
        "the speaker model: 0.2 MB of 0.7 MB, 30%",  # six seconds in
        "the speaker model: 0.4 MB of 0.7 MB, 60%",
        "the speaker model: 0.6 MB of 0.7 MB, 90%",
    ]


def test_a_download_with_no_length_still_counts_up(monkeypatch, capsys):
    serve(monkeypatch, b"x" * (4 << 16), length=False)
    clock(monkeypatch, 3.0)
    setup.download("https://example.com/model", "the model", timeout=5)
    said = capsys.readouterr()
    assert said.out == "Downloading the model.\n"
    assert said.err.splitlines() == ["the model: 0.1 MB so far", "the model: 0.3 MB so far"]


def no_denoiser(monkeypatch, tmp_path, body: bytes) -> None:
    monkeypatch.setattr(clean, "find_isolator", lambda: None)
    monkeypatch.setattr(clean, "find_deep_filter", lambda: None)
    monkeypatch.setattr(setup.platform, "system", lambda: "Linux")
    monkeypatch.setattr(setup.platform, "machine", lambda: "x86_64")
    monkeypatch.setitem(clean.DEEP_FILTER_RELEASES, "linux-x86_64", {"url": "https://example.com/deep-filter-x86_64", "sha256": hashlib.sha256(body).hexdigest()})
    monkeypatch.setattr(setup, "HOME", tmp_path / "home")


def test_the_denoiser_is_fetched_only_on_a_yes_and_named_as_it_comes(monkeypatch, tmp_path, capsys):
    no_denoiser(monkeypatch, tmp_path, b"a binary")
    asked = serve(monkeypatch, b"a binary")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    setup.install_denoiser(yes=False)
    assert asked == [] and "Skipped" in capsys.readouterr().out
    setup.install_denoiser(yes=True)
    assert asked == ["https://example.com/deep-filter-x86_64"]
    assert (tmp_path / "home" / "bin" / "deep-filter").read_bytes() == b"a binary"
    assert "Downloading deep-filter, 0.0 MB." in capsys.readouterr().out


def test_a_denoiser_that_fails_its_checksum_is_not_kept(monkeypatch, tmp_path):
    no_denoiser(monkeypatch, tmp_path, b"a binary")
    serve(monkeypatch, b"another binary")
    with pytest.raises(AltocueError, match="checksum"):
        setup.install_denoiser(yes=True)
    assert not (tmp_path / "home" / "bin" / "deep-filter").exists()


def test_the_face_model_is_named_as_it_comes(monkeypatch, tmp_path, capsys):
    body = b"a face model"
    serve(monkeypatch, body)
    monkeypatch.setattr(gaze, "MODEL_PATH", tmp_path / "models" / "face.task")
    monkeypatch.setattr(gaze, "MODEL_SHA256", hashlib.sha256(body).hexdigest())
    assert gaze.model().read_bytes() == body
    assert "Downloading the face landmarker model, 0.0 MB." in capsys.readouterr().out


def speech(monkeypatch, *, on_disk: bool = False, gpus: int = 0, fails: bool = False) -> list[tuple[str, bool]]:
    """A stand-in for faster-whisper's downloader: it records each call, and knows only what is on disk."""
    calls: list[tuple[str, bool]] = []
    disk = {"small.en", "large-v3-turbo"} if on_disk else set()

    def download_model(name, local_files_only=False):
        calls.append((name, local_files_only))
        if local_files_only and name not in disk:
            raise FileNotFoundError(f"{name} is not in the cache")
        if fails and not local_files_only:
            raise OSError("no route to host")
        disk.add(name)
        return f"/cache/{name}"

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(download_model=download_model))
    monkeypatch.setitem(sys.modules, "ctranslate2", SimpleNamespace(get_cuda_device_count=lambda: gpus))
    monkeypatch.delenv("ALTOCUE_WHISPER_DEVICE", raising=False)
    monkeypatch.setattr(transcribe, "select", lambda engine=None: transcribe.ENGINES["faster-whisper"])
    return calls


def test_the_speech_model_is_fetched_only_on_a_yes(monkeypatch, capsys):
    calls = speech(monkeypatch)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    setup.install_speech_model(yes=False)
    assert calls == [("small.en", True)]  # it looked on disk, and fetched nothing
    out = capsys.readouterr().out
    assert "Skipped: Download the small.en speech model for faster-whisper from huggingface.co, about 480 MB?" in out
    setup.install_speech_model(yes=True)
    assert calls[-1] == ("small.en", False)
    out = capsys.readouterr().out
    assert "Downloading the small.en speech model from huggingface.co, about 480 MB." in out and "Speech model small.en downloaded." in out
    setup.install_speech_model(yes=True)
    assert calls[-1] == ("small.en", True) and "already downloaded" in capsys.readouterr().out


def test_a_machine_with_a_card_fetches_the_model_it_will_load_and_claims_no_size(monkeypatch, capsys):
    calls = speech(monkeypatch, gpus=1)
    setup.install_speech_model(yes=True)
    assert calls == [("large-v3-turbo", True), ("large-v3-turbo", False)]
    assert "about" not in capsys.readouterr().out


def test_a_speech_model_that_cannot_be_fetched_says_what_to_do(monkeypatch):
    speech(monkeypatch, fails=True)
    with pytest.raises(AltocueError, match="no route to host") as caught:
        setup.install_speech_model(yes=True)
    assert "huggingface.co" in caught.value.fix and "altocue setup --speech-model" in caught.value.fix


def test_an_engine_that_can_only_download_by_loading_is_left_to_its_first_run(monkeypatch, capsys):
    calls = speech(monkeypatch)
    monkeypatch.setattr(transcribe, "select", lambda engine=None: transcribe.ENGINES["parakeet"])
    setup.install_speech_model(yes=True)
    assert calls == []
    assert "Skipped the speech model: parakeet fetches its model, about 2.5 GB, the first time it transcribes." in capsys.readouterr().out

    def none(engine=None):
        raise AltocueError("No local transcription engine is installed.", fix="Run `uv tool install`.")

    monkeypatch.setattr(transcribe, "select", none)
    setup.install_speech_model(yes=True)
    assert "Skipped the speech model: No local transcription engine is installed. Run `uv tool install`." in capsys.readouterr().out


def plain_setup(monkeypatch, **flags) -> argparse.Namespace:
    from altocue import doctor

    monkeypatch.setattr(doctor, "check_ffmpeg", lambda: SimpleNamespace(ok=True, detail="", fix=""))
    given = {"yes": True, "skip_denoiser": True, "deep_filter": False, "skip_renderer": True, "speakers": False, "plugin": False, "speech_model": False}
    return argparse.Namespace(**{**given, **flags})


def test_setup_offers_the_speech_model_and_fetches_it_only_when_asked(monkeypatch, capsys):
    """`--yes` alone still means the renderer and the noise remover. The speech model has its own flag, as speakers do."""
    calls = speech(monkeypatch)
    assert setup.run(plain_setup(monkeypatch)) == 0
    assert calls == [("small.en", True)]
    assert "The small.en speech model downloads the first time you transcribe. `altocue setup --speech-model` fetches it now." in capsys.readouterr().out
    assert setup.run(plain_setup(monkeypatch, speech_model=True)) == 0
    assert calls[-1] == ("small.en", False)
    capsys.readouterr()
    setup.run(plain_setup(monkeypatch))
    assert "speech model" not in capsys.readouterr().out  # on disk now, so nothing to offer
