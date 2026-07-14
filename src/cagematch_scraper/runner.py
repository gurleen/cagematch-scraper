"""Async orchestrator: drives a spider through the browser and writes JSONL output.

Runs concurrently, bounded by `settings.concurrency`: every `start_requests()` URL is
walked (following `next_page_url` for pagination) as its own concurrent chain, and every
item found on a page is enriched (`parse_profile`) and written concurrently too. All of
it shares one `BrowserManager`, so `settings.concurrency` is the real ceiling on
simultaneous in-flight fetches regardless of how many chains/items are queued.
"""

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
    """Fetch every start URL for `spider` (and its pagination), parse it, and append
    results to JSONL.

    Returns the number of items written. With `limit` set and concurrency > 1, the
    final count may slightly exceed `limit`: work already in flight when the limit is
    reached isn't cancelled, only further work is skipped.
    """
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = settings.output_dir / f"{spider.name}.jsonl"

    semaphore = asyncio.Semaphore(settings.concurrency)
    write_lock = asyncio.Lock()
    written = 0

    def limit_reached() -> bool:
        return limit is not None and written >= limit

    async with BrowserManager(settings) as browser:
        f = output_path.open("w", encoding="utf-8")
        try:

            async def write_item(item: dict) -> None:
                nonlocal written
                async with write_lock:
                    if limit_reached():
                        return
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
                    written += 1

            async def process_item(item: dict) -> None:
                if limit_reached():
                    return
                profile_url = item.get("profile_url")
                if spider.fetch_profile and profile_url:
                    async with semaphore:
                        if limit_reached():
                            return
                        logger.info("Fetching profile %s", profile_url)
                        try:
                            html = await browser.fetch(profile_url)
                        except Exception:
                            logger.exception(
                                "Giving up on profile %s after retries; skipping", profile_url
                            )
                            return
                    selector = Selector(text=html)
                    item = spider.parse_profile(selector, item)
                await write_item(item)

            async def process_list_url(start_url: str) -> None:
                url: str | None = start_url
                while url is not None and not limit_reached():
                    async with semaphore:
                        if limit_reached():
                            return
                        logger.info("Fetching %s", url)
                        try:
                            html = await browser.fetch(url)
                        except Exception:
                            logger.exception(
                                "Giving up on list page %s after retries; skipping", url
                            )
                            return
                    selector = Selector(text=html)
                    items = list(spider.parse(selector, url))
                    await asyncio.gather(*(process_item(item) for item in items))
                    url = spider.next_page_url(selector, url)

            await asyncio.gather(*(process_list_url(u) for u in spider.start_requests()))
        finally:
            f.close()

    logger.info("Wrote %d items to %s", written, output_path)
    return written
