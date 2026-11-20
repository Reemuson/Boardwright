# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks
#
# Frozen verbatim from prepare-release.yaml step "Commit accepted release state" at commit cb41fc6.
# Reference implementation for the equivalence tests of the extracted Python
# (docs/LOCAL_FIRST.md section 6). Do not edit.
set -euo pipefail
# Checkouts do not persist credentials, so the build container never sees
# the job token. Authenticate only this step's git commands.
auth="$(printf 'x-access-token:%s' "$GH_TOKEN" | base64 -w0)"
echo "::add-mask::$auth"
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0="http.https://github.com/.extraheader" GIT_CONFIG_VALUE_0="AUTHORIZATION: basic $auth"
version="$IN_VERSION"
variant="$IN_VARIANT"

git rm -r --ignore-unmatch HTML KiRI Manufacturing Reports Schematic Testing
git add CHANGELOG.md .boardwright/project.yaml .boardwright/document_revisions.yaml
git add -f .boardwright/revision_history_variables.env .boardwright/release.env
git add -f .boardwright/releases.yaml .boardwright/accepted_manifest.json .boardwright/review_candidate.json
git add README.md assets/renders/*.png || true

if git diff --cached --quiet; then
  echo "No release changes to commit." >&2
  exit 1
fi

# Trailers (docs/COMMITS.md): the reviewed source and, for an approved
# release, the held run whose candidate it matched.
trailers="Source-Commit: ${BOARDWRIGHT_SOURCE_SHA}"
if [[ -n "$IN_APPROVED_REVIEW_RUN" ]]; then
  trailers+=$'\n'"Reviewed-Run: ${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${IN_APPROVED_REVIEW_RUN}"
fi
git commit -m "release: prepare ${version} (${variant})" -m "$trailers"
# Non-forced push: fails if main moved after checkout.
git push origin HEAD:main
