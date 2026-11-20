# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""The shared source policy for previews and direct worktree builds (audit C1)."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from boardwright import container, local_preview, source_policy
from boardwright.errors import BoardwrightError
from boardwright.stage_lock import stage_lock

SECRET = "c1-secret-value-7f3a"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def _write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8", newline="\n")


def snapshot(root: Path) -> dict[str, str]:
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            path = Path(dirpath) / name
            out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


class Capture(io.StringIO):
    """stdout stand-in: container.main reconfigures stdout for line buffering."""

    def reconfigure(self, **_kwargs) -> None:
        return None


def _writable(func, path, _exc):
    os.chmod(path, 0o700)
    func(path)


class PolicyCase(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="bw policy "))
        self.repo = self.base / "board project"
        self.repo.mkdir()
        for args in (("init", "-q", "-b", "main"), ("config", "user.name", "t"), ("config", "user.email", "t@example.invalid"),
                     ("config", "core.autocrlf", "false")):
            _git(self.repo, *args)
        _write(self.repo / ".gitignore", ".boardwright/local.yaml\n")
        _write(self.repo / "board.kicad_pcb", "(kicad_pcb)\n")
        _write(self.repo / "board.kicad_sch", "(kicad_sch)\n")
        _write(self.repo / ".boardwright" / "project.yaml", "project: {name: board}\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "init")
        self.env = mock.patch.dict(os.environ, {"BOARDWRIGHT_STAGE_ROOT": str(self.base / "stages"),
                                                "BOARDWRIGHT_PREVIEW_INDEX": str(self.base / "index")})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        shutil.rmtree(self.base, onerror=_writable)

    def commit_file(self, name: str, text: str) -> None:
        _write(self.repo / name, text)
        _git(self.repo, "add", "-f", name)
        _git(self.repo, "commit", "-q", "-m", f"add {name}")


class TrackedCredentialPolicyTests(PolicyCase):
    def test_tracked_credential_like_files_are_refused_without_reading_them(self) -> None:
        self.commit_file("deploy/.env", f"TOKEN={SECRET}\n")
        self.commit_file("keys/signing.pem", f"-----BEGIN {SECRET}-----\n")
        with self.assertRaises(BoardwrightError) as caught:
            source_policy.check_preconditions(self.repo)
        message = str(caught.exception)
        self.assertIn("deploy/.env", message)
        self.assertIn("keys/signing.pem", message)
        self.assertNotIn(SECRET, message)

    def test_explicit_allow_list_admits_a_named_path_only(self) -> None:
        self.commit_file("docs/token-ring.md", "Token Ring notes, not a secret\n")
        self.commit_file("deploy/.env", f"TOKEN={SECRET}\n")
        self.commit_file(".boardwright/project.yaml",
                         "project: {name: board}\nsource_policy:\n  allow_tracked: [docs/token-ring.md]\n")
        self.assertEqual(["deploy/.env"], source_policy.tracked_credentials(self.repo))
        _git(self.repo, "rm", "-q", "deploy/.env")
        _git(self.repo, "commit", "-q", "-m", "remove secret")
        source_policy.check_preconditions(self.repo)  # nothing left to refuse

    def test_preview_refuses_tracked_credentials_too(self) -> None:
        self.commit_file("api_token.txt", f"{SECRET}\n")
        config = mock.Mock(root=self.repo, preview_variant="CHECKED", preview_backend="auto")
        outcome = local_preview.run_preview(
            config, "CHECKED", echo=lambda _l: None, runner=lambda *a: 0,
            inspect_remote=lambda root, check_access=False: mock.Mock(kind="none", usable_github=False, problems=(), url=""),
            inspect_docker=lambda root=None: mock.Mock(available=True, detail="Docker"))
        self.assertFalse(outcome.ok)
        self.assertIn("api_token.txt", outcome.message)
        self.assertNotIn(SECRET, outcome.message)


class DirectWorktreeBuildTests(PolicyCase):
    """``container build --worktree``: shared policy, verified snapshot, lock, non-release identity."""

    def _build(self, runner=None) -> tuple[int, str, list[Path]]:
        seen: list[Path] = []

        def fake_run(kicad, workdir, command, **_kwargs):
            seen.append(Path(workdir))
            self.staged = snapshot(Path(workdir))
            return 0 if runner is None else runner(workdir)

        out, err = Capture(), Capture()
        cwd = os.getcwd()
        os.chdir(self.repo)
        try:
            with mock.patch.object(container, "run_in_container", side_effect=fake_run), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = container.main(["build", "--variant", "CHECKED", "--worktree"])
        finally:
            os.chdir(cwd)
        return code, out.getvalue() + err.getvalue(), seen

    def test_untracked_credentials_and_unrelated_files_are_excluded_and_never_printed(self) -> None:
        _write(self.repo / "board.kicad_sch", "(kicad_sch (edited))\n")
        _write(self.repo / "new sheet.kicad_sch", "(kicad_sch new)\n")
        _write(self.repo / ".env", f"API_TOKEN={SECRET}\n")
        _write(self.repo / "notes.txt", f"remember {SECRET}\n")
        _write(self.repo / ".boardwright" / "local.yaml", f"template: {{logo: {SECRET}.png}}\n")
        before = snapshot(self.repo)
        code, output, seen = self._build()
        self.assertEqual(0, code, output)
        self.assertEqual(before, snapshot(self.repo))  # the developer's tree is untouched
        self.assertIn("board.kicad_sch", self.staged)
        self.assertIn("new sheet.kicad_sch", self.staged)
        for absent in (".env", "notes.txt", ".boardwright/local.yaml"):
            self.assertNotIn(absent, self.staged)
        self.assertNotIn(SECRET, output)
        stage = seen[0]
        for metadata in (stage / ".git").glob("boardwright-*"):
            self.assertNotIn(SECRET, metadata.read_text(encoding="utf-8", errors="replace"), metadata)
        self.assertIn("exclude: .env (looks like a credential)", output)

    def test_identified_as_a_non_release_worktree_build(self) -> None:
        _write(self.repo / "board.kicad_sch", "(kicad_sch (edited))\n")
        code, output, seen = self._build()
        stage = seen[0]
        marker = json.loads((stage / ".git" / container.STAGE_MARKER).read_text(encoding="utf-8"))
        self.assertEqual((True, "worktree-build"), (marker["worktree"], marker["purpose"]))
        record = json.loads((stage / ".git" / container.SOURCE_RECORD).read_text(encoding="utf-8"))
        self.assertEqual(("worktree-build", False, True), (record["purpose"], record["release_evidence"], record["dirty"]))
        self.assertFalse((stage / "boardwright-build" / "preview.json").exists())  # not a preview
        self.assertIn("non-release worktree build", output)
        self.assertTrue(stage.name.endswith("-worktree"))

    def test_locked_stage_is_refused(self) -> None:
        stage = (self.base / "stages" / f"{self.repo.name}-checked-k9-worktree").resolve()
        with stage_lock(stage, purpose="another build"):
            code, output, seen = self._build()
        self.assertEqual(2, code)
        self.assertIn("in use by", output)
        self.assertEqual([], seen)

    def test_tracked_credentials_refuse_the_direct_build(self) -> None:
        self.commit_file("secrets.yaml", f"key: {SECRET}\n")
        code, output, seen = self._build()
        self.assertEqual(2, code)
        self.assertIn("secrets.yaml", output)
        self.assertNotIn(SECRET, output)
        self.assertEqual([], seen)

    def test_change_during_the_read_is_refused(self) -> None:
        _write(self.repo / "board.kicad_sch", "(kicad_sch (edited))\n")
        real = source_policy._read_bytes

        def save_while_reading(path):
            _write(self.repo / "board.kicad_pcb", "(kicad_pcb (saved meanwhile))\n")
            return real(path)

        with mock.patch.object(source_policy, "_read_bytes", side_effect=save_while_reading):
            code, output, seen = self._build()
        self.assertEqual(2, code)
        self.assertIn("changed while it was being read", output)
        self.assertEqual([], seen)

    def test_clean_committed_build_is_unchanged_and_not_a_worktree_build(self) -> None:
        out = Capture()
        cwd = os.getcwd()
        os.chdir(self.repo)
        try:
            with mock.patch.object(container, "run_in_container", return_value=0) as run, contextlib.redirect_stdout(out):
                code = container.main(["build", "--variant", "CHECKED"])
        finally:
            os.chdir(cwd)
        self.assertEqual(0, code)
        stage = Path(run.call_args.args[1])
        marker = json.loads((stage / ".git" / container.STAGE_MARKER).read_text(encoding="utf-8"))
        self.assertEqual((False, "build"), (marker["worktree"], marker["purpose"]))


if __name__ == "__main__":
    unittest.main()
