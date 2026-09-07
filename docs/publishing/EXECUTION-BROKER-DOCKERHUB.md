# Hermes Execution Broker 0.1.3

Hermes Execution Broker is the security-separated execution companion for Hermes Linux Stack. Separate modes isolate local/Docker/SSH execution, Telegram approval signing, and browser-facing execution administration.

## Image

```text
afsharidevops/hermes-execution-broker:0.1.3
```

Recommended platforms after publication: `linux/amd64`, `linux/arm64`.

## Modes

- `docker` — Docker execution authority with approval verification; no signing private key or Telegram bot token.
- `ssh` — configured SSH profile authority with approval verification; no Docker socket or signing private key.
- `approver` — Telegram approval bot plus Ed25519 signing private key; no Docker socket or SSH private credentials.
- `admin` — narrow policy/configuration boundary; no signing private key, Docker socket, SSH private credentials, or Smart Router admin key.

## 0.1.3 change

The browser-facing admin mode improves compatibility for Hermes v0.5.8 by responding to Private Network Access preflight only for an exact allowed origin. Operators should configure a private bind and exact Operations Center origin using the stack management helper rather than exposing the admin service publicly.

## Production guidance

Do not expose Execution Admin directly to the public Internet. Prefer private administration networks or TLS/reverse proxy protection, exact CORS allowlists, version/digest pinning, and regular validation of container mounts so trust boundaries remain separated.
