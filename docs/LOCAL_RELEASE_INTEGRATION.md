<!-- SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available -->
<!-- Copyright (c) Ryan Hicks -->

# Local Releases: ci-test Integration Plan

Status: **approved for preparation (owner, 2026-11-21), not run.** The
installation commit is prepared locally and held for review; nothing has
been pushed to boardwright-ci-test and no run has been dispatched. The local-release engine is described in `LOCAL_FIRST.md` sections 5
and 7.1. Earlier results are in `AUDIT-2026-11.md`.

## 1. Purpose

Run the whole local-release lifecycle on a GitHub-hosted Linux runner with
real Docker and the pinned KiCad 9 image, against a disposable clone of
ci-test. This repeats the two live Windows releases (0.1.8 and 0.1.9 in
`AUDIT-2026-11.md`) on Linux. It also exercises the CLI and the TUI and
covers the source-review corrections F-R1 and F-R2. It follows the preview
integration that passed in run `44711268100`.

## 2. Boundaries

Each of these rules is also checked by the run itself.

| Boundary | How |
| --- | --- |
| Read-only workflow | `permissions: contents: read` and nothing else; `actions/checkout` with `persist-credentials: false` |
| Disposable clone | Clone ci-test into `$RUNNER_TEMP/bw local rélease/repo` (a path with a space and a non-ASCII character), then `git remote remove origin` before any Boardwright command: the release backend is local by default because there is no remote |
| Engine | This branch's Boardwright is committed in the clone only, as in the live runs; ci-test `main` keeps its installed payload |
| Store and stages | `BOARDWRIGHT_RELEASE_STORE` and `BOARDWRIGHT_STAGE_ROOT` under `$RUNNER_TEMP` |
| No push, tag or release on GitHub | No `git push`, no `gh release`, no workflow dispatch from the run. Before and after the run, record `git ls-remote` of ci-test and `gh release list` (read-only token) and require them to be identical |
| Held GitHub candidates | Not approved, not named, not downloaded except read-only for comparison (check 4) |

## 3. Installation (owner, after review)

A `workflow_dispatch` workflow must exist on the default branch before it can
be dispatched. One authorised installation commit on ci-test `main` adds the
reviewed Boardwright payload, the testbench-only
`local-release-integration.yaml` and its dedicated harness
(`.github/local-release-integration/`). It is reviewed before the owner's
authenticated push. Nothing else changes ci-test `main`; the run itself
writes only under `$RUNNER_TEMP` and to its own artifact.

## 4. Checks

| # | Area | Expected |
| --- | --- | --- |
| 1 | Backend selection | No remote: local by default; `--backend github` refused ("no remote"); a GitHub `origin` URL without `gh` login: release refused with the problem, and `--backend local` warns local-only; nothing falls back |
| 2 | Plan refusals | Dirty tree, untracked file and existing tag are each refused before any build. Refs, tree, store and index are unchanged |
| 3 | Read-only views | `status`, `plan`, the TUI summaries and the dashboard line leave `.git/objects`, refs, the index (mtime) and the store byte-identical |
| 4 | Release A `0.1.8` (tooling) | Candidate with the held candidate's inputs and date. Fabrication `A/2` and assembly `A` reused against `0.1.7`'s committed manifest. An untouched review draft is refused. Approve, finalise, duplicate finalise (no rebuild), publish. Package: 392 zip members and 358 `MANIFEST.txt` entries. Controlled outputs equivalent to held run `44670323650` (0 blocking) |
| 5 | Release B `0.1.9` (fabrication) | Silkscreen label moved 1 mm. With PCB revision `A` kept, preparation is refused before building. With `board_revision: B`: new baselines `B/1` and `B-A`, and exactly the front silkscreen Gerber differs |
| 6 | Interrupted finalisation | A driver wrapper raises at `after-branch` (the test checkpoint). `status` reports `advanced` and the interrupted step. TUI Recover brings the tree to the release commit. `finalize` resumes without a rebuild |
| 7 | Lost index events (F-R1) | A wrapper drops the candidate event after the freeze. `status` names `recover`, `recover` appends the event, and the same candidate is then approvable with no new entry |
| 8 | Publication failure | The store write fails before the rename: nothing is published and no temporary is left. The retry publishes. A one-byte change to the zip is reported by `status` and refused by `publish` (on a copy of the store) |
| 9 | Signing (F-R2, optional, two more builds) | `0.1.10` (documentation) with a runner-generated SSH key: the commit and tag verify. After the allowed-signers file is emptied, `finalize --sign` and `publish` refuse and `status` reports the problem |
| 10 | TUI | Headless Textual pilot on the real clone: backend screen (GitHub disabled, no remote), summaries for both releases (candidate id, source, drawing revisions, baselines, local approval labels), confirmation required for every write, Recover in check 6 |
| 11 | Developer checkout | Each release changes exactly the release commit's files. History is `0.1.7 <- source <- 0.1.8 <- … <- 0.1.9`. The ledger is append-only and the store index is intact |
| 12 | ci-test unchanged | `ls-remote` and the release list are identical before and after; there are no new tags or releases |

Pass: every check. The run uploads the logs, `index.jsonl`, `publish.json`,
`MANIFEST.txt` and each status output as an artifact, but not the release
zips. Estimated time: about 30 minutes for checks 1 to 8 (Windows took
200–290 s per build), plus about 10 minutes for check 9. Timeout: 90 minutes.

## 5. Stop points

1. Owner review of this plan.
2. The driver script is written and reviewed in this repository; it is not
   run against ci-test.
3. The owner installs the workflow on ci-test `main` and dispatches it, or
   authorises a dispatch.
4. Results are reviewed, and recorded in `AUDIT-2026-11.md`, before any
   local release on a real project or on ci-test `main`.
