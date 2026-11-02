"""Learn from one creator's own results. Associations by default, and paired tests for anything more.

A creator folder holds `results/` and `director-notes.md`, and one folder is one creator, because
a pooled model mostly learns who is big. Nothing shows as a pattern under 20 comparable posts,
nothing is suggested under 30, and every comparison is corrected for how many were tried.
The director may read the notes. The Editor in Chief never does.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import benchmark, qa, stats
from .errors import AltocueError
from .facts import NAMES, duration_bucket
from .project import CONFIG, Project

DEFAULT_AGE = 7
AGE_FACTOR = 1.5  # posts match in age within this factor, so one export of old posts can still be compared
MIN_BASELINE = 5
BASELINE_MOST = 20
PATTERN_FROM = 20
SUGGEST_FROM = 30
MOST_NOTES = 5
SIGNIFICANT = 0.05
NOTES_LAST_DAYS = 90
PAIRS_NEEDED = 6  # five pairs all one way still give p 0.0625 on a two-sided sign test
OUTCOMES_PER_PLATFORM = 2
NOTES = "director-notes.md"

TESTED = (
    "first_word_s",
    "first_text_s",
    "hook_word_count",
    "first_cut_s",
    "cuts_per_10s",
    "scene_changes_per_10s",
    "longest_static_s",
    "caption_coverage",
    "opening_minus_integrated",
    "text_reading_margin_s",
    "loop_ssim",
)
OUTCOMES = {
    "youtube": ("stayed_to_watch", "average_percentage_viewed", "views"),
    "meta": ("skip_rate", "shares_per_reach", "views"),
}
OUTCOME_NAMES = {
    "stayed_to_watch": "stayed to watch",
    "average_percentage_viewed": "average percentage viewed",
    "views": "views",
    "skip_rate": "skip rate",
    "shares_per_reach": "shares per reach",
}
PLATFORM_NAMES = {"youtube": "YouTube", "meta": "Meta"}
COLUMNS = {
    "youtube": {
        "key": ("content", "video id", "video"),
        "published": ("video publish time", "publish time", "published"),
        "duration_s": ("duration",),
        "views": ("views",),
        "average_percentage_viewed": ("average percentage viewed (%)", "average percentage viewed"),
        "average_view_duration": ("average view duration",),
        "stayed_to_watch": ("stayed to watch (%)", "stayed to watch"),
    },
    "meta": {
        "key": ("permalink", "post id", "post_id"),
        "published": ("publish time", "published", "date"),
        "duration_s": ("duration (sec)", "duration"),
        "views": ("views", "plays"),
        "reach": ("reach",),
        "shares": ("shares", "sends"),
        "saves": ("saves",),
        "skip_rate": ("skip rate", "reels skip rate", "reels_skip_rate"),
    },
}
SIGNATURE = {"youtube": "video title", "meta": "permalink"}
NOT_METRICS = ("key", "published", "duration_s")

HEAD = (
    "For the director only. Never show this file to the Editor in Chief, because a review must stay blind to results.\n\n"
    "These come from one creator's own posts. Association, not proof. None of this says why a post did well, and none of it predicts the next one. "
    "Treat each line as a hypothesis."
)


def creator_folder(path: str | Path) -> Path:
    """One folder, one creator. A film folder here would quietly split the record in two."""
    root = Path(path).expanduser().resolve()
    if (root / CONFIG).exists():
        raise AltocueError(f"{root} is a film, not a creator folder.", fix="Run this from the folder that holds this creator's films, or pass -p with it.")
    return root


def read_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def write_lines(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def post_key(url: str) -> tuple[str, str]:
    """The platform and the id inside a post's address, however it was copied."""
    parts = urlsplit(url.strip())
    host, path = parts.netloc.lower(), [piece for piece in parts.path.split("/") if piece]
    if "youtu.be" in host and path:
        return "youtube", path[0]
    if "youtube" in host:
        found = parse_qs(parts.query).get("v", [""])[0] or (path[-1] if path else "")
        if found:
            return "youtube", found
    if ("instagram" in host or "facebook" in host or "fb." in host) and path:
        return "meta", path[-1]
    raise AltocueError(f"Cannot read a post id from {url}.", fix="Use the post's full address, or pass --id and --platform.")


