<!-- SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available -->
<!-- Copyright (c) Ryan Hicks -->

# Commit Messages

Boardwright and the projects it manages use one commit-message convention.
It applies to new commits only. Existing history is never rewritten to match
it: published commits, tags and releases stay as they are, and audit records
cite commit hashes.

## Subject

```text
type(scope): imperative summary
```

- `type` is one of the types below.
- `scope` names the area changed. It is optional: omit it when a change spans
  several areas or no scope adds information. Never repeat the type
  (`docs(docs)`).
- The summary is an imperative phrase (`add`, `fix`, `keep`), in lower case
  except for names and acronyms (`PCB`, `KiCad`), with no full stop.
- Keep the subject to 72 characters or fewer where you can.

| Type | Use for |
| --- | --- |
| `feat` | New behaviour or a new design capability. |
| `fix` | Correcting wrong behaviour or a design error. |
| `docs` | Documentation, notes and changelog text only. |
| `test` | Tests only. |
| `refactor` | Restructuring without behaviour change. |
| `ci` | Workflows and CI configuration. |
| `build` | Build environment, container image, dependencies. |
| `chore` | Maintenance, generated snapshots, payload updates. |
| `revert` | Reverting an earlier commit. |
| `release` | Release commits made by the release workflow only. |

Preferred scopes, as examples rather than a closed list (add a new one
when it names the area better):

- Boardwright: `release`, `ci`, `container`, `build`, `worksheets`,
  `fab-notes`, `gerber`, `impact`, `review-gate`, `cli`, `tui`, `audit`.
- Projects: `pcb`, `schematic`, `drawings`, `project`, `outputs`, `payload`,
  `changelog`.

## Body and trailers

Separate the body from the subject with a blank line and wrap it at 72
characters. Say why the change is needed and what it changes, not how the
diff reads.

End with optional trailers, one per line, in a final paragraph:

| Trailer | Meaning |
| --- | --- |
| `Source-Commit:` | The full SHA a generated commit was made from. |
| `Reviewed-Run:` | URL of the held prepare run an approved release matched. |
| `Refs:` | Audit finding, issue or TODO item (`Refs: F17`). |
| `Provenance:` | Origin of copied or adapted material (`PROVENANCE.md`). |

Commits carry no AI-tool attribution or co-author lines for tools.

## Generated commits

| Workflow | Message |
| --- | --- |
| `prepare-release.yaml` | `release: prepare <version> (<variant>)`, trailer `Source-Commit:`, plus `Reviewed-Run:` when approved from a held run. |
| `main-outputs.yaml` merge | `chore(outputs): accept <sha12> from <source ref>`, trailers `Source-Commit:` and `Variant:`. |
| `main-outputs.yaml` snapshot | `chore(outputs): update README snapshot`, trailer `Source-Commit:`. |
| Release tag | Annotated, message `Release <version>`. |

`boardwright suggest-commit` and the TUI commit screen suggest a subject from
the changed files and print advisory notes for subjects that do not follow
this page. The notes never block a commit.

## Author identity

Commit under one identity: your GitHub noreply address
(`<id>+<user>@users.noreply.github.com`), set once in the global Git
configuration. Workflow commits use `boardwright[bot]`.
