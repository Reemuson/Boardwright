# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

import unittest
from pathlib import Path

from boardwright.build import VARIANTS, plan_build



# Diagnostic workflows build a reduced output set and never commit or publish;
# the output-pipeline parity rules below do not apply to them.
DIAGNOSTIC_WORKFLOWS = {"kicad-compat.yaml"}


def _pipeline_workflows() -> list[Path]:
    return sorted(
        path for path in Path(".github/workflows").glob("*.yaml") if path.name not in DIAGNOSTIC_WORKFLOWS
    )

class WorkflowTests(unittest.TestCase):
    def test_split_workflows_exist(self) -> None:
        expected = {
            "dev-preview.yaml": ("Boardwright Dev Preview", "Publish preview branch"),
            "main-outputs.yaml": ("Boardwright Accepted Outputs", "Commit accepted outputs"),
            "prepare-release.yaml": ("Boardwright Prepare Release Tag", "Create and push tag"),
            "release.yaml": ("Boardwright Publish Release", "Publish GitHub Release"),
        }

        for filename, markers in expected.items():
            workflow = Path(".github/workflows") / filename
            self.assertTrue(workflow.exists(), filename)
            text = workflow.read_text(encoding="utf-8")
            for marker in markers:
                self.assertIn(marker, text)

    def test_workflows_expose_src_to_kibot_python_helpers(self) -> None:
        for workflow in _pipeline_workflows():
            text = workflow.read_text(encoding="utf-8")

            self.assertIn("PYTHONPATH: ${{ github.workspace }}/src", text)
            self.assertNotIn("\n  PYTHONPATH: ${{ github.workspace }}/src\n          GH_TOKEN", text)

    def test_workflows_do_not_run_notes_as_standalone_target(self) -> None:
        for workflow in _pipeline_workflows():
            text = workflow.read_text(encoding="utf-8")

            self.assertNotIn("additional_args: --log kibot_preview_notes.log notes", text)
            self.assertNotIn("additional_args: --log kibot_main_notes.log notes", text)
            self.assertNotIn("additional_args: --log kibot_prepare_notes.log notes", text)
            self.assertNotIn("additional_args: --log kibot_release_notes.log notes", text)

    # The build pipeline itself lives in boardwright.build (shared by local
    # Docker and CI); these tests check it there. Each workflow only calls it.

    def test_pipeline_cleans_generated_outputs_after_kibot(self) -> None:
        for variant in VARIANTS:
            names = [step.name for step in plan_build(variant, "9").steps]
            last_kibot = max(i for i, step in enumerate(plan_build(variant, "9").steps) if step.argv[0] == "kibot")
            self.assertEqual("Clean generated outputs", names[-1])
            self.assertLess(last_kibot, names.index("Clean generated outputs"))

    def test_every_pipeline_workflow_calls_the_shared_build(self) -> None:
        for workflow in _pipeline_workflows():
            text = workflow.read_text(encoding="utf-8")
            self.assertRegex(text, r"python -m boardwright\.container (build|candidate)")

    def test_branch_mutating_workflows_discard_generated_source_side_effects(self) -> None:
        for filename in ("main-outputs.yaml", "prepare-release.yaml"):
            text = (Path(".github/workflows") / filename).read_text(encoding="utf-8")

            self.assertIn("Discard generated source side effects", text)
            # kibot_main.yaml and every other tracked side effect are restored,
            # fail-closed, by the shared helper (see test_ci_cleanup).
            self.assertIn("python -m boardwright.ci_cleanup", text)

    def test_pipeline_prepares_tables_before_full_outputs(self) -> None:
        for variant in ("PRELIMINARY", "CHECKED", "RELEASED"):
            steps = plan_build(variant, "9").steps
            names = [step.name for step in steps]
            main = names.index(f"KiBot {variant} outputs")
            for name in ("Prepare PDF table placeholders", "KiBot PDF table CSV sources",
                         "Prune empty conditional PDF pages", "Prepare combined PDF contents"):
                self.assertLess(names.index(name), main, name)
            tables = steps[names.index("KiBot PDF table CSV sources")].argv
            self.assertEqual("table_sources", tables[-1])
        self.assertNotIn("Prepare PDF table placeholders", [s.name for s in plan_build("DRAFT", "9").steps])

    def test_pipeline_regenerates_document_pdfs_with_document_metadata(self) -> None:
        steps = {step.name: step.argv for step in plan_build("CHECKED", "9").steps}
        fabrication = steps["Regenerate fabrication drawing"]
        assembly = steps["Regenerate assembly drawing"]
        self.assertIn("boardwright_resources/kibot/yaml/kibot_document_fabrication.generated.yaml", fabrication)
        self.assertEqual(("pdf_fabrication", "zip_compress_fab"), fabrication[-2:])
        self.assertIn("draw_fancy_stackup,erc,drc", fabrication)
        self.assertIn("boardwright_resources/kibot/yaml/kibot_document_assembly.generated.yaml", assembly)
        self.assertEqual("pdf_assembly", assembly[-1])

    def test_pipeline_installs_bundled_fonts_before_kibot(self) -> None:
        source = Path("src/boardwright/build.py").read_text(encoding="utf-8")
        self.assertIn('FONTS = "boardwright_resources/kibot/resources/fonts"', source)
        self.assertIn('"fc-cache"', source)
        self.assertLess(source.index("    install_fonts(root)"), source.index("    run_plan(plan, root, echo)"))

    def test_workflows_use_pinned_official_cache_action(self) -> None:
        for workflow in _pipeline_workflows():
            text = workflow.read_text(encoding="utf-8")

            self.assertIn("actions/cache@v4", text)
            self.assertNotIn("set-soft/cache@main", text)
            self.assertIn("restore-keys:", text)
            self.assertNotIn("hashFiles('**/*.kicad_pcb", text)
            self.assertNotIn("hashFiles('**/*.kicad_sch", text)

    def test_3d_cache_keys_do_not_depend_on_hash_files(self) -> None:
        for filename in (
            "dev-preview.yaml",
            "main-outputs.yaml",
            "prepare-release.yaml",
            "release.yaml",
        ):
            text = (Path(".github/workflows") / filename).read_text(encoding="utf-8")

            self.assertIn(
                "key: ${{ runner.os }}-kicad-${{ env.KICAD_VERSION }}-3d",
                text,
            )

    def test_prepare_release_caches_pip_downloads(self) -> None:
        text = Path(".github/workflows/prepare-release.yaml").read_text(encoding="utf-8")

        self.assertIn("Cache Python packages", text)
        self.assertIn("~/.cache/pip", text)

    def test_release_workflows_can_publish_curated_source_archive(self) -> None:
        cli = Path("src/boardwright/cli.py").read_text(encoding="utf-8")
        candidate = Path("src/boardwright/container.py").read_text(encoding="utf-8")
        release = Path(".github/workflows/release.yaml").read_text(encoding="utf-8")

        # prepare-release writes release.env through the shared candidate command.
        self.assertIn("RELEASE_INCLUDE_SOURCE_ARCHIVE", cli)
        self.assertIn('"--release-env"', candidate)
        self.assertIn("Build curated source archive", release)
        self.assertIn("boardwright-${{ github.ref_name }}-source.zip", release)
        package = Path("src/boardwright/release_package.py").read_text(encoding="utf-8")
        self.assertIn("Curated source package", package)
        self.assertIn("COPY_SET + (source_zip_name(tag),)", package)
        self.assertIn("RELEASE_INCLUDE_SOURCE_ARCHIVE", release)

    def test_preview_runs_are_explicitly_dispatched(self) -> None:
        text = Path(".github/workflows/dev-preview.yaml").read_text(encoding="utf-8")

        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("push:", text)

    def test_accept_workflow_builds_reviewed_source_and_pushes_target(self) -> None:
        text = Path(".github/workflows/main-outputs.yaml").read_text(encoding="utf-8")

        self.assertIn("source_ref:", text)
        self.assertIn("source_sha:", text)
        self.assertIn("source_label:", text)
        self.assertIn("target_branch:", text)
        self.assertIn("run-name: Accept", text)
        self.assertIn("Summarize accepted output request", text)
        self.assertIn("Verify reviewed source SHA", text)
        self.assertIn("ref: ${{ inputs.source_sha || inputs.source_ref || github.ref_name }}", text)
        self.assertIn("git merge --no-ff -X theirs \\", text)
        self.assertIn('-m "chore(outputs): accept ${source_sha:0:12} from ${IN_SOURCE_REF_OR_GH_REF_NAME}"', text)
        self.assertIn('            "$source_sha"\n', text)
        self.assertIn("git switch -C \"boardwright-accepted-${target_branch}\" \"origin/${target_branch}\"", text)
        self.assertIn("git push origin HEAD:\"$target_branch\"", text)
        self.assertIn(
            "No README snapshot changes to commit.\"\n            git push origin HEAD:\"$target_branch\"",
            text,
        )

    def test_main_mutating_workflows_are_serialized(self) -> None:
        accepted = Path(".github/workflows/main-outputs.yaml").read_text(encoding="utf-8")
        prepare = Path(".github/workflows/prepare-release.yaml").read_text(encoding="utf-8")

        self.assertIn("concurrency:", accepted)
        self.assertIn("group: boardwright-${{ inputs.target_branch || 'main' }}", accepted)
        self.assertIn("concurrency:", prepare)
        self.assertIn("group: boardwright-main", prepare)

    def test_workflow_run_names_are_descriptive(self) -> None:
        preview = Path(".github/workflows/dev-preview.yaml").read_text(encoding="utf-8")
        accepted = Path(".github/workflows/main-outputs.yaml").read_text(encoding="utf-8")
        prepare = Path(".github/workflows/prepare-release.yaml").read_text(encoding="utf-8")
        release = Path(".github/workflows/release.yaml").read_text(encoding="utf-8")

        self.assertIn("run-name: Preview", preview)
        self.assertIn("source_label", preview)
        self.assertIn("run-name: Accept", accepted)
        self.assertIn("source_label", accepted)
        self.assertIn("run-name: Prepare", prepare)
        self.assertIn("run-name: Publish", release)
        self.assertIn("GITHUB_STEP_SUMMARY", preview)
        self.assertIn("GITHUB_STEP_SUMMARY", accepted)

    def test_draft_pipeline_skips_heavy_preflights(self) -> None:
        for step in plan_build("DRAFT", "9").steps:
            if step.argv[0] == "kibot":
                self.assertIn("draw_fancy_stackup,erc,drc", step.argv)

    def test_prepare_release_force_adds_ignored_release_metadata(self) -> None:
        text = Path(".github/workflows/prepare-release.yaml").read_text(encoding="utf-8")

        # Staging moved to the shared module (docs/LOCAL_FIRST.md section 6).
        self.assertIn("python -m boardwright.release_commit", text)
        module = Path("src/boardwright/release_commit.py").read_text(encoding="utf-8")
        self.assertIn('".boardwright/revision_history_variables.env", ".boardwright/release.env"', module)
        self.assertIn('"CHANGELOG.md", ".boardwright/project.yaml", ".boardwright/document_revisions.yaml"', module)
        self.assertIn('_git(root, "add", "-f", *group)', module)

    def test_release_notes_do_not_duplicate_tag_heading(self) -> None:
        text = Path(".github/workflows/release.yaml").read_text(encoding="utf-8")

        module = Path("src/boardwright/release_package.py").read_text(encoding="utf-8")
        self.assertIn("python -m boardwright.release_package notes", text)
        self.assertNotIn('echo "# ${tag}"', text)
        self.assertIn('out = [f"Generated `{variant}` package for `{repository}`.\\n", "\\n"]', module)

    def test_prepare_release_can_dispatch_draft_release(self) -> None:
        prepare = Path(".github/workflows/prepare-release.yaml").read_text(encoding="utf-8")
        release = Path(".github/workflows/release.yaml").read_text(encoding="utf-8")

        self.assertIn("- draft", prepare)
        self.assertIn("default: prerelease", prepare)
        self.assertIn("release_kind", prepare)
        self.assertIn("drawing_revision", prepare)
        self.assertIn("revision_description", prepare)
        self.assertIn("--drawing-revision", prepare)
        self.assertIn("--revision-description", prepare)
        self.assertIn("draft: ${{ env.RELEASE_KIND == 'draft' }}", release)
        self.assertIn("RELEASE_KIND: release", release)


if __name__ == "__main__":
    unittest.main()
