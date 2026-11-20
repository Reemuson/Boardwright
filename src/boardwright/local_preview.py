# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Local-first previews: one implementation for the CLI and the TUI.

``run_preview`` selects the backend once (``backends.select_backend``; every
preview variant defaults to local Docker), then either builds locally in the
pinned container or dispatches the existing GitHub preview workflow. It never
switches backend after a failure; it names the override instead.

Local previews (``docs/LOCAL_FIRST.md`` section 4):

- build in an isolated stage (``container.stage``), never in the developer's
  working tree, which is fingerprinted before and after;
- need no remote, no GitHub authentication and no pushed commit;
- include saved-to-disk changes according to the source policy below;
- are identified by their staged content, not only by the last commit;
- are marked ``purpose: preview`` and ``release_evidence: false`` in the
  stage marker, the build record and the preview record.

Source policy for a dirty working tree:

- Modified and deleted tracked files are included, except the per-user and
  private files in ``ALWAYS_EXCLUDED``.
- Untracked files are included only when they are KiCad project sources
  (``UNTRACKED_PROJECT_PATTERNS``) and not credentials or private
  configuration (``CREDENTIAL_PATTERNS``). Everything else is listed as
  excluded with its reason; commit or ``git add -N`` a file to include it.
- Ignored files, including ``.boardwright/local.yaml``, never enter the stage.
  The build refuses a tree containing ``local.yaml``, so local overrides
  cannot reach outputs, fingerprints or release packages.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable

from . import backends
from .backends import Backend, Operation, Selection
from .errors import BoardwrightError

PREVIEW_SCHEMA = 1
PREVIEW_RECORD = "boardwright-preview.json"  # in the stage's .git directory
LOCAL_OVERRIDES = ".boardwright/local.yaml"

# Tracked or untracked: per-user state and private configuration.
ALWAYS_EXCLUDED = (
    LOCAL_OVERRIDES,
    "*.kicad_prl",          # KiCad per-user view state
    "*-backups/*",          # KiCad automatic backups
    "*.lck",                # KiCad lock files
    "fp-info-cache",
    "_autosave-*",
    "*.bak",
    "*~",
)
# Untracked files that are project sources and may be included.
UNTRACKED_PROJECT_PATTERNS = (
    "*.kicad_sch", "*.kicad_pcb", "*.kicad_pro", "*.kicad_sym", "*.kicad_mod", "*.kicad_wks", "*.kicad_dru",
    "fp-lib-table", "sym-lib-table", "*.step", "*.stp", "*.wrl",
)
# Never included when untracked, whatever the extension (matched on the file name, case-insensitively).
CREDENTIAL_PATTERNS = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "id_ecdsa*",
    ".netrc", ".git-credentials", "*credential*", "*secret*", "*token*", "*.kdbx",
)


# ---------------------------------------------------------------- source plan


@dataclass(frozen=True)
class SourceChange:
    path: str
    kind: str  # "modified" | "untracked"
    sha256: str


@dataclass(frozen=True)
class SourcePlan:
    """What a local preview stages from the working tree."""

    repository: Path
    base_commit: str
    branch: str
    included: tuple[SourceChange, ...]
    deleted: tuple[str, ...]
    excluded: tuple[tuple[str, str], ...]  # (path, reason)

    @property
    def dirty(self) -> bool:
        return bool(self.included or self.deleted)

    @property
    def source_digest(self) -> str:
        """Content identity of the staged source: base commit plus every staged change."""

        digest = hashlib.sha256(f"base {self.base_commit}\n".encode())
        for change in self.included:
            digest.update(f"file {change.path}\0{change.sha256}\n".encode())
        for path in self.deleted:
            digest.update(f"delete {path}\n".encode())
        return digest.hexdigest()


