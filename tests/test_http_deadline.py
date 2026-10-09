import json
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from newsbot.articles import Article, ContentKind
from newsbot.collection import Collector, FetchError, FetchResult, HttpFetcher
from newsbot.config import CollectionConfig, FeedSource
from newsbot.storage import ArticleStore


NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


class DeadlineTests(unittest.TestCase):
    def launch_with_fake_http(self, http_code):
        """Run the actual worker, substituting HTTP I/O inside the child only."""
        real_popen = subprocess.Popen
        self.children = []

        def launch(command, **kwargs):
            script = (
                "import runpy, sys, time, urllib.request\n"
                "from urllib.error import URLError\n"
                + http_code + "\n"
                "sys.argv = sys.argv[1:]\n"
                "runpy.run_path(sys.argv[0], run_name='__main__')\n"
            )
            child = real_popen([command[0], "-c", script, *command[1:]], **kwargs)
            self.children.append(child)
            return child

        return patch("newsbot.collection.subprocess.Popen", side_effect=launch)

    def test_stalled_connection_and_body_read_are_cancelled(self):
        scenarios = {
            "connection": """
def fake_open(*args, **kwargs):
    time.sleep(30)
urllib.request.urlopen = fake_open
""",
            "body read": """
class Response:
    url = 'https://example.com/article'
    headers = {'Content-Type': 'text/html'}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, size):
        time.sleep(30)
        return b'eventually'
urllib.request.urlopen = lambda *a, **kw: Response()
""",
        }
        for name, code in scenarios.items():
            with self.subTest(stage=name), self.launch_with_fake_http(code):
                started = time.monotonic()
                with self.assertRaisesRegex(FetchError, "deadline"):
                    HttpFetcher(timeout=0.5)("https://example.com/article")
                self.assertLess(time.monotonic() - started, 3)
                self.assertIsNotNone(self.children[0].poll())

    def test_success_preserves_body_redirect_url_and_content_type(self):
        code = """
class Response:
    url = 'https://example.com/final'
    headers = {'Content-Type': 'text/html; charset=utf-8'}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, size): return 'AI and tekoäly'.encode('utf-8')[:size]
urllib.request.urlopen = lambda *a, **kw: Response()
"""
        with self.launch_with_fake_http(code), self.assertLogs("newsbot.collection", level="INFO") as logs:
            result = HttpFetcher(timeout=3)("https://example.com/start")
        self.assertEqual(result.body, "AI and tekoäly".encode())
        self.assertEqual(result.url, "https://example.com/final")
        self.assertEqual(result.content_type, "text/html; charset=utf-8")
        self.assertTrue(any("Fetching https://example.com/start" in line for line in logs.output))
        self.assertTrue(any("Fetched https://example.com/start in" in line for line in logs.output))
        self.assertIsNotNone(self.children[0].poll())

    def test_worker_http_error_and_size_limit_become_fetch_errors(self):
        scenarios = [
            ("""
def fake_open(*args, **kwargs): raise URLError('Connection refused')
urllib.request.urlopen = fake_open
""", "Connection refused"),
            ("""
class Response:
    url = 'https://example.com/large'
    headers = {}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, size): return b'x' * size
urllib.request.urlopen = lambda *a, **kw: Response()
""", "size limit"),
        ]
        for code, message in scenarios:
            with self.subTest(message=message), self.launch_with_fake_http(code):
                with self.assertRaisesRegex(FetchError, message):
                    HttpFetcher(timeout=3, max_bytes=10)("https://example.com/article")
                self.assertIsNotNone(self.children[0].poll())

    def test_timed_out_article_uses_excerpt_and_preserves_existing_database(self):
        source = FeedSource("Example", "https://example.com/feed")
        feed = b'''<rss version="2.0"><channel><title>Example</title>
          <link>https://example.com</link><description>Example</description>
          <item><title>AI research</title><link>https://example.com/slow</link>
          <description>AI research excerpt</description>
          <pubDate>Fri, 09 Oct 2026 12:00:00 +0000</pubDate></item></channel></rss>'''
        code = """
def fake_open(*args, **kwargs): time.sleep(30)
urllib.request.urlopen = fake_open
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "articles.sqlite3"
            with ArticleStore(path) as store:
                prior = Article("https://example.com/prior", "Prior", "Example", published_at=NOW)
                store.add(prior)
                store.mark_ready(prior.url, "Previously saved summary")
                http = HttpFetcher(timeout=0.5)

                def fetch(url):
                    if url == source.url:
                        return FetchResult(feed, url, "application/rss+xml")
                    return http(url)

                with self.launch_with_fake_http(code), self.assertLogs("newsbot.collection", level="INFO") as logs:
                    result = Collector(CollectionConfig((source,), {"ai": []}), store,
                                       fetch=fetch).collect(now=NOW)
                self.assertEqual((result.stored, result.partial), (1, 1))
                self.assertTrue(any("deadline" in line for line in logs.output))
                self.assertIsNotNone(self.children[0].poll())
            with ArticleStore(path) as store:
                saved = store.get("https://example.com/slow")
                self.assertEqual(saved.content, "AI research excerpt")
                self.assertEqual(saved.content_kind, ContentKind.PARTIAL_ARTICLE)
                self.assertIsNone(saved.summary)
                self.assertEqual(store.get(prior.url).summary, "Previously saved summary")

    def test_interruption_kills_worker_and_preserves_committed_records(self):
        real_popen = subprocess.Popen
        children = []

        def launch(command, **kwargs):
            child = real_popen([command[0], "-c", "import time; time.sleep(30)"], **kwargs)
            children.append(child)
            communicate = child.communicate
            calls = 0

            def interrupt_once(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise KeyboardInterrupt
                return communicate(*args, **kwargs)

            child.communicate = interrupt_once
            return child

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "articles.sqlite3"
            with self.assertRaises(KeyboardInterrupt):
                with ArticleStore(path) as store:
                    store.add(Article("https://example.com/saved", "Saved", "Example"))
                    with patch("newsbot.collection.subprocess.Popen", side_effect=launch):
                        HttpFetcher()("https://example.com/slow")
            self.assertIsNotNone(children[0].poll())
            with ArticleStore(path) as store:
                self.assertIsNotNone(store.get("https://example.com/saved"))


if __name__ == "__main__":
    unittest.main()
