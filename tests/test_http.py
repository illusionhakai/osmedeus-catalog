import io
import unittest
from unittest import mock

from bugbounty_catalog.http import FetchError, fetch_json, fetch_url, is_public_http_url


class PublicURLTests(unittest.TestCase):
    def test_accepts_public_http_hosts(self):
        self.assertTrue(is_public_http_url("https://example.com/security"))
        self.assertTrue(is_public_http_url("http://8.8.8.8/"))

    def test_rejects_local_private_credentials_and_non_http_urls(self):
        blocked = (
            "http://localhost/",
            "http://service.local/",
            "http://127.0.0.1/",
            "http://10.10.10.10/",
            "http://[::1]/",
            "https://user:password@example.com/",
            "file:///etc/passwd",
            "http://example.com:not-a-port/",
            "http://[invalid/",
        )
        for url in blocked:
            with self.subTest(url=url):
                self.assertFalse(is_public_http_url(url))

    def test_public_fetch_rejects_hostname_resolving_to_private_address(self):
        private_result = [(2, 1, 6, "", ("127.0.0.1", 443))]
        with mock.patch("bugbounty_catalog.http.socket.getaddrinfo", return_value=private_result):
            with mock.patch("bugbounty_catalog.http.urllib.request.build_opener") as opener:
                with self.assertRaisesRegex(FetchError, "non-public address"):
                    fetch_url("https://public-looking.example/", public_only=True)
        opener.assert_not_called()

    def test_fetch_uses_effective_url_and_bounds_response_body(self):
        response = _FakeResponse(b"{}", url="https://example.com/final.json")
        with mock.patch("bugbounty_catalog.http.urllib.request.urlopen", return_value=response):
            result = fetch_url("https://example.com/start.json", max_bytes=2)
        self.assertEqual(result.url, "https://example.com/final.json")
        self.assertEqual(result.body, b"{}")

        oversized = _FakeResponse(b"four", url="https://example.com/data")
        with mock.patch("bugbounty_catalog.http.urllib.request.urlopen", return_value=oversized):
            with self.assertRaisesRegex(FetchError, "exceeds"):
                fetch_url("https://example.com/data", max_bytes=3)

    def test_fetch_json_reports_malformed_provider_data(self):
        response = _FakeResponse(b"not-json", url="https://example.com/data")
        with mock.patch("bugbounty_catalog.http.urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(FetchError, "invalid JSON"):
                fetch_json("https://example.com/data")


class _FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, *, url: str, content_length: str | None = None):
        super().__init__(body)
        self.status = 200
        self.headers = {
            "content-type": "application/json",
            **({"content-length": content_length} if content_length is not None else {}),
        }
        self._url = url

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


if __name__ == "__main__":
    unittest.main()
