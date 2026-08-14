from collections import Counter
from datetime import datetime
import json
from pathlib import Path
from typing import Dict, Mapping

from .analytics import SHANGHAI, classify_photographer, matured_cohort_counts, matured_cohort_kpi
from .model import AggregateState


LATENCY_BUCKETS = (
    ("0-6 小时", 0, 6),
    ("6-24 小时", 6, 24),
    ("24-48 小时", 24, 48),
    ("48-72 小时", 48, 72.000001),
)


def _current_task(state: AggregateState, now: datetime) -> Mapping[str, object]:
    today_id = now.astimezone(SHANGHAI).date().isoformat()
    execution_days = [
        daily
        for daily in state.daily_tasks.values()
        if daily.daily_task_id <= today_id and daily.confirmed_likes > 0
    ]
    daily = max(execution_days, key=lambda item: item.daily_task_id, default=None)
    if daily is None:
        daily = state.daily_tasks.get(today_id)
    if daily is None:
        return {
            "daily_task_id": today_id,
            "confirmed_likes": 0,
            "unique_photographers": 0,
            "confirmed_comments": 0,
            "status": "not_started",
            "pause_reason": state.paused_reason,
            "is_today": True,
        }
    return {
        "daily_task_id": daily.daily_task_id,
        "confirmed_likes": daily.confirmed_likes,
        "unique_photographers": len(daily.unique_photographer_ids),
        "confirmed_comments": daily.confirmed_comments,
        "status": "completed" if daily.confirmed_likes == 100 else daily.status,
        "pause_reason": state.paused_reason,
        "is_today": daily.daily_task_id == today_id,
    }


def _history_tabs(state: AggregateState):
    attributed_by_day = Counter(
        episode.last_touch_at.astimezone(SHANGHAI).date().isoformat()
        for episode in state.episodes.values()
        if episode.outcome == "success" and episode.feedback_first_seen_at is not None
    )
    tabs = []
    for daily in sorted(state.daily_tasks.values(), key=lambda item: item.daily_task_id, reverse=True):
        if daily.confirmed_likes != 100 or daily.completed_at is None:
            continue
        tabs.append(
            {
                "daily_task_id": daily.daily_task_id,
                "completed_at": daily.completed_at.isoformat(),
                "unique_photographers": len(daily.unique_photographer_ids),
                "reinforcement_likes": daily.reinforcement_likes,
                "confirmed_comments": daily.confirmed_comments,
                "attributed_reciprocators": attributed_by_day[daily.daily_task_id],
                "quota_counts": dict(daily.quota_counts),
                "skip_counts": dict(daily.skip_counts),
                "risk_events": list(daily.risk_events),
            }
        )
    return tabs


def _latency_buckets(state: AggregateState, daily_task_id: str):
    counts = Counter()
    for episode in state.episodes.values():
        if episode.outcome != "success" or episode.feedback_first_seen_at is None:
            continue
        if episode.last_touch_at.astimezone(SHANGHAI).date().isoformat() != daily_task_id:
            continue
        hours = (episode.feedback_first_seen_at - episode.last_touch_at).total_seconds() / 3600
        for label, lower, upper in LATENCY_BUCKETS:
            if lower <= hours < upper:
                counts[label] += 1
                break
    return [{"label": label, "count": counts[label]} for label, _lower, _upper in LATENCY_BUCKETS]


def _daily_trend(state: AggregateState):
    feedback_by_day = Counter()
    for episode in state.episodes.values():
        if episode.outcome == "success" and episode.feedback_first_seen_at is not None:
            day = episode.last_touch_at.astimezone(SHANGHAI).date().isoformat()
            feedback_by_day[day] += 1
    days = {
        day
        for day, daily in state.daily_tasks.items()
        if daily.confirmed_likes > 0
    } | set(feedback_by_day)
    return [
        {
            "day": day,
            "confirmed_likes": state.daily_tasks[day].confirmed_likes if day in state.daily_tasks else 0,
            "attributed_reciprocators": feedback_by_day[day],
        }
        for day in sorted(days)
    ]


def _trend_chart(points):
    count = len(points)
    if count == 0:
        kind = "empty"
    elif count == 1:
        kind = "single_day_bars"
    elif count < 8:
        kind = "grouped_bars"
    else:
        kind = "lines"
    return {"kind": kind, "points": points}


def build_dashboard_view_model(state: AggregateState, now: datetime) -> Mapping[str, object]:
    numerator, denominator = matured_cohort_counts(state, now)
    current_task = _current_task(state, now)
    review_day = str(current_task["daily_task_id"])
    tiers: Dict[str, str] = {
        identifier: classify_photographer(stats, now)
        for identifier, stats in state.photographers.items()
    }
    tier_distribution = Counter(tiers.values())
    ranking = []
    for identifier, stats in state.photographers.items():
        if tiers[identifier] != "verified":
            continue
        successes = [episode for episode in stats.episodes if episode.outcome == "success"]
        last_feedback = max(
            (episode.feedback_first_seen_at for episode in successes if episode.feedback_first_seen_at is not None),
            default=None,
        )
        ranking.append(
            {
                "photographer_id": identifier,
                "display_name": stats.display_name,
                "successes_30d": stats.success_count_30d,
                "total_successes": len(successes),
                "last_feedback_at": last_feedback.isoformat() if last_feedback else None,
            }
        )
    ranking.sort(key=lambda item: (-item["successes_30d"], item["display_name"], item["photographer_id"]))
    review_touches = [
        touch
        for touch in state.outgoing_touches
        if touch.occurred_at.astimezone(SHANGHAI).date().isoformat() == review_day
    ]
    review_episodes = [
        episode
        for episode in state.episodes.values()
        if episode.last_touch_at.astimezone(SHANGHAI).date().isoformat() == review_day
    ]
    successful_episodes = sum(episode.outcome == "success" for episode in review_episodes)
    open_episodes = sum(episode.outcome == "open" for episode in review_episodes)
    failed_episodes = sum(episode.outcome == "failure" for episode in review_episodes)
    daily_trend = _daily_trend(state)
    return {
        "generated_at": now.isoformat(),
        "current_task": current_task,
        "kpi": {
            "value": matured_cohort_kpi(state, now),
            "numerator": numerator,
            "denominator": denominator,
        },
        "verified_count": tier_distribution["verified"],
        "daily_trend": daily_trend,
        "trend_chart": _trend_chart(daily_trend),
        "tier_distribution": [
            {"tier": tier, "count": tier_distribution[tier]}
            for tier in ("verified", "promising", "new", "dormant")
        ],
        "cohort_outcomes": {
            "attributed": successful_episodes,
            "open": open_episodes,
            "failed": failed_episodes,
        },
        "latency_buckets": _latency_buckets(state, review_day),
        "verified_ranking": ranking,
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
