---
name: 500px-feedback-growth
description: Use when a user asks to run, resume, preview, inspect, or visualize the local 500px reciprocal-like growth workflow.
---

# 500px 正向反馈增长

## 核心原则

以页面确认动作和 append-only 日志为事实源。每次启动先扫描本人最新 3 张公开作品，用相对上次完整扫描的新点赞逐张计分，再处理本次剩余摄影师；新运行封存即结算，不等待未来回顾。反馈是归因观察，不声称严格因果。

**REQUIRED SUB-SKILL:** 使用 `chrome:control-chrome` 操作用户已登录的 Chrome。仅在浏览器接口无法可靠读取可见控件时使用 `computer-use:computer-use`。不得读取密码、Cookie、local storage 或认证文件。

## 用户入口

| 用户输入 | 行为 |
|---|---|
| `$500px-feedback-growth` | 恢复或开始本次任务：扫描本人最新 3 张、生成候选、处理 200 位摄影师并即时结算 |
| `确认执行` | 批准最新有效预览并继续同一任务 |
| `status` | 只读显示进度、暂停、积分和摄影师分层 |
| `preflight` | 只读生成候选预览 |
| `dashboard` | 从日志重建本地 Dashboard |
| `doctor` | 只读检查迁移状态、Git 边界和聚合证据 |

用户不需要输入 `preview_id`、`run_id` 或内部 CLI 参数。执行 preflight 或真实互动前，完整读取 [浏览器工作流](references/browser-workflow.md)、[运行恢复手册](references/operational-recovery.md) 和 [执行质量规则](../../../docs/quality.md)；重建或解释 Dashboard 前读取 [Dashboard 统计语义](references/dashboard-semantics.md)；排查事件时读取 [事件 schema](references/event-schema.md)。

macOS/Linux 内部命令：

```bash
python3 .agents/skills/500px-feedback-growth/scripts/feedback_growth.py <command>
```

Windows 原生 Codex 使用仓库启动器；它依次查找项目 `.venv`、Codex 随附 Python 和系统 Python，不改变 CLI 参数或业务行为：

```powershell
.\.agents\skills\500px-feedback-growth\scripts\feedback_growth.cmd <command>
```

默认状态根由 CLI 从仓库位置解析；只有测试、受控恢复或外部调度才使用 `--state-root` 或 `PRESSZAN_STATE_ROOT`。

## 标准执行顺序

1. 执行 `doctor`；只有 `ok=true` 才继续。
2. 执行 `status --json`。有 recoverable run 时始终使用 `resume --run-id <run_id>`；跨日继续同一 run，不重置覆盖或创建新任务。
3. `doctor/status` 后为本次任务实例化一次项目级只读 `feedback_supervisor`。不可用时由主 Agent按同一五段格式审计，并明确标记 `supervisor_degraded`，不得伪称独立监督。
4. 无可恢复 run 时执行 `begin --mode preflight`，先完成“本人最新 3 张反馈扫描”。
5. 只调用一次 `feedback-scan-complete`，在同一条命令中为最新 3 张分别重复 `--completed-photo-id <photo_id>`；不得按作品分三次调用。首次 preview 前确认返回的 `photo_ids` 与 `completed_photo_ids` 都恰好包含这 3 张且集合相同。
6. 先用最新 3 张扫描和本地历史记录 `candidate_observed` 并执行 `preview --run-id <run_id> --seed <seed>`；最新 3 张未完整结算时 CLI 返回 `latest_three_scan_incomplete`，必须补齐或以新 `scan_id` 完整重建后再 preview。候选不足时按“增量补充”规则扩展，达到 200 位即停止，然后封存 preflight。监督员在展示摘要和用户确认前完成首次审计。
7. 用户确认后先执行只读 `latest-preview`，再执行 `begin --mode run --approve-preview <preview_id>` 和 `approve --run-id <run_id> --preview-id <preview_id>`；仅在 `approved=true` 时互动。
8. 连续处理本次剩余覆盖。浏览器按每批最多 10 位执行和对账，但不拆分业务 run；覆盖达到 50/100/150 位时向同一监督员发送压缩状态，每 10 位对账不启动新的监督模型。完成、安全暂停或候选耗尽时执行 `finish --run-id <run_id> --status <status>`，再执行 `status --json` 和 `dashboard`；终态后由监督员读取 sealed 事实做最终审计与 Consolidation 判断。

## 本人最新 3 张反馈扫描

1. 验证本人账号后，按主页当前展示顺序读取最新 3 张公开作品；不是 3 张、账号不符或任一作品身份不明确时禁止进入点赞。
2. 为扫描写 `scan_started`，其中 `purpose=latest_three_feedback`；每张写 `work_observed`，每个可见点赞者写 `received_like_observed`。
3. 每张作品必须完整打开点赞者列表。首次读取失败只刷新一次；仍失败写 `scan_issue`，该作品不列入 `--completed-photo-id`。
4. 某个 `photo_id` 第一次被完整扫描时只建立 baseline，已有点赞不计分。以后扫描相同作品时，每个此前未见的 `(photo_id, photographer_id)` 计 1 个反馈分；同一轮 3 张各有新点赞可计 3 分。
5. 新 pair 只归到该摄影师扫描前最近一次触达；单次触达最多 3 分。扫描发现时间是 observation time，不是平台真实点赞时间。
6. 只有 3/3 完整才是完整扫描。不完整扫描仍写 `feedback_scan_completed` 保存已完成事实，但缺失作品显示“数据不完整”，不得按零反馈结算；`latest_three_scan_incomplete` 会阻止生成 preview，补齐或完整重建后才能进入互动。

## 只读 Preflight 与批准

