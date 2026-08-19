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
    "scan_started": ({"scan_id", "owner_id", "profile_url"}, {"purpose"}),
    "work_observed": ({"scan_id", "photo_id", "photo_url", "position"}, set()),
    "received_like_observed": (
        {"scan_id", "photo_id", "work_position", "photographer_id", "display_name", "profile_url"},
        set(),
    ),
    "candidate_observed": (
        {"photographer_id", "display_name", "profile_url", "source_photo_id", "source_url", "page_order"},
        set(),
    ),
    "scan_issue": ({"scan_id", "photo_id", "reason", "evidence_summary"}, set()),
    "feedback_scan_completed": (
        {
            "scan_id",
            "photo_ids",
            "completed_photo_ids",
            "baseline_photo_ids",
            "new_pair_count",
            "new_feedback_photographer_count",
            "new_feedback_points",
            "completed_at",
        },
        set(),
    ),
    "preview_created": (
        {"preview_id", "candidate_digest", "expires_at", "seed", "quota_snapshot", "candidate_ids", "candidate_plan"},
        set(),
    ),
    "onboarding_approved": ({"preview_id", "candidate_digest", "approved_at"}, set()),
    "outgoing_like_confirmed": (
        {"action_id", "photographer_id", "photo_id", "photo_url", "quota_bucket", "before_state", "after_state"},
        {"settlement_mode"},
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
    "candidate_skipped": ({"photographer_id", "reason"}, {"photo_id", "quota_bucket"}),
    "safety_paused": ({"reason", "page_url", "evidence_summary", "last_safe_action_id"}, set()),
    "run_finished": ({"status", "confirmed_like_count", "confirmed_comment_count"}, set()),
    "cycle_started": ({"cycle_id", "attribution_eligible"}, set()),
    "cycle_showcase_observed": (
        {"cycle_id", "photo_id", "photo_url", "owner_id", "visibility", "position", "evidence_summary"},
        set(),
    ),
    "cycle_showcase_frozen": ({"cycle_id", "photo_ids", "showcase_digest"}, set()),
    "cycle_baseline_scan_started": ({"cycle_id", "scan_id"}, set()),
    "cycle_baseline_like_observed": (
        {"cycle_id", "scan_id", "photo_id", "photographer_id", "display_name", "profile_url"},
        set(),
    ),
    "cycle_baseline_photo_completed": ({"cycle_id", "scan_id", "photo_id", "liker_count"}, set()),
    "cycle_baseline_completed": ({"cycle_id", "scan_id", "baseline_digest"}, set()),
    "cycle_run_bound": ({"cycle_id", "run_id", "baseline_digest", "bound_at"}, set()),
    "cycle_like_completed": (
        {"cycle_id", "mapped_run_ids", "touch_action_ids", "episode_ids", "like_completed_at", "terminal_status"},
        set(),
    ),
    "review_schedule_requested": (
        {"cycle_id", "review_kind", "attempt", "due_at", "state_root", "automation_name", "payload_digest"},
        set(),
    ),
    "review_scheduled": (
        {"cycle_id", "review_kind", "attempt", "automation_id", "payload_digest"},
        set(),
    ),
    "review_started": ({"cycle_id", "review_kind", "attempt", "due_at", "started_at"}, set()),
    "review_photo_observed": (
        {"cycle_id", "review_kind", "attempt", "scan_id", "photo_id", "photographer_ids", "observed_at"},
        set(),
    ),
    "review_completed": (
        {"cycle_id", "review_kind", "attempt", "scan_id", "completed_at"},
        set(),
    ),
    "review_failed": ({"cycle_id", "review_kind", "attempt", "reason", "failed_at"}, set()),
    "review_superseded": ({"cycle_id", "review_kind", "attempt", "superseded_at"}, set()),
    "cycle_abandoned": ({"cycle_id", "reason", "abandoned_at"}, set()),
    "cycle_attribution_scope_mapped": (
        {
            "cycle_id",
            "mapped_run_ids",
            "showcase_photo_ids",
            "touch_action_ids",
            "episode_ids",
            "observation_refs",
            "attribution_eligible",
            "mapping_digest",
        },
        set(),
    ),
}

