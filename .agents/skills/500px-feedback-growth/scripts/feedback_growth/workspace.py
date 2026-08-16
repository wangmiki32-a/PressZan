from pathlib import Path
import subprocess
from typing import Dict, Mapping, Optional, Tuple


STATE_ENV = "PRESSZAN_STATE_ROOT"
STATE_RELATIVE = Path(".local") / "500px-feedback-growth"


class WorkspaceError(RuntimeError):
    pass


def find_checkout_root(start: Path) -> Path:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    raise WorkspaceError("checkout_root_not_found")


def find_repository_root(start: Path) -> Path:
    checkout_root = find_checkout_root(start)
    marker = checkout_root / ".git"
    if marker.is_dir():
        return checkout_root

    line = marker.read_text(encoding="utf-8").strip()
    prefix = "gitdir: "
    if not line.startswith(prefix):
        raise WorkspaceError("invalid_git_file")
    git_dir = Path(line[len(prefix) :])
    if not git_dir.is_absolute():
        git_dir = checkout_root / git_dir
    git_dir = git_dir.resolve()
    common_dir = git_dir.parent.parent
    main_root = common_dir.parent
    if common_dir.name != ".git" or not main_root.exists():
        raise WorkspaceError("unsupported_worktree_layout")
    return main_root


def resolve_state_root(explicit: Optional[str], environ: Mapping[str, str], start: Path) -> Path:
    selected = explicit or environ.get(STATE_ENV)
    if selected:
        return Path(selected).expanduser().resolve()
    return find_repository_root(start) / STATE_RELATIVE


def _git(checkout_root: Path, *args: str) -> Tuple[int, str]:
    result = subprocess.run(
        ["git", "-C", str(checkout_root), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode, result.stdout


def inspect_git_state(checkout_root: Path, state_root: Path) -> Dict[str, object]:
    code, output = _git(
        checkout_root,
        "ls-files",
        "--",
        ".local/500px-feedback-growth",
    )
    if code != 0:
        raise WorkspaceError("git_ls_files_failed")

    tracked_local = {line for line in output.splitlines() if line}
    tracked_runs = {
        line
        for line in tracked_local
        if line.startswith(".local/500px-feedback-growth/runs/") and line.endswith(".md")
    }
    actual_runs = {
        f".local/500px-feedback-growth/runs/{path.name}"
        for path in (state_root / "runs").glob("*.md")
    }
    probes = (
        ".local/500px-feedback-growth/checkpoints/probe.md",
        ".local/500px-feedback-growth/dashboard.html",
        ".local/500px-feedback-growth/Cookies",
        ".local/500px-feedback-growth/token.json",
        ".env",
    )
    ignored = []
    for probe in probes:
        code, _ = _git(checkout_root, "check-ignore", "--no-index", "--quiet", "--", probe)
        if code not in (0, 1):
            raise WorkspaceError("git_check_ignore_failed")
        ignored.append(code == 0)

    return {
        "all_sealed_runs_tracked": bool(actual_runs) and actual_runs == tracked_runs,
        "local_only_paths_ignored": all(ignored),
        "tracked_only_sealed_runs_under_local": tracked_local == tracked_runs,
        "tracked_run_count": len(tracked_runs),
    }
