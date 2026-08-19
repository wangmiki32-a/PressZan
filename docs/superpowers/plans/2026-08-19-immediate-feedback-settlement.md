# Immediate Feedback Settlement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把新 500px 任务切换为“启动时扫描本人最新三张、覆盖 200 位后当日立即结算”的反馈积分模型，同时完整保留旧 sealed logs 的只读兼容。

**Architecture:** 在现有 append-only Markdown 事件流上新增 `feedback_scan_completed`，由 `analytics.py` 重建每次触达的 0–3 分即时反馈账本。新运行不再自动创建 episode、cycle 或 review；`cycles.py` 与 `automation.py` 保留用于读取历史数据，Dashboard 改为消费即时账本派生的五类简化视图。

**Tech Stack:** Python 3.9 标准库、`unittest`、append-only Markdown/JSON fenced events、离线 HTML/CSS/JavaScript。

**Spec:** `docs/superpowers/specs/2026-08-19-immediate-feedback-settlement-design.md`

## Global Constraints

- 不新增生产依赖；运行逻辑保持 Python 3.9 标准库兼容。
- 不修改、覆盖、移动或删除任何现有 `.local/500px-feedback-growth/` 日志或 checkpoint。
- `.local/500px-feedback-growth/runs/*.md` 继续是 sealed source of truth；Dashboard 与分层只能从事件重建。
- 新流程每次覆盖恰好 200 位不同摄影师；候选只检查主页第一张作品，确认点赞后评论 `👍👍👍`。
- 本人反馈扫描只读取当前最新 3 张公开作品；首次完整读取只建立基线，不计旧点赞。
- 原始反馈分不上限；排序有效分使用 30 天半衰期并封顶 12。
- 新配额固定为 `120 exploit_first / 60 new / 20 retest`。
- 旧 cycle、episode、review、automation 事件保持可解析，但不得限制不带 `--cycle-id` 的新运行。
- 测试使用临时状态目录、固定时钟和固定随机种子，不执行真实点赞或评论。
- 每个任务先看到目标测试失败，再写最小实现；每个提交前运行该任务列出的测试与 `git diff --check`。

## File Structure

- `.agents/skills/500px-feedback-growth/scripts/feedback_growth/model.py`：增加即时扫描、触达证据数据结构，并以默认字段保持旧测试构造兼容。
- `.agents/skills/500px-feedback-growth/scripts/feedback_growth/store.py`：校验 `purpose`、`settlement_mode` 与 `feedback_scan_completed` 的严格字段和列表约束。
- `.agents/skills/500px-feedback-growth/scripts/feedback_growth/analytics.py`：唯一的即时反馈账本重建器、旧 episode 适配器、分层与 Beta 参数。
- `.agents/skills/500px-feedback-growth/scripts/feedback_growth/selector.py`：新配额和确定性回填。
- `.agents/skills/500px-feedback-growth/scripts/feedback_growth/cli.py`：生成扫描完成事件；新点赞直接结算，不再自动创建/延长 episode。
- `.agents/skills/500px-feedback-growth/scripts/feedback_growth/dashboard.py`：构建五区块 view model，保留历史任务基础列表。
- `.agents/skills/500px-feedback-growth/assets/dashboard.html`：渲染简化 Dashboard，保留离线、浅色默认、手动深色和响应式合同。
- `tests/helpers.py`、`tests/test_store.py`、`tests/test_analytics.py`、`tests/test_selector.py`、`tests/test_cli.py`、`tests/test_dashboard.py`：固定新行为和 legacy 回归。
- `tests/test_skill_contract.py`：固定 skill、事件文档和浏览器流程的新合同。
- `AGENTS.md`、`README.md`、`docs/architecture.md`、`docs/operations.md`、`.agents/skills/500px-feedback-growth/SKILL.md` 及 references：同步公共操作语义。
- `docs/decisions/ADR-0005-immediate-feedback-settlement.md`：记录替代 cycle/review 主流程的长期决策。

---

### Task 1: 即时扫描事件与模型边界

**Files:**
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/model.py:7-180`
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/store.py:14-113,134-154`
- Modify: `tests/helpers.py:1-60`
- Modify: `tests/test_store.py:62-132`

**Interfaces:**
- Produces: `FeedbackScan`, `TouchFeedbackEvidence` dataclasses。
- Produces: `OutgoingTouch.episode_id: Optional[str]` 与 `OutgoingTouch.settlement_mode: str`。
- Produces: `AggregateState.feedback_scans`, `AggregateState.touch_feedback`, `AggregateState.baselined_photo_ids`。
- Produces: 严格事件 schema：`scan_started.purpose?`、`outgoing_like_confirmed.settlement_mode?`、`feedback_scan_completed`。

- [ ] **Step 1: 写失败的 store/model 测试**

在 `tests/test_store.py` 增加完整 round-trip 和非法列表测试：