def number(text: Any) -> float | None:
    value = str(text).strip().rstrip("%").replace(",", "")
    if not value:
        return None
    if ":" in value:
        return benchmark.parse_duration(value)
    try:
        return float(value)
    except ValueError:
        return None


def read_export(path: Path, *, exported: date, platform: str | None = None, mapping: dict[str, str] | None = None) -> tuple[list[dict[str, Any]], int]:
    """A Studio or Meta CSV as snapshots of each post at its age on the day it was exported."""
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    fields = reader.fieldnames or []
    lower = {name.strip().lower(): name for name in fields}
    chosen = platform or next((name for name, mark in SIGNATURE.items() if mark in lower), None)
    if chosen is None and any(alias in lower for alias in COLUMNS["youtube"]["key"]):
        chosen = "youtube"
    if chosen is None:
        chosen = "meta" if "post id" in lower else None
    if chosen is None:
        raise AltocueError(
            f"{path.name} does not look like a YouTube Studio or Meta export.",
            fix=f"It has: {', '.join(fields)}. Pass --platform, and --map \"Your column=views\" for anything named differently.",
        )
    columns = {field: [lower[alias] for alias in aliases if alias in lower] for field, aliases in COLUMNS[chosen].items()}
    for name, field in (mapping or {}).items():
        columns.setdefault(field, []).insert(0, name)
    missing = [field for field in ("key", "published") if not columns.get(field)]
    if missing:
        raise AltocueError(f"{path.name} has no {' or '.join(missing)} column.", fix=f"It has: {', '.join(fields)}. Use --map \"Your column=key\".")
    rows, skipped = [], 0
    for row in reader:
        value = {field: next((row[name] for name in names if str(row.get(name, "")).strip()), "") for field, names in columns.items()}
        published = benchmark.parse_date(value["published"])
        if str(value["key"]).strip().lower() == "total":
            continue
        if published is None or not str(value["key"]).strip() or published > exported:
            skipped += 1
            continue
        metrics = {field: found for field, raw in value.items() if field not in NOT_METRICS and (found := number(raw)) is not None}
        if metrics.get("reach") and metrics.get("shares") is not None:
            metrics["shares_per_reach"] = round(metrics["shares"] / metrics["reach"], 6)
        try:
            key = post_key(value["key"])[1] if str(value["key"]).startswith("http") else str(value["key"]).strip()
        except AltocueError:
            skipped += 1
            continue
        rows.append(
            {
                "platform": chosen,
                "key": key,
                "published": published.isoformat(),
                "exported": exported.isoformat(),
                "age_days": (exported - published).days,
                "duration_s": benchmark.parse_duration(value.get("duration_s", "")),
                "metrics": metrics,
            }
        )
    return rows, skipped


def chosen(snapshots: list[dict[str, Any]], age: int) -> dict[str, list[dict[str, Any]]]:
    """For each post, its nearest snapshot at or past the chosen age, split by platform and oldest first."""
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for snap in snapshots:
        if snap["age_days"] < age:
            continue
        key = (snap["platform"], snap["key"])
        if key not in best or snap["age_days"] < best[key]["age_days"]:
            best[key] = snap
    platforms: dict[str, list[dict[str, Any]]] = {}
    for (platform, _), snap in sorted(best.items(), key=lambda item: item[1]["published"]):
        platforms.setdefault(platform, []).append(snap)
    return platforms


def same_age(a: dict[str, Any], b: dict[str, Any]) -> bool:
    low, high = sorted((max(a["age_days"], 1), max(b["age_days"], 1)))
    return high / low <= AGE_FACTOR


