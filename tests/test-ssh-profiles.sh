#!/usr/bin/env bash
# Exercises both SSH profile authentication modes against a disposable local
# sshd. It never touches a configured profile, host, or credential: every key,
# password, and container here is created and destroyed inside this script.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${EXECUTION_BROKER_TEST_IMAGE:-hermes-execution-broker:sshtest}"
SSHD_IMAGE=hermes-test-sshd:sshtest
NETWORK=hermes-ssh-profile-test
SSHD=hermes-test-sshd
PROFILE_VOLUME=hermes-test-profiles
SECRET_VOLUME=hermes-test-secret
WORK="$(mktemp -d)"
PASSWORD='correct horse battery staple'

cleanup() {
  docker rm -f "$SSHD" >/dev/null 2>&1 || true
  docker volume rm "$PROFILE_VOLUME" "$SECRET_VOLUME" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
  rm -rf -- "$WORK"
}
trap cleanup EXIT

# The broker runs the probe; the loader, askpass transport, and SSH options are
# exactly the ones production uses.
docker build -q -t "$IMAGE" "$ROOT_DIR/execution-broker" >/dev/null

cat > "$WORK/Dockerfile" <<'EOF'
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends openssh-server \
  && rm -rf /var/lib/apt/lists/* && mkdir -p /run/sshd \
  && useradd -m -s /bin/bash tester && ssh-keygen -A
RUN printf 'PasswordAuthentication yes\nPubkeyAuthentication yes\nPermitRootLogin no\nKbdInteractiveAuthentication no\n' \
  > /etc/ssh/sshd_config.d/hermes.conf
CMD ["/usr/sbin/sshd", "-D", "-e"]
EOF
docker build -q -t "$SSHD_IMAGE" "$WORK" >/dev/null

docker network create "$NETWORK" >/dev/null
docker run -d --name "$SSHD" --network "$NETWORK" "$SSHD_IMAGE" >/dev/null
printf 'tester:%s\n' "$PASSWORD" | docker exec -i "$SSHD" chpasswd
for _ in $(seq 30); do
  docker exec "$SSHD" test -s /etc/ssh/ssh_host_ed25519_key.pub && break
  sleep 1
done

docker exec "$SSHD" cat /etc/ssh/ssh_host_ed25519_key.pub > "$WORK/hostkey.pub"
fingerprint="$(ssh-keygen -lf "$WORK/hostkey.pub" -E sha256 | awk '{print $2}')"

mkdir -p "$WORK/profiles/keyprof" "$WORK/profiles/passprof"
printf '%s %s %s\n' "$SSHD" "$(awk '{print $1}' "$WORK/hostkey.pub")" \
  "$(awk '{print $2}' "$WORK/hostkey.pub")" > "$WORK/profiles/keyprof/known_hosts"
cp "$WORK/profiles/keyprof/known_hosts" "$WORK/profiles/passprof/known_hosts"
printf '%s' "$PASSWORD" > "$WORK/profiles/passprof/password"
ssh-keygen -q -t ed25519 -N '' -f "$WORK/profiles/keyprof/identity"
ssh-keygen -y -f "$WORK/profiles/keyprof/identity" > "$WORK/identity.pub"
rm -f "$WORK/profiles/keyprof/identity.pub"
docker exec -u tester -i "$SSHD" sh -c \
  'mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat > ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys' \
  < "$WORK/identity.pub"

python3 - "$WORK/profiles" "$SSHD" "$fingerprint" <<'PY'
import json, secrets, sys
root, host, fingerprint = sys.argv[1:]
base = {"version": 2, "host": host, "port": 22, "user": "tester",
        "authority": "user", "fingerprint": fingerprint}
for name, auth in (("keyprof", "publickey"), ("passprof", "password")):
    value = dict(base, auth=auth, credential_revision=secrets.token_hex(32))
    with open(f"{root}/{name}/profile.json", "w", encoding="utf-8") as handle:
        json.dump(value, handle)
PY
python3 -c 'import secrets; print(secrets.token_hex(32))' > "$WORK/integrity-secret"

# Stage the fixtures with the broker's runtime ownership, exactly as manage.sh does.
docker volume create "$PROFILE_VOLUME" >/dev/null
docker volume create "$SECRET_VOLUME" >/dev/null
docker run --rm --user 0:0 -v "$WORK:/src:ro" -v "$PROFILE_VOLUME:/dst" \
  -v "$SECRET_VOLUME:/sec" --entrypoint sh "$IMAGE" -c \
  'cp -a /src/profiles/. /dst/ && cp /src/integrity-secret /sec/integrity-secret \
   && chown -R 10003:10003 /dst /sec && chmod 700 /dst/* \
   && find /dst -type f -exec chmod 600 {} + && chmod 600 /sec/integrity-secret'

probe() {
  local secret_mount=(-v "$SECRET_VOLUME:/sec:ro"
    -e BROKER_SSH_PROFILE_INTEGRITY_SECRET_FILE=/sec/integrity-secret)
  [[ "${2:-with-secret}" == with-secret ]] || secret_mount=()
  timeout 90 docker run --rm --network "$NETWORK" -v "$PROFILE_VOLUME:/profiles:ro" \
    "${secret_mount[@]}" --read-only \
    --tmpfs /tmp:size=16m,mode=0700,uid=10003,gid=10003 \
    --entrypoint python "$IMAGE" -m broker.ssh --probe "$1"
}

write_password() {
  docker run --rm --user 0:0 -v "$PROFILE_VOLUME:/dst" --entrypoint sh "$IMAGE" -c \
    "printf '%s' \"\$1\" > /dst/passprof/password && chown 10003:10003 /dst/passprof/password \
     && chmod 600 /dst/passprof/password" sh "$1"
}

probe keyprof | grep -q 'uid=1000(tester)'
probe passprof | grep -q 'uid=1000(tester)'

# A wrong password must fail after a single prompt rather than hang or retry.
write_password 'wrong-password'
! probe passprof
write_password "$PASSWORD"

# Password profiles fail closed without the broker-only integrity key; key
# profiles never depended on it.
! probe passprof without-secret
probe keyprof without-secret | grep -q 'uid=1000(tester)'

# Passwords must never reach argv, the sealed request, the result, or /tmp.
timeout 90 docker run --rm --network "$NETWORK" -v "$PROFILE_VOLUME:/profiles:ro" \
  -v "$SECRET_VOLUME:/sec:ro" -e BROKER_SSH_PROFILE_INTEGRITY_SECRET_FILE=/sec/integrity-secret \
  --read-only \
  --tmpfs /tmp:size=16m,mode=0700,uid=10003,gid=10003 \
  --entrypoint python "$IMAGE" -c '
import json, os, subprocess
from broker import ssh
profile = ssh.load_profile("passprof", integrity_key=ssh.read_integrity_secret())
secret = profile["password"].decode("utf-8")
result = ssh.run_ssh({"command": "id", "timeout": 30}, profile)
assert result["returncode"] == 0, result
assert secret not in json.dumps(ssh.seal_profile(profile)) + json.dumps(result)
assert secret not in " ".join(ssh.build_argv(profile, "id"))
assert "-i" not in ssh.build_argv(profile, "id")
timed = ssh.run_ssh({"command": "sleep 30", "timeout": 3}, profile)
assert timed["timed_out"] and timed["returncode"] == 124, timed
assert not [n for n in os.listdir("/tmp") if n.startswith("hermes-ssh-password-")]
# The askpass helper reveals nothing outside its private, owner-only file.
environment = dict(os.environ, HERMES_SSH_PASSWORD_FILE="/etc/hostname")
assert subprocess.run([ssh.ASKPASS_PATH], env=environment, capture_output=True).returncode == 1
open("/tmp/hermes-ssh-password-loose", "w").write("x")
os.chmod("/tmp/hermes-ssh-password-loose", 0o644)
environment["HERMES_SSH_PASSWORD_FILE"] = "/tmp/hermes-ssh-password-loose"
assert subprocess.run([ssh.ASKPASS_PATH], env=environment, capture_output=True).returncode == 1
'

# Profiles predating versioned metadata keep working as public-key profiles.
docker run --rm --user 0:0 -v "$PROFILE_VOLUME:/dst" --entrypoint python "$IMAGE" -c '
import json, os
path = "/dst/keyprof/profile.json"
value = json.load(open(path, encoding="utf-8"))
legacy = {key: value[key] for key in ("host", "port", "user", "authority", "fingerprint")}
open(path, "w", encoding="utf-8").write(json.dumps(legacy))
os.chown(path, 10003, 10003)
os.chmod(path, 0o600)
'
probe keyprof | grep -q 'uid=1000(tester)'

printf 'SSH profile integration test passed.\n'
