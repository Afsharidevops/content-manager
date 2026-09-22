"""Tests for the operator panel: editors, auth, exposure, actions, HTTP API."""

import http.client
import json
import os
import stat
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import yaml

from panel import __version__
from panel.actions import ActionError, ActionRunner
from panel import drafts as drafts_mod
from panel.editors import ConfigStore, EditError, EnvStore
from panel.platforms import PLATFORMS, PlatformStore, _linkedin_author, platform_for
from panel.server import PanelApp, PanelHandler
from panel.stack import CommandError, StackView

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = REPO_ROOT / "panel" / "static"

VALID_POLICY = """\
pipeline:
  timezone: Asia/Tehran
  daily_proposal_time: "09:00"
routines:
  - id: morning
    platform: telegram
    cadence: daily
    time: "09:00"
    count: 2
    media: auto
"""

VALID_TOOLS = json.dumps(
    {
        "schema_version": 1,
        "tools": [
            {
                "id": "n8n-mcp",
                "kind": "mcp",
                "url": "http://n8n:5678/mcp/hermes",
                "auth": {"token_env": "N8N_TRIGGER_MCP_TOKEN"},
            }
        ],
    }
)


def make_root() -> Path:
    root = Path(tempfile.mkdtemp(prefix="panel-test-"))
    (root / "data" / "content-manager" / "config").mkdir(parents=True)
    (root / "data" / "content-bot").mkdir(parents=True)
    (root / ".env").write_text(
        "COMPOSE_PROFILES=content\n"
        "CONTENT_BOT_TOKEN=super-secret-value\n"
        "MEDIA_STUDIO_PORT=8850\n",
        encoding="utf-8",
    )
    return root


class ConfigStoreTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.slug = self.root.name
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.store = ConfigStore(self.root)

    def config_path(self, name: str) -> Path:
        return self.root / "data" / "content-manager" / "config" / name

    def test_listing_reports_every_managed_file(self):
        names = {row["name"] for row in self.store.listing()}
        self.assertEqual(names, {"editorial-policy", "sources", "categories", "tools"})
        self.assertTrue(all(row["exists"] is False for row in self.store.listing()))

    def test_policy_write_creates_backup_and_keeps_content(self):
        path = self.config_path("editorial-policy.yaml")
        path.write_text(VALID_POLICY, encoding="utf-8")
        updated = VALID_POLICY + "  - id: weekly\n    platform: telegram\n    cadence: weekly\n"
        result = self.store.write("editorial-policy", updated)
        self.assertEqual(path.read_text(encoding="utf-8"), updated)
        self.assertTrue(result["backup"].startswith("editorial-policy-"))
        backups = self.store.backups("editorial-policy")
        self.assertEqual(len(backups), 1)
        self.assertEqual(result["bytes"], path.stat().st_size)

    def test_written_configs_stay_readable_for_the_other_containers(self):
        path = self.config_path("editorial-policy.yaml")
        path.write_text(VALID_POLICY, encoding="utf-8")
        os.chmod(path, 0o644)
        self.store.write(
            "editorial-policy",
            VALID_POLICY + "  - id: weekly\n    platform: telegram\n    cadence: weekly\n",
        )
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o644)

    def test_written_configs_repair_a_restrictive_mode(self):
        path = self.config_path("editorial-policy.yaml")
        path.write_text(VALID_POLICY, encoding="utf-8")
        os.chmod(path, 0o600)
        self.store.write("editorial-policy", VALID_POLICY)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o644)

    def test_seeded_config_is_readable_for_the_other_containers(self):
        source = self.root / "content" / "config"
        source.mkdir(parents=True)
        (source / "editorial-policy.yaml").write_text(VALID_POLICY, encoding="utf-8")
        self.store.seed("editorial-policy")
        seeded = self.config_path("editorial-policy.yaml")
        self.assertEqual(stat.S_IMODE(os.stat(seeded).st_mode), 0o644)

    def test_policy_rejects_duplicate_routine_ids(self):
        duplicate = VALID_POLICY + "  - id: morning\n    cadence: daily\n"
        with self.assertRaisesRegex(EditError, "duplicate routine id"):
            self.store.write("editorial-policy", duplicate)

    def test_policy_rejects_unknown_cadence_and_bad_count(self):
        with self.assertRaisesRegex(EditError, "cadence"):
            self.store.write("editorial-policy", "routines:\n  - id: hourly\n    cadence: hourly\n")
        with self.assertRaisesRegex(EditError, "at least 1"):
            self.store.write("editorial-policy", "routines:\n  - id: morning\n    count: 0\n")

    def test_policy_rejects_non_boolean_daily_enabled(self):
        with self.assertRaisesRegex(EditError, "daily_enabled"):
            self.store.write("editorial-policy", "pipeline:\n  daily_enabled: maybe\n")

    def test_policy_rejects_invalid_yaml(self):
        with self.assertRaisesRegex(EditError, "YAML error"):
            self.store.write("editorial-policy", "routines: [\n")

    def test_tools_registry_requires_supported_schema(self):
        result = self.store.write("tools", VALID_TOOLS)
        self.assertEqual(result["name"], "tools")
        with self.assertRaisesRegex(EditError, "schema_version"):
            self.store.write("tools", json.dumps({"tools": [{"id": "x", "kind": "mcp"}]}))
        with self.assertRaisesRegex(EditError, "kind must be"):
            self.store.write(
                "tools",
                json.dumps({"schema_version": 1, "tools": [{"id": "x", "kind": "carrier-pigeon"}]}),
            )
        with self.assertRaisesRegex(EditError, "needs a url"):
            self.store.write(
                "tools",
                json.dumps({"schema_version": 1, "tools": [{"id": "x", "kind": "mcp"}]}),
            )

    def test_restore_uses_a_backup_and_validates_it(self):
        path = self.config_path("editorial-policy.yaml")
        path.write_text(VALID_POLICY, encoding="utf-8")
        self.store.write("editorial-policy", "routines:\n  - id: updated\n")
        backup = self.store.backups("editorial-policy")[0]["name"]
        self.store.restore("editorial-policy", backup)
        self.assertEqual(path.read_text(encoding="utf-8"), VALID_POLICY)
        with self.assertRaisesRegex(EditError, "backup not found"):
            self.store.restore("editorial-policy", "editorial-policy-19700101T000000Z")

    def test_unknown_config_name_is_rejected(self):
        with self.assertRaisesRegex(EditError, "unknown configuration file"):
            self.store.read("../etc/passwd")


class ConfigSeedTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.shipped = self.root / "content" / "config"
        self.shipped.mkdir(parents=True)
        self.store = ConfigStore(self.root)

    def test_listing_marks_files_that_can_be_created_from_the_default(self):
        (self.shipped / "tools.json").write_text(VALID_TOOLS, encoding="utf-8")
        listing = {row["name"]: row for row in self.store.listing()}
        self.assertTrue(listing["tools"]["can_seed"])
        self.assertFalse(listing["tools"]["exists"])
        self.assertFalse(listing["sources"]["can_seed"])

    def test_seed_copies_the_shipped_default(self):
        (self.shipped / "tools.json").write_text(VALID_TOOLS, encoding="utf-8")
        result = self.store.seed("tools")
        target = self.root / "data" / "content-manager" / "config" / "tools.json"
        self.assertTrue(target.is_file())
        self.assertEqual(target.read_text(encoding="utf-8"), VALID_TOOLS)
        self.assertTrue(result["seeded_from"].endswith("content/config/tools.json"))

    def test_seed_refuses_to_overwrite_or_invent_a_default(self):
        (self.shipped / "tools.json").write_text(VALID_TOOLS, encoding="utf-8")
        self.store.seed("tools")
        with self.assertRaises(EditError):
            self.store.seed("tools")
        with self.assertRaises(EditError):
            self.store.seed("editorial-policy")


class DraftQueueTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))

    def queue_path(self) -> Path:
        return self.root / "data" / "content-bot" / drafts_mod.REQUEST_FILE

    def test_queue_action_appends_a_validated_request(self):
        request = drafts_mod.queue_action(self.root, "draft-1", "publish")
        self.assertEqual(request["draft_id"], "draft-1")
        self.assertEqual(request["action"], "publish")
        stored = json.loads(self.queue_path().read_text(encoding="utf-8").strip())
        self.assertEqual(stored["id"], request["id"])
        self.assertEqual(drafts_mod.pending(self.root)[0]["action"], "publish")

    def test_post_package_is_a_known_console_action(self):
        request = drafts_mod.queue_action(self.root, "draft-1", "post_package")
        self.assertEqual(request["action"], "post_package")
        self.assertEqual(drafts_mod.pending(self.root)[0]["action"], "post_package")

    def test_queue_action_rejects_bad_input(self):
        with self.assertRaises(drafts_mod.DraftActionError):
            drafts_mod.queue_action(self.root, "../etc/passwd", "publish")
        with self.assertRaises(drafts_mod.DraftActionError):
            drafts_mod.queue_action(self.root, "draft-1", "rm -rf")
        with self.assertRaises(drafts_mod.DraftActionError):
            drafts_mod.queue_action(self.root, "", "discard")

    def test_results_are_returned_newest_first(self):
        path = self.root / "data" / "content-bot" / drafts_mod.RESULT_FILE
        path.write_text(
            json.dumps({"results": [{"id": "1"}, {"id": "2"}, {"id": "3"}]}),
            encoding="utf-8",
        )
        self.assertEqual([row["id"] for row in drafts_mod.results(self.root)], ["3", "2", "1"])
        self.assertEqual(drafts_mod.results(self.root, limit=2), drafts_mod.results(self.root)[:2])
        self.assertEqual(drafts_mod.results(self.root / "missing"), [])

    def test_instagram_refresh_request_is_written_for_the_bot(self):
        drafts_mod.request_instagram_refresh(self.root)
        path = self.root / "data" / "content-bot" / "instagram-refresh.request"
        self.assertTrue(path.is_file())
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)


class MediaBaseUrlTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.tunnel = self.root / "data" / "content-bot" / "tunnel" / "trycloudflared.log"

    def write_tunnel(self, *hostnames: str) -> None:
        self.tunnel.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"INF Visit it at https://{name}.trycloudflare.com" for name in hostnames]
        self.tunnel.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_nothing_configured_resolves_to_empty(self):
        self.assertEqual(drafts_mod.media_base_url(self.root), "")

    def test_configured_value_is_used_when_the_tunnel_is_not_configured(self):
        self.assertEqual(
            drafts_mod.media_base_url(self.root, "https://media.example.com/"),
            "https://media.example.com",
        )

    def test_a_quick_tunnel_value_in_the_environment_is_ignored(self):
        self.write_tunnel("live-name")
        self.assertEqual(
            drafts_mod.media_base_url(self.root, "https://stale.trycloudflare.com"),
            "https://live-name.trycloudflare.com",
        )

    def test_the_most_recent_tunnel_hostname_wins(self):
        self.write_tunnel("first-name", "second-name")
        self.assertEqual(
            drafts_mod.media_base_url(self.root),
            "https://second-name.trycloudflare.com",
        )

    def test_a_pinned_file_wins_over_everything_else(self):
        self.write_tunnel("live-name")
        pin = self.root / "data" / "content-bot" / "media-base-url.txt"
        pin.write_text("# pinned\nhttps://media.example.com/media\n", encoding="utf-8")
        self.assertEqual(
            drafts_mod.media_base_url(self.root, "https://other.example.com"),
            "https://media.example.com/media",
        )


class EnvStoreTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.store = EnvStore(self.root)

    def test_secrets_are_never_returned_to_the_browser(self):
        entries = {row["key"]: row for row in self.store.entries()}
        self.assertIsNone(entries["CONTENT_BOT_TOKEN"]["value"])
        self.assertTrue(entries["CONTENT_BOT_TOKEN"]["secret"])
        self.assertTrue(entries["CONTENT_BOT_TOKEN"]["set"])
        self.assertEqual(entries["MEDIA_STUDIO_PORT"]["value"], "8850")
        self.assertFalse(entries["MEDIA_STUDIO_PORT"]["secret"])

    def test_set_updates_in_place_and_appends_new_keys(self):
        self.store.set("MEDIA_STUDIO_PORT", "8860")
        self.store.set("PANEL_PORT", "8899")
        text = (self.root / ".env").read_text(encoding="utf-8")
        self.assertIn("MEDIA_STUDIO_PORT=8860\n", text)
        self.assertIn("PANEL_PORT=8899\n", text)
        self.assertEqual(text.count("MEDIA_STUDIO_PORT="), 1)

    def test_set_rejects_multiline_and_bad_keys(self):
        with self.assertRaisesRegex(EditError, "one line"):
            self.store.set("PANEL_TOKEN", "line1\nline2")
        with self.assertRaisesRegex(EditError, "environment keys"):
            self.store.set("panel-token", "value")


class PlatformStoreTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        (self.root / ".env").write_text(
            "CONTENT_BOT_TOKEN=super-secret-value\n"
            "CONTENT_TELEGRAM_CHANNEL=-100123\n"
            "CONTENT_BALE_TOKEN=123456:abc\n"
            "CONTENT_BALE_CHAT_ID=@locallab\n"
            "CONTENT_BALE_API_BASE=https://tapi.bale.ai\n"
            "CONTENT_PLATFORMS_ENABLED=true\n",
            encoding="utf-8",
        )
        self.store = PlatformStore(self.root)

    def card(self, key):
        return next(row for row in self.store.view()["platforms"] if row["key"] == key)

    def field(self, card, key):
        return next(row for row in card["fields"] if row["key"] == key)

    def test_the_view_groups_the_stack_into_named_platforms(self):
        keys = [row["key"] for row in self.store.view()["platforms"]]
        self.assertEqual(
            keys,
            ["telegram", "bale", "eitaa", "instagram", "writer", "media", "chooser", "youtube", "aparat", "linkedin"],
        )

    def test_secrets_are_reported_as_stored_but_never_returned(self):
        card = self.card("bale")
        self.assertEqual(card["state"], "ready")
        token = self.field(card, "CONTENT_BALE_TOKEN")
        self.assertTrue(token["secret"])
        self.assertTrue(token["set"])
        self.assertIsNone(token["value"])
        chat = self.field(card, "CONTENT_BALE_CHAT_ID")
        self.assertFalse(chat["secret"])
        self.assertEqual(chat["value"], "@locallab")

    def test_states_cover_ready_partial_empty_package_and_flags(self):
        self.assertEqual(self.card("telegram")["state"], "ready")
        self.assertEqual(self.card("eitaa")["state"], "empty")
        self.assertEqual(self.card("instagram")["state"], "package")
        self.assertEqual(self.card("chooser")["state"], "on")
        self.assertEqual(self.card("youtube")["state"], "package")
        self.assertEqual(self.card("writer")["state"], "empty")

    def test_instagram_requires_credentials_only_when_auto_publish_is_on(self):
        text = (self.root / ".env").read_text(encoding="utf-8")
        (self.root / ".env").write_text(
            text + "INSTAGRAM_AUTO_PUBLISH=true\n", encoding="utf-8"
        )
        card = self.card("instagram")
        self.assertEqual(card["state"], "partial")
        self.assertTrue(self.field(card, "INSTAGRAM_BUSINESS_ID")["required"])

    def test_every_field_carries_the_api_base_urls(self):
        urls = {
            self.field(self.card(key), env_key)["kind"]
            for key, env_key in (
                ("telegram", "CONTENT_TELEGRAM_API_BASE"),
                ("bale", "CONTENT_BALE_API_BASE"),
                ("eitaa", "CONTENT_EITAA_API_BASE"),
                ("instagram", "INSTAGRAM_API_BASE"),
                ("writer", "CONTENT_WRITER_BASE_URL"),
                ("media", "CONTENT_MEDIA_STUDIO_URL"),
                ("linkedin", "CONTENT_LINKEDIN_API_BASE"),
            )
        }
        self.assertEqual(urls, {"url"})

    def test_update_writes_the_env_and_reports_the_consuming_service(self):
        result = self.store.update("bale", {"CONTENT_BALE_CHAT_ID": "@new_channel"})
        self.assertEqual(result["changed"], ["CONTENT_BALE_CHAT_ID"])
        self.assertEqual(result["service"], "content-bot")
        env = (self.root / ".env").read_text(encoding="utf-8")
        self.assertIn("CONTENT_BALE_CHAT_ID=@new_channel", env)
        self.assertIn("CONTENT_BALE_TOKEN=123456:abc", env)

    def test_update_validates_keys_urls_and_flags(self):
        with self.assertRaises(EditError):
            self.store.update("bale", {"CONTENT_BOT_TOKEN": "wrong-platform"})
        with self.assertRaises(EditError):
            self.store.update("bale", {"CONTENT_BALE_API_BASE": "tapi.bale.ai"})
        with self.assertRaises(EditError):
            self.store.update("chooser", {"CONTENT_PLATFORMS_ENABLED": "maybe"})
        with self.assertRaises(EditError):
            self.store.update("nope", {"CONTENT_BALE_TOKEN": "x"})
        self.assertIn(
            "CONTENT_BALE_API_BASE=https://tapi.bale.ai",
            (self.root / ".env").read_text(encoding="utf-8"),
        )

    def test_update_clears_a_value_with_an_empty_string(self):
        self.store.update("bale", {"CONTENT_BALE_CHAT_ID": ""})
        self.assertEqual(self.card("bale")["state"], "partial")

    def test_test_calls_the_provider_and_keeps_the_token_out_of_the_answer(self):
        payload = '{"ok": true, "result": {"username": "locallab_bot"}}'
        with mock.patch("panel.platforms._fetch", return_value=(200, payload, "")) as fetch:
            result = self.store.test("bale", {"CONTENT_BALE_TOKEN": "999:typed"})
        self.assertTrue(result["ok"])
        self.assertIn("locallab_bot", result["detail"])
        self.assertNotIn("999:typed", result["detail"])
        self.assertIn("999:typed", fetch.call_args[0][0])

    def test_a_typed_secret_wins_but_an_empty_box_keeps_the_stored_one(self):
        with mock.patch("panel.platforms._fetch", return_value=(0, "", "boom")) as fetch:
            self.store.test("bale", {"CONTENT_BALE_TOKEN": ""})
        self.assertIn("123456:abc", fetch.call_args[0][0])
        with mock.patch("panel.platforms._fetch", return_value=(0, "", "boom")) as fetch:
            self.store.test("eitaa", {"CONTENT_EITAA_TOKEN": "42:new", "CONTENT_EITAA_API_BASE": "https://eitaa.example/api"})
        self.assertIn("https://eitaa.example/api/42:new/getMe", fetch.call_args[0][0])

    def test_a_provider_error_is_reported_without_the_token(self):
        body = '{"ok": false, "description": "Unauthorized"}'
        with mock.patch("panel.platforms._fetch", return_value=(403, body, "")):
            result = self.store.test("bale", {})
        self.assertFalse(result["ok"])
        self.assertIn("Unauthorized", result["detail"])

    def test_writer_and_media_tests_probe_their_service_endpoints(self):
        (self.root / ".env").write_text(
            "CONTENT_WRITER_BASE_URL=http://gateway.local:9000/v1\n"
            "CONTENT_MEDIA_STUDIO_URL=http://media-studio:8850\n",
            encoding="utf-8",
        )
        seen = []
        posted = []

        def fake_fetch(url, headers=None):
            seen.append(url)
            if url.endswith("/models"):
                return 200, '{"data": [{"id": "auto"}]}', ""
            if url.endswith("/healthz"):
                return 200, '{"ok": true, "jobs": 3}', ""
            return 404, "", ""

        def fake_post(url, headers, payload):
            posted.append(url)
            return 400, '{"error": {"message": "prompt is required"}}', ""

        with mock.patch("panel.platforms._fetch", side_effect=fake_fetch), mock.patch(
            "panel.platforms._post_json", side_effect=fake_post
        ):
            writer = self.store.test("writer", {})
            media = self.store.test("media", {})
        self.assertTrue(writer["ok"])
        self.assertIn("1 models", writer["detail"])
        self.assertTrue(media["ok"])
        self.assertIn("3 jobs", media["detail"])
        self.assertEqual(seen, ["http://gateway.local:9000/v1/models", "http://media-studio:8850/healthz"])
        self.assertEqual(posted, ["http://gateway.local:9000/v1/images/generations"])

    def test_media_test_reports_a_chat_only_image_endpoint(self):
        (self.root / ".env").write_text(
            "CONTENT_MEDIA_STUDIO_URL=http://media-studio:8850\n"
            "MEDIA_STUDIO_WRITER_BASE_URL=http://smart-router:8080/v1\n",
            encoding="utf-8",
        )
        with mock.patch(
            "panel.platforms._fetch", return_value=(200, '{"ok": true, "jobs": 1}', "")
        ), mock.patch(
            "panel.platforms._post_json", return_value=(404, "Not Found", "")
        ):
            result = self.store.test("media", {})
        self.assertFalse(result["ok"])
        self.assertIn("no images API", result["detail"])
        self.assertIn("MEDIA_STUDIO_WRITER_BASE_URL", result["detail"])

    def test_media_test_reports_a_missing_image_endpoint(self):
        (self.root / ".env").write_text(
            "CONTENT_MEDIA_STUDIO_URL=http://media-studio:8850\n",
            encoding="utf-8",
        )
        with mock.patch(
            "panel.platforms._fetch", return_value=(200, '{"ok": true, "jobs": 1}', "")
        ):
            result = self.store.test("media", {})
        self.assertFalse(result["ok"])
        self.assertIn("MEDIA_STUDIO_WRITER_BASE_URL", result["detail"])

    def test_media_test_probes_the_video_route_when_the_api_driver_is_active(self):
        (self.root / ".env").write_text(
            "CONTENT_MEDIA_STUDIO_URL=http://media-studio:8850\n"
            "MEDIA_STUDIO_WRITER_BASE_URL=https://gateway.example/v1\n"
            "MEDIA_STUDIO_VIDEO_MODEL=xai/grok-imagine-video\n"
            "CONTENT_MEDIA_VIDEO_DRIVER=api-video\n",
            encoding="utf-8",
        )
        posted = []

        def fake_post(url, headers, payload):
            posted.append((url, payload))
            if url.endswith("/videos/generations"):
                return 400, '{"error": {"message": "prompt is required"}}', ""
            return 400, '{"error": {"message": "size is required"}}', ""

        with mock.patch(
            "panel.platforms._fetch", return_value=(200, '{"ok": true, "jobs": 1}', "")
        ), mock.patch("panel.platforms._post_json", side_effect=fake_post):
            result = self.store.test("media", {})
        self.assertTrue(result["ok"], result["detail"])
        self.assertIn("xai/grok-imagine-video", result["detail"])
        self.assertEqual(
            posted,
            [
                ("https://gateway.example/v1/images/generations", {}),
                ("https://gateway.example/v1/videos/generations", {"model": "xai/grok-imagine-video"}),
            ],
        )

    def test_media_test_reports_a_gateway_without_video_credentials(self):
        (self.root / ".env").write_text(
            "CONTENT_MEDIA_STUDIO_URL=http://media-studio:8850\n"
            "MEDIA_STUDIO_WRITER_BASE_URL=https://gateway.example/v1\n"
            "MEDIA_STUDIO_VIDEO_MODEL=xai/grok-imagine-video\n"
            "CONTENT_MEDIA_VIDEO_DRIVER=api-video\n",
            encoding="utf-8",
        )

        def fake_post(url, headers, payload):
            if url.endswith("/videos/generations"):
                return 400, '{"error": {"message": "No credentials for provider: xai"}}', ""
            return 400, '{"error": {"message": "size is required"}}', ""

        with mock.patch(
            "panel.platforms._fetch", return_value=(200, '{"ok": true, "jobs": 1}', "")
        ), mock.patch("panel.platforms._post_json", side_effect=fake_post):
            result = self.store.test("media", {})
        self.assertFalse(result["ok"])
        self.assertIn("No credentials for provider: xai", result["detail"])

    def test_media_test_asks_for_a_video_model(self):
        (self.root / ".env").write_text(
            "CONTENT_MEDIA_STUDIO_URL=http://media-studio:8850\n"
            "MEDIA_STUDIO_WRITER_BASE_URL=https://gateway.example/v1\n"
            "CONTENT_MEDIA_VIDEO_DRIVER=api-video\n",
            encoding="utf-8",
        )
        with mock.patch(
            "panel.platforms._fetch", return_value=(200, '{"ok": true, "jobs": 1}', "")
        ), mock.patch(
            "panel.platforms._post_json",
            return_value=(400, '{"error": {"message": "size is required"}}', ""),
        ):
            result = self.store.test("media", {})
        self.assertFalse(result["ok"])
        self.assertIn("MEDIA_STUDIO_VIDEO_MODEL", result["detail"])

    def test_the_writer_falls_back_to_the_health_route(self):
        (self.root / ".env").write_text(
            "CONTENT_WRITER_BASE_URL=http://gateway.local:9000\n", encoding="utf-8"
        )

        def fake_fetch(url, headers=None):
            if url.endswith("/health"):
                return 200, "{}", ""
            return 404, "{}", ""

        with mock.patch("panel.platforms._fetch", side_effect=fake_fetch):
            result = self.store.test("writer", {})
        self.assertTrue(result["ok"])

    def test_package_platforms_have_nothing_to_test(self):
        with self.assertRaises(EditError):
            self.store.test("youtube", {})
        self.assertEqual(platform_for("linkedin").mode, "auto")

    def test_linkedin_stays_partial_until_an_author_is_stored(self):
        self.store.update("linkedin", {"CONTENT_LINKEDIN_ACCESS_TOKEN": "AQXsecret"})
        self.assertEqual(self.card("linkedin")["state"], "partial")
        self.store.update(
            "linkedin",
            {
                "CONTENT_LINKEDIN_ACCOUNT_TYPE": "organization",
                "CONTENT_LINKEDIN_ORGANIZATION_ID": "12345678",
            },
        )
        card = self.card("linkedin")
        self.assertEqual(card["state"], "ready")
        self.assertEqual(card["mode"], "auto")
        token = self.field(card, "CONTENT_LINKEDIN_ACCESS_TOKEN")
        self.assertTrue(token["secret"])
        self.assertTrue(token["set"])
        self.assertIsNone(token["value"])

    def test_aparat_becomes_ready_with_either_session_key(self):
        card = self.card("aparat")
        self.assertEqual(card["mode"], "auto")
        self.assertEqual(card["state"], "empty")
        self.assertEqual("docs/APARAT-SETUP.md", card["docs"])
        self.store.update("aparat", {"CONTENT_APARAT_COOKIE": "AuthV1=abc"})
        self.assertEqual(self.card("aparat")["state"], "ready")
        token = self.field(self.card("aparat"), "CONTENT_APARAT_COOKIE")
        self.assertTrue(token["secret"])
        self.assertTrue(token["set"])
        self.assertIsNone(token["value"])
        self.store.update("aparat", {"CONTENT_APARAT_TOKEN": "jwt-value"})
        self.assertEqual(self.card("aparat")["state"], "ready")

    def test_aparat_test_asks_for_the_upload_server(self):
        seen = {}

        def fake_fetch(url, headers=None):
            seen["url"] = url
            seen["headers"] = dict(headers or {})
            return 200, '{"data": {"server": "https://upload.aparat.test"}}', ""

        with mock.patch("panel.platforms._fetch", side_effect=fake_fetch):
            result = self.store.test(
                "aparat", {"CONTENT_APARAT_TOKEN": "jwt-value"}
            )
        self.assertTrue(result["ok"])
        self.assertIn("upload.aparat.test", result["detail"])
        self.assertEqual(
            "https://www.aparat.com/api/fa/v1/video/upload/upload_config",
            seen["url"],
        )
        self.assertEqual("Bearer jwt-value", seen["headers"]["Authorization"])

    def test_aparat_test_reports_a_refused_session(self):
        def fake_fetch(url, headers=None):
            return 401, '{"errors": [{"status": 401, "detail": "کاربر پیدا نشد"}]}', ""

        with mock.patch("panel.platforms._fetch", side_effect=fake_fetch):
            result = self.store.test("aparat", {"CONTENT_APARAT_COOKIE": "AuthV1=abc"})
        self.assertFalse(result["ok"])
        self.assertIn("refused", result["detail"])

    def test_aparat_test_needs_a_session_before_calling_aparat(self):
        called = {"count": 0}

        def fake_fetch(url, headers=None):  # pragma: no cover - must not run
            called["count"] += 1
            return 200, "{}", ""

        with mock.patch("panel.platforms._fetch", side_effect=fake_fetch):
            result = self.store.test("aparat", {})
        self.assertFalse(result["ok"])
        self.assertEqual(0, called["count"])

    def test_aparat_test_rejects_a_console_placeholder(self):
        called = {"count": 0}

        def fake_fetch(url, headers=None):  # pragma: no cover - must not run
            called["count"] += 1
            return 200, "{}", ""

        with mock.patch("panel.platforms._fetch", side_effect=fake_fetch):
            result = self.store.test("aparat", {"CONTENT_APARAT_TOKEN": "undefined"})
        self.assertFalse(result["ok"])
        self.assertIn("localStorage.getItem('jwt')", result["detail"])
        self.assertEqual(0, called["count"])

    def test_linkedin_author_prefers_the_explicit_urn(self):
        self.assertEqual(
            _linkedin_author("person", "urn:li:person:1", "2", "3"), "urn:li:person:1"
        )
        self.assertEqual(
            _linkedin_author("person", "bare-id", "2", "3"), "urn:li:person:bare-id"
        )
        self.assertEqual(
            _linkedin_author("organization", "", "2", "3"), "urn:li:organization:3"
        )
        self.assertEqual(_linkedin_author("person", "", "2", "3"), "urn:li:person:2")
        self.assertEqual(_linkedin_author("person", "", "", ""), "")

    def test_linkedin_test_reserves_an_upload_slot_with_the_author_urn(self):
        seen = {}

        def fake_post(url, headers, payload):
            seen["url"] = url
            seen["headers"] = headers
            seen["payload"] = payload
            body = (
                '{"value": {"uploadUrl": "https://upload.linkedin.example/x", '
                '"image": "urn:li:image:1"}}'
            )
            return 201, body, ""

        with mock.patch("panel.platforms._post_json", side_effect=fake_post):
            result = self.store.test(
                "linkedin",
                {
                    "CONTENT_LINKEDIN_ACCESS_TOKEN": "AQXtyped",
                    "CONTENT_LINKEDIN_ACCOUNT_TYPE": "person",
                    "CONTENT_LINKEDIN_PERSON_ID": "abc123",
                },
            )
        self.assertTrue(result["ok"])
        self.assertIn("urn:li:person:abc123", result["detail"])
        self.assertNotIn("AQXtyped", result["detail"])
        self.assertEqual(
            seen["url"], "https://api.linkedin.com/rest/images?action=initializeUpload"
        )
        self.assertEqual(
            seen["payload"], {"initializeUploadRequest": {"owner": "urn:li:person:abc123"}}
        )
        self.assertEqual(seen["headers"]["LinkedIn-Version"], "202601")
        self.assertEqual(seen["headers"]["X-Restli-Protocol-Version"], "2.0.0")
        self.assertIn("AQXtyped", seen["headers"]["Authorization"])

    def test_linkedin_test_reports_the_token_and_author_problems(self):
        values = {
            "CONTENT_LINKEDIN_ACCESS_TOKEN": "AQXsecret",
            "CONTENT_LINKEDIN_ACCOUNT_TYPE": "organization",
            "CONTENT_LINKEDIN_ORGANIZATION_ID": "12345678",
        }
        with mock.patch("panel.platforms._post_json", return_value=(401, "{}", "")):
            expired = self.store.test("linkedin", values)
        self.assertFalse(expired["ok"])
        self.assertIn("expired", expired["detail"])
        with mock.patch("panel.platforms._post_json", return_value=(403, "{}", "")):
            refused = self.store.test("linkedin", values)
        self.assertFalse(refused["ok"])
        self.assertIn("urn:li:organization:12345678", refused["detail"])
        with mock.patch(
            "panel.platforms._post_json",
            return_value=(0, "", "api.linkedin.com is unreachable: boom"),
        ):
            offline = self.store.test("linkedin", values)
        self.assertFalse(offline["ok"])
        self.assertIn("unreachable", offline["detail"])

    def test_linkedin_test_asks_for_an_author_before_calling_the_api(self):
        with mock.patch(
            "panel.platforms._post_json", side_effect=AssertionError("no call expected")
        ):
            result = self.store.test("linkedin", {"CONTENT_LINKEDIN_ACCESS_TOKEN": "x"})
        self.assertFalse(result["ok"])
        self.assertIn("author URN", result["detail"])

    def test_env_store_exposes_single_values_and_secret_detection(self):
        store = EnvStore(self.root)
        self.assertEqual(store.value("CONTENT_BALE_CHAT_ID"), "@locallab")
        self.assertEqual(store.value("MISSING_KEY", "fallback"), "fallback")
        self.assertTrue(store.is_secret("CONTENT_BALE_TOKEN"))
        self.assertFalse(store.is_secret("CONTENT_BALE_CHAT_ID"))


class StackExposureTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.view = StackView(self.root)

    def test_loopback_binds_produce_no_warnings(self):
        (self.root / ".env").write_text("N8N_BIND_IP=127.0.0.1\n", encoding="utf-8")
        payload = self.view.exposure()
        self.assertEqual(payload["warnings"], [])
        self.assertTrue(payload["rows"][0]["loopback"])

    def test_public_n8n_bind_warns_about_mcp(self):
        (self.root / ".env").write_text("N8N_BIND_IP=0.0.0.0\n", encoding="utf-8")
        warnings = self.view.exposure()["warnings"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("MCP", warnings[0])

    def test_public_rustfs_bind_warns_about_object_storage(self):
        (self.root / ".env").write_text("RUSTFS_BIND_IP=192.168.1.50\n", encoding="utf-8")
        payload = self.view.exposure()
        self.assertEqual(payload["rows"][0]["service"], "rustfs")
        self.assertFalse(payload["rows"][0]["loopback"])
        self.assertEqual(len(payload["warnings"]), 1)
        self.assertIn("RUSTFS_CONSOLE_BIND_IP", payload["warnings"][0])


class StorageAndBackupViewTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))

    def test_storage_reports_the_shared_block_and_consumers(self):
        (self.root / ".env").write_text(
            "S3_STORAGE_BACKEND=rustfs\n"
            "S3_ENDPOINT_URL=http://rustfs:9000\n"
            "S3_BUCKET=locallab\n"
            "S3_REGION=us-east-1\n"
            "S3_PUBLIC_BASE_URL=https://s3.stack.example.com\n"
            "S3_PUBLIC_CONSOLE_URL=https://rfs.stack.example.com/rustfs/console\n"
            "S3_FORCE_PATH_STYLE=true\n"
            "OPENWEBUI_STORAGE_PROVIDER=s3\n"
            "RUSTFS_BIND_IP=192.168.1.50\n"
            "RUSTFS_PORT=9000\n"
            "RUSTFS_CONSOLE_BIND_IP=192.168.1.50\n",
            encoding="utf-8",
        )
        payload = StackView(self.root).storage()
        self.assertEqual(payload["backend"], "rustfs")
        self.assertEqual(payload["endpoint"], "http://rustfs:9000")
        self.assertEqual(payload["bucket"], "locallab")
        self.assertTrue(payload["force_path_style"])
        self.assertEqual(payload["public_base_url"], "https://s3.stack.example.com")
        self.assertEqual(payload["warnings"], [])
        modes = {row["service"]: row["mode"] for row in payload["consumers"]}
        self.assertEqual(modes["open-webui"], "s3")
        self.assertEqual(modes["content-bot"], "local")
        self.assertEqual(modes["n8n"], "local")
        self.assertEqual(payload["rustfs"]["api_url"], "https://s3.stack.example.com")
        self.assertEqual(
            payload["rustfs"]["console_url"],
            "https://rfs.stack.example.com/rustfs/console/",
        )
        self.assertEqual(payload["rustfs"]["service"], None)

    def test_storage_falls_back_to_the_bind_addresses(self):
        (self.root / ".env").write_text(
            "S3_STORAGE_BACKEND=rustfs\n"
            "RUSTFS_BIND_IP=192.168.1.50\n"
            "RUSTFS_CONSOLE_BIND_IP=192.168.1.50\n"
            "S3_PUBLIC_BASE_URL=http://localhost:9000\n"
            "S3_PUBLIC_CONSOLE_URL=http://127.0.0.1:9001/rustfs/console\n",
            encoding="utf-8",
        )
        rustfs = StackView(self.root).storage()["rustfs"]
        self.assertEqual(rustfs["api_url"], "http://192.168.1.50:9000")
        self.assertEqual(rustfs["console_url"], "http://192.168.1.50:9001/rustfs/console/")

    def test_storage_warns_when_openwebui_points_at_a_stopped_backend(self):
        (self.root / ".env").write_text(
            "S3_STORAGE_BACKEND=off\nOPENWEBUI_STORAGE_PROVIDER=s3\n", encoding="utf-8"
        )
        payload = StackView(self.root).storage()
        self.assertEqual(payload["backend"], "off")
        self.assertEqual(len(payload["warnings"]), 1)
        self.assertIn("object storage is off", payload["warnings"][0])

    def test_storage_warns_when_external_endpoint_is_missing(self):
        (self.root / ".env").write_text("S3_STORAGE_BACKEND=external\n", encoding="utf-8")
        payload = StackView(self.root).storage()
        self.assertEqual(payload["backend"], "external")
        self.assertIn("S3_ENDPOINT_URL", payload["warnings"][0])
        self.assertIsNone(payload["rustfs"])

    def test_backups_list_archives_with_metadata(self):
        archive = self.root.parent / f"{self.root.name}-backups"
        archive.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: __import__("shutil").rmtree(archive, ignore_errors=True))
        target = archive / "hermes-stack-20260911T120000Z-panel.tar.gz"
        target.write_bytes(b"x" * 2048)
        (archive / f"{target.name}.meta.json").write_text(
            json.dumps(
                {
                    "created_at": "2026-09-11T12:00:00+00:00",
                    "full": True,
                    "sections": [],
                    "stack_version": "v0.5.9",
                }
            ),
            encoding="utf-8",
        )
        encrypted = archive / "hermes-stack-20260910T120000Z-panel.tar.gz.age"
        encrypted.write_bytes(b"y" * 1024)

        payload = StackView(self.root).backups()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["exists"])
        self.assertEqual(payload["directory"], str(archive))
        self.assertEqual(len(payload["entries"]), 2)
        by_name = {entry["name"]: entry for entry in payload["entries"]}
        entry = by_name[target.name]
        self.assertEqual(entry["size_bytes"], 2048)
        self.assertTrue(entry["full"])
        self.assertFalse(entry["encrypted"])
        self.assertEqual(entry["stack_version"], "v0.5.9")
        self.assertEqual(entry["created_at"], "2026-09-11T12:00:00+00:00")
        self.assertTrue(by_name[encrypted.name]["encrypted"])
        self.assertEqual(by_name[encrypted.name]["sections"], [])
        self.assertEqual(payload["entries"][0]["name"], target.name)

    def test_backups_report_partial_sections_and_a_missing_directory(self):
        archive = self.root.parent / f"{self.root.name}-backups"
        archive.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: __import__("shutil").rmtree(archive, ignore_errors=True))
        target = archive / "hermes-stack-20260911T130000Z-env.tar.gz"
        target.write_bytes(b"z")
        (archive / f"{target.name}.meta.json").write_text(
            json.dumps({"full": False, "sections": ["env", "panel"]}), encoding="utf-8"
        )
        payload = StackView(self.root).backups()
        entry = payload["entries"][0]
        self.assertFalse(entry["full"])
        self.assertEqual(entry["sections"], ["env", "panel"])

        empty = StackView(self.root.parent / "missing-checkout").backups()
        self.assertTrue(empty["ok"])
        self.assertFalse(empty["exists"])
        self.assertEqual(empty["entries"], [])


class PanelLinksTest(unittest.TestCase):
    """The overview links prefer a published host and fall back to the bind."""

    def setUp(self):
        self.root = make_root()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))

    def links(self) -> dict:
        return {
            row["label"]: row["url"] for row in PanelApp(self.root, "token").links()
        }

    def test_links_fall_back_to_the_bind_addresses(self):
        (self.root / ".env").write_text(
            "COMPOSE_PROFILES=9router,smart-router,panel\n"
            "PANEL_BIND_IP=192.168.1.50\n"
            "SMART_ROUTER_BIND_IP=192.168.1.50\n"
            "NINEROUTER_BIND_IP=192.168.1.50\n"
            "NINEROUTER_PUBLIC_BASE_URL=http://localhost:20128\n"
            "MEDIA_STUDIO_BIND_IP=192.168.1.50\n"
            "RUSTFS_BIND_IP=192.168.1.50\n",
            encoding="utf-8",
        )
        links = self.links()
        self.assertEqual(links["Operator panel"], "http://192.168.1.50:8899/")
        self.assertEqual(
            links["Smart Router dashboard"], "http://192.168.1.50:8787/dashboard"
        )
        self.assertEqual(links["9router dashboard"], "http://192.168.1.50:20128/")
        self.assertEqual(
            links["Media Studio API"], "http://192.168.1.50:8850/openapi.json"
        )
        self.assertEqual(links["RustFS console"], "http://192.168.1.50:9001/rustfs/console/")
        self.assertNotIn("n8n editor", links)
        self.assertNotIn("Instagram media host", links)

    def test_links_prefer_recorded_public_origins(self):
        (self.root / ".env").write_text(
            "COMPOSE_PROFILES=omniroute,smart-router,n8n\n"
            "PANEL_PUBLIC_URL=https://panel.stack.example.com\n"
            "SMART_ROUTER_PUBLIC_URL=https://sr.stack.example.com\n"
            "OMNIROUTE_PUBLIC_BASE_URL=https://omni.stack.example.com\n"
            "MEDIA_STUDIO_PUBLIC_URL=https://studio.stack.example.com\n"
            "N8N_PUBLIC_URL=https://n8n.stack.example.com\n"
            "RUSTFS_BIND_IP=192.168.1.50\n"
            "S3_PUBLIC_CONSOLE_URL=https://rfs.stack.example.com/rustfs/console\n"
            "INSTAGRAM_MEDIA_PUBLIC_BASE_URL=https://media.stack.example.com\n",
            encoding="utf-8",
        )
        links = self.links()
        self.assertEqual(links["Operator panel"], "https://panel.stack.example.com/")
        self.assertEqual(
            links["Smart Router dashboard"], "https://sr.stack.example.com/dashboard"
        )
        self.assertEqual(links["OmniRoute dashboard"], "https://omni.stack.example.com/")
        self.assertNotIn("9router dashboard", links)
        self.assertEqual(
            links["Media Studio API"], "https://studio.stack.example.com/openapi.json"
        )
        self.assertEqual(links["n8n editor"], "https://n8n.stack.example.com/")
        self.assertEqual(
            links["RustFS console"], "https://rfs.stack.example.com/rustfs/console/"
        )
        self.assertEqual(links["Instagram media host"], "https://media.stack.example.com")


class ActionWhitelistTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))

    def test_listing_is_a_fixed_whitelist(self):
        names = {row["name"] for row in ActionRunner(self.root).listing()}
        self.assertIn("stack-up", names)
        self.assertIn("doctor", names)
        self.assertNotIn("shell", names)

    def test_only_stack_up_requires_confirmation(self):
        confirming = {row["name"] for row in ActionRunner(self.root).listing() if row["confirm"]}
        self.assertEqual(confirming, {"stack-up", "backup", "backup-section"})

    def test_compose_commands_use_the_stack_project(self):
        runner = ActionRunner(self.root)
        action = next(item for item in runner.listing() if item["name"] == "restart-content")
        command = runner.command_for(
            next(a for a in __import__("panel.actions", fromlist=["ACTIONS"]).ACTIONS if a.name == action["name"])
        )
        self.assertEqual(command[:2], ["docker", "compose"])
        self.assertIn(str(self.root / "docker-compose.yml"), command)
        self.assertEqual(command[-2:], ["restart", "content-bot"])

    def test_s3_verify_uses_the_in_network_endpoint(self):
        (self.root / ".env").write_text(
            "S3_STORAGE_BACKEND=rustfs\n"
            "S3_ENDPOINT_URL=http://rustfs:9000\n"
            "S3_HOST_ENDPOINT_URL=http://127.0.0.1:9000\n",
            encoding="utf-8",
        )
        runner = ActionRunner(self.root)
        action = next(item for item in __import__("panel.actions", fromlist=["ACTIONS"]).ACTIONS if item.name == "s3-verify")
        self.assertEqual(runner._env_overrides(action), {"S3_HOST_ENDPOINT_URL": "http://rustfs:9000"})
        other = next(item for item in __import__("panel.actions", fromlist=["ACTIONS"]).ACTIONS if item.name == "s3-status")
        self.assertEqual(runner._env_overrides(other), {})

    def test_backup_runs_as_a_one_off_container_from_the_panel_image(self):
        (self.root / ".env").write_text(
            "PANEL_IMAGE_REPOSITORY=afsharidevops/content-panel\n"
            "PANEL_IMAGE_TAG=0.3.0\n",
            encoding="utf-8",
        )
        runner = ActionRunner(self.root)
        action = next(item for item in __import__("panel.actions", fromlist=["ACTIONS"]).ACTIONS if item.name == "backup")
        command = runner.command_for(action)
        self.assertEqual(command[:2], ["docker", "run"])
        self.assertIn("--rm", command)
        self.assertIn("/var/run/docker.sock:/var/run/docker.sock", command)
        self.assertIn(f"{self.root}:{self.root}", command)
        backups = self.root.parent / f"{self.root.name}-backups"
        self.assertIn(f"{backups}:{backups}", command)
        self.assertEqual(command[command.index("--entrypoint") + 1], "bash")
        wrapper = command[command.index("-c") + 1]
        self.assertIn(str(self.root / "manage.sh"), wrapper)
        self.assertIn(f'chown "$owner" "{backups}"/hermes-stack-*', wrapper)
        self.assertNotIn("--label", wrapper)
        self.assertEqual(command[command.index("-c") + 2], "panel-action")
        self.assertEqual(command[-4:], ["backup", "--label", "panel", "--no-pause"])
        self.assertEqual(command[-8], "afsharidevops/content-panel:0.3.0")
        self.assertEqual(runner._env_overrides(action), {})

    def test_backup_container_mounts_a_custom_backup_directory(self):
        custom = Path(tempfile.mkdtemp(prefix="panel-backups-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(custom, ignore_errors=True))
        (self.root / ".env").write_text(
            f"CONTENT_MANAGER_BACKUP_DIR={custom}\n", encoding="utf-8"
        )
        runner = ActionRunner(self.root)
        action = next(item for item in __import__("panel.actions", fromlist=["ACTIONS"]).ACTIONS if item.name == "backup")
        command = runner.command_for(action)
        self.assertIn(f"{custom}:{custom}", command)
        self.assertIn(f"CONTENT_MANAGER_BACKUP_DIR={custom}", command)

    def write_manage_stub(self, sections=("env", "content", "panel")):
        table = "".join(
            f"{name:<12} data/{name}\n" for name in sections
        )
        script = self.root / "manage.sh"
        script.write_text(
            "#!/bin/sh\n"
            "printf 'SECTION      PATHS (relative to the stack root)\\n'\n"
            f"printf '%s' \"{table}\"\n"
            "printf '\\nExamples:\\n  ./manage.sh backup --only env --destination DIR\\n'\n",
            encoding="utf-8",
        )
        script.chmod(0o755)

    def test_backup_section_fills_the_validated_section_list(self):
        self.write_manage_stub(("env", "n8n", "s3", "panel"))
        runner = ActionRunner(self.root)
        action = next(item for item in __import__("panel.actions", fromlist=["ACTIONS"]).ACTIONS if item.name == "backup-section")
        self.assertEqual(action.params, ("sections",))
        command = runner.command_for(action, {"sections": " panel , env,env "})
        self.assertEqual(
            command[-6:],
            ["backup", "--only", "panel,env", "--label", "panel-section", "--no-pause"],
        )
        self.assertEqual(command[command.index("-c") + 2], "panel-action")
        listing = {row["name"]: row for row in runner.listing()}
        self.assertEqual(listing["backup-section"]["params"], ["sections"])

    def test_backup_section_rejects_unknown_or_missing_sections(self):
        self.write_manage_stub(("env", "panel"))
        runner = ActionRunner(self.root)
        action = next(item for item in __import__("panel.actions", fromlist=["ACTIONS"]).ACTIONS if item.name == "backup-section")
        with self.assertRaisesRegex(ActionError, "unknown backup section: router"):
            runner.command_for(action, {"sections": "env,router"})
        with self.assertRaisesRegex(ActionError, "select at least one"):
            runner.command_for(action, {"sections": " , "})
        with self.assertRaisesRegex(ActionError, "select at least one"):
            runner.command_for(action, {})

    def test_backup_section_reports_a_broken_section_list(self):
        runner = ActionRunner(self.root)
        action = next(item for item in __import__("panel.actions", fromlist=["ACTIONS"]).ACTIONS if item.name == "backup-section")
        with self.assertRaisesRegex(ActionError, "could not read the section list"):
            runner.command_for(action, {"sections": "env"})

    def test_unknown_action_and_disabled_actions_are_rejected(self):
        with self.assertRaisesRegex(ActionError, "unknown action"):
            ActionRunner(self.root).run("rm-rf")
        with self.assertRaisesRegex(ActionError, "disabled"):
            ActionRunner(self.root, enabled=False).run("stack-up")


class PanelAuthTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.app = PanelApp(self.root, "token-value")

    def test_session_value_is_derived_and_stable(self):
        self.assertEqual(self.app.session_value(), self.app.session_value())
        self.assertNotIn("token-value", self.app.session_value())
        self.assertEqual(len(self.app.session_value()), 64)

    def test_token_and_session_comparison(self):
        self.assertTrue(self.app.token_matches("token-value"))
        self.assertFalse(self.app.token_matches("token-value "))
        self.assertFalse(self.app.token_matches(""))
        self.assertTrue(self.app.session_matches(self.app.session_value()))
        self.assertFalse(self.app.session_matches(""))
        self.assertFalse(self.app.session_matches("x" * 64))

    def test_empty_token_never_authenticates(self):
        app = PanelApp(self.root, "")
        self.assertFalse(app.token_matches(""))
        self.assertFalse(app.session_matches(app.session_value()))

    def test_state_view_reads_drafts_and_policy_routines(self):
        policy = self.root / "data" / "content-manager" / "config" / "editorial-policy.yaml"
        policy.write_text(
            "routines:\n  - id: morning\n    platform: telegram\n    cadence: daily\n    count: 1\n",
            encoding="utf-8",
        )
        state = self.root / "data" / "content-bot" / "state.json"
        state.write_text(
            json.dumps(
                {
                    "day": "2026-09-10",
                    "published_today": 2,
                    "published": [{"id": "a"}, {"id": "b"}],
                    "drafts": {"d1": {"kind": "topic", "title": "T", "status": "pending"}},
                    "routine_last_run": {"morning": "2026-09-10T05:30:00+00:00"},
                }
            ),
            encoding="utf-8",
        )
        payload = self.app.state_view()
        self.assertTrue(payload["exists"])
        self.assertEqual(payload["counters"]["published_total"], 2)
        self.assertEqual(payload["counters"]["drafts_total"], 1)
        self.assertEqual(payload["routine_last_run"]["morning"], "2026-09-10T05:30:00+00:00")
        self.assertEqual(payload["routines"][0]["id"], "morning")


class PanelHttpTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.app = PanelApp(self.root, "token-value")
        PanelHandler.app = self.app
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), PanelHandler)
        self.server.app = self.app
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.port = self.server.server_address[1]
        self.cookie = ""

    def request(self, method, path, payload=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        body = json.dumps(payload) if payload is not None else None
        request_headers = dict(headers or {})
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        if self.cookie:
            request_headers["Cookie"] = self.cookie
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        set_cookie = response.getheader("Set-Cookie") or ""
        connection.close()
        if set_cookie.startswith("panel_session="):
            self.cookie = set_cookie.split(";", 1)[0]
        return response.status, raw

    def login(self, token="token-value"):
        return self.request("POST", "/api/login", {"token": token})

    def test_healthz_and_static_assets_are_public(self):
        status, body = self.request("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["version"], __version__)
        status, body = self.request("GET", "/static/app.js")
        self.assertEqual(status, 200)
        self.assertIn("X-Panel-Csrf", body)
        status, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("<title>Content Console</title>", body)

    def test_api_requires_authentication(self):
        status, body = self.request("GET", "/api/status")
        self.assertEqual(status, 401)
        self.assertIn("authentication required", body)

    def test_login_rejects_a_wrong_token(self):
        status, _ = self.login("nope")
        self.assertEqual(status, 401)

    def test_login_then_status_works_and_logout_clears_the_session(self):
        status, _ = self.login()
        self.assertEqual(status, 200)
        self.assertTrue(self.cookie)
        status, body = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertIn("services", payload)
        self.assertEqual(payload["state"]["drafts_total"], 0)
        status, _ = self.request("POST", "/api/logout", {})
        self.assertEqual(status, 200)
        self.cookie = ""
        status, _ = self.request("GET", "/api/status")
        self.assertEqual(status, 401)

    def test_state_changing_endpoints_require_the_csrf_header(self):
        self.login()
        status, body = self.request("PUT", "/api/env/MEDIA_STUDIO_PORT", {"key": "MEDIA_STUDIO_PORT", "value": "8860"})
        self.assertEqual(status, 403)
        self.assertIn("X-Panel-Csrf", body)
        status, _ = self.request(
            "PUT",
            "/api/env/MEDIA_STUDIO_PORT",
            {"key": "MEDIA_STUDIO_PORT", "value": "8860"},
            headers={"X-Panel-Csrf": "1"},
        )
        self.assertEqual(status, 200)
        self.assertIn("MEDIA_STUDIO_PORT=8860", (self.root / ".env").read_text(encoding="utf-8"))

    def test_config_write_through_the_api_validates(self):
        self.login()
        status, body = self.request(
            "PUT",
            "/api/config/editorial-policy",
            {"text": "routines:\n  - id: bad\n    cadence: hourly\n"},
            headers={"X-Panel-Csrf": "1"},
        )
        self.assertEqual(status, 400)
        self.assertIn("cadence", body)
        status, _ = self.request(
            "PUT",
            "/api/config/editorial-policy",
            {"text": VALID_POLICY},
            headers={"X-Panel-Csrf": "1"},
        )
        self.assertEqual(status, 200)

    def test_unknown_action_is_rejected_without_running_anything(self):
        self.login()
        status, body = self.request("POST", "/api/actions/not-an-action", {}, headers={"X-Panel-Csrf": "1"})
        self.assertEqual(status, 400)
        self.assertIn("unknown action", body)

    def test_backup_section_rejects_an_unknown_section_before_running(self):
        (self.root / "manage.sh").write_text(
            "#!/bin/sh\nprintf 'env          .env\\npanel        data/panel\\n'\n",
            encoding="utf-8",
        )
        (self.root / "manage.sh").chmod(0o755)
        self.login()
        status, body = self.request(
            "POST",
            "/api/actions/backup-section",
            {"sections": "env,secrets"},
            headers={"X-Panel-Csrf": "1"},
        )
        self.assertEqual(status, 400)
        self.assertIn("unknown backup section: secrets", body)


class PanelPlatformApiTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        (self.root / ".env").write_text(
            "CONTENT_BOT_TOKEN=super-secret-value\n"
            "CONTENT_BALE_TOKEN=123456:abc\n"
            "CONTENT_BALE_CHAT_ID=@locallab\n"
            "CONTENT_BALE_API_BASE=https://tapi.bale.ai\n",
            encoding="utf-8",
        )
        self.app = PanelApp(self.root, "token-value")
        PanelHandler.app = self.app
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), PanelHandler)
        self.server.app = self.app
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.port = self.server.server_address[1]
        self.cookie = ""

    def request(self, method, path, payload=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        body = json.dumps(payload) if payload is not None else None
        request_headers = dict(headers or {})
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        if self.cookie:
            request_headers["Cookie"] = self.cookie
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        set_cookie = response.getheader("Set-Cookie") or ""
        connection.close()
        if set_cookie.startswith("panel_session="):
            self.cookie = set_cookie.split(";", 1)[0]
        return response.status, raw

    def login(self):
        return self.request("POST", "/api/login", {"token": "token-value"})

    def test_platforms_api_requires_authentication(self):
        status, _ = self.request("GET", "/api/platforms")
        self.assertEqual(status, 401)

    def test_platforms_payload_masks_secrets_and_includes_base_urls(self):
        self.login()
        status, body = self.request("GET", "/api/platforms")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        bale = next(row for row in payload["platforms"] if row["key"] == "bale")
        token = next(row for row in bale["fields"] if row["key"] == "CONTENT_BALE_TOKEN")
        self.assertIsNone(token["value"])
        self.assertTrue(token["set"])
        base = next(row for row in bale["fields"] if row["key"] == "CONTENT_BALE_API_BASE")
        self.assertEqual(base["value"], "https://tapi.bale.ai")
        self.assertEqual(bale["state"], "ready")
        self.assertNotIn("123456:abc", body)

    def test_saving_a_platform_writes_env_and_needs_the_csrf_header(self):
        self.login()
        status, _ = self.request(
            "PUT",
            "/api/platforms/bale",
            {"values": {"CONTENT_BALE_CHAT_ID": "@changed"}},
        )
        self.assertEqual(status, 403)
        status, body = self.request(
            "PUT",
            "/api/platforms/bale",
            {"values": {"CONTENT_BALE_CHAT_ID": "@changed"}},
            headers={"X-Panel-Csrf": "1"},
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["changed"], ["CONTENT_BALE_CHAT_ID"])
        self.assertIn("Apply changes", payload["restart_hint"])
        self.assertIn(
            "CONTENT_BALE_CHAT_ID=@changed",
            (self.root / ".env").read_text(encoding="utf-8"),
        )

    def test_saving_an_unknown_platform_field_is_rejected(self):
        self.login()
        status, body = self.request(
            "PUT",
            "/api/platforms/bale",
            {"values": {"CONTENT_BOT_TOKEN": "x"}},
            headers={"X-Panel-Csrf": "1"},
        )
        self.assertEqual(status, 400)
        self.assertIn("not a field", body)

    def test_the_test_endpoint_answers_with_the_provider_result(self):
        self.login()
        payload = '{"ok": true, "result": {"username": "locallab_bot"}}'
        with mock.patch("panel.platforms._fetch", return_value=(200, payload, "")):
            status, body = self.request(
                "POST",
                "/api/platforms/bale/test",
                {"values": {}},
                headers={"X-Panel-Csrf": "1"},
            )
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertTrue(result["ok"])
        self.assertIn("locallab_bot", result["detail"])


class PanelStorageApiTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        (self.root / ".env").write_text(
            "S3_STORAGE_BACKEND=rustfs\nS3_BUCKET=locallab\nS3_ENDPOINT_URL=http://rustfs:9000\n",
            encoding="utf-8",
        )
        self.app = PanelApp(self.root, "token-value")
        PanelHandler.app = self.app
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), PanelHandler)
        self.server.app = self.app
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.port = self.server.server_address[1]
        self.cookie = ""

    def request(self, method, path, payload=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        body = json.dumps(payload) if payload is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        if self.cookie:
            headers["Cookie"] = self.cookie
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        set_cookie = response.getheader("Set-Cookie") or ""
        connection.close()
        if set_cookie.startswith("panel_session="):
            self.cookie = set_cookie.split(";", 1)[0]
        return response.status, raw

    def test_storage_and_backup_endpoints_need_authentication(self):
        for path in ("/api/storage", "/api/backups"):
            status, _ = self.request("GET", path)
            self.assertEqual(status, 401)

    def test_storage_endpoint_returns_the_shared_block(self):
        self.request("POST", "/api/login", {"token": "token-value"})
        status, body = self.request("GET", "/api/storage")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["backend"], "rustfs")
        self.assertEqual(payload["bucket"], "locallab")

    def test_backups_endpoint_lists_the_backup_directory(self):
        self.request("POST", "/api/login", {"token": "token-value"})
        status, body = self.request("GET", "/api/backups")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["entries"], [])
        self.assertFalse(payload["exists"])
        self.assertIn(str(self.root.parent / f"{self.root.name}-backups"), payload["directory"])


class PanelDraftApiTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.app = PanelApp(self.root, "token-value")
        PanelHandler.app = self.app
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), PanelHandler)
        self.server.app = self.app
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.port = self.server.server_address[1]
        self.cookie = ""

    def request(self, method, path, payload=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        body = json.dumps(payload) if payload is not None else None
        request_headers = dict(headers or {})
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        if self.cookie:
            request_headers["Cookie"] = self.cookie
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        set_cookie = response.getheader("Set-Cookie") or ""
        connection.close()
        if set_cookie.startswith("panel_session="):
            self.cookie = set_cookie.split(";", 1)[0]
        return response.status, raw

    def login(self):
        status, _ = self.request("POST", "/api/login", {"token": "token-value"})
        self.assertEqual(status, 200)
        return status

    def test_draft_actions_need_the_csrf_header(self):
        self.login()
        status, _ = self.request("POST", "/api/drafts/draft-1/action", {"action": "publish"})
        self.assertEqual(status, 403)

    def test_unknown_actions_and_ids_are_rejected(self):
        self.login()
        csrf = {"X-Panel-Csrf": "1"}
        status, body = self.request(
            "POST", "/api/drafts/draft-1/action", {"action": "destroy"}, headers=csrf
        )
        self.assertEqual(status, 400)
        self.assertIn("unknown draft action", body)
        status, body = self.request(
            "POST", "/api/drafts/..%2Fetc/action", {"action": "publish"}, headers=csrf
        )
        self.assertEqual(status, 400)

    def test_queued_action_and_results_are_visible_through_the_api(self):
        self.login()
        status, body = self.request(
            "POST", "/api/drafts/draft-1/action", {"action": "discard"}, headers={"X-Panel-Csrf": "1"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["queued"])
        results_path = self.root / "data" / "content-bot" / drafts_mod.RESULT_FILE
        results_path.write_text(
            json.dumps(
                {
                    "results": [
                        {
                            "draft_id": "draft-1",
                            "action": "discard",
                            "ok": True,
                            "message": "Draft discarded.",
                            "finished_at": "2026-09-10T12:00:00+00:00",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        status, body = self.request("GET", "/api/drafts")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["results"][0]["message"], "Draft discarded.")

    def test_instagram_view_reports_state_without_the_token(self):
        (self.root / ".env").write_text(
            "INSTAGRAM_BUSINESS_ID=17841426952001533\n"
            "INSTAGRAM_ACCESS_TOKEN=super-secret-token\n"
            "INSTAGRAM_API_VERSION=v26.0\n",
            encoding="utf-8",
        )
        token_file = self.root / "data" / "content-bot" / "instagram-token.json"
        token_file.write_text(
            json.dumps(
                {
                    "access_token": "refreshed-secret",
                    "refreshed_at": "2026-09-10T12:00:00+00:00",
                    "expires_at": "2026-11-09T12:00:00+00:00",
                    "source": "instagram-login",
                    "last_error": "",
                }
            ),
            encoding="utf-8",
        )
        self.login()
        status, body = self.request("GET", "/api/instagram")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(payload["token_set"])
        self.assertTrue(payload["configured"])
        self.assertTrue(payload["auto_publish"])
        self.assertEqual(payload["refreshed_at"], "2026-09-10T12:00:00+00:00")
        self.assertEqual(payload["refresh_source"], "instagram-login")
        self.assertNotIn("secret", body)

    def test_instagram_view_reports_the_auto_publish_switch(self):
        (self.root / ".env").write_text(
            "INSTAGRAM_BUSINESS_ID=17841426952001533\n"
            "INSTAGRAM_ACCESS_TOKEN=super-secret-token\n"
            "INSTAGRAM_AUTO_PUBLISH=false\n",
            encoding="utf-8",
        )
        self.login()
        status, body = self.request("GET", "/api/instagram")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertFalse(payload["auto_publish"])
        self.assertNotIn("secret", body)

    def test_instagram_view_resolves_the_tunnel_media_host(self):
        (self.root / ".env").write_text(
            "INSTAGRAM_BUSINESS_ID=17841426952001533\n"
            "INSTAGRAM_ACCESS_TOKEN=super-secret-token\n"
            "INSTAGRAM_MEDIA_PUBLIC_BASE_URL=https://stale.trycloudflare.com\n",
            encoding="utf-8",
        )
        tunnel = self.root / "data" / "content-bot" / "tunnel" / "trycloudflared.log"
        tunnel.parent.mkdir(parents=True, exist_ok=True)
        tunnel.write_text(
            "INF Visit it at https://live-name.trycloudflare.com\n", encoding="utf-8"
        )
        self.login()
        status, body = self.request("GET", "/api/instagram")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(
            payload["public_base_url"], "https://live-name.trycloudflare.com"
        )

    def test_instagram_refresh_queues_a_bot_request(self):
        self.login()
        status, body = self.request("POST", "/api/instagram/refresh", {}, headers={"X-Panel-Csrf": "1"})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["queued"])
        self.assertTrue((self.root / "data" / "content-bot" / "instagram-refresh.request").is_file())

    def test_video_studio_requires_authentication(self):
        status, _ = self.request("GET", "/api/video/studio")
        self.assertEqual(status, 401)

    def test_video_render_requires_the_csrf_header(self):
        self.login()
        status, body = self.request("POST", "/api/video/render", {"timeline": {"scenes": []}})
        self.assertEqual(status, 403)
        self.assertIn("X-Panel-Csrf", body)

    def test_video_studio_serves_the_view_payload(self):
        (self.root / ".env").write_text(
            "MEDIA_STUDIO_INTERNAL_URL=http://127.0.0.1:1\n"
            "MEDIA_STUDIO_DRIVERS=timeline-video\n",
            encoding="utf-8",
        )
        self.login()
        status, body = self.request("GET", "/api/video/studio")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertIn("jobs", payload)
        self.assertTrue(payload["timeline_driver"])


    def test_timeline_validate_requires_authentication_and_csrf(self):
        status, _ = self.request("POST", "/api/video/timeline/validate", {"timeline": {}})
        self.assertEqual(status, 401)
        self.login()
        status, body = self.request("POST", "/api/video/timeline/validate", {"timeline": {}})
        self.assertEqual(status, 403)
        self.assertIn("X-Panel-Csrf", body)

    def test_video_job_retry_requires_the_csrf_header(self):
        self.login()
        status, _ = self.request("POST", "/api/video/jobs/abc/retry")
        self.assertEqual(status, 403)

    def test_knowledge_search_requires_the_csrf_header(self):
        self.login()
        status, _ = self.request("POST", "/api/knowledge/search", {"query": "x"})
        self.assertEqual(status, 403)

    def test_knowledge_ingest_requires_the_csrf_header(self):
        self.login()
        status, _ = self.request("POST", "/api/knowledge/documents", {"kb_id": 1, "content": "x"})
        self.assertEqual(status, 403)

    def test_operations_views_require_authentication(self):
        for path in ("/api/hermes/overview", "/api/orchestration/runs", "/api/knowledge", "/api/knowledge/documents"):
            with self.subTest(path=path):
                status, _ = self.request("GET", path)
                self.assertEqual(status, 401)

    def test_operations_views_serve_payloads_and_report_a_missing_router(self):
        (self.root / ".env").write_text(
            "SMART_ROUTER_INTERNAL_URL=http://127.0.0.1:1\n"
            "SMART_ROUTER_ADMIN_API_KEY=router-key\n",
            encoding="utf-8",
        )
        self.login()
        status, body = self.request("GET", "/api/hermes/overview")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertFalse(payload["ok"])
        self.assertIn("console_url", payload)

        status, body = self.request("GET", "/api/orchestration/runs")
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["ok"])

        status, body = self.request("GET", "/api/knowledge")
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["ok"])


class MediaJobsViewTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))

    def test_media_jobs_default_to_the_compose_service_address(self):
        (self.root / ".env").write_text("MEDIA_STUDIO_PORT=8860\n", encoding="utf-8")
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            raise urllib.error.URLError("offline")

        with mock.patch("panel.server.urllib.request.urlopen", fake_urlopen):
            result = PanelApp(self.root, "token-value").media_jobs()
        self.assertFalse(result["ok"])
        self.assertEqual(captured["url"], "http://media-studio:8860/jobs")

    def test_media_jobs_use_the_configured_internal_url(self):
        body = json.dumps({"jobs": [{"id": "job-1", "status": "done"}]}).encode("utf-8")
        seen = {}

        class JobsHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen["path"] = self.path
                seen["auth"] = self.headers.get("Authorization")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), JobsHandler)
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        (self.root / ".env").write_text(
            "MEDIA_STUDIO_BIND_IP=192.168.1.50\n"
            "MEDIA_STUDIO_PORT=8850\n"
            "MEDIA_STUDIO_API_TOKEN=super-secret\n"
            f"MEDIA_STUDIO_INTERNAL_URL=http://127.0.0.1:{server.server_address[1]}\n",
            encoding="utf-8",
        )
        result = PanelApp(self.root, "token-value").media_jobs()
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(seen["path"], "/jobs")
        self.assertEqual(seen["auth"], "Bearer super-secret")
        self.assertEqual(result["jobs"][0]["id"], "job-1")


