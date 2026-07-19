"""Persistent changed-entity manifest for incremental Postgres synchronization."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path


Changes = dict[str, set[str]]


def load(path: Path) -> Changes:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {
        str(source): {str(entity_id) for entity_id in ids}
        for source, ids in raw.items()
        if isinstance(ids, list)
    }


def save(path: Path, changes: Changes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        source: sorted(ids)
        for source, ids in sorted(changes.items())
        if ids
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def merge(path: Path, additions: Changes) -> Changes:
    combined = load(path)
    for source, ids in additions.items():
        combined.setdefault(source, set()).update(str(entity_id) for entity_id in ids)
    save(path, combined)
    return combined


def ids_from_jsonl(path: Path, start_line: int = 0) -> set[str]:
    """Read entity IDs from non-empty JSONL lines at or after ``start_line``."""
    ids: set[str] = set()
    if not path.exists():
        return ids
    with path.open(encoding="utf-8") as file:
        for index, line in enumerate(file):
            if index < start_line or not line.strip():
                continue
            try:
                entity_id = json.loads(line)["id"]
            except (json.JSONDecodeError, KeyError):
                continue
            ids.add(str(entity_id))
    return ids


def clear(path: Path) -> None:
    path.unlink(missing_ok=True)
