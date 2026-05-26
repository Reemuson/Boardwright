import unittest
from pathlib import Path


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

    def test_workflows_do_not_run_notes_as_standalone_target(self) -> None:
        for workflow in Path(".github/workflows").glob("*.yaml"):
            text = workflow.read_text(encoding="utf-8")

            self.assertNotIn("additional_args: --log kibot_preview_notes.log notes", text)
            self.assertNotIn("additional_args: --log kibot_main_notes.log notes", text)
            self.assertNotIn("additional_args: --log kibot_prepare_notes.log notes", text)
            self.assertNotIn("additional_args: --log kibot_release_notes.log notes", text)

    def test_workflows_clean_generated_outputs_after_kibot(self) -> None:
        for workflow in Path(".github/workflows").glob("*.yaml"):
            text = workflow.read_text(encoding="utf-8")

            self.assertIn("Normalize generated output ownership", text)
            self.assertIn("sudo chown -R", text)
            self.assertIn("Clean generated outputs", text)
            self.assertIn("clean_generated_outputs.py", text)

    def test_branch_mutating_workflows_discard_generated_source_side_effects(self) -> None:
        for filename in ("main-outputs.yaml", "prepare-release.yaml"):
            text = (Path(".github/workflows") / filename).read_text(encoding="utf-8")

            self.assertIn("Discard generated source side effects", text)
            self.assertIn(
                "git checkout HEAD -- boardwright_resources/kibot/yaml/kibot_main.yaml",
                text,
            )

    def test_workflows_prepare_tables_before_full_outputs(self) -> None:
        for workflow in Path(".github/workflows").glob("*.yaml"):
            text = workflow.read_text(encoding="utf-8")

            self.assertIn("Prepare Boardwright PDF table placeholders", text)
            self.assertIn("prepare_pcb_tables.py", text)
            self.assertIn("Generate KiBot PDF table CSV sources", text)
            self.assertIn("table_sources", text)
            self.assertIn("env.KIBOT_VARIANT != 'DRAFT'", text)
            self.assertIn("Prune empty conditional PDF pages", text)
            self.assertIn("prune_pdf_pages.py", text)

    def test_workflows_install_bundled_fonts_before_kibot(self) -> None:
        for workflow in Path(".github/workflows").glob("*.yaml"):
            text = workflow.read_text(encoding="utf-8")

            self.assertIn("Install bundled KiCad/PDF fonts", text)
            self.assertIn("boardwright_resources/kibot/resources/fonts/*.ttf", text)
            self.assertIn("fc-cache -f ~/.local/share/fonts", text)

    def test_workflows_use_pinned_official_cache_action(self) -> None:
        for workflow in Path(".github/workflows").glob("*.yaml"):
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
        self.assertIn("git merge --no-ff --no-edit -X theirs \"$source_sha\"", text)
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

    def test_draft_workflows_skip_heavy_preflights(self) -> None:
        for workflow in Path(".github/workflows").glob("*.yaml"):
            text = workflow.read_text(encoding="utf-8")

            self.assertIn("--skip-pre draw_fancy_stackup,erc,drc", text)

    def test_prepare_release_force_adds_ignored_release_metadata(self) -> None:
        text = Path(".github/workflows/prepare-release.yaml").read_text(encoding="utf-8")

        self.assertIn(
            "git add -f .boardwright/revision_history_variables.env .boardwright/release.env",
            text,
        )

    def test_release_notes_do_not_duplicate_tag_heading(self) -> None:
        text = Path(".github/workflows/release.yaml").read_text(encoding="utf-8")

        self.assertNotIn('echo "# ${tag}"', text)
        self.assertIn('echo "Generated \\`${RELEASE_VARIANT}\\` package', text)


if __name__ == "__main__":
    unittest.main()
