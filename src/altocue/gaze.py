"""Where the speaker's eyes leave the lens, so a full-screen graphic can cover every such moment.

Each sampled frame is read with MediaPipe's face landmarker: its eye-look blendshapes and the head pose.
The eyeline is judged against the lens itself, not against the take. Head turn plus eye turn sits close
to `LENS` degrees when a speaker looks down the lens: eyes held on the lens while the head turns counter-
turn by about 0.048 of a blendshape point per degree, so `yaw + hs / 0.048` is the eyeline. `TOLERANCE`
degrees or more away from that is off the lens. An older rule judged each take against its own median,
and on a take read from notes beside the lens for most of its length it turned on-lens and off-lens around.

A blink is 0.3 s or less of the eyes only dropping or rising. It is not a look-away, but one at the edge
of a look-away is the speaker looking up from notes, so the cover grows to hold until the eyes are up.

MediaPipe (Apache 2.0) is optional: `uv tool install --force altocue --with mediapipe`. Its model,
about 3.7 MB, is downloaded once into ~/.altocue/models and checked against a pinned hash.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from statistics import median
from typing import Any

from .errors import AltocueError

MODEL_URL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
MODEL_SHA256 = "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"
MODEL_PATH = Path.home() / ".altocue" / "models" / "face_landmarker.task"
INSTALL = "uv tool install --force altocue --with mediapipe"
SAMPLE_FPS = 15.0
PER_DEGREE = 0.048  # eye blendshape points per degree of head turn when the eyes hold the lens
LENS = 2.5  # degrees: the eyeline when looking down the lens, measured on five takes
TOLERANCE = 9.0  # degrees off that eyeline that count as off the lens
TURNED = 18.0  # a head turned this far is off the lens whatever the eyes do
EYES = 0.3  # an eye-look score this far over the take's usual counts as looking up or down
TILT = 12.0  # degrees of head tilt over the take's usual
SHUT = 0.4  # eyes this far down or up are closing, which throws the sideways reading
JOIN = 0.15  # samples this close belong to one moment
BLINK = 0.3  # a moment this short of eyes only dropping or rising is a blink
LOOK_AWAY = 0.45  # a moment this long is a look-away, whatever it is
EYES_LEAVE = 0.2  # a moment this long with the eyes off sideways is a look-away
MARGIN = 0.05
BLINK_REACH = 0.25  # a blink this close to a look-away is part of it


def model() -> Path:
    """The face landmarker model, downloaded once and checked."""
    if MODEL_PATH.exists() and hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest() == MODEL_SHA256:
        return MODEL_PATH
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    from .setup import download

    try:
        data = download(MODEL_URL, "the face landmarker model", timeout=60)
    except OSError as error:
        raise AltocueError(
            f"Could not download the face landmarker model: {error}", fix=f"Check this machine can reach the web, or put the file at {MODEL_PATH}."
        ) from error
    if hashlib.sha256(data).hexdigest() != MODEL_SHA256:
        raise AltocueError("The face landmarker model did not match its pinned hash, so it was not saved.", fix="Try again later.")
    MODEL_PATH.write_bytes(data)
    return MODEL_PATH


def scan(video: Path, fps: float = SAMPLE_FPS) -> list[dict[str, Any]]:
    """One reading per sample: face found, eye-look scores, the signed sideways reading `hs`, head yaw and pitch."""
    try:
        import cv2
        import mediapipe as mp
        from mediapipe.tasks import python as tasks
        from mediapipe.tasks.python import vision
    except ImportError as error:
        raise AltocueError("Reading the eyeline needs MediaPipe, which is not installed.", fix=f"Run `{INSTALL}`.") from error
    options = vision.FaceLandmarkerOptions(
        base_options=tasks.BaseOptions(model_asset_path=str(model())),
        running_mode=vision.RunningMode.VIDEO,
        output_face_blendshapes=True,
        output_facial_transformation_matrixes=True,
        num_faces=1,
    )
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise AltocueError(f"Could not open {video}.")
    source_fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    every = max(1, round(source_fps / fps))
    rows: list[dict[str, Any]] = []
    with vision.FaceLandmarker.create_from_options(options) as landmarker:
        index = 0
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if index % every == 0:
                at = index / source_fps
                found = landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)), int(at * 1000))
                rows.append(reading(at, found))
            index += 1
    capture.release()
    return rows


def reading(at: float, found: Any) -> dict[str, Any]:
    if not found.face_landmarks:
        return {"t": round(at, 3), "face": False}
    scores = {c.category_name: c.score for c in found.face_blendshapes[0]}
    right = (scores["eyeLookOutLeft"] + scores["eyeLookInRight"]) / 2
    left = (scores["eyeLookInLeft"] + scores["eyeLookOutRight"]) / 2
    matrix = found.facial_transformation_matrixes[0]
    return {
        "t": round(at, 3),
        "face": True,
        "hs": round(right - left, 3),
        "up": round((scores["eyeLookUpLeft"] + scores["eyeLookUpRight"]) / 2, 3),
        "down": round((scores["eyeLookDownLeft"] + scores["eyeLookDownRight"]) / 2, 3),
        "yaw": round(math.degrees(math.atan2(matrix[0][2], matrix[2][2])), 2),
        "pitch": round(math.degrees(math.asin(max(-1.0, min(1.0, -matrix[1][2])))), 2),
    }


def kinds_of(row: dict[str, Any], usual: dict[str, float], lens: float = LENS) -> set[str]:
    """What takes this sample off the lens, if anything."""
    if not row.get("face"):
        return {"no face"}
    kinds = set()
    if row["down"] - usual["down"] > EYES:
        kinds.add("eyes down")
    if row["up"] - usual["up"] > EYES:
        kinds.add("eyes up")
    if abs(row["pitch"] - usual["pitch"]) > TILT:
        kinds.add("head tilted")
    if abs(row["yaw"]) >= TURNED:
        kinds.add("head turned")
    if abs(row["yaw"] + row["hs"] / PER_DEGREE - lens) >= TOLERANCE and row["down"] < SHUT and row["up"] < SHUT:
        kinds.add("eyes off")  # not while the eyes are closing: a blink throws the sideways reading
    return kinds


def moments(rows: list[dict[str, Any]], lens: float = LENS) -> list[tuple[float, float, set[str]]]:
    faces = [r for r in rows if r.get("face")]
    usual = {key: median(r[key] for r in faces) for key in ("up", "down", "pitch")} if faces else {"up": 0.0, "down": 0.0, "pitch": 0.0}
    step = min((b["t"] - a["t"] for a, b in zip(rows, rows[1:], strict=False) if b["t"] > a["t"]), default=1 / SAMPLE_FPS)
    runs: list[list[Any]] = []
    for row in rows:
        kinds = kinds_of(row, usual, lens)
        if not kinds:
            continue
        if runs and row["t"] - runs[-1][1] <= JOIN:
            runs[-1][1] = row["t"]
            runs[-1][2] |= kinds
        else:
            runs.append([row["t"], row["t"], set(kinds)])
    return [(round(a, 3), round(b + step, 3), kinds) for a, b, kinds in runs]


def away_spans(rows: list[dict[str, Any]], lens: float = LENS, no_face: bool = True) -> tuple[list[dict[str, Any]], list[tuple[float, float]]]:
    """Look-away spans to cover, each grown over a blink at its edges, and the blinks themselves.

    `no_face` counts frames with no face found as off the lens, as on a plate. On a finished render the face is
    small or covered on purpose, so there it is left out.
    """
    spans, blinks = [], []
    if not no_face:
        rows = [r for r in rows if r.get("face")]
    for a, b, kinds in moments(rows, lens):
        if b - a <= BLINK and kinds <= {"eyes down", "eyes up"}:
            blinks.append((a, b))
        elif b - a >= LOOK_AWAY or (b - a >= EYES_LEAVE and kinds & {"eyes off", "head turned", "no face"}):
            spans.append({"start": a, "end": b, "reason": ", ".join(sorted(kinds))})
    for span in spans:
        for a, b in blinks:
            if span["end"] - MARGIN <= a <= span["end"] + BLINK_REACH:  # back with the eyes still down: hold until they are up
                span["end"] = max(span["end"], round(b + MARGIN, 3))
            if span["start"] - BLINK_REACH <= b <= span["start"] + MARGIN:  # the eyes drop just before: the cover gets there first
                span["start"] = min(span["start"], round(max(0.0, a - MARGIN), 3))
    merged: list[dict[str, Any]] = []
    for span in sorted(spans, key=lambda s: s["start"]):
        if merged and span["start"] <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], span["end"])
            merged[-1]["reason"] = ", ".join(sorted(set(merged[-1]["reason"].split(", ")) | set(span["reason"].split(", "))))
        else:
            merged.append(dict(span))
    return merged, blinks


def uncovered(spans: list[dict[str, Any]], covers: list[tuple[float, float]], slack: float = MARGIN) -> list[dict[str, Any]]:
    """Look-away spans of a finished render that no hidden scene or cover holds."""
    return [s for s in spans if not any(a - slack <= s["start"] and s["end"] <= b + slack for a, b in covers)]
