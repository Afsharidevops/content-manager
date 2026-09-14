"""Social accounts that automatic publishers can post to.

One platform can publish to more than one destination: a personal LinkedIn
profile next to the company page, or two messenger channels for two brands.
Accounts are declared in ``social-accounts.yaml`` inside the policy directory
so no token is ever hardcoded, and the environment keeps working as the
single-account fallback for existing deployments.

File shape (both forms are accepted)::

    accounts:
      linkedin:
        personal:
          type: person
          access_token: "..."
          author_urn: "urn:li:person:abc123"

    linkedin:
      accounts:
        personal: {type: person, access_token: "...", person_id: "abc123"}

Environment fallback (``CONTENT_LINKEDIN_*``) describes exactly one account.
``CONTENT_LINKEDIN_ACCOUNTS`` may hold the whole mapping as JSON or YAML.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import yaml

log = logging.getLogger("content_bot")

ACCOUNTS_FILE = "social-accounts.yaml"
PERSON = "person"
ORGANIZATION = "organization"
ACCOUNT_KINDS = (PERSON, ORGANIZATION)
PLATFORM_LABELS = {
    "linkedin": "LinkedIn",
    "telegram": "Telegram",
    "bale": "Bale",
    "eitaa": "Eitaa",
}


def author_urn(platform: str, kind: str, urn: str = "", ids: Mapping[str, str] | None = None) -> str:
    """Return the API author URN for one account.

    An explicit ``author_urn`` wins; otherwise the platform-specific id keys
    (``person_id``, ``organization_id``) are turned into the LinkedIn shape
    ``urn:li:<kind>:<id>``.
    """
    value = str(urn or "").strip()
    if value:
        if value.startswith("urn:"):
            return value
        return f"urn:li:{kind}:{value}"
    ids = ids or {}
    if platform == "linkedin":
        if kind == ORGANIZATION:
            identifier = str(ids.get("organization_id") or "").strip()
            return f"urn:li:organization:{identifier}" if identifier else ""
        identifier = str(ids.get("person_id") or ids.get("member_id") or "").strip()
        return f"urn:li:person:{identifier}" if identifier else ""
    return value


@dataclass(frozen=True)
class SocialAccount:
    """One publishing destination: a platform plus the account on it."""

    platform: str
    account: str
    kind: str = PERSON
    access_token: str = ""
    refresh_token: str = ""
    expires_at: str = ""
    author: str = ""
    label: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)

    @property
    def target(self) -> str:
        """Target key used by drafts, buttons, and publication rows."""
        return f"{self.platform}_{self.account}"

    @property
    def display(self) -> str:
        platform = PLATFORM_LABELS.get(self.platform, self.platform.title())
        return self.label or f"{platform} ({self.account})"

    @property
    def configured(self) -> bool:
        """True when the account carries everything its publisher needs."""
        if not self.access_token:
            return False
        if self.platform == "linkedin":
            return bool(self.author)
        return True


def _clean(value) -> str:
    return str(value or "").strip()


def _account_from_mapping(platform: str, name: str, raw: Mapping) -> SocialAccount | None:
    """Build one account from its configuration mapping."""
    if not isinstance(raw, Mapping):
        return None
    account = _clean(name)
    if not account:
        return None
    kind = _clean(raw.get("type") or raw.get("kind")).lower() or PERSON
    if platform == "linkedin" and kind not in ACCOUNT_KINDS:
        log.warning(
            "social account %s/%s: unknown type %r, using person", platform, account, kind
        )
        kind = PERSON
    token = _clean(raw.get("access_token") or raw.get("token"))
    if not token:
        log.warning("social account %s/%s ignored: no access token", platform, account)
        return None
    metadata = {
        str(key): _clean(value)
        for key, value in raw.items()
        if key
        not in {
            "type",
            "kind",
            "access_token",
            "token",
            "refresh_token",
            "expires_at",
            "label",
        }
    }
    return SocialAccount(
        platform=platform,
        account=account,
        kind=kind,
        access_token=token,
        refresh_token=_clean(raw.get("refresh_token")),
        expires_at=_clean(raw.get("expires_at")),
        author=author_urn(
            platform,
            kind,
            _clean(raw.get("author_urn") or raw.get("author")),
            {key: _clean(value) for key, value in raw.items()},
        ),
        label=_clean(raw.get("label")),
        metadata=metadata,
    )


def _accounts_from_section(section: Mapping) -> dict[str, SocialAccount]:
    """Read one ``platform: {account: mapping}`` section."""
    accounts: dict[str, SocialAccount] = {}
    for raw_platform, raw_accounts in section.items():
        platform = _clean(raw_platform).lower()
        if not platform:
            continue
        if isinstance(raw_accounts, Mapping) and "accounts" in raw_accounts:
            raw_accounts = raw_accounts.get("accounts")
        if not isinstance(raw_accounts, Mapping):
            continue
        for name, raw in raw_accounts.items():
            account = _account_from_mapping(platform, _clean(name), raw)
            if account is not None:
                accounts[account.target] = account
    return accounts


def parse_accounts(data) -> dict[str, SocialAccount]:
    """Turn one decoded document into accounts keyed by target."""
    if not isinstance(data, Mapping):
        return {}
    if "accounts" in data and isinstance(data.get("accounts"), Mapping):
        return _accounts_from_section(data["accounts"])
    return _accounts_from_section(data)


def accounts_from_env(env: Mapping[str, str] | None = None) -> dict[str, SocialAccount]:
    """Read the single-account LinkedIn fallback out of the environment."""
    env = env if env is not None else os.environ
    raw = _clean(env.get("CONTENT_LINKEDIN_ACCOUNTS"))
    if raw:
        for loader in (json.loads, yaml.safe_load):
            try:
                accounts = parse_accounts(loader(raw))
            except (ValueError, yaml.YAMLError):
                accounts = {}
            if accounts:
                return accounts
        log.warning("CONTENT_LINKEDIN_ACCOUNTS could not be parsed; ignoring it")
    token = _clean(env.get("CONTENT_LINKEDIN_ACCESS_TOKEN"))
    if not token:
        return {}
    name = _clean(env.get("CONTENT_LINKEDIN_ACCOUNT")) or "personal"
    kind = _clean(env.get("CONTENT_LINKEDIN_ACCOUNT_TYPE")).lower() or PERSON
    account = SocialAccount(
        platform="linkedin",
        account=name,
        kind=kind if kind in ACCOUNT_KINDS else PERSON,
        access_token=token,
        refresh_token=_clean(env.get("CONTENT_LINKEDIN_REFRESH_TOKEN")),
        expires_at=_clean(env.get("CONTENT_LINKEDIN_EXPIRES_AT")),
        author=author_urn(
            "linkedin",
            kind,
            _clean(env.get("CONTENT_LINKEDIN_AUTHOR_URN")),
            {
                "person_id": _clean(env.get("CONTENT_LINKEDIN_PERSON_ID")),
                "organization_id": _clean(env.get("CONTENT_LINKEDIN_ORGANIZATION_ID")),
            },
        ),
        label=_clean(env.get("CONTENT_LINKEDIN_LABEL")),
    )
    return {account.target: account}


def load_accounts(
    policy_dir: str | Path,
    *,
    env: Mapping[str, str] | None = None,
    filename: str = ACCOUNTS_FILE,
) -> dict[str, SocialAccount]:
    """Return every configured account, file first and environment second."""
    path = Path(policy_dir) / (str(filename or "").strip() or ACCOUNTS_FILE)
    accounts: dict[str, SocialAccount] = {}
    if path.is_file():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as error:
            log.warning("social accounts ignored (%s): %s", path, error)
            data = {}
        accounts = parse_accounts(data)
    for key, account in accounts_from_env(env).items():
        accounts.setdefault(key, account)
    return accounts


def accounts_for(
    accounts: Mapping[str, SocialAccount], platform: str
) -> dict[str, SocialAccount]:
    """Return the accounts of one platform keyed by target."""
    wanted = _clean(platform).lower()
    return {
        key: account for key, account in accounts.items() if account.platform == wanted
    }
