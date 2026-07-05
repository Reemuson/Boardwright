# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

import shutil
import unittest
from pathlib import Path

from boardwright.config import BoardwrightConfig
from boardwright.revision_history import build_revision_slots, build_revision_slots_from_text, write_revision_variables


class RevisionHistoryTests(unittest.TestCase):
    def test_builds_fixed_slots(self) -> None:
        text = (
            "# Changelog\n\n"
            "## [Unreleased]\n\n### Added\n\n- Current work\n\n"
            "## [0.1.0] - 2026-04-25\n\n### Fixed\n\n- First release\n"
        )

        slots = build_revision_slots_from_text(text, slot_count=3)

        self.assertEqual(3, len(slots))
        self.assertEqual("Unreleased", slots[0].version)
        self.assertEqual("0.1.0", slots[1].version)
        self.assertEqual("", slots[2].version)

    def test_previous_releases_shift_down(self) -> None:
        text = (
            "# Changelog\n\n"
            "## [Unreleased]\n\n"
            "## [0.3.0] - 2026-04-25\n\n### Added\n\n- Third\n\n"
            "## [0.2.0] - 2026-04-20\n\n### Changed\n\n- Second\n\n"
            "## [0.1.0] - 2026-04-10\n\n### Fixed\n\n- First\n"
        )

        slots = build_revision_slots_from_text(
            text,
            slot_count=4,
            include_unreleased=False,
        )

        self.assertEqual("0.3.0", slots[0].version)
        self.assertEqual("0.2.0", slots[1].version)
        self.assertEqual("0.1.0", slots[2].version)
        self.assertEqual("", slots[3].version)
        self.assertEqual("0.3.0 - 2026-04-25", slots[0].title)
        self.assertEqual("Changed:\n  - Second", slots[1].body)

    def test_visible_variables_are_title_and_body_only(self) -> None:
        text = "# Changelog\n\n## [0.1.0] - 2026-04-25\n\n### Added\n\n- First\n"

        slots = build_revision_slots_from_text(text, slot_count=1)

        self.assertEqual("0.1.0 - 2026-04-25", slots[0].title)
        self.assertEqual("Added:\n  - First", slots[0].body)

    def test_omits_empty_sections(self) -> None:
        text = (
            "# Changelog\n\n"
            "## [Unreleased]\n\n"
            "### Added\n\n"
            "### Fixed\n\n"
            "- Actual fix\n"
        )

        slots = build_revision_slots_from_text(text, slot_count=1)

        self.assertEqual("Fixed:\n  - Actual fix", slots[0].body)


    def test_document_revision_rows_generate_legacy_and_structured_variables(self) -> None:
        root = Path("tests/.tmp_revision_history_document_rows")
        shutil.rmtree(root, ignore_errors=True)
        try:
            (root / ".boardwright").mkdir(parents=True)
            config = BoardwrightConfig(
                root=root,
                project={"project": {}},
                branches={"branches": {}},
                legal={"legal": {}},
                revision_history={"revision_history": {"slots": 2}},
                document_revisions={
                    "document_revisions": {
                        "rows": [
                            {
                                "revision": "B",
                                "description": "Released to production",
                                "date": "2026-07-05",
                                "drawn_by": "RH",
                                "drawn_date": "2026-07-01",
                                "checked_by": "AB",
                                "checked_date": "2026-07-03",
                                "approved_by": "CD",
                                "approved_date": "2026-07-05",
                            }
                        ]
                    }
                },
            )

            slots = build_revision_slots(config)
            output = write_revision_variables(config).read_text(encoding="utf-8")

            self.assertEqual("B", slots[0].version)
            self.assertEqual("B - 2026-07-05", slots[0].title)
            self.assertEqual("Released to production", slots[0].body)
            self.assertIn('REVHIST_1_TITLE="B - 2026-07-05"', output)
            self.assertIn('REVHIST_1_BODY="Released to production"', output)
            self.assertIn('REVHIST_1_REVISION="B"', output)
            self.assertIn('REVHIST_1_DESCRIPTION="Released to production"', output)
            self.assertIn('REVHIST_1_DRAWN_BY="RH"', output)
            self.assertIn('REVHIST_2_TITLE=""', output)
            self.assertIn('REVHIST_2_APPROVED_DATE=""', output)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_missing_document_revision_rows_preserves_changelog_output(self) -> None:
        root = Path("tests/.tmp_revision_history_changelog_fallback")
        shutil.rmtree(root, ignore_errors=True)
        try:
            (root / ".boardwright").mkdir(parents=True)
            (root / "CHANGELOG.md").write_text(
                "# Changelog\n\n## [0.1.0] - 2026-04-25\n\n### Added\n\n- First\n",
                encoding="utf-8",
            )
            config = BoardwrightConfig(
                root=root,
                project={"project": {}},
                branches={"branches": {}},
                legal={"legal": {}},
                revision_history={"revision_history": {"slots": 1}},
                document_revisions={},
            )

            output = write_revision_variables(config).read_text(encoding="utf-8")

            self.assertIn('REVHIST_1_TITLE="0.1.0 - 2026-04-25"', output)
            self.assertIn('REVHIST_1_REVISION="0.1.0"', output)
            self.assertIn('REVHIST_1_DESCRIPTION="Added:\\n  - First"', output)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
