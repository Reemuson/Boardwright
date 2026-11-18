# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""False-negative regression tests for release change detection.

Each test makes one realistic edit to a released board and checks that the
change is classified into the right domain, so a documentation-only release
cannot silently claim the approved fabrication or assembly baseline.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_revision_model import Project, _git, _rmtree  # noqa: E402

from boardwright.fingerprint import FINGERPRINT_SCHEMA, fingerprint_working_tree  # noqa: E402
from boardwright.impact import format_impact  # noqa: E402
from boardwright.release_ledger import comparable_fingerprint, load_ledger  # noqa: E402

FAB_NOTES = "boardwright_resources/kibot/resources/templates/fabrication_notes.txt"


class ChangeDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="bw-detect-"))
        self.project = Project(self.tmp)
        self.pcb = self.tmp / "board.kicad_pcb"

    def tearDown(self) -> None:
        _rmtree(self.tmp)

    # helpers -----------------------------------------------------------------
    def _edit_pcb(self, old: str, new: str) -> None:
        text = self.pcb.read_text(encoding="utf-8")
        self.assertIn(old, text)
        self.pcb.write_text(text.replace(old, new, 1), encoding="utf-8")

    def _add_board_item(self, item: str) -> None:
        text = self.pcb.read_text(encoding="utf-8").rstrip()
        assert text.endswith(")")
        self.pcb.write_text(text[:-1] + "\t" + item + "\n)\n", encoding="utf-8")

    def _domains_after(self, edit) -> tuple[str, ...]:
        before = fingerprint_working_tree(self.tmp)
        edit()
        after = fingerprint_working_tree(self.tmp)
        from boardwright.fingerprint import changed_domains

        return changed_domains(before, after)

    def _release_initial(self) -> None:
        self.project.release("1.0.0", "A.0", justification="Initial")

    # bare board -------------------------------------------------------------
    def test_board_thickness_is_geometry(self) -> None:
        self.assertEqual(
            ("pcb_geometry",),
            self._domains_after(lambda: self._edit_pcb("(general (thickness 1.6))", "(general (thickness 0.8))")),
        )

    def test_pad_size_is_geometry_not_assembly(self) -> None:
        domains = self._domains_after(lambda: self._edit_pcb("(size 0.9 1) (layers", "(size 1.0 1) (layers"))
        self.assertEqual(("pcb_geometry",), domains)

    def test_project_text_variable_on_silkscreen_is_geometry(self) -> None:
        (self.tmp / "board.kicad_pro").write_text(json.dumps({"text_variables": {"BOARD_MARK": "X1"}}), encoding="utf-8")
        self._add_board_item('(gr_text "${BOARD_MARK}" (at 5 5 0) (layer "F.SilkS") (uuid "m1"))')

        def edit() -> None:
            (self.tmp / "board.kicad_pro").write_text(
                json.dumps({"text_variables": {"BOARD_MARK": "X2"}}), encoding="utf-8"
            )

        self.assertEqual(("pcb_geometry",), self._domains_after(edit))

    def test_pcb_revision_on_silkscreen_follows_board_revision(self) -> None:
        self._add_board_item('(gr_text "REV ${PCB_REVISION}" (at 5 5 0) (layer "F.SilkS") (uuid "m1"))')
        self.assertIn("pcb_geometry", self._domains_after(lambda: self.project.set_revisions(pcb_rev="B")))

    def test_reference_variable_in_silkscreen_follows_annotation(self) -> None:
        self._edit_pcb(
            '(property "Reference" "C1" (at 0 -1 0) (layer "F.SilkS") (uuid "p1"))',
            '(property "Reference" "C1" (at 0 -1 0) (layer "F.Fab") (hide yes) (uuid "p1"))\n'
            '\t\t(fp_text user "${REFERENCE}" (at 0 -1 0) (layer "F.SilkS") (uuid "p4"))',
        )
        domains = self._domains_after(
            lambda: self._edit_pcb('(property "Reference" "C1"', '(property "Reference" "C9"')
        )
        self.assertIn("pcb_geometry", domains)
        self.assertIn("assembly", domains)

    def test_release_version_on_silkscreen_blocks_documentation_only_reuse(self) -> None:
        self._add_board_item('(gr_text "V${RELEASE_VERSION}" (at 5 5 0) (layer "B.SilkS") (uuid "m1"))')
        self.project.commit("add marking")
        self._release_initial()
        assessment = self.project.assess("1.0.1", "documentation", "A.0", "Tooling-free re-release")
        self.assertIn("release_marking", assessment.detected)
        self.assertNotEqual("reused", assessment.fabrication_baseline.status)
        self.assertTrue(any("declare the `fabrication` change class" in p for p in assessment.problems))
        self.assertTrue(any("${RELEASE_VERSION}" in r for r in assessment.reviews))

    def test_notes_template_is_tooling_not_fabrication_spec(self) -> None:
        template = self.tmp / FAB_NOTES
        template.parent.mkdir(parents=True)
        template.write_text("1. Finish: ENIG.\n", encoding="utf-8")
        before = fingerprint_working_tree(self.tmp)
        template.write_text("1. Finish: HASL.\n", encoding="utf-8")
        after = fingerprint_working_tree(self.tmp)
        from boardwright.fingerprint import changed_domains

        self.assertEqual((), changed_domains(before, after))
        self.assertNotEqual(before.tooling, after.tooling)
        # Line-ending-only differences (Windows checkout vs Git object) are not tooling changes.
        template.write_bytes(b"1. Finish: HASL.\r\n")
        self.assertEqual(after.tooling, fingerprint_working_tree(self.tmp).tooling)

    # F20: a tooling change that alters fabrication outputs -----------------
    def _release_then_change_gerber_options(self) -> None:
        gerber = self.tmp / "boardwright_resources/kibot/yaml/kibot_out_gerber.yaml"
        gerber.parent.mkdir(parents=True)
        gerber.write_text("options:\n  plot_footprint_values: false\n", encoding="utf-8")
        self.project.commit("gerber options")
        self._release_initial()
        gerber.write_text("options:\n  plot_footprint_values: true\n", encoding="utf-8")

    def test_fabrication_output_tooling_change_reissues_fabrication(self) -> None:
        self._release_then_change_gerber_options()
        self.project.set_revisions(fab_rev="B")
        assessment = self.project.assess("1.0.1", "fabrication,tooling", "A.0", "Plot test-point labels")
        self.assertEqual((), assessment.problems, format_impact(assessment))
        self.assertEqual(("reissued", "A/2"), (assessment.fabrication_baseline.status, assessment.fabrication_baseline.id))
        self.assertEqual("A", assessment.proposed.pcb_revision)
        self.assertEqual("reused", assessment.assembly_baseline.status)
        self.assertFalse(assessment.documentation_only)
        self.assertTrue(any("output tooling changed them" in r for r in assessment.reviews))

    def test_fabrication_output_reissue_requires_new_fabrication_drawing_revision(self) -> None:
        self._release_then_change_gerber_options()
        assessment = self.project.assess("1.0.1", "fabrication,tooling", "A.0", "Plot test-point labels")
        self.assertTrue(
            any("fabrication drawing revision must change" in p for p in assessment.problems),
            format_impact(assessment),
        )

    def test_tooling_only_keeps_baseline_and_points_to_reissue(self) -> None:
        # Without `fabrication` the baseline is claimed reused; check-baseline
        # must then prove the outputs identical, and blocks if they are not.
        self._release_then_change_gerber_options()
        assessment = self.project.assess("1.0.1", "tooling", "A.0", "Plot test-point labels")
        self.assertEqual((), assessment.problems, format_impact(assessment))
        self.assertEqual("reused", assessment.fabrication_baseline.status)
        self.assertTrue(any("declare `fabrication` to re-issue" in r for r in assessment.reviews))

    def test_fabrication_without_any_change_still_rejected(self) -> None:
        self._release_initial()
        assessment = self.project.assess("1.0.1", "fabrication", "A.0", "nothing")
        self.assertTrue(any("no fabrication input changed" in p for p in assessment.problems))

    def test_global_plot_definitions_are_tooling(self) -> None:
        main = self.tmp / "boardwright_resources/kibot/yaml/kibot_main.yaml"
        main.parent.mkdir(parents=True)
        main.write_text("definitions:\n  PLOT_VALUES: true\n", encoding="utf-8")
        before = fingerprint_working_tree(self.tmp)
        main.write_text("definitions:\n  PLOT_VALUES: false\n", encoding="utf-8")
        self.assertNotEqual(before.tooling, fingerprint_working_tree(self.tmp).tooling)

    def test_tooling_change_requires_tooling_class(self) -> None:
        template = self.tmp / FAB_NOTES
        template.parent.mkdir(parents=True)
        template.write_text("1. Finish: ENIG.\n", encoding="utf-8")
        self.project.commit("template")
        self._release_initial()
        template.write_text("1. Surface finish: ENIG.\n", encoding="utf-8")
        assessment = self.project.assess("1.0.1", "documentation", "A.0", "Update tooling")
        self.assertTrue(any("declare the `tooling` change class" in p for p in assessment.problems))
        assessment = self.project.assess("1.0.1", "tooling", "A.0", "Update tooling")
        self.assertEqual((), assessment.problems, format_impact(assessment))
        self.assertEqual("reused", assessment.fabrication_baseline.status)

    def test_pin_function_change_is_netname_data(self) -> None:
        self._edit_pcb('(net 2 "{net}") (uuid "a")'.replace("{net}", "VIN"), '(net 2 "VIN") (pinfunction "A") (uuid "a")')
        self.assertEqual(
            ("pcb_netnames",),
            self._domains_after(lambda: self._edit_pcb('(pinfunction "A")', '(pinfunction "K")')),
        )

    # assembly ---------------------------------------------------------------
    def test_footprint_swap_with_identical_pads_is_assembly(self) -> None:
        domains = self._domains_after(
            lambda: self._edit_pcb('(footprint "Capacitor_SMD:C_0603"', '(footprint "MyLib:C_0603_Alt"')
        )
        self.assertEqual(("assembly",), domains)

    def test_rotation_is_assembly_and_geometry(self) -> None:
        domains = self._domains_after(lambda: self._edit_pcb("(at 10 10)", "(at 10 10 90)"))
        # The footprint's visible F.Fab graphics move too (assembly drawing).
        self.assertEqual({"pcb_geometry", "assembly", "pcb_drawings"}, set(domains))

    def test_footprint_dnp_attribute_is_assembly_only(self) -> None:
        self.assertEqual(("assembly",), self._domains_after(lambda: self._edit_pcb("(attr smd)", "(attr smd dnp)")))

    def test_schematic_dnp_flag_is_assembly(self) -> None:
        def edit() -> None:
            path = self.tmp / "board.kicad_sch"
            path.write_text(path.read_text(encoding="utf-8").replace("(dnp no)", "(dnp yes)"), encoding="utf-8")

        self.assertEqual({"assembly", "schematic"}, set(self._domains_after(edit)))

    def test_hidden_bom_field_change_is_assembly(self) -> None:
        self.assertIn("assembly", self._domains_after(lambda: self.project.write(mpn="GRM188R61")))

    # drawings ---------------------------------------------------------------
    def test_fab_drawing_note_requires_drawing_revision(self) -> None:
        self._add_board_item('(gr_text "IMPEDANCE 50R" (at 50 5 0) (layer "Dwgs.User") (uuid "n1"))')
        self.project.commit("note")
        self._release_initial()
        self._edit_pcb('"IMPEDANCE 50R"', '"IMPEDANCE 90R DIFF"')
        assessment = self.project.assess("1.0.1", "documentation", "A.0", "Clarify fab note")
        self.assertEqual(("pcb_drawings",), assessment.detected)
        self.assertTrue(any("drawing revision must change" in p for p in assessment.problems))

        self.project.set_revisions(fab_rev="B")
        assessment = self.project.assess("1.0.1", "documentation", "A.0", "Clarify fab note")
        self.assertEqual((), assessment.problems)
        self.assertEqual("reused", assessment.fabrication_baseline.status)
        self.assertTrue(any("declare `fabrication`" in r for r in assessment.reviews))

    def test_courtyard_and_hidden_fab_text_are_not_drawing_changes(self) -> None:
        self._edit_pcb(
            "\t\t(attr smd)",
            '\t\t(fp_rect (start -1 -1) (end 1 1) (layer "F.CrtYd") (uuid "c"))\n\t\t(attr smd)',
        )
        self.assertEqual((), self._domains_after(lambda: self._edit_pcb("(end 1 1)", "(end 1.2 1.2)")))

    # schema migration -------------------------------------------------------
    def test_older_schema_baseline_is_recomputed_from_tag(self) -> None:
        self._release_initial()
        (record,) = load_ledger(self.tmp)
        old = replace(record, fingerprint=replace(record.fingerprint, schema=1, pcb_geometry="stale"))
        refreshed = comparable_fingerprint(self.tmp, old)
        self.assertEqual(FINGERPRINT_SCHEMA, refreshed.schema)
        self.assertEqual(record.fingerprint.pcb_geometry, refreshed.pcb_geometry)
        # Without tag and source commit the stored value is returned (caller reports it).
        _git(self.tmp, "tag", "-d", "1.0.0")
        orphan = replace(old, source_commit="")
        self.assertEqual("stale", comparable_fingerprint(self.tmp, orphan).pcb_geometry)

    def test_unrecorded_baseline_revisions_are_flagged_for_review(self) -> None:
        import yaml

        path = self.tmp / ".boardwright" / "project.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data.pop("documents")
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        self.project.commit("legacy metadata")
        _git(self.tmp, "tag", "0.9.0")
        assessment = self.project.assess("1.0.0", "documentation", "A.0", "First ledgered release")
        self.assertTrue(assessment.baseline_legacy)
        self.assertTrue(any("no recorded schematic" in r for r in assessment.reviews))

    # declared fabrication requirements (audit F18) ---------------------------
    def _append_manufacturing(self, body: str) -> None:
        import yaml

        project = self.tmp / ".boardwright" / "project.yaml"
        data = yaml.safe_load(project.read_text(encoding="utf-8"))
        manufacturing = data.get("manufacturing") or {}
        self.assertEqual({}, manufacturing)
        manufacturing.update(yaml.safe_load("x:\n" + body)["x"])
        data["manufacturing"] = manufacturing
        project.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    def _release_without_manufacturing(self) -> None:
        import yaml

        project = self.tmp / ".boardwright" / "project.yaml"
        data = yaml.safe_load(project.read_text(encoding="utf-8"))
        data.pop("manufacturing", None)
        project.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        self.project.commit("legacy project without declared requirements")
        self._release_initial()

    def test_newly_declared_requirements_keep_the_baseline_for_output_verification(self) -> None:
        self._release_without_manufacturing()
        self._append_manufacturing("  manufacturing_standard: IPC-6012A Class 2\n  conformal_coating: null\n")
        template = self.tmp / ".boardwright" / "templates" / "fabrication_notes.txt"
        template.parent.mkdir(parents=True)
        template.write_text("@NOTE@\tFABRICATE PER ${manufacturing_standard_cap}.\n", encoding="utf-8")
        assessment = self.project.assess("1.0.1", "documentation", "A.0", "Declare approved requirements")
        self.assertEqual((), assessment.problems, format_impact(assessment))
        self.assertNotIn("fabrication_spec", assessment.detected)
        self.assertEqual("reused", assessment.fabrication_baseline.status)
        self.assertTrue(any("newly declared" in r and "check-baseline" in r for r in assessment.reviews))

    def test_changed_declared_requirement_is_a_fabrication_change(self) -> None:
        self._release_without_manufacturing()
        self._append_manufacturing("  manufacturing_standard: IPC-6012A Class 2\n")
        self.project.commit("declare")
        self.project.release("1.0.1", "A.0", classes="documentation", justification="declare")
        project = self.tmp / ".boardwright" / "project.yaml"
        project.write_text(
            project.read_text(encoding="utf-8").replace("IPC-6012A Class 2", "IPC-6012 Class 3"), encoding="utf-8"
        )
        assessment = self.project.assess("1.0.2", "documentation", "A.0", "Class 3")
        self.assertIn("fabrication_spec", assessment.detected)
        self.assertNotEqual("reused", assessment.fabrication_baseline.status)
        self.assertTrue(any("declare the `fabrication` change class" in p for p in assessment.problems))

    def test_newly_added_non_requirement_setting_is_a_fabrication_change(self) -> None:
        self._release_without_manufacturing()
        self._append_manufacturing("  manufacturing_standard: IPC-6012A Class 2\n  impedance_enabled: true\n")
        assessment = self.project.assess("1.0.1", "documentation", "A.0", "Impedance")
        self.assertIn("fabrication_spec", assessment.detected)
        self.assertNotEqual("reused", assessment.fabrication_baseline.status)

    def test_documentation_only_release_still_reuses_baseline(self) -> None:
        self._release_initial()
        self.project.write(note="CORRECTED NOTE")
        assessment = self.project.assess("1.0.1", "documentation", "A.1", "Correct note")
        self.assertEqual((), assessment.problems, format_impact(assessment))
        self.assertEqual(("schematic",), assessment.detected)
        self.assertEqual("reused", assessment.fabrication_baseline.status)


if __name__ == "__main__":
    unittest.main()
