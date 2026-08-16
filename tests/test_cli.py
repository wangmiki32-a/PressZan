from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

from tests import bootstrap  # noqa: F401
from feedback_growth.model import Event, RunLog
from feedback_growth.store import read_checkpoint, seal_run
from tests.helpers import confirmed_like, dt, opened


SCRIPT = (
    Path(__file__).parents[1]
    / ".agents"
    / "skills"
    / "500px-feedback-growth"
    / "scripts"
    / "feedback_growth.py"
)


def invoke(root, *args):
    result = subprocess.run(
        ["python3", str(SCRIPT), *args, "--state-root", str(root)],
        text=True,
        capture_output=True,
        check=False,
    )
    payload = json.loads(result.stdout) if result.stdout else {}
    return result, payload


def invoke_without_state_root(*args, cwd=None, environ=None):
    result = subprocess.run(
        ["python3", str(SCRIPT), *args],
        cwd=cwd,
        env=environ,
        text=True,
        capture_output=True,
        check=False,
    )
    payload = json.loads(result.stdout) if result.stdout else {}
    return result, payload


def add_candidate(root, run_id, identifier="p1", page_order=1):
    occurred_at = read_checkpoint(root, run_id).header.started_at.isoformat()
    return invoke(
        root,
        "event",
        "--run-id",
        run_id,
        "--kind",
        "candidate_observed",
        "--field",
        f"photographer_id={identifier}",
        "--field",
        f"display_name={identifier}",
        "--field",
        f"profile_url=https://example.test/{identifier}",
        "--field",
        "source_photo_id=source",
        "--field",
        "source_url=https://example.test/source",
        "--field",
        f"page_order={page_order}",
        "--now",
        occurred_at,
    )


def deterministic_action_id(day, photographer_id, photo_id, kind):
    return hashlib.sha256(f"{day}{photographer_id}{photo_id}{kind}".encode()).hexdigest()


def create_preview(root, now="2026-08-12T09:00:00+00:00", identifier="p1"):
    result, begun = invoke(root, "begin", "--mode", "preflight", "--now", now)
    assert result.returncode == 0
    add_candidate(root, begun["run_id"], identifier)
    result, preview = invoke(root, "preview", "--run-id", begun["run_id"], "--seed", "8122026", "--now", now)
    assert result.returncode == 0
    result, _ = invoke(root, "finish", "--run-id", begun["run_id"], "--status", "completed", "--now", now)
    assert result.returncode == 0
    return preview


def seed_approved_likes(root, count, base_time, daily_task_id, *, offset=0, run_id=None):
    events = [
        Event(
            "onboarding_approved",
            base_time,
            {"preview_id": "seed", "candidate_digest": "seed", "approved_at": base_time.isoformat()},
        )
    ]
    for index in range(count):
        occurred_at = base_time + timedelta(seconds=index + 1)
        ordinal = index + offset
        photographer_id = f"p{ordinal:03}"
        photo_id = f"photo-{ordinal:03}"
        identifier = deterministic_action_id(daily_task_id, photographer_id, photo_id, "outgoing_like_confirmed")
        events.append(confirmed_like(identifier, photographer_id, occurred_at, bucket="new", photo_id=photo_id))
        events.append(opened(hashlib.sha256(f"episode:{photographer_id}:{identifier}".encode()).hexdigest(), photographer_id, identifier, occurred_at, occurred_at + timedelta(hours=72)))
    ended = base_time + timedelta(seconds=count + 1)
    events.append(
        Event(
            "run_finished",
            ended,
            {"status": "completed", "confirmed_like_count": count, "confirmed_comment_count": 0},
        )
    )
    seal_run(
        root,
        RunLog(1, run_id or f"seed-{count}-{offset}", daily_task_id, "run", "completed", base_time, ended, tuple(events)),
    )


