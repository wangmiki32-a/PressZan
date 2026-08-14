from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from typing import Mapping, Tuple


REVIEW_DELAYS = {"review_1d": timedelta(hours=20), "review_3d": timedelta(hours=70)}


@dataclass(frozen=True)
class ReviewAutomationRequest:
    cycle_id: str
    review_kind: str
    attempt: int
    due_at: datetime
    state_root: str

    @property
    def name(self) -> str:
        return f"500px-review-{self.cycle_id}-{self.review_kind}-{self.attempt}"

    @property
    def payload(self) -> Mapping[str, object]:
        return {
            "cycle_id": self.cycle_id,
            "review_kind": self.review_kind,
            "attempt": self.attempt,
            "due_at": self.due_at.isoformat(),
            "state_root": self.state_root,
            "prompt": (
                "执行 500px-feedback-growth 的只读周期回顾；仅扫描该 cycle 已冻结的 5 张作品，"
                "记录 observation、完成回顾并重建 Dashboard。不得点赞、评论、关注或发私信。"
            ),
        }

    @property
    def payload_digest(self) -> str:
        canonical = json.dumps(self.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_review_request(
    cycle_id: str,
    review_kind: str,
    attempt: int,
    like_completed_at: datetime,
    state_root: Path,
) -> ReviewAutomationRequest:
    if review_kind not in REVIEW_DELAYS:
        raise ValueError(f"unsupported review kind {review_kind}")
    if attempt < 1:
        raise ValueError("attempt must be positive")
    return ReviewAutomationRequest(
        cycle_id=cycle_id,
        review_kind=review_kind,
        attempt=attempt,
        due_at=like_completed_at + REVIEW_DELAYS[review_kind],
        state_root=str(state_root.resolve()),
    )


def build_review_requests(
    cycle_id: str,
    like_completed_at: datetime,
    state_root: Path,
) -> Tuple[ReviewAutomationRequest, ReviewAutomationRequest]:
    return (
        build_review_request(cycle_id, "review_1d", 1, like_completed_at, state_root),
        build_review_request(cycle_id, "review_3d", 1, like_completed_at, state_root),
    )


def matches_existing(request: ReviewAutomationRequest, payload: Mapping[str, object]) -> bool:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return request.payload_digest == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
