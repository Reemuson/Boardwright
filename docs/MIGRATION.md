<!-- SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available -->
<!-- Copyright (c) Ryan Hicks -->

# Migration Guide

## Existing projects: release ledger and change classes

No configuration change is required. Projects without
`.boardwright/releases.yaml` keep working:

- The first release prepared with this version reconstructs its baseline,
  read-only, from the newest semantic-version tag. It uses the tagged
  `.kicad_pcb`, `.kicad_sch` and `project.yaml` (including
  `project.board_revision` at that tag). The checklist marks this baseline as
  *legacy*.
- Projects with no tags make an initial release. That release establishes
  baseline `<PCB rev>/1`.
- Historical tags and their packages are not modified.

Update the managed workflows with the normal Boardwright update. The new
`prepare-release.yaml` accepts the `change_classes`, `justification` and
`source_sha` inputs. Older copies of the workflow cannot record a ledger entry.

Required behaviour changes:

1. Every release after the first needs `--change-class` and a justification.
   (In CI, `revision_description` is used when no justification is given.)
2. A changed PCB layout requires a new `project.board_revision` before release.
3. The schematic and PCB must agree on populated references, values and
   footprints.
4. A remote tag check that cannot reach `origin` now blocks the release. It is
   no longer read as "tag free". Repositories with no `origin` remote report
   `no-remote`.

Optional: set `documents.schematic.revision_scheme: board_minor` to use
`A.0`, `A.1`, `B.0` schematic revisions tied to the PCB revision.

Worksheets and title blocks:

- The Boardwright worksheets now print
  `PCB REV ${PCB_REVISION} | SCH REV ${SCHEMATIC_REVISION} | RELEASE ${RELEASE_VERSION}`
  in the drawing-number cell. Projects that use their own worksheet can copy
  the `boardwright_revision_identity` text item from a Boardwright worksheet.
- Replace a legacy PCB title-block `(rev "${REVISION}")` with
  `(rev "${PCB_REVISION}")`. `boardwright validate` warns about it, and
  `boardwright migrate` rewrites it. Replace schematic `${REVISION}` with
  `${DRAWING_REVISION}`; `boardwright validate` reports this as an error.
  Because the visible schematic text changes, release this with the
  `documentation` class and a new schematic revision.
- Keep `${RELEASE_VERSION}` and other per-release variables off copper and
  silkscreen; use `${PCB_REVISION}` for board markings.

The first release after updating the Boardwright payload needs the `tooling`
class, because the KiBot output configuration and notes templates changed.

Projects created before `.boardwright/state.yaml` existed cannot use
`boardwright update` yet. The payload can be installed from
`managed_project.collect_payload()`; the TODO tracks proper support.

## KiCad 9 to KiCad 10

Status: groundwork is in place. CI still builds with KiCad 9
(`INTI-CMNB/KiBot@v2_dk9`) until a project has passed the comparison below.

Verified upstream on 2026-11-17:

| Item | Version |
| --- | --- |
| KiBot stable | 1.9.1 |
| KiBot GitHub Action for KiCad 10 | `v2_dk10` (`b139bf6`) |
| Newest stable KiBot KiCad 10 image | `kicad10_auto_full:1.9.1-1_k10.0.5_d13.2_b4.2.4LTS` (KiCad **10.0.5**) |

KiBot images do not yet ship KiCad 10.0.8. KiCad 10.0.8 desktop files may
therefore be newer than the CI image. Confirm that the CI image can read
them before upgrading desktop installs across a team.

Implemented:

- `all_group_k10` output group (same outputs as `all_group_k9`, including
  ODB++).
- `scripts/kibot_launch.sh` selects the group by explicit KiCad major version
  (`9` or `10`, or `--kicad MAJOR`). Other versions now fail instead of falling
  back silently.
- `docker_kibot_linux.sh -v 10` uses the pinned KiCad 10.0.5 image.
- The fingerprints accept both KiCad 9 numbered nets and name-only net syntax.
  A format-only upgrade yields the same geometry hash, plus a `tooling` change.
- `.github/workflows/kicad-compat.yaml` is manual dispatch only. It builds
  one commit with KiCad 9 and KiCad 10, uploads both output sets, lists source
  files KiCad 10 rewrote, and compares fabrication and assembly outputs.

Procedure for a project:

1. Release or tag the current KiCad 9 state as the comparison baseline.
2. Dispatch **Boardwright KiCad Compatibility** on that ref. Review the
   summary, ERC/DRC reports, schematic/fabrication/assembly PDFs and renders in
   both artifacts. Record why each difference exists.
3. On a branch, open and save the project in KiCad 10, then commit the format
   upgrade alone.
4. Change `KICAD_VERSION`, `v2_dk9` → `v2_dk10`, and `all_group_k9` →
   `all_group_k10` in the four pipeline workflows.
5. Release with the `tooling` class. The checklist confirms that geometry,
   netnames and assembly fingerprints are unchanged. The baseline check
   reports whether Gerbers are byte-identical or only equivalent after
   normalisation.

Limitations of the comparison workflow: it skips document-PDF metadata
regeneration and the combined review PDF. Compare those by eye in the
uploaded PDFs.
