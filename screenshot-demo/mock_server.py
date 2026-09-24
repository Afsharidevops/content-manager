#!/usr/bin/env python3
"""Mock API server for Panel screenshot capture.

Serves the real panel/static SPA while returning realistic fake JSON
for every API endpoint.  No Docker, no production data, no real secrets.
"""

from __future__ import annotations
import argparse, http.server, json, logging, mimetypes, os, pathlib, time
from http import HTTPStatus

HERE = pathlib.Path(__file__).resolve().parent
STATIC = HERE.parent / "panel" / "static"
VERSION = "0.6.0"
log = logging.getLogger("mock-panel")

# ── helpers ──────────────────────────────────────────────────────────
def json_resp(data: dict, status=HTTPStatus.OK):
    body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
    return body, status

def html_resp(text: str, status=HTTPStatus.OK):
    body = text.encode("utf-8")
    return body, status

# ── fake data ────────────────────────────────────────────────────────
_now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())

# Realistic services that look like a healthy demo stack
MOCK_SERVICES = [
    {"service": "9router", "name": "content-9router-1", "state": "running", "status": "Up 3 hours", "health": "", "image": "decolua/9router:latest", "ports": ["20128"], "running_for": "3 hours"},
    {"service": "content-bot", "name": "content-content-bot-1", "state": "running", "status": "Up 3 hours", "health": "", "image": "afsharidevops/content-bot:0.4.3", "ports": [], "running_for": "3 hours"},
    {"service": "hermes-agent", "name": "content-hermes-agent-1", "state": "running", "status": "Up 3 hours", "health": "", "image": "nousresearch/hermes-agent:latest", "ports": ["8642", "9119"], "running_for": "3 hours"},
    {"service": "media-studio", "name": "content-media-studio-1", "state": "running", "status": "Up 3 hours", "health": "healthy", "image": "afsharidevops/media-studio:0.5.1", "ports": [], "running_for": "3 hours"},
    {"service": "rustfs", "name": "content-rustfs-1", "state": "running", "status": "Up 3 hours", "health": "", "image": "rustfs/rustfs:latest", "ports": ["9000"], "running_for": "3 hours"},
    {"service": "smart-router", "name": "content-smart-router-1", "state": "running", "status": "Up 3 hours", "health": "healthy", "image": "afsharidevops/hermes-smart-router:0.6.4", "ports": ["8787"], "running_for": "3 hours"},
    {"service": "open-webui", "name": "content-open-webui-1", "state": "running", "status": "Up 3 hours", "health": "", "image": "ghcr.io/open-webui/open-webui:main", "ports": ["3000"], "running_for": "3 hours"},
]

MOCK_PROFILES = ["content", "panel", "media"]

MOCK_IMAGE_TAGS = {
    "CONTENT_BOT_IMAGE_TAG": "0.4.3",
    "MEDIA_STUDIO_IMAGE_TAG": "0.5.1",
    "PANEL_IMAGE_TAG": "0.6.0",
    "SMART_ROUTER_IMAGE_TAG": "0.6.4",
    "NOTEBOOKLM_IMAGE_TAG": "0.1.0",
    "HERMES_IMAGE_TAG": "latest",
}

MOCK_DISK = {
    "filesystem": {"total": "120 GiB", "used": "42 GiB", "free": "78 GiB", "percent": 35},
    "data": [
        {"name": "config", "size": "128 KiB"},
        {"name": "content-bot", "size": "2.3 MiB"},
        {"name": "media-studio", "size": "1.2 GiB"},
        {"name": "rustfs", "size": "4.5 GiB"},
        {"name": "smart-router", "size": "86 MiB"},
        {"name": "hermes", "size": "34 MiB"},
        {"name": "open-webui", "size": "18 MiB"},
        {"name": "9router", "size": "2 MiB"},
    ],
}

MOCK_EXPOSURE = {
    "rows": [
        {"service": "9router", "key": "NINEROUTER_BIND_IP", "bind": "127.0.0.1:20128", "loopback": True, "ports": ["20128"]},
        {"service": "content-bot", "key": "CONTENT_BOT_*", "bind": "none (polling)", "loopback": True, "ports": []},
        {"service": "hermes-agent", "key": "HERMES_API_PORT", "bind": "127.0.0.1:8642", "loopback": True, "ports": ["8642", "9119"]},
        {"service": "media-studio", "key": "MEDIA_STUDIO_BIND_IP", "bind": "127.0.0.1:8850", "loopback": True, "ports": []},
        {"service": "rustfs", "key": "RUSTFS_BIND_IP", "bind": "127.0.0.1:9000", "loopback": True, "ports": ["9000"]},
        {"service": "smart-router", "key": "SMART_ROUTER_BIND_IP", "bind": "127.0.0.1:8787", "loopback": True, "ports": ["8787"]},
        {"service": "open-webui", "key": "OPENWEBUI_BIND_IP", "bind": "127.0.0.1:3000", "loopback": True, "ports": ["3000"]},
    ],
    "warnings": [],
}

