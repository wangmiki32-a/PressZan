# Single-Run 100 Consolidation Implementation Plan

> 状态：Implemented / Partially Superseded。零参数入口和单 run 恢复方向仍有效；100 赞目标由 ADR-0004 替代，跨日边界由 ADR-0006 替代。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the public four-times-25 workflow with one zero-argument skill invocation that completes the current Shanghai day's remaining quota up to 100 confirmed likes, while preserving approval safety, append-only recovery, and old-log compatibility.

**Architecture:** Keep every confirmed browser action as an immediate append-only checkpoint, but make one public invocation and one run lifecycle own the whole remaining daily quota. The selector plans up to the daily remainder, the CLI exposes a read-only latest-preview lookup so IDs stay internal, and the skill/doc layer presents only `$500px-feedback-growth` plus natural-language first approval.

**Tech Stack:** Python 3.9 standard library, `unittest`, Markdown skill/docs, append-only JSON-in-Markdown state, vanilla HTML Dashboard.

## Global Constraints

- The public default entry is exactly `$500px-feedback-growth` with no required arguments.
- One invocation targets the current Asia/Shanghai day's remaining quota up to exactly 100 confirmed likes.
- Never create action 101; unfinished quota never carries into the next Shanghai day.
- First approval asks only “确认执行？” and never requires the user to copy `preview_id` or `run_id`.
- CAPTCHA, rate limit, login loss, platform warning, account mismatch, ambiguous action state, and candidate exhaustion may stop before 100.
- Every successful like is persisted immediately after visible `not_liked → liked`; no end-of-run backfill.
- Old sealed logs containing four 25-like runs remain readable and are never rewritten.
- Keep Python 3.9 compatibility and add no production dependency.

---

## File Map

- `.agents/skills/500px-feedback-growth/scripts/feedback_growth/selector.py`: select candidates for the whole remaining daily run.
- `.agents/skills/500px-feedback-growth/scripts/feedback_growth/cli.py`: plan the daily remainder and discover the latest valid preview internally.
- `tests/test_selector.py`: selector naming, quota, shortage, determinism, and remaining-day tests.
- `tests/test_cli.py`: 100-candidate preview, 63-candidate remainder, latest-preview lookup, one-run-100, and old-log compatibility.
- `tests/test_skill_contract.py`: zero-argument public invocation and hidden-ID contract.
- `.agents/skills/500px-feedback-growth/SKILL.md`: public routing and full-day execution orchestration.
- `.agents/skills/500px-feedback-growth/agents/openai.yaml`: simplified default prompt.
- `.agents/skills/500px-feedback-growth/references/browser-workflow.md`: full-plan approval review and continuous execution.
- `.agents/skills/500px-feedback-growth/references/operational-recovery.md`: same-run recovery and no 25-candidate assumption.
- `AGENTS.md`, `README.md`, `docs/README.md`, `docs/architecture.md`, `docs/operations.md`: current project contract and durable experience.
- `docs/knowledge-gaps.md`: evidence gaps that future real runs must close.
- `docs/decisions/ADR-0002-single-run-daily-task.md`: accepted single-run daily-task decision.
- `docs/superpowers/specs/2026-08-12-500px-feedback-growth-design.md`: mark superseded by the new design.
- `docs/superpowers/plans/2026-08-12-500px-feedback-growth.md`: mark as historical implementation record.

### Task 1: Select the whole remaining daily quota

**Files:**
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/selector.py`
- Modify: `tests/test_selector.py`

**Interfaces:**
- Produces: `select_run_candidates(candidates, state, now, seed, limit=DAILY_TARGET) -> SelectionResult`
- Preserves: `DAILY_TARGET = 100`, `MIN_UNIQUE = 80`, and `QUOTAS = {"exploit_first": 45, "retest": 20, "new": 15, "verified_second": 20}`

- [ ] **Step 1: Rename test imports and add a default-limit assertion**

```python
from feedback_growth.selector import DAILY_TARGET, select_run_candidates

def test_default_limit_targets_full_day(self):
    candidates = [candidate(f"n{i:03}", "new", i) for i in range(DAILY_TARGET)]
    result = select_run_candidates(candidates, state_for(candidates), NOW, seed=8122026)
    self.assertEqual(len(result.selected), DAILY_TARGET)
    self.assertEqual(result.remaining_daily_quota, 0)
```

Update existing calls from `select_batch(...)` to `select_run_candidates(...)`; keep the explicit `limit=1` and `limit=25` determinism tests because `limit` remains a testable internal control.

- [ ] **Step 2: Run the selector tests and verify the import fails**

Run: `python3 -m unittest tests.test_selector -v`

Expected: FAIL because `select_run_candidates` does not exist.

- [ ] **Step 3: Implement the rename and remove the 25 default**

```python
DAILY_TARGET = 100
MIN_UNIQUE = 80

def select_run_candidates(
    candidates: Sequence[Candidate],
    state: AggregateState,
    now: datetime,
    seed: int,
    limit: int = DAILY_TARGET,
) -> SelectionResult:
    ...
