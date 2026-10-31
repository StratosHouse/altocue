"""The Gemini reviewer: it sees the real video. Files API plus Interactions API, over urllib.

Nothing leaves the machine unless the user opted in. Each run names where the file is going
and what it should cost, before the first byte moves.
"""

from __future__ import annotations

import json
import os
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..errors import AltocueError
from ..project import Project
from . import rubric

MODELS = Path(__file__).with_name("models.toml")
MAX_BYTES = 2 * 1024**3  # the Files API page says 2 GB per file; the video page says 20 GB paid
POLL_SECONDS = 5
POLL_TRIES = 120
UPLOAD_TIMEOUT = 900
ANSWER_TIMEOUT = 600
MIME = {".mp4": "video/mp4", ".mov": "video/mov", ".webm": "video/webm", ".mpeg": "video/mpeg", ".mpg": "video/mpg"}
AUDIO_NOTE = "Audio reaches the model at about 1 Kbps mono: enough to follow words, not to judge sound quality."


@lru_cache(maxsize=1)
def settings() -> dict[str, Any]:
    return tomllib.loads(MODELS.read_text())


def file_size(path: Path) -> int:
    return path.stat().st_size


def consented(project: Project, upload: bool, paid: bool) -> tuple[bool, bool]:
    return (upload or bool(project.setting("review", "allow_upload", False)), paid or bool(project.setting("review", "paid_key", False)))


def require(project: Project, *, upload: bool, paid: bool) -> None:
    """Refuse before anything moves. Opting in is always the user's own act."""
    allowed, declared = consented(project, upload, paid)
    if not allowed:
        raise AltocueError(
            "Gemini review uploads the whole video to Google, and you have not opted in.",
            fix="Pass --upload, or set allow_upload = true under [review] in altocue.toml. Or use --reviewer pack, which uploads nothing.",
        )
    if not declared:
        raise AltocueError(
            "Gemini review needs a paid API key. On the free tier Google may use your content and people may read it.",
            fix="Pass --paid-key to confirm yours is on a billed project, or set paid_key = true under [review] in altocue.toml.",
        )
    if not os.environ.get("GEMINI_API_KEY"):
        raise AltocueError(
            "GEMINI_API_KEY is not set.", fix="Create a key at https://aistudio.google.com/apikey and export it, or use --reviewer pack."
        )


def ready(project: Project, *, upload: bool, paid: bool) -> bool:
    allowed, declared = consented(project, upload, paid)
    return allowed and declared and bool(os.environ.get("GEMINI_API_KEY"))


def sample_fps(duration: float) -> int:
    """Google samples 1 frame a second by default, which misses a short flash. Short videos get more."""
    if duration <= 90:
        return 4
    if duration <= 300:
        return 2
    return 1


def estimate(duration: float, fps: int, config: dict[str, Any]) -> tuple[int, float]:
    tokens = int(duration * (fps * config["tokens_per_frame"] + config["audio_tokens_per_second"])) + config["prompt_tokens"]
    usd = (tokens * config["price_input_per_million"] + config["answer_tokens"] * config["price_output_per_million"]) / 1e6
    return tokens, usd


def warning(file: Path, duration: float, fps: int, config: dict[str, Any]) -> str:
    tokens, usd = estimate(duration, fps, config)
    return "\n".join(
        [
            f"Uploading {file.name} to Google for review, model {config['model']}.",
            "  What goes: the whole video, picture and sound, to generativelanguage.googleapis.com.",
            "  How long: Altocue deletes it after the review. Otherwise Google deletes it after 48 hours.",
            "  On a paid key: Google says it does not train on it, and logs prompts and responses for a limited time for abuse checks.",
            "  On a free tier key: Google may use it to improve its products and people may read it.",
            "  Never use a free tier key for footage you were trusted with.",
            f"  Estimated cost: about ${usd:.3f} ({tokens:,} tokens, prices checked {settings()['checked']}). {config['price_note']}",
        ]
    )


def _clean(text: str, key: str) -> str:
    return text.replace(key, "[key]")[:400]


def _send(request: urllib.request.Request, key: str, *, timeout: int = 60) -> tuple[bytes, Any]:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            return reply.read(), reply.headers
    except urllib.error.HTTPError as error:
        body = _clean(error.read().decode("utf-8", "replace"), key)
        raise AltocueError(
            f"Gemini said {error.code}: {body}", fix="Check the key, its billing, and the model id in review/models.toml."
        ) from error
    except urllib.error.URLError as error:
        raise AltocueError(
            f"Could not reach Gemini: {_clean(str(error.reason), key)}", fix="Check your connection and try again."
        ) from error


