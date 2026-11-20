# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Local release finalisation: rebuild, review gate, release commit, branch, tag."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import unittest
from pathlib import Path

from boardwright import local_finalize
from boardwright.local_release import LocalReleaseError

try:
    from tests.local_release_fixtures import Case, gerber
    from tests.test_local_release import _git, _write
except ImportError:  # unittest discover -s tests
    from local_release_fixtures import Case, gerber
    from test_local_release import _git, _write

class FinalizeTests(Case):
    def test_finalize_commits_advances_and_tags_without_pushing(self) -> None:
        record = self.approved()
        remotes = _git(self.repo, "remote")
        state = self.finalize()
        self.assertEqual("tagged", state.stage)
        commit = self.assert_finalised(record)
        changed = set(_git(self.repo, "diff", "--name-only", record["source_sha"], commit).split())
        self.assertTrue({"CHANGELOG.md", ".boardwright/releases.yaml", ".boardwright/approval.json",
                         ".boardwright/accepted_manifest.json", ".boardwright/review_candidate.json"} <= changed)
        self.assertFalse(any(p.startswith(("Manufacturing/", "Schematic/")) for p in changed))
        self.assertEqual(remotes, _git(self.repo, "remote"))
        tag = _git(self.repo, "cat-file", "tag", "1.0.0")
        self.assertIn("Approval-Authority: local", tag)
        self.assertIn("not server-enforced", tag)
        self.assertTrue(any("The tag was not pushed" in line for line in self.out))

    def test_duplicate_finalize_changes_nothing(self) -> None:
        self.approved()
        self.finalize()
        refs = _git(self.repo, "for-each-ref")
        events = self.store.events()
        builds = self.engine.builds
        self.finalize()
        self.assertEqual(refs, _git(self.repo, "for-each-ref"))
        self.assertEqual(events, self.store.events())
        self.assertEqual(builds, self.engine.builds)  # no rebuild

    def test_dirty_tree_moved_head_and_existing_tag_are_refused(self) -> None:
        record = self.approved()
        _write(self.repo / "notes.txt", "x\n")
        with self.assertRaisesRegex(LocalReleaseError, "uncommitted"):
            self.finalize()
        (self.repo / "notes.txt").unlink()
        _write(self.repo / "board.kicad_pcb", "(kicad_pcb moved)\n")
        _git(self.repo, "commit", "-q", "-am", "after approval")
        with self.assertRaisesRegex(LocalReleaseError, "HEAD moved"):
            self.finalize()
        _git(self.repo, "reset", "-q", "--hard", record["source_sha"])
        _git(self.repo, "tag", "-a", "1.0.0", "-m", "someone else's tag")
        with self.assertRaisesRegex(LocalReleaseError, "Tag 1.0.0 exists"):
            self.finalize()
        self.assertEqual([], self.store.entries("finalizations"))

    def test_changed_candidate_review_or_approval_is_refused(self) -> None:
        record = self.approved()
        for kind, name, relative in (("candidates", record["candidate_id"], ".boardwright/accepted_manifest.json"),
                                     ("approvals", record["candidate_id"], "REVIEW-1.0.0.md"),
                                     ("approvals", record["candidate_id"], "approval.json")):
            target = self.store.entry_path(kind, "1.0.0", name) / relative
            original = target.read_bytes()
            os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
            target.write_bytes(original.replace(b"1", b"2", 1))
            with self.assertRaises(LocalReleaseError, msg=relative):
                self.finalize()
            target.write_bytes(original)
        self.assertEqual(record["source_sha"], self.head("main"))
        self.assertEqual("", _git(self.repo, "tag"))

    def test_a_rebuild_that_differs_from_the_candidate_is_refused(self) -> None:
        self.approved()
        real = self.engine.__call__

        def different(kicad, workdir, command, environment=None):
            status = real(kicad, workdir, command, environment)
            if command[2:4] == ["boardwright", "build"]:
                _write(Path(workdir) / "Manufacturing" / "Fabrication" / "Gerbers" / "board-F_Cu.gbr", gerber("different"))
            return status

        self.engine.__call__ = different
        with self.assertRaisesRegex(LocalReleaseError, "does not match the approved candidate"):
            local_finalize.finalize(self.config, self.store, "1.0.0", runner=different, host_runner=self.engine.host,
                                    echo=self.out.append)
        self.assertEqual([], self.store.entries("finalizations"))
        self.assertEqual("", _git(self.repo, "tag"))

    def test_signing_requested_without_a_key_fails_closed(self) -> None:
        record = self.approved()
        with self.assertRaisesRegex(LocalReleaseError, "no signing key"):
            self.finalize(sign=True)
        _git(self.repo, "config", "gpg.format", "ssh")
        _git(self.repo, "config", "user.signingkey", str(self.base / "missing-key.pub"))
        with self.assertRaisesRegex(LocalReleaseError, "signing was requested and failed"):
            self.finalize(sign=True)
        self.assertEqual(([], record["source_sha"], ""), (self.store.entries("finalizations"), self.head("main"),
                                                          _git(self.repo, "tag")))

    @unittest.skipUnless(shutil.which("ssh-keygen"), "ssh-keygen is needed for a signing key")
    def test_signed_release_commit_and_tag_verify(self) -> None:
        key = self.base / "signing key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "reviewer@example.invalid", "-f", str(key)],
                       check=True, capture_output=True)
        allowed = self.base / "allowed_signers"
        public = Path(str(key) + ".pub")
        allowed.write_text("reviewer@example.invalid " + public.read_text(encoding="utf-8"), encoding="utf-8")
        for name, value in (("gpg.format", "ssh"), ("user.signingkey", str(public)),
                            ("gpg.ssh.allowedSignersFile", str(allowed))):
            _git(self.repo, "config", name, value)
        record = self.approved()
        self.finalize(sign=True)
        commit = self.assert_finalised(record)
        self.assertEqual(0, subprocess.run(["git", "verify-commit", commit], cwd=self.repo, capture_output=True).returncode)
        self.assertEqual(0, subprocess.run(["git", "tag", "-v", "1.0.0"], cwd=self.repo, capture_output=True).returncode)
        with self.assertRaisesRegex(LocalReleaseError, "started with --sign"):
            local_finalize.finalize(self.config, self.store, "1.0.0", sign=False, runner=self.engine,
                                    host_runner=self.engine.host, echo=self.out.append)


if __name__ == "__main__":
    unittest.main()
