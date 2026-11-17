# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Release identity / change-impact regression tests (PCB rev vs schematic rev vs release)."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from boardwright.config import load_config
from boardwright.errors import BoardwrightError
from boardwright.fingerprint import fingerprint_working_tree, schematic_pcb_parity, WorkingTreeReader
from boardwright.impact import assess_release_impact, format_impact, parse_change_classes
from boardwright.release import build_release_plan, prepare_release, remote_tag_state, validate_release_plan
from boardwright.release_ledger import (
    append_record,
    latest_baseline,
    load_ledger,
    parse_ledger_text,
    verify_ledger_history,
)

PCB = """(kicad_pcb
\t(version {pcb_version})
\t(generator "pcbnew")
\t(generator_version "9.0")
\t(general (thickness 1.6))
\t(paper "A4")
\t(title_block (rev "${{DRAWING_REVISION}}") (date "{date}"))
\t(layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user) (37 "F.SilkS" user) (49 "F.Fab" user))
\t(setup (pad_to_mask_clearance 0))
\t(net 0 "")
\t(net 1 "GND")
\t(net 2 "{net}")
\t(footprint "Capacitor_SMD:C_0603"
\t\t(layer "F.Cu")
\t\t(uuid "{uuid}")
\t\t(at 10 10)
\t\t(property "Reference" "C1" (at 0 -1 0) (layer "F.SilkS") (uuid "p1"))
\t\t(property "Value" "{value}" (at 0 1 0) (layer "F.Fab") (uuid "p2"))
\t\t(property "MPN" "{mpn}" (at 0 0 0) (layer "F.Fab") (hide yes) (uuid "p3"))
\t\t(attr smd)
\t\t(pad "1" smd rect (at -0.8 0) (size 0.9 1) (layers "F.Cu" "F.Paste" "F.Mask") (net 2 "{net}") (uuid "a"))
\t\t(pad "2" smd rect (at 0.8 0) (size 0.9 1) (layers "F.Cu" "F.Paste" "F.Mask") (net 1 "GND") (uuid "b"))
\t)
\t(segment (start 0 0) (end 5 0) (width {width}) (layer "F.Cu") (net 2) (uuid "s1"))
\t(gr_rect (start 0 0) (end 20 20) (stroke (width 0.05) (type default)) (fill no) (layer "Edge.Cuts") (uuid "e"))
)
"""

SCH = """(kicad_sch (version 20250114) (generator "eeschema") (generator_version "9.0") (uuid "root") (paper "A4")
  (title_block (rev "${{DRAWING_REVISION}}"))
  (lib_symbols)
  (text "{note}" (at 1 1 0) (uuid "t"))
  (symbol (lib_id "Device:C") (at 50 50 0) (unit 1) (in_bom yes) (on_board yes) (dnp no) (uuid "sym")
    (property "Reference" "C1" (at 0 0 0))
    (property "Value" "{value}" (at 0 0 0))
    (property "Footprint" "Capacitor_SMD:C_0603" (at 0 0 0))
    (property "MPN" "{mpn}" (at 0 0 0))
    (instances (project "t" (path "/root" (reference "C1") (unit 1))))
  )
)
"""

PROJECT = """project:
  name: Test Board
  board_revision: {pcb_rev}
documents:
  schematic:
    title: Schematic
    type: SCHEMATIC
    revision: {sch_rev}
    revision_scheme: board_minor
    drawn_by: RH
  assembly:
    title: Assembly Drawing
    type: ASSEMBLY
    revision: {asm_rev}
  fabrication:
    title: PCB Fabrication Drawing
    type: FABRICATION
    revision: {fab_rev}
manufacturing:
  core_material: {material}
  assembly_notes: '{asm_notes}'
"""

DEFAULTS = dict(
    pcb_version="20241229",
    date="2024-01-01",
    net="VIN",
    uuid="u1",
    value="10u",
    mpn="GRM188",
    width="0.25",
    note="NOTE",
    pcb_rev="A",
    sch_rev="A.0",
    asm_rev="A",
    fab_rev="A",
    material="FR-4",
    asm_notes="",
)



