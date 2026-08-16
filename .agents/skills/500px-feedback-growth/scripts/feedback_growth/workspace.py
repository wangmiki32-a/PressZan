from pathlib import Path
from typing import Mapping, Optional


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