def seed_scheduled_cycle(root, due_at, cycle_id="c1", review_kind="review_3d"):
    started = due_at - timedelta(hours=71)
    photo_ids = [f"work-{index}" for index in range(1, 6)]
    events = (
        Event("cycle_started", started, {"cycle_id": cycle_id, "attribution_eligible": True}),
        Event("cycle_showcase_frozen", started, {"cycle_id": cycle_id, "photo_ids": photo_ids, "showcase_digest": "showcase"}),
        Event("cycle_baseline_completed", started, {"cycle_id": cycle_id, "scan_id": "baseline", "baseline_digest": "baseline"}),
        Event(
            "cycle_like_completed",
            started + timedelta(hours=1),
            {
                "cycle_id": cycle_id,
                "mapped_run_ids": ["legacy-like-run"],
                "touch_action_ids": [],
                "episode_ids": [],
                "like_completed_at": (started + timedelta(hours=1)).isoformat(),
                "terminal_status": "completed",
            },
        ),
        Event(
            "review_schedule_requested",
            started + timedelta(hours=1),
            {
                "cycle_id": cycle_id,
                "review_kind": review_kind,
                "attempt": 1,
                "due_at": due_at.isoformat(),
                "state_root": str(root.resolve()),
                "automation_name": f"500px-review-{cycle_id}-{review_kind}-1",
                "payload_digest": "digest",
            },
        ),
    )
    seal_run(root, RunLog(1, f"cycle-seed-{cycle_id}", started.date().isoformat(), "cycle", "completed", started, events[-1].occurred_at, events))


