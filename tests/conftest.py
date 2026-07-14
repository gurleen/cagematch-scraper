from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def promotions_list_html() -> str:
    return (FIXTURES_DIR / "promotions_list.html").read_text(encoding="utf-8")


@pytest.fixture
def promotion_profile_html() -> str:
    return (FIXTURES_DIR / "promotion_profile.html").read_text(encoding="utf-8")
