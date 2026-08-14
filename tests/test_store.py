import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests import bootstrap  # noqa: F401
from feedback_growth.model import CheckpointHeader, Event, RunLog
from feedback_growth.store import (
    LogValidationError,
    append_checkpoint,
    begin_checkpoint,
    load_effective_runs,
    parse_run_log,
    read_checkpoint,
    render_run_log,
    seal_run,
)


UTC = timezone.utc


def utc(hour, minute=0):
    return datetime(2026, 8, 12, hour, minute, tzinfo=UTC)


def make_log(run_id="run-1"):
    return RunLog(
        schema_version=1,
        run_id=run_id,
        daily_task_id="2026-08-12",
        mode="preflight",
        status="completed",
        started_at=utc(9),
        ended_at=utc(9, 1),
        events=(
            Event(
                kind="scan_started",
                occurred_at=utc(9),
                data={
                    "scan_id": "scan-1",
                    "owner_id": "owner-1",
                    "profile_url": "https://example.test/owner",
                },
            ),
            Event(
                kind="run_finished",
                occurred_at=utc(9, 1),
                data={
                    "status": "completed",
                    "confirmed_like_count": 0,
                    "confirmed_comment_count": 0,
                },
            ),
        ),
    )


class StoreTest(unittest.TestCase):
    def test_sealed_log_round_trip(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = seal_run(root, make_log())

            self.assertEqual(parse_run_log(path), make_log())
            self.assertEqual(path.read_text(encoding="utf-8").count("```json"), 1)

    def test_unknown_schema_names_source_path(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "bad.md"
            payload = json.loads(render_run_log(make_log()).split("```json\n", 1)[1].split("\n```", 1)[0])
            payload["schema_version"] = 99
            path.write_text(f"# Bad\n\n```json\n{json.dumps(payload)}\n```\n", encoding="utf-8")

            with self.assertRaisesRegex(LogValidationError, "schema_version.*bad.md"):
                parse_run_log(path)

    def test_missing_required_event_field_names_source_path(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "missing.md"
            payload = json.loads(render_run_log(make_log()).split("```json\n", 1)[1].split("\n```", 1)[0])
            del payload["events"][1]["data"]["confirmed_like_count"]
            path.write_text(f"# Missing\n\n```json\n{json.dumps(payload)}\n```\n", encoding="utf-8")

            with self.assertRaisesRegex(LogValidationError, "confirmed_like_count.*missing.md"):
                parse_run_log(path)

    def test_seal_refuses_overwrite(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = seal_run(root, make_log())
            original = path.read_bytes()

            with self.assertRaises(FileExistsError):
                seal_run(root, make_log())

            self.assertEqual(path.read_bytes(), original)

    def test_checkpoint_preserves_header_and_event_order(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            header = CheckpointHeader(1, "run-1", "2026-08-12", "run", utc(9), None)
            begin_checkpoint(root, header)
            for event in make_log().events:
                append_checkpoint(root, "run-1", event)

            checkpoint = read_checkpoint(root, "run-1")
            self.assertEqual(checkpoint.header, header)
            self.assertEqual(checkpoint.events, make_log().events)

    def test_checkpoint_accepts_read_only_scan_issue(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            header = CheckpointHeader(1, "preflight-1", "2026-08-12", "preflight", utc(9), None)
            issue = Event(
                kind="scan_issue",
                occurred_at=utc(9, 1),
                data={
                    "scan_id": "scan-1",
                    "photo_id": "photo-8",
                    "reason": "liker_list_unavailable",
                    "evidence_summary": "53 likes visible; liker popover empty after one retry",
                },
            )
            begin_checkpoint(root, header)

            append_checkpoint(root, "preflight-1", issue)

            self.assertEqual(read_checkpoint(root, "preflight-1").events, (issue,))

    def test_sealed_log_wins_over_retained_checkpoint(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            header = CheckpointHeader(1, "run-1", "2026-08-12", "run", utc(9), None)
            begin_checkpoint(root, header)
            append_checkpoint(root, "run-1", make_log().events[0])
            seal_run(root, make_log())

            effective = load_effective_runs(root)
            self.assertEqual(effective, (make_log(),))

    def test_sealed_run_rejects_late_checkpoint_events(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            header = CheckpointHeader(1, "run-1", "2026-08-12", "run", utc(9), None)
            begin_checkpoint(root, header)
            seal_run(root, make_log())

            with self.assertRaisesRegex(LogValidationError, "already sealed"):
                append_checkpoint(root, "run-1", make_log().events[0])

    def test_duplicate_active_daily_task_is_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            begin_checkpoint(root, CheckpointHeader(1, "run-1", "2026-08-12", "run", utc(9), None))
            begin_checkpoint(root, CheckpointHeader(1, "run-2", "2026-08-12", "run", utc(10), None))

            with self.assertRaisesRegex(LogValidationError, "run-1.*run-2|run-2.*run-1"):
                load_effective_runs(root)

    def test_checkpoint_becomes_active_effective_log(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            header = CheckpointHeader(1, "run-1", "2026-08-12", "run", utc(9), None)
            begin_checkpoint(root, header)
            append_checkpoint(root, "run-1", make_log().events[0])

            effective = load_effective_runs(root)
            self.assertEqual(effective[0].status, "active")
            self.assertEqual(effective[0].ended_at, utc(9))
