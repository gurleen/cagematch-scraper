import pytest
from parsel import Selector

from cagematch_scraper.config import Settings
from cagematch_scraper.spiders.wrestlers import WrestlersSpider


def test_requires_promotion_ids() -> None:
    with pytest.raises(ValueError):
        WrestlersSpider(Settings(promotion_ids=""))


def test_start_requests_one_per_promotion() -> None:
    spider = WrestlersSpider(Settings(promotion_ids="1,2287"))
    urls = list(spider.start_requests())
    assert urls == [
        "https://www.cagematch.net/?id=8&nr=1&page=15",
        "https://www.cagematch.net/?id=8&nr=2287&page=15",
    ]


def test_parse_wwe_roster_has_brand_column(wwe_roster_html: str) -> None:
    spider = WrestlersSpider(Settings())
    selector = Selector(text=wwe_roster_html)

    items = list(spider.parse(selector, "https://www.cagematch.net/?id=8&nr=1&page=15"))

    assert len(items) == 303
    ids = [item["id"] for item in items]
    assert len(ids) == len(set(ids))

    adam_pearce = next(item for item in items if item["id"] == "448")
    assert adam_pearce["name"] == "Adam Pearce"
    assert adam_pearce["profile_url"] == "https://www.cagematch.net/?id=2&nr=448"
    assert adam_pearce["promotions"] == ["1"]
    assert adam_pearce["active_roles"] == [
        "Road Agent",
        "Trainer",
        "On-Air Official",
        "Backstage Helper",
    ]
    assert adam_pearce["current_brand"] == "RAW"
    assert adam_pearce["roster_rating"] == 6.46
    assert adam_pearce["roster_votes"] == 194


def test_parse_aew_roster_has_no_brand_column(aew_roster_html: str) -> None:
    spider = WrestlersSpider(Settings())
    selector = Selector(text=aew_roster_html)

    items = list(spider.parse(selector, "https://www.cagematch.net/?id=8&nr=2287&page=15"))

    aaron_solo = next(item for item in items if item["id"] == "9087")
    assert aaron_solo["name"] == "Aaron Solo"
    assert aaron_solo["promotions"] == ["2287"]
    assert "current_brand" not in aaron_solo
    assert aaron_solo["roster_rating"] == 5.1
    assert aaron_solo["roster_votes"] == 109


def test_wrestler_seen_across_promotions_only_yielded_once(
    wwe_roster_html: str, aew_roster_html: str
) -> None:
    spider = WrestlersSpider(Settings())
    wwe_items = list(
        spider.parse(Selector(text=wwe_roster_html), "https://www.cagematch.net/?id=8&nr=1&page=15")
    )
    aew_items = list(
        spider.parse(
            Selector(text=aew_roster_html), "https://www.cagematch.net/?id=8&nr=2287&page=15"
        )
    )
    all_ids = [item["id"] for item in wwe_items + aew_items]
    assert len(all_ids) == len(set(all_ids))


def test_parse_profile_multi_range_roles(wrestler_profile_rusev_html: str) -> None:
    spider = WrestlersSpider(Settings())
    selector = Selector(text=wrestler_profile_rusev_html)

    item = spider.parse_profile(selector, {"id": "10610", "name": "Rusev"})

    assert item["age"] == 40
    assert item["birthday"] == "25.12.1985"
    assert item["birthplace"] == "Plovdiv, Bulgarien"
    assert item["gender"] == "male"
    assert item["height_cm"] == 183
    assert item["weight_kg"] == 127
    assert item["background_in_sports"] == ["Kraftdreikampf", "Rudern"]
    assert item["alter_egos"] == [
        "Alexander Rusev",
        "Rusev",
        "Bashing Bulgarian",
        "Miro",
        "Miroslav Makaraov",
        "Miroslav The Bulgarian",
    ]
    assert item["trainers"] == ["Gangrel", "Rikishi"]
    assert item["nicknames"] == [
        "God's Favourite Champion",
        "The Best Man",
        "The Bulgarian Brute",
        "The Redeemer",
        "The Russian Gladiator",
    ]
    assert item["signature_moves"] == ["Accolade (Camel Clutch)", "Game Over", "Machka Kick"]
    assert item["career_start"] == "22.11.2008"
    assert item["career_experience_years"] == 17
    assert item["roles"] == [
        {"role": "Singles Wrestler", "date_ranges": [{"from_date": "2008", "to_date": None}]},
        {"role": "Tag Team Wrestler", "date_ranges": [{"from_date": "2017", "to_date": "2018"}]},
        {
            "role": "WWE Development Wrestler",
            "date_ranges": [{"from_date": "2010", "to_date": "2014"}],
        },
    ]


def test_parse_profile_role_with_multiple_date_ranges(
    wrestler_profile_multirange_html: str,
) -> None:
    spider = WrestlersSpider(Settings())
    selector = Selector(text=wrestler_profile_multirange_html)

    item = spider.parse_profile(selector, {"id": "100", "name": "Lilian Garcia"})

    ring_announcer = next(role for role in item["roles"] if role["role"] == "Ring Announcer")
    assert ring_announcer["date_ranges"] == [
        {"from_date": "1999", "to_date": "2009"},
        {"from_date": "2011", "to_date": "2016"},
        {"from_date": "2024", "to_date": None},
    ]

    interviewer = next(role for role in item["roles"] if role["role"] == "Interviewer")
    assert interviewer["date_ranges"] == []
