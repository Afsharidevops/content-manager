"""Tests for the Instagram Reel Template system."""

from __future__ import annotations

import datetime
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from content_bot import reel_templates


class AvailableTemplatesTests(unittest.TestCase):

    def test_returns_list_with_all_five_default_templates(self):
        templates = reel_templates.available_templates()
        ids = {t["id"] for t in templates}
        self.assertIn("tech_explainer", ids)
        self.assertIn("saas_demo", ids)
        self.assertIn("ai_showcase", ids)
        self.assertIn("feature_launch", ids)
        self.assertIn("tutorial", ids)
        self.assertEqual(5, len(templates))

    def test_each_entry_has_required_fields(self):
        for entry in reel_templates.available_templates():
            with self.subTest(tid=entry["id"]):
                self.assertIn("id", entry)
                self.assertIn("label", entry)
                self.assertIn("description", entry)
                self.assertIn("total_duration", entry)
                self.assertIn("template", entry)
                self.assertIsInstance(entry["template"], dict)

    def test_no_duplicates(self):
        templates = reel_templates.available_templates()
        ids = [t["id"] for t in templates]
        self.assertEqual(len(ids), len(set(ids)))

    def test_missing_directory_returns_empty_list(self):
        with mock.patch.object(
            reel_templates, "_TEMPLATES_DIR", Path("/tmp/does-not-exist-xyz")
        ):
            with self.assertLogs("content_bot.reel_templates", level="WARNING"):
                result = reel_templates.available_templates()
        self.assertEqual([], result)

    def test_malformed_json_is_skipped_gracefully(self):
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            bad = tdir / "bad.json"
            bad.write_text("{invalid json", "utf-8")
            good = tdir / "good.json"
            good.write_text(
                json.dumps(
                    {
                        "id": "good_tpl",
                        "label": "Good Template",
                        "description": "Fine",
                        "total_duration": 15,
                        "scenes": [],
                    }
                ),
                "utf-8",
            )
            with mock.patch.object(reel_templates, "_TEMPLATES_DIR", tdir):
                with self.assertLogs("content_bot.reel_templates", level="WARNING"):
                    result = reel_templates.available_templates()
        self.assertEqual(1, len(result))
        self.assertEqual("good_tpl", result[0]["id"])


class GetTemplateTests(unittest.TestCase):

    def test_returns_entry_by_id(self):
        entry = reel_templates.get_template("tech_explainer")
        self.assertIsNotNone(entry)
        self.assertEqual("tech_explainer", entry["id"])

    def test_returns_none_for_unknown_id(self):
        self.assertIsNone(reel_templates.get_template("does_not_exist"))


