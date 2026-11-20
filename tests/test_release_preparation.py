# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Release-preparation provenance, candidate comparison and stage release references."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_revision_model import Project, _git, _rmtree  # noqa: E402

from boardwright import container  # noqa: E402
from boardwright.build import BuildError, preflight  # noqa: E402
from boardwright.config import load_config  # noqa: E402
from boardwright.container import ContainerError, compare_builds, pin_release_refs, stage  # noqa: E402
from boardwright.errors import BoardwrightError  # noqa: E402
from boardwright.release import prepare_release, remote_tag_state  # noqa: E402
from boardwright.release_preparation import record_preparation, verify_preparation  # noqa: E402


def _prepare(root: Path, version: str = "1.0.1", release_date: str = "2026-11-19") -> None:
    from boardwright.cli import _write_release_env

    config = load_config(root)
    plan = prepare_release(
        config, version, allow_dirty=True, dry_run=False, drawing_revision="A.0", change_classes="tooling",
        justification="Tooling", revision_description="Tooling", check_remote=False, release_date=release_date,
    )
    _write_release_env(config, version, "RELEASED", "draft", plan.release_date)
    record_preparation(root, version=version, release_date=plan.release_date)


class PreparationRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="bw-prep-"))
        self.project = Project(self.tmp)
        self.project.release("1.0.0", "A.0", justification="Initial")
        # The build refuses CRLF working copies; give the tree a stage's LF checkout.
        _git(self.tmp, "config", "core.autocrlf", "false")
        _git(self.tmp, "config", "core.eol", "lf")
        # As in the template's .gitignore: generated revision CSVs are not tracked.
        (self.tmp / ".gitignore").write_text(".boardwright/revision_history_*.csv\n", encoding="utf-8", newline="\n")
        _git(self.tmp, "rm", "--cached", "-r", "-q", ".")
        self.project.commit("lf checkout, ignore generated CSVs")
        _git(self.tmp, "reset", "--hard", "-q")

    def tearDown(self) -> None:
        _rmtree(self.tmp)

    def _modified(self) -> list[str]:
        return [p for p in _git(self.tmp, "diff", "--name-only", "HEAD").splitlines() if p]

    def test_prepared_edits_are_verified(self) -> None:
        _prepare(self.tmp)
        result = verify_preparation(self.tmp, self._modified())
        self.assertEqual([], result["problems"])
        self.assertEqual(("1.0.1", "2026-11-19"), (result["version"], result["release_date"]))
        self.assertTrue(set(self._modified()) <= set(result["files"]))
        self.assertEqual([], preflight(self.tmp)["release_preparation"]["problems"])

    def test_edit_after_preparation_is_detected_and_fails_the_build(self) -> None:
        _prepare(self.tmp)
        changelog = self.tmp / "CHANGELOG.md"
        changelog.write_text(changelog.read_text(encoding="utf-8") + "\n- sneaked in\n", encoding="utf-8", newline="\n")
        result = verify_preparation(self.tmp, self._modified())
        self.assertIn("CHANGELOG.md changed after release preparation", result["problems"])
        with self.assertRaisesRegex(BuildError, "Prepared release sources were modified"):
            preflight(self.tmp)

    def test_untracked_by_git_metadata_edit_is_detected(self) -> None:
        _prepare(self.tmp)
        env = self.tmp / ".boardwright" / "release.env"
        env.write_text(env.read_text(encoding="utf-8").replace("2026-11-19", "2026-11-20"), encoding="utf-8")
        result = verify_preparation(self.tmp, self._modified())
        self.assertIn(".boardwright/release.env changed after release preparation", result["problems"])

    def test_unrelated_modification_is_never_authorised(self) -> None:
        _prepare(self.tmp)
        (self.tmp / "board.kicad_pcb").write_text("(kicad_pcb (version 1))\n", encoding="utf-8", newline="\n")
        result = verify_preparation(self.tmp, self._modified())
        self.assertIn("board.kicad_pcb was modified but is not a release-preparation file", result["problems"])
        with self.assertRaisesRegex(BuildError, "Prepared release sources were modified"):
            preflight(self.tmp)

    def test_preparation_refuses_a_tree_with_foreign_changes(self) -> None:
        (self.tmp / "board.kicad_pcb").write_text("(kicad_pcb (version 1))\n", encoding="utf-8", newline="\n")
        with self.assertRaises(BoardwrightError):
            record_preparation(self.tmp, version="1.0.1", release_date="2026-11-19")

    def test_record_for_another_commit_is_rejected(self) -> None:
        _prepare(self.tmp)
        record = self.tmp / ".git" / "boardwright-release-preparation.json"
        data = json.loads(record.read_text(encoding="utf-8"))
        data["source_commit"] = "0" * 40
        record.write_text(json.dumps(data), encoding="utf-8")
        self.assertTrue(any("recorded for" in p for p in verify_preparation(self.tmp, self._modified())["problems"]))

    def _kicad_pro(self, data: dict) -> Path:
        path = self.tmp / "board.kicad_pro"
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
        return path

    def test_project_text_variables_sync_is_authorised(self) -> None:
        self._kicad_pro({"board": {"x": 1}, "text_variables": {"GIT_URL": "old"}})
        self.project.commit("kicad project")
        self._kicad_pro({"board": {"x": 1}, "text_variables": {"GIT_URL": "new", "RELEASE_DATE": ""}})
        _prepare(self.tmp)
        result = verify_preparation(self.tmp, self._modified())
        self.assertEqual([], result["problems"])
        self.assertEqual(["board.kicad_pro"], result["project_files"])
        self.assertNotIn("board.kicad_pro", result["files"])

    def test_project_change_outside_text_variables_is_refused(self) -> None:
        self._kicad_pro({"board": {"x": 1}, "text_variables": {}})
        self.project.commit("kicad project")
        self._kicad_pro({"board": {"x": 2}, "text_variables": {}})
        with self.assertRaisesRegex(BoardwrightError, "board.kicad_pro"):
            _prepare(self.tmp)

    def test_project_edited_after_preparation_is_detected(self) -> None:
        self._kicad_pro({"board": {"x": 1}, "text_variables": {}})
        self.project.commit("kicad project")
        self._kicad_pro({"board": {"x": 1}, "text_variables": {"A": "1"}})
        _prepare(self.tmp)
        self._kicad_pro({"board": {"x": 9}, "text_variables": {"A": "1"}})
        self.assertIn("board.kicad_pro changed outside its text variables",
                      verify_preparation(self.tmp, self._modified())["problems"])

    def test_modified_tree_without_a_record_stays_unexplained(self) -> None:
        (self.tmp / "CHANGELOG.md").write_text("# Changelog\n\nedited\n", encoding="utf-8", newline="\n")
        self.assertIsNone(preflight(self.tmp)["release_preparation"])


