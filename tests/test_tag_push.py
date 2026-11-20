# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Bounded, verified release-tag push (audit R1)."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_revision_model import _rmtree  # noqa: E402

from boardwright import tag_push  # noqa: E402
from boardwright.tag_push import GitResult, classify, push_tag  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "a" * 40
OTHER = "b" * 40
TAGOBJ = "c" * 40
REJECTED = "To https://github.com/o/r\n ! [remote rejected] 0.1.7 -> 0.1.7 (failed)\nerror: failed to push some refs\n"


class FakeGit:
    """Scripted remote: ``remote`` is the tag state seen by ls-remote, mutated by pushes."""

    def __init__(self, pushes: list[GitResult], *, remote_after_push=None, remote: str | None = None,
                 ls_errors: list[str] | None = None) -> None:
        self.pushes = list(pushes)
        self.remote = remote  # commit the remote tag points at, or None
        self.remote_after_push = list(remote_after_push or [])
        self.ls_errors = list(ls_errors or [])
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> GitResult:
        self.calls.append(argv)
        cmd = argv[0]
        if cmd == "cat-file":
            return GitResult(0, "tag\n")
        if cmd == "rev-parse":
            return GitResult(0, COMMIT + "\n")
        if cmd == "ls-remote" and argv[1] == "--heads":
            return GitResult(0, f"{COMMIT}\trefs/heads/main\n")
        if cmd == "ls-remote":
            if self.ls_errors:
                return GitResult(128, "", self.ls_errors.pop(0))
            if self.remote is None:
                return GitResult(0, "")
            return GitResult(0, f"{TAGOBJ}\trefs/tags/0.1.7\n{self.remote}\trefs/tags/0.1.7^{{}}\n")
        if cmd == "push":
            assert "--force" not in argv and "-f" not in argv and not any(a.startswith("+") for a in argv)
            result = self.pushes.pop(0)
            if self.remote_after_push:
                self.remote = self.remote_after_push.pop(0)
            elif result.returncode == 0:
                self.remote = COMMIT
            return result
        raise AssertionError(f"unexpected git call {argv}")

    @property
    def push_count(self) -> int:
        return sum(1 for c in self.calls if c[0] == "push")


def _push(git: FakeGit, attempts: int = 4):
    sleeps: list[float] = []
    lines: list[str] = []
    code = push_tag("0.1.7", COMMIT, git=git, attempts=attempts, delay=1, sleep=sleeps.append, echo=lines.append)
    return code, sleeps, lines


class ClassifyTests(unittest.TestCase):
    def test_the_0_1_7_rejection_is_transient(self) -> None:
        self.assertEqual("transient", classify(REJECTED))

    def test_auth_and_conflict_are_not_transient(self) -> None:
        self.assertEqual("auth", classify("remote: Permission to o/r.git denied to github-actions[bot].\nfatal: ... 403"))
        self.assertEqual("auth", classify("fatal: Authentication failed for 'https://github.com/o/r/'"))
        self.assertEqual("conflict", classify(" ! [rejected]        0.1.7 -> 0.1.7 (already exists)"))
        self.assertEqual("unknown", classify("error: something nobody has seen before"))