_UNIQUE_STRING_LIST_FIELDS = {
    "photo_ids",
    "mapped_run_ids",
    "touch_action_ids",
    "episode_ids",
    "photographer_ids",
    "showcase_photo_ids",
    "observation_refs",
    "completed_photo_ids",
    "baseline_photo_ids",
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
    for field in _UNIQUE_STRING_LIST_FIELDS & keys:
        value = event.data[field]
        if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
            raise LogValidationError(f"{field} must be a string list in {source}")
        if len(value) != len(set(value)):
            raise LogValidationError(f"{field} must contain unique values in {source}")
    if event.kind == "scan_started" and event.data.get("purpose") not in {None, "latest_three_feedback"}:
        raise LogValidationError(f"invalid scan purpose in {source}")
    if event.kind == "outgoing_like_confirmed" and event.data.get("settlement_mode") not in {None, "immediate", "legacy"}:
        raise LogValidationError(f"invalid settlement_mode in {source}")
    if event.kind == "feedback_scan_completed":
        for field in ("new_pair_count", "new_feedback_photographer_count", "new_feedback_points"):
            value = event.data[field]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise LogValidationError(f"{field} must be a non-negative integer in {source}")
        photo_ids = set(event.data["photo_ids"])
        completed_photo_ids = set(event.data["completed_photo_ids"])
        baseline_photo_ids = set(event.data["baseline_photo_ids"])
        if not completed_photo_ids <= photo_ids:
            raise LogValidationError(f"completed_photo_ids must be a subset of photo_ids in {source}")
        if not baseline_photo_ids <= completed_photo_ids:
            raise LogValidationError(f"baseline_photo_ids must be a subset of completed_photo_ids in {source}")
        _datetime(event.data["completed_at"], source, "completed_at")
    if event.kind == "cycle_showcase_observed" and event.data["visibility"] != "public":
        raise LogValidationError(f"visibility must be public in {source}")
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
        "transaction_context": dict(header.transaction_context),
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
    if (root / "runs" / f"{run_id}.md").exists():
        raise LogValidationError(f"run {run_id} is already sealed")
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
    allowed = required | {"transaction_context"}
    if not required <= set(payload) or not set(payload) <= allowed:
        name = sorted((required - set(payload)) or (set(payload) - allowed))[0]
        raise LogValidationError(f"invalid checkpoint header field {name} in {source}")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise LogValidationError(f"unsupported schema_version {payload['schema_version']} in {source}")
    preview_id = payload["approve_preview_id"]
    if preview_id is not None and not isinstance(preview_id, str):
        raise LogValidationError(f"approve_preview_id must be string or null in {source}")
    context = payload.get("transaction_context", {})
    if not isinstance(context, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) or not key or not value
        for key, value in context.items()
    ):
        raise LogValidationError(f"transaction_context must be a string object in {source}")
    return CheckpointHeader(
        schema_version=SCHEMA_VERSION,
        run_id=str(payload["run_id"]),
        daily_task_id=str(payload["daily_task_id"]),
        mode=str(payload["mode"]),
        started_at=_datetime(payload["started_at"], source, "started_at"),
        approve_preview_id=preview_id,
        transaction_context=dict(context),
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
    active_by_key: Dict[Tuple[str, ...], str] = {}
    for checkpoint in iter_recoverable_checkpoints(root):
        if checkpoint.header.run_id in sealed_ids:
            continue
        header = checkpoint.header
        context = header.transaction_context
        if header.mode in {"run", "preflight"}:
            key = ("daily", header.daily_task_id)
        elif header.mode == "review":
            key = (
                "review",
                context.get("cycle_id", ""),
                context.get("review_kind", ""),
                context.get("attempt", ""),
            )
        else:
            key = (header.mode, context.get("cycle_id", header.run_id))
        existing = active_by_key.get(key)
        if existing is not None:
            raise LogValidationError(
                f"multiple active checkpoints for {key}: {existing}, {checkpoint.header.run_id}"
            )
        active_by_key[key] = checkpoint.header.run_id
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
