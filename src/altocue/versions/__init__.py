"""Version history kept in the project: each finished edit saved as plain files, compared by code, and restorable.

`altocue versions save` copies the decision files and a small copy of the render into `versions/v001/`, then
`v002/` and so on, and writes `version.json` beside them: what was asked for, what the editor says it changed,
and `diff`, the change list `diff.py` works out from the files themselves. Sources are never copied, nothing is
uploaded and no model is called. `restore` puts a version's decision files back and renders nothing, because a
render is rebuilt from its files, never restored. `page` and `serve` show the versions side by side and take
timed notes, and `notes` hands those notes back to whoever is editing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tomllib
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .. import __version__, ffmpeg
from ..errors import AltocueError
from ..models import Clip
from ..project import CONFIG, Project, write_json
from . import diff, notes

FOLDER = "versions"
RECORD = "version.json"
FILM = "film.mp4"
FULL = "full.mp4"
DECISIONS = (
    CONFIG,
    "brief.md",
    "edl.json",
    "script.md",
    "motion.json",
    "studio.json",
    "broll.json",
    "shots.json",
    "beats.json",
    "storyboard.json",
    "brief.json",
    "rules.json",
)
FILM_INPUTS = ("edl.json", "motion.json", "broll.json", "studio.json", "storyboard.json")  # a render older than one of these does not show it
NEVER_TOUCHED = ("sources", "work", "out", FOLDER)
ID = re.compile(r"v(\d{3,})")
# The key shapes scripts/check_repo.py blocks from the repo, and a few more that are as easy to paste by mistake.
SECRET = re.compile(
    r"ghp_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|gsk_[A-Za-z0-9]{20,}"
    r"|(?<![A-Za-z0-9])sk-(?:ant|proj|or|live|test)-[A-Za-z0-9_-]{16,}|github_pat_[A-Za-z0-9_]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}"
    r"|(?-i:AKIA[0-9A-Z]{16})|(?-i:AIza[0-9A-Za-z_-]{30,})|-----BEGIN [A-Z ]*PRIVATE KEY-----",
    re.IGNORECASE,
)


# The store


def folder(project: Project) -> Path:
    return project.root / FOLDER


def ids(project: Project) -> list[str]:
    """Every saved version, oldest first."""
    home = folder(project)
    found = [path.name for path in home.iterdir() if ID.fullmatch(path.name) and (path / RECORD).is_file()] if home.is_dir() else []
    return sorted(found, key=lambda name: int(name[1:]))


def load(project: Project, version: str) -> dict[str, Any]:
    known = ids(project)
    if version not in known:
        fix = f"Saved versions: {', '.join(known)}." if known else "Save one first with `altocue versions save -m <summary>`."
        raise AltocueError(f"There is no version {version}.", fix=fix)
    return json.loads((folder(project) / version / RECORD).read_text())


def records(project: Project) -> list[dict[str, Any]]:
    return [load(project, version) for version in ids(project)]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def inside(root: Path, name: str) -> Path | None:
    """Where a decision file sits. None for a path that leaves the project or lands in a folder a version never touches.

    A record and a brand setting are files anyone can edit, so no path from either is trusted.
    """
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or ".." in path.parts or path.parts[0] in NEVER_TOUCHED:
        return None
    target = (root / path).resolve()
    return target if target.is_relative_to(root.resolve()) and target != root.resolve() else None


def brand_file(root: Path, config: dict[str, Any] | None) -> str | None:
    """The brand file the settings point at, when it lives inside the project. The house brand ships with Altocue and is not a decision."""
    brand = (config or {}).get("brand")
    name = str(brand.get("name") or "").strip() if isinstance(brand, dict) else ""
    if not name or name == "default":
        return None
    target = inside(root, Path(name).as_posix())
    return target.relative_to(root.resolve()).as_posix() if target and target.is_file() and target.suffix.lower() == ".json" else None


def decision_files(root: Path) -> list[str]:
    """Every decision file this folder holds, by its path from the folder."""
    names = [name for name in DECISIONS if (root / name).is_file()]
    try:
        brand = brand_file(root, tomllib.loads((root / CONFIG).read_text()))
    except (OSError, ValueError):
        brand = None
    return names + ([brand] if brand and brand not in names else [])


def hashes(root: Path, names: Iterable[str]) -> dict[str, str]:
    return {name: sha256(root / name) for name in names}


def source_words(project: Project) -> diff.Words:
    """Each clip's words, so a cut can be named by what was said. A clip with no readable word file is simply not named."""
    words: diff.Words = {}
    for path in sorted(project.words_dir.glob("*.json")):
        try:
            words[path.stem] = [{"text": word.text, "start": word.start, "end": word.end} for word in project.words(path.stem)]
        except (AltocueError, ValueError, KeyError, TypeError):
            continue
    return words


