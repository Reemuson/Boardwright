# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Hash manifests for generated outputs.

The prepare-release workflow writes ``.boardwright/accepted_manifest.json`` for
the outputs it generated and commits it with the release state. The publish
workflow regenerates outputs from the tag and verifies them against that
manifest, so the published package is provably the accepted one.

Each file records a raw SHA-256 (byte identity) and, for text fabrication and
assembly outputs, a normalised SHA-256 with creation timestamps and generator
banners removed (content identity). Byte-identical and normalised-equivalent
are reported separately; neither is silently treated as the other.

Usage::

    python -m boardwright.output_manifest write  [--root .] [--out PATH]
    python -m boardwright.output_manifest verify [--root .] [--manifest PATH]
    python -m boardwright.output_manifest compare OLD.json NEW.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MANIFEST_SCHEMA = 1
DEFAULT_MANIFEST = Path(".boardwright") / "accepted_manifest.json"
OUTPUT_ROOTS = ("Manufacturing", "Schematic", "Reports", "Testing")
CONTROLLED_CATEGORIES = ("fabrication", "assembly")

FAB_TEXT_SUFFIXES = {".gbr", ".gbrjob", ".drl", ".gbl", ".gtl", ".gto", ".gbo", ".gts", ".gbs", ".gtp", ".gbp", ".gm1", ".gko", ".xln", ".ipc", ".d356"}
FAB_TEXT_SUFFIXES |= {f".g{n}" for n in range(1, 33)}
ASSEMBLY_TEXT_SUFFIXES = {".csv", ".pos"}

