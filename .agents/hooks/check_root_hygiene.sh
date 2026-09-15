#!/usr/bin/env bash
# Stop hook: enforces root directory hygiene.
# Reads Stop payload from stdin, checks for stray files, and blocks the agent
# from terminating until the mess is cleaned up.

set -euo pipefail

# Read the stdin JSON payload (not used directly here, but good practice)
read -r -t 5 PAYLOAD || PAYLOAD="{}"

# --- Configuration ---
PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

# Files explicitly allowed at the project root
ALLOWED_ROOTS=(
    ".DS_Store"
    ".git"
    ".gitattributes"
    ".gitignore"
    ".pytest_cache"
    ".venv"
    ".agents"
    "README.md"
    "__pycache__"
    "activate.sh"
    "firmware"
    "images"
    "pytest.ini"
    "requirements.txt"
    "run.bat"
    "run.sh"
    "run_macos.sh"
    "src"
    "tests"
)

# --- Check for stray files ---
STRAY=()
while IFS= read -r -d '' ITEM; do
    BASENAME="$(basename "$ITEM")"
    FOUND=0
    for ALLOWED in "${ALLOWED_ROOTS[@]}"; do
        if [[ "$BASENAME" == "$ALLOWED" ]]; then
            FOUND=1
            break
        fi
    done
    if [[ $FOUND -eq 0 ]]; then
        STRAY+=("$BASENAME")
    fi
done < <(find "$PROJECT_ROOT" -maxdepth 1 -mindepth 1 -print0)

# --- Output decision ---
if [[ ${#STRAY[@]} -eq 0 ]]; then
    # Clean — allow the agent to stop
    echo '{"decision": "allow"}'
else
    # Stray files found — block stop and force cleanup
    STRAY_LIST=$(printf '"%s", ' "${STRAY[@]}")
    STRAY_LIST="${STRAY_LIST%, }"  # trim trailing comma
    echo "{\"decision\": \"continue\", \"reason\": \"Root directory hygiene violation: stray file(s) detected: [${STRAY_LIST}]. Per workspace rules, all scratch files must be removed or moved to the designated artifact scratch directory before finishing. Please delete or relocate these files and then confirm completion.\"}"
fi
