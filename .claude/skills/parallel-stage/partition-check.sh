#!/usr/bin/env bash
# Intersect the file sets each worktree touched. Any COLLISION or
# LEAD-ONLY line must be resolved before merging; "clean" is the pass condition.
#
#   bash partition-check.sh <base-sha> <worktree> [<worktree> ...]
#
# One worktree is allowed: the lead-only check still applies to it.
set -u -o pipefail

base="${1:?usage: partition-check.sh <base-sha> <worktree>...}"
shift
trees=("$@")
[ "${#trees[@]}" -ge 1 ] || { echo "need at least one worktree"; exit 2; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

status=0
for t in "${trees[@]}"; do
  name="$(basename "$t")"
  # A base the worktree cannot resolve would diff to nothing and pass "clean".
  if ! git -C "$t" rev-parse --verify --quiet "$base^{commit}" > /dev/null; then
    echo "ERROR  $t cannot resolve base $base"; status=2; continue
  fi
  # Committed changes plus anything left uncommitted: an agent that forgot to
  # commit still touched the file.
  if ! { git -C "$t" diff --name-only "$base"..HEAD \
         && git -C "$t" diff --name-only HEAD \
         && git -C "$t" ls-files --others --exclude-standard; } \
       | sort -u > "$tmp/$name.files"; then
    echo "ERROR  cannot diff $t against $base"; status=2; continue
  fi
  printf '%-24s %s file(s)\n' "$name" "$(wc -l < "$tmp/$name.files" | tr -d ' ')"
done
[ "$status" -eq 2 ] && exit 2

echo
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

# Lead-only files: an agent touching one is a contract breach even when no
# other agent touched it. Keep in step with parallel-stage/SKILL.md section 1.
lead_only='^docs/|^CLAUDE\.md$|^README\.md$|^\.claude/|^src/views/theme\.py$|^src/views/base\.py$|^src/palette\.py$|^tests/test_architecture\.py$|^tests/golden/|^legacy/|^firmware/'
for t in "${trees[@]}"; do
  name="$(basename "$t")"
  bad="$(grep -E "$lead_only" "$tmp/$name.files" || true)"
  if [ -n "$bad" ]; then
    echo "LEAD-ONLY FILE TOUCHED by $name"
    echo "$bad" | sed 's/^/           /'
    status=1
  fi
done

[ "$status" -eq 0 ] && echo "clean: no collisions, no lead-only files touched"
exit "$status"
