# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""F20: visible PCB silkscreen text must not be dropped by Gerber plot options.

The static tests mirror KiCad's text-plotting rules; the geometry tests compare
Gerber drawing primitives. The opt-in test at the end plots a real board with
kicad-cli and checks the release options against an unfiltered plot.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from boardwright import silkscreen  # noqa: E402
from boardwright.kibot_config import scalar  # noqa: E402
from boardwright.validation import validate_project  # noqa: E402,F401  (import check)

PCB = """(kicad_pcb (version 20241229) (generator "pcbnew")
\t(footprint "TestPoint:TP" (layer "F.Cu") (at 148.5 99.25)
\t\t(property "Reference" "TP1" (at 0 -2 0) (layer "F.SilkS" knockout) (hide yes))
\t\t(property "Value" "GND" (at 0 1.5 0) (layer "F.Fab") (hide yes))
\t\t(fp_text user "${VALUE}" (at 2.5 0 0) (layer "F.SilkS" knockout))
\t\t(fp_text user "${REFERENCE}" (at 0 2 0) (layer "User.8"))
\t)
\t(footprint "R:R0603" (layer "F.Cu") (at 120 90)
\t\t(property "Reference" "R1" (at 0 -1.4 0) (layer "F.SilkS"))
\t\t(property "Value" "10k" (at 0 1.4 0) (layer "F.Fab"))
\t\t(fp_text user "${REFERENCE}" (at 0 0 0) (layer "F.Fab"))
\t)
\t(footprint "R:R0603" (layer "B.Cu") (at 125 90)
\t\t(property "Reference" "R2" (at 0 -1.4 0) (layer "B.SilkS") (hide yes))
\t\t(property "Value" "1k" (at 0 1.4 0) (layer "B.SilkS") (hide yes))
\t\t(fp_text user "hidden" (at 0 0 0) (layer "B.SilkS") (effects (font (size 1 1)) hide))
\t)
\t(gr_text "REV A" (at 140 95 0) (layer "F.SilkS"))
\t(gr_text "notes" (at 140 96 0) (layer "Dwgs.User"))
)
"""


class IntendedTextTests(unittest.TestCase):
    def test_lists_only_visible_silkscreen_text(self) -> None:
        items = {item.describe() for item in silkscreen.intended_silkscreen_text(PCB)}
        self.assertEqual(
            {
                "TP1 text '${VALUE}' on F.SilkS",
                "R1 reference 'R1' on F.SilkS",
                "board text 'REV A' on F.SilkS",
            },
            items,
        )

    def test_values_off_drops_value_user_text(self) -> None:
        items = silkscreen.intended_silkscreen_text(PCB)
        omitted = silkscreen.plot_option_omissions(items, plot_refs=True, plot_values=False)
        self.assertEqual(["TP1 text '${VALUE}' on F.SilkS"], [item.describe() for item in omitted])

    def test_refs_off_drops_reference_field(self) -> None:
        items = silkscreen.intended_silkscreen_text(PCB)
        omitted = silkscreen.plot_option_omissions(items, plot_refs=False, plot_values=True)
        self.assertEqual(["R1 reference 'R1' on F.SilkS"], [item.describe() for item in omitted])

    def test_everything_plotted_omits_nothing(self) -> None:
        items = silkscreen.intended_silkscreen_text(PCB)
        self.assertEqual([], silkscreen.plot_option_omissions(items, plot_refs=True, plot_values=True))


GERBER_YAML = """outputs:
- name: @NAME@
  type: gerber
  options:
    subtract_mask_from_silk: true
    plot_footprint_refs: @PLOT_REFS@
    plot_footprint_values: {values}
    create_gerber_job_file: false

...
definitions:
  NAME: gbr_gerbers
  PLOT_REFS: true
  PLOT_VALUES: true
"""

MAIN_YAML = """import:
  - file: kibot_out_gerber.yaml
    definitions:
      NAME: @GERBER_OUTPUT@
      PLOT_REFS: @PLOT_REFS@
      PLOT_VALUES: @PLOT_VALUES@

definitions:
  PLOT_REFS: true
  PLOT_VALUES: {main_values}
"""


class PlotOptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="bw-silk-"))
        (self.root / silkscreen.GERBER_CONFIG).parent.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, values: str, main_values: str = "true") -> None:
        (self.root / silkscreen.GERBER_CONFIG).write_text(GERBER_YAML.format(values=values), encoding="utf-8")
        (self.root / silkscreen.KIBOT_MAIN).write_text(MAIN_YAML.format(main_values=main_values), encoding="utf-8")

    def test_shipped_template_plots_refs_and_values(self) -> None:
        self.assertEqual(
            {"plot_footprint_refs": True, "plot_footprint_values": True},
            silkscreen.gerber_plot_options(REPO),
        )

    def test_shipped_fabrication_output_config_fully_resolves(self) -> None:
        from boardwright.kibot_config import fabrication_output_config

        def read(path: str):
            file = REPO / path
            return file.read_text(encoding="utf-8") if file.exists() else None

        config = fabrication_output_config(read)
        self.assertIn("plot_footprint_values: true", config)
        self.assertIn("kibot_out_excellon_drill.yaml", config)
        self.assertNotIn("(unresolved)", config)

    def test_placeholder_resolves_through_main_definitions(self) -> None:
        self._write("@PLOT_VALUES@", main_values="false")
        self.assertFalse(silkscreen.gerber_plot_options(self.root)["plot_footprint_values"])
        self._write("@PLOT_VALUES@", main_values="true")
        self.assertTrue(silkscreen.gerber_plot_options(self.root)["plot_footprint_values"])

    def test_crlf_config_resolves(self) -> None:
        self._write("@PLOT_VALUES@", main_values="false")
        for path in (silkscreen.GERBER_CONFIG, silkscreen.KIBOT_MAIN):
            data = (self.root / path).read_bytes().replace(b"\n", b"\r\n")
            (self.root / path).write_bytes(data)
        self.assertFalse(silkscreen.gerber_plot_options(self.root)["plot_footprint_values"])

    def test_validate_reports_dropped_test_point_label(self) -> None:
        self._write("false")
        problems = silkscreen.validate_plot_options(self.root, PCB)
        self.assertEqual(1, len(problems))
        self.assertIn("TP1 text '${VALUE}'", problems[0])

    def test_validate_passes_when_values_plotted(self) -> None:
        self._write("@PLOT_VALUES@")
        self.assertEqual([], silkscreen.validate_plot_options(self.root, PCB))

    def test_unresolvable_option_is_a_problem(self) -> None:
        self._write("@NOPE@")
        problems = silkscreen.validate_plot_options(self.root, PCB)
        self.assertTrue(problems and "cannot resolve @NOPE@" in problems[0])

    def test_validate_project_reports_it(self) -> None:
        # The check is wired into `boardwright validate`.
        source = (REPO / "src/boardwright/validation.py").read_text(encoding="utf-8")
        self.assertIn("_validate_silkscreen_plotting(config, pcbs[0], issues)", source)


HEADER = """%TF.GenerationSoftware,KiCad,Pcbnew,9.0.7*%
%TF.CreationDate,2026-11-18T00:05:00+00:00*%
%TF.ProjectId,FIC-100A,0000,A*%
%TF.FileFunction,Legend,Top*%
%FSLAX46Y46*%
%MOMM*%
%LPD*%
G01*
"""
LINE = "%ADD{n}C,0.120000*%\nD{n}*\nX147800000Y-98550000D02*\nX149200000Y-98550000D01*\n"
LABEL = """G36*
X149480000Y-98520000D02*
X152520000Y-98520000D01*
X152520000Y-99890000D01*
X149480000Y-99890000D01*
X149480000Y-98520000D01*
G37*
"""


def gerber(*parts: str) -> str:
    return HEADER + "".join(parts) + "M02*\n"


