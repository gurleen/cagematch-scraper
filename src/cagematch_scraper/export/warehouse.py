"""DuckDB warehouse: flattens data/*.jsonl into schema.sql's tables and exports parquet.

The DuckDB file at `settings.warehouse_path` is the persistent source of truth. Every
load is an `INSERT ... ON CONFLICT` against it, so loading is always safe to repeat —
correctness never depends on knowing exactly which rows are "new" (see `cursor.py` for
the fast-path optimization that skips unchanged files, which is a pure speed-up, not a
correctness mechanism).
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
from pathlib import Path

import duckdb

from . import changes as change_manifest

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

# Tables belonging to each top-level JSONL entity, in parent-before-child insert
# order. Filters use {ids} for a SQL literal list and {catalog} where a child must
# traverse its parent table. Incremental Postgres sync deletes these in reverse
# order, then reinserts the current local subtree in this order.
_SOURCE_SYNC_SPECS: dict[str, list[tuple[str, str]]] = {
    "promotions": [
        ("promotions", "id IN ({ids})"),
        ("promotion_name_history", "promotion_id IN ({ids})"),
    ],
    "wrestlers": [
        ("wrestlers", "id IN ({ids})"),
        ("wrestler_promotions", "wrestler_id IN ({ids})"),
        ("wrestler_attributes", "wrestler_id IN ({ids})"),
        ("wrestler_roles", "wrestler_id IN ({ids})"),
        (
            "wrestler_role_date_ranges",
            "wrestler_role_id IN (SELECT id FROM {catalog}wrestler_roles "
            "WHERE wrestler_id IN ({ids}))",
        ),
    ],
    "titles": [
        ("titles", "id IN ({ids})"),
        ("title_reigns", "title_id IN ({ids})"),
        (
            "title_reign_champions",
            "title_reign_id IN (SELECT id FROM {catalog}title_reigns "
            "WHERE title_id IN ({ids}))",
        ),
    ],
    "matches": [
        ("events", "id IN ({ids})"),
        ("event_commentators", "event_id IN ({ids})"),
        ("matches", "event_id IN ({ids})"),
        (
            "match_notes",
            "match_id IN (SELECT id FROM {catalog}matches WHERE event_id IN ({ids}))",
        ),
        (
            "match_sides",
            "match_id IN (SELECT id FROM {catalog}matches WHERE event_id IN ({ids}))",
        ),
        (
            "match_side_participants",
            "match_side_id IN (SELECT id FROM {catalog}match_sides WHERE match_id IN "
            "(SELECT id FROM {catalog}matches WHERE event_id IN ({ids})))",
        ),
    ],
    "sdh_titles": [
        ("sdh_titles", "id IN ({ids})"),
        ("sdh_title_name_history", "title_id IN ({ids})"),
        ("sdh_title_reigns", "title_id IN ({ids})"),
        (
            "sdh_title_reign_champions",
            "title_reign_id IN (SELECT id FROM {catalog}sdh_title_reigns "
            "WHERE title_id IN ({ids}))",
        ),
    ],
    "sdh_wrestlers": [
        ("sdh_wrestlers", "id IN ({ids})"),
        ("sdh_wrestler_attributes", "wrestler_id IN ({ids})"),
        ("sdh_wrestler_name_history", "wrestler_id IN ({ids})"),
        ("sdh_wrestler_promotions", "wrestler_id IN ({ids})"),
        ("sdh_wrestler_roles", "wrestler_id IN ({ids})"),
        ("sdh_wrestler_alignments", "wrestler_id IN ({ids})"),
        ("sdh_wrestler_images", "wrestler_id IN ({ids})"),
    ],
}

_WRESTLER_CROSSWALK_SOURCES = {"wrestlers", "sdh_wrestlers"}
_TITLE_CROSSWALK_SOURCES = {"titles", "sdh_titles"}

# Out-of-band Postgres tables that FK into warehouse parents but are not managed in
# the local DuckDB schema (e.g. operator-maintained Supabase helpers). Sync must
# stash+clear them before deleting parents, then restore rows whose parents remain.
# Tuple: (external_table, parent_table, fk_column).
_POSTGRES_EXTERNAL_CHILDREN: list[tuple[str, str, str]] = [
    ("promotion_abbr", "promotions", "promotion_id"),
]


def _postgres_relation_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    """Return True when `pg.<table>` is queryable on the attached Postgres catalog."""
    try:
        con.execute(f'SELECT 1 FROM pg."{table}" LIMIT 0')
    except Exception:
        return False
    return True


def _stash_external_children(
    con: duckdb.DuckDBPyConnection,
    *,
    parent_table: str | None = None,
    ids_sql: str | None = None,
) -> list[tuple[str, str, str, str]]:
    """Copy matching external FK-child rows into temp tables and delete them.

    When `parent_table`/`ids_sql` are set, only that parent's changed ids are stashed
    (incremental). When both are unset, every configured external child is stashed
    (full sync). Returns `(stash_name, external_table, parent_table, fk_column)` tuples
    for `_restore_external_children`.
    """
    stashes: list[tuple[str, str, str, str]] = []
    for table, parent, fk_column in _POSTGRES_EXTERNAL_CHILDREN:
        if parent_table is not None and parent != parent_table:
            continue
        if not _postgres_relation_exists(con, table):
            continue
        stash = f"_sync_stash_{table}"
        where = f'WHERE "{fk_column}" IN ({ids_sql})' if ids_sql is not None else ""
        con.execute(f'DROP TABLE IF EXISTS "{stash}"')
        con.execute(f'CREATE TEMP TABLE "{stash}" AS SELECT * FROM pg."{table}" {where}')
        con.execute(f'DELETE FROM pg."{table}" {where}')
        stashes.append((stash, table, parent, fk_column))
        logger.info(
            "Stashed Postgres external child %s (%s rows)",
            table,
            con.execute(f'SELECT count(*) FROM "{stash}"').fetchone()[0],
        )
    return stashes


def _restore_external_children(
    con: duckdb.DuckDBPyConnection,
    stashes: list[tuple[str, str, str, str]],
) -> None:
    """Re-insert stashed external rows for parents that still exist after sync."""
    for stash, table, parent, fk_column in stashes:
        if not _postgres_relation_exists(con, table):
            continue
        con.execute(
            f'INSERT INTO pg."{table}" '
            f'SELECT * FROM "{stash}" '
            f'WHERE "{fk_column}" IN (SELECT id FROM pg."{parent}")'
        )
        logger.info(
            "Restored Postgres external child %s (%s rows)",
            table,
            con.execute(
                f'SELECT count(*) FROM "{stash}" '
                f'WHERE "{fk_column}" IN (SELECT id FROM pg."{parent}")'
            ).fetchone()[0],
        )


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


def _sql_ids(ids: set[str]) -> str:
    return ", ".join("'" + entity_id.replace("'", "''") + "'" for entity_id in sorted(ids))


def _filter_sql(template: str, ids_sql: str, catalog: str = "") -> str:
    return template.format(ids=ids_sql, catalog=catalog)


def _clear_local_children(
    con: duckdb.DuckDBPyConnection, source: str, ids: set[str]
) -> None:
    """Remove list/child rows for entities about to be reloaded.

    Parent rows are upserted by transform.sql. Replacing children wholesale ensures
    corrections that remove/reorder a note, participant, role, etc. do not leave stale
    rows behind under the old sequence key.
    """
    spec = _SOURCE_SYNC_SPECS.get(source)
    if not spec or not ids:
        return
    ids_sql = _sql_ids(ids)
    for table, filter_template in reversed(spec[1:]):
        where = _filter_sql(filter_template, ids_sql)
        con.execute(f"DELETE FROM {table} WHERE {where}")


def _dedupe_jsonl(jsonl_path: Path) -> Path | None:
    """Return a temp copy of `jsonl_path` keeping only the *last* line per entity id,
    or None when the file has no duplicate ids (the common case — no copy needed).

    Scrape output is append-only, so a re-scraped entity appears as a second line for
    the same id. transform.sql flattens the whole file in one pass, and loading both
    occurrences together corrupts child tables whose synthetic ids differ between
    scrapes: an event scraped before *and* after it airs keeps its stale announced-card
    `-side-N` match_sides rows alongside the newer `-winner-0`/`-loser-N` rows, because
    the differing ids never collide for `ON CONFLICT DO NOTHING` to drop. Keeping only
    each id's newest line makes the transform see one consistent snapshot per entity.

    Lines with no parseable id are kept as-is. The caller deletes the temp file.
    """
    line_ids: list[str | None] = []
    last_line_for_id: dict[str, int] = {}
    duplicates = False
    with jsonl_path.open(encoding="utf-8") as file:
        for index, line in enumerate(file):
            entity_id: str | None = None
            if line.strip():
                try:
                    entity_id = str(json.loads(line)["id"])
                except (json.JSONDecodeError, KeyError):
                    entity_id = None
            line_ids.append(entity_id)
            if entity_id is not None:
                if entity_id in last_line_for_id:
                    duplicates = True
                last_line_for_id[entity_id] = index
    if not duplicates:
        return None

    keep = set(last_line_for_id.values())
    with tempfile.NamedTemporaryFile(
        "w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as tmp:
        with jsonl_path.open(encoding="utf-8") as file:
            for index, line in enumerate(file):
                if line_ids[index] is None or index in keep:
                    tmp.write(line)
        return Path(tmp.name)


def load_source(con: duckdb.DuckDBPyConnection, source: str, jsonl_path: Path) -> None:
    """Flatten one source JSONL file into the warehouse. No-op if the file doesn't exist."""
    if not jsonl_path.exists():
        logger.info("Skipping %s: %s not found", source, jsonl_path)
        return

    _clear_local_children(con, source, change_manifest.ids_from_jsonl(jsonl_path))
    deduped_path = _dedupe_jsonl(jsonl_path)
    if deduped_path is not None:
        logger.info("Deduplicated %s: loading only each id's newest line", jsonl_path)
    try:
        escaped_path = str(deduped_path or jsonl_path).replace("'", "''")
        for statement in _TRANSFORM_BLOCKS[source]:
            con.execute(statement.replace("{path}", escaped_path))
    finally:
        if deduped_path is not None:
            deduped_path.unlink(missing_ok=True)


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
    `ON CONFLICT` push-down through DuckDB's postgres attachment. This is retained for
    initial bootstrap and recovery; routine updates use `sync_postgres_incremental`.

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
            external_stashes = _stash_external_children(con)
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
            _restore_external_children(con, external_stashes)
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        return {table: con.execute(f"SELECT count(*) FROM pg.{table}").fetchone()[0] for table in TABLES}
    finally:
        con.execute("DETACH pg")