MOCK_LINKS = [
    {"label": "Operator panel", "url": "/", "note": "This console; the operator token is required to sign in."},
    {"label": "Smart Router dashboard", "url": "http://127.0.0.1:8787/dashboard", "note": "Flight deck: routing policies, telemetry, and traces."},
    {"label": "9router dashboard", "url": "http://127.0.0.1:20128", "note": "Provider keys and upstream models."},
    {"label": "Media Studio API", "url": "http://127.0.0.1:8850/openapi.json", "note": "Job API description (JSON)."},
    {"label": "RustFS console", "url": "http://127.0.0.1:9001/rustfs/console/", "note": "Object-storage console; the S3 API listens on port 9000."},
]

MOCK_INSTAGRAM = {
    "configured": True,
    "token_set": True,
    "business_id": "17841400000000000",
    "api_version": "v26.0",
    "auto_publish": True,
    "public_base_url": "https://media.example.com",
    "refreshed_at": "2026-09-23T12:00:00",
    "expires_at": "2027-03-23T12:00:00",
    "refresh_source": "bot",
    "last_error": "",
    "token_file": "data/content-bot/instagram-token.json",
}

MOCK_DRAFTS = {
    "counters": {
        "day": "2026-09-24",
        "published_today": 3,
        "published_total": 47,
        "drafts_total": 12,
        "daily_last_run": "2026-09-24T09:00:00",
    },
    "routine_last_run": {
        "morning": "2026-09-24T09:00",
        "afternoon": "2026-09-23T14:00",
        "evening": "2026-09-23T20:00",
    },
    "routines": [
        {"id": "morning", "platform": "telegram", "cadence": "daily", "time": "09:00", "count": 2, "media": "auto", "enabled": True, "weekday": None},
        {"id": "afternoon", "platform": "telegram", "cadence": "daily", "time": "14:00", "count": 1, "media": "image", "enabled": True, "weekday": None},
        {"id": "evening", "platform": "telegram", "cadence": "daily", "time": "20:00", "count": 1, "media": "auto", "enabled": True, "weekday": None},
        {"id": "weekly-digest", "platform": "telegram", "cadence": "weekly", "time": "10:00", "count": 3, "media": "auto", "enabled": True, "weekday": "friday"},
    ],
    "exists": True,
    "path": "data/content-bot/state.json",
    "drafts": [
        {"id": "d-001", "kind": "link", "title": "New Kubernetes release improves workload scheduling", "status": "published", "category": "kubernetes", "created_at": "2026-09-24T08:30:00", "media": "image"},
        {"id": "d-002", "kind": "link", "title": "Open-source AI router adds multi-provider failover", "status": "published", "category": "ai", "created_at": "2026-09-24T07:15:00", "media": "video"},
        {"id": "d-003", "kind": "topic", "title": "Platform teams adopt policy-driven deployment workflows", "status": "published", "category": "platform-engineering", "created_at": "2026-09-23T20:30:00", "media": ""},
        {"id": "d-004", "kind": "link", "title": "New observability tools simplify distributed tracing", "status": "awaiting_approval", "category": "observability", "created_at": "2026-09-24T08:45:00", "media": "image"},
        {"id": "d-005", "kind": "link", "title": "Container security: runtime detection moves left", "status": "awaiting_approval", "category": "security", "created_at": "2026-09-24T08:20:00", "media": ""},
        {"id": "d-006", "kind": "topic", "title": "Why AI agents need deterministic execution sandboxes", "status": "text", "category": "ai-agents", "created_at": "2026-09-24T07:50:00", "media": ""},
        {"id": "d-007", "kind": "link", "title": "How we scaled our content pipeline to 100+ daily posts", "status": "media_ask", "category": "engineering", "created_at": "2026-09-24T06:30:00", "media": "image"},
        {"id": "d-008", "kind": "link", "title": "Service mesh observability with OpenTelemetry", "status": "drafting", "category": "observability", "created_at": "2026-09-23T22:00:00", "media": ""},
        {"id": "d-009", "kind": "topic", "title": "Building a self-hosted AI gateway with Smart Router", "status": "scored", "category": "ai", "created_at": "2026-09-23T18:00:00", "media": ""},
        {"id": "d-010", "kind": "link", "title": "Kubernetes cost optimization at scale", "status": "media_ready", "category": "kubernetes", "created_at": "2026-09-23T16:00:00", "media": "video"},
        {"id": "d-011", "kind": "link", "title": "Zero-trust networking for container workloads", "status": "rejected", "category": "security", "created_at": "2026-09-23T12:00:00", "media": ""},
        {"id": "d-012", "kind": "link", "title": "GitOps workflows for platform engineering teams", "status": "failed", "category": "platform-engineering", "created_at": "2026-09-23T10:00:00", "media": ""},
    ],
    "results": [
        {"finished_at": "2026-09-24T08:30:05", "action": "publish", "draft_id": "d-001", "ok": True, "message": "ok"},
        {"finished_at": "2026-09-24T07:15:10", "action": "publish", "draft_id": "d-002", "ok": True, "message": "ok"},
        {"finished_at": "2026-09-23T20:30:00", "action": "publish", "draft_id": "d-003", "ok": True, "message": "ok"},
    ],
}

