# 公开作品回馈周期与临时回顾任务设计

- 状态：Draft for user review
- 日期：2026-08-14
- 范围：把一次点赞、1 日回顾和 3 日回顾串成可追踪周期；不接管上传与分享
- 审查：已完成三轮独立审查；第三轮剩余两项 P1 已按 scoped 原始 observation 重算和显式 attempt 修订，等待用户书面确认

## 1. 目标

用户手动完成一批作品的上传与分享后，通过一次点赞任务建立回馈观察周期。系统固定当时主页公开展示的 5 张作品，只根据这 5 张收到的新点赞判断本周期回馈，并为该周期临时创建两个一次性回顾任务：

- 点赞完成后约 20 小时执行 1 日回顾；
- 点赞完成后约 70 小时执行 3 日回顾。

两个回顾都只读访问 500px、追加观察事件并重建 Dashboard，不点赞、不评论、不关注、不修改主页展示。上传与分享继续由用户手动、独立完成。

## 2. 周期边界

一个 `feedback_cycle` 包含：

1. 用户手动上传作品；
2. 用户手动选出并在主页公开展示 5 张；
3. 用户显式启动点赞任务；
4. 系统在点赞开始前读取并冻结 5 个 `showcase_photo_id`；
5. 点赞任务产生触达 episode；
6. 临时任务执行 1 日回顾；
7. 临时任务执行 3 日回顾；
8. 所有 episode 真正到期后，聚合层完成成熟结算。

周期 ID、5 张作品 ID、点赞起止时间和回顾状态必须写入 append-only 事件日志。主页后来更换展示作品，不改变已启动周期的 5 张统计范围；回顾时直接访问冻结作品，而不是重新读取当前主页的 5 张。

同一时间只允许一个未结算周期。3 日回顾完成后，旧周期可能仍有约 2 小时 episode 到期尾差；新作品可以继续手动上传和展示，但下一轮点赞必须等旧周期全部 episode 到期并进入 `settled`。这样避免同一摄影师的新触达延长旧 episode，首版不支持两个点赞周期重叠。

周期主状态固定为：

```text
preparing → baseline_ready → liking → reviews_scheduled → settled
```

回顾不使用线性主状态，而是两个正交槽位：

- `review_1d = pending | completed | failed | superseded`
- `review_3d = pending | completed | failed`

两个槽位都 resolved，且全部 episode 到期后，cycle 才派生为 `settled`。因此 3 日任务先完成、迟到的 1 日任务再进入 `superseded` 也是合法路径。

- 5 张作品或 baseline 无法完整读取时，停在 `preparing`，不得点赞或调度任务。
- 点赞运行安全暂停且可恢复时保持 `liking`，不得提前调度。
- 回顾失败时保持原状态，由用户选择一次 `retry` 或 `abandon`；不得自动递归创建任务。
- `review_3d=completed` 不等于 `settled`；所有 episode 到期后，下一次状态读取或 Dashboard 重建才确定性进入 `settled`。
- `abandoned` 是不参与算法更新的终态，会释放下一周期；任一时刻只能有一个既非 `settled` 也非 `abandoned` 的周期。

## 3. 时间语义

### 调度基准

以本轮点赞 run 的最后一个 `outgoing_like_confirmed` 时间作为 `like_completed_at`：

- `review_1d_due_at = like_completed_at + 20h`
- `review_3d_due_at = like_completed_at + 70h`

使用 Asia/Shanghai 展示时间，日志继续保存带时区的 ISO 8601 时间。

### 观察与成熟分离

`+70h` 是最后一次页面观察，不是 72 小时成熟时刻。3 日回顾不得把仍未到期的 episode 提前写成 failure。

3 日回顾完成后：

- 已观察到回馈的 episode 保持 success；
- 未观察到回馈且尚未到期的 episode 保持 open；
- episode 到达自身 `expires_at` 后，由确定性聚合按“已完成最终观察且无成功证据”惰性计算为成熟 failure，不再创建第三个浏览器任务。

