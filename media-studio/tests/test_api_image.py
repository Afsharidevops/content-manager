import base64
import json
import os
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from media_studio.config import Settings
from media_studio.drivers.api_image import ApiImageDriver
from media_studio.drivers.base import RunContext

PNG_HEADER = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class FakeApiHandler(BaseHTTPRequestHandler):
    def log_message(self, _fmt, *_args):
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        if body.get("model") != "image-model":
            self.send_response(400)
            self.end_headers()
            return
        payload = {"data": [{"b64_json": base64.b64encode(PNG_HEADER).decode()}]}
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class ApiImageDriverTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ms-api-image-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeApiHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.httpd.shutdown)
        base = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        self.settings = Settings(
            data_dir=self.dir,
            writer_base_url=base,
            writer_api_key="key",
            writer_model="image-model",
            job_timeout_seconds=10,
        )

    def test_run_writes_png(self):
        ctx = RunContext(
            prompt="a red cube",
            work_dir=self.dir,
            settings=self.settings,
            log=lambda _m: None,
        )
        artifacts = ApiImageDriver().run(ctx)
        self.assertEqual(artifacts, [("image_1.png", "image")])
        with open(os.path.join(self.dir, "image_1.png"), "rb") as handle:
            self.assertTrue(handle.read(4).startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
