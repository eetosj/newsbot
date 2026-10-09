"""Editable JSON configuration for feeds and keyword groups."""

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


def http_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not parsed.username


@dataclass(frozen=True)
class FeedSource:
    name: str
    url: str
    kind: str = "news"
    enabled: bool = True

    def __post_init__(self):
        if not self.name.strip() or not http_url(self.url):
            raise ValueError("Each source needs a name and an HTTP(S) feed URL")
        if self.kind not in {"news", "research"}:
            raise ValueError("Source kind must be news or research")
        if not isinstance(self.enabled, bool):
            raise ValueError("Source enabled must be a boolean")


def _positive_number(value: float, name: str) -> None:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value <= 0 or value > 36500):
        raise ValueError(f"{name} must be a positive finite number no greater than 36500")


@dataclass(frozen=True)
class EvaluationConfig:
    news_max_age_days: float = 3
    research_max_age_days: float = 14
    rejection_cooldown_hours: float = 24

    def __post_init__(self):
        for name in ("news_max_age_days", "research_max_age_days", "rejection_cooldown_hours"):
            _positive_number(getattr(self, name), name)


@dataclass(frozen=True)
class OllamaConfig:
    model: str | None = None
    base_url: str = "http://localhost:11434"
    timeout_seconds: float = 120
    context_tokens: int = 16384

    def __post_init__(self):
        if self.model is not None and (not isinstance(self.model, str) or not self.model.strip()):
            raise ValueError("Ollama model must be a nonempty string, or null until selected")
        if not isinstance(self.base_url, str) or not http_url(self.base_url):
            raise ValueError("Ollama base_url must be an HTTP(S) URL")
        parsed = urlsplit(self.base_url)
        if parsed.query or parsed.fragment:
            raise ValueError("Ollama base_url must not include a query or fragment")
        _positive_number(self.timeout_seconds, "timeout_seconds")
        if type(self.context_tokens) is not int or not 4096 <= self.context_tokens <= 1_048_576:
            raise ValueError("context_tokens must be an integer between 4096 and 1048576")


@dataclass(frozen=True)
class HttpConfig:
    address_family: str = "ipv4"

    def __post_init__(self):
        if self.address_family not in {"ipv4", "auto"}:
            raise ValueError("http.address_family must be ipv4 or auto")


@dataclass(frozen=True)
class CollectionConfig:
    sources: tuple[FeedSource, ...]
    keywords: dict[str, list[str]]
    evaluation: EvaluationConfig = field(default_factory=EvaluationConfig)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    http: HttpConfig = field(default_factory=HttpConfig)


def load_config(path: str | Path) -> CollectionConfig:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Configuration must be a JSON object")
    keywords = data.get("keywords")
    if not isinstance(keywords, dict) or not keywords:
        raise ValueError("keywords must be a nonempty dictionary")
    for key, synonyms in keywords.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Keyword keys must be nonempty strings")
        if (not isinstance(synonyms, list)
                or any(not isinstance(term, str) or not term.strip() for term in synonyms)):
            raise ValueError("Each keyword must map to a list of nonempty synonyms")
    sources = data.get("sources")
    if not isinstance(sources, list):
        raise ValueError("sources must be a list")
    if any(not isinstance(source, dict) for source in sources):
        raise ValueError("Each source must be an object")
    evaluation = data.get("evaluation", {})
    ollama = data.get("ollama", {})
    http = data.get("http", {})
    if not all(isinstance(section, dict) for section in (evaluation, ollama, http)):
        raise ValueError("evaluation, ollama, and http must be objects")
    return CollectionConfig(tuple(FeedSource(**source) for source in sources), keywords,
                            EvaluationConfig(**evaluation), OllamaConfig(**ollama), HttpConfig(**http))
