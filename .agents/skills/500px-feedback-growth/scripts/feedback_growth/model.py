from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Tuple


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
