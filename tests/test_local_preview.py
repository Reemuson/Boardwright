# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Local-first previews (docs/LOCAL_FIRST.md section 4): source policy, isolation,
records, selection, failures and the shared CLI/TUI entry point."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from boardwright import backends, local_preview
from boardwright.backends import Backend, DockerInfo, RemoteInfo

GITHUB = RemoteInfo("github", "https://github.com/o/r.git", "o/r", True, True, True)
GITHUB_NO_AUTH = RemoteInfo("github", "https://github.com/o/r.git", "o/r", None, False, True,
                            ("the GitHub CLI is not authenticated (gh auth login)",))
GITHUB_UNREACHABLE = RemoteInfo("github", "https://github.com/o/r.git", "o/r", False, True, True,
                                ("repository o/r is not reachable",))
NONE = RemoteInfo("none")
DOCKER = DockerInfo(True, "Docker 29")
NO_DOCKER = DockerInfo(False, "the Docker engine is not running")
VARIANTS = ("DRAFT", "CHECKED", "PRELIMINARY", "RELEASED")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def _write(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8", newline="\n")


def snapshot(root: Path) -> dict[str, str]:
    """Byte-level snapshot of every file in the working tree, ignored files included (not .git)."""

    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            path = Path(dirpath) / name
            out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def make_project(root: Path) -> Path:
    root.mkdir(parents=True)
    _git(root, "init", "-q", "-b", "dev")
    for key, value in (("user.name", "t"), ("user.email", "t@example.invalid"), ("core.autocrlf", "false"),
                       ("commit.gpgsign", "false")):
        _git(root, "config", key, value)
    _write(root / ".gitignore", ".boardwright/local.yaml\nbuild-cache/\n")
    _write(root / "board.kicad_pcb", "(kicad_pcb (version 20241229))\n")
    _write(root / "board.kicad_sch", "(kicad_sch (version 20250114))\n")
    _write(root / "board.kicad_pro", "{}\n")
    _write(root / "board.kicad_prl", "{\"view\": 1}\n")
    _write(root / "old.kicad_sym", "(kicad_symbol_lib)\n")
    _write(root / ".boardwright" / "project.yaml", "project: {name: board}\n")
    _write(root / "README.md", "# board\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial")
    _write(root / ".boardwright" / "local.yaml", "template: {logo: personal.png}\n")  # ignored, private
    _write(root / "build-cache" / "x.bin", b"ignored")
    return root


class FakeRunner:
    """Stands in for the container: records the call and writes outputs and a build record."""

    def __init__(self, status: int = 0) -> None:
        self.status = status
        self.calls: list[tuple[str, Path, list[str]]] = []
        self.staged: dict[str, str] = {}

    def __call__(self, kicad: str, workdir: Path, command: list[str]) -> int:
        self.calls.append((kicad, workdir, command))
        self.staged = snapshot(workdir)
        marker = json.loads((workdir / ".git" / "boardwright-stage").read_text(encoding="utf-8"))
        if self.status == 0:
            _write(workdir / "Manufacturing" / "out.txt", (workdir / "board.kicad_sch").read_text(encoding="utf-8"))
            _write(workdir / "boardwright-build" / "record.json", json.dumps({
                "image": local_preview_image(kicad), "image_id": "sha256:feed", "variant": command[5],
                "boardwright_source": {"tree": "abc"}, "configuration": {"kibot_config_tree": "def"},
                "stage": {"head": marker["head"], "worktree": marker["worktree"], "purpose": marker["purpose"]},
                "tools": {"kicad": "9.0.7"},
            }))
        return self.status


def local_preview_image(kicad: str) -> str:
    from boardwright.container import image_for

    return image_for(kicad).reference


class PreviewTestCase(unittest.TestCase):
    def setUp(self) -> None:
        # Paths with spaces and non-ASCII characters, as on a Windows profile.
        self.base = Path(tempfile.mkdtemp(prefix="bw preview é "))
        self.repo = make_project(self.base / "my board project")
        self.env = mock.patch.dict(os.environ, {
            "BOARDWRIGHT_STAGE_ROOT": str(self.base / "stage root"),
            "BOARDWRIGHT_PREVIEW_INDEX": str(self.base / "preview index"),
        })
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()

        def writable(func, path, _exc):
            os.chmod(path, 0o700)
            func(path)

        shutil.rmtree(self.base, onerror=writable)

    def config(self, **overrides) -> SimpleNamespace:
        values = {"root": self.repo, "preview_variant": "CHECKED", "preview_backend": "auto", "dev_branch": "dev",
                  "preview_workflow": "dev-preview.yaml", "preview_engine": "github-actions",
                  "preview_branch": "preview", "github_repo": "o/r"}
        values.update(overrides)
        return SimpleNamespace(**values)

    def run_preview(self, variant="CHECKED", *, override="", remote=GITHUB, docker=DOCKER, runner=None,
                    dispatch=None, config=None):
        self.remote_calls = []

        def inspect_remote(root, check_access=True):
            self.remote_calls.append(check_access)
            return remote

        runner = runner or FakeRunner()
        self.runner = runner
        self.dispatched = []

        def default_dispatch(plan, root):
            self.dispatched.append(plan.variant)

        with mock.patch.object(local_preview, "github_preview_problems", return_value=[]):
            return local_preview.run_preview(
                config or self.config(), variant, override=override, echo=lambda _line: None, runner=runner,
                inspect_remote=inspect_remote, inspect_docker=lambda root=None: docker,
                dispatch=dispatch or default_dispatch)


class SourcePolicyTests(PreviewTestCase):
    def test_clean_tree(self) -> None:
        plan = local_preview.plan_source(self.repo)
        self.assertFalse(plan.dirty)
        self.assertEqual((), plan.included)
        self.assertEqual(plan.source_digest, local_preview.plan_source(self.repo).source_digest)

    def test_dirty_tree_policy(self) -> None:
        _write(self.repo / "board.kicad_sch", "(kicad_sch (version 20250114) (changed))\n")  # saved edit
        (self.repo / "old.kicad_sym").unlink()  # deleted tracked file
        _write(self.repo / "board.kicad_prl", "{\"view\": 2}\n")  # per-user state
        _write(self.repo / "sub sheet.kicad_sch", "(kicad_sch)\n")  # new sheet, space in name
        _write(self.repo / "lib" / "parts.pretty" / "R.kicad_mod", "(footprint R)\n")
        _write(self.repo / "notes.txt", "scratch\n")
        _write(self.repo / ".env", "TOKEN=x\n")
        _write(self.repo / "api_token.kicad_pcb", "(kicad_pcb)\n")
        _write(self.repo / "board-backups" / "board.zip", b"zip")
        _write(self.repo / "_autosave-board.kicad_sch", "(kicad_sch)\n")
        plan = local_preview.plan_source(self.repo)
        included = {c.path: c.kind for c in plan.included}
        self.assertEqual({"board.kicad_sch": "modified", "sub sheet.kicad_sch": "untracked",
                          "lib/parts.pretty/R.kicad_mod": "untracked"}, included)
        self.assertEqual(("old.kicad_sym",), plan.deleted)
        excluded = dict(plan.excluded)
        self.assertEqual("per-user or private file", excluded["board.kicad_prl"])
        self.assertEqual("untracked and not a KiCad project file", excluded["notes.txt"])
        self.assertEqual("looks like a credential", excluded[".env"])
        self.assertEqual("looks like a credential", excluded["api_token.kicad_pcb"])
        self.assertIn("board-backups/board.zip", excluded)
        self.assertIn("_autosave-board.kicad_sch", excluded)
        self.assertNotIn(".boardwright/local.yaml", excluded)  # ignored: never even considered
        self.assertTrue(plan.dirty)

    def test_intent_to_add_includes_any_untracked_file(self) -> None:
        _write(self.repo / "notes.txt", "now part of the project\n")
        _git(self.repo, "add", "-N", "notes.txt")
        self.assertIn("notes.txt", {c.path for c in local_preview.plan_source(self.repo).included})

    def test_digest_follows_content_not_timestamps(self) -> None:
        clean = local_preview.plan_source(self.repo).source_digest
        _write(self.repo / "board.kicad_pcb", "(kicad_pcb (version 20241229) (edit))\n")
        dirty = local_preview.plan_source(self.repo).source_digest
        self.assertNotEqual(clean, dirty)
        _write(self.repo / "board.kicad_pcb", "(kicad_pcb (version 20241229))\n")
        self.assertEqual(clean, local_preview.plan_source(self.repo).source_digest)


class LocalRunTests(PreviewTestCase):
    def test_clean_preview_record_and_stage(self) -> None:
        before = snapshot(self.repo)
        outcome = self.run_preview("CHECKED")
        self.assertTrue(outcome.ok, outcome.message)
        self.assertEqual(before, snapshot(self.repo))
        record = outcome.record
        self.assertEqual(("preview", False, "local", "CHECKED", "succeeded"),
                         (record["purpose"], record["release_evidence"], record["backend"], record["variant"], record["status"]))
        source = record["source"]
        self.assertFalse(source["dirty"])
        self.assertEqual(_git(self.repo, "rev-parse", "HEAD").strip(), source["base_commit"])
        # Clean: the base commit plus only the stage-side removal of generated
        # files (README.md is tracked here), recorded as cleared outputs.
        self.assertEqual([{"path": "README.md", "files": 1, "tracked": True}], record["cleared_outputs"])
        self.assertEqual(source["base_commit"], _git(Path(record["outputs"]), "rev-parse", "HEAD~1").strip())
        self.assertEqual(local_preview_image("9"), record["toolchain"]["image"])
        self.assertEqual("sha256:feed", record["toolchain"]["image_id"])
        self.assertEqual("preview", record["build_record_stage"]["purpose"])
        workdir = Path(record["outputs"])
        self.assertTrue(str(workdir).startswith(str(self.base / "stage root")))
        self.assertNotIn(".boardwright/local.yaml", self.runner.staged)
        self.assertNotIn("build-cache/x.bin", self.runner.staged)
        marker = json.loads((workdir / ".git" / "boardwright-stage").read_text(encoding="utf-8"))
        self.assertEqual("preview", marker["purpose"])
        self.assertEqual(record, json.loads(local_preview.index_path(self.repo, "CHECKED", "9").read_text(encoding="utf-8")))
        self.assertEqual(["python3", "-m", "boardwright", "build", "--variant", "CHECKED", "--kicad", "9",
                          "--log-prefix", "preview"], self.runner.calls[0][2])

    def test_dirty_preview_stages_saved_changes_without_touching_the_tree(self) -> None:
        _write(self.repo / "board.kicad_sch", "(kicad_sch (version 20250114) (saved edit))\n")
        (self.repo / "old.kicad_sym").unlink()
        _write(self.repo / "new sheet.kicad_sch", "(kicad_sch new)\n")
        _write(self.repo / "notes.txt", "excluded\n")
        _write(self.repo / ".env", "SECRET=1\n")
        before = snapshot(self.repo)
        status_before = _git(self.repo, "status", "--porcelain")
        outcome = self.run_preview("DRAFT")
        self.assertTrue(outcome.ok, outcome.message)
        self.assertEqual(before, snapshot(self.repo))
        self.assertEqual(status_before, _git(self.repo, "status", "--porcelain"))
        staged = self.runner.staged
        self.assertEqual(hashlib.sha256(b"(kicad_sch (version 20250114) (saved edit))\n").hexdigest(), staged["board.kicad_sch"])
        self.assertIn("new sheet.kicad_sch", staged)
        for absent in ("old.kicad_sym", "notes.txt", ".env", ".boardwright/local.yaml"):
            self.assertNotIn(absent, staged)
        record = outcome.record
        self.assertTrue(record["source"]["dirty"])
        self.assertNotEqual(record["source"]["base_commit"], record["source"]["staged_commit"])
        self.assertIn("dirty", record["identity"])
        self.assertIn(record["source"]["staged_tree"][:12], record["identity"])
        self.assertEqual(_git(self.repo, "rev-parse", "HEAD").strip(), record["source"]["base_commit"])
        self.assertEqual("(kicad_sch (version 20250114) (saved edit))\n",
                         (Path(record["outputs"]) / "Manufacturing" / "out.txt").read_text(encoding="utf-8"))

    def test_tracked_local_yaml_is_refused(self) -> None:
        _git(self.repo, "add", "-f", ".boardwright/local.yaml")
        _git(self.repo, "commit", "-q", "-m", "oops")
        outcome = self.run_preview()
        self.assertFalse(outcome.ok)
        self.assertIn("local.yaml is tracked", outcome.message)
        self.assertEqual([], self.runner.calls)

    def test_repeated_previews_replace_the_stage_and_update_the_index(self) -> None:
        first = self.run_preview("CHECKED").record
        _write(self.repo / "board.kicad_pcb", "(kicad_pcb (version 20241229) (second))\n")
        second = self.run_preview("CHECKED").record
        self.assertEqual(first["outputs"], second["outputs"])
        self.assertNotEqual(first["source"]["source_digest"], second["source"]["source_digest"])
        self.assertEqual(second["source"]["source_digest"],
                         json.loads(local_preview.index_path(self.repo, "CHECKED", "9").read_text(encoding="utf-8"))["source"]["source_digest"])
        self.assertTrue(self.run_preview("CHECKED").ok)  # third run on the same state


class SnapshotIntegrityTests(PreviewTestCase):
    """F1: the staged source is the identity; races during staging are detected."""

    def _hook_reads(self, on_read):
        real = local_preview._read_bytes

        def hooked(path: Path) -> bytes:
            return on_read(path, real)

        return mock.patch.object(local_preview, "_read_bytes", side_effect=hooked)

    def test_recorded_hashes_are_the_staged_bytes(self) -> None:
        _write(self.repo / "board.kicad_sch", "(kicad_sch (saved))\n")
        _write(self.repo / "extra sheet ü.kicad_sch", "(kicad_sch extra)\n")
        record = self.run_preview("CHECKED").record
        staged = Path(record["outputs"])
        for change in record["source"]["included"]:
            self.assertEqual(change["sha256"], hashlib.sha256((staged / change["path"]).read_bytes()).hexdigest(), change)
        self.assertEqual(_git(staged, "rev-parse", "HEAD^{tree}").strip(), record["source"]["staged_tree"])
        self.assertFalse(Path(str(staged) + ".snapshot").exists())  # snapshot removed after staging

    def test_change_to_another_file_while_reading_refuses_and_keeps_the_previous_preview(self) -> None:
        first = self.run_preview("CHECKED").record
        index = local_preview.index_path(self.repo, "CHECKED", "9")
        before_index = index.read_bytes()
        _write(self.repo / "board.kicad_sch", "(kicad_sch (edit 1))\n")
        seen = []

        def save_project_while_reading(path, real):
            if not seen:  # KiCad saves the project file while the schematic is read
                _write(self.repo / "board.kicad_pro", "{\"saved\": true}\n")
            seen.append(path)
            return real(path)

        with self._hook_reads(save_project_while_reading):
            outcome = self.run_preview("CHECKED")
        self.assertFalse(outcome.ok)
        self.assertIn("changed while the preview was reading", outcome.message)
        self.assertEqual([], self.runner.calls)
        self.assertEqual(before_index, index.read_bytes())
        self.assertTrue((Path(first["outputs"]) / "Manufacturing" / "out.txt").is_file())

    def test_edit_and_revert_during_the_read_records_what_was_built_and_is_stale(self) -> None:
        original = "(kicad_sch (version 20250114))\n"
        intermediate = "(kicad_sch (half-saved))\n"
        _write(self.repo / "board.kicad_pcb", "(kicad_pcb (dirty))\n")  # make the tree dirty

        # The schematic is unchanged on disk before and after; only a racing
        # read could see the intermediate content. Simulate it on the schematic.
        _write(self.repo / "board.kicad_sch", original + ";")

        def racing(path, real):
            if path.name == "board.kicad_sch":
                _write(path, intermediate)
                data = real(path)
                _write(path, original + ";")  # reverted before the second fingerprint
                return data
            return real(path)

        with self._hook_reads(racing):
            outcome = self.run_preview("CHECKED")
        self.assertTrue(outcome.ok, outcome.message)
        record = outcome.record
        recorded = {c["path"]: c["sha256"] for c in record["source"]["included"]}
        self.assertEqual(hashlib.sha256(intermediate.encode()).hexdigest(), recorded["board.kicad_sch"])
        self.assertEqual(intermediate, (Path(record["outputs"]) / "board.kicad_sch").read_text(encoding="utf-8"))
        status = local_preview.preview_status(self.repo)[0]
        self.assertEqual("stale", status.state)  # the tree now differs from what was built

    def test_save_during_the_build_marks_stale_without_changing_the_identity(self) -> None:
        _write(self.repo / "board.kicad_sch", "(kicad_sch (before build))\n")
        expected = local_preview.plan_source(self.repo).source_digest

        class SavingRunner(FakeRunner):
            def __call__(inner, kicad, workdir, command):
                status = FakeRunner.__call__(inner, kicad, workdir, command)
                _write(self.repo / "board.kicad_sch", "(kicad_sch (saved during build))\n")
                return status

        record = self.run_preview("CHECKED", runner=SavingRunner()).record
        self.assertEqual("succeeded", record["status"])
        self.assertTrue(record["worktree_changed_during_preview"])
        self.assertEqual(expected, record["source"]["source_digest"])
        self.assertEqual("(kicad_sch (before build))\n",
                         (Path(record["outputs"]) / "board.kicad_sch").read_text(encoding="utf-8"))
        status = local_preview.preview_status(self.repo)[0]
        self.assertEqual("stale", status.state)
        self.assertIn("changed while it ran", " ".join(status.reasons))

    def test_head_moving_while_staging_is_refused(self) -> None:
        from boardwright import container

        real_stage = container.stage

        def commit_then_stage(*args, **kwargs):
            _write(self.repo / "README.md", "# moved\n")
            _git(self.repo, "commit", "-q", "-am", "moved during staging")
            return real_stage(*args, **kwargs)

        with mock.patch.object(container, "stage", side_effect=commit_then_stage):
            outcome = self.run_preview("CHECKED")
        self.assertFalse(outcome.ok)
        self.assertIn("HEAD moved while staging", outcome.message)
        self.assertEqual([], self.runner.calls)


class EvidenceSeparationTests(PreviewTestCase):
    """F2: preview evidence is marked before Docker starts and refused by every check."""

    class EvidenceRunner(FakeRunner):
        """Writes comparable evidence; can die (KeyboardInterrupt) after writing it."""

        def __init__(self, interrupt: bool = False, keep_purpose: bool = False) -> None:
            super().__init__()
            self.interrupt, self.keep_purpose = interrupt, keep_purpose
            self.marker_before_docker = None

        def __call__(self, kicad, workdir, command):
            self.marker_before_docker = (workdir / "boardwright-build" / "preview.json").is_file()
            status = super().__call__(kicad, workdir, command)
            path = workdir / "boardwright-build" / "record.json"
            built = json.loads(path.read_text(encoding="utf-8"))
            if not self.keep_purpose:
                built["stage"].pop("purpose")  # an older payload
            built.update({"source_commit": "a" * 40, "normaliser": 2})
            path.write_text(json.dumps(built), encoding="utf-8")
            (workdir / "boardwright-build" / "manifest.json").write_text(
                json.dumps({"schema": 2, "normaliser": 2, "files": {}}), encoding="utf-8")
            if self.interrupt:
                raise KeyboardInterrupt  # the host dies after the container finished
            return status

    def _refused(self, directory: Path) -> list[str]:
        from boardwright.container import compare_builds

        problems, _ = compare_builds(directory, directory)
        return [p for p in problems if "preview" in p]

    def test_marker_is_written_before_docker_starts(self) -> None:
        runner = self.EvidenceRunner()
        self.run_preview("CHECKED", runner=runner)
        self.assertTrue(runner.marker_before_docker)

    def test_interrupted_build_evidence_is_never_acceptable(self) -> None:
        runner = self.EvidenceRunner(interrupt=True)
        with self.assertRaises(KeyboardInterrupt):
            self.run_preview("CHECKED", runner=runner)
        stage_dir = local_preview.preview_stage_dir(self.repo, "CHECKED", "9")
        evidence = stage_dir / "boardwright-build"
        self.assertTrue((evidence / "record.json").is_file())
        self.assertTrue(self._refused(evidence))
        self.assertTrue(self._refused(stage_dir))
        # Each marker alone still refuses it.
        (evidence / "preview.json").unlink()
        self.assertTrue(self._refused(evidence), "stage marker alone")
        copied = self.base / "copied evidence"
        shutil.copytree(evidence, copied)
        self.assertFalse(self._refused(copied))  # no markers left at all: documented limit
        runner2 = self.EvidenceRunner()
        self.run_preview("DRAFT", runner=runner2)
        draft_evidence = local_preview.preview_stage_dir(self.repo, "DRAFT", "9") / "boardwright-build"
        copied2 = self.base / "copied draft evidence"
        shutil.copytree(draft_evidence, copied2)
        self.assertTrue(self._refused(copied2), "evidence marker travels with copied evidence")
        # The stage is not left locked by the interrupted run.
        self.assertTrue(self.run_preview("CHECKED").ok)

    def test_newer_payload_record_purpose_alone_refuses(self) -> None:
        self.run_preview("CHECKED", runner=self.EvidenceRunner(keep_purpose=True))
        evidence = local_preview.preview_stage_dir(self.repo, "CHECKED", "9") / "boardwright-build"
        copied = self.base / "copied"
        shutil.copytree(evidence, copied)
        (copied / "preview.json").unlink()
        self.assertTrue(self._refused(copied), "record stage.purpose alone")

    def test_a_preview_cannot_satisfy_held_candidate_approval_or_checked_acceptance(self) -> None:
        from boardwright.preview import PreviewState, mark_preview_reviewed
        from boardwright.review_gate import verify_candidate
        from boardwright.errors import BoardwrightError

        record = self.run_preview("CHECKED", runner=self.EvidenceRunner()).record
        stage_dir = Path(record["outputs"])
        self.assertFalse(any(stage_dir.rglob("review_candidate.json")))
        try:
            problems = verify_candidate(stage_dir, stage_dir, environ={})
        except Exception:
            problems = ["refused"]
        self.assertTrue(problems)
        state = PreviewState("ready", "boardwright-preview-CHECKED", record["source"]["base_commit"], run=None)
        with self.assertRaises(BoardwrightError):
            mark_preview_reviewed(SimpleNamespace(root=self.repo), state)


class GeneratedOutputTests(PreviewTestCase):
    """F6: previously generated outputs never survive into a preview's stage."""

    def setUp(self) -> None:
        super().setUp()
        for path, text in (("Manufacturing/Gerbers/old-F_Cu.gbr", "G04 old*\n"), ("Schematic/old.pdf", "%PDF old"),
                           ("Reports/old-drc.rpt", "old\n"), ("assets/renders/old_top.png", "png")):
            _write(self.repo / path, text)
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "accepted outputs (tracked)")
        self.before = snapshot(self.repo)

    def test_tracked_outputs_are_cleared_from_the_stage_only(self) -> None:
        record = self.run_preview("DRAFT").record
        staged = self.runner.staged  # what the build saw
        for old in ("Manufacturing/Gerbers/old-F_Cu.gbr", "Schematic/old.pdf", "Reports/old-drc.rpt",
                    "assets/renders/old_top.png", "README.md"):
            self.assertNotIn(old, staged)
        cleared = {item["path"]: item for item in record["cleared_outputs"]}
        self.assertEqual({"Manufacturing", "Schematic", "Reports", "README.md", "assets/renders"}, set(cleared))
        self.assertTrue(all(item["tracked"] for item in cleared.values()))
        self.assertEqual(self.before, snapshot(self.repo))  # the working tree keeps them
        tree = _git(Path(record["outputs"]), "ls-tree", "-r", "--name-only", "HEAD")
        self.assertNotIn("Schematic/old.pdf", tree)  # the staged identity records the removal

    def test_failed_build_cannot_present_old_outputs(self) -> None:
        record = self.run_preview("CHECKED", runner=FakeRunner(status=2)).record
        outputs = Path(record["outputs"])
        self.assertFalse((outputs / "Manufacturing" / "Gerbers" / "old-F_Cu.gbr").exists())
        self.assertFalse((outputs / "Schematic" / "old.pdf").exists())
        text = "\n".join(local_preview.format_record(record))
        self.assertIn("Outputs: none usable (failed", text)
        self.assertIsNone(local_preview.latest_outputs(self.repo))
        self.assertEqual("failed", local_preview.preview_status(self.repo)[0].state)


