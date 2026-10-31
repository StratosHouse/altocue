"""The shot list: what to generate, and the craft rules code can hold it to.

A rule is a warning unless breaking it wastes money for certain. Words cannot be judged
perfectly by a regex, so craft rules advise and the director decides.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, UnidentifiedImageError

from . import ffmpeg
from .errors import AltocueError
from .models import GateResult, Model
from .project import Project

KINDS = ("video", "image")
TIERS: tuple[str, ...] = ("draft", "standard", "hero")
ASPECTS = ("21:9", "16:9", "3:2", "4:3", "5:4", "1:1", "4:5", "3:4", "2:3", "9:16")
CHECKS = {"video": ("motion", "frames", "none"), "image": ("still", "none")}
REQUIRED = ("id", "purpose", "kind", "tier", "prompt", "aspect", "duration", "placement", "check")
OPTIONAL = ("first_frame", "last_frame", "refs", "characters", "audio", "seed", "extra")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".tif", ".tiff", ".bmp"}
PUNCTUATION = " .,!?;:\"'"

MAX_REFS = 7
MAX_WORDS = 120
MIN_WORDS = 8
MAX_BLOCK_SHOT = 2.5
DRIFT_SECONDS = 12

MOVES = {
    "push in": r"\b(push(es|ing)?[- ]in|dolly(ing)?[- ]in|creep(s|ing)?[- ]in)\b",
    "pull back": r"\b(pull(s|ing)?[- ](back|out)|dolly(ing)?[- ](back|out))\b",
    "pan": r"\bwhip[- ]pan\b|\bpan(s|ning)?\s+(left|right|across|over|up|down|to)\b|\bcamera pans\b",
    "tilt": r"\b(tilt(s|ing)?\s+(up|down)\b(?! her| his| their)|camera tilts)\b",
    "orbit": r"\b(orbit(s|ing)?|arc(s|ing)? around)\b",
    "crane": r"\bcrane(s|ing)?\s+(up|down|shot|over)\b",
    "tracking": r"\b(tracks|tracking)\b",
    "handheld": r"\bhand-?held\b",
    "aerial": r"\b(aerial|drone|fpv)\b",
    "zoom": r"\bzoom(s|ing)?\b",
    "static": r"\b(static|locked[- ]off|tripod)\b",
}
ENDPOINT = (
    r"\b(ending|ends?|settl(es|ing)|land(s|ing)|stop(s|ping)|resolv(es|ing)|finish(es|ing)|rest(s|ing))\s+(on|at|in|into|with)\b"
    r"|\buntil\b"
)
REDESCRIBE = (
    r"\bwearing\b|\bdressed in\b|\bclad in\b|\bwith (\w+ ){0,3}(hair|beard|eyes)\b|\b\d+[- ]years?[- ]old\b"
    r"|\b(a|an) (young|old|elderly|middle-aged|tall|short|slim) (\w+ )?(man|woman|girl|boy|person)\b|\blooks like\b"
)
RESTYLE = (
    r"\b(photo-?real(istic)?|hyper-?real(istic)?|anime|cartoon|pixar|oil painting|watercolou?r"
    r"|pencil sketch|3d render|claymation|cgi)\b"
)
EMPTY = (
    r"\b(cinematic|beautiful(ly)?|stunning|epic|amazing|gorgeous|breathtaking|masterpiece"
    r"|award[- ]winning|ultra[- ]detailed|high quality)\b"
)
MOOD = r"\b(looks?|feels?|seems?|appears?)\s+(happy|sad|angry|scared|excited|nervous|worried|calm|joyful|upset|surprised)\b"
WIDE = r"\b(wide shot|extreme wide|establishing|long shot|aerial|drone)\b"
OTS = r"(?i:\bover[- ]the[- ]shoulder\b)|\bOTS\b"
UNRELIABLE = r"\b(analog(ue)? clock|clock face|mirror(ed)? text|reversed text)\b"
BLOCK = r"\bHARD CUT\b|\bSHOT\s+1\b"
BLOCK_SHOT = r"\bSHOT\s+\d+\s*-\s*([\d.]+)s?\s+to\s+([\d.]+)s?"
AGGRESSIVE = r"\b(unstabili[sz]ed|jitter|shaky|shake)"


@dataclass
class Shot(Model):
    id: str
    purpose: str
    kind: str
    tier: str
    prompt: str
    aspect: str
    duration: float
    placement: Any
    check: str
    first_frame: str | None = None
    last_frame: str | None = None
    refs: list[str] = field(default_factory=list)
    characters: list[str] = field(default_factory=list)
    audio: bool = False
    seed: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def media(self) -> list[str]:
        return [value for value in (self.first_frame, self.last_frame, *self.refs) if value]

    def shot_refs(self) -> list[str]:
        """Earlier shots this one is built from, written `@id`."""
        return [value[1:] for value in self.media() if value.startswith("@")]


@dataclass
class Finding(Model):
    level: str
    shot: str
    rule: str
    message: str


@dataclass
class Review:
    shots: list[Shot] = field(default_factory=list)
    style: str = ""
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors


def full_prompt(shot: Shot, style: str = "") -> str:
    """The style formula goes in byte-identical, so no shot can restyle by accident."""
    return f"{shot.prompt}\n\n{style}" if style else shot.prompt


def _placement_problem(value: Any) -> str | None:
    if isinstance(value, bool):
        return "placement must be a second or a word anchor."
    if isinstance(value, int | float):
        return "placement cannot be before the start." if value < 0 else None
    if not isinstance(value, dict) or not str(value.get("word", "")).strip():
        return 'placement is an output second, or {"word": "...", "occurrence": 1, "offset": 0}.'
    extra = set(value) - {"word", "occurrence", "offset"}
    return f"placement has unknown keys: {', '.join(sorted(extra))}." if extra else None


def _schema(raw: Any, index: int) -> tuple[str, list[Finding]]:
    if not isinstance(raw, dict):
        return f"#{index + 1}", [Finding("error", f"#{index + 1}", "required", "A shot must be an object.")]
    name = str(raw.get("id") or f"#{index + 1}")

    def error(rule: str, message: str) -> Finding:
        return Finding("error", name, rule, message)

    found = [error("required", f"Missing {key}.") for key in REQUIRED if raw.get(key) in (None, "")]
    if "model" in raw:
        found.append(error("no-model", "Shots ask for a tier, never a model. Model ids live in models.toml."))
    for key in sorted(set(raw) - {*REQUIRED, *OPTIONAL, "model"}):
        found.append(Finding("warning", name, "unknown-field", f"{key} is not a shot field, so it is ignored."))
    if "id" in raw and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", str(raw["id"])):
        found.append(error("id", "An id is letters, digits, hyphens and underscores, because it names files."))
    kind = raw.get("kind")
    if kind is not None and kind not in KINDS:
        found.append(error("kind", f"kind {kind} is not one of {', '.join(KINDS)}."))
    if raw.get("tier") is not None and raw["tier"] not in TIERS:
        found.append(error("tier", f"tier {raw['tier']} is not one of {', '.join(TIERS)}."))
    if raw.get("aspect") is not None and raw["aspect"] not in ASPECTS:
        found.append(error("aspect", f"aspect {raw['aspect']} is not one of {', '.join(ASPECTS)}."))
    duration = raw.get("duration")
    if duration is not None and (isinstance(duration, bool) or not isinstance(duration, int | float) or duration <= 0):
        found.append(error("duration", "duration is a number of seconds above 0."))
    if raw.get("check") is not None:
        allowed = CHECKS.get(str(kind), CHECKS["video"] + CHECKS["image"])
        if raw["check"] not in allowed:
            found.append(error("check", f"check {raw['check']} is not one of {', '.join(allowed)} for this kind."))
    if raw.get("placement") is not None:
        problem = _placement_problem(raw["placement"])
        if problem:
            found.append(error("placement", problem))
    for key in ("refs", "characters"):
        if key in raw and not (isinstance(raw[key], list) and all(isinstance(item, str) for item in raw[key])):
            found.append(error(key, f"{key} must be a list of strings."))
    if kind == "image" and raw.get("first_frame"):
        found.append(error("first-frame", "An image has no first frame. Use refs."))
    if raw.get("last_frame") and not raw.get("first_frame"):
        found.append(error("last-frame", "A last frame needs a first frame."))
    first = raw.get("first_frame")
    if isinstance(first, str) and not first.startswith("@") and Path(first).suffix.lower() not in IMAGE_SUFFIXES:
        found.append(error("first-frame", f"first_frame must be a still, and {first} is not an image."))
    return name, found


def _media_findings(shots: list[Shot], root: Path | None) -> list[Finding]:
    found: list[Finding] = []
    stills: set[str] = set()
    for shot in shots:
        for value in shot.media():
            if value.startswith("@"):
                if value[1:] not in stills:
                    found.append(Finding("error", shot.id, "shot-ref", f"{value} must name an earlier image shot."))
            elif root is not None:
                path = (root / value).resolve()
                if not path.is_relative_to(root.resolve()):
                    found.append(Finding("error", shot.id, "outside-project", f"{value} is outside the project, so it cannot be uploaded."))
                elif not path.is_file():
                    found.append(Finding("error", shot.id, "missing-file", f"{value} is not in the project."))
                elif path.suffix.lower() not in IMAGE_SUFFIXES:
                    found.append(Finding("error", shot.id, "not-an-image", f"{value} is not an image."))
        if shot.kind == "image":
            stills.add(shot.id)
    return found


def _character_findings(shots: list[Shot]) -> list[Finding]:
    """Identity comes from images. Drift is prevented upstream, never fixed after paying for video."""
    counts: dict[str, int] = {}
    for shot in shots:
        for name in set(shot.characters):
            counts[name] = counts.get(name, 0) + 1
    found: list[Finding] = []
    for shot in shots:
        if shot.kind == "video" and shot.characters and not shot.first_frame:
            recurring = [name for name in shot.characters if counts[name] > 1]
            level = "error" if recurring else "warning"
            who = ", ".join(recurring or shot.characters)
            found.append(Finding(level, shot.id, "still-first", f"{who} needs an approved still before video. Set first_frame."))
        if shot.characters and not shot.refs and not shot.first_frame:
            found.append(Finding("warning", shot.id, "identity-ref", "A character with no reference image will get an invented face."))
    return found


def lint_prompt(shot: Shot) -> list[Finding]:
    """The craft rules that can be read off one shot's prompt."""
    prompt = shot.prompt
    found: list[Finding] = []

    def warn(rule: str, message: str) -> None:
        found.append(Finding("warning", shot.id, rule, message))

    def has(pattern: str, flags: int = re.IGNORECASE) -> bool:
        return re.search(pattern, prompt, flags) is not None

    if shot.first_frame or shot.refs:
        if shot.first_frame and has(REDESCRIBE):
            warn("reference-conflict", "The prompt re-describes what the start frame shows. Keep the new motion, cut the appearance.")
        if has(RESTYLE):
            warn("reference-conflict", "A style word here can argue with the reference. Delete it, or restate only what it shows.")
    if has(EMPTY):
        warn("empty-words", "Empty style words give the model nothing. Name a lens, a film stock or a light source.")
    if has(MOOD):
        warn("emotion-as-physics", "Show the feeling as a body doing something, not as a mood word.")
    if has(UNRELIABLE):
        warn("unreliable-subject", "Clock faces and mirrored text rarely survive. Use a digital display, or crop it out.")
    if len(shot.refs) > MAX_REFS:
        warn("too-many-refs", f"{len(shot.refs)} references. Past {MAX_REFS} they fight. Trim props first.")
    if shot.characters and has(WIDE):
        warn("wide-character", "A character small in frame loses its reference. Shoot the wide empty, and cut to a medium.")
    if shot.characters and shot.duration > DRIFT_SECONDS:
        warn("drift", f"Identity drift grows with length. Consider two clips under {DRIFT_SECONDS}s.")
    if not shot.characters and has(OTS, 0):
        warn("ots-no-character", "Over-the-shoulder with no named character makes the model invent a person.")
    if shot.kind != "video":
        return found

    if shot.audio and not has(r"\bmusic\b"):
        warn("no-music", 'An open prompt tends to come back scored. Add "no music, only soft diegetic sound".')
    if has(BLOCK, 0):
        for start, end in re.findall(BLOCK_SHOT, prompt):
            if float(end) - float(start) > MAX_BLOCK_SHOT:
                length = float(end) - float(start)
                warn("block-length", f"A {length:g}s shot in a hard-cut block reads as a slideshow. Stay under {MAX_BLOCK_SHOT}s.")
        return found

    words = len(prompt.split())
    if words > MAX_WORDS or words < MIN_WORDS:
        warn("prompt-length", f"{words} words. A single shot lands best at about 50 to 80.")
    moves = [name for name, pattern in MOVES.items() if has(pattern)]
    if not moves:
        warn("no-move", "No named camera move. Name one, or say the frame is locked off.")
    if len(moves) > 1 and not has(r"\bthen\b"):
        warn("one-move", f"{', '.join(moves)}. Stacked moves warp the picture. Keep one, or give them an order with then.")
    if set(moves) - {"static"} and not has(ENDPOINT):
        warn("endpoint", 'The move has no destination, so it will drift. Add "ending on" or "settling at".')
    if "handheld" in moves and not has(AGGRESSIVE):
        warn("handheld", 'Handheld only reads if it is aggressive. Add "completely unstabilized".')
    return found