def snapshot(root: Path, saved: dict[str, str], duration: float | None) -> diff.Snapshot:
    """One folder's decision files, read for comparing. A file that cannot be parsed is compared as text, never dropped."""

    def data(name: str) -> Any:
        if name not in saved:
            return None
        try:
            return json.loads((root / name).read_text())
        except (OSError, ValueError):
            return None

    def text(name: str) -> str | None:
        return (root / name).read_text(errors="replace") if name in saved else None

    try:
        config = tomllib.loads(text(CONFIG) or "")
    except ValueError:
        config = None
    others: dict[str, Any] = {}
    for name in saved:
        if name not in diff.READ_BY:
            parsed = data(name) if name.endswith(".json") else None
            others[name] = parsed if parsed is not None else text(name)
    edl, motion, broll = data("edl.json"), data("motion.json"), data("broll.json")
    return diff.Snapshot(
        edl=edl if isinstance(edl, dict) else None,
        motion=motion if isinstance(motion, dict) else None,
        broll=broll if isinstance(broll, dict) else None,
        config=config,
        script=text("script.md"),
        others=others,
        hashes=dict(saved),
        duration=duration,
    )


def saved_files(record: dict[str, Any]) -> dict[str, str]:
    return {str(row["path"]): str(row["sha256"]) for row in record.get("files", [])}


def film_length(record: dict[str, Any]) -> float | None:
    return (record.get("render") or {}).get("duration")


def version_snapshot(project: Project, record: dict[str, Any]) -> diff.Snapshot:
    return snapshot(folder(project) / record["id"], saved_files(record), film_length(record))


def working_snapshot(project: Project) -> diff.Snapshot:
    """The project as it stands now, with no render: the files someone is still changing."""
    return snapshot(project.root, hashes(project.root, decision_files(project.root)), None)


def between(project: Project, old: dict[str, Any], new: dict[str, Any] | None) -> dict[str, Any]:
    """The code-made change list from one version to another, or to the project as it stands when `new` is None."""
    after = version_snapshot(project, new) if new else working_snapshot(project)
    found = diff.compare(version_snapshot(project, old), after, source_words(project))
    return {"from": old["id"], "to": new["id"] if new else "the project now", **found}


def matches(project: Project) -> str | None:
    """The newest version whose files are exactly the project's files now."""
    now = hashes(project.root, decision_files(project.root))
    return next((record["id"] for record in reversed(records(project)) if saved_files(record) == now), None)


# Saving


def refuse_secrets(texts: dict[str, list[str]]) -> None:
    """A version is kept for good and may be shown to others, so a key pasted into a prompt never reaches it."""
    for flag, values in texts.items():
        for value in values:
            found = SECRET.search(value)
            if found:
                raise AltocueError(
                    f"The text given to {flag} holds what looks like a key or token, starting {found.group(0)[:4]}. A version never stores one.",
                    fix="Take it out of the text and save again. If it is a real key that has been shared, revoke it.",
                )


def newest_render(project: Project) -> Path | None:
    found = [path for path in (project.out / "final.mp4", project.out / "draft.mp4") if path.is_file()]
    return max(found, key=lambda path: path.stat().st_mtime) if found else None


