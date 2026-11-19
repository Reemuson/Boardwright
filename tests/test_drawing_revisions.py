# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Drawing revision tables record drawing revisions, not project releases."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from boardwright.config import load_config
from boardwright.drawing_revisions import apply_revision_history, plan_revision_history
from boardwright.errors import BoardwrightError
from boardwright.release import plan_release_revision_history
from boardwright.release_ledger import load_ledger

from test_revision_model import Project, _git, _rmtree

ISSUED = """document_revisions:
  schematic:
    rows:
    - revision: A.0
      date: '2020-01-02'
      description: Original schematic issue
  fabrication:
    rows:
    - revision: A
      date: 2020-01-03
      description: Original fabrication issue
      drawn_by: R. HICKS
  assembly:
    rows:
    - revision: A
      date: 2020-01-04
      description: Original assembly issue
      drawn_by: R. HICKS
"""


def _rows(root: Path) -> dict:
    data = yaml.safe_load((root / ".boardwright" / "document_revisions.yaml").read_text(encoding="utf-8"))
    return {name: [{k: str(v) for k, v in row.items()} for row in entry["rows"]] for name, entry in data["document_revisions"].items()}


class ReleasePreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="bw-drawrev-"))
        self.project = Project(self.tmp)
        self.table = self.tmp / ".boardwright" / "document_revisions.yaml"
        self.table.write_text(ISSUED, encoding="utf-8", newline="\n")
        self.project.commit("issued drawings")
        self.project.release("1.0.0", "A.0", justification="First release")
        self.issued_bytes = self.table.read_bytes()
        self.issued = _rows(self.tmp)

    def tearDown(self) -> None:
        _rmtree(self.tmp)

    def test_first_release_keeps_already_issued_rows(self) -> None:
        self.assertEqual(ISSUED.encode("utf-8"), self.issued_bytes)
        self.assertEqual("2020-01-02", self.issued["schematic"][0]["date"])

    def test_consecutive_tooling_only_releases_leave_every_table_unchanged(self) -> None:
        env = self.tmp / ".boardwright" / "revision_history_variables.env"
        variables = env.read_bytes()
        for version in ("1.0.1", "1.0.2"):
            self.project.release(version, "A.0", classes="tooling", justification=f"Tooling update {version}")
            self.assertEqual(self.issued_bytes, self.table.read_bytes(), version)
            self.assertEqual(variables, env.read_bytes(), version)
        versions = [record.version for record in load_ledger(self.tmp)]
        self.assertEqual(["1.0.0", "1.0.1", "1.0.2"], versions)
        changelog = (self.tmp / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("## [1.0.2]", changelog)
        self.assertIn("## [1.0.1]", changelog)

    def test_documentation_change_to_one_drawing_adds_only_its_row(self) -> None:
        self.project.write(note="NOTE: corrected")
        self.project.commit("docs: correct note")
        self.project.release("1.0.1", "A.1", classes="documentation", justification="Correct schematic note")

        rows = _rows(self.tmp)
        self.assertEqual(["A.1", "A.0"], [row["revision"] for row in rows["schematic"]])
        self.assertEqual("Correct schematic note", rows["schematic"][0]["description"])
        self.assertEqual(self.issued["schematic"][0], rows["schematic"][1])
        self.assertEqual(self.issued["fabrication"], rows["fabrication"])
        self.assertEqual(self.issued["assembly"], rows["assembly"])
        self.assertEqual("A.1", load_config(self.tmp).document_settings("schematic")["revision"])

    def test_working_tree_edit_of_an_issued_row_is_refused(self) -> None:
        self.table.write_text(ISSUED.replace("'2020-01-02'", "'2026-01-01'"), encoding="utf-8", newline="\n")
        self.project.commit("edit issued row")
        plan = plan_release_revision_history(load_config(self.tmp), drawing_revision="A.0")
        self.assertTrue(any("issued schematic revision A.0 row differs from release 1.0.0" in p for p in plan.problems))
        with self.assertRaises(BoardwrightError):
            self.project.release("1.0.1", "A.0", classes="tooling", justification="Tooling")

    def test_unissued_row_without_revision_advance_is_refused(self) -> None:
        data = yaml.safe_load(ISSUED)
        data["document_revisions"]["assembly"]["rows"].insert(0, {"revision": "B", "date": "", "description": "Draft"})
        self.table.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8", newline="\n")
        plan = plan_release_revision_history(load_config(self.tmp), drawing_revision="A.0")
        self.assertTrue(any("assembly revision table has rows that were not issued" in p for p in plan.problems))

    def test_reusing_an_issued_revision_is_refused(self) -> None:
        self.project.write(note="NOTE: corrected")
        self.project.release("1.0.1", "A.1", classes="documentation", justification="Correct note")
        plan = plan_revision_history(
            load_config(self.tmp),
            previous_version="1.0.1",
            previous={"schematic": "A.1", "fabrication": "A", "assembly": "A"},
            proposed={"schematic": "A.0", "fabrication": "A", "assembly": "A"},
            description="Back to A.0",
            date="2026-01-01",
        )
        self.assertTrue(any("schematic revision A.0 was already issued" in p for p in plan.problems))


class PlannerTests(unittest.TestCase):
    """Fabrication and assembly follow the same rules as the schematic."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="bw-drawrev-plan-"))
        self.project = Project(self.tmp)
        (self.tmp / ".boardwright" / "document_revisions.yaml").write_text(ISSUED, encoding="utf-8", newline="\n")
        self.issued = {
            name: [{k: str(v) for k, v in row.items()} for row in entry["rows"]]
            for name, entry in yaml.safe_load(ISSUED)["document_revisions"].items()
        }

    def tearDown(self) -> None:
        _rmtree(self.tmp)

    def _plan(self, proposed: dict[str, str], description: str = "Re-issue"):
        return plan_revision_history(
            load_config(self.tmp),
            previous_version="1.0.0",
            previous={"schematic": "A.0", "fabrication": "A", "assembly": "A"},
            proposed={"schematic": "A.0", "fabrication": "A", "assembly": "A", **proposed},
            description=description,
            date="2026-02-03",
            issued=self.issued,
        )

    def test_fabrication_and_assembly_increments_add_rows_and_keep_history(self) -> None:
        plan = self._plan({"fabrication": "B", "assembly": "B"}, "Stack-up note corrected")
        self.assertEqual((), plan.problems)
        self.assertEqual({"fabrication", "assembly"}, {item.document for item in plan.changed})
        apply_revision_history(load_config(self.tmp), plan)
        rows = _rows(self.tmp)
        for document in ("fabrication", "assembly"):
            self.assertEqual(
                {"revision": "B", "date": "2026-02-03", "description": "Stack-up note corrected", "drawn_by": "R. HICKS"},
                rows[document][0],
            )
            self.assertEqual(self.issued[document], rows[document][1:])
        self.assertEqual(self.issued["schematic"], rows["schematic"])

    def test_no_advance_writes_nothing_and_ignores_description(self) -> None:
        path = self.tmp / ".boardwright" / "document_revisions.yaml"
        before = path.read_bytes()
        plan = self._plan({}, "Not a drawing change")
        self.assertEqual((), plan.problems)
        self.assertEqual((), plan.changed)
        self.assertTrue(plan.notes)
        self.assertIsNone(apply_revision_history(load_config(self.tmp), plan))
        self.assertEqual(before, path.read_bytes())

    def test_advance_needs_a_description(self) -> None:
        plan = self._plan({"fabrication": "B"}, "")
        self.assertTrue(any("fabrication revision advances to B" in p for p in plan.problems))

    def test_prepared_draft_row_supplies_the_description(self) -> None:
        data = yaml.safe_load(ISSUED)
        data["document_revisions"]["assembly"]["rows"].insert(
            0, {"revision": "B", "date": "", "description": "Polarity mark added", "drawn_by": "J. DOE"}
        )
        (self.tmp / ".boardwright" / "document_revisions.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8", newline="\n")
        plan = self._plan({"assembly": "B"}, "")
        self.assertEqual((), plan.problems)
        (item,) = plan.changed
        self.assertEqual(
            {"revision": "B", "date": "2026-02-03", "description": "Polarity mark added", "drawn_by": "J. DOE"},
            item.added,
        )

    def test_issued_rows_come_from_the_release_tag(self) -> None:
        self.project.commit("issued")
        _git(self.tmp, "tag", "1.0.0")
        (self.tmp / ".boardwright" / "document_revisions.yaml").write_text(
            ISSUED.replace("Original fabrication issue", "Rewritten"), encoding="utf-8"
        )
        plan = plan_revision_history(
            load_config(self.tmp),
            previous_version="1.0.0",
            previous={"schematic": "A.0", "fabrication": "A", "assembly": "A"},
            proposed={"schematic": "A.0", "fabrication": "A", "assembly": "A"},
            description="",
            date="2026-02-03",
        )
        self.assertEqual("tag 1.0.0", plan.source)
        self.assertTrue(any("issued fabrication revision A row differs" in p for p in plan.problems))


if __name__ == "__main__":
    unittest.main()
