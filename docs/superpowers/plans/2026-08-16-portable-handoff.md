# PressZan 可迁移交接 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让朋友 clone 私有仓库后，在不复制浏览器凭证的前提下继承 sealed 运行历史，并在任意本地路径安全执行同一 500px 账号的 skill。

**Architecture:** 直接把 `.local/500px-feedback-growth/runs/*.md` 作为 Git-backed sealed event store，checkpoint 与 Dashboard 继续只保留在执行机器。新增统一 workspace resolver 和只读 `doctor`，让 CLI、skill 和 Automation 在 clone、主工作区及 worktree 中都指向同一个主仓库状态根。

**Tech Stack:** Python 3.9+ 标准库、Markdown + JSON fenced event logs、Git、`unittest`、项目级 Codex skill。

## Global Constraints

- 不新增生产依赖，代码保持 Python 3.9 标准库兼容。
- 用户已明确选择把 sealed runs 明文提交到私有 Git；仓库不得改为公开。
- 只跟踪 `.local/500px-feedback-growth/runs/*.md`，不得跟踪 checkpoint、Dashboard、`.env`、Cookie、token 或 Chrome profile。
- sealed log 保持 append-only；迁移不得改写现有日志内容、文件名或事件时间。
- 默认状态根必须与 clone 路径无关；优先级固定为 `--state-root`、`PRESSZAN_STATE_ROOT`、主仓库 `.local/500px-feedback-growth`。
- worktree 不得静默生成第二份运行事实。
- 朋友与当前维护者必须串行执行同一账号；未封存 checkpoint 只能在原机器恢复。
- 真实浏览器页面兼容性不通过自动点赞测试验证。

---

## File Map

- Create: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/workspace.py` — 定位主仓库、解析状态根、执行只读 Git 边界检查。
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/cli.py` — 使用 resolver，新增 `doctor` 命令。
- Modify: `.gitignore` — 精确放行 sealed runs，继续拒绝其他 `.local` 内容。
- Add: `.local/500px-feedback-growth/runs/*.md` — 当前未改写的 sealed 历史事实。
- Create: `tests/test_workspace.py` — 路径优先级、normal clone 和 worktree 解析测试。
- Modify: `tests/test_cli.py` — `doctor` 成功/失败输出和零参数默认状态根测试。
- Create: `tests/test_repository_state.py` — 真实迁移包、Git ignore 边界和 42/58 聚合回归测试。
- Modify: `tests/test_skill_contract.py` — skill 文案中的动态路径、迁移前置检查和交接合同。
- Modify: `AGENTS.md`, `README.md`, `docs/architecture.md`, `docs/operations.md` — 当前权威项目规则和交接说明。
- Create: `docs/decisions/ADR-0003-git-backed-sealed-runs.md` — 记录明文状态入私有 Git 的长期决定。
- Modify: `.agents/skills/500px-feedback-growth/SKILL.md` — 默认无绝对路径、运行前 doctor/Git 同步 gate。
- Modify: `.agents/skills/500px-feedback-growth/references/operational-recovery.md` — 跨机器恢复和未封存运行规则。
- Modify: `.agents/skills/500px-feedback-growth/references/event-schema.md` — tracked sealed / local checkpoint 边界。

---

### Task 1: 可迁移 workspace resolver

**Files:**
- Create: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/workspace.py`
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/cli.py:54-68`
- Create: `tests/test_workspace.py`

**Interfaces:**
- Produces: `find_checkout_root(start: Path) -> Path`，返回当前 normal clone 或 worktree checkout 根，供当前分支 Git index 检查使用。
- Produces: `find_repository_root(start: Path) -> Path`
- Produces: `resolve_state_root(explicit: Optional[str], environ: Mapping[str, str], start: Path) -> Path`
- Consumes later: `command_doctor` and all existing CLI commands use the resolved absolute state root.

- [ ] **Step 1: Write failing resolver tests**

