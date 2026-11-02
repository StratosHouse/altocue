"""A project's yes to sending its own files to a provider whose terms let it train on them.

The yes is per project and per set of terms, so new terms ask again. Nothing here runs a
hosted call: it is the gate every route passes before one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..errors import AltocueError
from ..project import Project, write_json
from . import store
from .catalog import Catalog

CONSENT = "consent.json"


def consent_path(project: Project) -> Path:
    return project.root / CONSENT


def warning(catalog: Catalog, provider: str) -> str:
    """The named warning. Only a provider with one in models.toml is gated at all."""
    return str(catalog.providers.get(provider, {}).get("upload_warning", ""))


def agreed(project: Project, provider: str) -> dict[str, Any] | None:
    path = consent_path(project)
    return json.loads(path.read_text()).get("uploads", {}).get(provider) if path.exists() else None


def allowed(project: Project, catalog: Catalog, provider: str) -> bool:
    if not warning(catalog, provider):
        return True
    entry = agreed(project, provider)
    return bool(entry) and entry.get("terms_as_of") == catalog.provider(provider).get("terms_as_of")


def allow(project: Project, catalog: Catalog, provider: str) -> dict[str, Any]:
    """Record this project's yes, against the terms it was given for."""
    terms = catalog.provider(provider).get("terms_as_of", "")
    entry = {"agreed": store.now(), "terms_as_of": terms, "warning": warning(catalog, provider)}
    path = consent_path(project)
    data = json.loads(path.read_text()) if path.exists() else {}
    data.setdefault("uploads", {})[provider] = entry
    write_json(path, data)
    return entry


def require(project: Project, catalog: Catalog, provider: str, what: str) -> None:
    """Stop before a project's files leave for a provider it has not agreed to."""
    if allowed(project, catalog, provider):
        return
    entry = agreed(project, provider)
    label = catalog.provider(provider).get("label", provider)
    lapsed = f" This project agreed under terms dated {entry['terms_as_of']}, and the terms are now dated {catalog.provider(provider)['terms_as_of']}." if entry else ""
    raise AltocueError(
        f"{what} would send project files to {label}, and this project has not agreed to that.{lapsed}\n{warning(catalog, provider)}",
        fix=f"Show the owner that warning. If they agree, run: altocue generate --allow-upload {provider}",
    )