```python
def test_feedback_scan_completed_round_trip(self):
    completed_at = dt(19, 9)
    item = event(
        "feedback_scan_completed",
        completed_at,
        scan_id="scan-3",
        photo_ids=["mine-1", "mine-2", "mine-3"],
        completed_photo_ids=["mine-1", "mine-3"],
        baseline_photo_ids=["mine-3"],
        new_pair_count=2,
        new_feedback_photographer_count=1,
        new_feedback_points=2,
        completed_at=completed_at.isoformat(),
    )
    with TemporaryDirectory() as directory:
        root = Path(directory)
        path = seal_run(root, run([item], run_id="scan-run", day="2026-08-19"))
        rebuilt = parse_run_log(path)
    self.assertEqual(rebuilt.events, (item,))

def test_feedback_scan_rejects_duplicate_photo_ids(self):
    item = event(
        "feedback_scan_completed",
        dt(19, 9),
        scan_id="scan-3",
        photo_ids=["mine-1", "mine-1", "mine-3"],
        completed_photo_ids=["mine-1"],
        baseline_photo_ids=[],
        new_pair_count=0,
        new_feedback_photographer_count=0,
        new_feedback_points=0,
        completed_at=dt(19, 9).isoformat(),
    )
    with TemporaryDirectory() as directory:
        with self.assertRaisesRegex(LogValidationError, "photo_ids must contain unique"):
            seal_run(Path(directory), run([item]))
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_store -v`

Expected: `feedback_scan_completed` 报 `unknown event kind`，或新 dataclass 尚不存在。

- [ ] **Step 3: 增加不可变数据结构**

在 `model.py` 增加：

```python
@dataclass(frozen=True)
class FeedbackScan:
    scan_id: str
    occurred_at: datetime
    photo_ids: Tuple[str, ...]
    completed_photo_ids: FrozenSet[str]
    baseline_photo_ids: FrozenSet[str]
    new_pair_count: int
    new_feedback_photographer_count: int
    new_feedback_points: int
    issue_photo_ids: FrozenSet[str]


@dataclass(frozen=True)
class TouchFeedbackEvidence:
    action_id: str
    photographer_id: str
    touch_at: datetime
    feedback_points: int
    feedback_first_seen_at: Optional[datetime]
    unanswered: bool
    settlement_mode: str
```

把 `OutgoingTouch.episode_id` 改为 `Optional[str]`，并追加 `settlement_mode: str = "legacy"`。给 `PhotographerStats` 追加以下默认字段：

```python
raw_feedback_points: int = 0
feedback_points_30d: int = 0
touch_count: int = 0
touch_count_30d: int = 0
unanswered_touch_count_30d: int = 0
effective_feedback_points: float = 0.0
effective_unanswered_touches: float = 0.0
last_feedback_at: Optional[datetime] = None
last_unanswered_touch_at: Optional[datetime] = None
```

给 `AggregateState` 追加：

```python
feedback_scans: Tuple[FeedbackScan, ...] = ()
touch_feedback: Mapping[str, TouchFeedbackEvidence] = field(default_factory=dict)
baselined_photo_ids: FrozenSet[str] = frozenset()
```

- [ ] **Step 4: 扩展严格事件校验**

在 `_EVENT_FIELDS` 中将 `purpose` 设为 `scan_started` 可选字段，将 `settlement_mode` 设为 `outgoing_like_confirmed` 可选字段，并新增：

```python
"feedback_scan_completed": (
    {
        "scan_id",
        "photo_ids",
        "completed_photo_ids",
        "baseline_photo_ids",
        "new_pair_count",
        "new_feedback_photographer_count",
        "new_feedback_points",
        "completed_at",
    },
    set(),
),
```

把 `completed_photo_ids`、`baseline_photo_ids` 加入 `_UNIQUE_STRING_LIST_FIELDS`，并在 `_validate_event` 中验证：三个 count 字段是非负整数；`completed_photo_ids <= photo_ids`；`baseline_photo_ids <= completed_photo_ids`；`completed_at` 是带时区 ISO 时间；`purpose` 只能是 `latest_three_feedback`；`settlement_mode` 只能是 `immediate` 或 `legacy`。

- [ ] **Step 5: 运行 store 测试**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_store -v`

Expected: PASS。

- [ ] **Step 6: 提交事件与模型边界**

```powershell
git add -- .agents/skills/500px-feedback-growth/scripts/feedback_growth/model.py .agents/skills/500px-feedback-growth/scripts/feedback_growth/store.py tests/helpers.py tests/test_store.py
git commit -m "feat: add immediate feedback scan events"
```

---

### Task 2: 即时反馈账本、旧日志适配与分层

**Files:**
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/analytics.py:1-398`
- Modify: `tests/helpers.py:1-90`
- Modify: `tests/test_analytics.py:1-300`
- Test: `tests/test_cycles.py`

**Interfaces:**
- Consumes: Task 1 的 `FeedbackScan`、`TouchFeedbackEvidence` 与新事件字段。
- Produces: `build_feedback_scan_completed_event(logs, run_id, scan_id, completed_photo_ids, now) -> Event`。
- Produces: `rebuild_state(...).touch_feedback`，每个新触达为 0–3 分且最多一个轻负样本。
- Produces: `classify_photographer(stats, now) -> str` 与 `beta_parameters(stats, now) -> Tuple[float, float]` 的新语义。

