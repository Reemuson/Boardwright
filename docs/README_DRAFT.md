# Boardwright README Draft

This is a working draft for the repository README. It is separate from the
generated project README while we decide what stays from the inherited
Nguyen/KiBot template documentation and what becomes Boardwright-native.

## Working Position

Boardwright is a KiCad/KiBot hardware project template plus a small workflow
tool. It is meant to make the normal PCB loop boring:

```text
edit in KiCad -> record changes -> commit + push -> review artifacts
-> accept to main -> create release
```

The goal is not to expose every KiBot or GitHub Actions detail. The goal is to
let the designer keep working in KiCad while Boardwright handles routine
validation, revision-history variables, CI dispatch, artifact fetching, and
release preparation.

## What This Repository Contains

- KiCad template project files at the repository root.
- KiCad worksheet/title-block templates in `Templates/`.
- Boardwright project configuration in `.boardwright/`.
- GitHub Actions workflows in `.github/workflows/`.
- Boardwright CLI/TUI package in `src/boardwright/`.
- Boardwright/KiBot support resources in `boardwright_resources/`.
- Source assets in `assets/`.
- Planning and implementation docs in `docs/`.
- Regression tests in `tests/`.

Generated KiBot outputs such as `Manufacturing/`, `Schematic/`, `Reports/`,
`HTML/`, `KiRI/`, and `Testing/` are not part of the clean source tree.

## Current User Workflow

1. Create or open a Boardwright-based KiCad project.
2. Work on schematic, PCB, documentation, or project files.
3. Run `boardwright`.
4. Record what changed.
5. Commit and push to `dev`.
6. Review preview artifacts from CI.
7. Accept a reviewed state to `main`.
8. Create a draft, prerelease, or release tag when appropriate.

## Installing The Command

For a user/global editable install from the repository root:

```powershell
.\scripts\install_boardwright.ps1
```

Then run:

```powershell
boardwright
```

For a project-local install:

```powershell
.\scripts\install_boardwright.ps1 -Scope Project
.\boardwright.ps1
```

The project-local path keeps each hardware project pinned to its local
Boardwright checkout. The global command can also be configured to prefer the
nearest project-local install before falling back to the user/global install.

## TUI Shape

The TUI should stay small and state-driven. Its primary actions are:

- Record Changes
- Commit + Push
- Review Artifacts
- Accept to Main
- Create Release
- Refresh

Validation, revision-history generation, legal/notice refreshes, raw workflow
dispatch, and raw git plumbing should remain automatic, CLI-only, or advanced
fallback paths.

## Release Model

- `dev` is the normal working branch.
- Preview CI runs from `dev` and is disposable.
- `main` is the accepted project state.
- `main` should only receive source files plus accepted README/render snapshot
  assets needed for the front page.
- Tag workflows publish release artifacts without mutating branches.
- Drafts, prereleases, and releases are explicit user decisions.

## Licensing And Attribution

Boardwright is derived from Nguyen Vincent's KiCad/KiBot template work,
originally published as `KDT_Hierarchical_KiBot`.

The current repository still contains inherited material, including KiCad
worksheet/title-block templates, KiBot configuration patterns, support scripts,
fonts, colors, workflow structure, and parts of the original documentation.

The inherited template is MIT licensed. The preserved license text remains in
`LICENSE`. Current inherited-material notes live in
`THIRD_PARTY_NOTICES.md`.

Boardwright-specific Python tooling, workflow changes, project metadata, TUI
work, and documentation edits are modifications layered on top unless a file
states otherwise.

Open question before final README:

- Do we keep the existing MIT-only framing, add a clearer dual-origin notice,
  or split Boardwright-specific code notices from inherited template notices?

## What To Rewrite From The Existing README

Keep or adapt:

- KiCad template installation notes.
- Font/theme installation notes.
- KiBot output concepts.
- PCB layer/group naming guide.
- Revision-history and table-of-contents guidance, updated for Boardwright's
  current variable model.

Rewrite heavily:

- Branch/release workflow.
- Local install instructions.
- TUI usage.
- Generated README behavior.
- Folder structure.
- CI/CD instructions.

Possibly move out of the main README:

- Long PCB conversion guide.
- Long KiBot layer examples.
- Old screenshots from the inherited project.
- Docker/local KiBot details.

## Draft README Outline

1. What Boardwright is.
2. Who it is for.
3. Quick start.
4. The normal workflow.
5. TUI actions.
6. Repository layout.
7. GitHub Actions model.
8. Generated outputs and releases.
9. KiCad template conventions.
10. Licensing, attribution, and inherited material.
11. Development notes for contributors.

