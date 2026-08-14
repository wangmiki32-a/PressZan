from datetime import timedelta
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests import bootstrap  # noqa: F401
from feedback_growth.model import CheckpointHeader, RunLog
from feedback_growth.analytics import rebuild_state
from feedback_growth.cycles import rebuild_cycles
from feedback_growth.store import (
    LogValidationError,
    begin_checkpoint,
    load_effective_runs,
    read_checkpoint,
    render_run_log,
)
from tests.helpers import confirmed_like, dt, event, opened, run


def episode_id(photographer_id, action_id):
    return hashlib.sha256(f"episode:{photographer_id}:{action_id}".encode()).hexdigest()


def cycle_run(*, with_feedback=False):
    touch = dt(12, 8)
    episode = episode_id("p1", "a1")
    review_time = touch + timedelta(hours=20)
    final_time = touch + timedelta(hours=70)
    events = [
        event("cycle_started", touch - timedelta(hours=1), cycle_id="c1", attribution_eligible=True),
        event(
            "cycle_showcase_frozen",
            touch - timedelta(minutes=50),
            cycle_id="c1",
            photo_ids=[f"mine-{index}" for index in range(1, 6)],
            showcase_digest="showcase",
        ),
        event(
            "cycle_baseline_completed",
            touch - timedelta(minutes=40),
            cycle_id="c1",
            scan_id="baseline",
            baseline_digest="baseline",
        ),
        confirmed_like("a1", "p1", touch),
        opened(episode, "p1", "a1", touch, touch + timedelta(hours=72)),
        event(
            "cycle_like_completed",
            touch + timedelta(minutes=1),
            cycle_id="c1",
            mapped_run_ids=["run-1"],
            touch_action_ids=["a1"],
            episode_ids=[episode],
            like_completed_at=touch.isoformat(),
            terminal_status="completed",
        ),
    ]
    for kind, due in (("review_1d", review_time), ("review_3d", final_time)):
        events.append(
            event(
                "review_schedule_requested",
                touch + timedelta(minutes=2),
                cycle_id="c1",
                review_kind=kind,
                attempt=1,
                due_at=due.isoformat(),
                state_root="/state",
                automation_name=f"500px-review-c1-{kind}-1",
                payload_digest=kind,
            )
        )
    if with_feedback:
        events.append(
            event(
                "review_photo_observed",
                review_time,
                cycle_id="c1",
                review_kind="review_1d",
                attempt=1,
                scan_id="review-1",
                photo_id="mine-1",
                photographer_ids=["p1"],
                observed_at=review_time.isoformat(),
            )
        )
    events.extend(
        [
            event(
                "review_started",
                final_time,
                cycle_id="c1",
                review_kind="review_3d",
                attempt=1,
                due_at=final_time.isoformat(),
                started_at=final_time.isoformat(),
            ),
            event(
                "review_completed",
                final_time,
                cycle_id="c1",
                review_kind="review_3d",
                attempt=1,
                scan_id="review-3",
                completed_at=final_time.isoformat(),
            ),
        ]
    )
    return run(events), touch, episode


class CycleSchemaTest(unittest.TestCase):
    def test_migrated_historical_review_can_precede_cycle_registration_time(self):
        touch_at = dt(13, 7)
        cycle_created = dt(14, 13)
        review_at = dt(14, 2)
        photo_ids = [f"work-{index}" for index in range(1, 6)]
        events = [
            event("cycle_started", cycle_created, cycle_id="c1", attribution_eligible=True),
            event("cycle_showcase_frozen", cycle_created, cycle_id="c1", photo_ids=photo_ids, showcase_digest="d"),
            event("cycle_like_completed", cycle_created, cycle_id="c1", mapped_run_ids=["r1"], touch_action_ids=[], episode_ids=[], like_completed_at=touch_at.isoformat(), terminal_status="completed"),
            event("review_schedule_requested", cycle_created, cycle_id="c1", review_kind="review_1d", attempt=1, due_at=(touch_at + timedelta(hours=20)).isoformat(), state_root="/tmp/state", automation_name="review", payload_digest="d"),
            event("review_started", review_at, cycle_id="c1", review_kind="review_1d", attempt=1, due_at=(touch_at + timedelta(hours=20)).isoformat(), started_at=review_at.isoformat()),
        ]
        for photo_id in photo_ids:
            events.append(event("review_photo_observed", review_at, cycle_id="c1", review_kind="review_1d", attempt=1, scan_id="s1", photo_id=photo_id, photographer_ids=[], observed_at=review_at.isoformat()))
        events.append(event("review_completed", review_at, cycle_id="c1", review_kind="review_1d", attempt=1, scan_id="s1", completed_at=review_at.isoformat()))

        cycles = rebuild_cycles((run(events),), {}, cycle_created)

        self.assertEqual(cycles["c1"].review_1d.status, "completed")

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

    def test_final_review_does_not_mature_before_expiry(self):
        log, touch, episode = cycle_run()

        state = rebuild_state([log], touch + timedelta(hours=71, minutes=59))

        evidence = state.photographers["p1"].eligible_episodes[0]
        self.assertEqual(evidence.episode_id, episode)
        self.assertEqual(evidence.outcome, "open")
        self.assertEqual(state.cycles["c1"].status, "reviews_scheduled")

    def test_final_review_derives_failure_at_expiry(self):
        log, touch, _ = cycle_run()

        state = rebuild_state([log], touch + timedelta(hours=72))

        self.assertEqual(state.photographers["p1"].eligible_episodes[0].outcome, "failure")
        self.assertEqual(state.cycles["c1"].status, "settled")

    def test_scoped_feedback_becomes_success(self):
        log, touch, _ = cycle_run(with_feedback=True)

        state = rebuild_state([log], touch + timedelta(hours=72))

        evidence = state.photographers["p1"].eligible_episodes[0]
        self.assertEqual(evidence.outcome, "success")
        self.assertEqual(evidence.received_like_count, 1)


if __name__ == "__main__":
    unittest.main()
