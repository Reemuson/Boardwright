# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Title-block / worksheet revision identity integration tests."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from boardwright.migration import _rewrite_pcb_title_revision
from boardwright.sexpr import parse
from boardwright.validation import ValidationIssue, _validate_deprecated_schematic_variables

ROOT = Path(__file__).resolve().parents[1]
IDENTITY = "PCB REV ${PCB_REVISION} | SCH REV ${SCHEMATIC_REVISION} | RELEASE ${RELEASE_VERSION}"
BOARDWRIGHT_WORKSHEETS = sorted(
    path for path in (ROOT / "Templates").glob("Boardwright_Template*.kicad_wks") if "AS1100" not in path.name
)


class WorksheetIdentityTests(unittest.TestCase):
    def test_every_boardwright_worksheet_shows_all_three_identities(self) -> None:
        self.assertEqual(len(BOARDWRIGHT_WORKSHEETS), 8)
        for path in BOARDWRIGHT_WORKSHEETS:
            with self.subTest(path.name):
                text = path.read_text(encoding="utf-8")
                parse(text)  # still well-formed S-expression
                self.assertEqual(text.count(IDENTITY), 1)
                # The REVISION cell keeps the document's own revision.
                self.assertIn('(tbtext "${DRAWING_REVISION}"', text)

    def test_identity_line_sits_in_drawing_number_cell_without_overlap(self) -> None:
        for path in BOARDWRIGHT_WORKSHEETS:
            text = path.read_text(encoding="utf-8")
            block = re.search(r'\(tbtext "PCB REV.*?\n\t\)', text, re.S).group(0)
            self.assertIn("(pos 26 20)", block)
            self.assertIn("(justify right top)", block)
            # Title occupies y 11..14 (bottom), label y 21.5..24 (top) of the 10..25 cell.
            self.assertRegex(text, r'\(tbtext "\$\{DRAWING_TITLE\}"\s*\(name ""\)\s*\(pos 26 11')

    def test_pcb_title_block_uses_physical_pcb_revision(self) -> None:
        text = (ROOT / "boardwright.kicad_pcb").read_text(encoding="utf-8")
        self.assertIn('(rev "${PCB_REVISION}")', text)
        self.assertNotIn("${REVISION}", text)
        for schematic in ROOT.glob("*.kicad_sch"):
            self.assertNotIn('(rev "${PCB_REVISION}")', schematic.read_text(encoding="utf-8"))

    def test_kicad_project_defines_identity_variables(self) -> None:
        variables = json.loads((ROOT / "boardwright.kicad_pro").read_text(encoding="utf-8"))["text_variables"]
        for key in ("PCB_REVISION", "SCHEMATIC_REVISION", "DRAWING_REVISION", "RELEASE_VERSION"):
            self.assertIn(key, variables)
        self.assertEqual(variables["PCB_REVISION"], variables["BOARD_REVISION"])
        self.assertNotIn("REVISION", variables)

    def test_kibot_preflight_sets_identity_variables(self) -> None:
        preflight = (ROOT / "boardwright_resources/kibot/yaml/kibot_pre_set_text_variables.yaml").read_text(
            encoding="utf-8"
        )
        self.assertRegex(preflight, r"variable: 'PCB_REVISION'\s+text: '@BOARD_REVISION@'")
        self.assertRegex(preflight, r"variable: 'SCHEMATIC_REVISION'\s+text: '@SCHEMATIC_REVISION@'")
        main = (ROOT / "boardwright_resources/kibot/yaml/kibot_main.yaml").read_text(encoding="utf-8")
        self.assertIn("      SCHEMATIC_REVISION: @SCHEMATIC_REVISION@", main)
        self.assertRegex(main, r"(?m)^  SCHEMATIC_REVISION: ")


class LegacyPcbRevisionTests(unittest.TestCase):
    def test_validation_warns_about_removed_revision_variable_in_pcb(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "b.kicad_pcb").write_text('(kicad_pcb (title_block (rev "${REVISION}")))', encoding="utf-8")
            issues: list[ValidationIssue] = []
            _validate_deprecated_schematic_variables(root, issues)
            self.assertEqual([issue.level for issue in issues], ["warning"])
            self.assertIn("PCB_REVISION", issues[0].message)

    def test_migration_rewrites_only_the_title_revision_preserving_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = (
                b'(kicad_pcb\r\n\t(title_block\r\n\t\t(rev "${REVISION}")\r\n\t)\r\n'
                b'\t(gr_text "${REVISION} literal" (at 0 0))\r\n)\r\n'
            )
            (root / "b.kicad_pcb").write_bytes(original)
            self.assertEqual(len(_rewrite_pcb_title_revision(root)), 1)
            updated = (root / "b.kicad_pcb").read_bytes()
            self.assertEqual(updated, original.replace(b'(rev "${REVISION}")', b'(rev "${PCB_REVISION}")'))
            self.assertEqual(_rewrite_pcb_title_revision(root), [])


