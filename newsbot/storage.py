"""Persistent article state. External API calls belong outside transactions."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType

from .articles import Article, ArticleStatus, ContentKind


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamps must include a timezone")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _now(value: datetime | None) -> str:
    return str(_timestamp(value or datetime.now(timezone.utc)))


def _article(row: sqlite3.Row) -> Article:
    data = dict(row)
    for field in ("published_at", "posted_at", "cooldown_until", "next_delivery_attempt"):
        data[field] = datetime.fromisoformat(data[field]) if data[field] else None
    data["status"] = ArticleStatus(data["status"])
    data["content_kind"] = ContentKind(data["content_kind"])
    data["reevaluation_priority"] = bool(data["reevaluation_priority"])
    data["summary_omitted"] = bool(data["summary_omitted"])
    return Article(**data)


class ArticleStore:
    """A single local SQLite connection; use it from one thread at a time.

    All writes commit before returning. URLs are exact identifiers: collectors
    should normalize URLs before insertion if their feeds use tracking variants.
    """

    def __init__(self, path: str | Path):
        self._connection = sqlite3.connect(str(path), timeout=5)
        self._connection.row_factory = sqlite3.Row
        try:
            self._initialize()
        except Exception:
            self._connection.close()
            raise

    def _initialize(self) -> None:
        # An explicit transaction makes the table rebuild atomic, including DDL.
        self._connection.execute("BEGIN IMMEDIATE")
        with self._connection:
            version = self._connection.execute("PRAGMA user_version").fetchone()[0]
            if version > 3:
                raise ValueError("Database schema is newer than this application")
            exists = self._connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'articles'"
            ).fetchone()
            columns = {row["name"] for row in self._connection.execute(
                "PRAGMA table_info(articles)"
            )}
            if exists and "summary_omitted" not in columns:
                self._create_table("articles_v3")
                names = ("url, title, source, content, published_at, summary, status, "
                         "posted_at, cooldown_until, next_delivery_attempt, reevaluation_priority")
                if "content_kind" in columns:
                    names += ", content_kind"
                self._connection.execute(
                    f"INSERT INTO articles_v3 ({names}) SELECT {names} FROM articles ORDER BY rowid"
                )
                self._connection.execute("DROP TABLE articles")
                self._connection.execute("ALTER TABLE articles_v3 RENAME TO articles")
            elif not exists:
                self._create_table("articles")
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS articles_status ON articles(status)"
            )
            self._connection.execute("PRAGMA user_version = 3")
            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS delivery_cooldowns (
                    channel_id TEXT PRIMARY KEY NOT NULL,
                    next_attempt TEXT NOT NULL
                )
            """)

    def _create_table(self, name: str) -> None:
        # Names are fixed internal constants, never supplied by configuration.
        self._connection.execute(f"""
                CREATE TABLE {name} (
                    url TEXT PRIMARY KEY NOT NULL,
                    title TEXT NOT NULL,
                    source TEXT NOT NULL,
                    content TEXT NOT NULL,
                    published_at TEXT,
                    summary TEXT,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'cooldown', 'ready', 'posted')),
                    posted_at TEXT,
                    cooldown_until TEXT,
                    next_delivery_attempt TEXT,
                    reevaluation_priority INTEGER NOT NULL DEFAULT 0
                        CHECK (reevaluation_priority IN (0, 1)),
                    content_kind TEXT NOT NULL DEFAULT 'full_article'
                        CHECK (content_kind IN ('full_article', 'partial_article', 'research_abstract')),
                    summary_omitted INTEGER NOT NULL DEFAULT 0 CHECK (summary_omitted IN (0, 1)),
                    CHECK (summary_omitted = 0 OR (summary IS NULL
                           AND content_kind != 'partial_article' AND status IN ('ready', 'posted'))),
                    CHECK ((status = 'posted') = (posted_at IS NOT NULL)),
                    CHECK ((status = 'cooldown') = (cooldown_until IS NOT NULL)),
                    CHECK (content_kind != 'partial_article' OR summary IS NULL),
                    CHECK (status NOT IN ('ready', 'posted') OR content_kind = 'partial_article'
                           OR summary_omitted = 1
                           OR (summary IS NOT NULL AND length(trim(summary)) > 0)),
                    CHECK (next_delivery_attempt IS NULL OR status = 'ready')
                )
            """)

    def __enter__(self) -> "ArticleStore":
        return self

    def __exit__(self, exc_type: type[BaseException] | None,
                 exc: BaseException | None, traceback: TracebackType | None) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def add(self, article: Article) -> bool:
        """Insert a new pending article. Existing URLs keep their current state."""
        if not all(value.strip() for value in (article.url, article.title, article.source)):
            raise ValueError("URL, title, and source must not be blank")
        if (article.status != ArticleStatus.PENDING or article.summary is not None
                or article.posted_at is not None or article.cooldown_until is not None
                or article.next_delivery_attempt is not None or article.reevaluation_priority
                or article.summary_omitted):
            raise ValueError("New articles must be pending without processing state")
        with self._connection:
            cursor = self._connection.execute("""
                INSERT INTO articles (url, title, source, content, published_at, content_kind)
                VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(url) DO NOTHING
            """, (article.url, article.title, article.source, article.content,
                  _timestamp(article.published_at), ContentKind(article.content_kind).value))
        return cursor.rowcount == 1

    def get(self, url: str) -> Article | None:
        row = self._connection.execute(
            "SELECT * FROM articles WHERE url = ?", (url,)
        ).fetchone()
        return _article(row) if row else None

    def pending(self, *, now: datetime | None = None) -> list[Article]:
        """Release expired cooldowns, then return reevaluations before new items.

        The future collector must drain priority reevaluations before collecting
        new content to enforce the preference-change ordering requirement.
        """
        with self._connection:
            self._connection.execute("""
                UPDATE articles SET status = 'pending', cooldown_until = NULL,
                    reevaluation_priority = 1
                WHERE status = 'cooldown' AND cooldown_until <= ?
            """, (_now(now),))
        return self._select("""
            SELECT * FROM articles WHERE status = 'pending'
            ORDER BY reevaluation_priority DESC, rowid
        """)

    def reset_cooldowns(self) -> int:
        """Call when preferences change; posted and ready articles stay intact."""
        with self._connection:
            cursor = self._connection.execute("""
                UPDATE articles SET status = 'pending', cooldown_until = NULL,
                    reevaluation_priority = 1
                WHERE status = 'cooldown'
            """)
        return cursor.rowcount

    def reject(self, url: str, *, until: datetime) -> None:
        self._transition("""
            UPDATE articles SET status = 'cooldown', cooldown_until = ?,
                reevaluation_priority = 0
            WHERE url = ? AND status = 'pending'
        """, (_timestamp(until), url), url, ArticleStatus.PENDING)

    def mark_ready(self, url: str, summary: str | None = None, *,
                   summary_omitted: bool = False) -> None:
        """Queue a summary, partial news, or an explicit verification fallback."""
        if type(summary_omitted) is not bool:
            raise ValueError("summary_omitted must be a boolean")
        item = self.get(url)
        if item is None:
            raise KeyError(url)
        if item.content_kind == ContentKind.PARTIAL_ARTICLE:
            if summary is not None or summary_omitted:
                raise ValueError("Partial news must not have an AI summary")
        elif summary_omitted:
            if summary is not None:
                raise ValueError("An omitted summary must be null")
        elif summary is None or not summary.strip():
            raise ValueError("Full articles and abstract previews require a summary")
        self._transition("""
            UPDATE articles SET status = 'ready', summary = ?, summary_omitted = ?,
                reevaluation_priority = 0
            WHERE url = ? AND status = 'pending'
        """, (summary, int(summary_omitted), url), url, ArticleStatus.PENDING)

    def ready(self, *, now: datetime | None = None) -> list[Article]:
        return self._select("""
            SELECT * FROM articles WHERE status = 'ready'
                AND (next_delivery_attempt IS NULL OR next_delivery_attempt <= ?)
            ORDER BY rowid
        """, (_now(now),))

    def defer_delivery(self, url: str, *, until: datetime) -> None:
        """Keep the summary queued after a failed send; do not mark it posted."""
        self._transition("""
            UPDATE articles SET next_delivery_attempt = ?
            WHERE url = ? AND status = 'ready'
        """, (_timestamp(until), url), url, ArticleStatus.READY)

    def pause_delivery(self, channel_id: str, *, until: datetime) -> None:
        """Persist a channel-wide break, including for newly queued articles."""
        with self._connection:
            self._connection.execute("""
                INSERT INTO delivery_cooldowns VALUES (?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    next_attempt = max(next_attempt, excluded.next_attempt)
            """, (channel_id, _timestamp(until)))

    def delivery_paused_until(self, channel_id: str, *, now: datetime | None = None) -> datetime | None:
        row = self._connection.execute(
            "SELECT next_attempt FROM delivery_cooldowns WHERE channel_id = ? AND next_attempt > ?",
            (channel_id, _now(now)),
        ).fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    def mark_posted(self, url: str, *, at: datetime | None = None) -> None:
        """Caller must invoke this only after Discord confirms successful delivery."""
        self._transition("""
            UPDATE articles SET status = 'posted', posted_at = ?,
                next_delivery_attempt = NULL
            WHERE url = ? AND status = 'ready'
        """, (_now(at), url), url, ArticleStatus.READY)

    def _select(self, sql: str, parameters: tuple = ()) -> list[Article]:
        return [_article(row) for row in self._connection.execute(sql, parameters)]

    def _transition(self, sql: str, parameters: tuple, url: str,
                    expected: ArticleStatus) -> None:
        with self._connection:
            cursor = self._connection.execute(sql, parameters)
            if cursor.rowcount != 1:
                if self.get(url) is None:
                    raise KeyError(url)
                raise ValueError(f"Article must be {expected.value} for this operation")
