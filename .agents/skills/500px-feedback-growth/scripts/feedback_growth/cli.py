import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
import uuid
from zoneinfo import ZoneInfo

from . import SCHEMA_VERSION
from .analytics import WINDOW, classify_photographer, rebuild_state
from .dashboard import generate_dashboard
from .model import Candidate, CheckpointHeader, Event, RunLog
from .selector import DAILY_TARGET, select_run_candidates
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
    if value:
        return Path(value).resolve()
    project_root = Path(__file__).resolve().parents[5]
    return project_root / ".local" / "500px-feedback-growth"


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
        is_retest = bool(stats and (stats.failure_count == 1 or (tier == "dormant" and stats.dormant_retest_eligible)))
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


def _quota_snapshot(state, now: datetime) -> Mapping[str, object]:
    daily = state.daily_tasks.get(_day(now))
    return {
        "confirmed_likes": daily.confirmed_likes if daily else 0,
        "quota_counts": dict(daily.quota_counts) if daily else {},
        "unique_photographers": sorted(daily.unique_photographer_ids) if daily else [],
    }


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
    active_ids = {log.run_id for log in effective if log.status == "active"}
    requested_header = CheckpointHeader(
        SCHEMA_VERSION,
        "requested",
        _day(now),
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
        code = "recoverable_run" if same_day or args.mode == "review" else "stale_recoverable_run"
        return _error(code, recoverable_run_id=checkpoint.header.run_id)
    if args.mode == "run" and not args.approve_preview and not _events_have_approval(effective):
        return _error("preflight_required")
    state = rebuild_state(effective, now)
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
    daily = state.daily_tasks.get(_day(now))
    if args.mode == "run" and daily and daily.confirmed_likes >= DAILY_TARGET:
        return _error("daily_complete")
    run_id = f"{args.mode}-{uuid.uuid4().hex}"
    daily_task_id = _day(now)
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
    if args.mode in {"run", "preflight"}:
        _append_expired_episodes(root, run_id, now, state)
    remaining = DAILY_TARGET - (daily.confirmed_likes if daily else 0)
    _json(
        {
            "ok": True,
            "run_id": run_id,
            "daily_task_id": daily_task_id,
            "remaining_daily_quota": remaining,
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
    if checkpoint.header.mode in {"run", "preflight"} and checkpoint.header.daily_task_id != _day(now):
        return _error("daily_task_expired", daily_task_id=checkpoint.header.daily_task_id)
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
    if checkpoint.header.mode in {"run", "preflight"} and checkpoint.header.daily_task_id != _day(now):
        return _error("daily_task_expired", daily_task_id=checkpoint.header.daily_task_id)
    if any(item.kind == "safety_paused" for item in checkpoint.events) and args.kind.startswith("outgoing_"):
        return _error("run_paused")
    data = _parse_fields(args.field)
    event = Event(args.kind, now, data)
    additions = [event]
    state = rebuild_state(load_effective_runs(root), now)
    if args.kind == "outgoing_like_confirmed":
        day = checkpoint.header.daily_task_id
        daily = state.daily_tasks.get(day)
        if daily and daily.confirmed_likes >= DAILY_TARGET:
            return _error("daily_complete")
        expected_action = _action_id(day, str(data["photographer_id"]), str(data["photo_id"]), args.kind)
        if data.get("action_id") != expected_action:
            return _error("invalid_action_id", expected=expected_action)
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
    elif args.kind == "received_like_observed":
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


def command_preview(args) -> int:
    root = _state_root(args.state_root)
    now = _now(args.now)
    checkpoint = read_checkpoint(root, args.run_id)
    state = rebuild_state(load_effective_runs(root), now)
    quota_snapshot = _quota_snapshot(state, now)
    remaining = DAILY_TARGET - int(quota_snapshot["confirmed_likes"])
    result = select_run_candidates(_candidates(checkpoint, state), state, now, args.seed, remaining)
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
    if log.daily_task_id != _day(now):
        return _error("preview_not_current_day")
    if now > datetime.fromisoformat(str(preview.data["expires_at"])):
        return _error("preview_expired")
    state = rebuild_state(load_effective_runs(root), now)
    if _quota_snapshot(state, now) != preview.data["quota_snapshot"]:
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
    preview = requested[-1][0]
    if now > datetime.fromisoformat(str(preview.data["expires_at"])):
        return _error("preview_expired")
    checkpoint = read_checkpoint(root, args.run_id)
    if checkpoint.header.approve_preview_id != args.preview_id:
        return _error("preview_mismatch")
    state = rebuild_state(load_effective_runs(root), now)
    if _quota_snapshot(state, now) != preview.data["quota_snapshot"]:
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
        confirmed = daily.confirmed_likes if daily else 0
        if confirmed != DAILY_TARGET:
            return _error("daily_incomplete", confirmed_likes=confirmed, target=DAILY_TARGET)
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
    state = rebuild_state(load_effective_runs(root), now)
    day = _day(now)
    daily = state.daily_tasks.get(day)
    today = {
        "daily_task_id": day,
        "confirmed_likes": daily.confirmed_likes if daily else 0,
        "unique_photographers": len(daily.unique_photographer_ids) if daily else 0,
        "confirmed_comments": daily.confirmed_comments if daily else 0,
        "remaining_daily_quota": DAILY_TARGET - (daily.confirmed_likes if daily else 0),
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
    state = rebuild_state(load_effective_runs(root), now)
    path = generate_dashboard(root, state, now)
    _json({"ok": True, "path": str(path)})
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
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    handler = {
        "begin": command_begin,
        "resume": command_resume,
        "event": command_event,
        "cycle-start": command_cycle_start,
        "cycle-showcase-observe": command_cycle_showcase_observe,
        "cycle-showcase-freeze": command_cycle_showcase_freeze,
        "cycle-baseline-start": command_cycle_baseline_start,
        "cycle-baseline-observe": command_cycle_baseline_observe,
        "cycle-baseline-photo-complete": command_cycle_baseline_photo_complete,
        "cycle-baseline-complete": command_cycle_baseline_complete,
        "cycle-status": command_cycle_status,
        "preview": command_preview,
        "latest-preview": command_latest_preview,
        "approve": command_approve,
        "finish": command_finish,
        "status": command_status,
        "dashboard": command_dashboard,
    }[args.command]
    try:
        return handler(args)
    except CliError as error:
        return _error(error.code, **error.details)
    except (LogValidationError, ValueError, KeyError, FileNotFoundError) as error:
        return _error("invalid_state", message=str(error))


if __name__ == "__main__":
    sys.exit(main())