这样既保留两个临时任务的用户模型，也避免把 70 小时错误报告为完整 72 小时。Dashboard 必须区分“3 日回顾未观察到”和“72 小时成熟未回馈”。由于没有第三个调度器，磁盘上的静态 Dashboard 不承诺在 `expires_at` 当刻自行重写；下一次打开状态、重建 Dashboard 或开始新周期时才反映成熟结果。

## 4. 回馈统计范围

本周期只把冻结 5 张作品上的新点赞作为回馈证据：

- 其他 25 张新上传但未公开展示的作品不参与本周期统计；
- 更早作品和下一周期作品收到的点赞不参与本周期统计；
- 同一摄影师在 5 张作品上回赞多幅，独立回馈人数仍只计 1；
- 额外记录并展示 `received_like_count`，作为回赞深度；首版不把它用于 selector 排序；
- 页面没有精确点赞时间时，继续使用首次观察时间，并称为“归因回馈”，不声称严格因果。

1 日回顾与 3 日回顾必须使用同一份冻结名单和同一组基线点赞者。周期进入点赞前必须完成以下原子前置条件：

1. 5 个作品 ID 唯一，均属于本人且当前公开；
2. 5 张作品的完整 baseline liker 集合均成功读取并追加日志；
3. 写入 `cycle_baseline_completed` 和基于规范数据生成的 baseline digest；
4. 任一作品读取失败或状态不明确时，不开始点赞、不创建回顾任务。

周期开始前已经给这 5 张作品点过赞的摄影师属于 baseline，不能被后续回顾误判为新回馈。

## 5. 两个临时自动化任务

每个周期创建两个独立、一次性的 Codex 定时任务，不使用长期轮询或周期性待办检查器。

任务 payload 固定包含：

- `cycle_id`
- `review_kind`：`review_1d` 或 `review_3d`
- `attempt`：从 1 开始；只有用户明确授权 retry 才递增
- `due_at`
- 主工作区绝对 `state_root`
- 只读执行边界

作品 ID、摄影师 ID 和账号互动明细仍从本地日志读取，不复制到 automation 配置中。

每个 review 的逻辑完成键是 `(cycle_id, review_kind)`；每次实际调度的幂等键是 `(cycle_id, review_kind, attempt)`，并在 `review_scheduled` 中记录外部 automation ID。最少事件为：

- `cycle_started`
- `cycle_showcase_frozen`
- `cycle_baseline_completed`
- `cycle_like_completed`
- `review_schedule_requested`
- `review_scheduled`
- `review_started`
- `review_photo_observed`
- `review_completed`
- `review_failed`
- `review_superseded`
- `cycle_abandoned`
- `cycle_attribution_scope_mapped`（仅历史迁移）

现有 v1 点赞和 episode 事件保持不变；新 cycle 事件通过 action ID、episode ID、scan ID 和旧 run ID 映射已有事实，不给旧事件强加新字段。Parser 在 schema v1 中增加独立、精确字段的 cycle 事件类型，同时继续接受无 cycle 的历史日志；不修改既有事件的 required fields。

创建任务前先写 `review_schedule_requested`，任务名称使用可重建的确定性键 `500px-review-<cycle_id>-<review_kind>-<attempt>`。恢复时先检查本地完成/绑定事件，再按确定性名称查找已存在任务：只有其 `cycle_id`、`review_kind`、`attempt`、`due_at` 和 `state_root` 与 intent 完全一致时，才补写 `review_scheduled` 和 automation ID；同名但 payload 不一致必须暂停并报告。未找到才创建。这样覆盖“任务已创建但绑定事件尚未落盘”的崩溃点，不依赖重复创建。

任务运行顺序：

