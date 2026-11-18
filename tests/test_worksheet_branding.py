# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Title-block identity and branding of the shipped Boardwright worksheets.

The PROJECT cell shows the descriptive project name and the large identifier
cell shows the per-document drawing number (PCB name on fabrication drawings,
PCBA name on schematic and assembly drawings). A project that declares its own
logo must not silently ship Boardwright's placeholder logo.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from boardwright.validation import _validate_worksheet_logo
from boardwright.worksheet_logo import (
    BOARDWRIGHT_PLACEHOLDER_LOGO_SHA256,
    embed_logo_in_worksheet,
    embedded_png,
)

REPO = Path(__file__).resolve().parents[1]
TEMPLATES = sorted((REPO / "Templates").glob("Boardwright_*.kicad_wks"))
TITLE_BLOCK_TEMPLATES = [path for path in TEMPLATES if "AS1100" not in path.name]
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/l6gK0wAAAABJRU5ErkJggg=="
)


def cell(text: str, x: float, y: float) -> str:
    for match in re.finditer(r'\(tbtext "([^"]*)"\s*\(name "[^"]*"\)\s*\(pos ([\d.\-]+) ([\d.\-]+)', text):
        if abs(float(match.group(2)) - x) < 0.01 and abs(float(match.group(3)) - y) < 0.01:
            return match.group(1)
    raise AssertionError(f"no text cell at {x},{y}")


class TitleBlockIdentityTests(unittest.TestCase):
    def test_templates_found(self) -> None:
        self.assertEqual(8, len(TITLE_BLOCK_TEMPLATES))

    def test_project_cell_shows_project_name(self) -> None:
        for path in TITLE_BLOCK_TEMPLATES:
            text = path.read_text(encoding="utf-8")
            self.assertEqual("PROJECT", cell(text, 184, 69), path.name)
            self.assertEqual("${PROJECT_NUMBER}", cell(text, 146, 62), path.name)

    def test_identifier_cell_is_per_document_drawing_number(self) -> None:
        for path in TITLE_BLOCK_TEMPLATES:
            self.assertEqual("${DRAWING_NUMBER}", cell(path.read_text(encoding="utf-8"), 75, 35), path.name)

    def test_pcba_name_is_not_used_as_a_generic_identifier(self) -> None:
        # PCBA_NAME would put the assembly identifier on fabrication drawings.
        for path in TITLE_BLOCK_TEMPLATES:
            self.assertNotIn('(tbtext "${PCBA_NAME}"', path.read_text(encoding="utf-8"), path.name)

    def test_drawing_number_follows_the_document(self) -> None:
        from boardwright.config import BoardwrightConfig

        config = BoardwrightConfig(
            root=REPO,
            project={"project": {"name": "FLUKE IR", "pcba_name": "FIC-100A", "pcb_name": "FIC-100A-PCB"}},
            branches={},
            legal={},
            revision_history={},
        )
        self.assertEqual("FLUKE IR", config.drawing_variables("fabrication")["PROJECT_NUMBER"])
        self.assertEqual("FIC-100A-PCB", config.drawing_variables("fabrication")["DRAWING_NUMBER"])
        self.assertEqual("FIC-100A", config.drawing_variables("assembly")["DRAWING_NUMBER"])
        self.assertEqual("FIC-100A", config.drawing_variables("schematic")["DRAWING_NUMBER"])


class PlaceholderLogoTests(unittest.TestCase):
    def test_constant_matches_shipped_templates(self) -> None:
        for path in TEMPLATES:
            png = embedded_png(path.read_text(encoding="utf-8"))
            self.assertEqual(BOARDWRIGHT_PLACEHOLDER_LOGO_SHA256, hashlib.sha256(png).hexdigest(), path.name)

    def test_embedded_png_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worksheet = Path(tmp, "w.kicad_wks")
            shutil.copyfile(TITLE_BLOCK_TEMPLATES[0], worksheet)
            logo = Path(tmp, "logo.png")
            logo.write_bytes(PNG_1X1)
            embed_logo_in_worksheet(worksheet, logo)
            self.assertEqual(PNG_1X1, embedded_png(worksheet.read_text(encoding="utf-8")))

    def _project(self, tmp: Path, *, embed: bytes | None, declared: bytes | None) -> SimpleNamespace:
        (tmp / "Templates").mkdir()
        worksheet = tmp / "Templates" / "Boardwright_Template_PCB_GIT_A4.kicad_wks"
        shutil.copyfile(REPO / "Templates" / worksheet.name, worksheet)
        if embed is not None:
            (tmp / "embed.png").write_bytes(embed)
            embed_logo_in_worksheet(worksheet, tmp / "embed.png")
        (tmp / "board.kicad_pro").write_text(
            json.dumps({"pcbnew": {"page_layout_descr_file": "Templates/" + worksheet.name}}), encoding="utf-8"
        )
        if declared is not None:
            (tmp / "logo.png").write_bytes(declared)
        return SimpleNamespace(root=tmp, template_sheet_image="logo.png" if declared is not None else "")

    def check(self, **kwargs) -> list:
        with tempfile.TemporaryDirectory() as tmp:
            issues: list = []
            _validate_worksheet_logo(self._project(Path(tmp), **kwargs), issues)
            return issues

    def test_placeholder_with_project_logo_warns(self) -> None:
        issues = self.check(embed=None, declared=PNG_1X1)
        self.assertEqual(["warning"], [issue.level for issue in issues])
        self.assertIn("Boardwright placeholder logo", issues[0].message)

    def test_embedded_project_logo_passes(self) -> None:
        self.assertEqual([], self.check(embed=PNG_1X1, declared=PNG_1X1))

    def test_a_different_project_image_is_not_flagged(self) -> None:
        # Projects may embed a dedicated worksheet image different from assets.logo.
        self.assertEqual([], self.check(embed=PNG_1X1, declared=b"\x89PNG other"))

    def test_no_declared_logo_no_warning(self) -> None:
        self.assertEqual([], self.check(embed=None, declared=None))


if __name__ == "__main__":
    unittest.main()
