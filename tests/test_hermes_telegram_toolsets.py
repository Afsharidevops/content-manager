import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALL = (ROOT / "install.sh").read_text(encoding="utf-8")
MANAGE = (ROOT / "manage.sh").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "templates" / "hermes-config.yaml.template").read_text(encoding="utf-8")


class HermesTelegramToolsetRegressionTest(unittest.TestCase):
    def test_installer_persists_both_stack_toolsets_for_telegram(self):
        self.assertIn('[[ "${install_hermes:-false}" == true && -n "${telegram_token:-}" ]] || return 0', INSTALL)
        self.assertIn(
            "/opt/hermes/.venv/bin/hermes tools enable stack-execution-policy --platform telegram",
            INSTALL,
        )
        self.assertIn(
            "/opt/hermes/.venv/bin/hermes tools enable stack-package-policy --platform telegram",
            INSTALL,
        )
        self.assertIn("enable_hermes_telegram_policy_toolsets", INSTALL)
        self.assertIn('restart hermes >/dev/null', INSTALL)

    def test_doctor_checks_current_plugin_toolset_names(self):
        self.assertNotIn("stack_packages", MANAGE)
        self.assertIn('enabled[[:space:]]+stack-package-policy([[:space:]]|$)', MANAGE)
        self.assertIn('enabled[[:space:]]+stack-execution-policy([[:space:]]|$)', MANAGE)
        self.assertIn("Hermes package policy toolset: enabled for Telegram", MANAGE)
        self.assertIn("Hermes execution policy toolset: enabled for Telegram", MANAGE)

    def test_doctor_checks_execution_plugin_runtime_registration(self):
        self.assertIn(
            'stack-execution-policy is not registered as an enabled user plugin',
            MANAGE,
        )

    def test_template_does_not_hardcode_platform_toolsets_allowlist(self):
        self.assertNotIn("platform_toolsets:", TEMPLATE)


if __name__ == "__main__":
    unittest.main()
