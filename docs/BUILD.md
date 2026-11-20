<!-- SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available -->
<!-- Copyright (c) Ryan Hicks -->

# Build Environment

Boardwright has one output build. It runs in Docker Desktop on a Windows
workstation and in GitHub Actions. Both use the same pinned container image and
the same repository commands. This document is the source of truth for the
build environment. The release identity rules live in
[`RELEASE_MODEL.md`](RELEASE_MODEL.md).

## Pieces

| Piece | Role |
| --- | --- |
| `src/boardwright/build.py` (`boardwright build`) | The pipeline, run inside the container: preflight, KiBot stages, checks and evidence. It is the only definition of the build steps. |
| `src/boardwright/container.py` (`boardwright container`) | Runs commands in the pinned image. It stages clean local checkouts and compares builds. It is the only place the image is named. |
| `.github/workflows/*.yaml` | Call `python -m boardwright.container build` or `candidate` with `--workdir .`. The approval, commit, tag and publish steps stay in the workflows. |

## Image

| KiCad | Image | Use |
| --- | --- | --- |
| 9 (default) | `ghcr.io/inti-cmnb/kicad9_auto_full:1.9.1-2_k9.0.7_d13.2_b4.2.4LTS@sha256:4d47f3b8…` | Production builds. |
| 10 | `ghcr.io/inti-cmnb/kicad10_auto_full:1.9.1-2_k10.0.5_d13.2_b4.2.4LTS@sha256:96e75f88…` | Explicit `--kicad 10` only. It is validated separately (`kicad-compat.yaml`). |

Docker pulls the image by digest, so a moved tag cannot change a build. Print
the full reference with `python -m boardwright container image [--kicad 10]`.
To check the image and its tool versions, run
`python -m boardwright container image --check`.

The KiBot GitHub action (`INTI-CMNB/KiBot@v2_dk9`) is no longer used. It
built `FROM kicad9_auto_full:dev`, a second moving tag. Its entrypoint also ran
`kicad-git-filters.py`, which rewrote `.gitattributes` and `.git/config` during
every build (audit F16). Boardwright now calls `kibot` directly in the image.

The image has no `pypdf`, which the combined review PDF needs. The build
installs `pypdf==6.21.4`, checked against its SHA-256, into the
container-private home (`build.CONTAINER_REQUIREMENTS`).

### Changing the image

1. Choose a stable KiBot release tag that is at least two weeks old. Do not use
   `dev` or `dev_*`. Resolve its digest.
2. Update `IMAGES` in `container.py` with the tag and digest together. Then run
   `python -m boardwright container image --check`.
3. Build a released commit and verify its controlled outputs against that
   release's accepted manifest. See "Comparing builds" below.
4. Treat the change as `tooling` in the next release.

## Local commands (PowerShell)

Run these from the project folder. They need Docker Desktop and Python 3.11+
with Boardwright's requirements installed.

```powershell
# Validation (host, no Docker)
python -m boardwright validate
python -m unittest discover -s tests -v

# Image check
python -m boardwright container image --check

# Documentation (schematic PDF, netlist, BOM, README) from the last commit
python -m boardwright container build --variant DRAFT

# Manufacturing outputs with ERC/DRC
python -m boardwright container build --variant CHECKED

# Same, including uncommitted changes (marked as a working-tree build)
python -m boardwright container build --variant CHECKED --worktree

# KiCad 10 variant
python -m boardwright container build --variant CHECKED --kicad 10

# Release-candidate rehearsal; nothing is committed, pushed or tagged
python -m boardwright container candidate 1.2.0 --variant RELEASED --kind draft `
  --drawing-revision B --revision-description "Correct C1 note" `
  --change-class documentation --justification "DFM review"

# Compare a local build with a GitHub build (see below)
python -m boardwright container compare $HOME\.boardwright\stages\<stage> .\gh-evidence

# Interactive shell in the image (replaces the old docker-kibot scripts)
python -m boardwright container shell
```

Each build prints its stage folder. The outputs are there (`Manufacturing/`,
`Schematic/`, …), and the evidence is in `boardwright-build/`
(`record.json`, `manifest.json`). The default stage root is
`~\.boardwright\stages`; set `BOARDWRIGHT_STAGE_ROOT` to change it. The 3D
model cache is the Docker volume `boardwright-3d-k9` (or `-k10`).

## How a local build stays equivalent to CI

