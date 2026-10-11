"""One-pass Discord delivery with confirmed state changes and persistent breaks."""

import hashlib
import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from datetime import timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .articles import Article
from .recency import utc_now
from .storage import ArticleStore


logger = logging.getLogger(__name__)
RETRY_DELAYS = (1, 2, 4, 8, 16)
COOLDOWN_SECONDS = 600
MAX_INLINE_WAIT = 60


class DeliveryError(Exception):
    """Delivery is not confirmed; do not mark the article posted."""


class DeliverySetupError(DeliveryError):
    """Stop delivery until settings, permissions, or message data are fixed."""


class RetryableDeliveryError(DeliveryError):
    def __init__(self, message: str, *, retry_after: float = 0):
        super().__init__(message)
        self.retry_after = retry_after


class RateLimited(RetryableDeliveryError):
    pass


def _snowflake(value) -> bool:
    return (isinstance(value, str) and value.isascii() and value.isdecimal()
            and 0 < len(value) <= 20 and 0 < int(value) < 2**64)


@dataclass(frozen=True)
class DiscordSettings:
    token: str = field(repr=False)
    channel_id: str

    def __post_init__(self):
        if not self.token or any(c.isspace() for c in self.token):
            raise ValueError("DISCORD_BOT_TOKEN must be a nonempty token without whitespace")
        if not _snowflake(self.channel_id):
            raise ValueError("DISCORD_NEWS_CHANNEL_ID must be a positive Discord channel ID")

    @classmethod
    def from_environment(cls):
        return cls(os.environ.get("DISCORD_BOT_TOKEN", ""),
                   os.environ.get("DISCORD_NEWS_CHANNEL_ID", ""))


def format_message(article: Article) -> str:
    parts = [article.title.strip()]
    if article.published_at is not None:
        parts.append(article.published_at.astimezone(timezone.utc).strftime("%Y-%m-%d"))
    if article.summary:
        parts.append(article.summary.strip())
    if article.delivery_notice:
        parts.append(article.delivery_notice)
    parts.append(article.url)
    content = "\n\n".join(part for part in parts if part)
    # Conservative UTF-16 count also covers Discord clients' handling of emoji.
    if len(content.encode("utf-16-le")) // 2 > 2000:
        raise DeliverySetupError("Message exceeds Discord's 2000-character limit; remains queued")
    return content


def _seconds(value) -> float:
    if isinstance(value, bool):
        return 0
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return 0
    return seconds if math.isfinite(seconds) and seconds > 0 else 0


def _rate_wait(headers) -> float:
    if headers.get("X-RateLimit-Remaining") == "0":
        return _seconds(headers.get("X-RateLimit-Reset-After"))
    return 0


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open(request):
    # Do not forward the bot credential if an endpoint returns a redirect.
    return build_opener(_NoRedirect).open(request, timeout=15)


@dataclass(frozen=True)
class SendReceipt:
    message_id: str
    retry_after: float = 0


class DiscordClient:
    def __init__(self, settings: DiscordSettings):
        self.settings = settings

    def send(self, article: Article) -> SendReceipt:
        content = format_message(article)
        # Discord deduplicates matching nonces over a short window, not forever.
        nonce = hashlib.sha256((self.settings.channel_id + "\n" + article.url).encode()).hexdigest()[:24]
        payload = {"content": content, "allowed_mentions": {"parse": []},
                   "nonce": nonce, "enforce_nonce": True}
        request = Request(
            "https://discord.com/api/v10/channels/" + self.settings.channel_id + "/messages",
            data=json.dumps(payload, ensure_ascii=False).encode(), method="POST",
            headers={"Authorization": "Bot " + self.settings.token,
                     "Content-Type": "application/json", "User-Agent": "newsbot (personal bot)"})
        try:
            with _open(request) as response:
                status = response.status
                headers = response.headers
                body = response.read(1_000_001)
        except HTTPError as error:
            if error.code == 429:
                wait = _seconds(error.headers.get("Retry-After"))
                try:
                    data = json.loads(error.read(10000))
                    if isinstance(data, dict):
                        wait = max(wait, _seconds(data.get("retry_after")))
                except (ValueError, UnicodeError, OSError):
                    pass
                finally:
                    error.close()
                # Missing/malformed rate metadata gets a conservative saved break.
                raise RateLimited("Discord rate limit", retry_after=wait or COOLDOWN_SECONDS) from None
            code = error.code
            error.close()
            if code >= 500:
                raise RetryableDeliveryError(f"Discord server error (HTTP {code})") from None
            guidance = {401: "Invalid bot token", 403: "Bot lacks channel permissions",
                        404: "Channel not found or inaccessible"}
            raise DeliverySetupError(guidance.get(code, f"Discord rejected the message (HTTP {code})")) from None
        except (URLError, TimeoutError, OSError):
            # Do not log raw request/error data containing credentials.
            raise RetryableDeliveryError("Discord connection failed") from None
        if status not in (200, 201):
            raise DeliverySetupError("Discord did not return a successful message response")
        if len(body) > 1_000_000:
            raise DeliverySetupError("Discord response exceeded the limit; delivery unconfirmed")
        try:
            data = json.loads(body)
        except (ValueError, UnicodeError):
            raise DeliverySetupError("Discord returned invalid JSON; delivery unconfirmed") from None
        if (not isinstance(data, dict) or not _snowflake(data.get("id"))
                or data.get("channel_id") != self.settings.channel_id):
            raise DeliverySetupError("Discord response lacks a matching message confirmation")
        return SendReceipt(data["id"], _rate_wait(headers))


