-- transform.sql — flattens each source JSONL file into the tables in schema.sql.
-- Statements are grouped under `-- @source: <name>` markers (matching a spider's
-- jsonl filename stem); warehouse.py loads a source by running only its block.
-- `{path}` is substituted with the source file's absolute path (single-quote
-- escaped) before execution — these are internal, non-user-controlled paths.
--
-- Each block opens by declaring a `raw_<source>` view over the JSONL file with an
-- explicit `columns` schema (rather than `read_ndjson_auto`'s type inference):
-- DuckDB's auto-inference derives the schema from a sample of rows, so an
-- optional field absent from every sampled row (e.g. `name_history`, which is
-- unset in every promotion currently in data/promotions.jsonl) would silently
-- disappear from the inferred schema and break column references below. An
-- explicit schema always has every field, NULL where absent, regardless of
-- what the data currently contains.
--
-- Parent-entity tables (promotions/wrestlers/events/titles/matches) use
-- ON CONFLICT ... DO UPDATE so re-scraped rows refresh in place. Child/list
-- tables use ON CONFLICT ... DO NOTHING since a list entry's value at a given
-- position doesn't change independently of its parent being reloaded.

-- @source: promotions

CREATE OR REPLACE TEMP VIEW raw_promotions AS
SELECT * FROM read_ndjson('{path}', columns = {
    id: 'VARCHAR', name: 'VARCHAR', profile_url: 'VARCHAR', location: 'VARCHAR',
    active_year_start: 'INTEGER', active_year_end: 'INTEGER', rating: 'DOUBLE', votes: 'INTEGER',
    name_history: 'STRUCT(name VARCHAR, from_date VARCHAR, to_date VARCHAR)[]'
});

INSERT INTO promotions
SELECT id, name, profile_url, location, active_year_start, active_year_end, rating, votes
FROM raw_promotions
ON CONFLICT (id) DO UPDATE SET
    name = excluded.name,
    profile_url = excluded.profile_url,
    location = excluded.location,
    active_year_start = excluded.active_year_start,
    active_year_end = excluded.active_year_end,
    rating = excluded.rating,
    votes = excluded.votes;

INSERT INTO promotion_name_history (promotion_id, seq, name, from_date, to_date)
SELECT p.id, t.seq - 1, t.entry.name, t.entry.from_date, t.entry.to_date
FROM raw_promotions p, UNNEST(p.name_history) WITH ORDINALITY AS t(entry, seq)
WHERE p.name_history IS NOT NULL
ON CONFLICT (promotion_id, seq) DO NOTHING;

-- @source: wrestlers

CREATE OR REPLACE TEMP VIEW raw_wrestlers AS
SELECT * FROM read_ndjson('{path}', columns = {
    id: 'VARCHAR', name: 'VARCHAR', profile_url: 'VARCHAR', promotions: 'VARCHAR[]',
    gender: 'VARCHAR', birthday: 'VARCHAR', birthplace: 'VARCHAR', age: 'INTEGER',
    height_cm: 'INTEGER', weight_kg: 'INTEGER',
    background_in_sports: 'VARCHAR[]', alter_egos: 'VARCHAR[]', nicknames: 'VARCHAR[]',
    signature_moves: 'VARCHAR[]', wrestling_style: 'VARCHAR[]', trainers: 'VARCHAR[]',
    active_roles: 'VARCHAR[]',
    roles: 'STRUCT(role VARCHAR, date_ranges STRUCT(from_date VARCHAR, to_date VARCHAR)[])[]',
    career_start: 'VARCHAR', career_end: 'VARCHAR', career_experience_years: 'INTEGER',
    websites: 'VARCHAR[]', current_promotion: 'VARCHAR', current_brand: 'VARCHAR',
    roster_rating: 'DOUBLE', roster_votes: 'INTEGER', career_shows: 'INTEGER'
});

INSERT INTO wrestlers
SELECT id, name, profile_url, gender, birthday, birthplace, age, height_cm, weight_kg,
       career_start, career_end, career_experience_years, current_promotion,
       current_brand, roster_rating, roster_votes, career_shows
FROM raw_wrestlers
ON CONFLICT (id) DO UPDATE SET
    name = excluded.name,
    profile_url = excluded.profile_url,
    gender = excluded.gender,
    birthday = excluded.birthday,
    birthplace = excluded.birthplace,
    age = excluded.age,
    height_cm = excluded.height_cm,
    weight_kg = excluded.weight_kg,
    career_start = excluded.career_start,
    career_end = excluded.career_end,
    career_experience_years = excluded.career_experience_years,
    current_promotion = excluded.current_promotion,
    current_brand = excluded.current_brand,
    roster_rating = excluded.roster_rating,
    roster_votes = excluded.roster_votes,
    career_shows = excluded.career_shows;

INSERT INTO wrestler_promotions (wrestler_id, promotion_id, seq)
SELECT w.id, t.prom, t.seq - 1
FROM raw_wrestlers w, UNNEST(w.promotions) WITH ORDINALITY AS t(prom, seq)
WHERE w.promotions IS NOT NULL
ON CONFLICT (wrestler_id, seq) DO NOTHING;

INSERT INTO wrestler_attributes (wrestler_id, attr_type, seq, value)
SELECT w.id, 'nickname', t.seq - 1, t.val
FROM raw_wrestlers w, UNNEST(w.nicknames) WITH ORDINALITY AS t(val, seq)
WHERE w.nicknames IS NOT NULL
UNION ALL
SELECT w.id, 'alter_ego', t.seq - 1, t.val
FROM raw_wrestlers w, UNNEST(w.alter_egos) WITH ORDINALITY AS t(val, seq)
WHERE w.alter_egos IS NOT NULL
UNION ALL
SELECT w.id, 'signature_move', t.seq - 1, t.val
FROM raw_wrestlers w, UNNEST(w.signature_moves) WITH ORDINALITY AS t(val, seq)
WHERE w.signature_moves IS NOT NULL
UNION ALL
SELECT w.id, 'wrestling_style', t.seq - 1, t.val
FROM raw_wrestlers w, UNNEST(w.wrestling_style) WITH ORDINALITY AS t(val, seq)
WHERE w.wrestling_style IS NOT NULL
UNION ALL
SELECT w.id, 'trainer', t.seq - 1, t.val
FROM raw_wrestlers w, UNNEST(w.trainers) WITH ORDINALITY AS t(val, seq)
WHERE w.trainers IS NOT NULL
UNION ALL
SELECT w.id, 'website', t.seq - 1, t.val
FROM raw_wrestlers w, UNNEST(w.websites) WITH ORDINALITY AS t(val, seq)
WHERE w.websites IS NOT NULL
UNION ALL
SELECT w.id, 'background_in_sports', t.seq - 1, t.val
FROM raw_wrestlers w, UNNEST(w.background_in_sports) WITH ORDINALITY AS t(val, seq)
WHERE w.background_in_sports IS NOT NULL
UNION ALL
SELECT w.id, 'active_role', t.seq - 1, t.val
FROM raw_wrestlers w, UNNEST(w.active_roles) WITH ORDINALITY AS t(val, seq)
WHERE w.active_roles IS NOT NULL
ON CONFLICT (wrestler_id, attr_type, seq) DO NOTHING;

INSERT INTO wrestler_roles (wrestler_id, seq, role)
SELECT w.id, t.seq - 1, t.entry.role
FROM raw_wrestlers w, UNNEST(w.roles) WITH ORDINALITY AS t(entry, seq)
WHERE w.roles IS NOT NULL
ON CONFLICT (wrestler_id, seq) DO NOTHING;

INSERT INTO wrestler_role_date_ranges (wrestler_role_id, seq, from_date, to_date)
SELECT wr.id, dr.seq - 1, dr.entry.from_date, dr.entry.to_date
FROM raw_wrestlers w,
     UNNEST(w.roles) WITH ORDINALITY AS role_t(role_entry, role_seq),
     UNNEST(role_entry.date_ranges) WITH ORDINALITY AS dr(entry, seq)
JOIN wrestler_roles wr ON wr.wrestler_id = w.id AND wr.seq = role_t.role_seq - 1
WHERE w.roles IS NOT NULL AND role_entry.date_ranges IS NOT NULL
ON CONFLICT (wrestler_role_id, seq) DO NOTHING;

-- @source: titles

CREATE OR REPLACE TEMP VIEW raw_titles AS
SELECT * FROM read_ndjson('{path}', columns = {
    id: 'VARCHAR', name: 'VARCHAR', promotion: 'VARCHAR',
    reigns: 'STRUCT(
        reign_number INTEGER,
        champions STRUCT(id VARCHAR, name VARCHAR, title_reign_count INTEGER)[],
        team STRUCT(id VARCHAR, name VARCHAR, title_reign_count INTEGER),
        from_date VARCHAR, to_date VARCHAR, duration_days INTEGER, location VARCHAR
    )[]'
});

