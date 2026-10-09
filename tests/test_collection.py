import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from newsbot.articles import Article, ArticleStatus, ContentKind
from newsbot.collection import Collector, FetchError, FetchResult, extract_article
from newsbot.config import CollectionConfig, FeedSource, load_config
from newsbot.keywords import KeywordMatcher
from newsbot.storage import ArticleStore


NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


def rss(*items):
    return ('<?xml version="1.0"?><rss version="2.0"><channel>'
            '<title>Example</title><link>https://example.com</link><description>News</description>'
            + ''.join(items) + '</channel></rss>').encode()


def item(name, description="An ordinary excerpt", title=None):
    return (f'<item><title>{title or name}</title><link>https://example.com/{name}</link>'
            f'<description><![CDATA[{description}]]></description>'
            '<pubDate>Fri, 09 Oct 2026 12:00:00 +0300</pubDate></item>')


class FakeFetcher:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


def response(body, url="https://example.com/feed", kind="application/rss+xml"):
    return FetchResult(body, url, kind)


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.store = ArticleStore(":memory:")
        self.source = FeedSource("Example", "https://example.com/feed")
        self.config = CollectionConfig((self.source,), {"ai": ["artificial intelligence", "tekoäly"]})

    def tearDown(self):
        self.store.close()

    def test_full_body_match_stored_and_not_fetched_again(self):
        fetch = FakeFetcher({self.source.url: response(rss(item("body"))),
                             "https://example.com/body": response(b"html", kind="text/html")})
        collector = Collector(self.config, self.store, fetch=fetch,
                              extract=lambda r: "The body discusses artificial intelligence.")
        result = collector.collect(now=NOW)
        saved = self.store.get("https://example.com/body")
        self.assertEqual(result.stored, 1)
        self.assertEqual(saved.content_kind, ContentKind.FULL_ARTICLE)
        self.assertEqual(saved.status, ArticleStatus.PENDING)
        self.assertIsNone(saved.summary)
        self.assertEqual(saved.published_at, datetime(2026, 10, 9, 9, tzinfo=timezone.utc))
        self.assertEqual(collector.collect(now=NOW).duplicates, 1)
        self.assertEqual(fetch.calls.count(saved.url), 1)

    def test_unavailable_body_matches_excerpt_and_allows_summary_free_delivery(self):
        fetch = FakeFetcher({self.source.url: response(rss(item("partial", "AI in medicine"))),
                             "https://example.com/partial": FetchError("403 Forbidden")})
        collector = Collector(self.config, self.store, fetch=fetch)
        with self.assertLogs("newsbot.collection", level="WARNING"):
            result = collector.collect(now=NOW)
        saved = self.store.get("https://example.com/partial")
        self.assertEqual(result.partial, 1)
        self.assertEqual(saved.content, "AI in medicine")
        self.assertEqual(saved.content_kind, ContentKind.PARTIAL_ARTICLE)
        self.store.mark_ready(saved.url)
        self.assertIn("No AI summary", self.store.ready()[0].delivery_notice)
        with self.assertRaises(ValueError):
            self.store.mark_ready(saved.url, "Invented summary")

    def test_title_only_match_and_nonmatching_item(self):
        fetch = FakeFetcher({self.source.url: response(rss(
            item("title", "", "New AI research"), item("other", "Unrelated gardening news"))),
            "https://example.com/title": response(b"", kind="text/html"),
            "https://example.com/other": response(b"", kind="text/html")})
        result = Collector(self.config, self.store, fetch=fetch, extract=lambda r: None).collect(now=NOW)
        self.assertEqual((result.stored, result.unmatched), (1, 1))
        saved = self.store.get("https://example.com/title")
        self.assertEqual(saved.content_kind, ContentKind.PARTIAL_ARTICLE)
        self.assertEqual(saved.content, "")

    def test_arxiv_abstract_is_labeled_and_never_downloads_paper(self):
        source = FeedSource("arXiv", "https://example.com/research", kind="research")
        config = CollectionConfig((source,), self.config.keywords)
        feed = rss(item("paper", "arXiv:1234.5678 Announce Type: new Abstract: We study AI methods."))
        fetch = FakeFetcher({source.url: response(feed)})
        result = Collector(config, self.store, fetch=fetch).collect(now=NOW)
        saved = self.store.get("https://example.com/paper")
        self.assertEqual(result.abstracts, 1)
        self.assertEqual(saved.content, "We study AI methods.")
        self.assertEqual(saved.content_kind, ContentKind.RESEARCH_ABSTRACT)
        self.assertEqual(fetch.calls, [source.url])
        self.assertIn("Abstract-based", saved.delivery_notice)
        with self.assertRaises(ValueError):
            self.store.mark_ready(saved.url)
        self.store.mark_ready(saved.url, "A preview based on the abstract")

    def test_atom_research_summary_and_relative_link(self):
        source = FeedSource("Papers", "https://example.com/feed", kind="research")
        atom = b'''<feed xmlns="http://www.w3.org/2005/Atom"><title>Papers</title>
          <id>urn:papers</id><updated>2026-10-09T12:00:00Z</updated><entry>
          <id>urn:paper:1</id><title>New methods</title><link href="/paper"/>
          <summary>Artificial intelligence methods.</summary>
          <published>2026-10-09T12:00:00Z</published></entry></feed>'''
        fetch = FakeFetcher({source.url: response(atom, kind="application/atom+xml")})
        result = Collector(CollectionConfig((source,), self.config.keywords),
                           self.store, fetch=fetch).collect(now=NOW)
        self.assertEqual(result.stored, 1)
        self.assertEqual(self.store.get("https://example.com/paper").content_kind,
                         ContentKind.RESEARCH_ABSTRACT)

    def test_failed_source_does_not_prevent_other_sources(self):
        broken = FeedSource("Broken", "https://example.com/broken")
        disabled = FeedSource("Disabled", "https://example.com/disabled", enabled=False)
        config = CollectionConfig((broken, disabled, self.source), self.config.keywords)
        fetch = FakeFetcher({broken.url: FetchError("Unavailable"),
                             self.source.url: response(rss(item("ok", "AI"))),
                             "https://example.com/ok": response(b"", kind="text/html")})
        with self.assertLogs("newsbot.collection", level="WARNING") as logs:
            result = Collector(config, self.store, fetch=fetch, extract=lambda r: None).collect(now=NOW)
        self.assertEqual((result.source_errors, result.stored), (1, 1))
        self.assertIn("Broken", logs.output[0])
        self.assertNotIn(disabled.url, fetch.calls)

    def test_preference_reset_blocks_new_collection_until_reevaluation(self):
        old = Article("https://example.com/old", "Old AI article", "Example")
        self.store.add(old)
        self.store.reject(old.url, until=NOW + timedelta(days=1))
        self.store.reset_cooldowns()
        fetch = FakeFetcher({self.source.url: response(rss())})
        collector = Collector(self.config, self.store, fetch=fetch)
        self.assertTrue(collector.collect(now=NOW).deferred_for_reevaluation)
        self.assertEqual(fetch.calls, [])
        self.store.reject(old.url, until=NOW + timedelta(days=1))
        self.assertFalse(collector.collect(now=NOW).deferred_for_reevaluation)
        self.assertEqual(fetch.calls, [self.source.url])

    def test_tracking_variants_do_not_duplicate_stored_items(self):
        base = "https://example.com/story?id=42"
        self.store.add(Article(base, "AI", "Example"))
        feed = rss('<item><title>AI</title><link>'
                   'https://example.com/story?id=42&amp;utm_source=newsletter#top'
                   '</link></item>')
        fetch = FakeFetcher({self.source.url: response(feed)})
        result = Collector(self.config, self.store, fetch=fetch).collect(now=NOW)
        self.assertEqual(result.duplicates, 1)
        self.assertEqual(fetch.calls, [self.source.url])

    def test_invalid_feed_is_reported_and_empty_feed_is_valid(self):
        fetch = FakeFetcher({self.source.url: response(b"<html>Not a feed</html>")})
        with self.assertLogs("newsbot.collection", level="WARNING"):
            self.assertEqual(Collector(self.config, self.store, fetch=fetch).collect(now=NOW).source_errors, 1)
        fetch.responses[self.source.url] = response(rss())
        result = Collector(self.config, self.store, fetch=fetch).collect(now=NOW)
        self.assertEqual((result.source_errors, result.discovered), (0, 0))


