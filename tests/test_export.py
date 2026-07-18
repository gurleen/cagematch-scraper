import json
from pathlib import Path

import duckdb

from cagematch_scraper.export import warehouse


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _fresh_con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    warehouse.ensure_schema(con)
    return con


def test_promotions_flatten_with_name_history(tmp_path: Path) -> None:
    path = tmp_path / "promotions.jsonl"
    _write_jsonl(
        path,
        [
            {
                "id": "1",
                "name": "WWE",
                "profile_url": "https://example.com/1",
                "location": "Stamford",
                "active_year_start": 1948,
                "active_year_end": None,
                "rating": 7.5,
                "votes": 100,
                "name_history": [
                    {"name": "WWF", "from_date": "1979", "to_date": "2002"},
                    {"name": "WWE", "from_date": "2002", "to_date": None},
                ],
            }
        ],
    )
    con = _fresh_con()
    warehouse.load_source(con, "promotions", path)

    assert con.execute("SELECT count(*) FROM promotions").fetchone()[0] == 1
    history = con.execute(
        "SELECT seq, name FROM promotion_name_history ORDER BY seq"
    ).fetchall()
    assert history == [(0, "WWF"), (1, "WWE")]


def test_promotions_flatten_without_name_history(tmp_path: Path) -> None:
    """A field absent from every record must not break the explicit-schema read."""
    path = tmp_path / "promotions.jsonl"
    _write_jsonl(path, [{"id": "1", "name": "WWE", "rating": 7.5, "votes": 100}])
    con = _fresh_con()
    warehouse.load_source(con, "promotions", path)

    assert con.execute("SELECT count(*) FROM promotions").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM promotion_name_history").fetchone()[0] == 0


def test_wrestler_attributes_eav_flatten(tmp_path: Path) -> None:
    path = tmp_path / "wrestlers.jsonl"
    _write_jsonl(
        path,
        [
            {
                "id": "1",
                "name": "Test Wrestler",
                "nicknames": ["The Test"],
                "trainers": ["Coach A", "Coach B"],
                "roles": [
                    {
                        "role": "Wrestler",
                        "date_ranges": [{"from_date": "2010", "to_date": None}],
                    }
                ],
            }
        ],
    )
    con = _fresh_con()
    warehouse.load_source(con, "wrestlers", path)

    attrs = con.execute(
        "SELECT attr_type, seq, value FROM wrestler_attributes ORDER BY attr_type, seq"
    ).fetchall()
    assert attrs == [
        ("nickname", 0, "The Test"),
        ("trainer", 0, "Coach A"),
        ("trainer", 1, "Coach B"),
    ]

    roles = con.execute("SELECT role FROM wrestler_roles").fetchall()
    assert roles == [("Wrestler",)]
    date_ranges = con.execute(
        "SELECT from_date, to_date FROM wrestler_role_date_ranges"
    ).fetchall()
    assert date_ranges == [("2010", None)]


def test_match_sides_decisive_vs_non_decisive(tmp_path: Path) -> None:
    path = tmp_path / "matches.jsonl"
    _write_jsonl(
        path,
        [
            {
                "id": "e1",
                "name": "Test Event",
                "matches": [
                    {
                        "match_index": 1,
                        "result": "decisive",
                        "match_rating": 8.83,
                        "match_votes": 984,
                        "won_rating": "*****1/2",
                        "winners": {
                            "wrestlers": [{"id": "w1", "name": "Winner"}],
                            "is_champion": True,
                        },
                        "losers": [
                            {"wrestlers": [{"id": "w2", "name": "Loser"}], "is_champion": False}
                        ],
                    },
                    {
                        "match_index": 2,
                        "result": "no_decision",
                        "sides": [
                            {"wrestlers": [{"id": "w3", "name": "A"}]},
                            {"wrestlers": [{"id": "w4", "name": "B"}]},
                        ],
                        "notes": ["Double Count Out"],
                    },
                ],
            }
        ],
    )
    con = _fresh_con()
    warehouse.load_source(con, "matches", path)

    assert con.execute("SELECT count(*) FROM events").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM matches").fetchone()[0] == 2

    won = con.execute(
        "SELECT won_rating FROM matches WHERE id = 'e1-1'"
    ).fetchone()
    assert won == ("*****1/2",)
    assert con.execute("SELECT won_rating FROM matches WHERE id = 'e1-2'").fetchone() == (None,)

    decisive_sides = con.execute(
        "SELECT side_role FROM match_sides WHERE match_id = 'e1-1' ORDER BY side_role"
    ).fetchall()
    assert decisive_sides == [("loser",), ("winner",)]

    non_decisive_sides = con.execute(
        "SELECT side_role, side_index FROM match_sides WHERE match_id = 'e1-2' ORDER BY side_index"
    ).fetchall()
    assert non_decisive_sides == [("side", 0), ("side", 1)]

    notes = con.execute("SELECT note FROM match_notes WHERE match_id = 'e1-2'").fetchall()
    assert notes == [("Double Count Out",)]

    participant = con.execute(
        "SELECT participant_id FROM match_side_participants WHERE match_side_id = 'e1-1-winner-0'"
    ).fetchall()
    assert participant == [("w1",)]


def test_load_source_missing_file_is_noop(tmp_path: Path) -> None:
    con = _fresh_con()
    warehouse.load_source(con, "titles", tmp_path / "titles.jsonl")
    assert con.execute("SELECT count(*) FROM titles").fetchone()[0] == 0


def test_load_source_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "promotions.jsonl"
    _write_jsonl(path, [{"id": "1", "name": "WWE", "rating": 7.5, "votes": 100}])
    con = _fresh_con()
    warehouse.load_source(con, "promotions", path)
    warehouse.load_source(con, "promotions", path)

    assert con.execute("SELECT count(*) FROM promotions").fetchone()[0] == 1


def test_export_parquet_writes_all_tables(tmp_path: Path) -> None:
    path = tmp_path / "promotions.jsonl"
    _write_jsonl(path, [{"id": "1", "name": "WWE", "rating": 7.5, "votes": 100}])
    con = _fresh_con()
    warehouse.load_source(con, "promotions", path)

    out_dir = tmp_path / "parquet"
    warehouse.export_parquet(con, out_dir)

    assert (out_dir / "promotions.parquet").exists()
    assert (out_dir / "match_side_participants.parquet").exists()
    result = con.execute(f"SELECT count(*) FROM '{out_dir / 'promotions.parquet'}'").fetchone()
    assert result[0] == 1


def test_split_statements_ignores_semicolons_in_comments() -> None:
    sql = """
    -- a comment with a semicolon; right here
    CREATE TABLE foo (
        id VARCHAR NOT NULL,   -- trailing comment with one too; see?
        name VARCHAR
    );
    CREATE TABLE bar (id VARCHAR);
    """
    statements = warehouse._split_statements(sql)
    assert len(statements) == 2
    assert statements[0].startswith("CREATE TABLE foo")
    assert statements[1] == "CREATE TABLE bar (id VARCHAR)"


def test_postgres_schema_statements_drop_sequence_and_default() -> None:
    statements = warehouse._postgres_schema_statements()

    assert not any(s.startswith("CREATE SEQUENCE") for s in statements)
    wrestler_roles_stmt = next(s for s in statements if s.startswith("CREATE TABLE IF NOT EXISTS wrestler_roles"))
    assert "nextval" not in wrestler_roles_stmt
    assert "id              INTEGER PRIMARY KEY," in wrestler_roles_stmt
