import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path

from media_studio.config import Settings
from media_studio.drivers.base import DriverError, RunContext
from media_studio.drivers.timeline_video import TimelineVideoDriver, upload_root


def _fake_ffmpeg(directory: str) -> str:
    path = Path(directory) / "fake-ffmpeg"
    script = (
        "#!/bin/sh\n"
        'for last in "$@"; do :; done\n'
        'printf "render-bytes" > "$last"\n'
        "exit 0\n"
    )
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


def _settings(directory: str, ffmpeg: str) -> Settings:
    return Settings(data_dir=directory, ffmpeg_binary=ffmpeg)


def _timeline() -> dict:
    return {
        "version": 1,
        "meta": {"aspect_ratio": "9:16"},
        "scenes": [
            {"duration": 2, "narration": "First scene"},
            {"duration": 2, "narration": "Second scene", "transition": "fade"},
        ],
    }


class TimelineDriverConfigTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ms-timeline-driver-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.ffmpeg = _fake_ffmpeg(self.dir)

    def _context(self, params: dict, work_dir: str | None = None) -> RunContext:
        return RunContext(
            prompt="timeline render",
            params=params,
            work_dir=work_dir or self.dir,
            settings=_settings(self.dir, self.ffmpeg),
            log=lambda _message: None,
        )

    def test_missing_timeline_is_a_config_error(self):
        driver = TimelineVideoDriver()
        with self.assertRaises(DriverError) as caught:
            driver.run(self._context({"prompt": "no timeline here"}))
        self.assertEqual(caught.exception.step, "config")

    def test_invalid_timeline_is_a_validation_error(self):
        driver = TimelineVideoDriver()
        with self.assertRaises(DriverError) as caught:
            driver.run(self._context({"timeline": {"version": 1, "scenes": []}}))
        self.assertEqual(caught.exception.step, "validate")

    def test_timeline_accepts_a_json_string(self):
        driver = TimelineVideoDriver()
        import json

        artifacts = driver.run(self._context({"timeline": json.dumps(_timeline())}))
        self.assertEqual(artifacts, [("timeline-video.mp4", "video")])

    def test_render_produces_one_video_artifact(self):
        driver = TimelineVideoDriver()
        artifacts = driver.run(self._context({"timeline": _timeline()}))
        self.assertEqual(len(artifacts), 1)
        name, kind = artifacts[0]
        self.assertEqual(kind, "video")
        self.assertTrue(os.path.isfile(os.path.join(self.dir, name)))


class TimelineAssetResolutionTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ms-timeline-assets-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.uploads = upload_root(Settings(data_dir=self.dir))
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.roots = [self.uploads, Path(self.dir) / "work"]

    def test_upload_id_resolves_to_the_stored_file(self):
        upload_id = "a" * 32
        directory = self.uploads / upload_id
        directory.mkdir()
        (directory / "clip.png").write_bytes(b"image-bytes")
        resolved = TimelineVideoDriver._resolve({"upload_id": upload_id}, self.uploads, self.roots)
        self.assertEqual(resolved, str(directory / "clip.png"))

    def test_upload_id_absent_returns_none(self):
        self.assertIsNone(
            TimelineVideoDriver._resolve({"upload_id": "b" * 32}, self.uploads, self.roots)
        )

    def test_asset_path_outside_the_roots_is_rejected(self):
        outside = Path(self.dir) / "secret.txt"
        outside.write_text("not allowed")
        self.assertIsNone(
            TimelineVideoDriver._resolve({"asset_path": str(outside)}, self.uploads, self.roots)
        )

    def test_asset_path_inside_the_uploads_root_is_allowed(self):
        inside = self.uploads / "voice.mp3"
        inside.write_bytes(b"audio-bytes")
        self.assertEqual(
            TimelineVideoDriver._resolve({"asset_path": str(inside)}, self.uploads, self.roots),
            str(inside),
        )

    def test_traversal_attempt_is_rejected(self):
        self.assertIsNone(
            TimelineVideoDriver._resolve(
                {"asset_path": str(self.uploads / ".." / ".." / "etc" / "passwd")},
                self.uploads,
                self.roots,
            )
        )


if __name__ == "__main__":
    unittest.main()
