# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Assemble a release package: assets, identity, manifest, zip and notes.

Shared by ``release.yaml`` and the local release backend
(``docs/LOCAL_FIRST.md`` section 6). Replaces the shell in the "Generate
GitHub release markdown" and "Collect release assets" steps. The reference
shell is frozen in ``tests/fixtures/legacy_release_steps/`` and the tests
check this module against it.

Equivalence contract with the shell:

- ``MANIFEST.txt`` is byte-identical to
  ``find . -type f ! -name MANIFEST.txt -print0 | sort -z | xargs -0 sha256sum``
  (C.UTF-8 order, ``./`` prefixed paths, two-space separator).
- The zip has the same member names (directories included) as
  ``zip -r <zip> <release dir>``. The zip bytes are not reproduced: timestamps
  and compression differ. The controlled content is ``MANIFEST.txt``.
- The release notes are text-identical, given the same asset base URL.

Command line (used by the workflow)::

    python -m boardwright.release_package notes --tag T --variant V \\
        --repository owner/name [--asset-base-url URL] [--out RELEASE_NOTES.md]
    python -m boardwright.release_package collect --tag T [--release-dir D]
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import shutil
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .release_ledger import load_ledger

DEFAULT_RELEASE_DIR = "boardwright-release"
COPY_SET = (
    "Schematic", "Manufacturing", "assets", "HTML", "KiRI", "Testing", "Reports",
    "README.md", "NOTICE.md", "THIRD_PARTY_NOTICES.md", "LICENSE",
)
README_TEMPLATE = "boardwright_resources/kibot/resources/templates/readme.txt"


def release_zip_name(tag: str) -> str:
    return f"boardwright-{tag}-release.zip"


def source_zip_name(tag: str) -> str:
    return f"boardwright-{tag}-source.zip"


def github_asset_base_url(repository: str, tag: str, server: str = "https://github.com") -> str:
    return f"{server}/{repository}/releases/download/{tag}"


# ---------------------------------------------------------------- notes


def _render(root: Path, pattern: str) -> Path | None:
    """First ``assets/renders/*<pattern>*.png`` (case-insensitive) in C order."""

    renders = root / "assets" / "renders"
    if not renders.is_dir():
        return None
    names = sorted(
        f"assets/renders/{p.name}" for p in renders.iterdir()
        if p.is_file() and fnmatch.fnmatch(p.name.lower(), f"*{pattern}*.png")
    )
    return root / names[0] if names else None


def board_images(root: Path) -> tuple[Path | None, Path | None]:
    return _render(root, "top"), _render(root, "bottom")


def identity_section(root: Path, tag: str) -> str:
    record = next((r for r in load_ledger(root) if r.version == tag), None)
    if record is None:
        return "## Identities\n\nNo release ledger entry (legacy release).\n\n"
    lines = [
        "## Identities\n",
        "| Item | Value |\n|---|---|",
        f"| Project release | {record.version} |",
        f"| PCB revision | {record.pcb_revision} |",
        f"| Schematic revision | {record.schematic_revision} |",
        f"| Assembly drawing revision | {record.assembly_revision} |",
        f"| Fabrication drawing revision | {record.fabrication_revision} |",
        f"| Fabrication baseline | {record.fabrication_baseline.id} ({record.fabrication_baseline.status}) |",
        f"| Assembly baseline | {record.assembly_baseline.id} ({record.assembly_baseline.status}) |",
        f"| Change classes | {', '.join(record.change_classes) or 'initial'} |",
        f"| Source commit | {record.source_commit} |",
        f"\n{record.justification}\n",
    ]
    return "".join(line + "\n" for line in lines)


def release_notes(
    root: Path,
    tag: str,
    *,
    variant: str,
    repository: str,
    asset_base_url: str,
    source_archive: bool | None = None,
) -> str:
    """Release notes text. ``asset_base_url`` empty gives relative image links."""

    top, bottom = board_images(root)
    base = f"{asset_base_url}/" if asset_base_url else ""
    out = [f"Generated `{variant}` package for `{repository}`.\n", "\n"]
    if top or bottom:
        out += ["## Board Images\n", "\n", '<p align="center">\n']
        if top:
            out.append(f'  <img alt="Top board render" src="{base}{top.name}" width="45%">\n')
        if bottom:
            if top:
                out.append("&nbsp; &nbsp; &nbsp; &nbsp;\n")
            out.append(f'  <img alt="Bottom board render" src="{base}{bottom.name}" width="45%">\n')
        out += ["</p>\n", "\n"]
    out.append(identity_section(root, tag))
    out += ["## Changes\n", "\n", _awk_changelog(root, tag), "\n", "## Assets\n", "\n"]
    out.append(f"- Full release package: `{release_zip_name(tag)}`\n")
    if source_archive is None:
        source_archive = (root / source_zip_name(tag)).is_file()
    if source_archive:
        out.append(f"- Curated source package: `{source_zip_name(tag)}`\n")
    out.append(f"- Generated README: included in the release package and generated from `{README_TEMPLATE}`\n")
    return "".join(out)


def _awk_changelog(root: Path, tag: str) -> str:
    """Exactly what the workflow's awk printed: each captured line plus "\\n"."""

    path = root / "CHANGELOG.md"
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    records = text.split("\n")
    if text.endswith("\n"):
        records = records[:-1]  # awk has no record after the final newline
    out: list[str] = []
    capture = False
    for line in records:
        if not capture:
            if line == f"## [{tag}]" or line.startswith(f"## [{tag}] - "):
                capture = True
            continue
        if line.startswith("## ["):
            break
        out.append(line + "\n")
    return "".join(out)


