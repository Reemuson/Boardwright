# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Commit-message convention: suggestions, advisory checks and generated commits."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_revision_model import _rmtree  # noqa: E402

from boardwright.commit_messages import SUBJECT_LIMIT, subject_warnings, suggest_from_paths  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class SuggestionTests(unittest.TestCase):
    def test_single_area_gets_type_and_scope(self) -> None:
        self.assertEqual("chore(pcb): update the PCB layout", suggest_from_paths(["FIC-100A.kicad_pcb"]))
        self.assertEqual("chore(schematic): update the schematic", suggest_from_paths(["a.kicad_sch", "b.kicad_sch"]))
        self.assertEqual("ci(workflows): update GitHub workflows", suggest_from_paths([".github/workflows/x.yaml"]))
        self.assertEqual("chore(payload): update the Boardwright payload", suggest_from_paths(["src/boardwright/cli.py"]))

    def test_no_suggestion_repeats_its_type_as_scope(self) -> None:
        self.assertEqual("docs: update documentation", suggest_from_paths(["README.md"]))
        samples = (["README.md"], [".github/workflows/x.yaml"], ["tests/test_x.py"], ["CHANGELOG.md"], ["LICENSE"],
                   [".boardwright/templates/fabrication_notes.txt"], ["Templates/A.kicad_wks"], ["x.kicad_pcb"])
        for paths in samples:
            self.assertEqual([], subject_warnings(suggest_from_paths(paths)), paths)

    def test_project_metadata_is_not_called_template(self) -> None:
        self.assertEqual(
            "chore(project): update project configuration", suggest_from_paths([".boardwright/project.yaml"])
        )

    def test_mixed_areas_omit_the_scope(self) -> None:
        self.assertEqual("chore: update project files", suggest_from_paths(["board.kicad_pcb", "docs/SPEC.md"]))

    def test_change_text_keeps_acronyms_and_drops_the_full_stop(self) -> None:
        self.assertEqual("chore(pcb): PCB rev B outline", suggest_from_paths(["b.kicad_pcb"], "PCB rev B outline."))
        self.assertEqual("chore(pcb): move C12 away from U3", suggest_from_paths(["b.kicad_pcb"], "Move C12 away from U3"))


