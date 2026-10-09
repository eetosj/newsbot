import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from newsbot.articles import ArticleStatus, ContentKind
from newsbot.storage import ArticleStore


class MigrationTests(unittest.TestCase):
    def test_original_records_keep_all_state_and_migration_is_repeatable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            timestamp = "2026-10-09T12:00:00.000000+00:00"
            with sqlite3.connect(path) as connection:
                # Original schema, before content kinds were introduced.
                connection.execute('''CREATE TABLE articles (
                    url TEXT PRIMARY KEY NOT NULL, title TEXT NOT NULL,
                    source TEXT NOT NULL, content TEXT NOT NULL, published_at TEXT,
                    summary TEXT, status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'cooldown', 'ready', 'posted')),
                    posted_at TEXT, cooldown_until TEXT, next_delivery_attempt TEXT,
                    reevaluation_priority INTEGER NOT NULL DEFAULT 0
                    CHECK (reevaluation_priority IN (0,1)),
                    CHECK ((status = 'posted') = (posted_at IS NOT NULL)),
                    CHECK ((status = 'cooldown') = (cooldown_until IS NOT NULL)),
                    CHECK (status NOT IN ('ready', 'posted') OR
                           (summary IS NOT NULL AND length(trim(summary)) > 0)),
                    CHECK (next_delivery_attempt IS NULL OR status = 'ready'))''')
                connection.execute("CREATE INDEX articles_status ON articles(status)")
                for status in ("pending", "cooldown", "ready", "posted"):
                    connection.execute("INSERT INTO articles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (f"https://example.com/{status}", status, "Example", "Saved body", timestamp,
                         "Saved summary" if status in {"ready", "posted"} else None, status,
                         timestamp if status == "posted" else None,
                         "2099-01-01T00:00:00.000000+00:00" if status == "cooldown" else None,
                         timestamp if status == "ready" else None, int(status == "pending")))
            for _ in range(2):
                with ArticleStore(path) as store:
                    for status in ("pending", "cooldown", "ready", "posted"):
                        saved = store.get(f"https://example.com/{status}")
                        self.assertEqual(saved.status, ArticleStatus(status))
                        self.assertEqual(saved.content, "Saved body")
                        self.assertEqual(saved.content_kind, ContentKind.FULL_ARTICLE)
                        self.assertEqual(saved.published_at,
                                         datetime(2026, 10, 9, 12, tzinfo=timezone.utc))
                    self.assertTrue(store.get("https://example.com/pending").reevaluation_priority)
                    self.assertIsNotNone(store.get("https://example.com/cooldown").cooldown_until)
                    self.assertEqual(store.get("https://example.com/ready").summary, "Saved summary")
                    self.assertIsNotNone(store.get("https://example.com/ready").next_delivery_attempt)
                    self.assertIsNotNone(store.get("https://example.com/posted").posted_at)
            with sqlite3.connect(path) as connection:
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
