from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests import bootstrap  # noqa: F401
from feedback_growth.dashboard import build_dashboard_view_model, render_dashboard
from feedback_growth.model import AggregateState, DailyTaskStats, FeedbackScan, OutgoingTouch, PhotographerStats
from tests.helpers import dt


TEMPLATE = Path(__file__).parents[1] / ".agents" / "skills" / "500px-feedback-growth" / "assets" / "dashboard.html"


def daily(day, likes, unique, *, completed_at=None, comments=0, covered=None, quotas=None):
    covered = unique if covered is None else covered
    return DailyTaskStats(
        daily_task_id=day,
        confirmed_likes=likes,
        unique_photographer_ids=frozenset(f"p{index}" for index in range(unique)),
        quota_counts=quotas or {"exploit_first": min(likes, unique)},
        confirmed_comments=comments,
        status="completed" if completed_at else "in_progress",
        completed_at=completed_at,
        reinforcement_likes=max(0, likes - unique),
        skip_counts={"already_liked": max(0, covered - likes)},
        risk_events=(),
        covered_photographer_ids=frozenset(f"covered-{index}" for index in range(covered)),
    )


def photographer(identifier, *, raw, recent, effective, touches, unanswered, last_feedback):
    return PhotographerStats(
        photographer_id=identifier,
        display_name=identifier,
        profile_url=f"https://example.test/{identifier}",
        baseline_work_ids=frozenset(),
        baseline_work_positions={},
        historical_high_potential=False,
        episodes=(),
        eligible_episodes=(),
        last_comment_at=None,
        today_like_photo_ids=(),
        success_count_30d=1 if recent else 0,
        failure_count=unanswered,
        dormant_retest_eligible=False,
        raw_feedback_points=raw,
        feedback_points_30d=recent,
        touch_count=touches,
        touch_count_30d=touches,
        unanswered_touch_count_30d=unanswered,
        effective_feedback_points=effective,
        effective_unanswered_touches=float(unanswered),
        last_feedback_at=last_feedback,
        last_unanswered_touch_at=None,
    )


def state_with(*, tasks=(), photographers=None, touches=(), scans=(), paused_reason=None):
    return AggregateState(
        photographers=photographers or {},
        known_received_like_pairs=frozenset(),
        daily_tasks={task.daily_task_id: task for task in tasks},
        paused_reason=paused_reason,
        episodes={},
        outgoing_touches=tuple(touches),
        feedback_scans=tuple(scans),
    )


