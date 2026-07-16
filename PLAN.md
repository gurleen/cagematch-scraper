# Plan: Scaffold a cagematch.net web scraper (uv + patchright + parsel)

## Context

The repo `gurleen/cagematch-scraper` is currently empty (fresh git repo, no commits) on
branch `claude/python-cagematch-scraper-caghr3`. The goal is to stand up a Python web-scraping
project, managed by **uv**, that pulls data about pro-wrestling promotions, wrestlers, matches,
and titles from **cagematch.net**. Data retention/schema is explicitly out of scope for now —
the goal is simply *getting the data out*. It will likely be driven from GitHub Actions later,
so the entrypoint must be CI-friendly. Room must be left for proxy configuration.

### Key finding that drives the design
cagematch.net sits behind **Sucuri CloudProxy** with a JavaScript cookie challenge. A plain
HTTP request (verified via `curl`) returns `HTTP 307` and an obfuscated `<script>eval(...)>`
redirect page — no data. Only a *real browser* that executes the JS and stores the challenge
cookie can reach the content. This is exactly why **patchright** (the anti-detection,
Chromium-only, drop-in Playwright fork the user calls "patchwright") is the right fetch layer.
Once the challenge is solved, cagematch pages are plain **server-rendered HTML**, so a fast
selector library (parsel) is ideal for parsing.