@dataclass
class DeliveryResult:
    examined: int = 0
    posted: int = 0
    retries: int = 0
    deferred: int = 0
    errors: int = 0
    paused: bool = False


class Delivery:
    def __init__(self, settings: DiscordSettings, store: ArticleStore, *,
                 client=None, sleep=time.sleep, clock=utc_now):
        self.settings = settings
        self.store = store
        self.client = client if client is not None else DiscordClient(settings)
        self.sleep = sleep
        self.clock = clock

    def deliver_ready(self, *, limit: int | None = None) -> DeliveryResult:
        if limit is not None and limit < 1:
            raise ValueError("Delivery limit must be positive")
        result = DeliveryResult()
        until = self.store.delivery_paused_until(self.settings.channel_id, now=self.clock())
        if until:
            logger.info("Discord delivery paused until %s", until.isoformat())
            result.paused = True
            return result
        items = self.store.ready(now=self.clock())
        if limit is not None:
            items = items[:limit]
        for article in items:
            result.examined += 1
            for attempt in range(len(RETRY_DELAYS) + 1):
                try:
                    receipt = self.client.send(article)
                except RetryableDeliveryError as error:
                    logger.warning("Delivery failed for %s (attempt %s/6): %s",
                                   article.url, attempt + 1, error)
                    if attempt == len(RETRY_DELAYS) or error.retry_after > MAX_INLINE_WAIT:
                        wait = (max(error.retry_after, COOLDOWN_SECONDS)
                                if attempt == len(RETRY_DELAYS) else error.retry_after)
                        until = self.clock() + timedelta(seconds=wait)
                        self.store.defer_delivery(article.url, until=until)
                        self.store.pause_delivery(self.settings.channel_id, until=until)
                        result.deferred += 1
                        result.paused = True
                        result.errors += int(not isinstance(error, RateLimited))
                        logger.warning("Discord delivery paused until %s", until.isoformat())
                        return result
                    delay = max(RETRY_DELAYS[attempt], error.retry_after)
                    if isinstance(error, RateLimited):
                        # Persist before sleeping so Ctrl+C cannot bypass Discord's wait.
                        self.store.pause_delivery(self.settings.channel_id,
                                                  until=self.clock() + timedelta(seconds=delay))
                    logger.info("Retrying after %.1f seconds", delay)
                    self.sleep(delay)
                    result.retries += 1
                    continue
                except DeliverySetupError as error:
                    logger.error("Discord delivery stopped; items remain queued: %s", error)
                    result.errors += 1
                    result.paused = True
                    return result
                self.store.mark_posted(article.url, at=self.clock())
                result.posted += 1
                logger.info("Posted %s (Discord message %s)", article.url, receipt.message_id)
                if receipt.retry_after:
                    until = self.clock() + timedelta(seconds=receipt.retry_after)
                    self.store.pause_delivery(self.settings.channel_id, until=until)
                    result.paused = True
                    logger.info("Discord quota exhausted; next delivery after %s", until.isoformat())
                    return result
                break
        return result
