# Boardwright Specification

Boardwright is a guided KiCad/KiBot project system for producing clean PCB/PCBA
repositories, generated review outputs, and GitHub release packages without
making the user remember brittle git and CI rituals.

The intended user experience is boring: work in KiCad, record changes in
Boardwright, ask Boardwright for previews, promote accepted outputs, and publish
draft/prerelease/release tags from the TUI.

## Core Principles

- Tags are immutable.
- `dev` is for design work and is never mutated by CI.
- `preview` is disposable and may be force-updated.
- `main` is the accepted state and may contain generated README/output assets.
- Tag workflows publish only; they do not mutate `main`.
- Boardwright owns workflow orchestration through shared CLI/TUI actions.
- YAML and git commands are plumbing, not the user interface.
- Release-affecting operations require explicit user intent.
- The workflow should be boring when it matters.

## Branch And Release Model

```text
dev      = normal KiCad/source development
preview  = disposable generated preview outputs
main     = accepted source plus accepted generated README/output assets
tags     = immutable published packages from exact main commits
```

Normal work happens on `dev`. Users record changelog entries as they work.
Preview generation may run on GitHub Actions and publish artifacts or a
throwaway `preview` branch, but it must not commit back to `dev`.

`main` represents accepted project state. When the user promotes a variant,
Boardwright dispatches CI to generate outputs, update `README.md`, and commit
the accepted README snapshot and render assets to `main` when explicitly
requested. Full manufacturing/release artifacts live in workflow artifacts and
tagged GitHub Releases rather than being committed wholesale to `main`.

Tags are created by a deliberate Boardwright-controlled CI workflow, not by
normal CI and not by the tag-publish workflow. The tag workflow checks out the
tag and publishes release assets only.

## Product Workflow

The target TUI workflow is:

1. Initialise project metadata and workflows.
2. Edit in KiCad or project files.
3. Record changes in Boardwright.
4. Validate, write revision-history variables, commit, and push.
5. Generate previews from `dev`; poll CI and fetch artifacts locally.
6. Promote a good build to `main` with a selected variant.
7. Optionally tag that accepted `main` commit as a draft, prerelease, or release.
8. Continue development on `dev`.

Variant intent:

| Stage | Variant | GitHub release state |
| --- | --- | --- |
| early schematic | `DRAFT` | draft or prerelease |
| schematic mostly complete | `PRELIMINARY` | prerelease |
| fabrication package ready | `CHECKED` | prerelease/release candidate |
| official production release | `RELEASED` | full release |

## CI/CD Architecture

Boardwright-native workflows:

```text
.github/workflows/dev-preview.yaml
.github/workflows/main-outputs.yaml
.github/workflows/prepare-release.yaml
.github/workflows/release.yaml
```

`dev-preview.yaml` generates reviewable outputs from `dev` or manual dispatch.
It may publish `preview` and upload artifacts. It does not mutate `dev`.

`main-outputs.yaml` generates accepted outputs on `main`. It uploads generated
outputs as artifacts and can commit the accepted README snapshot/render assets
to `main` when explicitly requested.

`prepare-release.yaml` is manually dispatched by Boardwright. It validates the
release, promotes `CHANGELOG.md`, writes revision-history variables, generates
accepted outputs/README, commits accepted release state plus the README
snapshot/render assets to `main`, records release metadata, creates the tag,
and pushes the tag.

`release.yaml` runs on semantic version tags. It reads committed release
metadata, generates the tag package, creates/updates the GitHub Release, and
uploads assets. It never pushes branch commits.

GitHub Actions is the v1 build engine. GitHub CLI integration is optional and
used by Boardwright when available for dispatch/status/download convenience.

## Variants

Supported variants:

```text
DRAFT
PRELIMINARY
CHECKED
RELEASED
```

Defaults live in `.boardwright/project.yaml` and can be overridden from CLI/TUI
without editing workflow YAML.

## Project Config

