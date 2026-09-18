"""Give subprocess regressions the same source imports as pytest itself."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT), os.environ.get("PYTHONPATH", "")))
