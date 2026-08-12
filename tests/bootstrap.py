from pathlib import Path
import sys


PACKAGE_ROOT = (
    Path(__file__).parents[1]
    / ".agents"
    / "skills"
    / "500px-feedback-growth"
    / "scripts"
)
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))
