#!/bin/bash
# run_swap_macos.sh — macOS variant of run_swap.sh: same arguments, same
# flash-then-launch, plus run_macos.sh's PySide6 self-heal when the new app
# is launched with Qt (--qt / --pyside / --view qt|pyside).
#
#   ./run_swap_macos.sh [new|mvc-refactor|main|classic] [--no-flash] [--force-flash] [view flags...]
#
# See run_swap.sh for the details and the RUN_SWAP_DRY_RUN / STATION_FLASH_ONLY
# / STATION_MAIN_TREE environment variables.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
DRY="${RUN_SWAP_DRY_RUN:-0}"

if [ -f "$HERE/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$HERE/.venv/bin/activate"
elif [ -z "${VIRTUAL_ENV:-}" ]; then
    echo "No .venv here and none active: activate the project's venv first." >&2
    exit 1
fi

# Only the new app has a Qt view; main is Tkinter only.
BRANCH="new"
case "${1:-}" in main|classic) BRANCH="main" ;; esac

VIEW_MODE="tk"
prev=""
for arg in "$@"; do
    case "$arg" in
        --qt|--pyside) VIEW_MODE="pyside" ;;
        --web) VIEW_MODE="web" ;;
        --tk|--tkinter|--legacy) VIEW_MODE="tk" ;;
        --view=qt|--view=pyside) VIEW_MODE="pyside" ;;
    esac
    if [ "$prev" = "--view" ]; then
        case "$arg" in qt|pyside) VIEW_MODE="pyside" ;; *) VIEW_MODE="$arg" ;; esac
    fi
    prev="$arg"
done

if [ "$BRANCH" = "new" ] && [ "$VIEW_MODE" = "pyside" ]; then
    PYSIDE6_DIR=$(python3 -c "import PySide6, os; print(os.path.dirname(PySide6.__file__))" 2>/dev/null || echo "")
    if ! python3 -c "from PySide6.QtWidgets import QApplication" 2>/dev/null; then
        if [ "$DRY" = 1 ]; then
            echo "[macOS Fix] PySide6 looks broken; a real run would reinstall it."
        else
            echo "[macOS Fix] PySide6 appears corrupt or incomplete — rebuilding..."
            pip uninstall -y PySide6 PySide6-Essentials PySide6-Addons 2>/dev/null || true
            pip install --force-reinstall PySide6
            PYSIDE6_DIR=$(python3 -c "import PySide6, os; print(os.path.dirname(PySide6.__file__))")
            echo "[macOS Fix] PySide6 reinstalled successfully."
        fi
    else
        echo "[macOS Fix] PySide6 OK — skipping reinstall."
    fi
    # macOS marks pip-downloaded .dylib files hidden (UF_HIDDEN); Qt's plugin
    # scanner then skips them and reports "plugin not found".
    if [ "$DRY" != 1 ] && [ -n "$PYSIDE6_DIR" ] && [ -d "$PYSIDE6_DIR" ]; then
        echo "[macOS Fix] Clearing hidden flags on Qt plugins..."
        chflags -R nohidden "$PYSIDE6_DIR/" 2>/dev/null || true
    fi
fi

exec "$HERE/run_swap.sh" "$@"
