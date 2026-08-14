from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests import bootstrap  # noqa: F401
from feedback_growth.analytics import matured_cohort_kpi
from feedback_growth.dashboard import build_dashboard_view_model, render_dashboard
from feedback_growth.model import (
    AggregateState,
    DailyTaskStats,
    FeedbackEpisode,
    OutgoingTouch,
    PhotographerStats,
)
from tests.helpers import dt


TEMPLATE = (
    Path(__file__).parents[1]
    / ".agents"
    / "skills"
    / "500px-feedback-growth"
    / "assets"
    / "dashboard.html"
)


def daily(day, likes, unique, *, completed_at=None, comments=0):
    return DailyTaskStats(
        daily_task_id=day,
        confirmed_likes=likes,
        unique_photographer_ids=frozenset(f"p{index}" for index in range(unique)),
        quota_counts={"exploit_first": min(likes, unique)},
        confirmed_comments=comments,
        status="completed" if completed_at else "active",
        completed_at=completed_at,
        reinforcement_likes=max(0, likes - unique),
        skip_counts={"all_recent_works_liked": 2} if completed_at else {},
        risk_events=({"reason": "rate_limit", "evidence_summary": "slow down"},) if completed_at else (),
    )


def state_with_tasks(*tasks, paused_reason=None):
    return AggregateState(
        photographers={},
        known_received_like_pairs=frozenset(),
        daily_tasks={task.daily_task_id: task for task in tasks},
        paused_reason=paused_reason,
        episodes={},
        outgoing_touches=(),
    )


