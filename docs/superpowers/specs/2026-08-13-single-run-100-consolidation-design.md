# 单次 100 赞与启动简化 Consolidation 设计

- 状态：Approved
- 日期：2026-08-13
- 替代范围：原设计中所有“每次 25 个、同日调用四次”和公开 `run --approve <preview_id>` 规则

## 1. 目标

把 `500px-feedback-growth` 的公开体验收敛为一个零参数入口：

```text
$500px-feedback-growth
```

一次显式调用代表授权系统在当前 Asia/Shanghai 自然日内持续执行，直到当日累计完成 100 个页面确认的成功点赞。执行过程不再要求用户显式发起四个 25 赞批次，也不向用户暴露内部 `preview_id`、`run_id` 或 CLI 生命周期。

单次 100 是目标，不覆盖安全边界。出现 CAPTCHA、限频、登录失效、平台警告、账号不匹配、互动状态不明确或候选耗尽时可以提前结束并保留恢复点。

## 2. 公开交互合同

### 默认入口

`$500px-feedback-growth` 默认解释为“完成今天剩余点赞任务”：

1. 读取主工作区状态。
2. 若今日已完成 100 个，报告完成状态，不产生新互动。
3. 若存在 recoverable run，恢复同一个 run，从最后确认动作继续。
4. 若尚未完成首次批准，自动执行只读 preflight，展示候选与配额摘要，并只询问“确认执行？”。
5. 用户回复“确认执行”后，系统在本地解析最新、有效且未变化的 preview，继续同一个 100 赞目标；不要求用户复制 ID。
6. 已完成首次批准后，后续零参数调用直接执行当天剩余额度，不再预览或逐批确认。

### 辅助入口

保留三个面向用户的只读/维护意图：

```text
$500px-feedback-growth status
$500px-feedback-growth preflight
$500px-feedback-growth dashboard
```

内部 CLI 仍可包含 `begin`、`resume`、`event`、`preview`、`approve`、`finish` 等命令，但它们属于 skill 实现细节，不作为用户启动语法展示。

## 3. 单次运行生命周期

一次公开启动对应一个连续的 run 生命周期，而不是四个 25 赞 run：

- 新日从 0 开始，目标 100。
- 当日已有部分确认点赞时，目标是完成 `100 - confirmed_likes`。
- 每个点赞仍独立进行动作前读取、点击、动作后确认和事件追加。
- checkpoint 仍按每个确认动作立即持久化，因此“单次 100”不降低崩溃恢复能力。
- 正常完成时只在到达当日累计 100 后封存为 `completed`。
- 安全停止封存为 `paused_incomplete`；候选耗尽封存为 `incomplete_candidate_exhausted`。
- 人工或工具中断但页面状态可继续时保留 active checkpoint，下一次零参数启动恢复，而不是新建 run。

历史日志中四个 25 赞 run 保持不可变。新代码必须同时能重建旧日志与新的单 run 100 日志，不迁移、不改写历史事实。

## 4. 候选与审批

Preflight 的候选计划从最多 25 位改为最多 100 个当日合格动作，并按现有日配额合同选择：

- 45 个 `verified/promising` 首次点赞；
- 20 个复测；
- 15 个新人探索；
- 最多 20 个 `verified` 第二赞。

完成日仍要求至少覆盖 80 位不同摄影师，单人每天最多 2 幅，第二幅仅限 `verified`。候选不足时优先扩大不同摄影师覆盖，不通过第三幅点赞或放宽层级补数。

首次批准继续使用 preview digest、24 小时有效期和 quota snapshot 防止批准内容漂移，但 ID 对用户隐藏。Skill 通过只读内部命令取得最新 preview ID，再执行快速复核与批准：

- 同一自然日；
- preview 未过期；
- preview 后没有确认互动；
- 已批准候选的稳定字段和配额未变化。

不满足条件时自动生成新 preflight，并再次以自然语言询问确认；不得让用户处理 `preview_changed`、`preview_expired` 或内部 ID。

## 5. 代码变更

### Selector