### Chosen approach (confirmed with user)
- **Framework:** Lightweight custom async project — **patchright** for fetching, **parsel**
  (Scrapy's own lxml selector library) for parsing, **pydantic-settings** for config, a
  spider-per-entity layout, and a **typer** CLI. We deliberately skip full Scrapy: its engine
  is awkward with patchright's persistent-context stealth model, and the only Scrapy+patchright
  bridge (`scrapy-patchright`) is an unmaintained fork (0 stars, not on PyPI). We borrow
  Scrapy's best parts (parsel selectors, spider/item structure) without the engine.
- **Initial scope:** One entity (**promotions**) working end-to-end to prove the
  Sucuri-challenge → fetch → parse → output pipeline, plus clearly-marked stub spiders for
  wrestlers, matches, and titles.

## Tooling verified available
- `uv 0.8.17`, Python 3.11.15
- `patchright 1.61.2` on PyPI; async API `from patchright.async_api import async_playwright`
- `parsel 1.11.0` (maintained by the Scrapy org; CSS + XPath)
- Chromium pre-installed at `/opt/pw-browsers` (`PLAYWRIGHT_BROWSERS_PATH`); patchright still
  needs its own browser fetched via `patchright install chromium`.

## Project layout (src layout, uv-managed)

```
pyproject.toml                     # uv init --package; deps + [project.scripts] cagematch entrypoint
.python-version                    # 3.11
README.md                          # setup, usage, CI notes, proxy config
.gitignore                         # .venv, __pycache__, data/, .env, user-data-dir/
.env.example                       # documents proxy + settings env vars
src/cagematch_scraper/
  __init__.py
  config.py                        # pydantic-settings Settings (proxy, headless, channel, concurrency, base_url, output_dir, delay, user_data_dir)
  browser.py                       # BrowserManager: async patchright, stealth launch, proxy wiring, Sucuri-aware fetch, cookie/context reuse
  runner.py                        # async orchestrator: concurrency semaphore, drives a spider, writes JSONL
  items.py                         # loose dataclasses/TypedDicts per entity (schema intentionally minimal)
  cli.py                           # typer app: `cagematch scrape <spider> [--limit N] [--headful]`, `cagematch list-spiders`
  spiders/
    __init__.py                    # SPIDERS registry {name: class}
    base.py                        # BaseSpider ABC: name, start_requests(), parse(selector, url) -> Iterable[dict]
    promotions.py                  # WORKING end-to-end (list + profile)
    wrestlers.py                   # stub (raises NotImplementedError with TODO + target URL scheme)
    matches.py                     # stub
    titles.py                      # stub
tests/
  conftest.py
  fixtures/promotions_list.html    # saved real HTML (captured once via patchright) for offline parse tests
  test_promotions.py               # parses fixture, asserts fields — no network
.github/workflows/scrape.yml.example   # NON-active template (user said don't set up CI yet); shows uv sync + patchright install + run
```

## Component design

### config.py — `Settings(BaseSettings)`
- env prefix `CAGEMATCH_`, loads from `.env`.
- Fields: `base_url` (`https://www.cagematch.net`), `headless: bool = True`,
  `channel: str | None = "chromium"` (allow `"chrome"` for max stealth),
  `concurrency: int = 2`, `request_delay: float = 1.5`, `nav_timeout_ms: int = 30000`,
  `output_dir: Path = Path("data")`, `user_data_dir: Path | None = None`.
- Nested proxy: `proxy_server`, `proxy_username`, `proxy_password`, `proxy_bypass` →
  helper `.proxy_dict()` returning the patchright `proxy=` dict (or `None`). This is the
  "room for proxies" the user asked for — set via env / `.env`, no code changes.

### browser.py — `BrowserManager`
- Async context manager wrapping `async_playwright()`.
- Launches per patchright best-practice, made CI-safe: `channel` and `headless` from config;
  when `user_data_dir` is set, use `launch_persistent_context` (recommended stealth), else
  `launch` + `new_context`. Do **not** inject a custom UA/headers (patchright guidance).
  Wire `proxy=settings.proxy_dict()` at context level.
- `async fetch(url) -> str`: `goto(url, wait_until="domcontentloaded")`; detect the Sucuri
  interstitial (title "You are being redirected" / known script marker) and wait/reload until
  the real DOM is present, so the challenge cookie is captured and reused for subsequent
  navigations in the same context. Returns `page.content()`. Polite `request_delay` between navs.

### spiders/base.py — `BaseSpider`
- ABC with `name`, `start_requests() -> Iterable[str]`, and
  `parse(selector: parsel.Selector, url: str) -> Iterable[dict]`. Optional `follow()` for
  profile pages. Keeps the Scrapy mental model without the engine.

### spiders/promotions.py (the working slice)
- Targets the promotions section. cagematch uses `?id=<section>&nr=<record>` with `&page=<n>`
  pagination; the **exact section id + profile id and the DOM selectors will be confirmed live
  during implementation** by driving patchright against the site (curl can't pass Sucuri, so I
  won't hard-code guessed constants here). Extracts a first-pass field set (name, id/nr,
  profile URL, country, active years, etc.) into loose dicts — schema stays minimal per the
  user's "sort schema later."

### runner.py + cli.py
- `runner.run(spider, limit)`: opens one `BrowserManager`, iterates `start_requests()`,
  fetches, wraps HTML in `parsel.Selector`, calls `parse()`, writes results to
  `data/<spider>.jsonl` (one JSON object per line). Semaphore-bounded concurrency.
- `cli.py` (typer, wired as `[project.scripts] cagematch = "cagematch_scraper.cli:app"`):
  `cagematch scrape promotions --limit 3`, `cagematch list-spiders`, `--headful` flag.
  Stdout logging so GitHub Actions logs are useful.

## Dependencies (via `uv add`)
- Runtime: `patchright`, `parsel`, `pydantic`, `pydantic-settings`, `typer`
- Dev (`uv add --dev`): `pytest`

## Build steps
1. `uv init --package --name cagematch-scraper` (src layout, packaged); set `.python-version` to 3.11.
2. `uv add patchright parsel pydantic pydantic-settings typer` and `uv add --dev pytest`.
3. `uv run patchright install chromium` (fetch patchright's browser).
4. Write `config.py`, `browser.py`, `items.py`, `spiders/*`, `runner.py`, `cli.py`.
5. Drive patchright against the live promotions section to confirm the section id + selectors,
   finish the promotions parser, and **save one real HTML page** to `tests/fixtures/` for the
   offline test.
6. Write `test_promotions.py` (parses the fixture, no network).
7. `.gitignore`, `.env.example`, `README.md`, and the non-active
   `.github/workflows/scrape.yml.example` template.
8. Commit and push to the designated branch with `-u origin`.

## Verification (end-to-end)
- `uv sync` — installs cleanly.
- `uv run cagematch --help` and `uv run cagematch list-spiders` — CLI wired.
- `uv run cagematch scrape promotions --limit 3` — **produces `data/promotions.jsonl` with real
  records**, proving the Sucuri challenge is solved and parsing works against the live site.
- `uv run pytest` — offline fixture parse test passes (green without network).
- Stub spiders raise a clear `NotImplementedError` naming their target URL scheme.
- Confirm proxy wiring is inert-but-present: with no proxy env set it runs directly; setting
  `CAGEMATCH_PROXY_SERVER` routes through it (documented in `.env.example`/README).

## Notes / deferred
- GitHub Actions is intentionally **not activated** (only an `.example` template) — user said
  "we don't need to set these up yet," but the typer CLI + `uv run` entrypoint make it a
  one-file addition later.
- Data retention/schema deliberately minimal (JSONL of loose dicts) per user direction.
- Headless is default `True` for CI; README notes `--headful` + `channel="chrome"` (with xvfb)
  is the most stealth-robust mode if Sucuri ever hardens.

## Status: what's done vs. outstanding

The full scaffold (Build steps 1–8) has been implemented and pushed. Everything below is
what's left before the project matches the plan's original intent end-to-end.

### Done
- Project scaffold, `config.py`, `browser.py`, `items.py`, `runner.py`, `cli.py`,
  `spiders/base.py`, `spiders/__init__.py` registry — all as designed above.
- `promotions` spider wired end-to-end (fetch → parse → JSONL) and verified structurally
  against a local HTTP server (BrowserManager navigation/challenge-detection loop, runner
  concurrency + JSONL writing, CLI commands all confirmed working).
- `wrestlers` / `matches` / `titles` stubs raise `NotImplementedError` naming a target URL.
- Tests, `.gitignore`, `.env.example`, `README.md`, non-active CI workflow template.
- Proxy wiring present in `config.py`/`browser.py` (inert by default, `.env`-driven).

### Done (previously blocked, now resolved in a real-network environment)
A later session had normal network access (no egress-proxy TLS issue) and completed the
work the first pass couldn't:
- **Live-confirmed the promotions URL/selectors.** The plain `?id=8&page=4` URL used
  originally was actually cagematch's rating-sorted "Overview" (~50 rows, no real
  pagination). The correct browsable list is `?id=8&view=promotions`, paginated via
  `s=<row offset>` in steps of 100 (not a 1-based page number). Row cells are
  `[rank, logo, name, location, active_years, rating, votes]` — `promotions.py` and
  `items.py` (`PromotionItem`) were updated to match; the old guessed `location`/`status`
  fields were wrong (off-by-one into the logo/name cells) and are now
  `location`/`active_years`/`rating`/`votes`.
- **Replaced the synthetic test fixture** with a real captured page
  (`tests/fixtures/promotions_list.html`, 100 rows); `test_promotions.py` asserts against
  real values (WWE/AEW/NJPW rows) and passes offline.
- **Ran the live verification**: `uv run cagematch scrape promotions --limit 5` produced
  real records through the configured proxy; `uv run pytest` is green.
- **Added bandwidth-saving resource blocking**: `BrowserManager` now aborts
  image/media/font/stylesheet requests by default (`CAGEMATCH_BLOCK_RESOURCES=true`),
  cutting a promotions-list page fetch to ~85KB — important given the proxy's limited
  (~4GB) bandwidth cap.
- **Added proxy-list cycling**: `CAGEMATCH_PROXY_LIST_FILE` (default `proxy-creds.txt`,
  gitignored) holds `USERNAME:PASSWORD@HOST:PORT` lines; `Settings.load_proxy_pool()` +
  `ProxyPool` dedupe and cycle them, with the cursor persisted to
  `<output_dir>/.proxy_cursor` so successive CLI runs advance through the pool. Proxy
  selection happens once per browser context (per run), not per-request, so the Sucuri
  challenge cookie stays valid for the whole run.

### Done — promotion filter + wrestlers spider
- **Added `CAGEMATCH_PROMOTION_IDS`** (`Settings.promotion_ids` / `promotion_id_list()`),
  comma-separated cagematch promotion ids, default `1,2287` (WWE, AEW). `PromotionsSpider`
  filters its output by it; spiders are now constructed as `spider_cls(settings)`
  uniformly (`cli.py`, all spider `__init__`s).
- **Implemented `wrestlers` spider for real** (was a stub). There's no bare "browse all
  wrestlers" list on cagematch, so it discovers wrestlers via each configured promotion's
  roster page (`?id=8&nr=<id>&page=15` — a single un-paginated table; the brand column
  only exists for promotions that split into brands, so rating/votes are read from the
  last two cells rather than a fixed index) and dedupes wrestlers already seen across
  promotions. Each wrestler's profile page (`?id=2&nr=<id>`) is then fetched via
  `fetch_profile`/`parse_profile` for career/personal data — birthday, birthplace,
  gender, height/weight, background, alter egos, nicknames, signature moves, wrestling
  style, trainers, career span/experience, and a full role history (`roles`, supporting
  a role with multiple non-contiguous date ranges, e.g. Lilian Garcia's three separate
  "Ring Announcer" stints).
- Found and fixed a parsel gotcha along the way: `Selector(text=fragment, type="html")`
  still auto-detects JSON when a `<br>`-split text fragment happens to parse as valid
  JSON (e.g. a quoted nickname like `"God's Favourite Champion"`), overriding the
  explicit `type`. Both `promotions.py`'s `_parse_name_history` and `wrestlers.py`'s
  `_br_list` now strip tags with regex + `html.unescape` instead of nesting a `Selector`.
- New fixtures (`tests/fixtures/wwe_roster.html`, `aew_roster.html`,
  `wrestler_profile_rusev.html`, `wrestler_profile_multirange.html`) and
  `tests/test_wrestlers.py`; live-verified with `uv run cagematch scrape wrestlers
  --limit 3` through the configured proxy.

### Done — wrestlers spider now also covers the All-Time Roster
The initial `wrestlers` spider only fetched a promotion's "Roster" tab (`page=15`).
Checking live against WWE, that tab is broader than "currently active" (it already
includes retired legends like Ted DiBiase, John Cena, The Rock) but isn't a full
history: cagematch's separate "All-Time Roster" tab (`page=16`, appearance-count-based
rather than affiliation-based) turned out to be largely non-overlapping — missing some
top legends Roster has, but covering ~58 individual wrestlers (departed/lower-card
names) that Roster doesn't.
- `start_requests` now yields both tabs per configured promotion (Roster first, so its
  richer fields win on dedup for wrestlers present in both).
