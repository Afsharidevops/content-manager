from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import urlencode

import httpx

from .secrets_v52 import env_or_file, json_env

try:  # Installed in the package image; optional for disabled OIDC source tests.
    from authlib.jose import jwt  # type: ignore
except Exception:  # pragma: no cover
    jwt = None


class OIDCManager:
    def __init__(self, hmac_secret: str):
        self.enabled = os.getenv("SMART_ROUTER_OIDC_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
        self.issuer = os.getenv("SMART_ROUTER_OIDC_ISSUER_URL", "").strip().rstrip("/")
        self.client_id = os.getenv("SMART_ROUTER_OIDC_CLIENT_ID", "").strip()
        self.client_secret = env_or_file("SMART_ROUTER_OIDC_CLIENT_SECRET")
        self.redirect_uri = os.getenv("SMART_ROUTER_OIDC_REDIRECT_URI", "").strip()
        self.scopes = os.getenv("SMART_ROUTER_OIDC_SCOPES", "openid profile email groups").strip()
        self.default_role = os.getenv("SMART_ROUTER_OIDC_DEFAULT_ROLE", "user").strip() or "user"
        self.group_role_map = json_env("SMART_ROUTER_OIDC_GROUP_ROLE_MAP", {})
        self.auto_provision = os.getenv("SMART_ROUTER_OIDC_AUTO_PROVISION", "true").strip().lower() in {"1", "true", "yes", "on"}
        self.local_login_enabled = os.getenv("SMART_ROUTER_OIDC_LOCAL_LOGIN_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
        self.secret = hmac_secret.encode()
        self._discovery: dict | None = None
        if self.enabled and not all([self.issuer, self.client_id, self.client_secret, self.redirect_uri]):
            raise ValueError("OIDC is enabled but issuer/client/secret/redirect configuration is incomplete")

    async def authorization_url(self, client: httpx.AsyncClient) -> str:
        doc = await self._discover(client)
        nonce = secrets.token_urlsafe(24)
        state = self._state(nonce)
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": self.scopes,
            "state": state,
            "nonce": nonce,
        }
        return str(doc["authorization_endpoint"]) + "?" + urlencode(params)

    async def exchange(self, client: httpx.AsyncClient, code: str, state: str) -> dict:
        state_payload = self._verify_state(state)
        doc = await self._discover(client)
        response = await client.post(
            str(doc["token_endpoint"]),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        token = response.json()
        id_token = str(token.get("id_token", ""))
        if not id_token:
            raise ValueError("OIDC token response did not include id_token")
        if jwt is None:
            raise RuntimeError("Authlib is required for OIDC token validation")
        jwks_response = await client.get(str(doc["jwks_uri"]))
        jwks_response.raise_for_status()
        claims = jwt.decode(
            id_token,
            jwks_response.json(),
            claims_options={
                "iss": {"essential": True, "value": self.issuer},
                "aud": {"essential": True, "value": self.client_id},
                "exp": {"essential": True},
            },
        )
        claims.validate(leeway=30)
        if str(claims.get("nonce", "")) != str(state_payload["nonce"]):
            raise ValueError("OIDC nonce validation failed")
        return dict(claims)

    def identity(self, claims: dict) -> tuple[str, str, list[str], str]:
        subject = str(claims.get("sub", "")).strip()
        if not subject:
            raise ValueError("OIDC subject is missing")
        username = str(claims.get("preferred_username") or claims.get("email") or f"oidc-{subject[:12]}").strip()
        groups_raw = claims.get("groups", [])
        groups = [str(x) for x in groups_raw] if isinstance(groups_raw, list) else []
        role = self.default_role
        for group in groups:
            mapped = self.group_role_map.get(group)
            if mapped:
                role = str(mapped)
                break
        return subject, username[:120], groups, role

    async def _discover(self, client: httpx.AsyncClient) -> dict:
        if self._discovery is not None:
            return self._discovery
        response = await client.get(self.issuer + "/.well-known/openid-configuration")
        response.raise_for_status()
        doc = response.json()
        if str(doc.get("issuer", "")).rstrip("/") != self.issuer:
            raise ValueError("OIDC discovery issuer mismatch")
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not str(doc.get(key, "")).startswith("https://") and not str(doc.get(key, "")).startswith("http://localhost"):
                raise ValueError(f"OIDC {key} must use HTTPS")
        self._discovery = doc
        return doc

    def _state(self, nonce: str) -> str:
        payload = {"nonce": nonce, "exp": int(time.time()) + 600}
        body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
        sig = base64.urlsafe_b64encode(hmac.new(self.secret, body.encode(), hashlib.sha256).digest()).decode().rstrip("=")
        return body + "." + sig

    def _verify_state(self, state: str) -> dict:
        try:
            body, sig = state.split(".", 1)
            expected = base64.urlsafe_b64encode(hmac.new(self.secret, body.encode(), hashlib.sha256).digest()).decode().rstrip("=")
            if not hmac.compare_digest(sig, expected):
                raise ValueError("signature")
            payload = json.loads(base64.urlsafe_b64decode((body + "=" * (-len(body) % 4)).encode()))
            if int(payload.get("exp", 0)) < int(time.time()):
                raise ValueError("expired")
            return payload
        except Exception as exc:
            raise ValueError("invalid or expired OIDC state") from exc
