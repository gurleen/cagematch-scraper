from parsel import Selector

from cagematch_scraper.spiders.promotions import PromotionsSpider


def test_parse_promotions_list(promotions_list_html: str) -> None:
    spider = PromotionsSpider()
    selector = Selector(text=promotions_list_html)

    items = list(spider.parse(selector, "https://www.cagematch.net/?id=8&page=4"))

    assert len(items) == 3
    names = {item["name"] for item in items}
    assert names == {
        "World Wrestling Entertainment",
        "All Elite Wrestling",
        "New Japan Pro-Wrestling",
    }

    wwe = next(item for item in items if item["id"] == "1")
    assert wwe["name"] == "World Wrestling Entertainment"
    assert wwe["profile_url"] == "https://www.cagematch.net/?id=8&nr=1"
    assert wwe["location"] == "United States"
    assert wwe["status"] == "Active"
