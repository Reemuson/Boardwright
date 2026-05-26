# Boardwright Product Specification

Boardwright is a KiCad/KiBot hardware project template plus a small workflow
tool. Its job is to make the normal PCB loop predictable:

```text
edit in KiCad -> record changes -> commit + push -> review artifacts
-> accept to main -> create release
```

The user should not need to remember KiBot groups, GitHub Actions inputs, tag
rituals, or revision-history plumbing during normal design work.

## Current Codebase

The repository currently contains three coupled parts:

- KiCad template files at the repository root, with worksheets in `Templates/`.
- Boardwright Python tooling in `src/boardwright/`.
- KiBot/GitHub Actions build resources in `boardwright_resources/` and
  `.github/workflows/`.

The Python package provides:

- project config loading from `.boardwright/`
- validation of required config, KiCad, KiBot, README, licence, and asset files
- changelog parsing, writing, and release promotion
- revision-history variable generation for KiBot/KiCad text variables
- legal/notice file generation
- CLI commands for status, validation, change recording, preview planning,
  promotion planning, release preparation, and git commit dry-runs
- shared workflow action builders used by CLI and TUI
- optional Textual TUI with a console fallback
- GitHub CLI integration for workflow dispatch, CI polling, and preview artifact
  download when `gh` is available

The current tests are Python `unittest` tests under `tests/`. Run them with:

```powershell
python -m unittest discover -s tests -v
```

`python -m boardwright ...` and `python -m boardwright.cli ...` both work for
local module execution. The installed console script is `boardwright`.

## Core Rules

- `dev` is the normal KiCad/source development branch.
- CI must not mutate `dev`.
- `preview` is disposable and may be force-updated.
- `main` is the accepted state.
- `main` may contain source files plus accepted generated README/render snapshot
  assets, but not wholesale manufacturing output folders.
- Tags are immutable published package points.
- Tag workflows publish artifacts only; they do not commit back to branches.
- Release-affecting operations require explicit user intent.
- CLI and TUI should share action logic instead of duplicating workflow rules.

## Branch And Release Model

```text
dev      = normal design/source work
preview  = disposable generated preview branch/artifacts
main     = reviewed and accepted project state
tags     = immutable published release package points
```

Normal work happens on `dev`. Preview CI is explicitly dispatched when the user
is ready to review generated outputs. Preview CI generates reviewable artifacts
and can publish the disposable `preview` branch, but must not commit to `dev`.

`main` represents a reviewed state. The `Accept to Main` action dispatches the
main-output workflow from the exact reviewed `dev` source SHA, with a selected
variant. CI verifies that source SHA before generation. When requested, that
workflow pushes the reviewed source plus an accepted `README.md` and render
snapshot under `assets/renders/` to `main`.

Release preparation is CI-owned. Boardwright dispatches `prepare-release.yaml`;
that workflow promotes the changelog, writes release metadata, generates
accepted outputs, commits the accepted release state to `main`, creates the tag,
and dispatches the tag workflow. The tag workflow publishes the release package
without mutating `main`.

## Variants

Supported variants are:

```text
DRAFT
PRELIMINARY
CHECKED
RELEASED
```

Variant intent:

| Stage | Variant | Typical release state |
| --- | --- | --- |
| early schematic/design | `DRAFT` | draft or prerelease |
| schematic mostly complete | `PRELIMINARY` | prerelease |
| fabrication package ready | `CHECKED` | prerelease or release candidate |
| official production release | `RELEASED` | full release |

Defaults live in `.boardwright/project.yaml`:

- `variants.dev_default`
- `variants.preview_default`
- `variants.main_default`
- `variants.release_default`

Variant defaults are not the same thing as a CI run's selected output variant:

- `dev_default` is the source project's normal design-stage label and is what
  the TUI status strip calls `dev`.
- `preview_default`, `main_default`, and `release_default` seed the selector
  values for their respective actions.
- Dispatching preview, accepting to main, or preparing a release must not
  silently rewrite `.boardwright/project.yaml` on `dev`. Those actions are CI
  output selections and are recorded in run names, artifacts, job summaries,
  accepted-main evidence, and `.boardwright/release.env` for releases.
- If the project has genuinely moved from `DRAFT` to `PRELIMINARY` or
  `CHECKED`, the user should change `dev_default` deliberately in Project Info
  and commit that source-state change.

## Project Config

Boardwright config lives in `.boardwright/`:

```text
.boardwright/
  project.yaml
  branches.yaml
  legal.yaml
  revision_history.yaml
  revision_history_variables.env
  release.env
```

`project.yaml` holds project identity, GitHub repository settings, variant
defaults, workflow filenames, output policy, and visible asset paths.

`branches.yaml` maps the development, preview, and release branches. The
current default is:

```text
development: dev
preview: preview
release: main
```

`release.env` is written and committed by release preparation so the tag
workflow can read:

```text
RELEASE_VERSION=0.1.0
RELEASE_VARIANT=CHECKED
RELEASE_KIND=prerelease
```

KiCad text-variable naming:

- `${REVISION}` is the semantic release/version value, normally sourced from
  `.boardwright/release.env` during release preparation or from git tags as a
  fallback.
- `${RELEASE_VERSION}` is an explicit alias for the same semantic version.
- `${BOARD_REVISION}` is the hardware board spin such as `A`, `B`, or `C`,
  sourced from `project.board_revision`.

Dashboard tag display:

- The status bar's tag value is the latest semantic release tag in the
  repository, not the nearest tag reachable from the current branch tip.
- Stable semantic-version tags such as `0.1.3` or `v0.1.3` win over
  prerelease tags.
- If there are no stable release tags, the latest semantic prerelease tag such
  as `0.1.3-rc.1` is shown.
- If there are no semantic release/prerelease tags, the dashboard shows `none`.

## Project Information And Manufacturing Metadata

Boardwright should treat project-specific manufacturing text as structured
project data, not as hardcoded KiBot YAML. The TUI should expose this as a
`Project Info` screen with compact tabs or sections:

- Identity: project name, board name, board revision, company,
  designer/author, logo path, repository URL, development branch, preview
  branch, release branch.
- Variants: dev, preview, accepted-main, and release default variants.
- Fabrication: surface finish, soldermask color, silkscreen color, material
  requirements, IPC class, RoHS/Pb-free policy, tented-via policy, controlled
  impedance enabled/disabled, and editable fabrication notes.
- Assembly: DNP policy, BOM precedence policy, conformal coating requirement,
  pin-1/orientation note, and editable assembly notes.
- Tables: component-count behavior, testpoint policy, impedance table entries,
  and whether empty side-specific pages should be omitted.
- Outputs: README/render snapshot policy, release package contents, and
  generated output cleanup policy.

The first implementation should avoid a raw YAML editor. It should show fields
as normal form controls:

- text inputs for names, repository URL, colors, material notes, and freeform
  note bodies
- selects for variant defaults, IPC class, surface finish, soldermask color,
  silkscreen color, and release kind defaults
- checkboxes for RoHS/Pb-free, conformal coating, tented vias, controlled
  impedance, and side-specific testpoint pages
- a small editable impedance table with columns:
  `Transmission Line`, `Impedance [ohms]`, `Tolerance [ohms]`, `Layer`,
  `Trace Width [mm]`, `Gap [mm]`, and `Ref. Layers`

The manufacturing-note templates remain parameterized. Boardwright should
eventually render them from project metadata before KiBot runs, so the KiCad
text variables receive complete notes even when the report outputs have not
yet been generated. Freeform edits should be stored as project-local data under
`.boardwright/`, while the repository template keeps sane defaults.

Controlled impedance is opt-in. When no impedance entries exist, Boardwright
must render a short note in the impedance-table placeholder that says there are
no impedance controlled traces. When entries exist, Boardwright should generate
the CSV/table from the structured project data and leave the KiCad placeholder
movable.

## Changelog And Revision History

`CHANGELOG.md` is the source of release notes and schematic revision-history
content.

Supported changelog sections are:

```text
Added
Changed
Fixed
Removed
Notes
Status
```

The TUI exposes the everyday sections:

```text
Added
Changed
Fixed
Removed
Notes
```

Release preparation no longer fails solely because `Unreleased` is empty. If a
release is otherwise valid and no changelog entries are waiting, Boardwright
creates the version heading with a generated note:

```text
No changelog entries recorded for this release.
```

This keeps release packages traceable while still allowing the TUI to show the
missing changelog entry as a warning.

KiCad sheets consume fixed text-variable slots:

```text
${REVHIST_1_TITLE}
${REVHIST_1_BODY}
```

Boardwright writes every configured slot to
`.boardwright/revision_history_variables.env`. Newest visible release content
fills slot 1, and unused slots are written as blank values. The KiBot preflight
defines a larger ceiling than the default visible slot count so projects can
expand their revision-history sheets later.