class ChangedPathsTests(unittest.TestCase):
    """Suggestions use the real paths Git reports, first file included."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="bw-paths-"))
        git = lambda *a: subprocess.run(["git", "-C", str(self.tmp), *a], check=True, capture_output=True)
        self.git = git
        git("init", "-q", "-b", "main")
        (self.tmp / ".github" / "workflows").mkdir(parents=True)
        for name in (".github/workflows/a.yaml", "CHANGELOG.md", "Block Diagram.kicad_sch", "old.md"):
            (self.tmp / name).write_text("x\n", encoding="utf-8", newline="\n")
        git("add", "-A")
        git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "base")

    def tearDown(self) -> None:
        _rmtree(self.tmp)

    def _touch(self, name: str) -> None:
        with (self.tmp / name).open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("y\n")

    def test_single_unstaged_file_keeps_its_first_character(self) -> None:
        from boardwright.commit_messages import suggest_commit_message
        from boardwright.git_ops import changed_paths

        self._touch(".github/workflows/a.yaml")
        self.assertEqual([".github/workflows/a.yaml"], changed_paths(self.tmp))
        self.assertEqual("ci(workflows): update GitHub workflows", suggest_commit_message(self.tmp))
        subprocess.run(["git", "-C", str(self.tmp), "checkout", "-q", "--", "."], check=True)
        self._touch("CHANGELOG.md")
        self.assertEqual("docs(changelog): update the changelog", suggest_commit_message(self.tmp))

    def test_paths_with_spaces_and_renames(self) -> None:
        from boardwright.git_ops import changed_paths

        self._touch("Block Diagram.kicad_sch")
        self.git("mv", "old.md", "new.md")
        (self.tmp / "untracked file.txt").write_text("z\n", encoding="utf-8")
        self.assertEqual({"Block Diagram.kicad_sch", "new.md", "untracked file.txt"}, set(changed_paths(self.tmp)))


class AdvisoryTests(unittest.TestCase):
    def test_conforming_subjects_have_no_notes(self) -> None:
        for subject in (
            "fix(release): keep issued drawing revision rows unchanged",
            "docs: describe the review gate",
            "release: prepare 0.1.7 (RELEASED)",
            "feat(new-area): accept a scope that is not in the examples",
        ):
            self.assertEqual([], subject_warnings(subject), subject)

    def test_notes_are_advisory(self) -> None:
        notes = subject_warnings("Add a thing.")
        self.assertIn("subject is not in the form type(scope): summary", notes)
        long = "fix(release): " + "x" * SUBJECT_LIMIT
        self.assertTrue(any("characters" in n for n in subject_warnings(long)))
        self.assertTrue(any("not one of" in n for n in subject_warnings("wip: half done")))

    def test_merges_and_reverts_are_left_alone(self) -> None:
        self.assertEqual([], subject_warnings("Merge pull request #1 from someone/branch"))
        self.assertEqual([], subject_warnings('Revert "fix: something"'))


def _steps(workflow: str) -> dict[str, dict]:
    data = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8"))
    return {step.get("name"): step for job in data["jobs"].values() for step in job["steps"]}


def _commit_command(script: str) -> str:
    start = script.index("# Trailers")
    end = script.index("git push", start)
    return script[start:end]


@unittest.skipUnless(shutil.which("bash"), "needs bash (Git Bash on Windows)")
class GeneratedCommitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="bw-commitmsg-"))
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.tmp)], check=True)
        (self.tmp / "f").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.tmp), "add", "f"], check=True)

    def tearDown(self) -> None:
        _rmtree(self.tmp)

    def _run(self, snippet: str, env: dict[str, str]) -> str:
        script = "set -euo pipefail\n" + snippet
        full_env = {**__import__("os").environ, **env, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
        subprocess.run(["bash", "-c", script], cwd=self.tmp, env=full_env, check=True, capture_output=True)
        return subprocess.run(["git", "-C", str(self.tmp), "log", "-1", "--format=%B"], capture_output=True, text=True, check=True).stdout

    def _trailers(self, message: str) -> dict[str, str]:
        parsed = subprocess.run(["git", "interpret-trailers", "--parse"], input=message, capture_output=True, text=True, check=True).stdout
        return dict(line.split(": ", 1) for line in parsed.splitlines())

    def _release_snippet(self) -> str:
        script = _steps("prepare-release.yaml")["Commit accepted release state"]["run"]
        return 'version="0.1.7"; variant="RELEASED"\n' + _commit_command(script)

    def test_approved_release_commit_names_source_and_reviewed_run(self) -> None:
        env = {"BOARDWRIGHT_SOURCE_SHA": "f" * 40, "IN_APPROVED_REVIEW_RUN": "44644257445",
               "GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "o/r"}
        message = self._run(self._release_snippet(), env)
        self.assertEqual("release: prepare 0.1.7 (RELEASED)", message.splitlines()[0])
        self.assertEqual(
            {"Source-Commit": "f" * 40, "Reviewed-Run": "https://github.com/o/r/actions/runs/44644257445"},
            self._trailers(message),
        )

    def test_unreviewed_release_commit_has_no_reviewed_run(self) -> None:
        env = {"BOARDWRIGHT_SOURCE_SHA": "f" * 40, "IN_APPROVED_REVIEW_RUN": "",
               "GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "o/r"}
        self.assertEqual({"Source-Commit": "f" * 40}, self._trailers(self._run(self._release_snippet(), env)))

    def test_accept_merge_has_an_explicit_message(self) -> None:
        script = next(step["run"] for step in _steps("main-outputs.yaml").values() if "git merge" in step.get("run", ""))
        lines = script.splitlines()
        start = next(i for i, line in enumerate(lines) if line.strip().startswith("git merge"))
        end = start
        while lines[end].rstrip().endswith("\\"):
            end += 1
        merge = "\n".join(line.strip() for line in lines[start:end + 1])
        self.assertNotIn("--no-edit", merge)
        subprocess.run(["git", "-C", str(self.tmp), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "commit", "-q", "-m", "base"], check=True)
        subprocess.run(["git", "-C", str(self.tmp), "switch", "-q", "-c", "dev"], check=True)
        (self.tmp / "g").write_text("y\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.tmp), "add", "g"], check=True)
        subprocess.run(["git", "-C", str(self.tmp), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "commit", "-q", "-m", "feat: g"], check=True)
        source = subprocess.run(["git", "-C", str(self.tmp), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        subprocess.run(["git", "-C", str(self.tmp), "switch", "-q", "main"], check=True)
        message = self._run(f'source_sha="{source}"\n' + merge,
                            {"IN_SOURCE_REF_OR_GH_REF_NAME": "dev", "IN_VARIANT_OR_CHECKED": "CHECKED"})
        self.assertEqual(f"chore(outputs): accept {source[:12]} from dev", message.splitlines()[0])
        self.assertEqual({"Source-Commit": source, "Variant": "CHECKED"}, self._trailers(message))
        self.assertEqual([], subject_warnings(message))


if __name__ == "__main__":
    unittest.main()