class GeometryTests(unittest.TestCase):
    def test_identical_geometry_with_different_dcodes_matches(self) -> None:
        self.assertEqual(
            [], silkscreen.compare_gerbers("F", gerber(LINE.format(n=10), LABEL), gerber(LINE.format(n=17), LABEL))
        )

    def test_header_only_differences_ignored(self) -> None:
        other = gerber(LINE.format(n=10)).replace("9.0.7", "9.0.6").replace("2026-11-18", "2026-01-01")
        self.assertEqual([], silkscreen.compare_gerbers("F", gerber(LINE.format(n=10)), other))

    def test_missing_label_reported_with_location(self) -> None:
        diffs = silkscreen.compare_gerbers("F", gerber(LINE.format(n=10)), gerber(LINE.format(n=10), LABEL))
        self.assertEqual(1, len(diffs))
        self.assertEqual("missing", diffs[0].change)
        x0, y0, x1, y1 = diffs[0].bbox
        self.assertAlmostEqual(149.48, x0)
        self.assertAlmostEqual(152.52, x1)
        self.assertAlmostEqual(98.52, y0)
        self.assertAlmostEqual(99.89, y1)
        silkscreen.annotate(diffs, PCB)
        self.assertEqual("TP1", diffs[0].near)
        self.assertIn("missing from shipped Gerber", str(diffs[0]))

    def test_extra_marking_reported(self) -> None:
        diffs = silkscreen.compare_gerbers("F", gerber(LINE.format(n=10), LABEL), gerber(LINE.format(n=10)))
        self.assertEqual(["extra"], [d.change for d in diffs])

    def test_polarity_matters(self) -> None:
        clear = gerber(LINE.format(n=10), "%LPC*%\n", LABEL)
        dark = gerber(LINE.format(n=10), LABEL)
        self.assertEqual({"missing", "extra"}, {d.change for d in silkscreen.compare_gerbers("F", clear, dark)})

    def test_aperture_size_matters(self) -> None:
        thin = gerber(LINE.format(n=10))
        thick = thin.replace("C,0.120000", "C,0.150000")
        self.assertTrue(silkscreen.compare_gerbers("F", thin, thick))

    def test_real_candidate_layers_match_themselves(self) -> None:
        # Sanity on KiCad-shaped input: arcs, macros and regions parse.
        sample = HEADER + (
            "%AMRoundRect*\n0 Rectangle with rounded corners*\n4,1,4,$2,$3,$4,$5,$6,$7,$8,$9,$2,$3,0*%\n"
            "%ADD11RoundRect,0.1X-0.5X-0.5X0.5X-0.5X0.5X0.5X-0.5X0.5X0*%\nD11*\nX1000000Y-1000000D03*\n"
            "G75*\nG03*\nX2000000Y-2000000I500000J0D01*\nG01*\n"
        ) + "M02*\n"
        prims = silkscreen.gerber_primitives(sample)
        self.assertEqual(2, sum(prims.values()))
        self.assertEqual([], silkscreen.compare_gerbers("F", sample, sample))


class CheckCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="bw-silkcli-"))
        self.gerbers = self.root / "Manufacturing/Fabrication/Gerbers"
        self.reference = self.root / silkscreen.REFERENCE_DIR
        self.gerbers.mkdir(parents=True)
        self.reference.mkdir(parents=True)
        (self.root / "FIC-100A.kicad_pcb").write_text(PCB, encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _layers(self, shipped_front: str) -> None:
        for name, shipped, ref in (
            ("FIC-100A-F_Silkscreen.gbr", shipped_front, gerber(LINE.format(n=10), LABEL)),
            ("FIC-100A-B_Silkscreen.gbr", gerber(LINE.format(n=12)), gerber(LINE.format(n=12))),
        ):
            (self.gerbers / name).write_text(shipped, encoding="utf-8")
            (self.reference / name).write_text(ref, encoding="utf-8")

    def test_missing_label_fails_and_reference_is_removed(self) -> None:
        self._layers(gerber(LINE.format(n=10)))
        self.assertEqual(1, silkscreen.main(["--root", str(self.root)]))
        self.assertFalse(self.reference.exists())
        self.assertFalse(self.reference.parent.exists())

    def test_complete_silkscreen_passes(self) -> None:
        self._layers(gerber(LINE.format(n=11), LABEL))
        self.assertEqual(0, silkscreen.main(["--root", str(self.root)]))

    def test_missing_reference_plot_fails_closed(self) -> None:
        self._layers(gerber(LINE.format(n=10), LABEL))
        shutil.rmtree(self.reference)
        self.assertEqual(1, silkscreen.main(["--root", str(self.root)]))

    def test_missing_shipped_layer_fails(self) -> None:
        self._layers(gerber(LINE.format(n=10), LABEL))
        (self.gerbers / "FIC-100A-B_Silkscreen.gbr").unlink()
        self.assertEqual(1, silkscreen.main(["--root", str(self.root)]))

    def test_no_gerbers_is_skipped(self) -> None:
        shutil.rmtree(self.gerbers)
        shutil.rmtree(self.reference)
        self.assertEqual(0, silkscreen.main(["--root", str(self.root)]))

    def test_reference_without_gerbers_fails(self) -> None:
        shutil.rmtree(self.gerbers)
        self.assertEqual(1, silkscreen.main(["--root", str(self.root)]))
        self.assertFalse(self.reference.exists())


class WiringTests(unittest.TestCase):
    def test_reference_output_is_unfiltered_and_matches_mask_subtraction(self) -> None:
        reference = (REPO / "boardwright_resources/kibot/yaml/kibot_out_silk_reference.yaml").read_text(encoding="utf-8")
        release = (REPO / silkscreen.GERBER_CONFIG).read_text(encoding="utf-8")
        self.assertIn("plot_footprint_refs: true", reference)
        self.assertIn("plot_footprint_values: true", reference)
        self.assertIn("run_by_default: false", reference)
        self.assertIn("layers: ['F.Silkscreen', 'B.Silkscreen']", reference)
        self.assertEqual(
            scalar(release, "subtract_mask_from_silk"),
            scalar(reference, "subtract_mask_from_silk"),
        )

    def test_reference_runs_with_every_fabrication_group(self) -> None:
        main = (REPO / silkscreen.KIBOT_MAIN).read_text(encoding="utf-8")
        self.assertIn("- file: kibot_out_silk_reference.yaml", main)
        for group in ("fab", "fab_k9"):
            block = main.split(f"  - name: {group}\n", 1)[1].split("\n\n", 1)[0]
            self.assertIn("- @GERBER_OUTPUT@\n      - gbr_silk_reference", block)

    def test_workflows_verify_silkscreen_before_cleanup(self) -> None:
        for name in ("dev-preview", "main-outputs", "prepare-release"):
            text = (REPO / f".github/workflows/{name}.yaml").read_text(encoding="utf-8")
            check = text.index("python -m boardwright.silkscreen")
            self.assertLess(text.index("uses: INTI-CMNB/KiBot"), check, name)
            self.assertLess(check, text.index("- name: Clean generated outputs"), name)
            if name == "prepare-release":
                self.assertLess(check, text.index("python -m boardwright.output_manifest write"))

    def test_reference_directory_is_ignored(self) -> None:
        self.assertIn(".boardwright-checks/", (REPO / ".gitignore").read_text(encoding="utf-8").splitlines())


@unittest.skipUnless(
    os.environ.get("BOARDWRIGHT_KICAD_CLI") and os.environ.get("BOARDWRIGHT_REFERENCE_BOARD"),
    "set BOARDWRIGHT_KICAD_CLI and BOARDWRIGHT_REFERENCE_BOARD to plot a real board",
)
class RealBoardGeometryTests(unittest.TestCase):
    """Plots a real board with the release options and unfiltered; they must match."""

    def test_release_options_keep_every_visible_marking(self) -> None:
        cli = os.environ["BOARDWRIGHT_KICAD_CLI"]
        board = Path(os.environ["BOARDWRIGHT_REFERENCE_BOARD"])
        options = silkscreen.gerber_plot_options(REPO)
        with tempfile.TemporaryDirectory() as tmp:
            shipped, reference = Path(tmp, "shipped"), Path(tmp, "reference")
            base = [cli, "pcb", "export", "gerbers", "-l", "F.SilkS,B.SilkS", "--subtract-soldermask", "--no-protel-ext"]
            release_flags = ([] if options["plot_footprint_refs"] else ["--exclude-refdes"]) + (
                [] if options["plot_footprint_values"] else ["--exclude-value"]
            )
            for out, flags in ((shipped, release_flags), (reference, [])):
                subprocess.run(base + flags + ["-o", str(out) + os.sep, str(board)], check=True, capture_output=True)
            differences, problems = silkscreen.check_directories(
                shipped, reference, board.read_text(encoding="utf-8")
            )
        self.assertEqual([], problems)
        self.assertEqual([], [str(d) for d in differences])


if __name__ == "__main__":
    unittest.main()
