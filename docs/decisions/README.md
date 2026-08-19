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

- [ADR-0001：以追加式 Markdown 事件日志作为运行事实源](ADR-0001-append-only-event-log.md)
- [ADR-0002：一次运行完成当日剩余点赞任务](ADR-0002-single-run-daily-task.md)
- [ADR-0003：私有 Git 版本化 sealed runs](ADR-0003-git-backed-sealed-runs.md)
- [ADR-0004：以 200 位摄影师覆盖作为每日完成条件](ADR-0004-200-photographer-coverage.md)
- [ADR-0005：最新三张增量扫描与当日即时结算](ADR-0005-immediate-feedback-settlement.md)