MOCK_PLATFORMS = {
    "platforms": [
        {"key": "telegram", "label": "Telegram", "mode": "auto", "state": "ready", "summary": "Bot token and channel for the primary publishing target.", "docs": "docs/CONTENT-PRODUCTION-GUIDE.md", "fields": [
            {"key": "CONTENT_BOT_TOKEN", "label": "Bot token", "required": True, "secret": True, "set": True, "placeholder": "", "help": "From BotFather."},
            {"key": "CONTENT_TELEGRAM_CHANNEL", "label": "Channel", "required": True, "secret": False, "value": "@locallab", "placeholder": "@username or id", "help": "Bot must be admin."},
            {"key": "CONTENT_TELEGRAM_USERS", "label": "Operator IDs", "required": True, "secret": False, "value": "12345678", "placeholder": "", "help": "Numeric user IDs allowed to approve."},
        ]},
        {"key": "instagram", "label": "Instagram", "mode": "package", "state": "ready", "summary": "Meta Graph API publishing for photos, carousels, and video.", "docs": "docs/INSTAGRAM-SETUP.md", "fields": [
            {"key": "INSTAGRAM_BUSINESS_ID", "label": "Business ID", "required": True, "secret": False, "value": "17841400000000000", "placeholder": "", "help": "From Meta Business Suite."},
            {"key": "INSTAGRAM_ACCESS_TOKEN", "label": "Long-lived token", "required": True, "secret": True, "set": True, "placeholder": "", "help": "60-day token from the Graph API."},
        ]},
        {"key": "bale", "label": "Bale", "mode": "auto", "state": "configured", "summary": "Bot token and channel for Bale messaging.", "docs": "docs/BALE-EITAA-SETUP.md", "fields": [
            {"key": "CONTENT_BALE_TOKEN", "label": "Bale bot token", "required": True, "secret": True, "set": True, "placeholder": "", "help": "From Bale BotFather."},
            {"key": "CONTENT_BALE_CHANNEL", "label": "Channel id", "required": True, "secret": False, "value": "-1001234567890", "placeholder": "", "help": "Numeric channel id."},
        ]},
        {"key": "eitaa", "label": "Eitaa", "mode": "auto", "state": "configured", "summary": "Bot token and channel for Eitaa messaging.", "docs": "docs/BALE-EITAA-SETUP.md", "fields": [
            {"key": "CONTENT_EITAA_TOKEN", "label": "Eitaa bot token", "required": True, "secret": True, "set": True, "placeholder": "", "help": "From Eitaa BotFather."},
            {"key": "CONTENT_EITAA_CHANNEL", "label": "Channel id", "required": True, "secret": False, "value": "-1009876543210", "placeholder": "", "help": "Numeric channel id."},
        ]},
        {"key": "linkedin", "label": "LinkedIn", "mode": "auto", "state": "ready", "summary": "Publish to a personal profile or company page.", "docs": "docs/LINKEDIN-SETUP.md", "fields": [
            {"key": "LINKEDIN_CLIENT_ID", "label": "Client ID", "required": True, "secret": False, "value": "linkedin-client-id", "placeholder": "", "help": "From LinkedIn Developer Portal."},
            {"key": "LINKEDIN_CLIENT_SECRET", "label": "Client secret", "required": True, "secret": True, "set": True, "placeholder": "", "help": "From LinkedIn Developer Portal."},
        ]},
        {"key": "aparat", "label": "Aparat", "mode": "auto", "state": "ready", "summary": "Video upload to Aparat.", "docs": "docs/APARAT-SETUP.md", "fields": [
            {"key": "CONTENT_APARAT_TOKEN", "label": "Session token", "required": False, "secret": True, "set": True, "placeholder": "", "help": "Sign in to aparat.com."},
            {"key": "CONTENT_APARAT_CATEGORY", "label": "Category id", "required": False, "secret": False, "value": "10", "placeholder": "", "help": "10 = technology."},
        ]},
        {"key": "writer", "label": "AI writer", "mode": "service", "state": "ready", "summary": "OpenAI-compatible endpoint for draft generation.", "docs": "", "fields": [
            {"key": "CONTENT_WRITER_BASE_URL", "label": "Base URL", "required": True, "secret": False, "value": "http://smart-router:8787/v1", "placeholder": "", "help": "Writer API endpoint."},
            {"key": "CONTENT_WRITER_MODEL", "label": "Model", "required": True, "secret": False, "value": "gpt-4o", "placeholder": "", "help": "Model or router alias."},
        ]},
        {"key": "media", "label": "Media Studio", "mode": "service", "state": "ready", "summary": "Image and video generation service.", "docs": "docs/MEDIA-STUDIO.md", "fields": [
            {"key": "CONTENT_MEDIA_STUDIO_URL", "label": "Base URL", "required": True, "secret": False, "value": "http://media-studio:8850", "placeholder": "", "help": "Service address."},
            {"key": "CONTENT_MEDIA_STUDIO_TOKEN", "label": "API token", "required": False, "secret": True, "set": True, "placeholder": "", "help": "Bearer token."},
        ]},
    ],
}