def validate(data: dict[str, Any], root: Path | None = None) -> Review:
    """Every finding at once, so one fix does not reveal the next."""
    raw = data.get("shots") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        return Review(findings=[Finding("error", "shots.json", "required", "There are no shots.")])
    review = Review(style=str(data.get("style") or ""))
    seen: set[str] = set()
    for index, item in enumerate(raw):
        name, found = _schema(item, index)
        review.findings += found
        if name in seen:
            review.findings.append(Finding("error", name, "duplicate-id", f"{name} is used twice."))
        seen.add(name)
        if not any(f.level == "error" for f in found):
            review.shots.append(Shot.from_dict(item))
    review.findings += _media_findings(review.shots, root)
    review.findings += _character_findings(review.shots)
    for shot in review.shots:
        review.findings += lint_prompt(shot)
    return review


def load_shots(path: str | Path, *, root: Path | None = None) -> Review:
    source = Path(path)
    if not source.is_file():
        raise AltocueError(f"No shot list at {source}.", fix="Write shots.json. See the shot fields in docs/DESIGN.md.")
    try:
        data = json.loads(source.read_text())
    except ValueError as error:
        raise AltocueError(f"{source.name} is not valid JSON: {error}", fix="Fix the JSON and run again.") from error
    return validate(data, root=root)


