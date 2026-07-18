"""Async orchestrator: drives a spider through a fetcher and writes JSONL output.

Runs concurrently, bounded by `settings.concurrency`: every `start_requests()` URL is
walked (following `next_page_url` for pagination) as its own concurrent chain, and every
item found on a page is enriched (`parse_profile`) and written concurrently too. All of
it shares one fetcher (`BrowserManager` or `HttpClient`, per `spider.fetch_backend`),
so `settings.concurrency` is the real ceiling on simultaneous in-flight fetches
regardless of how many chains/items are queued.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Protocol

from parsel import Selector

from .browser import BrowserManager
from .config import Settings
from .http import HttpClient
from .spiders.base import BaseSpider

logger = logging.getLogger(__name__)


class Fetcher(Protocol):
    async def __aenter__(self) -> "Fetcher": ...

    async def __aexit__(self, *exc_info) -> None: ...

    async def fetch(self, url: str) -> str: ...


def _make_fetcher(spider: BaseSpider, settings: Settings) -> Fetcher:
    if spider.fetch_backend == "http":
        return HttpClient(settings)
    return BrowserManager(settings)


def _load_existing_ids(output_path: Path) -> set[str]:
    """Read `output_path`'s existing JSONL and return the ids already present.

    Any line that fails to parse (e.g. the process was killed mid-write, truncating
    the last line) is dropped and the file rewritten without it, so a resumed run
    doesn't leave a corrupt line behind.
    """
    if not output_path.exists():
        return set()

    raw_lines = [line for line in output_path.read_text(encoding="utf-8").splitlines() if line]
    valid_lines: list[str] = []
    ids: set[str] = set()
    for line in raw_lines:
        try:
            item_id = json.loads(line)["id"]
        except (json.JSONDecodeError, KeyError):
            continue
        valid_lines.append(line)
        ids.add(item_id)

    if len(valid_lines) != len(raw_lines):
        dropped = len(raw_lines) - len(valid_lines)
        output_path.write_text(
            "\n".join(valid_lines) + ("\n" if valid_lines else ""), encoding="utf-8"
        )
        logger.warning("Dropped %d corrupt line(s) from %s while resuming", dropped, output_path)

    return ids


async def run(
    spider: BaseSpider, settings: Settings, limit: int | None = None, resume: bool = False
) -> int:
    """Fetch every start URL for `spider` (and its pagination), parse it, and append
    results to JSONL.

    With `resume=True`, items already present in the output file (matched by `id`) are
    neither re-fetched nor re-written — useful for picking a long run back up after an
    interruption. Without it, the output file is overwritten from scratch as before.

    Returns the number of items written (including, when resuming, those already
    present). With `limit` set and concurrency > 1, the final count may slightly exceed
    `limit`: work already in flight when the limit is reached isn't cancelled, only
    further work is skipped.
    """
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = settings.output_dir / f"{spider.name}.jsonl"

    already_done: set[str] = _load_existing_ids(output_path) if resume else set()
    if already_done:
        logger.info("Resuming %s: %d items already present", output_path, len(already_done))

    semaphore = asyncio.Semaphore(settings.concurrency)
    write_lock = asyncio.Lock()
    written = len(already_done)

    def limit_reached() -> bool:
        return limit is not None and written >= limit

    async with _make_fetcher(spider, settings) as fetcher:
        file_mode = "a" if resume and already_done else "w"
        f = output_path.open(file_mode, encoding="utf-8")
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
                item_id = item.get("id")
                if item_id is not None and item_id in already_done:
                    return
                profile_url = item.get("profile_url")
                if spider.fetch_profile and profile_url:
                    async with semaphore:
                        if limit_reached():
                            return
                        logger.info("Fetching profile %s", profile_url)
                        try:
                            html = await fetcher.fetch(profile_url)
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
                            html = await fetcher.fetch(url)
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
