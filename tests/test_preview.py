import unittest
from pathlib import Path
from unittest import mock

from boardwright.config import BoardwrightConfig
from boardwright.errors import BoardwrightError
from boardwright.preview import build_preview_plan, fetch_latest_preview_artifact
from boardwright.variants import normalize_variant


class PreviewTests(unittest.TestCase):
    def test_normalize_variant(self) -> None:
        self.assertEqual("CHECKED", normalize_variant("checked"))
        with self.assertRaises(BoardwrightError):
            normalize_variant("FAST")

    def test_build_preview_plan_uses_config_defaults(self) -> None:
        config = BoardwrightConfig(
            root=Path("."),
            project={
                "project": {"id": "TEST", "name": "Test"},
                "variants": {"dev_default": "DRAFT", "preview_default": "PRELIMINARY"},
                "outputs": {
                    "preview_engine": "github-actions",
                    "preview_workflow": "ci.yaml",
                },
            },
            branches={"branches": {}},
            legal={"legal": {}},
            revision_history={"revision_history": {}},
        )

        plan = build_preview_plan(config)

        self.assertEqual("PRELIMINARY", plan.variant)
        self.assertEqual("github-actions", plan.engine)
        self.assertEqual("preview", plan.preview_branch)
        self.assertIn(Path("Schematic"), plan.output_paths)
        self.assertIn(Path("assets/renders"), plan.output_paths)

    def test_fetch_preview_requires_gh(self) -> None:
        config = BoardwrightConfig(
            root=Path("."),
            project={
                "project": {"id": "TEST", "name": "Test"},
                "variants": {"preview_default": "PRELIMINARY"},
                "outputs": {"preview_workflow": "ci.yaml"},
            },
            branches={"branches": {}},
            legal={"legal": {}},
            revision_history={"revision_history": {}},
        )

        with self.assertRaises(BoardwrightError):
            with mock.patch("boardwright.preview._gh_command", return_value=None):
                fetch_latest_preview_artifact(config)


if __name__ == "__main__":
    unittest.main()
