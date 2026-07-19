import json
from pathlib import Path

from cagematch_scraper.export import changes


def test_ids_from_jsonl_honors_start_line(tmp_path: Path) -> None:
    path = tmp_path / "matches.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"id": "old"}),
                json.dumps({"id": "new-1"}),
                "not json",
                json.dumps({"id": "new-2"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert changes.ids_from_jsonl(path, start_line=1) == {"new-1", "new-2"}


def test_merge_accumulates_until_cleared(tmp_path: Path) -> None:
    path = tmp_path / ".export_changes.json"

    changes.merge(path, {"matches": {"e1"}, "wrestlers": {"w1"}})
    changes.merge(path, {"matches": {"e1", "e2"}})

    assert changes.load(path) == {
        "matches": {"e1", "e2"},
        "wrestlers": {"w1"},
    }

    changes.clear(path)
    assert changes.load(path) == {}
