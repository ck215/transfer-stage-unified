#!/usr/bin/env bash
# Intersect the file sets each worktree touched. Any output is a collision
# that must be resolved before merging -- silence is the pass condition.
#
#   bash partition-check.sh <base-sha> <worktree> [<worktree> ...]
set -u

base="${1:?usage: partition-check.sh <base-sha> <worktree>...}"
shift
trees=("$@")
[ "${#trees[@]}" -ge 2 ] || { echo "need at least two worktrees"; exit 2; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

for t in "${trees[@]}"; do
  git -C "$t" diff --name-only "$base"..HEAD | sort > "$tmp/$(basename "$t").files"
  printf '%-24s %s file(s)\n' "$(basename "$t")" "$(wc -l < "$tmp/$(basename "$t").files" | tr -d ' ')"
done

echo
status=0
for ((i=0; i<${#trees[@]}; i++)); do
  for ((j=i+1; j<${#trees[@]}; j++)); do
    a="$(basename "${trees[i]}")"; b="$(basename "${trees[j]}")"
    hits="$(comm -12 "$tmp/$a.files" "$tmp/$b.files")"
    if [ -n "$hits" ]; then
      echo "COLLISION  $a <-> $b"
      echo "$hits" | sed 's/^/           /'
      status=1
    fi
  done
done

# Shared files are lead-only; an agent touching one is a contract breach
# even when no other agent touched it.
for t in "${trees[@]}"; do
  bad="$(git -C "$t" diff --name-only "$base"..HEAD \
        | grep -E '^docs/|^tests/architecture/test_invariants\.py$' || true)"
  if [ -n "$bad" ]; then
    echo "LEAD-ONLY FILE TOUCHED by $(basename "$t")"
    echo "$bad" | sed 's/^/           /'
    status=1
  fi
done

[ "$status" -eq 0 ] && echo "clean: no collisions, no lead-only files touched"
exit "$status"
