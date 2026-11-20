<!-- SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available -->
<!-- Copyright (c) Ryan Hicks -->

# Local-First Previews and Release Backends (design)

Status: **design for review.** Only backend selection (`backends.py`,
section 3) is implemented on `feat/local-first-backends`. Everything else here
is planned and must not be described as available until it is built and
tested.

## 1. Decisions (owner, 2026-11-20)

| Topic | Decision |
| --- | --- |
| Preview default | `DRAFT` and `CHECKED` previews build locally in Docker, even when GitHub is available. |
| Uncommitted changes | Local previews may include them through isolated staging (`container stage --worktree`). The developer's working tree is never modified, and such a build is never release evidence. |
| Release default | A usable GitHub remote means GitHub. No remote means a fully local release. |
| Overrides | Explicit backend overrides for previews and releases. |
| Failure | Never switch backend silently after a Docker, authentication, network or workflow failure. |
| Unsupported remotes | Refused explicitly (for example GitLab, a self-hosted server or a filesystem path); never assumed to be GitHub. |
| Candidates | A local candidate never substitutes for a GitHub held candidate and never satisfies `approved_review_run`. |
| Local releases | Fully supported: candidate, review, approval, immutable metadata, baseline verification, checksummed package, ledger. Local approval provenance is labelled as local and claims no server enforcement. |
| One engine | Both backends run the same Boardwright build and release-model code. |

## 2. Architecture

```text
            CLI / TUI  (policy-free front ends)
                    |
        backends.select_backend()      <- pure policy, section 3
                    |
     +--------------+---------------+
     |                              |
 LocalBackend                  GitHubBackend
 (container.stage/build,       (workflow dispatch, run/artifact
  local review + finalize)      polling: existing actions.py, preview.py)
     |                              |
     +-------- shared engine -------+
       release.py / release_date / release_preparation / review_gate
       output_manifest / impact / release_ledger / drawing_revisions
       build.py (inside the pinned image)
       release_package.py   <- extracted from release.yaml (section 6)
       release_commit.py    <- extracted from prepare-release.yaml
```

The workflows keep running the same Python. What changes is that the bash
now inside `release.yaml` and `prepare-release.yaml` (packaging, release
notes, the release-commit file list and message) moves into
`release_package.py` and `release_commit.py`, and the workflows call those
modules. That extraction is a prerequisite of the local backend and is
verified against the GitHub path first (section 9).

### Interfaces

```python
# backends.py (implemented)
class Operation(Enum): PREVIEW, RELEASE
class Backend(Enum): LOCAL, GITHUB

@dataclass(frozen=True)
class RemoteInfo:
    kind: str            # "none" | "github" | "unsupported"
    url: str             # origin URL, credentials refused
    repository: str      # "owner/name" for GitHub
    reachable: bool | None
    gh_authenticated: bool | None
    workflows_present: bool
    problems: tuple[str, ...]

@dataclass(frozen=True)
class DockerInfo:
    available: bool
    detail: str

@dataclass(frozen=True)
class Selection:
    operation: Operation
    backend: Backend | None    # None: refused, see problems
    source: str                # "override" | "project" | "default"
    reason: str
    problems: tuple[str, ...]  # blocking; nothing runs

def select_backend(operation, *, variant, remote, docker,
                   project_setting="auto", override="") -> Selection
```

```python
# execution.py (planned): both backends implement this.
class ExecutionBackend(Protocol):
    kind: Backend
    def preview(self, request: PreviewRequest) -> PreviewResult: ...
    def prepare_candidate(self, request: ReleaseRequest) -> CandidateRef: ...
    def approve(self, candidate: CandidateRef, review: ReviewRecord) -> ApprovalRef: ...
    def finalize(self, approval: ApprovalRef) -> ReleaseRef: ...
    def status(self) -> BackendStatus: ...
```

