import pytest
from parsel import Selector

from cagematch_scraper.config import Settings
from cagematch_scraper.spiders.wrestlers import WrestlersSpider


def test_requires_promotion_ids() -> None:
    with pytest.raises(ValueError):
        WrestlersSpider(Settings(promotion_ids=""))


def test_start_requests_roster_and_all_time_per_promotion() -> None:
    spider = WrestlersSpider(Settings(promotion_ids="1,2287"))
    urls = list(spider.start_requests())
    assert urls == [
        "https://www.cagematch.net/?id=8&nr=1&page=15",
        "https://www.cagematch.net/?id=8&nr=2287&page=15",
        "https://www.cagematch.net/?id=8&nr=1&page=16",
        "https://www.cagematch.net/?id=8&nr=2287&page=16",
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


def test_parse_all_time_roster_has_show_count_not_roles(wwe_all_time_roster_html: str) -> None:
    spider = WrestlersSpider(Settings(promotion_ids="1"))
    selector = Selector(text=wwe_all_time_roster_html)

    items = list(spider.parse(selector, "https://www.cagematch.net/?id=8&nr=1&page=16"))

    # Tag-team/stable entries (id=28/id=29 links) aren't individual wrestlers and are
    # excluded since only id=2 links are followed.
    assert all(item["name"] not in ("Bloodline", "Alpha Academy", "Bella Twins") for item in items)

    uncle_howdy = next(item for item in items if item["id"] == "7311")
    assert uncle_howdy["name"] == "Uncle Howdy"
    assert uncle_howdy["career_shows"] == 17
    assert "active_roles" not in uncle_howdy
    assert "roster_rating" not in uncle_howdy


def test_all_time_roster_only_adds_wrestlers_missing_from_roster(
    wwe_roster_html: str, wwe_all_time_roster_html: str
) -> None:
    spider = WrestlersSpider(Settings(promotion_ids="1"))
    roster_items = list(
        spider.parse(Selector(text=wwe_roster_html), "https://www.cagematch.net/?id=8&nr=1&page=15")
    )
    all_time_items = list(
        spider.parse(
            Selector(text=wwe_all_time_roster_html),
            "https://www.cagematch.net/?id=8&nr=1&page=16",
        )
    )

    roster_ids = {item["id"] for item in roster_items}
    all_time_ids = {item["id"] for item in all_time_items}

    # The spider's own _seen dedup means all_time_items never re-includes a roster id.
    assert roster_ids.isdisjoint(all_time_ids)
    assert len(all_time_items) > 0
    # Roster-only fields carry roles/rating; All-Time-only entries carry career_shows.
    assert all("career_shows" in item for item in all_time_items)


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
