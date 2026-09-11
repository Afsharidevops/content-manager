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

from panel import __version__
from panel.actions import ActionError, ActionRunner
from panel import drafts as drafts_mod
from panel.editors import ConfigStore, EditError, EnvStore
from panel.server import PanelApp, PanelHandler
from panel.stack import StackView

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

    def test_policy_rejects_duplicate_routine_ids(self):
        duplicate = VALID_POLICY + "  - id: morning\n    cadence: daily\n"
        with self.assertRaisesRegex(EditError, "duplicate routine id"):
            self.store.write("editorial-policy", duplicate)

    def test_policy_rejects_unknown_cadence_and_bad_count(self):
        with self.assertRaisesRegex(EditError, "cadence"):
            self.store.write("editorial-policy", "routines:\n  - id: hourly\n    cadence: hourly\n")
        with self.assertRaisesRegex(EditError, "at least 1"):
            self.store.write("editorial-policy", "routines:\n  - id: morning\n    count: 0\n")

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
        self.assertEqual(payload["rustfs"]["api_url"], "http://192.168.1.50:9000")
        self.assertEqual(payload["rustfs"]["service"], None)

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
        self.assertEqual(payload["refreshed_at"], "2026-09-10T12:00:00+00:00")
        self.assertEqual(payload["refresh_source"], "instagram-login")
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


class StaticAssetTest(unittest.TestCase):
    def test_console_assets_exist_and_are_wired(self):
        index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn("/static/app.js", index)
        self.assertIn("/static/style.css", index)
        script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        for endpoint in ("/api/login", "/api/session", "/api/status", "/api/config/", "/api/env/", "/api/logs/", "/api/actions/", "/api/drafts", "/api/instagram", "/api/storage", "/api/backups"):
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

    def test_manage_script_exposes_the_panel_commands(self):
        script = (REPO_ROOT / "manage.sh").read_text(encoding="utf-8")
        for command in ("panel-enable", "panel-disable", "panel-status", "panel-token", "panel-rotate-token"):
            self.assertIn(command, script)
        self.assertIn("$PANEL_DIR/token", script)


if __name__ == "__main__":
    unittest.main()
