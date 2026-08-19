from datetime import timedelta
import hashlib
import unittest

from tests import bootstrap  # noqa: F401
from feedback_growth import analytics
from feedback_growth.analytics import (
    StateValidationError,
    beta_parameters,
    classify_photographer,
    matured_cohort_counts,
    matured_cohort_kpi,
    rebuild_state,
)
from feedback_growth.model import RunLog
from tests.helpers import confirmed_like, dt, event, immediate_like, latest_three_scan, opened, received, run


NOW = dt()


def episode_id(photographer_id, action_id):
    return hashlib.sha256(f"episode:{photographer_id}:{action_id}".encode()).hexdigest()


def success(event_time, episode, photo_id, count=1):
    return event(
        "feedback_episode_succeeded",
        event_time,
        episode_id=episode,
        received_photo_id=photo_id,
        feedback_first_seen_at=event_time.isoformat(),
        received_like_count=count,
    )


def failed(event_time, episode, expires_at):
    return event(
        "feedback_episode_failed",
        event_time,
        episode_id=episode,
        expired_at=expires_at.isoformat(),
    )


class AnalyticsTest(unittest.TestCase):
    def test_three_new_works_give_three_points_and_clear_negative(self):
        baseline_at = dt(18, 8)
        touch_at = dt(18, 10)
        feedback_at = dt(19, 8)
        events = latest_three_scan(
            "base",
            baseline_at,
            baseline_photo_ids=("mine-1", "mine-2", "mine-3"),
        )
        events.append(immediate_like("a1", "p1", touch_at))
        events.extend(
            latest_three_scan(
                "next",
                feedback_at,
                (("mine-1", "p1"), ("mine-2", "p1"), ("mine-3", "p1")),
                new_pair_count=3,
                new_feedback_photographer_count=1,
                new_feedback_points=3,
            )
        )

        state = rebuild_state([run(events, day="2026-08-19")], feedback_at)

        evidence = state.touch_feedback["a1"]
        self.assertEqual(evidence.feedback_points, 3)
        self.assertFalse(evidence.unanswered)
        self.assertEqual(state.photographers["p1"].raw_feedback_points, 3)
        self.assertEqual(classify_photographer(state.photographers["p1"], feedback_at), "verified")

    def test_one_and_two_new_works_award_depth_points(self):
        for count in (1, 2):
            with self.subTest(count=count):
                baseline_at = dt(18, 8)
                touch_at = dt(18, 10)
                feedback_at = dt(19, 8)
                events = latest_three_scan(
                    "base",
                    baseline_at,
                    baseline_photo_ids=("mine-1", "mine-2", "mine-3"),
                )
                events.append(immediate_like("a1", "p1", touch_at))
                observations = tuple((f"mine-{index}", "p1") for index in range(1, count + 1))
                events.extend(
                    latest_three_scan(
                        "next",
                        feedback_at,
                        observations,
                        new_pair_count=count,
                        new_feedback_photographer_count=1,
                        new_feedback_points=count,
                    )
                )

                state = rebuild_state([run(events, day="2026-08-19")], feedback_at)

                self.assertEqual(state.touch_feedback["a1"].feedback_points, count)

    def test_repeated_pair_does_not_award_twice(self):
        baseline_at = dt(17, 8)
        touch_at = dt(17, 10)
        first_feedback = dt(18, 8)
        repeated = dt(19, 8)
        events = latest_three_scan("base", baseline_at, baseline_photo_ids=("mine-1", "mine-2", "mine-3"))
        events.append(immediate_like("a1", "p1", touch_at))
        events.extend(latest_three_scan("first", first_feedback, (("mine-1", "p1"),), new_pair_count=1, new_feedback_photographer_count=1, new_feedback_points=1))
        events.extend(latest_three_scan("repeat", repeated, (("mine-1", "p1"),)))

        state = rebuild_state([run(events, day="2026-08-19")], repeated)

        self.assertEqual(state.touch_feedback["a1"].feedback_points, 1)
        self.assertEqual(state.feedback_scans[-1].new_feedback_points, 0)

    def test_first_complete_scan_only_establishes_baseline(self):
        baseline_at = dt(18, 8)
        touch_at = dt(18, 10)
        observations = (("mine-1", "p1"), ("mine-2", "p1"))
        events = [immediate_like("a1", "p1", touch_at)]
        events.extend(
            latest_three_scan(
                "base",
                dt(19, 8),
                observations,
                baseline_photo_ids=("mine-1", "mine-2", "mine-3"),
            )
        )

        state = rebuild_state([run(events, day="2026-08-19")], dt(19, 8))

        self.assertEqual(state.touch_feedback["a1"].feedback_points, 0)
        self.assertTrue(state.touch_feedback["a1"].unanswered)
        self.assertEqual(state.baselined_photo_ids, frozenset({"mine-1", "mine-2", "mine-3"}))

    def test_incomplete_photo_does_not_establish_baseline(self):
        first_scan = dt(18, 8)
        second_scan = dt(19, 8)
        events = latest_three_scan(
            "partial",
            first_scan,
            (("mine-3", "p1"),),
            completed_photo_ids=("mine-1", "mine-2"),
            baseline_photo_ids=("mine-1", "mine-2"),
        )
        events.extend(
            latest_three_scan(
                "complete",
                second_scan,
                (("mine-3", "p1"),),
                completed_photo_ids=("mine-1", "mine-2", "mine-3"),
                baseline_photo_ids=("mine-3",),
            )
        )

        state = rebuild_state([run(events, day="2026-08-19")], second_scan)

        self.assertEqual(state.feedback_scans[0].baseline_photo_ids, frozenset({"mine-1", "mine-2"}))
        self.assertEqual(state.feedback_scans[1].baseline_photo_ids, frozenset({"mine-3"}))
        self.assertEqual(state.feedback_scans[1].new_feedback_points, 0)

    def test_full_latest_touch_does_not_backfill_older_touch(self):
        older_touch = dt(15, 8)
        newer_touch = dt(16, 8)
        legacy_feedback = newer_touch + timedelta(hours=1)
        baseline_at = dt(17, 8)
        scan_at = dt(18, 8)
        ep = episode_id("p1", "a2")
        events = [
            immediate_like("a1", "p1", older_touch),
            confirmed_like("a2", "p1", newer_touch),
            opened(ep, "p1", "a2", newer_touch, newer_touch + timedelta(hours=72)),
            received("legacy-mine", "p1", legacy_feedback),
            success(legacy_feedback, ep, "legacy-mine", count=3),
        ]
        events.extend(latest_three_scan("base", baseline_at, baseline_photo_ids=("mine-1", "mine-2", "mine-3")))
        events.extend(latest_three_scan("next", scan_at, (("mine-1", "p1"),), new_pair_count=1))

        state = rebuild_state([run(events, day="2026-08-19")], scan_at)

        self.assertEqual(state.touch_feedback["a2"].feedback_points, 3)
        self.assertEqual(state.touch_feedback["a1"].feedback_points, 0)
        self.assertTrue(state.touch_feedback["a1"].unanswered)
        self.assertEqual(state.feedback_scans[-1].new_feedback_points, 0)

    def test_build_feedback_scan_completed_event_uses_same_ledger(self):
        baseline_at = dt(18, 8)
        touch_at = dt(18, 10)
        feedback_at = dt(19, 8)
        events = latest_three_scan("base", baseline_at, baseline_photo_ids=("mine-1", "mine-2", "mine-3"))
        events.append(immediate_like("a1", "p1", touch_at))
        events.extend(latest_three_scan("next", feedback_at, (("mine-1", "p1"), ("mine-2", "p1")), include_summary=False))
        logs = [run(events, day="2026-08-19", status="active")]

        self.assertTrue(hasattr(analytics, "build_feedback_scan_completed_event"))
        completed = analytics.build_feedback_scan_completed_event(
            logs,
            "run-1",
            "next",
            ("mine-1", "mine-2", "mine-3"),
            feedback_at,
        )

        self.assertEqual(completed.data["baseline_photo_ids"], [])
        self.assertEqual(completed.data["new_pair_count"], 2)
        self.assertEqual(completed.data["new_feedback_photographer_count"], 1)
        self.assertEqual(completed.data["new_feedback_points"], 2)

    def test_feedback_scan_id_is_scoped_to_its_run(self):
        first_at = dt(18, 8)
        second_at = dt(19, 8)
        first = run(
            latest_three_scan(
                "shared",
                first_at,
                baseline_photo_ids=("mine-1", "mine-2", "mine-3"),
            ),
            run_id="scan-run-1",
            day="2026-08-18",
        )
        second = run(
            latest_three_scan("shared", second_at),
            run_id="scan-run-2",
            day="2026-08-19",
        )

        state = rebuild_state([first, second], second_at)

        self.assertEqual([scan.scan_id for scan in state.feedback_scans], ["shared", "shared"])

    def test_legacy_success_derived_from_immediate_scan_is_ignored(self):
        touch_at = dt(19, 6)
        scan_at = dt(19, 8)
        action_id = "legacy-touch"
        identifier = episode_id("p1", action_id)
        events = [
            confirmed_like(action_id, "p1", touch_at),
            opened(identifier, "p1", action_id, touch_at, touch_at + timedelta(hours=72)),
        ]
        events.extend(
            latest_three_scan(
                "latest-three",
                scan_at,
                (("mine-1", "p1"),),
                include_summary=False,
            )
        )
        events.append(success(scan_at, identifier, "mine-1"))

        state = rebuild_state([run(events, day="2026-08-19")], scan_at)

        self.assertEqual(state.episodes[identifier].outcome, "open")
        self.assertEqual(state.touch_feedback[action_id].feedback_points, 0)

    def test_daily_coverage_unions_confirmed_likes_and_skips_by_photographer(self):
        touched_at = NOW - timedelta(hours=1)
        episode = episode_id("p1", "a1")
        events = [
            confirmed_like("a1", "p1", touched_at),
            opened(episode, "p1", "a1", touched_at, touched_at + timedelta(hours=72)),
            event(
                "candidate_skipped",
                touched_at + timedelta(minutes=1),
                photographer_id="p2",
                reason="already_liked",
                quota_bucket="new",
            ),
            event(
                "candidate_skipped",
                touched_at + timedelta(minutes=2),
                photographer_id="p1",
                reason="already_liked",
                quota_bucket="retest",
            ),
        ]

        state = rebuild_state([run(events, status="active")], NOW)

        daily = state.daily_tasks["2026-08-12"]
        self.assertEqual(daily.covered_photographer_ids, frozenset({"p1", "p2"}))
        self.assertEqual(daily.confirmed_likes, 1)
        self.assertEqual(daily.quota_counts, {"exploit_first": 1, "new": 1})

    def test_future_100_like_day_is_not_complete_before_200_photographers(self):
        started = dt(18, 1)
        events = []
        for index in range(100):
            occurred_at = started + timedelta(seconds=index)
            photographer_id = f"p{index:03}"
            action_id = f"a{index:03}"
            episode = episode_id(photographer_id, action_id)
            events.extend(
                (
                    confirmed_like(action_id, photographer_id, occurred_at),
                    opened(episode, photographer_id, action_id, occurred_at, occurred_at + timedelta(hours=72)),
                )
            )

        state = rebuild_state(
            [run(events, day="2026-08-18", status="in_progress")],
            dt(18, 12),
        )

        daily = state.daily_tasks["2026-08-18"]
        self.assertEqual(daily.status, "in_progress")
        self.assertEqual(len(daily.covered_photographer_ids), 100)

    def test_second_touch_extends_same_episode(self):
        t0 = NOW - timedelta(days=10)
        ep = episode_id("p1", "a1")
        first_expiry = t0 + timedelta(hours=72)
        second = t0 + timedelta(hours=1)
        events = [
            confirmed_like("a1", "p1", t0),
            opened(ep, "p1", "a1", t0, first_expiry),
            confirmed_like("a2", "p1", second),
            event(
                "feedback_episode_extended",
                second,
                episode_id=ep,
                touch_action_id="a2",
                previous_expires_at=first_expiry.isoformat(),
                expires_at=(second + timedelta(hours=72)).isoformat(),
            ),
        ]

        state = rebuild_state([run(events)], NOW)

        episode = state.episodes[ep]
        self.assertEqual(episode.touch_action_ids, ("a1", "a2"))
        self.assertEqual(episode.expires_at, t0 + timedelta(hours=73))

    def test_multiple_received_likes_reward_one_episode_once(self):
        t0 = NOW - timedelta(days=10)
        ep = episode_id("p1", "a1")
        feedback = t0 + timedelta(hours=2)
        events = [
            confirmed_like("a1", "p1", t0),
            opened(ep, "p1", "a1", t0, t0 + timedelta(hours=72)),
            received("mine-1", "p1", feedback),
            success(feedback, ep, "mine-1"),
            received("mine-2", "p1", feedback + timedelta(hours=1)),
        ]

        state = rebuild_state([run(events)], NOW)

        self.assertEqual(state.episodes[ep].outcome, "success")
        self.assertEqual(state.episodes[ep].received_like_count, 2)
        self.assertEqual(sum(e.outcome == "success" for e in state.episodes.values()), 1)

    def test_baseline_pair_cannot_become_feedback(self):
        t0 = NOW - timedelta(days=10)
        ep = episode_id("p1", "a1")
        feedback = t0 + timedelta(hours=2)
        events = [
            received("mine-1", "p1", t0 - timedelta(days=1)),
            confirmed_like("a1", "p1", t0),
            opened(ep, "p1", "a1", t0, t0 + timedelta(hours=72)),
            received("mine-1", "p1", feedback),
            success(feedback, ep, "mine-1"),
        ]

        with self.assertRaisesRegex(StateValidationError, "new post-touch"):
            rebuild_state([run(events)], NOW)

    def test_episode_id_is_deterministic(self):
        t0 = NOW - timedelta(days=10)
        events = [
            confirmed_like("a1", "p1", t0),
            opened("wrong", "p1", "a1", t0, t0 + timedelta(hours=72)),
        ]

        with self.assertRaisesRegex(StateValidationError, "episode_id"):
            rebuild_state([run(events)], NOW)

    def test_matured_cohort_counts_all_touches_and_unique_successes(self):
        first = NOW - timedelta(days=29)
        second = NOW - timedelta(days=28)
        ep = episode_id("p1", "a1")
        feedback = second + timedelta(hours=2)
        events = [
            confirmed_like("a1", "p1", first),
            opened(ep, "p1", "a1", first, first + timedelta(hours=72)),
            confirmed_like("a2", "p1", second),
            event(
                "feedback_episode_extended",
                second,
                episode_id=ep,
                touch_action_id="a2",
                previous_expires_at=(first + timedelta(hours=72)).isoformat(),
                expires_at=(second + timedelta(hours=72)).isoformat(),
            ),
            received("mine-1", "p1", feedback),
            success(feedback, ep, "mine-1"),
        ]
        state = rebuild_state([run(events)], NOW)

        self.assertEqual(matured_cohort_counts(state, NOW), (1, 2))
        self.assertEqual(matured_cohort_kpi(state, NOW), 50.0)

    def test_open_episode_is_excluded_from_kpi(self):
        t0 = NOW - timedelta(hours=24)
        ep = episode_id("p1", "a1")
        state = rebuild_state(
            [run([confirmed_like("a1", "p1", t0), opened(ep, "p1", "a1", t0, t0 + timedelta(hours=72))])],
            NOW,
        )

        self.assertEqual(matured_cohort_counts(state, NOW), (0, 0))
        self.assertIsNone(matured_cohort_kpi(state, NOW))

    def test_extended_episode_is_not_mature_until_latest_expiry(self):
        first = NOW - timedelta(hours=73)
        second = NOW - timedelta(hours=70)
        ep = episode_id("p1", "a1")
        events = [
            confirmed_like("a1", "p1", first),
            opened(ep, "p1", "a1", first, first + timedelta(hours=72)),
            confirmed_like("a2", "p1", second),
            event(
                "feedback_episode_extended",
                second,
                episode_id=ep,
                touch_action_id="a2",
                previous_expires_at=(first + timedelta(hours=72)).isoformat(),
                expires_at=(second + timedelta(hours=72)).isoformat(),
            ),
        ]

        state = rebuild_state([run(events)], NOW)

        self.assertEqual(matured_cohort_counts(state, NOW), (0, 0))
        self.assertIsNone(matured_cohort_kpi(state, NOW))

    def test_latest_run_status_is_chronological_not_input_order(self):
        older = RunLog(
            1,
            "older",
            "2026-08-12",
            "run",
            "paused_incomplete",
            dt(12, 8),
            dt(12, 9),
            (),
        )
        newer = RunLog(
            1,
            "newer",
            "2026-08-12",
            "run",
            "incomplete_candidate_exhausted",
            dt(12, 10),
            dt(12, 11),
            (),
        )

        state = rebuild_state([newer, older], NOW)

        self.assertEqual(state.daily_tasks["2026-08-12"].status, "incomplete_candidate_exhausted")

    def test_two_recent_one_point_successes_are_promising(self):
        events = []
        for index, days in enumerate((20, 5), start=1):
            touch = NOW - timedelta(days=days)
            action = f"a{index}"
            ep = episode_id("p1", action)
            feedback = touch + timedelta(hours=1)
            events.extend([
                confirmed_like(action, "p1", touch),
                opened(ep, "p1", action, touch, touch + timedelta(hours=72)),
                received(f"mine-{index}", "p1", feedback),
                success(feedback, ep, f"mine-{index}"),
            ])
        state = rebuild_state([run(events)], NOW)

        self.assertEqual(state.photographers["p1"].feedback_points_30d, 2)
        self.assertEqual(classify_photographer(state.photographers["p1"], NOW), "promising")

    def test_dormant_cooldown_retest_boundary(self):
        events = []
        for index, days in enumerate((20, 15, 6), start=1):
            touch = NOW - timedelta(days=days)
            action = f"a{index}"
            ep = episode_id("p1", action)
            expiry = touch + timedelta(hours=72)
            events.extend([
                confirmed_like(action, "p1", touch),
                opened(ep, "p1", action, touch, expiry),
                failed(expiry, ep, expiry),
            ])
        state = rebuild_state([run(events)], NOW)
        stats = state.photographers["p1"]

        self.assertEqual(classify_photographer(stats, NOW), "dormant")
        self.assertFalse(stats.dormant_retest_eligible)
        later = NOW + timedelta(days=1)
        rebuilt = rebuild_state([run(events)], later)
        self.assertTrue(rebuilt.photographers["p1"].dormant_retest_eligible)

    def test_baseline_high_potential_prior_is_not_success(self):
        before = NOW - timedelta(days=40)
        events = [
            received("mine-2", "p1", before, position=2),
            received("mine-9", "p1", before + timedelta(minutes=1), position=9),
        ]
        state = rebuild_state([run(events)], NOW)
        stats = state.photographers["p1"]

        self.assertTrue(stats.historical_high_potential)
        self.assertEqual(stats.success_count_30d, 0)
        self.assertEqual(beta_parameters(stats, NOW), (1.0, 1.0))

    def test_legacy_success_caps_at_three_points_and_open_is_negative(self):
        success_touch = NOW - timedelta(days=5)
        open_touch = NOW - timedelta(days=1)
        success_episode = episode_id("p1", "a1")
        open_episode = episode_id("p1", "a2")
        feedback = success_touch + timedelta(hours=1)
        events = [
            confirmed_like("a1", "p1", success_touch),
            opened(success_episode, "p1", "a1", success_touch, success_touch + timedelta(hours=72)),
            received("mine-1", "p1", feedback),
            success(feedback, success_episode, "mine-1", count=5),
            confirmed_like("a2", "p1", open_touch),
            opened(open_episode, "p1", "a2", open_touch, open_touch + timedelta(hours=72)),
        ]

        state = rebuild_state([run(events)], NOW)

        self.assertEqual(state.touch_feedback["a1"].feedback_points, 3)
        self.assertFalse(state.touch_feedback["a1"].unanswered)
        self.assertTrue(state.touch_feedback["a2"].unanswered)
        self.assertEqual(state.photographers["p1"].raw_feedback_points, 3)

    def test_effective_feedback_points_cap_at_twelve(self):
        events = []
        for index in range(5):
            touch = NOW - timedelta(hours=10 - index)
            action = f"a{index}"
            ep = episode_id("p1", action)
            feedback = touch + timedelta(minutes=10)
            events.extend(
                [
                    confirmed_like(action, "p1", touch),
                    opened(ep, "p1", action, touch, touch + timedelta(hours=72)),
                    received(f"mine-{index}", "p1", feedback),
                    success(feedback, ep, f"mine-{index}", count=3),
                ]
            )

        state = rebuild_state([run(events)], NOW)
        stats = state.photographers["p1"]

        self.assertEqual(stats.raw_feedback_points, 15)
        self.assertEqual(stats.effective_feedback_points, 12.0)
        self.assertEqual(beta_parameters(stats, NOW)[0], 13.0)