Boardwright uses `.boardwright/`:

```text
.boardwright/
  project.yaml
  branches.yaml
  legal.yaml
  revision_history.yaml
  revision_history_variables.env
  release.env
```

`release.env` is committed by release preparation when a tag should carry
variant/release-state metadata such as `RELEASE_VARIANT=CHECKED` and
`RELEASE_KIND=prerelease`.

## Revision History

KiCad sheets use fixed slots:

```text
${REVHIST_1_TITLE}
${REVHIST_1_BODY}
```

Boardwright always defines every configured slot. The newest release fills slot
1. This keeps the most relevant changes visible first when a project has many
revisions. Unused slots resolve to blank strings.

Projects can increase `.boardwright/revision_history.yaml` `slots` and edit or
add KiCad sheets to consume more variables. The KiBot preflight defines a
larger blank-capable ceiling.

## Changelog

Boardwright manages `CHANGELOG.md` through CLI/TUI actions. Supported sections:

- Status
- Added
- Changed
- Fixed
- Removed
- Notes

Release preparation promotes `Unreleased` before tagging, rejects duplicate
versions, and updates revision-history variables.

## README

`README.md` is generated from `boardwright_resources/kibot/resources/templates/readme.txt`.

Accepted `main` README content should include, where available:

- CI/build status badges.
- Current revision/tag.
- Current variant.
- Board render images.
- Board dimensions.
- Brief stackup/fabrication summary.
- Component counts, including SMT/THT where KiBot data supports it.
- Links to latest release assets and generated manufacturing outputs.

The tag workflow also attaches the generated README and board images to the
GitHub Release. Release body markdown should be generated from changelog and
release assets, with board renders shown side by side.

## Assets

Visible project media lives under `assets/`:

- `assets/logos/` for project logos used by README and KiBot pages.
- `assets/renders/` for generated board render PNGs that may be committed to
  `main` as the accepted README snapshot.
- `assets/3d/` for generated STEP/3D exports that are packaged as release
  artifacts but not normally committed to source branches.

KiCad template metadata remains in `meta/`, and KiCad worksheet files remain
in `Templates/`.

## CLI And TUI

The TUI is the intended everyday interface. The CLI remains scriptable and is
used by CI. Both call the same internal action layer.

Core actions:

```text
boardwright
boardwright init
boardwright status
boardwright change
boardwright preview
boardwright promote
boardwright release
boardwright legal
boardwright validate
```

Plain `boardwright` opens the TUI. `boardwright tui` is kept as an explicit
alias, and `boardwright --help` prints CLI help. Local development installs
the command with `scripts/install_boardwright.ps1`, which performs an editable
install with the TUI dependency by default.

Installation supports two scopes:

- **User/global**: installs the `boardwright` command into the active Python
  user scripts directory. The command operates on whichever Boardwright project
  the user runs it from.
- **Project-local**: installs into `.venv/` inside the current project and
  writes `boardwright.ps1`. This pins that project to its local Boardwright
  copy.

An optional PowerShell profile function may be installed. It searches upward
from the current directory for `.venv/Scripts/boardwright.exe` or
`boardwright.ps1`, then falls back to the global `boardwright.exe`. This keeps
the terminal command short while allowing separate projects to carry separate
Boardwright versions.

### TUI Product Design

The TUI is a minimal cockpit for the normal PCB project workflow. It should not
expose every internal command. It should expose the decisions the user actually
cares about while Boardwright handles routine plumbing automatically.

The TUI should answer three questions:

1. What state is the project currently in?
2. What should I do next?
3. What artifacts or release outputs are ready to review?

#### Design Principle

Every visible primary action should represent a real user decision. Routine
operations should be automatic, hidden, or available only through the CLI,
advanced menu, or debug path.

Routine operations include validation, revision-history variable generation,
legal/notice regeneration, raw workflow dispatch, and raw git status
inspection. These should not dominate the main screen.

#### Core Workflow

