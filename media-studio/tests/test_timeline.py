import unittest

from media_studio.timeline import (
    TimelineError,
    contains_rtl,
    normalize_timeline,
    resolve_resolution,
    timeline_from_params,
)


def _timeline(**overrides):
    base = {
        "version": 1,
        "meta": {"title": "Short test", "aspect_ratio": "9:16"},
        "scenes": [
            {"duration": 3, "narration": "First scene", "transition": "fade"},
            {"duration": 4, "narration": "Second scene", "transition": "cut"},
        ],
    }
    base.update(overrides)
    return base


class NormalizeTimelineTests(unittest.TestCase):
    def test_fills_defaults(self):
        result = normalize_timeline(_timeline())
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["meta"]["resolution"], "1080x1920")
        self.assertEqual(result["meta"]["fps"], 30)
        self.assertEqual(result["meta"]["subtitle"], True)
        self.assertEqual(result["meta"]["brand"]["position"], "bottom-right")
        self.assertEqual(result["totals"]["scenes"], 2)
        self.assertEqual(result["totals"]["duration_seconds"], 7.0)

    def test_first_scene_transition_is_forced_to_cut(self):
        result = normalize_timeline(_timeline())
        self.assertEqual(result["scenes"][0]["transition"], "cut")
        self.assertEqual(result["scenes"][1]["transition"], "cut")

    def test_text_asset_type_is_inferred_for_narration_only_scenes(self):
        result = normalize_timeline(_timeline())
        self.assertTrue(all(scene["asset_type"] == "text" for scene in result["scenes"]))

    def test_upload_id_infers_image_asset(self):
        result = normalize_timeline(
            _timeline(
                scenes=[
                    {"duration": 2, "narration": "Media scene", "upload_id": "a" * 32},
                ]
            )
        )
        self.assertEqual(result["scenes"][0]["asset_type"], "image")

    def test_unknown_transition_falls_back_to_fade(self):
        result = normalize_timeline(
            _timeline(
                scenes=[
                    {"duration": 2, "narration": "x"},
                    {"duration": 2, "narration": "y", "transition": "explode"},
                ]
            )
        )
        self.assertEqual(result["scenes"][1]["transition"], "fade")

    def test_duration_is_clamped_to_the_supported_range(self):
        result = normalize_timeline(
            _timeline(
                scenes=[
                    {"duration": 0.01, "narration": "too short"},
                    {"duration": 9999, "narration": "too long"},
                ]
            )
        )
        self.assertEqual(result["scenes"][0]["duration"], 0.5)
        self.assertEqual(result["scenes"][1]["duration"], 120.0)

    def test_empty_scene_narration_gets_a_placeholder(self):
        result = normalize_timeline(_timeline(scenes=[{"duration": 2}]))
        self.assertEqual(result["scenes"][0]["narration"], "Scene 1")

    def test_missing_scenes_raises(self):
        with self.assertRaises(TimelineError) as caught:
            normalize_timeline({"version": 1, "scenes": []})
        self.assertIn("no scenes", str(caught.exception))

    def test_non_object_timeline_raises(self):
        with self.assertRaises(TimelineError):
            normalize_timeline(["not", "a", "timeline"])

    def test_newer_schema_version_raises(self):
        with self.assertRaises(TimelineError) as caught:
            normalize_timeline(_timeline(version=99))
        self.assertIn("newer than", str(caught.exception))

    def test_too_many_scenes_raises(self):
        scenes = [{"duration": 1, "narration": f"s{index}"} for index in range(121)]
        with self.assertRaises(TimelineError) as caught:
            normalize_timeline(_timeline(scenes=scenes))
        self.assertIn("limit", str(caught.exception))

    def test_audio_block_is_normalized(self):
        result = normalize_timeline(
            _timeline(audio={"upload_id": "b" * 32, "volume": 9.0})
        )
        self.assertEqual(result["audio"]["upload_id"], "b" * 32)
        self.assertEqual(result["audio"]["volume"], 4.0)

    def test_resolution_override_wins_over_aspect(self):
        result = normalize_timeline(
            _timeline(meta={"aspect_ratio": "16:9", "resolution": "720x1280"})
        )
        self.assertEqual(result["meta"]["resolution"], "720x1280")

    def test_unknown_aspect_falls_back(self):
        self.assertEqual(resolve_resolution({"aspect_ratio": "3:7"}), (1080, 1920))

    def test_contains_rtl_detects_persian(self):
        self.assertTrue(contains_rtl("سلام دنیا"))
        self.assertFalse(contains_rtl("hello world"))

    def test_timeline_from_params_accepts_json_string(self):
        raw = timeline_from_params({"timeline": '{"version": 1, "scenes": []}'})
        self.assertEqual(raw, {"version": 1, "scenes": []})

    def test_timeline_from_params_rejects_broken_json(self):
        with self.assertRaises(TimelineError):
            timeline_from_params({"timeline": "{not json"})

    def test_timeline_from_params_without_timeline(self):
        self.assertIsNone(timeline_from_params({"prompt": "no timeline"}))


if __name__ == "__main__":
    unittest.main()
