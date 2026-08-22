# 执行质量与持续改进

本文件是 `feedback_supervisor`、执行效率 KPI、监督降级、自动改进权限和 Project Consolidation 触发规则的唯一权威来源。事件、批准、配额和浏览器动作仍分别以 schema、Skill 和运行手册为准。

## 固定监督员

每次真实点赞任务在 `doctor` 与 `status --json` 通过后实例化一次项目级 custom agent `feedback_supervisor`。配置位于 [`.codex/agents/feedback-supervisor.toml`](../.codex/agents/feedback-supervisor.toml)，固定使用 `read-only` sandbox。

监督员只做过程质检、终态复盘、效率评价和 Consolidation 建议。主 Agent 是唯一可以操作 Chrome、追加运行事件和修改项目文件的主体；监督员不得改变候选、算法、`120/60/20` 配额、批准流程、安全边界或任何外部行为。

`doctor/status` 后只完成初始化，不输出审计结论，也不计入审计节点。单次任务固定五个审计节点：

1. preflight 封存后、用户确认前审计摘要；
2. 覆盖 50 位时接收压缩状态；
3. 覆盖 100 位时接收压缩状态；
4. 覆盖 150 位时接收压缩状态；
5. terminal 后读取 sealed 事实完成最终审计。

50/100/150 三个过程节点复用同一个监督员。每批最多 10 位的 checkpoint 对账仍由主 Agent执行，不启动新的监督模型。监督输出固定为 `Verdict`、`KPI`、`Problems`、`Actions`、`Consolidation` 五段，不包含摄影师身份、候选名单、run ID 或 preview ID。

监督结论保留在线程，不创建每批 Markdown 报告、监督 CLI、事件或第二套状态库。

## 监督降级

Custom agent 不可用、上下文中断或工具不支持委派时，主 Agent必须以相同五段格式完成审计，并在 `Verdict` 明确标记 `supervisor_degraded`。降级审计不得伪称独立监督，也不得阻止安全停机或降低完成门槛。

## 可评分条件

`quality.py` 的 `build_execution_efficiency(logs, state, daily_task_id)` 只从现有 sealed logs 与可重建聚合状态计算。任务满足以下全部条件时 `gate_status=pass`：

- 属于新即时结算 run；
- terminal 为 `completed`；
- 恰好覆盖 200 位不同摄影师；
- run 中没有 `safety_paused`；
- 能通过 `onboarding_approved.preview_id` 找到对应的 sealed preflight。

完成或安全门槛失败返回 `blocked`；缺运行、缺关联 preflight、legacy 结算或时长无效返回 `unscorable`。两者都不生成综合分。旧日志继续可解析，但不强行进入新速度基线。

## KPI 公式

```text
total_minutes = preflight_duration_minutes + run_duration_minutes
covered_per_minute = 200 / total_minutes

speed_score =
    没有历史合格基线时 80
    否则 clamp(80 + 100 × (当前速度 / 前 5 个合格批次速度中位数 - 1), 0, 100)

rework_count =
    max(preview_created_count - 1, 0)
    + scan_issue_count
    + approval_rejected_count

first_pass_score = max(100 - 10 × rework_count, 0)
first_preview_fill_score = min(first_preview_candidate_count / 200 × 100, 100)

efficiency_score =
    0.50 × speed_score
    + 0.30 × first_pass_score
    + 0.20 × first_preview_fill_score
```

Dashboard 展示门槛、综合分、总耗时、覆盖/分钟、一次成功分、候选充足分和最近 5 个合格批次趋势。confirmed likes/comments 只展示 sealed 事实；滚动 30 天每百次触达反馈分是效果护栏，不并入 `efficiency_score`。

评论数不进入硬门槛。现有 schema 只能证明新增评论已确认，不能区分“本次新增评论”和“本人相同评论已存在而跳过”，因此不得伪造精确评论履约率。

token 数不纳入 KPI：sealed logs 没有可重建的 token 事实源。监督成本通过单个只读 agent、最多五个审计节点和压缩状态控制。

## Consolidation 触发规则

满足任一条件即执行对应动作：

- 任一安全门槛失败或长期规则冲突：立即 Consolidation；
- 同类问题连续两个批次出现：仅当当前任务能够访问上一批线程中的监督结论时立即 Consolidation；前次结论不可访问时标记该条件不可判定，不从 sealed 互动事实猜测监督问题；
- `efficiency_score` 较上一个合格批次下降至少 10 分：立即 Consolidation；
- 当前与对照效果窗口两边均至少 60 次触达，滚动反馈效果下降超过 10%：触发调查，确认原因后再决定是否改动；
- 无异常时每 5 个合格批次执行一次全量 Consolidation。

Consolidation 只保留跨任务、可执行、可验证的结论，删除或替换重复、失效、冲突规则。临时 ID、候选身份和单次运行流水不得进入长期文档。

## 自动改进权限

监督员只提出建议，不修改任何状态。用户对本项目实施计划的明确批准授权主 Agent在 run 封存后自动实施低风险的 Skill 文案、流程说明、项目文档和测试优化，并完成验证与独立提交；该权限来自用户批准，不来自监督员裁决，也不扩展到未列出的项目写入。

以下变化仍必须先获得用户批准：选择算法、事件 schema、批准机制、安全边界、配额或任何对 500px 的外部行为。所有改进都不得反向修改 sealed logs。
