-- match.sql — derive the Cagematch <-> SDH crosswalk tables from the loaded warehouse.
-- Run by warehouse.build_crosswalks() after all sources are loaded. Idempotent: each
-- crosswalk is fully rebuilt (DELETE + INSERT) from the current tables. Pure DuckDB
-- (strip_accents / regexp_replace / try_strptime), no source files involved.

-- Normalize a display name to a comparable key: lowercase, accent-stripped, with every
-- run of non-alphanumerics collapsed to a single space and trimmed.
CREATE OR REPLACE MACRO norm_name(x) AS
    trim(regexp_replace(strip_accents(lower(x)), '[^a-z0-9]+', ' ', 'g'));

-- =============================== WRESTLERS ===============================
-- Candidate name keys from both sides, including aliases (Cagematch alter egos) and
-- SDH ring-name history, so e.g. "Hangman Page" <-> "Adam Page" can still link.

DELETE FROM wrestler_crosswalk;

INSERT INTO wrestler_crosswalk (cagematch_id, sdh_id, match_method, confidence)
WITH cm_names AS (
    SELECT id AS cm_id, norm_name(name) AS nkey, TRUE AS is_primary
    FROM wrestlers WHERE name IS NOT NULL
    UNION
    SELECT wrestler_id, norm_name(value), FALSE
    FROM wrestler_attributes WHERE attr_type = 'alter_ego' AND value IS NOT NULL
),
sdh_names AS (
    SELECT id AS sdh_id, norm_name(name) AS nkey, TRUE AS is_primary
    FROM sdh_wrestlers WHERE name IS NOT NULL
    UNION
    -- SDH renders multi-identity wrestlers as compound display names
    -- ('Apollo Crews / Uhaa Nation'); each part is a primary identity in its own right.
    SELECT id, norm_name(part.p), TRUE
    FROM sdh_wrestlers, UNNEST(string_split(name, '/')) AS part(p)
    WHERE name LIKE '%/%' AND trim(part.p) <> ''
    UNION
    SELECT wrestler_id, norm_name(name), FALSE
    FROM sdh_wrestler_name_history WHERE name IS NOT NULL
),
cm_bday AS (
    SELECT id AS cm_id, try_strptime(birthday, '%d.%m.%Y')::DATE AS bday FROM wrestlers
),
sdh_bday AS (
    SELECT id AS sdh_id, try_strptime(birthday, '%B %d, %Y')::DATE AS bday FROM sdh_wrestlers
),
name_pairs AS (
    SELECT c.cm_id, s.sdh_id,
           bool_or(c.is_primary AND s.is_primary) AS primary_match
    FROM cm_names c
    JOIN sdh_names s ON c.nkey = s.nkey AND length(c.nkey) > 1
    GROUP BY c.cm_id, s.sdh_id
),
scored AS (
    SELECT p.cm_id, p.sdh_id, p.primary_match,
           (cb.bday IS NOT NULL AND sb.bday IS NOT NULL AND cb.bday = sb.bday) AS bday_match,
           (cb.bday IS NOT NULL AND sb.bday IS NOT NULL AND cb.bday <> sb.bday) AS bday_conflict
    FROM name_pairs p
    LEFT JOIN cm_bday cb ON cb.cm_id = p.cm_id
    LEFT JOIN sdh_bday sb ON sb.sdh_id = p.sdh_id
),
labeled AS (
    -- A shared *primary* name is a strong signal on its own, so a birthday discrepancy
    -- only demotes it (the two sources often disagree by a day or a year) rather than
    -- dropping it. A birthday conflict on an *alias-only* match, however, is treated as
    -- two different people who happen to share a ring name, and excluded below.
    SELECT cm_id, sdh_id,
           CASE WHEN bday_match AND primary_match THEN 'name_and_birthday'
                WHEN bday_match THEN 'alias_and_birthday'
                WHEN primary_match AND bday_conflict THEN 'name_birthday_mismatch'
                WHEN primary_match THEN 'name'
                ELSE 'alias' END AS match_method,
           CASE WHEN bday_match AND primary_match THEN 1.0
                WHEN bday_match THEN 0.9
                WHEN primary_match AND bday_conflict THEN 0.7
                WHEN primary_match THEN 0.8
                ELSE 0.6 END AS confidence
    FROM scored
    WHERE NOT (bday_conflict AND NOT primary_match)
),
ranked AS (
    SELECT cm_id, sdh_id, match_method, confidence,
           row_number() OVER (PARTITION BY cm_id ORDER BY confidence DESC, sdh_id) AS rk_cm,
           row_number() OVER (PARTITION BY sdh_id ORDER BY confidence DESC, cm_id) AS rk_sdh
    FROM labeled
)
-- Keep only mutually-best pairs so the crosswalk stays 1:1.
SELECT cm_id, sdh_id, match_method, confidence
FROM ranked
WHERE rk_cm = 1 AND rk_sdh = 1;

