from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from dataclasses import dataclass
from typing import Any


USER_AGENT = "bugbounty-catalog/0.2 (+local research catalog)"
DEFAULT_MAX_RESPONSE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int
    content_type: str
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class FetchError(RuntimeError):
    pass


def fetch_url(
    url: str,
    *,
    timeout: float = 30.0,
    delay: float = 0.0,
    public_only: bool = False,
    max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> FetchResult:
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if delay < 0:
        raise ValueError("delay cannot be negative")
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    if delay > 0:
        time.sleep(delay)
    if public_only:
        _validate_public_destination(url)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        opener = urllib.request.build_opener(_PublicRedirectHandler()) if public_only else None
        response_context = (
            opener.open(request, timeout=timeout)
            if opener
            else urllib.request.urlopen(request, timeout=timeout)
        )
        with response_context as response:
            status = int(getattr(response, "status", 200))
            content_type = response.headers.get("content-type", "")
            content_length = response.headers.get("content-length")
            if content_length is not None:
                try:
                    declared_size = int(content_length)
                except ValueError:
                    declared_size = None
                if declared_size is not None and declared_size > max_bytes:
                    raise FetchError(
                        f"{url} response exceeds the {max_bytes}-byte limit"
                    )
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise FetchError(f"{url} response exceeds the {max_bytes}-byte limit")
            effective_url = response.geturl()
            return FetchResult(
                url=effective_url,
                status=status,
                content_type=content_type,
                body=body,
            )
    except urllib.error.HTTPError as exc:
        raise FetchError(f"{url} returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise FetchError(f"{url} could not be fetched: {exc.reason}") from exc
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise FetchError(f"{url} could not be fetched: {exc}") from exc


def fetch_json(
    url: str,
    *,
    timeout: float = 30.0,
    delay: float = 0.0,
    public_only: bool = False,
    max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> Any:
    result = fetch_url(
        url,
        timeout=timeout,
        delay=delay,
        public_only=public_only,
        max_bytes=max_bytes,
    )
    try:
        return json.loads(result.text)
    except json.JSONDecodeError as exc:
        raise FetchError(f"{url} returned invalid JSON: {exc.msg}") from exc


def is_public_http_url(url: str) -> bool:
    """Reject explicit local/private destinations without performing DNS I/O."""

    try:
        parsed = urlsplit(url)
        # Accessing these properties performs additional authority validation.
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        return False
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        return False
    if parsed.username or parsed.password:
        return False
    hostname = hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return address.is_global


class _PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        _validate_public_destination(newurl, context="redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _validate_public_destination(url: str, *, context: str = "URL") -> None:
    """Reject explicit and DNS-resolved non-public HTTP destinations."""

    if not is_public_http_url(url):
        raise FetchError(f"{context} is not a public HTTP URL: {url}")
    parsed = urlsplit(url)
    hostname = parsed.hostname
    if hostname is None:
        raise FetchError(f"{context} is not a public HTTP URL: {url}")
    try:
        addresses = socket.getaddrinfo(
            hostname,
            parsed.port or (443 if parsed.scheme.lower() == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise FetchError(f"{url} hostname could not be resolved: {exc}") from exc
    if not addresses:
        raise FetchError(f"{url} hostname did not resolve to an address")
    for *_, socket_address in addresses:
        address_text = str(socket_address[0]).split("%", 1)[0]
        try:
            address = ipaddress.ip_address(address_text)
        except ValueError as exc:
            raise FetchError(f"{url} resolved to an invalid address") from exc
        if not address.is_global:
            raise FetchError(f"{context} to non-public address blocked: {url}")
