import unittest
import shutil
import subprocess
from pathlib import Path

from boardwright.errors import BoardwrightError
from boardwright.config import BoardwrightConfig
from boardwright.release import _validate_version, prepare_release, validate_release_plan, build_release_plan


class ReleaseTests(unittest.TestCase):
    def test_validates_semver(self) -> None:
        _validate_version("0.1.0")
        with self.assertRaises(BoardwrightError):
            _validate_version("v0.1.0")

    def test_release_plan_allows_empty_unreleased_changelog(self) -> None:
        root = Path.cwd() / ".test_release_workspace"
        if root.exists():
            shutil.rmtree(root)
        try:
            root.mkdir()
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
            (root / "CHANGELOG.md").write_text(
                "# Changelog\n\n## [Unreleased]\n",
                encoding="utf-8",
            )
            config = BoardwrightConfig(
                root=root,
                project={"project": {}, "variants": {}, "outputs": {}, "assets": {}},
                branches={"branches": {"release": "main"}},
                legal={},
                revision_history={"revision_history": {}},
            )

            plan = build_release_plan(config, "0.1.0", check_remote=False)
            problems = validate_release_plan(plan, allow_dirty=True)

            self.assertFalse(plan.has_unreleased_changes)
            self.assertNotIn("CHANGELOG.md has no unreleased changes.", problems)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_prepare_release_writes_no_change_note_when_changelog_is_empty(self) -> None:
        root = Path.cwd() / ".test_release_workspace"
        if root.exists():
            shutil.rmtree(root)
        try:
            root.mkdir()
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
            (root / ".boardwright").mkdir()
            (root / "CHANGELOG.md").write_text(
                "# Changelog\n\n## [Unreleased]\n",
                encoding="utf-8",
            )
            config = BoardwrightConfig(
                root=root,
                project={"project": {}, "variants": {}, "outputs": {}, "assets": {}},
                branches={"branches": {"release": "main"}},
                legal={},
                revision_history={"revision_history": {"slots": 1}},
            )

            prepare_release(config, "0.1.0", allow_dirty=True, dry_run=False)

            changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
            self.assertIn("## [0.1.0] - ", changelog)
            self.assertIn("No changelog entries recorded", changelog)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
