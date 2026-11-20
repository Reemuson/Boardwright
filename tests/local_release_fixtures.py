# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Shared fixtures for the local release finalisation, publication and recovery tests.

Disposable repositories and stores. The container steps are replaced by a
deterministic fake that writes what the real steps write (ledger entry,
``release.env``, outputs) and uses the real ``output_manifest`` and
``review_gate`` to write the manifest and review candidate, so the real
review gate, ``ci_cleanup``, ``release_commit``, ``release_package`` and Git
operations run unchanged. The pinned-Docker runs are recorded in
docs/AUDIT-2026-11.md.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from boardwright import local_finalize, local_release
from boardwright.config import BoardwrightConfig
from boardwright.local_release import ReleaseRequest
from boardwright.release_store import ReleaseStore

try:
    from tests.test_local_release import DATE, _git, _rmtree, _write
except ImportError:  # unittest discover -s tests
    from test_local_release import DATE, _git, _rmtree, _write

FINGERPRINT = "{schema: 2, source: tag, pcb_geometry: g, pcb_netnames: n, fabrication_spec: f, assembly: a, schematic: s}"


def gerber(text: str) -> str:
    """Geometry derived from the board text (G04 comments are normalised away, coordinates are not)."""

    import hashlib

    x = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:6], 16)
    return f"G04 fabrication*\nX{x}Y0D02*\nX{x + 10}Y0D01*\nM02*\n"


class Interrupted(Exception):
    pass


