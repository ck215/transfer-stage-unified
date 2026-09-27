#!/usr/bin/env bash
# Bundle acceptance smoke test (PACKAGING_PLAN P4), macOS and Linux.
#
#     packaging/smoke.sh [BUNDLE_DIR]          (default: dist/station)
#
# Runs the three launchers FROM the bundle, in SIM, and asserts on exit codes
# and on lines in each run's own log file, never on timing alone:
#
#   1. station-web: /api/state 200; / is the bundle's own index.html;
#      /api/theme.css served; serial enumeration ran; every Setup row ticked
#      and set to SIM; Launch builds all six models; /api/estop_all latches
#      every one; /api/quit exits 0 within 5 s; its log file names the stop,
#      the quit and SDL teardown (the gamepad hub opened and closed).
#   2. station-tk:  opens its dashboard; SIGTERM -> the Controller's handler
#      closes, then re-raises the signal: exit status 143 within 5 s.
#   3. station-qt:  same, with QT_QPA_PLATFORM=offscreen (SMOKE_QT_PLATFORM
#      overrides it; empty = the native platform), and its log names the Qt
#      plugin path the entry point resolved.
#
# The exit code of a SIGTERM'd launcher is 143 (128 + 15), not 0: the
# Controller's handler runs close() and then re-raises the signal with the
# default action, the conventional way to report "ended by SIGTERM".
#
# Needs: bash, curl. Uses port 8099 (SMOKE_PORT). Logs land where the station
# puts them ($TRANSFER_STAGE_DATA_ROOT or ~/transfer-stage-runs, /logs); each
# step finds ITS log as the new file naming its view, so other stations
# logging to the same directory do not confuse it.
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
BUNDLE="${1:-$ROOT/dist/station}"
BUNDLE="$(cd "$BUNDLE" 2>/dev/null && pwd)" || { echo "FAIL no bundle at ${1:-$ROOT/dist/station}"; exit 2; }
PORT="${SMOKE_PORT:-8099}"
BASE="http://127.0.0.1:$PORT"
LOGDIR="${TRANSFER_STAGE_DATA_ROOT:-$HOME/transfer-stage-runs}/logs"
OUT="$(mktemp -d "${TMPDIR:-/tmp}/station-smoke.XXXXXX")"
QT_PLATFORM="${SMOKE_QT_PLATFORM-offscreen}"
# The seven Setup rows, in display order. Red Percent is screen capture and
# the Transfer Map is a store: neither has a port (their choice is "On"), so
# they are only ticked.
PORT_ROWS="stepper_probe dc_probe chuck_positioner temperature_controller rotator"
ALL_ROWS="$PORT_ROWS red_percent transfer_map"

FAILED=0
pass() { echo "PASS $*"; }
fail() { echo "FAIL $*"; FAILED=$((FAILED + 1)); }
check() {  # check <description> <command...>
    local what="$1"; shift
    if "$@"; then pass "$what"; else fail "$what"; fi
}
sleep_s() { sleep "$1" 2>/dev/null || perl -e "select(undef,undef,undef,$1)"; }

get() { curl -s --max-time 10 "$BASE$1"; }
code() { curl -s --max-time 10 -o /dev/null -w '%{http_code}' "$BASE$1"; }
post() {  # post <route> <json>; the station wants JSON and a same-origin Host
    curl -s --max-time 30 -X POST -H 'Content-Type: application/json' \
        --data "$2" "$BASE$1"
}
run_setup() {  # run_setup <command> [args-json]
    post /api/run "{\"name\":\"__setup__\",\"command\":\"$1\",\"args\":${2:-[]}}"
}