- [ ] **Step 1: 给 helpers 增加明确的即时扫描构造器**

```python
def immediate_like(action_id, photographer_id, occurred_at, bucket="exploit_first", photo_id=None):
    item = confirmed_like(action_id, photographer_id, occurred_at, bucket, photo_id)
    return Event(item.kind, item.occurred_at, {**item.data, "settlement_mode": "immediate"})


def feedback_scan(scan_id, occurred_at, observations):
    photo_ids = ("mine-1", "mine-2", "mine-3")
    events = [event("scan_started", occurred_at, scan_id=scan_id, owner_id="me", profile_url="https://example.test/me", purpose="latest_three_feedback")]
    for position, photo_id in enumerate(photo_ids, 1):
        events.append(event("work_observed", occurred_at, scan_id=scan_id, photo_id=photo_id, photo_url=f"https://example.test/{photo_id}", position=position))
    for photo_id, photographer_id in observations:
        events.append(received(photo_id, photographer_id, occurred_at, position=photo_ids.index(photo_id) + 1))
        events[-1] = Event(events[-1].kind, events[-1].occurred_at, {**events[-1].data, "scan_id": scan_id})
    return events
```

- [ ] **Step 2: 写失败的 0/1/2/3 分、去重和基线测试**

在 `tests/test_analytics.py` 增加一组使用两个完整扫描的测试；核心断言为：

```python
def test_three_new_works_give_three_points_and_clear_negative(self):
    baseline_at = dt(18, 8)
    touch_at = dt(18, 10)
    feedback_at = dt(19, 8)
    events = feedback_scan("base", baseline_at, [])
    events.append(event("feedback_scan_completed", baseline_at, scan_id="base", photo_ids=["mine-1", "mine-2", "mine-3"], completed_photo_ids=["mine-1", "mine-2", "mine-3"], baseline_photo_ids=["mine-1", "mine-2", "mine-3"], new_pair_count=0, new_feedback_photographer_count=0, new_feedback_points=0, completed_at=baseline_at.isoformat()))
    events.append(immediate_like("a1", "p1", touch_at))
    events.extend(feedback_scan("next", feedback_at, [("mine-1", "p1"), ("mine-2", "p1"), ("mine-3", "p1")]))
    events.append(event("feedback_scan_completed", feedback_at, scan_id="next", photo_ids=["mine-1", "mine-2", "mine-3"], completed_photo_ids=["mine-1", "mine-2", "mine-3"], baseline_photo_ids=[], new_pair_count=3, new_feedback_photographer_count=1, new_feedback_points=3, completed_at=feedback_at.isoformat()))

    state = rebuild_state([run(events, day="2026-08-19")], feedback_at)

    evidence = state.touch_feedback["a1"]
    self.assertEqual(evidence.feedback_points, 3)
    self.assertFalse(evidence.unanswered)
    self.assertEqual(classify_photographer(state.photographers["p1"], feedback_at), "verified")
```

同组分别覆盖：单张 `+1`、两张 `+2`、重复配对仍为原分、首次基线为 0、不完整作品不建基线、最近触达封顶 3 后不向旧触达倒灌。

- [ ] **Step 3: 写失败的 legacy 适配、dormant 与衰减测试**

新增断言：旧 success 的 `received_like_count=5` 映射为 3 分；旧 failure/open 各映射一个轻负样本；同一旧 episode 不与 observation 重复；历史累计 3 次触达且最近 30 天 0 分为 `dormant`；最后一次未反馈触达满 7 天才允许重测；有效正分封顶 12。

```python
def photographer_stats(**changes):
    values = {
        "photographer_id": "p1",
        "display_name": "p1",
        "profile_url": "https://example.test/p1",
        "baseline_work_ids": frozenset(),
        "baseline_work_positions": {},
        "historical_high_potential": False,
        "episodes": (),
        "eligible_episodes": (),
        "last_comment_at": None,
        "today_like_photo_ids": (),
        "success_count_30d": 0,
        "failure_count": 0,
        "dormant_retest_eligible": False,
    }
    values.update(changes)
    return PhotographerStats(**values)


def test_effective_feedback_points_decay_and_cap_at_twelve(self):
    stats = photographer_stats(
        raw_feedback_points=30,
        effective_feedback_points=12.0,
        effective_unanswered_touches=2.5,
    )
    self.assertEqual(beta_parameters(stats, NOW), (13.0, 3.5))
```

- [ ] **Step 4: 运行 analytics 测试并确认旧实现失败**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_analytics -v`

Expected: 新即时触达被报 `touch without episode lifecycle`，且新分层断言失败。

- [ ] **Step 5: 实现统一触达账本**

在 `analytics.py` 保留 legacy episode parser，并增加以下核心常量和计算边界：

```python
FEEDBACK_POINT_CAP_PER_TOUCH = 3
EFFECTIVE_FEEDBACK_CAP = 12.0