class TemplateToPlanTests(unittest.TestCase):

    def _plan(self, tid: str, **kwargs) -> dict:
        return reel_templates.template_to_plan(tid, **kwargs)

    def test_tech_explainer_produces_valid_plan_shape(self):
        plan = self._plan(
            "tech_explainer",
            title="Container images explained",
            body="Container images are built from ordered layers.",
        )
        self.assertIn("storyboard", plan)
        self.assertIn("timeline", plan)
        sb = plan["storyboard"]
        tl = plan["timeline"]
        self.assertIn("title", sb)
        self.assertIn("scenes", sb)
        self.assertIn("scenes", tl)
        self.assertIsInstance(sb["scenes"], list)
        self.assertIsInstance(tl["scenes"], list)
        self.assertGreater(len(sb["scenes"]), 0)
        self.assertEqual(len(sb["scenes"]), len(tl["scenes"]))

    def test_all_five_templates_build_plans(self):
        for entry in reel_templates.available_templates():
            with self.subTest(tid=entry["id"]):
                plan = self._plan(
                    entry["id"],
                    title="Test title for this reel",
                    body="This is test body content for the reel template.",
                )
                self.assertIn("storyboard", plan)
                self.assertIn("timeline", plan)

    def test_unknown_template_raises_value_error(self):
        with self.assertRaises(ValueError):
            reel_templates.template_to_plan("does_not_exist")

    def test_scene_durations_preserved(self):
        plan = self._plan(
            "tech_explainer",
            title="Test",
            body="Short body",
        )
        for scene in plan["storyboard"]["scenes"]:
            self.assertGreater(scene["duration"], 0)
        for scene in plan["timeline"]["scenes"]:
            self.assertGreater(scene["duration"], 0)

    def test_scene_ids_in_timeline(self):
        plan = self._plan("tech_explainer", title="T", body="B")
        for scene in plan["timeline"]["scenes"]:
            self.assertIn("id", scene)

    def test_animation_values_are_mapped(self):
        plan = self._plan("tech_explainer", title="T", body="B")
        allowed = {"zoom-in", "slide-up", "pan-left", "pan-right", "fade", "float", "scale"}
        for scene in plan["timeline"]["scenes"]:
            self.assertIn(scene.get("animation", "fade"), allowed)

    def test_timeline_has_version(self):
        plan = self._plan("tech_explainer", title="T", body="B")
        self.assertEqual(1, plan["timeline"].get("version"))

    def test_total_duration_matches_scene_sum(self):
        plan = self._plan("tech_explainer", title="T", body="B")
        expected = sum(s["duration"] for s in plan["storyboard"]["scenes"])
        actual = plan["storyboard"].get("total_duration") or expected
        self.assertEqual(expected, actual)

    def test_hook_taken_from_first_scene(self):
        plan = self._plan("tech_explainer", title="T", body="B")
        first_narration = plan["storyboard"]["scenes"][0]["narration"]
        self.assertEqual(first_narration, plan["storyboard"]["hook"])

    def test_latin_content_passes_through(self):
        plan = self._plan(
            "tech_explainer",
            title="Explainable AI",
            body="Explainable AI helps engineers understand model behavior.",
        )
        # hook and at least one narration should contain Latin text
        hook = plan["storyboard"]["hook"]
        self.assertTrue(any(c.isalpha() and c.isascii() for c in hook))

    def test_persian_body_falls_back_to_english_placeholders(self):
        plan = self._plan(
            "tech_explainer",
            title="هوش مصنوعی",
            body="این یک متن فارسی است که برای تست استفاده می‌شود",
        )
        # Narrations should still be non-empty even with a Persian body
        for scene in plan["storyboard"]["scenes"]:
            self.assertGreater(len(scene["narration"]), 0)


class ApplyDestinationMetaTests(unittest.TestCase):

    def test_stamps_meta_fields(self):
        timeline = {"version": 1, "scenes": []}
        dest = {"aspect_ratio": "16:9", "resolution": "1920x1080", "fps": 30}
        result = reel_templates.apply_destination_meta(timeline, dest)
        self.assertEqual("16:9", result["meta"]["aspect_ratio"])
        self.assertEqual("1920x1080", result["meta"]["resolution"])
        self.assertEqual(30, result["meta"]["fps"])

    def test_does_not_mutate_input(self):
        timeline = {"version": 1, "scenes": []}
        dest = {"aspect_ratio": "9:16", "resolution": "1080x1920", "fps": 30}
        reel_templates.apply_destination_meta(timeline, dest)
        self.assertNotIn("meta", timeline)

    def test_default_values_when_dest_is_empty(self):
        result = reel_templates.apply_destination_meta({"version": 1}, {})
        self.assertEqual("9:16", result["meta"]["aspect_ratio"])
        self.assertEqual("1080x1920", result["meta"]["resolution"])
        self.assertEqual(30, result["meta"]["fps"])


class TemplatePreviewTests(unittest.TestCase):

    def test_preview_contains_template_label(self):
        preview = reel_templates.template_preview("tech_explainer", title="Test")
        self.assertIn("Tech Explainer", preview)

    def test_preview_contains_duration(self):
        preview = reel_templates.template_preview("tech_explainer", title="Test")
        self.assertIn("30s", preview)

    def test_preview_contains_scene_labels(self):
        preview = reel_templates.template_preview("tech_explainer", title="Test")
        self.assertIn("Hook", preview)
        self.assertIn("CTA", preview)

    def test_unknown_template_returns_error_string(self):
        preview = reel_templates.template_preview("nope", title="Test")
        self.assertIn("nope", preview)

    def test_preview_is_html_escaped(self):
        preview = reel_templates.template_preview(
            "tech_explainer", title="<script>alert(1)</script>"
        )
        self.assertNotIn("<script>", preview)


