# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Backend selection: local Docker or GitHub (docs/LOCAL_FIRST.md section 3)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from boardwright.backends import (
    Backend,
    CommandResult,
    DockerInfo,
    Operation,
    RemoteInfo,
    classify_remote_url,
    format_selection,
    inspect_docker,
    inspect_remote,
    select_backend,
)

GITHUB = RemoteInfo("github", "https://github.com/o/r.git", "o/r", True, True, True)
GITHUB_NO_AUTH = RemoteInfo("github", "https://github.com/o/r.git", "o/r", None, False, True,
                            ("the GitHub CLI is not authenticated (gh auth login)",))
GITHUB_UNREACHABLE = RemoteInfo("github", "https://github.com/o/r.git", "o/r", False, True, True,
                                ("repository o/r is not reachable",))
NONE = RemoteInfo("none")
OTHER = RemoteInfo("unsupported", "https://gitlab.com/o/r.git", problems=("origin ... is not a GitHub repository",))
DOCKER = DockerInfo(True, "Docker 27")
NO_DOCKER = DockerInfo(False, "the Docker engine is not running")


def pick(op, remote, docker=DOCKER, variant="", setting="auto", override=""):
    return select_backend(op, remote=remote, docker=docker, variant=variant, project_setting=setting, override=override)


class PreviewDefaultTests(unittest.TestCase):
    def test_draft_and_checked_are_local_even_with_usable_github(self) -> None:
        for variant in ("DRAFT", "CHECKED", "checked"):
            s = pick(Operation.PREVIEW, GITHUB, variant=variant)
            self.assertEqual((Backend.LOCAL, "default", ()), (s.backend, s.source, s.problems), variant)

    def test_without_docker_a_local_preview_is_refused_not_switched(self) -> None:
        s = pick(Operation.PREVIEW, GITHUB, NO_DOCKER, variant="CHECKED")
        self.assertIsNone(s.backend)
        self.assertIn("--backend github", s.problems[0])

    def test_other_variants_use_usable_github_else_local(self) -> None:
        self.assertEqual(Backend.GITHUB, pick(Operation.PREVIEW, GITHUB, variant="RELEASED").backend)
        self.assertEqual(Backend.LOCAL, pick(Operation.PREVIEW, NONE, variant="RELEASED").backend)
        self.assertEqual(Backend.LOCAL, pick(Operation.PREVIEW, OTHER, variant="PRELIMINARY").backend)


class ReleaseDefaultTests(unittest.TestCase):
    def test_usable_github_releases_on_github(self) -> None:
        s = pick(Operation.RELEASE, GITHUB)
        self.assertEqual((Backend.GITHUB, "default"), (s.backend, s.source))

    def test_github_wins_even_without_docker(self) -> None:
        self.assertEqual(Backend.GITHUB, pick(Operation.RELEASE, GITHUB, NO_DOCKER).backend)

    def test_no_remote_releases_locally(self) -> None:
        self.assertEqual(Backend.LOCAL, pick(Operation.RELEASE, NONE).backend)
        refused = pick(Operation.RELEASE, NONE, NO_DOCKER)
        self.assertIsNone(refused.backend)

    def test_unsupported_remote_is_refused_not_assumed_github(self) -> None:
        s = pick(Operation.RELEASE, OTHER)
        self.assertIsNone(s.backend)
        self.assertTrue(any("--backend local" in p for p in s.problems))

    def test_unusable_github_never_falls_back_to_local(self) -> None:
        for remote in (GITHUB_NO_AUTH, GITHUB_UNREACHABLE):
            s = pick(Operation.RELEASE, remote)
            self.assertIsNone(s.backend)
            self.assertEqual(remote.problems[0], s.problems[0])
            self.assertTrue(any("--backend local" in p for p in s.problems))


class OverrideTests(unittest.TestCase):
    def test_override_beats_project_setting_beats_default(self) -> None:
        s = pick(Operation.PREVIEW, GITHUB, variant="CHECKED", setting="github")
        self.assertEqual((Backend.GITHUB, "project"), (s.backend, s.source))
        s = pick(Operation.PREVIEW, GITHUB, variant="CHECKED", setting="github", override="local")
        self.assertEqual((Backend.LOCAL, "override"), (s.backend, s.source))
        s = pick(Operation.RELEASE, GITHUB, setting="local", override="auto")
        self.assertEqual((Backend.LOCAL, "project"), (s.backend, s.source))

    def test_local_release_on_a_github_project_is_explicit_and_labelled(self) -> None:
        s = pick(Operation.RELEASE, GITHUB, override="local")
        self.assertEqual(Backend.LOCAL, s.backend)
        self.assertTrue(any("not server-enforced" in w for w in s.warnings))

    def test_local_release_on_unsupported_remote_by_override(self) -> None:
        self.assertEqual(Backend.LOCAL, pick(Operation.RELEASE, OTHER, override="local").backend)

    def test_github_override_requires_usable_github(self) -> None:
        for remote, text in ((NONE, "no remote"), (OTHER, "not a GitHub repository"), (GITHUB_NO_AUTH, "not authenticated")):
            s = pick(Operation.PREVIEW, remote, variant="CHECKED", override="github")
            self.assertIsNone(s.backend)
            self.assertIn(text, s.problems[0])

    def test_local_override_requires_docker(self) -> None:
        self.assertIsNone(pick(Operation.RELEASE, GITHUB, NO_DOCKER, override="local").backend)

    def test_invalid_settings_are_refused(self) -> None:
        self.assertIsNone(pick(Operation.RELEASE, GITHUB, override="gitlab").backend)
        self.assertIsNone(pick(Operation.RELEASE, GITHUB, setting="cloud").backend)

    def test_format_names_backend_source_and_reason(self) -> None:
        text = format_selection(pick(Operation.RELEASE, OTHER))
        self.assertTrue(text.startswith("release: refused (default)"))
        self.assertIn("problem:", text)


