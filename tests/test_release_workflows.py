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
        self.assertIn("python -m boardwright.release_commit", commit["run"])
        self.assertIn("git push origin HEAD:main", commit["run"])
        from boardwright import release_commit
        forced = [path for group in release_commit.FORCED for path in group]
        self.assertIn(".boardwright/releases.yaml", forced)
        self.assertIn(".boardwright/accepted_manifest.json", forced)
        names = [s.get("name") for s in _steps("prepare-release.yaml")]
        # check-baseline, the manifest and the review record are part of the
        # shared candidate command (order guarded in test_container).
        self.assertLess(names.index("Build release candidate in the pinned container"), names.index("Upload release candidate for review"))
        self.assertLess(names.index("Build release candidate in the pinned container"), names.index("Commit accepted release state"))
        candidate = next(s for s in _steps("prepare-release.yaml") if s.get("name") == "Build release candidate in the pinned container")
        self.assertIn("python -m boardwright.container candidate", candidate["run"])
        self.assertIn("GH_TOKEN", candidate["env"])

    def test_release_is_serialised_immutable_and_verified(self) -> None:
        data = yaml.safe_load((WORKFLOWS / "release.yaml").read_text(encoding="utf-8"))
        self.assertIn("github.ref_name", data["concurrency"]["group"])
        names = [s.get("name") for s in _steps("release.yaml")]
        self.assertIn("Refuse to overwrite a published release", names)
        self.assertIn("Verify outputs match the accepted release state", names)
        self.assertLess(names.index("Choose KiBot variant and output set"), names.index("Build release outputs in the pinned container"))
        self.assertLess(names.index("Build release outputs in the pinned container"), names.index("Verify outputs match the accepted release state"))
        collect = next(s for s in _steps("release.yaml") if s.get("name") == "Collect release assets")
        self.assertIn("python -m boardwright.release_package collect", collect["run"])
        self.assertIn('--release-dir "$RELEASE_DIR"', collect["run"])
        module = (WORKFLOWS.parents[1] / "src" / "boardwright" / "release_package.py").read_text(encoding="utf-8")
        self.assertIn("RELEASE_IDENTITY.json", module)
        self.assertIn("MANIFEST.txt", module)

    def test_builds_use_the_shared_container_command_only(self) -> None:
        for workflow in WORKFLOWS.glob("*.yaml"):
            text = workflow.read_text(encoding="utf-8")
            with self.subTest(workflow=workflow.name):
                # Image identity lives in src/boardwright/container.py only.
                self.assertNotIn("INTI-CMNB/KiBot", text)
                self.assertNotIn("ghcr.io", text)
                self.assertNotIn("sudo chown", text)
                runs = " ".join(step.get("run", "") for step in _steps(workflow.name))
                self.assertNotRegex(runs, r"(^|\s)kibot\s")
                self.assertNotIn("prepare_pcb_tables.py", runs)
                self.assertNotIn("drawing_sheets check", runs)
                self.assertRegex(runs, r"python -m boardwright\.container (build|candidate) .*--workdir \.")

    def test_checkout_token_never_reaches_the_build_container(self) -> None:
        import yaml

        for workflow in WORKFLOWS.glob("*.yaml"):
            data = yaml.safe_load(workflow.read_text(encoding="utf-8"))
            for job_name, job in data["jobs"].items():
                for step in job.get("steps", []):
                    with self.subTest(workflow=workflow.name, job=job_name, step=step.get("name") or step.get("uses")):
                        if str(step.get("uses", "")).startswith("actions/checkout"):
                            self.assertIs(False, (step.get("with") or {}).get("persist-credentials"))
                        run = step.get("run", "")
                        if "boardwright.container" in run:
                            # Only check-baseline (host side, inside candidate) may use gh.
                            self.assertNotIn("GIT_CONFIG_VALUE_0", run)
                        if "git push" in run or "git ls-remote" in run or "git fetch" in run:
                            self.assertIn("GIT_CONFIG_KEY_0=\"http.https://github.com/.extraheader\"", run)
                            self.assertIn("::add-mask::", run)

    def test_kicad10_group_and_launcher_are_explicit(self) -> None:
        main = Path("boardwright_resources/kibot/yaml/kibot_main.yaml").read_text(encoding="utf-8")
        self.assertIn("- name: all_group_k10", main)
        launcher = Path("scripts/kibot_launch.sh").read_text(encoding="utf-8")
        self.assertNotRegex(launcher, r"^\s*eval", )
        self.assertIn("unsupported KiCad major version", launcher)
        self.assertNotIn('all_group="all_group"', launcher)


if __name__ == "__main__":
    unittest.main()
