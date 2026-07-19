from __future__ import annotations

import sys

from config import APP_TITLE


def main() -> int:
    if any(argument in ("-h", "--help") for argument in sys.argv[1:]):
        print(f"{APP_TITLE}\n\nRun the native macOS application with no arguments.")
        return 0

    try:
        from native_ui import run_native_app
    except ModuleNotFoundError as exc:
        if exc.name not in {"AppKit", "Foundation", "objc"}:
            raise
        print(
            "The native macOS UI dependency is missing.\n"
            "Activate the project virtual environment, then run:\n\n"
            "  python -m pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1

    return run_native_app()


if __name__ == "__main__":
    raise SystemExit(main())