_VOLATILE_LINE = re.compile(
    r"(TF\.CreationDate|TF\.GenerationSoftware|TF\.ProjectId|CreationDate|Created by KiCad|"
    r"^;\s*(DRILL file|FORMAT|#@!)|\bGenerated\b.*\d{4}-\d{2}-\d{2}|"
    r'^"?(Date|Generated|Creation ?Date|KiCad Version|Tool|Generator)"?\s*[:,])',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Difference:
    path: str
    kind: str  # missing | unexpected | changed | regenerated

    def __str__(self) -> str:
        labels = {
            "missing": "missing from the new outputs",
            "unexpected": "not present in the accepted outputs",
            "changed": "content differs after normalisation",
            "regenerated": "bytes differ but normalised content is identical",
        }
        return f"{self.path}: {labels[self.kind]}"


def category_for(relative: str) -> str:
    lowered = relative.lower()
    suffix = Path(lowered).suffix
    if lowered.startswith("manufacturing/fabrication/gerbers/") and suffix != ".zip":
        return "fabrication"
    if lowered.startswith("manufacturing/assembly/") and suffix in ASSEMBLY_TEXT_SUFFIXES:
        return "assembly"
    return "informational"


def normalised_bytes(relative: str, data: bytes) -> bytes:
    if category_for(relative) == "informational":
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    kept = [
        line.rstrip()
        for line in text.replace("\r\n", "\n").split("\n")
        if not _VOLATILE_LINE.search(line)
    ]
    return "\n".join(kept).encode("utf-8")


def build_manifest(root: Path, roots: tuple[str, ...] = OUTPUT_ROOTS) -> dict[str, Any]:
    files: dict[str, dict[str, str]] = {}
    for top in roots:
        base = root / top
        if not base.is_dir():
            continue
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            relative = path.relative_to(root).as_posix()
            data = path.read_bytes()
            files[relative] = {
                "category": category_for(relative),
                "sha256": hashlib.sha256(data).hexdigest(),
                "normalized_sha256": hashlib.sha256(normalised_bytes(relative, data)).hexdigest(),
                "size": str(len(data)),
            }
    return {"schema": MANIFEST_SCHEMA, "files": files}


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read manifest {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != MANIFEST_SCHEMA or not isinstance(data.get("files"), dict):
        raise ValueError(f"Manifest {path} is not a schema {MANIFEST_SCHEMA} Boardwright manifest.")
    return data


def compare_manifests(
    accepted: dict[str, Any],
    current: dict[str, Any],
    categories: tuple[str, ...] = CONTROLLED_CATEGORIES,
) -> list[Difference]:
    def pick(manifest: dict[str, Any]) -> dict[str, dict[str, str]]:
        return {
            path: entry
            for path, entry in manifest["files"].items()
            if entry.get("category") in categories
        }

    old, new = pick(accepted), pick(current)
    differences: list[Difference] = []
    for path in sorted(set(old) | set(new)):
        if path not in new:
            differences.append(Difference(path, "missing"))
        elif path not in old:
            differences.append(Difference(path, "unexpected"))
        elif old[path]["normalized_sha256"] != new[path]["normalized_sha256"]:
            differences.append(Difference(path, "changed"))
        elif old[path]["sha256"] != new[path]["sha256"]:
            differences.append(Difference(path, "regenerated"))
    return differences


def blocking(differences: list[Difference]) -> list[Difference]:
    return [item for item in differences if item.kind != "regenerated"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m boardwright.output_manifest")
    sub = parser.add_subparsers(dest="command", required=True)
    write = sub.add_parser("write", help="Write a manifest for the current outputs.")
    write.add_argument("--root", default=".")
    write.add_argument("--out", default=str(DEFAULT_MANIFEST))
    verify = sub.add_parser("verify", help="Verify current outputs against an accepted manifest.")
    verify.add_argument("--root", default=".")
    verify.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    compare = sub.add_parser("compare", help="Compare two manifests.")
    compare.add_argument("old")
    compare.add_argument("new")
    check = sub.add_parser(
        "check-baseline",
        help="For reused baselines in the newest ledger entry, compare outputs with the previous release tag.",
    )
    check.add_argument("--root", default=".")
    args = parser.parse_args(argv)

    if args.command == "check-baseline":
        return _check_baseline(Path(args.root))

    if args.command == "write":
        root = Path(args.root)
        manifest = build_manifest(root)
        out = Path(args.out)
        out = out if out.is_absolute() else root / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        counts: dict[str, int] = {}
        for entry in manifest["files"].values():
            counts[entry["category"]] = counts.get(entry["category"], 0) + 1
        print(f"wrote {out} ({', '.join(f'{k}: {v}' for k, v in sorted(counts.items())) or 'no files'})")
        if not counts.get("fabrication"):
            print("warning: no fabrication outputs found under Manufacturing/Fabrication/Gerbers", file=sys.stderr)
        return 0

    try:
        if args.command == "verify":
            root = Path(args.root)
            path = Path(args.manifest)
            accepted = load_manifest(path if path.is_absolute() else root / path)
            current = build_manifest(root)
        else:
            accepted = load_manifest(Path(args.old))
            current = load_manifest(Path(args.new))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    differences = compare_manifests(accepted, current)
    hard = blocking(differences)
    for item in differences:
        print(("ERROR " if item in hard else "note  ") + str(item))
    if hard:
        print(f"{len(hard)} controlled output(s) differ.", file=sys.stderr)
        return 1
    soft = len(differences)
    print(
        "Controlled outputs are byte-identical."
        if not soft
        else f"Controlled outputs are equivalent after normalisation ({soft} regenerated file(s))."
    )
    return 0


def _check_baseline(root: Path) -> int:
    import subprocess

    from .release_ledger import load_ledger

    records = load_ledger(root)
    if not records:
        print("No release ledger entries; nothing to compare.")
        return 0
    record = records[-1]
    categories = tuple(
        name
        for name, baseline in (("fabrication", record.fabrication_baseline), ("assembly", record.assembly_baseline))
        if baseline.status == "reused"
    )
    print(
        f"Release {record.version}: fabrication baseline {record.fabrication_baseline.id} "
        f"({record.fabrication_baseline.status}), assembly baseline {record.assembly_baseline.id} "
        f"({record.assembly_baseline.status})."
    )
    if not categories:
        print("No reused baselines; new outputs become the approved baseline.")
        return 0
    if not record.previous_version:
        print("error: reused baseline without a previous release version.", file=sys.stderr)
        return 1
    completed = subprocess.run(
        ["git", "show", f"refs/tags/{record.previous_version}:{DEFAULT_MANIFEST.as_posix()}"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        print(
            f"warning: release {record.previous_version} has no committed output manifest; reuse is "
            "supported by source fingerprints only (legacy release).",
        )
        return 0
    try:
        previous = json.loads(completed.stdout.decode("utf-8"))
    except json.JSONDecodeError as exc:
        print(f"error: previous manifest is invalid: {exc}", file=sys.stderr)
        return 2
    differences = compare_manifests(previous, build_manifest(root), categories)
    hard = blocking(differences)
    for item in differences:
        print(("ERROR " if item in hard else "note  ") + str(item))
    if hard:
        print(
            f"{len(hard)} output(s) differ from approved release {record.previous_version} although the "
            f"ledger claims the {'/'.join(categories)} baseline is reused. Investigate; if the change is "
            "real, re-classify the release (fabrication reissue or new revision).",
            file=sys.stderr,
        )
        return 1
    print(
        f"{'/'.join(categories).capitalize()} outputs match approved release {record.previous_version}"
        + (" byte-for-byte." if not differences else f" after normalisation ({len(differences)} regenerated file(s)).")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
