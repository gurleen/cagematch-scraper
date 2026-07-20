import json
from pathlib import Path

from cagematch_scraper.runner import _load_existing_ids, _load_existing_items


def test_load_existing_ids_missing_file(tmp_path: Path) -> None:
    assert _load_existing_ids(tmp_path / "missing.jsonl") == set()
    assert _load_existing_items(tmp_path / "missing.jsonl") == {}


def test_load_existing_ids_valid_file(tmp_path: Path) -> None:
    path = tmp_path / "matches.jsonl"
    path.write_text(
        "\n".join(
            json.dumps({"id": item_id, "name": f"event {item_id}"}) for item_id in ("1", "2", "3")
        )
        + "\n",
        encoding="utf-8",
    )

    assert _load_existing_ids(path) == {"1", "2", "3"}
    items = _load_existing_items(path)
    assert set(items) == {"1", "2", "3"}
    assert items["2"]["name"] == "event 2"
    # untouched when nothing is corrupt
    assert path.read_text(encoding="utf-8").count("\n") == 3


def test_load_existing_items_keeps_newest_line_per_id(tmp_path: Path) -> None:
    path = tmp_path / "matches.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"id": "1", "matches": []}),
                json.dumps({"id": "1", "matches": [{"match_index": 1}]}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    items = _load_existing_items(path)
    assert set(items) == {"1"}
    assert items["1"]["matches"] == [{"match_index": 1}]


def test_load_existing_ids_drops_trailing_corrupt_line(tmp_path: Path) -> None:
    path = tmp_path / "matches.jsonl"
    good_lines = [json.dumps({"id": "1"}), json.dumps({"id": "2"})]
    path.write_text("\n".join(good_lines) + '\n{"id": "3", "name": "trunca', encoding="utf-8")

    ids = _load_existing_ids(path)

    assert ids == {"1", "2"}
    remaining_lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
    assert len(remaining_lines) == 2
    assert all(json.loads(line)["id"] in {"1", "2"} for line in remaining_lines)
