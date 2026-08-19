from collections import Counter
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Mapping

from .analytics import COVERAGE_CONTRACT_START_DAY, SHANGHAI, classify_photographer
from .model import AggregateState


STRATEGY_PLAN = {"exploit_first": 120, "new": 60, "retest": 20}
TIER_ORDER = ("verified", "promising", "dormant", "new")


def _current_daily(state: AggregateState, now: datetime):
    today_id = now.astimezone(SHANGHAI).date().isoformat()
    execution_days = [
        daily
        for daily in state.daily_tasks.values()
        if daily.daily_task_id <= today_id and daily.covered_photographer_ids
    ]
    return max(execution_days, key=lambda item: item.daily_task_id, default=state.daily_tasks.get(today_id))


def _current_task(state: AggregateState, now: datetime) -> Mapping[str, object]:
    today_id = now.astimezone(SHANGHAI).date().isoformat()
    daily = _current_daily(state, now)
    if daily is None:
        return {
            "daily_task_id": today_id,
            "confirmed_likes": 0,
            "unique_photographers": 0,
            "covered_photographers": 0,
            "coverage_target": 200 if today_id >= COVERAGE_CONTRACT_START_DAY else None,
            "confirmed_comments": 0,
            "skipped": 0,
            "skip_counts": {},
            "status": "not_started",
            "pause_reason": state.paused_reason,
            "is_today": True,
        }
    skip_counts = dict(daily.skip_counts)
    return {
        "daily_task_id": daily.daily_task_id,
        "confirmed_likes": daily.confirmed_likes,
        "unique_photographers": len(daily.unique_photographer_ids),
        "covered_photographers": len(daily.covered_photographer_ids),
        "coverage_target": 200 if daily.daily_task_id >= COVERAGE_CONTRACT_START_DAY else None,
        "confirmed_comments": daily.confirmed_comments,
        "skipped": sum(skip_counts.values()),
        "skip_counts": skip_counts,
        "status": daily.status,
        "pause_reason": state.paused_reason,
        "is_today": daily.daily_task_id == today_id,
    }


def _latest_feedback_scan(state: AggregateState) -> Mapping[str, object]:
    scan = max(state.feedback_scans, key=lambda item: (item.occurred_at, item.scan_id), default=None)
    if scan is None:
        return {
            "scan_id": None,
            "occurred_at": None,
            "complete": False,
            "status": "尚未扫描",
            "completed_count": 0,
            "target_count": 3,
            "new_pairs": 0,
            "new_feedback_photographers": 0,
            "feedback_points": 0,
            "issues": [],
            "photo_ids": [],
        }
    completed_count = len(scan.completed_photo_ids)
    complete = completed_count == 3 and not scan.issue_photo_ids
    return {
        "scan_id": scan.scan_id,
        "occurred_at": scan.occurred_at.isoformat(),
        "complete": complete,
        "status": "完整" if complete else "数据不完整",
        "completed_count": completed_count,
        "target_count": 3,
        "new_pairs": scan.new_pair_count,
        "new_feedback_photographers": scan.new_feedback_photographer_count,
        "feedback_points": scan.new_feedback_points,
        "issues": sorted(scan.issue_photo_ids),
        "photo_ids": list(scan.photo_ids),
    }


def _performance_30d(state: AggregateState, now: datetime) -> Mapping[str, object]:
    cutoff = now - timedelta(days=30)
    touches = sum(stats.touch_count_30d for stats in state.photographers.values())
    feedback_points = sum(stats.feedback_points_30d for stats in state.photographers.values())
    covered = {
        touch.photographer_id
        for touch in state.outgoing_touches
        if cutoff <= touch.occurred_at <= now
    }
    feedback_photographers = sum(
        stats.feedback_points_30d > 0 for stats in state.photographers.values()
    )
    unanswered = sum(stats.unanswered_touch_count_30d for stats in state.photographers.values())
    return {
        "touches": touches,
        "covered_photographers": len(covered),
        "feedback_photographers": feedback_photographers,
        "feedback_points": feedback_points,
        "unanswered_touches": unanswered,
        "feedback_points_per_100_touches": None if touches == 0 else round(100 * feedback_points / touches, 1),
    }


