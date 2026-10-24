"""Start Chatwell directly from a source checkout on any supported OS."""
from pathlib import Path
import sys

if sys.version_info < (3, 10):
    raise SystemExit('Python 3.10以上が必要です。')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chatwell.__main__ import main


if __name__ == '__main__':
    raise SystemExit(main())
