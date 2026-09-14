import base64
import json
import os
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from media_studio.config import Settings
from media_studio.drivers.api_video import ApiVideoDriver
from media_studio.drivers.base import DriverError, RunContext

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32


class FakeVideoHandler(BaseHTTPRequestHandler):
    """Serve one async video job: submit, poll, then download the clip."""

    polls = 0
    submitted: dict = {}
    path = ""

    def log_message(self, _fmt, *_args):
        pass

    def _send(self, status: int, payload, content_type: str = "application/json"):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_request(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_POST(self):  # noqa: N802
        type(self).path = self.path
        type(self).submitted = self._read_request()
        self._send(200, {"request_id": "vid-1"})

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/v1/videos/vid-1"):
            type(self).polls += 1
            if type(self).polls < 2:
                self._send(200, {"status": "pending"})
                return
            self._send(
                200,
                {
                    "status": "done",
                    "video": {
                        "url": f"http://127.0.0.1:{self.server.server_address[1]}/clip.mp4"
                    },
                },
            )
            return
        if self.path.startswith("/clip.mp4"):
            self._send(200, MP4, content_type="video/mp4")
            return
        self._send(404, {"error": "not found"})


class InlineVideoHandler(FakeVideoHandler):
    def do_POST(self):  # noqa: N802
        type(self).path = self.path
        type(self).submitted = self._read_request()
        self._send(
            200,
            {"status": "completed", "data": [{"b64_json": base64.b64encode(MP4).decode()}]},
        )


class ErrorVideoHandler(FakeVideoHandler):
    status = 400
    message = "Combos are not supported for video generation"

    def do_POST(self):  # noqa: N802
        type(self).path = self.path
        type(self).submitted = self._read_request()
        self._send(type(self).status, {"error": {"message": type(self).message}})


class FailedJobHandler(FakeVideoHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/v1/videos/vid-1"):
            self._send(200, {"status": "failed", "error": {"message": "prompt rejected"}})
            return
        self._send(404, {"error": "not found"})


class VideoDriverHarness(unittest.TestCase):
    """Shared server fixture; subclasses pick a handler and add tests."""

    handler = FakeVideoHandler

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ms-api-video-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        type(self).handler.polls = 0
        type(self).handler.submitted = {}
        type(self).handler.path = ""
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), type(self).handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.httpd.shutdown)
        self.addCleanup(self.httpd.server_close)
        base = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        self.base = base
        self.settings = Settings(
            data_dir=self.dir,
            writer_base_url=base,
            writer_api_key="key",
            writer_model="image-model",
            video_base_url=base,
            video_api_key="video-key",
            video_model="xai/grok-imagine-video",
            brand_label="",
            job_timeout_seconds=10,
        )

    def ctx(self, params=None):
        return RunContext(
            prompt="a calm lake at sunrise",
            params=params or {"poll_seconds": 1},
            work_dir=self.dir,
            settings=self.settings,
            log=lambda _m: None,
        )


class ApiVideoDriverTests(VideoDriverHarness):
    def test_run_polls_and_writes_mp4(self):
        artifacts = ApiVideoDriver().run(self.ctx())
        self.assertEqual(artifacts, [("video_1.mp4", "video")])
        with open(os.path.join(self.dir, "video_1.mp4"), "rb") as handle:
            self.assertEqual(handle.read(len(MP4)), MP4)
        self.assertEqual(type(self).handler.submitted.get("model"), "xai/grok-imagine-video")
        self.assertGreaterEqual(type(self).handler.polls, 2)

    def test_duration_hint_is_not_sent_as_a_provider_value(self):
        ApiVideoDriver().run(self.ctx({"poll_seconds": 1, "duration": "up_to_30_seconds"}))
        self.assertNotIn("duration", type(self).handler.submitted)

    def test_duration_seconds_are_forwarded(self):
        ApiVideoDriver().run(self.ctx({"poll_seconds": 1, "duration": "8"}))
        self.assertEqual(type(self).handler.submitted.get("duration"), 8)

    def test_provider_query_parameter_is_forwarded(self):
        ApiVideoDriver().run(self.ctx({"poll_seconds": 1, "provider": "xai"}))
        self.assertEqual(type(self).handler.path, "/v1/videos/generations?provider=xai")

    def test_missing_video_model_is_a_config_error(self):
        self.settings = Settings(
            data_dir=self.dir,
            video_base_url="http://127.0.0.1:1/v1",
            brand_label="",
        )
        with self.assertRaises(DriverError) as caught:
            ApiVideoDriver().run(self.ctx())
        self.assertEqual(caught.exception.step, "config")
        self.assertIn("MEDIA_STUDIO_VIDEO_MODEL", caught.exception.hint)


class ApiVideoInlineTests(VideoDriverHarness):
    handler = InlineVideoHandler

    def test_inline_video_is_saved_without_polling(self):
        artifacts = ApiVideoDriver().run(self.ctx())
        self.assertEqual(artifacts, [("video_1.mp4", "video")])
        self.assertEqual(type(self).handler.polls, 0)


class ApiVideoComboTests(VideoDriverHarness):
    handler = ErrorVideoHandler

    def test_combo_answer_names_the_model_setting(self):
        with self.assertRaises(DriverError) as caught:
            ApiVideoDriver().run(self.ctx())
        self.assertIn("MEDIA_STUDIO_VIDEO_MODEL", caught.exception.hint)
        self.assertIn("Combos are not supported", caught.exception.hint)


class ApiVideoCredentialsTests(VideoDriverHarness):
    handler = ErrorVideoHandler

    def setUp(self):
        super().setUp()
        type(self).handler.message = "No credentials for provider: xai"

    def test_missing_credentials_names_the_provider(self):
        with self.assertRaises(DriverError) as caught:
            ApiVideoDriver().run(self.ctx())
        self.assertIn("No credentials for provider: xai", caught.exception.hint)
        self.assertIn("MEDIA_STUDIO_VIDEO_BASE_URL", caught.exception.hint)


class ApiVideoFailedJobTests(VideoDriverHarness):
    handler = FailedJobHandler

    def test_failed_job_reports_the_provider_answer(self):
        with self.assertRaises(DriverError) as caught:
            ApiVideoDriver().run(self.ctx())
        self.assertEqual(caught.exception.step, "poll")
        self.assertIn("prompt rejected", caught.exception.hint)