```python
class WorkspaceTest(unittest.TestCase):
    def test_explicit_state_root_wins_over_environment(self):
        actual = resolve_state_root(
            "/tmp/explicit-state",
            {"PRESSZAN_STATE_ROOT": "/tmp/env-state"},
            Path(__file__),
        )
        self.assertEqual(actual, Path("/tmp/explicit-state").resolve())

    def test_environment_wins_over_repository_default(self):
        actual = resolve_state_root(
            None,
            {"PRESSZAN_STATE_ROOT": "/tmp/env-state"},
            Path(__file__),
        )
        self.assertEqual(actual, Path("/tmp/env-state").resolve())

    def test_normal_clone_uses_repository_local_state(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            script = root / ".agents/skills/example/scripts/tool.py"
            script.parent.mkdir(parents=True)
            script.touch()
            self.assertEqual(
                resolve_state_root(None, {}, script),
                root / ".local/500px-feedback-growth",
            )

    def test_worktree_uses_main_repository_state(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            common = root / ".git"
            worktree = root / ".worktrees/feature"
            gitdir = common / "worktrees/feature"
            gitdir.mkdir(parents=True)
            worktree.mkdir(parents=True)
            (worktree / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
            script = worktree / ".agents/skills/example/scripts/tool.py"
            script.parent.mkdir(parents=True)
            script.touch()
            self.assertEqual(
                resolve_state_root(None, {}, script),
                root / ".local/500px-feedback-growth",
            )
            self.assertEqual(find_checkout_root(script), worktree)
```

- [ ] **Step 2: Run the resolver tests and confirm RED**

Run: `python3 -m unittest -v tests.test_workspace`

Expected: FAIL with `ModuleNotFoundError` for `feedback_growth.workspace`.

- [ ] **Step 3: Implement the minimal resolver**

```python
STATE_ENV = "PRESSZAN_STATE_ROOT"
STATE_RELATIVE = Path(".local") / "500px-feedback-growth"


def find_checkout_root(start: Path) -> Path:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        marker = candidate / ".git"
        if marker.exists():
            return candidate
    raise WorkspaceError("checkout_root_not_found")


def find_repository_root(start: Path) -> Path:
    candidate = find_checkout_root(start)
    marker = candidate / ".git"
    if marker.is_dir():
        return candidate
    if marker.is_file():
        line = marker.read_text(encoding="utf-8").strip()
        if not line.startswith("gitdir: "):
            raise WorkspaceError("invalid_git_file")
        git_dir = Path(line.removeprefix("gitdir: "))
        if not git_dir.is_absolute():
            git_dir = (candidate / git_dir).resolve()
        common_dir = git_dir.parent.parent
        main_root = common_dir.parent
        if common_dir.name == ".git" and main_root.exists():
            return main_root
        raise WorkspaceError("unsupported_worktree_layout")


def resolve_state_root(explicit, environ, start):
    selected = explicit or environ.get(STATE_ENV)
    if selected:
        return Path(selected).expanduser().resolve()
    return find_repository_root(start) / STATE_RELATIVE
```

Use `str.removeprefix` equivalent compatible with Python 3.9 (`line[len("gitdir: "):]`), not the Python 3.9-incompatible method shown for readability above.

Replace `cli._state_root` with a call using `os.environ` and `Path(__file__)`; keep `--state-root` behavior unchanged.

- [ ] **Step 4: Run resolver and CLI regression tests**

Run: `python3 -m unittest -v tests.test_workspace tests.test_cli`

Expected: PASS.

- [ ] **Step 5: Commit resolver**

```bash
git add .agents/skills/500px-feedback-growth/scripts/feedback_growth/workspace.py \
  .agents/skills/500px-feedback-growth/scripts/feedback_growth/cli.py \
  tests/test_workspace.py
git commit -m "feat: resolve portable state root"
```

---

### Task 2: 只读迁移 doctor

**Files:**
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/workspace.py`
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/cli.py:1377-1625`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: `inspect_git_state(repo_root: Path, state_root: Path) -> Mapping[str, object]`
- Produces: CLI `doctor [--state-root PATH] [--now ISO8601]` with JSON output and exit code `0` only when all required checks pass.
- Consumes: `load_effective_runs`, `rebuild_state`, `find_repository_root`, `resolve_state_root`.

