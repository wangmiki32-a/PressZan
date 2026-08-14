from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .model import (
    CycleObservation,
    EpisodeEvidence,
    FeedbackCycle,
    FeedbackEpisode,
    ReviewAttempt,
    ReviewSlot,
    RunLog,
)


@dataclass
class _Attempt:
    attempt: int
    status: str = "pending"
    due_at: Optional[datetime] = None
    automation_id: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    failure_reason: Optional[str] = None
    observed_photo_ids: set = field(default_factory=set)


@dataclass
class _Cycle:
    cycle_id: str
    attribution_eligible: bool
    started_at: datetime
    showcase_photo_ids: List[str] = field(default_factory=list)
    baseline_pairs: set = field(default_factory=set)
    touch_action_ids: List[str] = field(default_factory=list)
    episode_ids: List[str] = field(default_factory=list)
    observations: List[CycleObservation] = field(default_factory=list)
    like_completed_at: Optional[datetime] = None
    abandoned: bool = False
    baseline_complete: bool = False
    attempts: Dict[str, Dict[int, _Attempt]] = field(
        default_factory=lambda: {"review_1d": {}, "review_3d": {}}
    )


def _parse_time(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed


def _attempt(cycle: _Cycle, kind: str, number: int) -> _Attempt:
    return cycle.attempts[kind].setdefault(number, _Attempt(number))


def _slot(kind: str, values: Mapping[int, _Attempt]) -> ReviewSlot:
    attempts = tuple(
        ReviewAttempt(
            attempt=item.attempt,
            status=item.status,
            due_at=item.due_at,
            automation_id=item.automation_id,
            started_at=item.started_at,
            completed_at=item.completed_at,
            failure_reason=item.failure_reason,
            observed_photo_ids=frozenset(item.observed_photo_ids),
        )
        for item in sorted(values.values(), key=lambda value: value.attempt)
        if item.due_at is not None
    )
    resolved = [item for item in attempts if item.status in {"completed", "superseded"}]
    status = resolved[-1].status if resolved else (attempts[-1].status if attempts else "not_scheduled")
    return ReviewSlot(
        kind=kind,
        status=status,
        due_at=attempts[-1].due_at if attempts else None,
        attempts=attempts,
        resolved_at=resolved[-1].completed_at if resolved else None,
    )


def rebuild_cycles(
    logs: Sequence[RunLog],
    raw_episodes: Mapping[str, FeedbackEpisode],
    now: datetime,
) -> Mapping[str, FeedbackCycle]:
    cycles: Dict[str, _Cycle] = {}
    raw_observations = {}
    ordered = []
    for log in logs:
        for index, item in enumerate(log.events):
            reference = f"{log.run_id}:{index}"
            ordered.append((item.occurred_at, log.run_id, index, reference, item))
            if item.kind == "received_like_observed":
                raw_observations[reference] = CycleObservation(
                    photo_id=str(item.data["photo_id"]),
                    photographer_id=str(item.data["photographer_id"]),
                    observed_at=item.occurred_at,
                    observation_ref=reference,
                )
    ordered.sort(key=lambda row: (row[0], row[1], row[2]))

    for occurred_at, _run_id, _index, _reference, item in ordered:
        if item.kind != "cycle_started":
            continue
        cycle_id = str(item.data["cycle_id"])
        if cycle_id in cycles:
            raise ValueError(f"duplicate cycle_started {cycle_id}")
        cycles[cycle_id] = _Cycle(cycle_id, bool(item.data["attribution_eligible"]), occurred_at)

    for occurred_at, _run_id, _index, reference, item in ordered:
        data = item.data
        if item.kind == "cycle_started":
            continue
        cycle_id = str(data.get("cycle_id", ""))
        if not cycle_id or cycle_id not in cycles:
            continue
        cycle = cycles[cycle_id]
        if item.kind == "cycle_showcase_frozen":
            cycle.showcase_photo_ids = [str(value) for value in data["photo_ids"]]
        elif item.kind == "cycle_baseline_like_observed":
            cycle.baseline_pairs.add((str(data["photo_id"]), str(data["photographer_id"])))
        elif item.kind == "cycle_baseline_completed":
            cycle.baseline_complete = True
        elif item.kind == "cycle_like_completed":
            cycle.touch_action_ids = [str(value) for value in data["touch_action_ids"]]
            cycle.episode_ids = [str(value) for value in data["episode_ids"]]
            cycle.like_completed_at = _parse_time(data["like_completed_at"], "like_completed_at")
        elif item.kind == "cycle_attribution_scope_mapped":
            cycle.attribution_eligible = bool(data["attribution_eligible"])
            cycle.showcase_photo_ids = [str(value) for value in data["showcase_photo_ids"]]
            cycle.touch_action_ids = [str(value) for value in data["touch_action_ids"]]
            cycle.episode_ids = [str(value) for value in data["episode_ids"]]
            for observation_ref in data["observation_refs"]:
                observation = raw_observations.get(str(observation_ref))
                if observation is not None:
                    cycle.observations.append(observation)
        elif item.kind == "review_schedule_requested":
            attempt = _attempt(cycle, str(data["review_kind"]), int(data["attempt"]))
            attempt.due_at = _parse_time(data["due_at"], "due_at")
        elif item.kind == "review_scheduled":
            attempt = _attempt(cycle, str(data["review_kind"]), int(data["attempt"]))
            attempt.automation_id = str(data["automation_id"])
        elif item.kind == "review_started":
            attempt = _attempt(cycle, str(data["review_kind"]), int(data["attempt"]))
            attempt.due_at = _parse_time(data["due_at"], "due_at")
            attempt.started_at = _parse_time(data["started_at"], "started_at")
            attempt.status = "running"
        elif item.kind == "review_photo_observed":
            attempt = _attempt(cycle, str(data["review_kind"]), int(data["attempt"]))
            attempt.observed_photo_ids.add(str(data["photo_id"]))
            observed_at = _parse_time(data["observed_at"], "observed_at")
            for photographer_id in data["photographer_ids"]:
                cycle.observations.append(
                    CycleObservation(
                        photo_id=str(data["photo_id"]),
                        photographer_id=str(photographer_id),
                        observed_at=observed_at,
                        observation_ref=reference,
                    )
                )
        elif item.kind == "review_completed":
            attempt = _attempt(cycle, str(data["review_kind"]), int(data["attempt"]))
            attempt.status = "completed"
            attempt.completed_at = _parse_time(data["completed_at"], "completed_at")
        elif item.kind == "review_failed":
            attempt = _attempt(cycle, str(data["review_kind"]), int(data["attempt"]))
            attempt.status = "failed"
            attempt.failure_reason = str(data["reason"])
            attempt.completed_at = _parse_time(data["failed_at"], "failed_at")
        elif item.kind == "review_superseded":
            attempt = _attempt(cycle, str(data["review_kind"]), int(data["attempt"]))
            attempt.status = "superseded"
            attempt.completed_at = _parse_time(data["superseded_at"], "superseded_at")
        elif item.kind == "cycle_abandoned":
            cycle.abandoned = True

    result = {}
    for cycle_id, value in cycles.items():
        review_1d = _slot("review_1d", value.attempts["review_1d"])
        review_3d = _slot("review_3d", value.attempts["review_3d"])
        if value.abandoned:
            status = "abandoned"
        elif value.like_completed_at is None:
            status = "baseline_ready" if value.baseline_complete else "preparing"
        elif review_3d.status == "completed" and all(
            raw_episodes[episode_id].expires_at <= now
            for episode_id in value.episode_ids
            if episode_id in raw_episodes
        ):
            status = "settled"
        else:
            status = "reviews_scheduled"
        result[cycle_id] = FeedbackCycle(
            cycle_id=cycle_id,
            attribution_eligible=value.attribution_eligible,
            showcase_photo_ids=tuple(value.showcase_photo_ids),
            baseline_pairs=frozenset(value.baseline_pairs),
            touch_action_ids=tuple(value.touch_action_ids),
            review_observations=tuple(value.observations),
            like_completed_at=value.like_completed_at,
            review_1d=review_1d,
            review_3d=review_3d,
            status=status,
        )
    return result


def eligible_episode_evidence(
    cycles: Mapping[str, FeedbackCycle],
    raw_episodes: Mapping[str, FeedbackEpisode],
    photographer_id: str,
    now: datetime,
) -> Tuple[EpisodeEvidence, ...]:
    mapped = {
        episode.episode_id
        for cycle in cycles.values()
        for episode in raw_episodes.values()
        if any(action_id in cycle.touch_action_ids for action_id in episode.touch_action_ids)
    }
    evidence: List[EpisodeEvidence] = []
    for episode in raw_episodes.values():
        if episode.photographer_id != photographer_id or episode.episode_id in mapped:
            continue
        evidence.append(
            EpisodeEvidence(
                episode.episode_id,
                episode.photographer_id,
                episode.outcome,
                episode.expires_at,
                episode.feedback_first_seen_at,
                episode.received_like_count,
                len(episode.touch_action_ids),
            )
        )

    for cycle in cycles.values():
        if not cycle.attribution_eligible or cycle.status == "abandoned":
            continue
        episode_values = [
            episode
            for episode in raw_episodes.values()
            if episode.photographer_id == photographer_id
            and any(action_id in cycle.touch_action_ids for action_id in episode.touch_action_ids)
        ]
        for episode in episode_values:
            observations = {
                (item.photo_id, item.photographer_id): item
                for item in cycle.review_observations
                if item.photographer_id == photographer_id
                and item.photo_id in cycle.showcase_photo_ids
                and (item.photo_id, item.photographer_id) not in cycle.baseline_pairs
                and episode.last_touch_at < item.observed_at <= episode.expires_at
            }
            if observations:
                first_seen = min(item.observed_at for item in observations.values())
                outcome = "success"
            elif cycle.review_3d.status == "completed" and episode.expires_at <= now:
                first_seen = None
                outcome = "failure"
            else:
                first_seen = None
                outcome = "open"
            evidence.append(
                EpisodeEvidence(
                    episode.episode_id,
                    photographer_id,
                    outcome,
                    episode.expires_at,
                    first_seen,
                    len(observations),
                    len(episode.touch_action_ids),
                )
            )
    evidence.sort(key=lambda item: (item.expires_at, item.episode_id))
    return tuple(evidence)


def latest_cycle_id(cycles: Mapping[str, FeedbackCycle]) -> Optional[str]:
    candidates = [cycle for cycle in cycles.values() if cycle.like_completed_at is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item.like_completed_at, item.cycle_id)).cycle_id
