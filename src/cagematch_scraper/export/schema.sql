-- schema.sql — flat, parquet/duckdb/postgres-friendly relational schema for the
-- data scraped into data/*.jsonl. No struct/array columns; nested JSONL lists are
-- flattened into child tables with a `seq` column preserving original order.
-- Portability: nothing DuckDB-specific beyond `nextval('...')` sequences, which
-- Postgres also supports natively.

-- ============================= PROMOTIONS =============================

CREATE TABLE IF NOT EXISTS promotions (
    id                  VARCHAR PRIMARY KEY,
    name                VARCHAR NOT NULL,
    profile_url         VARCHAR,
    location            VARCHAR,
    active_year_start   INTEGER,
    active_year_end     INTEGER,
    rating              DOUBLE,
    votes               INTEGER
);

CREATE TABLE IF NOT EXISTS promotion_name_history (
    promotion_id    VARCHAR NOT NULL REFERENCES promotions(id),
    seq             INTEGER NOT NULL,
    name            VARCHAR NOT NULL,
    from_date       VARCHAR,
    to_date         VARCHAR,
    PRIMARY KEY (promotion_id, seq)
);

-- ============================== WRESTLERS ==============================

CREATE TABLE IF NOT EXISTS wrestlers (
    id                          VARCHAR PRIMARY KEY,
    name                        VARCHAR NOT NULL,
    profile_url                 VARCHAR,
    gender                      VARCHAR,
    birthday                    VARCHAR,
    birthplace                  VARCHAR,
    age                         INTEGER,
    height_cm                   INTEGER,
    weight_kg                   INTEGER,
    career_start                VARCHAR,
    career_end                  VARCHAR,
    career_experience_years     INTEGER,
    current_promotion           VARCHAR,
    current_brand               VARCHAR,
    roster_rating               DOUBLE,
    roster_votes                INTEGER,
    career_shows                INTEGER
);

-- Junction: wrestler's promotion ids (referential, not a scalar bag)
CREATE TABLE IF NOT EXISTS wrestler_promotions (
    wrestler_id     VARCHAR NOT NULL REFERENCES wrestlers(id),
    promotion_id    VARCHAR NOT NULL,   -- soft FK; a wrestler may reference a
                                        -- promotion not present in promotions.jsonl
    seq             INTEGER NOT NULL,
    PRIMARY KEY (wrestler_id, seq)
);

-- Generic key-value table for every scalar-string-list field on WrestlerItem:
-- nicknames, alter_egos, signature_moves, wrestling_style, trainers, websites,
-- background_in_sports, active_roles.
CREATE TABLE IF NOT EXISTS wrestler_attributes (
    wrestler_id     VARCHAR NOT NULL REFERENCES wrestlers(id),
    attr_type       VARCHAR NOT NULL,   -- 'nickname' | 'alter_ego' | 'signature_move'
                                        -- | 'wrestling_style' | 'trainer' | 'website'
                                        -- | 'background_in_sports' | 'active_role'
    seq             INTEGER NOT NULL,
    value           VARCHAR NOT NULL,
    PRIMARY KEY (wrestler_id, attr_type, seq)
);

-- roles: list[{role, date_ranges: list[{from_date, to_date}]}]
CREATE SEQUENCE IF NOT EXISTS wrestler_roles_seq;
CREATE TABLE IF NOT EXISTS wrestler_roles (
    id              INTEGER PRIMARY KEY DEFAULT nextval('wrestler_roles_seq'),
    wrestler_id     VARCHAR NOT NULL REFERENCES wrestlers(id),
    seq             INTEGER NOT NULL,
    role            VARCHAR NOT NULL,
    UNIQUE (wrestler_id, seq)
);

CREATE TABLE IF NOT EXISTS wrestler_role_date_ranges (
    wrestler_role_id    INTEGER NOT NULL REFERENCES wrestler_roles(id),
    seq                 INTEGER NOT NULL,
    from_date           VARCHAR,
    to_date             VARCHAR,
    PRIMARY KEY (wrestler_role_id, seq)
);

-- ================================ TITLES ================================

CREATE TABLE IF NOT EXISTS titles (
    id          VARCHAR PRIMARY KEY,
    name        VARCHAR NOT NULL,
    promotion   VARCHAR
);

-- One row per reign in a title's "Title Holders" history. Synthetic id:
-- '<title_id>-<reign_number>'. A reign held by a named tag team/stable populates
-- team_id/team_name (its members are the title_reign_champions rows); a solo or
-- bare-co-champion reign leaves them NULL. reign_count is the page's "(N)" suffix
-- (that team's Nth reign here; a solo champion's own count lives on the champion row).
CREATE TABLE IF NOT EXISTS title_reigns (
    id                  VARCHAR PRIMARY KEY,
    title_id            VARCHAR NOT NULL REFERENCES titles(id),
    reign_number        INTEGER NOT NULL,
    from_date           VARCHAR,
    to_date             VARCHAR,           -- NULL = ongoing ("today" on the page)
    duration_days       INTEGER,
    location            VARCHAR,
    team_id             VARCHAR,           -- set only for named tag-team/stable reigns
    team_name           VARCHAR,
    team_reign_count    INTEGER
);

-- One row per champion in a reign (the solo holder, or each member of the team).
CREATE TABLE IF NOT EXISTS title_reign_champions (
    title_reign_id  VARCHAR NOT NULL REFERENCES title_reigns(id),
    seq             INTEGER NOT NULL,
    wrestler_id     VARCHAR,
    wrestler_name   VARCHAR,
    reign_count     INTEGER,               -- solo champion's "(N)" reign count
    PRIMARY KEY (title_reign_id, seq)
);

-- ================================ EVENTS ================================
-- One row per line in matches.jsonl (the "event")

CREATE TABLE IF NOT EXISTS events (
    id                  VARCHAR PRIMARY KEY,
    name                VARCHAR,
    profile_url         VARCHAR,
    promotion           VARCHAR,
    date                VARCHAR,
    location            VARCHAR,
    event_rating        DOUBLE,
    event_votes         INTEGER,
    event_type          VARCHAR,
    arena               VARCHAR,
    broadcast_type      VARCHAR,
    broadcast_date      VARCHAR,
    tv_network          VARCHAR
);

-- commentators: list[{id, name}] on the event
CREATE TABLE IF NOT EXISTS event_commentators (
    event_id        VARCHAR NOT NULL REFERENCES events(id),
    seq             INTEGER NOT NULL,
    wrestler_id     VARCHAR,
    wrestler_name   VARCHAR,
    PRIMARY KEY (event_id, seq)
);

-- ================================ MATCHES ================================
-- One row per entry in event.matches[]. Synthetic id: '<event_id>-<match_index>'

CREATE TABLE IF NOT EXISTS matches (
    id              VARCHAR PRIMARY KEY,
    event_id        VARCHAR NOT NULL REFERENCES events(id),
    match_index     INTEGER NOT NULL,
    match_type      VARCHAR,
    title_id        VARCHAR,
    title_name      VARCHAR,
    title_change    BOOLEAN,
    duration        VARCHAR,
    result          VARCHAR,               -- 'decisive' | 'no_decision' | 'unknown'
    finish_note     VARCHAR,
    match_rating    DOUBLE,
    match_votes     INTEGER
);

CREATE TABLE IF NOT EXISTS match_notes (
    match_id    VARCHAR NOT NULL REFERENCES matches(id),
    seq         INTEGER NOT NULL,
    note        VARCHAR NOT NULL,
    PRIMARY KEY (match_id, seq)
);

-- One row per MatchSide, across winners (0 or 1), losers[], and sides[].
-- side_role disambiguates which original list it came from, since a 'decisive'
-- result populates winners/losers while 'no_decision'/'unknown' populates sides.
CREATE TABLE IF NOT EXISTS match_sides (
    id              VARCHAR PRIMARY KEY,   -- '<match_id>-<side_role>-<side_index>'
    match_id        VARCHAR NOT NULL REFERENCES matches(id),
    side_role       VARCHAR NOT NULL,      -- 'winner' | 'loser' | 'side'
    side_index      INTEGER NOT NULL,      -- position within its list (0 for winner)
    is_champion     BOOLEAN
);

-- One row per participant, across wrestlers[], teams[], valets[] on a side.
CREATE TABLE IF NOT EXISTS match_side_participants (
    match_side_id       VARCHAR NOT NULL REFERENCES match_sides(id),
    participant_role    VARCHAR NOT NULL,  -- 'wrestler' | 'team' | 'valet'
    seq                 INTEGER NOT NULL,
    participant_id      VARCHAR,
    participant_name    VARCHAR,
    PRIMARY KEY (match_side_id, participant_role, seq)
);
