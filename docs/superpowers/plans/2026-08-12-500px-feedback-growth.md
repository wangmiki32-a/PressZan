# 500px Feedback Growth Implementation Plan

> 状态：Historical / Superseded。本文保留初版实施过程，不再作为当前执行计划。当前计划见 [2026-08-13 Single-Run 100 Consolidation](2026-08-13-single-run-100-consolidation.md)。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a project-local skill that learns which 500px photographers reciprocate likes, executes confirmed browser interactions in safe batches, persists append-only Markdown evidence, and renders a self-contained dashboard.

**Architecture:** Keep browser control in `SKILL.md` and its workflow reference; keep deterministic state reconstruction, feedback attribution, candidate selection, and rendering in small Python 3.9 standard-library modules. Treat sealed Markdown run logs as the source of truth, use append-only Markdown checkpoints for crash recovery, and derive all status and dashboard output from those files.

**Tech Stack:** Python 3.9 standard library, `unittest`, Markdown with fenced canonical JSON, vanilla HTML/CSS/JavaScript, Computer Use, `visualize:visualize`, `skill-creator`.

---

## File map

- `.agents/skills/500px-feedback-growth/SKILL.md`: user-facing routing, authorization, browser orchestration, and safety gates.
- `.agents/skills/500px-feedback-growth/agents/openai.yaml`: project skill discovery metadata.
- `.agents/skills/500px-feedback-growth/references/browser-workflow.md`: stable 500px page targets, scan sequence, action confirmation, and recovery rules.
- `.agents/skills/500px-feedback-growth/scripts/feedback_growth/`: focused Python package for models, Markdown storage, analytics, selection, dashboard generation, and CLI.
- `.agents/skills/500px-feedback-growth/assets/dashboard.html`: self-contained dashboard template produced and QA'd with `@visualize:visualize`.
- `tests/`: standard-library unit and integration tests; no live likes or comments.
- `.local/500px-feedback-growth/`: untracked run checkpoints, sealed Markdown logs, previews, derived cache, and dashboard.

## Locked data contracts

All identifiers are non-empty strings; all timestamps are timezone-aware ISO 8601 values normalized to UTC. Event `occurred_at` is the observation/confirmation time. Event payloads are exact allowlisted dictionaries:

| Event kind | Required payload fields |
|---|---|
| `scan_started` | `scan_id`, `owner_id`, `profile_url` |
| `work_observed` | `scan_id`, `photo_id`, `photo_url`, `position` (1–30) |
| `received_like_observed` | `scan_id`, `photo_id`, `work_position`, `photographer_id`, `display_name`, `profile_url` |
| `candidate_observed` | `photographer_id`, `display_name`, `profile_url`, `source_photo_id`, `source_url`, `page_order` |
| `preview_created` | `preview_id`, `candidate_digest`, `expires_at`, `seed`, `quota_snapshot`, `candidate_ids`, `candidate_plan` |
| `onboarding_approved` | `preview_id`, `candidate_digest`, `approved_at` |
| `outgoing_like_confirmed` | `action_id`, `photographer_id`, `photo_id`, `photo_url`, `quota_bucket`, `before_state`, `after_state` |
| `outgoing_comment_confirmed` | `action_id`, `photographer_id`, `photo_id`, `content`, `before_state`, `after_state` |
| `feedback_episode_opened` | `episode_id`, `photographer_id`, `touch_action_id`, `expires_at` |
| `feedback_episode_extended` | `episode_id`, `touch_action_id`, `previous_expires_at`, `expires_at` |
| `feedback_episode_succeeded` | `episode_id`, `received_photo_id`, `feedback_first_seen_at`, `received_like_count` |
| `feedback_episode_failed` | `episode_id`, `expired_at` |
| `candidate_skipped` | `photographer_id`, optional `photo_id`, `reason` |
| `safety_paused` | `reason`, `page_url`, `evidence_summary`, `last_safe_action_id` |
| `run_finished` | `status`, `confirmed_like_count`, `confirmed_comment_count` |

`before_state` and `after_state` are visible-state strings; a confirmed like requires distinct values and `after_state="liked"`. A confirmed comment requires `after_state="visible"`.

Define these dataclasses with no optional behavior hidden outside their fields:

