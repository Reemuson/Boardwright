# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Local releases: plan, candidate, review, approval, status (LOCAL_FIRST.md section 5).

Each test uses a disposable Git repository and store. The container steps are
replaced by a fake that writes what the real steps write (``release.env``, the
accepted manifest, ``review_candidate.json``); the real-engine run is in the
integration notes (docs/AUDIT-2026-11.md).
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from boardwright import local_release, release_store
from boardwright.backends import DockerInfo, RemoteInfo
from boardwright.config import BoardwrightConfig
from boardwright.local_release import LocalReleaseError, ReleaseRequest
from boardwright.release_store import ReleaseStore, StoreError

ROOT = Path(__file__).resolve().parents[1]
DATE = "2026-11-20"
PREVIOUS_LEDGER = """schema: 1
releases:
  - version: 0.9.0
    date: '2026-11-01'
    source_commit: 1111111111111111111111111111111111111111
    previous_version: ''
    variant: RELEASED
    identities: {pcb_revision: A, schematic_revision: A, assembly_revision: A, fabrication_revision: A}
    change_classes: []
    justification: Initial release.
    fabrication_baseline: {id: A/1, status: initial}
    assembly_baseline: {id: A, status: initial}
    fingerprint: {schema: 2, source: tag, pcb_geometry: g, pcb_netnames: n, fabrication_spec: f, assembly: a, schematic: s}
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _writable(func, path, _exc):
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    func(path)


def _rmtree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, **({"onexc": _writable} if sys.version_info >= (3, 12) else {"onerror": _writable}))


def snapshot(root: Path) -> dict[str, str]:
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            path = Path(dirpath) / name
            out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


class FakeSteps:
    """Stands in for the container: writes the files each candidate step writes."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.host_commands: list[list[str]] = []
        self.fail = ""
        self.during_build = None

    def __call__(self, kicad, workdir, command, environment=None):
        workdir = Path(workdir)
        self.commands.append(command)
        env = environment or {}
        text = " ".join(command)
        if self.fail and self.fail in text:
            return 1
        if "--prepare" in command:
            date = command[command.index("--release-date") + 1] if "--release-date" in command else DATE
            _write(workdir / ".boardwright" / "release.env", f"RELEASE_VERSION={env['IN_VERSION']}\nRELEASE_DATE_NUM={date}\n")
            _write(workdir / "CHANGELOG.md", f"# Changelog\n\n## [{env['IN_VERSION']}] - {date}\n")
        elif command[2:4] == ["boardwright", "build"]:
            if self.during_build:
                self.during_build()
            _write(workdir / "Manufacturing" / "Fabrication" / "board-fabrication.pdf",
                   "PDF " + (workdir / "board.kicad_pcb").read_text(encoding="utf-8"))
            _write(workdir / "Schematic" / "board-schematic.pdf", "PDF schematic\n")
        elif "boardwright.output_manifest" in command:
            pdf = (workdir / "Manufacturing" / "Fabrication" / "board-fabrication.pdf").read_bytes()
            _write(workdir / ".boardwright" / "accepted_manifest.json", json.dumps(
                {"schema": 1, "files": {"Manufacturing/Fabrication/board-fabrication.pdf":
                                        {"sha256": hashlib.sha256(pdf).hexdigest(), "kind": "drawing"}}},
                indent=2, sort_keys=True) + "\n")
        elif "boardwright.review_gate" in command:
            env_text = (workdir / ".boardwright" / "release.env").read_text(encoding="utf-8")
            date = env_text.split("RELEASE_DATE_NUM=")[1].strip()
            manifest = (workdir / ".boardwright" / "accepted_manifest.json").read_bytes()
            _write(workdir / ".boardwright" / "review_candidate.json", json.dumps({
                "schema": 2, "source_sha": env["BOARDWRIGHT_SOURCE_SHA"],
                "inputs": {k: env.get(k, "") for k in local_release.INPUT_KEYS},
                "ledger_entry": {"version": env["IN_VERSION"], "date": date}, "release_date": date,
                "release_metadata": {}, "manifest_sha256": hashlib.sha256(manifest).hexdigest()},
                indent=2, sort_keys=True) + "\n")
        return 0

    def host(self, command):
        self.host_commands.append(command)
        return 0