- `parse` branches on `page=15` vs `page=16`: the all-time table only has
  `[#, gimmick, # shows]`, so those entries get a new `career_shows` field instead of
  `active_roles`/`current_brand`/`roster_rating`/`roster_votes`.
- All-Time Roster also lists tag-team/stable entries (Bloodline, Alpha Academy, etc.),
  but those link to `id=28`/`id=29` (teams/stables), not `id=2` (wrestlers), so the
  existing `id=2&nr=` link match already excludes them with no extra filtering needed.
- New fixture `tests/fixtures/wwe_alltime_roster.html` and three new tests in
  `test_wrestlers.py` covering the all-time parse path and the cross-page dedup.

### Done — matches spider implemented, plus a real concurrency fix
- **Found and fixed the runner never actually running concurrently.** `runner.py` built
  an `asyncio.Semaphore(settings.concurrency)` but every fetch was `await`ed one at a
  time in a plain `for` loop — nothing was ever scheduled concurrently, so
  `concurrency=2` behaved identically to `concurrency=1`. Rewrote `run()` to walk each
  `start_requests()` URL (and its `next_page_url` chain) as its own coroutine, gathered
  concurrently via `asyncio.gather`, with per-item `parse_profile` enrichment also
  gathered concurrently — all still bounded by the same semaphore. Concurrent writes to
  the output file are serialized with a lock.