def _evidence(root: Path, name: str, record: dict) -> Path:
    directory = root / name
    directory.mkdir()
    (directory / "record.json").write_text(json.dumps(record), encoding="utf-8")
    manifest = {"schema": 1, "normaliser": 2, "files": {}}
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return directory


class CompareTests(unittest.TestCase):
    MODIFIED = ["CHANGELOG.md", ".boardwright/releases.yaml"]
    FILES = {"CHANGELOG.md": "1" * 64, ".boardwright/releases.yaml": "2" * 64, ".boardwright/release.env": "3" * 64}

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="bw-cmp-"))
        self.count = 0

    def tearDown(self) -> None:
        _rmtree(self.tmp)

    def _record(self, modified=None, preparation="default") -> dict:
        record = {
            "source_commit": "c" * 40, "variant": "RELEASED", "image": "img@sha256:1", "image_id": "sha256:1",
            "normaliser": 2, "stage": {}, "source_modified": list(self.MODIFIED if modified is None else modified),
        }
        if preparation == "default":
            preparation = {"version": "0.1.6", "release_date": "2026-11-19", "source_commit": "c" * 40,
                           "files": dict(self.FILES), "problems": []}
        record["release_preparation"] = preparation
        return record

    def _compare(self, first: dict, second: dict):
        self.count += 1
        return compare_builds(_evidence(self.tmp, f"a{self.count}", first), _evidence(self.tmp, f"b{self.count}", second))

    def test_identical_verified_preparations_match(self) -> None:
        problems, notes = self._compare(self._record(), self._record())
        self.assertEqual([], problems)
        self.assertTrue(any("same verified release preparation" in n for n in notes))

    def test_different_prepared_content_is_an_error(self) -> None:
        other = self._record()
        other["release_preparation"]["files"]["CHANGELOG.md"] = "9" * 64
        problems, _ = self._compare(self._record(), other)
        self.assertEqual(["release preparation differs between the builds: files"], problems)

    def test_different_release_date_is_an_error(self) -> None:
        other = self._record()
        other["release_preparation"]["release_date"] = "2026-11-20"
        problems, _ = self._compare(self._record(), other)
        self.assertIn("release date", problems[0])

    def test_modification_without_preparation_is_still_an_error(self) -> None:
        problems, _ = self._compare(self._record(), self._record(preparation=None))
        self.assertIn("second build was made from uncommitted changes: CHANGELOG.md, .boardwright/releases.yaml", problems)

    def test_preparation_with_problems_is_not_trusted(self) -> None:
        bad = self._record()
        bad["release_preparation"]["problems"] = ["CHANGELOG.md changed after release preparation"]
        problems, _ = self._compare(self._record(), bad)
        self.assertTrue(any("second build was made from uncommitted changes" in p for p in problems))

    def test_modified_file_outside_the_preparation_is_an_error(self) -> None:
        extra = self.MODIFIED + ["board.kicad_pcb"]
        problems, _ = self._compare(self._record(modified=extra), self._record(modified=extra))
        self.assertIn("first build was made from uncommitted changes: board.kicad_pcb", problems)
        self.assertIn("second build was made from uncommitted changes: board.kicad_pcb", problems)

    def test_prepared_against_clean_build_is_an_error(self) -> None:
        problems, _ = self._compare(self._record(), self._record(modified=[], preparation=None))
        self.assertTrue(any("modified files" in p for p in problems), problems)


