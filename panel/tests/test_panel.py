"""Tests for the operator panel: editors, auth, exposure, actions, HTTP API."""

import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from panel import __version__
from panel.actions import ActionError, ActionRunner
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
        self.assertEqual(confirming, {"stack-up"})

    def test_compose_commands_use_the_stack_project(self):
        runner = ActionRunner(self.root)
        action = next(item for item in runner.listing() if item["name"] == "restart-content")
        command = runner.command_for(
            next(a for a in __import__("panel.actions", fromlist=["ACTIONS"]).ACTIONS if a.name == action["name"])
        )
        self.assertEqual(command[:2], ["docker", "compose"])
        self.assertIn(str(self.root / "docker-compose.yml"), command)
        self.assertEqual(command[-2:], ["restart", "content-bot"])

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


class StaticAssetTest(unittest.TestCase):
    def test_console_assets_exist_and_are_wired(self):
        index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn("/static/app.js", index)
        self.assertIn("/static/style.css", index)
        script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        for endpoint in ("/api/login", "/api/session", "/api/status", "/api/config/", "/api/env/", "/api/logs/", "/api/actions/"):
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

    def test_manage_script_exposes_the_panel_commands(self):
        script = (REPO_ROOT / "manage.sh").read_text(encoding="utf-8")
        for command in ("panel-enable", "panel-disable", "panel-status", "panel-token", "panel-rotate-token"):
            self.assertIn(command, script)
        self.assertIn("$PANEL_DIR/token", script)


if __name__ == "__main__":
    unittest.main()