def _decayed(value: int, occurred_at: datetime, now: datetime) -> float:
    age_days = max(0.0, (now - occurred_at).total_seconds() / 86400.0)
    return value * evidence_weight(age_days)
```

重建顺序固定为：

1. 按 `(occurred_at, run_id, event_index)` 收集 touch、legacy episode、即时 scan 的原始事件。
2. legacy success 只给 episode 最后一个 touch `min(received_like_count, 3)` 分；legacy failure/open 各给最后一个 touch 一个轻负样本，episode 更早 touch 保持 neutral，避免同一 outcome 重复加权。
3. 只处理存在 `feedback_scan_completed` 且列入 `completed_photo_ids` 的 observation；`baseline_photo_ids` 中的配对只进入已知集合。
4. 非基线新配对按摄影师映射到扫描时间之前最近一次 touch；该 touch 已满 3 分时忽略超出分，不回填更早 touch。
5. 有至少 1 分的 touch 设置 `unanswered=False`；即时 0 分 touch 设置 `unanswered=True`。
6. 由账本一次性构造 `PhotographerStats` 的原始分、近 30 天分、近 30 天触达、未反馈触达、有效正负证据和最近反馈时间。

`build_feedback_scan_completed_event` 必须调用同一套内部扫描重建逻辑计算 `baseline_photo_ids`、新配对、新摄影师和新分数，禁止在 CLI 复制算法。它返回严格字段的 `Event("feedback_scan_completed", now, data)`。

- [ ] **Step 6: 替换分层与 Beta 参数**

```python
def classify_photographer(stats: PhotographerStats, now: datetime) -> str:
    if stats.feedback_points_30d >= 3:
        return "verified"
    if stats.touch_count >= 3 and stats.feedback_points_30d == 0:
        return "dormant"
    if stats.feedback_points_30d >= 1 or stats.raw_feedback_points >= 1:
        return "promising"
    return "new"


def beta_parameters(stats: PhotographerStats, now: datetime) -> Tuple[float, float]:
    return (
        1.0 + min(stats.effective_feedback_points, EFFECTIVE_FEEDBACK_CAP),
        1.0 + stats.effective_unanswered_touches,
    )
```

`dormant_retest_eligible` 依据 `last_unanswered_touch_at` 计算 7 天冷却，不再依赖 episode expiry；这样 dormant 不会在获得重测资格前掉回 new。

- [ ] **Step 7: 运行新旧聚合回归**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_analytics tests.test_cycles tests.test_cycle_migration -v`

Expected: PASS；legacy cycle/episode 测试仍能重建。

- [ ] **Step 8: 提交即时反馈账本**

```powershell
git add -- .agents/skills/500px-feedback-growth/scripts/feedback_growth/analytics.py tests/helpers.py tests/test_analytics.py
git commit -m "feat: rebuild immediate feedback scores"
```

---

### Task 3: 120/60/20 选择配额与安全回填

**Files:**
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/selector.py:11-145`
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/cli.py:106-130`
- Modify: `tests/test_selector.py:1-130`

**Interfaces:**
- Consumes: Task 2 的新 `classify_photographer`、`dormant_retest_eligible` 和 `beta_parameters`。
- Produces: `QUOTAS = {"exploit_first": 120, "new": 60, "retest": 20}`。
- Preserves: `select_run_candidates(...) -> SelectionResult` 与 preview plan 字段。

- [ ] **Step 1: 把现有完整日测试改为新配额并增加缺桶回填测试**

```python
def test_complete_day_allocates_120_60_20(self):
    exploit = [candidate(f"v{i:03}", "verified", i) for i in range(120)]
    new = [candidate(f"n{i:03}", "new", i) for i in range(60)]
    retest = [candidate(f"r{i:03}", "dormant", i, True) for i in range(20)]
    candidates = exploit + new + retest

    result = select_run_candidates(candidates, state_for(candidates), NOW, seed=8122026, limit=200)

    self.assertEqual(Counter(item["bucket"] for item in result.selected), {"exploit_first": 120, "new": 60, "retest": 20})
    self.assertEqual(len({item["photographer_id"] for item in result.selected}), 200)

def test_missing_retest_is_filled_without_expanding_dormant_pool(self):
    exploit = [candidate(f"v{i:03}", "verified", i) for i in range(140)]
    new = [candidate(f"n{i:03}", "new", i) for i in range(60)]
    result = select_run_candidates(exploit + new, state_for(exploit + new), NOW, seed=5, limit=200)
    self.assertEqual(Counter(item["bucket"] for item in result.selected), {"exploit_first": 140, "new": 60})
```

- [ ] **Step 2: 运行 selector 测试并确认配额失败**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_selector -v`

Expected: 仍得到 `112/50/38`。

- [ ] **Step 3: 更新桶顺序和回填**

```python
QUOTAS = {"exploit_first": 120, "new": 60, "retest": 20}
PRIMARY_BUCKET_ORDER = ("exploit_first", "new", "retest")


