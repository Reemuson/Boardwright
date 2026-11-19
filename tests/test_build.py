# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""The shared output build (python -m boardwright build): plan, preflight, side effects."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_revision_model import _git, _rmtree  # noqa: E402

from boardwright import build  # noqa: E402
from boardwright.build import BuildError, crlf_checkout_problems, plan_build, preflight  # noqa: E402


def _names(plan) -> list[str]:
    return [step.name for step in plan.steps]


def _kibot_steps(plan) -> list[tuple[str, ...]]:
    return [step.argv for step in plan.steps if step.argv[0] == "kibot"]


class PlanTests(unittest.TestCase):
    def test_sheet_check_runs_before_any_kibot_step(self) -> None:
        for variant in build.VARIANTS:
            plan = plan_build(variant, "9")
            names = _names(plan)
            first_kibot = next(i for i, step in enumerate(plan.steps) if step.argv[0] == "kibot")
            self.assertEqual("Check drawing sheets and branding", names[0], variant)
            self.assertLess(0, first_kibot)

    def test_checked_and_released_run_erc_drc_and_document_regeneration(self) -> None:
        for variant in ("CHECKED", "RELEASED"):
            main = [argv for argv in _kibot_steps(plan_build(variant, "9")) if "all_group_k9" in argv]
            self.assertEqual(1, len(main))
            self.assertNotIn("--skip-pre", main[0])
            self.assertIn(f"variant={variant}", main[0])
            configs = [argv[argv.index("-c") + 1] for argv in _kibot_steps(plan_build(variant, "9"))]
            self.assertIn(build.FABRICATION_DOCUMENT_CONFIG, configs)
            self.assertIn(build.ASSEMBLY_DOCUMENT_CONFIG, configs)

    def test_preliminary_skips_only_erc_and_drc(self) -> None:
        main = [argv for argv in _kibot_steps(plan_build("PRELIMINARY", "9")) if "all_group_k9" in argv][0]
        self.assertEqual("erc,drc", main[main.index("--skip-pre") + 1])

    def test_draft_builds_table_sources_before_the_readme(self) -> None:
        # md_readme configures pdf_fabrication, which needs the table CSVs.
        plan = plan_build("DRAFT", "9")
        kibot = _kibot_steps(plan)
        self.assertEqual(2, len(kibot))
        self.assertEqual("table_sources", kibot[0][-1])
        self.assertEqual(("draft_group", "md_readme"), kibot[1][-2:])
        self.assertNotIn("Generate revision table variables", _names(plan))
        self.assertNotIn("Prepare PDF table placeholders", _names(plan))

    def test_kicad10_selects_its_output_group(self) -> None:
        self.assertTrue(any("all_group_k10" in argv for argv in _kibot_steps(plan_build("CHECKED", "10"))))
        self.assertFalse(any("all_group_k9" in argv for argv in _kibot_steps(plan_build("CHECKED", "10"))))

    def test_log_prefix_names_every_kibot_log(self) -> None:
        for argv in _kibot_steps(plan_build("CHECKED", "9", "prepare")):
            self.assertTrue(argv[argv.index("--log") + 1].startswith("kibot_prepare"))

    def test_kibot_is_called_directly_without_git_filters(self) -> None:
        for step in plan_build("CHECKED", "9").steps:
            self.assertNotIn("kicad-git-filters", " ".join(step.argv))

    def test_unknown_variant_or_kicad_is_rejected(self) -> None:
        with self.assertRaises(BuildError):
            plan_build("NIGHTLY", "9")
        with self.assertRaises(BuildError):
            plan_build("CHECKED", "8")


class CrlfTests(unittest.TestCase):
    def test_lf_index_with_crlf_worktree_is_reported(self) -> None:
        listing = (
            "i/lf    w/crlf  attr/text=auto         \tboard.kicad_pcb\n"
            "i/lf    w/lf    attr/text=auto         \tok.kicad_sch\n"
            "i/lf    w/crlf  attr/text eol=crlf     \trun.bat\n"
            "i/lf    w/mixed attr/text=auto         \tmixed.txt\n"
            "i/-text w/-text attr/-text             \tlogo.png\n"
        )
        self.assertEqual(["board.kicad_pcb", "mixed.txt"], crlf_checkout_problems(listing))


class PreflightAndBuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="bw-build-"))
        _git(self.root, "init", "-q")
        _git(self.root, "config", "core.autocrlf", "false")
        _git(self.root, "config", "user.name", "t")
        _git(self.root, "config", "user.email", "t@example.invalid")
        (self.root / ".gitattributes").write_text("* text=auto\n", encoding="utf-8", newline="\n")
        (self.root / "board.kicad_pcb").write_text("(kicad_pcb)\n", encoding="utf-8", newline="\n")
        (self.root / "README.md").write_text("old\n", encoding="utf-8", newline="\n")
        _git(self.root, "add", ".")
        _git(self.root, "commit", "-q", "-m", "init")
        provenance = patch.object(
            build, "source_provenance", return_value={"boardwright_source": {"package": "src/boardwright", "tree": "t1"}, "kibot_config_tree": "t2"}
        )
        provenance.start()
        self.addCleanup(provenance.stop)

    def tearDown(self) -> None:
        _rmtree(self.root)

    def test_local_overrides_are_refused(self) -> None:
        (self.root / ".boardwright").mkdir()
        (self.root / ".boardwright" / "local.yaml").write_text("template: {}\n", encoding="utf-8")
        with self.assertRaisesRegex(BuildError, "local.yaml"):
            preflight(self.root)

    def test_crlf_checkout_is_refused(self) -> None:
        (self.root / "board.kicad_pcb").write_bytes(b"(kicad_pcb)\r\n")
        with self.assertRaisesRegex(BuildError, "CRLF"):
            preflight(self.root)

    def test_clean_lf_checkout_passes(self) -> None:
        source = preflight(self.root)
        self.assertEqual(_git(self.root, "rev-parse", "HEAD").strip(), source["commit"])
        self.assertEqual([], source["modified_before_build"])

    def _fake_build(self, step_effect) -> Path:
        def fake_run(plan, root, echo=print):
            step_effect(root)
            (root / "Manufacturing" / "Fabrication" / "Gerbers").mkdir(parents=True)
            (root / "Manufacturing" / "Fabrication" / "Gerbers" / "b-F_Cu.gbr").write_text("%FSLAX46Y46*%\nX0Y0D02*\n", encoding="utf-8")

        with patch.object(build, "detect_kicad", return_value="9.0.7"), patch.object(build, "install_fonts"), patch.object(
            build, "run_plan", side_effect=fake_run
        ), patch.object(build, "tool_versions", return_value={"kicad": "9.0.7"}), patch.dict(os.environ, {"BOARDWRIGHT_IMAGE": "repo:tag@sha256:abc", "BOARDWRIGHT_IMAGE_ID": "sha256:def"}):
            return build.build(self.root, "DRAFT", "9", "test", (), echo=lambda *_: None)

    def test_tracked_side_effects_are_restored_and_evidence_written(self) -> None:
        def rewrite(root: Path) -> None:
            (root / "board.kicad_pcb").write_text("(kicad_pcb rewritten)\n", encoding="utf-8")
            (root / "README.md").write_text("generated\n", encoding="utf-8")

        out = self._fake_build(rewrite)
        self.assertEqual("(kicad_pcb)\n", (self.root / "board.kicad_pcb").read_text(encoding="utf-8"))
        self.assertEqual("generated\n", (self.root / "README.md").read_text(encoding="utf-8"), "README is a kept output")
        record = json.loads((out / "record.json").read_text(encoding="utf-8"))
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(_git(self.root, "rev-parse", "HEAD").strip(), record["source_commit"])
        self.assertEqual("sha256:def", record["image_id"])
        self.assertEqual("repo:tag@sha256:abc", record["image"])
        self.assertEqual("t1", record["boardwright_source"]["tree"])
        self.assertEqual("t2", record["configuration"]["kibot_config_tree"])
        self.assertTrue(all(step["argv"][0] != sys.executable for step in record["steps"]))
        self.assertEqual("fabrication", manifest["files"]["Manufacturing/Fabrication/Gerbers/b-F_Cu.gbr"]["category"])

    def test_gitattributes_mutation_is_restored(self) -> None:
        def mutate(root: Path) -> None:
            with (root / ".gitattributes").open("a", encoding="utf-8", newline="\n") as handle:
                handle.write("*.kicad_pcb filter=kicad_pcb_f\n")

        self._fake_build(mutate)
        self.assertEqual("* text=auto\n", (self.root / ".gitattributes").read_text(encoding="utf-8"))

    def test_boardwright_imported_from_elsewhere_is_refused(self) -> None:
        # self.root has no src/boardwright, so the running package is not the checkout's.
        with self.assertRaisesRegex(BuildError, "not from the checkout"):
            build.boardwright_source(self.root)

    def test_checkout_boardwright_source_is_recorded(self) -> None:
        repo = Path(build.__file__).resolve().parents[2]
        self.assertEqual("src/boardwright", build.boardwright_source(repo)["package"])

    def test_kicad_version_mismatch_fails_before_any_step(self) -> None:
        with patch.object(build, "detect_kicad", return_value="10.0.5"), patch.object(build, "run_plan") as run:
            with self.assertRaisesRegex(BuildError, "Requested KiCad 9"):
                build.build(self.root, "DRAFT", "9", "test", (), echo=lambda *_: None)
        run.assert_not_called()

    def test_failing_step_stops_the_build(self) -> None:
        plan = build.BuildPlan("DRAFT", "9", [
            build.Step("ok", (sys.executable, "-c", "pass")),
            build.Step("fails", (sys.executable, "-c", "raise SystemExit(3)")),
            build.Step("never", (sys.executable, "-c", "open('never', 'w')")),
        ])
        with self.assertRaisesRegex(BuildError, "fails"):
            build.run_plan(plan, self.root, echo=lambda *_: None)
        self.assertFalse((self.root / "never").exists())

    def test_missing_readme_fails_full_variants(self) -> None:
        (self.root / "README.md").unlink()
        _git(self.root, "commit", "-qam", "drop readme")
        with patch.object(build, "detect_kicad", return_value="9.0.7"), patch.object(build, "install_fonts"), patch.object(build, "run_plan"):
            with self.assertRaisesRegex(BuildError, "README.md was not generated"):
                build.build(self.root, "CHECKED", "9", "test", (), echo=lambda *_: None)


if __name__ == "__main__":
    unittest.main()
