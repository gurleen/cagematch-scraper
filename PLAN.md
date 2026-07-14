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

### Outstanding — straightforward follow-up work, not blocked
- Implement `matches` and `titles` spiders for real (currently stubs). Each needs its own
  live selector pass, same as promotions/wrestlers.
- Firm up the `PromotionItem`/`WrestlerItem`/other item schemas once retention needs are
  clearer (currently deliberately loose per "sort schema later").
- Activate `.github/workflows/scrape.yml.example` (rename to `.yml`) once the team is ready
  for scheduled/CI runs — currently intentionally left inactive.
