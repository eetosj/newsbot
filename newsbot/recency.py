"""Deterministic publication-age checks shared by collection and evaluation."""

from datetime import datetime, timedelta, timezone

from .articles import ContentKind
from .config import EvaluationConfig


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def exclusion_reason(published_at: datetime | None, kind: ContentKind,
                     config: EvaluationConfig, *, now: datetime) -> str | None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Current time must include a timezone")
    if published_at is None or published_at.tzinfo is None or published_at.utcoffset() is None:
        return "No reliable publication date"
    age = now - published_at
    if age < timedelta(0):
        return "Publication date is in the future"
    days = (config.research_max_age_days if kind == ContentKind.RESEARCH_ABSTRACT
            else config.news_max_age_days)
    if age > timedelta(days=days):
        return f"Publication is older than the configured {days:g}-day limit"
    return None
