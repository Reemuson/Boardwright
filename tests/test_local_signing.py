# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Signed local releases: the signer must be the recorded approver (audit F-R7).

Real Git and ``ssh-keygen`` in disposable repositories and stores; only the
container is replaced (``local_release_fixtures``).
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import unittest
from pathlib import Path

from boardwright import local_finalize, local_recovery
from boardwright.local_release import LocalReleaseError, verify_approval
from boardwright.local_signing import SigningError, principals_for, release_signatures, signature_key

try:
    from tests.local_release_fixtures import Case
    from tests.test_local_release import _git
except ImportError:  # unittest discover -s tests
    from local_release_fixtures import Case
    from test_local_release import _git

APPROVER = "reviewer@example.invalid"  # the fixture's Git identity, recorded as the approver
OTHER = "someone-else@example.invalid"


@unittest.skipUnless(shutil.which("ssh-keygen"), "ssh-keygen is needed for SSH signing")
class SigningTests(Case):
    def setUp(self) -> None:
        super().setUp()
        self.public = self.key("signing key")
        self.allowed = self.base / "allowed_signers"
        self.allow(f"{APPROVER} {self.text(self.public)}")
        for name, value in (("gpg.format", "ssh"), ("user.signingkey", str(self.public)),
                            ("gpg.ssh.allowedSignersFile", str(self.allowed))):
            _git(self.repo, "config", name, value)

    # -- helpers ---------------------------------------------------------------
    def key(self, name: str) -> Path:
        path = self.base / name
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(path)], check=True,
                       capture_output=True)
        return Path(str(path) + ".pub")

    @staticmethod
    def text(public: Path) -> str:
        return public.read_text(encoding="utf-8").strip()

    def allow(self, *lines: str) -> None:
        self.allowed.write_text("".join(line + "\n" for line in lines), encoding="utf-8")

    def report(self, version: str = "1.0.0", approval: dict | None = None):
        _path, finalization = local_finalize.load_finalization(self.store, version)
        return release_signatures(self.repo, version, finalization, approval or verify_approval(self.store, version)[3])

    def signed_release(self) -> dict:
        record = self.approved()
        self.finalize(sign=True)
        return record

    def assert_refused_before_build(self, pattern: str) -> None:
        builds = self.engine.builds
        with self.assertRaisesRegex(SigningError, pattern):
            self.finalize(sign=True)
        self.assertEqual((builds, [], ""), (self.engine.builds, self.store.entries("finalizations"),
                                            _git(self.repo, "tag")))

    def status(self) -> str:
        return "\n".join(local_recovery.status_lines(self.repo, self.store))

    # -- 1 and 6: the approver's own key -------------------------------------------
    def test_valid_signature_by_the_approver_finalises_publishes_and_repeats_safely(self) -> None:
        self.signed_release()
        report = self.report()
        self.assertEqual((True, True, True, False, []), (report.signed, report.signature_valid,
                                                         report.signer_matches_approver, report.authenticated,
                                                         report.problems))
        self.assertIn(f"signer matches approver {APPROVER} yes; authenticated no", self.status())
        self.assertIn("not authenticated", self.out[-2])
        builds, refs = self.engine.builds, _git(self.repo, "for-each-ref")
        self.finalize(sign=True)  # repeated after verification: nothing rebuilt or changed
        self.assertEqual((builds, refs), (self.engine.builds, _git(self.repo, "for-each-ref")))
        publication = self.publish()
        self.assertEqual({"signed": True, "approver": APPROVER, "signature_valid": True,
                          "signer_matches_approver": True, "authenticated": False}, publication["signature"])
        notes = (self.store.root / "releases" / "1.0.0" / "RELEASE_NOTES.md").read_text(encoding="utf-8")
        self.assertIn("lists for the approver alone", notes)
        self.assertIn("**not authenticated**", notes)
        builds = self.engine.builds
        self.publish()  # repeated after verification: no rebuild, nothing new
        self.assertEqual((builds, refs), (self.engine.builds, _git(self.repo, "for-each-ref")))

    # -- 2: a different allowed signer ----------------------------------------------
    def test_a_valid_signature_by_another_allowed_signer_is_refused(self) -> None:
        self.allow(f"{OTHER} {self.text(self.public)}")
        self.approved()
        self.assert_refused_before_build(f"listed for {OTHER} in the allowed-signers file, not for the approver")
        # After a correct release, a key that now stands for someone else is refused everywhere.
        self.allow(f"{APPROVER} {self.text(self.public)}")
        self.finalize(sign=True)
        self.allow(f"{OTHER} {self.text(self.public)}")
        report = self.report()
        self.assertEqual((True, False), (report.signature_valid, report.signer_matches_approver))
        self.assertTrue(all(f"listed for {OTHER}" in p for p in report.problems), report.problems)
        self.assertIn(f"signer matches approver {APPROVER} NO", self.status())
        with self.assertRaisesRegex(LocalReleaseError, f"listed for {OTHER}"):
            self.finalize(sign=True)
        with self.assertRaisesRegex(LocalReleaseError, "Nothing was published"):
            self.publish()
        self.assertEqual([], self.store.entries("releases"))

    # -- 3: the key is not an allowed signer ---------------------------------------------
    def test_a_key_absent_from_the_allowed_signers_is_refused(self) -> None:
        other = self.key("other key")
        self.allow(f"{APPROVER} {self.text(other)}")
        self.approved()
        self.assert_refused_before_build("listed for no principal")
        self.allow(f"{APPROVER} {self.text(self.public)}")
        self.finalize(sign=True)
        self.allow(f"{APPROVER} {self.text(other)}")
        report = self.report()
        self.assertEqual((True, False), (report.signature_valid, report.signer_matches_approver))
        with self.assertRaisesRegex(LocalReleaseError, "Nothing was published"):
            self.publish()

    # -- 4: a corrupted signature -------------------------------------------------------
    def test_a_corrupted_tag_signature_is_not_valid(self) -> None:
        self.signed_release()
        raw = _git(self.repo, "cat-file", "tag", "1.0.0")
        head, marker, signature = raw.partition("-----BEGIN SSH SIGNATURE-----")
        lines = signature.split("\n")
        middle = len(lines) // 2
        lines[middle] = lines[middle][:10] + ("A" if lines[middle][10] != "A" else "B") + lines[middle][11:]
        forged = subprocess.run(["git", "mktag"], cwd=self.repo, input=(head + marker + "\n".join(lines)).encode(),
                                capture_output=True, check=True).stdout.decode().strip()
        _git(self.repo, "update-ref", "refs/tags/1.0.0", forged, _git(self.repo, "rev-parse", "refs/tags/1.0.0").strip())
        report = self.report()
        self.assertEqual((False, False), (report.signature_valid, report.signer_matches_approver))
        self.assertTrue(any("not cryptographically valid" in p for p in report.problems), report.problems)
        self.assertIn("signature: valid NO", self.status())
        with self.assertRaisesRegex(LocalReleaseError, "Nothing was published"):
            self.publish()

    # -- 5: ambiguous identity mappings -----------------------------------------------------
    def test_several_principals_patterns_and_options_fail_closed(self) -> None:
        key = self.text(self.public)
        blob = signature_key_from_public(self.public)
        cases = {
            "two principals on one line": [f"{APPROVER},{OTHER} {key}"],
            "two lines for the same key": [f"{APPROVER} {key}", f"{OTHER} {key}"],
            "a principal pattern": [f"*@example.invalid {key}"],
            "a negated pattern": [f"{APPROVER},!{OTHER} {key}"],
            "cert-authority": [f"{APPROVER} cert-authority {key}"],
            "a validity option": [f'{APPROVER} valid-before="20991231" {key}'],
        }
        for name, lines in cases.items():
            with self.subTest(name):
                self.allow(*lines)
                try:
                    principals = principals_for(self.allowed, blob)
                except SigningError:
                    continue  # refused outright: patterns and options
                self.assertNotEqual([APPROVER], sorted(set(principals)), name)
        self.allow(f'{APPROVER} namespaces="git" {key}')
        self.assertEqual([APPROVER], principals_for(self.allowed, blob))
        self.allow(f"{APPROVER},{OTHER} {key}")
        self.approved()
        self.assert_refused_before_build(f"listed for {APPROVER}, {OTHER} in the allowed-signers file")

    # -- 7: missing or altered approver identity ---------------------------------------------
    def test_a_missing_or_different_approver_identity_fails_closed(self) -> None:
        self.signed_release()
        approval = verify_approval(self.store, "1.0.0")[3]
        missing = self.report(approval={**approval, "approver": "Rev Iewer"})
        self.assertEqual((True, False), (missing.signed, missing.signer_matches_approver))
        self.assertIn("names no approver e-mail", missing.problems[0])
        altered = self.report(approval={**approval, "approver": f"Someone <{OTHER}>"})
        self.assertFalse(altered.signer_matches_approver)
        self.assertTrue(altered.problems)
        # An approval altered in the store is refused before any signature is considered.
        entry = next((self.store.root / "approvals" / "1.0.0").iterdir())
        path = entry / "approval.json"
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        path.write_text(path.read_text(encoding="utf-8").replace(APPROVER, OTHER), encoding="utf-8")
        with self.assertRaisesRegex(LocalReleaseError, "modified"):
            self.publish()

    # -- 8: unsigned releases ----------------------------------------------------------------
    def test_an_unsigned_release_keeps_its_unauthenticated_labels(self) -> None:
        self.approved()
        self.finalize()
        report = self.report()
        self.assertEqual((False, None, None, False, []), (report.signed, report.signature_valid,
                                                          report.signer_matches_approver, report.authenticated,
                                                          report.problems))
        self.assertIn("signature: unsigned (local approval, unauthenticated)", self.status())
        with self.assertRaisesRegex(LocalReleaseError, "started without --sign"):
            self.finalize(sign=True)  # never upgraded on resume
        publication = self.publish()
        self.assertEqual({"signed": False, "approver": "", "signature_valid": None, "signer_matches_approver": None,
                          "authenticated": False}, publication["signature"])
        self.assertEqual((False, False, "local", False),
                         (publication["signed"], publication["authenticated"], publication["authority"],
                          publication["server_enforced"]))
        notes = (self.store.root / "releases" / "1.0.0" / "RELEASE_NOTES.md").read_text(encoding="utf-8")
        self.assertIn("unsigned (the approval is unauthenticated)", notes)

    # -- unsupported formats and configuration ------------------------------------------------
    def test_unsupported_formats_and_missing_configuration_are_refused(self) -> None:
        self.approved()
        for value in ("openpgp", "x509"):
            _git(self.repo, "config", "gpg.format", value)
            self.assert_refused_before_build("SSH signing only")
        _git(self.repo, "config", "gpg.format", "ssh")
        _git(self.repo, "config", "--unset", "gpg.ssh.allowedSignersFile")
        self.assert_refused_before_build("allowedSignersFile is not set")
        _git(self.repo, "config", "gpg.ssh.allowedSignersFile", str(self.base / "missing"))
        self.assert_refused_before_build("does not exist")
        _git(self.repo, "config", "gpg.ssh.allowedSignersFile", str(self.allowed))
        _git(self.repo, "config", "user.signingkey", str(self.base / "missing-key.pub"))
        self.assert_refused_before_build("does not exist")
        _git(self.repo, "config", "--unset", "user.signingkey")
        self.assert_refused_before_build("no signing key")

    def test_a_signed_finalisation_is_never_resumed_unsigned(self) -> None:
        self.signed_release()
        with self.assertRaisesRegex(LocalReleaseError, "started with --sign"):
            self.finalize()  # never downgraded on resume
        self.assertEqual(True, self.report().signer_matches_approver)


def signature_key_from_public(public: Path) -> bytes:
    """The public key blob of a ``.pub`` file (as in an SSHSIG), for ``principals_for``."""

    import base64

    return base64.b64decode(public.read_text(encoding="utf-8").split()[1])


class SignatureKeyTests(unittest.TestCase):
    def test_the_signer_key_is_read_from_the_sshsig_blob(self) -> None:
        if not shutil.which("ssh-keygen"):
            self.skipTest("ssh-keygen is needed")
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            key = Path(temp) / "k"
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True,
                           capture_output=True)
            message = Path(temp) / "m"
            message.write_bytes(b"payload")
            subprocess.run(["ssh-keygen", "-q", "-Y", "sign", "-n", "git", "-f", str(key), str(message)], check=True,
                           capture_output=True)
            armoured = Path(str(message) + ".sig").read_bytes()
            self.assertEqual(signature_key_from_public(Path(str(key) + ".pub")), signature_key(armoured))


if __name__ == "__main__":
    unittest.main()
