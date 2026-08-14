from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests import bootstrap  # noqa: F401
from feedback_growth.automation import build_review_request, build_review_requests, matches_existing
from tests.helpers import dt


class AutomationContractTest(unittest.TestCase):
    def test_review_requests_are_relative_to_last_confirmed_like(self):
        completed = dt(14, 9, 27)
        with TemporaryDirectory() as directory:
            requests = build_review_requests("c1", completed, Path(directory))

        self.assertEqual(requests[0].due_at, completed + timedelta(hours=20))
        self.assertEqual(requests[1].due_at, completed + timedelta(hours=70))
        self.assertEqual(requests[0].name, "500px-review-c1-review_1d-1")
        self.assertEqual(requests[1].name, "500px-review-c1-review_3d-1")

    def test_payload_is_deterministic_and_scope_is_read_only(self):
        with TemporaryDirectory() as directory:
            first = build_review_request("c1", "review_3d", 2, dt(14, 9), Path(directory))
            second = build_review_request("c1", "review_3d", 2, dt(14, 9), Path(directory))

        self.assertEqual(first.payload_digest, second.payload_digest)
        self.assertTrue(matches_existing(first, second.payload))
        self.assertIn("只读", first.payload["prompt"])
        self.assertIn("冻结的 5 张作品", first.payload["prompt"])

    def test_payload_mismatch_is_rejected(self):
        with TemporaryDirectory() as directory:
            request = build_review_request("c1", "review_1d", 1, dt(14, 9), Path(directory))
            changed = dict(request.payload)
            changed["cycle_id"] = "c2"

        self.assertFalse(matches_existing(request, changed))


if __name__ == "__main__":
    unittest.main()