def _candidate_bucket(candidate: Candidate) -> Optional[str]:
    if candidate.tier == "dormant":
        return "retest" if candidate.is_retest else None
    if candidate.tier in {"verified", "promising"}:
        return "exploit_first"
    return "new"
```

同时从 `typing` 导入 `Optional`。首次分配与回填都使用 `PRIMARY_BUCKET_ORDER`。构建 pools 时跳过 `_candidate_bucket(candidate) is None`，不能把尚未冷却的 `dormant` 当作 new；`_candidates` 仅在 `tier == "dormant" and stats.dormant_retest_eligible` 时设置 `is_retest=True`。

- [ ] **Step 4: 运行 selector 测试**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_selector -v`

Expected: PASS；总数、去重、固定 seed 与近分页面顺序回归不变。

- [ ] **Step 5: 提交配额变更**

```powershell
git add -- .agents/skills/500px-feedback-growth/scripts/feedback_growth/selector.py .agents/skills/500px-feedback-growth/scripts/feedback_growth/cli.py tests/test_selector.py
git commit -m "feat: rebalance photographer selection quotas"
```

---

### Task 4: CLI 最新三张扫描与当日即时结算

**Files:**
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/cli.py:74-510,1250-1478,1486-1690`
- Modify: `tests/test_cli.py:168-1200`
- Test: `tests/test_automation_contract.py`

**Interfaces:**
- Consumes: `build_feedback_scan_completed_event(...)`。
- Produces command: `feedback-scan-complete --run-id --scan-id --completed-photo-id ...`。
- Produces: 新 `outgoing_like_confirmed` 自动写 `settlement_mode=immediate`，且不追加 episode lifecycle。
- Preserves: legacy `--cycle-id` 路径及旧日志解析，但默认 skill 不再调用 cycle/review 命令。

- [ ] **Step 1: 写失败的 scan complete CLI 测试**

测试先用 `event` 写入一个 `purpose=latest_three_feedback` 的 `scan_started`、三条 `work_observed` 和可见 liker，再调用：

```python
result, payload = invoke(
    root,
    "feedback-scan-complete",
    "--run-id", begun["run_id"],
    "--scan-id", "scan-3",
    "--completed-photo-id", "mine-1",
    "--completed-photo-id", "mine-2",
    "--completed-photo-id", "mine-3",
    "--now", "2026-08-19T09:10:00+08:00",
)
self.assertEqual(result.returncode, 0)
self.assertEqual(payload["photo_ids"], ["mine-1", "mine-2", "mine-3"])
self.assertEqual(payload["baseline_photo_ids"], ["mine-1", "mine-2", "mine-3"])
```

另测：少于或多于三条 `work_observed`、重复 position、completed photo 不在当前 scan、同一 scan 重复完成均返回稳定错误码且不追加事件。

- [ ] **Step 2: 写失败的新点赞无 episode 测试**

把现有点赞生命周期测试拆成 legacy 与 immediate 两条：

```python
def test_immediate_like_does_not_open_episode(self):
    action_id = deterministic_action_id("2026-08-19", "p1", "photo-1", "outgoing_like_confirmed")
    result, payload = invoke(
        root,
        "event",
        "--run-id", run_id,
        "--kind", "outgoing_like_confirmed",
        "--field", f"action_id={action_id}",
        "--field", "photographer_id=p1",
        "--field", "photo_id=photo-1",
        "--field", "photo_url=https://500px.test/photo/photo-1",
        "--field", "quota_bucket=new",
        "--field", "before_state=not_liked",
        "--field", "after_state=liked",
        "--now", "2026-08-19T09:20:00+08:00",
    )
    self.assertEqual(result.returncode, 0)
    self.assertEqual(payload["appended"], ["outgoing_like_confirmed"])
    checkpoint = read_checkpoint(root, run_id)
    self.assertEqual(checkpoint.events[-1].data["settlement_mode"], "immediate")
```

再断言新 run 可以在旧 cycle 未 settled 时不带 `--cycle-id` 正常开始，且 `begin preflight/run` 不再追加过期 failure 事件。

- [ ] **Step 3: 运行 CLI 测试并确认失败**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_cli -v`

Expected: parser 不认识 `feedback-scan-complete`，新点赞仍自动追加 `feedback_episode_opened`。

- [ ] **Step 4: 实现 scan complete 命令**

增加：

```python
def command_feedback_scan_complete(args) -> int:
    root = _state_root(args.state_root)
    now = _now(args.now)
    checkpoint = read_checkpoint(root, args.run_id)
    if checkpoint.header.mode not in {"preflight", "run"}:
        return _error("daily_transaction_required")
    if checkpoint.header.daily_task_id != _day(now):
        return _error("daily_task_expired", daily_task_id=checkpoint.header.daily_task_id)
    try:
        completed = build_feedback_scan_completed_event(
            load_effective_runs(root),
            args.run_id,
            args.scan_id,
            tuple(args.completed_photo_id),
            now,
        )
    except StateValidationError as error:
        return _error("invalid_feedback_scan", message=str(error))
    append_checkpoint_events(root, args.run_id, (completed,))
    _json({"ok": True, **completed.data})
    return 0
```

