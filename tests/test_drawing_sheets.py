# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Active drawing sheets: resolution, adoption, branding and release control."""

from __future__ import annotations

import base64
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from boardwright import drawing_sheets
from boardwright.config import apply_local_overrides, load_config
from boardwright.errors import BoardwrightError
from boardwright.fingerprint import fingerprint_working_tree
from boardwright.worksheet_logo import (
    BOARDWRIGHT_PLACEHOLDER_LOGO_SHA256,
    embed_logo_in_worksheet,
    embedded_png,
    select_logo_targets,
    transplant_bitmaps,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_revision_model import Project, _rmtree  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
SHEET = "Boardwright_Template_PCB_GIT_A4.kicad_wks"
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/l6gK0wAAAABJRU5ErkJggg=="
)
PNG_2X1 = PNG_1X1[:16] + (2).to_bytes(4, "big") + PNG_1X1[20:]

try:
    from compression import zstd as _zstd  # Python 3.14+
except ImportError:  # pragma: no cover - depends on interpreter
    _zstd = None


def _kicad_pro(schematic: str, pcbnew: str) -> str:
    return json.dumps(
        {
            "schematic": {"page_layout_descr_file": schematic},
            "pcbnew": {"page_layout_descr_file": pcbnew},
        },
        indent=2,
    )


def _embedded_files(name: str, content: bytes) -> str:
    data = base64.b64encode(_zstd.compress(content)).decode("ascii")
    return f'\t(embedded_files\n\t\t(file\n\t\t\t(name "{name}")\n\t\t\t(type worksheet)\n\t\t\t(data |{data}|)\n\t\t)\n\t)\n'


def _embed_into(path: Path, name: str, content: bytes) -> None:
    text = path.read_text(encoding="utf-8").rstrip()
    assert text.endswith(")")
    path.write_text(text[:-1] + _embedded_files(name, content) + ")\n", encoding="utf-8")


class SheetProject(Project):
    """The release-model fixture plus a KiCad project and an external sheet."""

    def __init__(self, root: Path) -> None:
        (root / "Templates").mkdir(parents=True)
        shutil.copyfile(REPO / "Templates" / SHEET, root / "Templates" / SHEET)
        shutil.copyfile(REPO / "Templates" / "Boardwright_Template_AS1100.kicad_wks", root / "Templates" / "Other.kicad_wks")
        ref = "${KIPRJMOD}/Templates/" + SHEET
        (root / "board.kicad_pro").write_text(_kicad_pro(ref, ref), encoding="utf-8")
        super().__init__(root)

    def sheet(self) -> Path:
        return self.root / "Templates" / SHEET


class ResolutionTests(unittest.TestCase):
    def test_parse_setting_kinds(self) -> None:
        self.assertEqual("file", drawing_sheets.parse_setting("pcbnew", "${KIPRJMOD}/Templates/a.kicad_wks").kind)
        self.assertEqual("Templates/a.kicad_wks", drawing_sheets.parse_setting("pcbnew", "${KIPRJMOD}/Templates/a.kicad_wks").path)
        embedded = drawing_sheets.parse_setting("schematic", "kicad-embed://a.kicad_wks")
        self.assertEqual(("embedded", "Templates/a.kicad_wks"), (embedded.kind, embedded.counterpart))
        self.assertEqual("relative", drawing_sheets.parse_setting("pcbnew", "Templates/a.kicad_wks").kind)
        self.assertEqual("absolute", drawing_sheets.parse_setting("pcbnew", "C:/x/a.kicad_wks").kind)
        self.assertEqual("unset", drawing_sheets.parse_setting("pcbnew", "").kind)

    def test_fabrication_falls_back_to_template_worksheet(self) -> None:
        refs = {"schematic": drawing_sheets.parse_setting("schematic", ""), "pcbnew": drawing_sheets.parse_setting("pcbnew", "")}
        ref = drawing_sheets.document_sheet("fabrication", refs, "Templates/Custom.kicad_wks")
        self.assertEqual(("file", "Templates/Custom.kicad_wks"), (ref.kind, ref.path))
        self.assertEqual(drawing_sheets.DEFAULT_PCB_SHEET, drawing_sheets.document_sheet("assembly", refs).path)

    def test_bare_relative_pcb_sheet_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "b.kicad_pro").write_text(_kicad_pro("", "Templates/x.kicad_wks"), encoding="utf-8")
            issues = drawing_sheets.sheet_issues(root)
        self.assertEqual(["error"], [issue.level for issue in issues])
        self.assertIn("${KIPRJMOD}/Templates/x.kicad_wks", issues[0].message)


