# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""One authoritative release date, the same in the held and the published build."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_revision_model import Project, _git, _rmtree  # noqa: E402

from boardwright.config import load_config  # noqa: E402
from boardwright.errors import BoardwrightError  # noqa: E402
from boardwright.output_manifest import build_manifest  # noqa: E402
from boardwright.release import prepare_release  # noqa: E402
from boardwright.release_date import display_date, parse_release_date, resolve_release_date, utc_today  # noqa: E402
from boardwright.release_ledger import load_ledger  # noqa: E402
from boardwright.review_gate import (  # noqa: E402
    CANDIDATE_FILE,
    build_candidate,
    reviewed_release_date,
    verify_candidate,
)

ROOT = Path(__file__).resolve().parents[1]
SYDNEY = timezone(timedelta(hours=11))  # AEDT, the owner's zone in the 0.1.6 defect
PACIFIC = timezone(timedelta(hours=-8))
ENV = {
    "BOARDWRIGHT_SOURCE_SHA": "a" * 40,
    "IN_VERSION": "1.0.1",
    "IN_VARIANT": "RELEASED",
    "IN_RELEASE_KIND": "draft",
    "IN_DRAWING_REVISION": "A.0",
    "IN_REVISION_DESCRIPTION": "Tooling",
    "IN_CHANGE_CLASSES": "tooling",
    "IN_JUSTIFICATION": "Tooling",
}


class UtcDateTests(unittest.TestCase):
    def test_local_morning_is_the_previous_utc_day(self) -> None:
        # 2026-11-20 01:14 in Sydney is 2026-11-19 14:14 UTC (held run 44541045029).
        self.assertEqual("2026-11-19", utc_today(datetime(2026, 11, 20, 1, 14, tzinfo=SYDNEY)).isoformat())

    def test_local_evening_is_the_next_utc_day(self) -> None:
        self.assertEqual("2026-11-20", utc_today(datetime(2026, 11, 19, 16, 10, tzinfo=PACIFIC)).isoformat())

    def test_exact_utc_midnight(self) -> None:
        self.assertEqual("2026-11-20", utc_today(datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)).isoformat())
        self.assertEqual(
            "2026-11-19", utc_today(datetime(2026, 11, 19, 23, 59, 59, tzinfo=timezone.utc)).isoformat()
        )

    def test_naive_time_is_refused(self) -> None:
        with self.assertRaises(BoardwrightError):
            utc_today(datetime(2026, 11, 20, 1, 14))

    def test_explicit_date_wins_and_is_validated(self) -> None:
        self.assertEqual("2026-11-19", resolve_release_date("2026-11-19", datetime(2030, 1, 1, tzinfo=timezone.utc)).isoformat())
        for bad in ("2026-11-31", "19/11/2026", "2026-1-9", "today"):
            with self.assertRaises(BoardwrightError, msg=bad):
                parse_release_date(bad)

    def test_display_date_ignores_the_host_locale(self) -> None:
        self.assertEqual("19-Nov-2026", display_date(parse_release_date("2026-11-19")))
        self.assertEqual("01-Jan-2027", display_date(parse_release_date("2027-01-01")))


