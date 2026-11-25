import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent / "backend"
if BACKEND.is_dir() and str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