def behind(render: Path, root: Path, names: Iterable[str]) -> str | None:
    """A file the render reads that changed after it was made, or None when the render shows these files."""
    made = render.stat().st_mtime
    reads = [name for name in names if name in FILM_INPUTS or name not in DECISIONS]  # the one name outside DECISIONS is the brand file
    return next((name for name in reads if (root / name).stat().st_mtime > made), None)


def render_for(project: Project, file: str | None, names: list[str], *, optional: bool) -> Path | None:
    """The film that belongs to these files. `optional` is for the save a restore makes, which keeps the files with or without one."""
    if file:
        chosen = Path(file).expanduser().resolve()
        if not chosen.is_file():
            raise AltocueError(f"No film at {chosen}.", fix="Check the path, or leave --file out to use the newest of out/final.mp4 and out/draft.mp4.")
        return chosen
    newest = newest_render(project)
    if newest is None:
        if optional:
            return None
        raise AltocueError(
            "Nothing has been rendered yet, and a version is a film beside the files that made it.",
            fix="Run `altocue render`, then `altocue master` and `altocue qa`, then save. Or pass the film with --file.",
        )
    changed = behind(newest, project.root, names)
    if changed:
        if optional:
            return None
        raise AltocueError(
            f"out/{newest.name} is older than {changed}, so it does not show this edit.",
            fix="Run `altocue render` again, then `altocue master` and `altocue qa`, then save. If another file is the film of this edit, pass it with --file.",
        )
    return newest


def film_copy(render: Path, dest: Path, clip: Clip) -> Path:
    """The small copy a version keeps: long side 960 at a capped rate, the same one the review page plays."""
    from ..review import page as review_page

    return review_page.review_copy(render, dest, clip.duration, clip.fps)


def render_facts(project: Project, render: Path, home: Path, *, keep_full: bool) -> dict[str, Any]:
    clip = ffmpeg.probe_clip(render)
    film_copy(render, home / FILM, clip)
    if keep_full:
        try:
            os.link(render, home / FULL)  # no second copy on disk when the project and its versions share one
        except OSError:
            shutil.copy2(render, home / FULL)
    shown = render.relative_to(project.root).as_posix() if render.is_relative_to(project.root) else render.name
    return {
        "source": shown,
        "stage": "draft" if "draft" in render.name else "final",
        "sha256": sha256(render),
        "duration": round(clip.duration, 3),
        "bytes": render.stat().st_size,
        "width": clip.display_size[0],
        "height": clip.display_size[1],
        "film": FILM,
        "film_bytes": (home / FILM).stat().st_size,
        "full": FULL if keep_full else None,
    }