@unittest.skipIf(_zstd is None, "zstd support unavailable")
class AdoptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="bw-sheets-"))
        (self.root / "Templates").mkdir()
        self.content = (REPO / "Templates" / SHEET).read_bytes()
        (self.root / "Templates" / SHEET).write_bytes(self.content)
        embed = "kicad-embed://" + SHEET
        (self.root / "b.kicad_pro").write_text(_kicad_pro(embed, embed), encoding="utf-8")
        for name, head in (("b.kicad_sch", "kicad_sch"), ("b.kicad_pcb", "kicad_pcb")):
            (self.root / name).write_text(f"({head}\n\t(version 20250114)\n)\n", encoding="utf-8")

    def tearDown(self) -> None:
        _rmtree(self.root)

    def test_identical_embedded_sheet_is_adopted_by_setting_only(self) -> None:
        for name in ("b.kicad_sch", "b.kicad_pcb"):
            _embed_into(self.root / name, SHEET, self.content.replace(b"\n", b"\r\n"))
        designs = {name: (self.root / name).read_bytes() for name in ("b.kicad_sch", "b.kicad_pcb")}
        results = drawing_sheets.plan_adoption(self.root)
        self.assertEqual(["adopted", "adopted"], [result.action for result in results])
        # Dry run: nothing written.
        self.assertIn("kicad-embed://", (self.root / "b.kicad_pro").read_text(encoding="utf-8"))
        drawing_sheets.apply_adoption(self.root, results)
        project = json.loads((self.root / "b.kicad_pro").read_text(encoding="utf-8"))
        self.assertEqual("${KIPRJMOD}/Templates/" + SHEET, project["schematic"]["page_layout_descr_file"])
        self.assertEqual("${KIPRJMOD}/Templates/" + SHEET, project["pcbnew"]["page_layout_descr_file"])
        for name, content in designs.items():
            self.assertEqual(content, (self.root / name).read_bytes(), f"{name} must not be rewritten")
        self.assertEqual([], [issue for issue in drawing_sheets.sheet_issues(self.root) if issue.level == "error"])

    def test_diverged_embedded_sheet_is_left_unchanged(self) -> None:
        _embed_into(self.root / "b.kicad_sch", SHEET, self.content.replace(b"(size 2.5 2.5)", b"(size 2 2)", 1))
        _embed_into(self.root / "b.kicad_pcb", SHEET, self.content)
        before = (self.root / "b.kicad_pro").read_bytes()
        results = {result.section: result for result in drawing_sheets.plan_adoption(self.root)}
        self.assertEqual("diverged", results["schematic"].action)
        self.assertEqual("adopted", results["pcbnew"].action)
        drawing_sheets.apply_adoption(self.root, list(results.values()))
        project = json.loads((self.root / "b.kicad_pro").read_text(encoding="utf-8"))
        self.assertEqual("kicad-embed://" + SHEET, project["schematic"]["page_layout_descr_file"])
        self.assertNotEqual(before, (self.root / "b.kicad_pro").read_bytes())

    def test_missing_counterpart_is_not_adopted(self) -> None:
        (self.root / "Templates" / SHEET).unlink()
        _embed_into(self.root / "b.kicad_sch", SHEET, self.content)
        actions = {result.section: result.action for result in drawing_sheets.plan_adoption(self.root)}
        self.assertEqual("missing", actions["schematic"])

    def test_extract_writes_review_copies_outside_templates(self) -> None:
        _embed_into(self.root / "b.kicad_sch", SHEET, self.content)
        written = drawing_sheets.extract_embedded(self.root, self.root / "review")
        self.assertEqual([self.root / "review" / f"schematic__{SHEET}"], written)
        self.assertEqual(self.content, written[0].read_bytes())

    def test_logo_on_embedded_sheet_is_refused(self) -> None:
        _embed_into(self.root / "b.kicad_sch", SHEET, self.content)
        _embed_into(self.root / "b.kicad_pcb", SHEET, self.content)
        (self.root / "logo.png").write_bytes(PNG_1X1)
        config = SimpleNamespace(root=self.root, template={})
        with self.assertRaisesRegex(BoardwrightError, "worksheet adopt"):
            select_logo_targets(config, self.root / "logo.png")

    def test_branding_check_reads_the_embedded_copy(self) -> None:
        _embed_into(self.root / "b.kicad_sch", SHEET, self.content)
        _embed_into(self.root / "b.kicad_pcb", SHEET, self.content)
        (self.root / "logo.png").write_bytes(PNG_1X1)
        # The Templates/ copy carries the project logo, but KiCad renders the embedded one.
        embed_logo_in_worksheet(self.root / "Templates" / SHEET, self.root / "logo.png")
        config = SimpleNamespace(root=self.root, template={"sheet_image": "logo.png"}, template_sheet_image="logo.png")
        issues = drawing_sheets.branding_issues(config)
        self.assertTrue(issues)
        self.assertTrue(all(issue.level == "error" for issue in issues))
        self.assertIn("still renders the Boardwright logo", issues[0].message)


class LogoTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="bw-logo-"))
        (self.root / "Templates").mkdir()
        for name in (SHEET, "Boardwright_Template_AS1100.kicad_wks"):
            shutil.copyfile(REPO / "Templates" / name, self.root / "Templates" / name)
        ref = "${KIPRJMOD}/Templates/" + SHEET
        (self.root / "b.kicad_pro").write_text(_kicad_pro(ref, ref), encoding="utf-8")
        (self.root / "logo.png").write_bytes(PNG_1X1)
        self.config = SimpleNamespace(root=self.root, template={})

    def tearDown(self) -> None:
        _rmtree(self.root)

    def test_default_targets_are_only_the_active_sheets(self) -> None:
        targets = select_logo_targets(self.config, self.root / "logo.png")
        self.assertEqual([(self.root / "Templates" / SHEET).resolve()], [path.resolve() for path in targets])

    def test_output_writes_copies_and_leaves_sources(self) -> None:
        before = (self.root / "Templates" / SHEET).read_bytes()
        copies = select_logo_targets(self.config, self.root / "logo.png", output=self.root / "out")
        self.assertEqual([self.root / "out" / SHEET], copies)
        embed_logo_in_worksheet(copies[0], self.root / "logo.png")
        self.assertEqual(before, (self.root / "Templates" / SHEET).read_bytes())
        self.assertEqual(PNG_1X1, embedded_png(copies[0].read_text(encoding="utf-8")))

    def test_local_override_logo_is_never_written_into_tracked_sheets(self) -> None:
        import subprocess

        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "Templates"], cwd=self.root, check=True)
        config = SimpleNamespace(
            root=self.root,
            template={"sheet_image": "logo.png"},
            template_sheet_image="logo.png",
            local_overrides=("template.sheet_image",),
        )
        with self.assertRaisesRegex(BoardwrightError, "local.yaml"):
            select_logo_targets(config, self.root / "logo.png")
        # Branded personal copies remain possible.
        self.assertTrue(select_logo_targets(config, self.root / "logo.png", output=self.root / "out"))
        # A logo declared in the tracked project.yaml may be embedded (downstream projects).
        declared = SimpleNamespace(root=self.root, template={}, template_sheet_image="logo.png", local_overrides=())
        self.assertTrue(select_logo_targets(declared, self.root / "logo.png"))


