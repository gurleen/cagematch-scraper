"""Typer CLI entrypoint: `cagematch scrape <spider>`, `cagematch list-spiders`."""

from __future__ import annotations

import asyncio
import logging

import typer

from .config import Settings
from .runner import run
from .spiders import SPIDERS

app = typer.Typer(help="cagematch.net scraper")


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

    written = asyncio.run(run(spider_cls(), settings, limit=limit))
    typer.echo(f"Wrote {written} items to {settings.output_dir / f'{spider_name}.jsonl'}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
