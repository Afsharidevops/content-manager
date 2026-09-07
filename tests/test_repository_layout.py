from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RepositoryLayoutTest(unittest.TestCase):
    def test_single_canonical_root_changelog(self):
        self.assertTrue((ROOT / 'CHANGELOG.md').is_file())
        self.assertEqual([], sorted(p.name for p in ROOT.glob('CHANGELOG-v*.md')))

    def test_root_markdown_is_intentionally_small(self):
        expected = {'README.md', 'CHANGELOG.md', 'SECURITY.md'}
        self.assertEqual(expected, {p.name for p in ROOT.glob('*.md')})

    def test_no_roadmap_or_plan_documents_in_active_tree(self):
        markdown = [p for p in ROOT.rglob('*.md') if '.git' not in p.parts]
        offenders = [p.relative_to(ROOT).as_posix() for p in markdown
                     if 'roadmap' in p.name.lower() or 'plan' in p.name.lower()]
        self.assertEqual([], sorted(offenders))

    def test_no_historical_docs_archive_in_active_tree(self):
        self.assertFalse((ROOT / 'docs' / 'archive').exists())

    def test_current_docs_remain_easy_to_find(self):
        required = [
            ROOT/'docs'/'HERMES-OPERATIONS-CENTER-USER-GUIDE-v0.5.9.md',
            ROOT/'docs'/'RELEASE-PROCESS.md',
            ROOT/'docs'/'SMART-ROUTER-CLIENT-API.md',
            ROOT/'docs'/'publishing'/'SMART-ROUTER-DOCKERHUB.md',
            ROOT/'docs'/'publishing'/'EXECUTION-BROKER-DOCKERHUB.md',
            ROOT/'smart-router'/'V0.5.9-RELEASE-NOTES.md',
            ROOT/'execution-broker'/'V0.1.3-RELEASE-NOTES.md',
        ]
        for path in required:
            self.assertTrue(path.is_file(), path)

    def test_release_version_is_current(self):
        self.assertEqual('v0.5.9', (ROOT/'VERSION').read_text(encoding='utf-8').strip())


if __name__ == '__main__':
    unittest.main()
