# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Crash consistency and recovery of local releases: every ref change interrupted and resumed."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from boardwright import local_finalize, local_publish, local_recovery
from boardwright.local_release import LocalReleaseError
from boardwright.release_store import ReleaseStore

try:
    from tests.local_release_fixtures import Case, Interrupted
    from tests.test_local_release import _git, _write, snapshot
except ImportError:  # unittest discover -s tests
    from local_release_fixtures import Case, Interrupted
    from test_local_release import _git, _write, snapshot

class CrashRecoveryTests(Case):
    """Interrupt immediately before and after every Git ref change, then resume."""

    POINTS = ("before-import", "after-import", "before-branch", "after-branch", "after-worktree", "before-tag", "after-tag")

    def interrupt_at(self, point: str):
        hits = []

        def maybe(name):
            if name == point and not hits:
                hits.append(name)
                raise Interrupted(name)

        return mock.patch.object(local_finalize, "checkpoint", side_effect=maybe)

    def test_every_ref_change_can_be_interrupted_and_resumed(self) -> None:
        for point in self.POINTS:
            for resume in ("finalize", "recover-then-finalize"):
                with self.subTest(point=point, resume=resume):
                    self.tearDown()
                    self.setUp()
                    record = self.approved()
                    before = snapshot(self.repo)
                    with self.interrupt_at(point), self.assertRaises(Interrupted):
                        self.finalize()
                    state = self.state()
                    self.assertFalse(state.problems, state.problems)
                    self.assertNotEqual("published", state.stage)
                    if point in ("before-import", "before-branch"):
                        self.assertEqual(before, snapshot(self.repo))  # nothing visible changed yet
                    if point == "after-branch":
                        self.assertIn("working tree still holds the source tree", " ".join(state.recoverable))
                    if resume == "recover-then-finalize":
                        self.recover("1.0.0")
                        self.assertFalse(self.state().recoverable)
                    self.finalize()
                    self.assert_finalised(record)
                    self.assertEqual(2, len(_git(self.repo, "rev-list", "main").split()))  # source + release commit

    def test_interrupt_between_record_and_event_is_recovered(self) -> None:
        self.approved()
        real = self.store.append_event

        def drop_prepared(event):
            if event.get("event") == "prepared":
                raise Interrupted("after freeze, before event")
            return real(event)

        with mock.patch.object(ReleaseStore, "append_event", autospec=True,
                               side_effect=lambda store, event: drop_prepared(event)):
            with self.assertRaises(Interrupted):
                self.finalize()
        self.assertEqual(1, len(self.store.entries("finalizations")))
        self.assertTrue(self.state().recoverable)
        with self.assertRaisesRegex(LocalReleaseError, "index event"):
            self.finalize()  # refused until recovered: an unrecorded entry is never trusted silently
        self.recover()
        self.assertIn("prepared", self.events())
        self.finalize()
        self.assertEqual("tagged", self.state().stage)

    def test_status_and_plan_never_write_to_the_repository(self) -> None:
        from boardwright import local_release

        self.approved()
        self.finalize()
        git_dir = self.repo / ".git"

        def snapshot():
            return (sorted(str(p) for p in (git_dir / "objects").rglob("*")),
                    (git_dir / "index").stat().st_mtime_ns, _git(self.repo, "for-each-ref"))

        before = snapshot()
        for _ in range(2):
            local_recovery.status_lines(self.repo, self.store)
            local_release.plan(self.config, "1.1.0", self.store)
        self.assertEqual(before, snapshot())  # no object written, index not refreshed, refs unchanged

    def test_a_truncated_index_is_reported_not_a_crash(self) -> None:
        from boardwright import local_release_ops

        self.approved()
        index = self.store.root / "index.jsonl"
        os.chmod(index, 0o600)
        index.write_bytes(index.read_bytes()[:-20])  # an interrupted or damaged last line
        lines = local_recovery.status_lines(self.repo, self.store)
        self.assertTrue(any("INCONSISTENT" in line for line in lines), lines)
        self.assertIn("Releases not evaluated", lines[-1])
        out: list[str] = []
        self.assertEqual(0, local_release_ops.run(self.config, "status", local_release_ops.LocalArgs(), echo=out.append))
        header, items = local_release_ops.summaries(self.config)
        self.assertEqual(([], True), (items, "INCONSISTENT" in header))
        self.assertIn("INCONSISTENT", local_release_ops.overview_line(self.config))
        with self.assertRaisesRegex(LocalReleaseError, "inconsistent"):
            self.finalize()  # mutating commands refuse

    def test_candidate_and_approval_entries_without_events_are_recovered(self) -> None:
        for lost in ("candidate", "approval"):
            with self.subTest(lost=lost):
                version = "1.0.0" if lost == "candidate" else "1.1.0"
                if lost == "approval":
                    _git(self.repo, "commit", "-q", "--allow-empty", "-m", "next")
                real = ReleaseStore.append_event

                def drop(store, event, lost=lost):
                    if event.get("event") == lost:
                        raise Interrupted("after freeze, before event")
                    return real(store, event)

                with mock.patch.object(ReleaseStore, "append_event", autospec=True, side_effect=drop):
                    with self.assertRaises(Interrupted):
                        self.approved(version)
                state = self.state(version)
                self.assertTrue(state.recoverable, state)
                self.assertIn("recover", state.next_action)
                self.recover(version)
                self.assertIn(lost, self.events(version))
                self.assertFalse(self.state(version).recoverable)
                if lost == "candidate":
                    self.approved(version)  # the same candidate, now approvable; no new entry
                    self.assertEqual(1, len(self.store.entries("candidates", version)))
                self.assertEqual("approved", self.state(version).stage)
                if lost == "candidate":
                    self.finalize(version)  # 1.1.0 needs 1.0.0 tagged first

    def test_a_branch_moved_after_preparation_is_refused_not_reset(self) -> None:
        record = self.approved()
        with self.interrupt_at("before-branch"), self.assertRaises(Interrupted):
            self.finalize()
        _write(self.repo / "board.kicad_pcb", "(kicad_pcb other work)\n")
        _git(self.repo, "commit", "-q", "-am", "other work")
        moved = self.head("main")
        with self.assertRaisesRegex(LocalReleaseError, "never reset"):
            self.finalize()
        self.recover()
        self.assertEqual(moved, self.head("main"))
        self.assertEqual("", _git(self.repo, "tag"))
        self.assertNotEqual(record["source_sha"], moved)

    def test_a_conflicting_tag_after_preparation_is_refused(self) -> None:
        self.approved()
        with self.interrupt_at("before-tag"), self.assertRaises(Interrupted):
            self.finalize()
        _git(self.repo, "tag", "-a", "1.0.0", "-m", "other", "HEAD~1")
        state = self.state()
        self.assertTrue(any("points elsewhere" in p for p in state.problems))
        with self.assertRaisesRegex(LocalReleaseError, "points elsewhere"):
            self.finalize()
        self.assertEqual(self.head("HEAD~1"), self.head("1.0.0^{commit}"))  # never deleted or moved

    def test_lost_pending_ref_is_reimported_from_the_bundle(self) -> None:
        self.approved()
        with self.interrupt_at("after-import"), self.assertRaises(Interrupted):
            self.finalize()
        _git(self.repo, "update-ref", "-d", local_finalize.pending_ref("1.0.0"))
        self.assertEqual("prepared", self.state().stage)
        self.recover()
        self.assertEqual("imported", self.state().stage)


class PublicationRecoveryTests(Case):
    def test_interrupted_publication_is_detected_and_recovered(self) -> None:
        self.approved()
        self.finalize()
        with mock.patch.object(local_publish, "checkpoint",
                               side_effect=lambda name: (_ for _ in ()).throw(Interrupted(name)) if name == "after-store" else None):
            with self.assertRaises(Interrupted):
                self.publish()
        state = self.state()
        self.assertTrue(state.recoverable, state)
        self.recover()
        self.assertEqual("published", self.state().stage)
        self.assertEqual(1, self.events().count("published"))


class CommandLineTests(Case):
    def test_status_names_the_next_step(self) -> None:
        self.approved()
        lines = local_recovery.status_lines(self.repo, self.store)
        self.assertTrue(any("Release 1.0.0: approved" in line for line in lines))
        self.assertTrue(any("next: `boardwright release local finalize`" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
