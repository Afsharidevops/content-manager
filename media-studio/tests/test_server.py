import io
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from media_studio.config import Settings
from media_studio.drivers.base import Driver, RunContext
from media_studio.runner import JobQueue
from media_studio.server import MediaStudioHandler
from media_studio.state import StateStore, STATUS_DONE


class FakeDriver(Driver):
    name = "fake"

    def run(self, ctx: RunContext):
        with open(os.path.join(ctx.work_dir, "result.txt"), "w", encoding="utf-8") as handle:
            handle.write("artifact-body")
        return [("result.txt", "file")]


def factory(_name):
    return FakeDriver()


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ms-server-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.settings = Settings(data_dir=self.dir, drivers=("fake",), api_token="test-token")
        self.state = StateStore(os.path.join(self.dir, "jobs.json"))
        self.queue = JobQueue(self.settings, self.state, driver_factory=factory)
        self.queue.start()
        self.addCleanup(self.queue.stop)

        class Bound(MediaStudioHandler):
            settings = self.settings
            state = self.state
            queue = self.queue

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Bound)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def _request(self, method, path, body=None, token=True):
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = "Bearer test-token"
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def _json(self, method, path, body=None, token=True):
        status, raw = self._request(method, path, body, token)
        return status, json.loads(raw) if raw else {}

    def test_healthz_is_open(self):
        status, raw = self._request("GET", "/healthz", token=False)
        self.assertEqual(status, 200)
        self.assertIn(b'"ok": true', raw)

    def test_protected_route_requires_token(self):
        status, payload = self._json("GET", "/session/info", token=False)
        self.assertEqual(status, 401)
        self.assertIn("error", payload)

    def test_submit_and_fetch_job(self):
        status, payload = self._json("POST", "/jobs", {"driver": "fake", "prompt": "make it"})
        self.assertEqual(status, 202)
        job_id = payload["job"]["id"]
        deadline = time.time() + 8
        while time.time() < deadline:
            status, payload = self._json("GET", f"/jobs/{job_id}")
            if payload["job"]["status"] == STATUS_DONE:
                break
            time.sleep(0.05)
        else:
            self.fail("job did not finish")
        self.assertEqual(payload["job"]["artifacts"][0]["name"], "result.txt")

    def test_artifact_download(self):
        _, payload = self._json("POST", "/jobs", {"driver": "fake", "prompt": "x"})
        job_id = payload["job"]["id"]
        deadline = time.time() + 8
        while time.time() < deadline:
            _, payload = self._json("GET", f"/jobs/{job_id}")
            if payload["job"]["status"] == STATUS_DONE:
                break
            time.sleep(0.05)
        status, raw = self._request("GET", f"/artifacts/{job_id}/result.txt")
        self.assertEqual(status, 200)
        self.assertEqual(raw, b"artifact-body")

    def test_artifact_path_traversal_rejected(self):
        status, _ = self._request("GET", "/artifacts/abc/..%2F..%2Fjobs.json")
        self.assertIn(status, (404, 400))

    def test_rejects_unknown_driver(self):
        status, payload = self._json("POST", "/jobs", {"driver": "nope", "prompt": "x"})
        self.assertEqual(status, 400)

    def test_probe_requires_known_target(self):
        status, _ = self._json("POST", "/session/probe", {"driver": "api-image"})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()


class HandlerFactoryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ms-handler-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_build_handler_binds_instance(self):
        settings = Settings(data_dir=self.dir, drivers=("fake",))
        state = StateStore(os.path.join(self.dir, "handler-jobs.json"))
        queue = JobQueue(settings, state, driver_factory=factory)
        from media_studio.server import build_handler

        cls = build_handler(settings, state, queue)
        self.assertIs(cls.settings, settings)
        self.assertIs(cls.state, state)
        self.assertIs(cls.queue, queue)
        self.assertTrue(issubclass(cls, MediaStudioHandler))


if __name__ == "__main__":
    unittest.main()


class BrandServerTests(unittest.TestCase):
    """POST /brand returns the image with the configured brand chip."""

    def setUp(self):
        import base64

        self.dir = tempfile.mkdtemp(prefix="ms-brand-srv-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.settings = Settings(data_dir=self.dir, drivers=("fake",), api_token="test-token")
        self.state = StateStore(os.path.join(self.dir, "jobs.json"))
        self.queue = JobQueue(self.settings, self.state, driver_factory=factory)
        self.queue.start()
        self.addCleanup(self.queue.stop)

        class Bound(MediaStudioHandler):
            settings = self.settings
            state = self.state
            queue = self.queue

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Bound)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        from PIL import Image

        image = Image.new("RGB", (220, 160), "white")
        buffer = tempfile.SpooledTemporaryFile(max_size=0)
        image.save(buffer, format="PNG")
        buffer.seek(0)
        self.png = buffer.read()

    def _raw(self, method, path, body, content_type, token=True):
        headers = {"Content-Type": content_type}
        if token:
            headers["Authorization"] = "Bearer test-token"
        request = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_brand_endpoint_requires_token(self):
        status, body = self._raw("POST", "/brand", self.png, "image/png", token=False)
        self.assertEqual(status, 401)
        self.assertIn(b"error", body)

    def test_brand_endpoint_draws_chip(self):
        status, body = self._raw("POST", "/brand", self.png, "image/png", token=True)
        self.assertEqual(status, 200)
        self.assertNotEqual(body, self.png)
        from PIL import Image as PilImage

        with PilImage.open(io.BytesIO(body)) as image:
            rgb = image.convert("RGB")
        width, height = rgb.size
        corner = rgb.crop((width * 3 // 4, height * 3 // 4, width, height))
        self.assertTrue(any(pixel[0] < 120 for pixel in corner.getdata()))

    def test_brand_endpoint_passthrough_when_label_disabled(self):
        bound = self.httpd.RequestHandlerClass
        original = bound.settings
        bound.settings = Settings(
            data_dir=self.dir,
            drivers=("fake",),
            api_token="test-token",
            brand_label="",
        )
        try:
            status, body = self._raw("POST", "/brand", self.png, "image/png", token=True)
        finally:
            bound.settings = original
        self.assertEqual(status, 200)
        self.assertEqual(body, self.png)
