"""PyInstaller entry point: keeps the frozen build to a single import path."""

import multiprocessing
import sys

from redscan.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
