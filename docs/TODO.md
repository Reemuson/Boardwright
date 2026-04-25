# Boardwright TODO

Active implementation tracker. Product rules live in `SPEC.md`; sequencing
lives in `ROADMAP.md`.

## Done

- [x] Split planning into `SPEC.md`, `ROADMAP.md`, and `TODO.md`.
- [x] Add `.boardwright/` project config.
- [x] Scaffold Python package and CLI.
- [x] Add `boardwright init`, `status`, `change`, `validate`, `legal`,
      `revision-history`, `preview`, and `release`.
- [x] Add changelog parser/writer and release promotion.
- [x] Add legal/notice generation.
- [x] Add README template validation.
- [x] Add optional Textual TUI with console fallback.
- [x] Make plain `boardwright` open the TUI.
- [x] Add user/global and project-local install helper for the `boardwright`
      command.
- [x] Add TUI changelog-entry form.
- [x] Add safe git status and dry-run commit helpers.
- [x] Add GitHub Actions preview workflow.
- [x] Add GitHub Actions main-output workflow.
- [x] Add tag publish workflow.
- [x] Add KiBot revision-history variables with newest release first.
- [x] Make schematic ToC recurse through nested KiCad sheets.
- [x] Populate `${REVISION}` from git tags during release builds.
- [x] Attach generated README and board images to GitHub Releases.

## Active: Boardwright-Orchestrated Release Flow

- [x] Add shared action layer used by CLI and TUI.
- [x] Add `boardwright promote` planner/dispatcher.
- [x] Add `prepare-release.yaml` workflow.
- [x] Commit `.boardwright/release.env` during release preparation.
- [x] Make tag workflow read release metadata for variant and release kind.
- [x] Let Boardwright dispatch CI-owned tag creation.
- [x] Add initial TUI controls for preview, accept/promote, and release flows.
- [x] Add TUI commit and push controls for the normal dev loop.
- [x] Add workflow status polling where GitHub CLI is available.
- [x] Add preview artifact download/fetch helper.
- [x] Consolidate visible project media under `assets/`.
- [x] Collapse TUI primary actions to Record Changes, Commit + Push, Review
      Artifacts, Accept to Main, Create Release, and Refresh.
- [x] Make Record Changes automatically validate and regenerate revision-history
      variables.
- [x] Make Commit + Push automatically validate, regenerate revision-history
      variables, commit, and push to `origin/dev`.
- [x] Rename Promote/Accept flow to `Accept to Main`.
- [x] Treat preview generation as push-triggered by default; keep manual rerun
      as advanced/fallback only.
- [ ] Add artifact freshness checks against the latest pushed `dev` commit.
- [x] Add ahead/behind remote state to the TUI status bar and next-action logic.

## Active: Generated Main README

- [ ] Refresh `boardwright_resources/kibot/resources/templates/readme.txt` for Boardwright projects.
- [ ] Add CI status badges.
- [ ] Add current revision and variant.
- [ ] Add board dimensions.
- [ ] Add brief stackup/fabrication summary.
- [ ] Add component count summary.
- [ ] Add latest release/package links.
- [x] Keep board images side by side in README and release markdown.

## Active: CI Retest After Asset Move

- [ ] Preview workflow uses `assets/renders` and `assets/3d` correctly.
- [ ] Main-output workflow commits only README/render snapshot assets.
- [ ] Prepare-release workflow commits release state plus README/render snapshot
      assets.
- [ ] Release workflow packages `assets/` and attaches board renders.

## Verification Targets

- [x] Dummy repo can generate preview outputs.
- [x] Dummy repo can publish a tag release.
- [x] Revision history populates on generated schematic.
- [x] Revision variable populates on generated schematic.
- [x] Cover ToC includes nested sheets.
- [x] Prepare-release workflow can create a prerelease tag from `main`.
- [ ] Prepare-release workflow can create a draft tag from `main`.
- [ ] Prepare-release workflow can create a full release tag from `main`.
- [ ] TUI can drive the full happy path after the asset-path workflow retest.

## Later

- [ ] Add `boardwright adopt` for existing projects.
- [ ] Add richer legal/licence profiles.
- [ ] Add curated source package support if needed.
- [ ] Add local KiBot/Docker runner support after CI-first flow is solid.
- [ ] Revisit multi-board or assembly variants after KiCad/KiBot variant support
      settles.