class JsonServiceHandler(BaseHTTPRequestHandler):
    """Tiny JSON responder used to stand in for Media Studio and the router."""

    routes = {}
    seen = []

    def _answer(self, method):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        type(self).seen.append(
            {
                "method": method,
                "path": self.path,
                "auth": self.headers.get("Authorization"),
                "body": json.loads(body.decode("utf-8")) if body else None,
            }
        )
        handler = type(self).routes.get((method, self.path))
        if handler is None:
            # Fall back to a path-prefix match so /jobs/<id> works.
            for (route_method, route_path), candidate in type(self).routes.items():
                if route_method == method and self.path.startswith(route_path):
                    handler = candidate
                    break
        if handler is None:
            self.send_response(404)
            self.end_headers()
            return
        status, payload, content_type = handler
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        self._answer("GET")

    def do_POST(self):  # noqa: N802
        self._answer("POST")

    def do_DELETE(self):  # noqa: N802
        self._answer("DELETE")

    def log_message(self, *args):
        pass


class VideoStudioTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))

    def _serve(self, routes):
        handler = type("Handler", (JsonServiceHandler,), {"routes": routes, "seen": []})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server, handler

    def _write_env(self, studio_url="", router_url=""):
        lines = [
            "MEDIA_STUDIO_DRIVERS=api-image,timeline-video",
            "MEDIA_STUDIO_BRAND_LABEL=Locallab",
            "MEDIA_STUDIO_API_TOKEN=studio-token",
            "SMART_ROUTER_ADMIN_API_KEY=router-key",
        ]
        if studio_url:
            lines.append(f"MEDIA_STUDIO_INTERNAL_URL={studio_url}")
        if router_url:
            lines.append(f"SMART_ROUTER_INTERNAL_URL={router_url}")
        (self.root / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_video_studio_reports_jobs_driver_and_router_readiness(self):
        server, _ = self._serve(
            {
                ("GET", "/jobs"): (
                    200,
                    {
                        "jobs": [
                            {
                                "id": "abc123",
                                "driver": "timeline-video",
                                "status": "done",
                                "artifacts": [{"name": "timeline-video.mp4", "kind": "video", "size": 10}],
                            }
                        ]
                    },
                    "application/json",
                )
            }
        )
        self._write_env(studio_url=f"http://127.0.0.1:{server.server_address[1]}")
        view = PanelApp(self.root, "token-value").video_studio()
        self.assertTrue(view["ok"], view.get("error"))
        self.assertTrue(view["timeline_driver"])
        self.assertTrue(view["router_ready"])
        self.assertEqual(view["jobs"][0]["id"], "abc123")
        self.assertEqual(view["jobs"][0]["artifacts"][0]["kind"], "video")

    def test_video_studio_reports_an_unreachable_worker(self):
        self._write_env(studio_url="http://127.0.0.1:1")
        view = PanelApp(self.root, "token-value").video_studio()
        self.assertFalse(view["ok"])
        self.assertEqual(view["jobs"], [])

    def test_video_plan_forwards_to_the_router_and_applies_the_brand(self):
        server, seen = self._serve(
            {
                ("POST", "/v1/content/video-plan"): (
                    200,
                    {
                        "storyboard": {"title": "Docker on RouterOS", "scenes": []},
                        "timeline": {"version": 1, "meta": {}, "scenes": [{"duration": 4, "narration": "x"}]},
                    },
                    "application/json",
                )
            }
        )
        self._write_env(
            studio_url="http://127.0.0.1:1",
            router_url=f"http://127.0.0.1:{server.server_address[1]}",
        )
        result = PanelApp(self.root, "token-value").video_plan({"topic": "docker"})
        self.assertEqual(result["timeline"]["meta"]["brand"]["label"], "Locallab")
        call = seen.seen[0]
        self.assertEqual(call["auth"], "Bearer router-key")
        self.assertEqual(call["body"]["topic"], "docker")

    def test_video_plan_requires_a_topic_or_script(self):
        self._write_env()
        with self.assertRaises(CommandError):
            PanelApp(self.root, "token-value").video_plan({"topic": "  "})

    def test_video_render_submits_the_timeline_and_reports_the_job(self):
        server, seen = self._serve(
            {
                ("POST", "/jobs"): (
                    200,
                    {"job": {"id": "job-9", "driver": "timeline-video", "status": "queued"}},
                    "application/json",
                )
            }
        )
        self._write_env(studio_url=f"http://127.0.0.1:{server.server_address[1]}")
        result = PanelApp(self.root, "token-value").video_render(
            {"timeline": {"version": 1, "meta": {}, "scenes": [{"duration": 3, "narration": "hi"}]}}
        )
        self.assertEqual(result["job"]["id"], "job-9")
        body = seen.seen[0]["body"]
        self.assertEqual(body["driver"], "timeline-video")
        self.assertEqual(body["params"]["timeline"]["scenes"][0]["narration"], "hi")

    def test_video_render_accepts_a_timeline_json_string(self):
        server, seen = self._serve(
            {
                ("POST", "/jobs"): (
                    200,
                    {"job": {"id": "job-10", "driver": "timeline-video", "status": "queued"}},
                    "application/json",
                )
            }
        )
        self._write_env(studio_url=f"http://127.0.0.1:{server.server_address[1]}")
        PanelApp(self.root, "token-value").video_render(
            {"timeline": json.dumps({"version": 1, "scenes": [{"duration": 3, "narration": "hi"}]})}
        )
        self.assertEqual(seen.seen[0]["body"]["driver"], "timeline-video")

    def test_video_render_rejects_broken_json_and_empty_scenes(self):
        self._write_env()
        app = PanelApp(self.root, "token-value")
        with self.assertRaises(CommandError):
            app.video_render({"timeline": "{not json"})
        with self.assertRaises(CommandError):
            app.video_render({"timeline": {"version": 1, "scenes": []}})

    def test_video_job_returns_the_log_tail(self):
        server, _ = self._serve(
            {
                ("GET", "/jobs/job-1"): (
                    200,
                    {"job": {"id": "job-1", "status": "running", "log_tail": "scene 1 rendered"}},
                    "application/json",
                )
            }
        )
        self._write_env(studio_url=f"http://127.0.0.1:{server.server_address[1]}")
        job = PanelApp(self.root, "token-value").video_job("job-1")["job"]
        self.assertEqual(job["log_tail"], "scene 1 rendered")

    def test_video_artifact_returns_bytes_and_content_type(self):
        server, _ = self._serve(
            {
                ("GET", "/artifacts/job-1/timeline-video.mp4"): (
                    200,
                    b"mp4-bytes",
                    "video/mp4",
                )
            }
        )
        self._write_env(studio_url=f"http://127.0.0.1:{server.server_address[1]}")
        body, content_type = PanelApp(self.root, "token-value").video_artifact(
            "job-1", "timeline-video.mp4"
        )
        self.assertEqual(body, b"mp4-bytes")
        self.assertEqual(content_type, "video/mp4")


    def test_timeline_validate_forwards_to_the_worker(self):
        server, seen = self._serve(
            {
                ("POST", "/timeline/validate"): (
                    200,
                    {"ok": True, "timeline": {"version": 1, "scenes": []}, "totals": {"scenes": 0}},
                    "application/json",
                )
            }
        )
        self._write_env(studio_url=f"http://127.0.0.1:{server.server_address[1]}")
        result = PanelApp(self.root, "token-value").video_timeline_validate(
            {"timeline": {"version": 1, "scenes": []}}
        )
        self.assertTrue(result["ok"])
        self.assertEqual(seen.seen[0]["body"]["timeline"]["version"], 1)

    def test_timeline_validate_reports_the_rejected_document(self):
        server, _ = self._serve(
            {
                ("POST", "/timeline/validate"): (
                    422,
                    {"ok": False, "error": "no scenes", "field": "scenes", "hint": "add one"},
                    "application/json",
                )
            }
        )
        self._write_env(studio_url=f"http://127.0.0.1:{server.server_address[1]}")
        app = PanelApp(self.root, "token-value")
        rejected = app.video_timeline_validate({"timeline": {"scenes": []}})
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["field"], "scenes")
        self.assertEqual(rejected["hint"], "add one")

    def test_timeline_validate_rejects_broken_json_and_missing_documents(self):
        self._write_env()
        app = PanelApp(self.root, "token-value")
        with self.assertRaises(CommandError):
            app.video_timeline_validate({"timeline": "{not json"})
        with self.assertRaises(CommandError):
            app.video_timeline_validate({})

    def test_video_retry_resubmits_the_original_params(self):
        server, seen = self._serve(
            {
                ("GET", "/jobs/job-1"): (
                    200,
                    {
                        "job": {
                            "id": "job-1",
                            "driver": "timeline-video",
                            "prompt": "retry me",
                            "status": "error",
                            "params": {"timeline": {"scenes": [{"duration": 4}]}},
                        }
                    },
                    "application/json",
                ),
                ("POST", "/jobs"): (
                    200,
                    {"job": {"id": "job-2", "driver": "timeline-video", "status": "queued"}},
                    "application/json",
                ),
            }
        )
        self._write_env(studio_url=f"http://127.0.0.1:{server.server_address[1]}")
        result = PanelApp(self.root, "token-value").video_retry("job-1")
        self.assertEqual(result["job"]["id"], "job-2")
        body = [call for call in seen.seen if call["method"] == "POST"][0]["body"]
        self.assertEqual(body["prompt"], "retry me")
        self.assertEqual(body["params"]["timeline"]["scenes"][0]["duration"], 4)

    def test_video_retry_rejects_an_unknown_job(self):
        server, _ = self._serve({("GET", "/jobs/missing"): (404, {"error": "Job not found."}, "application/json")})
        self._write_env(studio_url=f"http://127.0.0.1:{server.server_address[1]}")
        self.assertRaises(CommandError, PanelApp(self.root, "token-value").video_retry, "missing")

    def test_hermes_overview_collects_router_sections(self):
        server, seen = self._serve(
            {
                ("GET", "/router/info"): (200, {"version": "0.6.2", "control_plane": True}, "application/json"),
                ("GET", "/control/api/summary"): (200, {"requests": 12, "profiles": {"fast": 12}}, "application/json"),
                ("GET", "/control/api/agents"): (200, [{"id": 1, "name": "storyboard"}], "application/json"),
                ("GET", "/v1/content/agents"): (200, {"data": [{"name": "video-director"}]}, "application/json"),
                ("GET", "/v1/models"): (200, {"data": [{"id": "auto"}]}, "application/json"),
            }
        )
        self._write_env(
            studio_url="http://127.0.0.1:1",
            router_url=f"http://127.0.0.1:{server.server_address[1]}",
        )
        view = PanelApp(self.root, "token-value").hermes_overview()
        self.assertTrue(view["ok"])
        self.assertEqual(view["info"]["version"], "0.6.2")
        self.assertEqual(view["summary"]["requests"], 12)
        self.assertEqual(view["agents"][0]["name"], "storyboard")
        self.assertEqual(view["models"][0]["id"], "auto")
        self.assertIn("/control/", view["operations_url"])
        queries = [call["path"] for call in seen.seen]
        self.assertIn("/control/api/summary?hours=24", queries)

    def test_hermes_overview_reports_a_missing_router_key(self):
        (self.root / ".env").write_text("MEDIA_STUDIO_DRIVERS=timeline-video\n", encoding="utf-8")
        view = PanelApp(self.root, "token-value").hermes_overview()
        self.assertFalse(view["ok"])
        self.assertIn("SMART_ROUTER_ADMIN_API_KEY", view["info_error"])

    def test_orchestration_runs_and_detail(self):
        server, seen = self._serve(
            {
                ("GET", "/control/api/orchestrations"): (
                    200,
                    [{"id": 7, "status": "awaiting_approval", "steps_total": 3, "steps_done": 1}],
                    "application/json",
                ),
                ("GET", "/control/api/orchestrations/7"): (
                    200,
                    {"id": 7, "status": "awaiting_approval", "steps": [{"idx": 1, "status": "done"}]},
                    "application/json",
                ),
            }
        )
        self._write_env(
            studio_url="http://127.0.0.1:1",
            router_url=f"http://127.0.0.1:{server.server_address[1]}",
        )
        app = PanelApp(self.root, "token-value")
        listing = app.orchestration_runs(25)
        self.assertTrue(listing["ok"])
        self.assertEqual(listing["runs"][0]["id"], 7)
        detail = app.orchestration_run("7")
        self.assertEqual(detail["run"]["steps"][0]["status"], "done")
        paths = [call["path"] for call in seen.seen]
        self.assertIn("/control/api/orchestrations?limit=25", paths)

    def test_knowledge_overview_and_search(self):
        server, seen = self._serve(
            {
                ("GET", "/control/api/knowledge"): (
                    200,
                    [{"id": 1, "name": "brand", "chunks": 4}],
                    "application/json",
                ),
                ("POST", "/control/api/knowledge/search"): (
                    200,
                    [{"kb_id": 1, "score": 0.8, "content": "brand voice"}],
                    "application/json",
                ),
            }
        )
        self._write_env(
            studio_url="http://127.0.0.1:1",
            router_url=f"http://127.0.0.1:{server.server_address[1]}",
        )
        app = PanelApp(self.root, "token-value")
        overview = app.knowledge_overview()
        self.assertEqual(overview["bases"][0]["chunks"], 4)
        result = app.knowledge_search({"query": "brand", "kb_ids": ["1"], "limit": 3})
        self.assertEqual(result["results"][0]["content"], "brand voice")
        body = [call for call in seen.seen if call["method"] == "POST"][0]["body"]
        self.assertEqual(body["kb_ids"], [1])
        self.assertEqual(body["limit"], 3)

    def test_knowledge_ingest_posts_one_document(self):
        server, seen = self._serve(
            {
                ("POST", "/control/api/knowledge/3/documents"): (
                    200,
                    {"chunks": 2},
                    "application/json",
                )
            }
        )
        self._write_env(
            studio_url="http://127.0.0.1:1",
            router_url=f"http://127.0.0.1:{server.server_address[1]}",
        )
        result = PanelApp(self.root, "token-value").knowledge_ingest(
            {"kb_id": "3", "title": "Brand voice", "content": "Write plainly."}
        )
        self.assertEqual(result["chunks"], 2)
        body = seen.seen[0]["body"]
        self.assertEqual(body["title"], "Brand voice")
        self.assertEqual(body["source"], "panel")

    def test_knowledge_ingest_validates_input(self):
        self._write_env()
        app = PanelApp(self.root, "token-value")
        self.assertRaises(CommandError, app.knowledge_ingest, {"content": "x"})
        self.assertRaises(CommandError, app.knowledge_ingest, {"kb_id": "1", "content": " "})

    def test_knowledge_search_requires_a_query_and_numeric_ids(self):
        self._write_env()
        app = PanelApp(self.root, "token-value")
        self.assertRaises(CommandError, app.knowledge_search, {"query": " "})
        self.assertRaises(CommandError, app.knowledge_search, {"query": "x", "kb_ids": ["abc"]})


