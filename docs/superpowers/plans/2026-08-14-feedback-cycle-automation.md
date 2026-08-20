# 公开作品回馈周期与临时回顾任务 Implementation Plan

> 状态：Implemented then Superseded。本文只保留旧 cycle/review 实施历史；新运行的最新 3 张即时结算以 ADR-0005 为准。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把一次点赞、冻结的 5 张主页展示作品、+20h 1 日回顾和 +70h 3 日回顾串成可重建周期，并为当前历史周期只创建一次 +70h 回顾任务。

**Architecture:** 保持现有 schema v1 的点赞/episode 事件不变，新增独立 cycle 事件和 `cycles.py` 聚合边界；CLI 负责创建周期、冻结 baseline、生成/绑定调度 intent、记录逐作品回顾和迁移旧事件。Codex skill 作为宿主适配层调用临时 automation，automation payload 只保存 cycle 标识和本地状态路径；作品、摄影师和 observation 明细继续只存在 append-only 本地日志。

**Tech Stack:** Python 3.9 标准库、Markdown fenced JSON append-only 日志、`unittest`、自包含 HTML/CSS/JavaScript Dashboard、Codex heartbeat automation、Chrome Browser skill。

**Review:** 已完成三轮独立 plan review；第三轮剩余三个 P1 已按 cycle-aware begin/run binding 和显式 observation/evidence 数据流修订。

---

## 写入事务合同

所有 cycle mutation 继续使用现有 checkpoint/sealed-run，不增加第二套日志：

- `begin --mode cycle --cycle-id <id>`：周期准备、schedule intent/bind 等短事务；返回 `run_id`，header 从创建时就保存 cycle ID。
- `begin --mode review --cycle-id <id> --review-kind <kind> --attempt <n>`：一个回顾 attempt 一个 checkpoint；逐作品追加，完成、失败或 superseded 后 seal。
- `begin --mode migration`：旧周期 analyze 后的 apply/mapping 事务。
- `begin --mode run --cycle-id <id>` 在创建点赞 checkpoint 前验证 cycle 已 baseline_ready 且不存在其他未结算周期，并立即写 `cycle_run_bound`；`cycle_like_completed` 在独立 `mode=cycle` 事务中映射一个或多个已绑定且已封存的 run。

`cycle/review/migration` checkpoint 不占每日唯一点赞 checkpoint，也不使用每日额度。Review checkpoint 可以跨 Asia/Shanghai 日界线恢复，但只能恢复相同 `(cycle_id, review_kind, attempt)`；cycle/migration checkpoint 超过 24 小时未完成时封存为 `paused_incomplete`，重新执行前先重算事实。每个 mutating command 都接受 `--run-id`，成功/失败均通过现有 `finish` seal；外部 automation 创建期间保留 schedule checkpoint，以覆盖 create 后 bind 前崩溃。

`CheckpointHeader` 增加可选 `transaction_context: Mapping[str, str]`。旧 header 没有该字段时解析为 `{}`；新 header 精确保存：cycle 为 `{cycle_id}`，cycle-aware run 为 `{cycle_id}`，review 为 `{cycle_id, review_kind, attempt}`，migration 为 `{cycle_id}`。`load_effective_runs` 的 active 唯一键改为：run/preflight 按 `daily_task_id`，cycle/migration 按 `(mode, cycle_id)`，review 按 `(cycle_id, review_kind, attempt)`。`resume` 仅对 run/preflight 执行上海日界线限制；review 按 context 跨日恢复，且 begin 后尚无事件也能识别 attempt。

## 文件结构

