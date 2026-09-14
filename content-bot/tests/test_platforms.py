"""Tests for the manual-upload platform packages."""

from __future__ import annotations

import unittest

from content_bot import platforms


def record(**overrides) -> dict:
    base = {
        "id": "draft-1",
        "title": "Container layers explained",
        "body": "First paragraph.\n\nSecond paragraph.",
        "source_url": "https://example.com/layers",
        "media": {},
    }
    base.update(overrides)
    return base


class ProfilePolicyTests(unittest.TestCase):
    def test_built_in_profiles_cover_the_manual_targets(self):
        profiles = platforms.load_profiles({})
        self.assertEqual(
            set(profiles), {"youtube", "aparat", "linkedin", "bale", "eitaa"}
        )
        self.assertTrue(profiles["youtube"].wants_video)
        self.assertTrue(profiles["aparat"].upload_url)
        self.assertTrue(profiles["linkedin"].upload_url)
        self.assertFalse(profiles["linkedin"].wants_video)
        self.assertEqual(profiles["bale"].mode, "auto")
        self.assertEqual(profiles["eitaa"].channel_key, "eitaa")
        self.assertEqual(profiles["linkedin"].mode, "package")

    def test_policy_overrides_values_and_adds_platforms(self):
        policy = {
            "platforms": {
                "youtube": {"title_limit": 40, "hashtags": ["docker", "#devops"]},
                "linkedin": {
                    "label": "LinkedIn",
                    "upload_url": "https://www.linkedin.com/feed/",
                },
            }
        }
        profiles = platforms.load_profiles(policy)
        self.assertEqual(profiles["youtube"].title_limit, 40)
        self.assertEqual(profiles["youtube"].hashtags, ("#docker", "#devops"))
        self.assertEqual(profiles["linkedin"].label, "LinkedIn")
        self.assertEqual(profiles["linkedin"].key, "linkedin")

    def test_policy_can_remove_a_profile(self):
        profiles = platforms.load_profiles({"platforms": {"aparat": None}})
        self.assertNotIn("aparat", profiles)
        self.assertIn("youtube", profiles)

    def test_non_positive_limits_fall_back_to_defaults(self):
        profiles = platforms.load_profiles(
            {"platforms": {"youtube": {"title_limit": 0, "description_limit": "x"}}}
        )
        self.assertEqual(profiles["youtube"].title_limit, 100)
        self.assertEqual(profiles["youtube"].description_limit, 5000)

    def test_a_live_aparat_channel_turns_its_profile_automatic(self):
        profiles = platforms.load_profiles({}, None, {"aparat": object()})
        aparat = profiles["aparat"]
        self.assertEqual("auto", aparat.mode)
        self.assertEqual("aparat", aparat.channel_key)
        self.assertTrue(platforms.needs_video(aparat))
        self.assertTrue(aparat.wants_video)
        self.assertEqual(100, aparat.title_limit)
        self.assertEqual(4000, aparat.description_limit)

    def test_aparat_stays_a_package_without_a_session(self):
        aparat = platforms.load_profiles({})["aparat"]
        self.assertEqual("package", aparat.mode)
        self.assertFalse(platforms.needs_video(aparat))

    def test_policy_can_pin_the_video_only_flag(self):
        profiles = platforms.load_profiles({"platforms": {"youtube": {"video_only": True}}})
        self.assertTrue(platforms.needs_video(profiles["youtube"]))

    def test_collect_tags_reads_the_description_hashtags(self):
        profiles = platforms.load_profiles(
            {"platforms": {"youtube": {"hashtags": ["docker", "devops"]}}}
        )
        tags = platforms.collect_tags(
            record(body="A post about #Linux and #kernel work."), profiles["youtube"]
        )
        # Inline tags of the body keep their place before the profile ones.
        self.assertEqual(["Linux", "kernel", "docker", "devops"], tags)

    def test_video_meta_carries_the_title_description_and_tags(self):
        profiles = platforms.load_profiles(
            {"platforms": {"youtube": {"hashtags": ["docker"]}}}
        )
        meta = platforms.video_meta(record(), profiles["youtube"])
        self.assertEqual("Container layers explained", meta["title"])
        self.assertIn("First paragraph.", meta["description"])
        self.assertIn("https://example.com/layers", meta["description"])
        self.assertEqual(["docker"], meta["tags"])

    def test_hashtags_normalize_spaces_and_prefixes(self):
        profiles = platforms.load_profiles(
            {"platforms": {"youtube": {"hashtags": "docker optimization"}}}
        )
        self.assertEqual(profiles["youtube"].hashtags, ("#docker", "#optimization"))


