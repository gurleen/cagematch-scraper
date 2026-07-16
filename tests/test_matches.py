import pytest
from parsel import Selector

from cagematch_scraper.config import Settings
from cagematch_scraper.spiders.matches import MatchesSpider


def test_requires_promotion_ids() -> None:
    with pytest.raises(ValueError):
        MatchesSpider(Settings(promotion_ids=""))


def test_start_requests_one_per_promotion_per_year() -> None:
    spider = MatchesSpider(Settings(promotion_ids="1,2287", matches_since_year=2023))
    urls = list(spider.start_requests())

    assert len(urls) == 2 * len(spider.years)
    assert all(u.startswith("https://www.cagematch.net/?id=8&nr=") for u in urls)
    assert all(u.endswith("&s=0") for u in urls)
    assert {int(u.split("vYear=")[1].split("&")[0]) for u in urls} == set(spider.years)
    assert min(spider.years) == 2023


def test_parse_events_list(wwe_events_2020_html: str) -> None:
    spider = MatchesSpider(Settings(promotion_ids="1"))
    selector = Selector(text=wwe_events_2020_html)

    items = list(
        spider.parse(selector, "https://www.cagematch.net/?id=8&nr=1&page=4&vYear=2020&s=0")
    )

    assert len(items) == 100
    ids = [item["id"] for item in items]
    assert len(ids) == len(set(ids))

    raw = next(item for item in items if item["id"] == "297119")
    assert raw["name"] == "WWE Monday Night RAW #1440"
    assert raw["profile_url"] == "https://www.cagematch.net/?id=1&nr=297119"
    assert raw["promotion"] == "1"
    assert raw["date"] == "28.12.2020"
    assert raw["location"] == "St. Petersburg, Florida, USA"
    assert raw["event_rating"] == 6.73
    assert raw["event_votes"] == 25


def test_next_page_url_stops_on_partial_page(wwe_events_2020_html: str) -> None:
    spider = MatchesSpider(Settings(promotion_ids="1"))
    full_selector = Selector(text=wwe_events_2020_html)
    url = "https://www.cagematch.net/?id=8&nr=1&page=4&vYear=2020&s=0"

    assert spider.next_page_url(full_selector, url) == (
        "https://www.cagematch.net/?id=8&nr=1&page=4&vYear=2020&s=100"
    )

    empty_selector = Selector(text="<html><body>no table here</body></html>")
    assert spider.next_page_url(empty_selector, url) is None


def test_parse_profile_decisive_matches_with_valets_and_teams(wwe_event_results_html: str) -> None:
    spider = MatchesSpider(Settings(promotion_ids="1"))
    selector = Selector(text=wwe_event_results_html)

    item = spider.parse_profile(selector, {"id": "297119", "name": "RAW #1440"})
    matches = item["matches"]
    assert len(matches) == 7

    title_match = matches[0]
    assert title_match["match_type"] == "#1 Contendership Match"
    assert title_match["title_id"] == "20"
    assert title_match["title_name"] == "WWE Title"
    assert title_match["duration"] == "12:55"
    assert title_match["result"] == "decisive"
    assert title_match["winners"] == {"wrestlers": [{"id": "13732", "name": "Keith Lee"}]}
    assert title_match["losers"] == [{"wrestlers": [{"id": "2641", "name": "Sheamus"}]}]
    assert title_match["match_rating"] == 6.45
    assert title_match["match_votes"] == 21

    valet_match = matches[1]
    assert valet_match["winners"]["valets"] == [{"id": "4843", "name": "Lince Dorado"}]
    assert valet_match["losers"][0]["valets"] == [{"id": "1099", "name": "John Morrison"}]

    team_match = matches[6]
    assert team_match["match_type"] == "Eight Man Tag Team Match"
    assert team_match["winners"]["teams"] == [{"id": "2747", "name": "The Hurt Business"}]
    assert len(team_match["winners"]["wrestlers"]) == 4
    assert team_match["losers"][0]["teams"] == [{"id": "9622", "name": "The New Day"}]


def test_parse_profile_title_changes_and_elimination_notes(wwe_mania36_results_html: str) -> None:
    spider = MatchesSpider(Settings(promotion_ids="1"))
    selector = Selector(text=wwe_mania36_results_html)

    item = spider.parse_profile(selector, {"id": "225351", "name": "WrestleMania 36"})
    matches = item["matches"]

    title_changes = [m for m in matches if m["title_change"]]
    assert len(title_changes) == 2
    assert {m["title_name"] for m in title_changes} == {
        "WWE NXT Women's Title",
        "WWE Title",
    }
    lesnar_match = next(m for m in title_changes if m["title_name"] == "WWE Title")
    assert lesnar_match["losers"][0]["is_champion"] is True
    assert lesnar_match["losers"][0]["valets"] == [{"id": "664", "name": "Paul Heyman"}]

    elimination_match = next(m for m in matches if m["match_type"] == "Fatal Five Way Elimination Match")
    assert elimination_match["result"] == "decisive"
    assert len(elimination_match["losers"]) == 4
    assert elimination_match["notes"] == [
        "- Naomi, Lacey Evans, Sasha Banks & Bayley eliminate Tamina (6:35)",
        "- Sasha Banks eliminates Naomi (10:20)",
        "- Lacey Evans eliminates Sasha Banks (13:25)",
        "- Bayley eliminates Lacey Evans",
    ]


def test_parse_profile_event_info(wwe_event_results_html: str) -> None:
    spider = MatchesSpider(Settings(promotion_ids="1"))
    selector = Selector(text=wwe_event_results_html)

    item = spider.parse_profile(selector, {"id": "297119", "name": "RAW #1440"})

    assert item["event_type"] == "TV-Show"
    assert item["arena"] == "WWE ThunderDome (Tropicana Field)"
    assert item["broadcast_type"] == "Live"
    assert item["broadcast_date"] == "28.12.2020"
    assert item["tv_network"] == "USA Network"
    assert item["commentators"] == [
        {"id": "5663", "name": "Byron Saxton"},
        {"id": "2879", "name": "Drew McIntyre"},
        {"id": "676", "name": "Samoa Joe"},
        {"id": "14314", "name": "Tom Phillips"},
    ]


def test_parse_profile_event_info_taped_ple(wwe_mania36_results_html: str) -> None:
    spider = MatchesSpider(Settings(promotion_ids="1"))
    selector = Selector(text=wwe_mania36_results_html)

    item = spider.parse_profile(selector, {"id": "225351", "name": "WrestleMania 36"})

    assert item["event_type"] == "Premium Live Event"
    assert item["broadcast_type"] == "Taped"
    assert item["tv_network"] == "WWE Network"


def test_parse_profile_no_decision_result(wwe_dco_event_results_html: str) -> None:
    spider = MatchesSpider(Settings(promotion_ids="1"))
    selector = Selector(text=wwe_dco_event_results_html)

    item = spider.parse_profile(selector, {"id": "352975", "name": "x"})
    dco = next(m for m in item["matches"] if m["result"] == "no_decision")

    assert dco["finish_note"] == "Double Count Out"
    assert dco["duration"] == "8:50"
    assert "winners" not in dco
    assert dco["sides"] == [
        {"wrestlers": [{"id": "16664", "name": "Liv Morgan"}]},
        {"wrestlers": [{"id": "17658", "name": "Sonya Deville"}]},
    ]
