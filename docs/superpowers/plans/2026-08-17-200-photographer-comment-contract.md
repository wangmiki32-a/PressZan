# 200 Photographer Coverage and Comment Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make future daily runs complete after exactly 200 distinct photographers are processed, while commenting `👍👍👍` after every newly confirmed like and preserving historical completion.

**Architecture:** Derive coverage from immutable like and skip events, expose it through aggregate state, and make selector/CLI/Dashboard consume that derived count. Keep the event schema backward compatible and make sealed historical `completed` status authoritative.

**Tech Stack:** Python 3.9 standard library, `unittest`, Markdown skill/reference docs, self-contained HTML/JavaScript Dashboard.

## Global Constraints

- Do not edit sealed run logs or checkpoints.
- Do not add production dependencies.
- Count one photographer at most once per Shanghai day.
- New completion target is exactly 200 covered photographers; likes may be fewer.
- Only inspect the first work and comment `👍👍👍` after each confirmed new like.

---

### Task 1: Derived coverage state and selector

**Files:**
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/model.py`
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/analytics.py`
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/selector.py`
- Test: `tests/test_selector.py`
- Test: `tests/test_analytics.py`

**Interfaces:**
- Produces: `DailyTaskStats.covered_photographer_ids` and `DAILY_PHOTOGRAPHER_TARGET = 200`.
- Consumes: existing `outgoing_like_confirmed` and `candidate_skipped` events.

- [ ] Write tests proving likes plus skips yield unique coverage and selector returns 200 unique first-touch candidates with buckets `112/50/38`.
- [ ] Run focused tests and verify failures are caused by the missing coverage contract.
- [ ] Add the derived field and replace like-count quota logic with unique coverage logic.
- [ ] Run focused tests and verify they pass.

### Task 2: CLI completion and compatibility

**Files:**
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/cli.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_repository_state.py`

**Interfaces:**
- Consumes: `DailyTaskStats.covered_photographer_ids`.
- Produces: `covered_photographers`, `remaining_photographer_quota`, exact-200 completion enforcement, and legacy completed-run compatibility.

- [ ] Write tests for 200 mixed like/skip outcomes, rejection before 200, rejection of photographer 201, and a fresh 200 quota after Shanghai midnight.
- [ ] Run focused tests and verify expected failures.
- [ ] Update begin, event, preview, finish and status commands to use derived coverage.
- [ ] Update repository-state expectations for the newly committed open cohort without editing logs.
- [ ] Run focused tests and verify they pass.

### Task 3: Dashboard semantics

**Files:**
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/dashboard.py`
- Modify: `.agents/skills/500px-feedback-growth/assets/dashboard.html`
- Modify: `.agents/skills/500px-feedback-growth/references/dashboard-semantics.md`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `covered_photographer_ids`, confirmed likes/comments, and derived completion status.
- Produces: coverage-first progress and history views.

- [ ] Write tests that distinguish 200 covered photographers from fewer confirmed likes and preserve old completed history.
- [ ] Run focused tests and verify expected failures.
- [ ] Update view model and template labels/progress without changing the visual design.
- [ ] Run Dashboard tests and verify they pass.

### Task 4: Skill, operations, and decision record

**Files:**
- Modify: `.agents/skills/500px-feedback-growth/SKILL.md`
- Modify: `.agents/skills/500px-feedback-growth/references/browser-workflow.md`
- Modify: `.agents/skills/500px-feedback-growth/references/operational-recovery.md`
- Modify: `.agents/skills/500px-feedback-growth/references/event-schema.md`
- Modify: `.agents/skills/500px-feedback-growth/agents/openai.yaml`
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/operations.md`
- Add: `docs/decisions/ADR-0004-200-photographer-coverage.md`
- Test: `tests/test_skill_contract.py`

**Interfaces:**
- Documents the exact runtime behavior implemented by Tasks 1-3.

- [ ] Update skill-contract tests for 200 coverage, first-work-only behavior, exact comment text and removal of the old verified-only comment rule.
- [ ] Run the skill-contract tests and verify expected failures.
- [ ] Update the current authoritative skill and docs; leave historical specs/plans unchanged.
- [ ] Validate skill structure and rerun the contract tests.

### Task 5: Final verification

**Files:**
- Verify all modified files.

**Interfaces:**
- Produces a tested, repository-consistent implementation.

- [ ] Run `.\.venv\Scripts\python.exe -m unittest discover -v`.
- [ ] Run `git diff --check`, `doctor`, `git status -sb`, and `git worktree list`.
- [ ] Rebuild Dashboard and visually inspect desktop and narrow layouts without changing design.