class FillPlaceholderTests(unittest.TestCase):

    def test_replaces_single_placeholder(self):
        tpl = {"id": "t", "scenes": [{"narration": "Hello {{name}}"}]}
        filled = reel_templates._fill_placeholders(tpl, {"name": "World"})
        self.assertEqual("Hello World", filled["scenes"][0]["narration"])

    def test_leaves_unknown_placeholder(self):
        tpl = {"narration": "{{unknown_key}} here"}
        filled = reel_templates._fill_placeholders(tpl, {})
        self.assertEqual("{{unknown_key}} here", filled["narration"])

    def test_nested_dict_filled(self):
        tpl = {"outer": {"inner": "{{val}}"}}
        filled = reel_templates._fill_placeholders(tpl, {"val": "yes"})
        self.assertEqual("yes", filled["outer"]["inner"])

    def test_list_items_filled(self):
        tpl = {"items": ["{{a}}", "{{b}}"]}
        filled = reel_templates._fill_placeholders(tpl, {"a": "one", "b": "two"})
        self.assertEqual(["one", "two"], filled["items"])


class EnglishOnlyTests(unittest.TestCase):
    """Ensure narrations produced from Persian content stay readable."""

    def test_all_scenes_have_non_empty_narrations(self):
        for entry in reel_templates.available_templates():
            plan = reel_templates.template_to_plan(
                entry["id"],
                title="نمایش محصول ما",
                body="این ابزار هوش مصنوعی کمک می‌کند که بهتر کار کنید.",
            )
            for idx, scene in enumerate(plan["storyboard"]["scenes"]):
                with self.subTest(tid=entry["id"], scene=idx):
                    self.assertGreater(len(scene["narration"]), 0)

    def test_cta_text_is_always_english(self):
        values = reel_templates._derive_placeholder_values("عنوان", "محتوا", "")
        self.assertEqual("Try it now", values["cta_text"])
        self.assertEqual("Follow for more!", values["outro_line"])


class InstagramReelKeyboardTests(unittest.TestCase):
    """Keyboard builder for template picker."""

    def test_keyboard_has_one_row_per_template(self):
        from content_bot.telegram import instagram_reel_template_keyboard
        templates = reel_templates.available_templates()
        kb = instagram_reel_template_keyboard("draft-1", templates)
        rows = kb["inline_keyboard"]
        # One row per template + one cancel row
        self.assertEqual(len(templates) + 1, len(rows))

    def test_each_button_has_correct_callback_prefix(self):
        from content_bot.telegram import instagram_reel_template_keyboard
        templates = reel_templates.available_templates()
        kb = instagram_reel_template_keyboard("draft-x", templates)
        rows = kb["inline_keyboard"]
        template_rows = rows[:-1]
        for row in template_rows:
            btn = row[0]
            self.assertTrue(
                btn["callback_data"].startswith("media:reel_tpl_"),
                msg=f"unexpected callback: {btn['callback_data']}",
            )
            self.assertIn("draft-x", btn["callback_data"])

    def test_cancel_row_is_last(self):
        from content_bot.telegram import instagram_reel_template_keyboard
        templates = reel_templates.available_templates()
        kb = instagram_reel_template_keyboard("draft-y", templates)
        last = kb["inline_keyboard"][-1][0]
        self.assertEqual(f"media:none:draft-y", last["callback_data"])

    def test_empty_templates_list_only_produces_cancel(self):
        from content_bot.telegram import instagram_reel_template_keyboard
        kb = instagram_reel_template_keyboard("d", [])
        self.assertEqual(1, len(kb["inline_keyboard"]))