snapshot_logs() { mkdir -p "$LOGDIR"; ls -1 "$LOGDIR" > "$OUT/logs.before" 2>/dev/null; }
# The log file this run wrote: new since the snapshot, naming the view.
find_log() {  # find_log <needle>
    local f
    for f in $(ls -1t "$LOGDIR" 2>/dev/null); do
        grep -qxF "$f" "$OUT/logs.before" && continue
        if grep -qF -- "$1" "$LOGDIR/$f" 2>/dev/null; then echo "$LOGDIR/$f"; return 0; fi
    done
    return 1
}
wait_log() {  # wait_log <seconds> <needle...>: the run's log holds every needle
    local limit=$1 i=0 log n ok; shift
    while [ "$i" -lt $((limit * 2)) ]; do
        if log="$(find_log "$1")"; then
            ok=1
            for n in "$@"; do grep -qF -- "$n" "$log" || ok=0; done
            [ "$ok" = 1 ] && { echo "$log"; return 0; }
        fi
        sleep_s 0.5; i=$((i + 1))
    done
    return 1
}
wait_exit() {  # wait_exit <pid> <seconds>: sets RC to the exit status, 124 on timeout
    # Not in a $(...) subshell: only this shell can `wait` for its children.
    local pid=$1 limit=$2 i=0
    while kill -0 "$pid" 2>/dev/null && [ "$i" -lt $((limit * 10)) ]; do
        sleep_s 0.1; i=$((i + 1))
    done
    if kill -0 "$pid" 2>/dev/null; then
        kill -KILL "$pid" 2>/dev/null; wait "$pid" 2>/dev/null; RC=124; return
    fi
    wait "$pid"; RC=$?
}
log_has() { grep -qF -- "$2" "$1"; }

echo "bundle:  $BUNDLE"
echo "logs:    $LOGDIR"
echo "scratch: $OUT"
for view in tk qt web; do
    [ -x "$BUNDLE/station-$view" ] || fail "station-$view is not in the bundle"
done
[ "$FAILED" = 0 ] || exit 1
if curl -s --max-time 2 -o /dev/null "$BASE/api/state"; then
    echo "FAIL port $PORT is already answering; stop that server or set SMOKE_PORT"
    exit 1
fi

# -- 1. station-web -------------------------------------------------------
echo "== station-web"
snapshot_logs
"$BUNDLE/station-web" --no-browser --port "$PORT" > "$OUT/web.out" 2>&1 &
WEB=$!
up=0
for _ in $(seq 1 60); do
    [ "$(code /api/state)" = 200 ] && { up=1; break; }
    kill -0 "$WEB" 2>/dev/null || break
    sleep_s 0.5
done
if [ "$up" != 1 ]; then
    fail "station-web answered /api/state within 30 s"
    tail -20 "$OUT/web.out"
    kill -KILL "$WEB" 2>/dev/null
else
    pass "GET /api/state 200"
    get / > "$OUT/index.served"
    check "GET / is the bundle's own index.html" \
        cmp -s "$OUT/index.served" "$BUNDLE/_internal/views/web/static/index.html"
    check "GET /styles.css served from the bundle" \
        test "$(code /styles.css)" = 200
    get /api/theme.css > "$OUT/theme.css"
    check "GET /api/theme.css served (CSS variables)" grep -q -- '--' "$OUT/theme.css"

    # Launch waits for the port scan; cancel it so the smoke does not depend
    # on what is plugged into this machine. The rows are SIM either way.
    run_setup cancel_scan > /dev/null
    get /api/setup > "$OUT/setup.json"
    keys="$(grep -o '"key": "[a-z_]*"' "$OUT/setup.json" | sed 's/.*: "//; s/"$//' | tr '\n' ' ' | sed 's/ $//')"
    check "Setup rows are the six this script drives ($keys)" test "$keys" = "$ALL_ROWS"
    check "serial enumeration ran (Setup lists a ports key)" grep -q '"ports": \[' "$OUT/setup.json"
    # The gamepad list comes from the SDL hub (pygame). A bundle whose SDL
    # cannot load never gets this far: it hangs at `import pygame`.
    check "gamepad enumeration ran (Setup lists a gamepads key)" grep -q '"gamepads": \[' "$OUT/setup.json"
    for row in $ALL_ROWS; do
        r="$(run_setup "set_${row}_enabled" '[true]')"
        check "tick $row" grep -q '"status": "ok"' <<< "$r"
    done
    for row in $PORT_ROWS; do
        r="$(run_setup "set_${row}_port" '["SIM"]')"
        check "$row -> SIM" grep -q '"value": "SIM"' <<< "$r"
    done
    r="$(run_setup launch)"
    echo "     launch: $r"
    check "Launch built all six models" grep -q '"status": "ok"' <<< "$r"
    n=0
    for _ in $(seq 1 20); do
        get /api/state > "$OUT/state.json"
        n=$(grep -o '"model_mode": "' "$OUT/state.json" | wc -l | tr -d ' ')
        [ "$n" -ge 6 ] && break
        sleep_s 0.5
    done
    check "/api/state shows six models ($n)" test "$n" -ge 6

    r="$(post /api/estop_all '{}')"
    echo "     estop_all: $r"
    check "estop_all answered and latched" grep -q '"is_estopped": true' <<< "$r"
    get /api/state > "$OUT/state.estopped.json"
    latched=$(grep -o '"is_estopped": true' "$OUT/state.estopped.json" | wc -l | tr -d ' ')
    unlatched=$(grep -o '"is_estopped": false' "$OUT/state.estopped.json" | wc -l | tr -d ' ')
    # six models plus the station-wide flag
    check "every model latched ($latched latched, $unlatched not)" \
        test "$latched" -ge 7 -a "$unlatched" = 0

    r="$(post /api/quit '{}')"
    check "POST /api/quit answered ok" grep -q '"status": "ok"' <<< "$r"
    wait_exit "$WEB" 5
    check "station-web exited 0 within 5 s (exit $RC)" test "$RC" = 0

    if log="$(find_log "view=web port=$PORT")"; then
        pass "log file written: $log"
        check "log: Web dashboard served" log_has "$log" "Web Dashboard: serving at"
        check "log: serial ports enumerated" log_has "$log" "[Setup] Ports:"
        check "log: six models launched" log_has "$log" \
            "Launched: Stepper Probe, DC Probe, Chuck Positioner, Temperature Controller, Rotator, Red Percent"
        check "log: FULL STOP latched" log_has "$log" "FULL STOP"
        check "log: Quit from the Web console" log_has "$log" "Quit from the Web console"
        check "log: gamepad hub closed (SDL down)" log_has "$log" "[gamepad-hub] SDL down"
        if grep -q "Traceback" "$log"; then
            fail "log: no traceback"; grep -n -A3 "Traceback" "$log" | head -20
        else pass "log: no traceback"; fi
    else
        fail "a new log file names view=web port=$PORT in $LOGDIR"
    fi