```python
@dataclass(frozen=True)
class FeedbackEpisode:
    episode_id: str
    photographer_id: str
    touch_action_ids: Tuple[str, ...]
    opened_at: datetime
    last_touch_at: datetime
    expires_at: datetime
    outcome: str  # open | success | failure
    feedback_first_seen_at: Optional[datetime]
    received_like_count: int

@dataclass(frozen=True)
class PhotographerStats:
    photographer_id: str
    display_name: str
    profile_url: str
    baseline_work_ids: FrozenSet[str]
    baseline_work_positions: Mapping[str, int]
    historical_high_potential: bool
    episodes: Tuple[FeedbackEpisode, ...]
    last_comment_at: Optional[datetime]
    today_like_photo_ids: Tuple[str, ...]

@dataclass(frozen=True)
class DailyTaskStats:
    daily_task_id: str
    confirmed_likes: int
    unique_photographer_ids: FrozenSet[str]
    quota_counts: Mapping[str, int]
    confirmed_comments: int
    status: str
    completed_at: Optional[datetime]
    reinforcement_likes: int
    new_reciprocator_ids: FrozenSet[str]
    tier_changes: Tuple[Mapping[str, str], ...]
    skip_counts: Mapping[str, int]
    risk_events: Tuple[Mapping[str, str], ...]

@dataclass(frozen=True)
class AggregateState:
    photographers: Mapping[str, PhotographerStats]
    known_received_like_pairs: FrozenSet[Tuple[str, str]]
    daily_tasks: Mapping[str, DailyTaskStats]
    paused_reason: Optional[str]

@dataclass(frozen=True)
class Candidate:
    photographer_id: str
    display_name: str
    profile_url: str
    source_photo_id: str
    source_url: str
    page_order: int
    tier: str
    is_retest: bool

@dataclass(frozen=True)
class SelectionResult:
    selected: Tuple[Mapping[str, object], ...]
    status: str  # ready | incomplete_candidate_exhausted | daily_complete
    remaining_daily_quota: int
    projected_unique_count: int

@dataclass(frozen=True)
class CheckpointHeader:
    schema_version: int
    run_id: str
    daily_task_id: str
    mode: str
    started_at: datetime
    approve_preview_id: Optional[str]

@dataclass(frozen=True)
class Checkpoint:
    header: CheckpointHeader
    events: Tuple[Event, ...]
```

Only a `(photo_id, photographer_id)` pair not present in `known_received_like_pairs` before the current scan is “new”. It may satisfy an episode only when its first observation occurs strictly after that episode's `last_touch_at` and no later touch superseded that episode. Baseline or previously observed likes can never become future successes.

Effective reconstruction reads every sealed log plus recoverable checkpoints whose `run_id` has no sealed log. A checkpoint begins with one immutable `CheckpointHeader` fenced JSON block followed by append-only event blocks. If a sealed log and checkpoint share a run ID, the sealed log is authoritative and its retained checkpoint is ignored. More than one unsealed checkpoint for the same `daily_task_id` is a validation error. For analytics, a recoverable checkpoint becomes an effective `RunLog` using its header, status `active`, and `ended_at` equal to the latest event time or `started_at` when empty. `begin` reports a recoverable run before creating a new one; the skill must resume or explicitly finish it first.

Episode identity is deterministic: the first confirmed touch without an open episode uses `episode_id = sha256("episode:" + photographer_id + ":" + action_id)`. Recording that touch atomically appends `outgoing_like_confirmed` and `feedback_episode_opened`. A later touch during the window atomically appends the confirmed action plus `feedback_episode_extended`. A genuinely new received-like observation atomically appends `feedback_episode_succeeded`; starting a later preflight/run atomically appends `feedback_episode_failed` for every episode already expired at the injected/current clock. Rebuild verifies these lifecycle events against the underlying touches and observations and rejects inconsistent episode IDs or outcomes.

The public `run --approve <preview_id>` maps to this internal flow: start a CLI run with `begin --mode run --approve-preview <preview_id>`, repeat the current browser candidate scan into that run's checkpoint, then call `approve --run-id <run_id> --preview-id <preview_id>`. The CLI requires that ID to be the latest sealed preview, loads its seed and `quota_snapshot`, regenerates the candidate plan from the new observations, recomputes its digest internally, and writes `onboarding_approved` only if it matches and is within 24 hours. The digest is never accepted from the caller. `preview_not_latest`, `preview_changed`, or `preview_expired` causes the skill to finish the approval run as `approval_rejected`, automatically run a fresh `preflight`, return the new preview ID, and leave no active checkpoint.

Every CLI command accepts optional `--now <ISO8601>` and selectors accept `--seed`; tests and fixture generation must pass both. Production skill calls omit `--now` and use the real clock.

