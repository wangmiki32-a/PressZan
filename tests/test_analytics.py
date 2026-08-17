from datetime import timedelta
import hashlib
import unittest

from tests import bootstrap  # noqa: F401
from feedback_growth.analytics import (
    StateValidationError,
    beta_parameters,
    classify_photographer,
    matured_cohort_counts,
    matured_cohort_kpi,
    rebuild_state,
)
from feedback_growth.model import RunLog
from tests.helpers import confirmed_like, dt, event, opened, received, run


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
    def test_daily_coverage_unions_confirmed_likes_and_skips_by_photographer(self):
        touched_at = NOW - timedelta(hours=1)
        episode = episode_id("p1", "a1")
        events = [
            confirmed_like("a1", "p1", touched_at),
            opened(episode, "p1", "a1", touched_at, touched_at + timedelta(hours=72)),
            event("candidate_skipped", touched_at + timedelta(minutes=1), photographer_id="p2", reason="already_liked"),
            event("candidate_skipped", touched_at + timedelta(minutes=2), photographer_id="p1", reason="already_liked"),
        ]

        state = rebuild_state([run(events, status="active")], NOW)

        daily = state.daily_tasks["2026-08-12"]
        self.assertEqual(daily.covered_photographer_ids, frozenset({"p1", "p2"}))
        self.assertEqual(daily.confirmed_likes, 1)

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

    def test_two_recent_successes_are_verified(self):
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

        self.assertEqual(classify_photographer(state.photographers["p1"], NOW), "verified")

    def test_dormant_cooldown_retest_boundary(self):
        events = []
        for index, days in enumerate((35, 32), start=1):
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
        later = NOW + timedelta(days=30)
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
        self.assertEqual(beta_parameters(stats, NOW), (1.5, 1.0))
