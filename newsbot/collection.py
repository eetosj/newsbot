"""One collection pass: feeds → article text/abstract → keywords → pending storage."""

import calendar
import base64
import json
import logging
import math
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode

import feedparser
import trafilatura

from .articles import Article, ContentKind
from .config import CollectionConfig, FeedSource, HttpConfig, http_url
from .keywords import KeywordMatcher
from .recency import exclusion_reason, utc_now
from .storage import ArticleStore


logger = logging.getLogger(__name__)


class FetchError(Exception):
    """An HTTP resource could not be retrieved for this collection pass."""


@dataclass(frozen=True)
class FetchResult:
    body: bytes
    url: str
    content_type: str


class HttpFetcher:
    """Whole-fetch deadlines with cancellable workers and per-host spacing.

    A worker process bounds DNS, all connection attempts, redirects, and body
    reads together. Socket timeouts alone cannot enforce that deadline. Host
    spacing happens before the deadline; parsing/extraction happen afterward.
    """

    def __init__(self, *, timeout: float = 15, max_bytes: int = 5_000_000,
                 address_family: str = "ipv4"):
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("HTTP deadline must be positive and finite")
        if type(max_bytes) is not int or max_bytes < 1:
            raise ValueError("HTTP size limit must be a positive integer")
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.address_family = HttpConfig(address_family).address_family
        self._last_request: dict[str, float] = {}

    def __call__(self, url: str) -> FetchResult:
        if not http_url(url):
            raise FetchError("Only HTTP(S) resources are supported")
        host = urlsplit(url).hostname or ""
        gap = 3.0 if host == "arxiv.org" or host.endswith(".arxiv.org") else 0.5
        remaining = self._last_request.get(host, 0) + gap - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        started = time.monotonic()
        self._last_request[host] = started
        logger.info("Fetching %s (%s; %.1fs deadline)", url, self.address_family, self.timeout)
        process = None
        try:
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).with_name("_fetch_worker.py")),
                 url, str(self.timeout), str(self.max_bytes), self.address_family],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            remaining = self.timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise FetchError(f"HTTP fetch exceeded its {self.timeout:g}s deadline")
            output, error_output = process.communicate(timeout=remaining)
            if process.returncode:
                detail = error_output.decode("utf-8", errors="replace").strip()
                raise FetchError(detail or f"HTTP worker exited with code {process.returncode}")
            result = json.loads(output)
            body = base64.b64decode(result["body"], validate=True)
            if len(body) > self.max_bytes:
                raise FetchError("Resource exceeds the collection size limit")
            logger.info("Fetched %s in %.1fs (%d bytes)", url, time.monotonic() - started, len(body))
            return FetchResult(body, result["url"], result["content_type"])
        except subprocess.TimeoutExpired as error:
            raise FetchError(f"HTTP fetch exceeded its {self.timeout:g}s deadline") from error
        except (OSError, ValueError, KeyError) as error:
            raise FetchError(str(error)) from error
        except FetchError:
            raise
        finally:
            # Also runs on Ctrl+C: do not leave a blocked HTTP worker behind.
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate()
            if process is not None:
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()
            logger.info("Fetch ended for %s after %.1fs", url, time.monotonic() - started)


class _TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"p", "br", "div", "li"} and not self.hidden:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1
        if not self.hidden:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(html: str) -> str:
    parser = _TextParser()
    parser.feed(html)
    return " ".join("".join(parser.parts).split())


def canonical_url(url: str) -> str:
    """Remove fragments and common tracking parameters; preserve semantic queries."""
    parsed = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if not key.casefold().startswith("utm_") and key.casefold() not in {"fbclid", "gclid"}]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def _restricted_html(html: str) -> bool:
    # Detect declared paid content before extraction, even if HTML contains text.
    for script in re.findall(r"<script\b[^>]*>(.*?)</script\s*>", html, re.I | re.S):
        try:
            data = json.loads(script)
        except (ValueError, TypeError):
            continue

        def restricted(value):
            if isinstance(value, dict):
                free = value.get("isAccessibleForFree")
                if free is False or (isinstance(free, str) and free.casefold() == "false"):
                    return True
                return any(restricted(child) for child in value.values())
            if isinstance(value, list):
                return any(restricted(child) for child in value)
            return False

        if restricted(data):
            return True
    return bool(re.search(
        r"subscribe to (?:continue|keep) reading|subscription required to read|"
        r"tilaa lukeaksesi|kirjaudu lukeaksesi|vain tilaajille", plain_text(html), re.I
    ))


def extract_article(response: FetchResult) -> str | None:
    """Best-effort main-body extraction; never treat a declared paywall as full text."""
    if response.content_type and not any(
        value in response.content_type.casefold() for value in ("text/html", "application/xhtml+xml")
    ):
        return None
    html = trafilatura.utils.decode_file(response.body)
    if _restricted_html(html):
        return None
    text = trafilatura.extract(html, url=response.url, include_comments=False,
                               include_tables=True)
    # A short teaser is not enough evidence that the article body was retrieved.
    return text if text and len(text.strip()) >= 200 else None