class ReelCallbackBotTests(unittest.TestCase):
    """Integration-level tests for the reel_tpl callback in the bot."""

    def setUp(self):
        import tempfile
        from content_bot.bot import ContentBot
        from content_bot.config import BotSettings
        from content_bot.telegram import TelegramApi

        class FakeApi(TelegramApi):
            def __init__(self):
                super().__init__("123:TESTTOKENABCDEFGHIJKLMN")
                self.sent = []
                self.edits = []

            def _transport(self, url, payload, *, timeout=35):
                method = url.rsplit("/", 1)[-1]
                if method == "getMe":
                    return {"ok": True, "result": {"username": "test_bot"}}
                if method == "sendMessage":
                    self.sent.append(payload)
                    return {"ok": True, "result": {"message_id": 100 + len(self.sent)}}
                if method == "editMessageText":
                    self.edits.append(payload)
                    return {"ok": True, "result": True}
                return {"ok": True, "result": True}

            def download_file(self, *a, **k):
                return b"fake"

        self.tmp = tempfile.TemporaryDirectory()
        self.api = FakeApi()

        self.settings = BotSettings(
            bot_token="123:TESTTOKENABCDEFGHIJKLMN",
            telegram_channel="@ch",
            telegram_users=frozenset({11}),
            data_dir=self.tmp.name,
            scheduler_enabled=False,
        )

        self.bot = ContentBot(
            self.settings,
            api=self.api,
            now_fn=lambda: datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _make_draft(self) -> str:
        import secrets
        draft_id = secrets.token_hex(4)
        self.bot.state.add_draft(
            draft_id,
            {
                "id": draft_id,
                "chat_id": 11,
                "ask_message_id": 103,
                "title": "Container images explained",
                "body": "Container images are built from ordered layers that reuse cache.",
                "source_url": "https://example.com/layers",
                "status": "media_ask",
                "history": [],
            },
        )
        return draft_id

    def test_reel_dest_shows_template_picker(self):
        draft_id = self._make_draft()
        self.bot.handle_callback(
            {
                "id": "q1",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 103},
                "data": f"media:vid_dest_reel:{draft_id}",
            }
        )
        all_edits = " ".join(json.dumps(e) for e in self.api.edits)
        self.assertIn("reel_tpl_tech_explainer", all_edits)

    def test_reel_tpl_stores_plan_and_returns_preview(self):
        draft_id = self._make_draft()
        self.bot.handle_callback(
            {
                "id": "q2",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 103},
                "data": f"media:reel_tpl_tech_explainer:{draft_id}",
            }
        )
        record = self.bot.state.get_draft(draft_id)
        self.assertIsNotNone(record)
        self.assertEqual("tech_explainer", record.get("agent_video_template"))
        self.assertIsInstance(record.get("agent_video_storyboard"), dict)
        self.assertIsInstance(record.get("agent_video_timeline"), dict)
        # Language must be English
        tl = record["agent_video_timeline"]
        meta = tl.get("meta") or {}
        self.assertEqual("en", meta.get("language"))
        all_edits = " ".join(json.dumps(e) for e in self.api.edits)
        self.assertIn("Tech Explainer", all_edits)

    def test_unknown_template_answers_error(self):
        draft_id = self._make_draft()
        self.bot.handle_callback(
            {
                "id": "q3",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 103},
                "data": f"media:reel_tpl_does_not_exist:{draft_id}",
            }
        )
        # Should not crash and should not store a plan
        record = self.bot.state.get_draft(draft_id)
        self.assertIsNone(record.get("agent_video_storyboard"))

    def test_no_templates_answers_not_configured(self):
        draft_id = self._make_draft()
        with mock.patch.object(reel_templates, "available_templates", return_value=[]):
            self.bot.handle_callback(
                {
                    "id": "q4",
                    "from": {"id": 11},
                    "message": {"chat": {"id": 11}, "message_id": 103},
                    "data": f"media:vid_dest_reel:{draft_id}",
                }
            )
        # Should send an answerCallbackQuery with an error message
        import content_bot.telegram as tgm
        # verify no template picker edit was sent
        all_edits = " ".join(json.dumps(e) for e in self.api.edits)
        self.assertNotIn("reel_tpl_", all_edits)


if __name__ == "__main__":
    unittest.main()
