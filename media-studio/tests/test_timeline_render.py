import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path

from media_studio.timeline import normalize_timeline
from media_studio.timeline_render import (
    _assemble_arguments,
    _segment_arguments,
    render_timeline,
)


def _fake_ffmpeg(directory: str) -> str:
    """A fake ffmpeg that records the arguments of its last call."""
    path = Path(directory) / "fake-ffmpeg"
    script = (
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" > "{directory}/args.txt"\n'
        'for last in "$@"; do :; done\n'
        'printf "render-bytes" > "$last"\n'
        "exit 0\n"
    )
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


def _scene(duration: float, **overrides) -> dict:
    scene = {"id": 1, "duration": duration, "narration": "نریشن فارسی", "visual": ""}
    scene.update(overrides)
    return scene


class SegmentArgumentsTests(unittest.TestCase):
    def test_still_scene_loops_the_image_and_bounds_duration(self):
        arguments = _segment_arguments(
            ffmpeg="ffmpeg",
            scene=_scene(4.0, asset_type="text"),
            background="/tmp/card.png",
            caption=None,
            size=(1080, 1920),
            fps=30,
            destination="/tmp/segment.mp4",
        )
        self.assertIn("-loop", arguments)
        self.assertIn("-framerate", arguments)
        self.assertIn("30", arguments)
        self.assertIn("-t", arguments)
        self.assertIn("4.000", arguments)
        self.assertEqual(arguments[-1], "/tmp/segment.mp4")

    def test_video_scene_trims_without_looping(self):
        arguments = _segment_arguments(
            ffmpeg="ffmpeg",
            scene=_scene(2.5, asset_type="video"),
            background="/tmp/clip.mp4",
            caption=None,
            size=(1920, 1080),
            fps=30,
            destination="/tmp/segment.mp4",
        )
        self.assertNotIn("-loop", arguments)
        self.assertIn("-t", arguments)
        self.assertIn("2.500", arguments)

    def test_caption_is_overlaid_at_the_bottom(self):
        arguments = _segment_arguments(
            ffmpeg="ffmpeg",
            scene=_scene(3.0, asset_type="image"),
            background="/tmp/photo.png",
            caption="/tmp/caption.png",
            size=(1080, 1920),
            fps=30,
            destination="/tmp/segment.mp4",
        )
        graph = arguments[arguments.index("-filter_complex") + 1]
        self.assertIn("overlay=0:H-h", graph)
        self.assertIn("[1:v]", graph)

    def test_zoom_animation_uses_zoompan(self):
        arguments = _segment_arguments(
            ffmpeg="ffmpeg",
            scene=_scene(3.0, asset_type="text", animation="zoom-in"),
            background="/tmp/card.png",
            caption=None,
            size=(1080, 1920),
            fps=30,
            destination="/tmp/segment.mp4",
        )
        graph = arguments[arguments.index("-filter_complex") + 1]
        self.assertIn("zoompan", graph)


