from pathlib import Path
import sys
import unittest


PACKAGE_ROOT = (
    Path(__file__).parents[1]
    / ".agents"
    / "skills"
    / "500px-feedback-growth"
    / "scripts"
)
sys.path.insert(0, str(PACKAGE_ROOT))


class PackageTest(unittest.TestCase):
    def test_exposes_schema_version(self):
        import feedback_growth

        self.assertEqual(feedback_growth.SCHEMA_VERSION, 1)