def _tier_distribution(state: AggregateState, now: datetime):
    tiers = Counter(classify_photographer(stats, now) for stats in state.photographers.values())
    return [{"tier": tier, "count": tiers[tier]} for tier in TIER_ORDER]


def _relationship_ranking(state: AggregateState, now: datetime):
    ranking = []
    for identifier, stats in state.photographers.items():
        if stats.raw_feedback_points <= 0 and stats.feedback_points_30d <= 0:
            continue
        ranking.append(
            {
                "photographer_id": identifier,
                "display_name": stats.display_name,
                "tier": classify_photographer(stats, now),
                "raw_feedback_points": stats.raw_feedback_points,
                "feedback_points_30d": stats.feedback_points_30d,
                "effective_feedback_points": round(stats.effective_feedback_points, 3),
                "touch_count_30d": stats.touch_count_30d,
                "unanswered_touch_count_30d": stats.unanswered_touch_count_30d,
                "last_feedback_at": stats.last_feedback_at.isoformat() if stats.last_feedback_at else None,
            }
        )
    ranking.sort(
        key=lambda item: (
            -item["effective_feedback_points"],
            -item["raw_feedback_points"],
            item["display_name"],
            item["photographer_id"],
        )
    )
    return ranking


def _strategy_allocation(state: AggregateState, now: datetime) -> Mapping[str, object]:
    daily = _current_daily(state, now)
    actual = {bucket: 0 for bucket in STRATEGY_PLAN}
    if daily is not None:
        for bucket in actual:
            actual[bucket] = int(daily.quota_counts.get(bucket, 0))
    gaps = {bucket: max(0, STRATEGY_PLAN[bucket] - actual[bucket]) for bucket in STRATEGY_PLAN}
    overages = {bucket: max(0, actual[bucket] - STRATEGY_PLAN[bucket]) for bucket in STRATEGY_PLAN}
    return {
        "planned": dict(STRATEGY_PLAN),
        "actual": actual,
        "gaps": gaps,
        "overages": overages,
        "backfill": sum(overages.values()),
    }


def _history_tabs(state: AggregateState):
    tabs = []
    for daily in sorted(state.daily_tasks.values(), key=lambda item: item.daily_task_id, reverse=True):
        if daily.status != "completed" or daily.completed_at is None:
            continue
        tabs.append(
            {
                "daily_task_id": daily.daily_task_id,
                "completed_at": daily.completed_at.isoformat(),
                "unique_photographers": len(daily.unique_photographer_ids),
                "covered_photographers": len(daily.covered_photographer_ids),
                "confirmed_likes": daily.confirmed_likes,
                "reinforcement_likes": daily.reinforcement_likes,
                "confirmed_comments": daily.confirmed_comments,
                "quota_counts": dict(daily.quota_counts),
                "skip_counts": dict(daily.skip_counts),
                "risk_events": list(daily.risk_events),
            }
        )
    return tabs


def build_dashboard_view_model(state: AggregateState, now: datetime) -> Mapping[str, object]:
    return {
        "generated_at": now.isoformat(),
        "current_task": _current_task(state, now),
        "latest_feedback_scan": _latest_feedback_scan(state),
        "performance_30d": _performance_30d(state, now),
        "tier_distribution": _tier_distribution(state, now),
        "relationship_ranking": _relationship_ranking(state, now),
        "strategy_allocation": _strategy_allocation(state, now),
        "history_tabs": _history_tabs(state),
    }


def render_dashboard(
    state: AggregateState,
    now: datetime,
    template_path: Path,
    output_path: Path,
) -> Path:
    template = template_path.read_text(encoding="utf-8")
    if template.count("__DASHBOARD_DATA__") != 1:
        raise ValueError("dashboard template must contain exactly one data placeholder")
    payload = json.dumps(
        build_dashboard_view_model(state, now),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).replace("</", "<\\/")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(template.replace("__DASHBOARD_DATA__", payload), encoding="utf-8")
    return output_path


def generate_dashboard(state_root: Path, state: AggregateState, now: datetime) -> Path:
    skill_root = Path(__file__).resolve().parents[2]
    return render_dashboard(
        state,
        now,
        skill_root / "assets" / "dashboard.html",
        state_root / "dashboard.html",
    )