class PreparedDateTests(unittest.TestCase):
    """Ledger, CHANGELOG and release.env carry the same fixed date."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="bw-reldate-"))
        self.project = Project(self.tmp)
        self.project.release("1.0.0", "A.0", justification="Initial")

    def tearDown(self) -> None:
        _rmtree(self.tmp)

    def _cli(self, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        env.pop("BOARDWRIGHT_REMOTE_REFS", None)
        return subprocess.run(
            [sys.executable, "-m", "boardwright", "release", *args],
            cwd=self.tmp, env=env, capture_output=True, text=True,
        )

    def test_prepare_uses_the_utc_date_across_a_local_day_boundary(self) -> None:
        plan = prepare_release(
            load_config(self.tmp), "1.0.1", allow_dirty=True, dry_run=False, drawing_revision="A.0",
            change_classes="tooling", justification="Tooling", check_remote=False,
            now=datetime(2026, 11, 20, 1, 14, tzinfo=SYDNEY),
        )
        self.assertEqual("2026-11-19", plan.release_date)
        self.assertEqual("2026-11-19", load_ledger(self.tmp)[-1].date)
        self.assertIn("## [1.0.1] - 2026-11-19", (self.tmp / "CHANGELOG.md").read_text(encoding="utf-8"))

    def test_cli_writes_the_date_to_release_env_and_records_the_preparation(self) -> None:
        done = self._cli("1.0.1", "--prepare", "--allow-dirty", "--release-env", "--variant", "RELEASED",
                         "--kind", "draft", "--drawing-revision", "A.0", "--change-class", "tooling",
                         "--justification", "Tooling", "--release-date", "2026-11-19")
        self.assertEqual(0, done.returncode, done.stderr)
        env = (self.tmp / ".boardwright" / "release.env").read_text(encoding="utf-8").splitlines()
        self.assertIn("RELEASE_DATE=19-Nov-2026", env)
        self.assertIn("RELEASE_DATE_NUM=2026-11-19", env)
        self.assertEqual("2026-11-19", load_ledger(self.tmp)[-1].date)
        record = json.loads((self.tmp / ".git" / "boardwright-release-preparation.json").read_text(encoding="utf-8"))
        self.assertEqual(("1.0.1", "2026-11-19"), (record["version"], record["release_date"]))
        self.assertIn(".boardwright/release.env", record["files"])

    def test_cli_rejects_an_invalid_release_date_before_writing(self) -> None:
        before = (self.tmp / ".boardwright" / "releases.yaml").read_bytes()
        done = self._cli("1.0.1", "--prepare", "--allow-dirty", "--release-env", "--drawing-revision", "A.0",
                         "--change-class", "tooling", "--justification", "Tooling", "--release-date", "2026-02-30")
        self.assertNotEqual(0, done.returncode)
        self.assertEqual(before, (self.tmp / ".boardwright" / "releases.yaml").read_bytes())


@unittest.skipUnless(shutil.which("sh"), "needs a POSIX shell (Git Bash on Windows)")
class KiBotDateVariableTests(unittest.TestCase):
    """The KiBot text variables read release.env, not the commit timestamp."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="bw-kibotdate-"))
        _git(self.tmp, "init", "-q", "-b", "main")
        (self.tmp / "f.txt").write_text("x\n", encoding="utf-8")
        _git(self.tmp, "add", "f.txt")
        # Committed at 00:30 on 2026-11-20 in Sydney (13:30 UTC on 2026-11-19).
        env = {**os.environ, "GIT_AUTHOR_DATE": "2026-11-20T00:30:00+11:00", "GIT_COMMITTER_DATE": "2026-11-20T00:30:00+11:00"}
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "c"],
                       cwd=self.tmp, env=env, check=True)
        text = (ROOT / "boardwright_resources/kibot/yaml/kibot_pre_set_text_variables.yaml").read_text(encoding="utf-8")
        data = next(yaml.safe_load_all(text))  # KiBot puts its definitions after "..."
        items = data["preflight"]["set_text_variables"]
        self.commands = {item["variable"]: item["command"] for item in items if "command" in item}

    def tearDown(self) -> None:
        _rmtree(self.tmp)

    def _run(self, name: str) -> str:
        command = self.commands[name].replace("@RELEASE_DATE@", "")
        return subprocess.run(["sh", "-c", command], cwd=self.tmp, capture_output=True, text=True, check=True).stdout.strip()

    def test_release_env_date_overrides_the_commit_date(self) -> None:
        (self.tmp / ".boardwright").mkdir()
        (self.tmp / ".boardwright" / "release.env").write_text(
            "RELEASE_VERSION=1.0.1\nRELEASE_DATE=19-Nov-2026\nRELEASE_DATE_NUM=2026-11-19\n", encoding="utf-8", newline="\n"
        )
        self.assertEqual("2026-11-19", self._run("RELEASE_DATE_NUM"))
        self.assertEqual("19-Nov-2026", self._run("RELEASE_DATE"))

    def test_without_release_env_the_commit_date_is_used(self) -> None:
        # Unreleased builds keep the old behaviour (author date in its own zone).
        self.assertEqual("2026-11-20", self._run("RELEASE_DATE_NUM"))


