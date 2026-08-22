import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
import uuid
from zoneinfo import ZoneInfo

from . import SCHEMA_VERSION
from .analytics import (
    WINDOW,
    StateValidationError,
    build_feedback_scan_completed_event,
    classify_photographer,
    rebuild_state,
)
from .automation import build_review_request, build_review_requests
from .dashboard import generate_dashboard
from .model import Candidate, CheckpointHeader, Event, RunLog
from .selector import DAILY_PHOTOGRAPHER_TARGET, select_run_candidates
from .store import (
    LogValidationError,
    append_checkpoint_events,
    begin_checkpoint,
    iter_recoverable_checkpoints,
    iter_sealed_logs,
    load_effective_runs,
    read_checkpoint,
    seal_run,
)
from .workspace import (
    WorkspaceError,
    find_checkout_root,
    find_repository_root,
    inspect_git_state,
    resolve_state_root,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")


class CliError(RuntimeError):
    def __init__(self, code: str, **details):
        super().__init__(code)
        self.code = code
        self.details = details


def _now(value: Optional[str]) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CliError("invalid_now")
    return parsed.astimezone(timezone.utc)


def _day(value: datetime) -> str:
    return value.astimezone(SHANGHAI).date().isoformat()


def _json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _error(code: str, **details) -> int:
    _json({"ok": False, "code": code, **details})
    return 2


def _state_root(value: Optional[str]) -> Path:
    return resolve_state_root(value, os.environ, Path(__file__))


def _events_have_approval(logs: Iterable[RunLog]) -> bool:
    return any(event.kind == "onboarding_approved" for log in logs for event in log.events)


def _serialize_event(event: Event) -> Mapping[str, object]:
    return {"kind": event.kind, "occurred_at": event.occurred_at.isoformat(), "data": event.data}


def _parse_fields(values: Sequence[str]) -> Dict[str, object]:
    result = {}
    for value in values:
        if "=" not in value:
            raise CliError("invalid_field", field=value)
        key, raw = value.split("=", 1)
        if not key or key in result:
            raise CliError("invalid_field", field=value)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw
        result[key] = parsed
    return result


def _episode_id(photographer_id: str, action_id: str) -> str:
    return hashlib.sha256(f"episode:{photographer_id}:{action_id}".encode("utf-8")).hexdigest()


def _action_id(daily_task_id: str, photographer_id: str, photo_id: str, kind: str) -> str:
    return hashlib.sha256(f"{daily_task_id}{photographer_id}{photo_id}{kind}".encode("utf-8")).hexdigest()


def _candidates(checkpoint, state) -> List[Candidate]:
    latest = {}
    for item in checkpoint.events:
        if item.kind != "candidate_observed":
            continue
        data = item.data
        identifier = str(data["photographer_id"])
        stats = state.photographers.get(identifier)
        tier = classify_photographer(stats, item.occurred_at) if stats else "new"
        is_retest = bool(stats and tier == "dormant" and stats.dormant_retest_eligible)
        candidate = Candidate(
            photographer_id=identifier,
            display_name=str(data["display_name"]),
            profile_url=str(data["profile_url"]),
            source_photo_id=str(data["source_photo_id"]),
            source_url=str(data["source_url"]),
            page_order=int(data["page_order"]),
            tier=tier,
            is_retest=is_retest,
        )
        old = latest.get(identifier)
        if old is None or candidate.page_order < old.page_order:
            latest[identifier] = candidate
    return list(latest.values())


def _canonical_plan(plan: Sequence[Mapping[str, object]]) -> str:
    return json.dumps(list(plan), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(plan: Sequence[Mapping[str, object]]) -> str:
    return hashlib.sha256(_canonical_plan(plan).encode("utf-8")).hexdigest()


def _canonical_digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cycle_events(logs: Iterable[RunLog], cycle_id: str) -> List[Event]:
    events = [
        item
        for log in logs
        for item in log.events
        if item.data.get("cycle_id") == cycle_id
    ]
    events.sort(key=lambda item: item.occurred_at)
    return events


def _require_cycle_checkpoint(root: Path, run_id: str, cycle_id: str):
    checkpoint = read_checkpoint(root, run_id)
    if checkpoint.header.mode not in {"cycle", "migration"}:
        raise CliError("cycle_transaction_required")
    if checkpoint.header.transaction_context.get("cycle_id") != cycle_id:
        raise CliError("cycle_context_mismatch")
    return checkpoint


def _cycle_showcase(events: Sequence[Event]) -> List[Event]:
    return [item for item in events if item.kind == "cycle_showcase_observed"]


def _frozen_photo_ids(events: Sequence[Event]) -> Tuple[str, ...]:
    frozen = [item for item in events if item.kind == "cycle_showcase_frozen"]
    return tuple(str(value) for value in frozen[-1].data["photo_ids"]) if frozen else ()


def _baseline_digest(events: Sequence[Event]) -> Optional[str]:
    completed = [item for item in events if item.kind == "cycle_baseline_completed"]
    return str(completed[-1].data["baseline_digest"]) if completed else None


def _quota_snapshot(state, daily_task_id: str) -> Mapping[str, object]:
    daily = state.daily_tasks.get(daily_task_id)
    return {
        "confirmed_likes": daily.confirmed_likes if daily else 0,
        "quota_counts": dict(daily.quota_counts) if daily else {},
        "unique_photographers": sorted(daily.unique_photographer_ids) if daily else [],
        "covered_photographers": sorted(daily.covered_photographer_ids) if daily else [],
    }


def _remaining_photographer_quota(daily) -> int:
    if daily and daily.status == "completed":
        return 0
    covered = len(daily.covered_photographer_ids) if daily else 0
    return max(0, DAILY_PHOTOGRAPHER_TARGET - covered)


def _active_daily_task_id(root: Path, effective: Sequence[RunLog]) -> Optional[str]:
    active_ids = {log.run_id for log in effective if log.status == "active"}
    active_daily = sorted(
        (
            checkpoint.header.started_at,
            checkpoint.header.run_id,
            checkpoint.header.daily_task_id,
        )
        for checkpoint in iter_recoverable_checkpoints(root)
        if checkpoint.header.run_id in active_ids and checkpoint.header.mode in {"run", "preflight"}
    )
    return active_daily[-1][2] if active_daily else None


def _all_previews(root: Path) -> List[Tuple[Event, RunLog]]:
    previews = []
    for log in iter_sealed_logs(root):
        for item in log.events:
            if item.kind == "preview_created":
                previews.append((item, log))
    previews.sort(key=lambda pair: (pair[0].occurred_at, pair[0].data["preview_id"]))
    return previews


def _append_expired_episodes(root: Path, run_id: str, now: datetime, state) -> None:
    failures = []
    for episode in state.episodes.values():
        if episode.outcome == "open" and episode.expires_at <= now:
            failures.append(
                Event(
                    "feedback_episode_failed",
                    now,
                    {"episode_id": episode.episode_id, "expired_at": episode.expires_at.isoformat()},
                )
            )
    if failures:
        append_checkpoint_events(root, run_id, failures)


def _transaction_context(args) -> Dict[str, str]:
    cycle_id = getattr(args, "cycle_id", None)
    if args.mode in {"cycle", "migration"}:
        if not cycle_id:
            raise CliError("cycle_id_required")
        return {"cycle_id": cycle_id}
    if args.mode == "review":
        if not cycle_id:
            raise CliError("cycle_id_required")
        if not args.review_kind:
            raise CliError("review_kind_required")
        if args.attempt is None or args.attempt < 1:
            raise CliError("invalid_attempt")
        return {
            "cycle_id": cycle_id,
            "review_kind": args.review_kind,
            "attempt": str(args.attempt),
        }
    if args.mode == "run" and cycle_id:
        return {"cycle_id": cycle_id}
    return {}


def _checkpoint_key(header: CheckpointHeader) -> Tuple[str, ...]:
    context = header.transaction_context
    if header.mode in {"run", "preflight"}:
        return ("daily", header.daily_task_id)
    if header.mode == "review":
        return (
            "review",
            context.get("cycle_id", ""),
            context.get("review_kind", ""),
            context.get("attempt", ""),
        )
    return (header.mode, context.get("cycle_id", header.run_id))


def command_begin(args) -> int:
    root = _state_root(args.state_root)
    now = _now(args.now)
    context = _transaction_context(args)
    effective = load_effective_runs(root)
    daily_task_id = _day(now)
    if args.mode == "run" and args.approve_preview:
        matching_previews = [
            log.daily_task_id
            for preview, log in _all_previews(root)
            if preview.data["preview_id"] == args.approve_preview
        ]
        if matching_previews:
            daily_task_id = matching_previews[-1]
    active_ids = {log.run_id for log in effective if log.status == "active"}
    requested_header = CheckpointHeader(
        SCHEMA_VERSION,
        "requested",
        daily_task_id,
        args.mode,
        now,
        args.approve_preview,
        context,
    )
    requested_key = _checkpoint_key(requested_header)
    for checkpoint in iter_recoverable_checkpoints(root):
        if checkpoint.header.run_id not in active_ids:
            continue
        if args.mode in {"run", "preflight"}:
            if checkpoint.header.mode not in {"run", "preflight"}:
                continue
        elif _checkpoint_key(checkpoint.header) != requested_key:
            continue
        same_day = checkpoint.header.daily_task_id == _day(now)
        code = (
            "recoverable_run"
            if args.mode in {"run", "preflight", "review"} or same_day
            else "stale_recoverable_run"
        )
        return _error(code, recoverable_run_id=checkpoint.header.run_id)
    if args.mode == "run" and not args.approve_preview and not _events_have_approval(effective):
        return _error("preflight_required")
    state = rebuild_state(effective, now)
    review_due_at = None
    if args.mode == "review":
        cycle = state.cycles.get(args.cycle_id)
        if cycle is None:
            return _error("cycle_not_found")
        slot = cycle.review_1d if args.review_kind == "review_1d" else cycle.review_3d
        attempts = [item for item in slot.attempts if item.attempt == args.attempt]
        if not attempts:
            return _error("review_not_scheduled")
        review_attempt = attempts[-1]
        if review_attempt.status in {"completed", "superseded"}:
            return _error("review_already_resolved", status=review_attempt.status)
        if now < review_attempt.due_at:
            return _error("review_not_due", due_at=review_attempt.due_at.isoformat())
        review_due_at = review_attempt.due_at
    if args.mode == "run" and args.cycle_id:
        cycle = state.cycles.get(args.cycle_id)
        if cycle is None:
            return _error("cycle_not_found")
        if cycle.status != "baseline_ready":
            return _error("cycle_baseline_not_ready", status=cycle.status)
        unsettled = [
            item.cycle_id
            for item in state.cycles.values()
            if item.cycle_id != args.cycle_id and item.status not in {"settled", "abandoned"}
        ]
        if unsettled:
            return _error("previous_cycle_unsettled", cycle_ids=sorted(unsettled))
        sealed_events = _cycle_events(iter_sealed_logs(root), args.cycle_id)
        digest = _baseline_digest(sealed_events)
        if digest is None:
            return _error("cycle_baseline_not_sealed")
        if any(item.kind == "cycle_run_bound" for item in sealed_events):
            return _error("cycle_run_already_bound")
    daily = state.daily_tasks.get(daily_task_id)
    if args.mode == "run" and daily and _remaining_photographer_quota(daily) == 0:
        return _error("daily_complete")
    run_id = f"{args.mode}-{uuid.uuid4().hex}"
    begin_checkpoint(
        root,
        CheckpointHeader(
            SCHEMA_VERSION,
            run_id,
            daily_task_id,
            args.mode,
            now,
            args.approve_preview,
            context,
        ),
    )
    if args.mode == "run" and args.cycle_id:
        append_checkpoint_events(
            root,
            run_id,
            (
                Event(
                    "cycle_run_bound",
                    now,
                    {
                        "cycle_id": args.cycle_id,
                        "run_id": run_id,
                        "baseline_digest": digest,
                        "bound_at": now.isoformat(),
                    },
                ),
            ),
        )
    if args.mode == "review":
        append_checkpoint_events(
            root,
            run_id,
            (
                Event(
                    "review_started",
                    now,
                    {
                        "cycle_id": args.cycle_id,
                        "review_kind": args.review_kind,
                        "attempt": args.attempt,
                        "due_at": review_due_at.isoformat(),
                        "started_at": now.isoformat(),
                    },
                ),
            ),
        )
    remaining = _remaining_photographer_quota(daily)
    _json(
        {
            "ok": True,
            "run_id": run_id,
            "daily_task_id": daily_task_id,
            "remaining_daily_quota": remaining,
            "remaining_photographer_quota": remaining,
            "onboarding_approved": _events_have_approval(effective),
            "recoverable_run_id": None,
            "transaction_context": context,
        }
    )
    return 0


def command_resume(args) -> int:
    root = _state_root(args.state_root)
    now = _now(args.now)
    recoverable = {
        log.run_id
        for log in load_effective_runs(root)
        if log.status == "active"
    }
    if args.run_id not in recoverable:
        return _error("run_not_recoverable")
    checkpoint = read_checkpoint(root, args.run_id)
    if checkpoint.header.mode in {"cycle", "migration"} and now - checkpoint.header.started_at > timedelta(hours=24):
        return _error("transaction_expired", run_id=args.run_id)
    _json(
        {
            "ok": True,
            "header": {
                "run_id": checkpoint.header.run_id,
                "daily_task_id": checkpoint.header.daily_task_id,
                "mode": checkpoint.header.mode,
                "started_at": checkpoint.header.started_at.isoformat(),
                "approve_preview_id": checkpoint.header.approve_preview_id,
                "transaction_context": dict(checkpoint.header.transaction_context),
            },
            "events": [_serialize_event(item) for item in checkpoint.events],
        }
    )
    return 0


def command_event(args) -> int:
    root = _state_root(args.state_root)
    now = _now(args.now)
    checkpoint = read_checkpoint(root, args.run_id)
    if any(item.kind == "safety_paused" for item in checkpoint.events) and args.kind.startswith("outgoing_"):
        return _error("run_paused")
    data = _parse_fields(args.field)
    if args.kind == "outgoing_like_confirmed":
        data["settlement_mode"] = (
            "legacy" if checkpoint.header.transaction_context.get("cycle_id") else "immediate"
        )
    if args.kind == "outgoing_comment_confirmed" and data.get("content") != "👍👍👍":
        return _error("invalid_comment_content", expected="👍👍👍")
    event = Event(args.kind, now, data)
    additions = [event]
    state = rebuild_state(load_effective_runs(root), now)
    if args.kind in {"outgoing_like_confirmed", "candidate_skipped"}:
        day = checkpoint.header.daily_task_id
        daily = state.daily_tasks.get(day)
        photographer_id = str(data["photographer_id"])
        if daily and photographer_id in daily.covered_photographer_ids:
            return _error("photographer_already_covered", photographer_id=photographer_id)
        if daily and _remaining_photographer_quota(daily) == 0:
            return _error("daily_complete")
    if args.kind == "candidate_skipped" and data.get("quota_bucket") not in {"exploit_first", "new", "retest"}:
        return _error("invalid_quota_bucket", expected=["exploit_first", "new", "retest"])
    if args.kind == "outgoing_like_confirmed":
        day = checkpoint.header.daily_task_id
        expected_action = _action_id(day, str(data["photographer_id"]), str(data["photo_id"]), args.kind)
        if data.get("action_id") != expected_action:
            return _error("invalid_action_id", expected=expected_action)
    if args.kind == "outgoing_like_confirmed" and data["settlement_mode"] == "legacy":
        open_episodes = [
            item
            for item in state.episodes.values()
            if item.photographer_id == data["photographer_id"] and item.outcome == "open" and item.expires_at > now
        ]
        if open_episodes:
            episode = max(open_episodes, key=lambda item: item.last_touch_at)
            additions.append(
                Event(
                    "feedback_episode_extended",
                    now,
                    {
                        "episode_id": episode.episode_id,
                        "touch_action_id": data["action_id"],
                        "previous_expires_at": episode.expires_at.isoformat(),
                        "expires_at": (now + WINDOW).isoformat(),
                    },
                )
            )
        else:
            identifier = _episode_id(str(data["photographer_id"]), str(data["action_id"]))
            additions.append(
                Event(
                    "feedback_episode_opened",
                    now,
                    {
                        "episode_id": identifier,
                        "photographer_id": data["photographer_id"],
                        "touch_action_id": data["action_id"],
                        "expires_at": (now + WINDOW).isoformat(),
                    },
                )
            )
    elif args.kind == "received_like_observed" and not any(
        item.kind == "scan_started"
        and item.data.get("purpose") == "latest_three_feedback"
        and item.data.get("scan_id") == data.get("scan_id")
        for item in checkpoint.events
    ):
        pair = (str(data["photo_id"]), str(data["photographer_id"]))
        if pair not in state.known_received_like_pairs:
            eligible = [
                item
                for item in state.episodes.values()
                if item.photographer_id == data["photographer_id"]
                and item.outcome == "open"
                and item.last_touch_at < now <= item.expires_at
            ]
            if eligible:
                episode = max(eligible, key=lambda item: item.last_touch_at)
                additions.append(
                    Event(
                        "feedback_episode_succeeded",
                        now,
                        {
                            "episode_id": episode.episode_id,
                            "received_photo_id": data["photo_id"],
                            "feedback_first_seen_at": now.isoformat(),
                            "received_like_count": 1,
                        },
                    )
                )
    append_checkpoint_events(root, args.run_id, additions)
    _json({"ok": True, "appended": [item.kind for item in additions]})
    return 0


def command_feedback_scan_complete(args) -> int:
    root = _state_root(args.state_root)
    now = _now(args.now)
    checkpoint = read_checkpoint(root, args.run_id)
    if checkpoint.header.mode not in {"preflight", "run"}:
        return _error("daily_transaction_required")
    try:
        completed = build_feedback_scan_completed_event(
            load_effective_runs(root),
            args.run_id,
            args.scan_id,
            tuple(args.completed_photo_id),
            now,
        )
    except StateValidationError as error:
        return _error("invalid_feedback_scan", message=str(error))
    append_checkpoint_events(root, args.run_id, (completed,))
    _json({"ok": True, **completed.data})
    return 0


def command_cycle_start(args) -> int:
    root = _state_root(args.state_root)
    now = _now(args.now)
    checkpoint = _require_cycle_checkpoint(root, args.run_id, args.cycle_id)
    if any(item.kind == "cycle_started" for item in checkpoint.events):
        return _error("cycle_already_started")
    if args.attribution_eligible not in {"true", "false"}:
        return _error("invalid_attribution_eligible")
    existing = rebuild_state(load_effective_runs(root), now).cycles
    if args.cycle_id in existing:
        return _error("cycle_already_exists")
    unsettled = [
        item.cycle_id for item in existing.values() if item.status not in {"settled", "abandoned"}
    ]
    if unsettled:
        return _error("previous_cycle_unsettled", cycle_ids=sorted(unsettled))
    append_checkpoint_events(
        root,
        args.run_id,
        (
            Event(
                "cycle_started",
                now,
                {
                    "cycle_id": args.cycle_id,
                    "attribution_eligible": args.attribution_eligible == "true",
                },
            ),
        ),
    )
    _json({"ok": True, "cycle_id": args.cycle_id, "status": "preparing"})
    return 0


def command_cycle_showcase_observe(args) -> int:
    root = _state_root(args.state_root)
    now = _now(args.now)
    checkpoint = _require_cycle_checkpoint(root, args.run_id, args.cycle_id)
    if not any(item.kind == "cycle_started" for item in checkpoint.events):
        return _error("cycle_not_started")
    if any(item.kind == "cycle_showcase_frozen" for item in checkpoint.events):
        return _error("showcase_already_frozen")
    if args.position < 1 or args.position > 5:
        return _error("invalid_showcase_position")
    append_checkpoint_events(
        root,
        args.run_id,
        (
            Event(
                "cycle_showcase_observed",
                now,
                {
                    "cycle_id": args.cycle_id,
                    "photo_id": args.photo_id,
                    "photo_url": args.photo_url,
                    "owner_id": args.owner_id,
                    "visibility": args.visibility,
                    "position": args.position,
                    "evidence_summary": args.evidence_summary,
                },
            ),
        ),
    )
    _json({"ok": True, "photo_id": args.photo_id, "position": args.position})
    return 0


def command_cycle_showcase_freeze(args) -> int:
    root = _state_root(args.state_root)
    now = _now(args.now)
    checkpoint = _require_cycle_checkpoint(root, args.run_id, args.cycle_id)
    if any(item.kind == "cycle_showcase_frozen" for item in checkpoint.events):
        return _error("showcase_already_frozen")
    observed = _cycle_showcase(checkpoint.events)
    photo_ids = [str(item.data["photo_id"]) for item in observed]
    positions = [int(item.data["position"]) for item in observed]
    owners = {str(item.data["owner_id"]) for item in observed}
    if len(observed) != 5 or len(set(photo_ids)) != 5 or sorted(positions) != [1, 2, 3, 4, 5]:
        return _error("showcase_requires_exactly_five")
    if len(owners) != 1 or (args.owner_id and owners != {args.owner_id}):
        return _error("showcase_owner_mismatch")
    if any(item.data["visibility"] != "public" for item in observed):
        return _error("showcase_not_public")
    ordered = [
        str(item.data["photo_id"])
        for item in sorted(observed, key=lambda value: int(value.data["position"]))
    ]
    digest = _canonical_digest(ordered)
    append_checkpoint_events(
        root,
        args.run_id,
        (Event("cycle_showcase_frozen", now, {"cycle_id": args.cycle_id, "photo_ids": ordered, "showcase_digest": digest}),),
    )
    _json({"ok": True, "cycle_id": args.cycle_id, "photo_ids": ordered, "showcase_digest": digest})
    return 0


def command_cycle_baseline_start(args) -> int:
    root = _state_root(args.state_root)
    now = _now(args.now)
    checkpoint = _require_cycle_checkpoint(root, args.run_id, args.cycle_id)
    if not _frozen_photo_ids(checkpoint.events):
        return _error("showcase_not_frozen")
    if any(item.kind == "cycle_baseline_completed" for item in checkpoint.events):
        return _error("baseline_already_completed")
    if any(item.kind == "cycle_baseline_scan_started" and item.data["scan_id"] == args.scan_id for item in checkpoint.events):
        _json({"ok": True, "scan_id": args.scan_id, "resumed": True})
        return 0
    append_checkpoint_events(root, args.run_id, (Event("cycle_baseline_scan_started", now, {"cycle_id": args.cycle_id, "scan_id": args.scan_id}),))
    _json({"ok": True, "scan_id": args.scan_id, "resumed": False})
    return 0


def _require_baseline_scan(checkpoint, cycle_id: str, scan_id: str) -> Tuple[str, ...]:
    if not any(item.kind == "cycle_baseline_scan_started" and item.data["scan_id"] == scan_id for item in checkpoint.events):
        raise CliError("baseline_scan_not_started")
    frozen = _frozen_photo_ids(checkpoint.events)
    if not frozen:
        raise CliError("showcase_not_frozen")
    return frozen


def command_cycle_baseline_observe(args) -> int:
    root = _state_root(args.state_root)
    now = _now(args.now)
    checkpoint = _require_cycle_checkpoint(root, args.run_id, args.cycle_id)
    frozen = _require_baseline_scan(checkpoint, args.cycle_id, args.scan_id)
    if args.photo_id not in frozen:
        return _error("photo_not_in_showcase")
    duplicate = any(
        item.kind == "cycle_baseline_like_observed"
        and item.data["scan_id"] == args.scan_id
        and item.data["photo_id"] == args.photo_id
        and item.data["photographer_id"] == args.photographer_id
        for item in checkpoint.events
    )
    if duplicate:
        _json({"ok": True, "duplicate": True})
        return 0
    append_checkpoint_events(
        root,
        args.run_id,
        (
            Event(
                "cycle_baseline_like_observed",
                now,
                {
                    "cycle_id": args.cycle_id,
                    "scan_id": args.scan_id,
                    "photo_id": args.photo_id,
                    "photographer_id": args.photographer_id,
                    "display_name": args.display_name,
                    "profile_url": args.profile_url,
                },
            ),
        ),
    )
    _json({"ok": True, "duplicate": False})
    return 0


def command_cycle_baseline_photo_complete(args) -> int:
    root = _state_root(args.state_root)
    now = _now(args.now)
    checkpoint = _require_cycle_checkpoint(root, args.run_id, args.cycle_id)
    frozen = _require_baseline_scan(checkpoint, args.cycle_id, args.scan_id)
    if args.photo_id not in frozen:
        return _error("photo_not_in_showcase")
    observed = {
        str(item.data["photographer_id"])
        for item in checkpoint.events
        if item.kind == "cycle_baseline_like_observed"
        and item.data["scan_id"] == args.scan_id
        and item.data["photo_id"] == args.photo_id
    }
    if args.liker_count != len(observed):
        return _error("liker_count_mismatch", observed=len(observed))
    prior = [
        item for item in checkpoint.events
        if item.kind == "cycle_baseline_photo_completed"
        and item.data["scan_id"] == args.scan_id
        and item.data["photo_id"] == args.photo_id
    ]
    if prior:
        if int(prior[-1].data["liker_count"]) != args.liker_count:
            return _error("baseline_photo_conflict")
        _json({"ok": True, "duplicate": True})
        return 0
    append_checkpoint_events(root, args.run_id, (Event("cycle_baseline_photo_completed", now, {"cycle_id": args.cycle_id, "scan_id": args.scan_id, "photo_id": args.photo_id, "liker_count": args.liker_count}),))
    _json({"ok": True, "duplicate": False})
    return 0


def command_cycle_baseline_complete(args) -> int:
    root = _state_root(args.state_root)
    now = _now(args.now)
    checkpoint = _require_cycle_checkpoint(root, args.run_id, args.cycle_id)
    frozen = _require_baseline_scan(checkpoint, args.cycle_id, args.scan_id)
    completed = {
        str(item.data["photo_id"]): int(item.data["liker_count"])
        for item in checkpoint.events
        if item.kind == "cycle_baseline_photo_completed" and item.data["scan_id"] == args.scan_id
    }
    if set(completed) != set(frozen):
        return _error("baseline_incomplete", missing_photo_ids=sorted(set(frozen) - set(completed)))
    pairs = sorted(
        (str(item.data["photo_id"]), str(item.data["photographer_id"]))
        for item in checkpoint.events
        if item.kind == "cycle_baseline_like_observed" and item.data["scan_id"] == args.scan_id
    )
    digest = _canonical_digest({"photo_ids": list(frozen), "pairs": pairs, "counts": completed})
    append_checkpoint_events(root, args.run_id, (Event("cycle_baseline_completed", now, {"cycle_id": args.cycle_id, "scan_id": args.scan_id, "baseline_digest": digest}),))
    _json({"ok": True, "cycle_id": args.cycle_id, "baseline_digest": digest, "status": "baseline_ready"})
    return 0


def command_cycle_status(args) -> int:
    root = _state_root(args.state_root)
    now = _now(args.now)
    cycle = rebuild_state(load_effective_runs(root), now).cycles.get(args.cycle_id)
    if cycle is None:
        return _error("cycle_not_found")
    _json({
        "ok": True,
        "cycle_id": cycle.cycle_id,
        "status": cycle.status,
        "attribution_eligible": cycle.attribution_eligible,
        "showcase_photo_ids": list(cycle.showcase_photo_ids),
        "baseline_pair_count": len(cycle.baseline_pairs),
    })
    return 0


def _sealed_runs_by_id(root: Path) -> Mapping[str, RunLog]:
    return {log.run_id: log for log in iter_sealed_logs(root)}


def _schedule_event(request, root: Path, now: datetime) -> Event:
    return Event(
        "review_schedule_requested",
        now,
        {
            "cycle_id": request.cycle_id,
            "review_kind": request.review_kind,
            "attempt": request.attempt,
            "due_at": request.due_at.isoformat(),
            "state_root": str(root.resolve()),
            "automation_name": request.name,
            "payload_digest": request.payload_digest,
        },
    )


def command_cycle_like_complete(args) -> int:
    root = _state_root(args.state_root)
    now = _now(args.now)
    checkpoint = _require_cycle_checkpoint(root, args.run_id, args.cycle_id)
    if checkpoint.header.mode != "cycle":
        return _error("cycle_transaction_required")
    mapped_ids = tuple(dict.fromkeys(args.mapped_run_id))
    if not mapped_ids:
        return _error("mapped_run_required")
    sealed = _sealed_runs_by_id(root)
    missing = [run_id for run_id in mapped_ids if run_id not in sealed]
    if missing:
        return _error("mapped_run_not_sealed", run_ids=missing)
    run_events = [item for run_id in mapped_ids for item in sealed[run_id].events]
    for mapped_id in mapped_ids:
        bindings = [
            item for item in sealed[mapped_id].events
            if item.kind == "cycle_run_bound" and item.data["cycle_id"] == args.cycle_id
        ]
        if len(bindings) != 1:
            return _error("mapped_run_not_bound", run_id=mapped_id)
    likes = [item for item in run_events if item.kind == "outgoing_like_confirmed"]
    if not likes:
        return _error("cycle_has_no_confirmed_likes")
    if args.status not in {"completed", "incomplete_candidate_exhausted"}:
        return _error("cycle_not_schedulable", status=args.status)
    action_ids = [str(item.data["action_id"]) for item in likes]
    episode_ids = [
        str(item.data["episode_id"])
        for item in run_events
        if item.kind in {"feedback_episode_opened", "feedback_episode_extended"}
        and item.data["touch_action_id"] in action_ids
    ]
    episode_ids = list(dict.fromkeys(episode_ids))
    like_completed_at = max(item.occurred_at for item in likes)
    requests = build_review_requests(args.cycle_id, like_completed_at, root)
    additions = [
        Event(
            "cycle_like_completed",
            now,
            {
                "cycle_id": args.cycle_id,
                "mapped_run_ids": list(mapped_ids),
                "touch_action_ids": action_ids,
                "episode_ids": episode_ids,
                "like_completed_at": like_completed_at.isoformat(),
                "terminal_status": args.status,
            },
        )
    ] + [_schedule_event(request, root, now) for request in requests]
    append_checkpoint_events(root, args.run_id, additions)
    _json({
        "ok": True,
        "cycle_id": args.cycle_id,
        "like_completed_at": like_completed_at.isoformat(),
        "confirmed_likes": len(likes),
        "review_requests": [request.payload for request in requests],
        "review_request_digests": [request.payload_digest for request in requests],
    })
    return 0


def command_review_schedule_intent(args) -> int:
    root = _state_root(args.state_root)
    now = _now(args.now)
    checkpoint = _require_cycle_checkpoint(root, args.run_id, args.cycle_id)
    state = rebuild_state(load_effective_runs(root), now)
    cycle = state.cycles.get(args.cycle_id)
    if cycle is None or cycle.like_completed_at is None:
        return _error("cycle_like_not_completed")
    request = build_review_request(args.cycle_id, args.review_kind, args.attempt, cycle.like_completed_at, root)
    existing = [
        item for item in _cycle_events(load_effective_runs(root), args.cycle_id)
        if item.kind == "review_schedule_requested"
        and item.data["review_kind"] == args.review_kind
        and int(item.data["attempt"]) == args.attempt
    ]
    if existing:
        if existing[-1].data["payload_digest"] != request.payload_digest:
            return _error("schedule_payload_mismatch")
        _json({"ok": True, "duplicate": True, "request": request.payload, "payload_digest": request.payload_digest})
        return 0
    append_checkpoint_events(root, args.run_id, (_schedule_event(request, root, now),))
    _json({"ok": True, "duplicate": False, "request": request.payload, "payload_digest": request.payload_digest})
    return 0


def command_review_schedule_bind(args) -> int:
    root = _state_root(args.state_root)
    now = _now(args.now)
    _require_cycle_checkpoint(root, args.run_id, args.cycle_id)
    events = _cycle_events(load_effective_runs(root), args.cycle_id)
    intents = [
        item for item in events
        if item.kind == "review_schedule_requested"
        and item.data["review_kind"] == args.review_kind
        and int(item.data["attempt"]) == args.attempt
    ]
    if not intents:
        return _error("schedule_intent_not_found")
    if intents[-1].data["payload_digest"] != args.payload_digest:
        return _error("schedule_payload_mismatch")
    binds = [
        item for item in events
        if item.kind == "review_scheduled"
        and item.data["review_kind"] == args.review_kind
        and int(item.data["attempt"]) == args.attempt
    ]
    if binds:
        same = binds[-1].data["automation_id"] == args.automation_id and binds[-1].data["payload_digest"] == args.payload_digest
        if not same:
            return _error("schedule_binding_conflict")
        _json({"ok": True, "duplicate": True, "automation_id": args.automation_id})
        return 0
    append_checkpoint_events(
        root,
        args.run_id,
        (Event("review_scheduled", now, {"cycle_id": args.cycle_id, "review_kind": args.review_kind, "attempt": args.attempt, "automation_id": args.automation_id, "payload_digest": args.payload_digest}),),
    )
    _json({"ok": True, "duplicate": False, "automation_id": args.automation_id})
    return 0


def _require_review_checkpoint(root: Path, args):
    checkpoint = read_checkpoint(root, args.run_id)
    expected = {
        "cycle_id": args.cycle_id,
        "review_kind": args.review_kind,
        "attempt": str(args.attempt),
    }
    if checkpoint.header.mode != "review" or dict(checkpoint.header.transaction_context) != expected:
        raise CliError("review_context_mismatch")
    return checkpoint


def command_review_photo_observe(args) -> int:
    root = _state_root(args.state_root)
    now = _now(args.now)
    checkpoint = _require_review_checkpoint(root, args)
    state = rebuild_state(load_effective_runs(root), now)
    cycle = state.cycles.get(args.cycle_id)
    if cycle is None:
        return _error("cycle_not_found")
    if args.photo_id not in cycle.showcase_photo_ids:
        return _error("photo_not_in_showcase")
    photographer_ids = list(dict.fromkeys(args.photographer_id))
    if len(photographer_ids) != len(args.photographer_id):
        return _error("duplicate_photographer_id")
    if args.liker_count != len(photographer_ids):
        return _error("liker_count_mismatch", observed=len(photographer_ids))
    prior = [
        item for item in checkpoint.events
        if item.kind == "review_photo_observed" and item.data["photo_id"] == args.photo_id
    ]
    if prior:
        same = (
            prior[-1].data["scan_id"] == args.scan_id
            and prior[-1].data["photographer_ids"] == photographer_ids
        )
        if not same:
            return _error("review_photo_conflict")
        _json({"ok": True, "duplicate": True, "photo_id": args.photo_id})
        return 0
    append_checkpoint_events(
        root,
        args.run_id,
        (
            Event(
                "review_photo_observed",
                now,
                {
                    "cycle_id": args.cycle_id,
                    "review_kind": args.review_kind,
                    "attempt": args.attempt,
                    "scan_id": args.scan_id,
                    "photo_id": args.photo_id,
                    "photographer_ids": photographer_ids,
                    "observed_at": now.isoformat(),
                },
            ),
        ),
    )
    _json({"ok": True, "duplicate": False, "photo_id": args.photo_id, "liker_count": args.liker_count})
    return 0


def command_review_finish(args) -> int:
    root = _state_root(args.state_root)
    now = _now(args.now)
    checkpoint = _require_review_checkpoint(root, args)
    state = rebuild_state(load_effective_runs(root), now)
    cycle = state.cycles.get(args.cycle_id)
    if cycle is None:
        return _error("cycle_not_found")
    observed = {
        str(item.data["photo_id"])
        for item in checkpoint.events
        if item.kind == "review_photo_observed"
    }
    missing = set(cycle.showcase_photo_ids) - observed
    if missing:
        return _error("review_incomplete", missing_photo_ids=sorted(missing))
    additions = [
        Event(
            "review_completed",
            now,
            {
                "cycle_id": args.cycle_id,
                "review_kind": args.review_kind,
                "attempt": args.attempt,
                "scan_id": args.scan_id,
                "completed_at": now.isoformat(),
            },
        )
    ]
    if args.review_kind == "review_3d" and cycle.review_1d.status not in {"completed", "superseded"}:
        pending = cycle.review_1d.attempts[-1] if cycle.review_1d.attempts else None
        if pending is not None:
            additions.append(
                Event(
                    "review_superseded",
                    now,
                    {
                        "cycle_id": args.cycle_id,
                        "review_kind": "review_1d",
                        "attempt": pending.attempt,
                        "superseded_at": now.isoformat(),
                    },
                )
            )
    append_checkpoint_events(root, args.run_id, additions)
    _json({"ok": True, "cycle_id": args.cycle_id, "review_kind": args.review_kind, "observed_photo_count": len(observed)})
    return 0


def command_review_fail(args) -> int:
    root = _state_root(args.state_root)
    now = _now(args.now)
    _require_review_checkpoint(root, args)
    append_checkpoint_events(
        root,
        args.run_id,
        (
            Event(
                "review_failed",
                now,
                {
                    "cycle_id": args.cycle_id,
                    "review_kind": args.review_kind,
                    "attempt": args.attempt,
                    "reason": args.reason,
                    "failed_at": now.isoformat(),
                },
            ),
        ),
    )
    _json({"ok": True, "failed": True, "reason": args.reason})
    return 0


def _migration_analysis(root: Path, mapped_run_ids: Sequence[str], photo_ids: Sequence[str]) -> Mapping[str, object]:
    mapped_run_ids = list(dict.fromkeys(mapped_run_ids))
    photo_ids = list(dict.fromkeys(photo_ids))
    if len(mapped_run_ids) == 0:
        raise CliError("mapped_run_required")
    if len(photo_ids) != 5:
        raise CliError("showcase_requires_exactly_five")
    logs = list(iter_sealed_logs(root))
    by_id = {log.run_id: log for log in logs}
    if any(run_id not in by_id for run_id in mapped_run_ids):
        raise CliError("mapped_run_not_sealed")
    touch_events = [
        item for run_id in mapped_run_ids for item in by_id[run_id].events
        if item.kind == "outgoing_like_confirmed"
    ]
    if not touch_events:
        raise CliError("cycle_has_no_confirmed_likes")
    like_completed_at = max(item.occurred_at for item in touch_events)
    touch_action_ids = [str(item.data["action_id"]) for item in touch_events]
    episode_ids = list(dict.fromkeys(
        str(item.data["episode_id"])
        for run_id in mapped_run_ids
        for item in by_id[run_id].events
        if item.kind in {"feedback_episode_opened", "feedback_episode_extended"}
        and item.data["touch_action_id"] in touch_action_ids
    ))

    scans = []
    observation_refs = {}
    for log in logs:
        scan_starts = [item for item in log.events if item.kind == "scan_started"]
        for scan_start in scan_starts:
            scan_id = str(scan_start.data["scan_id"])
            works = {
                str(item.data["photo_id"]): item
                for item in log.events
                if item.kind == "work_observed" and item.data["scan_id"] == scan_id
            }
            issues = {
                str(item.data["photo_id"])
                for item in log.events
                if item.kind == "scan_issue" and item.data["scan_id"] == scan_id
            }
            refs = []
            pairs = []
            by_photo = {photo_id: [] for photo_id in photo_ids}
            for index, item in enumerate(log.events):
                if item.kind != "received_like_observed" or item.data["scan_id"] != scan_id:
                    continue
                photo_id = str(item.data["photo_id"])
                if photo_id not in by_photo:
                    continue
                reference = f"{log.run_id}:{index}"
                photographer_id = str(item.data["photographer_id"])
                refs.append(reference)
                pairs.append((photo_id, photographer_id))
                by_photo[photo_id].append(photographer_id)
                observation_refs[reference] = item
            complete = set(photo_ids).issubset(works) and not (set(photo_ids) & issues)
            scans.append({
                "run_id": log.run_id,
                "scan_id": scan_id,
                "started_at": scan_start.occurred_at,
                "completed_at": log.ended_at,
                "owner_id": str(scan_start.data["owner_id"]),
                "works": works,
                "complete": complete,
                "pairs": pairs,
                "refs": refs,
                "by_photo": by_photo,
            })
    baseline_candidates = [item for item in scans if item["complete"] and item["started_at"] < like_completed_at]
    review_candidates = [
        item for item in scans
        if item["complete"] and like_completed_at < item["started_at"] <= like_completed_at + WINDOW
    ]
    baseline = max(baseline_candidates, key=lambda item: item["started_at"], default=None)
    review = min(review_candidates, key=lambda item: item["started_at"], default=None)
    attribution_eligible = baseline is not None
    source_digest = _canonical_digest([
        {
            "run_id": log.run_id,
            "ended_at": log.ended_at.isoformat(),
            "events": [
                {"kind": item.kind, "occurred_at": item.occurred_at.isoformat(), "data": item.data}
                for item in log.events
            ],
        }
        for log in logs
    ])
    payload = {
        "mapped_run_ids": mapped_run_ids,
        "showcase_photo_ids": photo_ids,
        "like_completed_at": like_completed_at.isoformat(),
        "touch_action_ids": touch_action_ids,
        "episode_ids": episode_ids,
        "baseline": None if baseline is None else {
            "run_id": baseline["run_id"],
            "scan_id": baseline["scan_id"],
            "started_at": baseline["started_at"].isoformat(),
            "owner_id": baseline["owner_id"],
            "pairs": [list(pair) for pair in baseline["pairs"]],
        },
        "review_1d": None if review is None else {
            "run_id": review["run_id"],
            "scan_id": review["scan_id"],
            "started_at": review["started_at"].isoformat(),
            "completed_at": review["completed_at"].isoformat(),
            "observation_refs": review["refs"],
            "by_photo": review["by_photo"],
        },
        "attribution_eligible": attribution_eligible,
        "source_log_digest": source_digest,
    }
    payload["analysis_digest"] = _canonical_digest(payload)
    return payload


def command_cycle_migrate_analyze(args) -> int:
    root = _state_root(args.state_root)
    analysis = _migration_analysis(root, args.mapped_run_id, args.photo_id)
    _json({"ok": True, **analysis})
    return 0


def command_cycle_migrate_apply(args) -> int:
    root = _state_root(args.state_root)
    now = _now(args.now)
    checkpoint = _require_cycle_checkpoint(root, args.run_id, args.cycle_id)
    if checkpoint.header.mode != "migration":
        return _error("migration_transaction_required")
    analysis = _migration_analysis(root, args.mapped_run_id, args.photo_id)
    if analysis["analysis_digest"] != args.analysis_digest:
        return _error("migration_analysis_changed", current_digest=analysis["analysis_digest"])
    requested_eligible = args.confirm_attribution_eligible == "true"
    if requested_eligible != analysis["attribution_eligible"]:
        return _error("attribution_confirmation_mismatch", analyzed=analysis["attribution_eligible"])
    if any(item.kind == "cycle_started" for item in checkpoint.events):
        return _error("migration_already_applied")

    cycle_id = args.cycle_id
    photo_ids = list(analysis["showcase_photo_ids"])
    baseline = analysis["baseline"]
    additions = [Event("cycle_started", now, {"cycle_id": cycle_id, "attribution_eligible": requested_eligible})]
    owner_id = str(baseline["owner_id"]) if baseline else "unknown"
    work_urls = {}
    if baseline:
        source = _sealed_runs_by_id(root)[baseline["run_id"]]
        work_urls = {
            str(item.data["photo_id"]): str(item.data["photo_url"])
            for item in source.events
            if item.kind == "work_observed" and item.data["scan_id"] == baseline["scan_id"]
        }
    for position, photo_id in enumerate(photo_ids, 1):
        additions.append(Event("cycle_showcase_observed", now, {
            "cycle_id": cycle_id,
            "photo_id": photo_id,
            "photo_url": work_urls.get(photo_id, f"https://500px.com.cn/photo/{photo_id}"),
            "owner_id": owner_id,
            "visibility": "public",
            "position": position,
            "evidence_summary": "legacy homepage scope confirmed by user",
        }))
    showcase_digest = _canonical_digest(photo_ids)
    additions.append(Event("cycle_showcase_frozen", now, {"cycle_id": cycle_id, "photo_ids": photo_ids, "showcase_digest": showcase_digest}))
    baseline_digest = _canonical_digest({"photo_ids": photo_ids, "pairs": baseline["pairs"] if baseline else []})
    if baseline:
        additions.append(Event("cycle_baseline_scan_started", now, {"cycle_id": cycle_id, "scan_id": baseline["scan_id"]}))
        for photo_id, photographer_id in baseline["pairs"]:
            additions.append(Event("cycle_baseline_like_observed", now, {
                "cycle_id": cycle_id,
                "scan_id": baseline["scan_id"],
                "photo_id": photo_id,
                "photographer_id": photographer_id,
                "display_name": photographer_id,
                "profile_url": "",
            }))
        for photo_id in photo_ids:
            count = sum(pair[0] == photo_id for pair in baseline["pairs"])
            additions.append(Event("cycle_baseline_photo_completed", now, {"cycle_id": cycle_id, "scan_id": baseline["scan_id"], "photo_id": photo_id, "liker_count": count}))
        additions.append(Event("cycle_baseline_completed", now, {"cycle_id": cycle_id, "scan_id": baseline["scan_id"], "baseline_digest": baseline_digest}))
    like_completed_at = datetime.fromisoformat(str(analysis["like_completed_at"]))
    additions.append(Event("cycle_like_completed", now, {
        "cycle_id": cycle_id,
        "mapped_run_ids": list(analysis["mapped_run_ids"]),
        "touch_action_ids": list(analysis["touch_action_ids"]),
        "episode_ids": list(analysis["episode_ids"]),
        "like_completed_at": analysis["like_completed_at"],
        "terminal_status": "completed",
    }))
    requests = build_review_requests(cycle_id, like_completed_at, root)
    review = analysis["review_1d"]
    if review:
        additions.append(_schedule_event(requests[0], root, now))
        started_at = datetime.fromisoformat(str(review["started_at"]))
        additions.append(Event("review_started", started_at, {"cycle_id": cycle_id, "review_kind": "review_1d", "attempt": 1, "due_at": requests[0].due_at.isoformat(), "started_at": started_at.isoformat()}))
        for photo_id in photo_ids:
            additions.append(Event("review_photo_observed", datetime.fromisoformat(str(review["completed_at"])), {
                "cycle_id": cycle_id,
                "review_kind": "review_1d",
                "attempt": 1,
                "scan_id": review["scan_id"],
                "photo_id": photo_id,
                "photographer_ids": list(dict.fromkeys(review["by_photo"][photo_id])),
                "observed_at": review["completed_at"],
            }))
        additions.append(Event("review_completed", datetime.fromisoformat(str(review["completed_at"])), {"cycle_id": cycle_id, "review_kind": "review_1d", "attempt": 1, "scan_id": review["scan_id"], "completed_at": review["completed_at"]}))
    additions.append(_schedule_event(requests[1], root, now))
    mapping_digest = _canonical_digest({"analysis_digest": analysis["analysis_digest"], "cycle_id": cycle_id})
    additions.append(Event("cycle_attribution_scope_mapped", now, {
        "cycle_id": cycle_id,
        "mapped_run_ids": list(analysis["mapped_run_ids"]),
        "showcase_photo_ids": photo_ids,
        "touch_action_ids": list(analysis["touch_action_ids"]),
        "episode_ids": list(analysis["episode_ids"]),
        "observation_refs": list(review["observation_refs"]) if review else [],
        "attribution_eligible": requested_eligible,
        "mapping_digest": mapping_digest,
    }))
    append_checkpoint_events(root, args.run_id, additions)
    _json({"ok": True, "cycle_id": cycle_id, "event_count": len(additions), "review_3d_request": requests[1].payload, "review_3d_payload_digest": requests[1].payload_digest, "mapping_digest": mapping_digest})
    return 0


def command_preview(args) -> int:
    root = _state_root(args.state_root)
    now = _now(args.now)
    checkpoint = read_checkpoint(root, args.run_id)
    state = rebuild_state(load_effective_runs(root), now)
    daily_task_id = checkpoint.header.daily_task_id
    quota_snapshot = _quota_snapshot(state, daily_task_id)
    remaining = max(0, DAILY_PHOTOGRAPHER_TARGET - len(quota_snapshot["covered_photographers"]))
    result = select_run_candidates(
        _candidates(checkpoint, state),
        state,
        now,
        args.seed,
        remaining,
        daily_task_id=daily_task_id,
    )
    plan = list(result.selected)
    preview_id = f"preview-{uuid.uuid4().hex}"
    expires_at = now + timedelta(hours=24)
    digest = _digest(plan)
    item = Event(
        "preview_created",
        now,
        {
            "preview_id": preview_id,
            "candidate_digest": digest,
            "expires_at": expires_at.isoformat(),
            "seed": args.seed,
            "quota_snapshot": quota_snapshot,
            "candidate_ids": [entry["photographer_id"] for entry in plan],
            "candidate_plan": plan,
        },
    )
    append_checkpoint_events(root, args.run_id, (item,))
    _json(
        {
            "ok": True,
            "preview_id": preview_id,
            "candidate_digest": digest,
            "expires_at": expires_at.isoformat(),
            "candidate_plan": plan,
            "quota_snapshot": quota_snapshot,
            "status": result.status,
        }
    )
    return 0


def command_latest_preview(args) -> int:
    root = _state_root(args.state_root)
    now = _now(args.now)
    previews = _all_previews(root)
    if not previews:
        return _error("preview_not_found")
    preview, log = previews[-1]
    if now > datetime.fromisoformat(str(preview.data["expires_at"])):
        return _error("preview_expired")
    state = rebuild_state(load_effective_runs(root), now)
    if _quota_snapshot(state, log.daily_task_id) != preview.data["quota_snapshot"]:
        return _error("preview_changed")
    _json(
        {
            "ok": True,
            "preview_id": preview.data["preview_id"],
            "expires_at": preview.data["expires_at"],
            "candidate_count": len(preview.data["candidate_plan"]),
        }
    )
    return 0


def command_approve(args) -> int:
    root = _state_root(args.state_root)
    now = _now(args.now)
    previews = _all_previews(root)
    requested = [pair for pair in previews if pair[0].data["preview_id"] == args.preview_id]
    if not requested:
        return _error("preview_not_found")
    if previews[-1][0].data["preview_id"] != args.preview_id:
        return _error("preview_not_latest")
    preview, preview_log = requested[-1]
    if now > datetime.fromisoformat(str(preview.data["expires_at"])):
        return _error("preview_expired")
    checkpoint = read_checkpoint(root, args.run_id)
    if checkpoint.header.approve_preview_id != args.preview_id:
        return _error("preview_mismatch")
    if checkpoint.header.daily_task_id != preview_log.daily_task_id:
        return _error(
            "preview_mismatch",
            run_daily_task_id=checkpoint.header.daily_task_id,
            preview_daily_task_id=preview_log.daily_task_id,
        )
    state = rebuild_state(load_effective_runs(root), now)
    if _quota_snapshot(state, preview_log.daily_task_id) != preview.data["quota_snapshot"]:
        return _error("preview_changed")
    observed = {item.photographer_id: item for item in _candidates(checkpoint, state)}
    plan = list(preview.data["candidate_plan"])
    stable_fields = (
        "photographer_id",
        "display_name",
        "profile_url",
        "source_photo_id",
        "source_url",
        "page_order",
        "tier",
    )
    for expected in plan:
        current = observed.get(str(expected["photographer_id"]))
        if current is None:
            return _error("preview_changed")
        actual = {field: getattr(current, field) for field in stable_fields}
        if any(actual[field] != expected[field] for field in stable_fields):
            return _error("preview_changed")
    digest = _digest(plan)
    if digest != preview.data["candidate_digest"]:
        return _error("preview_changed")
    approval = Event(
        "onboarding_approved",
        now,
        {"preview_id": args.preview_id, "candidate_digest": digest, "approved_at": now.isoformat()},
    )
    append_checkpoint_events(root, args.run_id, (approval,))
    _json({"ok": True, "approved": True, "preview_id": args.preview_id, "candidate_plan": plan})
    return 0


def command_finish(args) -> int:
    root = _state_root(args.state_root)
    now = _now(args.now)
    checkpoint = read_checkpoint(root, args.run_id)
    if checkpoint.header.mode == "run" and args.status == "completed":
        state = rebuild_state(load_effective_runs(root), now)
        daily = state.daily_tasks.get(checkpoint.header.daily_task_id)
        covered = len(daily.covered_photographer_ids) if daily else 0
        if covered != DAILY_PHOTOGRAPHER_TARGET:
            return _error(
                "daily_incomplete",
                covered_photographers=covered,
                target=DAILY_PHOTOGRAPHER_TARGET,
            )
    likes = sum(item.kind == "outgoing_like_confirmed" for item in checkpoint.events)
    comments = sum(item.kind == "outgoing_comment_confirmed" for item in checkpoint.events)
    events = checkpoint.events + (
        Event(
            "run_finished",
            now,
            {"status": args.status, "confirmed_like_count": likes, "confirmed_comment_count": comments},
        ),
    )
    log = RunLog(
        schema_version=checkpoint.header.schema_version,
        run_id=checkpoint.header.run_id,
        daily_task_id=checkpoint.header.daily_task_id,
        mode=checkpoint.header.mode,
        status=args.status,
        started_at=checkpoint.header.started_at,
        ended_at=now,
        events=events,
    )
    path = seal_run(root, log)
    _json({"ok": True, "run_id": args.run_id, "status": args.status, "path": str(path)})
    return 0


def command_status(args) -> int:
    root = _state_root(args.state_root)
    now = _now(args.now)
    effective = load_effective_runs(root)
    state = rebuild_state(effective, now)
    day = _active_daily_task_id(root, effective) or _day(now)
    daily = state.daily_tasks.get(day)
    today = {
        "daily_task_id": day,
        "confirmed_likes": daily.confirmed_likes if daily else 0,
        "unique_photographers": len(daily.unique_photographer_ids) if daily else 0,
        "covered_photographers": len(daily.covered_photographer_ids) if daily else 0,
        "confirmed_comments": daily.confirmed_comments if daily else 0,
        "remaining_daily_quota": _remaining_photographer_quota(daily),
        "remaining_photographer_quota": _remaining_photographer_quota(daily),
        "status": daily.status if daily else "not_started",
    }
    _json(
        {
            "ok": True,
            "today": today,
            "tiers": dict(Counter(classify_photographer(item, now) for item in state.photographers.values())),
            "paused_reason": state.paused_reason,
        }
    )
    return 0


def command_dashboard(args) -> int:
    root = _state_root(args.state_root)
    now = _now(args.now)
    effective = load_effective_runs(root)
    state = rebuild_state(effective, now)
    path = generate_dashboard(root, state, now, _active_daily_task_id(root, effective), effective)
    _json({"ok": True, "path": str(path)})
    return 0


def command_doctor(args) -> int:
    root = _state_root(args.state_root)
    now = _now(args.now)
    try:
        logs = load_effective_runs(root)
        state = rebuild_state(logs, now)
        sealed_run_count = sum(1 for _ in iter_sealed_logs(root))
        eligible = {
            item.episode_id: item
            for stats in state.photographers.values()
            for item in stats.eligible_episodes
        }
        outcomes = Counter(item.outcome for item in eligible.values())
        for key in ("success", "failure", "open"):
            outcomes.setdefault(key, 0)
        checkout_root = find_checkout_root(Path(__file__))
        repository_root = find_repository_root(Path(__file__))
        git_state = inspect_git_state(checkout_root, root)
    except LogValidationError as error:
        return _error("doctor_failed", errors=["invalid_sealed_log"], message=str(error))
    except WorkspaceError as error:
        return _error("doctor_failed", errors=[str(error)])

    report = {
        "checkout_root": str(checkout_root),
        "repository_root": str(repository_root),
        "state_root": str(root),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "sealed_run_count": sealed_run_count,
        "eligible_outcomes": dict(sorted(outcomes.items())),
        "latest_cycle_id": state.latest_cycle_id,
        "git": git_state,
    }
    errors = []
    if sealed_run_count == 0:
        errors.append("sealed_runs_missing")
    if not git_state["all_sealed_runs_tracked"]:
        errors.append("untracked_sealed_runs")
    if not git_state["local_only_paths_ignored"]:
        errors.append("local_only_paths_not_ignored")
    if not git_state["tracked_only_sealed_runs_under_local"]:
        errors.append("unexpected_tracked_local_files")
    if errors:
        return _error("doctor_failed", errors=errors, report=report)
    _json({"ok": True, **report})
    return 0


def _add_common(parser) -> None:
    parser.add_argument("--state-root")
    parser.add_argument("--now")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    begin = commands.add_parser("begin")
    begin.add_argument("--mode", choices=("preflight", "run", "cycle", "review", "migration"), required=True)
    begin.add_argument("--approve-preview")
    begin.add_argument("--cycle-id")
    begin.add_argument("--review-kind", choices=("review_1d", "review_3d"))
    begin.add_argument("--attempt", type=int)
    _add_common(begin)

    resume = commands.add_parser("resume")
    resume.add_argument("--run-id", required=True)
    _add_common(resume)

    event = commands.add_parser("event")
    event.add_argument("--run-id", required=True)
    event.add_argument("--kind", required=True)
    event.add_argument("--field", action="append", default=[])
    _add_common(event)

    feedback_scan = commands.add_parser("feedback-scan-complete")
    feedback_scan.add_argument("--run-id", required=True)
    feedback_scan.add_argument("--scan-id", required=True)
    feedback_scan.add_argument("--completed-photo-id", action="append", default=[])
    _add_common(feedback_scan)

    cycle_start = commands.add_parser("cycle-start")
    cycle_start.add_argument("--run-id", required=True)
    cycle_start.add_argument("--cycle-id", required=True)
    cycle_start.add_argument("--attribution-eligible", default="true")
    _add_common(cycle_start)

    showcase_observe = commands.add_parser("cycle-showcase-observe")
    showcase_observe.add_argument("--run-id", required=True)
    showcase_observe.add_argument("--cycle-id", required=True)
    showcase_observe.add_argument("--photo-id", required=True)
    showcase_observe.add_argument("--photo-url", required=True)
    showcase_observe.add_argument("--owner-id", required=True)
    showcase_observe.add_argument("--visibility", choices=("public",), required=True)
    showcase_observe.add_argument("--position", type=int, required=True)
    showcase_observe.add_argument("--evidence-summary", required=True)
    _add_common(showcase_observe)

    showcase_freeze = commands.add_parser("cycle-showcase-freeze")
    showcase_freeze.add_argument("--run-id", required=True)
    showcase_freeze.add_argument("--cycle-id", required=True)
    showcase_freeze.add_argument("--owner-id")
    _add_common(showcase_freeze)

    baseline_start = commands.add_parser("cycle-baseline-start")
    baseline_start.add_argument("--run-id", required=True)
    baseline_start.add_argument("--cycle-id", required=True)
    baseline_start.add_argument("--scan-id", required=True)
    _add_common(baseline_start)

    baseline_observe = commands.add_parser("cycle-baseline-observe")
    baseline_observe.add_argument("--run-id", required=True)
    baseline_observe.add_argument("--cycle-id", required=True)
    baseline_observe.add_argument("--scan-id", required=True)
    baseline_observe.add_argument("--photo-id", required=True)
    baseline_observe.add_argument("--photographer-id", required=True)
    baseline_observe.add_argument("--display-name", required=True)
    baseline_observe.add_argument("--profile-url", required=True)
    _add_common(baseline_observe)

    baseline_photo = commands.add_parser("cycle-baseline-photo-complete")
    baseline_photo.add_argument("--run-id", required=True)
    baseline_photo.add_argument("--cycle-id", required=True)
    baseline_photo.add_argument("--scan-id", required=True)
    baseline_photo.add_argument("--photo-id", required=True)
    baseline_photo.add_argument("--liker-count", type=int, required=True)
    _add_common(baseline_photo)

    baseline_complete = commands.add_parser("cycle-baseline-complete")
    baseline_complete.add_argument("--run-id", required=True)
    baseline_complete.add_argument("--cycle-id", required=True)
    baseline_complete.add_argument("--scan-id", required=True)
    _add_common(baseline_complete)

    cycle_status = commands.add_parser("cycle-status")
    cycle_status.add_argument("--cycle-id", required=True)
    cycle_status.add_argument("--json", action="store_true")
    _add_common(cycle_status)

    cycle_like = commands.add_parser("cycle-like-complete")
    cycle_like.add_argument("--run-id", required=True)
    cycle_like.add_argument("--cycle-id", required=True)
    cycle_like.add_argument("--mapped-run-id", action="append", default=[], required=True)
    cycle_like.add_argument("--status", required=True)
    _add_common(cycle_like)

    schedule_intent = commands.add_parser("review-schedule-intent")
    schedule_intent.add_argument("--run-id", required=True)
    schedule_intent.add_argument("--cycle-id", required=True)
    schedule_intent.add_argument("--review-kind", choices=("review_1d", "review_3d"), required=True)
    schedule_intent.add_argument("--attempt", type=int, required=True)
    _add_common(schedule_intent)

    schedule_bind = commands.add_parser("review-schedule-bind")
    schedule_bind.add_argument("--run-id", required=True)
    schedule_bind.add_argument("--cycle-id", required=True)
    schedule_bind.add_argument("--review-kind", choices=("review_1d", "review_3d"), required=True)
    schedule_bind.add_argument("--attempt", type=int, required=True)
    schedule_bind.add_argument("--automation-id", required=True)
    schedule_bind.add_argument("--payload-digest", required=True)
    _add_common(schedule_bind)

    review_photo = commands.add_parser("review-photo-observe")
    review_photo.add_argument("--run-id", required=True)
    review_photo.add_argument("--cycle-id", required=True)
    review_photo.add_argument("--review-kind", choices=("review_1d", "review_3d"), required=True)
    review_photo.add_argument("--attempt", type=int, required=True)
    review_photo.add_argument("--scan-id", required=True)
    review_photo.add_argument("--photo-id", required=True)
    review_photo.add_argument("--liker-count", type=int, required=True)
    review_photo.add_argument("--photographer-id", action="append", default=[])
    _add_common(review_photo)

    review_finish = commands.add_parser("review-finish")
    review_finish.add_argument("--run-id", required=True)
    review_finish.add_argument("--cycle-id", required=True)
    review_finish.add_argument("--review-kind", choices=("review_1d", "review_3d"), required=True)
    review_finish.add_argument("--attempt", type=int, required=True)
    review_finish.add_argument("--scan-id", required=True)
    _add_common(review_finish)

    review_fail = commands.add_parser("review-fail")
    review_fail.add_argument("--run-id", required=True)
    review_fail.add_argument("--cycle-id", required=True)
    review_fail.add_argument("--review-kind", choices=("review_1d", "review_3d"), required=True)
    review_fail.add_argument("--attempt", type=int, required=True)
    review_fail.add_argument("--reason", required=True)
    _add_common(review_fail)

    migrate_analyze = commands.add_parser("cycle-migrate-analyze")
    migrate_analyze.add_argument("--mapped-run-id", action="append", default=[], required=True)
    migrate_analyze.add_argument("--photo-id", action="append", default=[], required=True)
    migrate_analyze.add_argument("--json", action="store_true")
    _add_common(migrate_analyze)

    migrate_apply = commands.add_parser("cycle-migrate-apply")
    migrate_apply.add_argument("--run-id", required=True)
    migrate_apply.add_argument("--cycle-id", required=True)
    migrate_apply.add_argument("--mapped-run-id", action="append", default=[], required=True)
    migrate_apply.add_argument("--photo-id", action="append", default=[], required=True)
    migrate_apply.add_argument("--analysis-digest", required=True)
    migrate_apply.add_argument("--confirm-attribution-eligible", choices=("true", "false"), required=True)
    _add_common(migrate_apply)

    preview = commands.add_parser("preview")
    preview.add_argument("--run-id", required=True)
    preview.add_argument("--seed", type=int, required=True)
    _add_common(preview)

    latest_preview = commands.add_parser("latest-preview")
    _add_common(latest_preview)

    approve = commands.add_parser("approve")
    approve.add_argument("--run-id", required=True)
    approve.add_argument("--preview-id", required=True)
    _add_common(approve)

    finish = commands.add_parser("finish")
    finish.add_argument("--run-id", required=True)
    finish.add_argument(
        "--status",
        choices=("completed", "paused_incomplete", "incomplete_candidate_exhausted", "approval_rejected"),
        required=True,
    )
    _add_common(finish)

    status = commands.add_parser("status")
    status.add_argument("--json", action="store_true")
    _add_common(status)

    dashboard = commands.add_parser("dashboard")
    _add_common(dashboard)

    doctor = commands.add_parser("doctor")
    _add_common(doctor)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    handler = {
        "begin": command_begin,
        "resume": command_resume,
        "event": command_event,
        "feedback-scan-complete": command_feedback_scan_complete,
        "cycle-start": command_cycle_start,
        "cycle-showcase-observe": command_cycle_showcase_observe,
        "cycle-showcase-freeze": command_cycle_showcase_freeze,
        "cycle-baseline-start": command_cycle_baseline_start,
        "cycle-baseline-observe": command_cycle_baseline_observe,
        "cycle-baseline-photo-complete": command_cycle_baseline_photo_complete,
        "cycle-baseline-complete": command_cycle_baseline_complete,
        "cycle-status": command_cycle_status,
        "cycle-like-complete": command_cycle_like_complete,
        "review-schedule-intent": command_review_schedule_intent,
        "review-schedule-bind": command_review_schedule_bind,
        "review-photo-observe": command_review_photo_observe,
        "review-finish": command_review_finish,
        "review-fail": command_review_fail,
        "cycle-migrate-analyze": command_cycle_migrate_analyze,
        "cycle-migrate-apply": command_cycle_migrate_apply,
        "preview": command_preview,
        "latest-preview": command_latest_preview,
        "approve": command_approve,
        "finish": command_finish,
        "status": command_status,
        "dashboard": command_dashboard,
        "doctor": command_doctor,
    }[args.command]
    try:
        return handler(args)
    except CliError as error:
        return _error(error.code, **error.details)
    except (LogValidationError, ValueError, KeyError, FileNotFoundError) as error:
        return _error("invalid_state", message=str(error))


if __name__ == "__main__":
    sys.exit(main())