- 删除 `BATCH_TARGET = 25`。
- 候选选择默认上限改为当日剩余额度，最大 100。
- 将带有旧批次含义的 `select_batch` 重命名为表达单次运行候选选择的函数。
- 保留可注入 `limit`、固定 seed 和固定时钟，支持局部测试和候选不足场景。

### CLI

- `preview` 使用当前当日剩余额度生成候选计划，而不是固定 25。
- 增加只读“获取最新可批准 preview”的内部能力，使 skill 不依赖线程中保存 ID。
- `begin --mode run` 继续拒绝第 101 个动作并优先返回 recoverable run。
- action ID、episode、append-only store 和 schema version 保持兼容；本次不修改历史事件结构。

### Skill

- 默认路由改为零参数执行到 100。
- 删除面向用户的 `run --approve <preview_id>` 和“每次最多 25”文案。
- `status`、`preflight`、`dashboard` 作为可选高级意图保留。
- 浏览器工作流删除 25 人假设，按完整候选计划和当天剩余额度运行。

## 6. 文档 Consolidation

### 长期规则

`AGENTS.md` 只保留以下新合同：

- 一次显式启动授权完成当日剩余额度，目标累计 100。
- 不拆成公开的 25 赞批次。
- 恢复继续同一 active run。
- 每动作立即 checkpoint；单次运行并不等于批量回填。
- 用户只需“确认执行”，内部 ID 不进入公开操作约定。

### 运行经验

`docs/operations.md` 和 skill references 保留真正可复用的经验：

- 异步空列表只刷新读取一次；
- 评论候选先于点赞弹层读取；
- 新鲜 preview 只复核批准候选，不重复完整 preflight；
- 绝对 `state-root` 防止 worktree 状态漂移；
- 每次写事件前校验当前 `run_id`、`scan_id`，写后检查结果；
- 状态不明确立即暂停，不重复点击。

首次真实运行“历史上曾用四个 25 赞 run 完成 100”只作为迁移背景保留一句，不再作为推荐流程或操作示例。

### 历史文档

- 更新原始设计 spec 中仍代表当前产品合同的章节，使其明确单次 100 与零参数入口。
- 原始实施 plan 属于历史执行记录，在顶部标记 `Superseded` 并链接本设计；不机械重写其逐步历史，以免伪造当时实施过程。
- README、架构、运行手册、skill 和测试合同统一使用“单次运行”“当日任务”，删除“本批 25”“四批累计”等当前态表述。

## 7. 测试与验收

必须新增或调整测试以证明：

1. Preflight 在候选充足时生成 100 个候选动作，而不是 25 个。
2. 当日已有 37 个确认点赞时，下一次 preview/运行只计划剩余 63 个。
3. 一个 run 可连续记录 100 个确认点赞并完成；第 101 个仍被拒绝。
4. 中断后零参数路由恢复同一 run，不创建第二个 run。
5. 最新 preview 可以由内部命令发现，过期或旧 preview 不会被公开执行。
6. 旧的四个 25 赞 sealed logs 仍能重建为当日 100。
7. Skill contract 不再包含公开 `run --approve <preview_id>` 或“每次 25”规则。
8. Dashboard 仍只在当日恰好完成 100 后生成历史 Tab。
9. 全量测试、`git diff --check`、Markdown 本地链接检查和 skill 结构验证通过。

## 8. 不变范围与知识缺口

本次不改变 Thompson Sampling、72 小时归因、30 天证据衰减、摄影师分层、固定评论文本或 Dashboard 视觉设计。

仍需通过未来真实运行补足：

- 单次连续 100 次互动是否更容易触发平台限频；安全停机规则保持优先，不能以完成目标为由规避。
- 一次 preflight 是否始终能提供足够 100 个有效候选；不足时需要在运行中扩展候选链，但不能降低资格约束。
- 长时间连续运行中 Chrome 连接和页面异步状态的稳定性；恢复必须依赖 checkpoint，而非假设浏览器会话持续存在。
- 当前尚无成熟的 72 小时回馈 cohort，算法效果仍处于数据积累期。
