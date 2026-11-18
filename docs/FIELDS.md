# Boardwright Fields And Template Variables

This document is the worksheet-facing field reference for the controlled drawing
model. The core rule is deliberately simple:

```text
drawing revision != board revision != release version
```

`${DRAWING_REVISION}` is the formal drawing revision for the current document.
`${BOARD_REVISION}` is the hardware spin. `${RELEASE_VERSION}` is package,
archive, GitHub release, and build metadata. Do not use `${REVISION}` as a
Boardwright drawing revision.

## Source Files

| Source | Purpose |
| --- | --- |
| `.boardwright/project.yaml` `project:` | Project identity, PCBA/PCB names, board revision, company, designer, and Git URL. |
| `.boardwright/project.yaml` `release:` | Package/release version and release date. |
| `.boardwright/project.yaml` `documents:` | Per-document drawing title, type, number source, revision, scheme, and drawn metadata. |
| `.boardwright/document_revisions.yaml` | YAML source of truth for per-document revision history rows. |
| `.boardwright/revision_history_variables.env` | Generated title-block revision table variables consumed by KiBot. |
| `.boardwright/revision_history_<document>.csv` | Generated full revision history report for a document. |
| `.boardwright/release.env` | Release automation metadata consumed by KiBot preflight when present. |

The GitHub repository slug is derived from `project.git_url`; it is not a
separate editable project field.

## Project Metadata

Fields live under `.boardwright/project.yaml` `project:`.

| Field | Meaning | Worksheet variable |
| --- | --- | --- |
| `id` | Short Boardwright project identifier. | `${PROJECT_ID}` |
| `name` | Descriptive project name (for example `FLUKE IR`). Printed through the KiCad `${PROJECT_NUMBER}` field. | `${PROJECT_NUMBER}` |
| `pcba_name` | Assembled PCB identifier, typically its part number (for example `FIC-100A`). | `${PCBA_NAME}` |
| `pcb_name` | Bare PCB identifier, typically its part number. Falls back to `pcba_name`, then `name`. | `${PCB_NAME}` |
| `company` | Owning company or organisation. | `${COMPANY}` |
| `designer` | Primary designer/engineer. | `${DESIGNER}` |
| `board_revision` | Hardware spin/design revision. | `${BOARD_REVISION}` |
| `git_url` | Canonical repository URL. | `${GIT_URL}` |

### PCB and PCBA identifiers

The three name fields are intentionally distinct; there are no separate
"number" fields.

| Use | Variable |
| --- | --- |
| Schematic, assembly drawing, BOM, README: the assembled board | `${PCBA_NAME}` |
| Fabrication drawing, bare-board marking: the bare PCB | `${PCB_NAME}` |
| Descriptive project title | `${PROJECT_NUMBER}` (from `project.name`) |

`pcba_name` falls back to `project.name`, and `pcb_name` falls back to
`pcba_name` and then `project.name`. Set both identifiers explicitly so a
drawing never shows the descriptive project name where a part number belongs.

The legacy `project.board_name` key is not read. `boardwright validate` warns
about it. Move its value to `pcba_name` and/or `pcb_name`, according to what it
identified.

## Release Metadata

Fields live under `.boardwright/project.yaml` `release:`.

| Field | Meaning | Worksheet variable |
| --- | --- | --- |
| `version` | Semantic package/release/build version. | `${RELEASE_VERSION}` |
| `date` | Release date, normally `YYYY-MM-DD`. | `${RELEASE_DATE}` |

Release metadata must not be used as the formal drawing revision.

KiBot also exposes `${RELEASE_DATE_NUM}` as the last Git commit date in
`YYYY-MM-DD` form. It is a generated helper, not a controlled document field.

## Document Metadata

Fields live under `.boardwright/project.yaml` `documents.<document_key>:`.
Current document keys are `schematic`, `assembly`, `fabrication`, `package`, and
`review`.

| Field | Meaning | Worksheet variable |
| --- | --- | --- |
| `title` | Current document title. | `${DRAWING_TITLE}` |
| `type` | Current document type, such as `SCHEMATIC`, `ASSEMBLY`, or `FABRICATION`. | `${DOCUMENT_TYPE}` |
| `number_source` | Which project name field resolves to the drawing number. | `${DRAWING_NUMBER}` |
| `revision` | Formal drawing revision for this document. | `${DRAWING_REVISION}` |
| `revision_scheme` | Validation hint: `lettered`, `numeric`, or `semantic`. | Not worksheet-facing. |
| `drawn_by` | Typed drawn-by value. | `${DRAWN_BY}` |
| `drawn_date` | Drawn date. | `${DRAWN_DATE}` |
| `include_full_revision_history` | Controls whether full history output is expected for the document. | Not worksheet-facing. |