INSERT INTO titles
SELECT id, name, promotion
FROM raw_titles
ON CONFLICT (id) DO UPDATE SET
    name = excluded.name,
    promotion = excluded.promotion;

-- Reigns are a full per-title snapshot rather than a growing log, so a reload replaces
-- them wholesale: delete this file's titles' existing reign rows (children first, to
-- satisfy the FK) and reinsert. This refreshes an ongoing reign's growing duration/end
-- date in place, and sidesteps DuckDB rejecting `ON CONFLICT DO UPDATE` on a table
-- referenced by a foreign key (which a plain upsert on title_reigns would hit).
DELETE FROM title_reign_champions
WHERE title_reign_id IN (
    SELECT tr.id FROM title_reigns tr WHERE tr.title_id IN (SELECT id FROM raw_titles)
);
DELETE FROM title_reigns WHERE title_id IN (SELECT id FROM raw_titles);

INSERT INTO title_reigns
SELECT t.id || '-' || r.reign.reign_number::VARCHAR, t.id, r.reign.reign_number,
       r.reign.from_date, r.reign.to_date, r.reign.duration_days, r.reign.location,
       r.reign.team.id, r.reign.team.name, r.reign.team.title_reign_count
FROM raw_titles t, UNNEST(t.reigns) AS r(reign)
WHERE t.reigns IS NOT NULL;

