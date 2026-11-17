# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Static guards for release-integrity properties of the GitHub workflows."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

WORKFLOWS = Path(".github/workflows")


def _steps(name: str) -> list[dict]:
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    return [step for job in data["jobs"].values() for step in job["steps"]]


class ReleaseWorkflowTests(unittest.TestCase):
    def test_no_dispatch_inputs_interpolated_into_scripts(self) -> None:
        pattern = re.compile(r"\$\{\{\s*(github\.event\.)?inputs\.")
        for workflow in WORKFLOWS.glob("*.yaml"):
            for step in _steps(workflow.name):
                self.assertNotRegex(step.get("run", ""), pattern, f"{workflow.name}: {step.get('name')}")

    def test_prepare_checks_tags_before_pushing_main(self) -> None:
        names = [step.get("name", "") for step in _steps("prepare-release.yaml")]
        self.assertLess(
            names.index("Verify reviewed source and tag availability"),
            names.index("Commit accepted release state"),
        )
        verify = next(s for s in _steps("prepare-release.yaml") if s.get("name") == "Verify reviewed source and tag availability")
        self.assertIn("refusing to continue", verify["run"])
        self.assertIn("IN_SOURCE_SHA", verify["run"])

    def test_prepare_commits_ledger_and_manifest(self) -> None:
        commit = next(s for s in _steps("prepare-release.yaml") if s.get("name") == "Commit accepted release state")
        self.assertIn(".boardwright/releases.yaml", commit["run"])
        self.assertIn(".boardwright/accepted_manifest.json", commit["run"])
        names = [s.get("name") for s in _steps("prepare-release.yaml")]
        self.assertIn("Check reused baselines against the previous release", names)

    def test_release_is_serialised_immutable_and_verified(self) -> None:
        data = yaml.safe_load((WORKFLOWS / "release.yaml").read_text(encoding="utf-8"))
        self.assertIn("github.ref_name", data["concurrency"]["group"])
        names = [s.get("name") for s in _steps("release.yaml")]
        self.assertIn("Refuse to overwrite a published release", names)
        self.assertIn("Verify outputs match the accepted release state", names)
        self.assertLess(names.index("Choose KiBot variant and output set"), names.index("Generate revision table variables"))
        collect = next(s for s in _steps("release.yaml") if s.get("name") == "Collect release assets")
        self.assertIn("sha256sum", collect["run"])
        self.assertIn("RELEASE_IDENTITY.json", collect["run"])

    def test_kicad10_group_and_launcher_are_explicit(self) -> None:
        main = Path("boardwright_resources/kibot/yaml/kibot_main.yaml").read_text(encoding="utf-8")
        self.assertIn("- name: all_group_k10", main)
        launcher = Path("scripts/kibot_launch.sh").read_text(encoding="utf-8")
        self.assertNotRegex(launcher, r"^\s*eval", )
        self.assertIn("unsupported KiCad major version", launcher)
        self.assertNotIn('all_group="all_group"', launcher)


if __name__ == "__main__":
    unittest.main()