### Task 1: Scaffold the project-local skill and test harness

**Files:**
- Create: `.gitignore`
- Create: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/test_package.py`

- [ ] **Step 1: Write a failing package import test**

```python
from pathlib import Path
import sys
import unittest

PACKAGE_ROOT = Path(__file__).parents[1] / ".agents/skills/500px-feedback-growth/scripts"
sys.path.insert(0, str(PACKAGE_ROOT))

class PackageTest(unittest.TestCase):
    def test_exposes_schema_version(self):
        import feedback_growth
        self.assertEqual(feedback_growth.SCHEMA_VERSION, 1)
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `python3 -m unittest tests.test_package -v`

Expected: `ModuleNotFoundError: No module named 'feedback_growth'`.

- [ ] **Step 3: Add the minimal package and ignore local state**

```python
# __init__.py
SCHEMA_VERSION = 1
```

`.gitignore` must contain only task-owned ignores:

```gitignore
.local/500px-feedback-growth/
__pycache__/
*.py[cod]
```

- [ ] **Step 4: Run the test and repository checks**

Run: `python3 -m unittest tests.test_package -v && git diff --check`

Expected: one passing test and no whitespace errors.

- [ ] **Step 5: Commit**

```bash
git add .gitignore .agents/skills/500px-feedback-growth/scripts/feedback_growth/__init__.py tests
git commit -m "chore: scaffold feedback growth skill"
```

### Task 2: Define the event model and append-only Markdown store

**Files:**
- Create: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/model.py`
- Create: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/store.py`
- Create: `tests/test_store.py`

- [ ] **Step 1: Write failing tests for canonical logs and validation**

Create a `RunLog` fixture with schema `1`, run `run-1`, daily task `2026-08-12`, preflight mode, completed status, `09:00Z`/`09:01Z` bounds, and ordered `scan_started` then `run_finished` events. Assert:

- Sealing then parsing returns the identical dataclass and the file contains exactly one `````json` fence.
- Replacing schema `1` with `99` raises `LogValidationError` containing both `schema_version` and the absolute source path.
- Removing `confirmed_like_count` from `run_finished` raises `LogValidationError` naming the field and source path.
- Calling `seal_run` twice for `run-1` raises `FileExistsError` and leaves the original bytes unchanged.
- Beginning a checkpoint with daily task `2026-08-12`, mode `run`, and `09:00Z` start, then appending two events and reopening the store, returns the identical header and both events in original order.
- A retained checkpoint with a matching sealed `run_id` is excluded by `load_effective_runs`; two unsealed headers for daily task `2026-08-12` raise `LogValidationError` naming both run IDs.

- [ ] **Step 2: Run the store tests and confirm all fail**

Run: `python3 -m unittest tests.test_store -v`

Expected: import failures for `feedback_growth.model` and `feedback_growth.store`.

- [ ] **Step 3: Implement strict event and run-log models**

Use dataclasses and explicit validators, not a generic framework:

```python
@dataclass(frozen=True)
class Event:
    kind: str
    occurred_at: datetime
    data: Dict[str, Any]

@dataclass(frozen=True)
class RunLog:
    schema_version: int
    run_id: str
    daily_task_id: str
    mode: str
    status: str
    started_at: datetime
    ended_at: datetime
    events: Tuple[Event, ...]
```

Keep an allowlist of event kinds and required keys for `scan_started`, `work_observed`, `received_like_observed`, `candidate_observed`, `preview_created`, `onboarding_approved`, `outgoing_like_confirmed`, `outgoing_comment_confirmed`, `feedback_episode_opened`, `feedback_episode_extended`, `feedback_episode_succeeded`, `feedback_episode_failed`, `candidate_skipped`, `safety_paused`, and `run_finished`.

- [ ] **Step 4: Implement Markdown rendering, parsing, checkpoints, and sealing**

Implement these exact public functions: `render_run_log(log) -> str`, `parse_run_log(path) -> RunLog`, `begin_checkpoint(root, header) -> Path`, `append_checkpoint(root, run_id, event) -> None`, `append_checkpoint_events(root, run_id, events) -> None`, `read_checkpoint(root, run_id) -> Checkpoint`, `iter_recoverable_checkpoints(root) -> Iterator[Checkpoint]`, `seal_run(root, log) -> Path`, `iter_sealed_logs(root) -> Iterator[RunLog]`, and `load_effective_runs(root) -> Tuple[RunLog, ...]`.