- Also fixed a related race in `BrowserManager._throttle`: it read-then-slept on
  `_last_request_at` without a lock, so concurrent callers could all pass the throttle
  check at once. Now guarded by an `asyncio.Lock` so request *start* times are correctly
  spaced even under concurrency, while the actual `page.goto`/`page.content` work still
  happens outside the lock (so network wait overlaps).
- **Concurrency=4 hit `ERR_TUNNEL_CONNECTION_FAILED`** against the configured proxy in
  testing (concurrency=2 was stable) — some proxies cap concurrent tunnel connections.
  Default `concurrency` is `2` (up from a `1`-in-practice baseline, still a real
  speedup), documented as raisable if the proxy supports it.
  Added retry-with-backoff (3 attempts) inside `BrowserManager.fetch` for transient
  navigation errors, and error isolation in `runner.py` (a page that still fails after
  retries is logged and skipped, not fatal to the whole run) — needed once runs are
  long enough (thousands of requests) that occasional proxy flakiness is expected.
- Added `BaseSpider.next_page_url(selector, url) -> str | None`, called by the runner
  after every list-page `parse`, for spiders whose page count isn't known upfront (the
  matches spider doesn't know how many event-listing pages a given promotion/year has
  until it sees a partial page).
- **Implemented the `matches` spider.** A promotion's event list lives at
  `?id=8&nr=<id>&page=4&vYear=<year>`, paginated via `s=<offset>` in steps of 100 — same
  convention as promotions/wrestlers listings. An event's own page (`?id=1&nr=<id>`, no
  `page=` — cagematch's nav calls this tab "Results") has the full match card with
  results, not just the lineup, in `<div class="Match">` blocks: match type (+ title
  link if applicable), `<span class="MatchTitleChange">` marking title changes, "(c)"
  marking the pre-match champion, "(w/...)" marking valets (not competitors), a
  matchguide rating, and elimination/misc notes. Non-decisive finishes (draws,
  no-contests — confirmed live, e.g. "Liv Morgan vs. Sonya Deville - Double Count Out")
  use "vs." instead of "defeats", so they get `sides` instead of `winners`/`losers`.
  Because the event page already has full results, there's no reason to fetch anything
  beyond it — one line per **event** (not per match) with a nested `matches` array,
  since that avoids repeating event fields on every match and fits the existing
  `fetch_profile`/`parse_profile` one-extra-request-per-item shape.