INSERT INTO title_reign_champions (title_reign_id, seq, wrestler_id, wrestler_name, reign_count)
SELECT t.id || '-' || r.reign.reign_number::VARCHAR, c.seq - 1, c.champ.id, c.champ.name,
       c.champ.title_reign_count
FROM raw_titles t,
     UNNEST(t.reigns) AS r(reign),
     UNNEST(r.reign.champions) WITH ORDINALITY AS c(champ, seq)
WHERE t.reigns IS NOT NULL AND r.reign.champions IS NOT NULL;

-- @source: matches

CREATE OR REPLACE TEMP VIEW raw_matches AS
SELECT * FROM read_ndjson('{path}', columns = {
    id: 'VARCHAR', name: 'VARCHAR', profile_url: 'VARCHAR', promotion: 'VARCHAR',
    date: 'VARCHAR', location: 'VARCHAR', event_rating: 'DOUBLE', event_votes: 'INTEGER',
    event_type: 'VARCHAR', arena: 'VARCHAR', broadcast_type: 'VARCHAR', broadcast_date: 'VARCHAR',
    tv_network: 'VARCHAR',
    commentators: 'STRUCT(id VARCHAR, name VARCHAR)[]',
    matches: 'STRUCT(
        match_index INTEGER, match_type VARCHAR, title_id VARCHAR, title_name VARCHAR,
        title_change BOOLEAN, duration VARCHAR, result VARCHAR, finish_note VARCHAR,
        winners STRUCT(
            wrestlers STRUCT(id VARCHAR, name VARCHAR)[],
            teams STRUCT(id VARCHAR, name VARCHAR)[],
            valets STRUCT(id VARCHAR, name VARCHAR)[],
            is_champion BOOLEAN
        ),
        losers STRUCT(
            wrestlers STRUCT(id VARCHAR, name VARCHAR)[],
            teams STRUCT(id VARCHAR, name VARCHAR)[],
            valets STRUCT(id VARCHAR, name VARCHAR)[],
            is_champion BOOLEAN
        )[],
        sides STRUCT(
            wrestlers STRUCT(id VARCHAR, name VARCHAR)[],
            teams STRUCT(id VARCHAR, name VARCHAR)[],
            valets STRUCT(id VARCHAR, name VARCHAR)[],
            is_champion BOOLEAN
        )[],
        match_rating DOUBLE, match_votes INTEGER, won_rating VARCHAR, notes VARCHAR[]
    )[]'
});

