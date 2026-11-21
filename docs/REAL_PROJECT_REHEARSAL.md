<!-- SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available -->
<!-- Copyright (c) Ryan Hicks -->

# Local Releases: Real-Project Rehearsal (FIC-100A 0.1.1)

Status: **plan, not executed (2026-11-21).** The read-only audit in
section 2 is complete. Nothing in sections 5 to 8 has been run. The owner
reviews this plan before any preparation commit, candidate, approval,
finalisation or publication.

This follows the ci-test integration (`LOCAL_RELEASE_INTEGRATION.md`, run
`44868431634`). The local-release engine is described in `LOCAL_FIRST.md`
sections 5 and 7.1. Baselines and change classes are defined in
`RELEASE_MODEL.md`.

## 1. Purpose and scope

Run one complete local release (plan, candidate, review, approve, finalize,
publish) on a real hardware project, in a disposable clone, with the pinned
KiCad 9 image. The release is an **upgrade of an existing release**: the
published legacy `0.1.0` stays the previous release, and the original Git
history is preserved.

The rehearsal changes only the disposable clone and a local release store
outside it. It never pushes, tags or releases on GitHub. The outcome is
evidence that the engine handles a real legacy history. It is not a
production release of FIC-100A: publishing a real `0.1.1` would need a separate
decision.

## 2. Project findings (read-only audit, 2026-11-21)

### 2.1 Source identity

| Item | Value |
| --- | --- |
| Repository | `Reemuson/FIC-100A` (public, default branch `main`) |
| Selected source | `dev` at `4dcaf5a675c128cb4387ebd28751d9de8c3eb9d7` ("fix: removed stale variable causing erc errors", 2026-07-29), tree `c73d3197d7373500964fec607107cbb7553f1dbb` |
| `main` | `91a52c5323bab2c132fe2780c17d0dac07963fa9` ("ci: bootstrap Boardwright workflows") |
| `preview` | `762cabf69f1178c65978853c60bfe6dc9898f196` (generated outputs, unrelated history) |
| Tags | `0.1.0` only: a **lightweight** tag on `9d2a3c6287a5e2090b67797cbb52d33bf4abd94e`, tree `05c85a9ac610865ec42c7f1e721445eb025ef94f` |
| GitHub release | `0.1.0`, published 2026-04-24 by `github-actions[bot]`, not a draft. 16 loose assets (Gerber and ODB++ zips, PDFs, BOM, CPL, STEP, notes), all uploaded by the release workflow and carrying SHA-256 digests |
| Working trees | None locally. The audit used a scratch clone with the push URL disabled. `git ls-remote` and `gh release list` were identical before and after the audit |

### 2.2 History and topology

- `dev` does **not** contain the release commit. It branched at `0603f21`;
  the tag commit is 11 commits later on `main` (CI output commits and a
  changelog reset). `0.1.0` is an ancestor of `main`, not of `dev`.