class ReviewedDateTests(unittest.TestCase):
    """Held at one instant, approved later: only the reviewed date passes the gate."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="bw-reviewdate-"))
        self.candidate = Path(tempfile.mkdtemp(prefix="bw-reviewdate-cand-"))
        self.project = Project(self.tmp)
        self.project.release("1.0.0", "A.0", justification="Initial")
        # Held run: 2026-11-20 01:14 in Sydney = 2026-11-19 UTC.
        self._prepare(release_date="", now=datetime(2026, 11, 20, 1, 14, tzinfo=SYDNEY))
        (self.candidate / ".boardwright").mkdir(parents=True)
        (self.candidate / CANDIDATE_FILE).write_text(json.dumps(build_candidate(self.tmp, ENV)), encoding="utf-8")
        shutil.copy(self.tmp / ".boardwright" / "accepted_manifest.json", self.candidate / ".boardwright")
        self._reset()

    def tearDown(self) -> None:
        _rmtree(self.tmp)
        shutil.rmtree(self.candidate, ignore_errors=True)

    def _prepare(self, release_date: str, now: datetime) -> None:
        from boardwright.cli import _write_release_env

        config = load_config(self.tmp)
        plan = prepare_release(
            config, "1.0.1", allow_dirty=True, dry_run=False, drawing_revision="A.0", change_classes="tooling",
            justification="Tooling", revision_description="Tooling", check_remote=False,
            release_date=release_date, now=now,
        )
        _write_release_env(config, "1.0.1", "RELEASED", "draft", plan.release_date)
        manifest = self.tmp / ".boardwright" / "accepted_manifest.json"
        manifest.write_text(json.dumps(build_manifest(self.tmp)), encoding="utf-8")

    def _reset(self) -> None:
        _git(self.tmp, "checkout", "-q", "--", ".")
        _git(self.tmp, "clean", "-qfdx", "--", ".boardwright")

    def test_reviewed_date_is_read_from_the_candidate(self) -> None:
        self.assertEqual("2026-11-19", reviewed_release_date(self.candidate))

    def test_approved_run_with_the_reviewed_date_matches(self) -> None:
        # Approved two days later, on the next local and UTC day.
        self._prepare(reviewed_release_date(self.candidate), now=datetime(2026, 11, 21, 9, 0, tzinfo=SYDNEY))
        self.assertEqual([], verify_candidate(self.tmp, self.candidate, ENV))

    def test_approved_run_on_another_date_is_rejected(self) -> None:
        self._prepare("", now=datetime(2026, 11, 21, 9, 0, tzinfo=SYDNEY))
        problems = verify_candidate(self.tmp, self.candidate, ENV)
        self.assertTrue(any("Release date differs" in p for p in problems), problems)
        self.assertTrue(any("Release metadata .boardwright/release.env" in p for p in problems), problems)
        self.assertTrue(any("Release metadata CHANGELOG.md" in p for p in problems), problems)

    def test_unexpected_document_metadata_change_is_rejected(self) -> None:
        self._prepare("2026-11-19", now=datetime(2026, 11, 21, 9, 0, tzinfo=SYDNEY))
        variables = self.tmp / ".boardwright" / "revision_history_variables.env"
        variables.write_text(variables.read_text(encoding="utf-8") + "REVTABLE_9_DATE=\"2026-11-21\"\n", encoding="utf-8")
        revisions = self.tmp / ".boardwright" / "document_revisions.yaml"
        revisions.write_text(revisions.read_text(encoding="utf-8").replace("Tooling", "Re-dated"), encoding="utf-8")
        problems = verify_candidate(self.tmp, self.candidate, ENV)
        self.assertTrue(any(".boardwright/revision_history_variables.env differs" in p for p in problems), problems)

    def test_release_env_date_must_match_the_ledger(self) -> None:
        self._prepare("2026-11-19", now=datetime(2026, 11, 21, 9, 0, tzinfo=SYDNEY))
        env = self.tmp / ".boardwright" / "release.env"
        env.write_text(env.read_text(encoding="utf-8").replace("RELEASE_DATE_NUM=2026-11-19", "RELEASE_DATE_NUM=2026-11-20"), encoding="utf-8")
        problems = verify_candidate(self.tmp, self.candidate, ENV)
        self.assertTrue(any("does not match the ledger date" in p for p in problems), problems)

    def test_schema_1_candidate_is_refused(self) -> None:
        data = json.loads((self.candidate / CANDIDATE_FILE).read_text(encoding="utf-8"))
        data["schema"] = 1
        (self.candidate / CANDIDATE_FILE).write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(ValueError):
            reviewed_release_date(self.candidate)
        self.assertIn("Unsupported review candidate schema", verify_candidate(self.tmp, self.candidate, ENV)[0])


class PrepareWorkflowDateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = (ROOT / ".github/workflows/prepare-release.yaml").read_text(encoding="utf-8")
        self.steps = yaml.safe_load(self.text)["jobs"]["prepare"]["steps"]
        self.names = [step.get("name") for step in self.steps]

    def test_approved_run_takes_the_reviewed_date_before_building(self) -> None:
        build = self.names.index("Build release candidate in the pinned container")
        for name in ("Check approved review run", "Download reviewed candidate", "Use the reviewed release date"):
            self.assertLess(self.names.index(name), build, name)
        step = self.steps[build]
        self.assertIn('${IN_RELEASE_DATE:+--release-date "$IN_RELEASE_DATE"}', step["run"])
        self.assertIn("release-date --candidate", self.steps[self.names.index("Use the reviewed release date")]["run"])

    def _run_step(self, name: str, env: dict[str, str]) -> subprocess.CompletedProcess:
        script = self.steps[self.names.index(name)]["run"]
        with tempfile.TemporaryDirectory() as tmp:
            github_env = Path(tmp) / "env"
            summary = Path(tmp) / "summary"
            full = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "GITHUB_ENV": str(github_env),
                    "GITHUB_STEP_SUMMARY": str(summary), **env}
            done = subprocess.run(["bash", "-c", script], env=full, capture_output=True, text=True)
            done.exported = github_env.read_text(encoding="utf-8") if github_env.exists() else ""
            return done

    def test_requested_date_applies_only_without_an_approved_run(self) -> None:
        step = self.steps[self.names.index("Use the requested release date")]
        self.assertEqual("${{ inputs.approved_review_run == '' && inputs.release_date != '' }}", step["if"])
        self.assertLess(self.names.index("Use the requested release date"),
                        self.names.index("Build release candidate in the pinned container"))

    @unittest.skipUnless(shutil.which("bash"), "needs bash")
    def test_requested_date_is_validated_and_exported(self) -> None:
        done = self._run_step("Use the requested release date", {"IN_RELEASE_DATE_INPUT": "2026-11-20"})
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("IN_RELEASE_DATE=2026-11-20", done.exported)
        bad = self._run_step("Use the requested release date", {"IN_RELEASE_DATE_INPUT": "2026-11-31"})
        self.assertNotEqual(0, bad.returncode)
        self.assertEqual("", bad.exported)

    @unittest.skipUnless(shutil.which("bash"), "needs bash")
    def test_approved_run_rejects_a_date_other_than_the_reviewed_one(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="bw-reviewed-date-"))
        try:
            (tmp / ".boardwright").mkdir()
            candidate = {"schema": 2, "release_date": "2026-11-20", "ledger_entry": {"date": "2026-11-20"}}
            (tmp / CANDIDATE_FILE).write_text(json.dumps(candidate), encoding="utf-8")
            name = "Use the reviewed release date"
            same = self._run_step(name, {"REVIEWED_CANDIDATE": str(tmp), "IN_RELEASE_DATE_INPUT": "2026-11-20"})
            self.assertEqual(0, same.returncode, same.stderr)
            self.assertIn("IN_RELEASE_DATE=2026-11-20", same.exported)
            other = self._run_step(name, {"REVIEWED_CANDIDATE": str(tmp), "IN_RELEASE_DATE_INPUT": "2026-11-21"})
            self.assertNotEqual(0, other.returncode)
            self.assertIn("differs from the reviewed date", other.stderr)
            self.assertEqual("", other.exported)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_reviewed_candidate_stays_outside_the_work_tree(self) -> None:
        download = self.steps[self.names.index("Download reviewed candidate")]
        self.assertTrue(download["with"]["path"].startswith("${{ runner.temp }}"))


if __name__ == "__main__":
    unittest.main()