Approval is intentionally not emitted as a Boardwright variable. It can be signed
after the fact on controlled drawings instead of being baked into generated
metadata.

## Fabrication Note Requirements

The fabrication notes are a controlled manufacturing specification. Boardwright
renders them from two sources and never from silent defaults.

Manufacturing statements come from `.boardwright/project.yaml`
`manufacturing:`. All of these keys must be present. Rendering and
`boardwright validate` fail if any is missing, so a Boardwright update cannot
change an approved specification. Set a key empty (text) or `null` (boolean) to
omit its statement deliberately.

| Key | Statement |
| --- | --- |
| `manufacturing_standard` | `FABRICATE PER …` |
| `core_material` | `CORE MATERIAL: …` |
| `flammability_rating` | flammability requirement |
| `tg_rating` | Tg requirement |
| `rohs_pb_free` | RoHS / Pb-free process (`true`: required; `false`: not required) |
| `halogen_free` | halogen-free equivalent material |
| `tented_vias` | via tenting |
| `silkscreen_enabled` | silkscreen legend |
| `conformal_coating` | `true`: required; `false`: states "not required"; `null`: no statement |

Minimum feature sizes come from the KiCad design, by
`boardwright.fabrication_requirements`:

| Note line | Source |
| --- | --- |
| TRACE WIDTH | Narrowest routed track; must not be below board-setup `min_track_width`. |
| TRACE TO TRACE | Smallest design-rule clearance (net classes, local overrides, `.kicad_dru` `clearance` constraints), floored by board-setup `min_clearance`. A rule, not measured spacing: DRC violations are deviations to record. |
| MIN. HOLE (PTH) | Smallest via or plated pad drill (slot width for slots). |
| MIN. HOLE (NPTH) | Smallest non-plated pad drill. |
| ANNULAR RING | Smallest outer ring over vias and plated pads, including slots, drill offsets and padstack layers. |
| COPPER TO HOLE / EDGE, HOLE TO HOLE | Board-setup `min_hole_clearance`, `min_copper_edge_clearance`, `min_hole_to_hole`. |
| BOARD THICKNESS | PCB `(general (thickness …))`. |

`N/A` means the board has no holes of that type. Missing rules, an unreadable
drill or a zero annular ring stop the build.

### Approved wording

The notes render from `.boardwright/templates/fabrication_notes.txt` when it
exists, otherwise from the Boardwright template. The project file is outside
the Boardwright payload, so updates never replace it; use it to keep a released
project's approved wording. It must reference every declared requirement that
makes a statement, as a `${...}` placeholder or in a `#?` condition, or
rendering fails.

Template syntax: `${name}` substitutes a value (for example
`${manufacturing_standard_cap}`, `${flammability_rating_cap}`,
`${tg_rating_cap}`, `${COMPANY_cap}`, and the feature sizes above). A line
starting `@NOTE@` becomes the next numbered note. A `#?EXPR` line keeps the next
line only if `EXPR` is true. `EXPR` uses `and`/`or`/`not` over `stackup`,
`impedance_controlled`, `rohs_pb_free`, `halogen_free`, `tented_vias`,
`silkscreen_enabled` and `conformal_coating`; any other name fails. Spacing is
reproduced exactly, and the file is written with LF line endings.

Notes have exactly one writer, `prepare_pcb_tables.py`; KiBot defines no notes
output.

### Releases before output manifests

For a reused baseline whose previous release has no committed
`accepted_manifest.json`, `check-baseline` compares against that release's
published `boardwright-<tag>-release.zip`. It authenticates the package first:
published by the release workflow, GitHub SHA-256 digest matched, and tag
unmoved and in history. The step fails if the package cannot be obtained or
authenticated.

## Drawing Number Resolution

In the Boardwright title blocks the PROJECT cell shows `${PROJECT_NUMBER}`
(the descriptive `project.name`) and the large identifier cell shows
`${DRAWING_NUMBER}`. The worksheets ship with the Boardwright placeholder logo;
a project with its own logo embeds it with `boardwright worksheet-logo`, and
`boardwright validate` warns while the placeholder is still embedded.


`${DRAWING_NUMBER}` is resolved per document before KiCad/KiBot output
generation.

| Document context | Resolution |
| --- | --- |
| `schematic` | `${PCBA_NAME}` |
| `assembly` | `${PCBA_NAME}` |
| `fabrication` | `${PCB_NAME}` |
| `package` | `${PCBA_NAME}` |
| `review` | `${PCBA_NAME}` unless a more specific context exists |

Never map `${BOARD_REVISION}` or `${RELEASE_VERSION}` to
`${DRAWING_REVISION}`.

## Controlled Worksheet Variables

Boardwright emits these for title blocks and drawing-facing reports.

