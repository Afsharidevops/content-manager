"""HTTP API tests against a live local server with a stub runner."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from app.config import Settings
from app.models import JobStore
from app.server import NotebookLMHandler


class StubRunner:
    """Stands in for the browser runner; the API never drives it here."""

    def run_once(self) -> bool:
        return False

    def start(self) -> None:
        return

    def stop(self) -> None:
        return


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nlm-server-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.settings = Settings(
            data_dir=self.dir,
            api_token="test-token",
            default_profile="technical_fa",
        )
        self.store = JobStore(os.path.join(self.dir, "jobs.json"))

        class Bound(NotebookLMHandler):
            settings = self.settings
            store = self.store
            runner = StubRunner()

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Bound)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.httpd.shutdown)
        self.addCleanup(self.httpd.server_close)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def request(self, method, path, body=None, token=True, raw=None, headers=None):
        data = None
        request_headers = dict(headers or {})
        if raw is not None:
            data = raw
        elif body is not None:
            data = json.dumps(body).encode()
            request_headers["Content-Type"] = "application/json"
        if token:
            request_headers["Authorization"] = "Bearer test-token"
        request = urllib.request.Request(
            f"{self.base}{path}", data=data, headers=request_headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = response.read()
                status = response.status
        except urllib.error.HTTPError as error:
            payload = error.read()
            status = error.code
        try:
            parsed = json.loads(payload.decode()) if payload else {}
        except (ValueError, UnicodeDecodeError):
            parsed = {"raw": payload}
        return status, parsed

    def test_healthz_is_open_and_reports_counts(self):
        status, payload = self.request("GET", "/healthz", token=False)
        self.assertEqual(200, status)
        self.assertEqual("ok", payload["status"])
        self.assertIn("version", payload)

    def test_a_request_without_the_token_is_refused(self):
        status, payload = self.request("GET", "/jobs", token=False)
        self.assertEqual(401, status)
        self.assertIn("token", payload["error"].lower())

    def test_profiles_list_the_three_spec_profiles(self):
        status, payload = self.request("GET", "/profiles")
        self.assertEqual(200, status)
        self.assertEqual(
            ["educational_fa", "news_fa", "technical_fa"], sorted(payload["profiles"])
        )
        self.assertEqual("technical_fa", payload["default"])
        self.assertIn("deep", payload["duration_targets"])

    def test_a_job_takes_its_profile_fields_from_the_profile(self):
        status, payload = self.request(
            "POST",
            "/jobs",
            {
                "topic": "Kubernetes security",
                "sources": ["https://example.com", "https://youtu.be/abc"],
                "profile": "news_fa",
                "content_id": "draft-1",
            },
        )
        self.assertEqual(201, status)
        job = payload["job"]
        self.assertEqual("news_fa", job["profile"])
        self.assertEqual("Persian", job["language"])
        self.assertEqual("3 to 5 minutes", job["duration"])
        self.assertEqual("neutral", job["voice_gender"])
        self.assertEqual("draft-1", job["content_id"])
        self.assertEqual("created", job["status"])
        self.assertEqual(2, len(job["sources"]))
        self.assertTrue(job["id"])

    def test_a_job_without_a_topic_is_refused(self):
        status, payload = self.request("POST", "/jobs", {"topic": "  "})
        self.assertEqual(400, status)
        self.assertIn("topic", payload["error"].lower())

    def test_a_duration_bucket_overrides_the_profile_length(self):
        _, payload = self.request(
            "POST",
            "/jobs",
            {"topic": "Topic", "profile": "technical_fa", "duration_profile": "deep"},
        )
        self.assertEqual("10 to 15 minutes", payload["job"]["duration"])

    def test_the_job_list_and_lookup_return_the_same_document(self):
        _, created = self.request("POST", "/jobs", {"topic": "Topic"})
        job_id = created["job"]["id"]
        status, payload = self.request("GET", f"/jobs/{job_id}")
        self.assertEqual(200, status)
        self.assertEqual(job_id, payload["job"]["id"])
        self.assertIn("log_tail", payload["job"])
        status, listing = self.request("GET", "/jobs")
        self.assertEqual(200, status)
        self.assertEqual([job_id], [job["id"] for job in listing["jobs"]])

    def test_an_unknown_job_is_a_404(self):
        status, _ = self.request("GET", "/jobs/nope")
        self.assertEqual(404, status)

    def test_a_job_that_has_not_started_can_be_canceled_once(self):
        _, created = self.request("POST", "/jobs", {"topic": "Topic"})
        job_id = created["job"]["id"]
        status, payload = self.request("DELETE", f"/jobs/{job_id}")
        self.assertEqual(200, status)
        self.assertEqual(job_id, payload["canceled"])
        status, _ = self.request("DELETE", f"/jobs/{job_id}")
        self.assertEqual(409, status)

    def test_an_upload_can_back_a_file_source_and_be_served_as_an_artifact(self):
        status, payload = self.request(
            "POST",
            "/uploads?name=brief.md",
            token=True,
            raw=b"# Brief body",
            headers={"Content-Type": "text/markdown"},
        )
        self.assertEqual(201, status)
        upload_id = payload["id"]
        self.assertTrue(upload_id)
        path = os.path.join(self.dir, "uploads")
        self.assertEqual(1, len(os.listdir(path)))

    def test_an_artifact_is_only_served_for_a_finished_job(self):
        _, created = self.request("POST", "/jobs", {"topic": "Topic"})
        job_id = created["job"]["id"]
        status, _ = self.request("GET", f"/artifacts/{job_id}/video.mp4")
        self.assertEqual(404, status)
        target = os.path.join(self.dir, "videos", f"{job_id}.mp4")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(b"video-bytes")
        self.store.update(job_id, video_path=target)
        status, payload = self.request("GET", f"/artifacts/{job_id}/{job_id}.mp4")
        self.assertEqual(200, status)


if __name__ == "__main__":
    unittest.main()
