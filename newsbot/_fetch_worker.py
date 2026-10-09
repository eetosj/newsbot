"""Isolated HTTP reader so its parent can cancel stalled DNS/connect/read work.

This module uses only the standard library and never opens the article database.
Its stdout protocol is one JSON object containing base64-encoded response bytes.
"""

import base64
import json
import socket
import sys
from contextlib import contextmanager
from http.client import HTTPException
from urllib.error import URLError
from urllib.request import Request, urlopen


@contextmanager
def connection_family(address_family: str):
    """Scope IPv4 DNS selection to this isolated worker, including redirects.

    The parent and Ollama client keep their ordinary resolver behavior. Auto
    leaves system IPv4/IPv6 selection intact. No system network settings change.
    """
    if address_family not in {"ipv4", "auto"}:
        raise ValueError("Address family must be ipv4 or auto")
    original = socket.getaddrinfo
    if address_family == "ipv4":
        def ipv4_addresses(host, port, family=0, type=0, proto=0, flags=0):
            return original(host, port, socket.AF_INET, type, proto, flags)

        socket.getaddrinfo = ipv4_addresses
    try:
        yield
    finally:
        socket.getaddrinfo = original


def main() -> int:
    url, timeout, max_bytes, address_family = sys.argv[1:]
    request = Request(url, headers={
        "User-Agent": "PersonalNewsBot/0.1 (personal RSS reader)",
        "Accept": "application/rss+xml, application/atom+xml, text/html, application/xml;q=0.9, */*;q=0.5",
    })
    try:
        with connection_family(address_family), urlopen(request, timeout=float(timeout)) as response:
            body = response.read(int(max_bytes) + 1)
            if len(body) > int(max_bytes):
                raise ValueError("Resource exceeds the collection size limit")
            result = {
                "body": base64.b64encode(body).decode("ascii"),
                "url": response.url,
                "content_type": response.headers.get("Content-Type", ""),
            }
        sys.stdout.write(json.dumps(result))
        return 0
    except (URLError, OSError, ValueError, HTTPException) as error:
        sys.stderr.write(" ".join(str(error).split()))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
