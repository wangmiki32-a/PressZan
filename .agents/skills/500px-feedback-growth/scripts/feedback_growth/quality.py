from dataclasses import dataclass
from statistics import median
from typing import List, Optional, Sequence, Tuple

from .model import AggregateState, RunLog


TARGET_COUNT = 200


@dataclass(frozen=True)
class ExecutionEfficiency:
    daily_task_id: str
    gate_status: str
    gate_reasons: Tuple[str, ...]
    total_minutes: Optional[float]
    covered_per_minute: Optional[float]
    speed_score: Optional[float]
    first_pass_score: Optional[float]
    first_preview_fill_score: Optional[float]
    efficiency_score: Optional[float]
    rework_count: Optional[int]
    first_preview_count: Optional[int]
    target_count: int = TARGET_COUNT


@dataclass(frozen=True)
class _ScorableBatch:
    daily_task_id: str
    ended_at: object
    total_minutes: float
    covered_per_minute: float
    rework_count: int
    first_preview_count: int


def _empty(daily_task_id: str, status: str, *reasons: str) -> ExecutionEfficiency:
    return ExecutionEfficiency(
        daily_task_id=daily_task_id,
        gate_status=status,
        gate_reasons=tuple(reasons),
        total_minutes=None,
        covered_per_minute=None,
        speed_score=None,
        first_pass_score=None,
        first_preview_fill_score=None,
        efficiency_score=None,
        rework_count=None,
        first_preview_count=None,
    )


def _execution_run(logs: Sequence[RunLog], daily_task_id: str) -> Optional[RunLog]:
    matches = [item for item in logs if item.mode == "run" and item.daily_task_id == daily_task_id]
    return max(matches, key=lambda item: (item.ended_at, item.run_id), default=None)


def _approved_preview_id(run: RunLog) -> Optional[str]:
    approvals = [
        str(item.data["preview_id"])
        for item in run.events
        if item.kind == "onboarding_approved" and item.data.get("preview_id")
    ]
    return approvals[-1] if approvals else None


def _associated_preflight(
    logs: Sequence[RunLog],
    run: RunLog,
    preview_id: str,
) -> Optional[RunLog]:
    matches = []
    for item in logs:
        if item.mode != "preflight" or item.daily_task_id != run.daily_task_id or item.status != "completed":
            continue
        if item.ended_at > run.started_at:
            continue
        preview_ids = {
            str(event.data["preview_id"])
            for event in item.events
            if event.kind == "preview_created" and event.data.get("preview_id")
        }
        if preview_id in preview_ids:
            matches.append(item)
    return max(matches, key=lambda item: (item.ended_at, item.run_id), default=None)


def _coverage_ids(run: RunLog) -> set:
    return {
        str(item.data["photographer_id"])
        for item in run.events
        if item.kind in {"outgoing_like_confirmed", "candidate_skipped"}
        and item.data.get("photographer_id")
    }


