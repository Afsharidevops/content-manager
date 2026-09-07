"""Remote execution against locally configured, host-key-pinned profiles."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import stat
import tempfile
import time
from pathlib import Path
from typing import Any

from .schema import MAX_OUTPUT_CHARS

PROFILE_ROOT = Path("/profiles")
INTEGRITY_SECRET_PATH = Path(os.environ.get(
    "BROKER_SSH_PROFILE_INTEGRITY_SECRET_FILE",
    "/run/secrets/execution-ssh-profile-integrity",
))
ASKPASS_PATH = "/usr/local/libexec/hermes-ssh-askpass"
_HOST_RE = re.compile(r"^[A-Za-z0-9._:-]{1,253}$")
_USER_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_FINGERPRINT_RE = re.compile(r"^SHA256:[A-Za-z0-9+/]{20,}={0,2}$")
_REVISION_RE = re.compile(r"^[0-9a-f]{64}$")

COMMON_OPTIONS = (
    "StrictHostKeyChecking=yes",
    "ForwardAgent=no",
    "ForwardX11=no",
    "ForwardX11Trusted=no",
    "PermitLocalCommand=no",
    "ClearAllForwardings=yes",
    "ControlMaster=no",
    "ControlPath=none",
    "RequestTTY=no",
    "GSSAPIAuthentication=no",
    "AddKeysToAgent=no",
    "LogLevel=ERROR",
)
PUBLICKEY_OPTIONS = (
    "BatchMode=yes",
    "IdentitiesOnly=yes",
    "PasswordAuthentication=no",
    "KbdInteractiveAuthentication=no",
    "PubkeyAuthentication=yes",
)
PASSWORD_OPTIONS = (
    "BatchMode=no",
    "PreferredAuthentications=password",
    "PasswordAuthentication=yes",
    "KbdInteractiveAuthentication=no",
    "PubkeyAuthentication=no",
    "NumberOfPasswordPrompts=1",
)
# Compatibility for callers that inspect the original key-only option tuple.
FIXED_OPTIONS = PUBLICKEY_OPTIONS + COMMON_OPTIONS


class ProfileError(ValueError):
    """A profile that is missing, unsafe, or incompletely configured."""


def read_integrity_secret(path: Path = INTEGRITY_SECRET_PATH) -> bytes:
    try:
        value = path.read_bytes().strip()
    except OSError:
        return b""
    return value if len(value) >= 32 else b""


def _safe_read(path: Path, *, maximum: int, allow_empty: bool = False) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode):
                raise ProfileError("An SSH profile file is not regular.")
            if file_stat.st_uid != os.geteuid() or file_stat.st_mode & 0o077:
                raise ProfileError("An SSH profile file has unsafe ownership or permissions.")
            value = os.read(descriptor, maximum + 1)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise ProfileError("An SSH profile file is invalid or unreadable.") from exc
    if len(value) > maximum or (not value and not allow_empty):
        raise ProfileError("An SSH profile file has an invalid size.")
    return value


def _file_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _identity_fingerprint(path: Path) -> str:
    try:
        completed = subprocess.run(
            ["ssh-keygen", "-y", "-f", str(path)], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True,
            text=True, encoding="utf-8", timeout=10,
        )
        public = subprocess.run(
            ["ssh-keygen", "-lf", "-", "-E", "sha256"], input=completed.stdout,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True,
            text=True, encoding="utf-8", timeout=10,
        )
        return public.stdout.split()[1]
    except (OSError, subprocess.SubprocessError, IndexError) as exc:
        raise ProfileError("The SSH identity key is invalid or unreadable.") from exc


def _known_hosts_fingerprint(path: Path) -> str:
    try:
        completed = subprocess.run(
            ["ssh-keygen", "-lf", str(path), "-E", "sha256"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, check=True, text=True,
            encoding="utf-8", timeout=10,
        )
        fingerprints = [line.split()[1] for line in completed.stdout.splitlines()
                        if len(line.split()) > 1]
    except (OSError, subprocess.SubprocessError, IndexError) as exc:
        raise ProfileError("The pinned SSH host key is invalid or unreadable.") from exc
    if len(fingerprints) != 1:
        raise ProfileError("The SSH profile must pin exactly one host key.")
    return fingerprints[0]


def _credential_tag(name: str, revision: str, password: bytes, key: bytes) -> str:
    if len(key) < 32:
        raise ProfileError("The SSH password-profile integrity secret is unavailable.")
    fields = (b"hermes-ssh-password-v1", name.encode(), revision.encode(), password)
    payload = b"".join(len(field).to_bytes(8, "big") + field for field in fields)
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def load_profile(name: str, root: Path = PROFILE_ROOT,
                 integrity_key: bytes | None = None) -> dict[str, Any]:
    if not _NAME_RE.fullmatch(name):
        raise ProfileError("The SSH profile name is invalid.")
    directory = root / name
    if directory.is_symlink() or not directory.is_dir():
        raise ProfileError(f"SSH profile '{name}' is not configured.")
    directory_stat = directory.stat()
    if directory_stat.st_uid != os.geteuid() or directory_stat.st_mode & 0o077:
        raise ProfileError(f"SSH profile '{name}' has unsafe ownership or permissions.")

    meta_path = directory / "profile.json"
    known_hosts = directory / "known_hosts"
    try:
        meta = json.loads(_safe_read(meta_path, maximum=16_384).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProfileError(f"SSH profile '{name}' metadata is invalid.") from exc
    legacy = isinstance(meta, dict) and set(meta) == {
        "host", "port", "user", "authority", "fingerprint"
    }
    expected = {"version", "auth", "credential_revision", "host", "port", "user",
                "authority", "fingerprint"}
    if not legacy and (not isinstance(meta, dict) or set(meta) != expected
                       or meta.get("version") != 2
                       or meta.get("auth") not in ("publickey", "password")
                       or not isinstance(meta.get("credential_revision"), str)
                       or not _REVISION_RE.fullmatch(meta["credential_revision"])):
        raise ProfileError(f"SSH profile '{name}' has unsupported or missing metadata.")
    if legacy:
        meta.update(version=1, auth="publickey", credential_revision="legacy")
    if not isinstance(meta["host"], str) or not _HOST_RE.fullmatch(meta["host"]):
        raise ProfileError(f"SSH profile '{name}' has an invalid host.")
    if (isinstance(meta["port"], bool) or not isinstance(meta["port"], int)
            or not 1 <= meta["port"] <= 65535):
        raise ProfileError(f"SSH profile '{name}' has an invalid port.")
    if not isinstance(meta["user"], str) or not _USER_RE.fullmatch(meta["user"]):
        raise ProfileError(f"SSH profile '{name}' has an invalid user.")
    if meta["authority"] not in ("user", "root", "sudo-nopasswd"):
        raise ProfileError(f"SSH profile '{name}' has an invalid authority label.")
    if (not isinstance(meta["fingerprint"], str)
            or not _FINGERPRINT_RE.fullmatch(meta["fingerprint"])):
        raise ProfileError(f"SSH profile '{name}' has an invalid pinned host fingerprint.")

    known_hosts_bytes = _safe_read(known_hosts, maximum=65_536)
    actual_fingerprint = _known_hosts_fingerprint(known_hosts)
    if not hmac.compare_digest(actual_fingerprint, meta["fingerprint"]):
        raise ProfileError("The stored SSH host fingerprint does not match the pinned key.")
    meta["name"] = name
    meta["known_hosts"] = str(known_hosts)
    meta["known_hosts_sha256"] = _file_sha256(known_hosts_bytes)
    meta["fingerprint"] = actual_fingerprint

    if meta["auth"] == "publickey":
        identity = directory / "identity"
        identity_bytes = _safe_read(identity, maximum=65_536)
        meta["identity"] = str(identity)
        meta["identity_fingerprint"] = _identity_fingerprint(identity)
        meta["identity_sha256"] = _file_sha256(identity_bytes)
    else:
        password = _safe_read(directory / "password", maximum=1_024)
        if b"\x00" in password or b"\r" in password or b"\n" in password:
            raise ProfileError("The SSH password file contains unsupported characters.")
        meta["password"] = password
        meta["credential_tag"] = _credential_tag(
            name, meta["credential_revision"], password, integrity_key or b""
        )
    return meta


def seal_profile(profile: dict[str, Any]) -> dict[str, Any]:
    sealed = {
        "host": profile["host"], "port": profile["port"], "user": profile["user"],
        "authority": profile["authority"], "fingerprint": profile["fingerprint"],
        "auth": profile["auth"], "credential_revision": profile["credential_revision"],
        "known_hosts_sha256": profile["known_hosts_sha256"],
    }
    if profile["auth"] == "publickey":
        sealed.update(identity_fingerprint=profile["identity_fingerprint"],
                      identity_sha256=profile["identity_sha256"])
    else:
        sealed["credential_tag"] = profile["credential_tag"]
    return sealed


def profile_matches(profile: dict[str, Any], sealed: dict[str, Any]) -> bool:
    current = seal_profile(profile)
    return (set(current) == set(sealed)
            and all(hmac.compare_digest(str(current[key]), str(sealed[key]))
                    for key in current))


def list_profiles(root: Path = PROFILE_ROOT,
                  integrity_key: bytes | None = None) -> list[dict[str, Any]]:
    if not root.is_dir():
        return []
    profiles = []
    for entry in sorted(root.iterdir()):
        if entry.is_symlink() or not entry.is_dir():
            continue
        try:
            meta = load_profile(entry.name, root, integrity_key)
        except ProfileError:
            continue
        item = {"name": entry.name, "host": meta["host"], "port": meta["port"],
                "user": meta["user"], "authority": meta["authority"],
                "auth": meta["auth"], "host_fingerprint": meta["fingerprint"]}
        if meta["auth"] == "publickey":
            item["identity_fingerprint"] = meta["identity_fingerprint"]
        profiles.append(item)
    return profiles


def build_argv(profile: dict[str, Any], command: str) -> list[str]:
    argv = ["ssh", "-n", "-T"]
    options = (PUBLICKEY_OPTIONS if profile["auth"] == "publickey"
               else PASSWORD_OPTIONS) + COMMON_OPTIONS
    for option in options:
        argv += ["-o", option]
    argv += ["-o", f"UserKnownHostsFile={profile['known_hosts']}"]
    if profile["auth"] == "publickey":
        argv += ["-i", profile["identity"]]
    argv += ["-p", str(profile["port"]), f"{profile['user']}@{profile['host']}",
             "--", command]
    return argv


def _ssh_environment(password_path: str) -> dict[str, str]:
    return {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
            "SSH_ASKPASS": ASKPASS_PATH, "SSH_ASKPASS_REQUIRE": "force",
            "DISPLAY": "hermes-askpass:0", "HERMES_SSH_PASSWORD_FILE": password_path}


def run_ssh(request: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    argv = build_argv(profile, request["command"])
    started = time.monotonic()
    timed_out = False
    password_path = ""
    environment = None
    try:
        if profile["auth"] == "password":
            descriptor, password_path = tempfile.mkstemp(prefix="hermes-ssh-password-", dir="/tmp")
            try:
                os.fchmod(descriptor, 0o600)
                os.write(descriptor, profile["password"])
            finally:
                os.close(descriptor)
            environment = _ssh_environment(password_path)
        completed = subprocess.run(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            timeout=request["timeout"], shell=False, check=False, env=environment,
            start_new_session=True,
        )
        returncode = completed.returncode
        output = completed.stdout or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = 124
        output = (exc.output or "") if isinstance(exc.output, str) else ""
    except OSError as exc:
        returncode = -1
        output = f"SSH failed to start: {type(exc).__name__}"
    finally:
        if password_path:
            try:
                os.unlink(password_path)
            except OSError:
                pass

    return {"returncode": returncode, "output": output[-MAX_OUTPUT_CHARS:],
            "truncated": len(output) > MAX_OUTPUT_CHARS, "timed_out": timed_out,
            "duration": round(time.monotonic() - started, 3)}


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] != "--probe" or not _NAME_RE.fullmatch(sys.argv[2]):
        raise SystemExit("Usage: python -m broker.ssh --probe PROFILE")
    try:
        profile = load_profile(sys.argv[2], integrity_key=read_integrity_secret())
        command = "sudo -n true && id" if profile["authority"] == "sudo-nopasswd" else "id"
        result = run_ssh({"command": command, "timeout": 30}, profile)
    except ProfileError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    if result["output"]:
        print(result["output"], end="" if result["output"].endswith("\n") else "\n")
    raise SystemExit(result["returncode"])


if __name__ == "__main__":
    main()
