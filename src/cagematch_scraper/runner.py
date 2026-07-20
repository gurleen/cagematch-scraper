"""Async orchestrator: drives a spider through a fetcher and writes JSONL output.

Runs concurrently, bounded by `settings.concurrency`: every `start_requests()` URL is
walked (following `next_page_url` for pagination) as its own concurrent chain, and every
item found on a page is enriched (`parse_profile`) and written concurrently too. All of
it shares one fetcher (`HybridFetcher`, `BrowserManager`, or `HttpClient`, per
`spider.fetch_backend`),
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
from .hybrid import HybridFetcher
from .spiders.base import BaseSpider

logger = logging.getLogger(__name__)


class Fetcher(Protocol):
    async def __aenter__(self) -> "Fetcher": ...

    async def __aexit__(self, *exc_info) -> None: ...

    async def fetch(self, url: str) -> str: ...


def _make_fetcher(spider: BaseSpider, settings: Settings) -> Fetcher:
    if spider.fetch_backend == "http":
        return HttpClient(settings)
    if spider.fetch_backend == "browser":
        return BrowserManager(settings)
    return HybridFetcher(settings)


def _load_existing_items(output_path: Path) -> dict[str, dict]:
    """Read `output_path`'s existing JSONL and return the newest item per id.

    Any line that fails to parse (e.g. the process was killed mid-write, truncating
    the last line) is dropped and the file rewritten without it, so a resumed run
    doesn't leave a corrupt line behind. When the same id appears more than once,
    the last valid line wins — matching how export dedupes before loading.
    """
    if not output_path.exists():
        return {}

    raw_lines = [line for line in output_path.read_text(encoding="utf-8").splitlines() if line]
    valid_lines: list[str] = []
    items: dict[str, dict] = {}
    for line in raw_lines:
        try:
            item = json.loads(line)
            item_id = item["id"]
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
        valid_lines.append(line)
        items[str(item_id)] = item

    if len(valid_lines) != len(raw_lines):
        dropped = len(raw_lines) - len(valid_lines)
        output_path.write_text(
            "\n".join(valid_lines) + ("\n" if valid_lines else ""), encoding="utf-8"
        )
        logger.warning("Dropped %d corrupt line(s) from %s while resuming", dropped, output_path)

    return items


def _load_existing_ids(output_path: Path) -> set[str]:
    """Ids already present in `output_path` (newest line per id)."""
    return set(_load_existing_items(output_path))


async def run(
    spider: BaseSpider, settings: Settings, limit: int | None = None, resume: bool = False
) -> int:
    """Fetch every start URL for `spider` (and its pagination), parse it, and append
    results to JSONL.

    With `resume=True`, items already present in the output file (matched by `id`) are
    skipped when `spider.should_skip_resume(existing, item)` returns True — the default,
    used to pick a long run back up after an interruption. Spiders may refresh selected
    existing rows (e.g. events scraped before results posted, active title reigns);
    refreshed items are appended so export can pick up the newest line per id. Without
    `--resume`, the output file is overwritten from scratch as before.

    Returns the number of items written (including, when resuming, those already
    present that were skipped). With `limit` set and concurrency > 1, the final count
    may slightly exceed `limit`: work already in flight when the limit is reached isn't
    cancelled, only further work is skipped.
    """
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = settings.output_dir / f"{spider.name}.jsonl"

    existing_items: dict[str, dict] = _load_existing_items(output_path) if resume else {}
    if existing_items:
        logger.info("Resuming %s: %d items already present", output_path, len(existing_items))

    semaphore = asyncio.Semaphore(settings.concurrency)
    write_lock = asyncio.Lock()
    # Claim entity ids for the duration of a run so concurrent list chains cannot
    # schedule two profile fetches for the same id (e.g. co-promoted events).
    claimed_ids: set[str] = set()
    claimed_lock = asyncio.Lock()
    written = len(existing_items)

    def limit_reached() -> bool:
        return limit is not None and written >= limit

    async with _make_fetcher(spider, settings) as fetcher:
        # Share HTML for identical URLs within a run — one network hit per page even
        # if two items/list chains request it. Failed fetches are evicted so a later
        # caller can retry rather than reusing the exception forever.
        fetch_cache: dict[str, asyncio.Task[str]] = {}
        fetch_cache_lock = asyncio.Lock()

        async def fetch_cached(url: str) -> str:
            async with fetch_cache_lock:
                task = fetch_cache.get(url)
                if task is None:
                    task = asyncio.create_task(fetcher.fetch(url))
                    fetch_cache[url] = task
            try:
                return await task
            except Exception:
                async with fetch_cache_lock:
                    if fetch_cache.get(url) is task:
                        del fetch_cache[url]
                raise

        file_mode = "a" if resume and existing_items else "w"
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
                if item_id is not None:
                    item_key = str(item_id)
                    existing = existing_items.get(item_key)
                    if existing is not None and spider.should_skip_resume(existing, item):
                        return
                    async with claimed_lock:
                        if item_key in claimed_ids:
                            return
                        claimed_ids.add(item_key)
                profile_url = item.get("profile_url")
                if spider.fetch_profile and profile_url:
                    async with semaphore:
                        if limit_reached():
                            return
                        logger.info("Fetching profile %s", profile_url)
                        try:
                            html = await fetch_cached(profile_url)
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
                            html = await fetch_cached(url)
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