为了让 helper 看到当前 active checkpoint，`load_effective_runs(root)` 必须在调用时包含该 checkpoint；不直接读取或改写 Markdown。

- [ ] **Step 5: 切换默认点赞为 immediate**

`command_event` 在 `outgoing_like_confirmed` 上复制 `data` 并设置：

```python
data["settlement_mode"] = "legacy" if checkpoint.header.transaction_context.get("cycle_id") else "immediate"
```

只有 `settlement_mode == "legacy"` 才执行现有 open/extend episode 分支。移除默认 `begin preflight/run` 的 `_append_expired_episodes` 调用；旧 open episode 由 Task 2 的只读适配器直接解释为轻负样本。

Parser 增加 `feedback-scan-complete` 及可重复 `--completed-photo-id`，handler 映射到新 command。

- [ ] **Step 6: 运行 CLI、automation 与状态回归**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_cli tests.test_automation_contract tests.test_repository_state tests.test_workspace -v`

Expected: PASS；旧 automation 纯数据构造测试保留，证明读取兼容未被误删。

- [ ] **Step 7: 提交新运行编排**

```powershell
git add -- .agents/skills/500px-feedback-growth/scripts/feedback_growth/cli.py tests/test_cli.py
git commit -m "feat: settle new feedback runs immediately"
```

---

### Task 5: 简化 Dashboard 数据与界面

**Files:**
- Modify: `.agents/skills/500px-feedback-growth/scripts/feedback_growth/dashboard.py:1-304`
- Modify: `.agents/skills/500px-feedback-growth/assets/dashboard.html`
- Modify: `tests/test_dashboard.py:1-330`

**Interfaces:**
- Consumes: `AggregateState.feedback_scans`、`touch_feedback` 与 PhotographerStats 新字段。
- Produces view keys: `current_task`, `latest_feedback_scan`, `performance_30d`, `tier_distribution`, `relationship_ranking`, `strategy_allocation`, `history_tabs`。
- Removes from new view: `cycle`, `kpi`, `cohort_outcomes`, `latency_buckets`。

- [ ] **Step 1: 用五区块合同替换旧 Dashboard 测试**

在测试中导入 `from dataclasses import replace` 和 `FeedbackScan`。新增/改写核心断言：

```python
def test_view_model_has_immediate_feedback_sections(self):
    now = dt(19, 12)
    state = state_with_tasks(daily("2026-08-19", 150, 150, covered=200, completed_at=now))
    view = build_dashboard_view_model(state, now)
    self.assertEqual(
        set(view),
        {"generated_at", "current_task", "latest_feedback_scan", "performance_30d", "tier_distribution", "relationship_ranking", "strategy_allocation", "history_tabs"},
    )
    self.assertEqual(view["strategy_allocation"]["planned"], {"exploit_first": 120, "new": 60, "retest": 20})
    self.assertNotIn("cycle", view)
    self.assertNotIn("latency_buckets", view)

def test_incomplete_scan_is_not_rendered_as_zero_feedback(self):
    now = dt(19, 12)
    scan = FeedbackScan(
        "scan-3",
        now,
        ("mine-1", "mine-2", "mine-3"),
        frozenset({"mine-1", "mine-2"}),
        frozenset(),
        0,
        0,
        0,
        frozenset({"mine-3"}),
    )
    base = state_with_tasks()
    state = replace(base, feedback_scans=(scan,))
    view = build_dashboard_view_model(state, now)
    self.assertFalse(view["latest_feedback_scan"]["complete"])
    self.assertEqual(view["latest_feedback_scan"]["status"], "数据不完整")
```

增加每 100 触达反馈分可超过 100、原始/有效分排名、200 覆盖少于 200 点赞、legacy 100 点赞历史仍显示的测试。

- [ ] **Step 2: 运行 Dashboard 测试并确认旧 view 失败**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_dashboard -v`

Expected: 缺少 `latest_feedback_scan`、`performance_30d`、`strategy_allocation`。

- [ ] **Step 3: 重写 view model 为直接派生的五区块**

`performance_30d` 使用：

```python
touches = sum(stats.touch_count_30d for stats in state.photographers.values())
points = sum(stats.feedback_points_30d for stats in state.photographers.values())
performance_30d = {
    "covered_photographers": len({touch.photographer_id for touch in state.outgoing_touches if start <= touch.occurred_at <= now}),
    "feedback_photographers": sum(stats.feedback_points_30d > 0 for stats in state.photographers.values()),
    "feedback_points": points,
    "unanswered_touches": sum(stats.unanswered_touch_count_30d for stats in state.photographers.values()),
    "feedback_points_per_100_touches": None if touches == 0 else 100.0 * points / touches,
    "touches": touches,
}
```

