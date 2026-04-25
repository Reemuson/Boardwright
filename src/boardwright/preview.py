from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .actions import _gh_command
from .config import BoardwrightConfig
from .errors import BoardwrightError
from .git_ops import current_branch
from .variants import normalize_variant


@dataclass(frozen=True)
class PreviewPlan:
    engine: str
    workflow: str
    branch: str
    preview_branch: str
    variant: str
    output_paths: tuple[Path, ...]
    gh_available: bool
    gh_command: str = "gh"


def build_preview_plan(config: BoardwrightConfig, variant: str | None = None) -> PreviewPlan:
    selected_variant = normalize_variant(variant or config.preview_variant)
    return PreviewPlan(
        engine=config.preview_engine,
        workflow=config.preview_workflow,
        branch=current_branch(config.root),
        preview_branch=config.preview_branch,
        variant=selected_variant,
        output_paths=expected_output_paths(config.root),
        gh_available=_gh_command() is not None,
        gh_command=_gh_command() or "gh",
    )


def expected_output_paths(root: Path) -> tuple[Path, ...]:
    return tuple(
        root / path
        for path in (
            "Schematic",
            "Manufacturing/Assembly",
            "Manufacturing/Fabrication",
            "Manufacturing/Fabrication/Gerbers",
            "assets/renders",
            "assets/3d",
            "HTML",
            "KiRI",
        )
    )


def dispatch_preview(plan: PreviewPlan, root: Path) -> None:
    if plan.engine != "github-actions":
        raise BoardwrightError(f"Unsupported preview engine: {plan.engine}")
    if not plan.gh_available:
        raise BoardwrightError(
            "GitHub CLI is not installed. Re-run without --dispatch, or install gh."
        )

    workflow_path = root / ".github" / "workflows" / plan.workflow
    if not workflow_path.exists():
        raise BoardwrightError(f"Missing preview workflow: {workflow_path}")

    completed = subprocess.run(
        [
            plan.gh_command,
            "workflow",
            "run",
            plan.workflow,
            "--ref",
            plan.branch,
            "-f",
            f"variant={plan.variant}",
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise BoardwrightError(f"GitHub workflow dispatch failed: {message}")


def fetch_latest_preview_artifact(
    config: BoardwrightConfig,
    variant: str | None = None,
    output_dir: Path | None = None,
) -> str:
    gh = _gh_command()
    if gh is None:
        raise BoardwrightError("GitHub CLI is not installed. Install gh to fetch artifacts.")

    selected_variant = normalize_variant(variant or config.preview_variant)
    destination = output_dir or (config.root / "boardwright-preview")
    artifact_name = f"boardwright-preview-{selected_variant}"

    run_list_command = [
        gh,
        "run",
        "list",
        "--workflow",
        config.preview_workflow,
        "--branch",
        current_branch(config.root),
        "--limit",
        "1",
        "--json",
        "databaseId,status,conclusion,displayTitle",
    ]
    if config.github_repo:
        run_list_command.extend(("--repo", config.github_repo))

    listed = subprocess.run(
        run_list_command,
        cwd=config.root,
        text=True,
        capture_output=True,
        check=False,
    )
    if listed.returncode != 0:
        message = listed.stderr.strip() or listed.stdout.strip()
        raise BoardwrightError(f"Could not list preview workflow runs: {message}")

    try:
        runs = json.loads(listed.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise BoardwrightError(f"Could not parse GitHub run list: {exc}") from exc

    if not runs:
        raise BoardwrightError("No preview workflow run found for the current branch.")

    run = runs[0]
    run_id = str(run.get("databaseId", "")).strip()
    if not run_id:
        raise BoardwrightError("Latest preview workflow run did not include an id.")

    destination.mkdir(parents=True, exist_ok=True)
    download_command = [
        gh,
        "run",
        "download",
        run_id,
        "--name",
        artifact_name,
        "--dir",
        str(destination),
    ]
    if config.github_repo:
        download_command.extend(("--repo", config.github_repo))

    downloaded = subprocess.run(
        download_command,
        cwd=config.root,
        text=True,
        capture_output=True,
        check=False,
    )
    if downloaded.returncode != 0:
        message = downloaded.stderr.strip() or downloaded.stdout.strip()
        raise BoardwrightError(f"Could not download preview artifact: {message}")

    status = run.get("status") or "unknown"
    conclusion = run.get("conclusion") or "unknown"
    return f"Fetched {artifact_name} from run {run_id} ({status}/{conclusion}) to {destination}."
