# Hermes Operations Center v0.5.4 — rate limits and RAG storage

The operator UI remains at `/control/` for compatibility. Its visible name is **Hermes Operations Center**. Existing `SMART_ROUTER_CONTROL_*` variables remain supported.

## Stack-client quota

```env
SMART_ROUTER_CLIENT_RPM=120
SMART_ROUTER_CLIENT_TPM=2000000
SMART_ROUTER_CLIENT_DAILY_REQUESTS=10000
```

These limits apply to `SMART_ROUTER_CLIENT_API_KEY`, used by trusted stack clients. Long tool sessions resend the full conversation on each request/retry, so 200,000 TPM was too small for ~70k-token prompts.

Virtual API keys keep per-key quotas and can now be edited without rotating the key in **Operations Center → Users & Keys**.

## Same PostgreSQL for Operations + RAG

```env
SMART_ROUTER_CONTROL_DATABASE_URL=postgresql+psycopg://hermes_router:REDACTED@postgres:5432/hermes_router
SMART_ROUTER_KNOWLEDGE_DATABASE_URL=
```

With the knowledge URL empty, knowledge bases/chunks use the same database/engine as users, routes, audit, memory and other Operations data.

## Separate PostgreSQL for RAG

```env
SMART_ROUTER_KNOWLEDGE_DATABASE_URL=postgresql+psycopg://hermes_rag:REDACTED@rag-postgres:5432/hermes_knowledge
```

Only knowledge-base/chunk tables are created in the separate database. The Operations Center reports a redacted DSN and connectivity status.

After changing database configuration:

```bash
docker compose --env-file .env up -d --no-deps --force-recreate smart-router
./manage.sh router-system
```

## Retrieval note

v0.5.4 still uses the built-in lexical retriever. PostgreSQL storage does not automatically mean pgvector/embedding search. A vector connector should be added explicitly in a later release if semantic embedding retrieval is required.