- [ ] **Step 1: Add failing doctor tests**

Add an `invoke_without_state_root(*args, cwd=None, environ=None)` helper and tests asserting:

```python
def test_doctor_reports_portable_state_and_detects_untracked_bundle(self):
    result = subprocess.run(
        ["python3", str(SCRIPT), "doctor", "--now", "2026-08-16T10:00:00+08:00"],
        cwd=SCRIPT.parents[5],
        text=True,
        capture_output=True,
        check=False,
    )
    payload = json.loads(result.stdout)
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
        (root / "runs/broken.md").write_text("not a run log", encoding="utf-8")
        result, payload = invoke(root, "doctor", "--now", "2026-08-16T10:00:00+08:00")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(payload["code"], "doctor_failed")
        self.assertIn("invalid_sealed_log", payload["errors"])
```

- [ ] **Step 2: Run doctor tests and confirm RED**

Run: `python3 -m unittest -v tests.test_cli.CliTest.test_doctor_reports_portable_state_and_detects_untracked_bundle tests.test_cli.CliTest.test_doctor_fails_for_corrupt_log`

Expected: FAIL because `doctor` is not a registered subcommand.

- [ ] **Step 3: Implement doctor aggregation and Git checks**

Register `doctor` with `_add_common`. `command_doctor` must:

```python
root = _state_root(args.state_root)
now = _now(args.now)
logs = load_effective_runs(root)
state = rebuild_state(logs, now)
eligible = {
    item.episode_id: item
    for stats in state.photographers.values()
    for item in stats.eligible_episodes
}
outcomes = Counter(item.outcome for item in eligible.values())
for key in ("success", "failure", "open"):
    outcomes.setdefault(key, 0)
```

`inspect_git_state` uses `subprocess.run(["git", "-C", str(checkout_root), ...], check=False)` only for read-only commands。`checkout_root` 来自当前脚本所在 worktree，`state_root` 可以位于主仓库；函数按 repository-relative 文件名比较实际 `runs/*.md` 与当前分支 `git ls-files -- .local/500px-feedback-growth/runs/*.md`，并用 `git check-ignore --no-index` 检查代表性 local-only 路径。它返回明确布尔值，不 fetch、不修改 Git。

Catch `LogValidationError` and `WorkspaceError` only. Required检查失败时返回 `_error("doctor_failed", errors=[...], report=report)`，让迁移前也能审计聚合结果；不要隐藏 unexpected exceptions。

- [ ] **Step 4: Run doctor and full CLI tests**

Run: `python3 -m unittest -v tests.test_cli tests.test_workspace`

Expected: PASS；当前尚未跟踪 runs，因此 doctor 明确返回 `untracked_sealed_runs`，同时 report 保留 42/58/0 聚合证据。

- [ ] **Step 5: Commit doctor**

```bash
git add .agents/skills/500px-feedback-growth/scripts/feedback_growth/workspace.py \
  .agents/skills/500px-feedback-growth/scripts/feedback_growth/cli.py \
  tests/test_cli.py
git commit -m "feat: add migration doctor"
```

---

### Task 3: Version sealed state and lock privacy boundary

**Files:**
- Modify: `.gitignore`
- Add: `.local/500px-feedback-growth/runs/*.md`
- Create: `tests/test_repository_state.py`

**Interfaces:**
- Produces: a clean clone containing the complete sealed event store.
- Consumes: resolver and doctor from Tasks 1–2.

- [ ] **Step 1: Add failing repository-state tests**

```python
class RepositoryStateTest(unittest.TestCase):
    def test_only_sealed_runs_are_versioned_under_local(self):
        tracked = subprocess.run(
            ["git", "ls-files", ".local/500px-feedback-growth"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.splitlines()
        self.assertTrue(tracked)
        self.assertTrue(all(path.startswith(".local/500px-feedback-growth/runs/") for path in tracked))
        self.assertTrue(all(path.endswith(".md") for path in tracked))

    def test_committed_state_rebuilds_expected_mature_outcomes(self):
        logs = load_effective_runs(ROOT / ".local/500px-feedback-growth")
        state = rebuild_state(logs, datetime.fromisoformat("2026-08-16T10:00:00+08:00"))
        eligible = {
            item.episode_id: item
            for stats in state.photographers.values()
            for item in stats.eligible_episodes
        }
        counts = Counter(item.outcome for item in eligible.values())
        self.assertEqual(counts, Counter({"failure": 58, "success": 42}))
```

