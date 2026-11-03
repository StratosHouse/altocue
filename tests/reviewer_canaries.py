"""Films with faults no fact gate can catch, for measuring the reviewer rather than the gates.

`canaries.py` plants faults for code to find. These are for judgement. Every film here is our published demo take, edited
by the real stages, and every one passes every fact gate, so it reaches the reviewer. Each carries one fault a harsh
editor should name. The same edit with nothing planted is the control.

What a run proves:
- On these films, a fresh-context reviewer holding only the review pack did or did not name the planted fault, in the
  right rubric dimension and at the right moment. `score` counts exactly that.
- Whether that same reviewer flagged the same place on the clean film, which is the false alarm count.
- That the sound and timing faults reach a reviewer who cannot hear, because no number in the pack states them.

What a run does not prove:
- Anything about a camera original. The take is one generated presenter, ten seconds long once it is cut.
- Anything about tone, harshness, music or lip sync. Those are not planted, and a pack cannot show them.
- Anything about Gemini or Codex, or about any model but the one that ran. A catch rate belongs to one reviewer.
- Reliability. Five faults and one control is a smoke test of the method, not a measured rate.
- Independence. A reviewer from the editor's vendor is still not an outside opinion.
- That SHIP and FIX are right. `score` reports what code decided, and judges only the notes.

On a Mac, with a real transcriber, and online for the one take:

    uv run python tests/reviewer_canaries.py build FOLDER
    # give each FOLDER/films/<id>/film/review/PACK.md to a fresh agent, which writes review/verdict.json there
    uv run python tests/reviewer_canaries.py score FOLDER

The key sits beside `films/`, never inside a film. Ids are random, so a folder name gives nothing away.
"""

from __future__ import annotations

import json
import random
import secrets
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from altocue import caption, cut, demo, ffmpeg, qa
from altocue.errors import AltocueError
from altocue.models import Segment
from altocue.project import Project, write_json
from altocue.review import pack, rubric

SLACK = 1.5  # seconds either side of a window, because notes use whole-second timecodes
QUIET_DB = -12.0
LATE = 0.9
DRAGGED = 0.66  # playing speed of the dragged sentence
DRAG_SPAN = (7.7, 11.04)  # seconds in the take. It is pinned by hash, so these cannot drift
OPENING = (0.04, 0.79)  # seconds in the take: "Okay, so"
SENTENCE = ("it's", "agent")  # the middle sentence, which is where the sound and pace faults land


@dataclass
class Fault:
    name: str
    dimensions: tuple[str, ...]  # the rubric dimensions that count as naming it
    what: str = ""
    soft_open: bool = False
    stop_early: bool = False
    drag: bool = False
    quiet: bool = False
    late: float = 0.0


CONTROL = Fault("clean control", (), what="Nothing planted.")
FAULTS = [
    Fault("weak hook", ("hook",), what="The film opens on the presenter's 'Okay, so', with no hook on screen.", soft_open=True),
    Fault("flat ending", ("ending",), what="The speech stops mid-sentence on 'and the', so the point never lands.", stop_early=True),
    Fault("dragged stretch", ("pacing",), what=f"One sentence plays at {DRAGGED:.0%} speed in a film otherwise at full pace.", drag=True),
    Fault("quiet stretch", ("sound_as_speech",), what=f"One sentence sits {abs(QUIET_DB):g} dB under the rest. Whole-file loudness still passes.", quiet=True),
    Fault("late captions", ("captions",), what=f"Every caption lands {LATE} s after its words. The words all match, so only the timeline sheet shows it.", late=LATE),
]


@contextmanager
def swapped(owner: Any, name: str, stand_in: Any) -> Iterator[None]:
    real = getattr(owner, name)
    setattr(owner, name, stand_in)
    try:
        yield
    finally:
        setattr(owner, name, real)


def delayed(pages: list[dict[str, Any]], seconds: float, end: float) -> list[dict[str, Any]]:
    """The same words, later. A page that would run past the film stops at its end."""
    moved = []
    for page in pages:
        words = [{**word, "start": round(word["start"] + seconds, 3), "end": round(word["end"] + seconds, 3)} for word in page["words"]]
        moved.append({**page, "start": round(page["start"] + seconds, 3), "end": round(min(page["end"] + seconds, end), 3), "words": words})
    return moved


