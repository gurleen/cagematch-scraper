from parsel import Selector

from cagematch_scraper.config import Settings
from cagematch_scraper.spiders.promotions import (
    PromotionsSpider,
    _parse_active_years,
    _parse_rating,
    _parse_votes,
)


def test_parse_promotions_list(promotions_list_html: str) -> None:
    spider = PromotionsSpider(Settings(promotion_ids=""))
    selector = Selector(text=promotions_list_html)

    items = list(spider.parse(selector, "https://www.cagematch.net/?id=8&view=promotions"))

    assert len(items) == 100
    ids = [item["id"] for item in items]
    assert len(ids) == len(set(ids))

    wwe = next(item for item in items if item["id"] == "1")
    assert wwe["name"] == "World Wrestling Entertainment"
    assert wwe["profile_url"] == "https://www.cagematch.net/?id=8&nr=1"
    assert wwe["location"] == "Stamford, Connecticut, USA"
    assert wwe["active_year_start"] == 1948
    assert wwe["active_year_end"] is None
    assert wwe["rating"] == 7.69
    assert wwe["votes"] == 1950

    aew = next(item for item in items if item["id"] == "2287")
    assert aew["name"] == "All Elite Wrestling"
    assert aew["active_year_start"] == 2019
    assert aew["active_year_end"] is None

    njpw = next(item for item in items if item["id"] == "7")
    assert njpw["name"] == "New Japan Pro Wrestling"

    ecw = next(item for item in items if item["id"] == "3")
    assert ecw["active_year_start"] == 1992
    assert ecw["active_year_end"] == 2001


def test_parse_promotions_list_filtered_by_default_settings(promotions_list_html: str) -> None:
    # _env_file=None keeps the developer's .env (which may widen promotion_ids)
    # from leaking into the default WWE+AEW assumption.
    spider = PromotionsSpider(Settings(_env_file=None))
    selector = Selector(text=promotions_list_html)

    items = list(spider.parse(selector, "https://www.cagematch.net/?id=8&view=promotions"))

    assert {item["id"] for item in items} == {"1", "2287"}


def test_parse_active_years() -> None:
    assert _parse_active_years("1948-") == (1948, None)
    assert _parse_active_years("2002-2023") == (2002, 2023)
    assert _parse_active_years("1999") == (1999, 1999)
    assert _parse_active_years("") == (None, None)


def test_parse_rating_and_votes() -> None:
    assert _parse_rating("7.69") == 7.69
    assert _parse_rating("n/a") is None
    assert _parse_votes("1950") == 1950
    assert _parse_votes("") is None


def test_parse_profile_name_history(promotion_profile_html: str) -> None:
    spider = PromotionsSpider(Settings(promotion_ids=""))
    selector = Selector(text=promotion_profile_html)

    item = spider.parse_profile(selector, {"id": "1", "name": "World Wrestling Entertainment"})

    history = item["name_history"]
    assert history[0] == {
        "name": "World Wrestling Entertainment",
        "from_date": "05.05.2002",
        "to_date": None,
    }
    assert history[1] == {
        "name": "World Wrestling Federation",
        "from_date": "30.03.1979",
        "to_date": "04.05.2002",
    }
    assert history[-1] == {
        "name": "National Wrestling Alliance Capital Sports",
        "from_date": "1948",
        "to_date": "1952",
    }
    assert len(history) == 5