1. 先复用最新 3 张扫描中的点赞者和本地历史生成 preview；不得默认扫描最近 30 幅，也不得点赞、评论、关注或私信。
2. 候选不足时，从第 4 张本人作品开始按从新到旧顺序增量补充：每次只增加一个来源，以每批最多 10 位读取并追加观察，再重算 preview；达到 200 位即停止，最近 30 幅只是候选来源上限。
3. preview 仍在有效期且之后没有确认互动，只按 `source_url` 分组快速复核已批准候选；把复核所得 `candidate_observed` 写入新的 approval run checkpoint 后才可调用 `approve`，不得用空 checkpoint 申请批准；不重复最新 3 张反馈扫描或已完成的候选来源。
4. 每个新 run 只确认一次，run 内不重复询问。`preview_not_found`、`preview_changed`、`preview_expired` 或 `preview_not_latest` 表示批准对象已失效：封存当前 approval run 为 `approval_rejected`，重新 preflight 后对新预览重新确认；不得绕过运行时或平台强制确认。

## 连续覆盖 200 位摄影师

1. 每位候选只检查主页当前第一张作品。已点赞或作品不可读时记录 `candidate_skipped`，并写入批准计划中的 `quota_bucket`；无论点赞或跳过，该摄影师本次任务只处理一次并计入覆盖，完成条件是恰好 200 位不同摄影师。
2. 候选主页 URL 必须包含计划中的稳定摄影师 ID。进入第一张作品后，上传者稳定 actor 链接或图片资源 URL 中的稳定摄影师 ID 满足任一即可作为正向 owner 证据；两者都缺失或任一可见证据发生冲突时立即 `safety_paused`，不得点击。
3. 用每批最多 10 位作为状态汇报和恢复边界，每批后核对同一 run 的日志覆盖数；这不是新的审批、结算或业务 run。每个确认动作仍立即追加 checkpoint。
4. 点赞前读取 `before_state=not_liked`；点击一次后重新读取同一控件。只有 `after_state=liked` 可见才记录成功。
5. 每次确认后立即追加 `outgoing_like_confirmed`，新运行使用 `settlement_mode=immediate`；禁止在结束后集中回填。
6. 每次确认点赞后，在同一作品评论固定文本 `👍👍👍`。当前账号已有相同可见评论时不重复；新增评论只有按本人稳定身份确认可见后，才以 `before_state=not_visible`、`after_state=visible` 追加 `outgoing_comment_confirmed`。状态不明确立即 `safety_paused`。
7. 配额固定为 `120 exploit_first / 60 new / 20 retest`。桶不足时确定性回填，但不得重复摄影师或处理第 201 位。
8. `exploit_first` 优先 verified/promising；`retest` 只接纳冷却满 7 天的 dormant；其余进入 new。确认点赞数可以少于 200。
9. 新触达在任务封存后立即成为一个未反馈轻负样本；后续最新 3 张扫描发现该摄影师新点赞时，同一触达改为 1-3 分正反馈，不同时保留正负。

## 积分与分层

- 原始反馈分不设上限，用于累计统计和审计。
- 排序使用 30 天半衰期衰减的有效反馈分，最多 12 分；未反馈触达也按相同半衰期衰减。
- `verified`：最近 30 天至少 3 个反馈分。
- `promising`：最近 30 天有 1-2 分，或历史上至少有 1 分且未进入 dormant。
- `dormant`：历史累计至少 3 次触达且最近 30 天 0 分；最后一次未反馈触达满 7 天后才可 retest。
- `new`：其余摄影师。判断顺序为 verified、dormant、promising、new。
- 旧 cycle、episode 和 review 事件只读兼容；旧 success/failure/open 各映射一次，不阻止新运行，也不进入新 Dashboard 的未来回顾流程。

## Dashboard

Dashboard 展示当前任务、执行效率、最新 3 张反馈扫描、滚动 30 天表现、关系分层/排行和 `120/60/20` 策略配额。效率卡只从 sealed logs 确定性重建门槛、综合分、耗时、覆盖速度、一次成功、候选充足度和最近 5 个合格批次趋势；点赞/评论只展示事实，滚动反馈效果是独立护栏。完整公式与监督闭环见 [执行质量规则](../../../docs/quality.md)。每 100 次触达反馈分允许超过 100，不得命名为回馈率。不完整扫描明确显示“数据不完整”。默认浅色，深色只由用户手动切换。

## 停止与恢复

- CAPTCHA、限频、登录失效、平台警告、账号不匹配或状态不明确：立即追加 `safety_paused` 并停止；不绕过、不切换账号、不重复点击。
- 候选池和评论链都耗尽：封存为 `incomplete_candidate_exhausted`，不得降低 200 位目标或放宽单人一次约束。
- 工具或线程中断但页面状态可恢复：保留 active checkpoint；下次零参数启动恢复同一 run。
- 浏览器超时或输出损坏：先对账当前页面、最近 action 和 checkpoint；未确认前不重放点击。
- Active run 可跨上海日界线继续，沿用启动时的 `daily_task_id` 和剩余覆盖；相邻新任务启动时间尽量间隔超过 24 小时。
- Checkpoint 与 sealed log 只追加；聚合状态和 Dashboard 必须能从日志重建。

## 跨机器交接

- 上传和分享由用户手动完成。
- 私有 Git 只共享 sealed `runs/*.md`；Dashboard、浏览器认证和未封存 checkpoint 不迁移。
- 同一账号必须串行执行。开始前 pull 并通过 `doctor`；运行封存后提交并推送新增 runs。
- 未封存 checkpoint 只能在产生它的机器恢复。账号主页 `Dora0125` 和稳定用户 ID 是固定安全校验。
