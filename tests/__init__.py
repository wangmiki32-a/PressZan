from pathlib import Path
import sys


PACKAGE_ROOT = (
    Path(__file__).parents[1]
    / ".agents"
    / "skills"
    / "500px-feedback-growth"
    / "scripts"
)
sys.path.insert(0, str(PACKAGE_ROOT))
