import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests import bootstrap  # noqa: F401
from feedback_growth import model
from feedback_growth.model import AggregateState, CheckpointHeader, Event, OutgoingTouch, PhotographerStats, RunLog
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
    def test_legacy_aggregate_constructor_gets_empty_immediate_feedback_state(self):
        self.assertTrue(hasattr(model, "FeedbackScan"))
        self.assertTrue(hasattr(model, "TouchFeedbackEvidence"))
        state = AggregateState({}, frozenset(), {}, None, {}, ())

        self.assertEqual(state.feedback_scans, ())
        self.assertEqual(state.touch_feedback, {})
        self.assertEqual(state.baselined_photo_ids, frozenset())
        touch = OutgoingTouch("a1", "p1", "photo-1", utc(9), "e1", "new")
        self.assertEqual(touch.settlement_mode, "legacy")
        stats = PhotographerStats("p1", "p1", "", frozenset(), {}, False, (), (), None, (), 0, 0, False)
        self.assertEqual(stats.raw_feedback_points, 0)
        self.assertEqual(stats.effective_feedback_points, 0.0)
        self.assertIsNone(stats.last_unanswered_touch_at)

    def test_scan_purpose_and_settlement_mode_round_trip(self):
        scan = Event(
            "scan_started",
            utc(9),
            {
                "scan_id": "scan-3",
                "owner_id": "owner-1",
                "profile_url": "https://example.test/owner",
                "purpose": "latest_three_feedback",
            },
        )
        touch = Event(
            "outgoing_like_confirmed",
            utc(9, 1),
            {
                "action_id": "a1",
                "photographer_id": "p1",
                "photo_id": "photo-1",
                "photo_url": "https://example.test/photo-1",
                "quota_bucket": "new",
                "before_state": "not_liked",
                "after_state": "liked",
                "settlement_mode": "immediate",
            },
        )
        log = replace(make_log("mode-run"), events=(scan, touch))

        with TemporaryDirectory() as directory:
            path = seal_run(Path(directory), log)

            self.assertEqual(parse_run_log(path).events, (scan, touch))

    def test_feedback_scan_completed_round_trip(self):
        completed_at = utc(9, 10)
        item = Event(
            kind="feedback_scan_completed",
            occurred_at=completed_at,
            data={
                "scan_id": "scan-3",
                "photo_ids": ["mine-1", "mine-2", "mine-3"],
                "completed_photo_ids": ["mine-1", "mine-3"],
                "baseline_photo_ids": ["mine-3"],
                "new_pair_count": 2,
                "new_feedback_photographer_count": 1,
                "new_feedback_points": 2,
                "completed_at": completed_at.isoformat(),
            },
        )
        log = replace(make_log("scan-run"), events=(item,))

        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = seal_run(root, log)
            rebuilt = parse_run_log(path)

        self.assertEqual(rebuilt.events, (item,))

    def test_feedback_scan_rejects_duplicate_photo_ids(self):
        completed_at = utc(9, 10)
        item = Event(
            kind="feedback_scan_completed",
            occurred_at=completed_at,
            data={
                "scan_id": "scan-3",
                "photo_ids": ["mine-1", "mine-1", "mine-3"],
                "completed_photo_ids": ["mine-1"],
                "baseline_photo_ids": [],
                "new_pair_count": 0,
                "new_feedback_photographer_count": 0,
                "new_feedback_points": 0,
                "completed_at": completed_at.isoformat(),
            },
        )
        log = replace(make_log("scan-run"), events=(item,))

        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(LogValidationError, "photo_ids must contain unique"):
                seal_run(Path(directory), log)

    def test_feedback_scan_rejects_negative_counts(self):
        completed_at = utc(9, 10)
        item = Event(
            "feedback_scan_completed",
            completed_at,
            {
                "scan_id": "scan-3",
                "photo_ids": ["mine-1", "mine-2", "mine-3"],
                "completed_photo_ids": ["mine-1"],
                "baseline_photo_ids": [],
                "new_pair_count": -1,
                "new_feedback_photographer_count": 0,
                "new_feedback_points": 0,
                "completed_at": completed_at.isoformat(),
            },
        )

        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(LogValidationError, "new_pair_count must be a non-negative integer"):
                seal_run(Path(directory), replace(make_log(), events=(item,)))

    def test_feedback_scan_rejects_baseline_outside_completed_set(self):
        completed_at = utc(9, 10)
        item = Event(
            "feedback_scan_completed",
            completed_at,
            {
                "scan_id": "scan-3",
                "photo_ids": ["mine-1", "mine-2", "mine-3"],
                "completed_photo_ids": ["mine-1"],
                "baseline_photo_ids": ["mine-2"],
                "new_pair_count": 0,
                "new_feedback_photographer_count": 0,
                "new_feedback_points": 0,
                "completed_at": completed_at.isoformat(),
            },
        )

        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(LogValidationError, "baseline_photo_ids must be a subset"):
                seal_run(Path(directory), replace(make_log(), events=(item,)))

    def test_scan_started_rejects_unknown_purpose(self):
        item = replace(
            make_log().events[0],
            data={**make_log().events[0].data, "purpose": "full_history"},
        )

        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(LogValidationError, "invalid scan purpose"):
                seal_run(Path(directory), replace(make_log(), events=(item,)))

    def test_outgoing_like_rejects_unknown_settlement_mode(self):
        item = Event(
            "outgoing_like_confirmed",
            utc(9),
            {
                "action_id": "a1",
                "photographer_id": "p1",
                "photo_id": "photo-1",
                "photo_url": "https://example.test/photo-1",
                "quota_bucket": "new",
                "before_state": "not_liked",
                "after_state": "liked",
                "settlement_mode": "scheduled",
            },
        )

        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(LogValidationError, "invalid settlement_mode"):
                seal_run(Path(directory), replace(make_log(), events=(item,)))

    def test_feedback_scan_rejects_completed_photo_outside_scope(self):
        completed_at = utc(9, 10)
        item = Event(
            "feedback_scan_completed",
            completed_at,
            {
                "scan_id": "scan-3",
                "photo_ids": ["mine-1", "mine-2", "mine-3"],
                "completed_photo_ids": ["mine-4"],
                "baseline_photo_ids": [],
                "new_pair_count": 0,
                "new_feedback_photographer_count": 0,
                "new_feedback_points": 0,
                "completed_at": completed_at.isoformat(),
            },
        )

        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(LogValidationError, "completed_photo_ids must be a subset"):
                seal_run(Path(directory), replace(make_log(), events=(item,)))

    def test_feedback_scan_rejects_naive_completed_at(self):
        item = Event(
            "feedback_scan_completed",
            utc(9, 10),
            {
                "scan_id": "scan-3",
                "photo_ids": ["mine-1", "mine-2", "mine-3"],
                "completed_photo_ids": [],
                "baseline_photo_ids": [],
                "new_pair_count": 0,
                "new_feedback_photographer_count": 0,
                "new_feedback_points": 0,
                "completed_at": "2026-08-12T09:10:00",
            },
        )

        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(LogValidationError, "completed_at must be timezone-aware"):
                seal_run(Path(directory), replace(make_log(), events=(item,)))

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
