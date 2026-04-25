import unittest

from boardwright import tui
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

    def test_inspector_shows_next_action(self) -> None:
        state = tui.collect_dashboard_state()
        text = tui._format_inspector(state)

        self.assertTrue(text.strip())
        self.assertIn("Latest CI", text)
        self.assertIn("Preview runs from dev pushes", text)

    def test_ci_status_shortens(self) -> None:
        self.assertEqual("CI not polled", tui._ci_status_short("CI not polled"))
        self.assertLessEqual(len(tui._ci_status_short("x" * 80)), 36)

    def test_top_status_is_rich_text(self) -> None:
        state = tui.collect_dashboard_state()

        self.assertTrue(tui._format_top_status(state.status, state.issues, "CI not polled").plain)


if __name__ == "__main__":
    unittest.main()
