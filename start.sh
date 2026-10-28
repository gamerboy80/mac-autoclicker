#!/bin/bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

die() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

if [[ "$(uname -s)" != "Darwin" ]]; then
    die "This autoclicker is designed for macOS."
fi

APP_SCRIPT="$SCRIPT_DIR/autoclicker.py"
REQUIREMENTS_FILE="$SCRIPT_DIR/requirements.txt"
VENV_DIR="$SCRIPT_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
STAMP_FILE="$VENV_DIR/.requirements.sha256"

[[ -f "$APP_SCRIPT" ]] || die "Could not find autoclicker.py beside start.sh."
[[ -f "$REQUIREMENTS_FILE" ]] || die "Could not find requirements.txt beside start.sh."

if [[ ! -x "$VENV_PYTHON" ]]; then
    if [[ -e "$VENV_DIR" ]]; then
        die "$VENV_DIR exists but has no usable Python interpreter. Repair or rename that environment, then run start.sh again."
    fi

    BOOTSTRAP_PYTHON="${PYTHON:-python3}"
    command -v "$BOOTSTRAP_PYTHON" >/dev/null 2>&1 \
        || die "Python 3 was not found. Install Python 3 or set PYTHON to its executable."

    printf 'Creating project virtual environment...\n'
    "$BOOTSTRAP_PYTHON" -m venv "$VENV_DIR" \
        || die "Could not create .venv. Check that the selected Python installation includes the venv module."
fi

[[ -x "$VENV_PYTHON" ]] || die "The project virtual environment does not contain an executable Python interpreter."

if ! "$VENV_PYTHON" -m pip --version >/dev/null 2>&1; then
    printf 'Repairing pip inside the project virtual environment...\n'
    "$VENV_PYTHON" -m ensurepip --upgrade \
        || die "Could not initialize pip inside .venv. No global Python packages were changed."
fi

REQUIREMENTS_HASH="$(shasum -a 256 "$REQUIREMENTS_FILE" | awk '{print $1}')"
STAMPED_HASH=""
if [[ -f "$STAMP_FILE" ]]; then
    STAMPED_HASH="$(<"$STAMP_FILE")"
fi

NEEDS_INSTALL=0
if [[ "$REQUIREMENTS_HASH" != "$STAMPED_HASH" ]]; then
    NEEDS_INSTALL=1
elif ! "$VENV_PYTHON" -m pip check >/dev/null 2>&1; then
    NEEDS_INSTALL=1
elif ! "$VENV_PYTHON" -c 'import AppKit, Foundation, objc' >/dev/null 2>&1; then
    NEEDS_INSTALL=1
fi

if [[ "$NEEDS_INSTALL" -eq 1 ]]; then
    printf 'Installing or repairing project dependencies in .venv...\n'
    "$VENV_PYTHON" -m pip install --disable-pip-version-check -r "$REQUIREMENTS_FILE" \
        || die "Dependency installation failed. The install was limited to .venv."
    "$VENV_PYTHON" -m pip check \
        || die "The installed dependencies are inconsistent. Review the pip output above."
    "$VENV_PYTHON" -c 'import AppKit, Foundation, objc' \
        || die "PyObjC could not be imported from .venv. Check your Python/macOS compatibility."

    TEMP_STAMP="$STAMP_FILE.tmp.$$"
    printf '%s\n' "$REQUIREMENTS_HASH" > "$TEMP_STAMP"
    mv -f "$TEMP_STAMP" "$STAMP_FILE"
fi

exec "$VENV_PYTHON" "$APP_SCRIPT" "$@"