- Extracted `spiders/htmlutils.py` (`strip_tags`, `br_list`) since three spiders now
  needed the same regex-tag-stripping trick (see `promotions.py`'s
  `_parse_name_history` / `wrestlers.py`'s old `_br_list` for why a nested `Selector`
  doesn't work here); `promotions.py` and `wrestlers.py` now import it instead of each
  keeping their own copy.
- New fixtures (`wwe_events_2020.html`, `wwe_event_results.html`,
  `wwe_mania36_results.html` — title changes + 5-way elimination notes,
  `wwe_dco_event_results.html` — no-decision result) and `tests/test_matches.py`;
  live-verified with `uv run cagematch scrape matches --limit 5` through the proxy.

### Done — matches spider now also captures event-level info, not just the card
The full 2020-onward live run (4,008 events, 22,162 matches, both promotions) got
interrupted by a machine crash partway through; `--resume` picked it back up cleanly
using the already-merged resume support, with zero corrupt lines. Afterward, asked
whether event data (not just match data) was captured — it was (name/date/location/
rating/votes), but not everything visible on the event's own page. Added the rest,
still from the same page fetch (no extra request):
- `event_type` (e.g. "TV-Show", "Premium Live Event"), `arena`, `broadcast_type`
  ("Live"/"Taped"), `broadcast_date`, `tv_network`, `commentators` (id+name, same
  `MatchParticipant` shape as match participants) — all pulled from the event page's
  `InformationBoxTitle`/`Contents` pairs, the same layout promotions/wrestlers profile
  pages use.
- Extracted `text_of`/`info_boxes` out of `wrestlers.py` into `htmlutils.py` alongside
  `strip_tags`/`br_list`, since `matches.py` needed the same InformationBox-pair
  parsing pattern; `wrestlers.py` now imports the shared versions instead of keeping
  its own copy.
- New tests in `test_matches.py` covering the event-info fields (including a case
  missing `Arena:` — cagematch omits it for some events, e.g. NXT UK tapings) and
  live-verified with a small `--limit 3` run (not a full backfill — deliberately held
  off per instruction) confirming the fields appear correctly, including the missing
  field case.

### Outstanding — straightforward follow-up work, not blocked
- Full backfill of `data/matches.jsonl` with the new event-info fields hasn't been run
  yet (the 4,008 events already scraped don't have them) — needs `uv run cagematch
  scrape matches --resume` re-run, except `--resume` as currently written skips ids
  already present rather than re-enriching them, so this needs either a fresh run or a
  resume-mode tweak that re-fetches existing ids to backfill new fields.
- Implement `titles` spider for real (currently a stub).
- Firm up the `PromotionItem`/`WrestlerItem`/`MatchItem` schemas once retention needs
  are clearer (currently deliberately loose per "sort schema later").
- Activate `.github/workflows/scrape.yml.example` (rename to `.yml`) once the team is ready
  for scheduled/CI runs — currently intentionally left inactive.