`render_run_log` emits one human summary followed by exactly one fenced `json` object using sorted keys and UTF-8 text. `begin_checkpoint` exclusively creates the Markdown journal and writes its immutable header before any events; event appends fail if the header is missing. `seal_run` writes a new immutable file with exclusive creation and never deletes the checkpoint. Effective loading indexes sealed logs by run ID before considering checkpoints, constructs active logs from checkpoint headers, and rejects duplicate active daily-task IDs.

- [ ] **Step 5: Run focused and full tests**

Run: `python3 -m unittest tests.test_store -v && python3 -m unittest discover -s tests -v`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add .agents/skills/500px-feedback-growth/scripts/feedback_growth tests/test_store.py
git commit -m "feat: add markdown event store"
```

### Task 3: Rebuild feedback state and calculate exact metrics

**Files:**
- Create: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/analytics.py`
- Create: `tests/test_analytics.py`
- Create: `tests/helpers.py`

- [ ] **Step 1: Write failing attribution and classification tests**

Use `NOW = 2026-08-12T12:00:00Z` and explicit event fixtures. Assert these exact outcomes:

- Likes sent to photographer `p1` at `T0` and `T0+1h` produce one open episode with two action IDs and expiry `T0+73h`.
- A new received-like pair first seen at `T0+2h` makes that episode success once; a second new pair from `p1` increments `received_like_count` but not successful-episode count.
- A pair observed during baseline at `T0-1d`, then observed again after a touch, never satisfies the episode.
- Opening lifecycle event ID differs from `sha256("episode:" + photographer_id + ":" + first_action_id)` raises `StateValidationError`; success without a new post-touch pair and failure before expiry are also rejected.
- Touches at 29 days and 28 days ago in one successful episode contribute denominator `2`, numerator photographer count `1`, and KPI `50.0`.
- A touch 24 hours ago remains open and contributes neither numerator nor denominator.
- Two separate successful episodes at 20 and 5 days ago classify `p1` as `verified`.
- Two failed episodes and no success in 30 days classify `p1` as `dormant`; at 29 cooldown days it is ineligible, at 30 days it is an eligible retest.
- Baseline appearances on work positions 2 and 9 produce historical-high-potential `Beta(1.5, 1)` but zero successful episodes.

- [ ] **Step 2: Run and confirm failure**

Run: `python3 -m unittest tests.test_analytics -v`

Expected: import failure for `feedback_growth.analytics`.

- [ ] **Step 3: Implement state reconstruction in timestamp order**

Expose `rebuild_state(logs, now) -> AggregateState`, `classify_photographer(stats, now) -> str`, `evidence_weight(age_days) -> float`, and `matured_cohort_kpi(state, now) -> Optional[float]`. Implement `evidence_weight` exactly as `2 ** (-age_days / 30.0)`.

Use one open `FeedbackEpisode` per photographer. A second confirmed touch links to it and extends `expires_at`; episode outcome is binary while all confirmed touches remain in the KPI denominator. De-duplicate received likes by `(my_photo_id, liker_id)`. Reconstruct from persisted lifecycle events and validate their deterministic IDs, touch membership, expiry ordering, and success/failure evidence rather than silently repairing inconsistencies.

- [ ] **Step 4: Implement baseline high-potential and tier rules**

Populate `baseline_work_positions` from the first completed scan and persist the derived `historical_high_potential` boolean. It is true when a photographer appears on at least two monitored works or appears on any of positions 1–5. Use `Beta(1.5, 1)` only while the photographer has no matured episode result. Map one matured failure to the retest pool while retaining tier `new`; two failures plus no 30-day success becomes `dormant`.

- [ ] **Step 5: Run analytics and full tests**

Run: `python3 -m unittest tests.test_analytics -v && python3 -m unittest discover -s tests -v`

Expected: all tests pass with no live network access.

- [ ] **Step 6: Commit**

```bash
git add .agents/skills/500px-feedback-growth/scripts/feedback_growth/analytics.py tests
git commit -m "feat: rebuild reciprocal feedback state"
```

### Task 4: Implement deterministic Thompson selection and daily allocation