| Variable | Meaning |
| --- | --- |
| `${PROJECT_ID}` | Short project identifier. |
| `${PCBA_NAME}` | Populated assembly display name. |
| `${PCB_NAME}` | Bare PCB display name. |
| `${COMPANY}` | Owning company or organisation. |
| `${DESIGNER}` | Primary designer/engineer. |
| `${PROJECT_NUMBER}` | Project name from `project.name`. |
| `${BOARD_REVISION}` | Hardware board spin/design revision. |
| `${PCB_REVISION}` | Same value as `${BOARD_REVISION}`; explicit name for the physical PCB revision. |
| `${SCHEMATIC_REVISION}` | Schematic document revision, available on every document (for example, PCB drawings). |
| `${DOCUMENT_TYPE}` | Current document type. |
| `${DRAWING_TITLE}` | Current document title. |
| `${DRAWING_NUMBER}` | Current document drawing number resolved from `number_source`. |
| `${DRAWING_REVISION}` | Current document controlled drawing revision. |
| `${RELEASE_VERSION}` | Package/release/build version. |
| `${RELEASE_DATE}` | Package/release date. |
| `${DRAWN_BY}` | Current document drawn-by value. |
| `${DRAWN_DATE}` | Current document drawn date. |

### Revision identities on drawings

Every Boardwright worksheet except `Boardwright_Template_AS1100.kicad_wks`
shows two kinds of revision:

- The **REVISION** cell shows `${DRAWING_REVISION}`, the revision of the drawing
  you are reading. On the fabrication drawing this is the fabrication drawing
  revision.
- The drawing-number cell carries one identity line:

  ```text
  PCB REV ${PCB_REVISION} | SCH REV ${SCHEMATIC_REVISION} | RELEASE ${RELEASE_VERSION}
  ```

The template PCB title block uses `(rev "${PCB_REVISION}")`. Gerber X2
`TF.ProjectId` therefore carries the physical board revision, which stays the
same across documentation-only releases. The KiBot preflight sets
`PCB_REVISION` and `SCHEMATIC_REVISION`, so CI renders the same values as
KiCad desktop.

Do not put `${RELEASE_VERSION}`, `${RELEASE_DATE}` or `${GIT_HASH_*}` on copper
or silkscreen. These change with every release, so release preparation treats
them as a fabrication re-issue (see [`RELEASE_MODEL.md`](RELEASE_MODEL.md)).
Use `${PCB_REVISION}` for board markings.

## Revision Table Variables

`.boardwright/document_revisions.yaml` is the only formal revision-history source
for Boardwright output. Rows are treated as newest-first.

Release preparation is the normal way to add a controlled revision row. The
operator supplies a drawing revision and a short description, 60 characters or
fewer. Boardwright inserts that row at the top of the relevant document history,
updates `documents.<document>.revision`, then regenerates the title-block
revision variables. Commit messages and changelog entries remain Git/release
narrative and are not copied into the drawing revision table.

The TUI Info panel also has a `Revisions` section for retroactive history entry.
It accepts CSV rows in newest-first order using these columns:

```text
REV, DATE, DESCRIPTION, DRAWN
```

This is intended for bringing older controlled projects into Boardwright without
hand-editing YAML.

```yaml
document_revisions:
  schematic:
    rows:
      - revision: B
        date: 2026-08-01
        description: Updated connector callouts
        drawn_by: RH
      - revision: A
        date: 2026-07-05
        description: Initial controlled release
        drawn_by: RH
```

Boardwright generates two views from the same data.

| View | Content |
| --- | --- |
| Title-block revision table | Latest six rows only, mapped to `${REVTABLE_1_*}` through `${REVTABLE_6_*}`. |
| Full revision history report | All rows, with columns `REV`, `DATE`, `DESCRIPTION`, `DRAWN`. |

For each title-block slot `N`, where `N` is `1` through `6`, these variables are
available:

| Variable pattern | Meaning |
| --- | --- |
| `${REVTABLE_N_REV}` | Revision value. |
| `${REVTABLE_N_DATE}` | Revision row date. |
| `${REVTABLE_N_DESC}` | Revision row description. |
| `${REVTABLE_N_DRAWN}` | Revision row drawn-by value. |

`${REVTABLE_1_*}` is the newest/current row. Unused title-block rows emit blank
strings. If more than six rows exist, `${REVTABLE_NOTE}` is:

```text
Latest 6 revisions shown. See full revision history.
```

Otherwise `${REVTABLE_NOTE}` is blank.

## Other Available KiCad/KiBot Variables

These are available to worksheets or reports, but they are not controlled
drawing identity fields.

