# 当日结算与最新三张反馈模型设计

> 状态：Implemented。当前最新 3 张、即时结算和积分合同以 ADR-0005、ADR-0006、代码和测试为准。

## 背景

当前系统把一次点赞任务绑定到冻结 5 张本人作品、逐张 baseline、72 小时 episode、`+20h`/`+70h` 两次回顾和最终成熟结算。近期两轮真实运行表明，已观察到的归因回馈都在第一次回顾时出现，第二次回顾没有增加成功摄影师，却显著增加了浏览器读取、事件数量和恢复成本。

本设计保留“优先奖励常反馈摄影师、持续拓展新群体、逐渐降低不反馈摄影师优先级”的方向，改用当日立即结算、下次启动顺带刷新反馈的轻量模型。新流程不再等待未来回顾完成，旧日志保持不可变并继续可读。

## 目标

- 每次任务覆盖恰好 200 位不同摄影师后立即完成并结算。
- 每次启动只扫描本人主页当前最新 3 张公开作品，吸收上次任务之后出现的新反馈。
- 一位摄影师在同一轮新点赞 1、2、3 张本人作品时，分别贡献 1、2、3 个反馈分。
- 用简单、可重建的反馈加减分模型驱动摄影师分层与候选排序。
- 继续使用 append-only Markdown 作为唯一事实源，不引入数据库或可变摘要文件。
- 简化 Dashboard，删除 cycle、回顾和 72 小时成熟相关视图。
- 保持现有页面互动、安全暂停、checkpoint 恢复、200 位覆盖和逐赞评论业务合同不变。

## 非目标

- 不修改历史 sealed run、checkpoint 或 cycle/review 事件。
- 不补做历史评论，不重新解释页面证据，不宣称严格因果关系。
- 不增加生产依赖，不引入后台轮询或新的定时任务。
- 不改变候选摄影师只检查主页第一张作品、成功点赞后评论 `👍👍👍` 的页面业务逻辑。

## 新运行流程

新运行按以下顺序执行：

```text
doctor
  -> status / recover
  -> 扫描本人最新 3 张公开作品
  -> 重建反馈分与摄影师分层
  -> preflight / 确认候选
  -> 覆盖 200 位摄影师
  -> 当日立即封存结算
  -> 重建 Dashboard
```

### 启动扫描

1. 读取并确认当前账号本人主页。
2. 取页面当前公开展示位置 1、2、3 的作品；必须以稳定作品 ID 和 URL 记录。
3. 分别读取三张作品的完整可见点赞者列表。
4. 普通加载失败时只刷新读取一次；仍失败则记录 `scan_issue`。读取失败的作品不产生零反馈结论，也不阻止本轮互动。
5. 扫描完成后，从事件历史识别新出现的 `(photo_id, photographer_id)` 对，并据此重建分数。

当一张作品首次进入最新三张范围时，其第一次完整扫描只建立点赞者基线：当时已经存在的点赞不计为本方案的新反馈。只有后续完整扫描首次观察到的新配对才产生反馈分。这样可以避免新上传作品或展示顺序变化时把旧点赞错误归因给近期触达。

### 互动与完成

- 新运行不创建 cycle、冻结 5 张、baseline cycle、feedback episode、review 或 automation。
- 每位摄影师每天只覆盖一次，只检查主页当前第一张作品。
- 第一张未点赞时，页面确认 `not_liked -> liked` 后记录点赞，并在同一作品评论 `👍👍👍`。
- 第一张已点赞或不可读时记录跳过，并计入当日覆盖。
- 点赞和评论分别确认、分别记录；评论失败不撤销已经确认的点赞。
- 覆盖达到 200 位不同摄影师后立即写入 `run_finished` 并封存，任务状态为已结算。
- 候选耗尽、安全暂停或日界线到达时允许不足 200 位封存为未完成，额度不结转。

## 反馈证据与计分

### 原始证据

每个 `outgoing_like_confirmed` 是一次触达。触达在没有后续新反馈证据时贡献一个轻负样本，不需要额外写入失败事件。

后续启动扫描首次发现该摄影师新增点赞本人最新三张中的作品时，把不同作品的新增配对只映射到扫描时点之前该摄影师最近一次确认触达：

- 新增 1 个不同作品配对：该触达获得 1 个反馈分；
- 新增 2 个不同作品配对：该触达获得 2 个反馈分；
- 新增 3 个不同作品配对：该触达获得 3 个反馈分；
- 同一 `(photo_id, photographer_id)` 后续重复观察不重复计分；
- 同一触达累计最多 3 分；
- 最近一次触达已达到 3 分时，超出部分不计分，不向更早触达回填；
- 触达一旦获得至少 1 分，其轻负样本被纠正，不再同时计正、负证据。

`candidate_skipped` 不是新触达，不产生轻负样本，但必须保留批准计划中的 `quota_bucket`，用于重建 `120/60/20` 实际覆盖。评论成功与否也不改变反馈分。

### 原始分与有效分

原始反馈分不设上限，始终能从不可变事件中重建，用于审计、累计统计和 Dashboard 展示。

候选排序使用按 30 天半衰期衰减的证据：

