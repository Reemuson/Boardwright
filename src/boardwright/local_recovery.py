# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Status and recovery of local releases (``docs/LOCAL_FIRST.md`` section 5).

``version_state`` proves where a version is in
``candidate -> approved -> prepared -> imported -> advanced -> tagged ->
published`` from its objects (store entries, checksums, index events, refs
and tags) and names the next action. ``recover`` performs only steps that
need no build and change nothing already valid:

- remove interrupted store writes (owned ``.tmp-*`` directories);
- append the index event of a complete, checksummed entry whose event was
  not written (an interruption between the rename and the append);
- import the recorded release commit from its frozen bundle;
- bring the working tree to the release commit after an interrupted branch
  advance (only when it still holds exactly the source tree).

It never builds, commits, tags, pushes, forces, resets or deletes a ref; a
missing tag is created by ``finalize`` (with the same signing choice).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .errors import BoardwrightError
from .local_finalize import (
    FINALIZE_FILE,
    _rev,
    finalize_state,
    import_release_commit,
    pending_ref,
    sync_worktree,
)
from .local_publish import PUBLISH_FILE, load_release
from .local_release import LocalReleaseError, verify_approval
from .release_store import ReleaseStore, sha256_file

NEXT = {
    "candidate": "review the candidate, then `boardwright release local approve`",
    "approved": "`boardwright release local finalize`",
    "prepared": "`boardwright release local recover` (imports the release commit) or finalize",
    "imported": "`boardwright release local finalize` (advances the branch and tags)",
    "advanced": "`boardwright release local finalize` (creates the tag)",
    "tagged": "`boardwright release local publish`",
    "published": "nothing: the release is published (the tag was not pushed)",
}


@dataclass
class VersionState:
    version: str
    stage: str = "none"
    detail: str = ""
    problems: list[str] = field(default_factory=list)
    recoverable: list[str] = field(default_factory=list)

    @property
    def next_action(self) -> str:
        if self.problems:
            return "resolve the problems above; nothing is changed automatically"
        if self.recoverable:
            return "`boardwright release local recover`"
        return NEXT.get(self.stage, "")


def versions(store: ReleaseStore) -> list[str]:
    names = set()
    for kind in ("candidates", "approvals", "finalizations", "releases"):
        names.update(entry.version for entry in store.entries(kind))
    return sorted(names)


def _missing_events(store: ReleaseStore, version: str) -> list[tuple[str, Path]]:
    """Complete entries whose index event is missing (interrupted between rename and append)."""

    events = {(e.get("event"), e.get("version")) for e in store.events()}
    missing = []
    for kind, event, record in (("finalizations", "prepared", FINALIZE_FILE), ("releases", "published", PUBLISH_FILE)):
        for entry in store.entries(kind, version):
            if (event, version) not in events and not store.verify_entry(entry.path):
                missing.append((event, entry.path / record))
    return missing


def version_state(repo: Path, store: ReleaseStore, version: str) -> VersionState:
    state = VersionState(version)
    if store.entries("candidates", version):
        state.stage, state.detail = "candidate", "candidate frozen; not approved"
    for event, _path in _missing_events(store, version):
        state.recoverable.append(f"the {event} entry is complete but its index event was not written")
    if state.recoverable:
        return state
    if store.entries("approvals", version):
        try:
            verify_approval(store, version)
        except BoardwrightError as exc:
            state.problems.append(str(exc))
            return state
        state.stage, state.detail = "approved", "approved; not finalised"
    if state.stage != "approved":
        return state
    try:
        finalized = finalize_state(repo, store, version)
    except BoardwrightError as exc:
        state.problems.append(str(exc))
        return state
    state.stage, state.detail = finalized.stage, finalized.detail
    if finalized.stage == "prepared":
        state.recoverable.append("the release commit is recorded but not imported")
    if finalized.stage in ("advanced", "tagged") and _rev(repo, "HEAD") == finalized.release_commit:
        index = subprocess.run(["git", "write-tree"], cwd=repo, capture_output=True, text=True).stdout.strip()
        if index != _rev(repo, f"{finalized.release_commit}^{{tree}}"):
            state.recoverable.append("the branch advanced but the working tree still holds the source tree")
    if finalized.stage == "tagged" and store.entries("releases", version):
        try:
            load_release(store, version)
        except BoardwrightError as exc:
            state.problems.append(str(exc))
            return state
        state.stage, state.detail = "published", f"published; {finalized.detail}"
    return state


def status_lines(repo: Path, store: ReleaseStore, version: str = "") -> list[str]:
    from .local_release import status_lines as store_lines

    lines = store_lines(store, version)
    if not store.exists():
        return lines
    for name in [version] if version else versions(store):
        state = version_state(repo, store, name)
        lines.append(f"Release {name}: {state.stage} ({state.detail})")
        lines += [f"  PROBLEM: {p}" for p in state.problems]
        lines += [f"  interrupted: {r}" for r in state.recoverable]
        lines.append(f"  next: {state.next_action}")
    return lines


def recover(config, store: ReleaseStore, version: str = "", *, echo=print) -> int:
    repo = Path(config.root)
    with store.locked("recover"):
        for path in store.remove_abandoned():
            echo(f"Removed interrupted store write {path.name}.")
        if store.verify_index():
            raise LocalReleaseError("The release-store index is inconsistent: " + "; ".join(store.verify_index()))
        for name in [version] if version else versions(store):
            for event, record_path in _missing_events(store, name):
                record = json.loads(record_path.read_text(encoding="utf-8"))
                extra = {"release_commit": record["release_commit"], "candidate_id": record["candidate_id"],
                         "bundle_sha256": sha256_file(record_path.parent / "release.bundle")} if event == "prepared" \
                    else {"zip_sha256": record["zip_sha256"], "sums_sha256": sha256_file(record_path.parent / "SHA256SUMS")}
                if event == "published" and sha256_file(record_path.parent / record["zip"]) != record["zip_sha256"]:
                    raise LocalReleaseError(f"The {name} package does not match its record; not recovered.")
                store.append_event({"event": event, "version": name,
                                    ("finalize_sha256" if event == "prepared" else "publish_sha256"): sha256_file(record_path),
                                    **extra, "recovered": True})
                echo(f"Recorded the missing {event} event for {name} (entry complete and checksummed).")
            state = version_state(repo, store, name)
            if state.problems:
                echo(f"{name}: not recovered: " + "; ".join(state.problems))
                continue
            if state.stage in ("prepared", "imported", "advanced", "tagged"):
                entry = store.entries("finalizations", name)[0].path
                record = json.loads((entry / FINALIZE_FILE).read_text(encoding="utf-8"))
                if import_release_commit(repo, entry, record, name):
                    echo(f"Imported release commit {record['release_commit'][:12]} into {pending_ref(name)}.")
                if _rev(repo, f"refs/heads/{record['branch']}") == record["release_commit"] \
                        and _rev(repo, "HEAD") == record["release_commit"]:
                    if sync_worktree(repo, record["source_sha"], record["release_commit"]):
                        echo(f"Updated the working tree to release commit {record['release_commit'][:12]}.")
            state = version_state(repo, store, name)
            echo(f"{name}: {state.stage}; next: {state.next_action}")
    return 0