## CI/CD Workflows

Boardwright-native workflows:

```text
.github/workflows/dev-preview.yaml
.github/workflows/main-outputs.yaml
.github/workflows/prepare-release.yaml
.github/workflows/release.yaml
```

Workflow run names should be human-readable in the GitHub Actions list. They
should include the user decision and short source label where practical, for
example `Preview PRELIMINARY from dev@a9cf86e1d223` and
`Accept CHECKED from dev@a9cf86e1d223 to main`. Full source SHAs belong in the
job summary as clickable commit links rather than in long run titles.

`dev-preview.yaml`

- runs on manual dispatch for the selected source ref
- selects a KiBot generation mode from the variant
- generates preview outputs
- cleans generated output packages before upload
- uploads `boardwright-preview-<VARIANT>` artifacts
- uploads KiBot logs
- publishes the disposable `preview` branch from `dev`
- does not mutate `dev`

`main-outputs.yaml`

- runs on manual dispatch
- checks out and verifies the reviewed source ref/SHA
- generates accepted outputs from that reviewed source
- intentionally regenerates outputs rather than copying the preview artifact.
  Preview artifacts are review evidence; accepted outputs are a reproducible CI
  build from the reviewed source SHA.
- cleans generated output packages before upload
- uploads generated outputs as artifacts
- discards generated source/config side effects, including temporary KiBot
  metadata injected into `boardwright_resources/kibot/yaml/kibot_main.yaml`,
  before switching to the accepted branch
- optionally pushes the reviewed source plus `README.md` and
  `assets/renders/*.png` to the target accepted branch
- when committing the accepted snapshot, merges the reviewed source SHA onto the
  current target branch first, then reapplies the generated README/render
  snapshot. This avoids non-fast-forward failures after earlier CI snapshot
  commits on `main`.
- always pushes the reviewed-source merge to the target branch, even when the
  generated README/render snapshot is unchanged. This keeps accepted source
  changes such as `CHANGELOG.md` from being stranded in the CI runner.

`prepare-release.yaml`

- runs on manual dispatch from `main`
- installs Boardwright
- promotes `CHANGELOG.md`
- writes `.boardwright/release.env`
- generates accepted outputs/README
- cleans generated output packages before commit/tag
- discards generated source/config side effects before committing release state
- commits accepted release state to `main`
- creates and pushes the tag
- dispatches `release.yaml` for the tag

`release.yaml`

- runs on semantic-version tags or manual dispatch against a tag
- reads `.boardwright/release.env`
- generates release outputs
- cleans generated output packages before packaging
- creates release notes from changelog content and board renders
- packages release assets
- publishes the GitHub Release
- does not push branch commits

CI cache policy:

- Use pinned official `actions/cache@v4` cache steps for Boardwright-owned
  caches, with restore keys.
- Treat GitHub cache-save outages as non-fatal infrastructure warnings. The
  build should still succeed without a cache write.
- Cache 3D model downloads by runner OS and KiCad major version only. This
  cache is a best-effort speedup and must not depend on fragile `hashFiles`
  expressions that can block workflow parsing.
- Cache Python package downloads where Boardwright is installed in CI.

## CLI

Core commands:

```text
boardwright
boardwright init
boardwright status
boardwright change
boardwright suggest-commit
boardwright validate
boardwright revision-history
boardwright preview
boardwright promote
boardwright accepted
boardwright review
boardwright release
boardwright doctor
boardwright testbench
boardwright outputs clean
boardwright legal
boardwright git-status
boardwright commit
boardwright tui
```

Plain `boardwright` opens the TUI. If Textual is not installed, it prints a
console status view and an install hint.

The CLI remains scriptable and useful in CI. The TUI is the intended everyday
interface for designers.

Planned CLI additions:

- `boardwright config show`: read-only project configuration summary.
- `boardwright adopt`: later helper for converting existing KiCad projects.

Implemented accepted-output CLI support:

- `boardwright accepted`: shows latest accepted main-output workflow evidence,
  including run id, branch, source SHA, expected reviewed `origin/dev` SHA,
  status, and freshness.

Implemented environment-readiness CLI support:

- `boardwright doctor`: checks local Git/repository state, configured branches
  and remotes, workflow dispatch shape, GitHub CLI/auth hints, Textual
  availability, and base project validation. It exits nonzero only for blocking
  errors; warnings are advisory readiness notes.

Implemented scriptable review/testbench support:

