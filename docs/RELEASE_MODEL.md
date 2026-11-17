<!-- SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available -->
<!-- Copyright (c) Ryan Hicks -->

# Release And Revision Model

This document defines how Boardwright identifies, classifies, and verifies a
release. It is the authoritative source for release identity rules; `SPEC.md`
links here.

## Identities

| Identity | Example | Source of truth | Changes when |
| --- | --- | --- | --- |
| Project release version | `1.0.1` | Git tag + `.boardwright/releases.yaml` | Any published package. |
| PCB revision | `A` | `project.board_revision` | The physical bare board changes. |
| Schematic revision | `A.1` | `documents.schematic.revision` | The schematic document changes. |
| Assembly drawing revision | `B` | `documents.assembly.revision` | BOM, population, placement or assembly notes change. |
| Fabrication drawing revision | `A` | `documents.fabrication.revision` | Fabrication data or requirements change. |
| Fabrication baseline | `A/1` | Ledger (computed) | Approved bare-board fabrication package issue. |
| Assembly baseline | `A-B` | Ledger (computed) | Approved assembly package. |

A **release** is a published package. A **revision** identifies a controlled
design or document. A **variant** (`DRAFT`, `CHECKED`, `RELEASED`, …) selects
generated outputs and is not an identity. A **baseline** is an approved set of
manufacturing inputs and outputs that later releases may reuse unchanged.

Publishing never edits `project.board_revision`. Release preparation edits only
the revision of the document being released (usually the schematic) and
appends to the ledger.

