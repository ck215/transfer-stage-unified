#!/bin/bash
# run.sh — the station's one launcher on macOS and Linux (run.bat on Windows).
#
#   ./run.sh [--qt | --web | --tk] [app flags...]      ./run.sh --help lists them
#
# Finds the project's Python (a .venv in this folder, else the virtualenv
# already active in the shell: a git worktree shares the main checkout's),
# then runs src/app.py with every argument. It prints nothing when all is
# well: the firmware check, the update check and the Web address are rows on
# the Setup page, not terminal lines. Not finding Python is the only message.
#
# macOS only, and only when the Qt view is coming (the default): PySide6 is
# checked first, reinstalled if it is broken, and the hidden flag pip leaves
# on its plugin dylibs is cleared, or Qt aborts with "plugin not found". That
# branch is the toolkit's, not the operator's; the app is the same on every OS.
cd "$(dirname "$0")" || exit 1

if [ -f .venv/bin/activate ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
elif [ -z "${VIRTUAL_ENV:-}" ]; then
    echo "run.sh: no .venv in $(pwd) and no virtualenv active: create one (python3 -m venv .venv; .venv/bin/pip install -e '.[qt]') or activate the project's, then run ./run.sh again." >&2
    exit 1
fi
PY=python3
[ -x "$VIRTUAL_ENV/bin/python3" ] && PY="$VIRTUAL_ENV/bin/python3"
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "run.sh: python3 not found in $VIRTUAL_ENV: recreate the venv (python3 -m venv .venv)." >&2
    exit 1
fi

# Is the Qt view coming? No view flag means yes (app.py's default).
wants_qt() {
    local view="qt" prev=""
    for arg in "$@"; do
        if [ "$prev" = "--view" ]; then view="$arg"; fi
        case "$arg" in
            -h|--help) return 1 ;;
            --qt|--pyside) view="qt" ;;
            --web) view="web" ;;
            --tk|--tkinter|--legacy) view="tk" ;;
            --view=*) view="${arg#--view=}" ;;
        esac
        prev="$arg"
    done
    case "$view" in qt|pyside|pyside6) return 0 ;; *) return 1 ;; esac
}

if [ "$(uname -s)" = "Darwin" ] && wants_qt "$@"; then
    if ! "$PY" -c "from PySide6.QtWidgets import QApplication" >/dev/null 2>&1; then
        echo "run.sh: PySide6 is broken in this venv; reinstalling it (once, needs the network)..." >&2
        "$PY" -m pip uninstall -y -q PySide6 PySide6-Essentials PySide6-Addons >/dev/null 2>&1
        if ! "$PY" -m pip install -q --force-reinstall PySide6 >/dev/null; then
            echo "run.sh: the PySide6 reinstall failed. Start with --web or --tk, or run: $PY -m pip install --force-reinstall PySide6" >&2
            exit 1
        fi
    fi
    PYSIDE6_DIR=$("$PY" -c "import PySide6, os; print(os.path.dirname(PySide6.__file__))" 2>/dev/null)
    if [ -n "$PYSIDE6_DIR" ] && [ -d "$PYSIDE6_DIR" ]; then
        chflags -R nohidden "$PYSIDE6_DIR/" 2>/dev/null
    fi
fi

exec "$PY" src/app.py "$@"
