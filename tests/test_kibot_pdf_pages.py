from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from boardwright.kibot_pdf_pages import (
    format_pdf_page_prune_summary,
    prune_empty_testpoint_pdf_pages,
)


class KiBotPdfPageTests(unittest.TestCase):
    def make_root(self) -> Path:
        root = Path("tests") / ".tmp_kibot_pdf_pages" / uuid.uuid4().hex
        root.mkdir(parents=True)
        self.addCleanup(lambda: shutil.rmtree(root.parent, ignore_errors=True))
        return root

    def write_fabrication_yaml(self, root: Path) -> Path:
        config = root / "boardwright_resources" / "kibot" / "yaml"
        config.mkdir(parents=True)
        path = config / "kibot_out_pdf_fabrication.yaml"
        path.write_text(
            """
outputs:
- name: pdf_fabrication
  options:
    pages:
      - scaling: 1
        sheet: 'TOP TEST POINTS (SCALE 1:1)'
        layers:
          - layer: F.TestPointList
      - scaling: 1
        sheet: 'BOTTOM TEST POINTS (SCALE 1:1)'
        layers:
          - layer: B.TestPointList
      - scaling: 1
        sheet: '%ln (SCALE 1:1)'
        layers:
          - layer: F.Cu
...
""",
            encoding="utf-8",
        )
        return path

    def test_prunes_only_empty_testpoint_side_pages(self) -> None:
        root = self.make_root()
        config = self.write_fabrication_yaml(root)
        testpoints = root / "Testing" / "Testpoints"
        testpoints.mkdir(parents=True)
        (testpoints / "board-testpoints-top.csv").write_text(
            "Ref.,Net,X [mm],Y [mm]\nTP1,+3V3,1,2\n",
            encoding="utf-8",
        )
        (testpoints / "board-testpoints-bottom.csv").write_text(
            "Ref.,Net,X [mm],Y [mm]\n",
            encoding="utf-8",
        )

        result = prune_empty_testpoint_pdf_pages(root)
        text = config.read_text(encoding="utf-8")

        self.assertEqual(("BOTTOM TEST POINTS",), result.removed_pages)
        self.assertIn("TOP TEST POINTS", text)
        self.assertNotIn("BOTTOM TEST POINTS", text)
        self.assertIn("%ln", text)
        self.assertIn("BOTTOM TEST POINTS", format_pdf_page_prune_summary(result))

    def test_prunes_both_testpoint_pages_when_no_side_has_rows(self) -> None:
        root = self.make_root()
        config = self.write_fabrication_yaml(root)
        testpoints = root / "Testing" / "Testpoints"
        testpoints.mkdir(parents=True)
        (testpoints / "board-testpoints-top.csv").write_text("Ref.,Net\n", encoding="utf-8")
        (testpoints / "board-testpoints-bottom.csv").write_text("Ref.,Net\n", encoding="utf-8")

        result = prune_empty_testpoint_pdf_pages(root)
        text = config.read_text(encoding="utf-8")

        self.assertEqual(("TOP TEST POINTS", "BOTTOM TEST POINTS"), result.removed_pages)
        self.assertNotIn("TOP TEST POINTS", text)
        self.assertNotIn("BOTTOM TEST POINTS", text)


if __name__ == "__main__":
    unittest.main()
