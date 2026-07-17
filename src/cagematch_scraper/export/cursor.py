"""Line-count cursor for `export nightly`'s fast path.

Mirrors the spirit of `runner.py`'s existing `.proxy_cursor` file: a small JSON
marker under `data/` recording how many lines of each source JSONL have already
been loaded into the warehouse. It only gates whether a file is *read* at all —
it never gates correctness, since every load is deduped by primary key against
the warehouse (see `warehouse.py`).
"""

from __future__ import annotations

import json
from pathlib import Path


def load_cursor(cursor_path: Path) -> dict[str, int]:
    if not cursor_path.exists():
        return {}
    try:
        return json.loads(cursor_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_cursor(cursor_path: Path, cursor: dict[str, int]) -> None:
    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    cursor_path.write_text(json.dumps(cursor, indent=2) + "\n", encoding="utf-8")


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())
