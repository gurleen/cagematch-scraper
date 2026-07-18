"""DuckDB warehouse: flattens data/*.jsonl into schema.sql's tables and exports parquet.

The DuckDB file at `settings.warehouse_path` is the persistent source of truth. Every
load is an `INSERT ... ON CONFLICT` against it, so loading is always safe to repeat —
correctness never depends on knowing exactly which rows are "new" (see `cursor.py` for
the fast-path optimization that skips unchanged files, which is a pure speed-up, not a
correctness mechanism).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import duckdb

logger = logging.getLogger(__name__)

_EXPORT_DIR = Path(__file__).parent
_SCHEMA_SQL = (_EXPORT_DIR / "schema.sql").read_text(encoding="utf-8")
_TRANSFORM_SQL = (_EXPORT_DIR / "transform.sql").read_text(encoding="utf-8")
_MATCH_SQL = (_EXPORT_DIR / "match.sql").read_text(encoding="utf-8")

# Tables in dependency order (parents before children), used both for parquet export
# and to report row counts after a load.
TABLES = [
    "promotions",
    "promotion_name_history",
    "wrestlers",
    "wrestler_promotions",
    "wrestler_attributes",
    "wrestler_roles",
    "wrestler_role_date_ranges",
    "titles",
    "title_reigns",
    "title_reign_champions",
    "events",
    "event_commentators",
    "matches",
    "match_notes",
    "match_sides",
    "match_side_participants",
    "sdh_titles",
    "sdh_title_name_history",
    "sdh_title_reigns",
    "sdh_title_reign_champions",
    "sdh_wrestlers",
    "sdh_wrestler_attributes",
    "sdh_wrestler_name_history",
    "sdh_wrestler_promotions",
    "sdh_wrestler_roles",
    "sdh_wrestler_alignments",
    "sdh_wrestler_images",
    "wrestler_crosswalk",
    "title_crosswalk",
]

# Source name -> jsonl filename stem, matches spider names / `-- @source:` markers.
SOURCES = ["promotions", "wrestlers", "titles", "matches", "sdh_titles", "sdh_wrestlers"]


def _split_statements(sql: str) -> list[str]:
    """Split a SQL blob into individual statements on `;`, ignoring semicolons that
    appear inside `--` comments (full-line or trailing) — schema.sql's prose has
    semicolons in a few of both. None of our SQL uses `--` inside a string literal,
    so stripping everything from `--` to end of line is safe here.
    """
    without_comments = "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())
    return [s.strip() for s in without_comments.split(";") if s.strip()]


def _parse_transform_blocks() -> dict[str, list[str]]:
    """Split transform.sql into {source_name: [statement, ...]} by `-- @source:` markers."""
    parts = re.split(r"^-- @source: (\w+)\s*$", _TRANSFORM_SQL, flags=re.MULTILINE)
    blocks: dict[str, list[str]] = {}
    for name, body in zip(parts[1::2], parts[2::2]):
        blocks[name] = _split_statements(body)
    return blocks


_TRANSFORM_BLOCKS = _parse_transform_blocks()


def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(_SCHEMA_SQL)


def load_source(con: duckdb.DuckDBPyConnection, source: str, jsonl_path: Path) -> None:
    """Flatten one source JSONL file into the warehouse. No-op if the file doesn't exist."""
    if not jsonl_path.exists():
        logger.info("Skipping %s: %s not found", source, jsonl_path)
        return

    escaped_path = str(jsonl_path).replace("'", "''")
    for statement in _TRANSFORM_BLOCKS[source]:
        con.execute(statement.replace("{path}", escaped_path))


