"""Typer CLI entrypoint: `cagematch scrape <spider>`, `cagematch list-spiders`."""

from __future__ import annotations

import asyncio
import logging

import duckdb
import typer

from .config import Settings
from .export import cursor as export_cursor
from .export import warehouse
from .runner import run
from .spiders import SPIDERS

app = typer.Typer(help="cagematch.net scraper")
export_app = typer.Typer(help="Convert scraped JSONL into flat parquet tables")
app.add_typer(export_app, name="export")


@app.command("list-spiders")
def list_spiders() -> None:
    """List available spider names."""
    for name in sorted(SPIDERS):
        typer.echo(name)


@app.command()
def scrape(
    spider_name: str = typer.Argument(..., help="Spider name, e.g. 'promotions'"),
    limit: int | None = typer.Option(None, "--limit", help="Max items to write"),
    headful: bool = typer.Option(False, "--headful", help="Run with a visible browser"),
    no_profiles: bool = typer.Option(
        False, "--no-profiles", help="Skip per-item profile-page fetches (saves bandwidth)"
    ),
    resume: bool = typer.Option(
        False,
        "--resume",
        help="Skip items already present in data/<spider>.jsonl (from an interrupted run) "
        "instead of overwriting the file",
    ),
) -> None:
    """Run a spider and write its output to data/<spider>.jsonl."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    spider_cls = SPIDERS.get(spider_name)
    if spider_cls is None:
        typer.echo(f"Unknown spider: {spider_name!r}. Run 'cagematch list-spiders'.", err=True)
        raise typer.Exit(1)

    settings = Settings()
    if headful:
        settings.headless = False

    spider = spider_cls(settings)
    if no_profiles:
        spider.fetch_profile = False

    written = asyncio.run(run(spider, settings, limit=limit, resume=resume))
    typer.echo(f"Wrote {written} items to {settings.output_dir / f'{spider_name}.jsonl'}")


@export_app.command()
def backfill(
    fresh: bool = typer.Option(
        False, "--fresh", help="Delete the existing warehouse and rebuild it from scratch"
    ),
) -> None:
    """Rebuild the full DuckDB warehouse and parquet exports from data/<spider>.jsonl."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings()

    if fresh and settings.warehouse_path.exists():
        settings.warehouse_path.unlink()

    settings.warehouse_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(settings.warehouse_path))
    try:
        warehouse.ensure_schema(con)
        new_cursor: dict[str, int] = {}
        for source in warehouse.SOURCES:
            jsonl_path = settings.output_dir / f"{source}.jsonl"
            warehouse.load_source(con, source, jsonl_path)
            new_cursor[source] = export_cursor.count_lines(jsonl_path)
        warehouse.export_parquet(con, settings.parquet_dir)
        export_cursor.save_cursor(settings.export_cursor_path, new_cursor)
        counts = warehouse.table_counts(con)
    finally:
        con.close()

    for table, count in counts.items():
        typer.echo(f"{table}: {count} rows")
    typer.echo(f"Wrote parquet files to {settings.parquet_dir}")


@export_app.command()
def nightly() -> None:
    """Load newly-appended JSONL lines since the last export and refresh parquet."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings()

    old_cursor = export_cursor.load_cursor(settings.export_cursor_path)
    settings.warehouse_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(settings.warehouse_path))
    try:
        warehouse.ensure_schema(con)
        new_cursor: dict[str, int] = {}
        for source in warehouse.SOURCES:
            jsonl_path = settings.output_dir / f"{source}.jsonl"
            line_count = export_cursor.count_lines(jsonl_path)
            if line_count == old_cursor.get(source):
                typer.echo(f"{source}: unchanged ({line_count} lines), skipping")
            else:
                warehouse.load_source(con, source, jsonl_path)
            new_cursor[source] = line_count
        warehouse.export_parquet(con, settings.parquet_dir)
        export_cursor.save_cursor(settings.export_cursor_path, new_cursor)
        counts = warehouse.table_counts(con)
    finally:
        con.close()

    for table, count in counts.items():
        typer.echo(f"{table}: {count} rows")
    typer.echo(f"Wrote parquet files to {settings.parquet_dir}")


@export_app.command("sync-postgres")
def sync_postgres() -> None:
    """Mirror the local DuckDB warehouse into Postgres (e.g. Supabase)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings()

    if not settings.postgres_url:
        typer.echo(
            "CAGEMATCH_POSTGRES_URL is not set. Set it to a Postgres connection string "
            "(e.g. your Supabase session-pooler URL) and try again.",
            err=True,
        )
        raise typer.Exit(1)

    if not settings.warehouse_path.exists():
        typer.echo(
            f"{settings.warehouse_path} doesn't exist yet. Run 'cagematch export backfill' "
            "or 'cagematch export nightly' first.",
            err=True,
        )
        raise typer.Exit(1)

    con = duckdb.connect(str(settings.warehouse_path))
    try:
        counts = warehouse.sync_postgres(con, settings.postgres_url)
    finally:
        con.close()

    for table, count in counts.items():
        typer.echo(f"{table}: {count} rows synced")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