def _rmtree(path: Path) -> None:
    """Remove a test workspace, including Git's read-only object files on Windows."""
    import os
    import stat

    def _retry(function, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        function(target)

    if path.exists():
        shutil.rmtree(path, onexc=_retry)

class Project:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.state = dict(DEFAULTS)
        _git(root, "init", "-b", "main")
        meta = root / ".boardwright"
        meta.mkdir()
        (root / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n", encoding="utf-8")
        (meta / "branches.yaml").write_text("branches:\n  release: main\n", encoding="utf-8")
        (meta / "legal.yaml").write_text("boardwright: {}\n", encoding="utf-8")
        (meta / "revision_history.yaml").write_text("revision_history:\n  slots: 1\n", encoding="utf-8")
        (meta / "document_revisions.yaml").write_text("document_revisions: {}\n", encoding="utf-8")
        self.write()
        self.commit("init")

    def write(self, **changes: str) -> None:
        self.state.update(changes)
        s = self.state
        (self.root / "board.kicad_pcb").write_text(PCB.format(**s), encoding="utf-8")
        (self.root / "board.kicad_sch").write_text(SCH.format(**s), encoding="utf-8")
        project = self.root / ".boardwright" / "project.yaml"
        project.write_text(PROJECT.format(**s), encoding="utf-8")

    def set_revisions(self, **revisions: str) -> None:
        # Revision identities in project.yaml are edited deliberately by engineers.
        self.state.update(revisions)
        import yaml

        path = self.root / ".boardwright" / "project.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if "pcb_rev" in revisions:
            data["project"]["board_revision"] = revisions["pcb_rev"]
        for key, doc in (("asm_rev", "assembly"), ("fab_rev", "fabrication"), ("sch_rev", "schematic")):
            if key in revisions:
                data["documents"][doc]["revision"] = revisions[key]
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    def commit(self, message: str) -> None:
        _git(self.root, "add", "-A")
        _git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", message)

    def release(self, version: str, schematic_revision: str, classes: str = "", justification: str = "x") -> None:
        prepare_release(
            load_config(self.root),
            version,
            allow_dirty=True,
            dry_run=False,
            drawing_revision=schematic_revision,
            revision_description=justification,
            change_classes=classes,
            justification=justification,
            check_remote=False,
        )
        self.commit(f"release: prepare {version}")
        _git(self.root, "tag", version)

    def assess(self, version: str, classes: str = "", schematic_revision: str = "", justification: str = "fix"):
        return assess_release_impact(
            load_config(self.root),
            version,
            change_classes=classes,
            justification=justification,
            schematic_revision=schematic_revision,
        )


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


class RevisionModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="bw-revmodel-"))
        self.project = Project(self.tmp)

    def tearDown(self) -> None:
        _rmtree(self.tmp)

    def _released_a(self) -> None:
        self.project.release("1.0.0", "A.0")

    # 1
    def test_initial_release_records_pcb_a_and_schematic_a0(self) -> None:
        self._released_a()
        (record,) = load_ledger(self.tmp)
        self.assertEqual(("1.0.0", "A", "A.0"), (record.version, record.pcb_revision, record.schematic_revision))
        self.assertEqual(("A/1", "initial"), (record.fabrication_baseline.id, record.fabrication_baseline.status))
        self.assertEqual(40, len(record.source_commit))

    # 2 + acceptance criterion
    def test_documentation_only_correction_keeps_pcb_a_and_reuses_baseline(self) -> None:
        self._released_a()
        self.project.write(note="NOTE: C1 rated 16 V minimum (DFM review)")
        self.project.commit("docs: correct schematic note")
        self.project.release("1.0.1", "A.1", classes="documentation", justification="DFM note correction")

        records = {r.version: r for r in load_ledger(self.tmp)}
        corrected = records["1.0.1"]
        self.assertEqual("A", corrected.pcb_revision)
        self.assertEqual("A.1", corrected.schematic_revision)
        self.assertEqual(("A/1", "reused"), (corrected.fabrication_baseline.id, corrected.fabrication_baseline.status))
        self.assertEqual("reused", corrected.assembly_baseline.status)
        self.assertEqual(records["1.0.0"].fingerprint.pcb_geometry, corrected.fingerprint.pcb_geometry)
        self.assertEqual("A", load_config(self.tmp).board_revision)

    # 3
    def test_schematic_change_requires_schematic_revision_change(self) -> None:
        self._released_a()
        self.project.write(note="changed")
        assessment = self.project.assess("1.0.1", "documentation", "A.0")
        self.assertTrue(any("schematic revision must change" in p for p in assessment.problems))
        ok = self.project.assess("1.0.1", "documentation", "A.1")
        self.assertEqual((), ok.problems)
        self.assertEqual(("schematic",), ok.detected)

    def test_title_block_and_formatting_changes_are_not_design_changes(self) -> None:
        self._released_a()
        before = fingerprint_working_tree(self.tmp)
        self.project.write(date="2026-11-17", uuid="u-regenerated")
        pcb = self.tmp / "board.kicad_pcb"
        text = pcb.read_text(encoding="utf-8").replace("\t", "    ")
        text = text.replace("(pad_to_mask_clearance 0)", "(pad_to_mask_clearance 0) (grid_origin 5 5)")
        pcb.write_text(text, encoding="utf-8")
        after = fingerprint_working_tree(self.tmp)
        self.assertEqual(before.pcb_geometry, after.pcb_geometry)
        self.assertEqual(before.assembly, after.assembly)

    # 4
    def test_modified_pcb_cannot_keep_revision_a(self) -> None:
        self._released_a()
        self.project.write(width="0.3")
        assessment = self.project.assess("1.0.1", "fabrication,documentation", "A.1")
        self.assertIn("pcb_geometry", assessment.detected)
        self.assertTrue(any("PCB revision is still A" in p for p in assessment.problems))
        undeclared = self.project.assess("1.0.1", "documentation", "A.1")
        self.assertTrue(any("declare the `fabrication`" in p for p in undeclared.problems))

    # 5
    def test_bom_change_is_not_classified_as_documentation_only(self) -> None:
        self._released_a()
        self.project.write(mpn="CL10A106")
        assessment = self.project.assess("1.0.1", "documentation", "A.1")
        self.assertIn("assembly", assessment.detected)
        self.assertNotIn("pcb_geometry", assessment.detected)
        self.assertTrue(any("declare the `assembly`" in p for p in assessment.problems))
        self.assertTrue(any("assembly drawing revision must change" in p for p in assessment.problems))
        self.project.set_revisions(asm_rev="B")
        ok = self.project.assess("1.0.1", "documentation,assembly", "A.1")
        self.assertEqual((), ok.problems)
        self.assertEqual("reused", ok.fabrication_baseline.status)
        self.assertEqual("new", ok.assembly_baseline.status)

    def test_value_change_changes_assembly_not_geometry(self) -> None:
        before = fingerprint_working_tree(self.tmp)
        self.project.write(value="22u")
        after = fingerprint_working_tree(self.tmp)
        self.assertEqual(before.pcb_geometry, after.pcb_geometry)
        self.assertNotEqual(before.assembly, after.assembly)

    def test_net_rename_reissues_fabrication_package_under_same_pcb_revision(self) -> None:
        self._released_a()
        self.project.write(net="VIN_5V", note="renamed")
        assessment = self.project.assess("1.0.1", "documentation", "A.1")
        self.assertEqual({"pcb_netnames", "schematic"}, set(assessment.detected))
        self.assertTrue(any("fabrication drawing revision must change" in p for p in assessment.problems))
        self.project.set_revisions(fab_rev="B")
        ok = self.project.assess("1.0.1", "documentation,fabrication", "A.1")
        self.assertEqual((), ok.problems)
        self.assertEqual(("A/2", "reissued"), (ok.fabrication_baseline.id, ok.fabrication_baseline.status))
        self.assertEqual("A", ok.proposed.pcb_revision)

    def test_fabrication_spec_change_is_detected(self) -> None:
        self._released_a()
        self.project.write(material="FR-4 High Tg")
        assessment = self.project.assess("1.0.1", "documentation", "A.0")
        self.assertEqual(("fabrication_spec",), assessment.detected)
        self.assertFalse(assessment.ok)

    # 6
    def test_deliberate_pcb_revision_b(self) -> None:
        self._released_a()
        self.project.write(width="0.3")
        self.project.set_revisions(pcb_rev="B", sch_rev="B.0")
        self.project.commit("pcb: widen VIN track")
        self.project.release("1.1.0", "B.0", classes="fabrication,electrical", justification="Widen VIN track")
        record = {r.version: r for r in load_ledger(self.tmp)}["1.1.0"]
        self.assertEqual(("B", "B.0"), (record.pcb_revision, record.schematic_revision))
        self.assertEqual(("B/1", "new"), (record.fabrication_baseline.id, record.fabrication_baseline.status))

    def test_pcb_revision_change_without_physical_change_is_rejected(self) -> None:
        self._released_a()
        self.project.set_revisions(pcb_rev="B", sch_rev="B.0")
        assessment = self.project.assess("1.0.1", "documentation", "B.0")
        self.assertTrue(any("physical design is unchanged" in p for p in assessment.problems))

    def test_pcb_revision_letter_cannot_be_reused_for_other_geometry(self) -> None:
        self._released_a()
        self.project.write(width="0.3")
        self.project.set_revisions(pcb_rev="B", sch_rev="B.0")
        self.project.commit("b")
        self.project.release("1.1.0", "B.0", classes="fabrication", justification="b")
        self.project.write(width="0.4")
        self.project.set_revisions(pcb_rev="A", sch_rev="A.2")
        assessment = self.project.assess("1.2.0", "fabrication", "A.2")
        self.assertTrue(any("cannot be reused" in p for p in assessment.problems))

    def test_board_minor_scheme_enforced(self) -> None:
        assessment = self.project.assess("1.0.0", "", "B.0")
        self.assertTrue(any("board_minor" in p for p in assessment.problems))

    def test_kicad_format_change_requires_tooling_class(self) -> None:
        self._released_a()
        self.project.write(pcb_version="20250901")
        assessment = self.project.assess("1.0.1", "documentation", "A.0")
        self.assertEqual((), assessment.detected)
        self.assertTrue(any("`tooling`" in p for p in assessment.problems))
        ok = self.project.assess("1.0.1", "tooling", "A.0")
        self.assertEqual((), ok.problems)
        self.assertEqual("reused", ok.fabrication_baseline.status)

    def test_schematic_pcb_parity_blocks_unsynchronised_value(self) -> None:
        self.project.write(value="10u")
        sch = self.tmp / "board.kicad_sch"
        sch.write_text(sch.read_text(encoding="utf-8").replace('"Value" "10u"', '"Value" "22u"'), encoding="utf-8")
        problems = schematic_pcb_parity(WorkingTreeReader(self.tmp))
        self.assertEqual(["C1 value differs: schematic '22u', PCB '10u'."], problems)
        assessment = self.project.assess("1.0.0")
        self.assertFalse(assessment.ok)

    # 7
    def test_duplicate_version_and_tag_rejected(self) -> None:
        self._released_a()
        plan = build_release_plan(load_config(self.tmp), "1.0.0", check_remote=False)
        problems = validate_release_plan(plan, allow_dirty=True)
        self.assertIn("Local tag already exists: 1.0.0", problems)
        self.assertTrue(any("already recorded" in p for p in problems))
        with self.assertRaises(BoardwrightError):
            self.project.release("1.0.0", "A.1", classes="documentation")

    # 8
    def test_failed_network_check_is_not_tag_available(self) -> None:
        config = load_config(self.tmp)
        _git(self.tmp, "remote", "add", "origin", "https://invalid.example.invalid/repo.git")
        failure = subprocess.CompletedProcess([], 128, "", "fatal: unable to access")
        ok = subprocess.CompletedProcess([], 0, "https://x\n", "")
        with mock.patch("boardwright.release.subprocess.run", side_effect=[ok, failure]):
            self.assertEqual(("unknown", "fatal: unable to access"), remote_tag_state(config, "1.0.0"))
        with mock.patch("boardwright.release.subprocess.run", side_effect=[ok, subprocess.TimeoutExpired("git", 20)]):
            self.assertEqual("unknown", remote_tag_state(config, "1.0.0")[0])
        with mock.patch("boardwright.release.remote_tag_state", return_value=("unknown", "fatal: unable to access")):
            plan = build_release_plan(config, "1.0.0")
        self.assertFalse(plan.remote_tag_exists)
        self.assertTrue(any("Could not confirm" in p for p in validate_release_plan(plan, allow_dirty=True)))
        absent = subprocess.CompletedProcess([], 2, "", "")
        with mock.patch("boardwright.release.subprocess.run", side_effect=[ok, absent]):
            self.assertEqual("absent", remote_tag_state(config, "1.0.0")[0])

    # 9
    def test_legacy_project_without_ledger_uses_tag_baseline(self) -> None:
        _git(self.tmp, "tag", "0.9.0")
        baseline = latest_baseline(self.tmp)
        self.assertIsNotNone(baseline)
        assert baseline is not None
        self.assertTrue(baseline.legacy)
        self.assertEqual(("0.9.0", "A", "A.0"), (baseline.version, baseline.pcb_revision, baseline.schematic_revision))
        self.project.write(note="fix")
        assessment = self.project.assess("0.9.1", "documentation", "A.1")
        self.assertEqual((), assessment.problems)
        self.assertTrue(any("predates the release ledger" in r for r in assessment.reviews))

    def test_historic_ledger_entry_tampering_detected(self) -> None:
        self._released_a()
        path = self.tmp / ".boardwright" / "releases.yaml"
        path.write_text(path.read_text(encoding="utf-8").replace("pcb_revision: A", "pcb_revision: Z"), encoding="utf-8")
        self.assertTrue(any("modified after tag 1.0.0" in p for p in verify_ledger_history(self.tmp)))
        with self.assertRaises(BoardwrightError):
            self.project.release("1.0.1", "A.1", classes="documentation")

    # Negative / interruption
    def test_invalid_ledger_metadata_rejected(self) -> None:
        with self.assertRaises(BoardwrightError):
            parse_ledger_text("schema: 1\nreleases:\n  - version: v1\n")
        with self.assertRaises(BoardwrightError):
            parse_ledger_text("schema: 99\nreleases: []\n")
        with self.assertRaises(BoardwrightError):
            parse_change_classes("cosmetic")

    def test_incomplete_approval_leaves_files_untouched(self) -> None:
        self._released_a()
        self.project.write(note="fix")
        changelog = (self.tmp / "CHANGELOG.md").read_text(encoding="utf-8")
        ledger = (self.tmp / ".boardwright" / "releases.yaml").read_text(encoding="utf-8")
        with self.assertRaises(BoardwrightError):
            self.project.release("1.0.1", "A.1", classes="", justification="")
        self.assertEqual(changelog, (self.tmp / "CHANGELOG.md").read_text(encoding="utf-8"))
        self.assertEqual(ledger, (self.tmp / ".boardwright" / "releases.yaml").read_text(encoding="utf-8"))

    def test_interrupted_append_cannot_duplicate(self) -> None:
        self._released_a()
        (record,) = load_ledger(self.tmp)
        with self.assertRaises(BoardwrightError):
            append_record(self.tmp, record)
        self.assertEqual(1, len(load_ledger(self.tmp)))

    # 10
    def test_identities_consistent_across_ledger_variables_and_checklist(self) -> None:
        self._released_a()
        config = load_config(self.tmp)
        (record,) = load_ledger(self.tmp)
        schematic_vars = config.drawing_variables("schematic")
        fab_vars = config.drawing_variables("fabrication")
        self.assertEqual(record.pcb_revision, schematic_vars["BOARD_REVISION"])
        self.assertEqual(record.schematic_revision, schematic_vars["DRAWING_REVISION"])
        self.assertEqual(record.schematic_revision, schematic_vars["SCHEMATIC_REVISION"])
        self.assertEqual(record.fabrication_revision, fab_vars["DRAWING_REVISION"])
        self.assertEqual(record.pcb_revision, fab_vars["PCB_REVISION"])
        lines = format_impact(self.project.assess("1.0.1", "tooling", "A.0"))
        self.assertIn("PCB revision: A (unchanged)", lines)
        self.assertIn("Schematic revision: A.0 (unchanged)", lines)


    def test_tui_checklist_and_cli_dry_run_share_impact(self) -> None:
        from boardwright import cli, tui
        from boardwright.accepted import AcceptedMainState
        import contextlib
        import io
        import os

        self._released_a()
        self.project.write(note="fix")
        self.project.commit("docs")
        ready = AcceptedMainState(state="ready", workflow="main-outputs.yaml", expected_sha="a" * 40, message="ok")
        blocked = tui.build_release_checklist(
            load_config(self.tmp), "1.0.1", "RELEASED", "release", "A.0", "Fix note", ready
        )
        self.assertFalse(blocked.can_dispatch)
        self.assertIn("[ ] Impact", tui._format_release_checklist(blocked))
        good = tui.build_release_checklist(
            load_config(self.tmp), "1.0.1", "RELEASED", "release", "A.1", "Fix note", ready,
            change_classes="documentation", justification="DFM note",
        )
        text = tui._format_release_checklist(good)
        self.assertIn("[x] Impact", text)
        self.assertIn("PCB revision: A (unchanged)", text)
        self.assertIn("Fabrication baseline: A/1 (reused)", text)
        self.assertIn(("source_sha", "a" * 40), good.action.fields if good.action else ())

        out = io.StringIO()
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            with contextlib.redirect_stdout(out):
                code = cli.main(["release", "1.0.1", "--allow-dirty", "--drawing-revision", "A.1",
                                 "--change-class", "documentation", "--justification", "DFM note"])
        finally:
            os.chdir(cwd)
        self.assertIn("PCB revision: A (unchanged)", out.getvalue())
        self.assertIn("Fabrication baseline: A/1 (reused)", out.getvalue())
        self.assertEqual(0, code, out.getvalue())


    def test_reused_baseline_outputs_checked_against_previous_tag(self) -> None:
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from boardwright import output_manifest

        gerbers = self.tmp / "Manufacturing" / "Fabrication" / "Gerbers"
        gerbers.mkdir(parents=True)
        (gerbers / "b-F_Cu.gbr").write_text("%TF.CreationDate,2026-01-01*%\nX1Y1D02*\n", encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            output_manifest.main(["write", "--root", str(self.tmp)])
        self._released_a()
        self.project.write(note="fix")
        self.project.commit("docs")
        self.project.release("1.0.1", "A.1", classes="documentation", justification="DFM")
        (gerbers / "b-F_Cu.gbr").write_text("%TF.CreationDate,2026-11-17*%\nX1Y1D02*\n", encoding="utf-8")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(0, output_manifest.main(["check-baseline", "--root", str(self.tmp)]))
        self.assertIn("after normalisation", out.getvalue())
        (gerbers / "b-F_Cu.gbr").write_text("X2Y1D02*\n", encoding="utf-8")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(1, output_manifest.main(["check-baseline", "--root", str(self.tmp)]))


    def test_kicad10_style_net_syntax_has_same_geometry(self) -> None:
        """KiCad 10 may drop the numbered net table; nets resolve by name."""
        before = fingerprint_working_tree(self.tmp)
        pcb = self.tmp / "board.kicad_pcb"
        text = pcb.read_text(encoding="utf-8")
        text = text.replace('\t(net 0 "")\n\t(net 1 "GND")\n\t(net 2 "VIN")\n', "")
        text = text.replace('(net 2 "VIN")', '(net "VIN")').replace('(net 1 "GND")', '(net "GND")')
        text = text.replace("(net 2)", '(net "VIN")').replace("(version 20241229)", "(version 20250901)")
        pcb.write_text(text, encoding="utf-8")
        after = fingerprint_working_tree(self.tmp)
        self.assertEqual(before.pcb_geometry, after.pcb_geometry)
        self.assertEqual(before.pcb_netnames, after.pcb_netnames)
        self.assertNotEqual(before.pcb_format, after.pcb_format)


if __name__ == "__main__":
    unittest.main()
