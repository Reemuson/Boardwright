# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Deprecated KiCad text variables: detection, migration and release impact.

Regression for the ci-test E2E preview failure (2026-11-18): a schematic text
item `${BOARD_NAME}` became an ERC `unresolved_variable` error once Boardwright's
sync removed BOARD_NAME from the .kicad_pro; the KDT worksheets in use also
referenced BOARD_NAME/PROJECT_NAME, which ERC does not check.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_revision_model import Project, _rmtree  # noqa: E402

from boardwright.config import load_config  # noqa: E402
from boardwright.deprecated_variables import find_deprecated_references, rewrite_unambiguous  # noqa: E402
from boardwright.kicad_variables import DEPRECATED_KICAD_TEXT_VARIABLES  # noqa: E402
from boardwright.migration import migration_summary  # noqa: E402
from boardwright.validation import ValidationIssue, _validate_deprecated_schematic_variables  # noqa: E402

SCH = '(kicad_sch (version 20250114)\n  (title_block (rev "${{REVISION}}"))\n  (text "{text}" (at 25.4 40.64 0))\n)\n'
WKS = '(kicad_wks (version 20231118)\n  (tbtext "${{BOARD_NAME}}" (pos 1 1))\n  (tbtext "${{REVISION}}" (pos 2 2))\n  (tbtext "${{PROJECT_NAME}}" (pos 3 3))\n)\n'


class DeprecatedVariableTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="bw-deprecated-"))

    def tearDown(self) -> None:
        _rmtree(self.root)

    def _project(self, text: str = "${BOARD_NAME}", worksheet: bool = True) -> None:
        (self.root / "b.kicad_sch").write_text(SCH.format(text=text), encoding="utf-8")
        settings = {}
        if worksheet:
            (self.root / "Templates").mkdir()
            (self.root / "Templates" / "legacy.kicad_wks").write_text(WKS.format(), encoding="utf-8")
            settings = {"schematic": {"page_layout_descr_file": "Templates/legacy.kicad_wks"}}
        (self.root / "b.kicad_pro").write_text(json.dumps(settings), encoding="utf-8")

    def _issues(self, project: dict | None = None) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        _validate_deprecated_schematic_variables(self.root, issues, project)
        return issues

    def test_original_board_name_erc_failure_is_a_validation_error_with_location(self) -> None:
        self.assertIn("BOARD_NAME", DEPRECATED_KICAD_TEXT_VARIABLES)  # removed from .kicad_pro by sync
        self._project(worksheet=False)
        messages = [issue.message for issue in self._issues() if issue.level == "error"]
        self.assertTrue(any("b.kicad_sch:3: ${BOARD_NAME}" in m and "${PCBA_NAME}" in m for m in messages), messages)

    def test_active_worksheet_references_are_reported_but_builtin_revision_is_not(self) -> None:
        self._project(text="ok")
        refs = {(ref.path, ref.variable) for ref in find_deprecated_references(self.root)}
        self.assertIn(("Templates/legacy.kicad_wks", "BOARD_NAME"), refs)
        self.assertIn(("Templates/legacy.kicad_wks", "PROJECT_NAME"), refs)
        self.assertNotIn(("Templates/legacy.kicad_wks", "REVISION"), refs)  # KiCad built-in in worksheets
        self.assertIn(("b.kicad_sch", "REVISION"), refs)

    def test_unused_worksheets_are_not_scanned(self) -> None:
        self._project(text="ok", worksheet=False)
        (self.root / "Templates").mkdir()
        (self.root / "Templates" / "unused.kicad_wks").write_text(WKS.format(), encoding="utf-8")
        self.assertFalse(any(ref.kind == "worksheet" for ref in find_deprecated_references(self.root)))

    def test_pcb_references_are_warnings(self) -> None:
        (self.root / "b.kicad_pcb").write_text('(kicad_pcb (gr_text "${PCB_NUMBER}" (layer "F.SilkS")))', encoding="utf-8")
        issues = self._issues()
        self.assertEqual(["warning"], [issue.level for issue in issues])
        self.assertIn("${PCB_NAME}", issues[0].message)

    def test_legacy_board_name_is_reported_not_substituted(self) -> None:
        issues = self._issues({"project": {"name": "FLUKE IR", "board_name": "FIC-100A"}})
        self.assertTrue(any("project.board_name ('FIC-100A') is deprecated" in issue.message for issue in issues))

    def test_migration_rewrites_only_unambiguous_references_preserving_bytes(self) -> None:
        original = (
            b'(kicad_sch\r\n  (title_block (rev "${REVISION}"))\r\n'
            b'  (text "${DOCUMENT_REVISION} ${PCBA_NUMBER}" (at 0 0 0))\r\n'
            b'  (text "${BOARD_NAME}" (at 1 1 0))\r\n)\r\n'
        )
        path = self.root / "b.kicad_sch"
        path.write_bytes(original)
        written, manual = rewrite_unambiguous(self.root, [path])
        self.assertEqual([path], written)
        self.assertEqual(
            original.replace(b"${REVISION}", b"${DRAWING_REVISION}")
            .replace(b"${DOCUMENT_REVISION}", b"${DRAWING_REVISION}")
            .replace(b"${PCBA_NUMBER}", b"${PCBA_NAME}"),
            path.read_bytes(),
        )
        self.assertEqual([("BOARD_NAME", 4)], [(ref.variable, ref.line) for ref in manual])

    def test_migration_summary_lists_manual_review_items(self) -> None:
        summary = migration_summary(
            {
                "deprecated_references": [
                    {"location": "b.kicad_sch:2", "variable": "REVISION", "recommendation": "replace with ${DRAWING_REVISION}"},
                    {"location": "b.kicad_sch:4", "variable": "BOARD_NAME", "recommendation": "use ${PCBA_NAME} ..."},
                ]
            }
        )
        self.assertIn("rewritten automatically: 1", summary)
        self.assertIn("Manual review: b.kicad_sch:4 ${BOARD_NAME}", summary)


