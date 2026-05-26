# Boardwright TODO

This is the actionable tracker. Product rules live in `SPEC.md`; sequencing
lives in `ROADMAP.md`.

## Current Focus: Source Of Truth And TUI Clarity

Goal: keep the docs, dashboard, and live workflow aligned after the first full
end-to-end release run.

- [x] Replace compact CI abbreviations with readable phase names in the TUI
      status strip and evidence panel.
- [x] Make the dashboard tag display use the latest semantic release tag, with
      prerelease fallback when no stable release tags exist.
- [x] Document the latest-tag rule in `SPEC.md`.
- [ ] Do one more TUI happy-path pass after the latest status-strip changes.
- [ ] Capture any remaining live CI/release findings in this tracker.

## Done: Fresh Preview Acceptance

Goal: make `Review Artifacts` and `Accept to Main` trustworthy.

- [x] Add a preview run/artifact model in `src/boardwright/preview.py`.
- [x] Query recent preview runs with enough metadata to know source branch,
      head SHA, status, conclusion, run id, and creation time.
- [x] Add a git helper for the latest pushed `origin/dev` SHA.
- [x] Mark preview state as `missing`, `running`, `failed`, `stale`, or
      `ready`.
- [x] Teach `fetch_latest_preview_artifact()` to require a successful fresh run
      for the requested variant.
- [x] Update TUI Review Artifacts to show branch, SHA, artifact name, creation
      time, and freshness.
- [x] Write a local review marker after fetching a fresh preview artifact.
- [x] Gate `Accept to Main` when the latest preview is
      missing, failed, stale, or unreviewed.
- [x] Add tests for ready/stale/failed/running preview-state decisions.
- [x] Add live GitHub CLI/manual fallback copy for missing `gh` or auth failure.

## Done: TUI Workflow State

Goal: make the TUI render from shared project state instead of local ad hoc
rules.

- [x] Add shared workflow-state model with stage, next action, reason, timeline
      steps, and primary action enablement.
- [x] Wire TUI timeline and Next Action panel to the shared model.
- [x] Lock/unlock primary TUI actions from the shared model.
- [x] Expose current stage and next action in `boardwright status`.
- [x] Add tests for key stages: validation blocked, needs changelog, ready to
      commit, needs push, stale preview, fresh reviewed preview, release ready.

## Near: Review Artifacts Screen

- [x] Add dedicated TUI screen/modal for preview artifact evidence.
- [x] Show run id, branch, source SHA, expected SHA, variant, artifact name,
      created time, state, and reviewed marker.
- [x] Provide fetch/review action from the screen.
- [x] Show manual GitHub fallback instructions in the screen.
- [ ] Add richer artifact open/browse shortcuts after download.

## Done: Accepted Main And Release Readiness

- [x] Track/report latest accepted main-output workflow state.
- [x] Show accepted main-output source SHA where available.
- [x] Poll and summarize preview, accepted-main, and release CI status in the
      TUI.
- [x] Add CLI `accepted` command for accepted main-output state.
- [x] Block Create Release unless accepted main outputs are fresh for
      the latest pushed `origin/dev` source SHA.
- [x] Add Create Release checklist before dispatch.
- [x] Add CLI `review` command for preview artifact state.
- [x] Add CLI `doctor` command for environment/integration checks.
- [x] Add live-testbench plan/init command and `docs/TESTBENCH.md`.

## Near: CI Retest And Output Polish

Goal: prove workflows still work after moving visible generated media under
`assets/`.

- [x] Record live-test finding: standalone KiBot `notes` target is unsafe for
      this template; notes should be generated through the normal output group.
- [x] Record live-test finding: use `PRELIMINARY` for template harness testing
      because the template PCB intentionally lacks a fabrication-ready outline.
- [x] Clean generated packages after KiBot so preview/release artifacts do not
      include numbered PDF page shards or empty generated CSV tables.
- [x] Stop generating the placeholder impedance CSV by default until there is a
      reliable project data source for controlled-impedance traces.
- [x] Make TUI preview artifact fetch non-blocking and simplify the review
      evidence display.
- [x] Remove global `include_table` preflight after live CI proved it runs
      before the CSV reports it needs.
- [x] Restore output-level `pcb_print.include_table` for movable drill,
      and testpoint placeholders.
- [x] Replace assembly component-count table with a non-recursive source; KiBot
      `report`-based component-count output recurses when used by
      `pcb_print.include_table` in the same run.
- [x] Prune empty top/bottom testpoint PDF pages after side-specific testpoint
      CSVs are generated.
- [x] Restore full default fabrication and assembly notes for PDF text-variable
      fallback.
- [x] Replace the impedance table body with "No impedance controlled traces"
      when no controlled-impedance entries are configured.
- [ ] Add a controlled-impedance data model and restore impedance table output
      when projects explicitly opt in.
- [x] Keep the PCB template's explicit Arial typography and install bundled
      fonts for CI rendering.
- [ ] Decide how Boardwright should model/edit fabrication notes,
      assembly notes, testpoint policy, and controlled-impedance requirements
      from the TUI.
- [x] Add a TUI project-information editor for project name, company, author,
      GitHub repository, assets, and variant defaults.
- [ ] Extend the TUI project-information editor to fabrication notes, assembly
      notes, and other project-specific manufacturing metadata.