排名按 `(-effective_feedback_points, -raw_feedback_points, display_name, photographer_id)`；分层固定顺序 `verified, promising, dormant, new`。最新扫描按 `(occurred_at, scan_id)` 取最后一条，并显式输出 `completed_count/3`、issues 和 `complete`。

- [ ] **Step 4: 精简离线模板**

删除 cycle/review/72h/latency/open-failure DOM 与脚本，保留：

- `id="current-task"`
- `id="latest-feedback-scan"`
- `id="performance-30d"`
- `id="relationship-tiers"`
- `id="strategy-allocation"`
- `id="theme-toggle"`、`aria-live="polite"`、`prefers-reduced-motion: reduce`
- `<html lang="zh-CN" data-theme="light">` 和 `@media (max-width: 720px)`

所有数字标签写清“分”“摄影师”“触达”；`feedback_points_per_100_touches` 标为“每 100 次触达反馈分”，不得写成率。

- [ ] **Step 5: 运行 Dashboard 测试并生成真实本地视图**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_dashboard tests.test_cli.CliTest.test_dashboard_command_writes_rebuildable_offline_html -v`

Run: `.\.agents\skills\500px-feedback-growth\scripts\feedback_growth.cmd dashboard --now 2026-08-19T12:00:00+08:00`

Expected: 测试 PASS；生成 `.local/500px-feedback-growth/dashboard.html`，不修改事实日志。

- [ ] **Step 6: 做桌面和窄窗口视觉 QA**

通过本地 HTTP 地址打开 Dashboard，分别检查桌面宽度与不超过 720px 的窄窗口：无横向溢出、五区块顺序正确、浅色默认、主题按钮可切换、空值与扫描不完整状态可读。发现渲染问题时只修改 template 和对应断言，再重跑 Step 5。

- [ ] **Step 7: 提交 Dashboard**

```powershell
git add -- .agents/skills/500px-feedback-growth/scripts/feedback_growth/dashboard.py .agents/skills/500px-feedback-growth/assets/dashboard.html tests/test_dashboard.py
git commit -m "feat: simplify immediate feedback dashboard"
```

---

### Task 6: Skill、运行文档与 ADR 同步

**Files:**
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `docs/README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/operations.md`
- Modify: `docs/decisions/README.md`
- Create: `docs/decisions/ADR-0005-immediate-feedback-settlement.md`
- Modify: `.agents/skills/500px-feedback-growth/SKILL.md`
- Modify: `.agents/skills/500px-feedback-growth/references/browser-workflow.md`
- Modify: `.agents/skills/500px-feedback-growth/references/operational-recovery.md`
- Modify: `.agents/skills/500px-feedback-growth/references/event-schema.md`
- Modify: `.agents/skills/500px-feedback-growth/references/dashboard-semantics.md`
- Modify: `tests/test_skill_contract.py:1-180`

**Interfaces:**
- Consumes: Tasks 1–5 已验证的 CLI、schema、配额与 Dashboard 字段。
- Produces: 零参数 `$500px-feedback-growth` 的新标准流程和可审计命令序列。

- [ ] **Step 1: 调用并完整读取 skill 写作约束**

在修改 skill 前必须使用 `superpowers:writing-skills` 和 `skill-creator`，完整读取两份 `SKILL.md` 及其要求的相关 references；若两者冲突，以用户已批准规格和仓库 `AGENTS.md` 为准。

- [ ] **Step 2: 把旧 cycle 合同测试替换为即时流程合同**

在 `tests/test_skill_contract.py` 固定以下文本和命令：

```python
def test_immediate_feedback_contract_scans_three_and_settles_same_day(self):
    skill = SKILL.read_text(encoding="utf-8")
    browser = BROWSER.read_text(encoding="utf-8")
    schema = EVENT_SCHEMA.read_text(encoding="utf-8")
    for text in (skill, browser):
        self.assertIn("最新 3 张", text)
        self.assertIn("200 位", text)
        self.assertIn("👍👍👍", text)
        self.assertNotIn("+20h", text)
        self.assertNotIn("+70h", text)
    self.assertIn("feedback_scan_completed", schema)
    self.assertIn("120", skill)
    self.assertIn("60", skill)
    self.assertIn("20", skill)
```

保留 portability、Windows launcher、安全暂停、事件文档覆盖测试。

- [ ] **Step 3: 运行 skill contract 并确认旧文档失败**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_skill_contract -v`

Expected: 文档仍包含冻结 5 张、两次 review 和旧配额。

- [ ] **Step 4: 更新公共 skill 工作流**

`SKILL.md` 的标准执行顺序必须明确：

```text
doctor -> status --json -> resume 或 begin preflight
-> 读取本人最新 3 张并逐张完成 liker scan
-> feedback-scan-complete
-> candidate_observed / preview / 用户批准
-> begin run -> 覆盖当日剩余摄影师
-> finish completed 或安全封存 -> dashboard
```