```text
decay(age_days) = 2 ^ (-age_days / 30)
effective_feedback_points = min(sum(raw_point * decay), 12)
effective_unanswered_touches = sum(unanswered_touch * decay)
alpha = 1 + effective_feedback_points
beta = 1 + effective_unanswered_touches
```

有效反馈分封顶 12，相当于四轮均获得 3 分的强证据。原始分仍继续累计；有效分随时间衰减到 12 以下后，新反馈可以再次提升排序证据。现有确定性随机种子和 Thompson Sampling 继续使用 `alpha`、`beta`，避免额外引入评分框架。

### 摄影师分层

分层使用最近 30 个自然日的未衰减原始事实，排序再使用上述衰减证据：

- `verified`：最近 30 天至少 3 个反馈分；单轮对 3 张作品的新点赞可以直接进入。
- `promising`：最近 30 天有 1–2 个反馈分；或历史上至少有 1 个反馈分、近期未达到 `dormant` 条件。
- `dormant`：历史累计至少 3 次确认触达且最近 30 天 0 个反馈分；最后一次未反馈触达满 7 天后才允许进入 `retest`。
- `new`：其余摄影师，包括从未观察到反馈且近期触达不足 3 次者。

判断顺序固定为 `verified -> dormant -> promising -> new`，避免历史正反馈掩盖近期连续不反馈。

## 选择配额

每个完整 200 人任务使用：

| 桶 | 人数 | 来源 |
|---|---:|---|
| `exploit_first` | 120 | `verified` 与 `promising` |
| `new` | 60 | `new` |
| `retest` | 20 | 已冷却、允许重测的 `dormant` |

桶内继续按模型分数与确定性种子排序。同日已覆盖者必须去重；每位摄影师最多出现一次，不再生成 `verified_second`。

当某桶不足时，按 `exploit_first -> new -> retest` 的候选安全优先级从其他非重复池确定性回填。只有显式进入重测桶的 `dormant` 才能回填，不因候选不足无条件扩大沉默摄影师数量。总计划和实际覆盖都不得超过 200。

## 事件与 Markdown 状态

### 事实源不变

- `.local/500px-feedback-growth/checkpoints/<run_id>.md`：当前机器 append-only 恢复证据，不进入 Git。
- `.local/500px-feedback-growth/runs/<run_id>.md`：sealed source of truth，进入私有 Git。
- Dashboard、分层、分数和排名：全部是派生物，不能反向修改事件。

不新增 mutable JSON、SQLite、缓存型摄影师状态表或人工维护的汇总 Markdown。

### 扫描事件

复用现有事件：

- `scan_started`：新扫描增加 `purpose=latest_three_feedback`；旧事件缺少该字段时按 legacy scan 读取。
- `work_observed`：记录当前最新三张的 `position=1..3`。
- `received_like_observed`：记录完整读取到的点赞者配对；重复观察允许存在，但计分按业务主键去重。
- `scan_issue`：记录一次刷新后仍失败的单张作品。

新增一个汇总事件：

| kind | 必填字段 |
|---|---|
| `feedback_scan_completed` | `scan_id`, `photo_ids`, `completed_photo_ids`, `baseline_photo_ids`, `new_pair_count`, `new_feedback_photographer_count`, `new_feedback_points`, `completed_at` |

约束如下：

- `photo_ids` 按主页位置顺序保存，正常情况下恰好 3 个。
- `completed_photo_ids` 只包含已完整读取点赞者列表的作品。
- `baseline_photo_ids` 只包含本次首次完成基线的作品。
- `new_pair_count` 与用于计分的去重配对一致。
- 扫描不完整也写汇总事件，但缺失作品不作为零反馈或基线完成。
- 聚合仍以原始 observation 为事实；汇总字段用于完整性审计，必须与重建结果一致。

事件按 `occurred_at` 和业务主键确定性排序。对同一配对，以首次完整、非基线 observation 作为新反馈时点。

## 历史兼容

所有现有 sealed logs 保持原样，解析层同时支持 legacy 与 immediate 两种运行模式：

- 旧 `feedback_episode_succeeded` 按 `received_like_count` 为对应触达贡献 `min(received_like_count, 3)` 个原始反馈分。
- 旧 `feedback_episode_failed` 为对应触达贡献一个轻负样本。
- 旧 open episode 在新模型中按当前指令对齐为一个尚无反馈的轻负样本，不再等待 expiry。
- 同一旧 episode 只从最终 outcome 映射一次，不与底层 observation 重复计分。
- 旧 cycle、baseline、review 和 automation 事件继续可展示在历史兼容解析中，但不再阻止新运行，也不进入新 Dashboard 主指标。
- 旧运行的完成状态、覆盖数、点赞数和评论数不反向改判。

启用新流程时停用尚未执行的旧回顾 automation；不补跑、不删除其历史事件，也不修改已经 sealed 的 review 日志。

## Dashboard

Dashboard 默认显示新模型，保留历史运行的基础可读性，不再展示需要未来结算的主流程。

### 本次任务

- 覆盖摄影师 `/ 200`
- 确认点赞数
- 确认评论数
- 跳过数与主要原因
- 当前状态：执行中、已结算、安全暂停或未完成