INSERT INTO events
SELECT id, name, profile_url, promotion, date, location, event_rating, event_votes,
       event_type, arena, broadcast_type, broadcast_date, tv_network
FROM raw_matches
ON CONFLICT (id) DO UPDATE SET
    name = excluded.name,
    profile_url = excluded.profile_url,
    promotion = excluded.promotion,
    date = excluded.date,
    location = excluded.location,
    event_rating = excluded.event_rating,
    event_votes = excluded.event_votes,
    event_type = excluded.event_type,
    arena = excluded.arena,
    broadcast_type = excluded.broadcast_type,
    broadcast_date = excluded.broadcast_date,
    tv_network = excluded.tv_network;

INSERT INTO event_commentators (event_id, seq, wrestler_id, wrestler_name)
SELECT e.id, t.seq - 1, t.c.id, t.c.name
FROM raw_matches e, UNNEST(e.commentators) WITH ORDINALITY AS t(c, seq)
WHERE e.commentators IS NOT NULL
ON CONFLICT (event_id, seq) DO NOTHING;

INSERT INTO matches
SELECT e.id || '-' || t.m.match_index::VARCHAR, e.id, t.m.match_index, t.m.match_type,
       t.m.title_id, t.m.title_name, t.m.title_change, t.m.duration, t.m.result,
       t.m.finish_note, t.m.match_rating, t.m.match_votes, t.m.won_rating
FROM raw_matches e, UNNEST(e.matches) AS t(m)
WHERE e.matches IS NOT NULL
ON CONFLICT (id) DO UPDATE SET
    match_type = excluded.match_type,
    title_id = excluded.title_id,
    title_name = excluded.title_name,
    title_change = excluded.title_change,
    duration = excluded.duration,
    result = excluded.result,
    finish_note = excluded.finish_note,
    match_rating = excluded.match_rating,
    match_votes = excluded.match_votes,
    won_rating = excluded.won_rating;

INSERT INTO match_notes (match_id, seq, note)
SELECT e.id || '-' || mt.m.match_index::VARCHAR, nt.seq - 1, nt.note
FROM raw_matches e,
     UNNEST(e.matches) AS mt(m),
     UNNEST(mt.m.notes) WITH ORDINALITY AS nt(note, seq)
WHERE e.matches IS NOT NULL AND mt.m.notes IS NOT NULL
ON CONFLICT (match_id, seq) DO NOTHING;

INSERT INTO match_sides (id, match_id, side_role, side_index, is_champion)
SELECT match_id || '-' || side_role || '-' || side_index::VARCHAR,
       match_id, side_role, side_index, side.is_champion
FROM (
    SELECT e.id || '-' || mt.m.match_index::VARCHAR AS match_id, 'winner' AS side_role,
           0 AS side_index, mt.m.winners AS side
    FROM raw_matches e, UNNEST(e.matches) AS mt(m)
    WHERE e.matches IS NOT NULL AND mt.m.winners IS NOT NULL

    UNION ALL

    SELECT e.id || '-' || mt.m.match_index::VARCHAR, 'loser', lt.seq - 1, lt.s
    FROM raw_matches e,
         UNNEST(e.matches) AS mt(m),
         UNNEST(mt.m.losers) WITH ORDINALITY AS lt(s, seq)
    WHERE e.matches IS NOT NULL AND mt.m.losers IS NOT NULL

    UNION ALL

    SELECT e.id || '-' || mt.m.match_index::VARCHAR, 'side', st.seq - 1, st.s
    FROM raw_matches e,
         UNNEST(e.matches) AS mt(m),
         UNNEST(mt.m.sides) WITH ORDINALITY AS st(s, seq)
    WHERE e.matches IS NOT NULL AND mt.m.sides IS NOT NULL
) all_sides
ON CONFLICT (id) DO NOTHING;