class PackageTests(unittest.TestCase):
    def profile(self, key: str = "youtube", overrides: dict | None = None):
        policy = {"platforms": {key: overrides}} if overrides else {}
        return platforms.load_profiles(policy)[key]

    def test_title_keeps_inside_the_limit(self):
        title, shortened = platforms.compose_title(
            record(title="x" * 150), self.profile()
        )
        self.assertTrue(shortened)
        self.assertEqual(len(title), 100)
        self.assertTrue(title.endswith("..."))

    def test_description_appends_source_and_hashtags(self):
        profile = self.profile("youtube", {"hashtags": ["docker"]})
        description, shortened = platforms.compose_description(record(), profile)
        self.assertFalse(shortened)
        self.assertIn("Source: https://example.com/layers", description)
        self.assertTrue(description.endswith("#docker"))

    def test_description_cut_prefers_a_paragraph_boundary(self):
        body = "A" * 80 + "\n\n" + "B" * 80
        profile = self.profile("youtube", {"description_limit": 100})
        description, shortened = platforms.compose_description(
            record(body=body, source_url=""), profile
        )
        self.assertTrue(shortened)
        self.assertEqual(description, "A" * 80)

    def test_description_without_boundary_stays_within_the_limit(self):
        profile = self.profile("youtube", {"description_limit": 40})
        description, shortened = platforms.compose_description(
            record(body="x" * 200, source_url=""), profile
        )
        self.assertTrue(shortened)
        self.assertLessEqual(len(description), 40)

    def test_package_text_escapes_html_and_links_upload(self):
        text = platforms.package_text(
            record(title="A <b>bold</b> title"), self.profile()
        )
        self.assertIn("YouTube upload package", text)
        self.assertIn("&lt;b&gt;", text)
        self.assertIn('href="https://studio.youtube.com/"', text)

    def test_package_text_warns_when_the_video_is_missing(self):
        text = platforms.package_text(record(), self.profile())
        self.assertIn("No video is attached to this draft yet.", text)
        with_video = platforms.package_text(
            record(media={"kind": "video", "local_path": "/tmp/clip.mp4"}),
            self.profile(),
        )
        self.assertNotIn("No video is attached", with_video)

    def test_package_text_notes_shortened_text(self):
        profile = self.profile("youtube", {"title_limit": 10})
        text = platforms.package_text(record(title="A very long title indeed"), profile)
        self.assertIn("Text was shortened to fit the platform limits.", text)

    def test_linkedin_package_is_copy_ready(self):
        profile = platforms.load_profiles({})["linkedin"]
        text = platforms.package_text(record(), profile)
        self.assertIn("LinkedIn upload package", text)
        self.assertIn("Container layers explained", text)
        self.assertIn("https://example.com/layers", text)

    def test_stored_media_reads_albums_and_single_files(self):
        album = record(
            media={
                "kind": "image",
                "files": [
                    {"kind": "image", "local_path": "/tmp/a.png", "name": "a.png"},
                    {"kind": "image", "local_path": "/tmp/b.png", "name": "b.png"},
                ],
            }
        )
        self.assertEqual(
            platforms.stored_media(album),
            [("image", "/tmp/a.png", "a.png"), ("image", "/tmp/b.png", "b.png")],
        )
        single = record(media={"kind": "video", "local_path": "/tmp/clip.mp4"})
        self.assertEqual(
            platforms.stored_media(single), [("video", "/tmp/clip.mp4", "clip.mp4")]
        )
        self.assertEqual(platforms.stored_media(record()), [])