def _raw_batch(
    logs: Sequence[RunLog],
    state: AggregateState,
    daily_task_id: str,
) -> Tuple[Optional[_ScorableBatch], ExecutionEfficiency]:
    run = _execution_run(logs, daily_task_id)
    if run is None:
        return None, _empty(daily_task_id, "unscorable", "execution_run_not_found")

    hard_reasons: List[str] = []
    if run.status != "completed":
        hard_reasons.append("run_not_completed")
    coverage_count = len(_coverage_ids(run))
    daily = state.daily_tasks.get(daily_task_id)
    if coverage_count != TARGET_COUNT or daily is None or len(daily.covered_photographer_ids) != TARGET_COUNT:
        hard_reasons.append("coverage_not_200")
    if any(item.kind == "safety_paused" for item in run.events):
        hard_reasons.append("safety_paused")
    if hard_reasons:
        return None, _empty(daily_task_id, "blocked", *hard_reasons)

    likes = [item for item in run.events if item.kind == "outgoing_like_confirmed"]
    if not likes or any(item.data.get("settlement_mode") != "immediate" for item in likes):
        return None, _empty(daily_task_id, "unscorable", "not_immediate_settlement")

    preview_id = _approved_preview_id(run)
    if preview_id is None:
        return None, _empty(daily_task_id, "unscorable", "approved_preview_not_found")
    preflight = _associated_preflight(logs, run, preview_id)
    if preflight is None:
        return None, _empty(daily_task_id, "unscorable", "approved_preflight_not_found")

    previews = [item for item in preflight.events if item.kind == "preview_created"]
    if not previews or not isinstance(previews[0].data.get("candidate_plan"), list):
        return None, _empty(daily_task_id, "unscorable", "first_preview_candidates_not_found")

    preflight_minutes = (preflight.ended_at - preflight.started_at).total_seconds() / 60.0
    run_minutes = (run.ended_at - run.started_at).total_seconds() / 60.0
    total_minutes = preflight_minutes + run_minutes
    if total_minutes <= 0:
        return None, _empty(daily_task_id, "unscorable", "invalid_duration")

    approval_rejected_count = sum(
        item.mode == "preflight"
        and item.daily_task_id == daily_task_id
        and item.status == "approval_rejected"
        and item.ended_at <= run.started_at
        for item in logs
    )
    rework_count = (
        max(len(previews) - 1, 0)
        + sum(item.kind == "scan_issue" for item in preflight.events)
        + approval_rejected_count
    )
    raw = _ScorableBatch(
        daily_task_id=daily_task_id,
        ended_at=run.ended_at,
        total_minutes=total_minutes,
        covered_per_minute=TARGET_COUNT / total_minutes,
        rework_count=rework_count,
        first_preview_count=len(previews[0].data["candidate_plan"]),
    )
    return raw, _empty(daily_task_id, "pass")


def _score(raw: _ScorableBatch, history: Sequence[_ScorableBatch]) -> ExecutionEfficiency:
    previous = sorted(
        (item for item in history if item.ended_at < raw.ended_at),
        key=lambda item: (item.ended_at, item.daily_task_id),
    )[-5:]
    if previous:
        baseline_speed = median(item.covered_per_minute for item in previous)
        speed_score = max(0.0, min(100.0, 80.0 + 100.0 * (raw.covered_per_minute / baseline_speed - 1.0)))
    else:
        speed_score = 80.0
    first_pass_score = max(100.0 - 10.0 * raw.rework_count, 0.0)
    fill_score = min(raw.first_preview_count / TARGET_COUNT * 100.0, 100.0)
    efficiency_score = 0.5 * speed_score + 0.3 * first_pass_score + 0.2 * fill_score
    return ExecutionEfficiency(
        daily_task_id=raw.daily_task_id,
        gate_status="pass",
        gate_reasons=(),
        total_minutes=round(raw.total_minutes, 3),
        covered_per_minute=round(raw.covered_per_minute, 3),
        speed_score=round(speed_score, 1),
        first_pass_score=round(first_pass_score, 1),
        first_preview_fill_score=round(fill_score, 1),
        efficiency_score=round(efficiency_score, 1),
        rework_count=raw.rework_count,
        first_preview_count=raw.first_preview_count,
    )


def _eligible_batches(logs: Sequence[RunLog], state: AggregateState) -> List[_ScorableBatch]:
    task_ids = sorted({item.daily_task_id for item in logs if item.mode == "run"})
    batches = []
    for task_id in task_ids:
        raw, gate = _raw_batch(logs, state, task_id)
        if raw is not None and gate.gate_status == "pass":
            batches.append(raw)
    return sorted(batches, key=lambda item: (item.ended_at, item.daily_task_id))


def build_execution_efficiency(
    logs: Sequence[RunLog],
    state: AggregateState,
    daily_task_id: str,
) -> ExecutionEfficiency:
    raw, gate = _raw_batch(logs, state, daily_task_id)
    if raw is None:
        return gate
    history = [item for item in _eligible_batches(logs, state) if item.daily_task_id != daily_task_id]
    return _score(raw, history)


def build_execution_efficiency_trend(
    logs: Sequence[RunLog],
    state: AggregateState,
    limit: int = 5,
) -> Tuple[ExecutionEfficiency, ...]:
    batches = _eligible_batches(logs, state)
    scored = [_score(item, batches) for item in batches]
    return tuple(scored[-max(limit, 0):])