def build_crosswalks(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Derive the Cagematch<->SDH crosswalk tables (and join views) from loaded data.

    Safe to run repeatedly: each crosswalk is fully rebuilt from the current tables.
    Returns row counts for the crosswalk tables.
    """
    for statement in _split_statements(_MATCH_SQL):
        con.execute(statement)
    return {
        table: con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in ("wrestler_crosswalk", "title_crosswalk")
    }


def table_counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    return {table: con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in TABLES}


def export_parquet(con: duckdb.DuckDBPyConnection, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for table in TABLES:
        dest = out_dir / f"{table}.parquet"
        con.execute(f"COPY (SELECT * FROM {table}) TO '{dest}' (FORMAT parquet)")


def _postgres_schema_statements() -> list[str]:
    """schema.sql's statements, adapted for DuckDB's postgres attachment: it can't push
    down `CREATE SEQUENCE` at all ("Postgres databases do not support creating
    sequences"). `wrestler_roles.id` only ever gets its default `nextval(...)` value
    when a row is inserted *without* an explicit id — `sync_postgres` always inserts the
    exact id already assigned by the local warehouse, so Postgres doesn't need the
    sequence or the default; both are dropped for this variant.
    """
    statements = [s for s in _split_statements(_SCHEMA_SQL) if not s.startswith("CREATE SEQUENCE")]
    return [re.sub(r"\s*DEFAULT nextval\('\w+'\)", "", s) for s in statements]


def sync_postgres(con: duckdb.DuckDBPyConnection, postgres_url: str) -> dict[str, int]:
    """Mirror the local warehouse's current state into a Postgres database.

    Full-refresh, not incremental: deletes every table and reinserts everything from
    the local warehouse. The clear+reinsert runs in a single transaction so a mid-sync
    failure rolls back instead of leaving tables empty. The local warehouse is already
    deduped/idempotent, so a full reinsert is simpler and more robust than depending on
    `ON CONFLICT` push-down through DuckDB's postgres attachment, and at this data scale
    (tens of thousands of rows) it's a sub-few-second operation.

    schema.sql's DDL is run unqualified (not prefixed with the attached catalog name)
    under `USE pg`: DuckDB resolves an unqualified `REFERENCES table(...)` against
    whatever the *default* catalog is at execution time, not the table-being-created's
    own catalog, so both a `pg.`-qualified REFERENCES *and* an unqualified one fail
    outside of a `USE pg` context (the former because Postgres has no schema literally
    named "pg", the latter because it points at the local warehouse instead). Switching
    the default catalog is the only combination that works for both the CREATE TABLE
    target and its REFERENCES clause simultaneously. DDL is left outside the data
    transaction — it is idempotent `CREATE TABLE IF NOT EXISTS`.
    """
    local_catalog = con.execute("SELECT current_catalog()").fetchone()[0]
    con.execute("INSTALL postgres; LOAD postgres;")
    escaped_url = postgres_url.replace("'", "''")
    con.execute(f"ATTACH '{escaped_url}' AS pg (TYPE postgres)")
    try:
        try:
            con.execute("USE pg")
            for statement in _postgres_schema_statements():
                con.execute(statement)
        finally:
            con.execute(f"USE {local_catalog}")

        # DuckDB rewrites `TRUNCATE` through a postgres attachment into a plain
        # `DELETE` (confirmed via its error output), which doesn't cascade the way a
        # native Postgres `TRUNCATE ... CASCADE` would — so clear tables ourselves in
        # reverse dependency order (children before parents) rather than relying on
        # CASCADE from the roots. All writes go to the attached `pg` catalog, so a
        # single DuckDB transaction (one attached DB) keeps the mirror atomic.
        con.execute("BEGIN TRANSACTION")
        try:
            for table in reversed(TABLES):
                con.execute(f"DELETE FROM pg.{table}")
            for table in TABLES:
                # Insert by explicit column name rather than positional `SELECT *`: the
                # Postgres target may be a *superset* of the local table (e.g. an
                # out-of-band `event_date` column added to Supabase directly), in which
                # case a positional insert fails with a column-count mismatch. Naming the
                # local table's columns fills exactly those and leaves any extra Postgres
                # columns to their own defaults.
                columns = [desc[0] for desc in con.execute(f"SELECT * FROM {table} LIMIT 0").description]
                col_list = ", ".join(f'"{c}"' for c in columns)
                con.execute(f"INSERT INTO pg.{table} ({col_list}) SELECT {col_list} FROM {table}")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        return {table: con.execute(f"SELECT count(*) FROM pg.{table}").fetchone()[0] for table in TABLES}
    finally:
        con.execute("DETACH pg")
