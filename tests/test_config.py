import unittest
import shutil
from pathlib import Path

from boardwright.config import BoardwrightConfig, _read_simple_yaml, load_config, update_project_config


class ConfigTests(unittest.TestCase):
    def test_simple_yaml_reads_folded_text(self) -> None:
        parsed = _read_simple_yaml(
            """legal:
  safety_notice: >
    Verify isolation, creepage, and clearance.
    Confirm regulatory compliance.
"""
        )

        self.assertEqual(
            "Verify isolation, creepage, and clearance. Confirm regulatory compliance.",
            parsed["legal"]["safety_notice"],
        )

    def test_update_project_config_edits_metadata_and_variants(self) -> None:
        root = Path.cwd() / ".test_config_workspace"
        if root.exists():
            shutil.rmtree(root)
        try:
            root.mkdir()
            config_dir = root / ".boardwright"
            config_dir.mkdir()
            (config_dir / "project.yaml").write_text(
                """project:
  id: OLD
  name: Old
  board_name: Old Board
  board_revision: A
  company: Old Co
  designer: Old Designer
  git_url: ""
  github_repo: ""
variants:
  dev_default: DRAFT
  preview_default: PRELIMINARY
  main_default: CHECKED
  release_default: RELEASED
outputs:
  preview_engine: github-actions
assets:
  logo: old.png
  product_image: ""
""",
                encoding="utf-8",
            )
            (config_dir / "branches.yaml").write_text("branches:\n  development: dev\n", encoding="utf-8")
            (config_dir / "legal.yaml").write_text("legal: {}\n", encoding="utf-8")
            (config_dir / "revision_history.yaml").write_text(
                "revision_history: {}\n",
                encoding="utf-8",
            )
            config = BoardwrightConfig(
                root=root,
                project=load_config(root).project,
                branches=load_config(root).branches,
                legal=load_config(root).legal,
                revision_history=load_config(root).revision_history,
            )

            update_project_config(
                config,
                project_fields={"id": "NEW", "github_repo": "owner/repo"},
                variant_fields={"preview_default": "checked"},
                asset_fields={"logo": "assets/logo.png"},
            )

            updated = load_config(root)
            self.assertEqual("NEW", updated.project_id)
            self.assertEqual("Old Board", updated.board_name)
            self.assertEqual("A", updated.board_revision)
            self.assertEqual("owner/repo", updated.github_repo)
            self.assertEqual("CHECKED", updated.preview_variant)
            self.assertEqual("assets/logo.png", updated.assets["logo"])
        finally:
            if root.exists():
                shutil.rmtree(root)


if __name__ == "__main__":
    unittest.main()