```

Delete `BATCH_TARGET`. Preserve all quota allocation, per-photographer limits, shortage status, and deterministic scoring behavior.

- [ ] **Step 4: Run selector tests**

Run: `python3 -m unittest tests.test_selector -v`

Expected: all selector tests pass.

- [ ] **Step 5: Commit**

```bash
git add .agents/skills/500px-feedback-growth/scripts/feedback_growth/selector.py tests/test_selector.py
git commit -m "refactor: select full daily like quota"
```

### Task 2: Plan the remainder and hide preview IDs behind internal lookup

**Files:**
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `select_run_candidates(..., limit=remaining_daily_quota)` from Task 1.
- Produces: internal CLI command `latest-preview --state-root PATH [--now ISO]`.
- `latest-preview` success JSON: `{"ok": true, "preview_id": str, "expires_at": str, "candidate_count": int}`.
- `latest-preview` errors: `preview_not_found`, `preview_not_current_day`, `preview_expired`, or `preview_changed`.

- [ ] **Step 1: Add failing preview-size tests**

Add a helper that creates `count` candidates, then assert:

```python
def test_preview_plans_full_daily_target(self):
    ...
    self.assertEqual(len(preview["candidate_plan"]), 100)
    self.assertEqual(preview["quota_snapshot"]["confirmed_likes"], 0)

def test_preview_plans_only_remaining_63_after_37_likes(self):
    seed_approved_likes(root, 37, dt(12, 8), "2026-08-12")
    ...
    self.assertEqual(len(preview["candidate_plan"]), 63)
```

Use candidate IDs not already touched by `seed_approved_likes`.

- [ ] **Step 2: Add failing latest-preview tests**

```python
def test_latest_preview_returns_current_valid_preview(self):
    preview = create_preview(root)
    result, payload = invoke(root, "latest-preview", "--now", "2026-08-12T10:00:00+00:00")
    self.assertEqual(result.returncode, 0)
    self.assertEqual(payload["preview_id"], preview["preview_id"])

def test_latest_preview_hides_expired_or_previous_day_preview(self):
    ...
    self.assertIn(payload["code"], {"preview_expired", "preview_not_current_day"})
```

- [ ] **Step 3: Add failing one-run and old-log compatibility tests**

Build one checkpoint, append 100 unique `outgoing_like_confirmed` events through the CLI, finish it once, and assert status is 100 with one sealed run. Separately seal four historical run logs with 25 unique actions each and assert the same status reconstruction returns 100.

- [ ] **Step 4: Run CLI tests and verify failures**

Run: `python3 -m unittest tests.test_cli -v`

Expected: FAIL because preview still caps at 25 and `latest-preview` is unknown.

- [ ] **Step 5: Make preview use the daily remainder**

Replace the batch import and fixed limit:

```python
from .selector import DAILY_TARGET, select_run_candidates

