from datetime import datetime, timezone

from feedback_growth.model import Event, RunLog


UTC = timezone.utc


def dt(day=12, hour=12, minute=0):
    return datetime(2026, 8, day, hour, minute, tzinfo=UTC)


def event(kind, occurred_at, **data):
    return Event(kind=kind, occurred_at=occurred_at, data=data)


def run(events, run_id="run-1", day="2026-08-12", status="completed"):
    events = tuple(events)
    start = events[0].occurred_at if events else dt()
    end = events[-1].occurred_at if events else start
    return RunLog(1, run_id, day, "run", status, start, end, events)


def confirmed_like(action_id, photographer_id, occurred_at, bucket="exploit_first", photo_id=None):
    photo_id = photo_id or f"photo-{action_id}"
    return event(
        "outgoing_like_confirmed",
        occurred_at,
        action_id=action_id,
        photographer_id=photographer_id,
        photo_id=photo_id,
        photo_url=f"https://example.test/{photo_id}",
        quota_bucket=bucket,
        before_state="not_liked",
        after_state="liked",
    )


def opened(episode_id, photographer_id, action_id, occurred_at, expires_at):
    return event(
        "feedback_episode_opened",
        occurred_at,
        episode_id=episode_id,
        photographer_id=photographer_id,
        touch_action_id=action_id,
        expires_at=expires_at.isoformat(),
    )


def received(photo_id, photographer_id, occurred_at, position=1):
    return event(
        "received_like_observed",
        occurred_at,
        scan_id=f"scan-{occurred_at.timestamp()}",
        photo_id=photo_id,
        work_position=position,
        photographer_id=photographer_id,
        display_name=photographer_id,
        profile_url=f"https://example.test/{photographer_id}",
    )