**Files:**
- Create: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/selector.py`
- Create: `tests/test_selector.py`

- [ ] **Step 1: Write failing quota and eligibility tests**

Build 45 exploit-first, 20 retest, 15 untouched-new, and 20 verified reinforcement candidates with distinct IDs and fixed eligible photos. Assert a four-batch simulated day selects exactly `45/20/15/20`, has 80 unique IDs, gives no ID more than two actions, and assigns all second actions to verified IDs. Also assert:

- A `new` photographer with one matured failure consumes `retest`, never `new` exploration.
- With only 70 eligible unique photographers and no verified second-photo candidates, selection ends `incomplete_candidate_exhausted` rather than weakening constraints.
- Two candidates with mocked samples `0.70` and `0.66` choose lower `page_order`; samples `0.70` and `0.64` choose `0.70`.
- Two calls with seed `8122026`, the same state, clock, and candidates return byte-for-byte equal serialized plans.

- [ ] **Step 2: Run and confirm failure**

Run: `python3 -m unittest tests.test_selector -v`

Expected: import failure for `feedback_growth.selector`.

- [ ] **Step 3: Implement eligibility and daily quota deficits**

Define `DAILY_TARGET = 100`, `BATCH_TARGET = 25`, `MIN_UNIQUE = 80`, and `QUOTAS = {"exploit_first": 45, "retest": 20, "new": 15, "verified_second": 20}`. Expose `select_batch(candidates, state, now, seed, limit=BATCH_TARGET) -> SelectionResult` using the locked dataclasses above.

Calculate remaining daily deficits from confirmed actions already in `state`. Make the retest pool mutually exclusive with untouched newcomer exploration. Reallocate shortages to eligible first likes before any verified second like; never create a third daily action for one photographer.

- [ ] **Step 4: Implement Thompson sampling and explanations**

Use `random.Random(seed).betavariate(alpha, beta)`. Preserve source page order and choose it when the top two samples differ by at most `0.05`. Every selected candidate returns `bucket`, sampled score, source URL, tier, daily ordinal, and a concise Chinese reason for preflight display.

- [ ] **Step 5: Run selector and full tests**

Run: `python3 -m unittest tests.test_selector -v && python3 -m unittest discover -s tests -v`

Expected: all tests pass and repeated seeded runs are identical.

- [ ] **Step 6: Commit**

```bash
git add .agents/skills/500px-feedback-growth/scripts/feedback_growth/selector.py tests/test_selector.py
git commit -m "feat: select feedback growth candidates"
```

### Task 5: Build the crash-safe CLI lifecycle and onboarding approval

**Files:**
- Create: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/cli.py`
- Create: `.agents/skills/500px-feedback-growth/scripts/feedback_growth.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI integration tests**

Use a temporary state root, `--now`, and subprocess calls. Assert:

- `begin --mode run` on empty state exits `2` with JSON code `preflight_required` and creates no run.
- A preflight at `2026-08-12T09:00:00Z` produces a preview expiring exactly at `2026-08-13T09:00:00Z`; its digest equals SHA-256 of canonical `candidate_plan` JSON.
- Approval at `+24h+1s` exits `2` with `preview_expired`; approval with one changed candidate exits `2` with `preview_changed`; neither writes approval.
- Approval of an older still-valid preview exits `2` with `preview_not_latest`.
- After each approval rejection, finishing as `approval_rejected` seals the checkpoint; an automatic replacement preflight creates a new preview and leaves exactly one active or recoverable run during its execution and none after sealing.
- A confirmed action appended to checkpoint, followed by a simulated process restart, is returned in `begin` as `recoverable_run_id`; after sealing, status counts it once and ignores the retained checkpoint.
- Adding action 101 to a completed Shanghai-day task exits `2` with `daily_complete`.
- An unfinished task at `2026-08-12T15:59:59Z` does not reduce the new task quota at `2026-08-12T16:00:00Z` (midnight Asia/Shanghai).
- After `safety_paused`, another outgoing action event exits `2` with `run_paused`, while `finish --status paused_incomplete` succeeds.

- [ ] **Step 2: Run and confirm failure**

Run: `python3 -m unittest tests.test_cli -v`

Expected: CLI module or wrapper not found.

- [ ] **Step 3: Implement CLI commands**

Provide these commands; every command also accepts `--state-root PATH` and `--now ISO8601`:

```text
begin --mode preflight|run [--approve-preview ID]
resume --run-id ID
event --run-id ID --kind KIND [--field KEY=VALUE]
preview --run-id ID --seed INTEGER
approve --run-id ID --preview-id ID
finish --run-id ID --status completed|paused_incomplete|incomplete_candidate_exhausted|approval_rejected
status [--json]
dashboard
```

`begin` prints machine-readable JSON containing `run_id`, `daily_task_id`, remaining quota, onboarding state, and any `recoverable_run_id`; it refuses to create a new run until recovery is resolved. It creates the full `CheckpointHeader` before returning and automatically appends deterministic failures for episodes expired at `--now`. `resume` reopens the effective checkpoint without copying events. `event` validates per-kind fields before appending and atomically adds required episode lifecycle events for confirmed outgoing likes or new received likes. `preview` canonicalizes candidate data, writes `preview_created`, and prints ID, digest, expiry, seed, quota snapshot, and explanations. `approve` requires the latest sealed preview, uses its seed/quota snapshot to regenerate the plan from the approval run's freshly observed candidates, recomputes the digest internally, and appends approval only on exact match within 24 hours. `finish` seals exactly one immutable run log.

- [ ] **Step 4: Add idempotency and pause guards**

Use `action_id = sha256(daily_task_id + photographer_id + photo_id + action_kind)` for duplicate rejection. `load_effective_runs` includes recoverable checkpoints only when no sealed log has the same run ID. Once a `safety_paused` event exists in the active checkpoint, reject further `outgoing_*` events; only `finish --status paused_incomplete` is allowed. A later explicit run may start after no active checkpoint remains.

- [ ] **Step 5: Run CLI and full tests**

Run: `python3 -m unittest tests.test_cli -v && python3 -m unittest discover -s tests -v`

Expected: all tests pass; all outputs are UTF-8 JSON or Markdown without external writes.

- [ ] **Step 6: Commit**

```bash
git add .agents/skills/500px-feedback-growth/scripts tests/test_cli.py
git commit -m "feat: add feedback growth cli"
```

### Task 6: Create the self-contained Dashboard with `@visualize:visualize`

**Files:**
- Create: `.agents/skills/500px-feedback-growth/assets/dashboard.html`
- Create: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/dashboard.py`
- Create: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing dashboard data and rendering tests**