class StaticAssetTest(unittest.TestCase):
    def test_console_assets_exist_and_are_wired(self):
        index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn("/static/app.js", index)
        self.assertIn("/static/style.css", index)
        script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        for endpoint in ("/api/login", "/api/session", "/api/status", "/api/config/", "/api/env/", "/api/logs/", "/api/actions/", "/api/drafts", "/api/instagram", "/api/storage", "/api/backups", "/api/platforms", "/api/video/studio", "/api/video/plan", "/api/video/render", "/api/video/timeline/validate", "/api/video/jobs/", "/api/hermes/overview", "/api/orchestration/runs", "/api/knowledge"):
            self.assertIn(endpoint, script)
        self.assertIn("X-Panel-Csrf", script)


class ComposeWiringTest(unittest.TestCase):
    def setUp(self):
        self.compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    def test_panel_service_is_optional_and_bound_to_loopback(self):
        self.assertIn("profiles: [\"panel\"]", self.compose)
        self.assertIn("${PANEL_BIND_IP:-127.0.0.1}:${PANEL_PORT:-8899}:8899", self.compose)
        self.assertIn("PANEL_TOKEN_FILE: /run/panel/token", self.compose)

    def test_panel_mounts_the_repository_at_its_host_path(self):
        self.assertIn("${PANEL_STACK_PATH:-${PWD}}:${PANEL_STACK_PATH:-${PWD}}", self.compose)
        self.assertIn("/var/run/docker.sock:/var/run/docker.sock", self.compose)
        self.assertIn("${PANEL_STACK_PATH:-${PWD}}-backups:${PANEL_STACK_PATH:-${PWD}}-backups", self.compose)

    def test_every_platform_field_reaches_its_service(self):
        compose = yaml.safe_load(self.compose)
        for platform in PLATFORMS:
            environment = compose["services"][platform.service]["environment"]
            if isinstance(environment, list):
                environment = {entry.split("=", 1)[0]: "" for entry in environment}
            for field in platform.fields:
                self.assertIn(
                    field.key,
                    environment,
                    f"{platform.key} writes {field.key} but {platform.service} "
                    "does not receive it",
                )

    def test_manage_script_exposes_the_panel_commands(self):
        script = (REPO_ROOT / "manage.sh").read_text(encoding="utf-8")
        for command in ("panel-enable", "panel-disable", "panel-status", "panel-token", "panel-rotate-token"):
            self.assertIn(command, script)
        self.assertIn("$PANEL_DIR/token", script)


if __name__ == "__main__":
    unittest.main()
