import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


PLUGIN_PATH = (
    Path(__file__).resolve().parents[1]
    / "plugins"
    / "stack-package-policy"
    / "__init__.py"
)


def load_plugin():
    name = f"stack_package_policy_test_{os.urandom(4).hex()}"
    spec = importlib.util.spec_from_file_location(name, PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class PackagePolicyTest(unittest.TestCase):
    def setUp(self):
        self.plugin = load_plugin()
        self.session = mock.patch.object(self.plugin, "_current_session_key", return_value="telegram:1")
        self.platform = mock.patch.object(self.plugin, "_session_platform", return_value="telegram")
        self.cron = mock.patch.object(self.plugin, "_cron_context", return_value=False)
        self.bypass = mock.patch.object(self.plugin, "_approval_bypass_active", return_value=False)
        self.manual = mock.patch.object(self.plugin, "_manual_approval_mode", return_value=True)
        self.session.start()
        self.platform.start()
        self.cron.start()
        self.bypass.start()
        self.manual.start()
        self.addCleanup(mock.patch.stopall)

    def parse(self, value):
        return json.loads(value)

    def prepare_python(self, spec="requests==2.32.5"):
        return self.parse(self.plugin._prepare_python({"spec": spec}))

    def prepare_npm(self, spec="is-number@7.0.0"):
        return self.parse(self.plugin._prepare_npm({"spec": spec}))

    def test_python_exact_spec_and_normalized_argv(self):
        result = self.prepare_python()
        self.assertEqual(result["status"], "prepared")
        self.assertEqual(result["package"], "requests")
        self.assertEqual(result["version"], "2.32.5")
        self.assertIn("--only-binary=:all:", result["command"])
        self.assertIn("/opt/data/lazy-packages", result["destination"])
        self.assertNotIn("shell", result)

    def test_npm_exact_spec_and_lifecycle_scripts_disabled(self):
        result = self.prepare_npm("@scope/pkg@1.2.3")
        self.assertEqual(result["package"], "@scope/pkg")
        self.assertEqual(result["version"], "1.2.3")
        self.assertIn("--ignore-scripts", result["command"])
        self.assertIn("/opt/data/npm-packages", result["destination"])

    def test_rejects_unpinned_and_untrusted_specs(self):
        bad_python = [
            "requests", "requests>=2", "requests==latest", "requests[security]==2.0.0",
            "-e .", "./pkg==1", "https://example.com/pkg.whl", "pkg==1;id", "pkg @ git+https://x",
        ]
        bad_npm = [
            "lodash", "lodash@latest", "lodash@^4.0.0", "lodash@1", "file:pkg@1.0.0",
            "https://example.com/pkg.tgz", "pkg@1.0.0 --registry=x", "pkg@1.0.0;id",
        ]
        for value in bad_python:
            with self.subTest(value=value):
                self.assertIn("error", self.parse(self.plugin._prepare_python({"spec": value})))
        for value in bad_npm:
            with self.subTest(value=value):
                self.assertIn("error", self.parse(self.plugin._prepare_npm({"spec": value})))

    def test_each_operation_has_unique_one_time_rule_key(self):
        one = self.prepare_python()
        two = self.prepare_python()
        self.assertNotEqual(one["pending_id"], two["pending_id"])
        directive = self.plugin._pre_tool_call(
            "stack_install_python_package", args={"pending_id": one["pending_id"]}, task_id="t1"
        )
        self.assertEqual(directive["action"], "approve")
        self.assertEqual(
            directive["rule_key"], f"stack-package-install:{one['pending_id']}"
        )
        replay = self.plugin._pre_tool_call(
            "stack_install_python_package", args={"pending_id": one["pending_id"]}, task_id="t1"
        )
        self.assertEqual(replay["action"], "block")

    def test_install_consumes_operation_before_execution(self):
        with tempfile.TemporaryDirectory() as target:
            original_target = self.plugin._PYTHON_TARGET
            self.plugin._PYTHON_TARGET = Path(target)
            try:
                prepared = self.prepare_python()
            finally:
                self.plugin._PYTHON_TARGET = original_target
            pending_id = prepared["pending_id"]
            directive = self.plugin._pre_tool_call("stack_install_python_package", args={"pending_id": pending_id}, task_id="t1")
            self.plugin._post_approval_response(pattern_key=f"plugin_rule:{directive['rule_key']}", choice="once")
            completed = types.SimpleNamespace(returncode=0, stdout="ok", stderr="")
            with mock.patch("subprocess.run", return_value=completed) as run:
                result = self.parse(self.plugin._install_python({"pending_id": pending_id}))
        self.assertEqual(result["status"], "installed")
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
        replay = self.parse(self.plugin._install_python({"pending_id": pending_id}))
        self.assertIn("error", replay)

    def test_denial_timeout_or_restart_invalidates_operation(self):
        prepared = self.prepare_python()
        pending_id = prepared["pending_id"]
        directive = self.plugin._pre_tool_call(
            "stack_install_python_package", {"pending_id": pending_id}
        )
        self.plugin._post_approval_response(
            pattern_key=f"plugin_rule:{directive['rule_key']}", choice="deny"
        )
        self.assertIn("error", self.parse(self.plugin._install_python({"pending_id": pending_id})))
        self.assertEqual(load_plugin()._operations, {})

    def test_install_rechecks_manual_approval_mode(self):
        with tempfile.TemporaryDirectory() as target:
            original_target = self.plugin._PYTHON_TARGET
            self.plugin._PYTHON_TARGET = Path(target)
            try:
                prepared = self.prepare_python()
            finally:
                self.plugin._PYTHON_TARGET = original_target
        pending_id = prepared["pending_id"]
        directive = self.plugin._pre_tool_call("stack_install_python_package", args={"pending_id": pending_id}, task_id="t1")
        self.plugin._post_approval_response(pattern_key=f"plugin_rule:{directive['rule_key']}", choice="once")
        with mock.patch.object(self.plugin, "_manual_approval_mode", return_value=False):
            result = self.parse(self.plugin._install_python({"pending_id": pending_id}))
        self.assertIn("error", result)

    def test_cron_non_telegram_and_bypass_fail_closed(self):
        with mock.patch.object(self.plugin, "_cron_context", return_value=True):
            self.assertIn("error", self.prepare_python())
        with mock.patch.object(self.plugin, "_session_platform", return_value="api"):
            self.assertIn("error", self.prepare_python())
        with mock.patch.object(self.plugin, "_approval_bypass_active", return_value=True):
            self.assertIn("error", self.prepare_python())
        with mock.patch.object(self.plugin, "_manual_approval_mode", return_value=False):
            self.assertIn("error", self.prepare_python())

    def test_blocks_raw_managers_and_managed_target_writes(self):
        blocked = [
            "pip install x", "python -m pip install x", "uv pip install x", "npm install x",
            "npx tool", "yarn add x", "pnpm add x", "corepack enable", "apt-get install x",
            "sudo apt install x", "hermes skills install https://example.com/SKILL.md",
            "echo ok && npm install x", "(pip install x)",
        ]
        for command in blocked:
            with self.subTest(command=command):
                directive = self.plugin._pre_tool_call("terminal", args={"command": command}, task_id="t1")
                self.assertEqual(directive["action"], "block")
        self.assertEqual(
            self.plugin._pre_tool_call(
                "write_file", args={"path": "/opt/data/lazy-packages/evil.py"}, task_id="t1"
            )["action"],
            "block",
        )
        self.assertIsNone(self.plugin._pre_tool_call("terminal", args={"command": "ls -la"}, task_id="t1"))

    def test_installer_env_uses_distinct_npm_configs_and_strips_injection(self):
        with tempfile.TemporaryDirectory() as prefix:
            original_prefix = self.plugin._NPM_PREFIX
            self.plugin._NPM_PREFIX = Path(prefix)
            try:
                prepared = self.prepare_npm()
            finally:
                self.plugin._NPM_PREFIX = original_prefix
        pending_id = prepared["pending_id"]
        directive = self.plugin._pre_tool_call("stack_install_npm_package", args={"pending_id": pending_id}, task_id="t1")
        self.plugin._post_approval_response(pattern_key=f"plugin_rule:{directive['rule_key']}", choice="once")
        ambient = {
            "NPM_CONFIG_REGISTRY": "https://evil.example/",
            "NPM_CONFIG_IGNORE_SCRIPTS": "false",
            "PIP_INDEX_URL": "https://evil.example/simple",
            "UV_INDEX_URL": "https://evil.example/simple",
            "NODE_OPTIONS": "--require /tmp/evil.js",
            "NODE_PATH": "/tmp/evil",
            "PYTHONPATH": "/tmp/evil",
            "PYTHONHOME": "/tmp/evil",
            "PYTHONSTARTUP": "/tmp/evil.py",
        }
        completed = types.SimpleNamespace(returncode=0, stdout="ok", stderr="")
        with mock.patch.dict(os.environ, ambient, clear=False):
            with mock.patch("subprocess.run", return_value=completed) as run:
                self.plugin._install_npm({"pending_id": pending_id})
        env = run.call_args.kwargs["env"]

        user_config = env["NPM_CONFIG_USERCONFIG"]
        global_config = env["NPM_CONFIG_GLOBALCONFIG"]
        # npm 10.11+ aborts when one path is loaded as both user and global.
        self.assertNotEqual(user_config, global_config)
        self.assertNotEqual(user_config, os.devnull)
        self.assertNotEqual(global_config, os.devnull)
        self.assertTrue(Path(user_config).is_file())
        self.assertTrue(Path(global_config).is_file())

        self.assertEqual(env["NPM_CONFIG_IGNORE_SCRIPTS"], "true")
        self.assertNotIn("NPM_CONFIG_REGISTRY", env)
        self.assertNotIn("PIP_INDEX_URL", env)
        self.assertNotIn("UV_INDEX_URL", env)
        self.assertEqual(env["PIP_CONFIG_FILE"], os.devnull)
        for key in ("NODE_OPTIONS", "NODE_PATH", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
            self.assertNotIn(key, env)

    @unittest.skipUnless(
        subprocess.run(["sh", "-c", "command -v npm"], capture_output=True).returncode == 0,
        "npm is unavailable",
    )
    def test_real_npm_install_accepts_the_stack_config_pair(self):
        """Regression for: double-loading config "/dev/null" as "global"."""
        env = os.environ.copy()
        for key in list(env):
            if key.upper().startswith("NPM_CONFIG_") or key.upper() in self.plugin._STRIPPED_ENV:
                env.pop(key, None)
        env.update({
            "NPM_CONFIG_USERCONFIG": str(self.plugin._NPM_USER_CONFIG),
            "NPM_CONFIG_GLOBALCONFIG": str(self.plugin._NPM_GLOBAL_CONFIG),
            "NPM_CONFIG_IGNORE_SCRIPTS": "true",
            "NPM_CONFIG_AUDIT": "false",
            "NPM_CONFIG_FUND": "false",
        })
        with tempfile.TemporaryDirectory() as prefix:
            env["HOME"] = prefix
            completed = subprocess.run(
                [
                    "npm", "install", "--global", "--prefix", prefix,
                    "--registry", self.plugin._NPM_REGISTRY,
                    "--ignore-scripts", "--no-audit", "--no-fund", "--dry-run",
                    "is-number@7.0.0",
                ],
                capture_output=True, text=True, env=env, timeout=180, check=False,
            )
        self.assertNotIn("double-loading config", completed.stderr)
        self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])

    def test_registration_exposes_no_generic_command_argument(self):
        class Context:
            def __init__(self):
                self.tools = []
                self.hooks = []

            def register_tool(self, name, toolset, schema, handler, **kwargs):
                self.tools.append({"name": name, "toolset": toolset, "schema": schema, "handler": handler, **kwargs})

            def register_hook(self, name, handler):
                self.hooks.append((name, handler))

        context = Context()
        self.plugin.register(context)
        self.assertEqual(len(context.tools), 4)
        self.assertIn("pre_tool_call", [name for name, _ in context.hooks])
        self.assertIn("post_approval_response", [name for name, _ in context.hooks])
        for tool in context.tools:
            properties = tool["schema"]["parameters"]["properties"]
            self.assertNotIn("command", properties)
            self.assertNotIn("destination", properties)
            self.assertEqual(tool["toolset"], "stack-package-policy")
            self.assertEqual(tool["schema"].get("name"), tool["name"])

    def test_handler_accepts_current_runtime_kwargs(self):
        result = self.parse(self.plugin._prepare_python({"spec": "requests==2.32.5"}, task_id="t1", session_id="s1"))
        self.assertEqual(result["status"], "prepared")

    def test_current_pre_tool_directive_shape(self):
        prepared = self.prepare_python()
        directive = self.plugin._pre_tool_call(
            "stack_install_python_package", args={"pending_id": prepared["pending_id"]}, task_id="t1"
        )
        self.assertEqual(directive["action"], "approve")
        self.assertIn("message", directive)
        self.assertNotIn("description", directive)
        self.assertNotIn("reason", directive)


if __name__ == "__main__":
    unittest.main()