def require_valid(review: Review) -> None:
    if review.errors:
        lines = "\n".join(f"  [{f.rule}] {f.shot}: {f.message}" for f in review.errors)
        raise AltocueError(f"The shot list has {len(review.errors)} errors:\n{lines}", fix="Run `altocue direct`, fix them, and run again.")


@dataclass
class ClipCheck(Model):
    """Whether a clip moves. Mean absolute luma change at 12 fps on a 480x270 frame, 0 is frozen."""

    path: str
    duration: float
    subject_mean: float
    background_mean: float
    subject_min_s: float
    background_min_s: float
    per_second: list[list[float]]
    frames: dict[str, str]
    passed: bool
    detail: str
    min_subject: float
    min_background: float

    def gate(self) -> GateResult:
        value = {"subject": self.subject_mean, "background": self.background_mean}
        threshold = {"subject": self.min_subject, "background": self.min_background}
        return GateResult(id="clip-motion", passed=self.passed, value=value, threshold=threshold, detail=self.detail)


def _gray_frames(path: Path, width: int = 480, height: int = 270) -> np.ndarray:
    vf = f"fps=12,scale={width}:{height},format=gray"
    done = subprocess.run([ffmpeg.require(), "-v", "error", "-i", str(path), "-vf", vf, "-f", "rawvideo", "-"], capture_output=True)
    if done.returncode != 0:
        raise AltocueError(f"Cannot read {path}: {done.stderr.decode(errors='replace').strip()}")
    if not done.stdout:
        return np.empty((0, height, width), np.float32)
    return np.frombuffer(done.stdout, np.uint8).reshape(-1, height, width).astype(np.float32)


