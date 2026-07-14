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
uv run cagematch scrape promotions --headful       # visible browser, for debugging
uv run cagematch scrape promotions --no-profiles   # skip per-item profile fetch (see below)
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
- `CAGEMATCH_PROXY_SERVER` / `_USERNAME` / `_PASSWORD` / `_BYPASS` — route browser traffic
  through a single upstream proxy. Unset by default; runs direct.
- `CAGEMATCH_PROXY_LIST_FILE` — path to a file of `USERNAME:PASSWORD@HOST:PORT` lines (one
  per proxy), default `proxy-creds.txt`. Ignored if `CAGEMATCH_PROXY_SERVER` is set. Each
  `cagematch scrape` invocation advances to the next distinct proxy in the list.

## Spiders

- `promotions` — extracts id, name, profile URL, location, `active_year_start`/
  `active_year_end` (ints, `active_year_end` is `null` if still active), `rating` (float),
  and `votes` (int).

  It also fetches each promotion's profile page to pull `name_history` — every name the
  promotion has used, with `from_date`/`to_date` (`to_date` is `null` for the current
  name). This is one extra request per item — pass `--no-profiles` to skip it and just get
  the list-page fields.
- `wrestlers`, `matches`, `titles` — stubs; each raises `NotImplementedError` naming its
  planned target URL.

## Tests

```bash
uv run pytest
```

## CI

`.github/workflows/scrape.yml.example` is a non-active template (rename to `.yml` to
enable) showing `uv sync` + `patchright install` + `cagematch scrape`, with proxy env vars
wired to GitHub secrets.
