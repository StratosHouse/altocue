"""A project's yes to sending its own files to a provider whose terms let it train on them.

The yes is per project and per set of terms, so new terms ask again. Nothing here runs a
hosted call: it is the gate every route passes before one.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from ..errors import AltocueError
from ..project import Project, write_json
from . import store
from .catalog import Catalog, load_catalog

CONSENT = "consent.json"


def consent_path(project: Project) -> Path:
    return project.root / CONSENT


def warning(catalog: Catalog, provider: str) -> str:
    """The named warning. A project's own catalog can add one, an old copy cannot switch one off, and newer terms bring their own words."""
    own, packaged = catalog.providers.get(provider, {}), load_catalog().providers.get(provider, {})
    first, second = (packaged, own) if _day(str(packaged.get("terms_as_of", ""))) > _day(str(own.get("terms_as_of", ""))) else (own, packaged)
    return str(first.get("upload_warning") or second.get("upload_warning", ""))


def agreed(project: Project, provider: str) -> dict[str, Any] | None:
    path = consent_path(project)
    return json.loads(path.read_text()).get("uploads", {}).get(provider) if path.exists() else None


def terms(catalog: Catalog, provider: str) -> str:
    """The date of the terms a yes must be for: the newer of the project's catalog and the packaged one.

    A project that sets its own `models` keeps the date it was copied with, so new terms would never ask again.
    """
    dates = [str(catalog.provider(provider).get("terms_as_of", "")), str(load_catalog().providers.get(provider, {}).get("terms_as_of", ""))]
    return max(dates, key=_day)


def _day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return date.min


def allowed(project: Project, catalog: Catalog, provider: str) -> bool:
    if not warning(catalog, provider):
        return True
    entry = agreed(project, provider)
    return bool(entry) and entry.get("terms_as_of") == terms(catalog, provider)


def allow(project: Project, catalog: Catalog, provider: str) -> dict[str, Any]:
    """Record this project's yes, against the terms it was given for."""
    entry = {"agreed": store.now(), "terms_as_of": terms(catalog, provider), "warning": warning(catalog, provider)}
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
    lapsed = f" This project agreed under terms dated {entry['terms_as_of']}, and the terms are now dated {terms(catalog, provider)}." if entry else ""
    raise AltocueError(
        f"{what} would send project files to {label}, and this project has not agreed to that.{lapsed}\n{warning(catalog, provider)}",
        fix=f"Show the owner that warning. If they agree, run: altocue generate --allow-upload {provider}",
    )