def copy_board_images(root: Path, dest: Path) -> list[Path]:
    """Copy the chosen renders next to the notes (they are release assets)."""

    copied = []
    for image in board_images(root):
        if image is not None:
            target = dest / image.name
            if image.resolve() != target.resolve():
                shutil.copyfile(image, target)
            copied.append(target)
    return copied


# ---------------------------------------------------------------- package


@dataclass(frozen=True)
class Package:
    release_dir: Path
    manifest: Path
    zip_path: Path
    files: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _walk_files(top: Path) -> list[str]:
    """``find . -type f`` relative names (symlinks are not regular files)."""

    found = []
    for dirpath, dirnames, filenames in os.walk(top):
        rel = Path(dirpath).relative_to(top).as_posix()
        prefix = "./" if rel == "." else f"./{rel}/"
        for name in filenames:
            if not os.path.islink(os.path.join(dirpath, name)):
                found.append(prefix + name)
    return found


def _sha256sum_line(digest: str, name: str) -> str:
    # GNU sha256sum escapes "\\" and "\n" in names and flags the line with "\\".
    if "\\" in name or "\n" in name:
        return "\\" + digest + "  " + name.replace("\\", "\\\\").replace("\n", "\\n") + "\n"
    return f"{digest}  {name}\n"


def manifest_text(release_dir: Path) -> str:
    names = sorted(
        (n for n in _walk_files(release_dir) if n.rsplit("/", 1)[-1] != "MANIFEST.txt"),
        key=lambda n: n.encode("utf-8", "surrogateescape"),
    )
    return "".join(_sha256sum_line(_sha256(release_dir / n[2:]), n) for n in names)


def write_manifest(release_dir: Path) -> Path:
    path = release_dir / "MANIFEST.txt"
    path.write_bytes(manifest_text(release_dir).encode("utf-8", "surrogateescape"))
    return path


def identity_json(root: Path, tag: str) -> str:
    record = [r for r in load_ledger(root) if r.version == tag]
    return (json.dumps(record[0].as_dict(), indent=2) if record else "{}") + "\n"


def collect_assets(root: Path, tag: str, release_dir: Path) -> None:
    """Fill ``release_dir`` (recreated) as the workflow's copy step did."""

    if release_dir.exists():
        shutil.rmtree(release_dir)
    release_dir.mkdir(parents=True)
    for name in COPY_SET + (source_zip_name(tag),):
        source = root / name
        if source.is_symlink() or source.is_file():
            shutil.copy2(source, release_dir / name, follow_symlinks=False)
        elif source.is_dir():
            shutil.copytree(source, release_dir / name, symlinks=True)
    if (root / ".boardwright" / "releases.yaml").is_file():
        (release_dir / "RELEASE_IDENTITY.json").write_text(identity_json(root, tag), encoding="utf-8", newline="\n")
    accepted = root / ".boardwright" / "accepted_manifest.json"
    if accepted.is_file():
        shutil.copyfile(accepted, release_dir / "ACCEPTED_OUTPUT_MANIFEST.json")


def zip_members(release_dir: Path, arcroot: str) -> list[tuple[str, Path | None]]:
    """``zip -r`` member names: the root, every directory (with "/"), every file."""

    members: list[tuple[str, Path | None]] = [(f"{arcroot}/", None)]
    for dirpath, dirnames, filenames in os.walk(release_dir, followlinks=False):
        dirnames.sort()
        rel = Path(dirpath).relative_to(release_dir).as_posix()
        base = arcroot if rel == "." else f"{arcroot}/{rel}"
        for name in dirnames:
            members.append((f"{base}/{name}/", None))
        for name in sorted(filenames):
            members.append((f"{base}/{name}", Path(dirpath) / name))
    return members


def write_zip(release_dir: Path, zip_path: Path, arcroot: str | None = None) -> Path:
    arcroot = arcroot or release_dir.name
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, source in zip_members(release_dir, arcroot):
            if source is None:
                archive.writestr(zipfile.ZipInfo(name), b"")
            else:
                archive.write(source, name)  # zip -r follows symlinks, as here
    return zip_path


def build_package(root: Path, tag: str, *, release_dir: Path | None = None, zip_path: Path | None = None) -> Package:
    release_dir = release_dir or root / DEFAULT_RELEASE_DIR
    zip_path = zip_path or root / release_zip_name(tag)
    collect_assets(root, tag, release_dir)
    manifest = write_manifest(release_dir)
    write_zip(release_dir, zip_path)
    files = manifest.read_text(encoding="utf-8", errors="surrogateescape").count("\n")
    return Package(release_dir, manifest, zip_path, files)


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m boardwright.release_package")
    sub = parser.add_subparsers(dest="command", required=True)
    notes = sub.add_parser("notes", help="write the release notes and copy the board renders")
    notes.add_argument("--tag", required=True)
    notes.add_argument("--variant", required=True)
    notes.add_argument("--repository", required=True)
    notes.add_argument("--asset-base-url", default=None,
                       help="image URL prefix; default: the GitHub release download URL")
    notes.add_argument("--out", default="RELEASE_NOTES.md")
    collect = sub.add_parser("collect", help="collect assets, write MANIFEST.txt and the zip")
    collect.add_argument("--tag", required=True)
    collect.add_argument("--release-dir", default=DEFAULT_RELEASE_DIR)
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if args.command == "notes":
        base = args.asset_base_url if args.asset_base_url is not None else github_asset_base_url(args.repository, args.tag)
        text = release_notes(root, args.tag, variant=args.variant, repository=args.repository, asset_base_url=base)
        (root / args.out).write_text(text, encoding="utf-8", newline="\n")
        copy_board_images(root, root)
        return 0
    package = build_package(root, args.tag, release_dir=root / args.release_dir)
    print(f"wrote {package.zip_path.name}: {package.files} file(s) in MANIFEST.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