def save(
    project: Project,
    *,
    summary: str,
    prompts: Iterable[str] = (),
    changes: Iterable[str] = (),
    resolves: Iterable[str] = (),
    model: str = "",
    file: str | None = None,
    keep_full: bool = False,
    before_restore: bool = False,
) -> dict[str, Any]:
    """Write the next version and return its record. Nothing is written unless every check passes."""
    summary = summary.strip()
    prompts = [prompt for prompt in prompts if prompt.strip()]  # kept exactly as given: these are the user's words, not ours
    changes = [change.strip() for change in changes if change.strip()]
    resolves = list(dict.fromkeys(resolves))
    if not summary:
        raise AltocueError("A version needs a summary.", fix='Say what this version is in one line: `altocue versions save -m "Tightened the opening"`.')
    refuse_secrets({"-m": [summary], "--prompt": prompts, "--change": changes, "--model": [model]})
    notes.require_open(project, resolves)
    names = decision_files(project.root)
    now = hashes(project.root, names)
    render = render_for(project, file, names, optional=before_restore)
    saved = records(project)
    last = saved[-1] if saved else None
    digest = sha256(render) if render else None
    if last and saved_files(last) == now and (last.get("render") or {}).get("sha256") == digest:
        raise AltocueError(
            f"Nothing has changed since {last['id']}: the same files and the same film.",
            fix=f"Change the edit and render it, then save. `altocue versions show {last['id']}` shows what is saved.",
        )
    home = folder(project)
    taken = [int(found.group(1)) for path in home.iterdir() if (found := ID.fullmatch(path.name))] if home.is_dir() else []
    count = max(taken, default=0) + 1
    version = f"v{count:03d}"
    scratch = home / f".saving-{version}"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    try:
        for name in names:
            (scratch / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(project.root / name, scratch / name)
        facts = render_facts(project, render, scratch, keep_full=keep_full) if render else None
        after = snapshot(project.root, now, facts["duration"] if facts else None)
        record = {
            "number": count,
            "id": version,
            "saved_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "summary": summary,
            "prompts": prompts,
            "model": model.strip() or None,
            "changes": changes,
            "resolves": resolves,
            "altocue": __version__,
            "previous": last["id"] if last else None,
            "render": facts,
            "files": [{"path": name, "sha256": now[name]} for name in names],
            "diff": {"from": last["id"], "to": version, **diff.compare(version_snapshot(project, last), after, source_words(project))} if last else None,
        }
        write_json(scratch / RECORD, record)
        scratch.rename(home / version)
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        raise
    notes.resolve(project, resolves, version)
    return record


# Restoring


def check_whole(project: Project, record: dict[str, Any]) -> None:
    """Refuse a version whose saved files are missing or were changed, before anything in the project moves."""
    home = folder(project) / record["id"]
    for name, digest in saved_files(record).items():
        kept = inside(home, name)
        if kept is None or inside(project.root, name) is None or not kept.is_file() or sha256(kept) != digest:
            raise AltocueError(
                f"{record['id']} is damaged: {name} is missing, was changed after it was saved, or does not belong in a version.",
                fix="Restore another version. `altocue versions list` shows them.",
            )


def restore(project: Project, version: str) -> dict[str, Any]:
    """Put a version's decision files back. The current files are saved first when no version holds them, so nothing is ever lost."""
    record = load(project, version)
    check_whole(project, record)
    wanted = saved_files(record)
    here = decision_files(project.root)
    now = hashes(project.root, here)
    if now == wanted:
        return {"version": version, "already": True, "restored": [], "removed": [], "saved_first": None, "held_by": version}
    held_by = matches(project)
    saved_first = None if held_by else save(project, summary=f"Before restoring {version}", before_restore=True)
    for name in wanted:
        target = project.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(folder(project) / version / name, target)
    removed = [name for name in here if name in DECISIONS and name != CONFIG and name not in wanted]
    for name in removed:
        (project.root / name).unlink()
    restored = [name for name in wanted if now.get(name) != wanted[name]]
    return {"version": version, "already": False, "restored": restored, "removed": removed, "saved_first": saved_first, "held_by": held_by}


def next_steps(root: Path, shown: str) -> list[str]:
    """What to run after a restore. The render first, and before it anything `altocue status` finds stale."""
    render = f"altocue film render -p {shown}" if (root / "storyboard.json").exists() else f"altocue render -p {shown}"
    lines = [f"Next: {render}"]
    try:
        from .. import status

        stages = status.collect(Project.open(root))
    except (AltocueError, OSError, ValueError, KeyError):
        return lines
    names = [stage.name for stage in stages]
    first = next((stage for stage in stages if stage.state != status.DONE), None)
    target = next((index for index, name in enumerate(names) if name in ("render", "film")), None)
    if first and target is not None and names.index(first.name) < target:
        lines.append(f"`altocue status` puts {first.name} ({first.state}) before the render, so run this first: {first.command.format(folder=shown)}")
    return lines


# What `altocue status` adds


def status_lines(project: Project) -> list[str]:
    """One line for the latest version and one for open notes, only for a project that keeps versions."""
    if not folder(project).is_dir():
        return []
    lines = []
    known = ids(project)
    if known:
        last = load(project, known[-1])
        lines.append(f'Latest version: {last["id"]}, saved {when(last)}. "{diff.clipped(last.get("summary", ""), 70)}"')
    waiting = len(notes.still_open(notes.load(project)))
    if waiting:
        lines.append(f"Open notes: {waiting}. Read them with `altocue versions notes`.")
    return lines


# The command


def when(record: dict[str, Any]) -> str:
    return str(record.get("saved_at", ""))[:16].replace("T", " ") + " UTC"


def megabytes(size: int | None) -> str:
    size = size or 0
    return f"{size / 1e6:.1f} MB" if size >= 1e6 else f"{size / 1e3:.0f} KB"


def row_of(record: dict[str, Any]) -> dict[str, Any]:
    found = record.get("diff")
    return {
        "id": record["id"],
        "number": record["number"],
        "saved_at": record.get("saved_at"),
        "summary": record.get("summary", ""),
        "duration": film_length(record),
        "changes": found["count"] if found else None,
        "resolved": len(record.get("resolves") or []),
        "film": bool(record.get("render")),
    }


def changed_text(row: dict[str, Any]) -> str:
    if row["changes"] is None:
        return "first version"
    text = f"{row['changes']} change{'' if row['changes'] == 1 else 's'}"
    return text + (f", {row['resolved']} note{'' if row['resolved'] == 1 else 's'} resolved" if row["resolved"] else "")


def do_list(project: Project, args: argparse.Namespace) -> int:
    rows = [row_of(record) for record in records(project)]
    if getattr(args, "json", False):
        print(json.dumps({"versions": rows, "project_matches": matches(project) if rows else None}, indent=2, ensure_ascii=False))
        return 0
    if not rows:
        print('No versions yet. After a render and QA, save one: altocue versions save -m "what this version is"')
        return 0
    wide = max(len(changed_text(row)) for row in rows)
    for row in rows:
        length = diff.stamp(row["duration"]) if row["duration"] is not None else "no film"
        print(f"{row['id']}  {when(row)}  {length:>7}  {changed_text(row):<{wide}}  {diff.clipped(row['summary'], 80)}")
    same = matches(project)
    print(f"The project's files are {same}'s." if same else "The project has changes no version holds yet.")
    return 0


def show_text(record: dict[str, Any]) -> list[str]:
    lines = [f"{record['id']}, saved {when(record)} by altocue {record.get('altocue', 'unknown')}", f"Summary: {record.get('summary', '')}"]
    if record.get("model"):
        lines.append(f"Model: {record['model']}")
    if record.get("prompts"):
        lines += ["Asked for, in the user's own words:", *(f'  "{prompt}"' for prompt in record["prompts"])]
    if record.get("changes"):
        lines += ["Changes the editor listed:", *(f"  - {change}" for change in record["changes"])]
    if record.get("resolves"):
        lines.append(f"Notes resolved: {', '.join(record['resolves'])}")
    film = record.get("render")
    if film:
        lines.append(
            f"Film: {film['source']} ({film['stage']}), {diff.stamp(film['duration'])}, {film['width']}x{film['height']}, {megabytes(film['bytes'])}, "
            f"sha256 {film['sha256'][:12]}. Review copy {film['film']}, {megabytes(film.get('film_bytes'))}."
            + (f" Full render kept as {film['full']}." if film.get("full") else "")
        )
    else:
        lines.append("Film: none. No render matched these files when they were saved.")
    lines.append(f"Files: {', '.join(row['path'] for row in record.get('files', []))}")
    found = record.get("diff")
    if not found:
        lines.append("First version, so there is nothing to compare it with.")
    else:
        lines += [f"Changed since {found['from']}, worked out by code:", *(f"  {line}" for line in diff.lines(found) or ["nothing that the files show"])]
    return lines


def do_show(project: Project, args: argparse.Namespace) -> int:
    record = load(project, args.version)
    print(json.dumps(record, indent=2, ensure_ascii=False) if args.json else "\n".join(show_text(record)))
    return 0


def do_diff(project: Project, args: argparse.Namespace) -> int:
    found = between(project, load(project, args.old), load(project, args.new) if args.new else None)
    if args.json:
        print(json.dumps(found, indent=2, ensure_ascii=False))
        return 0
    print(f"From {found['from']} to {found['to']}, worked out by code:")
    print("\n".join(diff.lines(found)) or "Nothing differs in the decision files.")
    return 0


def do_save(project: Project, args: argparse.Namespace) -> int:
    record = save(
        project,
        summary=args.summary,
        prompts=[text for group in args.prompt or [] for text in group],
        changes=[text for group in args.change or [] for text in group],
        resolves=[note for group in args.resolves or [] for note in group],
        model=args.model or "",
        file=args.file,
        keep_full=args.keep_full,
    )
    film = record["render"]
    home = folder(project) / record["id"]
    copy = f"a {megabytes(film['film_bytes'])} review copy of {film['source']}, {diff.stamp(film['duration'])} long"
    print(f"Saved {record['id']} in {home}: {len(record['files'])} files and {copy}.")
    found = record["diff"]
    if found:
        print(f"Changed since {found['from']}, worked out by code:")
        print("\n".join(f"  {line}" for line in diff.lines(found)) or "  nothing that the files show")
    if record["resolves"]:
        print(f"Resolved: {', '.join(record['resolves'])}.")
    print("Nothing was uploaded. `altocue versions page` writes the page to watch and compare versions in.")
    return 0


def do_restore(project: Project, args: argparse.Namespace) -> int:
    done = restore(project, args.version)
    if done["already"]:
        print(f"The project already holds {args.version}'s files, so nothing was changed.")
        return 0
    first = done["saved_first"]
    if first:
        film = "with its film" if first["render"] else "with no film, because no render matched those files"
        print(f"Saved the edit as it stood as {first['id']} first, {film}.")
    else:
        print(f"The edit as it stood is {done['held_by']} already, so nothing new was saved first.")
    gone = f" Removed, because {args.version} had none: {', '.join(done['removed'])}." if done["removed"] else ""
    print(f"Restored {args.version}: {', '.join(done['restored']) or 'no file differed'}.{gone}")
    print("sources/, work/ and out/ were not touched. A render is rebuilt from these files, never restored.")
    print("\n".join(next_steps(project.root, args.project)))
    return 0


def do_page(project: Project, args: argparse.Namespace) -> int:
    from . import page

    if not ids(project):
        raise AltocueError("There are no versions to show yet.", fix='Save one first: `altocue versions save -m "what this version is"`.')
    print(f"Wrote {page.write(project)}")
    print("View it in a browser yourself. Nothing was opened and the page asks the network for nothing.")
    return 0


def do_serve(project: Project, args: argparse.Namespace) -> int:
    from . import serve

    if not ids(project):
        raise AltocueError("There are no versions to serve yet.", fix='Save one first: `altocue versions save -m "what this version is"`.')
    return serve.run(project, host=args.host, port=args.port)


def note_text(note: dict[str, Any], picture: Path | None, root: Path) -> str:
    said = diff.clipped(note.get("text", ""), 400) or "(a drawing with no words)"
    drawn = ""
    if note.get("strokes"):
        drawn = f"  [drawing: {picture.relative_to(root).as_posix()}]" if picture else "  [drawing, but that version kept no film to show it on]"
    return f"{note['id']}  {note['version']}  {diff.stamp(float(note['at']))}  {said}{drawn}"


def do_notes(project: Project, args: argparse.Namespace) -> int:
    added: list[dict[str, Any]] = []
    if args.ingest:
        source = Path(args.ingest).expanduser()
        if not source.is_file():
            raise AltocueError(f"No notes file at {source}.", fix="Give the file the versions page downloaded, or paste the copied notes into a file first.")
        try:
            raw = json.loads(source.read_text())
        except ValueError as error:
            raise AltocueError(f"{source} is not JSON: {error}.", fix=notes.FIX) from error
        added, _ = notes.merge(project, raw, ids(project))
    waiting = notes.still_open(notes.load(project))
    pictures = {note["id"]: notes.picture(project, note) for note in waiting if note.get("strokes")}
    if args.json:
        rows = [{**note, "picture": str(pictures[note["id"]]) if pictures.get(note["id"]) else None} for note in waiting]
        print(json.dumps({"reminder": notes.REMINDER, "added": [note["id"] for note in added], "open": rows}, indent=2, ensure_ascii=False))
        return 0
    if args.ingest:
        new = f"Added {len(added)} new note{'' if len(added) == 1 else 's'} to {notes.path(project)}."
        print(new if added else "Every note in that file was already in the project.")
    if not waiting:
        print("No open notes.")
        return 0
    print(f"{len(waiting)} open note{'' if len(waiting) == 1 else 's'}:")
    for note in waiting:
        print(note_text(note, pictures.get(note["id"]), project.root))
    print(notes.REMINDER)
    print('Fix what you can, then resolve it with the save: altocue versions save -m "..." --resolves <ids>. Say which notes you did not fix, and why.')
    return 0


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    def shared(target: argparse.ArgumentParser, *, top: bool = False, as_json: bool = False) -> None:
        """On each action too, so -p works on either side of it. SUPPRESS keeps an action from resetting a value given first."""
        target.add_argument("--project", "-p", default="." if top else argparse.SUPPRESS, help="Project folder")
        if as_json:
            target.add_argument("--json", action="store_true", default=False if top else argparse.SUPPRESS, help="Print JSON")

    shared(parser, top=True, as_json=True)
    actions = parser.add_subparsers(dest="action", metavar="ACTION")
    saving = actions.add_parser("save", help="Save the edit as it stands as the next version, with what code finds changed")
    saving.add_argument("--summary", "-m", required=True, help="One line saying what this version is")
    saving.add_argument("--prompt", action="append", nargs="+", metavar="TEXT", help="What the user asked for, in their own words. Repeat for more than one request")
    saving.add_argument("--change", action="append", nargs="+", metavar="TEXT", help="One visible change, written for the person watching. Repeat for each")
    saving.add_argument("--resolves", action="append", nargs="+", metavar="NOTE", help="Open notes this version fixes, such as n1 n3")
    saving.add_argument("--model", help="The model that made the edit, if one did")
    saving.add_argument("--file", help="The render of this edit. Defaults to the newest of out/final.mp4 and out/draft.mp4")
    saving.add_argument("--keep-full", action="store_true", help="Also keep the full render, as a hard link where the disk allows")
    shared(saving)
    shared(actions.add_parser("list", help="Every version, newest last"), as_json=True)
    showing = actions.add_parser("show", help="One version's full record")
    showing.add_argument("version", help="Such as v003")
    shared(showing, as_json=True)
    comparing = actions.add_parser("diff", help="What code finds changed between two versions")
    comparing.add_argument("old", help="The earlier version, such as v002")
    comparing.add_argument("new", nargs="?", help="The later version. Left out, the project as it stands now")
    shared(comparing, as_json=True)
    restoring = actions.add_parser("restore", help="Put a version's decision files back. Renders are rebuilt, never restored")
    restoring.add_argument("version", help="Such as v002")
    shared(restoring)
    shared(actions.add_parser("page", help="Write versions/index.html: watch, compare and note, in one file with no network"))
    serving = actions.add_parser("serve", help="Serve that page on this machine, so notes save straight into the project")
    serving.add_argument("--port", type=int, default=8765, help="Port to listen on. 0 picks a free one")
    serving.add_argument("--host", default="127.0.0.1", help="Address to listen on. Anything but 127.0.0.1 lets others on that network in")
    shared(serving)
    reading = actions.add_parser("notes", help="List open notes, with a picture of each drawing")
    reading.add_argument("--ingest", metavar="FILE", help="Add the notes in a file exported from the versions page")
    shared(reading, as_json=True)


def run(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    actions = {"save": do_save, "show": do_show, "diff": do_diff, "restore": do_restore, "page": do_page, "serve": do_serve, "notes": do_notes}
    return actions.get(args.action or "", do_list)(project, args)
