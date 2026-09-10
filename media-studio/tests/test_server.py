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


class UploadServerTests(unittest.TestCase):
    """POST /uploads stores a raw clip for the video-edit driver."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ms-upload-srv-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.settings = Settings(
            data_dir=self.dir, drivers=("fake", "video-edit"), api_token="test-token"
        )
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

    def _raw(self, body, content_type="video/mp4", token=True):
        headers = {"Content-Type": content_type}
        if token:
            headers["Authorization"] = "Bearer test-token"
        request = urllib.request.Request(
            self.base + "/uploads", data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_upload_requires_token(self):
        status, body = self._raw(b"clip-bytes", token=False)
        self.assertEqual(status, 401)
        self.assertIn(b"error", body)

    def test_upload_returns_id_and_stores_file(self):
        status, body = self._raw(b"clip-bytes")
        self.assertEqual(status, 201)
        payload = json.loads(body)
        upload_id = payload["upload"]["id"]
        self.assertEqual(payload["upload"]["size"], 10)
        stored = os.path.join(self.dir, "uploads", upload_id, "source.mp4")
        with open(stored, "rb") as handle:
            self.assertEqual(handle.read(), b"clip-bytes")

    def test_upload_rejects_non_video_payload(self):
        status, _ = self._raw(b"not-a-clip", content_type="application/json")
        self.assertEqual(status, 415)

    def test_upload_rejects_empty_body(self):
        status, _ = self._raw(b"")
        self.assertEqual(status, 413)

    def test_upload_prunes_expired_directories(self):
        expired = os.path.join(self.dir, "uploads", "f" * 32)
        os.makedirs(expired)
        old = time.time() - 3 * 86400
        os.utime(expired, (old, old))
        status, body = self._raw(b"clip-bytes")
        self.assertEqual(status, 201)
        self.assertFalse(os.path.isdir(expired))
        self.assertTrue(
            os.path.isdir(os.path.join(self.dir, "uploads", json.loads(body)["upload"]["id"]))
        )

    def test_video_edit_job_runs_from_a_stored_upload(self):
        from media_studio.drivers.base import Driver, RunContext

        class RecordingDriver(Driver):
            name = "video-edit"

            def run(self, ctx: RunContext):
                with open(os.path.join(ctx.work_dir, f"seen-{ctx.params['upload_id']}.txt"), "w") as handle:
                    handle.write("ok")
                return [(f"seen-{ctx.params['upload_id']}.txt", "file")]

        original = self.queue._factory  # noqa: SLF001 - test seam
        self.queue._factory = lambda _name: RecordingDriver()  # noqa: SLF001
        try:
            _, raw = self._raw(b"clip-bytes")
            upload_id = json.loads(raw)["upload"]["id"]
            request = urllib.request.Request(
                self.base + "/jobs",
                data=json.dumps(
                    {
                        "driver": "video-edit",
                        "prompt": "prepare the clip",
                        "params": {"upload_id": upload_id},
                    }
                ).encode(),
                headers={"Content-Type": "application/json", "Authorization": "Bearer test-token"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertEqual(response.status, 202)
                job_id = json.loads(response.read())["job"]["id"]
            produced = os.path.join(self.dir, "artifacts", job_id, f"seen-{upload_id}.txt")
            deadline = time.time() + 8
            while time.time() < deadline and not os.path.isfile(produced):
                time.sleep(0.05)
        finally:
            self.queue._factory = original  # noqa: SLF001
        self.assertTrue(os.path.isfile(produced))


class OpenApiTests(unittest.TestCase):
    """GET /openapi.json describes the routes the registry points at."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ms-openapi-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.settings = Settings(data_dir=self.dir, drivers=("fake",), api_token="test-token")
        self.state = StateStore(os.path.join(self.dir, "jobs.json"))
        self.queue = JobQueue(self.settings, self.state, driver_factory=factory)

        class Bound(MediaStudioHandler):
            settings = self.settings
            state = self.state
            queue = self.queue

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Bound)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def test_document_is_readable_without_a_token(self):
        request = urllib.request.Request(self.base + "/openapi.json", method="GET")
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            document = json.loads(response.read())
        self.assertEqual(document["openapi"], "3.1.0")
        self.assertIn("/jobs", document["paths"])
        self.assertIn("/uploads", document["paths"])
        self.assertIn("/brand", document["paths"])
        self.assertIn("bearerAuth", document["components"]["securitySchemes"])

    def test_document_lists_the_enabled_drivers(self):
        from media_studio.openapi import openapi_document

        schema = openapi_document()["paths"]["/jobs"]["post"]["requestBody"]["content"]
        driver = schema["application/json"]["schema"]["properties"]["driver"]
        self.assertIn("video-edit", driver["enum"])
        self.assertIn("api-image", driver["enum"])
