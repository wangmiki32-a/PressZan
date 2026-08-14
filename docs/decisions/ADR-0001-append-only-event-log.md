# ADR-0001：以追加式 Markdown 事件日志作为运行事实源

- 状态：Accepted
- 日期：2026-08-12
- 适用范围：运行状态、恢复、归因、Dashboard

## 背景

真实浏览器互动可能因页面加载、浏览器连接、限频或人工中断而停止。系统必须区分“页面已确认但批次尚未结束”和“尚未执行”，避免恢复时重复点赞。同时，候选算法和 Dashboard 需要从同一证据集重建，不能依赖无法审计的缓存。

运行数据包含个人互动记录，不适合提交到 Git，也不需要独立数据库或远程服务。

## 决定

1. 使用 `.local/500px-feedback-growth/checkpoints/*.md` 保存活动运行的只追加事件。
2. 运行结束后写入 `.local/500px-feedback-growth/runs/*.md`，每个 run 只允许创建一次 sealed log。
3. sealed log 是完成运行的权威事实；同一 `run_id` 的 retained checkpoint 在重建时忽略。
4. 聚合摄影师状态、每日配额、归因 episode 和 Dashboard 全部从有效日志重建。
5. 事件只能通过 CLI 校验后追加；不手工编辑、覆盖或事后补写成功动作。
6. 主工作区的绝对 `--state-root` 是唯一运行状态位置，worktree 不建立第二份状态。

Run 的公开粒度和恢复合同由 [ADR-0002](ADR-0002-single-run-daily-task.md) 补充；它不改变本 ADR 的 append-only 事实源。

## 理由

- 每个动作确认后即可落盘，崩溃恢复点清晰。
- Markdown 便于人工审计，规范 JSON block 便于确定性解析。
- 不需要数据库、服务进程或新生产依赖。
- 事实源与 Dashboard 解耦，展示层损坏时可完整重建。
- 本地 Git ignore 保持个人互动数据不进入版本库。

## 后果

正面影响：

- 重复 action ID、损坏 schema、多个活动 checkpoint 等状态可显式拒绝。
- 可用固定时钟、固定 seed 和临时目录完成确定性测试。
- 状态迁移必须通过明确 schema 版本完成，不能静默猜测。

成本与限制：

- 日志会持续增长，重建成本随事件数增加；首版接受该成本。
- sealed log 不支持原地修复，错误事件需要通过新事件或显式迁移处理。
- 页面行为是否真实成功仍依赖动作前后的可见状态证据，日志不能替代页面确认。

## 验证

- `tests.test_store` 验证 append-only、sealed 优先级、schema 校验和 checkpoint 恢复。
- `tests.test_analytics` 验证从事件重建归因和分层。
- `tests.test_cli` 验证 action 去重、恢复、审批和每日上限。
- `tests.test_dashboard` 验证 Dashboard 只从聚合状态生成。
