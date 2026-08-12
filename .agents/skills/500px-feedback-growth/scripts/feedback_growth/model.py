from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, FrozenSet, Mapping, Optional, Tuple


@dataclass(frozen=True)
class Event:
    kind: str
    occurred_at: datetime
    data: Dict[str, Any]


@dataclass(frozen=True)
class RunLog:
    schema_version: int
    run_id: str
    daily_task_id: str
    mode: str
    status: str
    started_at: datetime
    ended_at: datetime
    events: Tuple[Event, ...]


@dataclass(frozen=True)
class CheckpointHeader:
    schema_version: int
    run_id: str
    daily_task_id: str
    mode: str
    started_at: datetime
    approve_preview_id: Optional[str]


@dataclass(frozen=True)
class Checkpoint:
    header: CheckpointHeader
    events: Tuple[Event, ...]


@dataclass(frozen=True)
class FeedbackEpisode:
    episode_id: str
    photographer_id: str
    touch_action_ids: Tuple[str, ...]
    opened_at: datetime
    last_touch_at: datetime
    expires_at: datetime
    outcome: str
    feedback_first_seen_at: Optional[datetime]
    received_like_count: int


@dataclass(frozen=True)
class PhotographerStats:
    photographer_id: str
    display_name: str
    profile_url: str
    baseline_work_ids: FrozenSet[str]
    baseline_work_positions: Mapping[str, int]
    historical_high_potential: bool
    episodes: Tuple[FeedbackEpisode, ...]
    last_comment_at: Optional[datetime]
    today_like_photo_ids: Tuple[str, ...]
    success_count_30d: int
    failure_count: int
    dormant_retest_eligible: bool


@dataclass(frozen=True)
class DailyTaskStats:
    daily_task_id: str
    confirmed_likes: int
    unique_photographer_ids: FrozenSet[str]
    quota_counts: Mapping[str, int]
    confirmed_comments: int
    status: str
    completed_at: Optional[datetime]
    reinforcement_likes: int
    new_reciprocator_ids: FrozenSet[str]
    tier_changes: Tuple[Mapping[str, str], ...]
    skip_counts: Mapping[str, int]
    risk_events: Tuple[Mapping[str, str], ...]


@dataclass(frozen=True)
class OutgoingTouch:
    action_id: str
    photographer_id: str
    photo_id: str
    occurred_at: datetime
    episode_id: str
    quota_bucket: str


@dataclass(frozen=True)
class AggregateState:
    photographers: Mapping[str, PhotographerStats]
    known_received_like_pairs: FrozenSet[Tuple[str, str]]
    daily_tasks: Mapping[str, DailyTaskStats]
    paused_reason: Optional[str]
    episodes: Mapping[str, FeedbackEpisode]
    outgoing_touches: Tuple[OutgoingTouch, ...]


@dataclass(frozen=True)
class Candidate:
    photographer_id: str
    display_name: str
    profile_url: str
    source_photo_id: str
    source_url: str
    page_order: int
    tier: str
    is_retest: bool


@dataclass(frozen=True)
class SelectionResult:
    selected: Tuple[Mapping[str, Any], ...]
    status: str
    remaining_daily_quota: int
    projected_unique_count: int