class SourceCompletenessTests(PreviewTestCase):
    """F4: submodules, file types, referenced images, non-ASCII names, dependencies."""

    def test_submodule_is_refused_not_silently_omitted(self) -> None:
        sha = _git(self.repo, "rev-parse", "HEAD").strip()
        _git(self.repo, "update-index", "--add", "--cacheinfo", f"160000,{sha},libs/shared lib")
        _git(self.repo, "commit", "-q", "-m", "add submodule")
        outcome = self.run_preview("CHECKED")
        self.assertFalse(outcome.ok)
        self.assertIn("Git submodules (libs/shared lib)", outcome.message)
        self.assertEqual([], self.runner.calls)
        self.assertFalse(local_preview.index_dir(self.repo).exists())

    def test_gitmodules_alone_is_refused(self) -> None:
        _write(self.repo / ".gitmodules", '[submodule "kicad-libs"]\n\tpath = kicad-libs\n\turl = https://example.invalid/l.git\n')
        _git(self.repo, "add", ".gitmodules")
        _git(self.repo, "commit", "-q", "-m", "declare submodule")
        outcome = self.run_preview("CHECKED")
        self.assertFalse(outcome.ok)
        self.assertIn("kicad-libs", outcome.message)

    def test_extended_project_file_types(self) -> None:
        names = ["db/parts.kicad_dbl", "db/vendor.kicad_httplib", "3d/a.stpz", "3d/b.wrz", "3d/c.igs", "3d/d.IGES"]
        for name in names:
            _write(self.repo / name, "x\n")
        included = {c.path for c in local_preview.plan_source(self.repo).included}
        self.assertEqual(set(names), included)

    def test_untracked_images_only_when_referenced(self) -> None:
        _write(self.repo / ".boardwright" / "project.yaml", "project: {name: board}\nassets: {logo: assets/logos/new logo.png}\n")
        _write(self.repo / "assets" / "logos" / "new logo.png", b"\x89PNG logo")
        _write(self.repo / "docs" / "notes.md", "![stack-up](figures/stackup.svg)\n")
        _write(self.repo / "docs" / "figures" / "stackup.svg", "<svg/>")
        _write(self.repo / "photo of desk.jpg", b"\xff\xd8 private")
        _write(self.repo / "assets" / "secret-badge.png", b"\x89PNG")
        _write(self.repo / "README.md", "# board\n![](assets/secret-badge.png)\n")
        plan = local_preview.plan_source(self.repo)
        included = {c.path for c in plan.included}
        excluded = dict(plan.excluded)
        self.assertIn("assets/logos/new logo.png", included)
        self.assertIn("docs/figures/stackup.svg", included)
        self.assertEqual("untracked image not referenced by a KiCad or project file", excluded["photo of desk.jpg"])
        self.assertEqual("looks like a credential", excluded["assets/secret-badge.png"])

    def test_non_ascii_file_names(self) -> None:
        _write(self.repo / "Größe.kicad_sym", "(kicad_symbol_lib v1)\n")
        _write(self.repo / "Ünterblatt.kicad_sch", "(kicad_sch old)\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "non-ascii names")
        _write(self.repo / "Größe.kicad_sym", "(kicad_symbol_lib v2)\n")      # modified, tracked
        (self.repo / "Ünterblatt.kicad_sch").unlink()                        # deleted, tracked
        _write(self.repo / "Schaltplan ü 電源.kicad_sch", "(kicad_sch new)\n")  # untracked
        before = snapshot(self.repo)
        record = self.run_preview("CHECKED").record
        self.assertEqual(before, snapshot(self.repo))
        included = {c["path"] for c in record["source"]["included"]}
        self.assertEqual({"Größe.kicad_sym", "Schaltplan ü 電源.kicad_sch"}, included)
        self.assertEqual(["Ünterblatt.kicad_sch"], record["source"]["deleted"])
        staged = Path(record["outputs"])
        self.assertEqual("(kicad_symbol_lib v2)\n", (staged / "Größe.kicad_sym").read_text(encoding="utf-8"))
        self.assertTrue((staged / "Schaltplan ü 電源.kicad_sch").is_file())
        self.assertFalse((staged / "Ünterblatt.kicad_sch").exists())

    def test_dependency_report(self) -> None:
        _write(self.repo / "3d" / "present.step", "step")
        _write(self.repo / "lib" / "parts.pretty" / "R.kicad_mod", "(footprint R)\n")
        _write(self.repo / "board.kicad_pcb", "\n".join([
            "(kicad_pcb",
            '  (model "${KICAD9_3DMODEL_DIR}/Resistor_SMD.3dshapes/R_0603.step")',
            '  (model "${KIPRJMOD}/3d/present.step")',
            '  (model "${KIPRJMOD}/3d/missing.step")',
            '  (model "C:\\\\Users\\\\someone\\\\models\\\\x.wrl")',
            '  (model "${MYLIBS}/y.step")',
            '  (model "${KICAD8_3DMODEL_DIR}/z.step")',
            ")", ""]))
        _write(self.repo / "fp-lib-table", "\n".join([
            "(fp_lib_table",
            '  (lib (name "parts")(type "KiCad")(uri "${KIPRJMOD}/lib/parts.pretty"))',
            '  (lib (name "corp")(type "KiCad")(uri "/srv/libs/corp.pretty"))',
            ")", ""]))
        record = self.run_preview("CHECKED").record
        dependencies = record["dependencies"]
        self.assertFalse(dependencies["complete"])
        reasons = {d["reference"]: d["reason"] for d in dependencies["unresolved"]}
        self.assertEqual("missing from the staged project", reasons["${KIPRJMOD}/3d/missing.step"])
        self.assertEqual("absolute path outside the project", reasons["C:\\\\Users\\\\someone\\\\models\\\\x.wrl"])
        self.assertIn("MYLIBS", reasons["${MYLIBS}/y.step"])
        self.assertIn("another KiCad version", reasons["${KICAD8_3DMODEL_DIR}/z.step"])
        self.assertEqual("absolute path outside the project", reasons["/srv/libs/corp.pretty"])
        self.assertNotIn("${KIPRJMOD}/3d/present.step", reasons)
        self.assertNotIn("${KICAD9_3DMODEL_DIR}/Resistor_SMD.3dshapes/R_0603.step", reasons)
        self.assertNotIn("${KIPRJMOD}/lib/parts.pretty", reasons)
        self.assertEqual({"3d-model": 6, "footprint-library": 2, "symbol-library": 0}, dependencies["checked"])
        text = "\n".join(local_preview.format_record(record))
        self.assertIn("not a complete reproduction", text)

    def test_complete_dependencies_are_reported_as_complete(self) -> None:
        self.assertTrue(self.run_preview("CHECKED").record["dependencies"]["complete"])


class DesignRuleReportingTests(PreviewTestCase):
    """F3: build completion and DRC/ERC results are reported separately."""

    class ReportingRunner(FakeRunner):
        def __call__(self, kicad, workdir, command):
            from test_design_rules import ERC_REPORT, drc_report

            status = super().__call__(kicad, workdir, command)
            _write(workdir / "Reports" / "report_board-drc.rpt", drc_report())
            _write(workdir / "Reports" / "report_board-erc.rpt", ERC_REPORT)
            _write(workdir / "kibot_preview.log", "ERROR:DRC errors: 1 (kibot.gs - gs.py:935)\n")
            return status

    def test_completed_build_with_drc_errors_is_not_reported_as_clean(self) -> None:
        import sys

        sys.path.insert(0, str(Path(__file__).parent))
        outcome = self.run_preview("CHECKED", runner=self.ReportingRunner())
        self.assertTrue(outcome.ok)  # the build completed
        self.assertIs(True, outcome.rule_errors)
        self.assertIn("build completed, outputs generated; design-rule errors: DRC 1 error(s)/28 violation(s)",
                      outcome.message)
        lines = local_preview.format_record(outcome.record)
        self.assertEqual("  Build: completed, outputs generated", lines[1])
        self.assertIn("28 violation(s): 1 error(s), 26 warning(s), 1 excluded", lines[2])
        self.assertIn("ERC (Reports/report_board-erc.rpt): 4 message(s): 0 error(s), 4 warning(s)", lines[3])
        self.assertEqual("  KiBot reported 1 DRC error(s)", lines[4])

    def test_missing_reports_are_unknown(self) -> None:
        outcome = self.run_preview("DRAFT")  # FakeRunner writes no reports
        self.assertIsNone(outcome.rule_errors)
        self.assertIn("design rules unknown", outcome.message)

    def test_failed_build_does_not_evaluate_design_rules(self) -> None:
        record = self.run_preview("CHECKED", runner=FakeRunner(status=1)).record
        lines = local_preview.format_record(record)
        self.assertEqual("  Build: FAILED, no usable outputs", lines[1])
        self.assertIn("not evaluated", lines[2])


class TemporaryOwnershipTests(PreviewTestCase):
    """S1: snapshots and new stages live in owned, unique temporaries."""

    def _baseline(self) -> tuple[Path, bytes]:
        record = self.run_preview("CHECKED").record
        index = local_preview.index_path(self.repo, "CHECKED", "9")
        return Path(record["outputs"]), index.read_bytes()

    def _temporaries(self, workdir: Path) -> list[str]:
        return sorted(p.name for p in workdir.parent.glob(f".{workdir.name}.*"))

    def _assert_previous_preserved(self, workdir: Path, index_bytes: bytes) -> None:
        self.assertTrue((workdir / "Manufacturing" / "out.txt").is_file())
        self.assertEqual(index_bytes, local_preview.index_path(self.repo, "CHECKED", "9").read_bytes())
        self.assertEqual([], self._temporaries(workdir))

    def test_unrelated_directory_at_the_old_snapshot_path_is_untouched(self) -> None:
        workdir = local_preview.preview_stage_dir(self.repo, "CHECKED", "9")
        old_path = workdir.with_name(workdir.name + ".snapshot")
        _write(old_path / "my notes.txt", "user data\n")
        lookalike = workdir.parent / f".{workdir.name}.snapshot-abc123"  # no owner marker
        _write(lookalike / "keep.txt", "also user data\n")
        self.assertTrue(self.run_preview("CHECKED").ok)
        self.assertTrue(self.run_preview("CHECKED").ok)
        self.assertEqual("user data\n", (old_path / "my notes.txt").read_text(encoding="utf-8"))
        self.assertEqual("also user data\n", (lookalike / "keep.txt").read_text(encoding="utf-8"))

    def test_exceptions_during_source_planning_keep_the_previous_preview(self) -> None:
        from boardwright.errors import BoardwrightError

        workdir, index_bytes = self._baseline()
        _write(self.repo / "board.kicad_sch", "(kicad_sch (edit))\n")
        for exc in (BoardwrightError("planning failed"), OSError("disk read error")):
            with self.subTest(exc=type(exc).__name__), \
                    mock.patch.object(local_preview, "plan_source", side_effect=exc):
                try:
                    outcome = self.run_preview("CHECKED")
                    self.assertFalse(outcome.ok)
                except OSError:
                    pass  # not a BoardwrightError: propagates, after clean-up
                self.assertEqual([], self.runner.calls)
                self._assert_previous_preserved(workdir, index_bytes)

    def test_interrupt_during_planning_cleans_up(self) -> None:
        workdir, index_bytes = self._baseline()
        with mock.patch.object(local_preview, "plan_source", side_effect=KeyboardInterrupt), \
                self.assertRaises(KeyboardInterrupt):
            self.run_preview("CHECKED")
        self._assert_previous_preserved(workdir, index_bytes)

    def test_staging_failure_keeps_the_previous_preview(self) -> None:
        from boardwright import container

        workdir, index_bytes = self._baseline()
        with mock.patch.object(container, "stage", side_effect=container.ContainerError("clone failed")):
            outcome = self.run_preview("CHECKED")
        self.assertFalse(outcome.ok)
        self.assertIn("clone failed", outcome.message)
        self._assert_previous_preserved(workdir, index_bytes)

    def test_abandoned_owned_temporaries_are_removed_and_others_kept(self) -> None:
        workdir, _ = self._baseline()
        abandoned = local_preview._owned_temporary(workdir, "snapshot")  # a killed run's leftovers
        _write(abandoned / "board.kicad_sch", "stale")
        other_stage = local_preview.preview_stage_dir(self.repo, "DRAFT", "9")
        foreign = local_preview._owned_temporary(other_stage, "snapshot")
        self.assertTrue(self.run_preview("CHECKED").ok)
        self.assertFalse(abandoned.exists())
        self.assertTrue(foreign.exists())  # another stage's, possibly in use

    def test_non_stage_directory_at_the_stage_path_is_refused(self) -> None:
        workdir = local_preview.preview_stage_dir(self.repo, "CHECKED", "9")
        _write(workdir / "important.txt", "not a stage\n")
        outcome = self.run_preview("CHECKED")
        self.assertFalse(outcome.ok)
        self.assertIn("not a Boardwright stage", outcome.message)
        self.assertEqual("not a stage\n", (workdir / "important.txt").read_text(encoding="utf-8"))
        self.assertEqual([], self._temporaries(workdir))


class LockTests(PreviewTestCase):
    def test_busy_preview_does_not_touch_the_stage_or_the_index(self) -> None:
        from boardwright.stage_lock import stage_lock

        first = self.run_preview("CHECKED").record
        index = local_preview.index_path(self.repo, "CHECKED", "9")
        before = index.read_bytes()
        stage_dir = Path(first["outputs"])
        with stage_lock(stage_dir):  # another run holds this stage
            outcome = self.run_preview("CHECKED")
        self.assertFalse(outcome.ok)
        self.assertIn("in use by", outcome.message)
        self.assertEqual([], self.runner.calls)
        self.assertEqual(before, index.read_bytes())
        self.assertTrue((stage_dir / "Manufacturing" / "out.txt").is_file())
        self.assertTrue(self.run_preview("DRAFT").ok)  # other variants are independent


class SelectionTests(PreviewTestCase):
    def test_every_variant_defaults_to_local_for_every_remote(self) -> None:
        for remote in (GITHUB, NONE, GITHUB_NO_AUTH, GITHUB_UNREACHABLE):
            for variant in VARIANTS:
                with self.subTest(remote=remote.kind, problems=remote.problems, variant=variant):
                    outcome = self.run_preview(variant, remote=remote)
                    self.assertTrue(outcome.ok, outcome.message)
                    self.assertIs(Backend.LOCAL, outcome.selection.backend)
                    self.assertEqual([], self.dispatched)
                    # No GitHub access is needed (or attempted) for a local preview.
                    self.assertEqual([False], self.remote_calls)

    def test_without_a_remote_or_any_commit_pushed(self) -> None:
        # Real inspection of a repository with no origin.
        outcome = local_preview.run_preview(self.config(), "CHECKED", echo=lambda _l: None, runner=FakeRunner(),
                                            inspect_docker=lambda root=None: DOCKER)
        self.assertTrue(outcome.ok, outcome.message)
        self.assertEqual("none", backends.inspect_remote(self.repo, check_access=False).kind)

    def test_explicit_github_override_dispatches_the_existing_workflow(self) -> None:
        for override in ("github",):
            outcome = self.run_preview("RELEASED", override=override)
            self.assertTrue(outcome.ok, outcome.message)
            self.assertIs(Backend.GITHUB, outcome.selection.backend)
            self.assertEqual(["RELEASED"], self.dispatched)
            self.assertEqual([], self.runner.calls)
            self.assertEqual([True], self.remote_calls)  # access is checked only when GitHub is requested

    def test_project_setting_github_and_local_override(self) -> None:
        outcome = self.run_preview(config=self.config(preview_backend="github"))
        self.assertIs(Backend.GITHUB, outcome.selection.backend)
        outcome = self.run_preview(config=self.config(preview_backend="github"), override="local")
        self.assertIs(Backend.LOCAL, outcome.selection.backend)
        self.assertEqual([], self.dispatched)

    def test_github_override_with_unusable_github_is_refused_not_switched(self) -> None:
        for remote in (NONE, GITHUB_NO_AUTH, GITHUB_UNREACHABLE):
            outcome = self.run_preview(override="github", remote=remote)
            self.assertFalse(outcome.ok)
            self.assertIsNone(outcome.selection.backend)
            self.assertEqual([], self.runner.calls)
            self.assertEqual([], self.dispatched)

    def test_missing_docker_is_refused_not_switched(self) -> None:
        outcome = self.run_preview(docker=NO_DOCKER)
        self.assertFalse(outcome.ok)
        self.assertIn("Docker is unavailable", outcome.message)
        self.assertIn("--backend github", outcome.message)
        self.assertEqual([], self.runner.calls)
        self.assertEqual([], self.dispatched)
        self.assertFalse(local_preview.index_dir(self.repo).exists())
        no_remote = self.run_preview(docker=NO_DOCKER, remote=NONE)
        self.assertNotIn("--backend github", no_remote.message)

    def test_unsupported_variant_is_refused(self) -> None:
        outcome = self.run_preview("FINAL")
        self.assertFalse(outcome.ok)
        self.assertIn("Unsupported variant", outcome.message)
        self.assertEqual([], self.runner.calls)

    def test_failed_local_build_is_reported_and_not_retried_on_github(self) -> None:
        outcome = self.run_preview(runner=FakeRunner(status=3))
        self.assertFalse(outcome.ok)
        self.assertIn("not retried on GitHub", outcome.message)
        self.assertEqual([], self.dispatched)
        self.assertEqual("failed", outcome.record["status"])
        self.assertEqual(3, outcome.record["exit_status"])
        status = local_preview.preview_status(self.repo)
        self.assertEqual("failed", status[0].state)

    def test_failed_github_dispatch_is_not_retried_locally(self) -> None:
        from boardwright.errors import BoardwrightError

        def failing(plan, root):
            raise BoardwrightError("HTTP 403")

        outcome = self.run_preview(override="github", dispatch=failing)
        self.assertFalse(outcome.ok)
        self.assertIn("not retried locally", outcome.message)
        self.assertEqual([], self.runner.calls)


class StatusTests(PreviewTestCase):
    def test_current_then_stale_then_current_again(self) -> None:
        self.run_preview("CHECKED")
        self.assertEqual("current", local_preview.preview_status(self.repo)[0].state)
        _write(self.repo / "board.kicad_pcb", "(kicad_pcb (edited after preview))\n")
        stale = local_preview.preview_status(self.repo)[0]
        self.assertEqual("stale", stale.state)
        self.assertIn("changed since this preview", stale.reasons[0])
        _write(self.repo / "board.kicad_pcb", "(kicad_pcb (version 20241229))\n")
        self.assertEqual("current", local_preview.preview_status(self.repo)[0].state)

    def test_toolchain_change_missing_outputs_and_interrupted_runs(self) -> None:
        record = self.run_preview("CHECKED").record
        with mock.patch("boardwright.container.image_for",
                        return_value=SimpleNamespace(reference="other@sha256:1")):
            self.assertIn("pinned toolchain changed", " ".join(local_preview.preview_status(self.repo)[0].reasons))
        shutil.rmtree(record["outputs"], onerror=lambda f, p, e: (os.chmod(p, 0o700), f(p)))
        self.assertEqual("missing outputs", local_preview.preview_status(self.repo)[0].state)
        index = local_preview.index_path(self.repo, "CHECKED", "9")
        data = json.loads(index.read_text(encoding="utf-8"))
        data["status"] = "running"
        index.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual("incomplete", local_preview.preview_status(self.repo)[0].state)

    def test_format_and_latest_outputs(self) -> None:
        record = self.run_preview("CHECKED").record
        text = local_preview.format_status(local_preview.preview_status(self.repo))
        for expected in ("never release evidence", record["outputs"], record["evidence"], record["source"]["base_commit"]):
            self.assertIn(expected, text)
        self.assertEqual(Path(record["outputs"]), local_preview.latest_outputs(self.repo))
        self.assertIn("No local previews", local_preview.format_status([]))


class EvidenceTests(PreviewTestCase):
    def test_preview_build_is_never_release_evidence(self) -> None:
        from boardwright.container import compare_builds

        record = self.run_preview("RELEASED").record
        evidence = Path(record["evidence"])
        manifest = {"schema": 2, "normaliser": 2, "files": {}}
        (evidence / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        built = json.loads((evidence / "record.json").read_text(encoding="utf-8"))
        built.update({"source_commit": "a" * 40, "normaliser": 2})
        (evidence / "record.json").write_text(json.dumps(built), encoding="utf-8")
        try:
            problems, _ = compare_builds(evidence, evidence)
        except Exception as exc:  # manifest schema mismatch must not mask the purpose check
            self.fail(f"compare_builds raised {exc}")
        self.assertTrue(any("local preview (never release evidence)" in p for p in problems), problems)

    def test_clean_preview_from_an_older_payload_is_still_refused(self) -> None:
        """The project's payload writes record.json; an old one omits stage.purpose."""

        from boardwright.container import compare_builds

        class OldPayloadRunner(FakeRunner):
            def __call__(self, kicad, workdir, command):
                status = super().__call__(kicad, workdir, command)
                path = workdir / "boardwright-build" / "record.json"
                built = json.loads(path.read_text(encoding="utf-8"))
                built["stage"].pop("purpose")
                built.update({"source_commit": "a" * 40, "normaliser": 2})
                path.write_text(json.dumps(built), encoding="utf-8")
                (workdir / "boardwright-build" / "manifest.json").write_text(
                    json.dumps({"schema": 2, "normaliser": 2, "files": {}}), encoding="utf-8")
                return status

        record = self.run_preview("CHECKED", runner=OldPayloadRunner()).record
        self.assertFalse(record["source"]["dirty"])  # clean: no worktree flag to rely on
        evidence = Path(record["evidence"])
        marker = json.loads((evidence / "preview.json").read_text(encoding="utf-8"))
        self.assertEqual(("preview", False), (marker["purpose"], marker["release_evidence"]))
        problems, _ = compare_builds(evidence, evidence)
        self.assertTrue(any("local preview evidence" in p for p in problems), problems)

    def test_build_record_carries_the_preview_purpose(self) -> None:
        from boardwright.build import _stage_marker

        record = self.run_preview("CHECKED").record
        self.assertEqual("preview", _stage_marker(Path(record["outputs"]))["purpose"])

    def test_no_review_marker_is_written(self) -> None:
        self.run_preview("CHECKED")
        markers = list(self.repo.rglob("*review*"))
        self.assertEqual([], [m for m in markers if ".git" not in m.parts])


class LocationTests(unittest.TestCase):
    def test_project_key_is_collision_resistant_and_case_insensitive_on_windows(self) -> None:
        a = local_preview.project_key(Path("C:/work/board"))
        b = local_preview.project_key(Path("D:/other/board"))
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("board-"))
        if os.name == "nt":
            self.assertEqual(a, local_preview.project_key(Path("c:/WORK/Board")).replace("Board", "board"))


class SharedEntryPointTests(unittest.TestCase):
    def test_tui_and_cli_call_the_same_function(self) -> None:
        from boardwright import cli, tui

        with mock.patch.object(local_preview, "run_preview",
                               return_value=SimpleNamespace(ok=True, message="m", record=None)) as run:
            tui.run_shared_preview(SimpleNamespace(), "DRAFT", "auto", print)
            tui.run_shared_preview(SimpleNamespace(), "DRAFT", "github", print)
        self.assertEqual("", run.call_args_list[0].kwargs["override"])
        self.assertEqual("github", run.call_args_list[1].kwargs["override"])
        source = Path(tui.__file__).read_text(encoding="utf-8")
        self.assertNotIn("select_backend(", source)  # no duplicated selection policy
        self.assertNotIn("dirty_count:\n", source.split("def _generate_preview")[1].split("def _run_preview")[0])

    def test_cli_dispatch_flag_is_the_github_override(self) -> None:
        from boardwright import cli

        config = SimpleNamespace(root=Path("."), preview_variant="CHECKED", preview_backend="auto")
        with mock.patch.object(cli, "load_config", return_value=config), \
                mock.patch.object(local_preview, "run_preview",
                                  return_value=SimpleNamespace(ok=True, message="dispatched", record=None)) as run:
            self.assertEqual(0, cli.main(["preview", "--dispatch", "-v", "RELEASED"]))
            self.assertEqual("github", run.call_args.kwargs["override"])
            self.assertEqual(0, cli.main(["preview", "-v", "DRAFT"]))
            self.assertEqual("", run.call_args.kwargs["override"])
            self.assertEqual(0, cli.main(["preview", "--backend", "local"]))
            self.assertEqual("local", run.call_args.kwargs["override"])
        with mock.patch.object(cli, "load_config", return_value=config):
            self.assertNotEqual(0, cli.main(["preview", "--dispatch", "--backend", "local"]))


if __name__ == "__main__":
    unittest.main()
