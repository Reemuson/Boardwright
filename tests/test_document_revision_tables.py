# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""F-R8: each drawing renders its own revision table.

Schematic, fabrication and assembly drawings share one KiCad project and
worksheet, but their histories differ. These tests use three distinct
histories (different revisions, dates, descriptions and authors).
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

import yaml

from boardwright import kicad_tables
from boardwright.config import load_config
from boardwright.drawing_revisions import plan_revision_history
from boardwright.kibot_config import Resolver
from boardwright.kicad_variables import build_kicad_text_variables
from boardwright.revision_history import revision_variables, write_revision_variables
from test_revision_model import Project, _rmtree

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "boardwright_resources/kibot/resources/scripts/get_revision_history_var.py"
KIBOT_YAML = REPO / "boardwright_resources/kibot/yaml"

HISTORIES = {
    "document_revisions": {
        "schematic": {"rows": [
            {"revision": "B", "date": "2026-11-21", "description": "SCHEMATIC B CHANGE", "drawn_by": "S. AUTHOR"},
            {"revision": "A", "date": "2026-04-24", "description": "SCHEMATIC FIRST ISSUE", "drawn_by": "S. EARLIER"},
        ]},
        "fabrication": {"rows": [
            {"revision": "A", "date": "2026-10-02", "description": "FABRICATION FIRST ISSUE", "drawn_by": "F. AUTHOR"},
        ]},
        "assembly": {"rows": [
            {"revision": "C", "date": "2026-09-30", "description": "ASSEMBLY C CHANGE", "drawn_by": "A. AUTHOR"},
            {"revision": "B", "date": "2026-07-15", "description": "ASSEMBLY B CHANGE", "drawn_by": "A. MIDDLE"},
            {"revision": "A", "date": "2026-05-01", "description": "ASSEMBLY FIRST ISSUE", "drawn_by": "A. FIRST"},
        ]},
    }
}

EXPECTED = {
    "schematic": [("A", "2026-04-24", "SCHEMATIC FIRST ISSUE", "S. EARLIER"),
                  ("B", "2026-11-21", "SCHEMATIC B CHANGE", "S. AUTHOR")],
    "fabrication": [("A", "2026-10-02", "FABRICATION FIRST ISSUE", "F. AUTHOR")],
    "assembly": [("A", "2026-05-01", "ASSEMBLY FIRST ISSUE", "A. FIRST"),
                 ("B", "2026-07-15", "ASSEMBLY B CHANGE", "A. MIDDLE"),
                 ("C", "2026-09-30", "ASSEMBLY C CHANGE", "A. AUTHOR")],
}


def rows(values: dict[str, str]) -> list[tuple[str, str, str, str]]:
    found = []
    for index in range(1, 7):
        row = tuple(values.get(f"REVTABLE_{index}_{field}", "") for field in ("REV", "DATE", "DESC", "DRAWN"))
        if any(row):
            found.append(row)
    return found


class DocumentRevisionTableTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self.root = Path(tempfile.mkdtemp(prefix="bw-revtables-"))
        self.addCleanup(_rmtree, self.root)
        self.project = Project(self.root)
        (self.root / ".boardwright" / "document_revisions.yaml").write_text(
            yaml.safe_dump(HISTORIES, sort_keys=False), encoding="utf-8"
        )
        self.config = load_config(self.root, local=False)

    def test_each_document_gets_its_own_table(self) -> None:
        for document, expected in EXPECTED.items():
            with self.subTest(document=document):
                self.assertEqual(expected, rows(revision_variables(self.config, document)))

    def test_schematic_file_keeps_only_the_schematic_table(self) -> None:
        path = write_revision_variables(self.config)
        text = path.read_text(encoding="utf-8")
        self.assertIn("SCHEMATIC B CHANGE", text)
        self.assertNotIn("FABRICATION", text)
        self.assertNotIn("ASSEMBLY", text)

    def test_kicad_text_variables_follow_the_document(self) -> None:
        write_revision_variables(self.config)
        config = load_config(self.root, local=False)
        for document, expected in EXPECTED.items():
            with self.subTest(document=document):
                self.assertEqual(expected, rows(build_kicad_text_variables(config, document)))

    def test_render_time_script_prints_each_documents_values(self) -> None:
        write_revision_variables(self.config)
        env = dict(os.environ, PYTHONPATH=str(REPO / "src"))

        def value(*args: str) -> str:
            completed = subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.root, env=env,
                                       capture_output=True, text=True, check=True)
            return completed.stdout.strip()

        self.assertEqual("SCHEMATIC B CHANGE", value("REVTABLE_2_DESC"))  # default: schematic file
        self.assertEqual("SCHEMATIC B CHANGE", value("REVTABLE_2_DESC", "--document", "schematic"))
        self.assertEqual("FABRICATION FIRST ISSUE", value("REVTABLE_1_DESC", "--document", "fabrication"))
        self.assertEqual("F. AUTHOR", value("REVTABLE_1_DRAWN", "--document", "fabrication"))
        self.assertEqual("", value("REVTABLE_2_REV", "--document", "fabrication"))
        self.assertEqual("C", value("REVTABLE_3_REV", "--document", "assembly"))
        self.assertEqual("2026-07-15", value("REVTABLE_2_DATE", "--document", "assembly"))
        # Rendering reads only: the project files are unchanged.
        self.assertEqual("", subprocess.run(["git", "status", "--porcelain", "--", "*.kicad_pro", "board.*"],
                                            cwd=self.root, capture_output=True, text=True).stdout)

    def test_generated_document_configs_select_their_table(self) -> None:
        main_text = (KIBOT_YAML / "kibot_main.yaml").read_text(encoding="utf-8")
        pre_set = (KIBOT_YAML / "kibot_pre_set_text_variables.yaml").read_text(encoding="utf-8")
        (self.root / "boardwright_resources/kibot/yaml").mkdir(parents=True)
        kicad_tables._write_document_kibot_configs(self.root, main_text, self.config, {})

        def command(main: str) -> str:
            return Resolver(pre_set, main, "kibot_pre_set_text_variables.yaml").resolve("@GET_REVTABLE_CMD@")

        self.assertTrue(command(main_text).endswith("--document @REVTABLE_DOCUMENT@"))
        self.assertEqual("schematic", Resolver(pre_set, main_text, "kibot_pre_set_text_variables.yaml")
                         .resolve("@REVTABLE_DOCUMENT@"))
        for document, path in kicad_tables.DOCUMENT_KIBOT_CONFIGS.items():
            with self.subTest(document=document):
                generated = (self.root / path).read_text(encoding="utf-8")
                self.assertEqual(document, Resolver(pre_set, generated, "kibot_pre_set_text_variables.yaml")
                                 .resolve("@REVTABLE_DOCUMENT@").strip("'\""))

    def test_new_row_author_falls_back_to_the_title_block_author(self) -> None:
        # The fabrication drawing has no drawn_by of its own and no earlier row:
        # its first row carries the author its title block shows.
        data = yaml.safe_load((self.root / ".boardwright" / "project.yaml").read_text(encoding="utf-8"))
        data["documents"]["schematic"]["drawn_by"] = "S. AUTHOR"
        data["documents"]["fabrication"].pop("drawn_by", None)
        (self.root / ".boardwright" / "project.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        (self.root / ".boardwright" / "document_revisions.yaml").write_text("document_revisions: {}\n", encoding="utf-8")
        config = load_config(self.root, local=False)
        plan = plan_revision_history(
            config, previous_version="", previous={"fabrication": ""}, proposed={"fabrication": "A"},
            description="FIRST ISSUE", date="2026-11-21", issued=None,
        )
        fabrication = next(item for item in plan.documents if item.document == "fabrication")
        self.assertEqual("S. AUTHOR", fabrication.added["drawn_by"])
        self.assertEqual(config.drawing_variables("fabrication")["DRAWN_BY"], fabrication.added["drawn_by"])


if __name__ == "__main__":
    unittest.main()
