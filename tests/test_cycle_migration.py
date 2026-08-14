from datetime import timedelta
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests import bootstrap  # noqa: F401
from feedback_growth.cli import _migration_analysis
from feedback_growth.model import Event, RunLog
from feedback_growth.store import seal_run
from tests.helpers import confirmed_like, dt, opened, received


class CycleMigrationTest(unittest.TestCase):
    def test_analyze_uses_latest_pre_touch_baseline_and_first_post_touch_review(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            photo_ids = [f"work-{index}" for index in range(1, 6)]
            baseline_at = dt(13, 6)
            baseline = [Event("scan_started", baseline_at, {"scan_id": "b1", "owner_id": "owner", "profile_url": "https://example.test/owner"})]
            baseline.extend(Event("work_observed", baseline_at, {"scan_id": "b1", "photo_id": photo_id, "photo_url": f"https://example.test/{photo_id}", "position": index}) for index, photo_id in enumerate(photo_ids, 1))
            baseline.append(received(photo_ids[0], "already", baseline_at, 1))
            baseline[-1] = Event("received_like_observed", baseline_at, {**baseline[-1].data, "scan_id": "b1"})
            seal_run(root, RunLog(1, "baseline", "2026-08-13", "preflight", "completed", baseline_at, baseline_at, tuple(baseline)))

            touch_at = dt(13, 7)
            action_id = "action-1"
            episode_id = hashlib.sha256(f"episode:p1:{action_id}".encode()).hexdigest()
            touch_events = (
                confirmed_like(action_id, "p1", touch_at, photo_id="target"),
                opened(episode_id, "p1", action_id, touch_at, touch_at + timedelta(hours=72)),
            )
            seal_run(root, RunLog(1, "like-run", "2026-08-13", "run", "completed", touch_at, touch_at, touch_events))

            review_at = dt(14, 2)
            review = [Event("scan_started", review_at, {"scan_id": "r1", "owner_id": "owner", "profile_url": "https://example.test/owner"})]
            review.extend(Event("work_observed", review_at, {"scan_id": "r1", "photo_id": photo_id, "photo_url": f"https://example.test/{photo_id}", "position": index}) for index, photo_id in enumerate(photo_ids, 1))
            review.append(Event("received_like_observed", review_at, {"scan_id": "r1", "photo_id": photo_ids[0], "work_position": 1, "photographer_id": "p1", "display_name": "p1", "profile_url": "https://example.test/p1"}))
            seal_run(root, RunLog(1, "review", "2026-08-14", "preflight", "completed", review_at, review_at, tuple(review)))

            analysis = _migration_analysis(root, ["like-run"], photo_ids)

            self.assertTrue(analysis["attribution_eligible"])
            self.assertEqual(analysis["baseline"]["run_id"], "baseline")
            self.assertEqual(analysis["review_1d"]["run_id"], "review")
            self.assertEqual(analysis["review_1d"]["by_photo"][photo_ids[0]], ["p1"])
            self.assertEqual(analysis["like_completed_at"], touch_at.isoformat())

    def test_missing_one_baseline_work_marks_cycle_ineligible(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            photo_ids = [f"work-{index}" for index in range(1, 6)]
            touch_at = dt(13, 7)
            action_id = "action-1"
            episode_id = hashlib.sha256(f"episode:p1:{action_id}".encode()).hexdigest()
            seal_run(root, RunLog(1, "like-run", "2026-08-13", "run", "completed", touch_at, touch_at, (
                confirmed_like(action_id, "p1", touch_at, photo_id="target"),
                opened(episode_id, "p1", action_id, touch_at, touch_at + timedelta(hours=72)),
            )))

            analysis = _migration_analysis(root, ["like-run"], photo_ids)

            self.assertFalse(analysis["attribution_eligible"])
            self.assertIsNone(analysis["baseline"])


if __name__ == "__main__":
    unittest.main()