def between(words: list[dict[str, Any]], first: str, last: str) -> tuple[float, float]:
    """Where a phrase sits in the finished film, found from the words the cut kept."""
    bare = [qa.rules.tokens(word["text"]) for word in words]
    start = next(i for i, tokens in enumerate(bare) if first.replace("'", "") in "".join(tokens))
    end = next(i for i in range(start, len(bare)) if last in bare[i])
    return float(words[start]["start"]), float(words[end]["end"])


def slowed(take: Path, dest: Path) -> Path:
    """The take with one sentence stretched in picture and sound, before any stage sees it."""
    a, b = DRAG_SPAN
    rate = DRAGGED
    video = f"[0:v]trim=0:{a},setpts=PTS-STARTPTS[v1];[0:v]trim={a}:{b},setpts=(PTS-STARTPTS)/{rate}[v2];[0:v]trim={b},setpts=PTS-STARTPTS[v3]"
    sound = f"[0:a]atrim=0:{a},asetpts=PTS-STARTPTS[a1];[0:a]atrim={a}:{b},asetpts=PTS-STARTPTS,atempo={rate}[a2];[0:a]atrim={b},asetpts=PTS-STARTPTS[a3]"
    join = "[v1][a1][v2][a2][v3][a3]concat=n=3:v=1:a=1[v][a]"
    outputs = ["-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "18", "-c:a", "aac", "-b:a", "192k", str(dest)]
    ffmpeg.run(["-i", str(take), "-filter_complex", f"{video};{sound};{join}", *outputs])
    return dest


def quieten(render: Path, span: tuple[float, float], db: float = QUIET_DB) -> None:
    """Under the master, so the level stays where a real mistake would leave it."""
    quiet = render.with_name("quiet.mp4")
    ffmpeg.run(["-i", str(render), "-c:v", "copy", "-af", f"volume={db}dB:enable='between(t,{span[0]:.3f},{span[1]:.3f})'", "-c:a", "alac", str(quiet)])
    quiet.replace(render)


def plant(fault: Fault, project: Project, step: str) -> None:
    """Runs after the named stage, on what that stage left."""
    if step == "cut" and fault.soft_open:
        edl = project.edl()
        edl.removed = [item for item in edl.removed if item.start >= OPENING[1]]
        edl.segments.insert(0, Segment(id="s000", clip=edl.segments[0].clip, start=OPENING[0], end=OPENING[1]))
        project.save_edl(edl)
        plan = json.loads((project.root / "motion.json").read_text())
        write_json(project.root / "motion.json", {**plan, "items": [item for item in plan["items"] if item["type"] != "hook"]})
    if step == "cut" and fault.stop_early:
        edl = cut.remove_segments(project, [project.edl().segments[-1].id], "planted")
        project.save_edl(edl)
    if step == "render" and fault.quiet:
        quieten(project.out / "render.mp4", between(cut.map_words(project, project.edl()), *SENTENCE))


def edit(fault: Fault, root: Path, take: Path) -> Project:
    """The real stages through to the master, with the fault going in where it would really happen."""
    source = slowed(take, take.with_name("dragged.mp4")) if fault.drag else take  # beside the take, because a name in films/ would give the fault away
    root.mkdir(parents=True, exist_ok=True)
    project = demo.start_project(root, source)
    made_pages = caption.build

    def late_pages(target: Project) -> list[dict[str, Any]]:
        pages = delayed(made_pages(target), fault.late, target.edl().duration)
        write_json(target.work / "captions.json", {"pages": pages})
        return pages

    with swapped(caption, "build", late_pages if fault.late else made_pages):
        for step in demo.steps(project, "basic"):
            if step.name == "qa":
                break
            step.work()
            plant(fault, project, step.name)
    return project


def window_of(fault: Fault, project: Project) -> tuple[float, float]:
    edl = project.edl()
    words = cut.map_words(project, edl)
    if fault.soft_open:
        return between(words, "okay", "altocue")
    if fault.stop_early:
        return float(words[-3]["start"]), edl.duration
    if fault.drag or fault.quiet:
        return between(words, *SENTENCE)
    return 0.0, edl.duration


def make_film(fault: Fault, root: Path, take: Path) -> dict[str, Any]:
    """One film, the real gates on it, and its pack. A film a gate would stop is no use here."""
    project = edit(fault, root, take)
    final = project.out / "final.mp4"
    gates = qa.run_gates(project, final)
    stopped, missing = [(g.id, g.detail) for g in qa.failed(gates)], [(g.id, g.detail) for g in qa.not_run(gates)]
    if stopped or missing:
        raise AltocueError(f"{fault.name} would never reach a reviewer: failed {stopped}, not run {missing}.")
    pack.write_prompt(project, pack.build(project, final, gates))
    window = window_of(fault, project)
    duration = ffmpeg.probe_clip(final).duration
    return {"name": fault.name, "dimensions": list(fault.dimensions), "window": [round(window[0], 2), round(window[1], 2)], "what": fault.what, "duration": duration}


def build(root: Path) -> dict[str, Any]:
    """Every film under a random id, in a random order. The key stays outside the films."""
    take = demo.fetch_sample(root / "take")
    key: dict[str, Any] = {}
    cases = [CONTROL, *FAULTS]
    random.SystemRandom().shuffle(cases)
    for fault in cases:
        name = secrets.token_hex(3)
        key[name] = make_film(fault, root / "films" / name, take)
        print(f"{name}  built", flush=True)
    write_json(root / "key.json", key)
    return key


def overlaps(note: dict[str, Any], window: list[float]) -> bool:
    start = float(note["start_seconds"])
    end = float(note.get("end_seconds") or start)
    return start <= window[1] + SLACK and end >= window[0] - SLACK


def named(notes: list[dict[str, Any]], dimensions: list[str], window: list[float]) -> list[dict[str, Any]]:
    return [note for note in notes if note["dimension"] in dimensions and overlaps(note, window)]


def read_verdict(folder: Path, duration: float) -> dict[str, Any] | None:
    path = folder / "film" / "review" / "verdict.json"
    return rubric.score(json.loads(path.read_text()), [], duration) if path.exists() else None


def score(root: Path) -> dict[str, Any]:
    """Who named what. A film with no verdict counts as not reviewed, never as a miss or a catch."""
    key = json.loads((root / "key.json").read_text())
    rows, reviewed = [], {}
    for name, entry in key.items():
        result = read_verdict(root / "films" / name, entry["duration"])
        reviewed[name] = result
        if not entry["dimensions"]:
            continue
        hits = named(result["notes"], entry["dimensions"], entry["window"]) if result else []
        rows.append(
            {
                "fault": entry["name"],
                "dimension": entry["dimensions"][0],
                "reviewed": result is not None,
                "caught": bool(hits),
                "scored": result["scores"][entry["dimensions"][0]]["score"] if result else None,
                "verdict": result["verdict"] if result else None,
                "weighted": result["weighted"] if result else None,
                "note": hits[0]["observed"] if hits else "",
            }
        )
    control = next((name for name, entry in key.items() if not entry["dimensions"]), None)
    alarms: list[str] = []
    calm = reviewed.get(control) if control else None
    if calm:
        for entry in key.values():
            if entry["dimensions"]:
                alarms += [f"{entry['name']}: {n['observed']}" for n in named(calm["notes"], entry["dimensions"], entry["window"]) if n["severity"] != "minor"]
    for row in rows:
        row["control_scored"] = calm["scores"][row["dimension"]]["score"] if calm else None
    done = [row for row in rows if row["reviewed"]]
    return {
        "planted": len(rows),
        "reviewed": len(done),
        "caught": sum(row["caught"] for row in done),
        "rows": rows,
        "control_reviewed": calm is not None,
        "control_verdict": calm["verdict"] if calm else None,
        "control_weighted": calm["weighted"] if calm else None,
        "false_alarms": alarms,
    }


def show(result: dict[str, Any]) -> None:
    for row in result["rows"]:
        state = "not reviewed" if not row["reviewed"] else "CAUGHT" if row["caught"] else "MISSED"
        marks = f"{row['dimension']} {row['scored']}, clean film {row['control_scored']}"
        print(f"{row['fault']:<16} {state:<13} {marks:<32} code said {row['verdict']} at {row['weighted']}  {row['note'][:70]}")
    print(f"\n{result['caught']} of {result['reviewed']} reviewed faults named, {result['planted']} planted.")
    print(f"Clean control: {result['control_verdict']} at {result['control_weighted']}, {len(result['false_alarms'])} false alarms.")
    for alarm in result["false_alarms"]:
        print(f"  {alarm[:120]}")


if __name__ == "__main__":
    command, folder = sys.argv[1], Path(sys.argv[2])
    if command == "build":
        build(folder)
    else:
        show(score(folder))
