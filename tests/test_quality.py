from datetime import timedelta
import unittest

from tests import bootstrap  # noqa: F401
from feedback_growth.model import AggregateState, DailyTaskStats, Event, RunLog
from feedback_growth.quality import build_execution_efficiency, build_execution_efficiency_trend
from tests.helpers import dt


TARGET = 200


def event(kind, occurred_at, **data):
    return Event(kind=kind, occurred_at=occurred_at, data=data)


def task(day, *, covered=TARGET, likes=1, comments=1, status="completed"):
    covered_ids = frozenset(f"{day}-p-{index}" for index in range(covered))
    return DailyTaskStats(
        daily_task_id=day,
        confirmed_likes=likes,
        unique_photographer_ids=frozenset(list(covered_ids)[:likes]),
        quota_counts={"exploit_first": min(covered, 120), "new": min(max(covered - 120, 0), 60), "retest": max(covered - 180, 0)},
        confirmed_comments=comments,
        status=status,
        completed_at=dt(int(day[-2:]), 13) if status == "completed" else None,
        reinforcement_likes=0,
        skip_counts={"already_liked": max(covered - likes, 0)},
        risk_events=(),
        covered_photographer_ids=covered_ids,
    )


def state(*tasks):
    return AggregateState(
        photographers={},
        known_received_like_pairs=frozenset(),
        daily_tasks={item.daily_task_id: item for item in tasks},
        paused_reason=None,
        episodes={},
        outgoing_touches=(),
    )


def preflight(day, *, minutes=10, first_count=197, preview_count=2, scan_issues=0, run_id=None):
    day_number = int(day[-2:])
    started = dt(day_number, 12)
    events = []
    for index in range(preview_count):
        count = first_count if index == 0 else TARGET
        events.append(
            event(
                "preview_created",
                started + timedelta(minutes=index + 1),
                preview_id=f"{day}-preview-{index + 1}",
                candidate_plan=[{"photographer_id": f"candidate-{item}"} for item in range(count)],
            )
        )
    for index in range(scan_issues):
        events.append(event("scan_issue", started + timedelta(minutes=2), photo_id=f"issue-{index}"))
    return RunLog(
        1,
        run_id or f"{day}-preflight",
        day,
        "preflight",
        "completed",
        started,
        started + timedelta(minutes=minutes),
        tuple(events),
    )


def execution_run(day, *, minutes=46, covered=TARGET, status="completed", safety_paused=False, legacy=False):
    day_number = int(day[-2:])
    started = dt(day_number, 12, 10)
    events = [
        event(
            "onboarding_approved",
            started,
            preview_id=f"{day}-preview-2",
        )
    ]
    if covered:
        events.append(
            event(
                "outgoing_like_confirmed",
                started + timedelta(minutes=1),
                photographer_id=f"{day}-p-0",
                settlement_mode="legacy" if legacy else "immediate",
            )
        )
    for index in range(1, covered):
        events.append(
            event(
                "candidate_skipped",
                started + timedelta(minutes=1),
                photographer_id=f"{day}-p-{index}",
                reason="already_liked",
            )
        )
    if safety_paused:
        events.append(event("safety_paused", started + timedelta(minutes=2), reason="captcha"))
    return RunLog(
        1,
        f"{day}-run",
        day,
        "run",
        status,
        started,
        started + timedelta(minutes=minutes),
        tuple(events),
    )


class ExecutionEfficiencyTest(unittest.TestCase):
    def test_first_eligible_batch_uses_baseline_speed_and_scores_86_7(self):
        day = "2026-08-19"
        result = build_execution_efficiency(
            [execution_run(day), preflight(day)],
            state(task(day)),
            day,
        )

        self.assertEqual(result.gate_status, "pass")
        self.assertEqual(result.speed_score, 80.0)
        self.assertEqual(result.first_preview_count, 197)
        self.assertEqual(result.first_preview_fill_score, 98.5)
        self.assertEqual(result.rework_count, 1)
        self.assertEqual(result.first_pass_score, 90.0)
        self.assertEqual(result.total_minutes, 56.0)
        self.assertEqual(result.efficiency_score, 86.7)

    def test_twenty_percent_speed_gain_scores_100(self):
        baseline_day = "2026-08-18"
        current_day = "2026-08-19"
        logs = [
            preflight(baseline_day, minutes=10),
            execution_run(baseline_day, minutes=50),
            preflight(current_day, minutes=10),
            execution_run(current_day, minutes=40),
        ]

        result = build_execution_efficiency(
            logs,
            state(task(baseline_day), task(current_day)),
            current_day,
        )

        self.assertEqual(result.speed_score, 100.0)

    def test_rework_includes_preview_scan_and_rejected_approval(self):
        day = "2026-08-19"
        rejected = RunLog(1, "rejected", day, "preflight", "approval_rejected", dt(19, 11), dt(19, 11, 1), ())
        result = build_execution_efficiency(
            [rejected, preflight(day, preview_count=3, scan_issues=2), execution_run(day)],
            state(task(day)),
            day,
        )

        self.assertEqual(result.rework_count, 5)
        self.assertEqual(result.first_pass_score, 50.0)

    def test_hard_gate_failures_do_not_produce_score(self):
        cases = (
            (execution_run("2026-08-19", status="in_progress"), task("2026-08-19", status="in_progress"), "run_not_completed"),
            (execution_run("2026-08-20", covered=199), task("2026-08-20", covered=199), "coverage_not_200"),
            (execution_run("2026-08-21", covered=201), task("2026-08-21", covered=201), "coverage_not_200"),
            (execution_run("2026-08-22", safety_paused=True), task("2026-08-22"), "safety_paused"),
        )
        for run, daily, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                result = build_execution_efficiency(
                    [preflight(daily.daily_task_id), run],
                    state(daily),
                    daily.daily_task_id,
                )
                self.assertEqual(result.gate_status, "blocked")
                self.assertIn(expected_reason, result.gate_reasons)
                self.assertIsNone(result.efficiency_score)

    def test_missing_preflight_is_unscorable(self):
        day = "2026-08-19"
        result = build_execution_efficiency([execution_run(day)], state(task(day)), day)

        self.assertEqual(result.gate_status, "unscorable")
        self.assertIn("approved_preflight_not_found", result.gate_reasons)
        self.assertIsNone(result.efficiency_score)

    def test_legacy_run_remains_parseable_but_is_not_a_baseline(self):
        day = "2026-08-19"
        result = build_execution_efficiency(
            [preflight(day), execution_run(day, legacy=True)],
            state(task(day)),
            day,
        )

        self.assertEqual(result.gate_status, "unscorable")
        self.assertIn("not_immediate_settlement", result.gate_reasons)

    def test_trend_returns_latest_five_in_deterministic_order(self):
        days = [f"2026-08-{day:02d}" for day in range(15, 22)]
        logs = []
        tasks = []
        for index, day in enumerate(reversed(days)):
            logs.extend((execution_run(day, minutes=40 + index), preflight(day)))
            tasks.append(task(day))

        trend = build_execution_efficiency_trend(logs, state(*tasks), limit=5)

        self.assertEqual([item.daily_task_id for item in trend], days[-5:])
        self.assertTrue(all(item.gate_status == "pass" for item in trend))


if __name__ == "__main__":
    unittest.main()
