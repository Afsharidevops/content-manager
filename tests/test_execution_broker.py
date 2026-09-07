import hashlib
import hmac
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "execution-broker" / "src"))
from broker import approval, schema, ssh
from broker.approver import ApprovalRequestStore, ApproverError, validate_submission
from broker.sandbox import build_sandbox_body
from broker.store import CapabilityError, CapabilityStore


class BrokerPolicyTest(unittest.TestCase):
    def test_local_defaults_and_bounds(self):
        request = schema.validate_local({"command": "id"})
        self.assertEqual(request["network"], "none")
        self.assertFalse(request["net_raw"])
        with self.assertRaises(schema.RequestError):
            schema.validate_local({"command": "id", "unknown": True})
        with self.assertRaises(schema.RequestError):
            schema.validate_local({"command": "id", "workdir": "/etc"})

    def test_docker_requires_pinned_image_and_complete_warning(self):
        with self.assertRaises(schema.RequestError):
            schema.validate_docker({"action": "run", "image": "alpine:latest"})
        request = schema.validate_docker({
            "action": "run", "image": "sha256:" + "a" * 64,
            "privileged": True,
            "mounts": [{"type": "bind", "source": "/tmp", "target": "/host", "read_only": False}],
            "pid_mode": "host",
        })
        rendered = approval.render_summary("docker", request)
        self.assertIn("PRIVILEGED", rendered)
        self.assertIn("Host PID namespace", rendered)
        self.assertIn("bind-mounted READ-WRITE", rendered)
        self.assertIn("cap_drop:", rendered)

    def test_floor_blocks_authority_paths_networks_and_protected_containers(self):
        image = "sha256:" + "b" * 64
        for source in ("/", "/run", "/run/secrets", "/run/secrets/child",
                       "/var/run", "/var/run/docker.sock"):
            with self.subTest(source=source):
                request = schema.validate_docker({
                    "action": "run", "image": image,
                    "mounts": [{"type": "bind", "source": source,
                                "target": "/host", "read_only": True}],
                })
                with self.assertRaises(approval.DeniedError):
                    approval.check_floor("docker", request)
        with self.assertRaises(approval.DeniedError):
            approval.check_floor("docker", schema.validate_docker({
                "action": "run", "image": image,
                "network": "hermes-execution-control",
            }))
        approval.set_protected_network_ids({"network-id"})
        with self.assertRaises(approval.DeniedError):
            request = schema.validate_docker({"action": "run", "image": image})
            request["resolved_network_id"] = "network-id"
            approval.check_floor("docker", request)
        with self.assertRaises(approval.DeniedError):
            approval.check_floor("docker", {"action": "remove", "container": "hermes-agent"})
        approval.set_protected_container_ids({"f" * 64})
        for action in ("inspect", "logs", "remove"):
            with self.subTest(action=action), self.assertRaises(approval.DeniedError):
                approval.check_floor("docker", {
                    "action": action, "container": "f" * 12,
                    "resolved_name": "renamed-service",
                })

    def test_secret_ref_is_rejected(self):
        with self.assertRaises(schema.RequestError):
            schema.validate_docker({
                "action": "run", "image": "sha256:" + "c" * 64,
                "environment": [{"name": "TOKEN", "secret_ref": "anything"}],
            })

    def test_approver_revalidates_exact_operation_and_resolves_once(self):
        request = schema.validate_local({"command": "id"})
        request.update(resolved_image_id="sha256:" + "d" * 64,
                       workspace_generation="7")
        payload = {
            "target": "docker", "feature": "local", "capability": "n" * 43,
            "digest": approval.canonical_digest("local", request), "request": request,
            "summary": approval.render_summary("local", request), "user_id": "123",
            "session": "telegram:123", "generation": "7",
        }
        self.assertIs(validate_submission(payload, generation="7"), payload)
        for field, value in (("summary", "changed"), ("digest", "0" * 64),
                             ("generation", "8")):
            changed = dict(payload)
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(ApproverError):
                validate_submission(changed, generation="7")
        with tempfile.TemporaryDirectory() as directory:
            store = ApprovalRequestStore(Path(directory) / "approvals.sqlite3")
            token = store.create(payload, ttl_seconds=300)
            with self.assertRaises(ApproverError):
                store.resolve(token, "approved", "123")
            store.mark_delivered(payload["capability"])
            with self.assertRaises(ApproverError):
                store.resolve(token, "approved", "999")
            grant = store.resolve(token, "approved", "123")
            self.assertEqual(grant["digest"], payload["digest"])
            with self.assertRaises(ApproverError):
                store.resolve(token, "approved", "123")
            self.assertEqual(store.unresolved_decisions()[0]["decision"], "approved")
            store.mark_granted(payload["capability"], "approved")
            self.assertEqual(store.unresolved_decisions(), [])

    def test_capability_atomic_consumption_and_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CapabilityStore(Path(directory) / "state.sqlite3")
            nonce = store.issue(feature="local", digest="d", request="{}", user_id="1", session="s", generation="1")
            with self.assertRaises(CapabilityError):
                store.consume(nonce=nonce, feature="local", digest="d", user_id="1", session="s", generation="1")
            store.approve(nonce=nonce, feature="local", digest="d", user_id="1", session="s", generation="1")
            with self.assertRaises(CapabilityError):
                store.consume(nonce=nonce, feature="local", digest="bad", user_id="1", session="s", generation="1")
            result = store.consume(nonce=nonce, feature="local", digest="d", user_id="1", session="s", generation="1")
            self.assertEqual(result["digest"], "d")
            with self.assertRaises(CapabilityError):
                store.consume(nonce=nonce, feature="local", digest="d", user_id="1", session="s", generation="1")
            revoked = store.issue(feature="local", digest="e", request="{}", user_id="1", session="s", generation="1")
            store.cancel_generation("2")
            with self.assertRaises(CapabilityError):
                store.consume(nonce=revoked, feature="local", digest="e", user_id="1", session="s", generation="2")

    def test_capability_waits_for_independent_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CapabilityStore(Path(directory) / "state.sqlite3")
            nonce = store.issue(feature="local", digest="d", request="{}", user_id="1",
                                session="s", generation="1")

            def approve_later():
                time.sleep(0.05)
                store.approve(nonce=nonce, feature="local", digest="d", user_id="1",
                              session="s", generation="1")

            thread = threading.Thread(target=approve_later)
            thread.start()
            result = store.consume(nonce=nonce, feature="local", digest="d", user_id="1",
                                   session="s", generation="1", wait_seconds=1)
            thread.join()
            self.assertEqual(result["digest"], "d")
            with self.assertRaises(CapabilityError):
                store.consume(nonce=nonce, feature="local", digest="d", user_id="1",
                              session="s", generation="1", wait_seconds=0.01)

    def test_capability_wait_stops_on_denial(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CapabilityStore(Path(directory) / "state.sqlite3")
            nonce = store.issue(feature="local", digest="d", request="{}", user_id="1",
                                session="s", generation="1")

            def deny_later():
                time.sleep(0.05)
                store.cancel_bound(nonce=nonce, feature="local", digest="d", user_id="1",
                                   session="s", generation="1")

            thread = threading.Thread(target=deny_later)
            thread.start()
            started = time.monotonic()
            with self.assertRaises(CapabilityError):
                store.consume(nonce=nonce, feature="local", digest="d", user_id="1",
                              session="s", generation="1", wait_seconds=1)
            thread.join()
            self.assertLess(time.monotonic() - started, 0.75)

    def test_sandbox_has_no_stack_network_or_host_authority(self):
        request = schema.validate_local({"command": "id"})
        body = build_sandbox_body(request, image="sha256:x", workspace_source="/workspace", egress_network="egress")
        host = body["HostConfig"]
        self.assertEqual(body["User"], "10002:10002")
        self.assertEqual(host["NetworkMode"], "none")
        self.assertEqual(host["CapDrop"], ["ALL"])
        self.assertTrue(host["ReadonlyRootfs"])
        self.assertEqual(host["Binds"], ["/workspace:/workspace:rw"])

    def test_ssh_options_are_pinned(self):
        common = " ".join(ssh.COMMON_OPTIONS)
        for expected in ("StrictHostKeyChecking=yes", "ForwardAgent=no", "ForwardX11=no", "ControlMaster=no", "RequestTTY=no"):
            self.assertIn(expected, common)
        key = {ssh.PUBLICKEY_OPTIONS[index] for index in range(len(ssh.PUBLICKEY_OPTIONS))}
        password = {ssh.PASSWORD_OPTIONS[index] for index in range(len(ssh.PASSWORD_OPTIONS))}
        self.assertIn("PasswordAuthentication=no", key)
        self.assertIn("PubkeyAuthentication=yes", key)
        self.assertIn("PasswordAuthentication=yes", password)
        self.assertIn("PubkeyAuthentication=no", password)
        self.assertIn("NumberOfPasswordPrompts=1", password)
        profile = {"auth": "password", "known_hosts": "/profiles/p/known_hosts",
                   "port": 22, "user": "u", "host": "h"}
        argv = ssh.build_argv(profile, "id")
        self.assertNotIn("-i", argv)
        self.assertNotIn("secret", " ".join(argv))

    def _host_material(self, directory):
        private = Path(directory) / "host"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(private)],
                       check=True)
        public = private.with_suffix(".pub").read_text(encoding="utf-8").split()
        known = Path(directory) / "known_hosts"
        known.write_text(f"host {public[0]} {public[1]}\n", encoding="utf-8")
        completed = subprocess.run(["ssh-keygen", "-lf", str(known), "-E", "sha256"],
                                   check=True, text=True, stdout=subprocess.PIPE)
        return known, completed.stdout.split()[1]

    def test_password_profile_is_hmac_sealed_without_password(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_dir = root / "profile"
            profile_dir.mkdir(mode=0o700)
            known, fingerprint = self._host_material(directory)
            (profile_dir / "known_hosts").write_bytes(known.read_bytes())
            revision = "a" * 64
            metadata = {"version": 2, "auth": "password", "credential_revision": revision,
                        "host": "host", "port": 22, "user": "debian", "authority": "user",
                        "fingerprint": fingerprint}
            (profile_dir / "profile.json").write_text(json.dumps(metadata), encoding="utf-8")
            password = b"correct horse battery staple"
            (profile_dir / "password").write_bytes(password)
            for path in profile_dir.iterdir():
                path.chmod(0o600)
            key = b"k" * 32
            loaded = ssh.load_profile("profile", root, key)
            sealed = ssh.seal_profile(loaded)
            serialized = json.dumps(sealed)
            rendered = approval.render_summary(
                "ssh", {"profile": "profile", "command": "id", "timeout": 30,
                        "sealed_profile": sealed}, loaded,
            )
            environment = ssh._ssh_environment("/tmp/hermes-ssh-password-example")
            self.assertNotIn(password.decode(), serialized)
            self.assertNotIn(password.decode(), rendered)
            self.assertNotIn(password.decode(), " ".join(environment.values()))
            self.assertNotIn(hashlib.sha256(password).hexdigest(), serialized)
            self.assertIn("auth:      password (broker-held)", rendered)
            self.assertEqual(sealed["auth"], "password")
            self.assertEqual(len(sealed["credential_tag"]), 64)
            (profile_dir / "password").write_bytes(b"changed")
            changed = ssh.load_profile("profile", root, key)
            self.assertFalse(ssh.profile_matches(changed, sealed))

    def test_profile_rejects_unsafe_mode_and_host_fingerprint_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_dir = root / "profile"
            profile_dir.mkdir(mode=0o700)
            known, fingerprint = self._host_material(directory)
            (profile_dir / "known_hosts").write_bytes(known.read_bytes())
            metadata = {"version": 2, "auth": "password", "credential_revision": "b" * 64,
                        "host": "host", "port": 22, "user": "debian", "authority": "user",
                        "fingerprint": fingerprint}
            (profile_dir / "profile.json").write_text(json.dumps(metadata), encoding="utf-8")
            (profile_dir / "password").write_bytes(b"password")
            for path in profile_dir.iterdir():
                path.chmod(0o600)
            (profile_dir / "password").chmod(0o644)
            with self.assertRaises(ssh.ProfileError):
                ssh.load_profile("profile", root, b"k" * 32)
            (profile_dir / "password").chmod(0o600)
            metadata["fingerprint"] = "SHA256:" + "A" * 43
            (profile_dir / "profile.json").write_text(json.dumps(metadata), encoding="utf-8")
            (profile_dir / "profile.json").chmod(0o600)
            with self.assertRaises(ssh.ProfileError):
                ssh.load_profile("profile", root, b"k" * 32)

    def test_askpass_outputs_only_private_file_value(self):
        helper = ROOT / "execution-broker" / "ssh-askpass.py"
        self.assertEqual(helper.read_text(encoding="utf-8").splitlines()[0],
                         "#!/usr/local/bin/python3")
        with tempfile.NamedTemporaryFile(prefix="hermes-ssh-password-", dir="/tmp", delete=False) as file:
            file.write(b"test-password")
            path = file.name
        os.chmod(path, 0o600)
        try:
            environment = dict(os.environ, HERMES_SSH_PASSWORD_FILE=path)
            completed = subprocess.run([sys.executable, str(helper), "ignored"], env=environment,
                                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(completed.stdout, b"test-password\n")
            self.assertEqual(completed.stderr, b"")
            self.assertNotIn("test-password", " ".join(completed.args))
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
