# cagematch-scraper

A scraper for [cagematch.net](https://www.cagematch.net) — pro-wrestling promotion,
wrestler, match, and title data.

## Setup

```bash
uv sync
uv run patchright install chromium
```

## Usage

```bash
uv run cagematch list-spiders
uv run cagematch scrape promotions --limit 20
uv run cagematch scrape wrestlers --limit 20
uv run cagematch scrape matches --limit 20
uv run cagematch scrape wrestlers --headful       # visible browser, for debugging
uv run cagematch scrape wrestlers --no-profiles   # skip per-item profile fetch (see below)
```

Output is written as JSONL to `data/<spider>.jsonl` (one JSON object per line). Schema is
intentionally minimal/loose for now — retention and normalization are out of scope.

## Configuration

All settings are environment variables with a `CAGEMATCH_` prefix, loadable from a `.env`
file (see `.env.example`). Notably:

- `CAGEMATCH_HEADLESS` — default `true`; CI-safe.
- `CAGEMATCH_USER_DATA_DIR` — set to persist a browser profile/cookies across runs.
- `CAGEMATCH_BLOCK_RESOURCES` — default `true`; skips loading images/media/fonts/CSS to
  cut bandwidth.
- `CAGEMATCH_CONCURRENCY` — default `2`; max concurrent in-flight page fetches (list
  pages and profile pages both draw from this). Raise it if your proxy can handle more
  simultaneous connections — some cap concurrent tunnels, which shows up as
  `ERR_TUNNEL_CONNECTION_FAILED`.
- `CAGEMATCH_REQUEST_DELAY` — default `1.5` seconds; minimum spacing between request
  *start* times, enforced even under concurrency.
- `CAGEMATCH_PROXY_SERVER` / `_USERNAME` / `_PASSWORD` / `_BYPASS` — route browser traffic
  through a single upstream proxy. Unset by default; runs direct.
- `CAGEMATCH_PROXY_LIST_FILE` — path to a file of `USERNAME:PASSWORD@HOST:PORT` lines (one
  per proxy), default `proxy-creds.txt`. Ignored if `CAGEMATCH_PROXY_SERVER` is set. Each
  `cagematch scrape` invocation advances to the next distinct proxy in the list.
- `CAGEMATCH_PROMOTION_IDS` — comma-separated cagematch promotion ids to restrict
  scraping to. Default `1,2287` (WWE, AEW). The `wrestlers` and `matches` spiders use
  this list to find their data (via each promotion's roster/events), so both always need
  at least one id here. Set to an empty string to scrape every promotion (only affects
  the `promotions` spider).
- `CAGEMATCH_MATCHES_SINCE_YEAR` — default `2020`; earliest year (inclusive) the
  `matches` spider fetches events for. It walks every year from this one through the
  current year, for each promotion in `CAGEMATCH_PROMOTION_IDS`.
- `CAGEMATCH_POSTGRES_URL` — Postgres connection string `cagematch export
  sync-postgres` mirrors the warehouse into (see "Exporting to a relational
  warehouse" below). Unset by default; the command errors clearly if it's needed but
  missing.

## Spiders

- `promotions` — extracts id, name, profile URL, location, `active_year_start`/
  `active_year_end` (ints, `active_year_end` is `null` if still active), `rating` (float),
  and `votes` (int).

  It also fetches each promotion's profile page to pull `name_history` — every name the
  promotion has used, with `from_date`/`to_date` (`to_date` is `null` for the current
  name). This is one extra request per item — pass `--no-profiles` to skip it and just get
  the list-page fields.
- `wrestlers` — finds wrestlers via both the current roster and the all-time roster of
  each promotion in `CAGEMATCH_PROMOTION_IDS` (the two lists only partially overlap, so
  using both catches former/departed roster members that the current roster alone
  misses), then fetches each wrestler's profile page for career and personal data:
  birthday, birthplace, gender, height/weight, background, alter egos, nicknames,
  signature moves, wrestling style, trainers, in-ring career span/experience, and a full
  role history (each role's date range(s), since a wrestler can hold the same role in
  separate stints). Pass `--no-profiles` to only get the roster-level fields (name,
  roles, brand, rating, or — for all-time-roster-only entries — a show count).
- `matches` — walks each promotion's event list (year by year, from
  `CAGEMATCH_MATCHES_SINCE_YEAR` onward) and fetches each event's results page. Each
  output line is one **event**, with all of its matches nested under `matches` (not one
  line per match — event fields like date/location aren't repeated per match).

  Event-level fields: name, date, location, rating/votes (from the listing), plus
  `event_type` (e.g. "TV-Show", "Premium Live Event"), `arena`, `broadcast_type`
  ("Live"/"Taped"), `broadcast_date`, `tv_network`, and `commentators` — pulled from the
  event's own page, no extra request since it's already fetched for the match card.

  Each match record has its type, title (if any) and whether it changed hands, duration,
  finish note (for draws/no-contests), a matchguide rating if voted on, elimination/other
  notes, and `winners`/`losers` (or `sides` for a non-decisive result) — each side listing
  its wrestlers, any named team/stable, valets (accompanying but not competing), and
  whether it was the side defending a title coming in.
- `titles` — stub; raises `NotImplementedError` naming its planned target URL.

## Exporting to a relational warehouse

`cagematch export` flattens `data/*.jsonl` into the relational schema in
`src/cagematch_scraper/export/schema.sql` (promotions, wrestlers, events, matches, and
their child/junction tables — no nested lists/structs), stored in a local DuckDB file
and exported as parquet:

```bash
uv run cagematch export backfill              # full rebuild from data/*.jsonl
uv run cagematch export backfill --fresh      # ...deleting the existing warehouse first
uv run cagematch export nightly               # only load newly-appended jsonl lines
```

Output: `data/warehouse.duckdb` (the persistent relational source of truth —
`ON CONFLICT`-based inserts, safe to rerun) and `data/parquet/<table>.parquet` (one
file per table, fully rewritten each run). `nightly` uses `data/.export_cursor.json`
(a per-file line count) purely as a fast path to skip unchanged jsonl files — it never
gates correctness, since every load is deduped against the warehouse regardless.

To mirror the warehouse into a Postgres database (e.g. Supabase):

```bash
CAGEMATCH_POSTGRES_URL="postgresql://..." uv run cagematch export sync-postgres
```

This is a **full-refresh mirror**, not an incremental upsert: it clears every table and
reinserts everything from the local warehouse each run (sub-few-second at this data
scale), using DuckDB's `postgres` extension (`ATTACH ... TYPE postgres`) rather than a
separate Postgres client. `schema.sql` bootstraps the target database automatically
(idempotent `CREATE TABLE IF NOT EXISTS`) — no separate migration step needed. Requires
`CAGEMATCH_POSTGRES_URL`, and `data/warehouse.duckdb` must already exist (run `export
backfill`/`nightly` first).

For Supabase specifically, use the **session pooler** connection string (port `5432` on
the pooler host), not the transaction-mode pooler (port `6543`) — transaction mode
doesn't reliably support the DDL/prepared-statement handshake DuckDB's postgres
extension needs.

## Tests

```bash
uv run pytest
```

## CI

`.github/workflows/nightly.yml` runs nightly (and on-demand via `workflow_dispatch`):
scrape every spider with `--resume`, `cagematch export nightly`, then `cagematch export
sync-postgres`. `data/` (jsonl history, warehouse, cursor) persists across runs via
`actions/cache`, so scraping stays incremental instead of restarting from scratch. Needs
a `SUPABASE_DB_URL` repository secret (the Postgres connection string — session pooler,
see above) and, if you're scraping through a proxy, the commented-out
`CAGEMATCH_PROXY_*` secrets in the workflow filled in.

`.github/workflows/scrape.yml.example` is an older, non-active template (rename to
`.yml` to enable) showing just the scrape step in isolation with proxy env vars wired to
GitHub secrets — useful as a manual-dispatch-only reference, superseded by
`nightly.yml` for the automated pipeline.