删除新流程中的冻结 5 张、72 小时 episode、`review_1d`、`review_3d`、automation 和“上一周期未 settled 禁止开始”。保留“候选只看第一张”“已点赞/不可读也计覆盖”“点赞后评论 `👍👍👍`”“状态不明确立即暂停”。

- [ ] **Step 5: 同步 schema、恢复、架构和 Dashboard 语义**

文档必须逐项写清：首次基线、完整扫描与 `scan_issue` 的区别；原始分/有效分；3 分 verified；3 次无反馈 dormant；12 分有效上限；`120/60/20`；旧日志只读映射；Dashboard 每 100 次触达反馈分可超过 100。

ADR-0005 使用 `Accepted`，记录：选择当日立即结算 + 下次最新三张增量扫描；拒绝保留双 review 和 mutable JSON/数据库；后果是反馈发现时间取下一次启动扫描时间，非严格因果。

- [ ] **Step 6: 运行文档与 skill 验证**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_skill_contract tests.test_package -v`

Run: `.\.venv\Scripts\python.exe C:\Users\mimi4\.codex\skills\.system\skill-creator\scripts\quick_validate.py .agents\skills\500px-feedback-growth`

Expected: 两组命令 PASS；如果 validator 在 Windows 默认编码失败，使用 `$env:PYTHONUTF8='1'` 后原命令重跑，不改变 skill 内容规避验证。

- [ ] **Step 7: 提交 skill 与文档**

```powershell
git add -- AGENTS.md README.md docs/README.md docs/architecture.md docs/operations.md docs/decisions/README.md docs/decisions/ADR-0005-immediate-feedback-settlement.md .agents/skills/500px-feedback-growth/SKILL.md .agents/skills/500px-feedback-growth/references/browser-workflow.md .agents/skills/500px-feedback-growth/references/operational-recovery.md .agents/skills/500px-feedback-growth/references/event-schema.md .agents/skills/500px-feedback-growth/references/dashboard-semantics.md tests/test_skill_contract.py
git commit -m "docs: adopt immediate feedback workflow"
```

---

### Task 7: 全量验证、旧 automation 停用与交付审计

**Files:**
- Verify only: entire repository
- Operational state: Codex automation registry
- Generated, ignored artifact: `.local/500px-feedback-growth/dashboard.html`

**Interfaces:**
- Consumes: Tasks 1–6 的完整实现。
- Produces: 通过测试、干净主分支、停用旧回顾调度、可打开的本地 Dashboard。

- [ ] **Step 1: 运行范围测试**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_store tests.test_analytics tests.test_cycles tests.test_cycle_migration tests.test_selector tests.test_cli tests.test_dashboard tests.test_workspace tests.test_repository_state tests.test_skill_contract -v
```

Expected: PASS，无真实浏览器写操作。

- [ ] **Step 2: 运行全量测试与差异检查**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
git diff --check
```

Expected: 全部 PASS，`git diff --check` 无输出。

- [ ] **Step 3: 验证 CLI 无副作用路径**

```powershell
.\.agents\skills\500px-feedback-growth\scripts\feedback_growth.cmd doctor
.\.agents\skills\500px-feedback-growth\scripts\feedback_growth.cmd status --json
.\.agents\skills\500px-feedback-growth\scripts\feedback_growth.cmd dashboard
```

Expected: doctor 和 status 返回 `ok: true`；Dashboard 在 `.local/500px-feedback-growth/dashboard.html` 重建成功。不得调用真实 `outgoing_like_confirmed` 或评论。

- [ ] **Step 4: 停用旧回顾 automation**

使用 Codex automation 管理工具列出名称匹配 `500px-review-` 的未执行 automation，只停用仍为 active 的旧 `review_1d/review_3d` 项。再次列出确认它们不再 active；不删除历史 automation，不写入或修改 sealed logs。

- [ ] **Step 5: Dashboard 最终视觉 QA**

通过 `http://127.0.0.1:8765/.local/500px-feedback-growth/dashboard.html` 打开生成页面，复核桌面和不超过 720px 窄窗口、主题切换、五区块、扫描不完整提示、单位与中文标签。只读检查，不触发 500px 页面互动。

- [ ] **Step 6: 审计 Git 交付边界**

```powershell
git status --short --branch
git worktree list
git log --oneline --decorate -8
git diff 96d9f35..HEAD --stat
```

Expected: 当前交付分支只包含本计划相关提交；没有被跟踪的 checkpoint、Dashboard、Cookie、token 或浏览器认证文件。

- [ ] **Step 7: 使用完成前验证与代码评审技能**

调用 `superpowers:verification-before-completion`，然后调用 `superpowers:requesting-code-review` 检查规格覆盖、legacy 兼容、事件去重、200 人上限与文档一致性。修复发现的问题时为每个问题先补失败测试，再最小修复并重跑 Step 1–2。

- [ ] **Step 8: 完成交付选择**

调用 `superpowers:finishing-a-development-branch`，向用户报告实际提交、测试数量、Dashboard 路径、旧 automation 停用结果和任何未完成的真实页面验证；未经用户明确要求不推送远端。
