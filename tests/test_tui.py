import unittest

from boardwright.accepted import AcceptedMainState
from boardwright.actions import WorkflowRunStatus
from boardwright import tui
from boardwright.config import load_config
from boardwright.preview import PreviewRun, evaluate_preview_state
from boardwright.validation import ValidationIssue


class TuiTests(unittest.TestCase):
    def test_textual_is_optional(self) -> None:
        self.assertIsInstance(tui.textual_available(), bool)
        self.assertIn("pip install", tui.INSTALL_HINT)

    def test_dashboard_state_collects(self) -> None:
        state = tui.collect_dashboard_state()

        self.assertTrue(state.status.project_id)
        self.assertIn("->", state.preview_summary)
        self.assertIsInstance(state.changed_files, tuple)

    def test_notification_severity(self) -> None:
        self.assertEqual(
            "warning",
            tui._notification_severity((ValidationIssue("warning", "Careful"),)),
        )
        self.assertEqual(
            "error",
            tui._notification_severity((ValidationIssue("error", "Broken"),)),
        )

    def test_issue_summary(self) -> None:
        self.assertEqual("validation ok", tui._issue_summary(()))
        self.assertIn(
            "warning",
            tui._issue_summary((ValidationIssue("warning", "Careful"),)),
        )

    def test_timeline_contains_release_steps(self) -> None:
        state = tui.collect_dashboard_state()
        text = tui._format_timeline(tui._workflow_steps(state)).plain

        self.assertIn("Edit in KiCad", text)
        self.assertIn("Record changes", text)
        self.assertIn("Preview CI", text)
        self.assertIn("Accept to main", text)
        self.assertEqual(tui._workflow_steps(state), state.workflow.steps)

    def test_inspector_shows_next_action(self) -> None:
        state = tui.collect_dashboard_state()
        text = tui._format_inspector(state).plain

        self.assertTrue(text.strip())
        self.assertIn("EVIDENCE", text)
        self.assertIn("RELEASE", text)
        self.assertIn("Preview CI is dispatched manually", text)
        self.assertIn("Stage:", text)

    def test_ci_status_shortens(self) -> None:
        self.assertEqual("CI not polled", tui._ci_status_short("CI not polled"))
        self.assertLessEqual(len(tui._ci_status_short("x" * 80)), 36)
        self.assertEqual(
            "preview CHECKED running",
            tui._ci_status_short("RUNNING | boardwright-preview-CHECKED"),
        )
        self.assertEqual(
            "preview CHECKED ready",
            tui._ci_status_short(
                "Artifact: boardwright-preview-CHECKED\nState: ready\nRun: 42\nReviewed: yes"
            ),
        )
        self.assertEqual(
            "preview CHECKED review needed",
            tui._ci_status_short(
                "Artifact: boardwright-preview-CHECKED\nState: ready\nRun: 42\nReviewed: no"
            ),
        )

    def test_top_status_is_rich_text(self) -> None:
        state = tui.collect_dashboard_state()

        self.assertTrue(tui._format_top_status(state.status, state.issues, "CI not polled").plain)

    def test_ci_status_style_prioritizes_active_work(self) -> None:
        self.assertEqual(
            "bold yellow",
            tui._ci_status_style("Preview:CHECKED ready | Accept:ready | Release:prepare running"),
        )
        self.assertEqual("bold green", tui._ci_status_style("Preview:CHECKED ready | Accept:ready"))
        self.assertEqual("bold red", tui._ci_status_style("Preview:CHECKED ready | Accept:failed"))

    def test_review_artifact_summary_contains_evidence(self) -> None:
        run = PreviewRun(
            database_id="42",
            status="completed",
            conclusion="success",
            branch="dev",
            head_sha="abcdef",
            created_at="2026-05-23T00:00:00Z",
            title="preview",
        )
        preview_state = evaluate_preview_state((run,), "abcdef", "CHECKED")

        text = tui._format_review_artifacts(preview_state, "Boardwright Dev Preview: completed/success")

        self.assertIn("READY | boardwright-preview-CHECKED", text)
        self.assertIn("Recent CI", text)
        self.assertIn("Run 42", text)

    def test_polled_ci_status_summarizes_preview_accepted_and_release(self) -> None:
        run = PreviewRun(
            database_id="42",
            status="completed",
            conclusion="success",
            branch="dev",
            head_sha="abcdef",
            created_at="2026-05-23T00:00:00Z",
            title="preview",
        )
        preview_state = evaluate_preview_state((run,), "abcdef", "CHECKED")
        accepted_state = AcceptedMainState(
            state="missing",
            workflow="main-outputs.yaml",
            expected_sha="abcdef",
            message="No accepted outputs.",
        )

        text = tui._format_polled_ci_status(
            preview_state,
            accepted_state,
            release_status="Release: prepare in_progress run 77 Prepare 0.1.3",
        )

        self.assertIn("CI: Preview:CHECKED review | Accept:missing | Release:prepare running", text)
        self.assertIn("Artifact: boardwright-preview-CHECKED", text)
        self.assertIn("Accepted main:", text)
        self.assertIn("No accepted outputs.", text)
        self.assertIn("Release: prepare in_progress run 77", text)

    def test_inspector_splits_ci_evidence_by_workflow(self) -> None:
        state = tui.collect_dashboard_state()
        ci_status = (
            "CI: Preview:CHECKED ready | Accept:ready | Release:prepare running\n\n"
            "Preview:\nArtifact: boardwright-preview-CHECKED\n"
            "Accepted main:\nState: ready\n"
            "Release: prepare in_progress run 77 Prepare 0.1.3"
        )

        text = tui._format_inspector(state, ci_status).plain

        self.assertIn("Preview: CHECKED ready", text)
        self.assertIn("Accept: ready", text)
        self.assertIn("Release: prepare running", text)
        self.assertNotIn("Preview: CHECKED ready | Accept:ready", text)

    def test_release_ci_status_from_recent_runs(self) -> None:
        runs = (
            WorkflowRunStatus(
                workflow="Boardwright Prepare Release Tag",
                status="in_progress",
                conclusion="",
                branch="main",
                title="Prepare 0.1.3 (CHECKED, release)",
                database_id="77",
            ),
            WorkflowRunStatus(
                workflow="Boardwright Publish Release",
                status="completed",
                conclusion="success",
                branch="0.1.2",
                title="Publish 0.1.2 release package",
                database_id="72",
            ),
        )

        text = tui._release_ci_status_from_runs(runs)

        self.assertIn("Release: prepare in_progress run 77", text)
        self.assertIn("publish success run 72", text)

    def test_review_artifact_summary_shows_selected_variant_artifact(self) -> None:
        run = PreviewRun(
            database_id="42",
            status="completed",
            conclusion="success",
            branch="dev",
            head_sha="abcdef",
            created_at="2026-05-23T00:00:00Z",
            title="preview",
        )
        preview_state = evaluate_preview_state((run,), "abcdef", "PRELIMINARY")

        text = tui._format_review_artifacts(preview_state, "Boardwright Dev Preview: completed/success")

        self.assertIn("READY | boardwright-preview-PRELIMINARY", text)

    def test_review_artifact_blocks_are_hierarchical(self) -> None:
        run = PreviewRun(
            database_id="42",
            status="completed",
            conclusion="success",
            branch="dev",
            head_sha="abcdef",
            created_at="2026-05-23T00:00:00Z",
            title="preview",
        )
        preview_state = evaluate_preview_state((run,), "abcdef", "CHECKED")

        status, message, run_summary = tui._review_artifact_blocks(preview_state)

        self.assertIn("READY | boardwright-preview-CHECKED", status)
        self.assertIn("fresh", message)
        self.assertIn("Run 42", run_summary)

    def test_download_progress_text_mentions_variant(self) -> None:
        text = tui._download_progress_text("PRELIMINARY")

        self.assertIn("boardwright-preview-PRELIMINARY", text)
        self.assertIn("[###.......]", text)

    def test_release_checklist_blocks_unready_accepted_outputs(self) -> None:
        accepted_state = AcceptedMainState(
            state="stale",
            workflow="main-outputs.yaml",
            expected_sha="abcdef",
            message="Accepted outputs are stale.",
        )

        checklist = tui.build_release_checklist(
            load_config(),
            "0.1.2",
            "RELEASED",
            "release",
            accepted_state,
        )

        text = tui._format_release_checklist(checklist)
        self.assertFalse(checklist.can_dispatch)
        self.assertIn("[ ] Accepted main outputs", text)
        self.assertIn("Blockers:", text)
        self.assertIn("Resolve blocking items", text)

    def test_release_checklist_reports_invalid_release_inputs(self) -> None:
        accepted_state = AcceptedMainState(
            state="ready",
            workflow="main-outputs.yaml",
            expected_sha="abcdef",
            message="Accepted main outputs are fresh.",
        )

        checklist = tui.build_release_checklist(
            load_config(),
            "v0.1.2",
            "RELEASED",
            "release",
            accepted_state,
        )

        text = tui._format_release_checklist(checklist)
        self.assertFalse(checklist.can_dispatch)
        self.assertIn("Release version must use semantic form", text)
        self.assertIn("[ ] Release inputs", text)


if __name__ == "__main__":
    unittest.main()
