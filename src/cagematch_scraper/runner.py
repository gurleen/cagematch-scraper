"""Async orchestrator: drives a spider through the browser and writes JSONL output."""

from __future__ import annotations

import asyncio
import json
import logging

from parsel import Selector

from .browser import BrowserManager
from .config import Settings
from .spiders.base import BaseSpider

logger = logging.getLogger(__name__)


async def run(spider: BaseSpider, settings: Settings, limit: int | None = None) -> int:
    """Fetch every start URL for `spider`, parse it, and append results to JSONL.

    Returns the number of items written.
    """
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = settings.output_dir / f"{spider.name}.jsonl"

    semaphore = asyncio.Semaphore(settings.concurrency)
    written = 0

    async with BrowserManager(settings) as browser:

        async def fetch_and_parse(url: str) -> list[dict]:
            async with semaphore:
                logger.info("Fetching %s", url)
                html = await browser.fetch(url)
            selector = Selector(text=html)
            return list(spider.parse(selector, url))

        with output_path.open("w", encoding="utf-8") as f:
            for url in spider.start_requests():
                if limit is not None and written >= limit:
                    break
                items = await fetch_and_parse(url)
                for item in items:
                    if limit is not None and written >= limit:
                        break
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
                    written += 1

    logger.info("Wrote %d items to %s", written, output_path)
    return written