MOCK_CONFIG = {
    "files": [
        {"name": "editorial-policy", "title": "Editorial policy", "exists": True, "can_seed": True, "backup_count": 6},
        {"name": "sources", "title": "Sources (RSS/Atom)", "exists": True, "can_seed": True, "backup_count": 4},
        {"name": "categories", "title": "Categories", "exists": True, "can_seed": True, "backup_count": 3},
        {"name": "tools", "title": "External tools (MCP)", "exists": True, "can_seed": True, "backup_count": 2},
    ],
}

MOCK_CONFIG_POLICY = """pipeline:
  timezone: Asia/Tehran
  daily_proposal_time: "09:00"
  daily_max_proposals: 5
  duplicate_window_days: 7
  category_streak_max: 2

routines:
  - id: morning
    platform: telegram
    cadence: daily
    time: "09:00"
    count: 2
    media: auto
  - id: afternoon
    platform: telegram
    cadence: daily
    time: "14:00"
    count: 1
    media: image
  - id: evening
    platform: telegram
    cadence: daily
    time: "20:00"
    count: 1
    media: auto
  - id: weekly-digest
    platform: telegram
    cadence: weekly
    time: "10:00"
    count: 3
    media: auto
    weekday: friday

scoring:
  min_score: 0
  max_score: 100
  weights:
    freshness: 0.3
    source_authority: 0.25
    category_relevance: 0.25
    content_quality: 0.2
"""

MOCK_ENV_VARS = {
    "COMPOSE_PROFILES": "9router,smart-router,hermes,content,panel,ig-media,rustfs",
    "CONTENT_BOT_TOKEN": "***",
    "CONTENT_TELEGRAM_CHANNEL": "@locallab",
    "CONTENT_TELEGRAM_USERS": "12345678",
    "CONTENT_WRITER_BASE_URL": "http://smart-router:8787/v1",
    "CONTENT_WRITER_MODEL": "gpt-4o",
    "CONTENT_SCHEDULER_ENABLED": "true",
    "CONTENT_PLATFORMS_ENABLED": "true",
    "CONTENT_MEDIA_STUDIO_URL": "http://media-studio:8850",
    "CONTENT_MEDIA_STUDIO_TOKEN": "***",
    "CONTENT_MEDIA_IMAGE_DRIVER": "api-image",
    "CONTENT_MEDIA_VIDEO_DRIVER": "flow-video",
    "CONTENT_MEDIA_VIDEO_EDIT_DRIVER": "video-edit",
    "CONTENT_SEARCH_ENABLED": "true",
    "CONTENT_TOPIC_DRAFTS_ENABLED": "true",
    "CONTENT_DAILY_PROPOSAL_LIMIT": "5",
    "NINEROUTER_BIND_IP": "127.0.0.1",
    "NINEROUTER_PORT": "20128",
    "SMART_ROUTER_BIND_IP": "127.0.0.1",
    "SMART_ROUTER_PORT": "8787",
    "HERMES_BIND_IP": "127.0.0.1",
    "HERMES_API_PORT": "8642",
    "HERMES_DASHBOARD_PORT": "9119",
    "MEDIA_STUDIO_BIND_IP": "127.0.0.1",
    "MEDIA_STUDIO_PORT": "8850",
    "PANEL_BIND_IP": "127.0.0.1",
    "PANEL_PORT": "8899",
    "RUSTFS_BIND_IP": "127.0.0.1",
    "S3_STORAGE_BACKEND": "rustfs",
    "S3_BUCKET": "content-stack",
    "S3_REGION": "auto",
    "N8N_TIMEZONE": "Asia/Tehran",
}