```text
Edit in KiCad
-> Record Changes
-> Commit + Push
-> Review Artifacts
-> Accept to Main
-> Create Release
```

#### Primary Actions

The main screen exposes only these primary actions:

```text
Record Changes
Commit + Push
Review Artifacts
Accept to Main
Create Release
Refresh
```

Optional advanced/fallback actions may exist, but they should not dominate the
main screen.

#### Record Changes

User intent: the user has made design/project changes and wants to record what
changed.

User input:

- changelog section
- changelog text

Supported changelog sections:

```text
Added
Changed
Fixed
Removed
Notes
```

After the user records a change, Boardwright should automatically:

1. update `CHANGELOG.md`
2. validate core project files
3. regenerate revision-history variable/env files
4. report any blocking issues

There should not normally be separate primary buttons for `Validate` or `Write
Revision History`; those are implementation details of preparing project state.

#### Commit + Push

User intent: the user wants to save the current project state and push it to
`dev`.

User input:

- commit message

Boardwright should:

1. check the current branch
2. check working tree state
3. check whether a changelog entry exists when source files changed
4. validate the project
5. regenerate revision-history variable/env files if needed
6. commit changed files
7. push to `origin/dev`

If preview CI already runs automatically on push to `dev`, the TUI does not need
a normal primary `Generate Preview` action. Preview generation should be treated
as a consequence of pushing `dev`.

Blocking failures should be stated directly, for example:

```text
Cannot commit: no changelog entry recorded.
Cannot push: no upstream branch configured.
Cannot push: GitHub authentication unavailable.
Cannot commit: validation failed.
```

#### Review Artifacts

User intent: the user wants to inspect generated preview outputs before
accepting them.

Boardwright should show:

- latest preview workflow status
- source branch
- source commit SHA
- artifact name
- artifact creation time
- whether the artifact matches the latest pushed `dev` commit
- fetch/download option
- fallback command or GitHub Actions link if GitHub CLI is unavailable

Artifact states should be simple:

```text
missing
running
failed
stale
ready
downloaded
```

This remains a first-class action because artifact review is a real human
decision point.

#### Optional Re-run Preview

`Re-run Preview` replaces `Generate Preview` as a normal primary action. It
should be shown or highlighted only when useful:

- latest preview failed
- latest preview is missing
- latest preview is stale
- user explicitly opens advanced/fallback actions

It should not be primary when preview CI already runs automatically on push to
`dev`, when the latest preview is running, or when the latest preview is ready
for the latest pushed commit.

#### Accept to Main

Use the label `Accept to Main`, not `Promote to Main`.

User intent: the user has reviewed preview artifacts and accepts them as the
current official project state. This means: the current reviewed output is good;
make it the accepted state on `main`.

Boardwright should:

1. confirm the selected variant
2. confirm latest preview artifact is ready
3. confirm preview matches the intended commit
4. require clean/pushed `dev`, unless explicitly overridden
5. dispatch or run the accepted-output workflow
6. update `main` with accepted README/render snapshot assets
7. report the accepted commit/output state

Variant options:

```text
DRAFT
PRELIMINARY
CHECKED
RELEASED
```

This action is not mainly about squashing. It exists because `main` represents
the reviewed and accepted project state, while `dev` is normal working design
history.

#### Create Release

User intent: the user wants to publish a draft, prerelease, or release from an
accepted `main` state.

User input:

- release kind: `draft`, `prerelease`, or `release`
- variant: `DRAFT`, `PRELIMINARY`, `CHECKED`, or `RELEASED`
- version/tag: `vMAJOR.MINOR.PATCH` or the configured semantic-version format

Boardwright should:

1. validate release metadata
2. reject duplicate versions/tags
3. ensure an accepted `main` state exists
4. prepare release state
5. create the tag through the controlled CI release-preparation flow
6. allow the tag workflow to publish artifacts only

Release-affecting operations must remain explicit. The TUI should make it hard
to accidentally create a release.