def check_clip(path: str | Path, *, out_dir: str | Path | None = None, min_subject: float = 4.0, min_background: float = 2.0) -> ClipCheck:
    """Measure motion and pull start, mid and end frames, for checking a clip before it is placed.

    Subject is the centre half of the frame, background the outer quarters. The default
    thresholds come from real rejections: frozen clips measured about 1.3 and 1.2.
    """
    source = Path(path)
    if not source.is_file():
        raise AltocueError(f"Not found: {source}")
    duration = float(ffmpeg.probe_json(source).get("format", {}).get("duration") or 0)
    frames: dict[str, str] = {}
    if out_dir is not None:
        folder = Path(out_dir)
        folder.mkdir(parents=True, exist_ok=True)
        marks = {"start": min(0.2, duration / 4), "mid": duration / 2, "end": max(duration - 0.5, duration * 3 / 4)}
        frames = {name: str(ffmpeg.frame(source, at, folder / f"{name}.png")) for name, at in marks.items()}
    gray = _gray_frames(source)
    if len(gray) < 2:
        return ClipCheck(str(source), duration, 0, 0, 0, 0, [], frames, False, "Too short to measure motion.", min_subject, min_background)
    change = np.abs(np.diff(gray, axis=0))
    width = change.shape[2]
    subject = change[:, :, width // 4 : 3 * width // 4].mean(axis=(1, 2))
    background = np.concatenate([change[:, :, : width // 4], change[:, :, 3 * width // 4 :]], axis=2).mean(axis=(1, 2))
    pairs = [(subject[i : i + 12].mean(), background[i : i + 12].mean()) for i in range(0, len(subject), 12)]
    per_second = [[round(float(front), 2), round(float(back), 2)] for front, back in pairs]
    subject_mean, background_mean = round(float(subject.mean()), 2), round(float(background.mean()), 2)
    passed = subject_mean >= min_subject and background_mean >= min_background
    verdict = "moving" if passed else f"reads as frozen, wants {min_subject:g} and {min_background:g}"
    detail = f"subject {subject_mean:g}, background {background_mean:g}: {verdict}"
    return ClipCheck(
        path=str(source),
        duration=duration,
        subject_mean=subject_mean,
        background_mean=background_mean,
        subject_min_s=min(p[0] for p in per_second),
        background_min_s=min(p[1] for p in per_second),
        per_second=per_second,
        frames=frames,
        passed=passed,
        detail=detail,
        min_subject=min_subject,
        min_background=min_background,
    )


def run_check(check: str, path: str | Path, out_dir: str | Path) -> dict[str, Any]:
    """Run a shot's check. `passed` is None when nothing was judged, so no check is claimed."""
    if check == "none":
        return {"check": check, "passed": None, "detail": "No check was asked for."}
    if check == "still":
        try:
            with Image.open(path) as picture:
                picture.verify()
            return {"check": check, "passed": True, "detail": "The image decodes."}
        except (UnidentifiedImageError, OSError, SyntaxError) as error:
            return {"check": check, "passed": False, "detail": f"The image does not decode: {error}"}
    try:
        result = asdict(check_clip(path, out_dir=out_dir))
    except AltocueError as error:
        return {"check": check, "passed": False, "detail": str(error)}
    return {**result, "check": check, "passed": None if check == "frames" else result["passed"]}


def _anchor_notes(review: Review, project: Project) -> list[str]:
    anchors = [(shot.id, shot.placement["word"]) for shot in review.shots if isinstance(shot.placement, dict)]
    if not anchors:
        return []
    files = sorted(project.words_dir.glob("*.json"))
    if not files:
        return ["Anchor words not checked: nothing has been transcribed yet."]
    said = {word["text"].strip(PUNCTUATION).lower() for path in files for word in json.loads(path.read_text())["words"]}
    missing = [(shot, word) for shot, word in anchors if word.strip(PUNCTUATION).lower() not in said]
    return [f"warning [anchor] {shot}: {word} is not in any transcript." for shot, word in missing]


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", "-p", default=".", help="Project folder")
    parser.add_argument("--shots", default="shots.json", help="Shot list, inside the project")
    parser.add_argument("--provider", choices=["fal", "higgsfield"], help="Overrides provider under [generate]")
    parser.add_argument("--check", metavar="CLIP", help="Measure one clip instead of reading the shot list")
    parser.add_argument("--json", action="store_true", help="Print the report as JSON")


def _check_one(project: Project, path: str, as_json: bool) -> int:
    result = check_clip(path, out_dir=project.work / "checks" / Path(path).stem)
    print(json.dumps(result.to_dict(), indent=2) if as_json else f"{Path(path).name}: {result.detail}")
    return 0 if result.passed else 1


def run(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    if args.check:
        return _check_one(project, args.check, args.json)
    from .generate.runner import catalog_for, estimate, get_provider

    review = load_shots(project.root / args.shots, root=project.root)
    notes = _anchor_notes(review, project)
    name = args.provider or project.setting("generate", "provider", "")
    cost = None
    if review.ok and name:
        cost = estimate(review.shots, get_provider(name, catalog_for(project), project=project), style=review.style)
    if args.json:
        report = {
            "ok": review.ok,
            "shots": len(review.shots),
            "findings": [f.to_dict() for f in review.findings],
            "notes": notes,
            "provider": name,
            "cost": cost.to_dict() if cost else None,
        }
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if review.ok else 1
    for finding in review.findings:
        print(f"{finding.level} [{finding.rule}] {finding.shot}: {finding.message}")
    for note in notes:
        print(note)
    print(f"{len(review.shots)} shots, {len(review.errors)} errors, {len(review.warnings)} warnings.")
    if cost:
        for line in cost.lines:
            price = "price unknown" if line.usd is None else f"${line.usd:.2f}"
            print(f"  {line.shot}: {price}  {line.model}")
        priced_note = f", with {len(cost.unpriced)} shots unpriced" if cost.unpriced else ""
        print(f"Total: ${cost.usd:.2f} on {name}{priced_note}")
    elif review.ok:
        print('No cost: set provider to "fal" or "higgsfield" under [generate] in altocue.toml.')
    return 0 if review.ok else 1