class FakeEngine:
    """Deterministic stand-in for the pinned container (and the host baseline check)."""

    def __init__(self) -> None:
        self.builds = 0

    def __call__(self, kicad, workdir, command, environment=None):
        workdir = Path(workdir)
        env = environment or {}
        if "--prepare" in command:
            # As the real release plan: preparation runs on the release branch only.
            branch = subprocess.run(["git", "symbolic-ref", "--quiet", "--short", "HEAD"], cwd=workdir,
                                    capture_output=True, text=True).stdout.strip()
            if branch != "main":
                return 1
            self.prepare(workdir, env, command)
        elif command[2:4] == ["boardwright", "build"]:
            self.builds += 1
            pcb = (workdir / "board.kicad_pcb").read_text(encoding="utf-8")
            _write(workdir / "Manufacturing" / "Fabrication" / "Gerbers" / "board-F_Cu.gbr", gerber(pcb))
            _write(workdir / "Schematic" / "board-schematic.txt", "schematic\n")
        elif "boardwright.output_manifest" in command:
            from boardwright.output_manifest import build_manifest

            _write(workdir / ".boardwright" / "accepted_manifest.json",
                   json.dumps(build_manifest(workdir), indent=2, sort_keys=True) + "\n")
        elif "boardwright.review_gate" in command:
            from boardwright.review_gate import build_candidate

            _write(workdir / ".boardwright" / "review_candidate.json",
                   json.dumps(build_candidate(workdir, env), indent=2, sort_keys=True) + "\n")
        return 0

    @staticmethod
    def prepare(workdir: Path, env: dict, command: list[str]) -> None:
        version = env["IN_VERSION"]
        date = command[command.index("--release-date") + 1]
        ledger = workdir / ".boardwright" / "releases.yaml"
        text = ledger.read_text(encoding="utf-8") if ledger.is_file() else "schema: 1\nreleases:\n"
        previous = ""
        for line in text.splitlines():
            if line.startswith("  - version: "):
                previous = line.split(": ", 1)[1]
        status = "reused" if previous and env["IN_CHANGE_CLASSES"] == "documentation" else ("reissued" if previous else "initial")
        text += (f"  - version: {version}\n    date: '{date}'\n    source_commit: {env['BOARDWRIGHT_SOURCE_SHA']}\n"
                 f"    previous_version: '{previous}'\n    variant: {env['IN_VARIANT']}\n"
                 "    identities: {pcb_revision: A, schematic_revision: A, assembly_revision: A, fabrication_revision: A}\n"
                 f"    change_classes: [{env['IN_CHANGE_CLASSES']}]\n    justification: {env['IN_JUSTIFICATION'] or 'Release.'}\n"
                 f"    fabrication_baseline: {{id: A/1, status: {status}}}\n    assembly_baseline: {{id: A, status: initial}}\n"
                 f"    fingerprint: {FINGERPRINT}\n")
        _write(ledger, text)
        _write(workdir / ".boardwright" / "release.env",
               f"RELEASE_VERSION={version}\nRELEASE_VARIANT={env['IN_VARIANT']}\nRELEASE_KIND={env['IN_RELEASE_KIND']}\n"
               f"RELEASE_INCLUDE_SOURCE_ARCHIVE=false\nRELEASE_DATE_NUM={date}\n")
        _write(workdir / ".boardwright" / "revision_history_variables.env", f"REV={version}\n")
        changelog = (workdir / "CHANGELOG.md").read_text(encoding="utf-8")
        _write(workdir / "CHANGELOG.md", changelog.replace("## [Unreleased]\n", f"## [Unreleased]\n\n## [{version}] - {date}\n", 1))

    @staticmethod
    def host(command):
        return 0


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="bw local final "))
        self.repo = self.base / "board é"
        self.repo.mkdir()
        for args in (("init", "-q", "-b", "main"), ("config", "user.name", "Rev Iewer"),
                     ("config", "user.email", "reviewer@example.invalid"), ("config", "core.autocrlf", "false")):
            _git(self.repo, *args)
        _write(self.repo / ".gitignore", ".boardwright/local.yaml\nManufacturing/\nSchematic/\n")
        _write(self.repo / "CHANGELOG.md", "# Changelog\n\n## [Unreleased]\n\n- Board.\n")
        _write(self.repo / "README.md", "# board\n")
        _write(self.repo / "board.kicad_pcb", "(kicad_pcb rev A)\n")
        _write(self.repo / ".boardwright" / "project.yaml", "project: {id: BOARD/2, name: board}\n")
        _write(self.repo / ".boardwright" / "document_revisions.yaml", "documents: {}\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "init")
        self.env = mock.patch.dict(os.environ, {"BOARDWRIGHT_RELEASE_STORE": str(self.base / "store base"),
                                                "BOARDWRIGHT_STAGE_ROOT": str(self.base / "stages")})
        self.env.start()
        self.config = BoardwrightConfig(root=self.repo, project={"project": {"id": "BOARD/2", "name": "board"}},
                                        branches={"branches": {"release": "main"}}, legal={},
                                        revision_history={"revision_history": {}})
        self.store = ReleaseStore.for_project(self.repo, self.config.project)
        self.engine = FakeEngine()
        self.out: list[str] = []

    def tearDown(self) -> None:
        self.env.stop()
        _rmtree(self.base)

    # -- lifecycle helpers ---------------------------------------------------
    def approved(self, version="1.0.0", classes="") -> dict:
        record = local_release.build_candidate(
            self.config, ReleaseRequest(version=version, drawing_revision="A", revision_description="Release",
                                        change_classes=classes, release_date=DATE), self.store,
            runner=self.engine, host_runner=self.engine.host, echo=self.out.append)
        draft = local_release.write_review_draft(self.store, version, record["candidate_id"])
        review = self.base / f"REVIEW-{version}.md"
        review.write_text(draft.read_text(encoding="utf-8").replace("- [ ]", "- [x]").replace("APPROVED: no", "APPROVED: yes"),
                          encoding="utf-8", newline="\n")
        local_release.approve(self.config, self.store, version, review, record["candidate_id"], echo=self.out.append)
        return record

    def finalize(self, version="1.0.0", **kw):
        return local_finalize.finalize(self.config, self.store, version, runner=self.engine,
                                       host_runner=self.engine.host, echo=self.out.append, **kw)

    def publish(self, version="1.0.0"):
        from boardwright import local_publish

        return local_publish.publish(self.config, self.store, version, runner=self.engine, echo=self.out.append)

    def recover(self, version=""):
        from boardwright import local_recovery

        return local_recovery.recover(self.config, self.store, version, echo=self.out.append)

    def state(self, version="1.0.0"):
        from boardwright import local_recovery

        return local_recovery.version_state(self.repo, self.store, version)

    def head(self, ref="HEAD") -> str:
        return _git(self.repo, "rev-parse", ref).strip()

    def events(self, version="1.0.0") -> list[str]:
        return [e["event"] for e in self.store.events() if e.get("version") == version]

    def assert_finalised(self, record: dict, version="1.0.0") -> str:
        commit = self.head("main")
        self.assertEqual([record["source_sha"]], _git(self.repo, "rev-list", "--parents", "-n", "1", commit).split()[1:])
        self.assertEqual("tag", _git(self.repo, "cat-file", "-t", version).strip())
        self.assertEqual(commit, self.head(f"{version}^{{commit}}"))
        self.assertEqual(commit, self.head("HEAD"))
        self.assertEqual("", _git(self.repo, "status", "--porcelain"))  # the working tree follows the branch
        body = _git(self.repo, "log", "-1", "--format=%B", commit)
        for line in (f"Source-Commit: {record['source_sha']}", f"Reviewed-Candidate: {record['candidate_id']}",
                     "Approval-Authority: local"):
            self.assertIn(line, body.splitlines())
        self.assertNotIn("Reviewed-Run", body)
        approval = json.loads(_git(self.repo, "show", f"{commit}:.boardwright/approval.json"))
        self.assertEqual(("local", False, False), (approval["authority"], approval["server_enforced"], approval["authenticated"]))
        for event in ("prepared", "imported", "advanced", "tagged"):
            self.assertEqual(1, self.events(version).count(event), (event, self.events(version)))
        return commit