- `boardwright review`: shows preview artifact freshness, run evidence,
  expected `origin/dev` SHA, and local reviewed-marker state. With `--fetch`,
  it downloads the fresh preview artifact and marks that exact run/SHA/artifact
  as reviewed.
- `boardwright testbench plan`: prints a live-test command sequence for a
  separate repository.
- `boardwright testbench init`: copies the template into a separate local
  testbench repo, excludes generated/local artifacts, optionally sets
  `project.github_repo`, and initializes local `main`/`dev` branches.
- `boardwright outputs clean`: removes KiBot packaging noise after generation.
  It drops numbered PDF page shards when a combined PDF exists and removes empty
  generated CSV tables for component-count, testpoint, and impedance-style
  outputs. CI workflows run the same cleanup before upload, commit, tag, or
  release packaging.

## TUI

The TUI is a small workflow cockpit, not a full git client or KiBot editor.
It should answer:

1. What state is the project in?
2. What should I do next?
3. What artifacts or release outputs are ready to review?

Primary actions:

```text
Record Changes
Commit + Push
Generate Preview
Review Artifacts
Accept to Main
Create Release
Project Info
Refresh
```

Target main-screen layout:

- Top status strip: one concise line with project id, branch, git state,
  variant, latest semantic release/prerelease tag, compact CI summary, and
  validation summary. CI phases should be named with words (`Preview`,
  `Accept`, `Release`) rather than single-letter abbreviations. Detailed run
  ids, titles, and evidence belong in the inspector or action modals, not in
  the top strip. The strip should scroll horizontally if the terminal is
  narrow instead of truncating important fields.
- Left action rail: grouped by intent rather than shown as one flat button
  pack.
  - Work: Record Changes, Commit + Push.
  - Preview: Generate Preview, Review Artifacts, Accept to Main.
  - Release: Create Release.
  - Setup: Project Info, Refresh.
- Center workflow map: a compact seven-step progress map with one line per
  step. It should show state with simple markers and color, but not repeat long
  explanatory paragraphs.
- Right inspector: a structured readout with `Now`, `Evidence`, and `Release`
  sections. `Now` states the next action and blocker. `Evidence` has separate
  preview, review, accept, and release rows so mixed CI states do not collapse
  into one noisy sentence. `Release` summarizes readiness. Raw workflow
  evidence belongs in the action modal or review screen, not permanently in the
  main readout.
- Bottom panels: validation and changed files, both scrollable when needed.

Button and keybind behavior must be identical. Every visible action and every
keyboard shortcut must pass through the same shared `action_state` gate before
opening a modal or dispatching work. Disabled actions should explain the lock
reason in a notification.

`Create Release` opens the release checklist once the local dev state is clean
and pushed. The checklist, not the main button, gates the actual
`prepare-release.yaml` dispatch against accepted-main evidence, release inputs,
changelog readiness, and tag availability. This keeps the release path
discoverable without allowing an accidental publish.

Routine plumbing should be automatic, CLI-only, or advanced/fallback:

```text
Validate
Write Revision History
Legal
Raw Git Status
Raw Workflow Dispatch
```

Current implemented TUI behavior:

- status bar shows project id, branch, dirty state, remote ahead/behind,
  variant, latest tag, CI summary, and validation summary
- workflow timeline shows edit, record, commit/push, preview, review, accept,
  and release steps as a compact progress map
- inspector is sectioned into Now, Evidence, and Release instead of showing a
  raw status dump
- Record Changes updates `CHANGELOG.md`, writes revision-history variables,
  validates, and suggests a commit message
- Commit + Push requires the configured `dev` branch, requires a changelog entry
  for dirty work, validates, writes revision-history variables, commits, and
  pushes `origin/dev`
- Generate Preview dispatches `dev-preview.yaml` for a selected variant after
  the configured `dev` branch is clean and pushed. It does not run implicitly on
  every push.
- Review Artifacts polls recent workflow runs and downloads the latest preview
  artifact evidence when `gh` is available. It opens a dedicated review screen
  showing run id, branch, source SHA, expected SHA, artifact name, created time,
  status, freshness, selected variant, and reviewed marker. The default variant
  comes from `variants.preview_default`; the operator can override it for manual
  workflow runs. During fetch, the TUI shows artifact download progress. A
  successful fetch writes a local review marker under `boardwright-preview/` for
  the exact artifact, run id, and SHA.
- Accept to Main dispatches `main-outputs.yaml` only when the selected variant
  has a fresh preview artifact for the latest pushed `origin/dev` SHA and that
  exact artifact has been reviewed locally. The dispatch pins that reviewed
  source SHA and tells CI to push the accepted state to `main`.
