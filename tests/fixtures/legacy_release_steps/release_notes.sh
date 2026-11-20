# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks
#
# Frozen verbatim from release.yaml step "Generate GitHub release markdown" at commit cb41fc6.
# Reference implementation for the equivalence tests of the extracted Python
# (docs/LOCAL_FIRST.md section 6). Do not edit.
tag="${GITHUB_REF_NAME}"
repo="${GITHUB_REPOSITORY}"
release_url="https://github.com/${repo}/releases/download/${tag}"

top_image=""
bottom_image=""
if [[ -d assets/renders ]]; then
  top_image="$(find assets/renders -maxdepth 1 -type f \( -iname '*angled_top*.png' -o -iname '*top*.png' \) | sort | head -n 1 || true)"
  bottom_image="$(find assets/renders -maxdepth 1 -type f \( -iname '*angled_bottom*.png' -o -iname '*bottom*.png' \) | sort | head -n 1 || true)"
fi

{
  echo "Generated \`${RELEASE_VARIANT}\` package for \`${repo}\`."
  echo
  if [[ -n "$top_image" || -n "$bottom_image" ]]; then
    echo "## Board Images"
    echo
    echo '<p align="center">'
    if [[ -n "$top_image" ]]; then
      echo "  <img alt=\"Top board render\" src=\"${release_url}/$(basename "$top_image")\" width=\"45%\">"
    fi
    if [[ -n "$bottom_image" ]]; then
      if [[ -n "$top_image" ]]; then
        echo '&nbsp; &nbsp; &nbsp; &nbsp;'
      fi
      echo "  <img alt=\"Bottom board render\" src=\"${release_url}/$(basename "$bottom_image")\" width=\"45%\">"
    fi
    echo '</p>'
    echo
  fi
  python - <<'PY'
import sys
sys.path.insert(0, "src")
from pathlib import Path
from boardwright.release_ledger import load_ledger
import os
tag = os.environ["GITHUB_REF_NAME"]
record = next((r for r in load_ledger(Path(".")) if r.version == tag), None)
if record is None:
    print("## Identities\n\nNo release ledger entry (legacy release).\n")
else:
    print("## Identities\n")
    print("| Item | Value |\n|---|---|")
    print(f"| Project release | {record.version} |")
    print(f"| PCB revision | {record.pcb_revision} |")
    print(f"| Schematic revision | {record.schematic_revision} |")
    print(f"| Assembly drawing revision | {record.assembly_revision} |")
    print(f"| Fabrication drawing revision | {record.fabrication_revision} |")
    print(f"| Fabrication baseline | {record.fabrication_baseline.id} ({record.fabrication_baseline.status}) |")
    print(f"| Assembly baseline | {record.assembly_baseline.id} ({record.assembly_baseline.status}) |")
    print(f"| Change classes | {', '.join(record.change_classes) or 'initial'} |")
    print(f"| Source commit | {record.source_commit} |")
    print(f"\n{record.justification}\n")
PY
  echo "## Changes"
  echo
  awk -v tag="$tag" '
    $0 == "## [" tag "]" || $0 ~ "^## \\[" tag "\\] - " { capture=1; next }
    capture && /^## \[/ { exit }
    capture { print }
  ' CHANGELOG.md
  echo
  echo "## Assets"
  echo
  echo "- Full release package: \`boardwright-${tag}-release.zip\`"
  if [[ -f "boardwright-${tag}-source.zip" ]]; then
    echo "- Curated source package: \`boardwright-${tag}-source.zip\`"
  fi
  echo "- Generated README: included in the release package and generated from \`boardwright_resources/kibot/resources/templates/readme.txt\`"
} > RELEASE_NOTES.md

if [[ -n "$top_image" ]]; then
  cp "$top_image" "$(basename "$top_image")"
fi
if [[ -n "$bottom_image" ]]; then
  cp "$bottom_image" "$(basename "$bottom_image")"
fi
