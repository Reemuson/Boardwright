# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Regression for the ci-test acceptance failure (run 44105437289).

`git checkout HEAD -- '*.kicad_pcb' … '*.kicad_prl' || true` restored nothing
because the project tracks no *.kicad_prl, so build side effects blocked
`git switch` to main.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_revision_model import _git, _rmtree  # noqa: E402

from boardwright.ci_cleanup import discard_side_effects, main  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TRACKED = {
    "b.kicad_pcb": "(kicad_pcb)\n",
    "b.kicad_pro": "{}\n",
    "b.kicad_sch": "(kicad_sch)\n",
    "README.md": "old\n",
    "boardwright_resources/kibot/yaml/kibot_out_pdf_fabrication.yaml": "pages: 3\n",
    "boardwright_resources/kibot/yaml/kibot_main.yaml": "definitions: {}\n",
    ".boardwright/project.yaml": "project: {}\n",
}


class CiCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="bw-cleanup-"))
        _git(self.root, "init", "-b", "main")
        for name, text in TRACKED.items():
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text(text, encoding="utf-8")
        _git(self.root, "add", "-A")
        _git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "init")
        for name in TRACKED:
            (self.root / name).write_text("changed by build\n", encoding="utf-8")
        generated = self.root / "boardwright_resources/kibot/yaml/kibot_document_fabrication.generated.yaml"
        generated.write_text("x\n", encoding="utf-8")
        (self.root / "Manufacturing").mkdir()
        (self.root / "Manufacturing" / "out.gbr").write_text("G04*\n", encoding="utf-8")

    def tearDown(self) -> None:
        _rmtree(self.root)

    def test_old_atomic_pathspec_restored_nothing(self) -> None:
        completed = subprocess.run(
            ["git", "checkout", "HEAD", "--", "*.kicad_pcb", "*.kicad_pro", "*.kicad_dru", "*.kicad_prl", "*.kicad_sch"],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("changed by build\n", (self.root / "b.kicad_pcb").read_text(encoding="utf-8"))

    def test_restores_every_tracked_side_effect_and_keeps_listed_paths(self) -> None:
        restored, remaining = discard_side_effects(self.root, ("README.md", ".boardwright"))
        self.assertEqual([], remaining)
        self.assertIn("boardwright_resources/kibot/yaml/kibot_out_pdf_fabrication.yaml", restored)
        for name in ("b.kicad_pcb", "b.kicad_pro", "b.kicad_sch"):
            self.assertEqual(TRACKED[name], (self.root / name).read_text(encoding="utf-8"))
        self.assertEqual("changed by build\n", (self.root / "README.md").read_text(encoding="utf-8"))
        self.assertEqual("changed by build\n", (self.root / ".boardwright/project.yaml").read_text(encoding="utf-8"))
        self.assertFalse((self.root / "boardwright_resources/kibot/yaml/kibot_document_fabrication.generated.yaml").exists())
        self.assertTrue((self.root / "Manufacturing" / "out.gbr").exists())
        # The branch switch that failed in CI now succeeds.
        _git(self.root, "switch", "-c", "boardwright-accepted-main")

    def test_cli_exit_codes(self) -> None:
        self.assertEqual(0, main(["--root", str(self.root), "--keep", "README.md", ".boardwright"]))


class WorkflowCleanupTests(unittest.TestCase):
    def test_workflows_use_fail_closed_cleanup_before_committing(self) -> None:
        for name in ("main-outputs.yaml", "prepare-release.yaml"):
            text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
            self.assertNotIn("'*.kicad_prl' '*.kicad_sch' || true", text, name)
            steps = yaml.safe_load(text)["jobs"]
            steps = next(iter(steps.values()))["steps"]
            names = [step.get("name") for step in steps]
            discard = names.index("Discard generated source side effects")
            self.assertIn("python -m boardwright.ci_cleanup", steps[discard]["run"])
            commit = "Commit accepted outputs" if name == "main-outputs.yaml" else "Commit accepted release state"
            self.assertLess(discard, names.index(commit), name)