remaining = DAILY_TARGET - _quota_snapshot(state, now)["confirmed_likes"]
result = select_run_candidates(_candidates(checkpoint, state), state, now, args.seed, remaining)
```

- [ ] **Step 6: Implement read-only latest-preview lookup**

Add `command_latest_preview(args)` that reads sealed previews, selects the latest, validates current Shanghai day, expiry, and current quota snapshot, then emits only the internal metadata contract. Register an argparse `latest-preview` subcommand using `_add_common`.

- [ ] **Step 7: Run CLI and selector tests**

Run: `python3 -m unittest tests.test_cli tests.test_selector -v`

Expected: all tests pass, including one-run 100 and old four-run reconstruction.

- [ ] **Step 8: Commit**

```bash
git add .agents/skills/500px-feedback-growth/scripts/feedback_growth/cli.py tests/test_cli.py
git commit -m "feat: plan one continuous 100-like run"
```

### Task 3: Consolidate the public skill contract and durable project knowledge

**Files:**
- Modify: `tests/test_skill_contract.py`
- Modify: `.agents/skills/500px-feedback-growth/SKILL.md`
- Modify: `.agents/skills/500px-feedback-growth/agents/openai.yaml`
- Modify: `.agents/skills/500px-feedback-growth/references/browser-workflow.md`
- Modify: `.agents/skills/500px-feedback-growth/references/operational-recovery.md`
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `docs/README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/operations.md`
- Create: `docs/knowledge-gaps.md`
- Create: `docs/decisions/ADR-0002-single-run-daily-task.md`
- Modify: `docs/decisions/README.md`
- Modify: `docs/superpowers/specs/2026-08-12-500px-feedback-growth-design.md`
- Modify: `docs/superpowers/plans/2026-08-12-500px-feedback-growth.md`

**Interfaces:**
- Public default: `$500px-feedback-growth`.
- First approval phrase: `确认执行`.
- Public maintenance intents: `status`, `preflight`, `dashboard`.
- Internal approval lookup: `latest-preview` from Task 2.

- [ ] **Step 1: Replace the skill contract tests first**

Assert positive current behavior and negative legacy behavior:

```python
self.assertIn("$500px-feedback-growth", text)
self.assertIn("确认执行", text)
self.assertIn("latest-preview", text)
self.assertIn("当日累计 100", text)
self.assertNotIn("run --approve <preview_id>", text)
self.assertNotIn("最多 25", text)
self.assertNotIn("四个 25", text)
```

Keep assertions for 30 works, 12 works, 80 unique photographers, 72-hour attribution, seven-day comment cooldown, page-state confirmation, and every hard stop.

- [ ] **Step 2: Run contract tests and verify legacy text fails them**

Run: `python3 -m unittest tests.test_skill_contract -v`

Expected: FAIL on the new zero-argument and hidden-ID contract.

- [ ] **Step 3: Rewrite SKILL.md around intent, not public CLI syntax**

Use this routing table:

```markdown
| 用户输入 | 行为 |
|---|---|
| `$500px-feedback-growth` | 恢复或开始今天的任务，连续执行到当日累计 100 |
| `确认执行` | 批准最新有效预览并继续同一日任务 |
| `status` | 只读显示进度与分层 |
| `preflight` | 只读刷新回馈与候选 |
| `dashboard` | 从日志重建本地 Dashboard |
```

Document internal `latest-preview`, quick review, same-run recovery, per-action checkpoint, and stop conditions without exposing IDs to the user.

- [ ] **Step 4: Update browser and recovery references**

Replace “25 candidates” with “the current preview plan / remaining daily quota”. State that one run continues until daily 100 and that reconnect/resume uses the same run ID from local state. Preserve one-refresh-only, candidate-before-popover, absolute state-root, and no-blind-click rules.

- [ ] **Step 5: Consolidate project documents**

Make `AGENTS.md`, README, architecture, and operations use only the current single-run contract. Add `ADR-0002` with context, decision, consequences, and verification. Add `docs/knowledge-gaps.md` with four evidence gaps: rate-limit behavior over 100 continuous actions, 100-candidate availability, long Chrome-session stability, and absent mature 72-hour feedback cohort.

Mark the 2026-08-12 spec and plan as superseded/historical at the top and link to the 2026-08-13 design. Do not rewrite their historical step-by-step contents.

- [ ] **Step 6: Update skill metadata**

Set the default prompt to a zero-argument action, for example:

```yaml
default_prompt: "使用 $500px-feedback-growth 恢复或开始今天的任务，安全执行到当日累计 100 个确认点赞。"
```

- [ ] **Step 7: Run contract tests and Markdown link check**

Run:

```bash
python3 -m unittest tests.test_skill_contract -v
ruby -e 'files = ["README.md", "AGENTS.md"] + Dir["docs/**/*.md"]; bad = []; files.each { |file| File.read(file).scan(/\[[^\]]+\]\(([^)]+)\)/).flatten.each { |target| next if target =~ /\A(?:https?:|#)/; path = File.expand_path(target.split("#", 2).first, File.dirname(file)); bad << "#{file} -> #{target}" unless File.exist?(path) } }; abort bad.join("\n") unless bad.empty?'
```

Expected: contract tests pass and the link checker exits 0.

- [ ] **Step 8: Commit**

```bash
git add AGENTS.md README.md docs .agents/skills/500px-feedback-growth tests/test_skill_contract.py
git commit -m "docs: consolidate single-run project contract"
```

### Task 4: Final validation and conflict audit

**Files:**
- Verify only; fix the owning file if a check exposes a conflict.

**Interfaces:**
- Consumes all tasks above.
- Produces a clean repository with current rules unambiguous and old records clearly historical.

- [ ] **Step 1: Run all tests**

Run: `python3 -m unittest discover -v`

Expected: all tests pass.

- [ ] **Step 2: Validate formatting and repository state**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; only intended consolidation changes before final commit.

- [ ] **Step 3: Audit current documents for active legacy rules**

Run:

```bash
rg -n '最多 25|每批 25|四批|run --approve <preview_id>|BATCH_TARGET|select_batch' \
  AGENTS.md README.md docs/README.md docs/architecture.md docs/operations.md \
  .agents/skills/500px-feedback-growth tests
```

Expected: no active legacy-contract matches. Matches inside explicitly superseded 2026-08-12 historical documents are allowed.

- [ ] **Step 4: Validate runtime compatibility without mutation**

Run:

```bash
python3 .agents/skills/500px-feedback-growth/scripts/feedback_growth.py status \
  --state-root /Users/pony/Documents/ChatGPT/PressZan/.local/500px-feedback-growth \
  --json
```

Expected: existing historical runtime rebuilds successfully and remains 100/100 for its recorded day when tested with the relevant fixed `--now`; no log is modified.

- [ ] **Step 5: Run skill structure validation**

Use the installed `skill-creator` validator against `.agents/skills/500px-feedback-growth/` and confirm frontmatter, metadata, references, and scripts remain discoverable.

- [ ] **Step 6: Commit any final consistency fixes**

```bash
git add -u
git add docs/knowledge-gaps.md docs/decisions/ADR-0002-single-run-daily-task.md
git commit -m "test: verify single-run consolidation"
```

- [ ] **Step 7: Report consolidation outcomes**

Report:

- project-level durable experience added;
- legacy rules removed or explicitly superseded;
- remaining knowledge gaps and the evidence required to close each;
- tests, structure validation, runtime readback, commit hashes, and whether remote push remains pending.