Build one partial task with 25 likes and one completed task with 100 likes, 82 unique photographers, 18 reinforcements, 6 comments, 9 new reciprocators, two tier changes, quota counts `45/20/15/20`, three `already_liked` skips, and no risk event. Assert:

- The partial task ID appears in `current_task` and not in `history_tabs`.
- The completed task produces exactly one tab containing all values above plus `completed_at`, skip counts, and `risk_events=[]`.
- `kpi.value` exactly equals `matured_cohort_kpi` and `kpi.denominator` excludes open episodes.
- The view model contains `daily_trend`, `tier_distribution`, `funnel`, `latency_buckets`, and `verified_ranking`.
- Rendered HTML has no remote script, stylesheet, font, image, `http://`, or `https://` reference.
- A display name containing `</script>` is embedded as `<\/script>` and cannot terminate the data script.

- [ ] **Step 2: Run and confirm failure**

Run: `python3 -m unittest tests.test_dashboard -v`

Expected: import failure for `feedback_growth.dashboard`.

- [ ] **Step 3: Invoke `@visualize:visualize` for the template**

Generate a dense but readable Chinese operations dashboard with: progress cards, 30-day trend, tier distribution, attribution funnel, latency distribution, verified ranking, and horizontally scrollable completed-task tabs. Require no remote assets and reserve a single `__DASHBOARD_DATA__` placeholder for embedded JSON.

- [ ] **Step 4: Implement dashboard view-model and safe embedding**

Expose `build_view_model(state, now) -> Dict[str, Any]` and `render_dashboard(template_path, output_path, view_model) -> None`.

The view model contract is:

```python
{
    "generated_at": "ISO8601",
    "current_task": {
        "daily_task_id": "string or empty",
        "confirmed_likes": 0,
        "unique_photographers": 0,
        "confirmed_comments": 0,
        "status": "not_started | active | paused_incomplete | incomplete_candidate_exhausted | completed",
        "pause_reason": "string or empty",
    },
    "kpi": {"value": None, "numerator": 0, "denominator": 0},
    "verified_count": 0,
    "daily_trend": [],
    "tier_distribution": {"verified": 0, "promising": 0, "new": 0, "dormant": 0},
    "funnel": {"touches": 0, "open_episodes": 0, "successful_episodes": 0, "verified": 0},
    "latency_buckets": [],
    "verified_ranking": [],
    "history_tabs": [{
        "daily_task_id": "YYYY-MM-DD",
        "completed_at": "ISO8601",
        "unique_photographers": 80,
        "reinforcement_likes": 20,
        "confirmed_comments": 0,
        "new_reciprocators": 0,
        "tier_changes": [],
        "quota_counts": {"exploit_first": 45, "retest": 20, "new": 15, "verified_second": 20},
        "skip_counts": {},
        "risk_events": [],
    }],
}
```

