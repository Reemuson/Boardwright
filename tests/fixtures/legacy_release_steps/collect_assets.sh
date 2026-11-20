# SPDX-License-Identifier: LicenseRef-Boardwright-Source-Available
# Copyright (c) Ryan Hicks
#
# Frozen verbatim from release.yaml step "Collect release assets" at commit cb41fc6.
# Reference implementation for the equivalence tests of the extracted Python
# (docs/LOCAL_FIRST.md section 6). Do not edit.
rm -rf "$RELEASE_DIR"
mkdir -p "$RELEASE_DIR"

for path in Schematic Manufacturing assets HTML KiRI Testing Reports README.md NOTICE.md THIRD_PARTY_NOTICES.md LICENSE boardwright-${{ github.ref_name }}-source.zip; do
  if [[ -e "$path" ]]; then
    cp -a "$path" "$RELEASE_DIR/"
  fi
done

if [[ -f .boardwright/releases.yaml ]]; then
  python -c "import json,os,sys; sys.path.insert(0,'src'); from pathlib import Path; from boardwright.release_ledger import load_ledger; r=[x for x in load_ledger(Path('.')) if x.version==os.environ['GITHUB_REF_NAME']]; print(json.dumps(r[0].as_dict(), indent=2) if r else '{}')" > "$RELEASE_DIR/RELEASE_IDENTITY.json"
fi
if [[ -f .boardwright/accepted_manifest.json ]]; then
  cp .boardwright/accepted_manifest.json "$RELEASE_DIR/ACCEPTED_OUTPUT_MANIFEST.json"
fi
(cd "$RELEASE_DIR" && find . -type f ! -name MANIFEST.txt -print0 | sort -z | xargs -0 sha256sum) > "$RELEASE_DIR/MANIFEST.txt"
zip -r "boardwright-${{ github.ref_name }}-release.zip" "$RELEASE_DIR"
