"""统一外部请求层的回归测试。"""

import unittest
from unittest.mock import patch

from app.services.http_client import ExternalRequestError, request_json


class _FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def read(self) -> bytes:
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class HttpClientTests(unittest.TestCase):
    @patch("app.services.http_client.urllib.request.urlopen")
    def test_get_json_uses_timeout_and_parses_response(self, mock_urlopen):
        mock_urlopen.return_value = _FakeResponse(b'{"status":"ok"}')

        result = request_json("https://example.com/data", timeout=7)

        self.assertEqual(result, {"status": "ok"})
        self.assertEqual(mock_urlopen.call_args.kwargs["timeout"], 7)

    @patch("app.services.http_client.urllib.request.urlopen")
    def test_non_retryable_http_error_is_exposed_with_status(self, mock_urlopen):
        import urllib.error

        mock_urlopen.side_effect = urllib.error.HTTPError(
            "https://example.com/data", 401, "Unauthorized", {}, None
        )

        with self.assertRaises(ExternalRequestError) as context:
            request_json("https://example.com/data", retries=0)

        self.assertEqual(context.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