1. 校验 cycle 存在、review 尚未成功、冻结作品恰好为 5 张；
2. 校验 Chrome 已登录正确账号；
3. 逐一读取 5 张冻结作品当前点赞者；
4. 与周期 baseline 和先前观察对比，追加新观察与 episode success 事件；
5. 追加对应 review 完成事件；
6. 重建 Dashboard；
7. 将本任务标记完成，不再重复执行。

重复投递已完成的 `(cycle_id, review_kind)` 时直接返回成功 no-op。任务在部分作品后中断时，只有用户明确授权 retry 才创建 `attempt + 1` 的 replacement automation；新 attempt 只补齐缺失的 `review_photo_observed`，不得重复扫描已确认作品或重复记 success。若 `review_3d` 已完成，迟到的 `review_1d` 写入 `review_superseded`，不再访问浏览器。

实际 `started_at`、`completed_at` 与计划 `due_at` 分开记录。1 日任务最晚允许在 3 日任务开始前执行；3 日任务即使因设备休眠迟到，也必须记录实际延迟，且不得把首次观察时间伪造成计划时间。迟到导致无法满足归因窗口时，保留扫描事实但不强行记 success。

验证码、登录失效、平台警告、页面不可判定或浏览器不可用时，任务不得伪造完成。它写入失败/暂停证据并通知用户；是否在剩余窗口内重新创建替代任务由用户明确授权，不自动产生无限重试。

点赞阶段只有在至少产生 1 个 confirmed like 且进入不可恢复 terminal 状态时，才写 `cycle_like_completed` 并创建两个任务：正常达到 100 使用 `completed`，候选耗尽使用 `incomplete_candidate_exhausted`。可恢复中断和安全暂停不得提前调度；0-like 周期直接进入 `abandoned`。跨日仍可恢复的旧点赞 run 先按现有规则封存，是否将其已确认触达建立为不完整周期必须由用户明确选择。

## 6. Dashboard

Dashboard 新增当前/最近周期摘要：

- 周期点赞完成时间；
- 冻结展示作品 `5 / 5`；
- 1 日回顾状态、完成时间和累计独立回馈者；
- 3 日回顾状态、完成时间和累计独立回馈者；
- episode 的 success / open / mature failure；
- 5 张展示作品分别收到的新增回赞数。

默认回顾基线从“最近执行日”扩展为“最近产生确认点赞的 cycle”，但历史无 cycle 的旧日志继续按执行日重建。趋势仍按触达周期归属，不按自动任务运行日归属。

成熟结果的优先级固定为：已有显式 success/failure 事件优先；只有仍为 open、已经完成 `review_3d` 且 `expires_at <= now` 的 episode 才派生 failure。派生结果不再写第二个 failure 事件，因此不会与旧显式 failure 重复计数。`settled` 完全由事件和当前时间派生；状态读取与 Dashboard 重建保持只读，不要求追加 `cycle_settled`。

## 7. 当前周期迁移

当前周期已经通过四个历史 sealed run 完成点赞，并完成人工 1 日回顾，因此只创建一个 3 日回顾临时任务：

1. 从全部相关 sealed run 确定 `like_completed_at = max(mapped outgoing_like_confirmed.occurred_at)`，并无歧义映射旧 run、scan、action 和 episode；
2. 在创建任务前只读确认当前主页 5 张仍是本轮展示作品，并查找覆盖这 5 张的触达前完整 baseline；用户确认作品链接只能确认范围，不能替代 baseline；
3. 如果 baseline 完整，把今天约 19.2 小时后的已有人工扫描作为一次性迁移例外映射为 `review_1d=completed`，不得重复扫描或重复记 success；
4. 迁移不复用旧 `feedback_episode_succeeded` 的判定结果，而是从 mapped `received_like_observed` 原始事件重算：只接受冻结 5 张，排除 baseline pair，校验 observation 位于 mapped episode 窗口，按摄影师去重，并只用 scoped pair 重算 `received_like_count`；
5. 写入 `cycle_attribution_scope_mapped`，无歧义列出本周期纳入的 5 张作品、action、episode、旧 run、旧 scan，以及实际纳入的 observation event ID/pair；旧 success 事件只保留审计用途，不直接进入迁移 cycle 的 tier/KPI；
6. 如果 baseline 不完整，走迁移专用 `migrated_observational` 路径并设置 `attribution_eligible=false`。它不经过 `baseline_ready/liking`，但可以把已有扫描映射为 `review_1d=completed` 并在用户再次确认后执行只读 3 日观察；整个 cycle 只用于历史展示，不参与 tier、KPI 或 selector；
7. 仅创建 `like_completed_at + 70h` 的 `review_3d` 一次性任务；如果目标时间已经过去，则立即报告并请求新的执行选择，不补造历史定时任务。

