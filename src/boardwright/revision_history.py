# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .changelog import ReleaseSection, parse_releases, read_changelog
from .config import BoardwrightConfig


@dataclass(frozen=True)
class RevisionSlot:
    index: int
    version: str
    date: str
    title: str
    body: str
    revision: str = ""
    description: str = ""
    drawn_by: str = ""
    drawn_date: str = ""
    checked_by: str = ""
    checked_date: str = ""
    approved_by: str = ""
    approved_date: str = ""


def build_revision_slots(config: BoardwrightConfig) -> list[RevisionSlot]:
    settings = config.revision_history.get("revision_history", {})
    slot_count = int(settings.get("slots", 4))
    document_rows = _document_revision_rows(config)
    if document_rows:
        return _build_document_revision_slots(document_rows, slot_count)

    include_unreleased = bool(settings.get("include_unreleased_in_preview", True))
    return build_revision_slots_from_text(
        read_changelog(config.root),
        slot_count=slot_count,
        include_unreleased=include_unreleased,
    )


def build_revision_slots_from_text(
    changelog_text: str,
    slot_count: int = 4,
    include_unreleased: bool = True,
) -> list[RevisionSlot]:
    releases = parse_releases(changelog_text)

    selected: list[ReleaseSection] = []
    for release in releases:
        if release.name == "Unreleased" and not include_unreleased:
            continue
        if not release.body.strip():
            continue
        selected.append(release)
        if len(selected) == slot_count:
            break

    slots: list[RevisionSlot] = []
    for index in range(1, slot_count + 1):
        release = selected[index - 1] if index <= len(selected) else None
        if release is None:
            slots.append(_blank_slot(index))
            continue
        body = _release_body(release)
        slots.append(
            RevisionSlot(
                index=index,
                version=release.name,
                date=release.date or "",
                title=_release_title(release),
                body=body,
                revision=release.name,
                description=body,
            )
        )
    return slots


def write_revision_variables(config: BoardwrightConfig) -> Path:
    path = config.root / ".boardwright" / "revision_history_variables.env"
    lines: list[str] = []
    for slot in build_revision_slots(config):
        prefix = f"REVHIST_{slot.index}"
        values = {
            "TITLE": slot.title,
            "BODY": slot.body,
            "REVISION": slot.revision or slot.version,
            "DESCRIPTION": slot.description or slot.body,
            "DATE": slot.date,
            "DRAWN_BY": slot.drawn_by,
            "DRAWN_DATE": slot.drawn_date,
            "CHECKED_BY": slot.checked_by,
            "CHECKED_DATE": slot.checked_date,
            "APPROVED_BY": slot.approved_by,
            "APPROVED_DATE": slot.approved_date,
        }
        for suffix, value in values.items():
            lines.append(f"{prefix}_{suffix}={_quote(value)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def _document_revision_rows(config: BoardwrightConfig) -> list[dict[str, Any]]:
    data = getattr(config, "document_revisions", None) or {}
    document_revisions = data.get("document_revisions", {})
    rows: Any
    if isinstance(document_revisions, list):
        rows = document_revisions
    elif isinstance(document_revisions, dict):
        rows = document_revisions.get("rows", [])
    else:
        rows = []
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and any(str(value).strip() for value in row.values())]


def _build_document_revision_slots(rows: list[dict[str, Any]], slot_count: int) -> list[RevisionSlot]:
    slots: list[RevisionSlot] = []
    for index in range(1, slot_count + 1):
        row = rows[index - 1] if index <= len(rows) else None
        if row is None:
            slots.append(_blank_slot(index))
            continue
        revision = _row_value(row, "revision")
        description = _row_value(row, "description")
        date = _row_value(row, "date")
        slots.append(
            RevisionSlot(
                index=index,
                version=revision,
                date=date,
                title=_document_revision_title(revision, date),
                body=description,
                revision=revision,
                description=description,
                drawn_by=_row_value(row, "drawn_by"),
                drawn_date=_row_value(row, "drawn_date"),
                checked_by=_row_value(row, "checked_by"),
                checked_date=_row_value(row, "checked_date"),
                approved_by=_row_value(row, "approved_by"),
                approved_date=_row_value(row, "approved_date"),
            )
        )
    return slots


def _blank_slot(index: int) -> RevisionSlot:
    return RevisionSlot(index, "", "", "", "")


def _row_value(row: dict[str, Any], key: str) -> str:
    return str(row.get(key) or "").strip()


def _document_revision_title(revision: str, date: str) -> str:
    if revision and date:
        return f"{revision} - {date}"
    return revision or date


def _release_title(release: ReleaseSection) -> str:
    if release.name == "Unreleased":
        return "Unreleased"
    if release.date:
        return f"{release.name} - {release.date}"
    return release.name


def _release_body(release: ReleaseSection) -> str:
    sections: list[tuple[str, list[str]]] = []
    current_section = ""
    current_items: list[str] = []

    for line in release.body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("###"):
            if current_items:
                sections.append((current_section, current_items))
            current_section = stripped.lstrip("#").strip()
            current_items = []
            continue
        if stripped.startswith("-"):
            current_items.append(_normalize_bullet(stripped))

    if current_items:
        sections.append((current_section, current_items))

    lines: list[str] = []
    for section, items in sections:
        if section:
            lines.append(f"{section}:")
        lines.extend(f"  {item}" for item in items)
    return "\n".join(lines)


def _normalize_bullet(value: str) -> str:
    if not value.startswith("-"):
        return value
    return "- " + value.lstrip("-").strip()



def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
    return f'"{escaped}"'