def sync_postgres_incremental(
    con: duckdb.DuckDBPyConnection,
    postgres_url: str,
    changes: change_manifest.Changes,
) -> dict[str, int]:
    """Replace only changed entity subtrees in Postgres.

    Each source's changed IDs are handled in one transaction: dependent rows are
    deleted child-first and the current DuckDB rows are inserted parent-first. This
    is both faster than a full mirror and more correct than child-table upserts when
    a re-scrape removes or reorders list entries.

    Crosswalk tables are tiny and derived globally, so a wrestler/title source change
    refreshes the corresponding crosswalk in full inside the same transaction.
    """
    pending = {
        source: {str(entity_id) for entity_id in ids}
        for source, ids in changes.items()
        if source in _SOURCE_SYNC_SPECS and ids
    }
    if not pending:
        return {}

    refresh_wrestler_crosswalk = bool(_WRESTLER_CROSSWALK_SOURCES & pending.keys())
    refresh_title_crosswalk = bool(_TITLE_CROSSWALK_SOURCES & pending.keys())

    con.execute("INSTALL postgres; LOAD postgres;")
    escaped_url = postgres_url.replace("'", "''")
    con.execute(f"ATTACH '{escaped_url}' AS pg (TYPE postgres)")
    try:
        synced: dict[str, int] = {}
        con.execute("BEGIN TRANSACTION")
        try:
            # These reference entity roots, so remove them before deleting a changed
            # wrestler/title. They are rebuilt below from the current local result.
            if refresh_wrestler_crosswalk:
                con.execute("DELETE FROM pg.wrestler_crosswalk")
            if refresh_title_crosswalk:
                con.execute("DELETE FROM pg.title_crosswalk")

            external_stashes: list[tuple[str, str, str, str]] = []
            for source in SOURCES:
                ids = pending.get(source)
                if not ids:
                    continue
                ids_sql = _sql_ids(ids)
                spec = _SOURCE_SYNC_SPECS[source]
                parent_table = spec[0][0]
                external_stashes.extend(
                    _stash_external_children(
                        con, parent_table=parent_table, ids_sql=ids_sql
                    )
                )

                for table, filter_template in reversed(spec):
                    where = _filter_sql(filter_template, ids_sql, catalog="pg.")
                    con.execute(f"DELETE FROM pg.{table} WHERE {where}")

                for table, filter_template in spec:
                    where = _filter_sql(filter_template, ids_sql)
                    columns = [
                        desc[0]
                        for desc in con.execute(f"SELECT * FROM {table} LIMIT 0").description
                    ]
                    col_list = ", ".join(f'"{column}"' for column in columns)
                    row_count = con.execute(
                        f"SELECT count(*) FROM {table} WHERE {where}"
                    ).fetchone()[0]
                    con.execute(
                        f"INSERT INTO pg.{table} ({col_list}) "
                        f"SELECT {col_list} FROM {table} WHERE {where}"
                    )
                    synced[table] = synced.get(table, 0) + row_count

            for table, refresh in (
                ("wrestler_crosswalk", refresh_wrestler_crosswalk),
                ("title_crosswalk", refresh_title_crosswalk),
            ):
                if not refresh:
                    continue
                columns = [
                    desc[0] for desc in con.execute(f"SELECT * FROM {table} LIMIT 0").description
                ]
                col_list = ", ".join(f'"{column}"' for column in columns)
                row_count = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                con.execute(
                    f"INSERT INTO pg.{table} ({col_list}) SELECT {col_list} FROM {table}"
                )
                synced[table] = row_count

            _restore_external_children(con, external_stashes)
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        return synced
    finally:
        con.execute("DETACH pg")