-- ================================ TITLES ================================
-- Match within the same promotion. Cagematch promotion ids: '1' = WWE, '2287' = AEW.
-- SDH title id is '<promo-slug>/<title-slug>', so its slug is split_part(id,'/',1).

DELETE FROM title_crosswalk;

INSERT INTO title_crosswalk (cagematch_id, sdh_id, match_method, confidence)
WITH promo_map(cm_promotion, sdh_slug) AS (
    VALUES ('1', 'wwe'), ('2287', 'aew')
),
cm_titles AS (
    SELECT t.id AS cm_id, pm.sdh_slug,
           norm_name(t.name) AS nkey,
           -- Fuzzy key: drop generic championship words + promotion tokens.
           trim(regexp_replace(norm_name(t.name),
               '\b(wwe|wwf|aew|championship|title|world|undisputed)\b', '', 'g')) AS fkey
    FROM titles t
    JOIN promo_map pm ON pm.cm_promotion = t.promotion
),
sdh_titles_norm AS (
    SELECT id AS sdh_id, split_part(id, '/', 1) AS sdh_slug,
           norm_name(name) AS nkey,
           trim(regexp_replace(norm_name(name),
               '\b(wwe|wwf|aew|championship|title|world|undisputed)\b', '', 'g')) AS fkey
    FROM sdh_titles
),
pairs AS (
    SELECT c.cm_id, s.sdh_id,
           (c.nkey = s.nkey) AS exact_match,
           (c.fkey = s.fkey AND length(c.fkey) > 1) AS fuzzy_match
    FROM cm_titles c
    JOIN sdh_titles_norm s
      ON c.sdh_slug = s.sdh_slug
     AND (c.nkey = s.nkey OR (c.fkey = s.fkey AND length(c.fkey) > 1))
),
labeled AS (
    SELECT cm_id, sdh_id,
           CASE WHEN exact_match THEN 'name' ELSE 'name_fuzzy' END AS match_method,
           CASE WHEN exact_match THEN 0.9 ELSE 0.7 END AS confidence
    FROM pairs
),
ranked AS (
    SELECT cm_id, sdh_id, match_method, confidence,
           row_number() OVER (PARTITION BY cm_id ORDER BY confidence DESC, sdh_id) AS rk_cm,
           row_number() OVER (PARTITION BY sdh_id ORDER BY confidence DESC, cm_id) AS rk_sdh
    FROM labeled
)
SELECT cm_id, sdh_id, match_method, confidence
FROM ranked
WHERE rk_cm = 1 AND rk_sdh = 1;

-- ======================= CONVENIENCE JOIN VIEWS =======================
-- Local warehouse only (not synced to Postgres). Flatten each crosswalk into a
-- side-by-side comparison for quick inspection / downstream querying.

CREATE OR REPLACE VIEW v_wrestlers_matched AS
SELECT x.cagematch_id, x.sdh_id, x.match_method, x.confidence,
       w.name AS cagematch_name, s.name AS sdh_name,
       s.real_name AS sdh_real_name,
       w.birthday AS cagematch_birthday, s.birthday AS sdh_birthday,
       s.image_url AS sdh_image_url, s.profile_url AS sdh_profile_url
FROM wrestler_crosswalk x
JOIN wrestlers w ON w.id = x.cagematch_id
JOIN sdh_wrestlers s ON s.id = x.sdh_id;

CREATE OR REPLACE VIEW v_titles_matched AS
SELECT x.cagematch_id, x.sdh_id, x.match_method, x.confidence,
       t.name AS cagematch_name, s.name AS sdh_name,
       t.promotion AS cagematch_promotion, s.promotion AS sdh_promotion,
       s.image_url AS sdh_image_url, s.profile_url AS sdh_profile_url
FROM title_crosswalk x
JOIN titles t ON t.id = x.cagematch_id
JOIN sdh_titles s ON s.id = x.sdh_id;
