import json
from datetime import datetime
from pathlib import Path
import re
from typing import Any, Dict, Iterator, Mapping, Sequence, Tuple

from . import SCHEMA_VERSION
from .model import Checkpoint, CheckpointHeader, Event, RunLog


class LogValidationError(ValueError):
    pass


_EVENT_FIELDS = {
    "scan_started": ({"scan_id", "owner_id", "profile_url"}, set()),
    "work_observed": ({"scan_id", "photo_id", "photo_url", "position"}, set()),
    "received_like_observed": (
        {"scan_id", "photo_id", "work_position", "photographer_id", "display_name", "profile_url"},
        set(),
    ),
    "candidate_observed": (
        {"photographer_id", "display_name", "profile_url", "source_photo_id", "source_url", "page_order"},
        set(),
    ),
    "preview_created": (
        {"preview_id", "candidate_digest", "expires_at", "seed", "quota_snapshot", "candidate_ids", "candidate_plan"},
        set(),
    ),
    "onboarding_approved": ({"preview_id", "candidate_digest", "approved_at"}, set()),
    "outgoing_like_confirmed": (
        {"action_id", "photographer_id", "photo_id", "photo_url", "quota_bucket", "before_state", "after_state"},
        set(),
    ),
    "outgoing_comment_confirmed": (
        {"action_id", "photographer_id", "photo_id", "content", "before_state", "after_state"},
        set(),
    ),
    "feedback_episode_opened": ({"episode_id", "photographer_id", "touch_action_id", "expires_at"}, set()),
    "feedback_episode_extended": ({"episode_id", "touch_action_id", "previous_expires_at", "expires_at"}, set()),
    "feedback_episode_succeeded": (
        {"episode_id", "received_photo_id", "feedback_first_seen_at", "received_like_count"},
        set(),
    ),
    "feedback_episode_failed": ({"episode_id", "expired_at"}, set()),
    "candidate_skipped": ({"photographer_id", "reason"}, {"photo_id"}),
    "safety_paused": ({"reason", "page_url", "evidence_summary", "last_safe_action_id"}, set()),
    "run_finished": ({"status", "confirmed_like_count", "confirmed_comment_count"}, set()),
}

_JSON_FENCE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise LogValidationError("timestamp must be timezone-aware")
    return value.isoformat()


