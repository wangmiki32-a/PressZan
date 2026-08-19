from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import hashlib
from typing import Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from .cycles import eligible_episode_evidence, latest_cycle_id, rebuild_cycles
from .model import (
    AggregateState,
    DailyTaskStats,
    Event,
    FeedbackEpisode,
    FeedbackScan,
    OutgoingTouch,
    PhotographerStats,
    RunLog,
    TouchFeedbackEvidence,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")
WINDOW = timedelta(hours=72)
ROLLING = timedelta(days=30)
COVERAGE_CONTRACT_START_DAY = "2026-08-18"
FEEDBACK_POINT_CAP_PER_TOUCH = 3
EFFECTIVE_FEEDBACK_CAP = 12.0
RETEST_COOLDOWN = timedelta(days=7)


class StateValidationError(ValueError):
    pass


@dataclass
class _Episode:
    episode_id: str
    photographer_id: str
    touch_action_ids: List[str]
    opened_at: datetime
    last_touch_at: datetime
    expires_at: datetime
    outcome: str = "open"
    feedback_first_seen_at: Optional[datetime] = None
    received_like_count: int = 0


def _parse_time(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise StateValidationError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise StateValidationError(f"invalid {field}: {error}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StateValidationError(f"{field} must be timezone-aware")
    return parsed


def _episode_id(photographer_id: str, action_id: str) -> str:
    return hashlib.sha256(f"episode:{photographer_id}:{action_id}".encode("utf-8")).hexdigest()


def evidence_weight(age_days: float) -> float:
    return 2 ** (-age_days / 30.0)


def _immutable_episode(episode: _Episode) -> FeedbackEpisode:
    return FeedbackEpisode(
        episode_id=episode.episode_id,
        photographer_id=episode.photographer_id,
        touch_action_ids=tuple(episode.touch_action_ids),
        opened_at=episode.opened_at,
        last_touch_at=episode.last_touch_at,
        expires_at=episode.expires_at,
        outcome=episode.outcome,
        feedback_first_seen_at=episode.feedback_first_seen_at,
        received_like_count=episode.received_like_count,
    )


def _scan_facts(ordered) -> Mapping[str, Mapping[str, object]]:
    immediate_ids = {
        str(row[4].data["scan_id"])
        for row in ordered
        if row[4].kind == "scan_started" and row[4].data.get("purpose") == "latest_three_feedback"
    }
    facts: Dict[str, Dict[str, object]] = {
        scan_id: {"works": [], "observations": [], "issues": set(), "summary": None}
        for scan_id in immediate_ids
    }
    for row in ordered:
        item = row[4]
        scan_id = str(item.data.get("scan_id", ""))
        if scan_id not in facts:
            continue
        if item.kind == "work_observed":
            facts[scan_id]["works"].append((int(item.data["position"]), str(item.data["photo_id"])))
        elif item.kind == "received_like_observed":
            facts[scan_id]["observations"].append(
                (str(item.data["photo_id"]), str(item.data["photographer_id"]), item.occurred_at)
            )
        elif item.kind == "scan_issue":
            facts[scan_id]["issues"].add(str(item.data["photo_id"]))
        elif item.kind == "feedback_scan_completed":
            if facts[scan_id]["summary"] is not None:
                raise StateValidationError(f"duplicate feedback scan completion {scan_id}")
            facts[scan_id]["summary"] = item
    return facts


def _scan_photo_ids(scan_id: str, facts: Mapping[str, object]) -> Tuple[str, ...]:
    works = list(facts["works"])
    positions = [position for position, _photo_id in works]
    if sorted(positions) != [1, 2, 3] or len(set(positions)) != 3:
        raise StateValidationError(f"feedback scan {scan_id} must observe positions 1, 2, 3 exactly once")
    return tuple(photo_id for _position, photo_id in sorted(works))


def _derive_scan_completion(
    scan_id: str,
    facts: Mapping[str, object],
    completed_photo_ids: Sequence[str],
    occurred_at: datetime,
    baselined_photo_ids: set,
    known_pairs: set,
    touches: Sequence[OutgoingTouch],
    touch_feedback: MutableMapping[str, TouchFeedbackEvidence],
) -> Tuple[Mapping[str, object], FeedbackScan]:
    photo_ids = _scan_photo_ids(scan_id, facts)
    completed = set(completed_photo_ids)
    if len(completed) != len(tuple(completed_photo_ids)):
        raise StateValidationError(f"feedback scan {scan_id} has duplicate completed photos")
    if not completed <= set(photo_ids):
        raise StateValidationError(f"feedback scan {scan_id} completed photos are outside latest three")
    baseline = completed - baselined_photo_ids
    observations = {
        (photo_id, photographer_id)
        for photo_id, photographer_id, _observed_at in facts["observations"]
        if photo_id in completed
    }
    new_pairs = sorted(
        pair
        for pair in observations
        if pair[0] not in baseline and pair not in known_pairs
    )
    feedback_photographers = set()
    new_feedback_points = 0
    for _photo_id, photographer_id in new_pairs:
        eligible = [
            touch
            for touch in touches
            if touch.photographer_id == photographer_id and touch.occurred_at < occurred_at
        ]
        if not eligible:
            continue
        touch = max(eligible, key=lambda value: (value.occurred_at, value.action_id))
        evidence = touch_feedback[touch.action_id]
        if evidence.feedback_points >= FEEDBACK_POINT_CAP_PER_TOUCH:
            continue
        touch_feedback[touch.action_id] = replace(
            evidence,
            feedback_points=evidence.feedback_points + 1,
            feedback_first_seen_at=evidence.feedback_first_seen_at or occurred_at,
            unanswered=False,
        )
        feedback_photographers.add(photographer_id)
        new_feedback_points += 1
    ordered_completed = [photo_id for photo_id in photo_ids if photo_id in completed]
    ordered_baseline = [photo_id for photo_id in photo_ids if photo_id in baseline]
    data = {
        "scan_id": scan_id,
        "photo_ids": list(photo_ids),
        "completed_photo_ids": ordered_completed,
        "baseline_photo_ids": ordered_baseline,
        "new_pair_count": len(new_pairs),
        "new_feedback_photographer_count": len(feedback_photographers),
        "new_feedback_points": new_feedback_points,
        "completed_at": occurred_at.isoformat(),
    }
    known_pairs.update(observations)
    baselined_photo_ids.update(baseline)
    scan = FeedbackScan(
        scan_id=scan_id,
        occurred_at=occurred_at,
        photo_ids=photo_ids,
        completed_photo_ids=frozenset(completed),
        baseline_photo_ids=frozenset(baseline),
        new_pair_count=len(new_pairs),
        new_feedback_photographer_count=len(feedback_photographers),
        new_feedback_points=new_feedback_points,
        issue_photo_ids=frozenset(facts["issues"]),
    )
    return data, scan


def rebuild_state(logs: Iterable[RunLog], now: datetime) -> AggregateState:
    logs = tuple(logs)
    ordered = []
    run_status: Dict[str, Tuple[datetime, str, str]] = {}
    run_ended = {}
    preflight_days = set()
    preview_days = set()
    for log in logs:
        if log.mode == "run":
            candidate_status = (log.ended_at, log.run_id, log.status)
            previous_status = run_status.get(log.daily_task_id)
            if previous_status is None or candidate_status[:2] > previous_status[:2]:
                run_status[log.daily_task_id] = candidate_status
            run_ended[log.daily_task_id] = max(run_ended.get(log.daily_task_id, log.ended_at), log.ended_at)
        elif log.mode == "preflight":
            preflight_days.add(log.daily_task_id)
        for index, item in enumerate(log.events):
            if item.kind == "preview_created":
                preview_days.add(log.daily_task_id)
            ordered.append((item.occurred_at, log.run_id, index, log.daily_task_id, item))
    ordered.sort(key=lambda row: (row[0], row[1], row[2]))
    scan_facts = _scan_facts(ordered)

    known_pairs = set()
    first_observations: Dict[Tuple[str, str], datetime] = {}
    profile_names: Dict[str, str] = {}
    profile_urls: Dict[str, str] = {}
    baseline_positions: MutableMapping[str, Dict[str, int]] = defaultdict(dict)
    actions: Dict[str, Tuple[Event, str]] = {}
    action_episode: Dict[str, str] = {}
    episodes: Dict[str, _Episode] = {}
    photographer_episodes: MutableMapping[str, List[str]] = defaultdict(list)
    daily_likes: MutableMapping[str, List[Event]] = defaultdict(list)
    daily_comments: MutableMapping[str, List[Event]] = defaultdict(list)
    daily_skips: MutableMapping[str, Counter] = defaultdict(Counter)
    daily_skipped_photographers: MutableMapping[str, set] = defaultdict(set)
    daily_risks: MutableMapping[str, List[Mapping[str, str]]] = defaultdict(list)
    last_comment: Dict[str, datetime] = {}
    paused_reason = None

    for occurred_at, _run_id, _index, daily_task_id, item in ordered:
        data = item.data
        if item.kind == "received_like_observed":
            photographer_id = str(data["photographer_id"])
            photo_id = str(data["photo_id"])
            pair = (photo_id, photographer_id)
            profile_names[photographer_id] = str(data["display_name"])
            profile_urls[photographer_id] = str(data["profile_url"])
            if str(data["scan_id"]) in scan_facts:
                continue
            if pair in known_pairs:
                continue
            known_pairs.add(pair)
            first_observations[pair] = occurred_at
            if not any(action.data["photographer_id"] == photographer_id for action, _day in actions.values()):
                baseline_positions[photographer_id][photo_id] = int(data["work_position"])
            successful = [
                episode
                for episode in episodes.values()
                if episode.photographer_id == photographer_id
                and episode.outcome == "success"
                and occurred_at > episode.last_touch_at
                and occurred_at <= episode.expires_at
            ]
            if successful:
                max(successful, key=lambda value: value.last_touch_at).received_like_count += 1
        elif item.kind == "candidate_observed":
            photographer_id = str(data["photographer_id"])
            profile_names[photographer_id] = str(data["display_name"])
            profile_urls[photographer_id] = str(data["profile_url"])
        elif item.kind == "outgoing_like_confirmed":
            action_id = str(data["action_id"])
            if action_id in actions:
                raise StateValidationError(f"duplicate action_id {action_id}")
            if data["before_state"] == data["after_state"] or data["after_state"] != "liked":
                raise StateValidationError(f"unconfirmed outgoing like {action_id}")
            actions[action_id] = (item, daily_task_id)
            daily_likes[daily_task_id].append(item)
            profile_names.setdefault(str(data["photographer_id"]), str(data["photographer_id"]))
            profile_urls.setdefault(str(data["photographer_id"]), "")
        elif item.kind == "feedback_episode_opened":
            action_id = str(data["touch_action_id"])
            if action_id not in actions:
                raise StateValidationError(f"episode references unknown touch {action_id}")
            touch = actions[action_id][0]
            photographer_id = str(data["photographer_id"])
            expected = _episode_id(photographer_id, action_id)
            if data["episode_id"] != expected:
                raise StateValidationError(f"invalid episode_id for {action_id}")
            if touch.data["photographer_id"] != photographer_id:
                raise StateValidationError(f"episode photographer mismatch for {action_id}")
            expires_at = _parse_time(data["expires_at"], "expires_at")
            if expires_at != touch.occurred_at + WINDOW:
                raise StateValidationError(f"invalid expiry for {action_id}")
            episode = _Episode(expected, photographer_id, [action_id], touch.occurred_at, touch.occurred_at, expires_at)
            episodes[expected] = episode
            photographer_episodes[photographer_id].append(expected)
            action_episode[action_id] = expected
        elif item.kind == "feedback_episode_extended":
            episode_id = str(data["episode_id"])
            action_id = str(data["touch_action_id"])
            if episode_id not in episodes or action_id not in actions:
                raise StateValidationError(f"invalid episode extension {episode_id}")
            episode = episodes[episode_id]
            touch = actions[action_id][0]
            if episode.outcome != "open" or touch.data["photographer_id"] != episode.photographer_id:
                raise StateValidationError(f"invalid episode extension {episode_id}")
            previous = _parse_time(data["previous_expires_at"], "previous_expires_at")
            expires_at = _parse_time(data["expires_at"], "expires_at")
            if previous != episode.expires_at or expires_at != touch.occurred_at + WINDOW:
                raise StateValidationError(f"invalid extension expiry {episode_id}")
            episode.touch_action_ids.append(action_id)
            episode.last_touch_at = touch.occurred_at
            episode.expires_at = expires_at
            action_episode[action_id] = episode_id
        elif item.kind == "feedback_episode_succeeded":
            episode_id = str(data["episode_id"])
            if episode_id not in episodes:
                raise StateValidationError(f"unknown episode {episode_id}")
            episode = episodes[episode_id]
            photo_id = str(data["received_photo_id"])
            pair = (photo_id, episode.photographer_id)
            observed_at = first_observations.get(pair)
            declared_at = _parse_time(data["feedback_first_seen_at"], "feedback_first_seen_at")
            if observed_at is None or observed_at != declared_at or observed_at <= episode.last_touch_at:
                raise StateValidationError(f"episode {episode_id} lacks new post-touch feedback")
            if observed_at > episode.expires_at or episode.outcome != "open":
                raise StateValidationError(f"invalid success timing for {episode_id}")
            episode.outcome = "success"
            episode.feedback_first_seen_at = observed_at
            episode.received_like_count = int(data["received_like_count"])
        elif item.kind == "feedback_episode_failed":
            episode_id = str(data["episode_id"])
            if episode_id not in episodes:
                raise StateValidationError(f"unknown episode {episode_id}")
            episode = episodes[episode_id]
            expired_at = _parse_time(data["expired_at"], "expired_at")
            if expired_at != episode.expires_at or occurred_at < episode.expires_at or episode.outcome != "open":
                raise StateValidationError(f"invalid failure timing for {episode_id}")
            episode.outcome = "failure"
        elif item.kind == "outgoing_comment_confirmed":
            if data["before_state"] == data["after_state"] or data["after_state"] != "visible":
                raise StateValidationError(f"unconfirmed comment {data['action_id']}")
            daily_comments[daily_task_id].append(item)
            last_comment[str(data["photographer_id"])] = occurred_at
        elif item.kind == "candidate_skipped":
            daily_skips[daily_task_id][str(data["reason"])] += 1
            daily_skipped_photographers[daily_task_id].add(str(data["photographer_id"]))
        elif item.kind == "safety_paused":
            paused_reason = str(data["reason"])
            daily_risks[daily_task_id].append(
                {"reason": paused_reason, "page_url": str(data["page_url"]), "evidence_summary": str(data["evidence_summary"])}
            )

    unlinked = {
        action_id
        for action_id, (item, _day) in actions.items()
        if item.data.get("settlement_mode", "legacy") != "immediate" and action_id not in action_episode
    }
    if unlinked:
        raise StateValidationError(f"touch without episode lifecycle: {sorted(unlinked)[0]}")

    immutable_episodes = {key: _immutable_episode(value) for key, value in episodes.items()}
    cycles = rebuild_cycles(logs, immutable_episodes, now)
    outgoing_touches = []
    touch_feedback: Dict[str, TouchFeedbackEvidence] = {}
    for action_id, (item, _day) in actions.items():
        settlement_mode = str(item.data.get("settlement_mode", "legacy"))
        touch = OutgoingTouch(
            action_id=action_id,
            photographer_id=str(item.data["photographer_id"]),
            photo_id=str(item.data["photo_id"]),
            occurred_at=item.occurred_at,
            episode_id=action_episode.get(action_id),
            quota_bucket=str(item.data["quota_bucket"]),
            settlement_mode=settlement_mode,
        )
        outgoing_touches.append(touch)
        touch_feedback[action_id] = TouchFeedbackEvidence(
            action_id=action_id,
            photographer_id=touch.photographer_id,
            touch_at=touch.occurred_at,
            feedback_points=0,
            feedback_first_seen_at=None,
            unanswered=settlement_mode == "immediate",
            settlement_mode=settlement_mode,
        )
    outgoing_touches.sort(key=lambda touch: (touch.occurred_at, touch.action_id))
    for episode in immutable_episodes.values():
        if not episode.touch_action_ids:
            continue
        action_id = episode.touch_action_ids[-1]
        evidence = touch_feedback[action_id]
        if episode.outcome == "success":
            touch_feedback[action_id] = replace(
                evidence,
                feedback_points=min(episode.received_like_count, FEEDBACK_POINT_CAP_PER_TOUCH),
                feedback_first_seen_at=episode.feedback_first_seen_at,
                unanswered=False,
            )
        elif episode.outcome in {"failure", "open"}:
            touch_feedback[action_id] = replace(evidence, unanswered=True)

    baselined_photo_ids = set()
    feedback_scans = []
    completed_scans = [
        (facts["summary"].occurred_at, scan_id, facts)
        for scan_id, facts in scan_facts.items()
        if facts["summary"] is not None
    ]
    for completed_at, scan_id, facts in sorted(completed_scans, key=lambda row: (row[0], row[1])):
        summary = facts["summary"]
        declared_completed_at = _parse_time(summary.data["completed_at"], "completed_at")
        if declared_completed_at != completed_at:
            raise StateValidationError(f"feedback scan {scan_id} completed_at mismatch")
        expected, scan = _derive_scan_completion(
            scan_id,
            facts,
            tuple(str(value) for value in summary.data["completed_photo_ids"]),
            completed_at,
            baselined_photo_ids,
            known_pairs,
            outgoing_touches,
            touch_feedback,
        )
        if dict(summary.data) != expected:
            raise StateValidationError(f"feedback scan {scan_id} summary mismatch")
        feedback_scans.append(scan)

    photographer_ids = set(profile_names) | set(photographer_episodes) | set(baseline_positions)
    photographers = {}
    today_key = now.astimezone(SHANGHAI).date().isoformat()
    for photographer_id in photographer_ids:
        episode_values = tuple(immutable_episodes[key] for key in photographer_episodes.get(photographer_id, []))
        eligible_values = eligible_episode_evidence(cycles, immutable_episodes, photographer_id, now)
        positions = dict(baseline_positions.get(photographer_id, {}))
        high_potential = len(positions) >= 2 or any(position <= 5 for position in positions.values())
        photographer_feedback = tuple(
            evidence
            for evidence in touch_feedback.values()
            if evidence.photographer_id == photographer_id
        )
        recent_start = now - ROLLING
        raw_feedback_points = sum(evidence.feedback_points for evidence in photographer_feedback)
        feedback_points_30d = sum(
            evidence.feedback_points
            for evidence in photographer_feedback
            if evidence.feedback_first_seen_at is not None
            and recent_start <= evidence.feedback_first_seen_at <= now
        )
        recent_touches = tuple(
            evidence for evidence in photographer_feedback if recent_start <= evidence.touch_at <= now
        )
        unanswered = tuple(evidence for evidence in photographer_feedback if evidence.unanswered)
        recent_unanswered = tuple(evidence for evidence in recent_touches if evidence.unanswered)
        effective_feedback_points = min(
            EFFECTIVE_FEEDBACK_CAP,
            sum(
                evidence.feedback_points
                * evidence_weight(max(0.0, (now - evidence.feedback_first_seen_at).total_seconds() / 86400.0))
                for evidence in photographer_feedback
                if evidence.feedback_first_seen_at is not None and evidence.feedback_first_seen_at <= now
            ),
        )
        effective_unanswered_touches = sum(
            evidence_weight(max(0.0, (now - evidence.touch_at).total_seconds() / 86400.0))
            for evidence in unanswered
            if evidence.touch_at <= now
        )
        last_feedback_at = max(
            (
                evidence.feedback_first_seen_at
                for evidence in photographer_feedback
                if evidence.feedback_first_seen_at is not None
            ),
            default=None,
        )
        last_unanswered_touch_at = max((evidence.touch_at for evidence in unanswered), default=None)
        today_photos = tuple(
            str(item.data["photo_id"])
            for item in daily_likes.get(today_key, [])
            if item.data["photographer_id"] == photographer_id
        )
        photographers[photographer_id] = PhotographerStats(
            photographer_id=photographer_id,
            display_name=profile_names.get(photographer_id, photographer_id),
            profile_url=profile_urls.get(photographer_id, ""),
            baseline_work_ids=frozenset(positions),
            baseline_work_positions=positions,
            historical_high_potential=high_potential,
            episodes=episode_values,
            eligible_episodes=eligible_values,
            last_comment_at=last_comment.get(photographer_id),
            today_like_photo_ids=today_photos,
            success_count_30d=sum(
                evidence.feedback_points > 0
                and evidence.feedback_first_seen_at is not None
                and recent_start <= evidence.feedback_first_seen_at <= now
                for evidence in photographer_feedback
            ),
            failure_count=len(unanswered),
            dormant_retest_eligible=bool(
                len(photographer_feedback) >= 3
                and feedback_points_30d == 0
                and last_unanswered_touch_at
                and now >= last_unanswered_touch_at + RETEST_COOLDOWN
            ),
            raw_feedback_points=raw_feedback_points,
            feedback_points_30d=feedback_points_30d,
            touch_count=len(photographer_feedback),
            touch_count_30d=len(recent_touches),
            unanswered_touch_count_30d=len(recent_unanswered),
            effective_feedback_points=effective_feedback_points,
            effective_unanswered_touches=effective_unanswered_touches,
            last_feedback_at=last_feedback_at,
            last_unanswered_touch_at=last_unanswered_touch_at,
        )

    daily_tasks = {}
    all_days = set(run_status) | preflight_days | set(daily_likes) | set(daily_comments) | set(daily_skips) | set(daily_risks)
    for day in all_days:
        likes = daily_likes.get(day, [])
        quota_counts = Counter(str(item.data["quota_bucket"]) for item in likes)
        unique = frozenset(str(item.data["photographer_id"]) for item in likes)
        covered = frozenset(set(unique) | daily_skipped_photographers.get(day, set()))
        latest_run_status = run_status[day][2] if day in run_status else None
        legacy_completed = day < COVERAGE_CONTRACT_START_DAY and len(likes) == 100
        completed = latest_run_status == "completed" or legacy_completed
        if completed:
            task_status = "completed"
        elif latest_run_status in {"paused_incomplete", "incomplete_candidate_exhausted"}:
            task_status = latest_run_status
        elif likes:
            task_status = "in_progress"
        elif day in preview_days:
            task_status = "preflight_ready"
        elif latest_run_status == "approval_rejected":
            task_status = latest_run_status
        elif day in preflight_days:
            task_status = "preflight_active"
        else:
            task_status = "in_progress"
        daily_tasks[day] = DailyTaskStats(
            daily_task_id=day,
            confirmed_likes=len(likes),
            unique_photographer_ids=unique,
            quota_counts=dict(quota_counts),
            confirmed_comments=len(daily_comments.get(day, [])),
            status=task_status,
            completed_at=run_ended.get(day) if completed else None,
            reinforcement_likes=quota_counts.get("verified_second", 0),
            skip_counts=dict(daily_skips.get(day, {})),
            risk_events=tuple(daily_risks.get(day, [])),
            covered_photographer_ids=covered,
        )

    return AggregateState(
        photographers=photographers,
        known_received_like_pairs=frozenset(known_pairs),
        daily_tasks=daily_tasks,
        paused_reason=paused_reason,
        episodes=immutable_episodes,
        outgoing_touches=tuple(outgoing_touches),
        cycles=cycles,
        latest_cycle_id=latest_cycle_id(cycles),
        feedback_scans=tuple(feedback_scans),
        touch_feedback=dict(touch_feedback),
        baselined_photo_ids=frozenset(baselined_photo_ids),
    )


def build_feedback_scan_completed_event(
    logs: Iterable[RunLog],
    scan_id: str,
    completed_photo_ids: Sequence[str],
    now: datetime,
) -> Event:
    logs = tuple(logs)
    state = rebuild_state(logs, now)
    ordered = [
        (item.occurred_at, log.run_id, index, log.daily_task_id, item)
        for log in logs
        for index, item in enumerate(log.events)
    ]
    ordered.sort(key=lambda row: (row[0], row[1], row[2]))
    facts_by_scan = _scan_facts(ordered)
    if scan_id not in facts_by_scan:
        raise StateValidationError(f"feedback scan {scan_id} not found")
    facts = facts_by_scan[scan_id]
    if facts["summary"] is not None:
        raise StateValidationError(f"feedback scan {scan_id} already completed")
    touch_feedback = dict(state.touch_feedback)
    data, _scan = _derive_scan_completion(
        scan_id,
        facts,
        tuple(completed_photo_ids),
        now,
        set(state.baselined_photo_ids),
        set(state.known_received_like_pairs),
        state.outgoing_touches,
        touch_feedback,
    )
    return Event("feedback_scan_completed", now, dict(data))


def classify_photographer(stats: PhotographerStats, now: datetime) -> str:
    if stats.feedback_points_30d >= 3:
        return "verified"
    if stats.touch_count >= 3 and stats.feedback_points_30d == 0:
        return "dormant"
    if stats.feedback_points_30d >= 1 or stats.raw_feedback_points >= 1:
        return "promising"
    return "new"


def beta_parameters(stats: PhotographerStats, now: datetime) -> Tuple[float, float]:
    return (
        1.0 + min(stats.effective_feedback_points, EFFECTIVE_FEEDBACK_CAP),
        1.0 + stats.effective_unanswered_touches,
    )


def matured_cohort_counts(state: AggregateState, now: datetime) -> Tuple[int, int]:
    start = now - ROLLING
    evidence_by_episode = (
        {
            episode.episode_id: episode
            for stats in state.photographers.values()
            for episode in stats.eligible_episodes
        }
        if state.cycles
        else dict(state.episodes)
    )
    touches = [
        touch
        for touch in state.outgoing_touches
        if start <= touch.occurred_at <= now
        and touch.episode_id in evidence_by_episode
        and evidence_by_episode[touch.episode_id].expires_at <= now
    ]
    successful = {
        touch.photographer_id
        for touch in touches
        if evidence_by_episode[touch.episode_id].outcome == "success"
    }
    return len(successful), len(touches)


def matured_cohort_kpi(state: AggregateState, now: datetime) -> Optional[float]:
    numerator, denominator = matured_cohort_counts(state, now)
    if denominator == 0:
        return None
    return 100.0 * numerator / denominator
