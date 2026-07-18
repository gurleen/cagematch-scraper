"""Tests for the Cagematch<->SDH crosswalk matcher (export/match.sql)."""

from __future__ import annotations

import duckdb

from cagematch_scraper.export import warehouse


def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    warehouse.ensure_schema(con)
    return con


def _add_cm_wrestler(con, id_, name, birthday=None, alter_egos=()):
    con.execute(
        "INSERT INTO wrestlers (id, name, birthday) VALUES (?, ?, ?)", [id_, name, birthday]
    )
    for seq, alias in enumerate(alter_egos):
        con.execute(
            "INSERT INTO wrestler_attributes (wrestler_id, attr_type, seq, value) "
            "VALUES (?, 'alter_ego', ?, ?)",
            [id_, seq, alias],
        )


def _add_sdh_wrestler(con, id_, name, birthday=None, ring_names=()):
    con.execute(
        "INSERT INTO sdh_wrestlers (id, name, birthday) VALUES (?, ?, ?)", [id_, name, birthday]
    )
    for seq, rn in enumerate(ring_names):
        con.execute(
            "INSERT INTO sdh_wrestler_name_history (wrestler_id, seq, name) VALUES (?, ?, ?)",
            [id_, seq, rn],
        )


def test_wrestler_name_and_birthday_is_top_confidence() -> None:
    con = _con()
    _add_cm_wrestler(con, "1", "CM Punk", "26.10.1978")
    _add_sdh_wrestler(con, "cm-punk", "CM Punk", "October 26, 1978")
    warehouse.build_crosswalks(con)

    row = con.execute(
        "SELECT cagematch_id, sdh_id, match_method, confidence FROM wrestler_crosswalk"
    ).fetchone()
    assert row == ("1", "cm-punk", "name_and_birthday", 1.0)


def test_birthday_discrepancy_demotes_but_keeps_primary_name_match() -> None:
    """Sources disagreeing on birthday by a day/year must not drop an exact name match."""
    con = _con()
    _add_cm_wrestler(con, "1", "Solo Sikoa", "17.03.1993")
    _add_sdh_wrestler(con, "solo-sikoa", "Solo Sikoa", "March 18, 1993")
    warehouse.build_crosswalks(con)

    row = con.execute(
        "SELECT match_method, confidence FROM wrestler_crosswalk"
    ).fetchone()
    assert row == ("name_birthday_mismatch", 0.7)


def test_alias_match_excluded_when_birthdays_conflict() -> None:
    """A shared ring name with conflicting birthdays is treated as two different people."""
    con = _con()
    _add_cm_wrestler(con, "1", "Real Person A", "01.01.1990", alter_egos=["Shared Gimmick"])
    _add_sdh_wrestler(
        con, "person-b", "Real Person B", "February 2, 1985", ring_names=["Shared Gimmick"]
    )
    warehouse.build_crosswalks(con)

    assert con.execute("SELECT count(*) FROM wrestler_crosswalk").fetchone()[0] == 0


def test_alias_match_kept_when_birthday_unknown() -> None:
    con = _con()
    _add_cm_wrestler(con, "1", "Hangman Page", alter_egos=["Adam Page"])
    _add_sdh_wrestler(con, "adam-page", "Adam Page")
    warehouse.build_crosswalks(con)

    row = con.execute("SELECT match_method, confidence FROM wrestler_crosswalk").fetchone()
    assert row == ("alias", 0.6)


def test_compound_sdh_display_name_matches_each_identity() -> None:
    """SDH renders multi-identity wrestlers as 'Name A / Name B'; each part should
    match as a primary name (e.g. Cagematch 'Apollo Crews' <-> SDH
    'Apollo Crews / Uhaa Nation'), even when birthdays disagree slightly."""
    con = _con()
    _add_cm_wrestler(con, "1", "Apollo Crews", "25.08.1987")
    _add_sdh_wrestler(con, "apollo-crews", "Apollo Crews / Uhaa Nation", "August 22, 1987")
    warehouse.build_crosswalks(con)

    row = con.execute(
        "SELECT sdh_id, match_method FROM wrestler_crosswalk WHERE cagematch_id = '1'"
    ).fetchone()
    assert row == ("apollo-crews", "name_birthday_mismatch")


def test_crosswalk_is_one_to_one() -> None:
    """Two Cagematch rows sharing a name key can't both claim the same SDH row."""
    con = _con()
    _add_cm_wrestler(con, "1", "John Doe", "01.01.1990")
    _add_cm_wrestler(con, "2", "John Doe", "05.05.1995")
    _add_sdh_wrestler(con, "john-doe", "John Doe", "01.01.1990")
    warehouse.build_crosswalks(con)

    rows = con.execute(
        "SELECT cagematch_id FROM wrestler_crosswalk WHERE sdh_id = 'john-doe'"
    ).fetchall()
    assert len(rows) == 1
    # The birthday-corroborated one wins.
    assert rows[0][0] == "1"


def test_title_match_within_promotion() -> None:
    con = _con()
    con.execute(
        "INSERT INTO titles (id, name, promotion) VALUES "
        "('16', 'WWE Intercontinental Championship', '1'), "
        "('99', 'AEW World Championship', '2287')"
    )
    con.execute(
        "INSERT INTO sdh_titles (id, name) VALUES "
        "('wwe/wwe-intercontinental-championship', 'WWE Intercontinental Championship'), "
        "('aew/aew-world-championship', 'AEW World Championship')"
    )
    warehouse.build_crosswalks(con)

    pairs = con.execute(
        "SELECT cagematch_id, sdh_id, match_method FROM title_crosswalk ORDER BY cagematch_id"
    ).fetchall()
    assert ("16", "wwe/wwe-intercontinental-championship", "name") in pairs
    assert ("99", "aew/aew-world-championship", "name") in pairs


def test_title_match_respects_promotion_boundary() -> None:
    """Same normalized name in different promotions must not cross-link."""
    con = _con()
    con.execute("INSERT INTO titles (id, name, promotion) VALUES ('17', 'World Championship', '1')")
    con.execute(
        "INSERT INTO sdh_titles (id, name) VALUES ('aew/world-championship', 'World Championship')"
    )
    warehouse.build_crosswalks(con)

    assert con.execute("SELECT count(*) FROM title_crosswalk").fetchone()[0] == 0


def test_join_views_expose_matched_rows() -> None:
    con = _con()
    _add_cm_wrestler(con, "1", "CM Punk", "26.10.1978")
    _add_sdh_wrestler(con, "cm-punk", "CM Punk", "October 26, 1978")
    con.execute("UPDATE sdh_wrestlers SET real_name = 'Phillip Jack Brooks' WHERE id = 'cm-punk'")
    warehouse.build_crosswalks(con)

    row = con.execute(
        "SELECT cagematch_name, sdh_name, sdh_real_name, confidence FROM v_wrestlers_matched"
    ).fetchone()
    assert row == ("CM Punk", "CM Punk", "Phillip Jack Brooks", 1.0)
