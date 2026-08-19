from dataclasses import dataclass, field
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
    transaction_context: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ReviewAttempt:
    attempt: int
    status: str
    due_at: datetime
    automation_id: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    failure_reason: Optional[str]
    observed_photo_ids: FrozenSet[str]


@dataclass(frozen=True)
class ReviewSlot:
    kind: str
    status: str
    due_at: Optional[datetime]
    attempts: Tuple[ReviewAttempt, ...]
    resolved_at: Optional[datetime]


@dataclass(frozen=True)
class CycleObservation:
    photo_id: str
    photographer_id: str
    observed_at: datetime
    observation_ref: str


@dataclass(frozen=True)
class EpisodeEvidence:
    episode_id: str
    photographer_id: str
    outcome: str
    expires_at: datetime
    feedback_first_seen_at: Optional[datetime]
    received_like_count: int
    touch_count: int


@dataclass(frozen=True)
class FeedbackScan:
    scan_id: str
    occurred_at: datetime
    photo_ids: Tuple[str, ...]
    completed_photo_ids: FrozenSet[str]
    baseline_photo_ids: FrozenSet[str]
    new_pair_count: int
    new_feedback_photographer_count: int
    new_feedback_points: int
    issue_photo_ids: FrozenSet[str]


@dataclass(frozen=True)
class TouchFeedbackEvidence:
    action_id: str
    photographer_id: str
    touch_at: datetime
    feedback_points: int
    feedback_first_seen_at: Optional[datetime]
    unanswered: bool
    settlement_mode: str


@dataclass(frozen=True)
class FeedbackCycle:
    cycle_id: str
    attribution_eligible: bool
    showcase_photo_ids: Tuple[str, ...]
    baseline_pairs: FrozenSet[Tuple[str, str]]
    touch_action_ids: Tuple[str, ...]
    review_observations: Tuple[CycleObservation, ...]
    like_completed_at: Optional[datetime]
    review_1d: ReviewSlot
    review_3d: ReviewSlot
    status: str


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
    eligible_episodes: Tuple[EpisodeEvidence, ...]
    last_comment_at: Optional[datetime]
    today_like_photo_ids: Tuple[str, ...]
    success_count_30d: int
    failure_count: int
    dormant_retest_eligible: bool
    raw_feedback_points: int = 0
    feedback_points_30d: int = 0
    touch_count: int = 0
    touch_count_30d: int = 0
    unanswered_touch_count_30d: int = 0
    effective_feedback_points: float = 0.0
    effective_unanswered_touches: float = 0.0
    last_feedback_at: Optional[datetime] = None
    last_unanswered_touch_at: Optional[datetime] = None


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
    skip_counts: Mapping[str, int]
    risk_events: Tuple[Mapping[str, str], ...]
    covered_photographer_ids: FrozenSet[str] = frozenset()


@dataclass(frozen=True)
class OutgoingTouch:
    action_id: str
    photographer_id: str
    photo_id: str
    occurred_at: datetime
    episode_id: Optional[str]
    quota_bucket: str
    settlement_mode: str = "legacy"


@dataclass(frozen=True)
class AggregateState:
    photographers: Mapping[str, PhotographerStats]
    known_received_like_pairs: FrozenSet[Tuple[str, str]]
    daily_tasks: Mapping[str, DailyTaskStats]
    paused_reason: Optional[str]
    episodes: Mapping[str, FeedbackEpisode]
    outgoing_touches: Tuple[OutgoingTouch, ...]
    cycles: Mapping[str, FeedbackCycle] = field(default_factory=dict)
    latest_cycle_id: Optional[str] = None
    feedback_scans: Tuple[FeedbackScan, ...] = ()
    touch_feedback: Mapping[str, TouchFeedbackEvidence] = field(default_factory=dict)
    baselined_photo_ids: FrozenSet[str] = frozenset()


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
