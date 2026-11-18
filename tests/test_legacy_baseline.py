# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Audit F19: a legacy baseline (no committed manifest) must be verified against
the authenticated published package, never passed with a warning."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from boardwright import output_manifest
from boardwright.legacy_baseline import LegacyBaselineError, fetch_published_package

REPO = "owner/board"
TAG = "0.1.3"
COMMIT = "f" * 40
NOTES = "Manufacturing/Fabrication/X-fabrication_notes.txt"
GERBER = "Manufacturing/Fabrication/Gerbers/X-F_Cu.gbr"


def package(files: dict[str, str], root: str = "boardwright-release") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in files.items():
            archive.writestr(f"{root}/{name}" if root else name, text)
    return buffer.getvalue()


APPROVED = {NOTES: "TRACE TO TRACE 0.200 mm\n", GERBER: "%FSLAX46Y46*%\nD10*\nM02*\n"}


class FakeGitHub:
    def __init__(self, data: bytes, **overrides) -> None:
        self.data = data
        self.served = overrides.pop("served", data)
        self.release = {
            "draft": False,
            "author": {"login": "github-actions[bot]"},
            "assets": [
                {
                    "name": f"boardwright-{TAG}-release.zip",
                    "state": "uploaded",
                    "uploader": {"login": "github-actions[bot]"},
                    "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                }
            ],
        }
        self.release.update(overrides.pop("release", {}))
        for key, value in overrides.pop("asset", {}).items():
            self.release["assets"][0][key] = value
        self.remote_commit = overrides.pop("remote_commit", COMMIT)
        self.ancestor = overrides.pop("ancestor", True)
        self.local_tag = overrides.pop("local_tag", True)

    def __call__(self, command: list[str], cwd: Path) -> bytes:
        if command[:2] == ["gh", "api"] and command[2].endswith(f"releases/tags/{TAG}"):
            return json.dumps(self.release).encode()
        if command[:2] == ["gh", "api"] and command[2].endswith(f"git/ref/tags/{TAG}"):
            return json.dumps({"object": {"type": "commit", "sha": self.remote_commit}}).encode()
        if command[:2] == ["git", "rev-parse"]:
            if not self.local_tag:
                raise LegacyBaselineError("unknown revision")
            return (COMMIT + "\n").encode()
        if command[:2] == ["git", "merge-base"]:
            if not self.ancestor:
                raise LegacyBaselineError("not ancestor")
            return b""
        if command[:3] == ["gh", "release", "download"]:
            directory = Path(command[command.index("--dir") + 1])
            (directory / command[command.index("--pattern") + 1]).write_bytes(self.served)
            return b""
        raise AssertionError(f"unexpected command {command}")


class FetchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="bw-legacy-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fetch(self, github: FakeGitHub, repository: str = REPO):
        return fetch_published_package(self.tmp, repository, TAG, self.tmp / "dl", run=github)

    def assertRejected(self, github: FakeGitHub, needle: str, repository: str = REPO) -> None:
        with self.assertRaises(LegacyBaselineError) as raised:
            self.fetch(github, repository)
        self.assertIn(needle, str(raised.exception))

    def test_authenticated_package_is_extracted(self) -> None:
        root, provenance = self.fetch(FakeGitHub(package(APPROVED)))
        self.assertEqual(APPROVED[NOTES], (root / NOTES).read_text(encoding="utf-8"))
        self.assertEqual(COMMIT, provenance.tag_commit)
        self.assertTrue(provenance.digest.startswith("sha256:"))

    def test_unknown_repository(self) -> None:
        self.assertRejected(FakeGitHub(package(APPROVED)), "Repository", repository="")

    def test_draft_release(self) -> None:
        self.assertRejected(FakeGitHub(package(APPROVED), release={"draft": True}), "draft")

    def test_release_not_made_by_workflow(self) -> None:
        self.assertRejected(FakeGitHub(package(APPROVED), release={"author": {"login": "someone"}}), "not the release workflow")

    def test_asset_uploaded_by_a_person(self) -> None:
        self.assertRejected(FakeGitHub(package(APPROVED), asset={"uploader": {"login": "someone"}}), "uploaded by 'someone'")

    def test_missing_package_asset(self) -> None:
        self.assertRejected(FakeGitHub(package(APPROVED), asset={"name": "other.zip"}), "expected exactly one")

    def test_missing_digest_cannot_be_authenticated(self) -> None:
        self.assertRejected(FakeGitHub(package(APPROVED), asset={"digest": None}), "cannot be authenticated")

    def test_downloaded_bytes_must_match_digest(self) -> None:
        tampered = package({**APPROVED, NOTES: "TRACE TO TRACE 0.600 mm\n"})
        self.assertRejected(FakeGitHub(package(APPROVED), served=tampered), "does not match GitHub digest")

    def test_moved_tag(self) -> None:
        self.assertRejected(FakeGitHub(package(APPROVED), remote_commit="e" * 40), "the tag moved")

    def test_tag_not_in_history(self) -> None:
        self.assertRejected(FakeGitHub(package(APPROVED), ancestor=False), "not an ancestor")

    def test_tag_not_fetched(self) -> None:
        self.assertRejected(FakeGitHub(package(APPROVED), local_tag=False), "not available locally")

    def test_unsafe_or_foreign_archive_paths(self) -> None:
        self.assertRejected(FakeGitHub(package({"../evil.txt": "x"})), "unsafe path")
        self.assertRejected(FakeGitHub(package(APPROVED, root="other")), "outside boardwright-release")
        self.assertRejected(FakeGitHub(package({"README.md": "x"})), "no boardwright-release/Manufacturing")


class CheckBaselineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="bw-check-baseline-"))  # not a Git repo: no committed manifest
        self.record = SimpleNamespace(
            version="0.1.4",
            previous_version=TAG,
            fabrication_baseline=SimpleNamespace(id="A/1", status="reused"),
            assembly_baseline=SimpleNamespace(id="A", status="new"),
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def write_outputs(self, files: dict[str, str]) -> None:
        for name, text in files.items():
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text(text, encoding="utf-8")

    def check(self, fetch) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("boardwright.release_ledger.load_ledger", return_value=[self.record]), \
                mock.patch("boardwright.legacy_baseline.fetch_published_package", side_effect=fetch), \
                redirect_stdout(out), redirect_stderr(err):
            code = output_manifest._check_baseline(self.root, REPO)
        return code, out.getvalue() + err.getvalue()

    def published(self, files: dict[str, str]):
        def fetch(root, repository, tag, destination):
            return FetchTests.fetch(SimpleNamespace(tmp=destination), FakeGitHub(package(files)))
        return fetch

    def test_unauthenticated_package_fails_closed(self) -> None:
        self.write_outputs(APPROVED)

        def fail(*_args):
            raise LegacyBaselineError("no digest")

        code, text = self.check(fail)
        self.assertEqual(2, code)
        self.assertIn("cannot be verified", text)

    def test_matching_published_package_passes(self) -> None:
        self.write_outputs(APPROVED)
        code, text = self.check(self.published(APPROVED))
        self.assertEqual(0, code, text)
        self.assertIn("sha256 verified", text)

    def test_changed_fabrication_notes_block_reuse(self) -> None:
        self.write_outputs({**APPROVED, NOTES: "TRACE TO TRACE 0.600 mm\n"})
        code, text = self.check(self.published(APPROVED))
        self.assertEqual(1, code)
        self.assertIn(NOTES, text)

    # F20: a re-issued fabrication baseline reports exactly what changed.
    def reissue(self) -> None:
        self.record.fabrication_baseline = SimpleNamespace(id="A/2", status="reissued")
        self.record.pcb_revision = "A"

    def test_reissue_lists_changed_fabrication_layers(self) -> None:
        self.reissue()
        silk = "Manufacturing/Fabrication/Gerbers/X-F_Silkscreen.gbr"
        approved = {**APPROVED, silk: "%FSLAX46Y46*%\nD10*\nM02*\n"}
        self.write_outputs({**approved, silk: "%FSLAX46Y46*%\nD10*\nG36*\nG37*\nM02*\n"})
        code, text = self.check(self.published(approved))
        self.assertEqual(0, code, text)
        self.assertIn("re-issued as A/2 under PCB revision A", text)
        self.assertIn(f"reissue {silk}: content differs", text)
        self.assertNotIn(f"reissue {GERBER}", text)
        self.assertIn("2 other controlled fabrication file(s) are identical", text)

    def test_reissue_keeps_newly_declared_notes_promise(self) -> None:
        self.reissue()
        silk = "Manufacturing/Fabrication/Gerbers/X-F_Silkscreen.gbr"
        approved = {**APPROVED, silk: "%FSLAX46Y46*%\nD10*\nM02*\n"}
        changed_silk = {**approved, silk: "%FSLAX46Y46*%\nD10*\nG36*\nG37*\nM02*\n"}
        with mock.patch("boardwright.impact._only_newly_declared_requirements", return_value=("tg_rating",)):
            self.write_outputs(changed_silk)
            code, text = self.check(self.published(approved))
            self.assertEqual(0, code, text)
            self.write_outputs({**changed_silk, NOTES: "TRACE TO TRACE 0.600 mm\n"})
            code, text = self.check(self.published(approved))
        self.assertEqual(1, code, text)
        self.assertIn(f"ERROR {NOTES}", text)
        self.assertIn("only newly declared", text)

    def test_reissue_with_no_fabrication_change_warns(self) -> None:
        self.reissue()
        self.write_outputs(APPROVED)
        code, text = self.check(self.published(APPROVED))
        self.assertEqual(0, code, text)
        self.assertIn("the re-issue changes nothing", text)

    def test_reissue_still_blocks_reused_assembly_changes(self) -> None:
        self.reissue()
        self.record.assembly_baseline = SimpleNamespace(id="A", status="reused")
        bom = "Manufacturing/Assembly/X-bom.csv"
        self.write_outputs({**APPROVED, bom: "R1,10k\n"})
        code, text = self.check(self.published({**APPROVED, bom: "R1,1k\n"}))
        self.assertEqual(1, code, text)
        self.assertIn(bom, text)

    def test_reissue_needs_the_previous_package(self) -> None:
        self.reissue()
        self.write_outputs(APPROVED)

        def fail(*_args):
            raise LegacyBaselineError("no digest")

        code, _text = self.check(fail)
        self.assertEqual(2, code)

    def test_package_without_controlled_outputs_fails(self) -> None:
        self.write_outputs(APPROVED)
        code, text = self.check(self.published({"Manufacturing/Fabrication/readme.pdf": "x"}))
        self.assertEqual(2, code)
        self.assertIn("no controlled", text)



class WorkflowTests(unittest.TestCase):
    def test_baseline_step_can_fetch_published_packages(self) -> None:
        import yaml

        path = Path(__file__).resolve().parents[1] / ".github/workflows/prepare-release.yaml"
        jobs = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]
        steps = next(iter(jobs.values()))["steps"]
        step = next(s for s in steps if s.get("name") == "Check reused baselines against the previous release")
        self.assertIn("check-baseline", step["run"])
        self.assertIn("set -o pipefail", step["run"])  # a failing check fails the step despite tee
        self.assertEqual("${{ github.token }}", step["env"]["GH_TOKEN"])
        checkout = next(s for s in steps if "actions/checkout" in str(s.get("uses")))
        self.assertEqual(0, checkout["with"]["fetch-depth"])  # tags and ancestry available


if __name__ == "__main__":
    unittest.main()
