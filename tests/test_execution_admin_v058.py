import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "execution-broker" / "src"))
from broker import admin


class ExecutionAdminV058Test(unittest.TestCase):
    def paths(self, root: Path):
        for name in ("features","generation","users","allowed","bot-token","hermes-token.sha256","control-secret","admin-key"):
            (root / name).touch()
        (root / "ssh").mkdir(exist_ok=True)
        return mock.patch.multiple(
            admin,
            FEATURES_PATH=root / "features",
            GENERATION_PATH=root / "generation",
            USERS_PATH=root / "users",
            ALLOWED_USERS_PATH=root / "allowed",
            BOT_TOKEN_PATH=root / "bot-token",
            HERMES_BOT_TOKEN_HASH_PATH=root / "hermes-token.sha256",
            CONTROL_SECRET_PATH=root / "control-secret",
            SSH_PROFILES_PATH=root / "ssh",
            AUDIT_PATH=root / "audit.jsonl",
            ADMIN_KEY_PATH=root / "admin-key",
        )

    def test_features_are_canonical_and_bump_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.paths(root):
                (root / "generation").write_text("7\n", encoding="utf-8")
                result = admin.set_features({"features": ["docker", "sandbox", "docker"]})
                self.assertEqual(result["features"], ["local", "docker"])
                self.assertEqual((root / "features").read_text().strip(), "local,docker")
                self.assertEqual((root / "generation").read_text().strip(), "8")
                with self.assertRaises(ValueError):
                    admin.set_features({"features": ["shell"]})

    def test_users_must_be_numeric_subset_of_telegram_allowed_users(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.paths(root):
                (root / "allowed").write_text("123,456\n", encoding="utf-8")
                result = admin.set_users({"users": ["456", "123", "456"]})
                self.assertEqual(result["users"], ["456", "123"])
                with self.assertRaises(ValueError):
                    admin.set_users({"users": ["999"]})
                with self.assertRaises(ValueError):
                    admin.set_users({"users": ["not-numeric"]})

    def test_bot_token_is_write_only_and_cannot_equal_hermes_token(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.paths(root):
                token = "123456:" + "A" * 24
                (root / "hermes-token.sha256").write_text(hashlib.sha256(token.encode()).hexdigest())
                with self.assertRaises(ValueError):
                    admin.replace_bot_token({"token": token})
                other = "654321:" + "B" * 24
                result = admin.replace_bot_token({"token": other})
                self.assertTrue(result["bot_token_configured"])
                self.assertIsNone(result["token"])
                self.assertEqual((root / "bot-token").read_text().strip(), other)
                self.assertNotIn(other, json.dumps(result))

    def test_browser_origins_are_exact_and_do_not_accept_wildcards(self):
        with mock.patch.dict("os.environ", {"EXECUTION_ADMIN_ALLOWED_ORIGINS": "http://192.168.1.20:8787,http://localhost:8787"}, clear=False):
            self.assertTrue(admin.allowed_origin("http://192.168.1.20:8787"))
            self.assertFalse(admin.allowed_origin("http://192.168.1.20:9999"))
            self.assertFalse(admin.allowed_origin("http://evil.example"))
            self.assertTrue(admin.allowed_origin(""))  # CLI/curl clients have no Origin.

    def test_status_never_claims_privileged_mounts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.paths(root), mock.patch.object(admin, "_probe", return_value={"reachable": False, "status": "unreachable"}):
                (root / "features").write_text("ssh\n")
                (root / "users").write_text("123\n")
                (root / "allowed").write_text("123\n")
                (root / "generation").write_text("3\n")
                (root / "bot-token").write_text("123456:" + "A" * 24)
                (root / "admin-key").write_text("secret")
                status = admin.status()
                self.assertEqual(status["version"], "0.1.3")
                self.assertEqual(status["features"], ["ssh"])
                self.assertFalse(status["security"]["signing_key_mounted"])
                self.assertFalse(status["security"]["docker_socket_mounted"])
                self.assertFalse(status["security"]["ssh_private_credentials_mounted"])
                self.assertFalse(status["security"]["bot_token_readback"])

    def test_execution_admin_compose_has_private_ingress_without_weakening_control_network(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        admin = compose.split("  execution-admin:\n", 1)[1].split("\n  open-webui:\n", 1)[0]
        docker_broker = compose.split("  execution-docker-broker:\n", 1)[1].split("\n  execution-ssh-broker:\n", 1)[0]
        ssh_broker = compose.split("  execution-ssh-broker:\n", 1)[1].split("\n  execution-approver:\n", 1)[0]
        approver = compose.split("  execution-approver:\n", 1)[1].split("\n  execution-admin:\n", 1)[0]
        networks = compose.split("\nnetworks:\n", 1)[1]

        self.assertIn("- execution-control-net", admin)
        self.assertIn("- execution-admin-ingress-net", admin)
        self.assertIn("${EXECUTION_ADMIN_BIND_IP:-127.0.0.1}:${EXECUTION_ADMIN_PORT:-8752}:8752", admin)
        self.assertNotIn("execution-admin-ingress-net", docker_broker)
        self.assertNotIn("execution-admin-ingress-net", ssh_broker)
        self.assertNotIn("execution-admin-ingress-net", approver)
        self.assertIn("execution-admin-ingress-net:", networks)
        control = networks.split("execution-control-net:", 1)[1].split("execution-admin-ingress-net:", 1)[0]
        self.assertIn("internal: true", control)


if __name__ == "__main__":
    unittest.main()
