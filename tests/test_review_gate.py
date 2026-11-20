# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Review gate between a held release candidate and the tagging run."""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_revision_model import Project, _rmtree  # noqa: E402

from boardwright.release_date import parse_release_date, release_env_lines  # noqa: E402
from boardwright.release_ledger import load_ledger  # noqa: E402


def write_release_env(root: Path) -> None:
    """release.env as release --prepare --release-env writes it (date = ledger date)."""

    when = parse_release_date(load_ledger(root)[-1].date)
    lines = ["RELEASE_VERSION=1.0.0", "RELEASE_VARIANT=RELEASED", "RELEASE_KIND=draft", *release_env_lines(when)]
    text = "".join(line + "\n" for line in lines)
    (root / ".boardwright" / "release.env").write_text(text, encoding="utf-8", newline="\n")

from boardwright.output_manifest import build_manifest  # noqa: E402
from boardwright.review_gate import CANDIDATE_FILE, build_candidate, verify_candidate  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ENV = {
    "BOARDWRIGHT_SOURCE_SHA": "a" * 40,
    "IN_VERSION": "1.0.0",
    "IN_VARIANT": "RELEASED",
    "IN_RELEASE_KIND": "draft",
    "IN_DRAWING_REVISION": "A.0",
    "IN_REVISION_DESCRIPTION": "Initial",
    "IN_CHANGE_CLASSES": "",
    "IN_JUSTIFICATION": "Initial",
}


class ReviewGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="bw-review-"))
        self.project = Project(self.tmp)
        self.project.release("1.0.0", "A.0", justification="Initial")
        write_release_env(self.tmp)
        self.gerber = self.tmp / "Manufacturing" / "Fabrication" / "Gerbers" / "b-F_Cu.gbr"
        self.gerber.parent.mkdir(parents=True)
        self.gerber.write_text("%TF.CreationDate,2026-01-01*%\nX1Y1D02*\n", encoding="utf-8")
        self._write_manifest()
        self.candidate = Path(tempfile.mkdtemp(prefix="bw-candidate-"))
        self._hold()

    def tearDown(self) -> None:
        _rmtree(self.tmp)
        shutil.rmtree(self.candidate, ignore_errors=True)

    def _write_manifest(self) -> None:
        path = self.tmp / ".boardwright" / "accepted_manifest.json"
        path.write_text(json.dumps(build_manifest(self.tmp)), encoding="utf-8")

    def _hold(self) -> None:
        (self.candidate / ".boardwright").mkdir(parents=True, exist_ok=True)
        (self.candidate / CANDIDATE_FILE).write_text(json.dumps(build_candidate(self.tmp, ENV)), encoding="utf-8")
        shutil.copy(self.tmp / ".boardwright" / "accepted_manifest.json", self.candidate / ".boardwright")

    def test_identical_rebuild_with_new_timestamps_matches(self) -> None:
        self.gerber.write_text("%TF.CreationDate,2026-01-02*%\nX1Y1D02*\n", encoding="utf-8")
        self._write_manifest()
        self.assertEqual([], verify_candidate(self.tmp, self.candidate, ENV))

    def test_changed_controlled_output_is_rejected(self) -> None:
        self.gerber.write_text("%TF.CreationDate,2026-01-02*%\nX2Y1D02*\n", encoding="utf-8")
        self._write_manifest()
        problems = verify_candidate(self.tmp, self.candidate, ENV)
        self.assertTrue(any("Controlled output differs" in p for p in problems))

    def test_different_source_or_inputs_are_rejected(self) -> None:
        problems = verify_candidate(self.tmp, self.candidate, dict(ENV, BOARDWRIGHT_SOURCE_SHA="b" * 40))
        self.assertTrue(any("Source commit differs" in p for p in problems))
        problems = verify_candidate(self.tmp, self.candidate, dict(ENV, IN_DRAWING_REVISION="A.1"))
        self.assertTrue(any("IN_DRAWING_REVISION" in p for p in problems))

    def test_changed_ledger_identity_is_rejected(self) -> None:
        path = self.tmp / ".boardwright" / "releases.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["releases"][-1]["identities"]["pcb_revision"] = "B"
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        problems = verify_candidate(self.tmp, self.candidate, ENV)
        self.assertTrue(any("ledger entry" in p for p in problems))

    def test_missing_candidate_fails_closed(self) -> None:
        problems = verify_candidate(self.tmp, self.tmp / "nowhere", ENV)
        self.assertEqual(1, len(problems))
        self.assertIn("Cannot read reviewed candidate", problems[0])


class PrepareWorkflowReviewGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = (ROOT / ".github/workflows/prepare-release.yaml").read_text(encoding="utf-8")
        self.steps = yaml.safe_load(self.text)["jobs"]["prepare"]["steps"]

    def _step(self, name: str) -> dict:
        return next(step for step in self.steps if step.get("name") == name)

    def test_held_run_skips_every_mutating_step(self) -> None:
        for name in ("Commit accepted release state", "Create and push tag", "Dispatch tag release workflow"):
            self.assertEqual("${{ !inputs.review_hold }}", self._step(name).get("if"), name)

    def test_review_checks_precede_commit(self) -> None:
        names = [step.get("name") for step in self.steps]
        verify = names.index("Verify release matches the reviewed candidate")
        self.assertLess(names.index("Check approved review run"), verify)
        self.assertLess(verify, names.index("Commit accepted release state"))
        self.assertLess(names.index("Upload release candidate for review"), names.index("Commit accepted release state"))

    def test_review_inputs_reach_shell_only_through_env(self) -> None:
        for step in self.steps:
            script = step.get("run", "")
            self.assertIsNone(re.search(r"\$\{\{\s*inputs\.", script), step.get("name"))
