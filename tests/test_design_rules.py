# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""DRC/ERC results read from generated KiCad reports (F3)."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from boardwright import design_rules


def drc_report(errors: int = 1, warnings: int = 26, excluded: int = 1, total: int | None = None,
               unconnected: list[str] | None = None, footprint_errors: int = 0) -> str:
    """Synthetic report in KiCad 9's layout (no real design data)."""

    lines = ["** Drc report for board.kicad_pcb **", "** Created on 2026-11-20T04:28:24+0000 **", ""]
    lines.append(f"** Found {total if total is not None else errors + warnings + excluded} DRC violations **")
    for i in range(errors):
        lines += [f"[clearance]: Clearance violation {i}", "    ; error", "    @(1.0 mm, 1.0 mm): Track"]
    for i in range(warnings):
        lines += [f"[lib_footprint_mismatch]: Footprint {i} does not match", "    ; warning", "    @(2.0 mm, 2.0 mm): Footprint"]
    for i in range(excluded):
        lines += [f"[silk_overlap]: Silkscreen overlap {i}", "    ; warning (excluded)", "    @(3.0 mm, 3.0 mm): Text"]
    lines += ["", f"** Found {len(unconnected or [])} unconnected pads **"]
    for item in unconnected or []:
        lines += [f"[unconnected_items]: {item}", "    ; error", "    @(4.0 mm, 4.0 mm): Pad"]
    lines += ["", f"** Found {footprint_errors} Footprint errors **", "", "** End of Report **", ""]
    return "\n".join(lines)


ERC_REPORT = """ERC report (2026-11-20T04:28:23+0000, Encoding UTF8)

***** Sheet /
[footprint_link_issues]: The current configuration does not include the footprint library 'X'.
    ; warning
    @(0.4 mm, 2.3 mm): Symbol TP1 [TestPoint]

[footprint_link_issues]: The current configuration does not include the footprint library 'X'.
    ; warning
    @(0.5 mm, 2.4 mm): Symbol TP2 [TestPoint]

[lib_symbol_mismatch]: Symbol 'R' does not match copy in library
    ; warning
    @(1.0 mm, 1.0 mm): Symbol R1

[lib_symbol_mismatch]: Symbol 'C' does not match copy in library
    ; warning
    @(1.0 mm, 2.0 mm): Symbol C1

***** Sheet /CIRCUIT/
 ** ERC messages: 4  Errors 0  Warnings 4
"""


class ParseTests(unittest.TestCase):
    def test_testbench_condition(self) -> None:
        drc = design_rules.parse_drc(drc_report(), "Reports/report_board-drc.rpt")
        self.assertEqual({"status": "parsed", "file": "Reports/report_board-drc.rpt", "violations": 28, "errors": 1,
                          "warnings": 26, "excluded": 1, "other": 0, "unconnected_pads": 0, "footprint_errors": 0}, drc)
        erc = design_rules.parse_erc(ERC_REPORT, "Reports/report_board-erc.rpt")
        self.assertEqual((4, 0, 4, 0), (erc["messages"], erc["errors"], erc["warnings"], erc["excluded"]))
        self.assertNotIn("note", erc)

    def test_unconnected_items_are_not_counted_as_violations(self) -> None:
        drc = design_rules.parse_drc(drc_report(errors=0, warnings=2, excluded=0, unconnected=["a", "b"]))
        self.assertEqual((2, 0, 2, 2), (drc["violations"], drc["errors"], drc["warnings"], drc["unconnected_pads"]))

    def test_inconsistent_listing_is_noted_not_hidden(self) -> None:
        drc = design_rules.parse_drc(drc_report(errors=1, warnings=1, excluded=0, total=5))
        self.assertEqual(5, drc["violations"])
        self.assertIn("counts may be incomplete", drc["note"])

    def test_missing_or_unparseable_is_unknown_never_zero(self) -> None:
        for text in ("", "garbage", "** Drc report for x **\n** End of Report **\n"):
            result = design_rules.parse_drc(text)
            self.assertEqual("unknown", result["status"])
            self.assertNotIn("errors", result)
        self.assertEqual("unknown", design_rules.parse_erc("ERC report\n")["status"])


class CollectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="bw rules "))

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, name: str, text: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_testbench_build(self) -> None:
        self._write("Reports/report_board-drc.rpt", drc_report())
        self._write("Reports/report_board-erc.rpt", ERC_REPORT)
        self._write("kibot_preview.log", "ERROR:1 DRC errors detected (kibot.pre_drc - pre_drc.py:153)\n"
                                         "ERROR:DRC errors: 1 (kibot.gs - gs.py:935)\n")
        results = design_rules.collect(self.root)
        self.assertEqual(1, results["kibot_drc_errors"])
        self.assertIsNone(results["kibot_erc_errors"])  # not reported: unknown, not zero
        self.assertTrue(design_rules.has_errors(results))
        lines = design_rules.summary_lines(results)
        self.assertEqual("DRC (Reports/report_board-drc.rpt): 28 violation(s): 1 error(s), 26 warning(s), 1 excluded; "
                         "0 unconnected pad(s); 0 footprint error(s)", lines[0])
        self.assertEqual("ERC (Reports/report_board-erc.rpt): 4 message(s): 0 error(s), 4 warning(s), 0 excluded", lines[1])
        self.assertEqual("KiBot reported 1 DRC error(s)", lines[2])
        self.assertTrue(design_rules.headline(results).startswith("design-rule errors: DRC 1 error(s)/28 violation(s)"))

    def test_no_reports_is_unknown(self) -> None:
        results = design_rules.collect(self.root)
        self.assertEqual("unknown", results["drc"][0]["status"])
        self.assertEqual("unknown", results["erc"][0]["status"])
        self.assertIsNone(design_rules.has_errors(results))
        self.assertIn("unknown", design_rules.headline(results))
        self.assertIn("DRC: unknown (no DRC report was generated)", design_rules.summary_lines(results))

    def test_clean_design(self) -> None:
        self._write("Reports/report_board-drc.rpt", drc_report(errors=0, warnings=0, excluded=0))
        self._write("Reports/report_board-erc.rpt", " ** ERC messages: 0  Errors 0  Warnings 0\n")
        results = design_rules.collect(self.root)
        self.assertIs(False, design_rules.has_errors(results))
        self.assertTrue(design_rules.headline(results).startswith("no design-rule errors"))

    def test_one_unknown_report_makes_the_whole_result_unknown(self) -> None:
        self._write("Reports/report_board-drc.rpt", drc_report(errors=0, warnings=0, excluded=0))
        self._write("Reports/report_board-erc.rpt", "truncated")
        self.assertIsNone(design_rules.has_errors(design_rules.collect(self.root)))


if __name__ == "__main__":
    unittest.main()