Add `git check-ignore --no-index` assertions proving checkpoint, Dashboard, `.env`, Cookie and token-shaped files remain ignored.

同时把 Task 2 的 doctor happy-path 断言更新为：退出码 `0`、`ok=true`、`all_sealed_runs_tracked=true`，并继续断言 42/58/0。

- [ ] **Step 2: Run repository-state tests and confirm RED**

Run: `python3 -m unittest -v tests.test_repository_state`

Expected: FAIL because `.local` is entirely ignored and no sealed runs are tracked.

- [ ] **Step 3: Narrow `.gitignore` and stage the untouched logs**

Use the exact intent below:

```gitignore
.local/*
!.local/500px-feedback-growth/
.local/500px-feedback-growth/*
!.local/500px-feedback-growth/runs/
.local/500px-feedback-growth/runs/*
!.local/500px-feedback-growth/runs/*.md
```

把现有主状态的 `runs/*.md` 逐字节复制到 worktree 同一 repository-relative 路径，不转换、不规范化内容；随后比较每一组源文件和目标文件的 SHA-256，再 stage 目标文件。

- [ ] **Step 4: Verify state and privacy boundary**

Run:

```bash
python3 -m unittest -v tests.test_repository_state tests.test_cli tests.test_workspace
python3 .agents/skills/500px-feedback-growth/scripts/feedback_growth.py doctor \
  --now 2026-08-16T10:00:00+08:00
git status --short --ignored
```

Expected: tests PASS; doctor reports 42 success, 58 failure, 0 open; only runs are staged/tracked under `.local`; checkpoint and Dashboard remain ignored.

- [ ] **Step 5: Commit the versioned state**

```bash
git add .gitignore .local/500px-feedback-growth/runs tests/test_repository_state.py
git commit -m "feat: version sealed feedback history"
```

---

### Task 4: Update skill and project operating contract

**Files:**
- Modify: `tests/test_skill_contract.py`
- Modify: `.agents/skills/500px-feedback-growth/SKILL.md`
- Modify: `.agents/skills/500px-feedback-growth/references/operational-recovery.md`
- Modify: `.agents/skills/500px-feedback-growth/references/event-schema.md`
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/operations.md`
- Create: `docs/decisions/ADR-0003-git-backed-sealed-runs.md`

**Interfaces:**
- Produces: zero-hardcoded-path public workflow and stable cross-machine handoff rules.
- Consumes: `doctor`, `PRESSZAN_STATE_ROOT`, Git-backed runs boundary.

- [ ] **Step 1: Write failing contract tests**

Add assertions that current authoritative text:

```python
for text in (skill, agents, readme, operations):
    self.assertNotIn("/Users/pony/Documents/ChatGPT/PressZan", text)
