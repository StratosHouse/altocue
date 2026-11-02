#!/usr/bin/env python3
"""Turn Natural Earth 110m shapes into the compact path data the map formats read. Dev tool: run it by hand, the output is checked in."""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

SOURCE = "https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json"
TARGET = Path(__file__).resolve().parents[1] / "src/altocue/data/maps/countries-110m.json"
UNITS = 20  # stored numbers are twentieths of a degree


def decode_arcs(topology: dict) -> list[list[tuple[float, float]]]:
    """TopoJSON arcs are delta-encoded integers. Undo that and return longitude and latitude."""
    (scale_x, scale_y), (move_x, move_y) = topology["transform"]["scale"], topology["transform"]["translate"]
    arcs = []
    for arc in topology["arcs"]:
        x = y = 0
        points = []
        for step_x, step_y in arc:
            x, y = x + step_x, y + step_y
            points.append((x * scale_x + move_x, y * scale_y + move_y))
        arcs.append(points)
    return arcs


def ring(indices: list[int], arcs: list[list[tuple[float, float]]]) -> list[tuple[int, int]]:
    """Stitch a ring from its arcs. A negative index means that arc runs backwards. Rounding absolute values keeps shared borders identical."""
    points: list[tuple[int, int]] = []
    for index in indices:
        arc = arcs[index] if index >= 0 else arcs[~index][::-1]
        for lon, lat in arc[1 if points else 0 :]:
            point = (round(lon * UNITS), round(lat * UNITS))
            if not points or points[-1] != point:
                points.append(point)
    return points


def path(rings: list[list[tuple[int, int]]]) -> str:
    """SVG path syntax in twentieths of a degree, relative after the first point of each ring."""

    def join(numbers: list[int]) -> str:
        out = ""
        for number in numbers:
            out += f"{'' if number < 0 or not out else ' '}{number}"
        return out

    parts = []
    for points in rings:
        if len(points) < 3:
            continue
        steps: list[int] = []
        for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
            steps += [x1 - x0, y1 - y0]
        parts.append(f"M{join(list(points[0]))}l{join(steps)}z")
    return "".join(parts)


def polygons(geometry: dict) -> list[list[list[int]]]:
    return geometry["arcs"] if geometry["type"] == "MultiPolygon" else [geometry["arcs"]]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=SOURCE, help="A URL or a local file")
    parser.add_argument("--out", type=Path, default=TARGET)
    args = parser.parse_args()
    if args.source.startswith("http"):
        with urllib.request.urlopen(args.source, timeout=30) as reply:
            topology = json.load(reply)
    else:
        topology = json.loads(Path(args.source).read_text())
    arcs = decode_arcs(topology)
    countries = {}
    for geometry in topology["objects"]["countries"]["geometries"]:
        rings = [ring(part, arcs) for polygon in polygons(geometry) for part in polygon]
        countries[geometry["properties"]["name"]] = path(rings)
    land_rings = [ring(part, arcs) for geometry in topology["objects"]["land"]["geometries"] for polygon in polygons(geometry) for part in polygon]
    data = {"units_per_degree": UNITS, "land": path(land_rings), "countries": dict(sorted(countries.items()))}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(args.out, f"{args.out.stat().st_size} bytes, {len(countries)} countries")


if __name__ == "__main__":
    main()
