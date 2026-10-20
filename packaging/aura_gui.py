"""PyInstaller entry point for the desktop app."""

import sys

from aura.gui import run

if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else None))