class UrlTests(unittest.TestCase):
    def test_github_url_forms(self) -> None:
        for url in ("https://github.com/Reemuson/boardwright-ci-test.git", "https://github.com/Reemuson/boardwright-ci-test",
                    "git@github.com:Reemuson/boardwright-ci-test.git", "ssh://git@github.com/Reemuson/boardwright-ci-test.git"):
            self.assertEqual(("github", "Reemuson/boardwright-ci-test"), classify_remote_url(url), url)

    def test_other_hosts_and_paths_are_unsupported(self) -> None:
        for url in ("https://gitlab.com/o/r.git", "https://github.example.com/o/r.git", "git@bitbucket.org:o/r.git",
                    "C:\\Users\\someone\\repo.git", "/srv/git/repo.git", "https://github.com.evil.example/o/r"):
            self.assertEqual("unsupported", classify_remote_url(url)[0], url)
        self.assertEqual(("none", ""), classify_remote_url(""))


class FakeRun:
    def __init__(self, responses: dict[str, CommandResult]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def __call__(self, argv, cwd) -> CommandResult:
        key = " ".join(argv[:3])
        self.calls.append(key)
        return self.responses.get(key, CommandResult(1, "", "unexpected"))


class InspectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="bw-backends-"))
        workflows = self.root / ".github" / "workflows"
        workflows.mkdir(parents=True)
        for name in ("prepare-release.yaml", "release.yaml", "dev-preview.yaml"):
            (workflows / name).write_text("on: workflow_dispatch\n", encoding="utf-8")

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)

    def _remote(self, url_result: CommandResult, **more: CommandResult) -> tuple[RemoteInfo, FakeRun]:
        run = FakeRun({"git remote get-url": url_result, **more})
        return inspect_remote(self.root, run=run), run

    def test_no_remote(self) -> None:
        info, run = self._remote(CommandResult(2, "", "error: No such remote 'origin'"))
        self.assertEqual("none", info.kind)
        self.assertEqual(["git remote get-url"], run.calls)

    def test_usable_github(self) -> None:
        info, _ = self._remote(CommandResult(0, "https://github.com/o/r.git\n"),
                               **{"gh auth status": CommandResult(0), "gh repo view": CommandResult(0, "{}")})
        self.assertTrue(info.usable_github)

    def test_unauthenticated_and_unreachable(self) -> None:
        info, run = self._remote(CommandResult(0, "git@github.com:o/r.git\n"), **{"gh auth status": CommandResult(1)})
        self.assertFalse(info.usable_github)
        self.assertNotIn("gh repo view", run.calls)
        info, _ = self._remote(CommandResult(0, "git@github.com:o/r.git\n"),
                               **{"gh auth status": CommandResult(0), "gh repo view": CommandResult(1, "", "HTTP 404: Not Found")})
        self.assertIn("not reachable: HTTP 404", info.problems[0])

    def test_missing_workflows_make_github_unusable(self) -> None:
        (self.root / ".github" / "workflows" / "release.yaml").unlink()
        info, _ = self._remote(CommandResult(0, "https://github.com/o/r\n"),
                               **{"gh auth status": CommandResult(0), "gh repo view": CommandResult(0)})
        self.assertIn("workflows are not in", info.problems[0])

    def test_credentials_in_url_are_never_echoed(self) -> None:
        info, run = self._remote(CommandResult(0, "https://user:secret@github.com/o/r.git\n"))
        self.assertNotIn("secret", info.url + " ".join(info.problems))
        self.assertFalse(info.usable_github)
        self.assertEqual(["git remote get-url"], run.calls)

    def test_unsupported_remote_is_not_queried_with_gh(self) -> None:
        info, run = self._remote(CommandResult(0, "https://gitlab.com/o/r.git\n"))
        self.assertEqual("unsupported", info.kind)
        self.assertEqual(["git remote get-url"], run.calls)

    def test_docker_inspection(self) -> None:
        self.assertTrue(inspect_docker(run=FakeRun({"docker info --format": CommandResult(0, "27.3.1\n")})).available)
        off = inspect_docker(run=FakeRun({"docker info --format": CommandResult(1, "", "Cannot connect to the Docker daemon")}))
        self.assertFalse(off.available)
        self.assertIn("not running", off.detail)


if __name__ == "__main__":
    unittest.main()