### 最新三张反馈扫描

- 扫描完成度，例如 `3 / 3`
- 新反馈摄影师数
- 新增原始反馈分
- 失败作品和 `scan_issue`

不完整扫描显示“数据不完整”，不得显示为零反馈。

### 近 30 天互动效果

- 覆盖摄影师数
- 有反馈摄影师数
- 原始反馈分
- 未反馈触达数
- 每 100 次触达获得的反馈分

“每 100 次触达反馈分”允许超过 100，因为单个摄影师一次最多贡献 3 分；Dashboard 必须明确单位和分母，不能标为摄影师回馈率。

### 关系分层与策略

- `verified / promising / dormant / new` 数量
- 高价值摄影师排名：原始反馈分、有效反馈分、最近反馈时间
- 计划配额 `120 / 60 / 20`
- 实际执行数量、缺口与回填来源

### 删除的旧视图

- cycle、冻结 5 张和 baseline 卡片
- `+1d / +3d` review 状态
- 72 小时成熟、open episode 和 settlement 卡片
- 自动回顾任务、延迟归因图表及依赖成熟窗口的 KPI

## 失败、安全与恢复

- 最新三张的普通读取失败只刷新一次；仍失败记录 `scan_issue` 并继续，不把缺失证据当成零。
- 登录失效、账号不匹配、CAPTCHA、限频、平台警告、点赞或评论状态不明确时，立即记录 `safety_paused` 并停止。
- 点赞成功但评论失败时保留点赞事实，单独记录暂停或失败证据，不伪造评论成功。
- Active run 继续使用 checkpoint 从最后一个确认事件恢复，不重复互动。
- Active run 只能在其 `daily_task_id` 当日恢复；跨 Asia/Shanghai 日界线后封存为未完成。
- Sealed run 仍不可恢复或追加；同一 `run_id` 同时存在 sealed log 与 checkpoint 时继续以 sealed log 为准。

## 实施边界

预计按现有模块职责做最小改造：

- `model.py`：新扫描汇总和即时证据模型。
- `analytics.py`：反馈分、轻负样本、衰减、分层和历史适配。
- `selector.py`：`120 / 60 / 20` 配额与回填。
- `cycles.py`：只保留 legacy 重建，不再参与新运行 gate。
- `cli.py`：最新三张启动扫描、立即结算和取消新 cycle/review 编排。
- `dashboard.py`：新五区块和旧日志基础兼容。
- `store.py`：继续负责 append-only 写入和 schema 校验。

实施时同步更新 `AGENTS.md`、`README.md`、`docs/architecture.md`、`docs/operations.md`、事件 schema、Dashboard 语义、浏览器工作流、恢复手册、skill 主文档以及必要 ADR。旧规范保留为历史决策记录，不覆盖或删除。

## 验收测试

### 计分与分层

- 新增 1、2、3 个不同作品配对分别产生 1、2、3 分。
- 同一配对重复扫描不重复计分。
- 新作品首次完整扫描只建基线，不产生分数。
- 不完整扫描不建立错误基线，也不产生零反馈。
- 触达从轻负样本变为有反馈后不同时保留正负。
- 单轮 3 分进入 `verified`。
- 历史累计至少 3 次触达且最近 30 天 0 分进入 `dormant`；最后一次未反馈触达满 7 天才允许重测。
- 原始分不封顶；有效分按 30 天半衰期衰减并封顶 12。

### 选择与运行

- 完整计划严格产生 120 个 exploit、60 个 new、20 个 retest。
- 桶不足时确定性回填，无重复摄影师且总数不超过 200。
- 点赞与跳过共同累计覆盖；恰好 200 才标记当日完成。
- 新运行不创建 cycle、episode、review 或 automation。
- 评论保持逐赞确认，`👍👍👍` 文本和安全暂停语义不变。
- 中断恢复不重复已确认动作，跨日不继续旧任务。

### 兼容与视图

- 旧 success、failure、open episode 映射正确且不重复计分。
- 旧 sealed logs 全部可解析，历史完成状态不改变。
- Dashboard 不再依赖旧 cycle/review 结算，五个新区块均可由事件重建。
- Dashboard 的分母、单位、扫描不完整状态明确。
- 完成桌面和窄窗口视觉 QA。
- skill contract 明确“本人最新 3 张反馈扫描、候选第一张、200 位覆盖、逐赞评论、即时结算”。

至少运行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
git diff --check
```

并按范围单独运行 CLI、store、analytics、selector、Dashboard、repository state 和 skill contract 测试。

## 上线顺序

1. 先用测试固定新旧日志的计分、分层和兼容语义。
2. 实现事件解析与分析模型，再切换 selector 配额。
3. 切换 CLI 新运行编排，保证 legacy 只读兼容。
4. 简化 Dashboard 与文档、skill 合同。
5. 停用尚未执行的旧回顾 automation。
6. 用临时状态完成无副作用测试和 Dashboard 视觉 QA。
7. 下一次真实任务先执行 `doctor`、`status --json` 和无副作用 preflight；页面互动仍遵守既有批准与安全边界。
