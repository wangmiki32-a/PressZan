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
        new_reciprocator_ids=frozenset({"p-new"}) if completed_at else frozenset(),
        tier_changes=({"photographer_id": "p-new", "from": "promising", "to": "verified"},) if completed_at else (),
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
    def test_partial_task_is_current_header_only(self):
        state = state_with_tasks(daily("2026-08-12", 25, 25))

        view = build_dashboard_view_model(state, dt(12, 12))

        self.assertEqual(view["current_task"]["confirmed_likes"], 25)
        self.assertEqual(view["current_task"]["unique_photographers"], 25)
        self.assertEqual(view["history_tabs"], [])

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
        self.assertEqual(tab["new_reciprocators"], 1)
        self.assertEqual(tab["tier_changes"][0]["to"], "verified")
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


if __name__ == "__main__":
    unittest.main()