`ReleaseRequest` holds the same inputs as `prepare-release.yaml` (version,
variant, kind, drawing revision, description, change classes, justification,
source SHA, optional release date). For GitHub, `prepare_candidate`
dispatches a held run, `approve` dispatches the approved run with
`approved_review_run`, and `finalize` waits for `release.yaml`; the gates
stay in the workflows. For local, each step runs in a stage (section 5).

## 3. Backend selection (implemented)

Selection happens once, before anything runs, and is printed with its reason.
If the selected backend then fails, the command fails and names the override
that would run the other backend. It never retries on the other backend by
itself.

| Operation | Override / project setting | Remote | Docker | Result |
| --- | --- | --- | --- | --- |
| Preview, `DRAFT`/`CHECKED` | auto | any | available | **local** |
| Preview, `DRAFT`/`CHECKED` | auto | any | unavailable | refused: "Docker unavailable; use `--backend github` to build on GitHub" |
| Preview, `PRELIMINARY`/`RELEASED` | auto | usable GitHub | any | github |
| Preview, `PRELIMINARY`/`RELEASED` | auto | none or unsupported | available | local |
| Release | auto | usable GitHub | any | **github** |
| Release | auto | none | available | **local** |
| Release | auto | none | unavailable | refused (Docker required) |
| Release | auto | unsupported | any | refused: unsupported remote; `--backend local` to release locally |
| Release | auto | GitHub, unusable (no `gh`, not authenticated, unreachable, workflows missing) | any | refused, with the specific problem; nothing falls back |
| any | `local` | any | available | local (for releases on a GitHub project, a warning that the release is local-only and its tag is not pushed) |
| any | `local` | any | unavailable | refused |
| any | `github` | usable GitHub | any | github |
| any | `github` | anything else | any | refused |

"Usable GitHub" means: an `origin` URL on `github.com` (https or ssh), `gh`
installed and authenticated, the repository readable, and the Boardwright
workflows present in the tree. The project setting lives in `project.yaml`:

```yaml
backends:
  preview: auto     # auto | local | github
  release: auto
```

A command-line `--backend` beats the project setting, which beats the
default.

## 4. Local previews

Previews run `container build` in a stage. Without uncommitted changes the
stage is a clean clone of `HEAD`. With them (default when the tree is dirty,
or `--worktree`), `stage(worktree=True)` copies the changed and untracked,
non-ignored files into the stage and commits them there. The developer's tree
is compared before and after the build, as now; any change fails the command.

The build record already marks such a stage (`stage.worktree: true`), and
`container compare` and the review gate already reject it. The design adds:

- `record.json` `purpose: "preview"` and `backend: "local"`; the release
  steps refuse any evidence whose purpose is not `release-candidate`;
- a preview index (`~/.boardwright/previews/<project>/index.json`) of stage
  path, source commit, worktree flag, variant and time, for the TUI;
- the reviewed-preview marker gains the backend and the worktree flag. A
  marker for a worktree preview cannot be used to accept to `main`.

## 5. Local releases

The local release is the GitHub release flow with the server replaced by the
developer's own repository and a local release store. It runs the same
modules in the same order.

### 5.1 Steps

1. **Plan** (host): `release` plan checks, with the remote-tag check replaced
   by a local tag check when there is no remote, and the ledger history check.
2. **Candidate** (`boardwright release local candidate`): `container candidate`
   in a stage of the clean, committed `HEAD` (never `--worktree`), with the
   explicit or UTC release date. `check-baseline` uses the previous release's
   committed manifest at its local tag. A previous release without a manifest
   cannot be checked locally (the legacy path needs the GitHub package); the
   candidate fails closed. The candidate is frozen into the release store
   (5.3) as `candidates/<version>/<candidate-id>/`, where `candidate-id` is the
   SHA-256 of its `review_candidate.json`.
3. **Review**: the person inspects the frozen candidate (drawings, metadata,
   manifest) and writes a review record from a template, as with
   `REVIEW-<version>.md`. `boardwright release local review` checks every box
   is ticked and that `APPROVED: yes` is the last line.
