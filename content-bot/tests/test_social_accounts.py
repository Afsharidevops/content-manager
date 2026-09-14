"""Tests for the social account registry behind the publishers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from content_bot import accounts, platforms

YAML_TWO_ACCOUNTS = """\
accounts:
  linkedin:
    personal:
      type: person
      access_token: token-personal
      person_id: abc123
    locallab:
      type: organization
      access_token: token-locallab
      author_urn: urn:li:organization:987
"""


def write_accounts(directory: str, text: str) -> None:
    Path(directory, "social-accounts.yaml").write_text(text, encoding="utf-8")


class SocialAccountFileTests(unittest.TestCase):
    def load(self, text: str):
        with tempfile.TemporaryDirectory() as tmp:
            write_accounts(tmp, text)
            return accounts.load_accounts(tmp, env={})

    def test_two_linkedin_accounts_load_with_their_author_urns(self):
        loaded = self.load(YAML_TWO_ACCOUNTS)
        self.assertEqual({"linkedin_personal", "linkedin_locallab"}, set(loaded))
        personal = loaded["linkedin_personal"]
        self.assertEqual("person", personal.kind)
        self.assertEqual("urn:li:person:abc123", personal.author)
        self.assertTrue(personal.configured)
        company = loaded["linkedin_locallab"]
        self.assertEqual("organization", company.kind)
        self.assertEqual("urn:li:organization:987", company.author)
        self.assertEqual("LinkedIn (locallab)", company.display)

    def test_platform_first_shape_is_accepted(self):
        loaded = self.load(
            "linkedin:\n"
            "  accounts:\n"
            "    personal:\n"
            "      access_token: t\n"
            "      author_urn: urn:li:person:1\n"
        )
        self.assertEqual({"linkedin_personal"}, set(loaded))
        self.assertEqual("urn:li:person:1", loaded["linkedin_personal"].author)

    def test_account_without_a_token_is_ignored(self):
        loaded = self.load(
            "accounts:\n  linkedin:\n    personal:\n      person_id: abc\n"
        )
        self.assertEqual({}, loaded)

    def test_unknown_type_falls_back_to_person(self):
        loaded = self.load(
            "accounts:\n"
            "  linkedin:\n"
            "    personal:\n"
            "      type: robot\n"
            "      access_token: t\n"
            "      person_id: 5\n"
        )
        self.assertEqual("person", loaded["linkedin_personal"].kind)

    def test_metadata_keeps_extra_keys(self):
        loaded = self.load(
            "accounts:\n"
            "  linkedin:\n"
            "    personal:\n"
            "      access_token: t\n"
            "      author_urn: urn:li:person:1\n"
            "      expires_at: 2026-12-01\n"
            "      notify: ops@example.com\n"
        )
        account = loaded["linkedin_personal"]
        self.assertEqual("2026-12-01", account.expires_at)
        self.assertEqual("ops@example.com", account.metadata["notify"])

    def test_broken_yaml_is_ignored(self):
        self.assertEqual({}, self.load("accounts: [unclosed\n"))


class SocialAccountEnvTests(unittest.TestCase):
    def test_single_account_from_the_environment(self):
        loaded = accounts.accounts_from_env(
            {
                "CONTENT_LINKEDIN_ACCESS_TOKEN": "tok",
                "CONTENT_LINKEDIN_PERSON_ID": "person-9",
            }
        )
        self.assertEqual({"linkedin_personal"}, set(loaded))
        self.assertEqual("urn:li:person:person-9", loaded["linkedin_personal"].author)

    def test_account_name_and_type_come_from_the_environment(self):
        loaded = accounts.accounts_from_env(
            {
                "CONTENT_LINKEDIN_ACCESS_TOKEN": "tok",
                "CONTENT_LINKEDIN_ACCOUNT": "locallab",
                "CONTENT_LINKEDIN_ACCOUNT_TYPE": "organization",
                "CONTENT_LINKEDIN_ORGANIZATION_ID": "42",
            }
        )
        account = loaded["linkedin_locallab"]
        self.assertEqual("organization", account.kind)
        self.assertEqual("urn:li:organization:42", account.author)

    def test_accounts_mapping_in_the_environment(self):
        loaded = accounts.accounts_from_env(
            {
                "CONTENT_LINKEDIN_ACCOUNTS": json.dumps(
                    {
                        "accounts": {
                            "linkedin": {
                                "locallab": {
                                    "type": "organization",
                                    "access_token": "t",
                                    "author_urn": "urn:li:organization:7",
                                }
                            }
                        }
                    }
                )
            }
        )
        self.assertEqual({"linkedin_locallab"}, set(loaded))

    def test_broken_json_is_ignored_and_the_single_account_is_used(self):
        loaded = accounts.accounts_from_env(
            {
                "CONTENT_LINKEDIN_ACCOUNTS": "{not-json",
                "CONTENT_LINKEDIN_ACCESS_TOKEN": "tok",
                "CONTENT_LINKEDIN_AUTHOR_URN": "urn:li:person:1",
            }
        )
        self.assertEqual({"linkedin_personal"}, set(loaded))

    def test_the_file_wins_over_the_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_accounts(tmp, YAML_TWO_ACCOUNTS)
            loaded = accounts.load_accounts(
                tmp,
                env={
                    "CONTENT_LINKEDIN_ACCESS_TOKEN": "env-token",
                    "CONTENT_LINKEDIN_PERSON_ID": "env-person",
                },
            )
        self.assertEqual({"linkedin_personal", "linkedin_locallab"}, set(loaded))
        self.assertEqual("token-personal", loaded["linkedin_personal"].access_token)


class AccountProfileTests(unittest.TestCase):
    def test_each_account_becomes_an_automatic_linkedin_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_accounts(tmp, YAML_TWO_ACCOUNTS)
            loaded = accounts.load_accounts(tmp, env={})
        profiles = platforms.load_profiles({}, loaded)
        self.assertNotIn("linkedin", profiles)
        personal = profiles["linkedin_personal"]
        self.assertEqual("auto", personal.mode)
        self.assertEqual("linkedin_personal", personal.channel_key)
        self.assertEqual("linkedin_personal", personal.tone)
        self.assertEqual("LinkedIn (locallab)", profiles["linkedin_locallab"].label)
        self.assertEqual("linkedin_company", profiles["linkedin_locallab"].tone)

    def test_without_accounts_the_manual_package_stays(self):
        profiles = platforms.load_profiles({}, {})
        self.assertIn("linkedin", profiles)
        self.assertEqual("package", profiles["linkedin"].mode)

    def test_policy_can_override_a_derived_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_accounts(tmp, YAML_TWO_ACCOUNTS)
            loaded = accounts.load_accounts(tmp, env={})
        profiles = platforms.load_profiles(
            {
                "platforms": {
                    "linkedin_locallab": {"label": "LocalLab page", "hashtags": ["#AI"]}
                }
            },
            loaded,
        )
        company = profiles["linkedin_locallab"]
        self.assertEqual("LocalLab page", company.label)
        self.assertEqual(("#AI",), company.hashtags)
        self.assertEqual("auto", company.mode)
        self.assertEqual("linkedin_company", company.tone)
