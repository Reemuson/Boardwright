# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""The TUI's local-release screen: shared engine, confirmation, concurrency, interruption, parity.

Ordinary UI tests mock the irreversible operations. The interaction tests
drive the real TUI app through the whole lifecycle in disposable repositories
and release stores (``local_release_fixtures``: the container replaced by a
deterministic engine; Git, the store, the review gate and packaging are real).
"""

from __future__ import annotations

import asyncio
import os
import unittest
from pathlib import Path
from unittest import mock

from boardwright import backends, local_finalize, local_release, local_release_ops, tui
from boardwright.local_release_ops import LocalArgs

try:
    from tests.local_release_fixtures import Case, Interrupted
    from tests.test_local_release import DATE, _git
except ImportError:  # unittest discover -s tests
    from local_release_fixtures import Case, Interrupted
    from test_local_release import DATE, _git

NO_REMOTE = lambda root, **kw: backends.RemoteInfo("none")  # noqa: E731
GITHUB = lambda root, **kw: backends.RemoteInfo("github", "https://github.com/o/r.git", "o/r", workflows_present=True)  # noqa: E731
UNSUPPORTED = lambda root, **kw: backends.RemoteInfo("unsupported", "https://gitlab.com/o/r.git",  # noqa: E731
                                                     problems=("origin is not a GitHub repository",))
DOCKER = lambda **kw: backends.DockerInfo(True, "fake Docker")  # noqa: E731
NO_DOCKER = lambda **kw: backends.DockerInfo(False, "the Docker engine is not running")  # noqa: E731


def _needs_textual(test):
    return unittest.skipUnless(tui.textual_available(), "Textual is not installed")(test)


LONG_TEXT = "\n".join(f"line {n}" for n in range(80))


async def _idle(app, pilot, seconds: float = 60.0) -> None:
    for _ in range(int(seconds / 0.05)):
        await pilot.pause(0.05)
        if not getattr(app, "_release_running", False):
            await pilot.pause(0.05)
            return
    raise AssertionError("the local release operation did not finish")


class SharedEngineTests(unittest.TestCase):
    """No policy in the front ends: one dispatcher, the same calls for every operation."""

    CLI = {
        "plan": ["plan", "1.0.0", "--backend", "local"],
        "candidate": ["candidate", "1.0.0", "--drawing-revision", "A", "--revision-description", "Release",
                      "--release-date", DATE, "--backend", "local"],
        "review": ["review", "1.0.0"],
        "approve": ["approve", "1.0.0", "--record", "REVIEW.md", "--backend", "local"],
        "finalize": ["finalize", "1.0.0", "--sign", "--backend", "local"],
        "publish": ["publish", "1.0.0", "--backend", "local"],
        "status": ["status", "1.0.0"],
        "recover": ["recover", "1.0.0"],
    }
    TUI = {
        "plan": LocalArgs(version="1.0.0", backend="local"),
        "candidate": LocalArgs(version="1.0.0", backend="local", drawing_revision="A", revision_description="Release",
                               release_date=DATE),
        "review": LocalArgs(version="1.0.0", backend="local"),
        "approve": LocalArgs(version="1.0.0", backend="local", record=Path("REVIEW.md")),
        "finalize": LocalArgs(version="1.0.0", backend="local", sign=True),
        "publish": LocalArgs(version="1.0.0", backend="local"),
        "status": LocalArgs(version="1.0.0", backend="local"),
        "recover": LocalArgs(version="1.0.0", backend="local"),
    }

    def test_every_operation_has_cli_and_tui_entry_points_with_the_same_arguments(self) -> None:
        self.assertEqual(set(local_release_ops.OPERATIONS), set(self.CLI))
        self.assertEqual(set(local_release_ops.OPERATIONS), {op for _l, op in self._tui_operations()})
        for operation in local_release_ops.OPERATIONS:
            with self.subTest(operation=operation), \
                    mock.patch.object(local_release_ops, "run", return_value=0) as run, \
                    mock.patch("boardwright.config.load_config", return_value=mock.sentinel.config):
                self.assertEqual(0, local_release.main(self.CLI[operation]))
                tui.run_shared_local_release(mock.sentinel.config, operation, self.TUI[operation], print)
                (cli_call, tui_call) = run.call_args_list
                self.assertEqual(cli_call.args[:2], tui_call.args[:2])
                cli_args, tui_args = cli_call.args[2], tui_call.args[2]
                if operation in ("status", "recover", "review"):
                    tui_args.backend = cli_args.backend  # these never select a backend
                self.assertEqual(cli_args, tui_args)

    def _tui_operations(self):
        if not tui.textual_available():
            self.skipTest("Textual is not installed")
        source = Path(tui.__file__).read_text(encoding="utf-8")
        start = source.index('OPERATIONS = (("Status"')
        namespace: dict = {}
        exec(source[start:source.index("\n\n", start)].strip(), namespace)  # the screen's button table
        return namespace["OPERATIONS"]

    def test_dispatcher_calls_the_engine_once_per_operation(self) -> None:
        config = mock.Mock(root=Path("."), project={})
        calls = []
        store = mock.Mock(exists=lambda: True)
        patches = {
            "boardwright.local_release.plan": lambda *a, **k: (calls.append("plan"),
                                                               mock.Mock(notes=[], problems=[], head="0" * 40))[1],
            "boardwright.local_release.build_candidate": lambda *a, **k: calls.append("candidate"),
            "boardwright.local_release.write_review_draft": lambda *a, **k: calls.append("review") or Path("d.md"),
            "boardwright.local_release.approve": lambda *a, **k: calls.append("approve"),
            "boardwright.local_finalize.finalize": lambda *a, **k: calls.append("finalize"),
            "boardwright.local_publish.publish": lambda *a, **k: calls.append("publish"),
            "boardwright.local_recovery.status_lines": lambda *a, **k: calls.append("status") or [],
            "boardwright.local_recovery.recover": lambda *a, **k: calls.append("recover") or 0,
        }
        with mock.patch.object(local_release_ops, "store_for", return_value=store), \
                mock.patch.object(local_release, "select_local_backend",
                                  return_value=mock.Mock(reason="test", warnings=())):
            stack = [mock.patch(name, side_effect=fn) for name, fn in patches.items()]
            for patcher in stack:
                patcher.start()
            try:
                for operation in local_release_ops.OPERATIONS:
                    args = LocalArgs(version="1.0.0", record=Path("r.md"))
                    self.assertEqual(0, local_release_ops.run(config, operation, args, echo=lambda _l: None))
            finally:
                for patcher in stack:
                    patcher.stop()
        self.assertEqual(list(local_release_ops.OPERATIONS), calls)

    def test_irreversible_operations_are_exactly_the_writers(self) -> None:
        self.assertEqual({"candidate", "approve", "finalize", "publish", "recover"}, set(local_release_ops.IRREVERSIBLE))
        self.assertTrue(set(local_release_ops.READ_ONLY).isdisjoint(local_release_ops.IRREVERSIBLE))
        config = mock.Mock(release_branch="main")
        texts = {op: local_release_ops.confirmation_text(config, op, LocalArgs(version="1.0.0", record=Path("r.md")))
                 for op in local_release_ops.IRREVERSIBLE}
        self.assertIn("not server-enforced", texts["approve"])
        self.assertIn("asserted, not authenticated", texts["approve"])
        self.assertIn("not pushed", texts["finalize"])
        self.assertIn("unsigned", texts["finalize"])
        self.assertIn("cannot be cancelled", texts["publish"])
        self.assertIn("never reset", texts["recover"])

    def test_backend_overview_is_offline_and_never_chooses_for_the_user(self) -> None:
        config = mock.Mock(root=Path("."), project={})
        seen = []

        def remote(root, **kw):
            seen.append(kw)
            return NO_REMOTE(root)

        overview = local_release_ops.backend_overview(config, inspect_remote=remote)
        self.assertEqual([{"check_access": False}], seen)  # never contacts GitHub
        self.assertEqual(("local", local_release_ops.LOCAL_LABEL, False),
                         (overview.default, overview.label, overview.github_available))
        self.assertIn("No GitHub remote", tui.release_backend_text(overview))
        overview = local_release_ops.backend_overview(config, inspect_remote=GITHUB)
        self.assertEqual(("github", local_release_ops.GITHUB_LABEL, True),
                         (overview.default, overview.label, overview.github_available))
        self.assertIn("local-only", tui.release_backend_text(overview))
        overview = local_release_ops.backend_overview(config, inspect_remote=UNSUPPORTED)
        self.assertEqual(("", False), (overview.default, overview.github_available))
        self.assertTrue(overview.problems)

    def test_cli_errors_go_to_stderr_with_the_same_exit_status(self) -> None:
        with mock.patch("boardwright.config.load_config", return_value=mock.Mock(root=Path("."), project={})), \
                mock.patch.object(local_release_ops, "store_for", side_effect=local_release.LocalReleaseError("boom")), \
                mock.patch("sys.stderr") as stderr:
            self.assertEqual(2, local_release.main(["status"]))
        self.assertIn("error: boom", "".join(c.args[0] for c in stderr.write.call_args_list))


@_needs_textual
class ScreenTests(unittest.TestCase):
    """UI behaviour with every operation mocked: nothing irreversible runs."""

    def run_app(self, body, overview_remote=NO_REMOTE, size=(110, 40), summaries=("Store: test", [])):
        async def check() -> None:
            app = tui._build_textual_app()()
            real_overview = local_release_ops.backend_overview
            with mock.patch.object(local_release_ops, "backend_overview",
                                   side_effect=lambda config, **kw: real_overview(
                                       config, inspect_remote=overview_remote)), \
                    mock.patch.object(local_release_ops, "summaries", return_value=summaries), \
                    mock.patch.object(tui, "run_shared_local_release", return_value=0) as run, \
                    mock.patch.object(tui, "dispatch_workflow_action") as dispatch:
                async with app.run_test(size=size) as pilot:
                    await pilot.pause(0.2)
                    await body(app, pilot, run)
                dispatch.assert_not_called()  # a local operation never dispatches GitHub

        asyncio.run(check())

    def test_backend_screen_labels_and_github_unavailable_without_a_remote(self) -> None:
        async def body(app, pilot, run):
            app.action_release_ci()
            await pilot.pause(0.2)
            screen = app.screen
            self.assertEqual("ReleaseBackendScreen", type(screen).__name__)
            self.assertTrue(screen.query_one("#release_backend_github").disabled)
            self.assertIn("(default)", str(screen.query_one("#release_backend_local").label))
            self.assertIn("not server-enforced", str(screen.query_one("#release_backend_local").label))
            screen.dismiss("local")
            await pilot.pause(0.3)
            self.assertEqual("LocalReleaseScreen", type(app.screen).__name__)
            self.assertIn("not server-enforced", str(app.screen.query_one(".section-title").render()))
            self.assertIn("asserted, not authenticated", str(app.screen.query_one("#local_release_notice").render()))
            run.assert_not_called()

        self.run_app(body)

    def test_choosing_local_on_a_github_project_is_an_explicit_override(self) -> None:
        async def body(app, pilot, run):
            app.action_release_ci()
            await pilot.pause(0.2)
            self.assertFalse(app.screen.query_one("#release_backend_github").disabled)
            self.assertIn("(default)", str(app.screen.query_one("#release_backend_github").label))
            app.screen.dismiss("local")
            await pilot.pause(0.3)
            app.screen.dismiss(("plan", LocalArgs(version="1.0.0")))
            await _idle(app, pilot)
            self.assertEqual("local", run.call_args.args[2].backend)

        self.run_app(body, overview_remote=GITHUB)

    def test_every_irreversible_operation_needs_confirmation_and_cancel_runs_nothing(self) -> None:
        async def body(app, pilot, run):
            app._release_overview = local_release_ops.backend_overview(mock.Mock(root=Path("."), project={}),
                                                                       inspect_remote=NO_REMOTE)
            for operation in local_release_ops.IRREVERSIBLE:
                app._local_release_request((operation, LocalArgs(version="1.0.0", record=Path("r.md"))))
                await pilot.pause(0.1)
                self.assertEqual("ConfirmOperationScreen", type(app.screen).__name__, operation)
                self.assertTrue(app.screen.query_one("#confirm_cancel").has_focus)  # the safe default
                await pilot.press("enter")  # Enter on the focused Cancel
                await pilot.pause(0.1)
                run.assert_not_called()
            app._local_release_request(("finalize", LocalArgs(version="1.0.0")))
            await pilot.pause(0.1)
            await pilot.click("#confirm_run")
            await _idle(app, pilot)
            self.assertEqual(1, run.call_count)
            self.assertEqual("finalize", run.call_args.args[1])
            for operation in local_release_ops.READ_ONLY:
                app._local_release_request((operation, LocalArgs(version="1.0.0")))
                await _idle(app, pilot)
            self.assertEqual(3, run.call_count)  # status and plan need no confirmation

        self.run_app(body)

    def test_close_buttons_are_reachable_on_short_terminals(self) -> None:
        for size in ((82, 26), (120, 45)):
            with self.subTest(size=size):
                async def body(app, pilot, run):
                    # Each screen opened the app's own way, with content longer than the terminal.
                    opened = {
                        "#local_release_close": app.open_local_release,
                        "#close_local_release_report": lambda: app._finish_local_release(
                            "status", LocalArgs(version="1.0.0"), 0, LONG_TEXT),
                    }
                    for button, open_screen in opened.items():
                        open_screen()
                        for _ in range(50):
                            await pilot.pause(0.1)
                            if app.screen.query(button):
                                break
                        await pilot.pause(0.5)  # every widget mounted
                        name = type(app.screen).__name__
                        widget = app.screen.query_one(button)
                        widget.scroll_visible(animate=False)
                        await pilot.pause(0.3)
                        dialog = app.screen.query_one("#dialog").region
                        self.assertTrue(dialog.contains_region(widget.region), (button, widget.region, dialog))
                        self.assertTrue(await pilot.click(button))
                        await pilot.pause(0.3)
                        self.assertNotEqual(name, type(app.screen).__name__)
                    run.assert_not_called()

                self.run_app(body, size=size, summaries=("Store: test\n" + LONG_TEXT, []))

    def test_concurrent_operations_are_refused_in_the_app(self) -> None:
        async def body(app, pilot, run):
            app._release_running = True
            self.assertFalse(app.start_local_release("finalize", LocalArgs(version="1.0.0")))
            app._generate_preview(("CHECKED", "local"))  # a preview is refused while a release runs
            self.assertFalse(getattr(app, "_preview_running", False))
            app._release_running = False
            app._preview_running = True
            self.assertFalse(app.start_local_release("publish", LocalArgs(version="1.0.0")))
            app._preview_running = False
            run.assert_not_called()

        self.run_app(body)

    def test_quitting_during_an_operation_needs_a_second_press(self) -> None:
        async def body(app, pilot, run):
            app._release_running = True
            with mock.patch.object(app, "exit") as exit_:
                app.action_quit()
                exit_.assert_not_called()
                app.action_quit()
                exit_.assert_called_once()
            app._release_running = False

        self.run_app(body)

    def test_a_failure_is_reported_and_never_retried_or_switched(self) -> None:
        async def body(app, pilot, run):
            run.return_value = 2
            run.side_effect = lambda config, op, args, echo, **kw: (echo("error: Release backend refused: local "
                                                                         "Docker is unavailable"), 2)[1]
            app.start_local_release("candidate", LocalArgs(version="1.0.0"))
            await _idle(app, pilot)
            self.assertEqual(1, run.call_count)
            self.assertEqual("LocalReleaseReportScreen", type(app.screen).__name__)
            self.assertIn("Docker is unavailable", str(app.screen.query_one("#local_release_report").render()))
            self.assertIn("failed", app.ci_status)

        self.run_app(body)


class DisposableRepositoryTests(Case):
    """The real TUI app, the real engine modules and Git, in disposable repositories and stores."""

    HOOKS: dict = {}

    def setUp(self) -> None:
        super().setUp()
        if not tui.textual_available():
            self.skipTest("Textual is not installed")
        self.hooks = {"runner": self.engine, "host_runner": self.engine.host,
                      "inspect_remote": NO_REMOTE, "inspect_docker": DOCKER}

    def app(self):
        app = tui._build_textual_app()()
        app.local_release_config = lambda: self.config
        app.local_release_hooks = self.hooks
        app._release_overview = local_release_ops.backend_overview(self.config, inspect_remote=NO_REMOTE)
        return app

    async def confirm(self, app, pilot, operation: str, args: LocalArgs) -> None:
        app._local_release_request((operation, args))
        await pilot.pause(0.1)
        if operation in local_release_ops.IRREVERSIBLE:
            self.assertEqual("ConfirmOperationScreen", type(app.screen).__name__)
            await pilot.click("#confirm_run")
        await _idle(app, pilot)
        if type(app.screen).__name__ == "LocalReleaseReportScreen":
            app.screen.dismiss(None)
            await pilot.pause(0.05)

    def snapshot(self) -> tuple:
        objects = sorted(str(p) for p in (self.repo / ".git" / "objects").rglob("*"))
        refs = _git(self.repo, "for-each-ref")
        store = sorted((str(p), p.stat().st_mtime_ns, p.stat().st_size)
                       for p in self.store.root.rglob("*")) if self.store.root.exists() else []
        index = (self.repo / ".git" / "index").stat().st_mtime_ns
        return objects, refs, store, index, _git(self.repo, "--no-optional-locks", "status", "--porcelain")

    def test_full_lifecycle_through_the_tui_with_no_remote(self) -> None:
        async def check() -> None:
            app = self.app()
            async with app.run_test(size=(110, 40)) as pilot:
                await pilot.pause(0.2)
                args = LocalArgs(version="1.0.0", drawing_revision="A", revision_description="Release",
                                 release_date=DATE)
                await self.confirm(app, pilot, "candidate", args)
                self.assertEqual(0, app.last_local_release[1], app.last_local_release[2])
                await self.confirm(app, pilot, "review", LocalArgs(version="1.0.0"))
                draft = next(self.store.drafts().glob("REVIEW-1.0.0-*.md"))
                record = self.base / "REVIEW.md"
                record.write_text(draft.read_text(encoding="utf-8").replace("- [ ]", "- [x]")
                                  .replace("APPROVED: no", "APPROVED: yes"), encoding="utf-8", newline="\n")
                await self.confirm(app, pilot, "approve", LocalArgs(version="1.0.0", record=record))
                self.assertEqual(0, app.last_local_release[1], app.last_local_release[2])
                self.assertIn("not server-enforced", app.last_local_release[2])
                await self.confirm(app, pilot, "finalize", LocalArgs(version="1.0.0"))
                self.assertEqual(0, app.last_local_release[1], app.last_local_release[2])
                await self.confirm(app, pilot, "publish", LocalArgs(version="1.0.0"))
                self.assertEqual(0, app.last_local_release[1], app.last_local_release[2])
                await self.confirm(app, pilot, "status", LocalArgs(version="1.0.0"))
                self.assertIn("Release 1.0.0: published", app.last_local_release[2])

        asyncio.run(check())
        self.assertEqual("tag", _git(self.repo, "cat-file", "-t", "1.0.0").strip())
        self.assertEqual("", _git(self.repo, "remote"))  # no remote, nothing pushed
        self.assertEqual(1, len(self.store.entries("releases", "1.0.0")))
        header, items = local_release_ops.summaries(self.config)
        summary = items[0]
        self.assertEqual(("published", self.events().count("published")), (summary.stage, 1))
        self.assertEqual(64, len(summary.candidate_id))
        self.assertEqual(40, len(summary.source_sha))
        self.assertEqual("A", summary.drawing_revision)
        self.assertEqual("A", summary.revisions["PCB"])
        self.assertIn("fabrication A/1 initial", summary.baselines)
        self.assertIn("not server-enforced", summary.approval)
        text = local_release_ops.format_summary(summary)
        for expected in ("candidate " + summary.candidate_id, "source " + summary.source_sha, "drawing revision A",
                         "baselines: fabrication A/1 initial"):
            self.assertIn(expected, text)

    def test_status_and_dashboard_views_are_read_only(self) -> None:
        self.approved()
        self.finalize()
        before = self.snapshot()
        for _ in range(2):
            local_release_ops.summaries(self.config)
            local_release_ops.overview_line(self.config)
            self.assertEqual(0, local_release_ops.run(self.config, "status", LocalArgs(), echo=lambda _l: None))
            self.assertEqual(0, local_release_ops.run(self.config, "plan", LocalArgs(version="1.1.0", backend="local"),
                                                      echo=lambda _l: None, inspect_remote=NO_REMOTE,
                                                      inspect_docker=DOCKER))
        self.assertEqual(before, self.snapshot())
        self.assertIn("1.0.0 tagged", local_release_ops.overview_line(self.config))

    def test_no_remote_and_no_docker_is_refused_without_switching(self) -> None:
        hooks = {**self.hooks, "inspect_docker": NO_DOCKER}
        lines: list[str] = []
        status = local_release_ops.run(self.config, "candidate",
                                       LocalArgs(version="1.0.0", drawing_revision="A", revision_description="R"),
                                       echo=lines.append, **hooks)
        self.assertEqual(2, status)
        self.assertIn("Docker", "\n".join(lines))
        self.assertEqual(0, self.engine.builds)
        self.assertFalse(self.store.exists() and self.store.entries("candidates"))

    def test_interrupted_session_is_shown_and_recovered_through_the_tui(self) -> None:
        self.approved()

        def interrupt(name):
            if name == "after-branch":
                raise Interrupted(name)

        async def first_session() -> None:
            app = self.app()
            async with app.run_test(size=(110, 40)) as pilot:
                await pilot.pause(0.2)
                with mock.patch.object(local_finalize, "checkpoint", side_effect=interrupt):
                    await self.confirm(app, pilot, "finalize", LocalArgs(version="1.0.0"))
                self.assertNotEqual(0, app.last_local_release[1])
                self.assertFalse(app._release_running)  # the guard is always released

        async def second_session() -> None:
            app = self.app()
            async with app.run_test(size=(110, 40)) as pilot:
                await pilot.pause(0.2)
                await self.confirm(app, pilot, "status", LocalArgs(version="1.0.0"))
                self.assertIn("interrupted:", app.last_local_release[2])
                self.assertIn("advanced", app.last_local_release[2])
                await self.confirm(app, pilot, "recover", LocalArgs(version="1.0.0"))
                self.assertEqual(0, app.last_local_release[1], app.last_local_release[2])
                await self.confirm(app, pilot, "finalize", LocalArgs(version="1.0.0"))
                self.assertEqual(0, app.last_local_release[1], app.last_local_release[2])

        asyncio.run(first_session())
        self.assertEqual("advanced", self.state().stage)
        builds = self.engine.builds
        asyncio.run(second_session())
        self.assertEqual(builds, self.engine.builds)  # resumed without a rebuild
        self.assertEqual("tagged", self.state().stage)
        self.assertEqual("", _git(self.repo, "status", "--porcelain"))

    def test_a_second_process_holding_the_store_lock_is_refused(self) -> None:
        self.approved()
        from boardwright.stage_lock import stage_lock

        with stage_lock(self.store.root, purpose="another process"):
            lines: list[str] = []
            status = local_release_ops.run(self.config, "finalize", LocalArgs(version="1.0.0", backend="local"),
                                           echo=lines.append, **self.hooks)
        self.assertEqual(2, status)
        self.assertIn("error:", "\n".join(lines))
        self.assertEqual("approved", self.state().stage)
        self.assertEqual("", _git(self.repo, "tag"))


if __name__ == "__main__":
    unittest.main()
