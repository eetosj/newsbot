import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from newsbot.__main__ import main
from newsbot.articles import Article, ArticleStatus, ContentKind
from newsbot.delivery import (Delivery, DeliverySetupError, DiscordClient, DiscordSettings,
                              RateLimited, RetryableDeliveryError, SendReceipt, format_message)
from newsbot.storage import ArticleStore


NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
SETTINGS = DiscordSettings("private-test-token", "123456789")


def item(name="article", **changes):
    return replace(Article(f"https://example.com/{name}", f"Title {name}", "Example",
                           published_at=NOW, content="Original article"), **changes)


def response(data=None, *, headers=None, status=200, body=None):
    result = Mock()
    result.status = status
    result.headers = headers or {}
    result.read.return_value = (json.dumps(data or {"id": "987654321", "channel_id": SETTINGS.channel_id}).encode()
                                if body is None else body)
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    return result


def http_error(code, *, headers=None, body=b"{}"):
    return HTTPError("https://discord.com/api/v10/channels/123456789/messages", code,
                     "Error", headers or {}, io.BytesIO(body))


class MessageAndSettingsTests(unittest.TestCase):
    def test_layout_preserves_source_order_and_omits_missing_parts(self):
        article = item(summary="A source-faithful summary.")
        self.assertEqual(format_message(article),
                         "Title article\n\n2026-10-11\n\nA source-faithful summary.\n\nhttps://example.com/article")
        self.assertEqual(format_message(replace(article, published_at=None, summary=None)),
                         "Title article\n\nhttps://example.com/article")

    def test_notices_precede_final_link_for_all_content_kinds(self):
        for kind, omitted in ((ContentKind.PARTIAL_ARTICLE, False),
                              (ContentKind.RESEARCH_ABSTRACT, False),
                              (ContentKind.FULL_ARTICLE, True)):
            with self.subTest(kind=kind):
                article = item(content_kind=kind, summary_omitted=omitted)
                content = format_message(article)
                self.assertIn(article.delivery_notice, content)
                self.assertTrue(content.endswith(article.url))
                self.assertNotIn("None", content)

    def test_date_is_utc_and_oversize_messages_are_not_truncated(self):
        offset = timezone(timedelta(hours=3))
        article = item(published_at=datetime(2026, 10, 11, 1, tzinfo=offset))
        self.assertIn("2026-10-10", format_message(article))
        for title in ("x" * 2001, "😀" * 1001):
            with self.subTest(title=title[:1]), self.assertRaises(DeliverySetupError):
                format_message(replace(article, title=title))

    def test_environment_settings_and_validation_do_not_expose_token(self):
        with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": SETTINGS.token,
                                     "DISCORD_NEWS_CHANNEL_ID": SETTINGS.channel_id}, clear=True):
            self.assertEqual(DiscordSettings.from_environment(), SETTINGS)
        self.assertNotIn(SETTINGS.token, repr(SETTINGS))
        for token, channel in (("", "123"), ("secret\nvalue", "123"), ("token", ""),
                               ("token", "../channels"), ("token", "0"), ("token", "١٢٣"),
                               ("token", str(2**64))):
            with self.subTest(channel=channel), self.assertRaises(ValueError) as caught:
                DiscordSettings(token, channel)
            self.assertNotIn("secret", str(caught.exception))


