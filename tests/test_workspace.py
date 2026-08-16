import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests import bootstrap  # noqa: F401
from feedback_growth.workspace import (
    find_checkout_root,
    find_repository_root,
    resolve_state_root,
)


class WorkspaceTest(unittest.TestCase):
    def test_explicit_state_root_wins_over_environment(self):
        actual = resolve_state_root(
            "/tmp/presszan-explicit-state",
            {"PRESSZAN_STATE_ROOT": "/tmp/presszan-env-state"},
            Path(__file__),
        )

        self.assertEqual(actual, Path("/tmp/presszan-explicit-state").resolve())

    def test_environment_state_root_wins_over_repository_default(self):
        actual = resolve_state_root(
            None,
            {"PRESSZAN_STATE_ROOT": "/tmp/presszan-env-state"},
            Path(__file__),
        )

        self.assertEqual(actual, Path("/tmp/presszan-env-state").resolve())

    def test_normal_clone_uses_repository_local_state(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            script = root / ".agents" / "skills" / "example" / "scripts" / "tool.py"
            script.parent.mkdir(parents=True)
            script.touch()

            resolved_root = root.resolve()
            self.assertEqual(find_checkout_root(script), resolved_root)
            self.assertEqual(find_repository_root(script), resolved_root)
            self.assertEqual(
                resolve_state_root(None, {}, script),
                resolved_root / ".local" / "500px-feedback-growth",
            )

    def test_worktree_uses_current_checkout_and_main_repository_state(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            common = root / ".git"
            worktree = root / ".worktrees" / "feature"
            git_dir = common / "worktrees" / "feature"
            git_dir.mkdir(parents=True)
            worktree.mkdir(parents=True)
            (worktree / ".git").write_text(f"gitdir: {git_dir}\n", encoding="utf-8")
            script = worktree / ".agents" / "skills" / "example" / "scripts" / "tool.py"
            script.parent.mkdir(parents=True)
            script.touch()

            resolved_root = root.resolve()
            self.assertEqual(find_checkout_root(script), worktree.resolve())
            self.assertEqual(find_repository_root(script), resolved_root)
            self.assertEqual(
                resolve_state_root(None, {}, script),
                resolved_root / ".local" / "500px-feedback-growth",
            )

    def test_relative_worktree_gitdir_is_resolved_from_checkout(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            common = root / ".git"
            worktree = root / ".worktrees" / "feature"
            (common / "worktrees" / "feature").mkdir(parents=True)
            worktree.mkdir(parents=True)
            relative = os.path.relpath(common / "worktrees" / "feature", worktree)
            (worktree / ".git").write_text(f"gitdir: {relative}\n", encoding="utf-8")

            self.assertEqual(find_repository_root(worktree), root.resolve())


if __name__ == "__main__":
    unittest.main()