def definition(post: dict[str, Any]) -> tuple[int, bool]:
    """A YouTube view change moves every number with views in it, not only the view count."""
    if post["platform"] != "youtube":
        return 0, False
    length = post.get("duration_s")
    video = benchmark.Video(channel="", views=0, published=date.fromisoformat(post["published"]), format="short" if length and length <= benchmark.SHORT_MOST else "long")
    return benchmark.definition(video)


def bucket(post: dict[str, Any]) -> str | None:
    return duration_bucket(post["duration_s"]) if post.get("duration_s") is not None else None


def normalised(posts: list[dict[str, Any]], metric: str) -> dict[str, float]:
    """Log2 of each post against the median of the creator's earlier posts at matching age and length."""
    usable = [p for p in posts if (p["metrics"].get(metric) or 0) > 0 and not definition(p)[1]]
    found: dict[str, float] = {}
    for post in usable:
        earlier = [
            p["metrics"][metric]
            for p in usable
            if p["published"] < post["published"] and bucket(p) == bucket(post) and same_age(p, post) and definition(p)[0] == definition(post)[0]
        ][-BASELINE_MOST:]
        if len(earlier) >= MIN_BASELINE:
            found[post["key"]] = math.log2(post["metrics"][metric] / stats.median(earlier))
    return found


def outcomes_for(platform: str, posts: list[dict[str, Any]]) -> list[str]:
    present = {name for post in posts for name in post["metrics"]}
    return [name for name in OUTCOMES.get(platform, ()) if name in present][:OUTCOMES_PER_PLATFORM]


