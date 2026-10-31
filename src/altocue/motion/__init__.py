"""Graphics as transparent layers. The core only ever sees a spec and a finished layer."""

from __future__ import annotations

import argparse

from .. import brand as brands
from ..errors import AltocueError
from ..project import Project
from .basic import BasicRenderer
from .hyperframes import HyperFramesRenderer
from .layer import Layer, Renderer

RENDERERS: dict[str, type[Renderer]] = {"hyperframes": HyperFramesRenderer, "basic": BasicRenderer}


def add_arguments(name: str, parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", "-p", default=".", help="Project folder")
    parser.add_argument("--renderer", choices=[*RENDERERS, "auto"], default="auto")
    parser.add_argument("--draft", action="store_true", help="Lower quality for a quick look")


def pick(name: str = "auto") -> Renderer:
    if name != "auto":
        renderer = RENDERERS[name]()
        ok, why = renderer.available()
        if not ok:
            raise AltocueError(f"The {name} renderer is not ready: {why}", fix="Run `altocue setup`, or use `--renderer basic`.")
        return renderer
    for cls in RENDERERS.values():
        renderer = cls()
        if renderer.available()[0]:
            return renderer
    raise AltocueError("No renderer is available.", fix="Run `altocue doctor`.")


def render_layers(project: Project, *, renderer: str = "auto", draft: bool = False) -> Layer:
    from .. import timeline as timelines

    timeline = timelines.build(project)
    chosen = pick(renderer)
    return chosen.render(timeline, brands.load(project), project.layers_dir, draft=draft)


def run(args: argparse.Namespace) -> int:
    layer = render_layers(Project.open(args.project), renderer=args.renderer, draft=args.draft)
    print(f"{layer.renderer} layer: {layer.path}")
    return 0