def remote(kind="none", usable=False):
    return RemoteInfo(kind=kind, url="" if kind == "none" else "https://github.com/o/r.git",
                      repository="o/r" if kind == "github" else "", reachable=usable or None,
                      gh_authenticated=usable or None, workflows_present=usable, problems=())


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="bw local release "))
        self.repo = self.base / "board é"
        self.repo.mkdir()
        for args in (("init", "-q", "-b", "main"), ("config", "user.name", "Rev Iewer"),
                     ("config", "user.email", "reviewer@example.invalid"), ("config", "core.autocrlf", "false")):
            _git(self.repo, *args)
        _write(self.repo / ".gitignore", ".boardwright/local.yaml\n")
        _write(self.repo / "CHANGELOG.md", "# Changelog\n\n## [Unreleased]\n\n- First board.\n")
        _write(self.repo / "board.kicad_pcb", "(kicad_pcb rev A)\n")
        _write(self.repo / ".boardwright" / "project.yaml", "project: {id: BOARD/1, name: board}\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "init")
        self.env = mock.patch.dict(os.environ, {"BOARDWRIGHT_RELEASE_STORE": str(self.base / "store base"),
                                                "BOARDWRIGHT_STAGE_ROOT": str(self.base / "stages")})
        self.env.start()
        self.config = BoardwrightConfig(root=self.repo, project={"project": {"id": "BOARD/1", "name": "board"}},
                                        branches={"branches": {"release": "main"}}, legal={},
                                        revision_history={"revision_history": {}})
        self.store = ReleaseStore.for_project(self.repo, self.config.project)
        self.steps = FakeSteps()
        self.out: list[str] = []

    def tearDown(self) -> None:
        self.env.stop()
        _rmtree(self.base)

    def request(self, version="1.0.0", **kw) -> ReleaseRequest:
        return ReleaseRequest(version=version, drawing_revision="A", revision_description="First release",
                              release_date=DATE, **kw)

    def candidate(self, version="1.0.0", **kw) -> dict:
        return local_release.build_candidate(self.config, self.request(version, **kw), self.store,
                                             runner=self.steps, host_runner=self.steps.host, echo=self.out.append)

    def review(self, record: dict, *, approved=True) -> Path:
        draft = local_release.write_review_draft(self.store, record["version"], record["candidate_id"])
        text = draft.read_text(encoding="utf-8").replace("- [ ]", "- [x]")
        if approved:
            text = text.replace("APPROVED: no", "APPROVED: yes")
        path = self.base / f"REVIEW-{record['version']}.md"
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    def approve(self, record: dict, path: Path | None = None) -> dict:
        return local_release.approve(self.config, self.store, record["version"], path or self.review(record),
                                     record["candidate_id"], echo=self.out.append)


class StoreTests(Case):
    def test_project_identity_is_collision_resistant_and_shared_by_clones(self) -> None:
        other = self.base / "other"
        other.mkdir()
        _git(other, "init", "-q", "-b", "main")
        _write(other / "x", "different history\n")
        _git(other, "add", "-A")
        _git(other, "-c", "user.name=a", "-c", "user.email=a@b.c", "commit", "-q", "-m", "x")
        clone = self.base / "clone"
        _git(self.base, "clone", "-q", str(self.repo), str(clone))
        same_name = {"project": {"id": "BOARD/1"}}
        mine = release_store.project_identity(self.repo, same_name)
        self.assertNotEqual(mine, release_store.project_identity(other, same_name))
        self.assertEqual(mine, release_store.project_identity(clone, same_name))
        self.assertRegex(mine, r"^board-1-[0-9a-f]{16}$")

    def test_default_store_is_outside_the_project_and_inside_is_refused(self) -> None:
        with mock.patch.dict(os.environ, {"BOARDWRIGHT_RELEASE_STORE": str(self.repo / "store")}):
            with self.assertRaises(StoreError):
                ReleaseStore.for_project(self.repo, self.config.project)
        with mock.patch.dict(os.environ, {"BOARDWRIGHT_RELEASE_STORE": ""}):
            store = ReleaseStore.for_project(self.repo, {"project": {"id": "X"},
                                                         "release": {"local_store": str(self.base / "configured")}})
            self.assertEqual(self.base / "configured", store.root.parent)

    def test_unrelated_files_and_other_projects_are_refused(self) -> None:
        _write(self.store.root / "my notes.txt", "personal\n")
        with self.assertRaises(StoreError) as caught:
            self.store.ensure()
        self.assertIn("not a Boardwright release store", str(caught.exception))
        (self.store.root / "my notes.txt").unlink()
        _write(self.store.root / release_store.STORE_MARKER, json.dumps({"schema": 1, "project_id": "someone-else"}))
        with self.assertRaises(StoreError):
            self.store.ensure()

    def test_links_and_junctions_in_the_store_are_refused(self) -> None:
        target = self.base / "elsewhere"
        target.mkdir()
        link = self.store.root
        link.parent.mkdir(parents=True)
        made = False
        try:
            os.symlink(target, link, target_is_directory=True)
            made = True
        except (OSError, NotImplementedError):
            if os.name == "nt":
                made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                      capture_output=True).returncode == 0
        if not made:
            self.skipTest("cannot create a symbolic link or junction here")
        with self.assertRaises(StoreError) as caught:
            self.store.ensure()
        self.assertIn("link or junction", str(caught.exception))
        self.assertEqual([], list(target.iterdir()))  # nothing written through the link

    def test_path_traversal_is_refused(self) -> None:
        self.store.ensure()
        for bad in ("..", "../x", "a/b", "a\\b", ".hidden", "", "x" * 200):
            with self.assertRaises(StoreError, msg=bad):
                self.store.entry_path("candidates", bad)
        for relative in ("../escape", "/abs", "a/../b", "C:/x", "a//b"):
            with self.assertRaises(StoreError, msg=relative):
                self.store.freeze("candidates", "1.0.0", "a" * 64, {relative: b"x"})
        self.assertFalse((self.store.root.parent / "escape").exists())
        self.assertEqual([], self.store.abandoned())

    def test_entries_are_write_once_checksummed_and_read_only(self) -> None:
        self.store.ensure()
        entry = self.store.freeze("candidates", "1.0.0", "a" * 64, {"x/y.txt": b"hello\n"})
        self.assertEqual([], self.store.verify_entry(entry.path))
        self.assertFalse(os.stat(entry.path / "x" / "y.txt").st_mode & stat.S_IWUSR)  # mode bits (root ignores access)
        with self.assertRaises(StoreError):
            self.store.freeze("candidates", "1.0.0", "a" * 64, {"x/y.txt": b"other\n"})
        os.chmod(entry.path / "x" / "y.txt", stat.S_IWRITE | stat.S_IREAD)
        (entry.path / "x" / "y.txt").write_bytes(b"tampered\n")
        _write(entry.path / "extra.txt", "added\n")
        problems = self.store.verify_entry(entry.path)
        self.assertTrue(any("does not match its checksum" in p for p in problems))
        self.assertTrue(any("not listed" in p for p in problems))

    def test_interrupted_freeze_leaves_no_entry_and_is_recoverable(self) -> None:
        self.store.ensure()
        with mock.patch.object(release_store, "_read_only", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.store.freeze("candidates", "1.0.0", "b" * 64, {"f": b"x"})
        self.assertEqual([], self.store.entries("candidates"))
        self.assertEqual([], self.store.abandoned())
        # A hard kill leaves the owned temporary behind: detected and recoverable.
        (self.store.root / ".tmp-deadbeef").mkdir()
        lines = local_release.status_lines(self.store)
        self.assertTrue(any("Interrupted write: .tmp-deadbeef" in line for line in lines))
        local_release.recover(self.store, echo=self.out.append)
        self.assertEqual([], self.store.abandoned())

    def test_index_hash_chain_detects_edits_removals_and_reordering(self) -> None:
        self.store.ensure()
        for n in range(3):
            self.store.append_event({"event": "e", "n": n})
        self.assertEqual([], self.store.verify_index())
        index = self.store.root / release_store.INDEX
        original = index.read_bytes()
        lines = original.splitlines(keepends=True)
        for broken in (original.replace(b'"n":1', b'"n":9'), lines[0] + lines[2], lines[1] + lines[0] + lines[2],
                       original[:-1]):
            index.write_bytes(broken)
            self.assertTrue(self.store.verify_index(), broken)
            if broken != original[:-1]:
                with self.assertRaises(StoreError):
                    self.store.append_event({"event": "after edit"})
        index.write_bytes(original)
        self.assertEqual([], self.store.verify_index())


class PlanTests(Case):
    def test_clean_committed_head_plans(self) -> None:
        result = local_release.plan(self.config, "1.0.0", self.store)
        self.assertEqual([], result.problems)
        self.assertEqual(_git(self.repo, "rev-parse", "HEAD").strip(), result.head)

    def test_dirty_and_untracked_worktrees_are_refused(self) -> None:
        _write(self.repo / "board.kicad_pcb", "(kicad_pcb edited)\n")
        self.assertTrue(any("uncommitted" in p for p in local_release.plan(self.config, "1.0.0").problems))
        _git(self.repo, "checkout", "--", "board.kicad_pcb")
        _write(self.repo / "scratch.kicad_sch", "(kicad_sch)\n")
        self.assertTrue(any("uncommitted" in p for p in local_release.plan(self.config, "1.0.0").problems))

    def test_existing_tag_wrong_branch_and_bad_version_are_refused(self) -> None:
        _git(self.repo, "tag", "1.0.0")
        self.assertTrue(any("tag already exists" in p for p in local_release.plan(self.config, "1.0.0").problems))
        _git(self.repo, "checkout", "-q", "-b", "feature")
        self.assertTrue(any("expected release branch" in p for p in local_release.plan(self.config, "1.0.1").problems))
        self.assertTrue(local_release.plan(self.config, "../1").problems)

    def test_previous_release_without_a_local_tag_is_refused(self) -> None:
        _write(self.repo / ".boardwright" / "releases.yaml", PREVIOUS_LEDGER)
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "ledger")
        problems = local_release.plan(self.config, "1.0.0").problems
        self.assertTrue(any("0.9.0 has no local tag" in p for p in problems), problems)