class ShippedTemplateTests(unittest.TestCase):
    def test_every_tracked_sheet_carries_only_the_boardwright_logo(self) -> None:
        import hashlib

        for path in sorted((REPO / "Templates").glob("*.kicad_wks")):
            png = embedded_png(path.read_text(encoding="utf-8"))
            if png is not None:
                self.assertEqual(BOARDWRIGHT_PLACEHOLDER_LOGO_SHA256, hashlib.sha256(png).hexdigest(), path.name)


class LocalOverrideTests(unittest.TestCase):
    def test_template_keys_overlay_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "local.yaml")
            path.write_text("template:\n  sheet_image: ../private/logo.png\n", encoding="utf-8")
            merged, keys = apply_local_overrides({"template": {"sheet_image": "a.png", "worksheet": "w"}}, path)
        self.assertEqual({"sheet_image": "../private/logo.png", "worksheet": "w"}, merged["template"])
        self.assertEqual(("template.sheet_image",), keys)

    def test_other_sections_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "local.yaml")
            path.write_text("project:\n  board_revision: Z\n", encoding="utf-8")
            with self.assertRaisesRegex(BoardwrightError, "may only override template"):
                apply_local_overrides({}, path)

    def test_local_file_is_ignored_by_git(self) -> None:
        self.assertIn(".boardwright/local.yaml", (REPO / ".gitignore").read_text(encoding="utf-8").splitlines())


class UpdateMergeTests(unittest.TestCase):
    def _merge(self, local: str, base: str, incoming: str) -> str | None:
        from boardwright.managed_project import _merge_text

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "base.kicad_wks").write_text(base, encoding="utf-8")
            (root / "incoming.kicad_wks").write_text(incoming, encoding="utf-8")
            (root / "local.kicad_wks").write_text(local, encoding="utf-8")
            return _merge_text(root, root / "local.kicad_wks", root / "incoming.kicad_wks", {"base": "base.kicad_wks"})

    def _with_logo(self, text: str, png: bytes) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            sheet, logo = Path(tmp, "s.kicad_wks"), Path(tmp, "l.png")
            sheet.write_text(text, encoding="utf-8")
            logo.write_bytes(png)
            embed_logo_in_worksheet(sheet, logo)
            return sheet.read_text(encoding="utf-8")

    def test_upstream_layout_change_keeps_project_logo(self) -> None:
        base = (REPO / "Templates" / SHEET).read_text(encoding="utf-8")
        incoming = base.replace("(textsize 1.5 1.5)", "(textsize 1.8 1.8)", 1)
        self.assertNotEqual(base, incoming)
        local = self._with_logo(base, PNG_1X1)
        merged = self._merge(local, base, incoming)
        self.assertIsNotNone(merged)
        self.assertIn("(textsize 1.8 1.8)", merged)
        self.assertEqual(PNG_1X1, embedded_png(merged))

    def test_upstream_logo_change_does_not_replace_or_conflict_with_project_logo(self) -> None:
        base = (REPO / "Templates" / SHEET).read_text(encoding="utf-8")
        incoming = self._with_logo(base, PNG_2X1)
        local = self._with_logo(base, PNG_1X1)
        merged = self._merge(local, base, incoming)
        self.assertEqual(PNG_1X1, embedded_png(merged))

    def test_transplant_requires_matching_bitmap_count(self) -> None:
        base = (REPO / "Templates" / SHEET).read_text(encoding="utf-8")
        self.assertIsNone(transplant_bitmaps(base, "(kicad_wks (version 1))"))


class ReleaseControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="bw-sheet-release-"))
        self.project = SheetProject(self.tmp)
        self.project.release("1.0.0", "A.0")

    def tearDown(self) -> None:
        _rmtree(self.tmp)

    def _commit_logo(self, png: bytes) -> None:
        (self.tmp / "logo.png").write_bytes(png)
        embed_logo_in_worksheet(self.project.sheet(), self.tmp / "logo.png")
        self.project.commit("branding")

    def test_logo_change_requires_new_drawing_revisions(self) -> None:
        self._commit_logo(PNG_1X1)
        assessment = self.project.assess("1.0.1", "documentation", "A.0", "rebrand")
        for domain in ("schematic_sheet", "fabrication_sheet", "assembly_sheet"):
            self.assertIn(domain, assessment.detected)
        self.assertNotIn("pcb_geometry", assessment.detected)
        problems = " ".join(assessment.problems)
        self.assertIn("fabrication drawing revision must change", problems)
        self.assertIn("assembly drawing revision must change", problems)
        self.assertIn("schematic revision must change", problems)

    def test_logo_change_with_new_revisions_reuses_fabrication_baseline(self) -> None:
        self._commit_logo(PNG_1X1)
        self.project.set_revisions(asm_rev="B", fab_rev="B")
        self.project.commit("revise drawings")
        assessment = self.project.assess("1.0.1", "documentation", "A.1", "rebrand")
        self.assertEqual((), assessment.problems)
        self.assertEqual("A", assessment.proposed.pcb_revision)
        self.assertEqual(("A/1", "reused"), (assessment.fabrication_baseline.id, assessment.fabrication_baseline.status))

    def test_inactive_template_change_is_not_a_drawing_change(self) -> None:
        other = self.tmp / "Templates" / "Other.kicad_wks"
        other.write_text(other.read_text(encoding="utf-8").replace("(textsize 1.5 1.5)", "(textsize 2 2)", 1), encoding="utf-8")
        self.project.commit("unused template")
        before = load_config(self.tmp)
        self.assertEqual((), tuple(d for d in self.project.assess("1.0.1", "tooling", "A.0").detected if d.endswith("_sheet")))
        self.assertIsNotNone(before)

    def test_drawn_variable_change_is_detected_but_revision_variables_are_not(self) -> None:
        start = fingerprint_working_tree(self.tmp)
        self.project.set_revisions(fab_rev="B")
        revised = fingerprint_working_tree(self.tmp)
        self.assertEqual(start.fabrication_sheet, revised.fabrication_sheet)
        path = self.tmp / ".boardwright" / "project.yaml"
        path.write_text(path.read_text(encoding="utf-8").replace("name: Test Board", "name: Renamed Board"), encoding="utf-8")
        renamed = fingerprint_working_tree(self.tmp)
        self.assertNotEqual(revised.fabrication_sheet, renamed.fabrication_sheet)

    @unittest.skipIf(_zstd is None, "zstd support unavailable")
    def test_embedded_pcb_worksheet_is_not_pcb_geometry(self) -> None:
        start = fingerprint_working_tree(self.tmp)
        _embed_into(self.tmp / "board.kicad_pcb", SHEET, b"(kicad_wks (version 1))\n")
        after = fingerprint_working_tree(self.tmp)
        self.assertEqual(start.pcb_geometry, after.pcb_geometry)


AS1100_PCB = "Boardwright_Template_PCB_AS1100_A3.kicad_wks"
AS1100_SCH = "Boardwright_Template_AS1100.kicad_wks"


class As1100SheetProject(Project):
    """Release fixture whose drawings all render the AS 1100 pair.

    These sheets draw RELEASE_VERSION, every revision identity and the
    revision table, so they are the worst case for release-identity churn.
    """

    def __init__(self, root: Path) -> None:
        (root / "Templates").mkdir(parents=True)
        for name in (AS1100_PCB, AS1100_SCH):
            shutil.copyfile(REPO / "Templates" / name, root / "Templates" / name)
        (root / "board.kicad_pro").write_text(
            _kicad_pro("${KIPRJMOD}/Templates/" + AS1100_SCH, "${KIPRJMOD}/Templates/" + AS1100_PCB),
            encoding="utf-8",
        )
        super().__init__(root)

    def edit_yaml(self, mutate) -> None:
        import yaml

        path = self.root / ".boardwright" / "project.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        mutate(data)
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _sheet_domains(assessment) -> tuple[str, ...]:
    return tuple(domain for domain in assessment.detected if domain.endswith("_sheet"))


