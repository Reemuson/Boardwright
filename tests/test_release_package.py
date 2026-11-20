# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Release packaging and release commit: the extracted Python against the
frozen workflow shell (tests/fixtures/legacy_release_steps, LOCAL_FIRST.md
section 6)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from boardwright import release_commit, release_package

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "tests" / "fixtures" / "legacy_release_steps"
TOOLS = all(shutil.which(t) for t in ("bash", "zip", "sha256sum", "git"))
TAG = "1.2.0"

LEDGER = """schema: 1
releases:
  - version: 1.1.0
    date: '2026-10-01'
    source_commit: 1111111111111111111111111111111111111111
    previous_version: ''
    variant: RELEASED
    identities: {pcb_revision: A, schematic_revision: A, assembly_revision: A, fabrication_revision: A}
    change_classes: []
    justification: Initial release.
    fabrication_baseline: {id: A/1, status: initial}
    assembly_baseline: {id: A, status: initial}
    fingerprint: {schema: 2, source: tag, pcb_geometry: g, pcb_netnames: n, fabrication_spec: f, assembly: a, schematic: s}
  - version: 1.2.0
    date: '2026-11-20'
    source_commit: 2222222222222222222222222222222222222222
    previous_version: 1.1.0
    variant: RELEASED
    identities: {pcb_revision: A, schematic_revision: B, assembly_revision: A, fabrication_revision: A}
    change_classes: [documentation, tooling]
    justification: 'Schematic rev B: notes. Fabrication A/1 reused.'
    fabrication_baseline: {id: A/1, status: reused}
    assembly_baseline: {id: A, status: reused}
    fingerprint: {schema: 2, source: tag, pcb_geometry: g, pcb_netnames: n, fabrication_spec: f, assembly: a, schematic: s}
"""

CHANGELOG = """# Changelog

## [Unreleased]

## [1.2.0] - 2026-11-20

### Changed

- Notes updated.

## [1.1.0] - 2026-10-01

- First.
"""


def _legacy(name: str) -> str:
    text = (LEGACY / name).read_text(encoding="utf-8")
    # Workflow expressions become the environment the runner provides.
    text = text.replace("${{ github.ref_name }}", "${GITHUB_REF_NAME}")
    if os.name == "nt":
        # Platform shims so the reference behaves as on the Linux runner;
        # each is meaning-preserving. Git for Windows' sha256sum defaults to
        # binary mode (" *name"); its awk degrades the dynamic regex "\["
        # to a bracket expression, so the literal-bracket form is used.
        text = text.replace("xargs -0 sha256sum", "xargs -0 sha256sum -t")
        # Windows Python prints CRLF; the runner writes LF.
        text = text.replace(' > "$RELEASE_DIR/RELEASE_IDENTITY.json"', " | tr -d '\\r' > \"$RELEASE_DIR/RELEASE_IDENTITY.json\"")
        shimmed = text.replace(r'"^## \\[" tag "\\] - "', '"^## [[]" tag "[]] - "')
        assert shimmed != text or "awk" not in text, "awk shim did not apply"
        text = shimmed
    return text


def _bash(script: str, cwd: Path, **env: str) -> subprocess.CompletedProcess:
    full = {**os.environ, "LC_ALL": "C", "PYTHONPATH": str(ROOT / "src"), **env}
    return subprocess.run(["bash", "-c", script], cwd=cwd, env=full, capture_output=True, text=True)


