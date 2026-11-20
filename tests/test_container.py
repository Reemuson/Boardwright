# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Container runner: pinned image, mounts, LF staging, local-vs-GitHub comparison."""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_revision_model import _git, _rmtree  # noqa: E402

from boardwright import container  # noqa: E402
from boardwright.container import ContainerError, compare_builds, docker_command, image_for, stage  # noqa: E402
from boardwright.output_manifest import build_manifest  # noqa: E402


class ImageTests(unittest.TestCase):
    def test_every_image_is_pinned_by_digest_to_a_release_tag(self) -> None:
        self.assertEqual({"9", "10"}, set(container.IMAGES))
        self.assertEqual("9", container.DEFAULT_KICAD)
        for kicad, image in container.IMAGES.items():
            self.assertRegex(image.digest, r"^sha256:[0-9a-f]{64}$")
            self.assertNotRegex(image.tag, r"^(dev|latest)|^dev_")
            self.assertIn(f"_k{kicad}.", image.tag)
            self.assertTrue(image.reference.endswith("@" + image.digest))

    def test_unknown_kicad_is_rejected(self) -> None:
        with self.assertRaises(ContainerError):
            image_for("8")


class CandidateTests(unittest.TestCase):
    def _args(self):
        return container.main.__globals__["argparse"].Namespace(
            version="1.2.3", variant="RELEASED", kind="draft", drawing_revision="B", revision_description="d",
            change_class="documentation", justification="j", kicad="9", release_date="",
        )

    def test_candidate_runs_the_prepare_release_sequence_and_never_publishes(self) -> None:
        steps = container.candidate_steps(self._args())
        names = [step[0] for step in steps]
        self.assertEqual(["release-prepare", "build", "manifest", "check-baseline", "review-candidate"], names)
        text = " ".join(" ".join(step) for step in steps)
        for forbidden in ("push", "tag", "commit", "gh ", "approved"):
            self.assertNotIn(forbidden, text)
        build = steps[1]
        self.assertEqual(["CHANGELOG.md", ".boardwright"], build[build.index("--keep") + 1:])


class DockerCommandTests(unittest.TestCase):
    def _argv(self, **kwargs) -> list[str]:
        return docker_command(image_for("9"), Path("work"), ["python3", "-m", "boardwright", "build"], **kwargs)

    def test_only_the_work_tree_is_mounted_read_write(self) -> None:
        argv = self._argv(user="1000:1000")
        volumes = [argv[i + 1] for i, item in enumerate(argv) if item == "--volume"]
        self.assertEqual(1, len(volumes))
        self.assertTrue(volumes[0].endswith(":/work"))
        self.assertNotIn("/etc/shadow", " ".join(argv))
        self.assertEqual("/work", argv[argv.index("--workdir") + 1])

    def test_identity_user_and_private_home(self) -> None:
        argv = self._argv(user="1001:121")
        env = dict(item.split("=", 1) for item in (argv[i + 1] for i, a in enumerate(argv) if a == "--env"))
        self.assertEqual("1001:121", argv[argv.index("--user") + 1])
        self.assertEqual(image_for("9").reference, env["BOARDWRIGHT_IMAGE"])
        self.assertEqual("/tmp/boardwright-home", env["HOME"])
        self.assertEqual("/work/src", env["PYTHONPATH"])
        self.assertEqual("safe.directory", env["GIT_CONFIG_KEY_0"])
        self.assertEqual(image_for("9").reference, argv[argv.index("--entrypoint") + 2])

    def test_container_user_override_for_runner_uid(self) -> None:
        with patch.dict("os.environ", {"BOARDWRIGHT_CONTAINER_USER": "1001:121"}):
            argv = docker_command(image_for("9"), Path("work"), ["true"])
        self.assertEqual("1001:121", argv[argv.index("--user") + 1])

    def test_cache_volume_sets_kibot_3d_models(self) -> None:
        argv = self._argv(user="", cache_dir="boardwright-3d-k9")
        self.assertIn("boardwright-3d-k9:/cache_3d", argv)
        self.assertIn("KIBOT_3D_MODELS=/cache_3d", argv)
        self.assertNotIn("--user", argv)

    def test_dispatch_inputs_are_not_forwarded_implicitly(self) -> None:
        with patch.dict("os.environ", {"GITHUB_ACTIONS": "true", "IN_JUSTIFICATION": "x; rm -rf /"}):
            argv = self._argv(user="")
        self.assertFalse(any(item.startswith("IN_") for item in argv))
        self.assertIn("GITHUB_ACTIONS=true", argv)


class CredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="bw-cred-"))
        self.addCleanup(_rmtree, self.root)
        _git(self.root, "init", "-q")

    def test_tokens_in_the_runner_environment_are_never_forwarded(self) -> None:
        secrets = {"GITHUB_ACTIONS": "true", "GH_TOKEN": "ghs_x", "GITHUB_TOKEN": "ghs_y", "ACTIONS_RUNTIME_TOKEN": "z"}
        with patch.dict("os.environ", secrets):
            argv = docker_command(image_for("9"), self.root, ["true"], user="")
        joined = " ".join(argv)
        for value in ("ghs_x", "ghs_y", "ACTIONS_RUNTIME_TOKEN"):
            self.assertNotIn(value, joined)
        self.assertIn("GITHUB_ACTIONS=true", joined)

    def test_secret_like_environment_is_refused(self) -> None:
        with patch.object(container.shutil, "which", return_value="docker"), patch.object(container.subprocess, "run") as run:
            with self.assertRaisesRegex(ContainerError, "GH_TOKEN"):
                container.run_in_container("9", self.root, ["true"], environment={"GH_TOKEN": "x"})
        self.assertFalse(any(call.args and call.args[0][:2] == ["docker", "run"] for call in run.call_args_list))

    def test_persisted_checkout_token_blocks_the_container(self) -> None:
        _git(self.root, "config", "http.https://github.com/.extraheader", "AUTHORIZATION: basic c2VjcmV0")
        self.assertEqual(["http.https://github.com/.extraheader"], container.repository_credentials(self.root))
        with patch.object(container.shutil, "which", return_value="docker"):
            with self.assertRaisesRegex(ContainerError, "persist-credentials: false") as caught:
                container.run_in_container("9", self.root, ["true"])
        self.assertNotIn("c2VjcmV0", str(caught.exception))

    def test_credentials_in_remote_url_block_the_container(self) -> None:
        _git(self.root, "remote", "add", "origin", "https://x-access-token:abc@github.com/o/r.git")
        self.assertEqual(["remote.origin.url"], container.repository_credentials(self.root))

    def test_clean_repository_has_no_credentials(self) -> None:
        _git(self.root, "remote", "add", "origin", "https://github.com/o/r.git")
        self.assertEqual([], container.repository_credentials(self.root))


class OwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="bw-own-"))
        self.addCleanup(_rmtree, self.root)
        (self.root / "Manufacturing").mkdir()
        (self.root / "Manufacturing" / "b-F_Cu.gbr").write_text("x", encoding="utf-8")
        self.owner = self.root.lstat().st_uid

    def test_outputs_owned_by_the_caller_pass(self) -> None:
        with patch.object(container.os, "getuid", create=True, return_value=self.owner):
            self.assertEqual([], container.foreign_owned(self.root))

    def test_outputs_owned_by_another_user_are_reported(self) -> None:
        with patch.object(container.os, "getuid", create=True, return_value=self.owner + 1):
            found = container.foreign_owned(self.root)
        self.assertIn("Manufacturing/b-F_Cu.gbr", found)


class StageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="bw-stage-"))
        self.repo = self.tmp / "project"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "config", "core.autocrlf", "true")
        _git(self.repo, "config", "user.name", "t")
        _git(self.repo, "config", "user.email", "t@example.invalid")
        (self.repo / ".gitattributes").write_bytes(b"* text=auto\n*.bat text eol=crlf\n")
        (self.repo / ".gitignore").write_bytes(b".boardwright/local.yaml\nManufacturing/\n")
        (self.repo / ".boardwright").mkdir()
        (self.repo / ".boardwright" / "project.yaml").write_bytes(b"project: {}\n")
        (self.repo / "board.kicad_pcb").write_bytes(b"(kicad_pcb\r\n  (version 1)\r\n)\r\n")
        (self.repo / "run.bat").write_bytes(b"@echo off\r\n")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-q", "-m", "init")
        (self.repo / ".boardwright" / "local.yaml").write_bytes(b"template:\n  sheet_image: x.png\n")
        self.dest = self.tmp / "stage"

    def tearDown(self) -> None:
        _rmtree(self.tmp)

    def test_stage_is_lf_without_local_overrides_and_leaves_host_untouched(self) -> None:
        before = container.host_tree_fingerprint(self.repo)
        target = stage(self.repo, dest=self.dest)
        self.assertEqual(b"(kicad_pcb\n  (version 1)\n)\n", (target / "board.kicad_pcb").read_bytes())
        self.assertEqual(b"@echo off\r\n", (target / "run.bat").read_bytes())
        self.assertFalse((target / ".boardwright" / "local.yaml").exists())
        self.assertEqual("main", _git(target, "symbolic-ref", "--short", "HEAD").strip())
        self.assertEqual(_git(self.repo, "rev-parse", "HEAD"), _git(target, "rev-parse", "HEAD"))
        self.assertEqual(before, container.host_tree_fingerprint(self.repo))
        self.assertTrue((self.repo / ".boardwright" / "local.yaml").exists())

    def test_committed_stage_ignores_uncommitted_changes(self) -> None:
        (self.repo / "board.kicad_pcb").write_bytes(b"(kicad_pcb changed)\r\n")
        target = stage(self.repo, dest=self.dest)
        self.assertEqual(b"(kicad_pcb\n  (version 1)\n)\n", (target / "board.kicad_pcb").read_bytes())

    def test_worktree_stage_includes_changes_normalised_but_not_ignored_files(self) -> None:
        (self.repo / "board.kicad_pcb").write_bytes(b"(kicad_pcb changed)\r\n")
        (self.repo / "new.kicad_sch").write_bytes(b"(kicad_sch)\r\n")
        (self.repo / "run.bat").unlink()
        (self.repo / "Manufacturing").mkdir()
        (self.repo / "Manufacturing" / "old.gbr").write_bytes(b"G04*\n")
        target = stage(self.repo, worktree=True, dest=self.dest)
        self.assertEqual(b"(kicad_pcb changed)\n", (target / "board.kicad_pcb").read_bytes())
        self.assertEqual(b"(kicad_sch)\n", (target / "new.kicad_sch").read_bytes())
        self.assertFalse((target / "run.bat").exists())
        self.assertFalse((target / "Manufacturing").exists())
        self.assertFalse((target / ".boardwright" / "local.yaml").exists())
        self.assertEqual("", _git(target, "status", "--porcelain"))
        self.assertNotEqual(_git(self.repo, "rev-parse", "HEAD"), _git(target, "rev-parse", "HEAD"))
        self.assertEqual(_git(self.repo, "rev-parse", "HEAD"), _git(target, "rev-parse", "HEAD~1"))
        self.assertTrue(json.loads((target / ".git" / container.STAGE_MARKER).read_text(encoding="utf-8"))["worktree"])

    def test_restaging_replaces_only_a_previous_stage(self) -> None:
        stage(self.repo, dest=self.dest)
        (self.dest / "stale-output").write_text("x", encoding="utf-8")
        stage(self.repo, dest=self.dest)
        self.assertFalse((self.dest / "stale-output").exists())
        other = self.tmp / "not-a-stage"
        other.mkdir()
        (other / "keep.txt").write_text("mine", encoding="utf-8")
        with self.assertRaisesRegex(ContainerError, "not a Boardwright stage"):
            stage(self.repo, dest=other)
        self.assertTrue((other / "keep.txt").exists())