- Create: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/cycles.py`：cycle 状态机、调度时间、scope 和成熟派生。
- Create: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/automation.py`：纯数据调度 request、确定性名称和 payload 校验，不直接调用宿主工具。
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/model.py`：cycle/review dataclass 与 AggregateState 字段。
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/store.py`：新增 cycle 事件 exact-field schema。
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/analytics.py`：重建 cycle、scoped attribution、派生成熟和旧日志 fallback。
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/cli.py`：cycle、schedule、review、migration 命令编排。
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/dashboard.py`：cycle 数据模型。
- Modify: `.agents/skills/500px-feedback-growth/assets/dashboard.html`：5 张作品、两次回顾和成熟尾差 UI。
- Modify: `.agents/skills/500px-feedback-growth/SKILL.md`、`agents/openai.yaml` 和 `references/*.md`：执行与临时任务合同。
- Modify: `AGENTS.md`、`README.md`、`docs/architecture.md`、`docs/operations.md`、`docs/knowledge-gaps.md`：长期规则和真实运行边界。
- Create: `tests/test_cycles.py`、`tests/test_automation_contract.py`、`tests/test_cycle_migration.py`。
- Modify: `tests/test_cli.py`、`tests/test_dashboard.py`、`tests/test_skill_contract.py`、`tests/helpers.py`。

### Task 1: Cycle 数据模型与事件 schema

**Files:**
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/model.py:41-102`
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/store.py:15-50`
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/cli.py:373-520`
- Create: `tests/test_cycles.py`
- Modify: `tests/test_store.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: 写失败测试，锁定 cycle dataclass 和 exact-field 事件**

```python
def test_cycle_events_keep_v1_touch_events_unchanged(self):
    old_log = run([confirmed_like("a1", "p1", NOW)])
    render_run_log(old_log)  # 旧事件仍可序列化
    render_run_log(run([event("cycle_started", NOW, cycle_id="c1", attribution_eligible=True)]))

def test_cycle_event_rejects_unknown_field(self):
    with self.assertRaisesRegex(LogValidationError, "unexpected extra"):
        render_run_log(run([event(
            "cycle_started", NOW, cycle_id="c1", attribution_eligible=True, extra=1
        )]))
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `python3 -m unittest -v tests.test_cycles tests.test_store`

Expected: FAIL，`cycle_started` 未定义或 model 缺失。

- [ ] **Step 3: 增加最小 dataclass**

```python
@dataclass(frozen=True)
class ReviewAttempt:
    attempt: int
    status: str
    due_at: datetime
    automation_id: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    failure_reason: Optional[str]
    observed_photo_ids: FrozenSet[str]

@dataclass(frozen=True)
class ReviewSlot:
    kind: str
    status: str
    due_at: Optional[datetime]
    attempts: Tuple[ReviewAttempt, ...]
    resolved_at: Optional[datetime]

@dataclass(frozen=True)
class FeedbackCycle:
    cycle_id: str
    attribution_eligible: bool
    showcase_photo_ids: Tuple[str, ...]
    baseline_pairs: FrozenSet[Tuple[str, str]]
    touch_action_ids: Tuple[str, ...]
    review_observations: Tuple["CycleObservation", ...]
    like_completed_at: Optional[datetime]
    review_1d: ReviewSlot
    review_3d: ReviewSlot
    status: str

@dataclass(frozen=True)
class CycleObservation:
    photo_id: str
    photographer_id: str
    observed_at: datetime
    observation_ref: str

@dataclass(frozen=True)
class EpisodeEvidence:
    episode_id: str
    photographer_id: str
    outcome: str
    expires_at: datetime
    feedback_first_seen_at: Optional[datetime]
    received_like_count: int
    touch_count: int
```

向 `AggregateState` 增加 `cycles: Mapping[str, FeedbackCycle]` 和 `latest_cycle_id: Optional[str]`，不修改既有字段。

同时给 `CheckpointHeader` 增加默认空的 `transaction_context`；parser 同时接受旧六字段 header 与新七字段 header，serializer 对新 checkpoint 总是写出 context。

- [ ] **Step 4: 在 `_EVENT_FIELDS` 增加以下 exact-field 合同**

| Event | Required fields |
|---|---|
| `cycle_started` | `cycle_id`, `attribution_eligible` |
| `cycle_showcase_observed` | `cycle_id`, `photo_id`, `photo_url`, `owner_id`, `visibility`, `position`, `evidence_summary` |
| `cycle_showcase_frozen` | `cycle_id`, `photo_ids`, `showcase_digest` |
| `cycle_baseline_scan_started` | `cycle_id`, `scan_id` |
| `cycle_baseline_like_observed` | `cycle_id`, `scan_id`, `photo_id`, `photographer_id`, `display_name`, `profile_url` |
| `cycle_baseline_photo_completed` | `cycle_id`, `scan_id`, `photo_id`, `liker_count` |
| `cycle_baseline_completed` | `cycle_id`, `scan_id`, `baseline_digest` |
| `cycle_run_bound` | `cycle_id`, `run_id`, `baseline_digest`, `bound_at` |
| `cycle_like_completed` | `cycle_id`, `mapped_run_ids`, `touch_action_ids`, `episode_ids`, `like_completed_at`, `terminal_status` |
| `review_schedule_requested` | `cycle_id`, `review_kind`, `attempt`, `due_at`, `state_root`, `automation_name`, `payload_digest` |
| `review_scheduled` | `cycle_id`, `review_kind`, `attempt`, `automation_id`, `payload_digest` |
| `review_started` | `cycle_id`, `review_kind`, `attempt`, `due_at`, `started_at` |
| `review_photo_observed` | `cycle_id`, `review_kind`, `attempt`, `scan_id`, `photo_id`, `photographer_ids`, `observed_at` |
| `review_completed` | `cycle_id`, `review_kind`, `attempt`, `scan_id`, `completed_at` |
| `review_failed` | `cycle_id`, `review_kind`, `attempt`, `reason`, `failed_at` |
| `review_superseded` | `cycle_id`, `review_kind`, `attempt`, `superseded_at` |
| `cycle_abandoned` | `cycle_id`, `reason`, `abandoned_at` |
| `cycle_attribution_scope_mapped` | `cycle_id`, `mapped_run_ids`, `showcase_photo_ids`, `touch_action_ids`, `episode_ids`, `observation_refs`, `attribution_eligible`, `mapping_digest` |

历史 observation 使用稳定引用 `run_id:event_index`；新 review observation 由 `(run_id, event_index)` 同样标识。List 字段必须验证为唯一字符串序列；`visibility` 仅允许 `public`；旧事件字段集合完全不变。缺字段、多字段、类型错误和重复 stable ref 都要有 RED 测试。测试通过公开 `render_run_log`、`append_checkpoint_events` 或 `seal_run` 触发 validator，不新增仅供测试的生产 API。

- [ ] **Step 5: 实现 context-aware begin/resume/load**

增加 begin 后立即崩溃、跨日 review resume、同日 run 与 review 并存、不同 review attempt checkpoint 不冲突的 CLI/store 测试，再按上面的唯一键和日界线规则实现最小修改。

- [ ] **Step 6: 运行测试确认 GREEN**

Run: `python3 -m unittest -v tests.test_cycles tests.test_store tests.test_cli`

Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add .agents/skills/500px-feedback-growth/scripts/feedback_growth/model.py \
  .agents/skills/500px-feedback-growth/scripts/feedback_growth/store.py \
  .agents/skills/500px-feedback-growth/scripts/feedback_growth/cli.py \
  tests/test_cycles.py tests/test_store.py tests/test_cli.py
git commit -m "feat: add feedback cycle event model"
```

### Task 2: Cycle 重建、状态机与 72 小时成熟

**Files:**
- Create: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/cycles.py`
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/analytics.py:73-365`
- Modify: `tests/test_cycles.py`
- Modify: `tests/test_analytics.py`

- [ ] **Step 1: 写状态机和边界失败测试**

覆盖：恰好 5 张、baseline 未完成、review 正交槽位、3 日先于 1 日、`expires_at-1s / expires_at / expires_at+1s`、旧显式 failure 优先、派生 failure 不重复、未结算周期阻止下一轮点赞。

```python
def test_review_3d_does_not_mature_before_expiry(self):
    state = rebuild_cycle(review_3d_done=True, now=EXPIRY - timedelta(seconds=1))
    self.assertEqual(state.episodes[EPISODE].outcome, "open")

def test_open_episode_derives_failure_at_expiry_after_final_review(self):
    state = rebuild_cycle(review_3d_done=True, now=EXPIRY)
    self.assertEqual(cycle_episode_outcome(state, EPISODE), "failure")
```

- [ ] **Step 2: 运行目标测试确认 RED**

Run: `python3 -m unittest -v tests.test_cycles tests.test_analytics`

Expected: FAIL，cycle reducer/派生函数不存在。

- [ ] **Step 3: 实现 focused reducer**

在 `cycles.py` 提供：

```python
def rebuild_cycles(events, episodes, now) -> Mapping[str, FeedbackCycle]: ...
def ensure_cycle_can_start(cycles, now) -> None: ...
def derived_cycle_status(cycle, episodes, now) -> str: ...
def scoped_episode_outcome(cycle, episode, now) -> str: ...
```

规则：`review_1d` 与 `review_3d` 独立；`review_3d=completed` 后、open episode 到期才派生 failure；`abandoned` 不参与 KPI；读操作不写 `cycle_settled`。

- [ ] **Step 4: 将 reducer 接入 `rebuild_state`**

无 cycle 事件时返回空 mapping，并保持现有 64 项测试输出不变。cycle 映射不得修改原始 `FeedbackEpisode.outcome`；Dashboard/KPI 通过 scoped outcome 读取。

- [ ] **Step 5: 运行测试确认 GREEN 与旧逻辑兼容**

Run: `python3 -m unittest -v tests.test_cycles tests.test_analytics`

Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add .agents/skills/500px-feedback-growth/scripts/feedback_growth/cycles.py \
  .agents/skills/500px-feedback-growth/scripts/feedback_growth/analytics.py \
  tests/test_cycles.py tests/test_analytics.py
git commit -m "feat: rebuild feedback cycle state"
```

### Task 3: 冻结 5 张展示作品和 baseline 硬门槛

**Files:**
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/cli.py:373-520`
- Modify: `tests/test_cli.py`
- Modify: `tests/helpers.py`

- [ ] **Step 1: 写 CLI 失败测试**

覆盖：不是 5 张、重复 ID、非本人/非公开、第五张 baseline 读取失败、零 liker 但完成、部分恢复、digest 确定性、baseline 完成后主页变化不修改冻结列表。

```python
def test_cycle_showcase_freeze_requires_five_complete_public_works(self):
    run_id = begin(root, mode="cycle", cycle_id="c1")["run_id"]
    invoke(root, "cycle-start", "--run-id", run_id, "--cycle-id", "c1")
    _, payload = invoke(root, "cycle-showcase-freeze", "--run-id", run_id, "--cycle-id", "c1")
    self.assertEqual(payload["code"], "showcase_requires_exactly_five")
```

- [ ] **Step 2: 运行确认 RED**

Run: `python3 -m unittest -v tests.test_cli.CliTest.test_cycle_showcase_freeze_requires_five_complete_public_works`

Expected: FAIL，未知命令。

- [ ] **Step 3: 增加命令**

```text
cycle-start --run-id <id> --cycle-id <id> --now <iso>
cycle-showcase-observe --run-id <id> --cycle-id <id> --photo-id <id> \
  --photo-url <url> --owner-id <owner> --visibility public --position <1..5> --evidence-summary <text>
cycle-showcase-freeze --run-id <id> --cycle-id <id>
cycle-baseline-start --run-id <id> --cycle-id <id> --scan-id <id>
cycle-baseline-observe --run-id <id> --cycle-id <id> --scan-id <id> \
  --photo-id <id> --photographer-id <id> --display-name <name> --profile-url <url>
cycle-baseline-photo-complete --run-id <id> --cycle-id <id> --scan-id <id> \
  --photo-id <id> --liker-count <n>
cycle-baseline-complete --run-id <id> --cycle-id <id> --scan-id <id> --now <iso>
cycle-status --cycle-id <id> --json
```

`cycle-showcase-freeze` 验证 owner 等于配置中的本人账号、visibility 为 public、position 恰好 1..5、URL canonical。`cycle-baseline-photo-complete` 使零 liker 作品也有明确完成证据；declared liker_count 必须等于该 photo/scan 的 observed unique count。`cycle-baseline-complete` 只有在 5 张作品都有独立 completion 时写 digest；任何 scan issue 阻止点赞 run。

`cycle-start` 只创建 `preparing`，本身不要求已经有 5 张；5 张硬门槛在 `cycle-showcase-freeze` 和 `cycle-baseline-complete`。真实点赞必须使用 `begin --mode run --cycle-id <id>`：它读取 sealed baseline、写 `cycle_run_bound`，并拒绝未 baseline_ready、已绑定到其他 cycle、存在未结算其他 cycle或同一 run 重复绑定。增加时间顺序、可恢复暂停仍保持 liking、一个 run 不能映射两个 cycle 的测试。

- [ ] **Step 4: 运行整个 CLI 测试**

Run: `python3 -m unittest -v tests.test_cli`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add .agents/skills/500px-feedback-growth/scripts/feedback_growth/cli.py \
  tests/test_cli.py tests/helpers.py
git commit -m "feat: freeze showcase cycle baseline"
```

### Task 4: 点赞终态与临时任务调度 intent

**Files:**
- Create: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/automation.py`
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/cli.py`
- Create: `tests/test_automation_contract.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: 写调度合同失败测试**

覆盖：最后 confirmed like 为基准、+20h/+70h、0-like abandoned、recoverable pause 不调度、deterministic name 带 attempt、payload mismatch、创建后未绑定恢复、同名已绑定 no-op。

```python
def test_review_requests_are_relative_to_last_confirmed_like(self):
    requests = build_review_requests("c1", LAST_TOUCH, STATE_ROOT)
    self.assertEqual(requests[0].due_at, LAST_TOUCH + timedelta(hours=20))
    self.assertEqual(requests[1].due_at, LAST_TOUCH + timedelta(hours=70))
```

- [ ] **Step 2: 运行确认 RED**

Run: `python3 -m unittest -v tests.test_automation_contract`

Expected: FAIL，`automation.py` 不存在。

- [ ] **Step 3: 实现纯数据 request**

```python
@dataclass(frozen=True)
class ReviewAutomationRequest:
    cycle_id: str
    review_kind: str
    attempt: int
    due_at: datetime
    state_root: str

    @property
    def name(self):
        return f"500px-review-{self.cycle_id}-{self.review_kind}-{self.attempt}"
```

提供 canonical payload/digest 和 `matches_existing()`；不得从 Python 调用 Codex 宿主 API。

- [ ] **Step 4: 增加 intent/bind CLI**

```text
cycle-like-complete --run-id <cycle-transaction-id> --cycle-id <id> \
  --mapped-run-id <id>... --status <terminal>
review-schedule-intent --run-id <cycle-transaction-id> --cycle-id <id> \
  --review-kind <kind> --attempt <n>
review-schedule-bind --run-id <cycle-transaction-id> --cycle-id <id> \
  --review-kind <kind> --attempt <n> --automation-id <id> --payload-digest <digest>
```

`cycle-like-complete` 验证至少 1 个 confirmed like，取所有 mapped run 中最大时间；只有 `completed` 或 `incomplete_candidate_exhausted` 时，才在同一次 checkpoint append 中原子写入 `cycle_like_completed` 和两个 attempt-1 `review_schedule_requested`。独立 `review-schedule-intent` 只用于当前历史迁移中单独补 3 日任务，以及用户明确授权的 retry attempt。

- [ ] **Step 5: 用 fake host adapter 覆盖崩溃点**

Fake adapter 保存 `name → payload/id`，模拟：两个原子 intent 已存在但第一个 host create 后崩溃、create 后 bind 前崩溃、同名 payload 不一致、用户授权 `attempt+1`。

- [ ] **Step 6: 运行测试确认 GREEN**

Run: `python3 -m unittest -v tests.test_automation_contract tests.test_cli`

Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add .agents/skills/500px-feedback-growth/scripts/feedback_growth/automation.py \
  .agents/skills/500px-feedback-growth/scripts/feedback_growth/cli.py \
  tests/test_automation_contract.py tests/test_cli.py
git commit -m "feat: add review automation intents"
```

### Task 5: 1 日/3 日逐作品回顾与 scoped attribution

**Files:**
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/cli.py`
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/cycles.py`
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/analytics.py`
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/model.py`
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/selector.py`
- Modify: `tests/test_cycles.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_selector.py`

- [ ] **Step 1: 写失败测试**

覆盖：只接受冻结 5 张、baseline pair 排除、同一摄影师多图只计 1、received_like_count 只数 scoped pair、零 liker 原子完成、部分扫描续跑、重复投递 no-op、3 日先完成后 1 日 superseded、迟到 observation 不伪造时间。

- [ ] **Step 2: 运行确认 RED**

Run: `python3 -m unittest -v tests.test_cycles tests.test_cli`

Expected: FAIL，review 命令不存在。

- [ ] **Step 3: 增加 review 命令**

```text
begin --mode review --cycle-id <id> --review-kind <kind> --attempt <n>
review-photo-observe --run-id <id> --cycle-id <id> --review-kind <kind> --attempt <n> \
  --photo-id <id> --scan-id <id> --liker-count <n> [--photographer-id <id> ...]
review-finish --run-id <id> --cycle-id <id> --review-kind <kind> --attempt <n>
review-fail --run-id <id> --cycle-id <id> --review-kind <kind> --attempt <n> --reason <reason>
```

`review-photo-observe` 一次原子写入该作品完整 photographer ID 列表；`liker-count=0` 且没有 photographer 参数表示已完整扫描但无人点赞，declared count 必须等于唯一 ID 数。每张作品完成后立即写一个 `review_photo_observed`。`review-finish` 要求 5 张都有本 attempt 或先前 attempt 的成功证据；只为 scoped、非 baseline 且在 episode 时间窗口内的 pair 生成 cycle attribution。

- [ ] **Step 4: 实现 scoped 计算**

```python
def scoped_feedback_pairs(cycle, observations, episodes): ...
def scoped_received_like_count(cycle, photographer_id): ...
```

旧 `feedback_episode_succeeded` 仍保留审计；增加统一证据接口：

```python
def eligible_episode_evidence(
    cycles: Mapping[str, FeedbackCycle],
    raw_episodes: Mapping[str, FeedbackEpisode],
    photographer_id: str,
    now: datetime,
) -> Tuple[EpisodeEvidence, ...]: ...
```

`rebuild_cycles` 从 `review_photo_observed` 和 migration `observation_refs` 生成 `CycleObservation(photo_id, photographer_id, observed_at, observation_ref)`，存入对应 `FeedbackCycle.review_observations`。`eligible_episode_evidence` 使用这些 observation、冻结 5 张、baseline pairs、raw episode 时间窗和 mapping 关系生成 `EpisodeEvidence`；它不读取旧 success 的 received photo/count。随后 `rebuild_state` 把结果写入 `PhotographerStats.eligible_episodes`；`PhotographerStats.episodes` 继续保存审计事实。`classify_photographer()`、`beta_parameters()`、`matured_cohort_counts()`、30 天 KPI 和 selector 的 tier 构建全部改读 `eligible_episodes`，保持它们当前只接收 `PhotographerStats` 的调用方式。无 cycle 的旧 episode 作为 eligible fallback；mapped cycle 只使用 scoped raw observations；`attribution_eligible=false` 和 abandoned cycle 返回零算法证据。首版回赞深度不进入 selector。

- [ ] **Step 5: 运行确认 GREEN**

增加 excluded success、abandoned cycle 和 attribution_eligible=false 对 tier、Beta 参数及 selector 采样都无影响的测试。

Run: `python3 -m unittest -v tests.test_cycles tests.test_cli tests.test_analytics tests.test_selector`

Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add .agents/skills/500px-feedback-growth/scripts/feedback_growth/cycles.py \
  .agents/skills/500px-feedback-growth/scripts/feedback_growth/analytics.py \
  .agents/skills/500px-feedback-growth/scripts/feedback_growth/model.py \
  .agents/skills/500px-feedback-growth/scripts/feedback_growth/selector.py \
  .agents/skills/500px-feedback-growth/scripts/feedback_growth/cli.py \
  tests/test_cycles.py tests/test_cli.py tests/test_analytics.py tests/test_selector.py
git commit -m "feat: record scoped cycle reviews"
```

### Task 6: 当前四-run 周期迁移

**Files:**
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/cli.py`
- Create: `tests/test_cycle_migration.py`
- Create: `tests/fixtures/legacy-four-run-cycle.json`

- [ ] **Step 1: 从真实日志生成脱敏 fixture**

只保留结构、时间关系、5 张/30 张范围、action/episode/observation 关联；替换摄影师、作品、URL 和 display name，不复制个人互动信息进 Git。

- [ ] **Step 2: 写迁移失败测试**

覆盖：四个 run 的最大 touch 时间、完整/缺失 baseline、约 19.2h 人工 review 迁移例外、旧 success 首次命中 5 张外但后续命中 5 张内、回赞深度重算、attribution_eligible=false。

- [ ] **Step 3: 运行确认 RED**

Run: `python3 -m unittest -v tests.test_cycle_migration`

Expected: FAIL，迁移命令不存在。

- [ ] **Step 4: 增加只读分析与显式写入两阶段命令**

```text
cycle-migrate-analyze --mapped-run-id <id>... --photo-id <id>... --json
cycle-migrate-apply --run-id <migration-transaction-id> --mapped-run-id <id>... \
  --photo-id <id>... --analysis-digest <digest> \
  --confirm-attribution-eligible <true|false>
```

Analyze 不写日志；Apply 在新进程中使用同一组 run/photo 输入重新分析，校验 digest、当前 sealed-log digest 和 5 张 baseline 未变化后，追加 mapping 事件。不得只凭裸 digest，也不得改写任何旧 run。

- [ ] **Step 5: 运行确认 GREEN**

Run: `python3 -m unittest -v tests.test_cycle_migration tests.test_analytics`

Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add .agents/skills/500px-feedback-growth/scripts/feedback_growth/cli.py \
  tests/test_cycle_migration.py tests/fixtures/legacy-four-run-cycle.json
git commit -m "feat: migrate legacy feedback cycle"
```

### Task 7: Dashboard 周期视图

**Files:**
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/dashboard.py`
- Modify: `.agents/skills/500px-feedback-growth/assets/dashboard.html`
- Modify: `tests/test_dashboard.py`

- [ ] **Step 1: 写数据与模板失败测试**

断言：最近 cycle 优先、旧日志 fallback、固定 5/5、两个 review 槽位、+70h 后到期前仍 open、到期后重建为 mature failure、每张展示作品新增回赞数、默认浅色主题。

- [ ] **Step 2: 运行确认 RED**

Run: `python3 -m unittest -v tests.test_dashboard`

Expected: FAIL，cycle 字段缺失。

- [ ] **Step 3: 实现 Dashboard payload**

```json
{
  "cycle": {
    "cycle_id": "...",
    "showcase_count": 5,
    "review_1d": {"status": "completed", "completed_at": "..."},
    "review_3d": {"status": "pending", "due_at": "..."},
    "settlement": {"status": "open", "next_expiry_at": "..."},
    "works": []
  }
}
```

- [ ] **Step 4: 调整 UI**

保留现有自适应柱状/折线规则；新增一个紧凑周期带和 5 张作品表，不引入第三方依赖，不把 +70h 观察写成 72h 成熟。

- [ ] **Step 5: 运行确认 GREEN**

Run: `python3 -m unittest -v tests.test_dashboard`

Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add .agents/skills/500px-feedback-growth/scripts/feedback_growth/dashboard.py \
  .agents/skills/500px-feedback-growth/assets/dashboard.html tests/test_dashboard.py
git commit -m "feat: show feedback cycles in dashboard"
```

### Task 8: Skill 与项目规则同步

**Files:**
- Modify: `.agents/skills/500px-feedback-growth/SKILL.md`
- Modify: `.agents/skills/500px-feedback-growth/agents/openai.yaml`
- Modify: `.agents/skills/500px-feedback-growth/references/browser-workflow.md`
- Modify: `.agents/skills/500px-feedback-growth/references/event-schema.md`
- Modify: `.agents/skills/500px-feedback-growth/references/operational-recovery.md`
- Modify: `.agents/skills/500px-feedback-growth/references/dashboard-semantics.md`
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/operations.md`
- Modify: `docs/knowledge-gaps.md`
- Modify: `tests/test_skill_contract.py`

- [ ] **Step 1: 写 skill contract 失败测试**

断言：启动点赞前冻结 5 张并完成 baseline；自动创建两个临时任务；当前迁移只创建 3 日任务；+70h 不提前判失败；任务只读、幂等、失败通知；上传与分享保持手动。

- [ ] **Step 2: 运行确认 RED**

Run: `python3 -m unittest -v tests.test_skill_contract`

Expected: FAIL，文档尚未包含新合同。

- [ ] **Step 3: 更新 skill 与 references**

自动化 prompt 必须明确：读取主工作区绝对 state root、恢复对应 cycle/review/attempt、只扫描冻结 5 张、每张立即 checkpoint、完成后 rebuild Dashboard、验证码/登录/警告立即停止、不执行互动。

- [ ] **Step 4: 更新项目级文档**

`AGENTS.md` 只保留长期硬规则；运行细节进入 operations/reference；knowledge gaps 删除已由实现解决的项，仅保留设备休眠延迟、真实临时任务可靠性和回赞深度排序证据。

- [ ] **Step 5: 验证文档和 skill**

Run: `python3 -m unittest -v tests.test_skill_contract`

Run: `python3 /Users/pony/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/500px-feedback-growth`

Expected: PASS；`Skill is valid!`

- [ ] **Step 6: Commit**

```bash
git add .agents/skills/500px-feedback-growth/SKILL.md \
  .agents/skills/500px-feedback-growth/agents/openai.yaml \
  .agents/skills/500px-feedback-growth/references/browser-workflow.md \
  .agents/skills/500px-feedback-growth/references/event-schema.md \
  .agents/skills/500px-feedback-growth/references/operational-recovery.md \
  .agents/skills/500px-feedback-growth/references/dashboard-semantics.md \
  AGENTS.md README.md docs/architecture.md docs/operations.md \
  docs/knowledge-gaps.md tests/test_skill_contract.py
git commit -m "docs: define automated feedback review cycles"
```

### Task 9: 当前周期迁移与唯一 +70h 任务

**Files:**
- Runtime only: `.local/500px-feedback-growth/`
- Host automation: current Codex task heartbeat

- [ ] **Step 1: 只读确认 due time 和迁移资格**

Run: `python3 .agents/skills/500px-feedback-growth/scripts/feedback_growth.py cycle-migrate-analyze --mapped-run-id <run-1> --mapped-run-id <run-2> --mapped-run-id <run-3> --mapped-run-id <run-4> --photo-id <photo-1> --photo-id <photo-2> --photo-id <photo-3> --photo-id <photo-4> --photo-id <photo-5> --state-root /Users/pony/Documents/ChatGPT/PressZan/.local/500px-feedback-growth --json`

Expected: 输出最大 touch、目标 +70h 时间、baseline 完整性、scoped observation 数、digest；不写日志。

若 due time 已过去，停止且不创建 migration checkpoint；若 baseline_unknown，先报告并取得用户是否接受 observational-only 的明确选择。

- [ ] **Step 2: 浏览器只读确认当前主页 5 张**

按 `chrome:control-chrome` 和 skill browser workflow 执行。若当前 5 张无法与历史 baseline 对齐，停止并向用户报告 `baseline_unknown`，不自行猜测 attribution eligibility。

- [ ] **Step 3: 开始 migration transaction 并应用 mapping**

Run: `python3 .agents/skills/500px-feedback-growth/scripts/feedback_growth.py begin --mode migration --cycle-id <cycle-id> --state-root /Users/pony/Documents/ChatGPT/PressZan/.local/500px-feedback-growth`

保存返回的 `<migration-run-id>`。只有 analyze digest 与现场读取一致时执行：

```text
cycle-migrate-apply --run-id <migration-run-id>
  --mapped-run-id <run-1> ... --mapped-run-id <run-4>
  --photo-id <photo-1> ... --photo-id <photo-5>
  --analysis-digest <digest>
  --confirm-attribution-eligible <true|false>
```

写后运行 `cycle-status --json`，确认 1 日 review 已完成且没有重复 success。

- [ ] **Step 4: 生成 review_3d attempt 1 intent**

在同一 `<migration-run-id>` checkpoint 执行：

```text
review-schedule-intent --run-id <migration-run-id> --cycle-id <cycle-id>
  --review-kind review_3d --attempt 1
```

再次确认 due time 尚未过去，只生成 `review_3d`；不得创建 `review_1d`。

- [ ] **Step 5: 创建一次性 heartbeat automation**

通过 Codex `automation_update` 创建附着当前任务的一次性临时任务，目标时间等于 intent 的 `due_at`。名称、cycle、kind、attempt、state_root 与 payload digest 必须完全一致；prompt 不包含作品/摄影师明细，也不包含通知偏好。

- [ ] **Step 6: 绑定 automation ID**

在同一 transaction 运行：

```text
review-schedule-bind --run-id <migration-run-id> --cycle-id <cycle-id>
  --review-kind review_3d --attempt 1 --automation-id <id>
  --payload-digest <digest>
finish --run-id <migration-run-id> --status completed
```

随后读取 automation 与 cycle 状态，确认 migration checkpoint 已封存，且只有一个 active `review_3d attempt=1`。Host create 后 bind 前失败时保留 checkpoint，下一次从相同 run/context 恢复；不得重新 apply mapping。

- [ ] **Step 7: 重建 Dashboard**

Run: `python3 .agents/skills/500px-feedback-growth/scripts/feedback_growth.py dashboard --state-root /Users/pony/Documents/ChatGPT/PressZan/.local/500px-feedback-growth`

Expected: 当前 cycle 显示 5/5、1 日已完成、3 日已调度；统计只来自 scoped 5 张。

### Task 10: 全量验证与交付

**Files:**
- All changed implementation, tests, docs, runtime-derived Dashboard

- [ ] **Step 1: 运行全量测试**

Run: `python3 -m unittest discover -v`

Expected: 全部 PASS，0 failures/errors。

- [ ] **Step 2: 运行静态检查**

Run: `git diff --check`

Expected: exit 0。

- [ ] **Step 3: 验证 skill 结构**

Run: `python3 /Users/pony/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/500px-feedback-growth`

Expected: `Skill is valid!`

- [ ] **Step 4: 真实状态核验**

确认 `.local` 仍被忽略、sealed logs 未被改写、只追加 migration/cycle 事件、automation 数量恰好 1、due time 正确、Dashboard payload 不含 30 张范围外的 cycle success。

- [ ] **Step 5: Dashboard 视觉 QA**

在桌面和窄窗口检查浅色默认、周期带、5 张作品表、1 日/3 日状态和主题切换；若 `file://` 被浏览器策略阻止，明确记录未验证项，不以源码检查替代实际渲染。

- [ ] **Step 6: 检查 repository 状态**

Run: `git status --short --branch`

Expected: 计划任务的实现已由前序精确 commit 收录；只剩进入执行前就存在的、已识别的工作区改动或运行时 ignored 文件。若验证阶段产生修复，只暂存修复涉及的明确文件并单独提交。

- [ ] **Step 7: 最终报告**

报告：当前周期 ID、冻结 5 张是否有完整 baseline、scoped 1 日回馈人数、3 日任务执行时间、automation ID、测试结果、未验证风险；不得输出个人摄影师名单或认证数据。
