from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

from tests import bootstrap  # noqa: F401
from feedback_growth.model import Event, RunLog
from feedback_growth.store import seal_run
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
    payload = json.loads(result.stdout)
    return result, payload


def add_candidate(root, run_id, identifier="p1", page_order=1):
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


def seed_approved_likes(root, count, base_time, daily_task_id):
    events = [
        Event(
            "onboarding_approved",
            base_time,
            {"preview_id": "seed", "candidate_digest": "seed", "approved_at": base_time.isoformat()},
        )
    ]
    for index in range(count):
        occurred_at = base_time + timedelta(seconds=index + 1)
        photographer_id = f"p{index:03}"
        photo_id = f"photo-{index:03}"
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
    seal_run(root, RunLog(1, f"seed-{count}", daily_task_id, "run", "completed", base_time, ended, tuple(events)))


class CliTest(unittest.TestCase):
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
            result, resumed = invoke(root, "resume", "--run-id", run_id)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(sum(e["kind"] == "outgoing_like_confirmed" for e in resumed["events"]), 1)
            invoke(root, "finish", "--run-id", run_id, "--status", "completed", "--now", "2026-08-12T10:03:00+00:00")
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