def _datetime(value: Any, source: Path, field: str) -> datetime:
    if not isinstance(value, str):
        raise LogValidationError(f"{field} must be an ISO timestamp in {source}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise LogValidationError(f"invalid {field} in {source}: {error}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LogValidationError(f"{field} must be timezone-aware in {source}")
    return parsed


def _validate_event(event: Event, source: Path) -> None:
    if event.kind not in _EVENT_FIELDS:
        raise LogValidationError(f"unknown event kind {event.kind!r} in {source}")
    required, optional = _EVENT_FIELDS[event.kind]
    keys = set(event.data)
    missing = required - keys
    extra = keys - required - optional
    if missing:
        raise LogValidationError(f"missing {sorted(missing)[0]} in {source}")
    if extra:
        raise LogValidationError(f"unexpected {sorted(extra)[0]} in {source}")
    _iso(event.occurred_at)


def _event_dict(event: Event) -> Dict[str, Any]:
    _validate_event(event, Path("<memory>"))
    return {"kind": event.kind, "occurred_at": _iso(event.occurred_at), "data": event.data}


def _event_from_dict(payload: Mapping[str, Any], source: Path) -> Event:
    required = {"kind", "occurred_at", "data"}
    if set(payload) != required:
        missing = required - set(payload)
        extra = set(payload) - required
        name = sorted(missing or extra)[0]
        raise LogValidationError(f"invalid event field {name} in {source}")
    if not isinstance(payload["data"], dict):
        raise LogValidationError(f"event data must be an object in {source}")
    event = Event(
        kind=str(payload["kind"]),
        occurred_at=_datetime(payload["occurred_at"], source, "occurred_at"),
        data=dict(payload["data"]),
    )
    _validate_event(event, source)
    return event


def _run_dict(log: RunLog) -> Dict[str, Any]:
    if log.schema_version != SCHEMA_VERSION:
        raise LogValidationError(f"unsupported schema_version {log.schema_version} in <memory>")
    return {
        "schema_version": log.schema_version,
        "run_id": log.run_id,
        "daily_task_id": log.daily_task_id,
        "mode": log.mode,
        "status": log.status,
        "started_at": _iso(log.started_at),
        "ended_at": _iso(log.ended_at),
        "events": [_event_dict(event) for event in log.events],
    }


def render_run_log(log: RunLog) -> str:
    payload = json.dumps(_run_dict(log), ensure_ascii=False, sort_keys=True, indent=2)
    return (
        f"# 500px Feedback Growth Run {log.run_id}\n\n"
        f"- Daily task: `{log.daily_task_id}`\n"
        f"- Mode: `{log.mode}`\n"
        f"- Status: `{log.status}`\n"
        f"- Events: `{len(log.events)}`\n\n"
        f"```json\n{payload}\n```\n"
    )


def _run_from_dict(payload: Mapping[str, Any], source: Path) -> RunLog:
    required = {"schema_version", "run_id", "daily_task_id", "mode", "status", "started_at", "ended_at", "events"}
    if set(payload) != required:
        name = sorted((required - set(payload)) or (set(payload) - required))[0]
        raise LogValidationError(f"invalid run field {name} in {source}")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise LogValidationError(f"unsupported schema_version {payload['schema_version']} in {source}")
    if not isinstance(payload["events"], list):
        raise LogValidationError(f"events must be an array in {source}")
    log = RunLog(
        schema_version=SCHEMA_VERSION,
        run_id=str(payload["run_id"]),
        daily_task_id=str(payload["daily_task_id"]),
        mode=str(payload["mode"]),
        status=str(payload["status"]),
        started_at=_datetime(payload["started_at"], source, "started_at"),
        ended_at=_datetime(payload["ended_at"], source, "ended_at"),
        events=tuple(_event_from_dict(item, source) for item in payload["events"]),
    )
    if not log.run_id or not log.daily_task_id:
        raise LogValidationError(f"run identifiers must be non-empty in {source}")
    if log.ended_at < log.started_at:
        raise LogValidationError(f"ended_at precedes started_at in {source}")
    return log


def parse_run_log(path: Path) -> RunLog:
    text = path.read_text(encoding="utf-8")
    matches = _JSON_FENCE.findall(text)
    if len(matches) != 1:
        raise LogValidationError(f"expected exactly one json fence in {path}")
    try:
        payload = json.loads(matches[0])
    except json.JSONDecodeError as error:
        raise LogValidationError(f"invalid JSON in {path}: {error}") from error
    if not isinstance(payload, dict):
        raise LogValidationError(f"run payload must be an object in {path}")
    return _run_from_dict(payload, path)


def _checkpoint_path(root: Path, run_id: str) -> Path:
    return root / "checkpoints" / f"{run_id}.md"


def _header_dict(header: CheckpointHeader) -> Dict[str, Any]:
    return {
        "schema_version": header.schema_version,
        "run_id": header.run_id,
        "daily_task_id": header.daily_task_id,
        "mode": header.mode,
        "started_at": _iso(header.started_at),
        "approve_preview_id": header.approve_preview_id,
    }


def begin_checkpoint(root: Path, header: CheckpointHeader) -> Path:
    if header.schema_version != SCHEMA_VERSION:
        raise LogValidationError(f"unsupported schema_version {header.schema_version} in checkpoint")
    path = _checkpoint_path(root, header.run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_header_dict(header), ensure_ascii=False, sort_keys=True, indent=2)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(f"# Active Run {header.run_id}\n\n```json\n{payload}\n```\n")
    return path


def append_checkpoint_events(root: Path, run_id: str, events: Sequence[Event]) -> None:
    path = _checkpoint_path(root, run_id)
    if not path.exists():
        raise LogValidationError(f"checkpoint header missing for {run_id}")
    with path.open("a", encoding="utf-8") as handle:
        for event in events:
            payload = json.dumps(_event_dict(event), ensure_ascii=False, sort_keys=True)
            handle.write(f"\n```json\n{payload}\n```\n")


def append_checkpoint(root: Path, run_id: str, event: Event) -> None:
    append_checkpoint_events(root, run_id, (event,))


def _header_from_dict(payload: Mapping[str, Any], source: Path) -> CheckpointHeader:
    required = {"schema_version", "run_id", "daily_task_id", "mode", "started_at", "approve_preview_id"}
    if set(payload) != required:
        name = sorted((required - set(payload)) or (set(payload) - required))[0]
        raise LogValidationError(f"invalid checkpoint header field {name} in {source}")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise LogValidationError(f"unsupported schema_version {payload['schema_version']} in {source}")
    preview_id = payload["approve_preview_id"]
    if preview_id is not None and not isinstance(preview_id, str):
        raise LogValidationError(f"approve_preview_id must be string or null in {source}")
    return CheckpointHeader(
        schema_version=SCHEMA_VERSION,
        run_id=str(payload["run_id"]),
        daily_task_id=str(payload["daily_task_id"]),
        mode=str(payload["mode"]),
        started_at=_datetime(payload["started_at"], source, "started_at"),
        approve_preview_id=preview_id,
    )


def read_checkpoint(root: Path, run_id: str) -> Checkpoint:
    path = _checkpoint_path(root, run_id)
    matches = _JSON_FENCE.findall(path.read_text(encoding="utf-8"))
    if not matches:
        raise LogValidationError(f"checkpoint header missing in {path}")
    try:
        payloads = [json.loads(match) for match in matches]
    except json.JSONDecodeError as error:
        raise LogValidationError(f"invalid checkpoint JSON in {path}: {error}") from error
    header = _header_from_dict(payloads[0], path)
    events = tuple(_event_from_dict(item, path) for item in payloads[1:])
    return Checkpoint(header=header, events=events)


def iter_recoverable_checkpoints(root: Path) -> Iterator[Checkpoint]:
    checkpoint_dir = root / "checkpoints"
    if not checkpoint_dir.exists():
        return
    for path in sorted(checkpoint_dir.glob("*.md")):
        yield read_checkpoint(root, path.stem)


def seal_run(root: Path, log: RunLog) -> Path:
    path = root / "runs" / f"{log.run_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(render_run_log(log))
    return path


def iter_sealed_logs(root: Path) -> Iterator[RunLog]:
    run_dir = root / "runs"
    if not run_dir.exists():
        return
    for path in sorted(run_dir.glob("*.md")):
        yield parse_run_log(path)


def load_effective_runs(root: Path) -> Tuple[RunLog, ...]:
    sealed = tuple(iter_sealed_logs(root))
    sealed_ids = {log.run_id for log in sealed}
    active = []
    active_by_day: Dict[str, str] = {}
    for checkpoint in iter_recoverable_checkpoints(root):
        if checkpoint.header.run_id in sealed_ids:
            continue
        existing = active_by_day.get(checkpoint.header.daily_task_id)
        if existing is not None:
            raise LogValidationError(
                f"multiple active checkpoints for {checkpoint.header.daily_task_id}: {existing}, {checkpoint.header.run_id}"
            )
        active_by_day[checkpoint.header.daily_task_id] = checkpoint.header.run_id
        ended_at = checkpoint.events[-1].occurred_at if checkpoint.events else checkpoint.header.started_at
        active.append(
            RunLog(
                schema_version=checkpoint.header.schema_version,
                run_id=checkpoint.header.run_id,
                daily_task_id=checkpoint.header.daily_task_id,
                mode=checkpoint.header.mode,
                status="active",
                started_at=checkpoint.header.started_at,
                ended_at=ended_at,
                events=checkpoint.events,
            )
        )
    return tuple(sorted(sealed + tuple(active), key=lambda log: (log.started_at, log.run_id)))