INSERT INTO match_side_participants (match_side_id, participant_role, seq, participant_id, participant_name)
SELECT match_side_id, 'wrestler', wt.seq - 1, wt.p.id, wt.p.name
FROM (
    SELECT e.id || '-' || mt.m.match_index::VARCHAR || '-winner-0' AS match_side_id, mt.m.winners AS side
    FROM raw_matches e, UNNEST(e.matches) AS mt(m)
    WHERE e.matches IS NOT NULL AND mt.m.winners IS NOT NULL

    UNION ALL

    SELECT e.id || '-' || mt.m.match_index::VARCHAR || '-loser-' || (lt.seq - 1)::VARCHAR, lt.s
    FROM raw_matches e,
         UNNEST(e.matches) AS mt(m),
         UNNEST(mt.m.losers) WITH ORDINALITY AS lt(s, seq)
    WHERE e.matches IS NOT NULL AND mt.m.losers IS NOT NULL

    UNION ALL

    SELECT e.id || '-' || mt.m.match_index::VARCHAR || '-side-' || (st.seq - 1)::VARCHAR, st.s
    FROM raw_matches e,
         UNNEST(e.matches) AS mt(m),
         UNNEST(mt.m.sides) WITH ORDINALITY AS st(s, seq)
    WHERE e.matches IS NOT NULL AND mt.m.sides IS NOT NULL
) side_ids, UNNEST(side.wrestlers) WITH ORDINALITY AS wt(p, seq)
WHERE side.wrestlers IS NOT NULL

UNION ALL

SELECT match_side_id, 'team', tt.seq - 1, tt.p.id, tt.p.name
FROM (
    SELECT e.id || '-' || mt.m.match_index::VARCHAR || '-winner-0' AS match_side_id, mt.m.winners AS side
    FROM raw_matches e, UNNEST(e.matches) AS mt(m)
    WHERE e.matches IS NOT NULL AND mt.m.winners IS NOT NULL

    UNION ALL

    SELECT e.id || '-' || mt.m.match_index::VARCHAR || '-loser-' || (lt.seq - 1)::VARCHAR, lt.s
    FROM raw_matches e,
         UNNEST(e.matches) AS mt(m),
         UNNEST(mt.m.losers) WITH ORDINALITY AS lt(s, seq)
    WHERE e.matches IS NOT NULL AND mt.m.losers IS NOT NULL

    UNION ALL

    SELECT e.id || '-' || mt.m.match_index::VARCHAR || '-side-' || (st.seq - 1)::VARCHAR, st.s
    FROM raw_matches e,
         UNNEST(e.matches) AS mt(m),
         UNNEST(mt.m.sides) WITH ORDINALITY AS st(s, seq)
    WHERE e.matches IS NOT NULL AND mt.m.sides IS NOT NULL
) side_ids, UNNEST(side.teams) WITH ORDINALITY AS tt(p, seq)
WHERE side.teams IS NOT NULL

UNION ALL

SELECT match_side_id, 'valet', vt.seq - 1, vt.p.id, vt.p.name
FROM (
    SELECT e.id || '-' || mt.m.match_index::VARCHAR || '-winner-0' AS match_side_id, mt.m.winners AS side
    FROM raw_matches e, UNNEST(e.matches) AS mt(m)
    WHERE e.matches IS NOT NULL AND mt.m.winners IS NOT NULL

    UNION ALL

    SELECT e.id || '-' || mt.m.match_index::VARCHAR || '-loser-' || (lt.seq - 1)::VARCHAR, lt.s
    FROM raw_matches e,
         UNNEST(e.matches) AS mt(m),
         UNNEST(mt.m.losers) WITH ORDINALITY AS lt(s, seq)
    WHERE e.matches IS NOT NULL AND mt.m.losers IS NOT NULL

    UNION ALL

    SELECT e.id || '-' || mt.m.match_index::VARCHAR || '-side-' || (st.seq - 1)::VARCHAR, st.s
    FROM raw_matches e,
         UNNEST(e.matches) AS mt(m),
         UNNEST(mt.m.sides) WITH ORDINALITY AS st(s, seq)
    WHERE e.matches IS NOT NULL AND mt.m.sides IS NOT NULL
) side_ids, UNNEST(side.valets) WITH ORDINALITY AS vt(p, seq)
WHERE side.valets IS NOT NULL