class StageReleaseReferenceTests(unittest.TestCase):
    """A stage gets its release references from the real upstream, on the host."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="bw-refs-"))
        self.upstream = self.tmp / "upstream.git"
        self.work = self.tmp / "work"
        self.work.mkdir()
        project = Project(self.work)
        project.release("1.0.0", "A.0", justification="Initial")
        _git(self.tmp, "init", "-q", "--bare", str(self.upstream))
        _git(self.work, "remote", "add", "origin", str(self.upstream))
        _git(self.work, "push", "-q", "origin", "main", "--tags")
        self.stages = self.tmp / "stages"
        self.env = mock.patch.dict(os.environ, {"BOARDWRIGHT_STAGE_ROOT": str(self.stages)})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        _rmtree(self.tmp)

    def _stage(self) -> Path:
        return stage(self.work, label="candidate")

    def test_stage_points_at_the_upstream_with_pinned_tags(self) -> None:
        target = self._stage()
        self.assertEqual(str(self.work.resolve()), str(Path(_git(target, "remote", "get-url", "origin").strip()).resolve()))
        # An upstream-only tag (pushed from elsewhere) must reach the stage.
        _git(self.work, "tag", "upstream-only")
        _git(self.work, "push", "-q", "origin", "upstream-only")
        _git(self.work, "tag", "-d", "upstream-only")
        snapshot = json.loads(pin_release_refs(self.work, target).read_text(encoding="utf-8"))
        self.assertEqual(str(self.upstream), snapshot["url"])
        self.assertEqual({"1.0.0", "upstream-only"}, set(snapshot["tags"]))
        self.assertEqual(str(self.upstream), _git(target, "remote", "get-url", "origin").strip())
        self.assertNotEqual(str(target), _git(target, "remote", "get-url", "origin").strip())
        self.assertTrue(_git(target, "rev-parse", "--verify", "refs/tags/upstream-only").strip())

    def test_local_tag_that_differs_from_the_upstream_fails(self) -> None:
        (self.work / "CHANGELOG.md").write_text("# Changelog\n\nmoved\n", encoding="utf-8")
        Project.commit(type("P", (), {"root": self.work})(), "moved")  # reuse the commit helper
        _git(self.work, "tag", "-f", "1.0.0")
        with self.assertRaises(ContainerError):
            pin_release_refs(self.work, self._stage())

    def test_origin_with_credentials_is_refused(self) -> None:
        _git(self.work, "remote", "set-url", "origin", "https://user:secret@example.invalid/repo.git")
        with self.assertRaises(ContainerError):
            pin_release_refs(self.work, self._stage())

    def test_missing_origin_is_refused(self) -> None:
        _git(self.work, "remote", "remove", "origin")
        with self.assertRaises(ContainerError):
            pin_release_refs(self.work, self._stage())


class PinnedRemoteTagTests(unittest.TestCase):
    """Inside the container the remote-tag check reads the snapshot, bound to origin."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="bw-pinned-"))
        self.project = Project(self.tmp)
        # A Windows path the Linux container cannot open: never contacted.
        self.url = "C:\\Users\\someone\\project"
        _git(self.tmp, "remote", "add", "origin", self.url)
        self.snapshot = self.tmp / ".git" / "boardwright-remote-refs.json"
        self._write({"schema": 1, "url": self.url, "tags": {"0.1.5": "a" * 40}})

    def tearDown(self) -> None:
        _rmtree(self.tmp)

    def _write(self, data) -> None:
        self.snapshot.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")

    def _state(self, version: str) -> tuple[str, str]:
        with mock.patch.dict(os.environ, {"BOARDWRIGHT_REMOTE_REFS": ".git/boardwright-remote-refs.json"}):
            return remote_tag_state(load_config(self.tmp), version)

    def test_snapshot_answers_without_contacting_the_remote(self) -> None:
        with mock.patch("boardwright.release.subprocess.run", wraps=__import__("subprocess").run) as run:
            self.assertEqual(("absent", ""), self._state("0.1.6"))
            self.assertEqual(("exists", ""), self._state("0.1.5"))
        self.assertFalse(any("ls-remote" in call.args[0] for call in run.call_args_list))

    def test_snapshot_for_another_remote_is_unknown(self) -> None:
        self._write({"schema": 1, "url": "https://example.invalid/other.git", "tags": {}})
        self.assertEqual("unknown", self._state("0.1.6")[0])

    def test_corrupt_or_missing_snapshot_is_unknown(self) -> None:
        self._write("{not json")
        self.assertEqual("unknown", self._state("0.1.6")[0])
        self.snapshot.unlink()
        self.assertEqual("unknown", self._state("0.1.6")[0])
        self._write({"schema": 2, "url": self.url, "tags": {}})
        self.assertEqual("unknown", self._state("0.1.6")[0])

    def test_without_the_variable_the_remote_is_queried_and_fails_closed(self) -> None:
        os.environ.pop("BOARDWRIGHT_REMOTE_REFS", None)
        self.assertEqual("unknown", remote_tag_state(load_config(self.tmp), "0.1.6")[0])


class CandidateStepsTests(unittest.TestCase):
    def test_release_date_is_passed_only_when_given(self) -> None:
        import argparse

        base = dict(version="1.2.3", variant="RELEASED", kind="draft", drawing_revision="B", revision_description="d",
                    change_class="tooling", justification="j", kicad="9")
        held = container.candidate_steps(argparse.Namespace(**base, release_date=""))[0]
        approved = container.candidate_steps(argparse.Namespace(**base, release_date="2026-11-19"))[0]
        self.assertNotIn("--release-date", held)
        self.assertEqual("2026-11-19", approved[approved.index("--release-date") + 1])


if __name__ == "__main__":
    unittest.main()