- After the release, `main` gained `91fde94` ("chore: snapshot project before
  Boardwright migration"), `c241497` (outputs) and `91a52c5` (workflows). The
  migration on `dev` (`e43a6e7`, `23d8e35`, `4dcaf5a`) carries the design
  content of `91fde94`, not its commit.
- `git merge-tree origin/main origin/dev` reports conflicts in
  `FIC-100A.kicad_pcb`, `FIC-100A.kicad_pro`, `FIC-100A.xml` and
  `FIC-100A-netlist.net`. `main` also still has the pre-migration tree: legacy
  `kibot_yaml/`, `kibot_resources/` and committed generated outputs
  (`Manufacturing/`, `KiRI/`, `HTML/` and others), which `dev` removed.

### 2.3 Boardwright configuration and payload

- `.boardwright/` exists on `dev` only. `state.yaml`: `schema_version: 1`,
  `template_version: 0.1.0`, origin `nguyen-hierarchical`.
- `project.yaml`: `pcba_name`/`pcb_name` `FIC-100A`, `board_revision: A`,
  schematic, assembly and fabrication drawings `A` (lettered), release branch
  `main`, `commit_generated_outputs_to_main: false`.
- `document_revisions.yaml`: one schematic row, `A 2026-04-24 CHECKED
  PRE-RELEASE FOR FIRST BOARD SPIN`; assembly and fabrication rows empty.
  This row was written at migration. The published `0.1.0` drawings carry
  revision `0.1.0` in every title block (`REVISION` text variable, PCB
  `(rev "${REVISION}")`), not `A`.
- No `.boardwright/releases.yaml` (ledger) and no accepted output manifest.
- The embedded payload predates the ledger and the local-release engine. 34
  modules of this branch are absent (`local_release.py`,
  `release_ledger.py`, `output_manifest.py`, `fingerprint.py` and others)
  and 20 shared modules differ. With this branch's engine, `update status`
  reports 66 modified managed files and 2 blockers; the update plan has 156
  operations (42 replace, 38 preserve, 37 add, 26 merge, 9 unchanged, 2
  remove, 2 conflicts):
  - `Templates/Boardwright_Template_AS1100.kicad_wks` (944 local lines changed
    against its managed base, likely the embedded logo);
  - `boardwright_resources/kibot/yaml/kibot_main.yaml` (28 local lines: the
    project's own definitions such as `PCBA_NAME` and `COMPANY`).

### 2.4 Design differences, published 0.1.0 to dev

Computed with this branch's fingerprint (schema 3) from Git objects only:

| Fingerprint | 0.1.0 = `0603f21` | `dev` | Changed in |
| --- | --- | --- | --- |
| `pcb_geometry` | `774a48ac…` | `a0d0a195…` | `91fde94` (main), then `23d8e35` |
| `pcb_netnames` | `d7e4fea1…` | same | — |
| `fabrication_spec` | `44136fa3…` | `5ce009de…` | migration |
| `assembly` | `d92fe308…` | `89a8491b…` | `91fde94` |
| `schematic` | `34fa08d9…` | `da35772c…` | `91fde94`, migration |
| `tooling`, drawing sheets | | all differ | migration |

The geometry change between `0.1.0` and `91fde94` is a real layout edit:
`TP2` moved, vias 66 → 70, segments 162 → 167, 91 copper items differ.
Footprints and `Edge.Cuts` are unchanged. `23d8e35` changed only properties
and embedded files. The published fabrication notes (`ENIG`, `IPC-6012A
CLASS 2`, "all vias are tented") differ from the migrated `project.yaml`
(`IPC-6012 Class 2`, `tented_vias: false`). The fabrication data therefore
changes and needs **PCB revision B**.

### 2.5 Engine support for this legacy history

- With no ledger, `latest_baseline` reconstructs `0.1.0` read-only from its
  tag (`legacy_baseline_from_tag`). Without `project.yaml` at the tag, it
  assumes PCB revision `A` and blank drawing revisions.
- `check-baseline --local` fails closed when a baseline is **reused or
  reissued** against a previous release without a committed manifest
  (`test_legacy_previous_release_fails_closed_without_github`). The GitHub
  fallback would fail too, because the `0.1.0` release has no
  `boardwright-0.1.0-release.zip` asset.
- With `board_revision: B`, both baselines are **new** (`B/1`, `B-A`), so no
  comparison with `0.1.0` is required and nothing is bypassed. In-memory impact
  assessment on the pinned tree (no file written):

  ```text
  Project release: 0.1.1 (previous 0.1.0, legacy tag)
  PCB revision: B (A -> B)
  Schematic revision: B ((blank) -> B)
  Assembly drawing revision: A ((blank) -> A)
  Fabrication drawing revision: A ((blank) -> A)
  Fabrication baseline: B/1 (new)
  Assembly baseline: B-A (new)
  Review: Baseline 0.1.0 has no recorded schematic, assembly drawing,
          fabrication drawing revision, so revision changes cannot be enforced
  ```

  This assumes the classes `fabrication`, `assembly` and `tooling` (each
  required: dropping any one is a problem) plus `documentation`. With PCB
  revision `A` it is refused: "The physical PCB design changed since 0.1.0
  but the PCB revision is still A."
- Gaps:
  - G1: no test covers a local candidate whose previous release is a legacy
    tag and whose baselines are both new. The rehearsal would be its first
    exercise.
  - G2: schematic revision `A` is also accepted, because the legacy baseline
    records none. The rehearsal must impose `B` itself (section 4).
  - G3: the drawing-revision table at the tag is absent, so the migration
    row (schematic `A` = 2026-04-24) becomes permanent issued history once
    `0.1.1` is tagged.
  - G4: `0.1.0` is a lightweight tag. Local tag checks list it, but its
    annotation (none) is not an approval record and is not treated as one.

### 2.6 Validation with this branch's engine

`python -m boardwright validate` on the pinned commit (scratch checkout):
**exit 1, 2 errors, 3 warnings.**

```text
error: Silkscreen text omitted from Gerbers by plot options: TP2 text '${VALUE}' on F.SilkS. ...
error: Silkscreen text omitted from Gerbers by plot options: TP1 text '${VALUE}' on F.SilkS. ...
warning: Deprecated text variable FIC-100A.kicad_pcb:12: ${REVISION} (pcb) - replace with ${PCB_REVISION}.
warning: schematic renders the worksheet embedded in the design file (kicad-embed://Boardwright_Template_AS1100.kicad_wks). ...
warning: pcbnew renders the worksheet embedded in the design file (kicad-embed://Boardwright_Template_AS1100.kicad_wks). ...
```

`dev` no longer defines `REVISION`, so the PCB title block revision would not
resolve. `release local plan 0.1.1` on `dev` was refused only for the branch
("Current branch is dev; expected release branch main"). Refs and the tree
were unchanged.

### 2.7 Eligibility

FIC-100A is **not eligible as pinned**. The blockers are B1 to B5 below.
Each is resolved by a preparation commit in the disposable clone (section 5)
and needs approval. None is in the engine.

| # | Blocker | Resolution in the clone |
| --- | --- | --- |
| B1 | Release branch `main` does not contain the migrated design | R1: history-preserving reconciliation merge |
| B2 | Embedded payload has no local-release engine | P1: `boardwright update` to this branch, two conflicts resolved |
| B3 | Validation errors (TP1/TP2 silkscreen) | P2: owner decision: hide the text or enable its plot option |
| B4 | PCB geometry changed, revision still `A` | P3: `board_revision: B` |
| B5 | `${REVISION}` unresolved on the PCB | P2: replace with `${PCB_REVISION}` |

Advisory (not blocking): `[Unreleased]` is empty (P4 adds owner-written
entries). The embedded worksheets could be adopted with
`boardwright worksheet adopt`; this is optional and not proposed.

## 3. Selected rehearsal: 0.1.1 upgrade, original history preserved

The engine handles the legacy baseline safely here because the change
requires new baselines. No baseline check is skipped (`--skip-baseline` is
never used), and `0.1.0` stays the declared previous release (`previous 0.1.0,
legacy tag` in the candidate and the ledger). The rehearsal verifies that
outcome (section 7) rather than assuming it.

The release commit has `0.1.0` as an ancestor, so the history reads
`0.1.0 ← main ← reconciliation ← preparation ← release 0.1.1`.

**Fallback (only if the owner declines R1 or the preparation commits):** a
first-release rehearsal in a fresh disposable repository, importing the
pinned tree `c73d319…` as a single root commit. Its evidence must record the
source repository, commit and tree, state that the new history is not
FIC-100A's, and state that FIC-100A has a published legacy `0.1.0` that the
rehearsal does not model. Its version would be `0.1.1`, so it cannot be
confused with `0.1.0`. This tests a first release, not an upgrade. It is not
proposed while the upgrade path is available.

## 4. Proposed release inputs

| Input | Value | Why |
| --- | --- | --- |
| Version | `0.1.1` | Next patch after the only release. The design change is the first board respin of a pre-release product, so it does not need a minor bump. The owner may choose `0.2.0` instead; nothing else in this plan depends on the version |
| `project.board_revision` | `B` (P3) | Required: geometry changed since `0.1.0`. `B` was never issued: `0.1.0` was labelled `0.1.0`, and the engine treats it as `A` |
| `--drawing-revision` | `B` | Schematic content changed. The migration row records `0.1.0` as schematic `A`, so `A` cannot be reused (G2) |
| Assembly and fabrication drawing revisions | `A` (unchanged in `project.yaml`) | First lettered issue. The `0.1.0` drawings were labelled `0.1.0` |
| `--revision-description` | `PCB REV B: TP2 MOVED, ROUTING AND FAB NOTES UPDATED` (51 chars) | States the controlled change. The owner may reword it within 60 characters |
| `--change-class` | `fabrication,assembly,tooling,documentation` | The first three are required (2.5). `documentation`: schematic and drawing sheets changed. `electrical` only if the owner confirms a circuit change; the PCB net names are unchanged |
| `--justification` | `First Boardwright release of FIC-100A after legacy 0.1.0: PCB revision B (TP2 moved, routing and fabrication notes updated) and migration to Boardwright. New fabrication B/1 and assembly B-A baselines; no baseline reused.` | Recorded in the ledger. It names the legacy predecessor and the expected baselines |
| `--kind` | `draft` | Marks the rehearsal as not a production release, as on ci-test |
| `--variant` | `RELEASED` (default) | The project's `release_default` |
| `--release-date` | The UTC date of the candidate run, passed explicitly | Reproducible; finalize and publish reuse it |
| `--backend` | omitted (`local` by default, because the clone has no remote) | Matches the boundary in section 5 |
| Signing | Unsigned (no `--sign`) | Signing was proven on ci-test (check 9). Using the owner's real key would bind a rehearsal to a real identity; it can be added if the owner asks |

## 5. Procedure

### 5.0 Prerequisites (owner, before any execution)

- Owner approval of this plan, of R1, of P1 to P4 and of the inputs in
  section 4.
- Docker running. Audit state: Docker 29.7.2; the pinned image
  `ghcr.io/inti-cmnb/kicad9_auto_full:1.9.1-2_k9.0.7_d13.2_b4.2.4LTS@sha256:4d47f3b808e018d0a45afa597b4ffcd2586128bfa9801e5ebedd64cc45758092`
  is already present locally (`docker image inspect`). Do not pull another tag.
- Disk: 42 GB free on `C:` (96 % used). Keep at least 15 GB free; three
  builds plus the stages and store are expected to need under 5 GB.
- Boardwright at a reviewed commit of `feat/local-first-backends`,
  recorded as `ENGINE_SHA`, with a clean tree and the full suite passing.
  G1 should be covered by a focused test first (a separate, reviewed change).
- Paths, all outside both repositories:
  - `W=~/Documents/boardwright-rehearsal/fic-100a-0.1.1` (clone in `W/repo`)
  - `BOARDWRIGHT_RELEASE_STORE=$W/store`
  - `BOARDWRIGHT_STAGE_ROOT=$W/stages`
  - `EVIDENCE=~/Documents/boardwright-e2e/evidence/runs/rehearsal-fic-100a-0.1.1`

### 5.1 Authorised read-only checks (may be repeated at any time)

```bash
git ls-remote https://github.com/Reemuson/FIC-100A.git      # must equal section 2.1
gh release list -R Reemuson/FIC-100A                         # one release, 0.1.0
gh api repos/Reemuson/FIC-100A/releases/tags/0.1.0 --jq '.assets[]|.name+" "+.digest'
python -m boardwright validate                                # in a scratch checkout
python -m boardwright release local plan 0.1.1                # documented read-only
python -m boardwright release local status
```

Expected `ls-remote` (the rehearsal stops if any line differs):

```text
91a52c5323bab2c132fe2780c17d0dac07963fa9	HEAD
4dcaf5a675c128cb4387ebd28751d9de8c3eb9d7	refs/heads/dev
91a52c5323bab2c132fe2780c17d0dac07963fa9	refs/heads/main
762cabf69f1178c65978853c60bfe6dc9898f196	refs/heads/preview
9d2a3c6287a5e2090b67797cbb52d33bf4abd94e	refs/tags/0.1.0
```

### 5.2 Clone and isolate (needs approval)

```bash
git clone https://github.com/Reemuson/FIC-100A.git "$W/repo" && cd "$W/repo"
git fetch origin --tags
test "$(git rev-parse origin/dev)"       = 4dcaf5a675c128cb4387ebd28751d9de8c3eb9d7
test "$(git rev-parse origin/main)"      = 91a52c5323bab2c132fe2780c17d0dac07963fa9
test "$(git rev-parse 'refs/tags/0.1.0')" = 9d2a3c6287a5e2090b67797cbb52d33bf4abd94e
git branch -f dev 4dcaf5a675c128cb4387ebd28751d9de8c3eb9d7
git remote remove origin          # local backend by default; nothing can push
git for-each-ref > "$EVIDENCE/refs-0-clone.txt"
```

Make a bundle backup of the untouched clone (`git bundle create
$W/backup-0-clone.bundle --all`) before R1.

### 5.3 R1: reconciliation merge (needs approval)

`main` becomes a merge commit with parents `91a52c5` and `4dcaf5a` whose tree
is exactly the pinned `dev` tree. This keeps `0.1.0` in the release history
and drops `main`'s pre-migration files, as the migration intended
(`commit_generated_outputs_to_main: false`). The four design-file conflicts
are resolved to `dev`, which already contains `91fde94`'s layout (equal
`pcb_geometry` at `e43a6e7`).

```bash
git checkout main
git merge --no-ff --no-commit -s ours 4dcaf5a675c128cb4387ebd28751d9de8c3eb9d7
git read-tree -u --reset 4dcaf5a675c128cb4387ebd28751d9de8c3eb9d7
git commit -m "merge: reconcile migrated dev into main for 0.1.1"
test "$(git rev-parse HEAD^{tree})" = c73d3197d7373500964fec607107cbb7553f1dbb
git merge-base --is-ancestor 9d2a3c6287a5e2090b67797cbb52d33bf4abd94e HEAD
```

The real repository would need the same reconciliation before a real `0.1.1`.
The rehearsal records whether this strategy works; it does not decide it for
the real repository.

### 5.4 Preparation commits on `main` in the clone (each needs approval)

| # | Change | Check after |
| --- | --- | --- |
| P1 | `boardwright update plan`, then resolve the two conflicts: take the upstream `kibot_main.yaml` with the project's definitions restored from `project.yaml`; take the upstream AS1100 worksheet with the project logo re-embedded (`boardwright worksheet-logo`). Then `update apply`. One commit | `update status`: 0 blockers; the payload matches `ENGINE_SHA` |
| P2 | Owner's choice for TP1/TP2 `${VALUE}` on `F.SilkS` (hide it, or enable the plot option); replace PCB `${REVISION}` with `${PCB_REVISION}` | `validate`: 0 errors |
| P3 | `project.board_revision: B` | impact as in 2.5 |
| P4 | Owner-written `CHANGELOG.md` `[Unreleased]` entries | — |

Each edit in KiCad sources is the owner's or is reviewed as a diff. Root
`*.kicad_*` files are not bulk-formatted. After P4: `git status` clean,
`validate` exit 0 (warnings recorded), `release local plan 0.1.1` with no
problem. The source identity of the rehearsal is then `PREP_SHA` (the `main`
HEAD), which descends from `4dcaf5a` and `0.1.0`.

### 5.5 Release lifecycle (each step needs approval)

```bash
python -m boardwright release local candidate 0.1.1 --drawing-revision B \
  --revision-description "PCB REV B: TP2 MOVED, ROUTING AND FAB NOTES UPDATED" \
  --change-class fabrication,assembly,tooling,documentation \
  --justification "<section 4>" --kind draft --release-date <YYYY-MM-DD>
python -m boardwright release local review   0.1.1
# owner reviews the frozen candidate: drawings, revision tables, notes, manifest
python -m boardwright release local review   0.1.1 --check <RECORD>
python -m boardwright release local approve  0.1.1 --record <RECORD>
python -m boardwright release local finalize 0.1.1
python -m boardwright release local publish  0.1.1
python -m boardwright release local status   0.1.1
```

Negative control, before the real candidate and in a throwaway branch of the
clone: a candidate with `board_revision: A` must be refused before building.

## 6. Boundaries

| Boundary | How it is kept |
| --- | --- |
| Real repository | No push (no remote), no `gh release`, no workflow dispatch. `ls-remote` and the release list are recorded before and after and must be identical |
| Legacy history | `0.1.0` is neither moved, re-tagged, deleted nor given a ledger entry or manifest. No approval record is created for it |
| Engine | Host Python from `ENGINE_SHA`; the payload in the clone comes from P1 only |
| Baseline checks | `--skip-baseline` is never used. Any refusal stops the rehearsal |
| Held GitHub candidates and ci-test | Untouched |

## 7. Acceptance criteria

1. Before and after the run, the FIC-100A `ls-remote` equals 5.1 and its
   release list is `0.1.0` only.
2. The candidate log shows `previous 0.1.0, legacy tag`; `check-baseline`
   prints "No reused baselines"; the fabrication baseline is `B/1 (new)` and
   the assembly baseline `B-A (new)`.
3. The ledger has exactly one entry, `0.1.1`, with `previous_version: 0.1.0`,
   PCB `B`, schematic `B`, assembly and fabrication drawing `A`, the declared
   classes and the justification.
4. `document_revisions.yaml` keeps the migration row unchanged and adds
   exactly one schematic row `B`. The assembly and fabrication tables gain
   their first rows `A`.
5. The rendered drawings show PCB revision `B` on the fabrication drawing and
   Gerber title data. `${REVISION}` appears nowhere, and TP1/TP2 silkscreen
   follow the P2 decision.
6. Finalize: review gate match. The release commit is a fast-forward of
   `main` from `PREP_SHA` and changes only the release-commit files. The
   annotated tag `0.1.1` points to it. `0.1.0` is still `9d2a3c6`.
7. Publish: outputs rebuilt from the tag match the tagged accepted manifest
   (0 blocking). `publish.json` and the notes say local, not server-enforced,
   not authenticated, unsigned.
8. `status` is `published` with integrity intact; the store index is
   hash-chained and append-only.
9. The negative control (PCB revision `A`) is refused before building.
10. Manufacturing review by the owner: Gerbers, drill, ODB++, BOM, CPL and
    fabrication notes are fit for PCB revision B. Differences from the
    `0.1.0` assets are listed in the evidence for information; they are not
    a pass criterion.

## 8. Recovery

- Interrupted finalize or publish: `release local status`, then
  `release local recover` and a repeat of the step, as in ci-test check 6.
  Never reset or delete store entries.
- Any refusal: stop, record the output, do not work around it. The owner
  decides on a retry.
- Abandon: delete `$W` (clone, stages, store). Nothing outside it changed.
  The bundles in `$W/backup-*.bundle` are moved to the evidence directory
  first.
- Stale Docker containers: `docker ps -a --filter ancestor=<pinned image>`,
  removed only after their logs are saved.

## 9. Evidence retention

Kept in `~/Documents/boardwright-e2e/evidence/runs/rehearsal-fic-100a-0.1.1/`,
with `SHA256SUMS`:

- `ls-remote` and `gh release list` before and after; the `0.1.0` asset
  digests;
- `ENGINE_SHA`, `4dcaf5a…`, the R1 commit, each P commit with its diff, and
  `PREP_SHA`;
- refs after each step; bundles after R1, after P4 and after publish;
- every command's output (validate, update, plan, candidate, review, approve,
  finalize, publish, status), the review record, `approval.json`,
  `index.jsonl`, `publish.json`, `MANIFEST.txt`, `RELEASE_IDENTITY.json`;
- the release zip, kept in the store copy only (not uploaded anywhere).

Results are recorded in `AUDIT-2026-11.md`. The `TODO.md` item is closed only
after the owner accepts them.

## 10. Stop points

1. Owner review of this plan (now).
2. Approval of R1 and P1 to P4 and the section 4 inputs; G1 test change.
3. Clone, R1, P1 to P4 executed and reviewed; stop before the candidate.
4. Candidate built and reviewed by the owner; stop before approval.
5. Approve, finalize, publish; results reviewed and recorded.