def _git(repo: Path, *args: str, check: bool = True) -> str:
    done = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8")
    if check and done.returncode != 0:
        raise BoardwrightError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    name = PurePosixPath(path).name.lower()
    lowered = path.lower()
    return any(fnmatch.fnmatchcase(name, p.lower()) or fnmatch.fnmatchcase(lowered, p.lower()) for p in patterns)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def plan_source(repo: Path) -> SourcePlan:
    """Apply the source policy to the working tree (read-only)."""

    repo = Path(_git(repo, "rev-parse", "--show-toplevel").strip())
    base = _git(repo, "rev-parse", "HEAD").strip()
    branch = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False).strip() or "detached"
    modified = {p for p in _git(repo, "ls-files", "-z", "--modified").split("\0") if p}
    deleted = {p for p in _git(repo, "ls-files", "-z", "--deleted").split("\0") if p}
    untracked = {p for p in _git(repo, "ls-files", "-z", "--others", "--exclude-standard").split("\0") if p}
    included: list[SourceChange] = []
    excluded: list[tuple[str, str]] = []
    for path in sorted(modified - deleted):
        if _matches(path, ALWAYS_EXCLUDED):
            excluded.append((path, "per-user or private file"))
        elif not (repo / path).is_file() or (repo / path).is_symlink():
            excluded.append((path, "not a regular file"))
        else:
            included.append(SourceChange(path, "modified", _sha256(repo / path)))
    for path in sorted(untracked):
        if _matches(path, ALWAYS_EXCLUDED):
            excluded.append((path, "per-user or private file"))
        elif _matches(path, CREDENTIAL_PATTERNS):
            excluded.append((path, "looks like a credential"))
        elif not _matches(path, UNTRACKED_PROJECT_PATTERNS):
            excluded.append((path, "untracked and not a KiCad project file"))
        elif (repo / path).is_symlink() or not (repo / path).is_file():
            excluded.append((path, "not a regular file"))
        else:
            included.append(SourceChange(path, "untracked", _sha256(repo / path)))
    kept_deleted = []
    for path in sorted(deleted):
        if _matches(path, ALWAYS_EXCLUDED):
            excluded.append((path, "per-user or private file"))
        else:
            kept_deleted.append(path)
    return SourcePlan(repo, base, branch, tuple(included), tuple(kept_deleted), tuple(excluded))


