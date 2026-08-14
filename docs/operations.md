# 运行与恢复手册

## 使用原则

真实互动优先通过 `$500px-feedback-growth` 执行。直接调用 CLI 主要用于状态检查、事件持久化、恢复和 Dashboard 重建；浏览器动作仍必须遵守 skill 的可见状态确认和安全停机规则。

每次执行前完整读取：

- [`SKILL.md`](../.agents/skills/500px-feedback-growth/SKILL.md)
- [`browser-workflow.md`](../.agents/skills/500px-feedback-growth/references/browser-workflow.md)
- [`operational-recovery.md`](../.agents/skills/500px-feedback-growth/references/operational-recovery.md)

## 固定路径

所有 CLI 命令必须指向主工作区状态根：

```text
/Users/pony/Documents/ChatGPT/PressZan/.local/500px-feedback-growth
```

不要依赖当前工作目录推导状态根，也不要使用 `.worktrees/.../.local`。工作树可以承载代码，不能承载第二份运行事实。

## 开始前检查

```bash
python3 .agents/skills/500px-feedback-growth/scripts/feedback_growth.py status \
  --state-root /Users/pony/Documents/ChatGPT/PressZan/.local/500px-feedback-growth \
  --json
```

根据结果处理：

| 状态 | 动作 |
|---|---|
| `daily_complete` 或 `confirmed_likes=100` | 当日停止，不创建第 101 个动作 |
| 存在 recoverable run | 使用 `resume --run-id <run_id>`，从最后确认事件继续 |
| `paused_reason` 非空 | 保留断点，人工确认页面和账号已恢复后再继续 |
| 首次尚未批准 | 完整执行只读 preflight，展示摘要并询问“确认执行？” |
| 今日有剩余额度且无 active run | 开始一个连续 run，执行到当日累计 100 |

## 标准日任务

### Preflight

Preflight 只读：扫描最近 30 幅本人作品、点赞来源和候选评论者，生成 preview。不得点赞、评论、关注或私信。

扫描中每页观察立即追加事件。点赞数字存在但列表空白时，只刷新读取一次；仍为空写 `scan_issue: liker_list_unavailable`，不要把空白解释为零点赞。

### 首次批准

用户回复“确认执行”后，skill 通过内部 `latest-preview` 解析最新 preview，再启动 approval run；不向用户暴露 ID。满足以下条件才使用快速复核：

- preview 与当前 `daily_task_id` 相同；
- 仍在 24 小时有效期内；
- preview 后没有确认外发互动。

快速复核只打开 preview 中候选对应的唯一 `source_url`，并只追加仍可见的已批准候选。任何稳定字段、顺序、配额或 digest 变化都应返回 `preview_changed`，封存为 `approval_rejected` 后重新 preflight。

### 点赞执行

1. 打开候选主页，检查最近 12 幅作品。
2. 选择第一幅可见且未点赞的作品。
3. 读取 `before_state=not_liked`，点击一次，再读取 `after_state=liked`。
4. 页面确认成功后立即追加事件；失败或不明确时不得重复点击。
5. 从当前作品评论区选择下一位未访问候选，链路不足时从本地高分队列重新播种。
6. 同一个 run 持续执行当天剩余额度；达到当日累计 100 后封存，并重建 status 和 Dashboard。

## 已验证故障与最短恢复

| 现象 | 长期判定 | 最短恢复 |
|---|---|---|
| 批准时 `preview_changed` | 完整复扫引入页面变化，或批准内容确实变化 | 封存旧 approval run；新鲜 preview 只复核已批准候选，不能伪造旧顺序 |
| 点赞数字可见但弹层条目为 0 | 异步加载或首次展开失败 | 只刷新读取一次；仍为空记录 `liker_list_unavailable` |
| 评论区首次为 0，但页面历史上有评论 | 异步加载或点赞弹层遮挡 | 关闭弹层或重新导航，只刷新一次；先读候选再开点赞弹层 |
| 候选主页作品不可读 | 页面临时不可用，不能确认未点赞作品 | 记录 `candidate_skipped: profile_works_unavailable`，不消耗额度 |
| 12 幅作品均已点赞 | 当前候选暂时耗尽 | 记录 `all_recent_works_liked`，转下一位，不消耗额度 |
| checkpoint 写入旧 run | 临时命令或脚本复用了旧 ID | 每次写入前核对当前 `run_id`、`scan_id` 和绝对 `state-root`；写后检查返回值 |
| Chrome 有进程但无法连接 | 没有可接管窗口或扩展通信未建立 | 保留 runtime，打开一个 Chrome 窗口后最多重连一次 |
| 导航后调用不存在的等待 API | 使用了包装层不支持的方法 | `goto()` 后用可见 DOM 条件读取，不调用不存在的等待方法 |
| 点赞或评论状态不明确 | 可能重复动作，属于安全风险 | 立即 `safety_paused: ambiguous_state`，不重按 |

更细的浏览器恢复步骤以 [`operational-recovery.md`](../.agents/skills/500px-feedback-growth/references/operational-recovery.md) 为执行依据。

## 首轮真实运行沉淀

以下是截至 2026-08-13 的历史验证基线，不代表未来当前状态：

- 历史首轮曾以四个旧式 run 合计完成 100 个确认点赞；这只用于验证旧日志兼容，不再是推荐流程。
- 当日覆盖 100 位不同摄影师，满足并高于至少 80 位的约束。
- 没有发生安全暂停，也没有第 101 个动作。
- 一次旧审批因候选状态变化被正确拒绝，证明 digest 和 `approval_rejected` 路径有效。
- 九个只读页面在一次重试后仍无法读取点赞者列表，被记录为 `scan_issue`，未阻断其余任务。
- 两位候选主页作品不可读，被跳过且没有消耗成功额度。
- 当时尚无 `verified` 摄影师，因此没有发送评论；不能为了增加互动而放宽评论资格。
- 次日只读扫描首次观察到 51 位归因回馈者，另有 49 个 episode 仍在 72 小时窗口中；这证明回馈观察链可工作，但窗口成熟前不能把 51% 报成最终成熟回馈率。

这些结果形成的长期规则已经分别进入 `AGENTS.md`、本手册和 skill references；运行 ID、preview ID、摄影师名单和个人互动细节只保留在本地日志。

## 任务结束检查

1. 达到 100、安全暂停或候选耗尽时，当前 run 已按真实状态封存；普通可恢复中断保留 active checkpoint。
2. `status --json` 的当日数量与页面确认事件一致。
3. Dashboard 已从日志重建，而不是手工修改。
4. 没有 active checkpoint 遗留；若必须中断，已明确保留 recoverable run。
5. Dashboard 的“最近执行”、互斥 episode 结果和成熟 KPI 符合 [Dashboard 统计语义](../.agents/skills/500px-feedback-growth/references/dashboard-semantics.md)。
6. 新出现的问题只有在重复出现且解法稳定后，才更新长期文档。
