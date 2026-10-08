#!/usr/bin/env bash
# Bundle acceptance smoke test (PACKAGING_PLAN P4), macOS and Linux.
#
#     packaging/smoke.sh [BUNDLE_DIR]          (default: dist/station)
#
# Runs the launcher (station-web, the only one: Tk and Qt retired 2026-10-07) FROM the bundle, in SIM, and asserts on exit codes
# and on lines in each run's own log file, never on timing alone:
#
#   0. the layout (packaging/layout.py): VERSION and release.json;
#      firmware/<every sketch dir the board table names> and libraries/;
#      tools/arduino-cli runs OFFLINE (a dead proxy is set) and its
#      `core list` shows arduino:avr and teensy:avr; tools/teensy_loader_cli
#      knows the Teensy 3.5 MCU; stable/station-stable is executable and its
#      --self-check resolves every stable module without opening a window;
#      stable/firmware/ has its sketches. A missing piece fails by name.
#   1. station-web: /api/state 200; / is the bundle's own index.html;
#      /api/theme.css served; serial enumeration ran; every Setup row ticked
#      and set to SIM; Launch builds all six models; /api/estop_all latches
#      every one; the station_version Setup reports is VERSION's tag and
#      build date; /api/quit exits 0 within 5 s; its log file names the stop,
#      the quit and SDL teardown (the gamepad hub opened and closed).
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
# The Setup rows, in display order. The Transfer Map and the Sample DB are
# stores with no port (their choice is "On"), so they are only ticked; rgb_analysis (screen
# capture) has no row of its own since 2026-09-28: it is drawn on the
# Transfer Map's page and launches with that row.
PORT_ROWS="stepper_probe dc_probe chuck_positioner temperature_controller rotator"
ALL_ROWS="$PORT_ROWS transfer_map sample_db"
# The sketch directories firmware/flash_firmware.py's DEVICES table names
# (tests/test_packaging.py keeps this list equal to the table).
SKETCHES="stepper_firmware high_polling_rate chuck_firmware temp_controller"
# arduino:avr and teensy:avr: the platforms of the flasher's two FQBNs.
CORES="arduino:avr teensy:avr"

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
post() {  # post <route> <json>; the station wants JSON and an Origin naming it
    curl -s --max-time 30 -X POST -H 'Content-Type: application/json' \
        -H "Origin: $BASE" --data "$2" "$BASE$1"
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
[ -x "$BUNDLE/station-web" ] || fail "station-web is not in the bundle"
[ "$FAILED" = 0 ] || exit 1
# -- 0. the layout --------------------------------------------------------
echo "== layout"
for f in VERSION release.json; do
    check "$f beside the launchers" test -s "$BUNDLE/$f"
done
TAG="$(sed -n 1p "$BUNDLE/VERSION" 2>/dev/null)"
BUILT="$(sed -n 3p "$BUNDLE/VERSION" 2>/dev/null)"
EXPECTED_VERSION="$TAG"
for sketch in $SKETCHES; do
    check "firmware/$sketch/$sketch.ino" test -f "$BUNDLE/firmware/$sketch/$sketch.ino"
    check "stable/firmware/$sketch/$sketch.ino" test -f "$BUNDLE/stable/firmware/$sketch/$sketch.ino"
done
check "firmware/libraries/" test -d "$BUNDLE/firmware/libraries"
check "tools/arduino-cli is executable" test -x "$BUNDLE/tools/arduino-cli"
check "tools/arduino-cli.yaml" test -f "$BUNDLE/tools/arduino-cli.yaml"
check "tools/arduino-data/" test -d "$BUNDLE/tools/arduino-data/packages"
# Offline: every request through a proxy that is not there. The config's
# directories are relative to the working directory, so run from tools/.
offline_cli() {
    (cd "$BUNDLE/tools" && HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 \
        ARDUINO_NETWORK_PROXY=http://127.0.0.1:9 \
        ./arduino-cli --config-file arduino-cli.yaml "$@")
}
if [ -x "$BUNDLE/tools/arduino-cli" ]; then
    offline_cli version > "$OUT/cli-version.txt" 2>&1
    check "tools/arduino-cli version runs offline ($(head -1 "$OUT/cli-version.txt"))" \
        grep -q "Version:" "$OUT/cli-version.txt"
    offline_cli core list > "$OUT/cli-cores.txt" 2>&1
    for core in $CORES; do
        check "tools/arduino-cli core list shows $core" grep -q "^$core " "$OUT/cli-cores.txt"
    done
fi
check "tools/teensy_loader_cli is executable" test -x "$BUNDLE/tools/teensy_loader_cli"
if [ -x "$BUNDLE/tools/teensy_loader_cli" ]; then
    "$BUNDLE/tools/teensy_loader_cli" --list-mcus > "$OUT/tlc.txt" 2>&1
    check "tools/teensy_loader_cli knows the Teensy 3.5 (mk64fx512)" grep -qi "mk64fx512" "$OUT/tlc.txt"
fi
check "stable/station-stable is executable" test -x "$BUNDLE/stable/station-stable"
if [ -x "$BUNDLE/stable/station-stable" ]; then
    "$BUNDLE/stable/station-stable" --self-check > "$OUT/stable.out" 2>&1 &
    wait_exit $! 60
    check "stable/station-stable --self-check resolves every stable module (exit $RC)" \
        grep -q "self-check ok" "$OUT/stable.out"
    [ "$RC" = 0 ] || tail -15 "$OUT/stable.out"
fi

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
    check "Setup rows are the ones this script drives ($keys)" test "$keys" = "$ALL_ROWS"
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

    # Setup reads the running version when an update check runs (the startup
    # check is off here: STATION_NO_UPDATE_CHECK); Check again reads it.
    run_setup check_updates > /dev/null
    reported=""
    for _ in $(seq 1 40); do
        reported="$(get /api/setup | grep -o '"station_version": "[^"]*"' | sed 's/.*: "//; s/"$//')"
        [ -n "$reported" ] && [ "$reported" != unknown ] && break
        sleep_s 0.5
    done
    check "station-web reports VERSION ($reported = $EXPECTED_VERSION)" \
        test "$reported" = "$EXPECTED_VERSION"

    r="$(post /api/quit '{}')"
    check "POST /api/quit answered ok" grep -q '"status": "ok"' <<< "$r"
    wait_exit "$WEB" 5
    check "station-web exited 0 within 5 s (exit $RC)" test "$RC" = 0

    if log="$(find_log "view=web port=$PORT")"; then
        pass "log file written: $log"
        check "log: Web dashboard served" log_has "$log" "Web Dashboard: serving at"
        check "log: serial ports enumerated" log_has "$log" "[Setup] Ports:"
        check "log: six models launched" log_has "$log" \
            "Launched: Stepper Probe, DC Probe, Chuck Positioner, Temperature Controller, Rotator, RGB Analysis"
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

echo
if [ "$FAILED" = 0 ]; then
    echo "SMOKE PASSED ($BUNDLE)"
    exit 0
fi
echo "SMOKE FAILED: $FAILED check(s) ($BUNDLE); outputs in $OUT"
exit 1