GERBER = "%TF.GenerationSoftware,KiCad,Pcbnew,9.0.7*%\n%TF.CreationDate,{date}*%\n%TF.ProjectId,b,62,{rev}*%\nX{x}Y0D02*\n"


class CompareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="bw-compare-"))

    def tearDown(self) -> None:
        _rmtree(self.tmp)

    def _build(self, name: str, *, date: str = "2026-11-19T10:00:00", x: str = "0", pdf: bytes = b"%PDF-1", **record) -> Path:
        root = self.tmp / name
        gerbers = root / "Manufacturing" / "Fabrication" / "Gerbers"
        gerbers.mkdir(parents=True)
        (gerbers / "b-F_Cu.gbr").write_text(GERBER.format(date=date, rev="A", x=x), encoding="utf-8", newline="\n")
        (root / "Manufacturing" / "Fabrication" / "b-fabrication.pdf").write_bytes(pdf)
        evidence = root / "boardwright-build"
        evidence.mkdir()
        (evidence / "manifest.json").write_text(json.dumps(build_manifest(root)), encoding="utf-8")
        data = {"source_commit": "abc", "variant": "CHECKED", "image": image_for("9").reference, "image_id": "sha256:aaa", "normaliser": 2, "source_modified": []}
        data.update(record)
        (evidence / "record.json").write_text(json.dumps(data), encoding="utf-8")
        return root

    def test_identical_builds_match(self) -> None:
        problems, _ = compare_builds(self._build("a"), self._build("b"))
        self.assertEqual([], problems)

    def test_timestamps_differ_only_in_normalised_fields(self) -> None:
        problems, notes = compare_builds(self._build("a"), self._build("b", date="2026-11-20T08:00:00", pdf=b"%PDF-2"))
        self.assertEqual([], problems)
        self.assertTrue(any("normalised content is identical" in note for note in notes))
        self.assertTrue(any("informational" in note for note in notes))

    def test_geometry_difference_fails(self) -> None:
        problems, _ = compare_builds(self._build("a"), self._build("b", x="100"))
        self.assertTrue(any("b-F_Cu.gbr" in p and "content differs" in p for p in problems))

    def test_identity_mismatches_fail(self) -> None:
        for field, value in (("image", image_for("10").reference), ("source_commit", "def"), ("variant", "RELEASED"), ("image", ""), ("image_id", "")):
            with self.subTest(field=field, value=value):
                problems, _ = compare_builds(self._build(f"a{field}{value[:3]}"), self._build(f"b{field}{value[:3]}", **{field: value}))
                self.assertTrue(problems)

    def test_image_id_form_may_differ_between_docker_stores(self) -> None:
        problems, _ = compare_builds(self._build("a"), self._build("b", image_id="sha256:config-digest"))
        self.assertEqual([], problems)

    def test_uncommitted_build_fails(self) -> None:
        problems, _ = compare_builds(self._build("a"), self._build("b", source_modified=["board.kicad_pcb"]))
        self.assertTrue(any("uncommitted" in p for p in problems))

    def test_worktree_stage_build_fails(self) -> None:
        problems, _ = compare_builds(self._build("a"), self._build("b", stage={"head": "abc", "worktree": True}))
        self.assertTrue(any("uncommitted working tree" in p for p in problems))

    def test_missing_evidence_is_an_error(self) -> None:
        (self.tmp / "empty").mkdir()
        with self.assertRaises(ContainerError):
            compare_builds(self._build("a"), self.tmp / "empty")

    def test_cli_exit_codes(self) -> None:
        self.assertEqual(0, container.main(["compare", str(self._build("a")), str(self._build("b"))]))
        self.assertEqual(1, container.main(["compare", str(self._build("c")), str(self._build("d", x="5"))]))


if __name__ == "__main__":
    unittest.main()
