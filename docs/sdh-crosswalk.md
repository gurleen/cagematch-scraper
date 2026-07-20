# Pulling SDH data for a Cagematch wrestler

The warehouse holds two scrape sources side by side: **Cagematch** (`wrestlers`,
`titles`, `matches`, ...) and **The Smackdown Hotel** (`sdh_wrestlers`, `sdh_titles`,
...). They use different ID namespaces — Cagematch IDs are numeric strings (`"11207"`),
SDH IDs are URL slugs (`"apollo-crews"`). Never compare them directly; always go
through the crosswalk tables.

## The join

```sql
SELECT s.*
FROM wrestlers w
JOIN wrestler_crosswalk x ON x.cagematch_id = w.id
JOIN sdh_wrestlers s      ON s.id = x.sdh_id
WHERE w.id = :cagematch_wrestler_id;
```

If the join returns no row, that wrestler has no known SDH counterpart (~20% of
Cagematch wrestlers, mostly indie/developmental talent SDH doesn't track). Treat
absence as "no SDH data", not an error.

Titles work identically via `title_crosswalk` (`cagematch_id` ↔ `titles.id`,
`sdh_id` ↔ `sdh_titles.id`).

## Trusting a match

Each crosswalk row carries `match_method` and `confidence`. The mapping is 1:1 in both
directions (mutually-best pairs only).

| `match_method` | `confidence` | Meaning |
|---|---|---|
| `name_and_birthday` | 1.0 | Primary names and birthdays both agree |
| `alias_and_birthday` | 0.9 | Matched via ring-name/alter-ego, birthday agrees |
| `name` | 0.8 | Primary names agree; a birthday is missing on one side |
| `name_birthday_mismatch` | 0.7 | Primary names agree but birthdays disagree (the sources often differ by a day or a year — usually still the same person) |
| `alias` | 0.6 | Ring-name overlap only, no birthday on either side |

Filter on `confidence >= 0.7` when you want high precision; include 0.6 for recall.

## What SDH adds per wrestler

Once you have the `sdh_id`:

| Table | Contents |
|---|---|
| `sdh_wrestlers` | `real_name`, `birthday`, `nationality`, `birthplace`, `billed_from`, `height_cm`, `weight_kg`, `image_url` (current full-body render), `profile_url` |
| `sdh_wrestler_name_history` | Ring names / gimmicks with date ranges |
| `sdh_wrestler_promotions` | Promotion **and brand** history with date ranges (Cagematch lacks brand data) |
| `sdh_wrestler_alignments` | Face/heel turns with reasons and date ranges |
| `sdh_wrestler_attributes` | `attr_type IN ('nickname', 'finisher')` |
| `sdh_wrestler_roles` | Roles (Wrestler, Manager, ...) with date ranges |
| `sdh_wrestler_images` | Dated headshot gallery (`label` like `'Apr 2026'`, `image_url`) |
| `sdh_wrestler_career_awards` | Career awards (e.g. Triple Crown) with optional badge `image_url` and page `url` |
| `sdh_wrestler_hall_of_fames` | Hall of Fame inductions (`name`, `category`, `year`, optional icon `image_url`) |
| `sdh_wrestler_title_wins` | Per-wrestler title/tournament summary by promotion (`times`, partners/years in `details`, `source` `auto`/`manual`); not a substitute for reign history |
| `sdh_wrestler_accomplishments` | Other Accomplishments free-text list (Slammys, PWI, WON, tournaments, …) |
| `sdh_title_reigns` (+ `sdh_title_reign_champions`) | Reigns with `event_name`/`event_url`, `location`, free-text `notes` (cash-ins, vacancies, injuries), and `is_vacant` rows — richer context than Cagematch reigns |

All child tables key on `wrestler_id = sdh_id` with a `seq` column preserving page order.

Example — everything SDH knows about a wrestler's reigns with a given title:

```sql
SELECT tr.reign_number, tr.from_date, tr.to_date, tr.duration_days,
       tr.event_name, tr.notes
FROM wrestler_crosswalk wx
JOIN sdh_title_reign_champions rc ON rc.wrestler_id = wx.sdh_id
JOIN sdh_title_reigns tr          ON tr.id = rc.title_reign_id
JOIN title_crosswalk tx           ON tx.sdh_id = tr.title_id
WHERE wx.cagematch_id = :cagematch_wrestler_id
  AND tx.cagematch_id = :cagematch_title_id
ORDER BY tr.seq;
```

## Gotchas

- **Dates are raw display strings**, not typed dates: Cagematch uses `DD.MM.YYYY`, SDH
  uses `Month DD, YYYY`. Parse before comparing (`try_strptime(x, '%d.%m.%Y')` /
  `try_strptime(x, '%B %d, %Y')` in DuckDB). `to_date IS NULL` means ongoing/present.
- **SDH display names can be compound** (`'Apollo Crews / Uhaa Nation'`) when a wrestler
  had multiple identities. Prefer `sdh_wrestler_name_history` for clean individual names.
- **Vacancies** in `sdh_title_reigns` are rows with `is_vacant = true` and no champion
  rows — don't treat them as wrestlers.
- The convenience views `v_wrestlers_matched` / `v_titles_matched` (pre-joined
  side-by-side comparisons) exist **only in the local DuckDB warehouse**
  (`data/warehouse.duckdb`), not in Supabase. In Supabase, join through the crosswalk
  tables as shown above.
- Everything above is in both the local warehouse and Supabase; local also has parquet
  mirrors under `data/parquet/`.
- Crosswalks are derived tables, rebuilt by `cagematch export match` (also runs inside
  `export backfill`/`nightly`). Re-run after any re-scrape.
