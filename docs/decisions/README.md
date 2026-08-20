# 架构决策记录

本目录保存少量长期、跨任务且难以仅从代码推断的决策。ADR 一旦接受就不改写历史；后续变化创建新的 ADR，并在旧记录中标记被替代。

## 何时创建 ADR

仅在以下情况创建：

- 更换运行状态的事实源或持久化模型；
- 改变浏览器执行、安全授权或恢复边界；
- 改变核心归因、配额或候选算法合同；
- 引入新的生产依赖、服务或远程数据源；
- 做出会长期影响目录或公共接口的选择。

普通 bug 修复、文案调整、页面选择器适配和局部重构不创建 ADR。

## 命名与状态

- 文件名：`ADR-XXXX-short-title.md`。
- 状态：`Proposed`、`Accepted`、`Superseded` 或 `Rejected`。
- 内容：背景、决定、理由、后果和验证方式。

## 当前记录

| 记录 | 状态 | 当前有效范围 | 部分替代关系 |
|---|---|---|---|
| [ADR-0001：追加式 Markdown 事件日志](ADR-0001-append-only-event-log.md) | Accepted | Append-only、sealed 优先、可重建派生状态 | Git 与 state root 条款由 ADR-0003 部分替代 |
| [ADR-0002：单次连续任务](ADR-0002-single-run-daily-task.md) | Accepted / Partially Superseded | 零参数入口、自然语言批准、单 run 恢复 | 100 赞目标由 ADR-0004 部分替代；跨日边界由 ADR-0006 部分替代 |
| [ADR-0003：Git-backed sealed runs](ADR-0003-git-backed-sealed-runs.md) | Accepted / Partially Superseded | 私有 Git、动态 state root、串行交接 | 新周期 Automation 条款由 ADR-0005 部分替代 |
| [ADR-0004：200 位摄影师覆盖](ADR-0004-200-photographer-coverage.md) | Accepted / Partially Superseded | 200 位、第一张作品、点赞与跳过共同计覆盖 | 自然日终止边界由 ADR-0006 部分替代 |
| [ADR-0005：最新三张即时结算](ADR-0005-immediate-feedback-settlement.md) | Accepted / Partially Superseded | 最新 3 张、0-3 分、即时账本、`120/60/20` | 跨日恢复边界由 ADR-0006 部分替代 |
| [ADR-0006：Active run 跨日恢复](ADR-0006-cross-day-active-run.md) | Accepted | 当前跨日恢复合同 | 无 |
