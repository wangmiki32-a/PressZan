---
name: 500px-feedback-growth
description: Use when a user asks to run, resume, preview, inspect, or visualize the local 500px reciprocal-like growth workflow.
---

# 500px 正向反馈增长

## 核心原则

把每次互动当作可追溯实验：先读本地状态，再读页面；只记录页面确认的变化；以 72 小时内首次观察到的归因回馈优化候选，不声称严格因果。

**REQUIRED SUB-SKILL:** 使用 `chrome:control-chrome` 操作用户已登录的 Chrome。仅在浏览器接口无法可靠读取可见控件时，使用 `computer-use:computer-use`。不得读取密码、Cookie、local storage 或认证文件。

## 用户入口

| 用户输入 | 行为 |
|---|---|
| `$500px-feedback-growth` | 冻结本轮公开作品、恢复或开始点赞，并在完成后创建两次只读回顾任务 |
| `确认执行` | 批准最新有效预览并继续同一日任务 |
| `status` | 只读显示进度、暂停和摄影师分层 |
| `preflight` | 只读刷新回馈并生成候选预览 |
| `dashboard` | 从日志重建本地 Dashboard |
| `doctor` | 只读检查迁移状态、Git 边界和聚合证据 |

用户不需要输入 `preview_id`、`run_id` 或内部 CLI 参数。执行 `preflight` 或真实互动前，完整读取 [浏览器工作流](references/browser-workflow.md) 和 [运行恢复手册](references/operational-recovery.md)；重建或解释 Dashboard 前读取 [Dashboard 统计语义](references/dashboard-semantics.md)；排查事件时再读取 [事件 schema](references/event-schema.md)。

内部命令统一使用：

```bash
python3 .agents/skills/500px-feedback-growth/scripts/feedback_growth.py <command>
```

默认状态根由 CLI 从仓库位置解析；只有测试、受控恢复或外部调度才使用 `--state-root` 或 `PRESSZAN_STATE_ROOT`。

## 默认启动

上传和分享由用户手动完成。Skill 从点赞周期开始负责“冻结公开作品 → baseline → 点赞 → +20h 回顾 → +70h 回顾 → Dashboard”。

1. 先执行 `doctor`。只有 `ok=true` 才继续；日志未跟踪、路径漂移或本地状态不明确时，不打开 500px 页面。
2. 执行 `status --json` 和 `cycle-status`。上一周期尚未结算时，不开始新一轮点赞；用户仍可手动上传和调整展示。
3. 若返回同日 recoverable run，执行 `resume --run-id <run_id>`，继续同一个 run；不得新建运行或重复动作。若返回 `stale_recoverable_run`，不得跨日追加动作，先封存旧日为 `paused_incomplete`，再开始新日任务。
4. 新周期先 `begin --mode cycle --cycle-id <cycle_id>`：按主页当前顺序确认并冻结 5 张公开本人作品，逐张完整读取当前点赞者作为 baseline；不是 5 张、账号不符、非公开或任一张未完成读取时禁止点赞。
5. 若首次尚未批准，执行只读 preflight，展示候选数、层级、配额和风险摘要，只询问“确认执行？”。
6. 已批准且 baseline 已 sealed 时，使用 `begin --mode run --cycle-id <cycle_id>` 绑定周期并连续执行到当日累计 100。

## 周期回顾自动化

1. 点赞运行以最后一次 `outgoing_like_confirmed` 为时间基准。正常完成或候选耗尽且至少成功点赞 1 次后，原子记录 `cycle_like_completed` 和两个调度 intent。
2. 在当前执行机器为本周期临时创建两个一次性任务：`+20h` 的 `review_1d` 与 `+70h` 的 `review_3d`。不得创建周期性轮询任务；Automation 不随 Git 迁移。
3. 两次任务都只读：只扫描本周期冻结 5 张作品，完整记录每张作品当前点赞者；不得点赞、评论、关注、私信或修改展示。
4. baseline 中已经存在的 `(photo_id, photographer_id)` 不算新回馈。冻结范围内、首次观察晚于触达且不超过 episode expiry 的新 pair 才进入 scoped attribution。
5. `+70h` 是最后一次主动观察，不等于 72 小时成熟。到达每个 episode 的 `expires_at` 后，下一次状态或 Dashboard 重建才把仍无回馈者派生为 failure。
6. 每次回顾完成后立即封存 review checkpoint 并重建 Dashboard；失败时记录原因、保留断点并通知用户，不把缺失作品当作零点赞。
7. 历史迁移周期若人工 `+20h` 回顾已完成，只补建一次 `+70h` 任务；不得重建或重跑 1 日任务。

## 只读 Preflight

1. 执行 `begin --mode preflight`，保存内部 `run_id`。
2. 扫描自己的最近 30 幅作品、收到的点赞和评论候选；不得点赞、评论、关注或私信。
3. 每次页面观察后立即执行 `event --run-id <run_id> --kind <kind> --field key=value`。
4. 执行 `preview --run-id <run_id> --seed <seed>`；计划上限是当天剩余额度，最多 100 个合格动作。
5. 执行 `finish --run-id <run_id> --status completed`，向用户展示摘要，但隐藏内部 ID。