class TextAndConfigTests(unittest.TestCase):
    def test_aliases_case_and_word_boundaries(self):
        matcher = KeywordMatcher({"ai": ["artificial intelligence", "tekoäly"]})
        for text in ("AI research", "ARTIFICIAL\n INTELLIGENCE", "Uusi tekoäly", "AI-based"):
            self.assertEqual(matcher.match(text), ("ai",))
        for text in ("said", "chair", "email", "trailing", "tekoälyttömyys"):
            self.assertEqual(matcher.match(text), ())

    def test_real_article_extraction_excludes_navigation_and_comments(self):
        body = ("Researchers investigate artificial intelligence models and their mathematical "
                "foundations. The study describes experimental methods and limitations. " * 5)
        html = f'''<html><head><title>Research</title></head><body>
          <nav>Navigation Menu Home Contact</nav><article><h1>Research</h1>
          <p>{body}</p><p>{body}</p></article></body></html>'''.encode()
        extracted = extract_article(response(html, kind="text/html"))
        self.assertIsNotNone(extracted)
        self.assertIn("artificial intelligence", extracted)
        self.assertNotIn("Navigation Menu", extracted)

    def test_paywall_short_teaser_and_non_html_are_partial(self):
        paid = b'''<html><script type="application/ld+json">
          {"@graph":[{"@type":"NewsArticle","isAccessibleForFree":false}]}</script>
          <article><p>AI article</p></article></html>'''
        self.assertIsNone(extract_article(response(paid, kind="text/html")))
        self.assertIsNone(extract_article(response(b"<p>Short AI teaser</p>", kind="text/html")))
        self.assertIsNone(extract_article(response(b"PDF", kind="application/pdf")))

    def test_example_configuration_and_invalid_synonyms(self):
        config = load_config("config.example.json")
        self.assertTrue(any(source.kind == "research" for source in config.sources))
        self.assertIn("ai", config.keywords)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"sources": [], "keywords": {"ai": "wrong type"}}))
            with self.assertRaises(ValueError):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
