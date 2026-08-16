from collections import Counter
from datetime import datetime
import hashlib
from pathlib import Path
import subprocess
import unittest

from tests import bootstrap  # noqa: F401
from feedback_growth.analytics import rebuild_state
from feedback_growth.store import load_effective_runs
from feedback_growth.workspace import find_repository_root


ROOT = Path(__file__).parents[1].resolve()
STATE_ROOT = find_repository_root(Path(__file__)) / ".local" / "500px-feedback-growth"
BUNDLE_ROOT = ROOT / ".local" / "500px-feedback-growth"


def git(*args):
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RepositoryStateTest(unittest.TestCase):
    def test_only_sealed_markdown_runs_are_versioned_under_local(self):
        result = git("ls-files", ".local/500px-feedback-growth")
        self.assertEqual(result.returncode, 0, result.stderr)
        tracked = result.stdout.splitlines()

        self.assertTrue(tracked)
        self.assertTrue(
            all(path.startswith(".local/500px-feedback-growth/runs/") for path in tracked),
            tracked,
        )
        self.assertTrue(all(path.endswith(".md") for path in tracked), tracked)

    def test_versioned_bundle_matches_main_state_byte_for_byte(self):
        source = {path.name: digest(path) for path in (STATE_ROOT / "runs").glob("*.md")}
        bundle = {path.name: digest(path) for path in (BUNDLE_ROOT / "runs").glob("*.md")}

        self.assertTrue(source)
        self.assertEqual(bundle, source)

    def test_committed_state_rebuilds_expected_mature_outcomes(self):
        logs = load_effective_runs(BUNDLE_ROOT)
        state = rebuild_state(logs, datetime.fromisoformat("2026-08-16T16:00:00+08:00"))
        eligible = {
            item.episode_id: item
            for stats in state.photographers.values()
            for item in stats.eligible_episodes
        }
        counts = Counter(item.outcome for item in eligible.values())

        self.assertEqual(counts, Counter({"failure": 58, "success": 42}))

    def test_checkpoints_dashboard_and_credentials_remain_ignored(self):
        probes = (
            ".local/500px-feedback-growth/checkpoints/probe.md",
            ".local/500px-feedback-growth/dashboard.html",
            ".local/500px-feedback-growth/Cookies",
            ".local/500px-feedback-growth/token.json",
            ".env",
        )
        for probe in probes:
            with self.subTest(probe=probe):
                result = git("check-ignore", "--no-index", "--quiet", "--", probe)
                self.assertEqual(result.returncode, 0, probe)


if __name__ == "__main__":
    unittest.main()