MOCK_STORAGE = {
    "backend": "rustfs",
    "endpoint": "http://rustfs:9000",
    "host_endpoint": "http://127.0.0.1:9000",
    "bucket": "content-stack",
    "region": "auto",
    "key_prefix": "",
    "force_path_style": True,
    "public_base_url": "http://127.0.0.1:9000",
    "public_console_url": "http://127.0.0.1:9001/rustfs/console/",
    "openwebui_storage_provider": "local",
    "rustfs": {
        "service": {"state": "running", "health": ""},
        "api_url": "http://rustfs:9000",
        "console_bind": "127.0.0.1:9001",
        "console_url": "http://127.0.0.1:9001/rustfs/console/",
    },
    "consumers": [
        {"service": "open-webui", "mode": "local", "note": "Local volumes; set OPENWEBUI_STORAGE_PROVIDER=s3 to use the bucket."},
        {"service": "content-bot", "mode": "local", "note": "Drafts, media, and Instagram state stay in data/content-bot."},
        {"service": "media-studio", "mode": "local", "note": "Jobs and generated media stay in data/media-studio."},
        {"service": "n8n", "mode": "local", "note": "External binary storage requires n8n Enterprise."},
    ],
    "warnings": [],
    "guide": "docs/S3-STORAGE.md",
}

MOCK_BACKUPS = {
    "ok": True,
    "error": "",
    "directory": "/home/lab/content-manager-backups",
    "exists": True,
    "entries": [
        {"name": "hermes-stack-2026-09-24-08-00-00.tar.gz", "path": "...", "size": "2.3 GiB", "size_bytes": 2469600000, "modified": "2026-09-24T08:00:00", "created_at": "2026-09-24T08:00:00", "full": True, "sections": ["env", "content", "config", "panel", "media-studio", "smart-router"], "stack_version": "0.5.9", "encrypted": False},
        {"name": "hermes-stack-2026-09-23-20-00-00.tar.gz", "path": "...", "size": "2.2 GiB", "size_bytes": 2362800000, "modified": "2026-09-23T20:00:00", "created_at": "2026-09-23T20:00:00", "full": True, "sections": ["env", "content", "config", "panel", "media-studio", "smart-router"], "stack_version": "0.5.9", "encrypted": False},
        {"name": "hermes-stack-2026-09-23-08-00-00.tar.gz", "path": "...", "size": "2.1 GiB", "size_bytes": 2256000000, "modified": "2026-09-23T08:00:00", "created_at": "2026-09-23T08:00:00", "full": False, "sections": ["env", "config"], "stack_version": "0.5.9", "encrypted": False},
    ],
    "sections": [{"name": "env", "paths": ".env"}, {"name": "content", "paths": "data/content-manager"}, {"name": "config", "paths": "data/content-manager/config"}, {"name": "panel", "paths": "data/panel"}, {"name": "media-studio", "paths": "data/media-studio"}, {"name": "smart-router", "paths": "data/smart-router"}, {"name": "hermes", "paths": "data/hermes"}],
}

MOCK_MEDIA_JOBS = {
    "ok": True,
    "error": "",
    "jobs": [
        {"id": "mj-001", "driver": "api-image", "status": "completed", "created_at": "2026-09-24T08:30:00", "artifacts": ["output.png"], "error": ""},
        {"id": "mj-002", "driver": "flow-video", "status": "completed", "created_at": "2026-09-24T07:15:00", "artifacts": ["output.mp4"], "error": ""},
        {"id": "mj-003", "driver": "api-image", "status": "running", "created_at": "2026-09-24T08:45:00", "artifacts": [], "error": ""},
        {"id": "mj-004", "driver": "api-image", "status": "queued", "created_at": "2026-09-24T08:50:00", "artifacts": [], "error": ""},
    ],
}

