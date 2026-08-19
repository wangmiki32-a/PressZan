# ADR-0006：Active run 跨日连续恢复

- 状态：Accepted
- 日期：2026-08-20
- 替代：ADR-0002、ADR-0004、ADR-0005 中“跨日必须结束旧任务”的恢复边界；其他决定保持不变

## 背景

200 位摄影师任务可能因执行时长、浏览器恢复或安全暂停跨过 Asia/Shanghai 日界线。按日界线强制封存会拆散同一次明确启动的任务，并增加重复候选、重复动作和状态对齐成本。用户实际约束是相邻新任务尽量间隔超过 24 小时，不是 active run 必须在自然日内结束。

## 决定

1. Active `preflight` 或 `run` 跨过 Asia/Shanghai 日界线后仍可恢复。
2. 恢复时沿用启动时的 `daily_task_id`、批准 preview、配额桶和已覆盖摄影师集合。
3. 跨日不创建第二个 run，不重置覆盖，也不重复已确认动作。
4. 任务仍只在覆盖 200 位、候选耗尽或安全暂停时进入终态。
5. 已完成任务之间不强制按自然日切割；相邻新任务启动时间尽量间隔超过 24 小时。
6. Preview 只受自身 24 小时 expiry、digest 与配额快照约束，跨日界线本身不使其失效。

## 后果

- `daily_task_id` 表示任务启动日，而不是动作必须发生的唯一自然日。
- Dashboard 的当前进度和策略配额使用“本次任务”口径。
- 无 active run 时，新任务仍使用启动时的 Asia/Shanghai 日期创建新的 `daily_task_id`。
- 旧 sealed logs 和按自然日聚合的历史趋势保持可读，不重写历史事件。

## 验证

- CLI 回归测试覆盖跨日 `begin`、`resume`、`status`、事件追加和 preview 有效性。
- Selector 使用 active run 的 `daily_task_id` 计算剩余覆盖与配额。
- 全量单元测试、skill 结构验证和 `git diff --check` 必须通过；真实未跟踪 sealed run 导致的 `doctor` 保护失败需单独报告。