class CliTest(unittest.TestCase):
    def test_doctor_reports_portable_state_and_detects_untracked_bundle(self):
        result, payload = invoke_without_state_root(
            "doctor",
            "--now",
            "2026-08-16T16:00:00+08:00",
            cwd=SCRIPT.parents[4],
        )

        self.assertTrue(payload, result.stderr)
        self.assertEqual(result.returncode, 2, payload)
        self.assertEqual(payload["code"], "doctor_failed")
        self.assertIn("untracked_sealed_runs", payload["errors"])
        report = payload["report"]
        self.assertTrue(Path(report["state_root"]).is_absolute())
        self.assertGreater(report["sealed_run_count"], 0)
        self.assertEqual(report["eligible_outcomes"], {"failure": 58, "open": 0, "success": 42})
        self.assertFalse(report["git"]["all_sealed_runs_tracked"])
        self.assertTrue(report["git"]["local_only_paths_ignored"])

    def test_doctor_fails_for_corrupt_log(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runs").mkdir()
            (root / "runs" / "broken.md").write_text("not a run log", encoding="utf-8")

            result, payload = invoke(root, "doctor", "--now", "2026-08-16T16:00:00+08:00")

            self.assertTrue(payload, result.stderr)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(payload["code"], "doctor_failed")
            self.assertIn("invalid_sealed_log", payload["errors"])

    def _begin_cycle(self, root, cycle_id="c1", count=5):
        now = "2026-08-14T09:00:00+00:00"
        result, begun = invoke(root, "begin", "--mode", "cycle", "--cycle-id", cycle_id, "--now", now)
        self.assertEqual(result.returncode, 0, begun)
        result, payload = invoke(root, "cycle-start", "--run-id", begun["run_id"], "--cycle-id", cycle_id, "--now", now)
        self.assertEqual(result.returncode, 0, payload)
        for position in range(1, count + 1):
            result, payload = invoke(
                root,
                "cycle-showcase-observe",
                "--run-id",
                begun["run_id"],
                "--cycle-id",
                cycle_id,
                "--photo-id",
                f"work-{position}",
                "--photo-url",
                f"https://500px.test/photo/work-{position}",
                "--owner-id",
                "owner-1",
                "--visibility",
                "public",
                "--position",
                str(position),
                "--evidence-summary",
                "homepage card visible",
                "--now",
                now,
            )
            self.assertEqual(result.returncode, 0, payload)
        return begun

    def test_cycle_showcase_freeze_requires_exactly_five(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            begun = self._begin_cycle(root, count=4)

            result, payload = invoke(
                root,
                "cycle-showcase-freeze",
                "--run-id",
                begun["run_id"],
                "--cycle-id",
                "c1",
                "--owner-id",
                "owner-1",
                "--now",
                "2026-08-14T09:01:00+00:00",
            )

            self.assertEqual(result.returncode, 2)
            self.assertEqual(payload["code"], "showcase_requires_exactly_five")

    def test_cycle_baseline_accepts_five_zero_liker_works_and_binds_run(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            begun = self._begin_cycle(root)
            common = (
                "--run-id",
                begun["run_id"],
                "--cycle-id",
                "c1",
                "--now",
                "2026-08-14T09:01:00+00:00",
            )
            result, frozen = invoke(root, "cycle-showcase-freeze", *common, "--owner-id", "owner-1")
            self.assertEqual(result.returncode, 0, frozen)
            result, payload = invoke(root, "cycle-baseline-start", *common, "--scan-id", "scan-1")
            self.assertEqual(result.returncode, 0, payload)
            for photo_id in frozen["photo_ids"]:
                result, payload = invoke(
                    root,
                    "cycle-baseline-photo-complete",
                    *common,
                    "--scan-id",
                    "scan-1",
                    "--photo-id",
                    photo_id,
                    "--liker-count",
                    "0",
                )
                self.assertEqual(result.returncode, 0, payload)
            result, completed = invoke(root, "cycle-baseline-complete", *common, "--scan-id", "scan-1")
            self.assertEqual(result.returncode, 0, completed)
            result, _ = invoke(root, "finish", "--run-id", begun["run_id"], "--status", "completed", "--now", "2026-08-14T09:02:00+00:00")
            self.assertEqual(result.returncode, 0)
            seed_approved_likes(root, 0, dt(14, 8), "2026-08-14")

            result, run = invoke(root, "begin", "--mode", "run", "--cycle-id", "c1", "--now", "2026-08-14T09:03:00+00:00")

            self.assertEqual(result.returncode, 0, run)
            checkpoint = read_checkpoint(root, run["run_id"])
            binding = [item for item in checkpoint.events if item.kind == "cycle_run_bound"]
            self.assertEqual(len(binding), 1)
            self.assertEqual(binding[0].data["baseline_digest"], completed["baseline_digest"])

    def test_cycle_like_complete_atomically_creates_two_schedule_intents(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            begun = self._begin_cycle(root)
            common = ("--run-id", begun["run_id"], "--cycle-id", "c1", "--now", "2026-08-14T09:01:00+00:00")
            _, frozen = invoke(root, "cycle-showcase-freeze", *common, "--owner-id", "owner-1")
            invoke(root, "cycle-baseline-start", *common, "--scan-id", "scan-1")
            for photo_id in frozen["photo_ids"]:
                invoke(root, "cycle-baseline-photo-complete", *common, "--scan-id", "scan-1", "--photo-id", photo_id, "--liker-count", "0")
            result, _ = invoke(root, "cycle-baseline-complete", *common, "--scan-id", "scan-1")
            self.assertEqual(result.returncode, 0)
            invoke(root, "finish", "--run-id", begun["run_id"], "--status", "completed", "--now", "2026-08-14T09:02:00+00:00")
            seed_approved_likes(root, 0, dt(14, 8), "2026-08-14")
            _, like_run = invoke(root, "begin", "--mode", "run", "--cycle-id", "c1", "--now", "2026-08-14T09:03:00+00:00")
            action_id = deterministic_action_id("2026-08-14", "p1", "photo-1", "outgoing_like_confirmed")
            result, payload = invoke(
                root,
                "event",
                "--run-id",
                like_run["run_id"],
                "--kind",
                "outgoing_like_confirmed",
                "--field",
                f"action_id={action_id}",
                "--field",
                "photographer_id=p1",
                "--field",
                "photo_id=photo-1",
                "--field",
                "photo_url=https://example.test/photo-1",
                "--field",
                "quota_bucket=new",
                "--field",
                "before_state=not_liked",
                "--field",
                "after_state=liked",
                "--now",
                "2026-08-14T09:04:00+00:00",
            )
            self.assertEqual(result.returncode, 0, payload)
            invoke(root, "finish", "--run-id", like_run["run_id"], "--status", "incomplete_candidate_exhausted", "--now", "2026-08-14T09:05:00+00:00")
            _, transaction = invoke(root, "begin", "--mode", "cycle", "--cycle-id", "c1", "--now", "2026-08-14T09:06:00+00:00")

            result, completed = invoke(
                root,
                "cycle-like-complete",
                "--run-id",
                transaction["run_id"],
                "--cycle-id",
                "c1",
                "--mapped-run-id",
                like_run["run_id"],
                "--status",
                "incomplete_candidate_exhausted",
                "--now",
                "2026-08-14T09:06:00+00:00",
            )

            self.assertEqual(result.returncode, 0, completed)
            self.assertEqual(completed["like_completed_at"], "2026-08-14T09:04:00+00:00")
            self.assertEqual(
                [item["due_at"] for item in completed["review_requests"]],
                ["2026-08-15T05:04:00+00:00", "2026-08-17T07:04:00+00:00"],
            )
            checkpoint = read_checkpoint(root, transaction["run_id"])
            self.assertEqual(
                [item.kind for item in checkpoint.events],
                ["cycle_like_completed", "review_schedule_requested", "review_schedule_requested"],
            )

    def test_review_checkpoint_resumes_across_shanghai_midnight(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            seed_scheduled_cycle(root, datetime.fromisoformat("2026-08-14T15:40:00+00:00"))
            result, begun = invoke(
                root,
                "begin",
                "--mode",
                "review",
                "--cycle-id",
                "c1",
                "--review-kind",
                "review_3d",
                "--attempt",
                "1",
                "--now",
                "2026-08-14T15:50:00+00:00",
            )
            self.assertEqual(result.returncode, 0, begun)

            result, resumed = invoke(
                root,
                "resume",
                "--run-id",
                begun["run_id"],
                "--now",
                "2026-08-14T16:10:00+00:00",
            )

            self.assertEqual(result.returncode, 0, resumed)
            self.assertEqual(resumed["header"]["transaction_context"]["cycle_id"], "c1")

    def test_daily_run_and_review_checkpoint_can_begin_together(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            seed_approved_likes(root, 0, dt(14, 8), "2026-08-14")
            seed_scheduled_cycle(root, datetime.fromisoformat("2026-08-14T09:00:00+00:00"))
            result, run_begun = invoke(
                root, "begin", "--mode", "run", "--now", "2026-08-14T09:00:00+00:00"
            )
            self.assertEqual(result.returncode, 0, run_begun)

            result, review_begun = invoke(
                root,
                "begin",
                "--mode",
                "review",
                "--cycle-id",
                "c1",
                "--review-kind",
                "review_3d",
                "--attempt",
                "1",
                "--now",
                "2026-08-14T09:01:00+00:00",
            )

            self.assertEqual(result.returncode, 0, review_begun)
            self.assertNotEqual(review_begun["run_id"], run_begun["run_id"])

    def test_review_records_exactly_frozen_five_and_finishes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            due = datetime.fromisoformat("2026-08-14T09:00:00+00:00")
            seed_scheduled_cycle(root, due)
            result, begun = invoke(
                root,
                "begin",
                "--mode",
                "review",
                "--cycle-id",
                "c1",
                "--review-kind",
                "review_3d",
                "--attempt",
                "1",
                "--now",
                due.isoformat(),
            )
            self.assertEqual(result.returncode, 0, begun)

            result, payload = invoke(
                root,
                "review-photo-observe",
                "--run-id",
                begun["run_id"],
                "--cycle-id",
                "c1",
                "--review-kind",
                "review_3d",
                "--attempt",
                "1",
                "--scan-id",
                "review-scan",
                "--photo-id",
                "outside",
                "--liker-count",
                "0",
                "--now",
                due.isoformat(),
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(payload["code"], "photo_not_in_showcase")

            for photo_id in [f"work-{index}" for index in range(1, 6)]:
                result, payload = invoke(
                    root,
                    "review-photo-observe",
                    "--run-id",
                    begun["run_id"],
                    "--cycle-id",
                    "c1",
                    "--review-kind",
                    "review_3d",
                    "--attempt",
                    "1",
                    "--scan-id",
                    "review-scan",
                    "--photo-id",
                    photo_id,
                    "--liker-count",
                    "0",
                    "--now",
                    due.isoformat(),
                )
                self.assertEqual(result.returncode, 0, payload)

            result, payload = invoke(
                root,
                "review-finish",
                "--run-id",
                begun["run_id"],
                "--cycle-id",
                "c1",
                "--review-kind",
                "review_3d",
                "--attempt",
                "1",
                "--scan-id",
                "review-scan",
                "--now",
                due.isoformat(),
            )
            self.assertEqual(result.returncode, 0, payload)
            self.assertEqual(payload["observed_photo_count"], 5)

    def test_cycle_begin_requires_cycle_id(self):
        with TemporaryDirectory() as directory:
            result, payload = invoke(
                Path(directory), "begin", "--mode", "cycle", "--now", "2026-08-14T09:00:00+00:00"
            )

            self.assertEqual(result.returncode, 2)
            self.assertEqual(payload["code"], "cycle_id_required")

    def test_preview_plans_full_daily_target(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _, begun = invoke(root, "begin", "--mode", "preflight", "--now", "2026-08-12T09:00:00+00:00")
            for index in range(100):
                add_candidate(root, begun["run_id"], identifier=f"n{index:03}", page_order=index + 1)

            result, preview = invoke(
                root,
                "preview",
                "--run-id",
                begun["run_id"],
                "--seed",
                "8122026",
                "--now",
                "2026-08-12T09:00:00+00:00",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(preview["candidate_plan"]), 100)
            self.assertEqual(preview["quota_snapshot"]["confirmed_likes"], 0)

    def test_preview_plans_only_remaining_63_after_37_likes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            seed_approved_likes(root, 37, dt(12, 8), "2026-08-12")
            _, begun = invoke(root, "begin", "--mode", "preflight", "--now", "2026-08-12T09:00:00+00:00")
            for index in range(100):
                add_candidate(root, begun["run_id"], identifier=f"q{index:03}", page_order=index + 1)

            result, preview = invoke(
                root,
                "preview",
                "--run-id",
                begun["run_id"],
                "--seed",
                "8122026",
                "--now",
                "2026-08-12T09:00:00+00:00",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(preview["candidate_plan"]), 63)
            self.assertEqual(preview["quota_snapshot"]["confirmed_likes"], 37)

    def test_latest_preview_returns_current_valid_preview(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            preview = create_preview(root)

            result, payload = invoke(root, "latest-preview", "--now", "2026-08-12T10:00:00+00:00")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(payload["preview_id"], preview["preview_id"])
            self.assertEqual(payload["candidate_count"], 1)

    def test_latest_preview_rejects_cross_day_preview(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            create_preview(root)

            result, payload = invoke(root, "latest-preview", "--now", "2026-08-13T09:00:01+00:00")

            self.assertEqual(result.returncode, 2)
            self.assertEqual(payload["code"], "preview_not_current_day")

    def test_one_run_can_record_all_100_likes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            seed_approved_likes(root, 0, dt(12, 8), "2026-08-12")
            result, begun = invoke(root, "begin", "--mode", "run", "--now", "2026-08-12T09:00:00+00:00")
            self.assertEqual(result.returncode, 0, result.stderr)

            for index in range(100):
                photographer_id = f"r{index:03}"
                photo_id = f"run-photo-{index:03}"
                action_id = deterministic_action_id(
                    "2026-08-12", photographer_id, photo_id, "outgoing_like_confirmed"
                )
                result, payload = invoke(
                    root,
                    "event",
                    "--run-id",
                    begun["run_id"],
                    "--kind",
                    "outgoing_like_confirmed",
                    "--field",
                    f"action_id={action_id}",
                    "--field",
                    f"photographer_id={photographer_id}",
                    "--field",
                    f"photo_id={photo_id}",
                    "--field",
                    f"photo_url=https://example.test/{photo_id}",
                    "--field",
                    "quota_bucket=new",
                    "--field",
                    "before_state=not_liked",
                    "--field",
                    "after_state=liked",
                    "--now",
                    f"2026-08-12T09:{index // 60:02}:{index % 60:02}+00:00",
                )
                self.assertEqual(result.returncode, 0, payload)

            result, _ = invoke(
                root,
                "finish",
                "--run-id",
                begun["run_id"],
                "--status",
                "completed",
                "--now",
                "2026-08-12T10:00:00+00:00",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            result, status = invoke(root, "status", "--json", "--now", "2026-08-12T10:01:00+00:00")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(status["today"]["confirmed_likes"], 100)
            self.assertEqual(len(list((root / "runs").glob("run-*.md"))), 1)

    def test_four_historical_25_like_runs_still_rebuild_to_100(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for group in range(4):
                seed_approved_likes(
                    root,
                    25,
                    dt(12, group + 1),
                    "2026-08-12",
                    offset=group * 25,
                    run_id=f"historical-{group}",
                )

            result, status = invoke(root, "status", "--json", "--now", "2026-08-12T12:00:00+00:00")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(status["today"]["confirmed_likes"], 100)
            self.assertEqual(status["today"]["unique_photographers"], 100)

    def test_resume_rejects_sealed_run(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            seed_approved_likes(root, 0, dt(12, 8), "2026-08-12", run_id="sealed-run")

            result, payload = invoke(root, "resume", "--run-id", "sealed-run")

            self.assertEqual(result.returncode, 2)
            self.assertEqual(payload["code"], "run_not_recoverable")

    def test_run_cannot_claim_completed_before_daily_target(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            seed_approved_likes(root, 0, dt(12, 8), "2026-08-12")
            _, begun = invoke(root, "begin", "--mode", "run", "--now", "2026-08-12T09:00:00+00:00")

            result, payload = invoke(
                root,
                "finish",
                "--run-id",
                begun["run_id"],
                "--status",
                "completed",
                "--now",
                "2026-08-12T09:01:00+00:00",
            )

            self.assertEqual(result.returncode, 2)
            self.assertEqual(payload["code"], "daily_incomplete")
            self.assertTrue((root / "checkpoints" / f"{begun['run_id']}.md").exists())
            self.assertFalse((root / "runs" / f"{begun['run_id']}.md").exists())

    def test_active_run_cannot_carry_actions_across_shanghai_midnight(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            seed_approved_likes(root, 0, dt(12, 8), "2026-08-12")
            _, begun = invoke(root, "begin", "--mode", "run", "--now", "2026-08-12T15:59:00+00:00")

            result, payload = invoke(root, "begin", "--mode", "run", "--now", "2026-08-12T16:01:00+00:00")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(payload["code"], "stale_recoverable_run")
            self.assertEqual(payload["recoverable_run_id"], begun["run_id"])

            result, payload = invoke(
                root,
                "resume",
                "--run-id",
                begun["run_id"],
                "--now",
                "2026-08-12T16:01:00+00:00",
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(payload["code"], "daily_task_expired")

            action_id = deterministic_action_id("2026-08-12", "p1", "photo-1", "outgoing_like_confirmed")
            result, payload = invoke(
                root,
                "event",
                "--run-id",
                begun["run_id"],
                "--kind",
                "outgoing_like_confirmed",
                "--field",
                f"action_id={action_id}",
                "--field",
                "photographer_id=p1",
                "--field",
                "photo_id=photo-1",
                "--field",
                "photo_url=https://example.test/photo-1",
                "--field",
                "quota_bucket=new",
                "--field",
                "before_state=not_liked",
                "--field",
                "after_state=liked",
                "--now",
                "2026-08-12T16:01:00+00:00",
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(payload["code"], "daily_task_expired")

    def test_fixed_clock_and_seed_rebuild_status_and_dashboard_deterministically(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            preview = create_preview(root, "2026-08-12T09:00:00+00:00", "p1")
            self.assertEqual(preview["candidate_plan"][0]["photographer_id"], "p1")

            first_status, first_payload = invoke(
                root,
                "status",
                "--json",
                "--now",
                "2026-08-12T12:00:00+00:00",
            )
            second_status, second_payload = invoke(
                root,
                "status",
                "--json",
                "--now",
                "2026-08-12T12:00:00+00:00",
            )
            self.assertEqual(first_status.returncode, 0, first_status.stderr)
            self.assertEqual(second_status.returncode, 0, second_status.stderr)
            self.assertEqual(first_payload, second_payload)
            self.assertEqual(first_payload["today"]["status"], "preflight_ready")

            first_dashboard, first_dashboard_payload = invoke(
                root,
                "dashboard",
                "--now",
                "2026-08-12T12:00:00+00:00",
            )
            first_html = Path(first_dashboard_payload["path"]).read_bytes()
            second_dashboard, second_dashboard_payload = invoke(
                root,
                "dashboard",
                "--now",
                "2026-08-12T12:00:00+00:00",
            )
            second_html = Path(second_dashboard_payload["path"]).read_bytes()
            self.assertEqual(first_dashboard.returncode, 0, first_dashboard.stderr)
            self.assertEqual(second_dashboard.returncode, 0, second_dashboard.stderr)
            self.assertEqual(first_html, second_html)

    def test_dashboard_command_writes_rebuildable_offline_html(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            seed_approved_likes(root, 25, dt(12, 9), "2026-08-12")

            result, payload = invoke(
                root,
                "dashboard",
                "--now",
                "2026-08-12T12:00:00+00:00",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            output = Path(payload["path"])
            self.assertEqual(output, root.resolve() / "dashboard.html")
            html = output.read_text(encoding="utf-8")
            self.assertIn('"confirmed_likes":25', html)
            self.assertNotIn("https://", html)

            status_result, status_payload = invoke(
                root,
                "status",
                "--json",
                "--now",
                "2026-08-12T12:00:00+00:00",
            )
            self.assertEqual(status_result.returncode, 0, status_result.stderr)
            self.assertEqual(status_payload["today"]["status"], "in_progress")

    def test_first_run_requires_preflight(self):
        with TemporaryDirectory() as directory:
            result, payload = invoke(Path(directory), "begin", "--mode", "run", "--now", "2026-08-12T09:00:00+00:00")

            self.assertEqual(result.returncode, 2)
            self.assertEqual(payload["code"], "preflight_required")
            self.assertFalse((Path(directory) / "checkpoints").exists())

    def test_preview_has_exact_expiry_and_digest(self):
        with TemporaryDirectory() as directory:
            preview = create_preview(Path(directory))
            canonical = json.dumps(preview["candidate_plan"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))

            self.assertEqual(preview["expires_at"], "2026-08-13T09:00:00+00:00")
            self.assertEqual(preview["candidate_digest"], hashlib.sha256(canonical.encode()).hexdigest())

    def test_approval_rejects_expired_preview(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            preview = create_preview(root)
            _, begun = invoke(
                root,
                "begin",
                "--mode",
                "run",
                "--approve-preview",
                preview["preview_id"],
                "--now",
                "2026-08-13T09:00:01+00:00",
            )
            add_candidate(root, begun["run_id"])

            result, payload = invoke(
                root,
                "approve",
                "--run-id",
                begun["run_id"],
                "--preview-id",
                preview["preview_id"],
                "--now",
                "2026-08-13T09:00:01+00:00",
            )

            self.assertEqual(result.returncode, 2)
            self.assertEqual(payload["code"], "preview_expired")

    def test_approval_rejects_changed_preview(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            preview = create_preview(root)
            _, begun = invoke(
                root,
                "begin",
                "--mode",
                "run",
                "--approve-preview",
                preview["preview_id"],
                "--now",
                "2026-08-12T10:00:00+00:00",
            )
            add_candidate(root, begun["run_id"], identifier="changed")

            result, payload = invoke(
                root,
                "approve",
                "--run-id",
                begun["run_id"],
                "--preview-id",
                preview["preview_id"],
                "--now",
                "2026-08-12T10:00:00+00:00",
            )

            self.assertEqual(result.returncode, 2)
            self.assertEqual(payload["code"], "preview_changed")

    def test_approval_accepts_exact_selected_candidates_without_full_pool_replay(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _, preflight = invoke(root, "begin", "--mode", "preflight", "--now", "2026-08-12T09:00:00+00:00")
            for index in range(30):
                add_candidate(root, preflight["run_id"], identifier=f"p{index:02}", page_order=index + 1)
            _, preview = invoke(
                root,
                "preview",
                "--run-id",
                preflight["run_id"],
                "--seed",
                "8122026",
                "--now",
                "2026-08-12T09:00:00+00:00",
            )
            invoke(
                root,
                "finish",
                "--run-id",
                preflight["run_id"],
                "--status",
                "completed",
                "--now",
                "2026-08-12T09:01:00+00:00",
            )
            _, run = invoke(
                root,
                "begin",
                "--mode",
                "run",
                "--approve-preview",
                preview["preview_id"],
                "--now",
                "2026-08-12T10:00:00+00:00",
            )
            for candidate in reversed(preview["candidate_plan"]):
                add_candidate(
                    root,
                    run["run_id"],
                    identifier=candidate["photographer_id"],
                    page_order=candidate["page_order"],
                )

            result, payload = invoke(
                root,
                "approve",
                "--run-id",
                run["run_id"],
                "--preview-id",
                preview["preview_id"],
                "--now",
                "2026-08-12T10:00:00+00:00",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(payload["approved"])
            self.assertEqual(payload["candidate_plan"], preview["candidate_plan"])

    def test_approval_rejects_valid_but_older_preview(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            older = create_preview(root, "2026-08-12T09:00:00+00:00", "p1")
            create_preview(root, "2026-08-12T10:00:00+00:00", "p2")
            _, begun = invoke(
                root,
                "begin",
                "--mode",
                "run",
                "--approve-preview",
                older["preview_id"],
                "--now",
                "2026-08-12T11:00:00+00:00",
            )
            add_candidate(root, begun["run_id"], "p1")

            result, payload = invoke(
                root,
                "approve",
                "--run-id",
                begun["run_id"],
                "--preview-id",
                older["preview_id"],
                "--now",
                "2026-08-12T11:00:00+00:00",
            )

            self.assertEqual(result.returncode, 2)
            self.assertEqual(payload["code"], "preview_not_latest")

    def test_checkpoint_action_is_recoverable_and_sealed_once(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            preview = create_preview(root)
            _, begun = invoke(
                root,
                "begin",
                "--mode",
                "run",
                "--approve-preview",
                preview["preview_id"],
                "--now",
                "2026-08-12T10:00:00+00:00",
            )
            run_id = begun["run_id"]
            add_candidate(root, run_id)
            result, approved = invoke(
                root,
                "approve",
                "--run-id",
                run_id,
                "--preview-id",
                preview["preview_id"],
                "--now",
                "2026-08-12T10:00:00+00:00",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(approved["approved"])
            result, _ = invoke(
                root,
                "event",
                "--run-id",
                run_id,
                "--kind",
                "outgoing_like_confirmed",
                "--field",
                f"action_id={deterministic_action_id('2026-08-12', 'p1', 'photo-1', 'outgoing_like_confirmed')}",
                "--field",
                "photographer_id=p1",
                "--field",
                "photo_id=photo-1",
                "--field",
                "photo_url=https://example.test/photo-1",
                "--field",
                "quota_bucket=new",
                "--field",
                "before_state=not_liked",
                "--field",
                "after_state=liked",
                "--now",
                "2026-08-12T10:01:00+00:00",
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result, payload = invoke(root, "begin", "--mode", "run", "--now", "2026-08-12T10:02:00+00:00")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(payload["recoverable_run_id"], run_id)
            result, resumed = invoke(root, "resume", "--run-id", run_id, "--now", "2026-08-12T10:02:30+00:00")
            self.assertEqual(result.returncode, 0)
            self.assertEqual(sum(e["kind"] == "outgoing_like_confirmed" for e in resumed["events"]), 1)
            invoke(root, "finish", "--run-id", run_id, "--status", "incomplete_candidate_exhausted", "--now", "2026-08-12T10:03:00+00:00")
            result, status = invoke(root, "status", "--json", "--now", "2026-08-12T10:04:00+00:00")
            self.assertEqual(result.returncode, 0)
            self.assertEqual(status["today"]["confirmed_likes"], 1)

    def test_safety_pause_blocks_actions_but_can_finish(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _, begun = invoke(root, "begin", "--mode", "preflight", "--now", "2026-08-12T09:00:00+00:00")
            run_id = begun["run_id"]
            result, _ = invoke(
                root,
                "event",
                "--run-id",
                run_id,
                "--kind",
                "safety_paused",
                "--field",
                "reason=captcha",
                "--field",
                "page_url=https://example.test",
                "--field",
                "evidence_summary=visible captcha",
                "--field",
                "last_safe_action_id=none",
                "--now",
                "2026-08-12T09:01:00+00:00",
            )
            self.assertEqual(result.returncode, 0)

            result, payload = invoke(
                root,
                "event",
                "--run-id",
                run_id,
                "--kind",
                "outgoing_like_confirmed",
                "--field",
                "action_id=a1",
                "--field",
                "photographer_id=p1",
                "--field",
                "photo_id=photo-1",
                "--field",
                "photo_url=https://example.test/photo-1",
                "--field",
                "quota_bucket=new",
                "--field",
                "before_state=not_liked",
                "--field",
                "after_state=liked",
                "--now",
                "2026-08-12T09:02:00+00:00",
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(payload["code"], "run_paused")
            result, _ = invoke(
                root,
                "finish",
                "--run-id",
                run_id,
                "--status",
                "paused_incomplete",
                "--now",
                "2026-08-12T09:03:00+00:00",
            )
            self.assertEqual(result.returncode, 0)

    def test_completed_day_refuses_action_101(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            seed_approved_likes(root, 100, dt(hour=1), "2026-08-12")

            result, payload = invoke(root, "begin", "--mode", "run", "--now", "2026-08-12T12:00:00+00:00")

            self.assertEqual(result.returncode, 2)
            self.assertEqual(payload["code"], "daily_complete")

    def test_shanghai_midnight_starts_fresh_quota_without_carryover(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            seed_approved_likes(root, 1, datetime.fromisoformat("2026-08-12T15:59:00+00:00"), "2026-08-12")

            result, payload = invoke(root, "begin", "--mode", "run", "--now", "2026-08-12T16:00:00+00:00")

            self.assertEqual(result.returncode, 0)
            self.assertEqual(payload["daily_task_id"], "2026-08-13")
            self.assertEqual(payload["remaining_daily_quota"], 100)