class DashboardTest(unittest.TestCase):
    def test_template_has_redesign_quality_and_accessibility_contract(self):
        template = TEMPLATE.read_text(encoding="utf-8")

        self.assertIn('id="theme-toggle"', template)
        self.assertIn('aria-label="切换明暗主题"', template)
        self.assertIn('<html lang="zh-CN" data-theme="light">', template)
        self.assertNotIn("prefers-color-scheme: dark", template)
        self.assertNotIn("matchMedia", template)
        self.assertIn("prefers-reduced-motion: reduce", template)
        self.assertIn('role="img"', template)
        self.assertIn('role="tablist"', template)
        self.assertIn('aria-selected=', template)
        self.assertIn('aria-live="polite"', template)
        self.assertIn('data.trend_chart.kind === "empty"', template)
        self.assertIn('"single_day_bars", "grouped_bars"', template)
        self.assertIn("cohort_outcomes", template)
        self.assertNotIn('id="funnel"', template)
        self.assertIn("@media (max-width: 720px)", template)
        self.assertNotIn("class=\"cards\"", template)
        self.assertNotIn("class=\"progress\"", template)
        self.assertNotIn("Feedback intelligence", template)
        self.assertNotIn("—", template)
        self.assertNotIn("–", template)

    def test_partial_task_is_current_header_only(self):
        state = state_with_tasks(daily("2026-08-12", 25, 25))

        view = build_dashboard_view_model(state, dt(12, 12))

        self.assertEqual(view["current_task"]["confirmed_likes"], 25)
        self.assertEqual(view["current_task"]["unique_photographers"], 25)
        self.assertEqual(view["history_tabs"], [])

    def test_review_uses_latest_execution_instead_of_zero_like_current_day(self):
        previous = daily("2026-08-11", 100, 100, completed_at=dt(11, 15))
        current_preflight = daily("2026-08-12", 0, 0)
        state = state_with_tasks(previous, current_preflight)

        view = build_dashboard_view_model(state, dt(12, 12))

        self.assertEqual(view["current_task"]["daily_task_id"], "2026-08-11")
        self.assertEqual(view["current_task"]["confirmed_likes"], 100)
        self.assertEqual(view["current_task"]["unique_photographers"], 100)
        self.assertFalse(view["current_task"]["is_today"])
        self.assertEqual(
            view["daily_trend"],
            [{"day": "2026-08-11", "confirmed_likes": 100, "attributed_reciprocators": 0}],
        )
        self.assertEqual(view["trend_chart"]["kind"], "single_day_bars")

    def test_new_execution_becomes_review_cohort_and_feedback_stays_with_touch_day(self):
        now = dt(12, 12)
        previous_touch_at = dt(11, 15)
        current_touch_at = dt(12, 9)
        previous_episode = FeedbackEpisode(
            "e1", "p1", ("a1",), previous_touch_at, previous_touch_at,
            previous_touch_at + timedelta(hours=72), "success",
            current_touch_at, 1,
        )
        current_episode = FeedbackEpisode(
            "e2", "p2", ("a2",), current_touch_at, current_touch_at,
            current_touch_at + timedelta(hours=72), "open", None, 0,
        )
        state = AggregateState(
            photographers={},
            known_received_like_pairs=frozenset(),
            daily_tasks={
                "2026-08-11": daily("2026-08-11", 100, 100, completed_at=dt(11, 16)),
                "2026-08-12": daily("2026-08-12", 20, 20),
            },
            paused_reason=None,
            episodes={"e1": previous_episode, "e2": current_episode},
            outgoing_touches=(
                OutgoingTouch("a1", "p1", "photo-1", previous_touch_at, "e1", "exploit_first"),
                OutgoingTouch("a2", "p2", "photo-2", current_touch_at, "e2", "new"),
            ),
        )

        view = build_dashboard_view_model(state, now)

        self.assertEqual(view["current_task"]["daily_task_id"], "2026-08-12")
        self.assertEqual(view["current_task"]["confirmed_likes"], 20)
        self.assertTrue(view["current_task"]["is_today"])
        self.assertEqual(
            view["cohort_outcomes"],
            {"attributed": 0, "open": 1, "failed": 0},
        )
        self.assertEqual(
            view["daily_trend"],
            [
                {"day": "2026-08-11", "confirmed_likes": 100, "attributed_reciprocators": 1},
                {"day": "2026-08-12", "confirmed_likes": 20, "attributed_reciprocators": 0},
            ],
        )
        self.assertEqual(view["trend_chart"]["kind"], "grouped_bars")

    def test_eight_execution_days_use_line_chart(self):
        tasks = [daily(f"2026-08-{day:02}", 100, 100, completed_at=dt(day, 15)) for day in range(1, 9)]

        view = build_dashboard_view_model(state_with_tasks(*tasks), dt(12, 12))

        self.assertEqual(view["trend_chart"]["kind"], "lines")

    def test_history_uses_attributed_episodes_and_omits_untracked_tier_changes(self):
        touch_at = dt(11, 15)
        feedback_at = touch_at + timedelta(hours=8)
        episode = FeedbackEpisode(
            "e1", "p1", ("a1",), touch_at, touch_at,
            touch_at + timedelta(hours=72), "success", feedback_at, 1,
        )
        task = daily("2026-08-11", 100, 100, completed_at=dt(11, 16))
        state = AggregateState(
            photographers={},
            known_received_like_pairs=frozenset(),
            daily_tasks={task.daily_task_id: task},
            paused_reason=None,
            episodes={episode.episode_id: episode},
            outgoing_touches=(
                OutgoingTouch("a1", "p1", "photo-1", touch_at, "e1", "exploit_first"),
            ),
        )

        history = build_dashboard_view_model(state, dt(12, 12))["history_tabs"][0]

        self.assertEqual(history["attributed_reciprocators"], 1)
        self.assertNotIn("tier_changes", history)

    def test_only_exactly_100_likes_creates_complete_history_tab(self):
        complete = daily("2026-08-11", 100, 84, completed_at=dt(11, 15), comments=3)
        over_limit = daily("2026-08-10", 101, 85, completed_at=dt(10, 15))
        state = state_with_tasks(complete, over_limit)

        view = build_dashboard_view_model(state, dt(12, 12))

        self.assertEqual(len(view["history_tabs"]), 1)
        tab = view["history_tabs"][0]
        self.assertEqual(tab["daily_task_id"], "2026-08-11")
        self.assertEqual(tab["completed_at"], dt(11, 15).isoformat())
        self.assertEqual(tab["unique_photographers"], 84)
        self.assertEqual(tab["reinforcement_likes"], 16)
        self.assertEqual(tab["confirmed_comments"], 3)
        self.assertEqual(tab["attributed_reciprocators"], 0)
        self.assertNotIn("tier_changes", tab)
        self.assertEqual(tab["quota_counts"]["exploit_first"], 84)
        self.assertEqual(tab["skip_counts"]["all_recent_works_liked"], 2)
        self.assertEqual(tab["risk_events"][0]["reason"], "rate_limit")

    def test_kpi_matches_analytics(self):
        now = dt(12, 12)
        touch_at = now - timedelta(days=5)
        success_episode = FeedbackEpisode(
            "e1", "p1", ("a1",), touch_at, touch_at, touch_at + timedelta(hours=72),
            "success", touch_at + timedelta(hours=8), 1,
        )
        failed_episode = FeedbackEpisode(
            "e2", "p2", ("a2",), touch_at, touch_at, touch_at + timedelta(hours=72),
            "failure", None, 0,
        )
        state = AggregateState(
            photographers={},
            known_received_like_pairs=frozenset(),
            daily_tasks={},
            paused_reason=None,
            episodes={"e1": success_episode, "e2": failed_episode},
            outgoing_touches=(
                OutgoingTouch("a1", "p1", "photo-1", touch_at, "e1", "exploit_first"),
                OutgoingTouch("a2", "p2", "photo-2", touch_at, "e2", "new"),
            ),
        )

        view = build_dashboard_view_model(state, now)

        self.assertEqual(view["kpi"]["value"], matured_cohort_kpi(state, now))
        self.assertEqual(view["kpi"]["numerator"], 1)
        self.assertEqual(view["kpi"]["denominator"], 2)

    def test_render_is_offline_and_escapes_script_terminator(self):
        malicious_name = "Alice </script><script>alert(1)</script>"
        first = FeedbackEpisode(
            "e1", "p1", ("a1",), dt(8), dt(8), dt(11), "success", dt(9), 1,
        )
        second = FeedbackEpisode(
            "e2", "p1", ("a2",), dt(9), dt(9), dt(12), "success", dt(10), 1,
        )
        photographer = PhotographerStats(
            photographer_id="p1",
            display_name=malicious_name,
            profile_url="https://example.test/p1",
            baseline_work_ids=frozenset(),
            baseline_work_positions={},
            historical_high_potential=True,
            episodes=(first, second),
            eligible_episodes=(),
            last_comment_at=None,
            today_like_photo_ids=(),
            success_count_30d=2,
            failure_count=0,
            dormant_retest_eligible=False,
        )
        state = AggregateState(
            photographers={"p1": photographer},
            known_received_like_pairs=frozenset(),
            daily_tasks={},
            paused_reason=None,
            episodes={"e1": first, "e2": second},
            outgoing_touches=(),
        )

        with TemporaryDirectory() as directory:
            output = Path(directory) / "dashboard.html"
            render_dashboard(state, dt(12, 12), TEMPLATE, output)
            html = output.read_text(encoding="utf-8")

        self.assertNotIn("__DASHBOARD_DATA__", html)
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)
        self.assertNotIn("</script><script>alert(1)</script>", html)
        self.assertIn("<\\/script>", html)
        self.assertNotIn("—", html)
        self.assertNotIn("–", html)


if __name__ == "__main__":
    unittest.main()