| Variable | Meaning |
| --- | --- |
| `${GIT_URL}` | Canonical repository URL from project config. |
| `${GIT_HASH_SCH}` | Last non-merge Git hash touching the schematic. |
| `${GIT_HASH_PCB}` | Last non-merge Git hash touching the PCB. |
| `${RELEASE_DATE_NUM}` | Last Git commit date in `YYYY-MM-DD` format. |
| `${VARIANT}` | Current KiBot variant value. |
| `${SHEET_LEGAL_NOTICE}` | Legal notice text expanded into worksheet templates. |
| `${TEMPLATE_LOGO}` | Template logo path/text value passed into KiBot. |
| `${FABRICATION_NOTES}` | Generated or template fabrication notes text. |
| `${FABRICATION_SUMMARY}` | Generated fabrication snapshot text. |
| `${COMPONENT_COUNT_SUMMARY}` | Generated component snapshot text. |
| `${README_BADGES}` | Generated README badge block. |

KiCad native sheet variables such as `${#}` and `${##}` remain KiCad-owned and
are used for sheet number and total sheet count. Boardwright should not shadow
those with custom drawing metadata variables.

## Deprecated Or Removed Boardwright Worksheet Variables

These are not Boardwright worksheet variables in the controlled-drawing model:

```text
${REVISION}
${DOCUMENT_REVISION}
${DOCUMENT_REVISION_SCHEME}
${APPROVED_BY}
${APPROVED_DATE}
${CHECKED_BY}
${CHECKED_DATE}
${REVTABLE_N_CHECKED}
${REVHIST_N_*}
```

`${REVISION}` should not be used as Boardwright's controlled drawing revision.
Use `${DRAWING_REVISION}` instead.

Boardwright's project sync removes these legacy KiCad text variables from
`*.kicad_pro`. Any remaining reference then renders as literal text: in a
schematic, ERC fails with `unresolved_variable`, and worksheet text is not
checked by ERC at all. `boardwright validate` reports every reference in
schematics, PCB files and the active worksheets, with its location and
replacement. `boardwright migrate` rewrites only the unambiguous ones.

| Legacy | Replacement | Automatic |
| --- | --- | --- |
| `${REVISION}` | `${DRAWING_REVISION}` in schematics, `${PCB_REVISION}` in PCB files. In worksheets it is KiCad's built-in title-block field and is allowed. | Yes |
| `${DOCUMENT_REVISION}` | `${DRAWING_REVISION}` | Yes |
| `${PCB_NUMBER}` / `${PCBA_NUMBER}` | `${PCB_NAME}` / `${PCBA_NAME}` | Yes |
| `${BOARD_NAME}` | `${PCBA_NAME}` or `${PCB_NAME}`, depending on what the text describes | No: manual review |
| `${PROJECT_NAME}` | `${PROJECT_NUMBER}`, or KiCad's built-in `${PROJECTNAME}` | No: manual review |
| `${PROJECT_ID}`, `${DOCUMENT_REVISION_SCHEME}` | No equivalent | No: manual review |

## Worksheet Intent

A standard title block should conceptually consume the variables like this:

```text
TITLE:      ${DRAWING_TITLE}
DWG NO:     ${DRAWING_NUMBER}
DWG REV:    ${DRAWING_REVISION}
BOARD REV:  ${BOARD_REVISION}   (alias ${PCB_REVISION})
TYPE:       ${DOCUMENT_TYPE}
COMPANY:    ${COMPANY}
DRAWN:      ${DRAWN_BY}       DATE: ${DRAWN_DATE}
CHECKED:    [signature area]
APPROVED:   [signature area]
SHEET:      ${#} of ${##}
```

The title-block revision table should use six visible rows:

```text
REV | DATE | DESCRIPTION | DRAWN
```

Checked and approved signoffs are post-generation/signature actions, not
generated variables.

## Combined Output

Combined review output is intentionally one project option:

```yaml
outputs:
  combined_review_pdf: true
```

When enabled, Boardwright uses the standard combined output name, ToC/bookmarks,
and combined sheet numbering defaults.

## KiBot Macros

The KiBot YAML uses `@...@` import macros to seed the text-variable preflight.
The controlled metadata macros mirror the worksheet variables:

```text
@PROJECT_ID@
@PCBA_NAME@
@PCB_NAME@
@COMPANY@
@DESIGNER@
@PROJECT_NUMBER@
@BOARD_REVISION@
@DOCUMENT_TYPE@
@DRAWING_TITLE@
@DRAWING_NUMBER@
@DRAWING_REVISION@
@RELEASE_VERSION@
@RELEASE_DATE@
@DRAWN_BY@
@DRAWN_DATE@
```

Other KiBot macros still cover paths, output names, filters, render settings,
manufacturing reports, revision-table lookup, and helper commands. Those are
implementation plumbing, not title-block identity metadata.