class DiscordClientTests(unittest.TestCase):
    def setUp(self):
        self.client = DiscordClient(SETTINGS)

    def test_request_and_confirmed_message_with_short_window_deduplication(self):
        article = item(summary="Summary mentioning @everyone")
        with patch("newsbot.delivery._open", return_value=response()) as opened:
            receipt = self.client.send(article)
            self.client.send(article)
        request = opened.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "https://discord.com/api/v10/channels/123456789/messages")
        self.assertEqual(request.get_header("Authorization"), "Bot " + SETTINGS.token)
        self.assertEqual(payload["content"], format_message(article))
        self.assertEqual(payload["allowed_mentions"], {"parse": []})
        self.assertTrue(payload["enforce_nonce"])
        self.assertLessEqual(len(payload["nonce"]), 25)
        self.assertEqual(payload["nonce"], json.loads(opened.call_args_list[0].args[0].data)["nonce"])
        self.assertEqual(receipt, SendReceipt("987654321"))

    def test_connection_and_server_errors_are_retryable_without_secret_logs(self):
        for failure in (URLError(SETTINGS.token), TimeoutError(SETTINGS.token), http_error(503)):
            with self.subTest(type=type(failure)), patch("newsbot.delivery._open", side_effect=failure):
                with self.assertRaises(RetryableDeliveryError) as caught:
                    self.client.send(item())
                self.assertNotIn(SETTINGS.token, str(caught.exception))

    def test_setup_errors_and_redirects_are_not_retried(self):
        for status in (400, 401, 403, 404, 302):
            with self.subTest(status=status), patch("newsbot.delivery._open", side_effect=http_error(status)):
                with self.assertRaises(DeliverySetupError):
                    self.client.send(item())

    def test_redirect_handler_does_not_forward_authorization(self):
        from newsbot.delivery import _NoRedirect
        self.assertIsNone(_NoRedirect().redirect_request(Mock(), None, 302, "", {}, "https://other.test"))

    def test_rate_limit_wait_uses_largest_valid_hint(self):
        cases = [({"Retry-After": "5"}, b'{"retry_after": 12.5}', 12.5),
                 ({"Retry-After": "30"}, b'{"retry_after": 4}', 30),
                 ({}, b'{}', 600), ({}, b'not JSON', 600),
                 ({"Retry-After": "nan"}, b'{"retry_after": -1}', 600)]
        for headers, body, expected in cases:
            with self.subTest(body=body), patch("newsbot.delivery._open", side_effect=
                    http_error(429, headers=headers, body=body)):
                with self.assertRaises(RateLimited) as caught:
                    self.client.send(item())
                self.assertEqual(caught.exception.retry_after, expected)

    def test_successful_response_retains_rate_reset(self):
        with patch("newsbot.delivery._open", return_value=response(headers={
                "X-RateLimit-Remaining": "0", "X-RateLimit-Reset-After": "1.25"})):
            self.assertEqual(self.client.send(item()).retry_after, 1.25)

    def test_unconfirmed_responses_never_count_as_success(self):
        for body in (b'not JSON', b'[]', b'{"id":"123"}',
                     b'{"id":"123","channel_id":"wrong"}',
                     b'{"id":false,"channel_id":"123456789"}', b'x' * 1_000_001):
            with self.subTest(body=body[:40]), patch("newsbot.delivery._open", return_value=response(body=body)):
                with self.assertRaises(DeliverySetupError):
                    self.client.send(item())


class DeliveryFlowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "articles.sqlite3"
        self.store = ArticleStore(self.path)
        self.now = NOW
        self.delays = []
        self.client = Mock()
        self.client.send.return_value = SendReceipt("987654321")

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def sleep(self, seconds):
        self.delays.append(seconds)
        self.now += timedelta(seconds=seconds)

    def delivery(self):
        return Delivery(SETTINGS, self.store, client=self.client, sleep=self.sleep, clock=lambda: self.now)

    def queue(self, name="article", *, omitted=False):
        article = item(name)
        self.store.add(article)
        self.store.mark_ready(article.url, None if omitted else "Summary", summary_omitted=omitted)
        return article

    def test_confirmation_precedes_posted_state(self):
        article = self.queue()
        def confirmed(saved):
            self.assertEqual(self.store.get(saved.url).status, ArticleStatus.READY)
            return SendReceipt("987654321")
        self.client.send.side_effect = confirmed
        result = self.delivery().deliver_ready()
        self.assertEqual((result.posted, result.errors), (1, 0))
        self.assertEqual(self.store.get(article.url).posted_at, NOW)
        self.assertEqual(self.delivery().deliver_ready().examined, 0)

    def test_five_retries_then_persistent_ten_minute_break(self):
        article = self.queue()
        self.queue("later")
        self.client.send.side_effect = RetryableDeliveryError("Temporary outage")
        with self.assertLogs("newsbot.delivery", level="WARNING"):
            result = self.delivery().deliver_ready()
        self.assertEqual(self.client.send.call_count, 6)
        self.assertEqual(self.delays, [1, 2, 4, 8, 16])
        self.assertEqual((result.retries, result.deferred, result.posted, result.errors), (5, 1, 0, 1))
        deadline = self.now + timedelta(minutes=10)
        self.assertEqual(self.store.get(article.url).next_delivery_attempt, deadline)
        self.store.close()
        self.store = ArticleStore(self.path)
        self.queue("new-arrival")
        self.client.send.reset_mock(side_effect=True)
        self.assertTrue(self.delivery().deliver_ready().paused)
        self.client.send.assert_not_called()
        self.now = deadline
        result = self.delivery().deliver_ready()
        self.assertEqual(result.posted, 3)
        self.assertIsNone(self.store.get(article.url).next_delivery_attempt)

    def test_last_retry_can_succeed(self):
        article = self.queue(omitted=True)
        self.client.send.side_effect = [RetryableDeliveryError("Outage")] * 5 + [SendReceipt("987654321")]
        with self.assertLogs("newsbot.delivery", level="WARNING"):
            result = self.delivery().deliver_ready()
        self.assertEqual((result.posted, result.retries, result.deferred), (1, 5, 0))
        self.assertTrue(self.store.get(article.url).summary_omitted)
        self.assertIsNone(self.store.get(article.url).summary)

    def test_setup_error_stops_batch_without_modifying_queue(self):
        article = self.queue()
        self.queue("later")
        self.client.send.side_effect = DeliverySetupError("Bot lacks channel permissions")
        with self.assertLogs("newsbot.delivery", level="ERROR"):
            result = self.delivery().deliver_ready()
        self.assertEqual((result.errors, result.retries, result.posted), (1, 0, 0))
        self.assertEqual(self.client.send.call_count, 1)
        self.assertEqual(self.store.get(article.url).status, ArticleStatus.READY)
        self.assertEqual(len(self.store.ready(now=NOW)), 2)

    def test_short_rate_limit_wait_is_respected(self):
        self.queue()
        self.client.send.side_effect = [RateLimited("Quota", retry_after=12.5), SendReceipt("987654321")]
        with self.assertLogs("newsbot.delivery", level="WARNING"):
            result = self.delivery().deliver_ready()
        self.assertEqual(self.delays, [12.5])
        self.assertEqual(result.posted, 1)

    def test_interrupted_rate_wait_is_preserved_for_next_pass(self):
        self.queue()
        self.client.send.side_effect = RateLimited("Quota", retry_after=30)
        delivery = Delivery(SETTINGS, self.store, client=self.client,
                            sleep=Mock(side_effect=KeyboardInterrupt), clock=lambda: NOW)
        with self.assertLogs("newsbot.delivery", level="WARNING"), self.assertRaises(KeyboardInterrupt):
            delivery.deliver_ready()
        self.assertEqual(self.store.delivery_paused_until(SETTINGS.channel_id, now=NOW),
                         NOW + timedelta(seconds=30))
        self.client.send.reset_mock()
        self.assertTrue(self.delivery().deliver_ready().paused)
        self.client.send.assert_not_called()

    def test_long_rate_limit_is_saved_without_blocking_sleep(self):
        article = self.queue()
        self.client.send.side_effect = RateLimited("Quota", retry_after=900)
        with self.assertLogs("newsbot.delivery", level="WARNING"):
            result = self.delivery().deliver_ready()
        self.assertEqual(self.delays, [])
        self.assertEqual((result.errors, result.deferred), (0, 1))
        self.assertEqual(self.store.get(article.url).next_delivery_attempt, NOW + timedelta(seconds=900))
        self.assertEqual(self.store.delivery_paused_until(SETTINGS.channel_id, now=NOW),
                         NOW + timedelta(seconds=900))

    def test_success_at_quota_boundary_posts_one_and_preserves_rest(self):
        first = self.queue()
        second = self.queue("later")
        self.client.send.return_value = SendReceipt("987654321", retry_after=2.5)
        result = self.delivery().deliver_ready()
        self.assertEqual(result.posted, 1)
        self.assertEqual(self.store.get(first.url).status, ArticleStatus.POSTED)
        self.assertEqual(self.store.get(second.url).status, ArticleStatus.READY)
        self.assertEqual(self.store.delivery_paused_until(SETTINGS.channel_id, now=NOW),
                         NOW + timedelta(seconds=2.5))

    def test_only_due_ready_articles_are_sent_and_limit_is_obeyed(self):
        future = self.queue("future")
        self.store.defer_delivery(future.url, until=NOW + timedelta(hours=1))
        self.store.add(item("pending"))
        self.queue("due")
        self.queue("another-due")
        self.assertEqual(self.delivery().deliver_ready(limit=1).posted, 1)
        self.assertEqual(self.client.send.call_args.args[0].url, item("due").url)

    def test_keyboard_interrupt_keeps_unconfirmed_item_ready(self):
        article = self.queue()
        self.client.send.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.delivery().deliver_ready()
        self.assertEqual(self.store.get(article.url).status, ArticleStatus.READY)

    def test_preview_needs_no_credentials_and_does_not_send_or_change_status(self):
        article = self.queue(omitted=True)
        args = ["newsbot", "--deliver", "--preview", "--database", str(self.path),
                "--config", "/missing/config.json", "--limit-items", "1"]
        output = io.StringIO()
        with patch("sys.argv", args), patch.dict(os.environ, {}, clear=True), redirect_stdout(output), \
                patch("newsbot.delivery._open") as opened:
            main()
        opened.assert_not_called()
        self.assertIn(article.title, output.getvalue())
        self.assertIn("AI summary omitted", output.getvalue())
        self.assertEqual(self.store.get(article.url).status, ArticleStatus.READY)

    def test_cli_checks_credentials_before_opening_database(self):
        args = ["newsbot", "--deliver", "--database", str(Path(self.directory.name) / "missing.sqlite3")]
        with patch("sys.argv", args), patch.dict(os.environ, {}, clear=True), \
                patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit) as caught:
            main()
        self.assertEqual(caught.exception.code, 2)
        self.assertFalse(Path(self.directory.name, "missing.sqlite3").exists())

    def test_cli_rejects_conflicting_modes_and_wrong_flags(self):
        for flags in (["--deliver", "--evaluate"], ["--preview"],
                      ["--deliver", "--model", "x"], ["--deliver", "--limit-per-source", "1"]):
            with self.subTest(flags=flags), patch("sys.argv", ["newsbot", *flags]), \
                    patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit) as caught:
                main()
            self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