class DashboardTest(unittest.TestCase):
    def test_view_model_keeps_explicit_cross_day_task_without_coverage(self):
        task = daily("2026-08-19", 0, 0)

        view = build_dashboard_view_model(
            state_with(tasks=(task,)),
            dt(20, 1),
            current_daily_task_id="2026-08-19",
        )

        self.assertEqual(view["current_task"]["daily_task_id"], "2026-08-19")
        self.assertEqual(view["current_task"]["covered_photographers"], 0)
        self.assertEqual(view["current_task"]["status"], "in_progress")

    def test_view_model_has_immediate_feedback_sections_and_allocation(self):
        now = dt(19, 12)
        task = daily(
            "2026-08-19",
            150,
            150,
            covered=200,
            completed_at=now,
            comments=149,
            quotas={"exploit_first": 130, "new": 50, "retest": 20},
        )

        view = build_dashboard_view_model(state_with(tasks=(task,)), now)

        self.assertEqual(
            set(view),
            {
                "generated_at",
                "current_task",
                "latest_feedback_scan",
                "performance_30d",
                "tier_distribution",
                "relationship_ranking",
                "strategy_allocation",
                "history_tabs",
            },
        )
        self.assertEqual(view["current_task"]["covered_photographers"], 200)
        self.assertEqual(view["current_task"]["skipped"], 50)
        self.assertEqual(view["strategy_allocation"]["planned"], {"exploit_first": 120, "new": 60, "retest": 20})
        self.assertEqual(view["strategy_allocation"]["actual"]["exploit_first"], 130)
        self.assertNotIn("cycle", view)
        self.assertNotIn("latency_buckets", view)

    def test_incomplete_scan_is_not_rendered_as_zero_feedback(self):
        now = dt(19, 12)
        scan = FeedbackScan(
            "scan-3",
            now,
            ("mine-1", "mine-2", "mine-3"),
            frozenset({"mine-1", "mine-2"}),
            frozenset({"mine-1", "mine-2"}),
            0,
            0,
            0,
            frozenset({"mine-3"}),
        )

        latest = build_dashboard_view_model(state_with(scans=(scan,)), now)["latest_feedback_scan"]

        self.assertFalse(latest["complete"])
        self.assertEqual(latest["status"], "数据不完整")
        self.assertEqual(latest["completed_count"], 2)
        self.assertEqual(latest["issues"], ["mine-3"])

    def test_feedback_points_per_100_touches_can_exceed_100(self):
        now = dt(19, 12)
        first = photographer("p1", raw=3, recent=3, effective=3.0, touches=1, unanswered=0, last_feedback=now)
        second = photographer("p2", raw=3, recent=3, effective=3.0, touches=1, unanswered=0, last_feedback=now)
        touches = (
            OutgoingTouch("a1", "p1", "photo-1", now - timedelta(hours=2), None, "exploit_first", "immediate"),
            OutgoingTouch("a2", "p2", "photo-2", now - timedelta(hours=1), None, "new", "immediate"),
        )

        performance = build_dashboard_view_model(
            state_with(photographers={"p1": first, "p2": second}, touches=touches),
            now,
        )["performance_30d"]

        self.assertEqual(performance["touches"], 2)
        self.assertEqual(performance["feedback_points"], 6)
        self.assertEqual(performance["feedback_points_per_100_touches"], 300.0)

    def test_relationship_ranking_uses_effective_then_raw_points(self):
        now = dt(19, 12)
        photographers = {
            "p1": photographer("p1", raw=20, recent=6, effective=8.123456, touches=5, unanswered=0, last_feedback=now),
            "p2": photographer("p2", raw=9, recent=5, effective=9.0, touches=4, unanswered=0, last_feedback=now),
        }

        view = build_dashboard_view_model(state_with(photographers=photographers), now)

        self.assertEqual([item["photographer_id"] for item in view["relationship_ranking"]], ["p2", "p1"])
        self.assertEqual(view["relationship_ranking"][0]["effective_feedback_points"], 9.0)
        self.assertEqual(view["relationship_ranking"][0]["raw_feedback_points"], 9)
        self.assertEqual(view["relationship_ranking"][1]["effective_feedback_points"], 8.123)

    def test_legacy_completed_100_like_task_remains_in_history(self):
        completed_at = dt(11, 15)
        task = daily("2026-08-11", 100, 84, covered=84, completed_at=completed_at, comments=3)

        view = build_dashboard_view_model(state_with(tasks=(task,)), dt(19, 12))

        self.assertEqual(len(view["history_tabs"]), 1)
        self.assertEqual(view["history_tabs"][0]["daily_task_id"], "2026-08-11")
        self.assertEqual(view["history_tabs"][0]["confirmed_likes"], 100)
        self.assertEqual(view["history_tabs"][0]["confirmed_comments"], 3)

    def test_template_keeps_visual_and_accessibility_contract(self):
        template = TEMPLATE.read_text(encoding="utf-8")

        self.assertIn('<html lang="zh-CN" data-theme="light">', template)
        self.assertIn('id="theme-toggle"', template)
        self.assertIn('aria-label="切换明暗主题"', template)
        self.assertIn('aria-live="polite"', template)
        self.assertIn("prefers-reduced-motion: reduce", template)
        self.assertIn("@media (max-width: 720px)", template)
        for section_id in ("current-task", "latest-feedback-scan", "performance-30d", "relationship-tiers", "strategy-allocation"):
            self.assertIn(f'id="{section_id}"', template)
        self.assertNotIn("review_1d", template)
        self.assertNotIn("review_3d", template)
        self.assertNotIn("latency_buckets", template)

    def test_render_is_offline_and_escapes_script_terminator(self):
        now = dt(19, 12)
        malicious_name = "Alice </script><script>alert(1)</script>"
        stats = photographer(malicious_name, raw=3, recent=3, effective=3.0, touches=1, unanswered=0, last_feedback=now)

        with TemporaryDirectory() as directory:
            output = Path(directory) / "dashboard.html"
            render_dashboard(state_with(photographers={malicious_name: stats}), now, TEMPLATE, output)
            html = output.read_text(encoding="utf-8")

        self.assertNotIn("__DASHBOARD_DATA__", html)
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)
        self.assertNotIn("</script><script>alert(1)</script>", html)
        self.assertIn("<\\/script>", html)


if __name__ == "__main__":
    unittest.main()
