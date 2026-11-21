#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Print one ``REVTABLE_*`` title-block value for KiBot's set_text_variables.

``--document schematic`` (the default) reads the recorded schematic table in
``.boardwright/revision_history_variables.env``. ``--document fabrication``
or ``assembly`` computes that drawing's own table from
``.boardwright/document_revisions.yaml`` at render time, so each PCB drawing
shows its own history without any file being written.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("variable")
    parser.add_argument(
        "-f",
        "--file",
        default=".boardwright/revision_history_variables.env",
        help="Revision history variable file (schematic table).",
    )
    parser.add_argument(
        "-d",
        "--document",
        default="schematic",
        choices=("schematic", "fabrication", "assembly"),
        help="Drawing whose revision table is printed.",
    )
    parser.add_argument("--root", default=".", help="Project root.")
    args = parser.parse_args()

    if args.document == "schematic":
        values = read_env(Path(args.file))
    else:
        values = document_values(Path(args.root), args.document)
    print(values.get(args.variable, ""))
    return 0


def document_values(root: Path, document: str) -> dict[str, str]:
    src = root / "src"
    if src.is_dir():
        sys.path.insert(0, str(src.resolve()))
    from boardwright.config import load_config
    from boardwright.revision_history import revision_variables

    return revision_variables(load_config(root.resolve(), local=False), document)


def read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = unquote(value.strip())
    return values


def unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    return value.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")


if __name__ == "__main__":
    raise SystemExit(main())