ON CONFLICT (match_side_id, participant_role, seq) DO NOTHING;

-- @source: sdh_titles

CREATE OR REPLACE TEMP VIEW raw_sdh_titles AS
SELECT * FROM read_ndjson('{path}', columns = {
    id: 'VARCHAR', name: 'VARCHAR', profile_url: 'VARCHAR', promotion: 'VARCHAR',
    brand: 'VARCHAR', gender: 'VARCHAR', date_established: 'VARCHAR',
    current_champion: 'VARCHAR', territory: 'VARCHAR', title_type: 'VARCHAR',
    image_url: 'VARCHAR',
    name_history: 'STRUCT(name VARCHAR, from_date VARCHAR, to_date VARCHAR, image_url VARCHAR)[]',
    reigns: 'STRUCT(
        reign_number INTEGER,
        champions STRUCT(id VARCHAR, name VARCHAR, title_reign_count INTEGER)[],
        from_date VARCHAR, to_date VARCHAR, duration_days INTEGER, location VARCHAR,
        event_name VARCHAR, event_url VARCHAR, notes VARCHAR, is_vacant BOOLEAN
    )[]'
});

INSERT INTO sdh_titles
SELECT id, name, profile_url, promotion, brand, gender, date_established,
       current_champion, territory, title_type, image_url
FROM raw_sdh_titles
ON CONFLICT (id) DO UPDATE SET
    name = excluded.name,
    profile_url = excluded.profile_url,
    promotion = excluded.promotion,
    brand = excluded.brand,
    gender = excluded.gender,
    date_established = excluded.date_established,
    current_champion = excluded.current_champion,
    territory = excluded.territory,
    title_type = excluded.title_type,
    image_url = excluded.image_url;

DELETE FROM sdh_title_name_history WHERE title_id IN (SELECT id FROM raw_sdh_titles);
INSERT INTO sdh_title_name_history (title_id, seq, name, from_date, to_date, image_url)
SELECT t.id, h.seq - 1, h.entry.name, h.entry.from_date, h.entry.to_date, h.entry.image_url
FROM raw_sdh_titles t, UNNEST(t.name_history) WITH ORDINALITY AS h(entry, seq)
WHERE t.name_history IS NOT NULL;

DELETE FROM sdh_title_reign_champions
WHERE title_reign_id IN (
    SELECT tr.id FROM sdh_title_reigns tr WHERE tr.title_id IN (SELECT id FROM raw_sdh_titles)
);
DELETE FROM sdh_title_reigns WHERE title_id IN (SELECT id FROM raw_sdh_titles);

INSERT INTO sdh_title_reigns
SELECT t.id || '-' || (r.seq - 1)::VARCHAR, t.id, r.seq - 1, r.reign.reign_number,
       r.reign.from_date, r.reign.to_date, r.reign.duration_days, r.reign.location,
       r.reign.event_name, r.reign.event_url, r.reign.notes, r.reign.is_vacant
FROM raw_sdh_titles t, UNNEST(t.reigns) WITH ORDINALITY AS r(reign, seq)
WHERE t.reigns IS NOT NULL;

INSERT INTO sdh_title_reign_champions (title_reign_id, seq, wrestler_id, wrestler_name, reign_count)
SELECT t.id || '-' || (r.seq - 1)::VARCHAR, c.seq - 1, c.champ.id, c.champ.name,
       c.champ.title_reign_count
FROM raw_sdh_titles t,
     UNNEST(t.reigns) WITH ORDINALITY AS r(reign, seq),
     UNNEST(r.reign.champions) WITH ORDINALITY AS c(champ, seq)