#### Main Screen Layout

The TUI uses one main cockpit screen:

```text
+-------------------------------------------------------------+
| Boardwright | dev | dirty 3 | rev v0.1.2 | PRELIMINARY | CI |
+----------------+-------------------------+------------------+
| Actions        | Workflow                | Next Action      |
|                |                         |                  |
| Record Changes | 1 Edit        external  | Record changes   |
| Commit + Push  | 2 Record      needed    | then commit/push |
| Review Output  | 3 Commit      blocked   |                  |
| Accept to Main | 4 Preview     waiting   |                  |
| Create Release | 5 Review      locked    |                  |
| Refresh        | 6 Accept      locked    |                  |
|                | 7 Release     locked    |                  |
+----------------+-------------------------+------------------+
| Changed files / validation issues / latest CI evidence       |
+-------------------------------------------------------------+
```

The top status bar should show project name, current branch, dirty file count,
ahead/behind remote, latest revision/tag, selected/default variant, CI state,
and validation state.

The central workflow timeline should show ordered project state:

1. Edit
2. Record
3. Commit + Push
4. Preview
5. Review Artifacts
6. Accept to Main
7. Release

Each step should use one of these state labels:

```text
external
ready
needed
blocked
running
waiting
failed
passed
locked
done
```

#### Next Action Logic

The TUI should compute one recommended next action.

Examples:

```text
Next: Record Changes
Reason: Source files changed but no changelog entry has been recorded.

Next: Commit + Push
Reason: Changelog is updated and validation passed.

Next: Review Artifacts
Reason: Preview CI passed for the latest pushed dev commit.

Next: Accept to Main
Reason: Preview artifacts are ready and match the latest dev commit.

Next: Create Release
Reason: Main has accepted outputs but no release tag has been created.
```

Approximate decision logic:

```text
if .boardwright config missing or incomplete:
    next = initialise/configure project
elif validation fails:
    next = fix validation issue
elif working tree dirty and no changelog entry:
    next = record changes
elif working tree dirty:
    next = commit + push
elif local dev is ahead of origin/dev:
    next = push
elif preview CI is running:
    next = wait/review CI status
elif preview missing or stale:
    next = wait for push-triggered preview or offer re-run preview
elif preview failed:
    next = inspect/re-run preview
elif preview artifact ready and not reviewed:
    next = review artifacts
elif preview reviewed and not accepted:
    next = accept to main
elif accepted main exists and release is desired:
    next = create release
else:
    next = continue editing in KiCad
```

#### Hidden Or Advanced Actions

These should not be primary main-screen actions:

```text
Validate
Write Revision History
Generate Preview
Legal
Raw Git Status
Raw Workflow Dispatch
```

They may exist as automatic sub-steps, CLI commands, advanced/debug menu
entries, or fallback actions when automation is unavailable.

#### Fallbacks

If GitHub CLI is unavailable or unauthenticated, the TUI should not fail
silently. It should show:

1. what action could not be automated
2. the exact command that would have been run, where practical
3. the manual GitHub Actions fallback

Example:

```text
GitHub CLI is unavailable.

Manual fallback:
Open GitHub Actions -> dev-preview.yaml -> Run workflow
Branch: dev
Variant: PRELIMINARY
```

If Textual is not installed, `boardwright` and `boardwright tui` keep the CLI
usable and print an installation hint.

#### Out Of Scope For Current TUI

Do not prioritise these yet:

```text
full YAML editor
full git client
full GitHub Actions browser
KiCad file browser
rich decorative dashboard
multi-board management
full metadata editor
```

These can be added later after the normal workflow is reliable.
## Legal And Notices

Boardwright generates:

```text
LICENSE
NOTICE.md
THIRD_PARTY_NOTICES.md
```

The notice system keeps hardware licence scope, branding exclusions,
compatibility wording, non-affiliation wording, safety notes, and third-party
notices explicit without implying legal advice or OEM endorsement.