MOCK_VIDEO_STUDIO = {
    "drivers": ["api-image", "flow-video", "video-edit"],
    "drivers_source": "env",
    "timelines": [
        {"id": "tl-001", "title": "Kubernetes scheduling update", "status": "rendered", "scenes": 6, "created_at": "2026-09-24T08:00:00", "thumbnail": ""},
        {"id": "tl-002", "title": "AI router multi-provider failover", "status": "approved", "scenes": 8, "created_at": "2026-09-24T06:30:00", "thumbnail": ""},
        {"id": "tl-003", "title": "Platform engineering workflows", "status": "draft", "scenes": 4, "created_at": "2026-09-23T20:00:00", "thumbnail": ""},
    ],
    "stories": [
        {"id": "sb-001", "title": "Kubernetes scheduling update", "status": "rendered", "created_at": "2026-09-24T07:45:00"},
        {"id": "sb-002", "title": "AI router failover", "status": "approved", "created_at": "2026-09-24T06:00:00"},
    ],
    "media_base_url": "http://127.0.0.1:8850",
    "ok": True,
}

MOCK_ORCHESTRATION = {
    "runs": [
        {"id": "run-001", "goal": "Write a technical post about Kubernetes scheduling", "status": "completed", "approval_mode": "auto", "steps": 6, "review_status": None, "awaiting_step": None, "actor": "system", "error": ""},
        {"id": "run-002", "goal": "Create video script for AI router announcement", "status": "awaiting_approval", "approval_mode": "manual", "steps": 4, "review_status": None, "awaiting_step": {"index": 3, "title": "Review video script", "agent_id": "video-director"}, "actor": "operator", "error": ""},
        {"id": "run-003", "goal": "Research platform engineering trends", "status": "running", "approval_mode": "supervised", "steps": 5, "review_status": None, "awaiting_step": None, "actor": "system", "error": ""},
        {"id": "run-004", "goal": "Generate observability comparison post", "status": "completed", "approval_mode": "auto", "steps": 3, "review_status": "approved", "awaiting_step": None, "actor": "system", "error": ""},
        {"id": "run-005", "goal": "Security best practices for container runtimes", "status": "failed", "approval_mode": "auto", "steps": 2, "review_status": None, "awaiting_step": None, "actor": "system", "error": "Model rate limit exceeded"},
    ],
    "ok": True,
}

MOCK_KNOWLEDGE = {
    "bases": [
        {"id": 1, "name": "Technical docs", "chunks": 1240, "owner": "admin", "description": "Project documentation and architecture notes."},
        {"id": 2, "name": "Content drafts", "chunks": 860, "owner": "content-bot", "description": "Previously published drafts and editorial guidelines."},
        {"id": 3, "name": "AI research", "chunks": 3200, "owner": "hermes", "description": "Curated papers, articles and model benchmarks."},
    ],
    "ok": True,
}

MOCK_NOTEBOOKLM = {
    "enabled": True,
    "session_mode": "persistent",
    "worker_running": True,
    "signed_in": True,
    "error": "",
    "google_creds_set": True,
    "google_creds_email": "admin@...",
}

MOCK_ACTIONS = [
    {"name": "stack-up", "label": "Apply changes", "confirm": False, "dangerous": False, "description": "docker compose up -d"},
    {"name": "stack-stop", "label": "Stop stack", "confirm": True, "dangerous": False, "description": "docker compose stop"},
    {"name": "stack-start", "label": "Start stack", "confirm": False, "dangerous": False, "description": "docker compose start"},
    {"name": "stack-pull", "label": "Pull images", "confirm": False, "dangerous": False, "description": "docker compose pull"},
    {"name": "lock-images", "label": "Lock images", "confirm": True, "dangerous": False, "description": "manage.sh lock-images"},
    {"name": "verify-images", "label": "Verify images", "confirm": False, "dangerous": False, "description": "manage.sh verify-images"},
    {"name": "s3-status", "label": "S3 status", "confirm": False, "dangerous": False, "description": "manage.sh s3-status"},
    {"name": "s3-verify", "label": "Verify S3", "confirm": False, "dangerous": False, "description": "manage.sh s3-verify"},
    {"name": "create-full-backup", "label": "Create full backup", "confirm": True, "dangerous": False, "description": "manage.sh backup"},
    {"name": "create-section-backup", "label": "Create section backup", "confirm": True, "dangerous": False, "description": "manage.sh backup --only ..."},
]

MOCK_HERMES_STATE = {
    "profiles_enabled": 4,
    "models_accessible": 12,
    "current_model": "gpt-4o",
    "router_mode": "capability",
    "telemetry_enabled": True,
}