WHERE t.reigns IS NOT NULL AND r.reign.champions IS NOT NULL;

-- @source: sdh_wrestlers

CREATE OR REPLACE TEMP VIEW raw_sdh_wrestlers AS
SELECT * FROM read_ndjson('{path}', columns = {
    id: 'VARCHAR', name: 'VARCHAR', profile_url: 'VARCHAR', real_name: 'VARCHAR',
    gender: 'VARCHAR', birthday: 'VARCHAR', age: 'INTEGER', nationality: 'VARCHAR',
    birthplace: 'VARCHAR', billed_from: 'VARCHAR', height_cm: 'INTEGER', weight_kg: 'INTEGER',
    image_url: 'VARCHAR',
    nicknames: 'VARCHAR[]', finishers: 'VARCHAR[]',
    name_history: 'STRUCT(name VARCHAR, from_date VARCHAR, to_date VARCHAR)[]',
    promotions: 'STRUCT(promotion VARCHAR, brand VARCHAR, from_date VARCHAR, to_date VARCHAR)[]',
    roles: 'STRUCT(role VARCHAR, from_date VARCHAR, to_date VARCHAR)[]',
    alignments: 'STRUCT(alignment VARCHAR, details VARCHAR, from_date VARCHAR, to_date VARCHAR)[]',
    images: 'STRUCT(label VARCHAR, image_url VARCHAR)[]',
    career_awards: 'STRUCT(name VARCHAR, url VARCHAR, image_url VARCHAR)[]',
    hall_of_fames: 'STRUCT(name VARCHAR, category VARCHAR, year INTEGER, url VARCHAR, image_url VARCHAR)[]',
    title_wins: 'STRUCT(promotion VARCHAR, title VARCHAR, times INTEGER, details VARCHAR, title_url VARCHAR, image_url VARCHAR, source VARCHAR)[]',
    accomplishments: 'VARCHAR[]'
});

INSERT INTO sdh_wrestlers
SELECT id, name, profile_url, real_name, gender, birthday, age, nationality,
       birthplace, billed_from, height_cm, weight_kg, image_url
FROM raw_sdh_wrestlers
ON CONFLICT (id) DO UPDATE SET
    name = excluded.name,
    profile_url = excluded.profile_url,
    real_name = excluded.real_name,
    gender = excluded.gender,
    birthday = excluded.birthday,
    age = excluded.age,
    nationality = excluded.nationality,
    birthplace = excluded.birthplace,
    billed_from = excluded.billed_from,
    height_cm = excluded.height_cm,
    weight_kg = excluded.weight_kg,
    image_url = excluded.image_url;

DELETE FROM sdh_wrestler_attributes WHERE wrestler_id IN (SELECT id FROM raw_sdh_wrestlers);
INSERT INTO sdh_wrestler_attributes (wrestler_id, attr_type, seq, value)
SELECT w.id, 'nickname', t.seq - 1, t.val
FROM raw_sdh_wrestlers w, UNNEST(w.nicknames) WITH ORDINALITY AS t(val, seq)
WHERE w.nicknames IS NOT NULL
UNION ALL
SELECT w.id, 'finisher', t.seq - 1, t.val
FROM raw_sdh_wrestlers w, UNNEST(w.finishers) WITH ORDINALITY AS t(val, seq)
WHERE w.finishers IS NOT NULL;

DELETE FROM sdh_wrestler_name_history WHERE wrestler_id IN (SELECT id FROM raw_sdh_wrestlers);
INSERT INTO sdh_wrestler_name_history (wrestler_id, seq, name, from_date, to_date)
SELECT w.id, t.seq - 1, t.entry.name, t.entry.from_date, t.entry.to_date
FROM raw_sdh_wrestlers w, UNNEST(w.name_history) WITH ORDINALITY AS t(entry, seq)
WHERE w.name_history IS NOT NULL;