迁移不得改写已有日志；所有映射和补充状态通过新 append-only 事件完成。

## 8. 测试与验收

必须证明：

1. 点赞开始时冻结且只能冻结 5 张作品；主页后来变化不改变旧 cycle。
2. 只有冻结 5 张的新点赞能触发本周期 success。
3. 旧 baseline 点赞者不会被误记为回馈。
4. `+20h` 与 `+70h` 分别只生成一个 review；重复运行保持幂等。
5. `+70h` 回顾不会提前制造成熟 failure。
6. episode 到期后无需第三次浏览器扫描即可确定性成熟。
7. 两个周期的作品、摄影师和回馈不会串账。
8. 旧的无 cycle 日志仍能重建当前 Dashboard。
9. 当前迁移周期只创建 3 日任务，不重复 1 日回顾。
10. 自动任务失败时保留证据、更新 Dashboard 并通知，不产生无限重试。
11. 同一摄影师在旧周期尾差期间不能进入新点赞周期。
12. 5 张 baseline 中任一张失败时不得开始点赞；恢复只补缺失作品。
13. 任务创建前后、部分扫描后和 review 完成前后的崩溃均可幂等恢复。
14. 1 日/3 日任务迟到、乱序和重复投递有确定结果。
15. `+70h`、`expires_at-1s`、`expires_at`、`expires_at+1s` 的 outcome 正确，旧显式 failure 与新派生成熟结果不重复计数。
16. 使用当前历史四 run 的脱敏 fixture 验证迁移。
17. 迁移映射能排除冻结 5 张之外的旧 success，`baseline_unknown` 路径不参与算法。
18. 使用注入时钟和 fake automation adapter 覆盖两个 schedule intent 之间崩溃、创建后未绑定、重复投递和同名 payload 不一致。
19. 部分扫描失败后，用户授权 retry 会创建递增 attempt 的 replacement，且只补缺失作品。
20. 3 日先完成、1 日后 supersede 时 cycle 状态仍可重建。
21. 迁移从 scoped 原始 observation 重算人数与回赞深度，不受旧 success 首次命中作品影响。
22. 全量单元测试、skill 结构验证、`git diff --check` 和 Dashboard 视觉 QA 通过。

## 9. 不变范围

- 每日点赞目标、至少 80 位摄影师覆盖、单人上限和候选配额不变。
- `verified / promising / new / dormant` 分层规则不变。
- 独立回馈人数仍是主目标；首版只记录和展示回赞深度，不参与 selector 排序。
- 上传、分享、主页展示选择保持纯手动。
- 自动回顾不点赞、不评论、不关注、不发私信，也不读取认证信息。

## 10. Knowledge gaps

- 当前日志是否足以无歧义还原本轮主页 5 张及触达前 baseline；用户确认名单不能补足缺失 baseline。
- Codex 临时任务在设备休眠、Chrome 断开或登录失效时的实际启动延迟，需要真实运行验证。
- `+70h` 后到各 episode 精确到期之间的成熟派生逻辑，需要确认不会与旧的显式 failure 事件重复计数。
- 回赞深度何时以及如何进入排序，积累多个成熟 cycle 后再设计；首版不参与排序。
