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
from .selector import BATCH_TARGET, DAILY_TARGET, select_batch
from .store import (
    LogValidationError,
    append_checkpoint_events,
    begin_checkpoint,
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


def command_begin(args) -> int:
    root = _state_root(args.state_root)
    now = _now(args.now)
    effective = load_effective_runs(root)
    active = [log for log in effective if log.status == "active"]
    if active:
        return _error("recoverable_run", recoverable_run_id=active[-1].run_id)
    if args.mode == "run" and not args.approve_preview and not _events_have_approval(effective):
        return _error("preflight_required")
    state = rebuild_state(effective, now)
    daily = state.daily_tasks.get(_day(now))
    if args.mode == "run" and daily and daily.confirmed_likes >= DAILY_TARGET:
        return _error("daily_complete")
    run_id = f"{args.mode}-{uuid.uuid4().hex}"
    daily_task_id = _day(now)
    begin_checkpoint(root, CheckpointHeader(SCHEMA_VERSION, run_id, daily_task_id, args.mode, now, args.approve_preview))
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
        }
    )
    return 0


def command_resume(args) -> int:
    root = _state_root(args.state_root)
    checkpoint = read_checkpoint(root, args.run_id)
    _json(
        {
            "ok": True,
            "header": {
                "run_id": checkpoint.header.run_id,
                "daily_task_id": checkpoint.header.daily_task_id,
                "mode": checkpoint.header.mode,
                "started_at": checkpoint.header.started_at.isoformat(),
                "approve_preview_id": checkpoint.header.approve_preview_id,
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


def command_preview(args) -> int:
    root = _state_root(args.state_root)
    now = _now(args.now)
    checkpoint = read_checkpoint(root, args.run_id)
    state = rebuild_state(load_effective_runs(root), now)
    result = select_batch(_candidates(checkpoint, state), state, now, args.seed, BATCH_TARGET)
    plan = list(result.selected)
    preview_id = f"preview-{uuid.uuid4().hex}"
    expires_at = now + timedelta(hours=24)
    digest = _digest(plan)
    quota_snapshot = _quota_snapshot(state, now)
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
    regenerated = select_batch(
        _candidates(checkpoint, state),
        state,
        now,
        int(preview.data["seed"]),
        BATCH_TARGET,
    )
    plan = list(regenerated.selected)
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
    begin.add_argument("--mode", choices=("preflight", "run"), required=True)
    begin.add_argument("--approve-preview")
    _add_common(begin)

    resume = commands.add_parser("resume")
    resume.add_argument("--run-id", required=True)
    _add_common(resume)

    event = commands.add_parser("event")
    event.add_argument("--run-id", required=True)
    event.add_argument("--kind", required=True)
    event.add_argument("--field", action="append", default=[])
    _add_common(event)

    preview = commands.add_parser("preview")
    preview.add_argument("--run-id", required=True)
    preview.add_argument("--seed", type=int, required=True)
    _add_common(preview)

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
        "preview": command_preview,
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