DELETE FROM sdh_wrestler_promotions WHERE wrestler_id IN (SELECT id FROM raw_sdh_wrestlers);
INSERT INTO sdh_wrestler_promotions (wrestler_id, seq, promotion, brand, from_date, to_date)
SELECT w.id, t.seq - 1, t.entry.promotion, t.entry.brand, t.entry.from_date, t.entry.to_date
FROM raw_sdh_wrestlers w, UNNEST(w.promotions) WITH ORDINALITY AS t(entry, seq)
WHERE w.promotions IS NOT NULL;

DELETE FROM sdh_wrestler_roles WHERE wrestler_id IN (SELECT id FROM raw_sdh_wrestlers);
INSERT INTO sdh_wrestler_roles (wrestler_id, seq, role, from_date, to_date)
SELECT w.id, t.seq - 1, t.entry.role, t.entry.from_date, t.entry.to_date
FROM raw_sdh_wrestlers w, UNNEST(w.roles) WITH ORDINALITY AS t(entry, seq)
WHERE w.roles IS NOT NULL;

DELETE FROM sdh_wrestler_alignments WHERE wrestler_id IN (SELECT id FROM raw_sdh_wrestlers);
INSERT INTO sdh_wrestler_alignments (wrestler_id, seq, alignment, details, from_date, to_date)
SELECT w.id, t.seq - 1, t.entry.alignment, t.entry.details, t.entry.from_date, t.entry.to_date
FROM raw_sdh_wrestlers w, UNNEST(w.alignments) WITH ORDINALITY AS t(entry, seq)
WHERE w.alignments IS NOT NULL;

DELETE FROM sdh_wrestler_images WHERE wrestler_id IN (SELECT id FROM raw_sdh_wrestlers);
INSERT INTO sdh_wrestler_images (wrestler_id, seq, label, image_url)
SELECT w.id, t.seq - 1, t.entry.label, t.entry.image_url
FROM raw_sdh_wrestlers w, UNNEST(w.images) WITH ORDINALITY AS t(entry, seq)
WHERE w.images IS NOT NULL;

DELETE FROM sdh_wrestler_career_awards WHERE wrestler_id IN (SELECT id FROM raw_sdh_wrestlers);
INSERT INTO sdh_wrestler_career_awards (wrestler_id, seq, name, url, image_url)
SELECT w.id, t.seq - 1, t.entry.name, t.entry.url, t.entry.image_url
FROM raw_sdh_wrestlers w, UNNEST(w.career_awards) WITH ORDINALITY AS t(entry, seq)
WHERE w.career_awards IS NOT NULL;

DELETE FROM sdh_wrestler_hall_of_fames WHERE wrestler_id IN (SELECT id FROM raw_sdh_wrestlers);
INSERT INTO sdh_wrestler_hall_of_fames (wrestler_id, seq, name, category, year, url, image_url)
SELECT w.id, t.seq - 1, t.entry.name, t.entry.category, t.entry.year, t.entry.url, t.entry.image_url
FROM raw_sdh_wrestlers w, UNNEST(w.hall_of_fames) WITH ORDINALITY AS t(entry, seq)
WHERE w.hall_of_fames IS NOT NULL;

DELETE FROM sdh_wrestler_title_wins WHERE wrestler_id IN (SELECT id FROM raw_sdh_wrestlers);
INSERT INTO sdh_wrestler_title_wins (
    wrestler_id, seq, promotion, title, times, details, title_url, image_url, source
)
SELECT w.id, t.seq - 1, t.entry.promotion, t.entry.title, t.entry.times, t.entry.details,
       t.entry.title_url, t.entry.image_url, t.entry.source
FROM raw_sdh_wrestlers w, UNNEST(w.title_wins) WITH ORDINALITY AS t(entry, seq)
WHERE w.title_wins IS NOT NULL;

DELETE FROM sdh_wrestler_accomplishments WHERE wrestler_id IN (SELECT id FROM raw_sdh_wrestlers);
INSERT INTO sdh_wrestler_accomplishments (wrestler_id, seq, value)
SELECT w.id, t.seq - 1, t.val
FROM raw_sdh_wrestlers w, UNNEST(w.accomplishments) WITH ORDINALITY AS t(val, seq)
WHERE w.accomplishments IS NOT NULL;
