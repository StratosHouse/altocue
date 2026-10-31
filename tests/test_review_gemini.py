"""The Gemini reviewer, against a fake server. Nothing here touches the network."""

from __future__ import annotations

import io
import json
import socket
import tomllib
import urllib.error
from pathlib import Path

import pytest

from altocue.errors import AltocueError
from altocue.project import Project
from altocue.review import gemini, rubric

KEY = "test-key-value"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to reach the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)


class Reply(io.BytesIO):
    def __init__(self, body: bytes = b"{}", headers: dict[str, str] | None = None):
        super().__init__(body)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Server:
    """Plays the Files API and the Interactions API, and records every request."""

    def __init__(self, answer: dict | None = None, states: tuple[str, ...] = ("PROCESSING", "ACTIVE")):
        self.requests: list[dict] = []
        self.states = list(states)
        self.answer = answer or {"scores": {k: {"score": 4, "why": "ok"} for k in rubric.DIMENSIONS}, "notes": [], "could_not_judge": []}
        self.fail_generate: Exception | None = None

    def __call__(self, request, timeout=None):
        url, method = request.full_url, request.get_method()
        body = request.data
        if hasattr(body, "read"):
            body = b"<stream>"
        self.requests.append({"url": url, "method": method, "headers": dict(request.header_items()), "body": body})
        if url.endswith("/upload/v1beta/files"):
            return Reply(b"{}", {"x-goog-upload-url": "https://upload.example/session"})
        if url == "https://upload.example/session":
            return Reply(json.dumps({"file": {"name": "files/abc", "uri": "https://files/abc", "state": "PROCESSING"}}).encode())
        if url.endswith("/v1beta/files/abc") and method == "GET":
            return Reply(json.dumps({"name": "files/abc", "state": self.states.pop(0)}).encode())
        if url.endswith("/v1beta/files/abc") and method == "DELETE":
            return Reply(b"{}")
        if url.endswith("/v1beta/interactions"):
            if self.fail_generate:
                raise self.fail_generate
            text = json.dumps(self.answer)
            return Reply(
                json.dumps(
                    {
                        "status": "completed",
                        "steps": [
                            {"type": "thought", "content": []},
                            {"type": "model_output", "content": [{"type": "text", "text": text}]},
                        ],
                    }
                ).encode()
            )
        raise AssertionError(f"unexpected request: {method} {url}")

    def sent_to(self, ending: str) -> dict:
        return next(r for r in self.requests if r["url"].endswith(ending))


@pytest.fixture
def project(tmp_path):
    return Project.create(tmp_path / "p")


@pytest.fixture
def file(tmp_path):
    path = tmp_path / "final.mp4"
    path.write_bytes(b"not really a video")
    return path


@pytest.fixture
def server(monkeypatch):
    fake = Server()
    monkeypatch.setattr(gemini.urllib.request, "urlopen", fake)
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    return fake


def run(project, file, **over):
    args = {"duration": 30.0, "prompt": "review this", "upload": True, "paid": True, "say": lambda text: None}
    return gemini.review(project, file, **{**args, **over})


def test_model_id_lives_in_data_and_is_dated():
    data = tomllib.loads((Path(gemini.__file__).parent / "models.toml").read_text())
    assert data["checked"] == "2026-10-30"
    assert data["gemini"]["model"] == "gemini-3.8-flash"
    assert data["gemini"]["model"] not in Path(gemini.__file__).read_text()
    assert all(u.startswith("https://ai.google.dev/") for u in data["gemini"]["sources"])


def test_no_upload_without_opt_in(project, file, server):
    with pytest.raises(AltocueError) as error:
        run(project, file, upload=False)
    assert "--upload" in str(error.value) and not server.requests


def test_no_upload_without_a_paid_key_declared(project, file, server):
    with pytest.raises(AltocueError) as error:
        run(project, file, paid=False)
    assert "--paid-key" in str(error.value) and not server.requests


def test_consent_can_come_from_project_config(project, file, server):
    project.config["review"].update(allow_upload=True, paid_key=True)
    raw, info = run(project, file, upload=False, paid=False)
    assert raw["scores"] and info["vendor"] == "google"