self.assertIn("doctor", skill)
self.assertIn("PRESSZAN_STATE_ROOT", operations)
self.assertIn("runs/*.md", agents)
self.assertIn("私有", agents)
self.assertIn("串行", recovery)
self.assertIn("checkpoint", recovery)
self.assertIn("Git", schema)
```

Also assert the skill requires `doctor` before real interaction and does not claim Automation or active checkpoint migrates through Git.

- [ ] **Step 2: Run skill contract and confirm RED**

Run: `python3 -m unittest -v tests.test_skill_contract`

Expected: FAIL on hardcoded path and missing handoff rules.

- [ ] **Step 3: Apply the minimal documentation changes**

Use path-free commands:

```bash
python3 .agents/skills/500px-feedback-growth/scripts/feedback_growth.py doctor
python3 .agents/skills/500px-feedback-growth/scripts/feedback_growth.py status --json
python3 .agents/skills/500px-feedback-growth/scripts/feedback_growth.py dashboard
```

State explicitly:

- real interaction gate: pull/sync → `doctor` → `status --json` → resume or begin;
- only sealed runs are shared; active checkpoint remains on the originating machine;
- Dashboard is rebuilt locally;
- scheduled reviews are created on the executing machine using its resolved path;
- same account must not be operated concurrently;
- repository stays private and collaborators receive least necessary access;
- fixed `Dora0125` profile and stable account ID remain intentional account-safety checks.

ADR-0003 supersedes only ADR-0001 statements that personal runs never enter Git and that a machine-specific absolute state root is required. Preserve append-only, sealed precedence and no-credential invariants.

- [ ] **Step 4: Run contract and doc checks**

Run:

```bash
python3 -m unittest -v tests.test_skill_contract
rg -n "/Users/pony/Documents/ChatGPT/PressZan" AGENTS.md README.md docs/architecture.md docs/operations.md \
  .agents/skills/500px-feedback-growth/SKILL.md \
  .agents/skills/500px-feedback-growth/references
git diff --check
```

Expected: tests PASS; `rg` returns no match in current authoritative documents; historical specs/plans may retain old paths as historical evidence.

- [ ] **Step 5: Commit operating contract**

```bash
git add AGENTS.md README.md docs/architecture.md docs/operations.md \
  docs/decisions/ADR-0003-git-backed-sealed-runs.md \
  .agents/skills/500px-feedback-growth/SKILL.md \
  .agents/skills/500px-feedback-growth/references/operational-recovery.md \
  .agents/skills/500px-feedback-growth/references/event-schema.md \
  tests/test_skill_contract.py
git commit -m "docs: define portable account handoff"
```

---

### Task 5: Clean-clone verification, merge and GitHub publication

**Files:**
- Verify only; no production file should be created by this task.

**Interfaces:**
- Consumes all previous deliverables.
- Produces a verified `origin/main` that a collaborator can clone and execute.

- [ ] **Step 1: Run the complete verification suite**

Run:

```bash
python3 -m unittest discover -v
python3 /Users/pony/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  .agents/skills/500px-feedback-growth
git diff --check main...HEAD
git status --short --branch
```

Expected: all tests and skill validation PASS; worktree is clean.

- [ ] **Step 2: Verify a clean clone without hardcoded paths**

Create a temporary local clone from the worktree branch, then run:

```bash
python3 .agents/skills/500px-feedback-growth/scripts/feedback_growth.py doctor \
  --now 2026-08-16T10:00:00+08:00
python3 .agents/skills/500px-feedback-growth/scripts/feedback_growth.py status \
  --now 2026-08-16T10:00:00+08:00 --json
python3 .agents/skills/500px-feedback-growth/scripts/feedback_growth.py dashboard \
  --now 2026-08-16T10:00:00+08:00
```

Expected: doctor reports 42/58/0 and absolute temp-clone state path; status loads history; Dashboard builds locally and remains untracked.

- [ ] **Step 3: Audit staged/tracked content for sensitive material**

Run `git ls-files`, `git grep` for credential-shaped keys, and inspect every tracked path under `.local`. Expected: only sealed Markdown runs are present; no `.env`, Cookie, token, local storage, checkpoint, Dashboard or Chrome profile.

- [ ] **Step 4: Restore GitHub authentication if required**

Run: `gh auth status -h github.com`

If invalid, user runs `gh auth login -h github.com`; do not request or print a token. Confirm repository visibility remains private before publishing.

- [ ] **Step 5: Merge and push**

After all verification passes:

```bash
git checkout main
git merge --ff-only codex/portable-handoff
git push origin main
git status --short --branch
```

Expected: local `main` and `origin/main` point to the same final commit.

- [ ] **Step 6: Report collaborator handoff**

Report final commit, remote URL, tracked run count, 42/58/0 verification, private repository requirement, non-migrated local resources, and these first-run steps: collaborator access → clone → Codex open → Chrome login → `doctor` → `$500px-feedback-growth`.
