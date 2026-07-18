from .base import BaseSpider
from .matches import MatchesSpider
from .promotions import PromotionsSpider
from .sdh import SdhTitlesSpider, SdhWrestlersSpider
from .titles import TitlesSpider
from .wrestlers import WrestlersSpider

SPIDERS: dict[str, type[BaseSpider]] = {
    PromotionsSpider.name: PromotionsSpider,
    WrestlersSpider.name: WrestlersSpider,
    MatchesSpider.name: MatchesSpider,
    TitlesSpider.name: TitlesSpider,
    SdhTitlesSpider.name: SdhTitlesSpider,
    SdhWrestlersSpider.name: SdhWrestlersSpider,
}

__all__ = ["BaseSpider", "SPIDERS"]
