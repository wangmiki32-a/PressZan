from collections import Counter
from datetime import datetime
import random
from typing import Dict, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from .analytics import beta_parameters
from .model import AggregateState, Candidate, SelectionResult


DAILY_PHOTOGRAPHER_TARGET = 200
DAILY_TARGET = DAILY_PHOTOGRAPHER_TARGET
MIN_UNIQUE = DAILY_PHOTOGRAPHER_TARGET
QUOTAS = {"exploit_first": 120, "new": 60, "retest": 20}
PRIMARY_BUCKET_ORDER = ("exploit_first", "new", "retest")
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _today(now: datetime) -> str:
    return now.astimezone(SHANGHAI).date().isoformat()


def _candidate_bucket(candidate: Candidate) -> Optional[str]:
    if candidate.tier == "dormant":
        return "retest" if candidate.is_retest else None
    if candidate.tier in {"verified", "promising"}:
        return "exploit_first"
    return "new"


def _ranked(
    candidates: Sequence[Candidate],
    scores: Mapping[str, float],
) -> List[Candidate]:
    remaining = list(candidates)
    ranked = []
    while remaining:
        highest = max(scores[item.photographer_id] for item in remaining)
        close = [item for item in remaining if highest - scores[item.photographer_id] <= 0.05]
        chosen = min(close, key=lambda item: (item.page_order, item.photographer_id))
        ranked.append(chosen)
        remaining.remove(chosen)
    return ranked


def _selection(candidate: Candidate, bucket: str, score: float, ordinal: int) -> Mapping[str, object]:
    label = {
        "exploit_first": "利用已验证或高潜关系",
        "retest": "复测已有失败或冷却结束关系",
        "new": "探索新摄影师",
    }[bucket]
    return {
        "photographer_id": candidate.photographer_id,
        "display_name": candidate.display_name,
        "profile_url": candidate.profile_url,
        "source_photo_id": candidate.source_photo_id,
        "source_url": candidate.source_url,
        "page_order": candidate.page_order,
        "tier": candidate.tier,
        "bucket": bucket,
        "sampled_score": round(score, 8),
        "daily_ordinal": ordinal,
        "reason": label,
    }


def select_run_candidates(
    candidates: Sequence[Candidate],
    state: AggregateState,
    now: datetime,
    seed: int,
    limit: int = DAILY_TARGET,
) -> SelectionResult:
    day = _today(now)
    daily = state.daily_tasks.get(day)
    existing_ids = set(getattr(daily, "covered_photographer_ids", ()) if daily else ())
    existing_total = len(existing_ids)
    existing_quota = Counter(daily.quota_counts if daily else {})
    existing_per_photographer = Counter({identifier: 1 for identifier in existing_ids})

    remaining_daily = max(0, DAILY_TARGET - existing_total)
    requested = min(max(0, limit), remaining_daily)
    if requested == 0:
        return SelectionResult((), "daily_complete", 0, len(existing_ids))

    unique_candidates: Dict[str, Candidate] = {}
    for candidate in candidates:
        if existing_per_photographer[candidate.photographer_id] >= 1:
            continue
        current = unique_candidates.get(candidate.photographer_id)
        if current is None or candidate.page_order < current.page_order:
            unique_candidates[candidate.photographer_id] = candidate

    rng = random.Random(seed)
    scores = {}
    for identifier, candidate in unique_candidates.items():
        stats = state.photographers.get(identifier)
        alpha, beta = beta_parameters(stats, now) if stats else (1.0, 1.0)
        scores[identifier] = rng.betavariate(alpha, beta)

    selected: List[Mapping[str, object]] = []
    selected_counts = Counter(existing_per_photographer)
    selected_ids = set(existing_ids)

    def add_from(pool: Sequence[Candidate], bucket: str, count: int) -> None:
        if count <= 0:
            return
        for candidate in _ranked(pool, scores):
            if len(selected) >= requested or count <= 0:
                break
            identifier = candidate.photographer_id
            if selected_counts[identifier] != 0:
                continue
            selected_counts[identifier] += 1
            selected_ids.add(identifier)
            selected.append(_selection(candidate, bucket, scores[identifier], selected_counts[identifier]))
            count -= 1

    pools = {"exploit_first": [], "retest": [], "new": []}
    for candidate in unique_candidates.values():
        if existing_per_photographer[candidate.photographer_id] == 0:
            bucket = _candidate_bucket(candidate)
            if bucket is not None:
                pools[bucket].append(candidate)

    for bucket in PRIMARY_BUCKET_ORDER:
        deficit = max(0, QUOTAS[bucket] - existing_quota[bucket])
        add_from(pools[bucket], bucket, deficit)

    if len(selected) < requested:
        unselected_first = [
            candidate
            for candidate in unique_candidates.values()
            if selected_counts[candidate.photographer_id] == 0
        ]
        for bucket in PRIMARY_BUCKET_ORDER:
            add_from(
                [candidate for candidate in unselected_first if _candidate_bucket(candidate) == bucket],
                bucket,
                requested - len(selected),
            )

    projected_total = existing_total + len(selected)
    remaining_after = max(0, DAILY_TARGET - projected_total)
    if projected_total >= DAILY_TARGET and len(selected_ids) >= MIN_UNIQUE:
        status = "daily_complete"
    elif len(selected) < requested:
        status = "incomplete_candidate_exhausted"
    else:
        status = "ready"
    return SelectionResult(tuple(selected), status, remaining_after, len(selected_ids))
