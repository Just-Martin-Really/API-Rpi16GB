#!/usr/bin/env bash
set -euo pipefail

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

sync_repo() {
  local component=$1
  local url=$2
  local dest="docs/$component"
  local src="$WORK/$component"

  rm -rf "$dest"
  mkdir -p "$dest"
  git clone --depth 1 "$url" "$src"

  while IFS= read -r -d '' f; do
    rel="${f#./}"
    if [[ "$(basename "$rel")" == "README.md" ]]; then
      dir="$(dirname "$rel")"
      if [[ "$dir" == "." ]]; then
        rel="index.md"
      else
        rel="$dir/index.md"
      fi
    fi
    mkdir -p "$dest/$(dirname "$rel")"
    cp "$src/$f" "$dest/$rel"
  done < <(cd "$src" && find . -name '*.md' -not -path './.git/*' -print0)

  echo "synced $component: $(find "$dest" -name '*.md' | wc -l | tr -d ' ') files"
}

sync_repo pico   https://github.com/Just-Martin-Really/API-pico.git
sync_repo router https://github.com/Just-Martin-Really/API-Rpi2GB.git