AS1100_SHEETS = (
    ROOT / "Templates/Boardwright_Template_AS1100.kicad_wks",
    ROOT / "Templates/Boardwright_Template_PCB_AS1100_A3.kicad_wks",
)
AS1100_IDENTITY = (
    "PCB REV ${PCB_REVISION} | ASSY REV ${ASSEMBLY_REVISION} | SCH REV ${SCHEMATIC_REVISION} | "
    "RELEASE ${RELEASE_VERSION}"
)
TBTEXT = re.compile(r'\t\(tbtext "((?:[^"\\]|\\.)*)"\n(.*?)\n\t\)\n', re.S)


class As1100WorksheetTests(unittest.TestCase):
    """AS 1100 schematic/PCB pair (audit F11; GE01412/ELEC4840 reconciliation)."""

    def items(self, path: Path) -> list[tuple[str, str]]:
        return [(m.group(1), m.group(2)) for m in TBTEXT.finditer(path.read_text(encoding="utf-8"))]

    def test_well_formed_with_one_identity_line_and_own_revision_cell(self) -> None:
        for path in AS1100_SHEETS:
            with self.subTest(path.name):
                text = path.read_text(encoding="utf-8")
                parse(text)
                self.assertEqual(1, text.count(AS1100_IDENTITY))
                self.assertIn('(tbtext "${DRAWING_REVISION}"', text)
                self.assertIn('(tbtext "${SHEET_LEGAL_NOTICE}"', text)
                for label, _ in self.items(path):  # no stray tabs inside labels
                    self.assertNotIn("\t", label)

    def test_every_variable_cell_is_length_limited(self) -> None:
        exempt = {"${PAPER}", "SHEET ${#} OF ${##}"}
        for path in AS1100_SHEETS:
            for label, body in self.items(path):
                if "${" in label and label not in exempt:
                    with self.subTest(path.name, label=label):
                        self.assertIn("(maxlen", body)

    def test_revision_table_rows_use_compact_text(self) -> None:
        for path in AS1100_SHEETS:
            for label, body in self.items(path):
                if re.fullmatch(r"\$\{REVTABLE_\d_(REV|DATE|DESC|DRAWN)\}", label):
                    with self.subTest(path.name, label=label):
                        self.assertIn("(size 1.8 1.8)", body)

    def test_revision_note_stays_inside_the_title_band(self) -> None:
        # The 50 mm bottom band is reserved for the sheet; anything above it is
        # drawing area. The note sits in the framed cell right of the table
        # (x 145..A3 title block, y 10..50), away from ${LAYER} at y 15.
        for path in AS1100_SHEETS:
            body = dict(self.items(path))["${REVTABLE_NOTE}"]
            x, y = (float(v) for v in re.search(r"\(pos ([\d.]+) ([\d.]+) lbcorner\)", body).groups())
            self.assertTrue(145 < x and 10 < y < 50 - 1.8 / 2, (path.name, x, y))
            self.assertIn("(size 1.8 1.8)", body)

    def test_pcb_variant_adds_only_the_layer_label(self) -> None:
        schematic, pcb = (path.read_text(encoding="utf-8") for path in AS1100_SHEETS)
        layer = re.search(r'\t\(tbtext "\$\{LAYER\}"\n.*?\n\t\)\n', pcb, re.S).group(0)
        self.assertEqual(schematic, pcb.replace(layer, "", 1))

    def test_no_deprecated_or_project_specific_identifiers(self) -> None:
        for path in AS1100_SHEETS:
            text = path.read_text(encoding="utf-8")
            for name in ("${REVISION}", "${BOARD_NAME}", "${PROJECT_NAME}", "${DRAWING_TYPE}", "COUGAR"):
                self.assertNotIn(name, text, path.name)

    def test_identity_variables_reach_kicad_and_kibot(self) -> None:
        from boardwright.config import BoardwrightConfig

        config = BoardwrightConfig(
            root=ROOT,
            project={"documents": {"assembly": {"revision": "B"}, "fabrication": {"revision": "C"}}},
            branches={},
            legal={},
            revision_history={},
        )
        variables = config.drawing_variables("schematic")
        self.assertEqual(("B", "C"), (variables["ASSEMBLY_REVISION"], variables["FABRICATION_REVISION"]))
        preflight = (ROOT / "boardwright_resources/kibot/yaml/kibot_pre_set_text_variables.yaml").read_text(
            encoding="utf-8"
        )
        main = (ROOT / "boardwright_resources/kibot/yaml/kibot_main.yaml").read_text(encoding="utf-8")
        for key in ("ASSEMBLY_REVISION", "FABRICATION_REVISION"):
            self.assertRegex(preflight, rf"variable: '{key}'\s+text: '@{key}@'")
            self.assertIn(f"      {key}: @{key}@", main)
            self.assertRegex(main, rf"(?m)^  {key}: ")

    def test_template_project_renders_the_external_as1100_pair(self) -> None:
        project = json.loads((ROOT / "boardwright.kicad_pro").read_text(encoding="utf-8"))
        self.assertEqual(
            "${KIPRJMOD}/Templates/Boardwright_Template_AS1100.kicad_wks",
            project["schematic"]["page_layout_descr_file"],
        )
        self.assertEqual(
            "${KIPRJMOD}/Templates/Boardwright_Template_PCB_AS1100_A3.kicad_wks",
            project["pcbnew"]["page_layout_descr_file"],
        )


if __name__ == "__main__":
    unittest.main()
