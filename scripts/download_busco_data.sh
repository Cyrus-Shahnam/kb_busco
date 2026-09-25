#!/bin/bash
# Download BUSCO lineage datasets listed in data/refdata_lineages.txt into
# <DATA_DIR>/busco_downloads. Called by `entrypoint.sh init`.
# Layout produced: busco_downloads/lineages/<name>/dataset.cfg ...
set -euo pipefail

DATA_DIR="${1:-/data}"
DL="${DATA_DIR}/busco_downloads"
LIST="${LINEAGE_LIST:-/kb/module/data/refdata_lineages.txt}"
BIN=/opt/conda/envs/busco/bin
export PATH="${BIN}:${PATH}"

mkdir -p "$DL"
cd "$(mktemp -d)"          # BUSCO may drop log files in cwd

"$BIN/busco" --version
while IFS= read -r name || [ -n "$name" ]; do
  name="$(echo "$name" | sed 's/#.*//' | tr -d '[:space:]')"
  [ -z "$name" ] && continue
  echo "=== downloading ${name}"
  "$BIN/busco" --download_path "$DL" --download "$name"
  case "$name" in
    all|prokaryota|eukaryota|virus) ;;                 # bulk keywords
    *) test -f "$DL/lineages/$name/dataset.cfg" \
         || { echo "dataset.cfg missing for $name"; exit 1; } ;;
  esac
done < "$LIST"

echo "=== reference data contents"
ls "$DL" "$DL/lineages"
du -sh "$DL"