class BaselineFailClosedTests(Case):
    """A reused baseline whose previous release has no committed manifest is refused locally."""

    def test_legacy_previous_release_fails_closed_without_github(self) -> None:
        from boardwright import legacy_baseline, output_manifest

        reused = PREVIOUS_LEDGER + """  - version: 1.0.0
    date: '2026-11-20'
    source_commit: 2222222222222222222222222222222222222222
    previous_version: 0.9.0
    variant: RELEASED
    identities: {pcb_revision: A, schematic_revision: B, assembly_revision: A, fabrication_revision: A}
    change_classes: [documentation]
    justification: Notes.
    fabrication_baseline: {id: A/1, status: reused}
    assembly_baseline: {id: A, status: reused}
    fingerprint: {schema: 2, source: tag, pcb_geometry: g, pcb_netnames: n, fabrication_spec: f, assembly: a, schematic: s}
"""
        _git(self.repo, "tag", "-a", "0.9.0", "-m", "legacy release without a committed manifest")
        _write(self.repo / ".boardwright" / "releases.yaml", reused)
        err = io.StringIO()
        with mock.patch.object(legacy_baseline, "fetch_published_package",
                               side_effect=AssertionError("GitHub must not be contacted")), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = output_manifest.main(["check-baseline", "--root", str(self.repo), "--local"])
        self.assertEqual(1, code)
        self.assertIn("no committed output manifest at its local tag", err.getvalue())


