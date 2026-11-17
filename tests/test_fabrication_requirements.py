# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Regression for audit finding F17: fabrication notes understated requirements.

The 0.1.4 candidate for the ci-test board stated TRACE TO TRACE 0.600 mm (a
fiducial's local clearance override), MIN. HOLE (PTH) 0.600 mm and ANNULAR RING
0.200 mm (vias ignored) where the approved 0.1.3 notes and the board require
0.200 / 0.300 / 0.150 mm.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from boardwright import kicad_tables
from boardwright.config import DEFAULT_CONFIG_FILES
from boardwright.fabrication_requirements import (
    DECLARED_MANUFACTURING_KEYS,
    FabricationRequirementError,
    derive_project_requirements,
    derive_requirements,
    undeclared_manufacturing_keys,
)

RULES = {
    "min_clearance": 0.2,
    "min_track_width": 0.2,
    "min_hole_clearance": 0.254,
    "min_copper_edge_clearance": 0.25,
    "min_hole_to_hole": 0.254,
}


def project(rules: dict | None = None, classes: list | None = None) -> str:
    return json.dumps(
        {
            "board": {"design_settings": {"rules": RULES if rules is None else rules}},
            "net_settings": {"classes": [{"name": "Default", "clearance": 0.2}] if classes is None else classes},
        }
    )


FIDUCIAL = """
  (footprint "Fiducial:Fiducial_1mm_Mask2mm" (layer "F.Cu")
    (property "Reference" "FID1")
    (pad "" smd circle (at 0 0) (size 1 1) (layers "F.Cu" "F.Mask")
      (solder_mask_margin 0.5) (clearance 0.6)))
"""
HEADER = """
  (footprint "Connector:Header" (layer "F.Cu")
    (property "Reference" "J1")
    (pad "1" thru_hole circle (at 0 0) (size 1 1) (drill 0.6) (layers "*.Cu" "*.Mask") (clearance 0))
    (pad "" np_thru_hole circle (at 2 0) (size 0.65 0.65) (drill 0.65) (layers "*.Cu" "*.Mask")))
"""
VIAS = "".join(
    f'\n  (via (at {x} 5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1))' for x in range(3)
)
TRACKS = '\n  (segment (start 0 0) (end 5 0) (width 0.2) (layer "F.Cu") (net 1))'


def board(*parts: str, thickness: str = "(general (thickness 1.6))") -> str:
    return "(kicad_pcb " + thickness + "".join(parts) + "\n)"


class DerivationTests(unittest.TestCase):
    def test_fiducial_local_clearance_is_not_the_board_clearance(self) -> None:
        # Old heuristic: min positive "(clearance x)" token = 0.6 from the fiducial.
        result = derive_requirements(board(FIDUCIAL, HEADER, VIAS, TRACKS), project())
        self.assertEqual("0.200", result.trace_to_trace.text())

    def test_vias_count_for_minimum_hole_and_annular_ring(self) -> None:
        # Old heuristic read pads only: PTH 0.600, ring 0.200.
        result = derive_requirements(board(FIDUCIAL, HEADER, VIAS, TRACKS), project())
        self.assertEqual("0.300", result.min_pth.text())
        self.assertIn("3 via(s)", result.min_pth.source)
        self.assertEqual("0.150", result.annular_ring.text())
        self.assertEqual("0.650", result.min_npth.text())

    def test_reference_board_matches_approved_values(self) -> None:
        result = derive_requirements(board(FIDUCIAL, HEADER, VIAS, TRACKS), project())
        self.assertEqual(
            {
                "trace_width": "0.200",
                "trace_to_trace": "0.200",
                "min_pth": "0.300",
                "min_npth": "0.650",
                "annular_ring": "0.150",
                "copper_to_hole": "0.254",
                "copper_to_edge": "0.250",
                "hole_to_hole": "0.254",
                "thickness": "1.600",
            },
            {name: value.text() for name, value in result.as_dict().items()},
        )

    def test_rule_values_come_from_the_project_not_constants(self) -> None:
        rules = dict(RULES, min_hole_clearance=0.3, min_copper_edge_clearance=0.4, min_hole_to_hole=0.5)
        result = derive_requirements(board(TRACKS), project(rules))
        self.assertEqual(("0.300", "0.400", "0.500"), (
            result.copper_to_hole.text(), result.copper_to_edge.text(), result.hole_to_hole.text()))

    def test_netclass_below_board_minimum_is_floored(self) -> None:
        result = derive_requirements(board(TRACKS), project(classes=[{"name": "Tight", "clearance": 0.1}]))
        self.assertEqual("0.200", result.trace_to_trace.text())
        self.assertIn("floors", result.trace_to_trace.source)

    def test_smaller_netclass_or_custom_rule_lowers_clearance_to_its_value(self) -> None:
        rules = dict(RULES, min_clearance=0.1)
        classes = [{"name": "Default", "clearance": 0.2}, {"name": "Fine", "clearance": 0.15}]
        self.assertEqual("0.150", derive_requirements(board(TRACKS), project(rules, classes)).trace_to_trace.text())
        dru = '(version 1)\n# comment\n(rule "fine" (constraint clearance (min 0.12mm)) (condition "A.NetClass == \'X\'"))'
        self.assertEqual("0.120", derive_requirements(board(TRACKS), project(rules), dru).trace_to_trace.text())

    def test_slot_and_offset_reduce_annular_ring(self) -> None:
        pad = """
  (footprint "X" (layer "F.Cu") (property "Reference" "J2")
    (pad "1" thru_hole oval (at 0 0) (size 1.2 2.0) (drill oval 0.8 1.4 (offset 0 0.1)) (layers "*.Cu")))
"""
        result = derive_requirements(board(pad, TRACKS), project())
        # x: (1.2-0.8)/2 = 0.2; y: (2.0-1.4)/2 - 0.1 = 0.2; slot width 0.8 is the min drill.
        self.assertEqual("0.200", result.annular_ring.text())
        self.assertEqual("0.800", result.min_pth.text())

    def test_padstack_inner_layer_size_is_considered(self) -> None:
        via = '\n  (via (at 0 0) (size 0.6) (drill 0.3) (padstack (mode front_inner_back) (layer "Inner" (size 0.5))) (net 1))'
        self.assertEqual("0.100", derive_requirements(board(via, TRACKS), project()).annular_ring.text())

    def test_no_holes_reads_not_applicable(self) -> None:
        result = derive_requirements(board(TRACKS), project())
        self.assertEqual(("N/A", "N/A", "N/A"), (
            result.min_pth.text(), result.min_npth.text(), result.annular_ring.text()))

    def test_no_routed_tracks_states_the_board_rule(self) -> None:
        result = derive_requirements(board(), project())
        self.assertEqual("0.200", result.trace_width.text())
        self.assertIn("min_track_width", result.trace_width.source)


class FailClosedTests(unittest.TestCase):
    def assertFails(self, pcb: str, proj: str, *needles: str, dru: str | None = None) -> None:
        with self.assertRaises(FabricationRequirementError) as raised:
            derive_requirements(pcb, proj, dru)
        for needle in needles:
            self.assertIn(needle, str(raised.exception))

    def test_missing_design_rules(self) -> None:
        self.assertFails(board(TRACKS), json.dumps({}), "min_clearance", "min_hole_to_hole", "net class")

    def test_missing_thickness(self) -> None:
        self.assertFails(board(TRACKS, thickness=""), project(), "thickness")

    def test_track_below_board_minimum(self) -> None:
        narrow = '\n  (segment (start 0 0) (end 1 0) (width 0.15) (layer "F.Cu") (net 1))'
        self.assertFails(board(narrow), project(), "0.150 mm is below the board minimum 0.200")

    def test_plated_pad_without_drill_or_ring(self) -> None:
        nodrill = '\n  (footprint "X" (property "Reference" "U1") (pad "1" thru_hole circle (size 1 1) (layers "*.Cu")))'
        self.assertFails(board(nodrill, TRACKS), project(), "U1.1", "without a readable drill")
        noring = '\n  (footprint "X" (property "Reference" "U2") (pad "1" thru_hole circle (size 0.6 0.6) (drill 0.6) (layers "*.Cu")))'
        self.assertFails(board(noring, TRACKS), project(), "U2.1", "no annular ring")

    def test_unreadable_custom_rule(self) -> None:
        dru = '(version 1)\n(rule "bad" (constraint clearance (min lots)))'
        self.assertFails(board(TRACKS), project(), "unreadable clearance minimum", dru=dru)

    def test_undeclared_manufacturing_requirements(self) -> None:
        self.assertEqual(list(DECLARED_MANUFACTURING_KEYS), undeclared_manufacturing_keys(None))
        declared = {key: None for key in DECLARED_MANUFACTURING_KEYS}
        self.assertEqual([], undeclared_manufacturing_keys(declared))
        declared.pop("manufacturing_standard")
        self.assertEqual(["manufacturing_standard"], undeclared_manufacturing_keys(declared))


PROJECT_YAML = """project:
  id: DEMO
  name: Demo Project
  pcba_name: DEMO-100A
  pcb_name: DEMO-100
  board_revision: A
  company: Demo Co
  designer: Demo Designer
manufacturing:
{manufacturing}
"""
DECLARED = """  manufacturing_standard: IPC-6012A Class 2
  core_material: ''
  flammability_rating: UL94V-0
  tg_rating: 170 C
  rohs_pb_free: true
  halogen_free: true
  tented_vias: true
  silkscreen_enabled: true
  conformal_coating: null"""


class RenderedNotesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="bw-fabreq-"))
        (self.root / "demo.kicad_pro").write_text(project(), encoding="utf-8")
        (self.root / "demo.kicad_pcb").write_text(
            board('\n  (gr_rect (start 0 0) (end 40 25) (layer "Edge.Cuts"))', FIDUCIAL, HEADER, VIAS, TRACKS),
            encoding="utf-8",
        )
        (self.root / ".boardwright").mkdir()
        for name, text in DEFAULT_CONFIG_FILES.items():
            if name != "project.yaml":
                (self.root / ".boardwright" / name).write_text(text, encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _render(self, manufacturing: str) -> str:
        (self.root / ".boardwright" / "project.yaml").write_text(
            PROJECT_YAML.format(manufacturing=manufacturing), encoding="utf-8"
        )
        kicad_tables._write_manufacturing_notes(self.root)
        return (self.root / "Manufacturing/Fabrication/demo-fabrication_notes.txt").read_text(encoding="utf-8")

    def test_rendered_notes_state_board_requirements(self) -> None:
        notes = self._render(DECLARED)
        self.assertRegex(notes, r"TRACE TO TRACE\s+0\.200 mm")
        self.assertRegex(notes, r"MIN\. HOLE \(PTH\)\s+0\.300 mm")
        self.assertRegex(notes, r"ANNULAR RING\s+0\.150 mm")
        self.assertRegex(notes, r"COPPER TO HOLE\s+0\.254 mm")
        self.assertIn("FABRICATE PER IPC-6012A CLASS 2.", notes)
        # Declared empty / null: the statement is omitted, not defaulted.
        self.assertNotIn("CORE MATERIAL", notes)
        self.assertNotIn("CONFORMAL COATING", notes)

    def test_lettered_items_wrap_under_their_text(self) -> None:
        notes = self._render(DECLARED)
        lines = notes.splitlines()
        index = next(i for i, line in enumerate(lines) if "EQUIVALENT MATERIAL" in line)
        self.assertRegex(lines[index], r"^\t[A-Z]\.\t")
        self.assertTrue(lines[index + 1].startswith("\t\t"), repr(lines[index + 1]))
        self.assertFalse(re.search(r"[A-Z]\. {2,}", notes))

    def test_controlled_notes_are_written_with_lf_on_every_host(self) -> None:
        self._render(DECLARED)
        for relative in ("Manufacturing/Fabrication/demo-fabrication_notes.txt",
                         "Manufacturing/Assembly/demo-assembly_notes.txt"):
            self.assertNotIn(b"\r", (self.root / relative).read_bytes(), relative)

    def test_undeclared_requirements_stop_rendering(self) -> None:
        without_standard = "\n".join(
            line for line in DECLARED.splitlines() if "manufacturing_standard" not in line
        )
        with self.assertRaises(FabricationRequirementError) as raised:
            self._render(without_standard)
        self.assertIn("manufacturing.manufacturing_standard", str(raised.exception))
        self.assertFalse((self.root / "Manufacturing/Fabrication/demo-fabrication_notes.txt").exists())


@unittest.skipUnless(
    os.environ.get("BOARDWRIGHT_REFERENCE_BOARD"),
    "set BOARDWRIGHT_REFERENCE_BOARD to a project directory to check it against approved values",
)
class ReferenceBoardTests(unittest.TestCase):
    """Opt-in check against a real board kept outside this repository.

    BOARDWRIGHT_REFERENCE_BOARD=<dir>; optional BOARDWRIGHT_REFERENCE_EXPECT as
    name=value pairs, e.g. "trace_to_trace=0.200,min_pth=0.300,annular_ring=0.150".
    """

    def test_reference_board(self) -> None:
        root = Path(os.environ["BOARDWRIGHT_REFERENCE_BOARD"])
        pcbs = sorted(root.glob("*.kicad_pcb"))
        self.assertEqual(1, len(pcbs))
        result = derive_project_requirements(root, pcbs[0])
        expected = dict(
            pair.split("=", 1)
            for pair in os.environ.get("BOARDWRIGHT_REFERENCE_EXPECT", "").split(",")
            if "=" in pair
        )
        actual = {name: value.text() for name, value in result.as_dict().items()}
        for name, value in expected.items():
            self.assertEqual(value, actual[name], f"{name}: {result.as_dict()[name].source}")



class UpdateMergeTests(unittest.TestCase):
    """`boardwright update` must not fill undeclared requirements from defaults (F18)."""

    def test_update_leaves_undeclared_requirements_missing(self) -> None:
        import yaml

        from boardwright.managed_project import _migrated_config_bytes

        root = Path(tempfile.mkdtemp(prefix="bw-update-merge-"))
        try:
            (root / ".boardwright").mkdir()
            (root / ".boardwright" / "project.yaml").write_text(
                "project:\n  name: Demo\nmanufacturing:\n  manufacturing_standard: IPC-6012A Class 2\n"
                "  conformal_coating: null\n",
                encoding="utf-8",
            )
            merged = yaml.safe_load(_migrated_config_bytes(root, ".boardwright/project.yaml"))
        finally:
            shutil.rmtree(root, ignore_errors=True)
        manufacturing = merged["manufacturing"]
        self.assertEqual("IPC-6012A Class 2", manufacturing["manufacturing_standard"])
        self.assertIsNone(manufacturing["conformal_coating"])  # deliberate omission kept
        for key in set(DECLARED_MANUFACTURING_KEYS) - {"manufacturing_standard", "conformal_coating"}:
            self.assertNotIn(key, manufacturing)  # reported by validate, not defaulted
        self.assertIn("impedance_table", manufacturing)  # non-requirement defaults still merge



class SingleWriterTests(unittest.TestCase):
    """Fabrication notes have one writer: boardwright.kicad_tables (F17)."""

    def test_kibot_configuration_defines_no_notes_output(self) -> None:
        kibot_dir = Path(__file__).resolve().parents[1] / "boardwright_resources" / "kibot" / "yaml"
        main = (kibot_dir / "kibot_main.yaml").read_text(encoding="utf-8")
        self.assertNotIn("kibot_out_txt_report.yaml", main)
        self.assertNotIn("NOTES_OUTPUT", main)
        self.assertNotRegex(main, r"(?m)^\s*OUTPUT_ID:\s*(fabrication|assembly)_notes")
        for path in kibot_dir.glob("*.yaml"):
            if path.name == "kibot_out_txt_report.yaml":
                continue  # unreferenced (asserted above); kept for provenance
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"(?m)^\s*output_id:\s*_?notes", path.name)


if __name__ == "__main__":
    unittest.main()
