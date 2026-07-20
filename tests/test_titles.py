from cagematch_scraper.config import Settings
from cagematch_scraper.spiders.sdh.titles import SdhTitlesSpider
from cagematch_scraper.spiders.titles import TitlesSpider


def test_requires_promotion_ids() -> None:
    import pytest

    with pytest.raises(ValueError):
        TitlesSpider(Settings(promotion_ids=""))


def test_should_skip_resume_refreshes_active_titles() -> None:
    spider = TitlesSpider(Settings(promotion_ids="1"))
    existing = {
        "id": "5",
        "name": "WWE Women's Tag Team Title",
        "status": "active",
        "reigns": [{"reign_number": 1}],
    }
    assert spider.should_skip_resume(existing) is False
    assert spider.should_skip_resume(existing, {"status": "active"}) is False


def test_should_skip_resume_skips_inactive_titles() -> None:
    spider = TitlesSpider(Settings(promotion_ids="1"))
    existing = {"id": "99", "status": "inactive", "reigns": []}
    assert spider.should_skip_resume(existing) is True
    assert spider.should_skip_resume(existing, {"status": "inactive"}) is True


def test_should_skip_resume_prefers_listing_status() -> None:
    spider = TitlesSpider(Settings(promotion_ids="1"))
    # Stored as inactive, but tonight's listing says active — refresh.
    existing = {"id": "5", "status": "inactive", "reigns": []}
    assert spider.should_skip_resume(existing, {"status": "active"}) is False
    # Listing says inactive even if stored row claims active — skip.
    existing_active = {"id": "5", "status": "active", "reigns": []}
    assert spider.should_skip_resume(existing_active, {"status": "inactive"}) is True


def test_should_skip_resume_refreshes_unknown_status() -> None:
    spider = TitlesSpider(Settings(promotion_ids="1"))
    existing = {"id": "5", "reigns": []}
    assert spider.should_skip_resume(existing) is False


def test_sdh_titles_never_skip_resume() -> None:
    spider = SdhTitlesSpider(Settings())
    existing = {
        "id": "wwe/wwe-womens-tag-team-championship",
        "reigns": [{"reign_number": 1}],
    }
    assert spider.should_skip_resume(existing) is False
    assert spider.should_skip_resume(existing, existing) is False
