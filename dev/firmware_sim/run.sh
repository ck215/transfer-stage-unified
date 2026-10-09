#!/bin/bash
# run.sh <binary> <log-dir>: every scenario in its own process (fresh static state); one summary line each.
bin="$1"; logs="$2"; mkdir -p "$logs"; fail=0
for s in $("$bin" list); do
  "$bin" "$s" > "$logs/$s.log" 2>&1; rc=$?
  line=$(grep '^SCENARIO' "$logs/$s.log"); [ -z "$line" ] && line="SCENARIO $s CRASHED rc=$rc"
  echo "$line"; grep '^  FAIL' "$logs/$s.log" | sed 's/^/      /'
  [ $rc -ne 0 ] && fail=$((fail+1))
done
echo "failed scenarios: $fail"