fi

# -- 2 and 3. the desktop launchers ----------------------------------------
desktop() {  # desktop <view> <ready-needle> [extra log needles...]
    local view=$1 ready=$2 pid rc log=; shift 2
    echo "== station-$view"
    snapshot_logs
    "$BUNDLE/station-$view" > "$OUT/$view.out" 2>&1 &
    pid=$!
    if log="$(wait_log 30 "view=$view" "$ready")"; then
        pass "station-$view opened ($ready) - log $log"
    else
        fail "station-$view opened within 30 s"
        tail -20 "$OUT/$view.out"
    fi
    sleep_s 5
    kill -TERM "$pid" 2>/dev/null
    wait_exit "$pid" 5
    rc=$RC
    check "station-$view: SIGTERM -> Controller handler -> exit 143 within 5 s (exit $rc)" \
        test "$rc" = 143
    if [ -n "${log:-}" ]; then
        local needle
        for needle in "$@"; do
            check "log: $needle" log_has "$log" "$needle"
        done
        if grep -q "Traceback" "$log"; then
            fail "log: no traceback"; grep -n -A3 "Traceback" "$log" | head -20
        else pass "log: no traceback"; fi
    fi
    if [ "$rc" = 1 ]; then
        echo "     exit 1 with no SIGTERM handler run: a toolkit's own handler ended the"
        echo "     process past Controller.close() (Tk 9 on Aqua does this; see the"
        echo "     rb-pack handoff, CORE CHANGE REQUESTS). Nothing was stopped or closed."
    fi
    if [ "$rc" != 143 ]; then
        echo "     --- station-$view output (tail)"; tail -15 "$OUT/$view.out"
    fi
}

desktop tk "Dashboard Open: Tk dashboard ready" "[app] View: tk starting"

if [ -n "$QT_PLATFORM" ]; then export QT_QPA_PLATFORM="$QT_PLATFORM"; fi
desktop qt "Qt dashboard shown" "[app] View: qt starting" \
    "[packaging] Qt Plugin Path:" \
    "QT_QPA_PLATFORM_PLUGIN_PATH=$BUNDLE/_internal/PySide6/Qt/plugins/platforms"
unset QT_QPA_PLATFORM

echo
if [ "$FAILED" = 0 ]; then
    echo "SMOKE PASSED ($BUNDLE)"
    exit 0
fi
echo "SMOKE FAILED: $FAILED check(s) ($BUNDLE); outputs in $OUT"
exit 1