def pair_rows(pairs: list[dict[str, Any]], posts: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """A paired test holds the body still, so a repeated gap sits with the opening, on that metric."""
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for pair in pairs:
        index = {p["key"]: p for p in posts.get(pair["platform"], [])}
        a, b = index.get(pair["a"]), index.get(pair["b"])
        if not a or not b or not same_age(a, b):
            continue
        metric = next((m for m in OUTCOMES.get(pair["platform"], ()) if a["metrics"].get(m) and b["metrics"].get(m)), None)
        if metric is None:
            continue
        blank = {"platform": pair["platform"], "change": pair["change"], "metric": metric, "gaps": [], "a_first": 0}
        row = groups.setdefault((pair["platform"], pair["change"], metric), blank)
        row["gaps"].append(math.log2(a["metrics"][metric] / b["metrics"][metric]))
        row["a_first"] += a["published"] < b["published"]
    rows = list(groups.values())
    for row in rows:
        row["a_higher"] = sum(gap > 0 for gap in row["gaps"])
        row["b_higher"] = sum(gap < 0 for gap in row["gaps"])
        row["pairs"] = row["a_higher"] + row["b_higher"]
        row["p"] = stats.sign_test(row["a_higher"], row["b_higher"])
        row["a_first_always"] = row["a_first"] == len(row["gaps"])
    for row, adjusted in zip(rows, stats.adjust([r["p"] for r in rows]), strict=True):
        row["adjusted_p"] = round(adjusted, 4)
        row["clear"] = row["pairs"] >= PAIRS_NEEDED and adjusted <= SIGNIFICANT
    return rows


def analyse(films: list[dict[str, Any]], snapshots: list[dict[str, Any]], pairs: list[dict[str, Any]], *, age: int, today: date) -> dict[str, Any]:
    """Spearman on every fact and outcome, corrected together, with intervals for what survives."""
    posts = chosen(snapshots, age)
    tests: list[dict[str, Any]] = []
    platforms = []
    for platform, rows in posts.items():
        mine = {f["key"]: f for f in films if f["platform"] == platform}
        comparable = 0
        for metric in outcomes_for(platform, rows):
            scores = normalised(rows, metric)
            comparable = max(comparable, len(set(scores) & set(mine)))
            for fact in TESTED:
                pairs_seen = [(mine[key]["facts"][fact], y) for key, y in scores.items() if key in mine and mine[key]["facts"].get(fact) is not None]
                if len(pairs_seen) < PATTERN_FROM:
                    continue
                x, y = [p[0] for p in pairs_seen], [p[1] for p in pairs_seen]
                rho = stats.spearman(x, y)
                if rho is None:
                    continue
                test = {"platform": platform, "fact": fact, "metric": metric, "posts": len(x), "rho": round(rho, 3), "p": stats.permutation_p(x, y)}
                tests.append({**test, "x": x, "y": y})
        platforms.append({"platform": platform, "posts": len(rows), "films": len(set(mine) & {p["key"] for p in rows}), "comparable": comparable})
    for test, adjusted in zip(tests, stats.adjust([t["p"] for t in tests]), strict=True):
        test["adjusted_p"] = round(adjusted, 4)
    findings = sorted((t for t in tests if t["adjusted_p"] <= SIGNIFICANT), key=lambda t: t["adjusted_p"])[:MOST_NOTES]
    for finding in findings:
        finding["low"], finding["high"] = (round(end, 3) for end in stats.bootstrap_interval(finding["x"], finding["y"]))
        finding["suggest"] = finding["posts"] >= SUGGEST_FROM
    for test in tests:
        del test["x"], test["y"]
    return {"age": age, "written": today.isoformat(), "platforms": platforms, "tests": tests, "findings": findings, "pairs": pair_rows(pairs, posts)}


def expiry(today: date) -> date:
    return today + timedelta(days=NOTES_LAST_DAYS)


def finding_line(finding: dict[str, Any], today: date) -> str:
    direction = "higher" if finding["rho"] > 0 else "lower"
    line = (
        f"- In your last {finding['posts']} {PLATFORM_NAMES[finding['platform']]} posts, higher {NAMES[finding['fact']].lower()} went with {direction} "
        f"{OUTCOME_NAMES[finding['metric']]}. Spearman rho {finding['rho']:+.2f}, 95% interval {finding['low']:+.2f} to {finding['high']:+.2f}, "
        f"{finding['posts']} posts, adjusted p {finding['adjusted_p']:g}. Association, not proof. Dated {today}, expires {expiry(today)}."
    )
    return line + (" To test it: run a paired test where only this changes." if finding["suggest"] else "")


def pair_line(row: dict[str, Any]) -> str:
    metric = OUTCOME_NAMES[row["metric"]]
    head = f'- "{row["change"]}" on {PLATFORM_NAMES[row["platform"]]}: {row["pairs"]} pairs, version A had the higher {metric} in {row["a_higher"]}'
    if row["pairs"] < PAIRS_NEEDED:
        return f"{head}. Too few pairs to say anything: {PAIRS_NEEDED} are needed."
    if not row["clear"]:
        return f"{head}. No clear difference, adjusted p {row['adjusted_p']:g}."
    line = f"{head}, adjusted p {row['adjusted_p']:g}. A paired result: same body, two openings, so the gap sits with the opening, on this metric, for this audience."
    line += " It says nothing about views."
    return line + (" Version A went up first every time, so timing is mixed in with the opening. Swap the order." if row["a_first_always"] else "")


def notes_text(result: dict[str, Any], today: date) -> str:
    lines = ["# Director notes", "", f"Written {today}. Expires {expiry(today)}.", "", HEAD, "", "## Patterns", ""]
    enough = [p for p in result["platforms"] if p["comparable"] >= PATTERN_FROM]
    for platform in result["platforms"]:
        if platform["comparable"] < PATTERN_FROM:
            lines.append(f"- Too few to learn from on {PLATFORM_NAMES[platform['platform']]}: {platform['comparable']} comparable posts, and {PATTERN_FROM} are needed.")
    if not result["platforms"]:
        lines.append(f"- Too few to learn from. No comparable posts yet, and {PATTERN_FROM} are needed.")
    lines += [finding_line(finding, today) for finding in result["findings"]]
    if enough and not result["findings"]:
        tried = len(result["tests"])
        lines.append(f"- No reliable pattern yet. {tried} comparisons were tried, and none held up after correcting for how many. That is the usual result.")
    if result["pairs"]:
        lines += ["", "## Paired tests", "", *[pair_line(row) for row in result["pairs"]]]
    return "\n".join([*lines, ""])


def live_notes(path: Path, today: date) -> str | None:
    """The notes while they are in date. Past its date a file is deleted, and with no date it is not read."""
    if not path.exists():
        return None
    text = path.read_text()
    found = re.search(r"Expires (\d{4}-\d{2}-\d{2})", text)
    if not found:
        return None
    if today <= date.fromisoformat(found.group(1)):
        return text
    path.unlink()
    return None


def new_pair(name: str, change: str, a: str, b: str, today: date, *, platform: str = "youtube") -> dict[str, Any]:
    return {"id": name, "change": change, "platform": platform, "a": a, "b": b, "logged": today.isoformat()}


def record_post(root: Path, film: str, url: str | None, key: str | None, platform: str | None) -> dict[str, Any]:
    """Tie a post to the exact render that went up, and keep a copy of its facts with the creator."""
    project = Project.open(film)
    path = project.review_dir / "facts.json"
    if not path.exists():
        raise AltocueError(f"{project.root.name} has no facts yet.", fix="Run `altocue facts`, then this again.")
    data = json.loads(path.read_text())
    final = project.out / "final.mp4"
    if data.get("stage") != "final":
        raise AltocueError("These facts are for a draft.", fix="Run `altocue facts` on the render you posted.")
    if final.exists() and qa.sha256(final) != data["sha256"]:
        raise AltocueError("These facts are for another render.", fix="Run `altocue facts` on the render you posted.")
    if key and platform:
        where = (platform, key)
    elif url:
        where = post_key(url)
    else:
        raise AltocueError("Say where it went up.", fix="Pass --url, or --id with --platform.")
    return {
        "platform": where[0],
        "key": where[1],
        "url": url or "",
        "sha256": data["sha256"],
        "measured_at": data["measured_at"],
        "duration_s": data["duration_s"],
        "facts": {name: data["facts"][name]["value"] for name in TESTED},
    }


def analyse_folder(root: Path, age: int, today: date) -> dict[str, Any]:
    results = root / "results"
    result = analyse(read_lines(results / "films.jsonl"), read_lines(results / "ledger.jsonl"), read_lines(results / "pairs.jsonl"), age=age, today=today)
    (root / NOTES).write_text(notes_text(result, today))
    for platform in result["platforms"]:
        name = PLATFORM_NAMES[platform["platform"]]
        print(f"{name}: {platform['posts']} posts at day {age} or later, {platform['films']} films matched, {platform['comparable']} comparable.")
    print(f"{len(result['findings'])} patterns from {len(result['tests'])} comparisons. Saved {root / NOTES}")
    return result


def today_from(args: argparse.Namespace) -> date:
    return date.fromisoformat(args.as_of) if args.as_of else date.today()


def do_post(args: argparse.Namespace, root: Path) -> int:
    row = record_post(root, args.film, args.url, args.id, args.platform)
    path = root / "results" / "films.jsonl"
    write_lines(path, [*(r for r in read_lines(path) if (r["platform"], r["key"]) != (row["platform"], row["key"])), row])
    print(f"Recorded {row['key']} on {PLATFORM_NAMES.get(row['platform'], row['platform'])}, render {row['sha256'][:12]}.")
    return 0


def do_import(args: argparse.Namespace, root: Path) -> int:
    path = Path(args.csv).expanduser()
    if not path.is_file():
        raise AltocueError(f"No file at {path}.", fix="Export the table from YouTube Studio or Meta Business Suite first.")
    exported = date.fromisoformat(args.exported) if args.exported else datetime.fromtimestamp(path.stat().st_mtime).date()
    mapping = dict(item.split("=", 1) for item in args.map or [] if "=" in item)
    rows, skipped = read_export(path, exported=exported, platform=args.platform, mapping=mapping)
    ledger = root / "results" / "ledger.jsonl"
    merged = {(r["platform"], r["key"], r["exported"]): r for r in [*read_lines(ledger), *rows]}
    write_lines(ledger, list(merged.values()))
    films = {(f["platform"], f["key"]) for f in read_lines(root / "results" / "films.jsonl")}
    matched = sum((r["platform"], r["key"]) in films for r in rows)
    print(f"{len(rows)} rows read, exported {exported}. {matched} films matched." + (f" {skipped} rows could not be read." if skipped else ""))
    analyse_folder(root, args.age, today_from(args))
    return 0


def do_pair(args: argparse.Namespace, root: Path) -> int:
    (first, a), (second, b) = post_key(args.a), post_key(args.b)
    if first != second:
        raise AltocueError("A paired test compares two posts on the same platform.", fix="Post both versions in the same place.")
    path = root / "results" / "pairs.jsonl"
    rows = read_lines(path)
    write_lines(path, [*rows, new_pair(f"p{len(rows) + 1}", args.change, a, b, today_from(args), platform=first)])
    print(f'Logged a paired test of "{args.change}". Only that one thing may differ between A and B. Altocue cannot check that for you.')
    return 0


def do_notes(args: argparse.Namespace, root: Path) -> int:
    print(live_notes(root / NOTES, today_from(args)) or f"No live notes. Notes last {NOTES_LAST_DAYS} days.")
    return 0


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    def shared(target: argparse.ArgumentParser, *, top: bool) -> None:
        """On each action too, so -p works on either side of it. SUPPRESS keeps an action from resetting a value given first."""
        target.add_argument("--project", "-p", default="." if top else argparse.SUPPRESS, help="The creator's folder. One folder, one creator")
        target.add_argument("--age", type=int, default=DEFAULT_AGE if top else argparse.SUPPRESS, help="Compare posts from this many days old")
        target.add_argument("--as-of", dest="as_of", default=None if top else argparse.SUPPRESS, help="Treat this date as today, YYYY-MM-DD")

    shared(parser, top=True)
    actions = parser.add_subparsers(dest="action")
    post = actions.add_parser("post", help="Record where a film went up, with its facts")
    post.add_argument("film", help="The film's project folder")
    post.add_argument("--url", help="The post's address")
    post.add_argument("--id", help="The post's id, if there is no address")
    post.add_argument("--platform", choices=sorted(OUTCOMES), help="Needed with --id")
    shared(post, top=False)
    read = actions.add_parser("import", help="Read a YouTube Studio or Meta CSV, then write the notes")
    read.add_argument("csv", help="The exported CSV")
    read.add_argument("--platform", choices=sorted(OUTCOMES), help="Which export this is, if it cannot be told")
    read.add_argument("--exported", help="The day it was exported, YYYY-MM-DD. Defaults to the file's date")
    read.add_argument("--map", action="append", metavar="COLUMN=FIELD", help="Read a column under another name, such as Plays=views")
    shared(read, top=False)
    pair = actions.add_parser("pair", help="Log a paired test: two openings on one body")
    pair.add_argument("--change", required=True, help="The one thing that differs")
    pair.add_argument("--a", required=True, help="Address of the version with the change")
    pair.add_argument("--b", required=True, help="Address of the version without it")
    shared(pair, top=False)
    notes = actions.add_parser("notes", help="Print the notes, if they are still in date")
    shared(notes, top=False)


def run(args: argparse.Namespace) -> int:
    root = creator_folder(args.project)
    root.mkdir(parents=True, exist_ok=True)
    action = {"post": do_post, "import": do_import, "pair": do_pair, "notes": do_notes}.get(args.action or "")
    if action:
        return action(args, root)
    analyse_folder(root, args.age, today_from(args))
    return 0
