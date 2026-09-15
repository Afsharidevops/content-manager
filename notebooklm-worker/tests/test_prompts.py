"""Profile and prompt rendering tests."""

from __future__ import annotations

import unittest

from app import prompts


class ProfileTests(unittest.TestCase):
    def test_the_spec_profiles_exist_with_their_fields(self):
        for name, profile in prompts.PROFILES.items():
            self.assertEqual(name, profile.name)
            self.assertTrue(profile.language)
            self.assertTrue(profile.duration)
            self.assertIn(profile.voice_gender, {"male", "female", "neutral"})
            self.assertTrue(profile.style)
            self.assertTrue(profile.tone)
            self.assertTrue(profile.audience)

    def test_an_unknown_profile_falls_back_to_the_default(self):
        profile = prompts.get_profile("does-not-exist")
        self.assertEqual(prompts.PROFILES[prompts.DEFAULT_PROFILE], profile)

    def test_a_duration_bucket_overrides_the_profile_length(self):
        profile = prompts.get_profile("technical_fa")
        self.assertEqual("10 to 15 minutes", prompts.duration_target(profile, "deep"))
        self.assertEqual(profile.duration, prompts.duration_target(profile, ""))
        self.assertEqual(profile.duration, prompts.duration_target(profile, "nonsense"))


class PromptTests(unittest.TestCase):
    def render(self, **kwargs) -> str:
        profile = prompts.get_profile(kwargs.pop("profile", "technical_fa"))
        return prompts.render_prompt(
            kwargs.pop("topic", "Kubernetes security"),
            profile,
            **kwargs,
        )

    def test_the_prompt_carries_every_profile_field(self):
        prompt = self.render()
        self.assertIn("Kubernetes security", prompt)
        self.assertIn("Persian", prompt)
        self.assertIn("5 to 8 minutes", prompt)
        self.assertIn("male", prompt)
        self.assertIn("technical", prompt)
        self.assertIn("Developers, DevOps Engineers", prompt)

    def test_the_prompt_lists_its_rules_and_structure(self):
        prompt = self.render()
        self.assertIn("Use only the provided sources.", prompt)
        self.assertIn("First part - a compelling opening:", prompt)
        self.assertIn("Third part - the closing:", prompt)

    def test_a_duration_override_and_source_note_reach_the_prompt(self):
        prompt = self.render(duration="2 to 3 minutes", sources_note="- https://example.com")
        self.assertIn("2 to 3 minutes", prompt)
        self.assertIn("Sources:\n- https://example.com", prompt)

    def test_a_blank_topic_still_renders(self):
        prompt = self.render(topic="   ")
        self.assertIn("the provided sources", prompt)


if __name__ == "__main__":
    unittest.main()