# ── request handler ─────────────────────────────────────────────────
class MockHandler(http.server.BaseHTTPRequestHandler):
    # silence per-request log
    def log_message(self, fmt, *args):
        log.debug(fmt, *args)

    def _respond(self, body, status=HTTPStatus.OK, content_type="application/json"):
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.end_headers()
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.wfile.write(body)

    def _json(self, data, status=HTTPStatus.OK):
        self._respond(json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"), status)

    def do_OPTIONS(self):
        self._respond(b"", HTTPStatus.NO_CONTENT)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        # Health
        if path == "/healthz":
            return self._json({"ok": True, "version": VERSION})
        # Static files
        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            fp = STATIC / rel
            if not fp.is_file():
                return self._respond(b"not found", HTTPStatus.NOT_FOUND)
            ctype, _ = mimetypes.guess_type(str(fp))
            body = fp.read_bytes()
            return self._respond(body, content_type=ctype or "application/octet-stream")
        # Serve SPA
        if path in ("/", "/index.html"):
            body = (STATIC / "index.html").read_bytes()
            return self._respond(body, content_type="text/html; charset=utf-8")
        # API
        if path == "/api/session":
            return self._json({
                "authenticated": True,
                "actions_enabled": True,
                "root": "/tmp/demo",
                "version": VERSION,
                "started_at": time.time() - 10800,
            })
        if path == "/api/status":
            return self._json({
                "profiles": MOCK_PROFILES,
                "image_tags": MOCK_IMAGE_TAGS,
                "disk": MOCK_DISK,
                "exposure": MOCK_EXPOSURE,
                "services": MOCK_SERVICES,
                "state": MOCK_DRAFTS["counters"],
                "error": "",
                "generated_at": _now,
                "version": VERSION,
                "actions_enabled": True,
            })
        if path == "/api/state":
            return self._json(MOCK_DRAFTS)
        if path == "/api/links":
            return self._json({"links": MOCK_LINKS})
        if path == "/api/drafts":
            return self._json(MOCK_DRAFTS)
        if path == "/api/platforms":
            return self._json(MOCK_PLATFORMS)
        if path == "/api/config":
            return self._json(MOCK_CONFIG)
        if path.startswith("/api/config/") and path.endswith("/backups"):
            return self._json({"backups": [
                {"name": f"{path.split('/')[3]}-20260924-080000.yaml", "bytes": 2840, "created_at": "2026-09-24T08:00:00", "path": "/tmp/demo/data/content-manager/config/backups/..."},
                {"name": f"{path.split('/')[3]}-20260923-200000.yaml", "bytes": 2800, "created_at": "2026-09-23T20:00:00", "path": "/tmp/demo/data/content-manager/config/backups/..."},
            ]})
        if path.startswith("/api/config/"):
            name = path.split("/")[3]
            text = MOCK_CONFIG_POLICY
            modified = "2026-09-24T08:00:00"
            if name == "sources":
                text = "# RSS/Atom sources\nsources:\n  - id: kubernetes-io\n    url: https://kubernetes.io/feed.xml\n    category: kubernetes\n  - id: the-new-stack\n    url: https://thenewstack.io/feed\n    category: platform-engineering\n  - id: hacker-news\n    url: https://hnrss.org/frontpage\n    category: technology\n"
            elif name == "categories":
                text = "categories:\n  kubernetes:\n    label: Kubernetes\n    max_daily: 3\n    keywords: [k8s, kubernetes, container, orchestration]\n  ai:\n    label: AI & ML\n    max_daily: 4\n    keywords: [ai, machine learning, llm, neural]\n  security:\n    label: Security\n    max_daily: 2\n    keywords: [security, vulnerability, cve]\n  platform-engineering:\n    label: Platform Engineering\n    max_daily: 3\n    keywords: [platform, engineering, devops, sre]\n  observability:\n    label: Observability\n    max_daily: 2\n    keywords: [observability, monitoring, tracing]\n"
            elif name == "tools":
                text = "schema_version: 1\ntools:\n  - id: n8n-mcp\n    kind: mcp\n    url: http://n8n:5678/mcp/hermes\n    auth:\n      token_env: N8N_TRIGGER_MCP_TOKEN\n  - id: web-search\n    kind: builtin\n"
            return self._json({"name": name, "text": text, "path": f"data/content-manager/config/{name}.yaml", "modified_at": modified, "size": len(text)})
        if path == "/api/env":
            return self._json({"env": MOCK_ENV_VARS, "changed": False, "path": "/tmp/demo/.env", "modified_at": _now})
        if path == "/api/storage":
            return self._json(MOCK_STORAGE)
        if path == "/api/backups":
            return self._json(MOCK_BACKUPS)
        if path == "/api/media/jobs":
            return self._json(MOCK_MEDIA_JOBS)
        if path == "/api/video/studio":
            return self._json(MOCK_VIDEO_STUDIO)
        if path == "/api/video/storyboards":
            return self._json({"storyboards": MOCK_VIDEO_STUDIO["stories"]})
        if path.startswith("/api/video/storyboards/"):
            sb_id = path.split("/")[4]
            return self._json({"id": sb_id, "title": "Sample storyboard", "status": "approved", "scenes": [
                {"id": "sc-1", "type": "title", "text": "Introduction", "duration": 5},
                {"id": "sc-2", "type": "explain", "text": "Key concepts explained", "duration": 15},
                {"id": "sc-3", "type": "visual", "text": "Architecture diagram", "duration": 10},
                {"id": "sc-4", "type": "callout", "text": "Main takeaway", "duration": 8},
            ], "created_at": "2026-09-24T06:00:00"})
        if path == "/api/video/jobs":
            return self._json({"jobs": [
                {"id": "vj-001", "storyboard": "sb-001", "status": "completed", "progress": 100, "created_at": "2026-09-24T08:00:00"},
                {"id": "vj-002", "storyboard": "sb-002", "status": "rendering", "progress": 65, "created_at": "2026-09-24T07:00:00"},
            ]})
        if path == "/api/orchestration/runs":
            return self._json(MOCK_ORCHESTRATION)
        if path.startswith("/api/orchestration/runs/"):
            run_id = path.split("/")[4]
            run = next((r for r in MOCK_ORCHESTRATION["runs"] if r["id"] == run_id), MOCK_ORCHESTRATION["runs"][0])
            return self._json({"run": {
                **run,
                "steps": [
                    {"index": 0, "status": "completed", "title": "Research topic", "agent_id": "researcher", "output": {"summary": "Gathered source material and links."}},
                    {"index": 1, "status": "completed", "title": "Draft outline", "agent_id": "writer", "output": {"outline": "Introduction, Key points, Conclusion"}},
                    {"index": 2, "status": "completed", "title": "Generate draft", "agent_id": "editor", "output": {"content": "Full draft generated."}},
                    {"index": 3, "status": "completed" if run["status"] != "awaiting_approval" else "awaiting", "title": "Review and approve", "agent_id": "reviewer", "output": {}},
                ],
            }})
        if path == "/api/knowledge":
            return self._json(MOCK_KNOWLEDGE)
        if path == "/api/notebooklm/status":
            return self._json(MOCK_NOTEBOOKLM)
        if path == "/api/instagram":
            return self._json(MOCK_INSTAGRAM)
        if path == "/api/hermes":
            return self._json(MOCK_HERMES_STATE)
        if path == "/api/actions":
            return self._json({"actions": MOCK_ACTIONS, "ok": True})
        if path == "/api/logs":
            return self._json({"services": [
                {"name": "content-bot", "lines": ["2026-09-24T08:30:00 INFO Draft published d-001", "2026-09-24T08:20:00 INFO New link received d-005", "2026-09-24T07:15:00 INFO Media completed mj-002"]},
                {"name": "media-studio", "lines": ["2026-09-24T08:45:00 INFO Job mj-003 started", "2026-09-24T08:30:00 INFO Job mj-001 completed", "2026-09-24T08:00:00 INFO Worker ready"]},
            ]})
        # Default
        log.warning("Unhandled GET %s", path)
        return self._json({"error": "not found", "path": path}, HTTPStatus.NOT_FOUND)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len) if content_len else b"{}"
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            data = {}
        if path == "/api/login":
            return self._json({"ok": True})
        if path == "/api/logout":
            return self._json({"ok": True})
        if path == "/api/instagram/refresh":
            return self._json({"ok": True})
        log.warning("Unhandled POST %s", path)
        return self._json({"ok": True})

    def do_PUT(self):
        path = self.path.split("?", 1)[0]
        log.warning("Unhandled PUT %s", path)
        return self._json({"ok": True, "changed": []})

    def do_DELETE(self):
        path = self.path.split("?", 1)[0]
        log.warning("Unhandled DELETE %s", path)
        return self._json({"ok": True})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18990)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    server = http.server.ThreadingHTTPServer((args.bind, args.port), MockHandler)
    log.info("Mock panel server at http://%s:%d", args.bind, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