class RetryTests(unittest.TestCase):
    def test_transient_failure_then_success(self) -> None:
        git = FakeGit([GitResult(1, "", REJECTED), GitResult(0)], remote_after_push=[None, COMMIT])
        code, sleeps, _ = _push(git)
        self.assertEqual(0, code)
        self.assertEqual(2, git.push_count)
        self.assertEqual([1], sleeps)

    def test_ambiguous_success_is_detected_without_pushing_again(self) -> None:
        # The push reports failure, but the tag did reach the remote.
        git = FakeGit([GitResult(1, "", REJECTED)], remote_after_push=[COMMIT])
        code, sleeps, lines = _push(git)
        self.assertEqual(0, code)
        self.assertEqual(1, git.push_count)
        self.assertEqual([], sleeps)
        self.assertTrue(any("in place" in line for line in lines))

    def test_existing_matching_tag_is_success_without_a_push(self) -> None:
        git = FakeGit([], remote=COMMIT)
        self.assertEqual(0, _push(git)[0])
        self.assertEqual(0, git.push_count)

    def test_conflicting_tag_stops_without_force(self) -> None:
        git = FakeGit([], remote=OTHER)
        self.assertEqual(tag_push.EXIT_CONFLICT, _push(git)[0])
        self.assertEqual(0, git.push_count)

    def test_conflict_appearing_during_push_is_not_retried(self) -> None:
        git = FakeGit([GitResult(1, "", " ! [rejected] 0.1.7 -> 0.1.7 (already exists)")], remote_after_push=[OTHER])
        code, sleeps, _ = _push(git)
        self.assertEqual(tag_push.EXIT_CONFLICT, code)
        self.assertEqual((1, []), (git.push_count, sleeps))

    def test_authentication_failure_is_not_retried(self) -> None:
        git = FakeGit([GitResult(128, "", "remote: Permission to o/r.git denied.\nfatal: unable to access: 403")])
        code, sleeps, _ = _push(git)
        self.assertEqual(tag_push.EXIT_AUTH, code)
        self.assertEqual((1, []), (git.push_count, sleeps))

    def test_authentication_failure_reading_the_remote_is_not_retried(self) -> None:
        git = FakeGit([], ls_errors=["fatal: Authentication failed for 'https://github.com/o/r/'"])
        self.assertEqual(tag_push.EXIT_AUTH, _push(git)[0])
        self.assertEqual(0, git.push_count)

    def test_unknown_failure_is_not_retried(self) -> None:
        git = FakeGit([GitResult(1, "", "error: a new kind of failure")], remote_after_push=[None])
        code, sleeps, _ = _push(git)
        self.assertEqual(tag_push.EXIT_EXHAUSTED, code)
        self.assertEqual((1, []), (git.push_count, sleeps))

    def test_retries_are_bounded_with_backoff(self) -> None:
        git = FakeGit([GitResult(1, "", REJECTED)] * 3, remote_after_push=[None, None, None])
        code, sleeps, _ = _push(git, attempts=3)
        self.assertEqual(tag_push.EXIT_EXHAUSTED, code)
        self.assertEqual(3, git.push_count)
        self.assertEqual([1, 3], sleeps)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class RealRemoteTests(unittest.TestCase):
    """Against real bare repositories: no simulated Git."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="bw-tagpush-"))
        self.remote = self.tmp / "remote.git"
        self.work = self.tmp / "work"
        _git(self.tmp, "init", "-q", "--bare", str(self.remote))
        _git(self.tmp, "init", "-q", "-b", "main", str(self.work))
        for key, value in (("user.name", "t"), ("user.email", "t@example.invalid")):
            _git(self.work, "config", key, value)
        (self.work / "f").write_text("1\n", encoding="utf-8")
        _git(self.work, "add", "f")
        _git(self.work, "commit", "-q", "-m", "release: prepare 1.0.0 (RELEASED)")
        _git(self.work, "remote", "add", "origin", str(self.remote))
        _git(self.work, "push", "-q", "origin", "main")
        self.commit = _git(self.work, "rev-parse", "HEAD")
        _git(self.work, "tag", "-a", "1.0.0", "-m", "Release 1.0.0")

    def tearDown(self) -> None:
        _rmtree(self.tmp)

    def _run(self) -> int:
        import os

        cwd = os.getcwd()
        os.chdir(self.work)
        try:
            return push_tag("1.0.0", self.commit, attempts=2, delay=0, sleep=lambda _s: None, echo=lambda _l: None)
        finally:
            os.chdir(cwd)

    def _remote_tag(self) -> str:
        return _git(self.tmp, "--git-dir", str(self.remote), "rev-parse", "refs/tags/1.0.0^{commit}")

    def test_push_succeeds(self) -> None:
        self.assertEqual(0, self._run())
        self.assertEqual(self.commit, self._remote_tag())

    def test_existing_matching_tag(self) -> None:
        _git(self.work, "push", "-q", "origin", "refs/tags/1.0.0")
        self.assertEqual(0, self._run())

    def test_conflicting_tag_is_left_untouched(self) -> None:
        (self.work / "f").write_text("2\n", encoding="utf-8")
        _git(self.work, "commit", "-q", "-am", "other")
        _git(self.work, "push", "-q", "origin", "main")
        other = _git(self.work, "rev-parse", "HEAD")
        _git(self.work, "tag", "-a", "other-1.0.0", "-m", "x")
        _git(self.work, "push", "-q", "origin", "refs/tags/other-1.0.0:refs/tags/1.0.0")
        self.assertEqual(tag_push.EXIT_CONFLICT, self._run())
        self.assertEqual(other, self._remote_tag())

    def test_lightweight_or_misplaced_local_tag_is_refused(self) -> None:
        _git(self.work, "tag", "-d", "1.0.0")
        _git(self.work, "tag", "1.0.0")
        self.assertEqual(tag_push.EXIT_LOCAL, self._run())

    def test_release_commit_missing_from_the_remote_branch_is_refused(self) -> None:
        (self.work / "f").write_text("3\n", encoding="utf-8")
        _git(self.work, "commit", "-q", "-am", "not pushed")
        self.commit = _git(self.work, "rev-parse", "HEAD")
        _git(self.work, "tag", "-f", "-a", "1.0.0", "-m", "Release 1.0.0")
        self.assertEqual(tag_push.EXIT_LOCAL, self._run())
        self.assertEqual("", _git(self.tmp, "--git-dir", str(self.remote), "tag", "--list", "1.0.0"))


class WorkflowTests(unittest.TestCase):
    def test_prepare_release_uses_the_bounded_push_without_force(self) -> None:
        steps = yaml.safe_load((ROOT / ".github/workflows/prepare-release.yaml").read_text(encoding="utf-8"))["jobs"]["prepare"]["steps"]
        script = next(s for s in steps if s["name"] == "Create and push tag")["run"]
        self.assertIn('python -m boardwright.tag_push --tag "$version" --commit "$release_commit"', script)
        self.assertNotIn("git push", script.replace("git push origin refs/tags/$version, then", ""))
        self.assertNotIn("--force", script)


if __name__ == "__main__":
    unittest.main()
