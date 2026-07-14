from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def promotions_list_html() -> str:
    return (FIXTURES_DIR / "promotions_list.html").read_text(encoding="utf-8")


@pytest.fixture
def promotion_profile_html() -> str:
    return (FIXTURES_DIR / "promotion_profile.html").read_text(encoding="utf-8")


@pytest.fixture
def wwe_roster_html() -> str:
    return (FIXTURES_DIR / "wwe_roster.html").read_text(encoding="utf-8")


@pytest.fixture
def aew_roster_html() -> str:
    return (FIXTURES_DIR / "aew_roster.html").read_text(encoding="utf-8")


@pytest.fixture
def wwe_all_time_roster_html() -> str:
    return (FIXTURES_DIR / "wwe_alltime_roster.html").read_text(encoding="utf-8")


@pytest.fixture
def wrestler_profile_rusev_html() -> str:
    return (FIXTURES_DIR / "wrestler_profile_rusev.html").read_text(encoding="utf-8")


@pytest.fixture
def wrestler_profile_multirange_html() -> str:
    return (FIXTURES_DIR / "wrestler_profile_multirange.html").read_text(encoding="utf-8")
