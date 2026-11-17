# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks

"""Design fingerprints used for release change-impact assessment.

A fingerprint is a SHA-256 over a *normalised* view of the KiCad sources. Each
domain answers one engineering question:

``pcb_geometry``
    Is the physical board (copper, mask, paste, silk, edge, drills, stackup)
    the same? Net *names*, UUIDs, timestamps, title blocks, assembly/fab
    documentation layers, 3D models and formatting are excluded. Nets are
    identified by the pads they connect, so a net rename alone does not change
    this hash.
``pcb_netnames``
    Do net names differ? Net names appear in IPC-D-356/ODB++ and Gerber X2
    attributes, so a rename changes fabrication *data* but not copper.
``fabrication_spec``
    Do the fabrication requirements in ``project.yaml`` differ (material,
    finish, fab notes, impedance)?
``assembly``
    Do populated parts, values, footprints, placement, DNP/BOM flags, BOM
    fields or assembly notes differ?
``schematic``
    Does the schematic content differ (title blocks excluded)?

Equal source fingerprints mean the design inputs are equivalent; they do not
prove byte-identical generated Gerbers. Generated-output comparison is handled
separately by :mod:`boardwright.output_manifest`.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .errors import BoardwrightError
from .sexpr import Node, SList, canonical, parse, unquote

FINGERPRINT_SCHEMA = 1

# Layers that are not part of the bare-board fabrication deliverable.
NON_FAB_LAYER_SUFFIXES = (".Fab", ".CrtYd")
NON_FAB_LAYERS = {
    "Dwgs.User",
    "Cmts.User",
    "Eco1.User",
    "Eco2.User",
    "Margin",
}

PCB_DROP_HEADS = {
    "uuid",
    "tstamp",
    "path",
    "sheetname",
    "sheetfile",
    "model",
    "attr",
    "locked",
    "unlocked",
    "descr",
    "tags",
    "pintype",
    "pinfunction",
    "generator",
    "generator_version",
    "group",
    "embedded_fonts",
    "outputdirectory",
    # Editor-only state; aux_axis_origin is kept because plots may use it.
    "grid_origin",
}
PCB_TOP_DROP_HEADS = {"version", "title_block", "paper", "property", "net", "general"}

SCH_DROP_HEADS = {"uuid", "generator", "generator_version", "title_block", "version"}

ASSEMBLY_EXCLUDED_MANUFACTURING_KEYS = {"assembly_notes", "testpoint_policy"}
FABRICATION_EXCLUDED_MANUFACTURING_KEYS = ASSEMBLY_EXCLUDED_MANUFACTURING_KEYS


class SourceReader(Protocol):
    label: str

    def list_files(self) -> list[str]: ...

    def read_text(self, path: str) -> str | None: ...


@dataclass(frozen=True)
class WorkingTreeReader:
    root: Path
    label: str = "working tree"

    def list_files(self) -> list[str]:
        return sorted(path.name for path in self.root.iterdir() if path.is_file())

    def read_text(self, path: str) -> str | None:
        target = self.root / path
        if not target.is_file():
            return None
        return target.read_text(encoding="utf-8")


@dataclass(frozen=True)
class GitRefReader:
    root: Path
    ref: str
    label: str = ""

    def __post_init__(self) -> None:
        if not self.label:
            object.__setattr__(self, "label", self.ref)

    def list_files(self) -> list[str]:
        completed = _git(self.root, "ls-tree", "--name-only", self.ref)
        return sorted(line for line in completed.splitlines() if line)

    def read_text(self, path: str) -> str | None:
        try:
            return _git(self.root, "show", f"{self.ref}:{path}")
        except BoardwrightError:
            return None


@dataclass(frozen=True)
class DesignFingerprint:
    schema: int
    source: str
    pcb_geometry: str
    pcb_netnames: str
    fabrication_spec: str
    assembly: str
    schematic: str
    pcb_format: str = ""
    schematic_format: str = ""
    pcb_file: str = ""
    schematic_files: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["schematic_files"] = list(self.schematic_files)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DesignFingerprint":
        if not isinstance(data, dict):
            raise BoardwrightError("Fingerprint record must be a mapping.")
        try:
            return cls(
                schema=int(data.get("schema", 0)),
                source=str(data.get("source", "")),
                pcb_geometry=str(data["pcb_geometry"]),
                pcb_netnames=str(data["pcb_netnames"]),
                fabrication_spec=str(data["fabrication_spec"]),
                assembly=str(data["assembly"]),
                schematic=str(data["schematic"]),
                pcb_format=str(data.get("pcb_format", "")),
                schematic_format=str(data.get("schematic_format", "")),
                pcb_file=str(data.get("pcb_file", "")),
                schematic_files=tuple(str(item) for item in data.get("schematic_files", []) or []),
            )
        except KeyError as exc:
            raise BoardwrightError(f"Fingerprint record is missing `{exc.args[0]}`.") from exc


def compute_fingerprint(reader: SourceReader) -> DesignFingerprint:
    files = reader.list_files()
    pcb_files = [name for name in files if name.endswith(".kicad_pcb")]
    if len(pcb_files) > 1:
        raise BoardwrightError(
            f"Expected at most one root .kicad_pcb in {reader.label}; found {len(pcb_files)}."
        )
    pcb_name = pcb_files[0] if pcb_files else ""
    pcb = _parse_named(reader.read_text(pcb_name) or "", pcb_name) if pcb_name else SList(["kicad_pcb"])

    schematic_names = sorted(name for name in files if name.endswith(".kicad_sch"))
    schematics = [(name, _parse_named(reader.read_text(name) or "", name)) for name in schematic_names]

    project_yaml = reader.read_text(".boardwright/project.yaml")
    manufacturing = _manufacturing_settings(project_yaml)

    net_labels = _net_labels(pcb)
    geometry = _normalise_pcb(pcb, net_labels)
    netnames = sorted(
        (label, name) for name, label in net_labels.items() if name
    )
    fab_spec = {
        key: value
        for key, value in sorted(manufacturing.items())
        if key not in FABRICATION_EXCLUDED_MANUFACTURING_KEYS
    }
    assembly = {
        "footprints": _pcb_assembly_rows(pcb),
        "symbols": sorted(row for _, tree in schematics for row in _schematic_bom_rows(tree)),
        "notes": {
            key: manufacturing.get(key, "") for key in sorted(ASSEMBLY_EXCLUDED_MANUFACTURING_KEYS)
        },
    }
    schematic_canonical = [
        (name, canonical(_strip(tree, SCH_DROP_HEADS))) for name, tree in schematics
    ]

    return DesignFingerprint(
        schema=FINGERPRINT_SCHEMA,
        source=reader.label,
        pcb_geometry=_hash(canonical(geometry)),
        pcb_netnames=_hash(json.dumps(netnames)),
        fabrication_spec=_hash(json.dumps(fab_spec, sort_keys=True, default=str)),
        assembly=_hash(json.dumps(assembly, sort_keys=True, default=str)),
        schematic=_hash(json.dumps(schematic_canonical)),
        pcb_format=_format_label(pcb),
        schematic_format=",".join(sorted({_format_label(tree) for _, tree in schematics})),
        pcb_file=pcb_name,
        schematic_files=tuple(schematic_names),
    )


def fingerprint_working_tree(root: Path) -> DesignFingerprint:
    return compute_fingerprint(WorkingTreeReader(root))


def fingerprint_git_ref(root: Path, ref: str) -> DesignFingerprint:
    return compute_fingerprint(GitRefReader(root, ref))


# --- PCB normalisation ---------------------------------------------------


def _net_labels(pcb: SList) -> dict[str, str]:
    """Map net name -> rename-invariant label based on connected pads."""
    numbers: dict[str, str] = {}
    for net in pcb.children("net"):
        if len(net.items) >= 3:
            numbers[net.items[1]] = net.value(2)

    members: dict[str, list[str]] = {}
    for footprint in pcb.children("footprint"):
        reference = _footprint_reference(footprint)
        for pad in footprint.children("pad"):
            name = _pad_net_name(pad, numbers)
            if name is None:
                continue
            members.setdefault(name, []).append(f"{reference}:{pad.value(1)}")

    labels: dict[str, str] = {}
    for name in set(numbers.values()) | set(members):
        pads = sorted(members.get(name, []))
        labels[name] = f"pads[{pads[0]}]" if pads else f"name[{name}]"
    labels[""] = "unconnected"
    return labels


def _pad_net_name(pad: SList, numbers: dict[str, str]) -> str | None:
    net = pad.child("net")
    if net is None or len(net.items) < 2:
        return None
    if len(net.items) >= 3:
        return net.value(2)
    first = net.items[1]
    if isinstance(first, str) and first.startswith('"'):
        return unquote(first)
    return numbers.get(str(first), "")


def _normalise_pcb(pcb: SList, net_labels: dict[str, str]) -> SList:
    numbers = {
        net.items[1]: net.value(2) for net in pcb.children("net") if len(net.items) >= 3
    }
    kept: list[Node] = []
    for item in pcb.items:
        if isinstance(item, SList) and item.head in PCB_TOP_DROP_HEADS:
            continue
        kept.append(item)
    return _normalise_pcb_node(SList(kept), net_labels, numbers)


def _normalise_pcb_node(node: SList, net_labels: dict[str, str], numbers: dict[str, str]) -> SList:
    out: list[Node] = []
    is_footprint = node.head == "footprint"
    for position, item in enumerate(node.items):
        if is_footprint and position == 1:
            # Library identifier: renaming a library entry is not physical.
            continue
        if isinstance(item, str):
            out.append(item)
            continue
        if item.head in PCB_DROP_HEADS:
            continue
        if item.head == "property" and is_footprint:
            if not _fab_visible_property(item):
                continue
        elif _only_non_fab_layers(item):
            continue
        if item.head == "net":
            out.append(SList(["net", _net_label_for(item, net_labels, numbers)]))
            continue
        if item.head == "net_name":
            out.append(SList(["net_name", net_labels.get(item.value(1), "unconnected")]))
            continue
        out.append(_normalise_pcb_node(item, net_labels, numbers))
    return SList(out)


def _net_label_for(net: SList, net_labels: dict[str, str], numbers: dict[str, str]) -> str:
    if len(net.items) >= 3:
        name = net.value(2)
    elif len(net.items) == 2 and isinstance(net.items[1], str):
        token = net.items[1]
        name = unquote(token) if token.startswith('"') else numbers.get(token, "")
    else:
        name = ""
    return net_labels.get(name, f"name[{name}]")


def _layers_of(node: SList) -> list[str]:
    layers: list[str] = []
    for child in node.items:
        if isinstance(child, SList) and child.head in {"layer", "layers"}:
            layers.extend(unquote(item) for item in child.items[1:] if isinstance(item, str))
    return layers


def _is_fab_layer(layer: str) -> bool:
    if layer in NON_FAB_LAYERS or layer.startswith("User."):
        return False
    return not layer.endswith(NON_FAB_LAYER_SUFFIXES)


def _only_non_fab_layers(node: SList) -> bool:
    layers = _layers_of(node)
    return bool(layers) and not any(_is_fab_layer(layer) for layer in layers)


def _fab_visible_property(node: SList) -> bool:
    layers = _layers_of(node)
    if not layers or not any(_is_fab_layer(layer) for layer in layers):
        return False
    return not _is_hidden(node)


def _is_hidden(node: SList) -> bool:
    for item in node.items:
        if item == "hide":
            return True
        if isinstance(item, SList):
            if item.head == "hide" and (len(item.items) == 1 or item.value(1) == "yes"):
                return True
            if item.head == "effects" and _is_hidden(item):
                return True
    return False


def _footprint_reference(footprint: SList) -> str:
    for prop in footprint.children("property"):
        if prop.value(1) == "Reference":
            return prop.value(2)
    for text in footprint.children("fp_text"):
        if text.value(1) == "reference":
            return text.value(2)
    return "?"


def _pcb_assembly_rows(pcb: SList) -> list[list[str]]:
    rows: list[list[str]] = []
    for footprint in pcb.children("footprint"):
        properties = {
            prop.value(1): prop.value(2) for prop in footprint.children("property")
        }
        layer = footprint.child("layer")
        at = footprint.child("at")
        attr = footprint.child("attr")
        rows.append(
            [
                _footprint_reference(footprint),
                footprint.value(1),
                properties.get("Value", ""),
                layer.value(1) if layer else "",
                canonical(at) if at else "",
                canonical(attr) if attr else "",
                json.dumps(
                    {k: v for k, v in sorted(properties.items()) if k not in {"Reference", "Value", "Datasheet", "Description"}},
                    sort_keys=True,
                ),
            ]
        )
    return sorted(rows)


# --- Schematic -------------------------------------------------------------

BOM_IGNORED_PROPERTIES = {"Datasheet", "Description", "ki_description", "ki_keywords", "Sim.Device", "Sim.Pins", "Sim.Type", "Sim.Params"}


def _schematic_bom_rows(tree: SList) -> list[str]:
    rows: list[str] = []
    for symbol in tree.children("symbol"):
        lib_id = symbol.child("lib_id")
        if lib_id is None:
            continue
        properties = {
            prop.value(1): prop.value(2)
            for prop in symbol.children("property")
            if prop.value(1) not in BOM_IGNORED_PROPERTIES
        }
        flags = {
            head: symbol.child(head).value(1)
            for head in ("in_bom", "on_board", "dnp", "exclude_from_sim")
            if symbol.child(head) is not None
        }
        flags.pop("exclude_from_sim", None)
        references = sorted(_instance_references(symbol)) or [properties.get("Reference", "")]
        unit = symbol.child("unit")
        for reference in references:
            data = dict(properties)
            data["Reference"] = reference
            if properties.get("Reference", "").startswith("#"):
                break
            rows.append(
                json.dumps(
                    {"lib_id": lib_id.value(1), "unit": unit.value(1) if unit else "", "props": data, "flags": flags},
                    sort_keys=True,
                )
            )
    return rows


def _instance_references(symbol: SList) -> list[str]:
    references: list[str] = []
    instances = symbol.child("instances")
    if instances is None:
        return references
    for project in instances.children("project"):
        for path in project.children("path"):
            reference = path.child("reference")
            if reference is not None:
                references.append(reference.value(1))
    return references


# --- helpers ---------------------------------------------------------------


def _strip(node: SList, drop: set[str]) -> SList:
    out: list[Node] = []
    for item in node.items:
        if isinstance(item, SList):
            if item.head in drop:
                continue
            out.append(_strip(item, drop))
        else:
            out.append(item)
    return SList(out)


def _format_label(tree: SList) -> str:
    version = tree.child("version")
    generator = tree.child("generator_version")
    parts = [version.value(1) if version else ""]
    if generator is not None:
        parts.append(generator.value(1))
    return "/".join(part for part in parts if part)


def _parse_named(text: str, name: str) -> SList:
    try:
        return parse(text)
    except BoardwrightError as exc:
        raise BoardwrightError(f"Cannot parse {name}: {exc}") from exc


def _manufacturing_settings(project_yaml: str | None) -> dict[str, Any]:
    if not project_yaml:
        return {}
    try:
        import yaml
    except ImportError:  # pragma: no cover - PyYAML is a declared dependency
        from .config import _read_simple_yaml

        data = _read_simple_yaml(project_yaml)
    else:
        data = yaml.safe_load(project_yaml) or {}
    manufacturing = data.get("manufacturing", {}) if isinstance(data, dict) else {}
    return manufacturing if isinstance(manufacturing, dict) else {}


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _git(root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BoardwrightError(f"git {' '.join(args)} failed: {exc}") from exc
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", "replace").strip()
        raise BoardwrightError(f"git {' '.join(args)} failed: {stderr}")
    return completed.stdout.decode("utf-8")


def changed_domains(old: DesignFingerprint, new: DesignFingerprint) -> tuple[str, ...]:
    domains = ("pcb_geometry", "pcb_netnames", "fabrication_spec", "assembly", "schematic")
    return tuple(name for name in domains if getattr(old, name) != getattr(new, name))


def schematic_pcb_parity(reader: SourceReader) -> list[str]:
    """Report populated-part disagreements between schematic and PCB.

    Returns human-readable mismatches. An empty PCB (no footprints) is treated
    as "layout not started" and yields no mismatches.
    """
    files = reader.list_files()
    pcb_files = [name for name in files if name.endswith(".kicad_pcb")]
    if len(pcb_files) > 1:
        return [f"Expected at most one root .kicad_pcb; found {len(pcb_files)}."]
    if not pcb_files:
        return []
    pcb = _parse_named(reader.read_text(pcb_files[0]) or "", pcb_files[0])
    pcb_parts: dict[str, tuple[str, str]] = {}
    for footprint in pcb.children("footprint"):
        properties = {prop.value(1): prop.value(2) for prop in footprint.children("property")}
        pcb_parts[_footprint_reference(footprint)] = (properties.get("Value", ""), footprint.value(1))
    if not pcb_parts:
        return []
    sch_parts: dict[str, tuple[str, str]] = {}
    for name in sorted(n for n in files if n.endswith(".kicad_sch")):
        tree = _parse_named(reader.read_text(name) or "", name)
        for row in _schematic_bom_rows(tree):
            data = json.loads(row)
            if data["flags"].get("on_board") == "no":
                continue
            props = data["props"]
            sch_parts[props["Reference"]] = (props.get("Value", ""), props.get("Footprint", ""))
    problems: list[str] = []
    for ref in sorted(set(sch_parts) - set(pcb_parts)):
        problems.append(f"{ref} is in the schematic but not on the PCB.")
    for ref in sorted(set(pcb_parts) - set(sch_parts)):
        if ref.startswith(("#", "REF**")):
            continue
        problems.append(f"{ref} is on the PCB but not in the schematic.")
    for ref in sorted(set(pcb_parts) & set(sch_parts)):
        sch_value, sch_fp = sch_parts[ref]
        pcb_value, pcb_fp = pcb_parts[ref]
        if sch_value != pcb_value:
            problems.append(f"{ref} value differs: schematic {sch_value!r}, PCB {pcb_value!r}.")
        if sch_fp and pcb_fp and sch_fp != pcb_fp:
            problems.append(f"{ref} footprint differs: schematic {sch_fp!r}, PCB {pcb_fp!r}.")
    return problems


__all__ = [
    "DesignFingerprint",
    "GitRefReader",
    "WorkingTreeReader",
    "changed_domains",
    "compute_fingerprint",
    "fingerprint_git_ref",
    "fingerprint_working_tree",
    "schematic_pcb_parity",
]