def test_missing_key_is_a_clear_error(project, file, server, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    with pytest.raises(AltocueError) as error:
        run(project, file)
    assert "GEMINI_API_KEY" in str(error.value) and not server.requests


def test_warning_prints_before_any_byte_leaves(project, file, server):
    seen = []
    run(project, file, say=lambda text: seen.append((text, len(server.requests))))
    assert seen and seen[0][1] == 0
    text = " ".join(t for t, _ in seen)
    assert "Google" in text and "free tier" in text.lower() and file.name in text and "$" in text


def test_upload_follows_the_documented_resumable_flow(project, file, server):
    run(project, file)
    start = server.sent_to("/upload/v1beta/files")
    headers = {k.lower(): v for k, v in start["headers"].items()}
    assert headers["x-goog-api-key"] == KEY
    assert headers["x-goog-upload-protocol"] == "resumable" and headers["x-goog-upload-command"] == "start"
    assert headers["x-goog-upload-header-content-length"] == str(file.stat().st_size)
    assert headers["x-goog-upload-header-content-type"] == "video/mp4"
    send = next(r for r in server.requests if r["url"] == "https://upload.example/session")
    sent = {k.lower(): v for k, v in send["headers"].items()}
    assert sent["x-goog-upload-offset"] == "0" and sent["x-goog-upload-command"] == "upload, finalize"
    assert str(file.stat().st_size) == sent["content-length"]


def test_waits_for_active_then_asks(project, file, server):
    run(project, file)
    urls = [(r["method"], r["url"].rsplit("/", 1)[-1]) for r in server.requests]
    assert urls.index(("GET", "abc")) < urls.index(("POST", "interactions"))
    assert urls.count(("GET", "abc")) == 2


def test_failed_processing_stops(project, file, monkeypatch):
    fake = Server(states=("FAILED",))
    monkeypatch.setattr(gemini.urllib.request, "urlopen", fake)
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    with pytest.raises(AltocueError):
        run(project, file)
    assert ("DELETE", "abc") in [(r["method"], r["url"].rsplit("/", 1)[-1]) for r in fake.requests]


def test_request_matches_the_interactions_docs(project, file, server):
    run(project, file, duration=30.0)
    body = json.loads(server.sent_to("/v1beta/interactions")["body"])
    assert body["model"] == "gemini-3.8-flash"
    assert body["store"] is False
    video, text = body["input"]
    assert video == {
        "type": "video",
        "uri": "https://files/abc",
        "mime_type": "video/mp4",
        "resolution": "high",
        "processing": {"type": "static", "fps": 4},
    }
    assert text == {"type": "text", "text": "review this"}
    assert body["response_format"]["type"] == "text"
    assert body["response_format"]["mime_type"] == "application/json"
    assert body["response_format"]["schema"] == rubric.schema()


@pytest.mark.parametrize(("seconds", "fps"), [(30, 4), (90, 4), (200, 2), (1200, 1)])
def test_short_videos_get_more_frames(seconds, fps):
    assert gemini.sample_fps(seconds) == fps


def test_cost_is_estimated_from_data():
    tokens, usd = gemini.estimate(60.0, 4, gemini.settings()["gemini"])
    assert tokens > 60 * 4 * 200 and 0.01 < usd < 1


def test_uploaded_file_is_always_deleted(project, file, server):
    server.fail_generate = urllib.error.URLError("boom")
    with pytest.raises(AltocueError):
        run(project, file)
    assert ("DELETE", "abc") in [(r["method"], r["url"].rsplit("/", 1)[-1]) for r in server.requests]


def test_api_errors_never_echo_the_key(project, file, server):
    server.fail_generate = urllib.error.HTTPError("https://x", 400, "Bad", {}, io.BytesIO(f"bad key {KEY}".encode()))
    with pytest.raises(AltocueError) as error:
        run(project, file)
    assert KEY not in str(error.value) and "400" in str(error.value)


def test_blocked_response_is_an_error_not_a_pass(project, file, server, monkeypatch):
    def empty(request, timeout=None):
        if request.full_url.endswith("/interactions"):
            return Reply(json.dumps({"status": "failed", "steps": []}).encode())
        return server(request, timeout)

    monkeypatch.setattr(gemini.urllib.request, "urlopen", empty)
    with pytest.raises(AltocueError):
        run(project, file)


def test_non_json_answer_is_an_error(project, file, server, monkeypatch):
    monkeypatch.setattr(gemini, "read_answer", lambda data: "not json")
    with pytest.raises(AltocueError):
        run(project, file)


def test_reviewer_card_states_what_it_really_sampled(project, file, server):
    _, info = run(project, file, duration=30.0)
    assert info["vendor"] == "google" and info["sampled_fps"] == 4 and info["sees"] == "video"
    assert "1 Kbps" in info["audio_note"]


def test_two_gigabytes_is_the_limit(project, file, server, monkeypatch):
    monkeypatch.setattr(gemini, "file_size", lambda path: 3 * 1024**3)
    with pytest.raises(AltocueError):
        run(project, file)
    assert not server.requests


def test_ready_needs_all_three(project, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert not gemini.ready(project, upload=True, paid=True)
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    assert not gemini.ready(project, upload=False, paid=True)
    assert not gemini.ready(project, upload=True, paid=False)
    assert gemini.ready(project, upload=True, paid=True)
