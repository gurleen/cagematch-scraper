# cagematch-scraper

A scraper for [cagematch.net](https://www.cagematch.net) — pro-wrestling promotion,
wrestler, match, and title data.

## Why a browser, not requests/httpx

cagematch.net sits behind Sucuri CloudProxy with a JavaScript cookie challenge. A plain
HTTP request gets a `307` redirect to an obfuscated interstitial — no data. This project
uses [patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright) (an anti-detection,
Chromium-only Playwright fork) to run a real browser, solve the challenge, and capture the
resulting cookies. Once past the challenge, pages are plain server-rendered HTML, parsed
with [parsel](https://github.com/scrapy/parsel) (Scrapy's selector library).

We deliberately skip full Scrapy — its engine doesn't fit patchright's persistent-context
stealth model well, and the only Scrapy+patchright bridge is an unmaintained fork. Instead
this is a small custom async project that borrows Scrapy's best ideas (parsel selectors,
a spider/item structure) without the engine.

## Setup

```bash
uv sync
uv run patchright install chromium
```

## Usage

```bash
uv run cagematch list-spiders
uv run cagematch scrape promotions --limit 20
uv run cagematch scrape promotions --headful   # visible browser, for debugging
```

Output is written as JSONL to `data/<spider>.jsonl` (one JSON object per line). Schema is
intentionally minimal/loose for now — retention and normalization are out of scope.

## Configuration

All settings are environment variables with a `CAGEMATCH_` prefix, loadable from a `.env`
file (see `.env.example`). Notably:

- `CAGEMATCH_HEADLESS` — default `true`; CI-safe.
- `CAGEMATCH_CHANNEL` — `chromium` by default; `chrome` is more stealth-robust if Sucuri
  ever hardens its challenge (pair with `--headful` + xvfb, or a real Chrome install).
- `CAGEMATCH_USER_DATA_DIR` — set to persist a browser profile/cookies across runs.
- `CAGEMATCH_PROXY_SERVER` / `_USERNAME` / `_PASSWORD` / `_BYPASS` — route browser traffic
  through an upstream proxy (e.g. residential/rotating). Unset by default; runs direct.

## Spiders

- `promotions` — **working**, but its exact section id/selectors are best-effort (see
  "Known limitations" below) and should be sanity-checked against the live site before
  relying on the output.
- `wrestlers`, `matches`, `titles` — stubs; each raises `NotImplementedError` naming its
  planned target URL.

## Tests

```bash
uv run pytest
```

`tests/test_promotions.py` parses a local HTML fixture — no network required.

## Known limitations

The `promotions` spider's section id (`?id=8`) and CSS/XPath selectors are based on
cagematch.net's well-known URL and table markup conventions, but **were not confirmed
against a live page** during initial development: the sandboxed environment used to build
this project could not get headless Chromium's TLS handshake through its mandatory egress
proxy (`net::ERR_CONNECTION_RESET` at the TLS layer, independent of proxy config — `curl`
and raw Python TLS clients worked fine through the same proxy, so this is a Chromium/proxy
compatibility issue specific to that sandbox, not a limitation of this codebase). The
offline test fixture (`tests/fixtures/promotions_list.html`) is therefore synthetic, not a
captured real page.

Before relying on `promotions` output: run `uv run cagematch scrape promotions --limit 3
--headful` somewhere with normal network access (locally, or via the CI template below),
compare the output against the live site, and adjust `SECTION_ID` / selectors in
`src/cagematch_scraper/spiders/promotions.py` if needed. Once confirmed, replace the
fixture with a real saved page.

## CI

`.github/workflows/scrape.yml.example` is a non-active template (rename to `.yml` to
enable) showing `uv sync` + `patchright install` + `cagematch scrape`, with proxy env vars
wired to GitHub secrets.