- Create Release opens a release readiness checklist before dispatching
  `prepare-release.yaml`. The checklist shows accepted-main evidence, release
  inputs, unreleased changelog readiness, local tag availability, and dispatch
  target. Dispatch is disabled while any checklist item is blocking.
- primary action buttons lock/unlock from the shared workflow-state model
- keybindings use the same action gates as the visible buttons
- Project Info edits project id, name, company, designer, Git URL, GitHub repo,
  logo path, product image path, and the dev/preview/main/release variant
  defaults stored in `.boardwright/project.yaml`.
- Refresh checks accepted-main evidence when GitHub CLI is available. Create
  Release is blocked unless accepted main outputs are fresh for the latest
  pushed `origin/dev` source SHA.
- CI polling summarizes all three active CI phases in the top status and
  inspector: preview, accepted-main outputs, and release preparation/publish
  runs. Preview polling infers the latest variant from the workflow run title
  instead of always using `variants.preview_default`.
- The compact CI summary uses human-readable phase names, for example
  `Preview:CHECKED ready | Accept:ready | Release:prepare running`. Color
  priority is failure/error first, then running/queued/stale/review-needed
  states, then ready/success states.

The TUI renders from a shared workflow-state model rather than keeping its own
private timeline rules. That model provides:

- current stage
- next action
- human-readable reason
- ordered timeline steps
- primary action enablement and lock reasons

Initial workflow stages:

```text
validation_blocked
needs_changelog
ready_to_commit
needs_push
behind_remote
preview_missing
preview_running
preview_failed
preview_stale
preview_ready
preview_reviewed
accepted_missing
accepted_running
release_ready
editing
```

Important missing TUI behavior:

- first-run metadata editing/onboarding is not implemented
- project-information editing for identity, fabrication metadata, assembly
  notes, and controlled-impedance requirements is not implemented

## README And Assets

The generated project README is produced from:

```text
boardwright_resources/kibot/resources/templates/readme.txt
```

The current template includes project logo, board renders, board revision,
release version, variant, dimensions, generated-output guidance, and legal
notes. Workflow badges are deferred until repository URL metadata is reliable
enough to avoid broken relative links in generated project READMEs.

The target README should also include, where KiBot data makes it practical:

- latest release/package links
- stackup/fabrication summary
- component counts, including SMT/THT if available
- clearer links to generated manufacturing outputs

Visible project media belongs under:

```text
assets/logos/
assets/renders/
assets/3d/
```

`assets/renders/` may be committed to `main` as the accepted README snapshot.
`assets/3d/` is packaged into release artifacts but is not normally committed
as source state.

## Validation Contract

Validation currently checks:

- required `.boardwright/` config files
- required root files: `CHANGELOG.md`, `LICENSE`, `README.md`
- variant values
- supported preview engine
- configured workflow files
- changelog structure and duplicate releases
- revision-history slot settings
- presence of KiCad project/schematic/PCB files
- warns when PCB files have no `Edge.Cuts` outline geometry, because
  `CHECKED`/`RELEASED` CI runs may fail DRC without a real board outline
- presence of the KiBot main config
- configured asset paths
- README template mentions legal files

Validation should remain fast and local. CI/runtime output freshness and GitHub
authentication checks belong in status/review actions rather than base project
validation.

## Known Product Gaps

These are the important gaps between the current code and the intended product:

1. Project Info depth: identity and variant defaults are editable, but
   fabrication metadata, assembly notes, controlled-impedance entries, and
   output-policy fields still need proper TUI sections.
2. TUI full-path polish: the main screen is now structured, but the full
   record -> commit/push -> preview -> review -> accept -> release loop still
   needs repeated live use to tune labels, lock reasons, and modal density.
3. CI retest: workflows need live retesting after moving visible generated media
   under `assets/renders/` and `assets/3d/`.
4. README richness: the generated README template is partly refreshed but still
   lacks stackup/component/latest-release sections.
5. Onboarding: new/adopted project setup still relies on hand-editing config.
6. GitHub fallback UX can still be refined with direct URLs after repository
   metadata is configured.

## Out Of Scope For The Current Build

Do not prioritize these until the normal workflow is reliable:

- full YAML editor
- full git client
- full GitHub Actions browser
- KiCad file browser
- local KiBot/Docker runner as the primary flow
- multi-board management
- complete metadata editor
