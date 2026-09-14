"""Tests for the shared HTTP transport, against a real local server."""

from __future__ import annotations

import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from content_bot import http as http_mod


class _Handler(BaseHTTPRequestHandler):
    """Echo the request body back so a test can inspect it."""

    seen: list[dict] = []

    def do_POST(self):  # noqa: N802 - the name is fixed by http.server
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        type(self).seen.append(
            {
                "path": self.path,
                "content_type": self.headers.get("Content-Type") or "",
                "body": body,
            }
        )
        payload = b"answered"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # keep the test output quiet
        return


class MultipartTransportTest(unittest.TestCase):
    def setUp(self):
        _Handler.seen = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/upload"

    def test_request_multipart_returns_the_status_and_the_body(self):
        status, body = http_mod.request_multipart(
            self.url,
            fields={"qqpartindex": 0, "qqfilename": "clip.mp4"},
            file_field="qqfile",
            filename="clip.mp4",
            file_bytes=b"video-bytes",
            timeout=10,
        )
        self.assertEqual(200, status)
        self.assertEqual(b"answered", body)
        seen = _Handler.seen[-1]
        self.assertTrue(seen["content_type"].startswith("multipart/form-data; boundary="))
        for expected in (b"video-bytes", b'name="qqpartindex"', b'name="qqfile"'):
            self.assertIn(expected, seen["body"])

    def test_request_multipart_many_returns_the_status_and_the_body(self):
        status, body = http_mod.request_multipart_many(
            self.url,
            fields={"chat_id": "1"},
            files=[("photo", "a.jpg", b"one"), ("photo", "b.jpg", b"two")],
            timeout=10,
        )
        self.assertEqual(200, status)
        self.assertEqual(b"answered", body)
        seen = _Handler.seen[-1]
        for expected in (b"one", b"two", b'name="chat_id"'):
            self.assertIn(expected, seen["body"])


if __name__ == "__main__":
    unittest.main()
