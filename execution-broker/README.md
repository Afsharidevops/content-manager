# Hermes Stack Execution Broker

Security boundary image for the optional execution tools in [Hermes Linux Stack](https://github.com/Afsharidevops/hermes-linux-stack). It is not a general remote shell or Docker API proxy.

## Purpose and security model

The image runs one of four narrowly scoped modes. Hermes can prepare structured operations through an authenticated broker, but cannot independently approve or execute them. A separate approver recomputes the canonical digest and exact human-readable summary, sends that summary through a dedicated Telegram bot to an authorized numeric user, and signs a one-time decision. Brokers persist the exact grant and atomically consume it once. The independent signed Telegram decision is the broker's trust root; Hermes's native manual prompt is defense in depth and is not treated as an independent security boundary.

Authority is separated by Compose mounts:

- Hermes receives only the broker control secret and execution-user policy.
- Docker mode alone receives the Docker socket and the approver public verification key.
- SSH mode alone receives read-only SSH profiles, the SSH password-profile integrity secret, and the approver public verification key.
- Approver mode alone receives the Telegram approval-bot token and private signing key.

The shared request-authentication secret lets brokers submit sealed requests to the approver. It cannot forge a signed approval. Broker ports are not published.

## Modes

- `docker`: local sandbox and structured Docker operations. Local execution uses a digest-pinned image, sealed workspace generation, non-root identity, read-only root, dropped capabilities, and no network by default.
- `ssh`: commands through locally managed profiles with sealed host, user, authority, authentication type, and known-hosts digest/fingerprint, plus a private-key digest/public fingerprint for public-key profiles or a keyed credential tag for password profiles.
- `approver`: Telegram long polling, persistent one-time inline approval/denial, and Ed25519 decision signing. It has no Docker socket, SSH profiles, or broker capability database.
- `admin` (v0.1.3): optional configuration boundary for the Operations Center. It can change the live feature policy, numeric approver allowlist, write-only dedicated bot token, and broker control secret. It does **not** mount the Ed25519 signing key, Docker socket, or SSH private credentials.

All modes use Python standard-library HTTP. The image also contains OpenSSH client for SSH mode and OpenSSL for approval signing/verification.

## Use through the stack

Use this image through the repository's `docker-compose.yml` and `manage.sh`; do not start it as a standalone privileged container. The stack creates mode-`0700` state directories and mode-`0600` secret files, keeps execution disabled until approval configuration is complete, and applies read-only filesystems, dropped capabilities, `no-new-privileges`, bounded PIDs, private control networking, and mode-specific egress.

Configure the approval bot without placing its token in argv:

```text
# from the repository root
./manage.sh set-execution-approval-bot-token
```

Then configure execution users and explicitly enable only required features. See the stack README and SECURITY policy for the complete lifecycle.

## Required configuration

Compose supplies these mode-specific settings. Paths are descriptive; secret values must never be put in image arguments, environment examples, logs, or public Compose files.

| Mode | Required environment | Required read-only authority mounts | Writable state |
|---|---|---|---|
| docker | `BROKER_MODE=docker`, feature/policy/workspace settings, approver and callback URLs | control secret, approval-request secret, approval public key, Docker socket | capability state |
| ssh | `BROKER_MODE=ssh`, feature/policy settings, approver and callback URLs | control secret, approval-request secret, approval public key, SSH profiles, SSH password-profile integrity secret | capability state |
| approver | `BROKER_MODE=approver`, policy generation, bot/users paths, broker callback URLs | approval-request secret, approval private key, approval bot token, numeric users policy | approval state |
| admin | `BROKER_MODE=admin`, admin bind/CORS settings and live policy paths | execution-admin key, allowed-user metadata, Hermes-bot token hash marker, SSH profile metadata | admin audit state; live feature/users/token/control-secret policy files |

Do not mount the private signing key or approval bot token into Hermes or either broker. Do not mount the Docker socket outside Docker mode, or SSH profiles and the SSH password-profile integrity secret outside SSH mode. Do not publish broker/approver ports or attach brokers to the general stack network.

An SSH profile authenticates with a dedicated key or a locally configured password. A password reaches OpenSSH only through the image-owned askpass helper at `/usr/local/libexec/hermes-ssh-askpass`, which reads one mode-`0600` file on the broker's private `tmpfs`; `sshpass` is not installed and no password is ever placed in argv, an environment value, an approval summary, or a log. The sealed request binds an HMAC-SHA256 credential tag keyed by the integrity secret, so the approver can detect a changed credential without being able to test password guesses. Password mode disables public-key and keyboard-interactive authentication and permits a single prompt.

There are deliberately no safe standalone defaults for execution authority: empty/missing secrets, keys, users, token, image seal, workspace seal, or feature policy make readiness and operations fail closed.

## Tags and platforms

Use the versioned tag `afsharidevops/hermes-execution-broker:0.1.3` or its immutable manifest digest. `latest` is unsuitable for a security boundary because it is mutable. Version `0.1.3` improves the Hermes v0.5.8 Execution Admin browser boundary and dynamic feature/policy-generation files while retaining the one-time signed approval model. Publish it for both Linux `amd64` and `arm64`, then pin the manifest digest for production. Confirm the tag's manifest before deployment. Source, Compose wiring, operational commands, and security documentation live in the Hermes Linux Stack repository linked above.


## v0.1.3 / Hermes v0.5.8 Execution Admin

Enable the optional host-published admin endpoint with `./manage.sh enable-execution-admin`. It binds to `127.0.0.1:8752` by default and requires a separate high-entropy admin key. Hermes Operations Center connects to it **directly from the operator browser**; the Smart Router backend never receives the Execution Admin key or Telegram bot token.

Supported operations are deliberately narrow:

- inspect approver/Docker/SSH broker health;
- enable/disable the live `local`, `docker`, and `ssh` execution policy for already-deployed brokers;
- manage numeric execution approver IDs, restricted to the Hermes `TELEGRAM_ALLOWED_USERS` set;
- replace the dedicated execution Telegram bot token without readback;
- rotate the broker control secret;
- list SSH profile names/authentication type without exposing keys/passwords;
- view an execution-admin audit trail.

The admin service has **no** approval signing key, Docker socket, or SSH credential mount. First-time deployment of Docker/SSH broker containers and SSH credential creation/removal remain `manage.sh` host operations. For remote browser access, bind the admin port only to a trusted private address and configure exact `EXECUTION_ADMIN_ALLOWED_ORIGINS`; never use wildcard CORS or expose port 8752 directly to the public internet.