def _write(path: Path, text: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8", newline="\n")


def make_workspace(root: Path, *, ledger: bool = True, renders: tuple[str, ...] = ("FIC-100A-angled_top.png", "FIC-100A-angled_bottom.png"),
                   changelog: str = CHANGELOG, source_zip: bool = False) -> None:
    _write(root / "Manufacturing" / "Gerbers" / "board-F_Cu.gbr", "G04 top*\n")
    _write(root / "Manufacturing" / "Gerbers" / "board-B_Cu.gbr", "G04 bottom*\n")
    _write(root / "Manufacturing" / "Assembly" / "BOM file.csv", "a,b\n")  # space in name
    _write(root / "Manufacturing" / "_under.txt", "u\n")
    _write(root / "Manufacturing" / "Zed.txt", "z\n")
    _write(root / "Manufacturing" / "alpha.txt", "a\n")
    (root / "Testing" / "empty").mkdir(parents=True)
    _write(root / "Schematic" / "board-schematic.pdf", b"%PDF-1.7\n\x00\xff")
    _write(root / "Reports" / "drc.txt", "ok\n")
    _write(root / "HTML" / "ibom.html", "<html></html>\n")
    _write(root / "KiRI" / "index.html", "kiri\n")
    _write(root / "assets" / "logo.svg", "<svg/>\n")
    for name in renders:
        _write(root / "assets" / "renders" / name, b"\x89PNG" + name.encode())
    _write(root / "README.md", "# Board\n")
    _write(root / "LICENSE", "license\n")
    _write(root / "NOTICE.md", "notice\n")
    _write(root / "unrelated.txt", "not packaged\n")
    _write(root / ".boardwright" / "accepted_manifest.json", '{"schema": 1}\n')
    if ledger:
        _write(root / ".boardwright" / "releases.yaml", LEDGER)
    if changelog:
        _write(root / "CHANGELOG.md", changelog)
    if source_zip:
        _write(root / f"boardwright-{TAG}-source.zip", b"PK\x05\x06" + b"\x00" * 18)


def _zip_names(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as archive:
        return set(archive.namelist())


@unittest.skipUnless(TOOLS, "needs bash, zip, sha256sum and git")
class PackageEquivalenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="bw-pkg-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)

    def _pair(self, **workspace) -> tuple[Path, Path]:
        legacy, python = self.base / "legacy", self.base / "python"
        for root in (legacy, python):
            make_workspace(root, **workspace)
        return legacy, python

    def _compare_collect(self, **workspace) -> None:
        legacy, python = self._pair(**workspace)
        done = _bash(_legacy("collect_assets.sh"), legacy, GITHUB_REF_NAME=TAG, RELEASE_DIR="boardwright-release")
        self.assertEqual(0, done.returncode, done.stderr)
        release_package.build_package(python, TAG)
        rel = "boardwright-release"
        self.assertEqual((legacy / rel / "MANIFEST.txt").read_bytes(), (python / rel / "MANIFEST.txt").read_bytes())
        for name in ("RELEASE_IDENTITY.json", "ACCEPTED_OUTPUT_MANIFEST.json"):
            self.assertEqual((legacy / rel / name).exists(), (python / rel / name).exists(), name)
            if (legacy / rel / name).exists():
                self.assertEqual((legacy / rel / name).read_bytes(), (python / rel / name).read_bytes(), name)
        zip_name = release_package.release_zip_name(TAG)
        self.assertEqual(_zip_names(legacy / zip_name), _zip_names(python / zip_name))
        # Every packaged file has the same bytes inside both zips.
        with zipfile.ZipFile(legacy / zip_name) as a, zipfile.ZipFile(python / zip_name) as b:
            for name in a.namelist():
                if not name.endswith("/"):
                    self.assertEqual(a.read(name), b.read(name), name)

    def test_collect_matches_the_workflow(self) -> None:
        self._compare_collect()

    def test_collect_with_source_archive_and_without_ledger(self) -> None:
        self._compare_collect(source_zip=True, ledger=False)

    def test_manifest_lists_every_file_and_verifies(self) -> None:
        _, python = self._pair()
        package = release_package.build_package(python, TAG)
        done = subprocess.run(["sha256sum", "-c", "--quiet", "MANIFEST.txt"], cwd=package.release_dir,
                              capture_output=True, text=True)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertNotIn("unrelated.txt", package.manifest.read_text(encoding="utf-8"))
        identity = json.loads((package.release_dir / "RELEASE_IDENTITY.json").read_text(encoding="utf-8"))
        self.assertEqual("1.2.0", identity["version"])

    def _compare_notes(self, **workspace) -> None:
        legacy, python = self._pair(**workspace)
        done = _bash(_legacy("release_notes.sh"), legacy, GITHUB_REF_NAME=TAG,
                     GITHUB_REPOSITORY="o/board", RELEASE_VARIANT="RELEASED")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(0, release_package.main(["--root", str(python), "notes", "--tag", TAG,
                                                  "--variant", "RELEASED", "--repository", "o/board"]))
        expected = (legacy / "RELEASE_NOTES.md").read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(expected.decode(), (python / "RELEASE_NOTES.md").read_text(encoding="utf-8"))
        self.assertEqual(sorted(p.name for p in legacy.glob("*.png")), sorted(p.name for p in python.glob("*.png")))

    def test_notes_match_the_workflow(self) -> None:
        self._compare_notes()

    def test_notes_legacy_release_without_ledger_entry(self) -> None:
        self._compare_notes(ledger=False, source_zip=True)

    def test_notes_without_renders_and_with_one(self) -> None:
        self._compare_notes(renders=())
        shutil.rmtree(self.base)
        self.base.mkdir()
        self._compare_notes(renders=("board_bottom.PNG", "zz_top.png", "a_top.png"))

    def test_notes_changelog_section_at_end_of_file_and_bare_heading(self) -> None:
        self._compare_notes(changelog="# Changelog\n\n## [1.2.0]\n\n- Only entry.\n\n")
        shutil.rmtree(self.base)
        self.base.mkdir()
        self._compare_notes(changelog="## [1.2.0] - 2026-11-20\n- no trailing newline")


def _workflow_step(workflow: str, name: str) -> str:
    import yaml

    data = yaml.safe_load((ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8"))
    return next(s["run"] for job in data["jobs"].values() for s in job["steps"] if s.get("name") == name)


@unittest.skipUnless(TOOLS, "needs bash, zip, sha256sum and git")
class WorkflowWiringTests(unittest.TestCase):
    """The installed release.yaml steps produce what the frozen shell produced."""

    def test_release_yaml_steps_match_the_legacy_steps(self) -> None:
        base = Path(tempfile.mkdtemp(prefix="bw-wiring-"))
        try:
            legacy, current = base / "legacy", base / "current"
            env = {"GITHUB_REF_NAME": TAG, "GITHUB_REPOSITORY": "o/board", "GITHUB_SERVER_URL": "https://github.com",
                   "RELEASE_VARIANT": "RELEASED", "RELEASE_DIR": "boardwright-release"}
            for root in (legacy, current):
                make_workspace(root, source_zip=True)
            for script in (_legacy("release_notes.sh"), _legacy("collect_assets.sh")):
                done = _bash(script, legacy, **env)
                self.assertEqual(0, done.returncode, done.stderr)
            for step in ("Generate GitHub release markdown", "Collect release assets"):
                done = _bash(_workflow_step("release.yaml", step), current, **env)
                self.assertEqual(0, done.returncode, done.stderr)
            self.assertEqual((legacy / "RELEASE_NOTES.md").read_bytes().replace(b"\r\n", b"\n"),
                             (current / "RELEASE_NOTES.md").read_bytes())
            self.assertEqual((legacy / "boardwright-release" / "MANIFEST.txt").read_bytes(),
                             (current / "boardwright-release" / "MANIFEST.txt").read_bytes())
            zip_name = release_package.release_zip_name(TAG)
            self.assertEqual(_zip_names(legacy / zip_name), _zip_names(current / zip_name))
            self.assertEqual(sorted(p.name for p in legacy.glob("*.png")), sorted(p.name for p in current.glob("*.png")))
        finally:
            shutil.rmtree(base, ignore_errors=True)


class ReviewHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="bw-harden-"))
        make_workspace(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_release_dir_that_would_delete_project_content_is_refused(self) -> None:
        for bad in (self.root, self.root.parent, self.root / "Manufacturing", self.root / "assets" / "pkg",
                    self.root / ".git", self.root / ".boardwright"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                release_package.collect_assets(self.root, TAG, bad)
        self.assertTrue((self.root / "Manufacturing" / "Zed.txt").is_file())
        release_package.collect_assets(self.root, TAG, self.root / "boardwright-release")
        outside = Path(tempfile.mkdtemp(prefix="bw-store-"))
        try:
            release_package.collect_assets(self.root, TAG, outside / "pkg")  # e.g. a release store
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_zip_directory_entries_are_marked_as_directories(self) -> None:
        package = release_package.build_package(self.root, TAG)
        with zipfile.ZipFile(package.zip_path) as archive:
            dirs = [i for i in archive.infolist() if i.filename.endswith("/")]
            self.assertTrue(dirs)
            for info in dirs:
                self.assertTrue(info.is_dir())
                self.assertEqual(0x10, info.external_attr & 0x10, info.filename)
            self.assertIsNone(archive.testzip())
            names = archive.namelist()
            self.assertEqual(len(names), len(set(names)))
            self.assertEqual("boardwright-release/", names[0])

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_symlinked_render_is_not_chosen(self) -> None:
        renders = self.root / "assets" / "renders"
        (renders / "FIC-100A-angled_top.png").unlink()
        os.symlink(renders / "FIC-100A-angled_bottom.png", renders / "a_top.png")
        self.assertIsNone(release_package.board_images(self.root)[0])


class NotesUnitTests(unittest.TestCase):
    def test_local_notes_use_relative_images(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="bw-notes-"))
        try:
            make_workspace(root)
            text = release_package.release_notes(root, TAG, variant="RELEASED", repository="local/board", asset_base_url="")
            self.assertIn('src="FIC-100A-angled_top.png"', text)
            self.assertNotIn("https://", text)
            self.assertIn("| Fabrication baseline | A/1 (reused) |", text)
        finally:
            shutil.rmtree(root, ignore_errors=True)


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for args in (["init", "-q", "-b", "main"], ["config", "user.name", "boardwright[bot]"],
                 ["config", "user.email", "boardwright[bot]@users.noreply.github.com"],
                 ["config", "core.autocrlf", "false"], ["config", "commit.gpgsign", "false"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def make_release_repo(root: Path, *, renders: bool = True) -> None:
    """A project after preparation: generated trees committed, release files written."""

    _init_repo(root)
    _write(root / ".gitignore", ".boardwright/*.env\n.boardwright/releases.yaml\n.boardwright/accepted_manifest.json\n"
                                ".boardwright/review_candidate.json\nscratch/\n")
    _write(root / "board.kicad_pcb", "(kicad_pcb)\n")
    _write(root / "CHANGELOG.md", "# Changelog\n")
    _write(root / "README.md", "# Board\n")
    _write(root / ".boardwright" / "project.yaml", "name: board\n")
    _write(root / ".boardwright" / "document_revisions.yaml", "schema: 1\n")
    _write(root / "Manufacturing" / "old.gbr", "old\n")
    _write(root / "Schematic" / "old.pdf", "old\n")
    if renders:
        _write(root / "assets" / "renders" / "top.png", b"\x89PNG1")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "source"], cwd=root, check=True, capture_output=True)
    # Preparation output.
    _write(root / "CHANGELOG.md", CHANGELOG)
    _write(root / "README.md", "# Board\n\nRelease 1.2.0\n")
    _write(root / ".boardwright" / "project.yaml", "name: board\nversion: 1.2.0\n")
    _write(root / ".boardwright" / "release.env", "RELEASE_VARIANT=RELEASED\n")
    _write(root / ".boardwright" / "revision_history_variables.env", "REV=B\n")
    _write(root / ".boardwright" / "releases.yaml", LEDGER)
    _write(root / ".boardwright" / "accepted_manifest.json", "{}\n")
    _write(root / ".boardwright" / "review_candidate.json", "{}\n")
    _write(root / "scratch" / "ignored.txt", "x\n")
    _write(root / "untracked.txt", "not part of a release\n")
    if renders:
        _write(root / "assets" / "renders" / "top.png", b"\x89PNG2")
        _write(root / "assets" / "renders" / "bottom.png", b"\x89PNG3")


def _legacy_commit_script() -> str:
    lines = _legacy("commit_release.sh").splitlines()
    kept = [l for l in lines if not any(k in l for k in ("GH_TOKEN", "add-mask", "GIT_CONFIG_COUNT", "git push"))]
    return "\n".join(kept) + "\n"


def _head(root: Path) -> tuple[str, str]:
    tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=root, capture_output=True, text=True, check=True).stdout
    body = subprocess.run(["git", "log", "-1", "--format=%B"], cwd=root, capture_output=True, text=True, check=True).stdout
    return tree.strip(), body


@unittest.skipUnless(TOOLS, "needs bash, zip, sha256sum and git")
class CommitEquivalenceTests(unittest.TestCase):
    SOURCE = "2222222222222222222222222222222222222222"

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="bw-commit-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)

    def _compare(self, run_id: str, *, renders: bool = True) -> None:
        legacy, python = self.base / "legacy", self.base / "python"
        for root in (legacy, python):
            make_release_repo(root, renders=renders)
        done = _bash(_legacy_commit_script(), legacy, IN_VERSION=TAG, IN_VARIANT="RELEASED",
                     IN_APPROVED_REVIEW_RUN=run_id, BOARDWRIGHT_SOURCE_SHA=self.SOURCE,
                     GITHUB_SERVER_URL="https://github.com", GITHUB_REPOSITORY="o/board")
        self.assertEqual(0, done.returncode, done.stderr)
        url = f"https://github.com/o/board/actions/runs/{run_id}" if run_id else ""
        release_commit.commit_release(python, version=TAG, variant="RELEASED", source_sha=self.SOURCE, reviewed_run_url=url)
        self.assertEqual(_head(legacy), _head(python))

    def test_approved_release_commit_matches_the_workflow(self) -> None:
        self._compare("44648605373")

    def test_unreviewed_release_commit_matches_the_workflow(self) -> None:
        self._compare("")

    def _changed(self, root: Path) -> set[str]:
        out = subprocess.run(["git", "diff", "--name-only", "HEAD~1", "HEAD"], cwd=root,
                             capture_output=True, text=True, check=True).stdout
        return set(out.split())

    def test_legacy_shell_dropped_readme_without_renders(self) -> None:
        # Documents audit E1 against the frozen shell; the module now differs here.
        legacy, python = self.base / "legacy", self.base / "python"
        for root in (legacy, python):
            make_release_repo(root, renders=False)
        done = _bash(_legacy_commit_script(), legacy, IN_VERSION=TAG, IN_VARIANT="RELEASED",
                     IN_APPROVED_REVIEW_RUN="", BOARDWRIGHT_SOURCE_SHA=self.SOURCE,
                     GITHUB_SERVER_URL="https://github.com", GITHUB_REPOSITORY="o/board")
        self.assertEqual(0, done.returncode, done.stderr)
        release_commit.commit_release(python, version=TAG, variant="RELEASED", source_sha=self.SOURCE)
        self.assertNotIn("README.md", self._changed(legacy))
        self.assertEqual(self._changed(legacy) | {"README.md"}, self._changed(python))

    def test_readme_is_staged_without_renders(self) -> None:
        root = self.base / "no-renders"
        make_release_repo(root, renders=False)
        release_commit.commit_release(root, version=TAG, variant="RELEASED", source_sha=self.SOURCE)
        changed = self._changed(root)
        self.assertIn("README.md", changed)
        self.assertFalse(any(p.startswith("assets/renders/") for p in changed))

    def test_readme_and_renders_are_staged_together(self) -> None:
        root = self.base / "renders"
        make_release_repo(root)
        release_commit.commit_release(root, version=TAG, variant="RELEASED", source_sha=self.SOURCE)
        self.assertTrue({"README.md", "assets/renders/top.png", "assets/renders/bottom.png"} <= self._changed(root))

    def test_unchanged_or_missing_readme_is_not_an_error(self) -> None:
        root = self.base / "unchanged"
        make_release_repo(root, renders=False)
        subprocess.run(["git", "checkout", "--", "README.md"], cwd=root, check=True, capture_output=True)
        release_commit.commit_release(root, version=TAG, variant="RELEASED", source_sha=self.SOURCE)
        self.assertNotIn("README.md", self._changed(root))

        root = self.base / "missing"
        make_release_repo(root)
        (root / "README.md").unlink()
        subprocess.run(["git", "rm", "-q", "--cached", "README.md"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "drop readme"], cwd=root, check=True, capture_output=True)
        release_commit.commit_release(root, version=TAG, variant="RELEASED", source_sha=self.SOURCE)
        changed = self._changed(root)
        self.assertNotIn("README.md", changed)
        self.assertIn("assets/renders/bottom.png", changed)

    def test_ignored_readme_now_fails_loudly(self) -> None:
        # The legacy "|| true" hid staging errors; an ignored README.md is one.
        root = self.base / "ignored"
        make_release_repo(root, renders=False)
        subprocess.run(["git", "rm", "-q", "--cached", "README.md"], cwd=root, check=True, capture_output=True)
        with (root / ".gitignore").open("a", encoding="utf-8") as handle:
            handle.write("README.md\n")
        with self.assertRaises(subprocess.CalledProcessError):
            release_commit.commit_release(root, version=TAG, variant="RELEASED", source_sha=self.SOURCE)

    def test_nothing_to_commit_is_refused(self) -> None:
        root = self.base / "empty"
        root.mkdir()
        _init_repo(root)
        for name in ("CHANGELOG.md", ".boardwright/project.yaml", ".boardwright/document_revisions.yaml",
                     ".boardwright/release.env", ".boardwright/revision_history_variables.env",
                     ".boardwright/releases.yaml", ".boardwright/accepted_manifest.json",
                     ".boardwright/review_candidate.json", "README.md"):
            _write(root / name, "x\n")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "all"], cwd=root, check=True, capture_output=True)
        with self.assertRaises(release_commit.NothingToCommit):
            release_commit.commit_release(root, version=TAG, variant="RELEASED", source_sha=self.SOURCE)
        self.assertEqual(1, release_commit.main(["--root", str(root), "--version", TAG,
                                                 "--variant", "RELEASED", "--source-sha", self.SOURCE]))

    def test_local_approval_trailers(self) -> None:
        root = self.base / "local"
        make_release_repo(root)
        release_commit.commit_release(root, version=TAG, variant="RELEASED", source_sha=self.SOURCE,
                                      reviewed_candidate="ab" * 32, local_approval=True)
        body = _head(root)[1]
        parsed = subprocess.run(["git", "interpret-trailers", "--parse"], input=body, cwd=root,
                                capture_output=True, text=True, check=True).stdout
        self.assertIn(f"Reviewed-Candidate: {'ab' * 32}", parsed)
        self.assertIn("Approval-Authority: local", parsed)
        self.assertNotIn("Reviewed-Run", parsed)


class TrailerTests(unittest.TestCase):
    def test_local_approval_can_never_claim_a_github_run(self) -> None:
        with self.assertRaises(ValueError):
            release_commit.trailers("s", reviewed_run_url="https://github.com/o/r/actions/runs/1", local_approval=True)
        with self.assertRaises(ValueError):
            release_commit.trailers("s", local_approval=True)

    def test_message_shape(self) -> None:
        self.assertEqual("release: prepare 1.2.0 (RELEASED)\n\nSource-Commit: s\n",
                         release_commit.message("1.2.0", "RELEASED", release_commit.trailers("s")))


if __name__ == "__main__":
    unittest.main()
