# Content layer

Deterministic, offline building blocks and editorial configuration for the
Content Manager daily content-production stack. The surrounding repository is
a fork of the Hermes Linux Stack platform; this directory adds the
content-production intelligence that n8n orchestration and the Hermes
editorial step drive.

The rules in `config/` are the owner-editable source of truth. All checks run
before any model call and are pure functions of an item and the policy: URL
and title normalization, deduplication, and filtering (freshness, blocked
domains, low-value marketing titles, and topic blocklists). Persian post copy
is produced at runtime by the editorial step; nothing Persian is stored in
this repository.

## Layout

```text
content/
  pyproject.toml            # Python package (pip-installable, dev extra with pytest)
  config/
    editorial-policy.yaml   # cadence, limits, freshness, scoring, blocklists
    categories.yaml         # the nine enabled content categories
  content_pipeline/         # deterministic building blocks
    config.py               # YAML loading merged over code defaults
    normalize.py            # canonical URL + normalized title + content hash
    dedupe.py               # exact-URL and near-title deduplication
    filter.py               # freshness/domain/low-value/topic filtering
  tests/                    # offline unit tests (no network, no secrets)
```

## Quick start

From this `content/` directory:

```bash
python3 -m venv .venv            # or reuse the repository-root .venv
. .venv/bin/activate
pip install -e ".[dev]"
pytest
```

The test suite is fully offline and must stay that way.