| Concern | Rule |
| --- | --- |
| Source | `container build` clones the current commit into a new stage. The working tree is never mounted. `--worktree` adds uncommitted, non-ignored changes as a commit inside the stage. The record marks such a build, and `compare` rejects it as evidence. |
| Line endings | The stage is cloned with `core.autocrlf=false` and `core.eol=lf`, so its files have the same bytes as a Linux checkout. Files with `eol=crlf` (`*.bat`) stay CRLF, as on GitHub. The build refuses any tree where tracked text checked in as LF has a CRLF working copy. |
| Local overrides | `.boardwright/local.yaml` is ignored, so it is never cloned. The build refuses to run if one is present. Tracked writers already ignore it (`load_config(local=False)`). |
| Tracked sources | The build restores every tracked file it rewrote (`ci_cleanup`, `.gitattributes` first). It fails if anything outside the kept outputs (`README.md`, `assets/renders`, plus the caller's `--keep`) is still modified. Locally, `container build` also checks that the developer's `git status` is unchanged. |
| Ownership and permissions | On Linux the container runs as the calling uid:gid, so CI outputs belong to the runner and the workflows no longer use `sudo chown`. After every run the runner fails if any file in the work tree belongs to another user; the record states the container's uid:gid. Docker Desktop maps ownership itself. `BOARDWRIGHT_CONTAINER_USER=1001:1001` reproduces the GitHub runner's uid:gid locally (recorded by the live runs). Scripts run through `python`, so execute bits do not matter. |
| Home, fonts, Git | `HOME` is container-private, and the bundled fonts are installed there before KiBot. Git trusts the mounted tree through `safe.directory`. |
| Mounts | One read-write mount (the stage or CI checkout at `/work`) and the 3D cache at `/cache_3d`. Nothing else from the host is mounted. |
| Order | The worksheet and logo check (`drawing_sheets check`) is the first step, before any KiBot run. |

## Pipeline

`python -m boardwright build --variant V --plan` prints the steps:

1. Check drawing sheets and branding.
2. KiBot `table_sources` (all variants); revision table variables, PDF table
   placeholders, empty-page pruning, combined-PDF preparation (not `DRAFT`).
3. KiBot output set: `draft_group md_readme` (`DRAFT`); `all_group_k9` or
   `all_group_k10` (ERC/DRC skipped for `PRELIMINARY`).
4. Fabrication and assembly drawing regeneration with the generated document
   configs (not `DRAFT`).
5. Combined review PDF, silkscreen verification, generated-output cleanup.
6. README check, side-effect check, then `boardwright-build/manifest.json` and
   `boardwright-build/record.json`.

## Release workflows

| Workflow | Container command | Stays in the workflow |
| --- | --- | --- |
| `dev-preview.yaml` | `container build --workdir .` | Packaging, preview branch. |
| `main-outputs.yaml` | `container build --workdir .` | Reviewed-SHA check, side-effect check, README snapshot commit. |
| `prepare-release.yaml` | `container candidate --workdir .`: `release --prepare --release-env` (an approved run passes the reviewed `--release-date`), build, accepted manifest, `check-baseline` (on the host, with `GH_TOKEN`), review-candidate record | Tag-availability check, review hold and upload, approved-run checks, `review_gate verify`, commit, tag, dispatch. |
| `release.yaml` | `container build --workdir .` | Refuse-overwrite check, `output_manifest verify`, packaging, publication. |
| `kicad-compat.yaml` | `container build --workdir . --kicad 9` and `--kicad 10` | Manifest comparison. |

The container never receives a GitHub token:

- Every checkout uses `persist-credentials: false`. Otherwise
  `actions/checkout` leaves the job token in `.git/config`, inside the mounted
  work tree. Steps that fetch, query or push with git authenticate themselves
  through `GIT_CONFIG_*` variables for that step only, and mask the value.
- The runner refuses to start a container when the mounted repository's Git
  config holds an `extraheader`, a `credential.*` setting or a URL with
  credentials, or when the variables passed in have secret-like names.
- Only `GITHUB_ACTIONS`, `GITHUB_REPOSITORY`, `GITHUB_RUN_ID`, `GITHUB_SHA` and
  `GITHUB_REF_NAME` are forwarded from the runner.
- `check-baseline` needs `gh` and runs on the host. Before a previous release's
  package is used as a baseline, it is authenticated: GitHub release author
  and asset uploader, the server-side SHA-256 digest, the remote tag equal to
  the local tag, and ancestry (`legacy_baseline.py`). A release with an
  accepted manifest is compared against that manifest at its tag.

Inputs reach the container as explicit arguments from `env`, never through
`${{ }}` in scripts. Approval, tagging and publication run only in the
workflows.

`release.yaml` now also runs the silkscreen check. A tag prepared before this
change is still published by the `release.yaml` at that tag. Its outputs are
verified against its accepted manifest as before, so a build-environment
difference fails before publication.

## Comparing builds

Every build writes `boardwright-build/manifest.json` (`output_manifest`
format) and `boardwright-build/record.json`:

| Field | Content |
| --- | --- |
| `source_commit`, `source_modified`, `stage` | The commit built, tracked changes present before the build, and the local stage marker. |
| `release_preparation` | For a prepared release: version, release date, commit and the SHA-256 of every file `release --prepare` wrote, as recorded by that command in `.git/` and verified by the build. A prepared file changed afterwards, or any other modification, stops the build. |
| `boardwright_source` | `src/boardwright` and its Git tree ID. The build fails if Python imported Boardwright from anywhere other than the checkout being built. |
| `image`, `image_id` | The pinned reference, and the local ID of the image that ran. Before every run the runner checks that Docker lists the pinned digest for that image. The ID's form depends on the Docker image store (Docker Desktop reports the manifest digest), so builds are compared on `image`. |
| `tools` | `kicad-cli`, KiBot and Python versions from inside the container. |
| `configuration` | Variant, KiCad major version, the Git tree ID of `boardwright_resources/kibot`, and the pinned Python requirements. |
| `steps` | Every step's name and full command. |
| `normaliser`, `github_run`, `container_user` | Manifest normaliser version; repository and run ID on GitHub; the uid:gid the build ran as. |

The workflows upload `boardwright-build/` with the KiBot logs.

```powershell
gh run download <run-id> -n boardwright-main-output-logs -D gh-evidence
python -m boardwright container compare <local stage> gh-evidence
```

`compare` fails on any of:

- a different source commit, variant, image or normaliser;
- a working-tree (`--worktree`) build;
- a modified-source build, unless every modification belongs to a verified
  release preparation and both builds carry the same one: same commit,
  version, release date, file contents and project files. Root `*.kicad_pro`
  files count only when they differ from the commit in nothing but
  `text_variables`, which preparation syncs; any other change is an error;
- a missing image identity;
- a blocking difference in a controlled output (`missing`, `unexpected`,
  `changed`, `incomparable`).

Files that differ only in normalised fields are reported as notes. A
difference in informational files (PDFs, renders, reports, ODB++) is counted
but not compared. Normalisation follows `output_manifest` exactly. It removes
only Gerber and Excellon creation-date and generator lines, the KiCad banner,
the `TF.ProjectId` revision field, IPC-D-356 header comments, and Gerber job
creation date and generator. Nothing else is normalised.

To check a release built elsewhere, verify the outputs against the accepted
manifest committed at its tag:

```powershell
python -m boardwright.output_manifest verify --root <stage>
```

## Known differences and limits

- Historical CI used the moving `dev` image: KiCad 9.0.7+1 with a KiBot
  1.9.2 development build. The pinned image has the same KiCad (9.0.7+1;
  `kicad-cli --version` prints `9.0.7`) and the stable KiBot 1.9.1. A rebuild
  of 0.1.5 verified against its accepted manifest. KiCad 9.0.6 is only the
  desktop install used for local checks; it never builds controlled outputs.
- KiCad 10 builds are not reproducible across hosts yet. On the same commit
  and image, a Docker Desktop build and a GitHub build filled one copper zone
  differently (L1 and L2), and the manifest normaliser does not recognise
  the KiCad 10 drill-file header (`; DRILL file KiCad 10…`, without braces),
  so drill files differ in their dates only. Both must be resolved before any
  project moves to KiCad 10 (`MIGRATION.md`).
- `DRAFT` also generates the table CSVs (`table_sources`). The README report
  resolves every output's path, which configures `pdf_fabrication`, and that
  output refuses to load without its drill-table and test-point CSVs. The
  fabrication and assembly PDFs the README depends on are therefore drawn in a
  `DRAFT` build too (informational, without the revision-table preparation).
- A board without holes cannot build: KiBot writes no drill-table CSV, but the
  board's drill-table group requires one (the template's placeholder PCB).
- The fonts are now installed where KiCad in the container sees them. Before,
  they went to the runner's home, outside the action's container. Drawing PDFs
  (informational) may therefore render differently from earlier CI builds.
- A local `candidate` takes its release references from the upstream of the
  repository it runs in, on the host. It lists the `origin` tags, fetches them
  into the stage without forcing, and points the stage's `origin` at that URL.
  The container reads the snapshot (`.git/boardwright-remote-refs.json`, named
  by `BOARDWRIGHT_REMOTE_REFS`) for the remote-tag check, so a Windows-path or
  credentialed remote is never contacted from inside it. The rehearsal stops if
  the upstream cannot be listed, if a local tag differs from the upstream's, or
  if the `origin` URL embeds credentials. The CI run checks GitHub directly.