KiCad text variables: `${RELEASE_VERSION}`, `${PCB_REVISION}` (alias of
`${BOARD_REVISION}`), `${SCHEMATIC_REVISION}`, and `${DRAWING_REVISION}` (the
current document's own revision). See [`FIELDS.md`](FIELDS.md).

### Schematic revision scheme

`documents.schematic.revision_scheme` may be `lettered` (default, `A`, `B`, …)
or `board_minor` (`A.0`, `A.1`, `B.0`). With `board_minor` the schematic
revision must begin with the current PCB revision; this is enforced.

## Change classes

Every release after the first declares one or more classes and a justification.

| Class | Meaning |
| --- | --- |
| `documentation` | Schematic/drawing presentation or text; no physical or BOM change. |
| `electrical` | Schematic electrical design change. |
| `fabrication` | Bare-PCB fabrication data or requirements change. |
| `assembly` | BOM, population, placement or assembly requirements change. |
| `tooling` | KiCad/KiBot format, scripts or metadata only. |

## Detected changes

Boardwright compares the working tree with the latest approved baseline (the
newest ledger entry, or for projects released before the ledger, the newest
semantic-version tag read directly from Git). It computes normalised SHA-256
fingerprints (`src/boardwright/fingerprint.py`):

| Domain | Includes | Excludes |
| --- | --- | --- |
| `pcb_geometry` | Copper, mask, paste, silk, edge cuts, drills, zones, stackup, plot parameters. Nets are identified by the pads they connect. | Net names, UUIDs, timestamps, title block, `*.Fab`/`*.CrtYd`/`User.*` layers, hidden or Fab-layer footprint fields, 3D models, footprint library names, formatting. |
| `pcb_netnames` | Net names. These appear in IPC-D-356, ODB++ and Gerber X2 attributes. | — |
| `fabrication_spec` | `manufacturing.*` settings except assembly and test-point notes. | — |
| `assembly` | Footprint reference, value, library, side, position, attributes and fields; schematic symbol BOM fields and DNP/BOM/board flags; assembly notes. | Datasheet, description, simulation fields. |
| `schematic` | All root `.kicad_sch` content. | Title blocks, UUIDs, generator/version. |

The KiCad file format version is tracked separately. A format change alone
requires the `tooling` class.

Before any release, the schematic and PCB must agree on populated references,
values and footprints. A schematic value correction that has not been synced to
the PCB blocks the release.

## Rules

| Detected | Required |
| --- | --- |
| `pcb_geometry` changed | `fabrication` class **and** a new PCB revision. The fabrication baseline is `new`. |
| PCB revision changed, geometry unchanged | Rejected unless `fabrication` is declared (deliberate re-spin; flagged for review). |
| `pcb_netnames` or `fabrication_spec` changed only | `fabrication` class and a new fabrication drawing revision. The PCB revision is retained and the fabrication baseline is `reissued` (`A/2`). |
| No fabrication input changed | Fabrication baseline `reused`; declaring `fabrication` is rejected. |
| `assembly` changed | `assembly` class and a new assembly drawing revision. |
| `schematic` changed | `documentation` or `electrical` class and a new schematic revision. |
| Schematic revision changed, schematic unchanged | `documentation` required (for example, title-block-only corrections). |
| `electrical` declared, PCB and BOM unchanged | Allowed, with a review item to confirm the as-built PCB still implements the schematic. |
| KiCad format changed | `tooling` class, with a review item that regenerated outputs may not be byte-identical. |
| PCB revision previously released with different geometry | Rejected: a revision identifier is never reused. |

Ambiguous situations produce review items in the checklist. They do not
silently pass.

## Release ledger

`.boardwright/releases.yaml` is append-only. Each entry records the version,
date, reviewed source commit, previous release, variant, all identities,
change classes, justification, both baselines, and the fingerprints.
Boardwright refuses to release when an entry that exists at a release tag has
since been modified or removed, or when the version is already recorded.

## Output evidence

`prepare-release.yaml` writes `.boardwright/accepted_manifest.json` (raw and
normalised SHA-256 for every generated file) and commits it with the release
state. Normalisation removes creation dates, generator banners and Gerber
`TF.ProjectId` only. Then:

1. When the ledger says a baseline is `reused`, the fabrication (and/or
   assembly) outputs are compared with the previous release's committed
   manifest. A content difference fails the run before anything is pushed.
2. `release.yaml` regenerates outputs from the tag and verifies them against
   the accepted manifest before publishing.
3. The package includes `MANIFEST.txt` (SHA-256 per file),
   `RELEASE_IDENTITY.json`, and `ACCEPTED_OUTPUT_MANIFEST.json`.

Results distinguish *byte-identical* from *equivalent after normalisation*
(`regenerated`). Historical packages are never overwritten. A published
release is never re-published.

Releases prepared before manifests existed have source-fingerprint evidence
only. The workflows report this as a warning instead of a pass.

## Worked example: documentation-only correction

PCB revision `A` is released as `1.0.0` with schematic `A.0`. A DFM review finds
a schematic note error.

1. Correct the schematic note on `dev`. Do not edit `board_revision`.
2. Review and accept to `main` as usual.
3. Check the plan:

   ```sh
   boardwright release 1.0.1 --drawing-revision A.1 \
     --change-class documentation --justification "Correct C1 voltage note (DFM review)"
   ```

   Expected: `PCB revision: A (unchanged)`,
   `Schematic revision: A.1 (A.0 -> A.1)`,
   `Fabrication baseline: A/1 (reused)`, and no problems.
4. Dispatch with the same arguments plus `--dispatch --revision-description ...`,
   or use the TUI Release screen.

If the correction requires a value change, the PCB footprint value must be
updated too (parity). The `assembly` class and a new assembly drawing revision
are then required. The fabrication baseline is still reused because a value
change does not alter the bare board.

## Recovery from partial releases

| Failure point | State | Recovery |
| --- | --- | --- |
| Before "Commit accepted release state" | Nothing pushed. | Fix the problem and re-dispatch. |
| Push to `main` rejected | `main` moved after checkout; nothing pushed. | Review the new commits, then re-dispatch with the new `source_sha`. |
| Tag push failed | The release commit is on `main`, with no tag. | The workflow prints the exact `git tag -a … && git push` commands. Then dispatch `release.yaml` on the tag. Do not re-run prepare: the ledger already records the version. |
| `release.yaml` failed | Tag exists, no published release. | Re-run `release.yaml` on the tag. It is serialised per tag and refuses to overwrite a published release. |
| Output verification failed in `release.yaml` | Tag exists; outputs differ from accepted state. | Investigate tool drift (for example, a moved KiBot image). Do not publish. Prepare a new release version. |
