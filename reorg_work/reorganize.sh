#!/bin/bash
# Usage: reorganize.sh <extracted-dir>
# Moves each .txt to {category}/{path-with-dashes}-{filename}, then prints the hash.
set -euo pipefail
cd "$1"

# Collect the file list first so newly moved files aren't re-processed
mapfile -d '' files < <(find . -type f -name "*.txt" -print0)

for file in "${files[@]}"; do
  category=$(grep -m 1 "^category:" "$file" | cut -d' ' -f2- | tr -d '\r')
  relpath="${file#./}"
  newname=$(printf '%s' "$relpath" | tr '/' '-')
  mkdir -p "$category"
  mv -n "$file" "$category/$newname"
done

find . -type d -empty -delete
find . -type f | LC_ALL=C sort | sha256sum