def upload_file(path: Path, key: str, config: dict[str, Any]) -> dict[str, Any]:
    """The resumable upload from the Files API docs. The file's own name stays here."""
    size, mime = file_size(path), MIME.get(path.suffix.lower(), "video/mp4")
    start = urllib.request.Request(
        f"{config['endpoint']}/upload/v1beta/files",
        data=json.dumps({"file": {"display_name": "altocue-review"}}).encode(),
        method="POST",
        headers={
            "x-goog-api-key": key,
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(size),
            "X-Goog-Upload-Header-Content-Type": mime,
            "Content-Type": "application/json",
        },
    )
    _, headers = _send(start, key)
    target = headers.get("x-goog-upload-url")
    if not target:
        raise AltocueError("Gemini gave no upload address.", fix="Try again.")
    with path.open("rb") as handle:
        body, _ = _send(
            urllib.request.Request(
                target,
                data=handle,
                method="POST",
                headers={"Content-Length": str(size), "X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize"},
            ),
            key,
            timeout=UPLOAD_TIMEOUT,
        )
    return {**json.loads(body)["file"], "mime_type": mime}


def wait_until_active(name: str, key: str, config: dict[str, Any]) -> None:
    request = urllib.request.Request(f"{config['endpoint']}/v1beta/{name}", headers={"x-goog-api-key": key})
    for _ in range(POLL_TRIES):
        body, _ = _send(request, key)
        state = json.loads(body).get("state")
        if state == "ACTIVE":
            return
        if state == "FAILED":
            raise AltocueError("Gemini could not process the video.", fix="Check that the file plays, then try again.")
        time.sleep(POLL_SECONDS)
    raise AltocueError("Gemini took too long to process the video.", fix="Try again later.")


def delete(name: str, key: str, config: dict[str, Any]) -> None:
    request = urllib.request.Request(f"{config['endpoint']}/v1beta/{name}", method="DELETE", headers={"x-goog-api-key": key})
    try:
        _send(request, key)
    except AltocueError as error:
        print(f"Could not delete the upload, so Google will after 48 hours: {error}")


def request_body(file: dict[str, Any], fps: int, prompt: str, config: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": config["model"],
        "store": False,  # interactions are otherwise kept 55 days on a paid key
        "input": [
            {
                "type": "video",
                "uri": file["uri"],
                "mime_type": file["mime_type"],
                "resolution": config["media_resolution"],
                "processing": {"type": "static", "fps": fps},
            },
            {"type": "text", "text": prompt},
        ],
        "response_format": {"type": "text", "mime_type": "application/json", "schema": rubric.schema()},
    }


def read_answer(data: dict[str, Any]) -> str:
    """The text of the final step. Thoughts and tool steps are not the answer."""
    if data.get("status") != "completed":
        raise AltocueError(f"Gemini did not finish: {data.get('status')}.", fix="Try again, or use --reviewer pack.")
    return "".join(
        part.get("text", "")
        for step in data.get("steps", [])
        if step.get("type") == "model_output"
        for part in step.get("content", [])
        if part.get("type") == "text"
    )


def ask(file: dict[str, Any], fps: int, prompt: str, key: str, config: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{config['endpoint']}/v1beta/interactions",
        data=json.dumps(request_body(file, fps, prompt, config)).encode(),
        method="POST",
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
    )
    body, _ = _send(request, key, timeout=ANSWER_TIMEOUT)
    try:
        return json.loads(read_answer(json.loads(body)))
    except json.JSONDecodeError as error:
        raise AltocueError("Gemini's answer was not JSON.", fix="Try again, or use --reviewer pack.") from error


def review(
    project: Project, file: Path, *, duration: float, prompt: str, upload: bool, paid: bool, say: Callable[[str], None] = print
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Upload, wait, ask, delete. Returns the raw scores and an honest card for the reviewer."""
    require(project, upload=upload, paid=paid)
    config = settings()["gemini"]
    key = os.environ["GEMINI_API_KEY"]
    if file_size(file) > MAX_BYTES:
        raise AltocueError(f"{file.name} is over the 2 GB upload limit.", fix="Use --reviewer pack, or review a smaller encode.")
    fps = sample_fps(duration)
    say(warning(file, duration, fps, config))
    uploaded = upload_file(file, key, config)
    try:
        wait_until_active(uploaded["name"], key, config)
        raw = ask(uploaded, fps, prompt, key, config)
    finally:
        delete(uploaded["name"], key, config)
    return raw, {"vendor": "google", "model": config["model"], "sees": "video", "sampled_fps": fps, "audio_note": AUDIO_NOTE}
