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
        self.assertEqual(1, manifest["schema"])
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


if __name__ == "__main__":
    unittest.main()