Only daily tasks with exactly 100 confirmed likes become history tabs. Partial, paused, and exhausted tasks appear in the current-status area. Serialize with `ensure_ascii=False`, sorted keys, and replace `</` with `<\/` before embedding.

- [ ] **Step 5: Run automated tests**

Run: `python3 -m unittest tests.test_dashboard -v && python3 -m unittest discover -s tests -v`

Expected: all tests pass and generated HTML contains no `http://` or `https://` dependency.

- [ ] **Step 6: Perform visual QA**

Generate fixture dashboards for a completed and a partial task, open/render at desktop and narrow widths, and inspect screenshots. Fix only layout defects that obscure metrics, tabs, or labels; rerun automated tests after changes.

- [ ] **Step 7: Commit**

```bash
git add .agents/skills/500px-feedback-growth/assets .agents/skills/500px-feedback-growth/scripts/feedback_growth/dashboard.py tests/test_dashboard.py
git commit -m "feat: add feedback growth dashboard"
```

### Task 7: Write the discoverable skill and stable browser workflow

**Files:**
- Create: `.agents/skills/500px-feedback-growth/SKILL.md`
- Create: `.agents/skills/500px-feedback-growth/agents/openai.yaml`
- Create: `.agents/skills/500px-feedback-growth/references/browser-workflow.md`
- Create: `tests/test_skill_contract.py`

- [ ] **Step 1: Write failing static contract tests**

Assert the skill declares all four operations, references Computer Use explicitly, contains first-run approval, 30/12/25/100 limits, exact comment text, page-confirmation rules, CLI commands, and all hard stop conditions. Assert `agents/openai.yaml` exists and opts out of implicit invocation.

- [ ] **Step 2: Run and confirm failure**

Run: `python3 -m unittest tests.test_skill_contract -v`

Expected: missing skill files.

- [ ] **Step 3: Write `SKILL.md` with progressive disclosure**

The entrypoint must:

- Route natural requests to `preflight`, `run`, `status`, or `dashboard`.
- Run `status` before any browser mutation and stop if today is complete or paused.
- When internal `begin --mode run` returns `preflight_required`, immediately execute the full `preflight` path and present its preview instead of surfacing a raw CLI error or attempting interaction.
- Require public `run --approve <preview_id>` only for onboarding. Internally call `begin --mode run --approve-preview <preview_id>`, repeat the candidate scan into that run, call CLI `approve --run-id <run_id> --preview-id <preview_id>`, and proceed only when its JSON response is `approved=true`.
- On `preview_not_latest`, `preview_changed`, or `preview_expired`, seal the approval run with `approval_rejected`, automatically run a new preflight, and return the replacement preview ID; never leave the rejected approval checkpoint active.
- Read `references/browser-workflow.md` before `preflight` or `run`.
- Invoke `computer-use:computer-use` only for logged-in page reading and confirmed UI actions.
- Append a checkpoint immediately after each observed or confirmed action.
- Never claim success from the click alone.
- Stop on CAPTCHA, rate limit, login loss, warning, ambiguous state, or mismatched account.

- [ ] **Step 4: Write the browser workflow reference**

Document the exact semantic sequence:

1. Verify Chrome account profile URL resolves to `Dora0125` / user ID `f43fc656a435b8f41e84d05b0123c2485`.
2. Read the current newest 30 works and liker lists; record work position and each liker by stable ID.
3. Start from the baseline promising queue, otherwise the newest owned work with comments.
4. On a candidate profile inspect at most 12 newest works and open the first visibly unliked one.
5. Confirm before/after like state; record only confirmed change.
6. For eligible verified users, check local 7-day cooldown and visible duplicate before commenting exactly “拍的真棒👍”.
7. Read the resulting work comments for the next candidate; use local reseed when the chain is exhausted.
8. Apply one-refresh ordinary retry and immediate safety stop rules.

- [ ] **Step 5: Add discovery metadata**

```yaml
interface:
  display_name: "500px Feedback Growth"
  short_description: "用可归因反馈优化 500px 点赞互动"
  default_prompt: "使用 $500px-feedback-growth 扫描回馈、选择摄影师并安全执行本次点赞批次。"

policy:
  allow_implicit_invocation: false
```

