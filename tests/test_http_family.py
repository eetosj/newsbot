import io
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from newsbot import _fetch_worker
from newsbot.collection import Collector, HttpFetcher
from newsbot.config import HttpConfig, load_config
from newsbot.storage import ArticleStore


class AddressFamilyTests(unittest.TestCase):
    def test_ipv4_resolution_preserves_socket_options_and_restores_resolver(self):
        expected = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443))]
        resolver = Mock(return_value=expected)
        with patch("socket.getaddrinfo", resolver):
            with _fetch_worker.connection_family("ipv4"):
                result = socket.getaddrinfo("example.com", 443, socket.AF_UNSPEC,
                                            socket.SOCK_STREAM, 6, socket.AI_CANONNAME)
            self.assertIs(socket.getaddrinfo, resolver)
        self.assertEqual(result, expected)
        resolver.assert_called_once_with("example.com", 443, socket.AF_INET,
                                         socket.SOCK_STREAM, 6, socket.AI_CANONNAME)

    def test_auto_keeps_system_selection_and_ipv4_restores_after_failure(self):
        resolver = Mock(return_value=[])
        with patch("socket.getaddrinfo", resolver):
            with _fetch_worker.connection_family("auto"):
                self.assertIs(socket.getaddrinfo, resolver)
                socket.getaddrinfo("example.com", 443)
            resolver.assert_called_once_with("example.com", 443)
            with self.assertRaises(RuntimeError):
                with _fetch_worker.connection_family("ipv4"):
                    raise RuntimeError("Interrupted request")
            self.assertIs(socket.getaddrinfo, resolver)

    def test_worker_uses_selected_family_during_http_request(self):
        for mode, expected in (("ipv4", socket.AF_INET), ("auto", socket.AF_UNSPEC)):
            with self.subTest(mode=mode):
                resolver = Mock(return_value=[])
                response = MagicMock()
                response.__enter__.return_value = response
                response.read.return_value = b"response body"
                response.url = "https://example.com/final"
                response.headers = {"Content-Type": "text/html"}

                def open_request(request, timeout):
                    socket.getaddrinfo("example.com", 443, socket.AF_UNSPEC, socket.SOCK_STREAM)
                    return response

                output = io.StringIO()
                with patch("socket.getaddrinfo", resolver), patch(
                    "newsbot._fetch_worker.urlopen", side_effect=open_request
                ), patch("sys.argv", ["worker", "https://example.com", "15", "5000000", mode]), patch(
                    "sys.stdout", output
                ):
                    self.assertEqual(_fetch_worker.main(), 0)
                    self.assertIs(socket.getaddrinfo, resolver)
                if mode == "ipv4":
                    resolver.assert_called_once_with("example.com", 443, expected,
                                                     socket.SOCK_STREAM, 0, 0)
                else:
                    resolver.assert_called_once_with("example.com", 443, expected,
                                                     socket.SOCK_STREAM)
                self.assertEqual(json.loads(output.getvalue())["url"], "https://example.com/final")

    def test_config_default_override_and_collector_forwarding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            data = {"sources": [], "keywords": {"ai": []}}
            path.write_text(json.dumps(data))
            self.assertEqual(load_config(path).http.address_family, "ipv4")
            data["http"] = {"address_family": "auto"}
            path.write_text(json.dumps(data))
            config = load_config(path)
            self.assertEqual(config.http.address_family, "auto")
            with ArticleStore(":memory:") as store:
                collector = Collector(config, store)
                self.assertEqual(collector.fetch.address_family, "auto")
        with self.assertRaises(ValueError):
            HttpConfig("invalid")
        with self.assertRaises(ValueError):
            HttpFetcher(address_family="invalid")


if __name__ == "__main__":
    unittest.main()