class CandidateTests(Case):
    def test_candidate_is_built_from_the_committed_head_and_frozen(self) -> None:
        before = snapshot(self.repo)
        status = _git(self.repo, "status", "--porcelain")
        record = self.candidate()
        head = _git(self.repo, "rev-parse", "HEAD").strip()
        self.assertEqual((head, "local", "1.0.0"), (record["source_sha"], record["authority"], record["version"]))
        path, loaded, reviewed = local_release.load_candidate(self.store, "1.0.0")
        self.assertEqual(hashlib.sha256((path / ".boardwright" / "review_candidate.json").read_bytes()).hexdigest(),
                         record["candidate_id"])
        self.assertEqual((DATE, head), (reviewed["release_date"], reviewed["source_sha"]))
        self.assertEqual(self.request().inputs(), reviewed["inputs"])
        self.assertTrue((path / "Manufacturing" / "Fabrication" / "board-fabrication.pdf").is_file())
        self.assertEqual([], self.store.verify_entry(path))
        self.assertEqual(before, snapshot(self.repo))
        self.assertEqual(status, _git(self.repo, "status", "--porcelain"))
        self.assertEqual({}, local_release.local_tags(self.repo))  # nothing tagged
        self.assertEqual(1, len(_git(self.repo, "rev-list", "--all").split()))  # nothing committed
        self.assertTrue(all("--local" in c for c in self.steps.host_commands))  # check-baseline fails closed
        self.assertEqual(["candidate"], [e["event"] for e in self.store.events()])

    def test_identical_rebuild_is_idempotent(self) -> None:
        first = self.candidate()
        again = self.candidate()
        self.assertEqual(first["candidate_id"], again["candidate_id"])
        self.assertEqual(1, len(self.store.events()))

    def test_failed_step_freezes_nothing(self) -> None:
        self.steps.fail = "boardwright.output_manifest"
        with self.assertRaises(LocalReleaseError):
            self.candidate()
        self.assertEqual([], self.store.entries("candidates"))
        self.assertEqual([], self.store.events())

    def test_head_moving_during_the_build_freezes_nothing(self) -> None:
        def commit_meanwhile():
            _write(self.repo / "board.kicad_pcb", "(kicad_pcb moved)\n")
            _git(self.repo, "commit", "-q", "-am", "moved")

        self.steps.during_build = commit_meanwhile
        with self.assertRaises(LocalReleaseError) as caught:
            self.candidate()
        self.assertIn("changed while the candidate was built", str(caught.exception))
        self.assertEqual([], self.store.entries("candidates"))

    def test_plan_refusal_builds_nothing(self) -> None:
        _write(self.repo / "notes.txt", "dirty\n")
        with self.assertRaises(LocalReleaseError):
            self.candidate()
        self.assertEqual([], self.steps.commands)
        self.assertFalse(self.store.exists())

    def test_local_tags_feed_the_plan_tag_check(self) -> None:
        _git(self.repo, "tag", "0.0.1")
        self.candidate()
        stage = local_release.stage_dir(self.repo, "1.0.0")
        refs = json.loads((stage / ".git" / "boardwright-remote-refs.json").read_text(encoding="utf-8"))
        self.assertEqual(("local", ["0.0.1"]), (refs["source"], list(refs["tags"])))
        self.assertEqual(_git(stage, "remote", "get-url", "origin").strip(), refs["url"])