- [ ] **Step 6: Run contract and full tests**

Run: `python3 -m unittest tests.test_skill_contract -v && python3 -m unittest discover -s tests -v`

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add .agents/skills/500px-feedback-growth tests/test_skill_contract.py
git commit -m "feat: add 500px feedback growth skill"
```

### Task 8: Validate structure, run a non-mutating preflight, and hand off

**Files:**
- Modify only if validation finds defects in task-owned files.
- Create locally ignored fixture/output data under `.local/500px-feedback-growth/`.

- [ ] **Step 1: Run the complete automated suite**

Run: `python3 -m unittest discover -s tests -v`

Expected: all tests pass.

- [ ] **Step 2: Run `skill-creator` structural validation**

Invoke `@skill-creator`, then run its official validator against `.agents/skills/500px-feedback-growth`. Fix structural failures and rerun until clean.

Expected: skill metadata, description, references, and discovery YAML validate successfully.

- [ ] **Step 3: Validate CLI reconstruction and Dashboard determinism**

Create fixture events through the CLI using `--now 2026-08-12T12:00:00Z` and seed `8122026`, seal them, run `status --json` twice with the same `--now`, rebuild Dashboard twice with the same `--now`, and compare outputs.

Expected: identical status JSON and HTML for a fixed injected clock/seed.

- [ ] **Step 4: Execute browser `preflight` only**

Invoke `@computer-use:computer-use`. Verify the logged-in account, dynamically scan the current latest 30 works, inspect stable like/comment targets, ingest observations, and generate the first 25-candidate preview. Do not click Like, submit a comment, follow, message, or attempt to solve any CAPTCHA.

Expected: a sealed preflight Markdown log plus a preview ID, digest, expiry, and explained candidate list.

- [ ] **Step 5: Rebuild and inspect the real local Dashboard**

Invoke `@visualize:visualize`, generate `.local/500px-feedback-growth/dashboard.html`, and visually verify the current incomplete state appears in the header with no completed history Tab.

- [ ] **Step 6: Run final verification**

Run: `python3 -m unittest discover -s tests -v && git diff --check && git status --short`

Expected: tests pass; no whitespace errors; only intended task files are modified or untracked. Local state remains ignored.

- [ ] **Step 7: Commit validation fixes, if any**

```bash
git add .agents/skills/500px-feedback-growth tests .gitignore
git commit -m "test: validate feedback growth workflow"
```

- [ ] **Step 8: Handoff at the first interaction gate**

Report the skill path, tests, structural validation, Dashboard path, preflight preview ID, candidate count, and any page limitations. Stop before real likes/comments and ask the user to approve the exact preview via `run --approve <preview_id>`.

## End-to-end contract example

The implementation is complete only when this exact lifecycle works in a temporary state root and the browser preflight reproduces the same state transitions:

1. `begin --mode run --now 2026-08-12T09:00:00Z` returns exit `2`, code `preflight_required`.
2. `begin --mode preflight --now 2026-08-12T09:00:00Z` returns `run_id=preflight-1` and remaining daily quota `100`.
3. Browser observations are appended with `event`; `preview --run-id preflight-1 --seed 8122026` returns preview `preview-1`, digest `D`, and expiry `2026-08-13T09:00:00Z`.
4. `finish --run-id preflight-1 --status completed` seals one immutable preflight log.
5. Public `run --approve preview-1` starts internal run `run-1`; the browser repeats the candidate scan, then `approve --run-id run-1 --preview-id preview-1` recomputes `D` and returns `approved=true`.
6. A confirmed like is checkpointed in `run-1`; simulate interruption before sealing.
7. `begin --mode run` refuses a new run and reports `recoverable_run_id=run-1`; `resume --run-id run-1` returns the confirmed action.
8. Continue to 25 confirmed likes and call `finish --run-id run-1 --status completed`; reconstruction counts all 25 once and ignores the retained checkpoint.
9. Repeat batches to 100 for the Shanghai day. `status --json` reports 100 likes and at least 80 unique photographers; Dashboard creates exactly one completed history Tab.
10. On the next scan, a genuinely new liker pair first observed after its photographer's latest touch closes the matching episode successfully. A pair already known before the touch remains baseline evidence and cannot close it.
11. Create two valid previews and verify approval of the older one returns `preview_not_latest`. Change one candidate before approving the latest and verify `preview_changed`; seal that approval run as `approval_rejected`, automatically produce a replacement preview, and verify no rejected checkpoint remains active.
