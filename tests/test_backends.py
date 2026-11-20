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

    def test_every_variant_is_local_by_default_even_with_usable_github(self) -> None:
        # Owner decision 1 (2026-11-20): all preview variants default to local Docker.
        for remote in (GITHUB, NONE, OTHER, GITHUB_NO_AUTH):
            for variant in ("DRAFT", "CHECKED", "PRELIMINARY", "RELEASED", ""):
                s = pick(Operation.PREVIEW, remote, variant=variant)
                self.assertEqual(Backend.LOCAL, s.backend, (remote.kind, variant))

    def test_github_preview_only_by_explicit_choice(self) -> None:
        s = pick(Operation.PREVIEW, GITHUB, variant="RELEASED", override="github")
        self.assertEqual((Backend.GITHUB, "override"), (s.backend, s.source))

    def test_unknown_preview_variant_is_refused(self) -> None:
        self.assertIsNone(pick(Operation.PREVIEW, GITHUB, variant="FINAL").backend)


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

    def test_token_only_userinfo_is_a_credential(self) -> None:
        for url in ("https://ghp_abcdef123@github.com/o/r.git", "https://tok3n@gitlab.com/o/r.git"):
            info, run = self._remote(CommandResult(0, url + "\n"))
            text = format_selection(pick(Operation.RELEASE, info)) + info.url + " ".join(info.problems)
            self.assertNotIn("ghp_abcdef123", text)
            self.assertNotIn("tok3n", text)
            self.assertFalse(info.usable_github)
            self.assertEqual(["git remote get-url"], run.calls)
            self.assertEqual("unsupported", classify_remote_url(url)[0])

    def test_passwords_and_query_tokens_in_any_scheme_are_never_echoed(self) -> None:
        for url in ("ssh://git:hunter2@github.com/o/r.git", "ssh://u:hunter2@gitlab.com/o/r",
                    "ftp://a:hunter2@host/r", "https://github.com/o/r.git?token=hunter2",
                    "https://github.com/o/r.git#hunter2"):
            info, run = self._remote(CommandResult(0, url + "\n"))
            for op in (Operation.RELEASE, Operation.PREVIEW):
                for override in ("", "github", "local"):
                    text = format_selection(pick(op, info, override=override)) + info.url + " ".join(info.problems)
                    self.assertNotIn("hunter2", text, (url, op, override))
            self.assertFalse(info.usable_github, url)
            self.assertEqual(["git remote get-url"], run.calls, url)

    def test_unsupported_remote_is_shown_without_secrets(self) -> None:
        from boardwright.backends import redact_url
        self.assertEqual("https://gitlab.com/o/r.git", redact_url("https://tok@gitlab.com/o/r.git?x=1"))
        info, _ = self._remote(CommandResult(0, "https://gitlab.com/o/r.git\n"))
        self.assertIn("https://gitlab.com/o/r.git", info.problems[0])

    def test_ssh_login_name_is_not_a_credential(self) -> None:
        self.assertEqual(("github", "o/r"), classify_remote_url("git@github.com:o/r.git"))

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