class LegacyMetadataReleaseTests(unittest.TestCase):
    """The ci-test correction: ${BOARD_NAME} -> ${PCBA_NAME}, board_name -> pcba_name."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="bw-legacy-names-"))
        self.project = Project(self.tmp)
        self.project.write(note="${BOARD_NAME}")
        path = self.tmp / ".boardwright" / "project.yaml"
        path.write_text(path.read_text(encoding="utf-8").replace("  name: Test Board\n", "  name: FLUKE IR\n  board_name: FIC-100A\n"), encoding="utf-8")
        self.project.commit("legacy names")
        self.project.release("1.0.0", "A.0", justification="Initial")

    def tearDown(self) -> None:
        _rmtree(self.tmp)

    def test_variables_resolve_from_pcba_name_without_using_project_name(self) -> None:
        legacy = load_config(self.tmp).drawing_variables("schematic")
        self.assertEqual("FLUKE IR", legacy["PCBA_NAME"])  # the silent fallback validation warns about
        self._migrate()
        variables = load_config(self.tmp).drawing_variables("schematic")
        self.assertEqual("FIC-100A", variables["PCBA_NAME"])
        self.assertEqual("FIC-100A", variables["PCB_NAME"])  # documented fallback to pcba_name
        self.assertEqual("FLUKE IR", variables["PROJECT_NUMBER"])

    def test_correction_is_documentation_only_and_keeps_pcb_revision_a(self) -> None:
        self._migrate()
        assessment = self.project.assess("1.0.1", "documentation", "A.1", "Replace deprecated BOARD_NAME")
        self.assertEqual((), assessment.problems)
        self.assertEqual(("schematic",), assessment.detected)
        self.assertEqual("A", assessment.proposed.pcb_revision)
        self.assertEqual("A/1", assessment.fabrication_baseline.id)
        self.assertEqual("reused", assessment.fabrication_baseline.status)

    def _migrate(self) -> None:
        schematic = self.tmp / "board.kicad_sch"
        schematic.write_text(
            schematic.read_text(encoding="utf-8").replace('(text "${BOARD_NAME}"', '(text "${PCBA_NAME}"'),
            encoding="utf-8",
        )
        path = self.tmp / ".boardwright" / "project.yaml"
        text = path.read_text(encoding="utf-8")
        self.assertIn("board_name: FIC-100A", text)
        path.write_text(text.replace("board_name: FIC-100A", "pcba_name: FIC-100A"), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