class ReviewAndApprovalTests(Case):
    def test_review_record_must_be_complete_and_bound(self) -> None:
        record = self.candidate()
        _path, loaded, reviewed = local_release.load_candidate(self.store, "1.0.0")
        draft = local_release.write_review_draft(self.store, "1.0.0").read_text(encoding="utf-8")
        self.assertIn(record["candidate_id"], draft)
        self.assertIn("not enforced by any server", draft)
        self.assertTrue(local_release.check_review(draft, loaded, reviewed))  # untouched draft refused
        good = draft.replace("- [ ]", "- [x]").replace("APPROVED: no", "APPROVED: yes")
        self.assertEqual([], local_release.check_review(good, loaded, reviewed))
        cases = {
            "unticked": good.replace("- [x]", "- [ ]", 1),
            "not last": good + "\nmore text\n",
            "wrong id": good.replace(record["candidate_id"], "f" * 64),
            "wrong source": good.replace(reviewed["source_sha"], "0" * 40),
            "wrong manifest": good.replace(reviewed["manifest_sha256"], "e" * 64),
            "wrong date": good.replace(f"| Release date | {DATE} |", "| Release date | 2026-11-21 |"),
            "lowercase": good.replace("APPROVED: yes", "approved: yes"),
        }
        for name, text in cases.items():
            self.assertTrue(local_release.check_review(text, loaded, reviewed), name)

    def test_approval_is_local_unauthenticated_and_bound(self) -> None:
        record = self.candidate()
        approval = self.approve(record)
        self.assertEqual(("local", False, False), (approval["authority"], approval["server_enforced"],
                                                    approval["authenticated"]))
        self.assertEqual("Rev Iewer <reviewer@example.invalid>", approval["approver"])
        self.assertEqual((record["candidate_id"], record["source_sha"], DATE),
                         (approval["candidate_id"], approval["source_sha"], approval["release_date"]))
        self.assertFalse(approval["signing_configured"])
        _p, _r, _c, verified = local_release.verify_approval(self.store, "1.0.0")
        self.assertEqual(approval, verified)
        self.assertEqual(["candidate", "approval"], [e["event"] for e in self.store.events()])
        self.assertTrue(any("not server-enforced" in line and "unsigned" in line for line in self.out))
        self.assertEqual({}, local_release.local_tags(self.repo))
        self.assertEqual(1, len(_git(self.repo, "rev-list", "--all").split()))

    def test_repeat_approval_is_idempotent_and_a_different_one_is_refused(self) -> None:
        record = self.candidate()
        path = self.review(record)
        self.approve(record, path)
        self.approve(record, path)
        self.assertEqual(2, len(self.store.events()))
        other = path.read_text(encoding="utf-8").replace("Notes / deviations:", "Notes / deviations: changed")
        path.write_text(other, encoding="utf-8")
        with self.assertRaises(LocalReleaseError):
            self.approve(record, path)

    def test_signing_key_is_reported_not_treated_as_authentication(self) -> None:
        _git(self.repo, "config", "user.signingkey", "ABCDEF")
        approval = self.approve(self.candidate())
        self.assertTrue(approval["signing_configured"])
        self.assertFalse(approval["authenticated"])

    def test_incomplete_review_is_refused(self) -> None:
        record = self.candidate()
        with self.assertRaises(LocalReleaseError):
            self.approve(record, self.review(record, approved=False))
        self.assertEqual([], self.store.entries("approvals"))

    def test_tampered_candidate_or_manifest_is_refused(self) -> None:
        record = self.candidate()
        review = self.review(record)
        path = self.store.entry_path("candidates", "1.0.0", record["candidate_id"])
        manifest = path / ".boardwright" / "accepted_manifest.json"
        original = manifest.read_bytes()
        os.chmod(manifest, stat.S_IWRITE | stat.S_IREAD)
        manifest.write_bytes(original.replace(b"drawing", b"other"))
        with self.assertRaises(LocalReleaseError) as caught:
            self.approve(record, review)
        self.assertIn("modified", str(caught.exception))
        # Re-checksummed by hand: the manifest no longer matches the reviewed digest.
        sums = path / "SHA256SUMS"
        os.chmod(sums, stat.S_IWRITE | stat.S_IREAD)
        text = sums.read_text(encoding="utf-8").replace(hashlib.sha256(original).hexdigest(),
                                                         hashlib.sha256(manifest.read_bytes()).hexdigest())
        sums.write_text(text, encoding="utf-8", newline="\n")
        with self.assertRaises(LocalReleaseError) as caught:
            self.approve(record, review)
        self.assertIn("accepted manifest", str(caught.exception))

    def test_changed_review_candidate_is_refused(self) -> None:
        record = self.candidate()
        review = self.review(record)
        path = self.store.entry_path("candidates", "1.0.0", record["candidate_id"])
        target = path / ".boardwright" / "review_candidate.json"
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
        target.write_text(target.read_text(encoding="utf-8").replace(DATE, "2026-11-21"), encoding="utf-8")
        with self.assertRaises(LocalReleaseError):
            self.approve(record, review)

    def test_existing_tag_and_second_candidate_are_refused(self) -> None:
        first = self.candidate()
        self.approve(first)
        second = local_release.build_candidate(
            self.config, ReleaseRequest(version="1.0.0", drawing_revision="A", revision_description="Second try",
                                        release_date=DATE), self.store,
            runner=self.steps, host_runner=self.steps.host, echo=self.out.append)
        self.assertNotEqual(first["candidate_id"], second["candidate_id"])
        with self.assertRaises(LocalReleaseError) as caught:
            self.approve(second)
        self.assertIn("already has an approval", str(caught.exception))
        third = self.candidate("1.0.2")
        _git(self.repo, "tag", "1.0.2")
        with self.assertRaises(LocalReleaseError) as caught:
            self.approve(third)
        self.assertIn("Tag 1.0.2 already exists", str(caught.exception))

    def test_invalid_approval_is_detected(self) -> None:
        record = self.candidate()
        self.approve(record)
        entry = self.store.entry_path("approvals", "1.0.0", record["candidate_id"])
        target = entry / "approval.json"
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
        data = json.loads(target.read_text(encoding="utf-8"))
        for key, value in (("server_enforced", True), ("authority", "github"), ("source_sha", "0" * 40)):
            changed = {**data, key: value}
            target.write_text(json.dumps(changed, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
            with self.assertRaises(LocalReleaseError, msg=key):
                local_release.verify_approval(self.store, "1.0.0")
            # Even with the checksum rewritten, the binding check refuses it.
            sums = entry / "SHA256SUMS"
            os.chmod(sums, stat.S_IWRITE | stat.S_IREAD)
            lines = [line for line in sums.read_text(encoding="utf-8").splitlines() if not line.endswith("approval.json")]
            lines.append(f"{hashlib.sha256(target.read_bytes()).hexdigest()}  approval.json")
            sums.write_text("\n".join(sorted(lines, key=lambda l: l.split('  ')[1])) + "\n", encoding="utf-8", newline="\n")
            with self.assertRaises(LocalReleaseError, msg=key):
                local_release.verify_approval(self.store, "1.0.0")

    def test_edited_index_blocks_approval(self) -> None:
        record = self.candidate()
        review = self.review(record)
        index = self.store.root / release_store.INDEX
        index.write_bytes(index.read_bytes().replace(record["source_sha"].encode(), b"0" * 40))
        with self.assertRaises(LocalReleaseError) as caught:
            self.approve(record, review)
        self.assertIn("index is inconsistent", str(caught.exception))
        self.assertIn("index.head", str(caught.exception))


class BackendTests(Case):
    def select(self, kind, usable, override=""):
        return local_release.select_local_backend(
            self.config, override, inspect_remote=lambda root: remote(kind, usable),
            inspect_docker=lambda root=None: DockerInfo(True, "Docker"))

    def test_no_remote_releases_locally_by_default(self) -> None:
        self.assertEqual("local", self.select("none", False).backend.value)

    def test_github_remote_needs_an_explicit_local_override(self) -> None:
        with self.assertRaises(LocalReleaseError) as caught:
            self.select("github", True)
        self.assertIn("--backend local", str(caught.exception))
        self.assertEqual("local", self.select("github", True, "local").backend.value)

    def test_unusable_or_unsupported_remotes_never_fall_back(self) -> None:
        for kind, usable in (("github", False), ("unsupported", False)):
            with self.assertRaises(LocalReleaseError):
                self.select(kind, usable)

    def test_docker_unavailable_is_refused(self) -> None:
        with self.assertRaises(LocalReleaseError):
            local_release.select_local_backend(self.config, "local", inspect_remote=lambda root: remote(),
                                               inspect_docker=lambda root=None: DockerInfo(False, "no Docker"))


class GitHubIsolationTests(Case):
    """No local candidate or approval can satisfy a GitHub ``approved_review_run``."""

    def workflows(self) -> dict[str, str]:
        return {p.name: p.read_text(encoding="utf-8") for p in (ROOT / ".github" / "workflows").glob("*.y*ml")}

    def test_workflows_never_read_local_release_evidence(self) -> None:
        for name, text in self.workflows().items():
            for marker in ("approval.json", ".boardwright/releases/", "release_store",
                           "local_release", "Approval-Authority", "BOARDWRIGHT_RELEASE_STORE"):
                self.assertNotIn(marker, text, f"{name} mentions {marker!r}")
            # The local candidate record, not review_candidate.json (exact name).
            self.assertNotRegex(text, r"(?<![\w-])candidate\.json", name)

    def test_approved_run_candidate_comes_only_from_the_reviewed_run(self) -> None:
        import yaml

        prepare = yaml.safe_load(self.workflows()["prepare-release.yaml"])
        steps = [s for job in prepare["jobs"].values() for s in job["steps"]]
        download = next(s for s in steps if s.get("name") == "Download reviewed candidate")
        self.assertEqual("${{ inputs.approved_review_run }}", download["with"]["run-id"])
        self.assertIn("runner.temp", download["with"]["path"])
        gate = next(s for s in steps if s.get("name") == "Check approved review run")["run"]
        self.assertIn('.github/workflows/prepare-release.yaml', gate)
        self.assertIn('"$conclusion" == "success"', gate)

    @unittest.skipUnless(shutil.which("bash"), "bash is needed to run the workflow gate")
    def test_gate_refuses_a_local_candidate_id(self) -> None:
        import yaml

        record = self.candidate()
        approval = self.approve(record)
        prepare = yaml.safe_load(self.workflows()["prepare-release.yaml"])
        gate = next(s for j in prepare["jobs"].values() for s in j["steps"]
                    if s.get("name") == "Check approved review run")["run"]
        fake = self.base / "bin"
        fake.mkdir()
        # The GitHub API has no workflow run with a local id: it answers 404.
        _write(fake / "gh", "#!/bin/sh\necho 'gh: Not Found (HTTP 404)' >&2\nexit 1\n")
        os.chmod(fake / "gh", 0o755)
        for value in (record["candidate_id"], approval["candidate_id"], str(self.store.root)):
            env = {**os.environ, "PATH": f"{fake}{os.pathsep}{os.environ['PATH']}", "GITHUB_REPOSITORY": "o/r",
                   "IN_APPROVED_REVIEW_RUN": value, "BOARDWRIGHT_SOURCE_SHA": record["source_sha"]}
            done = subprocess.run(["bash", "-c", gate], env=env, capture_output=True, text=True)
            self.assertNotEqual(0, done.returncode, value)


class CommandLineTests(Case):
    def run_cli(self, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        cwd = os.getcwd()
        os.chdir(self.repo)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), \
                    mock.patch("boardwright.config.load_config", return_value=self.config):
                code = local_release.main(list(args))
        finally:
            os.chdir(cwd)
        return code, out.getvalue()

    def test_status_is_read_only_before_the_first_candidate(self) -> None:
        code, output = self.run_cli("status")
        self.assertEqual(0, code)
        self.assertIn("No local releases yet", output)
        self.assertFalse(self.store.root.exists())

    def test_status_reports_entries_and_modifications(self) -> None:
        record = self.candidate()
        code, output = self.run_cli("status")
        self.assertIn("Index: intact", output)
        self.assertIn(f"candidate 1.0.0 {record['candidate_id'][:12]}: intact", output)
        target = self.store.entry_path("candidates", "1.0.0", record["candidate_id"]) / "candidate.json"
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
        target.write_text("{}", encoding="utf-8")
        self.assertIn("MODIFIED", self.run_cli("status")[1])

    def test_github_project_needs_backend_local(self) -> None:
        with mock.patch("boardwright.backends.inspect_remote", return_value=remote("github", True)), \
                mock.patch("boardwright.backends.inspect_docker", return_value=DockerInfo(True, "Docker")):
            code, output = self.run_cli("plan", "1.0.0")
            self.assertEqual(2, code)
            self.assertIn("--backend local", output)
            code, output = self.run_cli("plan", "1.0.0", "--backend", "local")
        self.assertEqual(0, code, output)
        self.assertIn("not server-enforced", output)

    def test_review_check_reports_problems(self) -> None:
        record = self.candidate()
        draft = local_release.write_review_draft(self.store, "1.0.0")
        code, output = self.run_cli("review", "1.0.0", "--check", str(draft))
        self.assertEqual(1, code)
        self.assertIn("not ticked", output)
        code, output = self.run_cli("review", "1.0.0")
        self.assertIn(str(draft), output)


if __name__ == "__main__":
    unittest.main()