def _drawing_revision_problems(assessment) -> list[str]:
    return [p for p in assessment.problems if "revision must change" in p]


class ReleaseIdentityTests(unittest.TestCase):
    """A release-identity update must not force new drawing revisions."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="bw-release-identity-"))
        self.project = As1100SheetProject(self.tmp)
        self.project.release("1.0.0", "A.0")

    def tearDown(self) -> None:
        _rmtree(self.tmp)

    def test_sheets_draw_the_release_and_revision_identities(self) -> None:
        drawn = drawing_sheets.referenced_variables((self.tmp / "Templates" / AS1100_PCB).read_text(encoding="utf-8"))
        for key in ("RELEASE_VERSION", "PCB_REVISION", "ASSEMBLY_REVISION", "SCHEMATIC_REVISION", "DRAWING_REVISION"):
            self.assertIn(key, drawn)
        self.assertTrue(any(key.startswith("REVTABLE_") for key in drawn))

    def test_release_metadata_change_is_not_a_drawing_change(self) -> None:
        start = fingerprint_working_tree(self.tmp)

        def bump(data) -> None:
            data.setdefault("release", {}).update({"version": "1.0.1", "date": "2026-12-01"})

        self.project.edit_yaml(bump)
        after = fingerprint_working_tree(self.tmp)
        for domain in drawing_sheets.DOCUMENTS:
            self.assertEqual(start.__dict__[f"{domain}_sheet"], after.__dict__[f"{domain}_sheet"], domain)

    def test_next_release_without_substantive_change_needs_no_drawing_revision(self) -> None:
        # Sequential releases 1.0.0 -> 1.0.1 -> 1.0.2: each prepare step rewrites
        # release version/date and revision-table rows, all drawn on the sheets.
        self.project.release("1.0.1", "A.0", classes="tooling")
        assessment = self.project.assess("1.0.2", "tooling", "A.0")
        self.assertEqual((), _sheet_domains(assessment))
        self.assertEqual([], _drawing_revision_problems(assessment))

    def test_other_document_revision_change_does_not_cascade(self) -> None:
        # SCH REV is drawn on the fabrication/assembly identity line, but a
        # schematic revision does not re-issue the PCB drawings (and vice versa).
        self.project.set_revisions(sch_rev="A.1")
        self.project.commit("schematic revision")
        assessment = self.project.assess("1.0.1", "documentation", "A.1")
        self.assertNotIn("fabrication_sheet", assessment.detected)
        self.assertNotIn("assembly_sheet", assessment.detected)
        start = fingerprint_working_tree(self.tmp)
        self.project.set_revisions(asm_rev="B")
        self.assertEqual(start.schematic_sheet, fingerprint_working_tree(self.tmp).schematic_sheet)
        self.assertEqual(start.fabrication_sheet, fingerprint_working_tree(self.tmp).fabrication_sheet)

    def test_worksheet_layout_change_still_requires_drawing_revisions(self) -> None:
        for name in (AS1100_PCB, AS1100_SCH):
            path = self.tmp / "Templates" / name
            path.write_text(path.read_text(encoding="utf-8").replace("(size 1.8 1.8)", "(size 2 2)", 1), encoding="utf-8")
        self.project.commit("layout")
        assessment = self.project.assess("1.0.1", "documentation", "A.0")
        self.assertEqual({"schematic_sheet", "fabrication_sheet", "assembly_sheet"}, set(_sheet_domains(assessment)))
        problems = " ".join(assessment.problems)
        for document in ("fabrication drawing", "assembly drawing", "schematic"):
            self.assertIn(f"{document} revision must change", problems)

    def test_branding_change_still_requires_drawing_revisions(self) -> None:
        (self.tmp / "logo.png").write_bytes(PNG_1X1)
        for name in (AS1100_PCB, AS1100_SCH):
            embed_logo_in_worksheet(self.tmp / "Templates" / name, self.tmp / "logo.png")
        self.project.commit("rebrand")
        assessment = self.project.assess("1.0.1", "documentation", "A.0")
        self.assertEqual({"schematic_sheet", "fabrication_sheet", "assembly_sheet"}, set(_sheet_domains(assessment)))
        self.assertTrue(_drawing_revision_problems(assessment))

    def test_title_block_value_change_still_requires_drawing_revisions(self) -> None:
        def retitle(data) -> None:
            data["documents"]["fabrication"]["title"] = "Bare Board Drawing"
            data.setdefault("sheet", {})["legal_notice"] = "PROPERTY OF ANOTHER OWNER"

        self.project.edit_yaml(retitle)
        self.project.commit("retitle")
        assessment = self.project.assess("1.0.1", "documentation", "A.0")
        self.assertIn("fabrication_sheet", assessment.detected)
        self.assertIn("fabrication drawing revision must change", " ".join(assessment.problems))


class LocalOverrideReleaseTests(unittest.TestCase):
    """An untracked local.yaml can never reach tracked files or release evidence."""

    LOCAL = "template:\n  sheet_image: private/logo.png\n  worksheet: Templates/Private.kicad_wks\n  logo: private/logo.png\n"

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="bw-local-override-"))
        self.project = As1100SheetProject(self.tmp)
        (self.tmp / "private").mkdir()
        (self.tmp / "private" / "logo.png").write_bytes(PNG_1X1)
        (self.tmp / ".gitignore").write_text(".boardwright/local.yaml\nprivate/\n", encoding="utf-8")
        self.project.commit("ignore local")
        self.project.release("1.0.0", "A.0")
        (self.tmp / ".boardwright" / "local.yaml").write_text(self.LOCAL, encoding="utf-8")

    def tearDown(self) -> None:
        _rmtree(self.tmp)

    def test_local_override_is_visible_only_to_local_loads(self) -> None:
        self.assertEqual("private/logo.png", load_config(self.tmp).template_sheet_image)
        self.assertNotEqual("private/logo.png", load_config(self.tmp, local=False).template_sheet_image)

    def test_local_override_does_not_change_fingerprints_or_impact(self) -> None:
        (self.tmp / ".boardwright" / "local.yaml").unlink()
        without = fingerprint_working_tree(self.tmp)
        (self.tmp / ".boardwright" / "local.yaml").write_text(self.LOCAL, encoding="utf-8")
        self.assertEqual(without, fingerprint_working_tree(self.tmp))
        self.assertEqual((), _sheet_domains(self.project.assess("1.0.1", "tooling", "A.0")))

    def test_metadata_sync_writes_only_tracked_values(self) -> None:
        from boardwright.kicad_tables import _load_project_config

        config = _load_project_config(self.tmp)
        self.assertEqual((), config.local_overrides)
        self.assertNotIn("private", config.template_sheet_image)
        self.assertNotIn("Private", config.worksheet)

    def test_settings_save_does_not_persist_local_values(self) -> None:
        from boardwright.config import update_project_config

        config = load_config(self.tmp)
        tracked = (self.tmp / ".boardwright" / "project.yaml").read_text(encoding="utf-8")
        # The TUI shows (and submits back) the merged value.
        update_project_config(config, template_fields={"sheet_image": config.template_sheet_image})
        self.assertEqual(tracked, (self.tmp / ".boardwright" / "project.yaml").read_text(encoding="utf-8"))
        # A deliberate new value is still saved.
        update_project_config(load_config(self.tmp), template_fields={"sheet_image": "assets/logo.png"})
        self.assertIn("sheet_image: assets/logo.png", (self.tmp / ".boardwright" / "project.yaml").read_text(encoding="utf-8"))

    def test_ci_check_ignores_local_override(self) -> None:
        import contextlib
        import io
        import os

        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                drawing_sheets.main(["check"])
        finally:
            os.chdir(cwd)
        self.assertNotIn("private", out.getvalue())
        self.assertNotIn("Private", out.getvalue())


if __name__ == "__main__":
    unittest.main()
