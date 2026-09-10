import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from media_studio.config import Settings
from media_studio.drivers.base import DriverError, RunContext
from media_studio.drivers.video_edit import VideoEditDriver, resolve_upload
from media_studio.video_edit import (
    VideoEditError,
    edit_arguments,
    edit_video,
    find_ffmpeg,
    probe_dimensions,
    target_size,
)


def _fake_ffmpeg(directory: str, *, exit_code: int = 0, output: bytes = b"edited-bytes") -> str:
    """Write a fake ffmpeg that records its arguments and writes an output."""
    path = Path(directory) / "fake-ffmpeg"
    script = (
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" > "{directory}/args.txt"\n'
        "for last in \"$@\"; do :; done\n"
        + ("printf 'boom' >&2\n" if exit_code else "")
        + f"printf '{output.decode()}' > \"$last\"\n"
        + f"exit {exit_code}\n"
    )
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


def _fake_ffmpeg_with_stream(directory: str, *, size: str = "720x1280") -> str:
    """A fake ffmpeg that also reports one video stream, like the real one."""
    path = Path(directory) / "fake-ffmpeg-stream"
    script = (
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" > "{directory}/args.txt"\n'
        f'printf "  Stream #0:0(und): Video: h264, yuv420p, {size}, 24 fps\\n" >&2\n'
        'for last in "$@"; do :; done\n'
        'printf "edited-bytes" > "$last"\n'
        "exit 0\n"
    )
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


class VideoEditBrandTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ms-brand-edit-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.source = Path(self.dir) / "clip.mp4"
        self.source.write_bytes(b"source-bytes")
        self.target = Path(self.dir) / "edited.mp4"

    def test_target_size_caps_the_long_side(self):
        self.assertEqual(target_size(1080, 1920, 1920), (1080, 1920))
        self.assertEqual(target_size(2160, 3840, 1920), (1080, 1920))
        self.assertEqual(target_size(1001, 501, 0), (1000, 500))

    def test_arguments_add_the_overlay_as_a_second_input(self):
        arguments = edit_arguments(
            "in.mp4",
            "out.mp4",
            overlay="/tmp/chip.png",
            overlay_position="top-left",
            overlay_margin=12,
        )
        joined = " ".join(arguments)
        self.assertIn("-i in.mp4 -i /tmp/chip.png", joined)
        self.assertIn("-filter_complex", joined)
        self.assertIn("overlay=12:12:format=auto[v]", joined)
        self.assertIn("-map [v]", joined)
        self.assertNotIn("-vf", joined)

    def test_arguments_without_an_overlay_keep_the_simple_filter(self):
        arguments = edit_arguments("in.mp4", "out.mp4")
        joined = " ".join(arguments)
        self.assertIn("-vf", joined)
        self.assertNotIn("-filter_complex", joined)

    def test_overlay_position_covers_every_corner(self):
        for corner, expected in {
            "bottom-right": "W-w-8:H-h-8",
            "bottom-left": "8:H-h-8",
            "top-right": "W-w-8:8",
            "top-left": "8:8",
        }.items():
            joined = " ".join(
                edit_arguments("in.mp4", "out.mp4", overlay="/tmp/c.png", overlay_position=corner, overlay_margin=8)
            )
            self.assertIn(f"overlay={expected}", joined, corner)

    def test_probe_dimensions_reads_the_stream_summary(self):
        binary = _fake_ffmpeg_with_stream(self.dir)
        self.assertEqual(probe_dimensions(binary, str(self.source)), (720, 1280))

    def test_probe_dimensions_reports_a_clip_without_a_video_stream(self):
        binary = _fake_ffmpeg(self.dir)
        with self.assertRaises(VideoEditError):
            probe_dimensions(binary, str(self.source))

    def test_edit_video_bakes_in_the_brand_mark_and_removes_the_overlay(self):
        binary = _fake_ffmpeg_with_stream(self.dir)
        logs = []
        edit_video(
            str(self.source),
            str(self.target),
            ffmpeg=binary,
            max_side=1920,
            log=logs.append,
            brand_label="Locallab",
        )
        arguments = (Path(self.dir) / "args.txt").read_text(encoding="utf-8")
        self.assertIn("-filter_complex", arguments)
        self.assertIn("overlay=", arguments)
        self.assertTrue(any("brand mark" in line for line in logs))
        leftovers = [name for name in os.listdir(self.dir) if name.endswith("-brand.png")]
        self.assertEqual(leftovers, [])

    def test_edit_video_without_a_label_skips_the_brand_mark(self):
        binary = _fake_ffmpeg_with_stream(self.dir)
        edit_video(str(self.source), str(self.target), ffmpeg=binary, max_side=1920)
        arguments = (Path(self.dir) / "args.txt").read_text(encoding="utf-8")
        self.assertNotIn("-filter_complex", arguments)


class VideoEditTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ms-edit-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.source = Path(self.dir) / "clip.mp4"
        self.source.write_bytes(b"source-bytes")
        self.target = Path(self.dir) / "edited.mp4"

    def test_find_ffmpeg_prefers_explicit_binary(self):
        self.assertEqual(find_ffmpeg("/opt/ffmpeg"), "/opt/ffmpeg")

    def test_find_ffmpeg_reports_missing_binary(self):
        with patch.dict(os.environ, {"MEDIA_STUDIO_FFMPEG": ""}, clear=False):
            with patch("media_studio.video_edit.shutil.which", return_value=None):
                with patch.dict("sys.modules", {"imageio_ffmpeg": None}):
                    with self.assertRaises(VideoEditError):
                        find_ffmpeg()

    def test_arguments_cap_long_side_and_convert(self):
        arguments = edit_arguments("in.mp4", "out.mp4", max_side=1280, max_seconds=45)
        joined = " ".join(arguments)
        self.assertIn("force_original_aspect_ratio=decrease", joined)
        self.assertIn("force_divisible_by=2", joined)
        self.assertIn("-c:v libx264", joined)
        self.assertIn("-movflags +faststart", joined)
        self.assertIn("-map_metadata -1", joined)
        self.assertEqual(arguments[-3:-1], ["-t", "45"])
        self.assertEqual(arguments[-1], "out.mp4")

    def test_arguments_without_caps_keep_even_dimensions(self):
        arguments = edit_arguments("in.mp4", "out.mp4", max_side=0, max_seconds=0)
        joined = " ".join(arguments)
        self.assertIn("scale=trunc(iw/2)*2:trunc(ih/2)*2", joined)
        self.assertNotIn("-t ", joined)

    def test_edit_video_runs_ffmpeg_and_writes_output(self):
        binary = _fake_ffmpeg(self.dir)
        edit_video(str(self.source), str(self.target), ffmpeg=binary)
        self.assertEqual(self.target.read_bytes(), b"edited-bytes")
        recorded = Path(self.dir, "args.txt").read_text().splitlines()
        self.assertIn("-map_metadata", recorded)

    def test_edit_video_reports_missing_source(self):
        with self.assertRaises(VideoEditError):
            edit_video(str(Path(self.dir) / "missing.mp4"), str(self.target))

    def test_edit_video_reports_ffmpeg_failure(self):
        binary = _fake_ffmpeg(self.dir, exit_code=1)
        with self.assertRaises(VideoEditError) as caught:
            edit_video(str(self.source), str(self.target), ffmpeg=binary)
        self.assertIn("exit code 1", str(caught.exception))
        self.assertIn("boom", str(caught.exception))

    def test_edit_video_reports_empty_output(self):
        binary = _fake_ffmpeg(self.dir, output=b"")
        with self.assertRaises(VideoEditError):
            edit_video(str(self.source), str(self.target), ffmpeg=binary)


class VideoEditDriverTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ms-edit-driver-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.settings = Settings(data_dir=self.dir, drivers=("video-edit",))
        self.uploads = Path(self.dir) / "uploads"
        self.driver = VideoEditDriver()

    def _store_upload(self, upload_id="a" * 32, name="source.mp4", body=b"clip"):
        directory = self.uploads / upload_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_bytes(body)
        return upload_id

    def test_resolve_upload_rejects_bad_ids(self):
        with self.assertRaises(DriverError):
            resolve_upload(self.settings, "../../etc/passwd")
        with self.assertRaises(DriverError):
            resolve_upload(self.settings, "")

    def test_resolve_upload_reports_missing_clip(self):
        with self.assertRaises(DriverError):
            resolve_upload(self.settings, "b" * 32)

    def test_run_prepares_the_uploaded_clip(self):
        upload_id = self._store_upload()
        binary = _fake_ffmpeg(self.dir, output=b"normalised")
        self.settings = Settings(
            data_dir=self.dir, drivers=("video-edit",), ffmpeg_binary=binary
        )
        work_dir = Path(self.dir) / "artifacts" / "job1"
        ctx = RunContext(
            prompt="prepare the clip",
            params={"upload_id": upload_id},
            work_dir=str(work_dir),
            settings=self.settings,
        )
        artifacts = self.driver.run(ctx)
        self.assertEqual(artifacts, [("edited-source.mp4", "video")])
        self.assertEqual((work_dir / "edited-source.mp4").read_bytes(), b"normalised")

    def test_run_bakes_the_brand_mark_when_the_label_is_configured(self):
        upload_id = self._store_upload()
        binary = _fake_ffmpeg_with_stream(self.dir)
        self.settings = Settings(
            data_dir=self.dir,
            drivers=("video-edit",),
            ffmpeg_binary=binary,
            brand_label="Locallab",
        )
        work_dir = Path(self.dir) / "artifacts" / "job2"
        ctx = RunContext(
            prompt="prepare the clip",
            params={"upload_id": upload_id},
            work_dir=str(work_dir),
            settings=self.settings,
        )
        self.assertEqual(self.driver.run(ctx), [("edited-source.mp4", "video")])
        arguments = (Path(self.dir) / "args.txt").read_text(encoding="utf-8")
        self.assertIn("overlay=", arguments)

    def test_run_skips_the_brand_mark_when_the_job_disables_it(self):
        upload_id = self._store_upload()
        binary = _fake_ffmpeg_with_stream(self.dir)
        self.settings = Settings(
            data_dir=self.dir,
            drivers=("video-edit",),
            ffmpeg_binary=binary,
            brand_label="Locallab",
        )
        work_dir = Path(self.dir) / "artifacts" / "job3"
        ctx = RunContext(
            prompt="prepare the clip",
            params={"upload_id": upload_id, "brand": "false"},
            work_dir=str(work_dir),
            settings=self.settings,
        )
        self.driver.run(ctx)
        arguments = (Path(self.dir) / "args.txt").read_text(encoding="utf-8")
        self.assertNotIn("overlay=", arguments)

    def test_run_requires_an_upload_id(self):
        ctx = RunContext(prompt="x", params={}, work_dir=self.dir, settings=self.settings)
        with self.assertRaises(DriverError):
            self.driver.run(ctx)


if __name__ == "__main__":
    unittest.main()