- [x] Review stackup legend layer colors against the original template.
- [x] Preview workflow uses `assets/renders` and `assets/3d` correctly.
- [x] Preview artifact includes `README.md`, `assets/`, and expected output
      folders.
- [x] Preview branch publishes only disposable generated review content.
- [x] Main-output workflow builds from the reviewed source SHA, verifies that
      SHA, and pushes the accepted source plus `README.md` and
      `assets/renders/*.png` to `main` when `commit_outputs` is true.
- [x] Main-output workflow still pushes the reviewed source merge when the
      README/render snapshot is unchanged.
- [x] Prepare-release can create a release section with a generated no-change
      note when `CHANGELOG.md` has no unreleased entries.
- [x] Prepare-release workflow commits `CHANGELOG.md`,
      `.boardwright/revision_history_variables.env`, `.boardwright/release.env`,
      `README.md`, and `assets/renders/*.png`.
- [x] Release workflow packages `assets/` and attaches board render PNGs to the
      GitHub Release.
- [ ] Record any live CI findings back into this tracker.

## Next: Generated README

Goal: make the generated README a useful hardware project front page.

- [x] Add Boardwright-specific README structure.
- [ ] Add CI status badges only when repository URL metadata is reliable and
      generated README links will not be broken.
- [x] Keep `${REVISION}` as semantic version and add `${BOARD_REVISION}` for
      A/B/C board spins.
- [x] Add board dimensions.
- [x] Keep board images side by side in README and release markdown.
- [ ] Add latest release/package links.
- [ ] Add brief stackup/fabrication summary if KiBot exposes reliable variables.
- [ ] Add component count summary if KiBot output data supports it.
- [x] Remove or reword placeholder text that will look stale in real projects.
- [ ] Confirm generated README still mentions `LICENSE`, `NOTICE.md`, and
      `THIRD_PARTY_NOTICES.md`.

## Near: CLI And Packaging Polish

- [x] Add `src/boardwright/__main__.py` so `python -m boardwright` works.
- [x] Update `boardwright release --prepare` output so it no longer suggests
      the old local tag/push sequence as the primary release path.
- [ ] Decide whether to add a dev/test optional dependency group.
- [ ] Make bare test instructions explicit in contributor docs:
      `python -m unittest discover -s tests -v`.

## Near: TUI Polish

- [x] Show exact manual fallback commands when GitHub CLI is unavailable.
- [ ] Add direct GitHub Actions URLs after repository metadata is configured.
- [x] Make Generate Preview a direct TUI action instead of hiding dispatch in
      CLI-only/manual fallback flows.
- [x] Rebalance primary/secondary action layout now that Generate Preview and
      Project Info are first-class TUI actions.
- [x] Compact the main workflow map so long per-step explanations live in the
      inspector or modal screens instead.
- [x] Add a sectioned Now/Evidence/Release inspector instead of a raw status
      dump.
- [x] Make TUI keybindings use the same action gates as disabled/enabled
      buttons.
- [ ] Tune next-action labels after more full-path live use.
- [x] Verify the TUI can drive the full happy path after CI retest.

## Verification Targets

- [x] Local validation passes.
- [x] Python unittest suite passes.
- [x] Dummy repo can generate preview outputs.
- [x] Dummy repo can publish a tag release.
- [x] Revision history populates on generated schematic.
- [x] Revision variable populates on generated schematic.
- [x] Cover ToC includes nested sheets.
- [x] Prepare-release workflow can create a prerelease tag from `main`.
- [ ] Prepare-release workflow can create a draft tag from `main`.
- [x] Prepare-release workflow can create a full release tag from `main`.
- [x] TUI can drive record -> commit/push -> review -> accept -> release.
- [ ] Measure CI time after switching caches to `actions/cache@v4`; decide
      whether accepted outputs need a later artifact-promotion fast path.

## Done

- [x] Split planning into `SPEC.md`, `ROADMAP.md`, and `TODO.md`.
- [x] Add `.boardwright/` project config.
- [x] Scaffold Python package and CLI.
- [x] Add `boardwright init`, `status`, `change`, `validate`, `legal`,
      `revision-history`, `preview`, `promote`, and `release`.
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
- [x] Add prepare-release workflow.
- [x] Add tag publish workflow.
- [x] Add shared action layer used by CLI and TUI.
- [x] Commit `.boardwright/release.env` during release preparation.
- [x] Make tag workflow read release metadata for variant and release kind.
- [x] Let Boardwright dispatch CI-owned tag creation.
- [x] Add TUI commit and push controls for the normal dev loop.
- [x] Add workflow status polling where GitHub CLI is available.
- [x] Add preview artifact download/fetch helper.
- [x] Consolidate visible project media under `assets/`.
- [x] Make schematic ToC recurse through nested KiCad sheets.
- [x] Add KiBot revision-history variables with newest release first.
- [x] Populate `${REVISION}` from git tags during release builds.
- [x] Attach generated README and board images to GitHub Releases.

## Later

- [ ] Add `boardwright adopt` for existing projects.
- [ ] Add richer legal/licence profiles.
- [ ] Add curated source package support if needed.
- [ ] Add local KiBot/Docker runner support after CI-first flow is solid.
- [ ] Revisit multi-board or assembly variants after KiCad/KiBot variant support
      settles.