## 首次“确认执行”

1. 执行内部只读 `latest-preview`，取得最新有效 preview。若返回 `preview_not_found`、`preview_not_current_day`、`preview_changed` 或 `preview_expired`，自动重新 preflight，再次请求自然语言确认。
2. 执行 `begin --mode run --approve-preview <preview_id>`。
3. 对同日新鲜 preview 走快速复核：按 `source_url` 分组，每个来源只打开一次；只记录仍可见的已批准候选及当前 `page_order`，不重扫全部 30 幅作品或点赞者列表。
4. 执行 `approve --run-id <run_id> --preview-id <preview_id>`；仅在 `approved=true` 时继续。
5. 若返回 `preview_not_latest`、`preview_changed` 或 `preview_expired`，执行 `finish --run-id <run_id> --status approval_rejected`，自动生成新 preflight，不把内部错误或 ID 交给用户处理。

## 连续执行到 100

1. 每位候选检查最近 12 幅作品，选择第一幅可见未点赞作品。全部已点赞或作品不可读则跳过，不消耗成功额度。
2. 点赞前读取 `before_state=not_liked`；点击一次后重新读取同一控件。只有 `after_state=liked` 才记成功。
3. 每次确认后立即追加 `outgoing_like_confirmed`；禁止在运行结束后集中回填。
4. 继续当前评论链，链路不足时从本地高分队列重新播种，直到当日累计 100、安全停止或候选耗尽。
5. 完成日覆盖至少 80 位摄影师；单人每天最多 2 幅，第二幅只限 verified。配额为 45 个 verified/promising 首赞、20 个复测、15 个新人、最多 20 个 verified 第二赞。
6. verified 距上次确认评论至少 7 天时，才可在当天第一幅成功点赞作品评论固定文本“拍的真棒👍”；评论单独确认和记录。
7. 达到当日累计 100 后执行 `finish --run-id <run_id> --status completed`，再执行 `status --json` 和 `dashboard`。

## Dashboard 回顾

1. 有 cycle 时优先显示最新周期：冻结 5 张、baseline、新增 liker、两次回顾状态和成熟尾差；无 cycle 的旧日志才退回最近执行日口径。
2. 新周期点赞一旦产生确认动作，成为新的回顾 cohort；只有 preflight 的日期不能覆盖它。
3. `归因回馈 / 观察窗口中 / 窗口成熟未回馈` 是互斥 episode 结果；不得把 `Verified` 身份混入结果漏斗。
4. 只有至少 8 个执行日才画折线；1 个执行日使用双柱对比，2-7 个执行日使用分组柱状图。
5. 默认浅色主题；手动切换深色。Dashboard 只展示日志可重建的指标，不展示未落入事件模型的“层级变化”。

## 停止与恢复

- CAPTCHA、限频、登录失效、平台警告、账号不匹配或状态不明确：立即追加 `safety_paused` 并停止；不绕过、不切换账号、不重复点击。
- 候选池和评论链都耗尽：执行 `finish --run-id <run_id> --status incomplete_candidate_exhausted`，不得降低 100/80/单人上限或 verified 第二赞约束。
- 工具或线程中断但页面状态仍可恢复：保留 active checkpoint，不错误封存；下次零参数启动通过 `resume --run-id` 继续。
- 上海日界线后旧 active run 不可继续；`resume` 和 `event` 必须返回 `daily_task_expired`，旧日未完成额度不结转。
- 普通加载失败只刷新读取一次；仍失败则记录 `scan_issue` 或 `candidate_skipped`。
- Checkpoint 与 sealed log 只追加；聚合状态和 Dashboard 必须能从日志重建。

## 跨机器交接

- 私有 Git 只共享 sealed `runs/*.md`；Dashboard、Automation、浏览器认证和未封存 checkpoint 不迁移。
- 同一账号必须串行执行。开始前先 pull 并通过 `doctor`；运行封存后提交并推送新增 runs。
- 未封存 checkpoint 只能在产生它的机器恢复。另一台机器不得猜测页面动作或创建替代 run。
- 账号主页 `Dora0125` 和稳定用户 ID 仍是固定安全校验，不因执行者或 clone 路径改变。

## 已验证经验

- 候选读取先于点赞弹层，避免弹层遮挡或异步加载造成假空白。
- 新鲜 preview 只复核批准候选；重复完整 preflight 会变慢并增加 `preview_changed`。
- CLI 根据 checkout 与 Git common directory 自动回到主仓库状态根；worktree 不产生第二份状态。
- 临时命令每次校验当前 `run_id`、`scan_id` 和写入结果，不复用写死的旧 ID。
- 历史点赞者只能初始化为 promising；滚动 30 天内至少 2 次独立归因回馈才是 verified。
