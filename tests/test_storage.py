import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from newsbot.articles import Article, ArticleStatus, ContentKind
from newsbot.storage import ArticleStore


NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


def article(name: str) -> Article:
    return Article(f"https://example.com/{name}", name, "Example",
                   content=f"Full text of {name}", published_at=NOW)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "articles.sqlite3"
        self.store = ArticleStore(self.path)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def reopen(self):
        self.store.close()
        self.store = ArticleStore(self.path)

    def test_metadata_and_queued_summary_survive_restart(self):
        item = article("queued")
        self.assertTrue(self.store.add(item))
        self.store.mark_ready(item.url, "Saved summary")
        self.reopen()
        saved = self.store.get(item.url)
        self.assertEqual(saved.content, item.content)
        self.assertEqual(saved.title, item.title)
        self.assertEqual(saved.source, item.source)
        self.assertEqual(saved.published_at, NOW)
        self.assertEqual(saved.summary, "Saved summary")
        self.assertEqual(saved.status, ArticleStatus.READY)
        self.assertIsNone(saved.posted_at)
        self.assertEqual(self.store.ready(now=NOW), [saved])

    def test_duplicate_collection_preserves_posted_state(self):
        item = article("posted")
        self.store.add(item)
        self.store.mark_ready(item.url, "Summary")
        self.store.mark_posted(item.url, at=NOW)
        self.reopen()
        self.assertFalse(self.store.add(item))
        self.assertEqual(self.store.get(item.url).posted_at, NOW)
        self.assertEqual(self.store.ready(now=NOW), [])
        self.assertEqual(self.store.pending(now=NOW), [])
        with self.assertRaises(ValueError):
            self.store.mark_ready(item.url, "Another summary")

    def test_rejection_stays_on_cooldown_until_expiration(self):
        item = article("rejected")
        self.store.add(item)
        self.store.reject(item.url, until=NOW + timedelta(hours=1))
        self.reopen()
        self.assertEqual(self.store.pending(now=NOW), [])
        pending = self.store.pending(now=NOW + timedelta(hours=1))
        self.assertEqual([a.url for a in pending], [item.url])
        self.assertIsNone(pending[0].cooldown_until)
        self.assertEqual(pending[0].content, item.content)

    def test_preference_change_prioritizes_rejections_and_preserves_other_states(self):
        for name in ("new", "rejected", "queued", "posted"):
            self.store.add(article(name))
        self.store.reject(article("rejected").url, until=NOW + timedelta(days=1))
        for name in ("queued", "posted"):
            self.store.mark_ready(article(name).url, "Summary")
        self.store.mark_posted(article("posted").url, at=NOW)
        self.assertEqual(self.store.reset_cooldowns(), 1)
        self.reopen()
        self.assertEqual([a.title for a in self.store.pending(now=NOW)],
                         ["rejected", "new"])
        self.assertEqual(self.store.get(article("queued").url).status, ArticleStatus.READY)
        self.assertEqual(self.store.get(article("posted").url).status, ArticleStatus.POSTED)
        self.assertEqual(self.store.reset_cooldowns(), 0)

    def test_failed_send_remains_queued_until_retry_and_success(self):
        item = article("retry")
        self.store.add(item)
        self.store.mark_ready(item.url, "Keep this summary")
        retry_at = NOW + timedelta(minutes=5)
        self.store.defer_delivery(item.url, until=retry_at)
        self.reopen()
        self.assertEqual(self.store.ready(now=NOW), [])
        saved = self.store.get(item.url)
        self.assertEqual(saved.status, ArticleStatus.READY)
        self.assertIsNone(saved.posted_at)
        self.assertEqual(saved.summary, "Keep this summary")
        self.assertEqual(len(self.store.ready(now=retry_at)), 1)
        self.store.mark_posted(item.url, at=retry_at)
        self.assertIsNone(self.store.get(item.url).next_delivery_attempt)
        self.assertEqual(self.store.ready(now=retry_at), [])

    def test_invalid_transitions_do_not_change_records(self):
        item = article("pending")
        self.store.add(item)
        with self.assertRaises(ValueError):
            self.store.mark_posted(item.url, at=NOW)
        with self.assertRaises(ValueError):
            self.store.mark_ready(item.url, " ")
        with self.assertRaises(KeyError):
            self.store.mark_ready("missing", "Summary")
        self.assertEqual(self.store.get(item.url), item)

    def test_timezone_conversion_and_naive_timestamp_rejection(self):
        item = article("timezone")
        self.store.add(item)
        local = NOW.astimezone(timezone(timedelta(hours=3)))
        self.store.reject(item.url, until=local)
        self.assertEqual(len(self.store.pending(now=NOW)), 1)
        with self.assertRaises(ValueError):
            self.store.reject(item.url, until=datetime(2026, 10, 10))
        self.assertEqual(self.store.get(item.url).status, ArticleStatus.PENDING)

    def test_partial_content_notice_and_summary_free_queue_survive_restart(self):
        item = Article("https://example.com/partial", "AI news", "Example",
                       content="An excerpt", content_kind=ContentKind.PARTIAL_ARTICLE)
        self.store.add(item)
        self.store.mark_ready(item.url)
        self.reopen()
        saved = self.store.ready(now=NOW)[0]
        self.assertIsNone(saved.summary)
        self.assertIn("Access may be restricted", saved.delivery_notice)
        self.store.mark_posted(item.url, at=NOW)
        self.reopen()
        self.assertEqual(self.store.get(item.url).status, ArticleStatus.POSTED)
        self.assertEqual(self.store.get(item.url).content_kind, ContentKind.PARTIAL_ARTICLE)


if __name__ == "__main__":
    unittest.main()
