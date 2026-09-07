# Security notes — Smart Router v0.5.9

Keep `.env`, `data/hermes/.env`, gateway state, Smart Router databases, observations, n8n credentials, generated backups, and execution secrets out of version control.

Smart Router v0.5.9 keeps deterministic capability floors authoritative. Health scoring, calibrated/adaptive signals, provider quality, and fallbacks cannot intentionally bypass tool, vision, context, policy, ACL, budget, or execution-approval constraints.

## Secure defaults in this package

- Smart Router client authentication and control-plane authentication default to enabled.
- Open WebUI signup defaults to disabled.
- Public host bindings remain loopback-first unless the operator explicitly changes them.
- Installer-generated secrets replace blank/`CHANGE_ME*` placeholders and are written to `.env` with restrictive permissions.
- Supported `*_FILE` variables allow secret-file/Docker/Kubernetes secret mounting.
- Control-plane status redacts database credentials and never returns configured secret values.
- Execution profiles remain disabled until explicitly enabled; Docker execution verifies the host Docker socket GID instead of assuming a fixed group.
- Execution brokers retain read-only/rootfs/capability restrictions from the existing security architecture.

Mutable application tags (`latest`/`main`) are retained by operator request. For stronger supply-chain reproducibility, pin `*_IMAGE_TAG` values in `.env`, run `./manage.sh lock-images`, commit only the non-secret lock metadata if appropriate, and verify with `./manage.sh verify-images` before an upgrade.

OIDC, ACLs, Redis/PostgreSQL HA, RAG ingestion, plugin endpoints, and execution paths expand the attack surface. Enable only the components you need, terminate public traffic behind authenticated TLS, apply firewall/NetworkPolicy restrictions, and keep the security CI workflow passing. See `docs/HERMES-SMART-ROUTER-v0.5.2-IMPLEMENTATION-STATUS.md` for features that are not yet claimed production-complete.


## Execution Admin private ingress

Execution Admin is intentionally dual-homed when remote private-browser access is enabled: it remains attached to the internal `execution-control-net` for broker/admin control traffic and additionally joins `execution-admin-ingress-net`, a dedicated bridge used only for the explicitly bound private host port. The other execution brokers and Smart Router must not join this ingress network. Never make `execution-control-net` non-internal as a workaround for port publication.