def worktree_fingerprint(repo: Path) -> str:
    """Hash of every non-ignored file's bytes, ``local.yaml``, HEAD and the index.

    Compared before and after a preview: Boardwright never writes to the
    working tree, so any difference is reported.
    """

    digest = hashlib.sha256()
    digest.update(_git(repo, "rev-parse", "HEAD").encode())
    digest.update(_git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").encode())
    files = sorted({p for p in _git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split("\0") if p})
    for path in files + [LOCAL_OVERRIDES]:
        target = repo / path
        digest.update(path.encode() + b"\0")
        if target.is_file():
            digest.update(_sha256(target).encode())
    return digest.hexdigest()


# ---------------------------------------------------------------- locations


def project_key(repo: Path) -> str:
    """Collision-resistant per-checkout key: name plus a hash of the absolute path."""

    path = str(Path(repo).resolve())
    if os.name == "nt":
        path = path.lower()
    return f"{Path(repo).resolve().name}-{hashlib.sha256(path.encode()).hexdigest()[:10]}"


def stage_root() -> Path:
    from .container import default_stage_root

    return default_stage_root()


def preview_stage_dir(repo: Path, variant: str, kicad: str) -> Path:
    return stage_root() / f"{project_key(repo)}-preview-{variant.lower()}-k{kicad}"


def index_dir(repo: Path) -> Path:
    base = Path(os.environ.get("BOARDWRIGHT_PREVIEW_INDEX") or Path.home() / ".boardwright" / "previews")
    return base / project_key(repo)


def index_path(repo: Path, variant: str, kicad: str) -> Path:
    return index_dir(repo) / f"{variant.upper()}-k{kicad}.json"


# ---------------------------------------------------------------- local run

Runner = Callable[[str, Path, list[str]], int]


def _container_runner(kicad: str, workdir: Path, command: list[str]) -> int:
    from .container import run_in_container

    return run_in_container(kicad, workdir, command)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def build_command(variant: str, kicad: str) -> list[str]:
    """The same in-container command as ``container build`` (``container._build``)."""

    return ["python3", "-m", "boardwright", "build", "--variant", variant, "--kicad", kicad, "--log-prefix", "preview"]


def run_local_preview(
    repo: Path,
    variant: str,
    *,
    kicad: str = "9",
    runner: Runner | None = None,
    echo: Callable[[str], None] = print,
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    """Stage, build in the pinned container, record. Returns the preview record."""

    from .build import BUILD_DIR
    from .container import ContainerError, image_for, stage

    runner = runner or _container_runner
    variant = variant.upper()
    plan = plan_source(repo)
    repo = plan.repository
    if _git(repo, "ls-files", "--", LOCAL_OVERRIDES).strip():
        raise BoardwrightError(f"{LOCAL_OVERRIDES} is tracked by Git; it must stay local (untrack it) before previewing.")
    before = worktree_fingerprint(repo)
    started, start = _now(), clock()
    workdir = preview_stage_dir(repo, variant, kicad)
    record: dict = {
        "schema": PREVIEW_SCHEMA,
        "purpose": "preview",
        "release_evidence": False,
        "backend": "local",
        "variant": variant,
        "kicad": kicad,
        "experimental_toolchain": kicad != "9",
        "status": "running",
        "started_at": started,
        "source": {
            "repository": str(repo),
            "base_commit": plan.base_commit,
            "branch": plan.branch,
            "dirty": plan.dirty,
            "source_digest": plan.source_digest,
            "included": [change.__dict__ for change in plan.included],
            "deleted": list(plan.deleted),
            "excluded": [{"path": p, "reason": r} for p, r in plan.excluded],
        },
        "toolchain": {"image": image_for(kicad).reference},
        "outputs": str(workdir),
        "evidence": str(workdir / BUILD_DIR),
    }
    echo(f"[preview] {variant} local (KiCad {kicad}) from {plan.base_commit[:12]}"
         + (f" + {len(plan.included)} changed, {len(plan.deleted)} deleted file(s)" if plan.dirty else " (clean)"))
    for path, reason in plan.excluded:
        echo(f"[preview] excluded {path}: {reason}")
    index = index_path(repo, variant, kicad)
    try:
        stage(repo, worktree=plan.dirty, dest=workdir, purpose="preview",
              changes=([c.path for c in plan.included], list(plan.deleted)))
        staged_commit = _git(workdir, "rev-parse", "HEAD").strip()
        record["source"]["staged_commit"] = staged_commit
        record["source"]["staged_tree"] = _git(workdir, "rev-parse", "HEAD^{tree}").strip()
        record["identity"] = preview_identity(record)
        _write_json(workdir / ".git" / PREVIEW_RECORD, record)
        _write_json(index, record)
        echo(f"[preview] staged {workdir}")
        status = runner(kicad, workdir, build_command(variant, kicad))
        error = "" if status == 0 else f"the container build exited with status {status}"
    except (ContainerError, BoardwrightError, OSError, subprocess.CalledProcessError) as exc:
        status, error = 1, str(exc)
    record["exit_status"] = status
    record["finished_at"] = _now()
    record["elapsed_seconds"] = round(clock() - start, 1)
    evidence = Path(record["evidence"]) / "record.json"
    if Path(record["evidence"]).is_dir():
        # Marks the evidence itself, independent of the project's payload
        # version (an older payload's record.json has no stage purpose).
        from .container import PREVIEW_EVIDENCE_MARKER

        _write_json(Path(record["evidence"]) / PREVIEW_EVIDENCE_MARKER, {
            "purpose": "preview", "release_evidence": False, "backend": "local",
            "variant": variant, "source_digest": plan.source_digest,
            "staged_tree": record["source"].get("staged_tree", ""),
        })
    if evidence.is_file():
        built = json.loads(evidence.read_text(encoding="utf-8"))
        record["toolchain"].update({
            "image": built.get("image") or record["toolchain"]["image"],
            "image_id": built.get("image_id", ""),
            "boardwright_tree": (built.get("boardwright_source") or {}).get("tree", ""),
            "kibot_config_tree": (built.get("configuration") or {}).get("kibot_config_tree", ""),
            "tools": built.get("tools", {}),
        })
        record["build_record_stage"] = built.get("stage", {})
    if worktree_fingerprint(repo) != before:
        record["worktree_changed_during_preview"] = True
        echo("[preview] warning: the working tree changed while the preview ran; "
             "the outputs show the staged snapshot and the preview is stale.")
    record["status"] = "succeeded" if status == 0 and not error else "failed"
    if error:
        record["error"] = error
    record.setdefault("identity", preview_identity(record))
    if (workdir / ".git").is_dir():
        _write_json(workdir / ".git" / PREVIEW_RECORD, record)
    _write_json(index, record)
    return record


def preview_identity(record: dict) -> str:
    """``VARIANT@<staged tree>`` plus ``+dirty`` and the base commit when not clean."""

    source = record.get("source", {})
    tree = (source.get("staged_tree") or source.get("source_digest") or "")[:12]
    text = f"{record.get('variant')}@{tree}"
    if source.get("dirty"):
        text += f" (dirty, base {source.get('base_commit', '')[:12]})"
    return text


# ---------------------------------------------------------------- selection and dispatch


@dataclass
class PreviewOutcome:
    selection: Selection
    ok: bool
    message: str
    record: dict | None = None
    lines: list[str] = field(default_factory=list)


def select_preview_backend(
    repo: Path,
    variant: str,
    *,
    project_setting: str = "auto",
    override: str = "",
    inspect_remote=backends.inspect_remote,
    inspect_docker=backends.inspect_docker,
) -> Selection:
    """The shared selection. GitHub access is inspected only when GitHub is requested."""

    wanted = (override if override and override != "auto" else project_setting) or "auto"
    remote = inspect_remote(repo, check_access=(wanted == "github"))
    docker = inspect_docker(root=repo)
    return backends.select_backend(Operation.PREVIEW, remote=remote, docker=docker, variant=variant,
                                   project_setting=project_setting, override=override)


def github_preview_problems(config) -> list[str]:
    """Preconditions of the GitHub preview workflow (it builds a pushed commit)."""

    from .status import collect_status

    status = collect_status(config)
    problems = []
    if status.branch != config.dev_branch:
        problems.append(f"switch to {config.dev_branch} to preview on GitHub")
    if status.dirty_count:
        problems.append("commit local changes before a GitHub preview (a local preview includes them)")
    if status.ahead:
        problems.append("push commits before a GitHub preview")
    return problems


def run_preview(
    config,
    variant: str | None,
    *,
    override: str = "",
    kicad: str = "9",
    echo: Callable[[str], None] = print,
    runner: Runner | None = None,
    inspect_remote=backends.inspect_remote,
    inspect_docker=backends.inspect_docker,
    dispatch: Callable | None = None,
) -> PreviewOutcome:
    """Select once, run the selected backend, never switch."""

    from .preview import build_preview_plan, dispatch_preview
    from .variants import normalize_variant

    try:
        chosen = normalize_variant(variant or config.preview_variant)
    except BoardwrightError as exc:
        refused = Selection(Operation.PREVIEW, None, "override", "invalid variant", (str(exc),))
        return PreviewOutcome(refused, False, str(exc))
    selection = select_preview_backend(config.root, chosen, project_setting=config.preview_backend, override=override,
                                       inspect_remote=inspect_remote, inspect_docker=inspect_docker)
    echo(backends.format_selection(selection))
    if not selection.ok:
        return PreviewOutcome(selection, False, "; ".join(selection.problems))
    if selection.backend is Backend.GITHUB:
        problems = github_preview_problems(config)
        if problems:
            return PreviewOutcome(selection, False, "; ".join(problems))
        plan = build_preview_plan(config, chosen)
        try:
            (dispatch or dispatch_preview)(plan, config.root)
        except BoardwrightError as exc:
            return PreviewOutcome(selection, False, f"GitHub preview failed: {exc} (not retried locally; "
                                  "run with --backend local to build locally)")
        return PreviewOutcome(selection, True, f"GitHub preview {chosen} dispatched ({plan.workflow} on {plan.branch}).")
    try:
        record = run_local_preview(config.root, chosen, kicad=kicad, runner=runner, echo=echo)
    except BoardwrightError as exc:
        return PreviewOutcome(selection, False, str(exc))
    if record["status"] != "succeeded":
        hint = " (not retried on GitHub; run with --backend github to build there)"
        return PreviewOutcome(selection, False, f"Local preview {chosen} failed: {record.get('error', '')}{hint}", record)
    return PreviewOutcome(selection, True, f"Local preview {record['identity']} ready: {record['outputs']}", record)


# ---------------------------------------------------------------- status


@dataclass(frozen=True)
class PreviewStatus:
    variant: str
    kicad: str
    state: str  # "current" | "stale" | "failed" | "incomplete" | "missing outputs"
    reasons: tuple[str, ...]
    record: dict


def preview_status(repo: Path, *, plan: SourcePlan | None = None) -> list[PreviewStatus]:
    """Every recorded local preview of this checkout, with staleness."""

    from .container import image_for

    directory = index_dir(repo)
    if not directory.is_dir():
        return []
    plan = plan or plan_source(repo)
    found = []
    for path in sorted(directory.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        reasons = []
        state = "current"
        if record.get("status") == "running":
            state, reasons = "incomplete", ["the preview did not finish (interrupted, or still running)"]
        elif record.get("status") != "succeeded":
            state, reasons = "failed", [record.get("error", "the build failed")]
        else:
            source = record.get("source", {})
            if source.get("source_digest") != plan.source_digest:
                reasons.append("the working tree or HEAD changed since this preview")
            if record.get("worktree_changed_during_preview"):
                reasons.append("the working tree changed while it ran")
            if record.get("toolchain", {}).get("image") != image_for(record.get("kicad", "9")).reference:
                reasons.append("the pinned toolchain changed")
            if not Path(record.get("outputs", "")).is_dir():
                state, reasons = "missing outputs", reasons + ["the stage directory was removed"]
            elif reasons:
                state = "stale"
        found.append(PreviewStatus(record.get("variant", "?"), str(record.get("kicad", "9")), state, tuple(reasons), record))
    return found


def format_record(record: dict) -> list[str]:
    source = record.get("source", {})
    toolchain = record.get("toolchain", {})
    lines = [
        f"Preview: {record.get('identity', '?')} - {record.get('status')} (local, never release evidence)",
        f"  Base commit: {source.get('base_commit', '')}" + (" + uncommitted changes" if source.get("dirty") else " (clean)"),
        f"  Staged source: tree {source.get('staged_tree', '?')}, digest {source.get('source_digest', '')[:16]}",
        f"  Toolchain: {toolchain.get('image', '')}" + (f" ({toolchain['image_id']})" if toolchain.get("image_id") else ""),
        f"  Outputs: {record.get('outputs', '')}",
        f"  Evidence: {record.get('evidence', '')}",
    ]
    if record.get("elapsed_seconds") is not None:
        lines.append(f"  Elapsed: {record['elapsed_seconds']} s")
    if source.get("included"):
        lines.append(f"  Included changes: {', '.join(c['path'] for c in source['included'])}")
    if source.get("deleted"):
        lines.append(f"  Deleted: {', '.join(source['deleted'])}")
    if source.get("excluded"):
        lines.append(f"  Excluded: {', '.join(e['path'] + ' (' + e['reason'] + ')' for e in source['excluded'])}")
    if record.get("experimental_toolchain"):
        lines.append("  KiCad 10 is experimental.")
    if record.get("error"):
        lines.append(f"  Error: {record['error']}")
    return lines


def format_status(statuses: list[PreviewStatus]) -> str:
    if not statuses:
        return "No local previews recorded for this checkout. Run: boardwright preview"
    lines = []
    for status in statuses:
        lines.append(f"{status.variant} (KiCad {status.kicad}): {status.state}")
        lines += [f"  - {reason}" for reason in status.reasons]
        lines += ["  " + line for line in format_record(status.record)]
    return "\n".join(lines)


def latest_outputs(repo: Path) -> Path | None:
    """Outputs of the most recent successful local preview (for "open folder")."""

    best = None
    for status in preview_status(repo):
        if status.record.get("status") == "succeeded" and Path(status.record.get("outputs", "")).is_dir():
            if best is None or status.record.get("finished_at", "") > best.record.get("finished_at", ""):
                best = status
    return Path(best.record["outputs"]) if best else None


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin wrapper for debugging
    print(format_status(preview_status(Path.cwd())))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