4. **Approve** (`boardwright release local approve`): binds the record to the
   candidate and writes `approval.json`:

   ```json
   {
     "schema": 1,
     "authority": "local",
     "server_enforced": false,
     "version": "1.2.0",
     "candidate_id": "<sha256 of review_candidate.json>",
     "source_sha": "<40 hex>",
     "manifest_sha256": "<as reviewed>",
     "release_date": "2026-11-20",
     "review_record_sha256": "<sha256>",
     "approver": "Name <email>",
     "approved_at": "<UTC timestamp>",
     "host": "<hostname>"
   }
   ```

   `approver` comes from the Git identity and is *asserted, not
   authenticated*. If the user has a signing key configured, the approval
   commit and tag are signed (section 5.2); otherwise the documentation and
   release notes state that the approval is unsigned.
5. **Finalize** (`boardwright release local finalize`): the approved run of the
   local backend. It refuses unless:
   - the repository is clean;
   - `HEAD` is still the candidate's source commit;
   - the version's tag is free;
   - the approval binds this candidate.

   Then it rebuilds in a fresh stage with the reviewed release date and runs
   `review_gate verify` against the frozen candidate, as the GitHub approved
   run does. It creates the release commit in the stage
   (`release_commit.py`: same file list, same message and trailers, with
   `Reviewed-Candidate: <candidate-id>` and `Approval-Authority: local`
   instead of `Reviewed-Run`). It then fetches that commit into the
   developer's repository and fast-forwards the release branch; there is no
   merge and no rebase. Finally it creates the annotated tag (signed when
   possible).
6. **Package** (`boardwright release local publish`): builds again from the
   tag in a fresh stage, as `release.yaml` does, runs
   `output_manifest verify` against the tagged manifest, and calls
   `release_package.py` to produce the zip, `MANIFEST.txt`,
   `RELEASE_IDENTITY.json` and the notes. The result is written to the
   release store.

A local release on a project that has a GitHub remote needs an explicit
`--backend local`. Its tag is not pushed. Pushing it later is a separate,
explicit action, and the GitHub draft release would then be built by
`release.yaml` from that tag.

### 5.2 What is persisted where

| Data | Location | Immutability |
| --- | --- | --- |
| Ledger (`releases.yaml`), accepted manifest, review candidate, release date, revision tables | The release commit in the project repository, exactly as on GitHub | Git history plus the annotated tag; `verify_ledger_history` checks append-only against every tag |
| Approval provenance | `.boardwright/approval.json` in the release commit (new, local releases only); the commit and tag trailers | Committed and tagged; signed when a key is configured |
| Manufacturing baseline | The ledger entry (fingerprints, `A/2 reused`) and `accepted_manifest.json` at the tag; `check-baseline` for the next release reads them from the local tag, exactly as now | As above |
| Frozen candidate, review record, build records | Release store `candidates/<version>/<candidate-id>/` | Read-only after freezing; `SHA256SUMS` per directory |
| Release package | Release store `releases/<version>/`: zip, `MANIFEST.txt`, `RELEASE_IDENTITY.json`, notes, `SHA256SUMS` | Written once; refused if present (like "refuse to overwrite a published release") |
| Store index | Release store `index.jsonl`: one line per event (candidate, approval, finalize, publish) with digests | Append-only; each line includes the SHA-256 of the previous line, so an edit breaks the chain |

The release store defaults to `~/.boardwright/releases/<project-id>/` and
can be set in `project.yaml` (`release.local_store`). It is outside the
working tree and never committed. Losing it loses the frozen candidate and
the package, not the release identity: the commit and tag hold the ledger,
the manifest and the approval, and the package can be rebuilt from the tag
and verified against the manifest.

### 5.3 Local versus GitHub approval

