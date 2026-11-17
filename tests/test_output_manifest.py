# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from boardwright.output_manifest import blocking, build_manifest, category_for, compare_manifests, main

GERBER = """%TF.GenerationSoftware,KiCad,Pcbnew,{ver}*%
%TF.CreationDate,{date}*%
%TF.ProjectId,board,626f6172,{rev}*%
G04 Created by KiCad (PCBNEW {ver}) date {date}*
%FSLAX46Y46*%
D10*
X{x}Y0D02*
M02*
"""


class OutputManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="bw-manifest-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, x: str = "100", date: str = "2026-01-01T00:00:00", ver: str = "9.0.4", rev: str = "A") -> None:
        gerbers = self.root / "Manufacturing" / "Fabrication" / "Gerbers"
        gerbers.mkdir(parents=True, exist_ok=True)
        (gerbers / "board-F_Cu.gbr").write_text(GERBER.format(x=x, date=date, ver=ver, rev=rev), encoding="utf-8")
        assembly = self.root / "Manufacturing" / "Assembly"
        assembly.mkdir(parents=True, exist_ok=True)
        (assembly / "board-CPL.csv").write_text("Ref,Val,PosX\nC1,10u,10\n", encoding="utf-8")
        (self.root / "Schematic").mkdir(exist_ok=True)
        (self.root / "Schematic" / "board.pdf").write_bytes(b"%PDF-" + date.encode())

    def test_categories(self) -> None:
        self.assertEqual("fabrication", category_for("Manufacturing/Fabrication/Gerbers/x-F_Cu.gbr"))
        self.assertEqual("assembly", category_for("Manufacturing/Assembly/x-CPL.csv"))
        self.assertEqual("informational", category_for("Schematic/x.pdf"))
        self.assertEqual("informational", category_for("Manufacturing/Fabrication/Gerbers/x.zip"))

    def test_identical_regeneration_is_byte_identical(self) -> None:
        self._write()
        first = build_manifest(self.root)
        self.assertEqual([], compare_manifests(first, build_manifest(self.root)))

    def test_timestamp_tool_and_revision_label_changes_are_regenerated_not_changed(self) -> None:
        self._write()
        first = build_manifest(self.root)
        self._write(date="2026-11-17T09:00:00", ver="10.0.8", rev="A.1")
        differences = compare_manifests(first, build_manifest(self.root))
        self.assertEqual(["regenerated"], [d.kind for d in differences])
        self.assertEqual([], blocking(differences))

    def test_geometry_change_is_blocking(self) -> None:
        self._write()
        first = build_manifest(self.root)
        self._write(x="200")
        differences = compare_manifests(first, build_manifest(self.root))
        self.assertEqual(["changed"], [d.kind for d in blocking(differences)])

    def test_missing_and_unexpected_files_are_blocking(self) -> None:
        self._write()
        first = build_manifest(self.root)
        gerbers = self.root / "Manufacturing" / "Fabrication" / "Gerbers"
        (gerbers / "board-F_Cu.gbr").unlink()
        (gerbers / "board-B_Cu.gbr").write_text("X", encoding="utf-8")
        kinds = sorted(d.kind for d in blocking(compare_manifests(first, build_manifest(self.root))))
        self.assertEqual(["missing", "unexpected"], kinds)

    def test_cli_write_and_verify(self) -> None:
        self._write()
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, main(["write", "--root", str(self.root)]))
        manifest = json.loads((self.root / ".boardwright" / "accepted_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(2, manifest["schema"])
        self.assertEqual(2, manifest["normaliser"])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, main(["verify", "--root", str(self.root)]))
        (self.root / "Manufacturing" / "Assembly" / "board-CPL.csv").write_text(
            "Ref,Val,PosX\nC1,22u,10\n", encoding="utf-8"
        )
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(1, main(["verify", "--root", str(self.root)]))

    def test_cli_rejects_invalid_manifest(self) -> None:
        bad = self.root / "bad.json"
        bad.write_text("{}", encoding="utf-8")
        with redirect_stderr(io.StringIO()):
            self.assertEqual(2, main(["compare", str(bad), str(bad)]))


# Header forms copied from real KiCad 9.0.7 / KiBot CI outputs (Gerber X2 and
# Excellon). Geometry is synthetic.
REAL_GERBER = """%TF.GenerationSoftware,KiCad,Pcbnew,{ver}*%
%TF.CreationDate,{date}+00:00*%
%TF.ProjectId,{name},4649432d-3130-4304-912e-6b696361645f,{rev}*%
%TF.SameCoordinates,Original*%
%TF.FileFunction,{function}*%
%TF.FilePolarity,Positive*%
%FSLAX46Y46*%
G04 Gerber Fmt 4.6, Leading zero omitted, Abs format (unit mm)*
G04 Created by KiCad (PCBNEW {ver}) date {date_space}*
%MOMM*%
%LPD*%
G01*
%ADD10C,0.250000*%
D10*
X{x}Y95000000D02*
X110000000Y95000000D01*
M02*
"""

REAL_DRILL = """M48
; DRILL file {{KiCad {ver}}} date {date}+0000
; FORMAT={{-:-/ absolute / metric / decimal}}
; #@! TF.CreationDate,{date}+00:00
; #@! TF.GenerationSoftware,Kicad,Pcbnew,{ver}
; #@! TF.FileFunction,{function}
FMAT,2
METRIC
; #@! TA.AperFunction,Plated,PTH,ComponentDrill
T1C{diameter}
%
G90
G05
T1
X100.0Y-95.0
M30
"""


class RealOutputNormalisationTests(unittest.TestCase):
    """Regression tests for false mismatches seen on real CI output pairs."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="bw-manifest-real-"))
        self.gerbers = self.root / "Manufacturing" / "Fabrication" / "Gerbers"
        self.gerbers.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _build(self, **changes: str) -> dict:
        values = dict(
            ver="9.0.7+1",
            date="2026-05-25T15:18:47",
            date_space="2026-05-25 15:18:47",
            name="BOARD-100A",
            rev="0.1.2",
            function="Copper,L1,Top",
            x="100000000",
            drill_function="Plated,1,2,PTH",
            diameter="0.300",
            pdf=b"%PDF-1.5 2026-05-25",
        )
        values.update(changes)
        gerber = REAL_GERBER.format(**values)
        drill = REAL_DRILL.format(ver=values["ver"], date=values["date"], function=values["drill_function"], diameter=values["diameter"])
        (self.gerbers / "b-F_Cu.gbr").write_text(gerber, encoding="utf-8")
        (self.gerbers / "b-PTH.drl").write_text(drill, encoding="utf-8")
        (self.gerbers / "b-PTH_drill_map.pdf").write_bytes(values["pdf"])
        job = {
            "Header": {
                "GenerationSoftware": {"Vendor": "KiCad", "Application": "Pcbnew", "Version": values["ver"]},
                "CreationDate": values["date"],
                "ProjectId": {"Name": values["name"], "GUID": "4649432d", "Revision": values["rev"]},
            },
            "FilesAttributes": [{"Path": "b-F_Cu.gbr", "FileFunction": values["function"]}],
        }
        (self.gerbers / "b-job.gbrjob").write_text(json.dumps(job, indent=2), encoding="utf-8")
        archive = self.root / "Manufacturing" / "Fabrication" / "b-GERBERS.zip"
        import zipfile

        with zipfile.ZipFile(archive, "w") as handle:
            for name, data in (("b-F_Cu.gbr", gerber), ("b-PTH.drl", drill)):
                info = zipfile.ZipInfo(name, date_time=tuple(int(p) for p in values["date"][:10].split("-")) + (0, 0, 0))
                handle.writestr(info, data)
            handle.writestr("b-PTH_drill_map.pdf", values["pdf"])
        odb = self.root / "Manufacturing" / "Fabrication" / "b-odb.zip"
        with zipfile.ZipFile(odb, "w") as handle:
            handle.writestr("misc/info", f"CREATION_DATE={values['date']}")
        return build_manifest(self.root)

    def test_rebuild_with_new_release_version_and_timestamps_is_not_blocking(self) -> None:
        first = self._build()
        second = self._build(date="2026-05-26T02:06:36", date_space="2026-05-26 02:06:36", rev="0.1.3", pdf=b"%PDF-1.5 other")
        differences = compare_manifests(first, second)
        self.assertEqual([], blocking(differences))
        self.assertIn("Manufacturing/Fabrication/b-GERBERS.zip!/b-F_Cu.gbr", {d.path for d in differences})

    def test_drill_maps_pdfs_and_odb_are_informational(self) -> None:
        manifest = self._build()
        files = manifest["files"]
        self.assertEqual("informational", files["Manufacturing/Fabrication/Gerbers/b-PTH_drill_map.pdf"]["category"])
        self.assertEqual("informational", files["Manufacturing/Fabrication/b-GERBERS.zip!/b-PTH_drill_map.pdf"]["category"])
        self.assertEqual("informational", files["Manufacturing/Fabrication/b-odb.zip"]["category"])
        self.assertFalse(any(path.startswith("Manufacturing/Fabrication/b-odb.zip!/") for path in files))
        self.assertEqual("fabrication", files["Manufacturing/Fabrication/b-GERBERS.zip!/b-PTH.drl"]["category"])

    def test_real_changes_remain_blocking(self) -> None:
        first = self._build()
        cases = {
            "copper coordinate": dict(x="100000001"),
            "gerber file function": dict(function="Copper,L2,Bot"),
            "project identity": dict(name="OTHER-200B"),
            "drill diameter": dict(diameter="0.350"),
            "drill file function": dict(drill_function="NonPlated,1,2,NPTH"),
        }
        for label, change in cases.items():
            with self.subTest(label):
                kinds = {d.kind for d in blocking(compare_manifests(first, self._build(**change)))}
                self.assertEqual({"changed"}, kinds)

    def test_change_only_inside_shipped_archive_is_blocking(self) -> None:
        first = self._build()
        import zipfile

        archive = self.root / "Manufacturing" / "Fabrication" / "b-GERBERS.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("b-F_Cu.gbr", "%FSLAX46Y46*%\nX1Y1D02*\nM02*\n")
            handle.writestr("b-PTH.drl", (self.gerbers / "b-PTH.drl").read_text(encoding="utf-8"))
        paths = {d.path for d in blocking(compare_manifests(first, build_manifest(self.root)))}
        self.assertEqual({"Manufacturing/Fabrication/b-GERBERS.zip!/b-F_Cu.gbr"}, paths)

    def test_different_normaliser_versions_are_incomparable_unless_bytes_match(self) -> None:
        first = self._build()
        legacy = json.loads(json.dumps(first))
        legacy["normaliser"] = 1
        self.assertEqual([], compare_manifests(legacy, first))
        second = self._build(date="2026-05-26T02:06:36", date_space="2026-05-26 02:06:36")
        kinds = {d.kind for d in blocking(compare_manifests(legacy, second))}
        self.assertEqual({"incomparable"}, kinds)

    def test_schema_one_manifest_still_loads(self) -> None:
        path = self.root / "old.json"
        path.write_text(json.dumps({"schema": 1, "files": {}}), encoding="utf-8")
        from boardwright.output_manifest import load_manifest

        self.assertEqual(1, load_manifest(path)["normaliser"])


if __name__ == "__main__":
    unittest.main()
