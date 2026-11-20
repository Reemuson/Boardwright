# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Local release publication from the tag into the release store; two consecutive releases."""

from __future__ import annotations

import json
import os
import stat
import unittest
from pathlib import Path
from unittest import mock

from boardwright import local_publish, release_store
from boardwright.local_release import LocalReleaseError

try:
    from tests.local_release_fixtures import Case, gerber
    from tests.test_local_release import _git, _write, snapshot
except ImportError:  # unittest discover -s tests
    from local_release_fixtures import Case, gerber
    from test_local_release import _git, _write, snapshot

class PublishTests(Case):
    def test_publish_from_the_tag_into_the_store(self) -> None:
        record = self.approved()
        self.finalize()
        before = snapshot(self.repo)
        publication = self.publish()
        entry = self.store.entry_path("releases", "1.0.0")
        self.assertEqual([], self.store.verify_entry(entry))
        self.assertEqual(("local", False, False), (publication["authority"], publication["server_enforced"],
                                                   publication["authenticated"]))
        self.assertEqual(self.head("1.0.0^{commit}"), publication["release_commit"])
        self.assertEqual(self.head("refs/tags/1.0.0"), publication["tag_object"])
        names = {p.name for p in entry.iterdir()}
        self.assertTrue({"boardwright-1.0.0-release.zip", "MANIFEST.txt", "RELEASE_IDENTITY.json",
                         "ACCEPTED_OUTPUT_MANIFEST.json", "RELEASE_NOTES.md", "publish.json", "SHA256SUMS"} <= names)
        notes = (entry / "RELEASE_NOTES.md").read_text(encoding="utf-8")
        self.assertIn("local and not server-enforced", notes)
        self.assertIn("asserted, not authenticated", notes)
        self.assertEqual(_git(self.repo, "show", "1.0.0:.boardwright/accepted_manifest.json").encode(),
                         (entry / "ACCEPTED_OUTPUT_MANIFEST.json").read_bytes())
        identity = json.loads((entry / "RELEASE_IDENTITY.json").read_text(encoding="utf-8"))
        self.assertEqual((record["source_sha"], "1.0.0"), (identity["source_commit"], identity["version"]))
        self.assertEqual(before, snapshot(self.repo))
        self.assertIsNotNone(local_publish.load_release(self.store, "1.0.0"))

    def test_duplicate_publication_is_a_no_op_and_tampering_is_detected(self) -> None:
        self.approved()
        self.finalize()
        self.publish()
        builds, events = self.engine.builds, self.store.events()
        self.publish()
        self.assertEqual((builds, events), (self.engine.builds, self.store.events()))
        package = self.store.entry_path("releases", "1.0.0") / "boardwright-1.0.0-release.zip"
        os.chmod(package, stat.S_IWRITE | stat.S_IREAD)
        package.write_bytes(package.read_bytes() + b"tampered")
        with self.assertRaisesRegex(LocalReleaseError, "modified"):
            local_publish.load_release(self.store, "1.0.0")
        with self.assertRaisesRegex(LocalReleaseError, "modified"):
            self.publish()

    def test_publish_needs_a_finalised_release(self) -> None:
        self.approved()
        with self.assertRaisesRegex(LocalReleaseError, "not finalised"):
            self.publish()
        self.assertEqual([], self.store.entries("releases"))

    def test_outputs_that_differ_from_the_tagged_manifest_are_refused(self) -> None:
        self.approved()
        self.finalize()

        def different(kicad, workdir, command, environment=None):
            _write(Path(workdir) / "Manufacturing" / "Fabrication" / "Gerbers" / "board-F_Cu.gbr", gerber("changed"))
            return 0

        with self.assertRaisesRegex(LocalReleaseError, "differ from the accepted manifest"):
            local_publish.publish(self.config, self.store, "1.0.0", runner=different, echo=self.out.append)
        self.assertEqual([], self.store.entries("releases"))

    def test_failed_package_write_publishes_nothing_and_can_be_retried(self) -> None:
        self.approved()
        self.finalize()
        # Fails inside the store write: files copied to the temporary, not yet renamed.
        with mock.patch.object(release_store, "_read_only", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.publish()
        self.assertEqual(([], []), (self.store.entries("releases"), self.store.abandoned()))
        self.assertNotIn("published", self.events())
        self.publish()
        self.assertIsNotNone(local_publish.load_release(self.store, "1.0.0"))


class TwoReleaseTests(Case):
    def test_two_consecutive_local_releases(self) -> None:
        first = self.approved()
        self.finalize()
        self.publish()
        # A fabrication change on top of the first release.
        _write(self.repo / "board.kicad_pcb", "(kicad_pcb rev A moved trace)\n")
        _git(self.repo, "commit", "-q", "-am", "fix(pcb): move a trace")
        second = self.approved("1.1.0", classes="fabrication")
        self.finalize("1.1.0")
        self.publish("1.1.0")
        self.assertEqual(self.head("1.0.0^{commit}"), self.head("1.1.0^{commit}~2"))
        ledger = _git(self.repo, "show", "1.1.0:.boardwright/releases.yaml")
        self.assertIn("version: 1.0.0", ledger)
        self.assertIn("previous_version: '1.0.0'", ledger)
        self.assertNotEqual(first["manifest_sha256"], second["manifest_sha256"])
        for version in ("1.0.0", "1.1.0"):
            self.assertIsNotNone(local_publish.load_release(self.store, version))
        self.assertEqual([], self.store.verify_index())


if __name__ == "__main__":
    unittest.main()
