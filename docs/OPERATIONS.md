# Operational safety commands

This document covers the P0 operations module introduced for the 0.2 development cycle.

## Health

```bash
./manage.sh health
./manage.sh health --json
./manage.sh health --wait 180
```

The command treats Docker healthchecks as authoritative when a service defines one. A selected service without a Docker healthcheck is considered ready when it is running. `*-init` one-shot services are considered ready when they exit successfully.

## Backups

By default backups are stored next to the checkout in `../hermes-linux-stack-backups/` (derived from the checkout directory name), so restoring `data/` cannot accidentally delete the safety backup. Override this with `HERMES_STACK_BACKUP_DIR` or `--destination`.

```bash
./manage.sh backup
./manage.sh backup --destination /mnt/backups
./manage.sh backup --label before-provider-change
./manage.sh backup --age-recipient age1...
./manage.sh backup-list
```

By default the command briefly pauses running containers while archiving `.env` and `data/`. This avoids copying SQLite database and WAL files while they are actively changing. `--no-pause` is available for operators who explicitly accept live-backup consistency risk.

Every unencrypted backup receives a `.sha256` sidecar. Encrypted backups use `age` and receive a checksum for the encrypted artifact.

## Restore

```bash
./manage.sh restore ../hermes-linux-stack-backups/hermes-stack-YYYYMMDDTHHMMSSZ-manual.tar.gz
```

Restore performs path-safety validation, creates a pre-restore backup, stops services, restores `.env` and `data/`, validates Compose configuration, restarts the stack, and waits for readiness. If readiness fails, it restores the previous local state.

The archived `docker-compose.yml`, `manifest.json`, `images.json`, and optional `stack.lock.json` are retained for diagnostics; restore does not overwrite the currently checked-out Compose file.

## Safe update and rollback

```bash
./manage.sh update --plan
./manage.sh update
./manage.sh rollback
```

Before an update, the manager:

1. validates the current stack;
2. requires current readiness unless `--force` is used;
3. creates a consistent pre-update backup unless `--no-backup` is used;
4. records current image IDs and creates local rollback tags;
5. pulls and recreates selected services;
6. waits for health/readiness;
7. automatically recreates services with the old image set if readiness fails.

Image rollback intentionally does **not** silently roll persistent data backward. The pre-update backup path is recorded in `data/stack-state/releases/<STATE_ID>/update.json`. If an upstream image migration made old binaries incompatible with migrated data, restore that backup explicitly.

## Release image locking

```bash
./manage.sh lock-images
./manage.sh verify-images
```

`lock-images` resolves the image references configured by `.env` to immutable registry digests and writes `stack.lock.json`. This is intended for maintainers after release compatibility testing. It does not silently rewrite a user's `.env`.

A future tagged release can commit its tested `stack.lock.json` and have the installer consume those immutable digests by default.
