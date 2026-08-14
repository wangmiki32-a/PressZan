from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests import bootstrap  # noqa: F401
from feedback_growth.model import CheckpointHeader, RunLog
from feedback_growth.store import (
    LogValidationError,
    begin_checkpoint,
    load_effective_runs,
    read_checkpoint,
    render_run_log,
)
from tests.helpers import confirmed_like, dt, event, run


class CycleSchemaTest(unittest.TestCase):
    def test_cycle_event_keeps_old_touch_event_valid(self):
        render_run_log(run([confirmed_like("a1", "p1", dt())]))
        render_run_log(
            run([event("cycle_started", dt(), cycle_id="c1", attribution_eligible=True)])
        )

    def test_cycle_event_rejects_unknown_field(self):
        with self.assertRaisesRegex(LogValidationError, "unexpected extra"):
            render_run_log(
                run(
                    [
                        event(
                            "cycle_started",
                            dt(),
                            cycle_id="c1",
                            attribution_eligible=True,
                            extra=1,
                        )
                    ]
                )
            )

    def test_review_observation_rejects_duplicate_photographers(self):
        with self.assertRaisesRegex(LogValidationError, "photographer_ids"):
            render_run_log(
                run(
                    [
                        event(
                            "review_photo_observed",
                            dt(),
                            cycle_id="c1",
                            review_kind="review_1d",
                            attempt=1,
                            scan_id="s1",
                            photo_id="mine-1",
                            photographer_ids=["p1", "p1"],
                            observed_at=dt().isoformat(),
                        )
                    ]
                )
            )

    def test_checkpoint_context_round_trip(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            header = CheckpointHeader(
                1,
                "review-1",
                "2026-08-14",
                "review",
                dt(14, 8),
                None,
                {"cycle_id": "c1", "review_kind": "review_3d", "attempt": "1"},
            )
            begin_checkpoint(root, header)

            rebuilt = read_checkpoint(root, "review-1")

            self.assertEqual(rebuilt.header.transaction_context, header.transaction_context)

    def test_run_and_review_checkpoints_can_coexist_same_day(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            begin_checkpoint(
                root,
                CheckpointHeader(1, "run-1", "2026-08-14", "run", dt(14, 8), None),
            )
            begin_checkpoint(
                root,
                CheckpointHeader(
                    1,
                    "review-1",
                    "2026-08-14",
                    "review",
                    dt(14, 9),
                    None,
                    {"cycle_id": "c1", "review_kind": "review_3d", "attempt": "1"},
                ),
            )

            effective = load_effective_runs(root)

            self.assertEqual({item.run_id for item in effective}, {"run-1", "review-1"})

    def test_duplicate_review_context_is_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            context = {"cycle_id": "c1", "review_kind": "review_3d", "attempt": "1"}
            begin_checkpoint(
                root,
                CheckpointHeader(1, "review-1", "2026-08-14", "review", dt(14, 8), None, context),
            )
            begin_checkpoint(
                root,
                CheckpointHeader(1, "review-2", "2026-08-14", "review", dt(14, 9), None, context),
            )

            with self.assertRaisesRegex(LogValidationError, "multiple active checkpoints"):
                load_effective_runs(root)


if __name__ == "__main__":
    unittest.main()
