"""Chatwell user command; see README.md."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chatwell.tooling import main

if __name__ == "__main__":
    raise SystemExit(main('stop-worker'))