| Control | GitHub release | Local release |
| --- | --- | --- |
| Candidate binding (source, inputs, date, metadata, manifest) | `review_gate verify` in the approved run | The same `review_gate verify` in `finalize` |
| Who approved | The workflow dispatcher (GitHub account) plus the local signed review record (P1: not enforced by the server) | Git identity (asserted); optional commit/tag signature |
| Tamper resistance | Server-side runs, artifacts and tags | The local repository and release store only; the hash-chained index makes edits detectable but cannot prevent them |
| Labelled as | `Reviewed-Run:` trailer | `Approval-Authority: local`, `server_enforced: false` in `approval.json` and in the release notes |

## 6. Shared engine extraction (before the local backend)

| New module | Replaces | Used by |
| --- | --- | --- |
| `release_package.py` | `release.yaml` "Collect release assets" (copy set, `RELEASE_IDENTITY.json`, `ACCEPTED_OUTPUT_MANIFEST.json`, `MANIFEST.txt`, zip) and "Generate GitHub release markdown" | `release.yaml`, local publish |
| `release_commit.py` | `prepare-release.yaml` "Commit accepted release state" file list, message and trailers | `prepare-release.yaml`, local finalize |

The extraction must produce a byte-identical `MANIFEST.txt` and identical zip
member lists for an existing release. It is tested against the published
0.1.7 package before any workflow changes are installed on the testbench.

## 7. CLI and TUI

- `boardwright preview [--variant V] [--backend auto|local|github] [--worktree|--committed]`.
- `boardwright release <version> … [--backend …]`, with the local subcommands
  `candidate`, `review`, `approve`, `finalize`, `publish` and `status`.
- `boardwright backends`: prints the selection for each operation and why
  (`select_backend` with no side effects).
- TUI: the Preview action shows the selected backend and offers the override.
  The release screen shows "Local release (not server-enforced)" or "GitHub
  release". A failure shows the override needed; it never switches by itself.

## 8. Test plan

| Area | Tests |
| --- | --- |
| Selection (done) | Every row of the section 3 table; credentials in the URL refused; ssh/https GitHub URL forms; non-GitHub hosts and filesystem paths unsupported; project setting vs override precedence; invalid settings |
| Remote inspection | No remote; GitHub with and without `gh`/authentication; unreachable; workflows missing |
| Local preview | Committed tree; uncommitted and untracked changes included; ignored files excluded; developer tree unchanged (status fingerprint and file hashes); worktree evidence rejected by `compare` and the review gate |
| Engine extraction | Byte-identical `MANIFEST.txt` and zip members for 0.1.7; workflow scripts call the modules |
| Local release | Candidate on committed state only; review record checks; approval binding (changed source, date, inputs, metadata, manifest, review record all refused); finalize refuses a moved `HEAD`, dirty tree or existing tag; ledger append-only across two local releases; baseline reuse (`A/2 reused`) and a fabrication change that must re-issue; legacy previous release fails closed; release store refuses overwrite; index hash chain detects edits |
| Projects | GitHub remote; no remote; inaccessible remote; unsupported remote; uncommitted changes; explicit overrides each way |
| GitHub path unchanged | Existing workflow tests; a testbench held candidate after the extraction, compared with a local candidate (only after review) |

## 9. Delivery order

1. Backend selection and tests (done on this branch).
2. Shared engine extraction (section 6), with the 0.1.7 package comparison.
3. Local previews in the CLI and TUI.
4. Local release backend (section 5).
5. Project matrix tests (section 8).
6. Benchmark: the same commit, `CHECKED`, built locally (Docker Desktop) and
   on GitHub (dispatch to completion, including queueing and artifact
   download). Five runs each, cold and warm 3D cache, reported as median and
   range.

## 10. Questions for the owner

1. A local release on a GitHub project (explicit `--backend local`): should
   its tag ever be pushed automatically, or always by hand? The design above
   says by hand.
2. Release store default location: `~/.boardwright/releases/<project-id>/`
   (proposed), or inside the project directory but ignored?
3. Local publish rebuilds from the tag, a third build as on GitHub (about
   4 minutes). Keep this for parity (proposed), or package the approved
   build's outputs after `output_manifest verify`?
4. Signing: require a signing key for local approvals, or record unsigned
   approvals as such (proposed)?
