# Boardwright Roadmap

## Milestone 1: Local Project Control

Status: implemented.

Scope:

- `.boardwright/` config.
- CLI package and validation.
- Changelog recording.
- Legal/notice generation.
- Textual TUI cockpit with console fallback.
- Safe git status, commit, and push helpers.
- User/global and project-local install options.

Success criteria:

- A project can be initialised.
- A user can record changes without editing changelog structure by hand.
- Boardwright can validate core files and config.
- Primary TUI actions represent user decisions, not routine plumbing.

## Milestone 2: Preview Loop

Status: implemented, with artifact freshness checks still pending.

Scope:

- GitHub Actions preview workflow.
- Variant-aware preview dispatch.
- Disposable preview artifacts/branch.
- Local summary of expected generated outputs.
- Push-triggered preview as the normal path.
- Manual re-run preview as advanced/fallback.
- TUI Review Artifacts action.
- TUI/CLI fetch of preview artifacts when GitHub CLI is available.
- TUI polling for recent GitHub Actions runs.

Success criteria:

- A user can generate preview outputs from `dev`.
- CI never mutates `dev`.
- Preview artifacts can be inspected locally.
- Artifact freshness is checked against the latest pushed `dev` commit.

## Milestone 3: Accepted Main Outputs

Status: implemented plumbing, with preview freshness guards still pending.

Scope:

- `main-outputs.yaml` for accepted generated outputs.
- `boardwright promote`/Accept to Main action.
- Generated `README.md` committed to `main` on explicit promotion.
- Accepted output paths committed only when requested.
- TUI flow labelled `Accept to Main`, with variant selection and preview
  freshness checks.
- Accepted README render snapshots live under `assets/renders/`.

Success criteria:

- Boardwright can dispatch accepted-output generation on `main`.
- `main` README reflects the latest accepted outputs.
- Promotion is explicit, repeatable, and reviewable.

## Milestone 4: CI-Owned Release Tagging

Status: implemented, needs CI retest after asset-path move.

Scope:

- `prepare-release.yaml` manual workflow.
- Boardwright dispatches release preparation instead of requiring local git tag commands.
- CI promotes changelog/revision-history variables.
- CI generates accepted outputs and README.
- CI commits accepted files to `main`.
- CI tags the exact accepted commit.
- Tag workflow publishes artifacts without mutating branches.
- Draft/prerelease/release state is recorded in committed metadata.

Success criteria:

- A user can create a draft, prerelease, or release from the TUI.
- The tag points at the generated `main` commit.
- GitHub Release assets include package zip, README, and board images.
- No tag workflow commits back to `main`.

## Milestone 5: Rich README And Dashboard

Status: active next build.

Scope:

- README template refresh for Boardwright projects.
- CI status badges.
- Current revision and variant.
- Board dimensions.
- Stackup/fabrication summary.
- Component counts, including SMT/THT where available.
- Latest release links.
- TUI status polish as needed after real use.
- Maintain the small state-driven cockpit model:
  Record Changes, Commit + Push, Review Artifacts, Accept to Main, Create
  Release, Refresh.

Success criteria:

- The generated README is useful as the front page of a hardware repo.
- The TUI shows enough status that the user rarely needs GitHub Actions pages.

## Milestone 6: Project Onboarding And Metadata Editing

Status: planned.

Scope:

- First-run setup when `.boardwright/` is missing or incomplete.
- Edit/view project metadata from the TUI.
- Set GitHub repository, branch names, variants, logo/assets paths, and legal
  metadata without hand-editing YAML.
- Detect missing GitHub CLI authentication and show exact fallback commands.

Success criteria:

- A new board repo can be initialised and configured mostly through
  Boardwright.
- The user can keep working in KiCad and Boardwright without memorising config
  file paths.