class AssembleArgumentsTests(unittest.TestCase):
    def test_xfade_chain_offsets_shrink_with_transitions(self):
        scenes = [
            {"duration": 4.0, "transition": "cut"},
            {"duration": 4.0, "transition": "cut"},
            {"duration": 4.0, "transition": "fade"},
        ]
        arguments = _assemble_arguments(
            ffmpeg="ffmpeg",
            segments=["/tmp/s0.mp4", "/tmp/s1.mp4", "/tmp/s2.mp4"],
            scenes=scenes,
            size=(1080, 1920),
            fps=30,
            destination="/tmp/out.mp4",
        )
        graph = arguments[arguments.index("-filter_complex") + 1]
        self.assertNotIn("transition=dissolve", graph)
        self.assertIn("transition=fade", graph)
        self.assertEqual(graph.count("xfade="), 2)
        # First join: 4.0s scene minus the 0.04s cut overlap.
        self.assertIn("offset=3.960", graph)
        # Second join: 7.96s of joined output minus the 0.5s fade.
        self.assertIn("offset=7.460", graph)

    def test_audio_input_is_padded_and_shortest(self):
        scenes = [{"duration": 3.0, "transition": "cut"}]
        arguments = _assemble_arguments(
            ffmpeg="ffmpeg",
            segments=["/tmp/s0.mp4"],
            scenes=scenes,
            size=(1080, 1920),
            fps=30,
            destination="/tmp/out.mp4",
            audio="/tmp/voice.mp3",
            audio_volume=0.8,
        )
        self.assertIn("-af", arguments)
        audio_filter = arguments[arguments.index("-af") + 1]
        self.assertIn("apad", audio_filter)
        self.assertIn("volume=0.80", audio_filter)
        self.assertIn("-shortest", arguments)

    def test_brand_overlay_is_appended_to_the_chain(self):
        scenes = [
            {"duration": 3.0, "transition": "cut"},
            {"duration": 3.0, "transition": "fade"},
        ]
        arguments = _assemble_arguments(
            ffmpeg="ffmpeg",
            segments=["/tmp/s0.mp4", "/tmp/s1.mp4"],
            scenes=scenes,
            size=(1080, 1920),
            fps=30,
            destination="/tmp/out.mp4",
            brand_png="/tmp/brand.png",
            brand_position="top-left",
            brand_margin=12,
        )
        graph = arguments[arguments.index("-filter_complex") + 1]
        self.assertIn("overlay=12:12", graph)
        self.assertIn("[vout]", graph)

    def test_single_scene_uses_null_filter(self):
        arguments = _assemble_arguments(
            ffmpeg="ffmpeg",
            segments=["/tmp/s0.mp4"],
            scenes=[{"duration": 3.0, "transition": "cut"}],
            size=(1080, 1920),
            fps=30,
            destination="/tmp/out.mp4",
        )
        graph = arguments[arguments.index("-filter_complex") + 1]
        self.assertEqual(graph, "[0:v]null[vout]")


class RenderTimelineTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ms-timeline-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.ffmpeg = _fake_ffmpeg(self.dir)

    def test_render_writes_the_output_and_reports_a_summary(self):
        timeline = normalize_timeline(
            {
                "version": 1,
                "meta": {"aspect_ratio": "16:9", "title": "Report"},
                "scenes": [
                    {"duration": 2, "narration": "One"},
                    {"duration": 3, "narration": "Two", "transition": "fade"},
                ],
            }
        )
        messages: list[str] = []
        destination = os.path.join(self.dir, "out.mp4")
        summary = render_timeline(
            timeline,
            destination,
            work_dir=self.dir,
            ffmpeg=self.ffmpeg,
            log=messages.append,
            resolve_asset=lambda _mapping: None,
        )
        self.assertTrue(os.path.isfile(destination))
        self.assertEqual(summary["scenes"], 2)
        self.assertEqual(summary["resolution"], "1920x1080")
        self.assertEqual(summary["audio"], False)
        self.assertEqual(summary["size_bytes"], os.path.getsize(destination))
        self.assertTrue(any("scene 1/2 rendered" in message for message in messages))

    def test_missing_asset_falls_back_to_a_generated_card(self):
        timeline = normalize_timeline(
            {
                "version": 1,
                "meta": {},
                "scenes": [
                    {"duration": 2, "narration": "image scene", "upload_id": "a" * 32},
                ],
            }
        )
        messages: list[str] = []
        summary = render_timeline(
            timeline,
            os.path.join(self.dir, "out.mp4"),
            work_dir=self.dir,
            ffmpeg=self.ffmpeg,
            log=messages.append,
            resolve_asset=lambda _mapping: None,
        )
        self.assertTrue(summary["scenes"] == 1)
        self.assertTrue(any("falling back" in message for message in messages))

    def test_audio_asset_is_reported(self):
        audio_file = Path(self.dir) / "voice.mp3"
        audio_file.write_bytes(b"audio-bytes")
        timeline = normalize_timeline(
            {
                "version": 1,
                "meta": {},
                "audio": {"asset_path": str(audio_file)},
                "scenes": [{"duration": 2, "narration": "One"}],
            }
        )
        summary = render_timeline(
            timeline,
            os.path.join(self.dir, "out.mp4"),
            work_dir=self.dir,
            ffmpeg=self.ffmpeg,
            log=lambda _message: None,
            resolve_asset=lambda mapping: str(audio_file) if mapping.get("asset_path") else None,
        )
        self.assertTrue(summary["audio"])


if __name__ == "__main__":
    unittest.main()
