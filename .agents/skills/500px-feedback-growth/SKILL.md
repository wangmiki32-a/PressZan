---
name: 500px-feedback-growth
description: Use when a user asks to analyze 500px reciprocal likes, preview or execute a controlled 500px liking batch, inspect daily interaction progress, or rebuild the local feedback dashboard.
---

# 500px 正向反馈增长

## 核心原则

把每次互动当作可追溯实验：先读取状态，后操作页面；只记录页面确认的变化；以 72 小时内首次观察到的归因回馈优化下一批人选，不声称严格因果。

**REQUIRED SUB-SKILL:** 使用 `chrome:control-chrome` 操作用户已登录的 Chrome。仅在浏览器接口无法可靠读取可见控件时，使用 `computer-use:computer-use` 读取页面或执行已获授权且可确认的 UI 动作。禁止读取密码、Cookie、local storage 或认证文件。

## 操作路由

| 用户意图 | 操作 |
|---|---|
| 首次分析、刷新回馈、预览候选 | `preflight` |
| 执行一批点赞 | `run`，默认最多 25 个成功点赞 |
| 查看今天进度、暂停和层级 | `status` |
| 重建本地 HTML | `dashboard` |

执行 `preflight` 或 `run` 前必须完整读取 [浏览器工作流](references/browser-workflow.md) 和 [运行恢复手册](references/operational-recovery.md)。仅在需要手工追加/排查事件时读取 [事件 schema](references/event-schema.md)。所有命令使用 `python3 .agents/skills/500px-feedback-growth/scripts/feedback_growth.py`，并显式传入主工作区的 `--state-root`。

## 通用前置检查

1. 先执行 `status --json`，再做任何浏览器变更。
2. 若今日已完成 100 次，停止；未完成额度不跨 Asia/Shanghai 日界线结转。
3. 若状态暂停、存在 CAPTCHA、限频、登录失效、平台警告、账号不匹配或状态不明确，停止并报告断点。
4. 若存在可恢复运行，执行 `resume --run-id <run_id>`，不要新建运行或重复动作。

## `preflight`

1. 执行 `begin --mode preflight` 并保存 `run_id`。
2. 按浏览器工作流扫描自己的最近 30 幅作品、收到的点赞和候选评论者；全程不得点赞、评论、关注或私信。
3. 每次页面观察后立即用 `event --run-id <run_id> --kind <kind> --field key=value` 追加 checkpoint。
4. 执行 `preview --run-id <run_id> --seed <seed>`，再执行 `finish --run-id <run_id> --status completed`。
5. 展示 preview ID、digest、24 小时 expiry、候选数、层级/配额和跳过原因。首次只接受用户明确输入 `run --approve <preview_id>`。

## `run`

1. 再次执行 `status --json`。若内部 `begin --mode run` 返回 `preflight_required`，立即完成完整 `preflight` 并返回预览；不要把原始错误交给用户，也不要互动。
2. 首次批准时，把公开的 `run --approve <preview_id>` 映射为内部 `begin --mode run --approve-preview <preview_id>`。
3. 若批准预览来自同一自然日、仍在 24 小时有效期内、且预览后没有确认互动，走快速复核：读取 preview 的 `candidate_plan`，按 `source_url` 分组，每个来源作品只打开一次；只记录仍在评论区可见的已批准候选及其当前 `page_order`。不要再次扫描自己的 30 幅作品或点赞者列表。随后执行 `approve --run-id <run_id> --preview-id <preview_id>`；仅在响应为 `approved=true` 时继续。
4. 若返回 `preview_not_latest`、`preview_changed` 或 `preview_expired`，先执行 `finish --run-id <run_id> --status approval_rejected`，自动完成新 `preflight` 并返回替代 preview ID。不得遗留活动 approval checkpoint。
5. 每位候选只检查最近 12 幅作品，完成最多 25 个页面确认的成功点赞，直到今日累计 100。每日覆盖至少 80 位摄影师；单人最多 2 幅，第二幅只限 verified。默认配额为 45 个 verified/promising 首赞、20 个不确定/复测、15 个新人、最多 20 个 verified 第二赞。
6. 只有 verified 且本地 7 天内未评论时，才可在当天第一幅成功点赞作品评论一次固定文本“拍的真棒👍”。不得改写或追加内容。
7. 每次点赞或评论都先记录 `before_state`，操作后重新读取页面；只有可见状态变为预期 `after_state` 才立即追加确认事件。点击本身不代表成功。
8. 达到批次上限或安全停止点后执行 `finish --run-id <run_id> --status <status>`；随后运行 `status --json` 和 `dashboard`。

## 硬停止与恢复

- CAPTCHA、限频、登录失效、平台警告、账号不匹配或状态不明确：立即追加 `safety_paused`，保留断点并停止；不得绕过验证。
- 普通加载失败：最多刷新一次并重新读取；仍失败则记录跳过。不得连续刷新三次或以坐标盲点。
- 候选不足：优先扩大不同摄影师覆盖；不得放宽每日 100、至少 80 人、单人 2 幅或 verified 第二赞约束。
- 所有 checkpoint 与 sealed Markdown 日志只追加。聚合状态和 Dashboard 必须能从日志重建；不得事后补写成功动作。

## 快速检查

- 成功点赞：页面 `not_liked → liked`，并已 checkpoint。
- 成功评论：页面 `not_visible → visible`，文本完全一致，并已 checkpoint。
- 归因回馈：新 liker pair 首次观察在最近触达之后且不晚于 72 小时；同一摄影师窗口内只算一个独立回馈者。
- 完成日：恰好 100 个确认点赞才生成历史 Tab；未完成只显示顶部当前状态。

## 常见错误

- 把录制过工作流当成首次候选批准：录制不等于 `run --approve <preview_id>`。
- 点赞后再统一写日志：崩溃会造成重复；每次确认后立即追加。
- 将历史点赞者直接标为 verified：基线只能初始化 promising；滚动 30 天内至少 2 次归因回馈才是 verified。
- 评论链断裂就停止探索：按浏览器工作流从本地高分队列重新播种。
- 批准后又完整扫描 30 幅作品：这会把分钟级页面加载差异混入候选池，既慢又容易触发 `preview_changed`；同日新鲜预览只复核已批准候选。
- 把点赞弹层的短暂空白当成零点赞：只读刷新一次，仍为空才记录 `liker_list_unavailable`。
- 复用写死旧 `run_id` 的临时追加脚本：每次运行必须生成或校验当前 `run_id`、`scan_id` 和主工作区 `state-root`。