@dataclass
class CollectionResult:
    discovered: int = 0
    stored: int = 0
    duplicates: int = 0
    unmatched: int = 0
    partial: int = 0
    abstracts: int = 0
    source_errors: int = 0
    date_excluded: int = 0
    deferred_for_reevaluation: bool = False


class Collector:
    def __init__(self, config: CollectionConfig, store: ArticleStore, *,
                 fetch: Callable[[str], FetchResult] | None = None,
                 extract: Callable[[FetchResult], str | None] = extract_article):
        self.config = config
        self.store = store
        self.matcher = KeywordMatcher(config.keywords)
        self.fetch = fetch or HttpFetcher(address_family=config.http.address_family)
        self.extract = extract

    def collect(self, *, limit_per_source: int | None = None,
                now: datetime | None = None) -> CollectionResult:
        if limit_per_source is not None and limit_per_source < 1:
            raise ValueError("limit_per_source must be positive")
        result = CollectionResult()
        now = now or utc_now()
        if any(item.reevaluation_priority for item in self.store.pending(now=now)):
            # Evaluation will later drain these before new feeds are taken in.
            result.deferred_for_reevaluation = True
            logger.info("Collection deferred: priority reevaluations must be processed first")
            return result
        for source in self.config.sources:
            if not source.enabled:
                continue
            started = time.monotonic()
            stored_before = result.stored
            logger.info("Reading feed %s: %s", source.name, source.url)
            try:
                response = self.fetch(source.url)
                feed = feedparser.parse(response.body, response_headers={
                    "content-location": response.url, "content-type": response.content_type,
                })
                if not feed.version:
                    raise FetchError("Response is not a supported RSS/Atom feed")
                if feed.bozo:
                    logger.warning("Feed %s has parsing issues: %s", source.name,
                                   feed.get("bozo_exception"))
                entries = feed.entries if limit_per_source is None else feed.entries[:limit_per_source]
                logger.info("Feed %s: checking %d of %d items", source.name, len(entries), len(feed.entries))
                for entry in entries:
                    self._collect_entry(source, entry, response.url, result, now)
            except (FetchError, ValueError) as error:
                result.source_errors += 1
                logger.warning("Source %s failed: %s", source.name, error)
            logger.info("Feed %s finished in %.1fs; %d candidates stored", source.name,
                        time.monotonic() - started, result.stored - stored_before)
        return result

    def _collect_entry(self, source: FeedSource, entry, feed_url: str,
                       result: CollectionResult, now: datetime) -> None:
        result.discovered += 1
        url = canonical_url(urljoin(feed_url, entry.get("link", "")))
        title = plain_text(entry.get("title", ""))
        if not entry.get("link") or not title or not http_url(url):
            logger.warning("Skipping malformed item from %s", source.name)
            return
        if self.store.get(url) is not None:
            result.duplicates += 1
            return
        # Publication date only: an updated timestamp does not prove a new item.
        date = entry.get("published_parsed")
        published_at = None
        if date:
            try:
                published_at = datetime.fromtimestamp(calendar.timegm(date), timezone.utc)
            except (ValueError, OverflowError, OSError):
                pass
        initial_kind = (ContentKind.RESEARCH_ABSTRACT if source.kind == "research"
                        else ContentKind.FULL_ARTICLE)
        reason = exclusion_reason(published_at, initial_kind, self.config.evaluation, now=now)
        if reason:
            result.date_excluded += 1
            logger.debug("Excluded %s: %s", url, reason)
            return
        excerpt = plain_text(entry.get("summary", ""))
        if source.kind == "research":
            # Current arXiv RSS descriptions prefix the abstract with metadata.
            abstract = re.split(r"\bAbstract:\s*", excerpt, maxsplit=1)
            content = abstract[-1].strip()
            if not content:
                logger.warning("Skipping research item without an abstract: %s", url)
                return
            kind = ContentKind.RESEARCH_ABSTRACT
        else:
            try:
                content = self.extract(self.fetch(url))
            except (FetchError, ValueError) as error:
                logger.warning("Article retrieval failed for %s: %s", url, error)
                content = None
            kind = ContentKind.FULL_ARTICLE if content else ContentKind.PARTIAL_ARTICLE
            content = content or excerpt
        if not self.matcher.match(f"{title}\n{content}"):
            result.unmatched += 1
            return
        item = Article(url=url, title=title, source=source.name, content=content,
                       published_at=published_at, content_kind=kind)
        if self.store.add(item):
            result.stored += 1
            result.partial += int(kind == ContentKind.PARTIAL_ARTICLE)
            result.abstracts += int(kind == ContentKind.RESEARCH_ABSTRACT)
        else:
            result.duplicates += 1
