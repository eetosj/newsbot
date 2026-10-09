"""Article data shared by collection, evaluation, and delivery."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ArticleStatus(StrEnum):
    PENDING = "pending"
    COOLDOWN = "cooldown"
    READY = "ready"
    POSTED = "posted"


class ContentKind(StrEnum):
    FULL_ARTICLE = "full_article"
    PARTIAL_ARTICLE = "partial_article"
    RESEARCH_ABSTRACT = "research_abstract"


@dataclass(frozen=True)
class Article:
    url: str
    title: str
    source: str
    content: str = ""
    published_at: datetime | None = None
    summary: str | None = None
    status: ArticleStatus = ArticleStatus.PENDING
    posted_at: datetime | None = None
    cooldown_until: datetime | None = None
    next_delivery_attempt: datetime | None = None
    reevaluation_priority: bool = False
    content_kind: ContentKind = ContentKind.FULL_ARTICLE

    @property
    def delivery_notice(self) -> str | None:
        if self.content_kind == ContentKind.PARTIAL_ARTICLE:
            return "Full article text was not retrieved. Access may be restricted. No AI summary."
        if self.content_kind == ContentKind.RESEARCH_ABSTRACT:
            return "Abstract-based research preview; the full paper was not reviewed."
        return None
