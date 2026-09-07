import json
import os
import sqlite3
import time

database = "/app/backend/data/webui.db"
api_key = os.environ.get("OPENAI_API_KEY", "")
base_url = os.environ.get("OPENAI_API_BASE_URL", "")

if not api_key or not base_url:
    raise SystemExit("Open WebUI API URL/key environment is missing")

connection = sqlite3.connect(database, timeout=30)
try:
    now = int(time.time() * 1000)
    values = {
        "openai.enable": "true",
        "openai.api_keys": json.dumps([api_key]),
        "openai.api_base_urls": json.dumps([base_url]),
    }
    with connection:
        for key, value in values.items():
            connection.execute(
                """
                INSERT INTO config(key, value, updated_at) VALUES(?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, value, now),
            )
finally:
    connection.close()

print("Open WebUI persisted 9router connection synchronized")
