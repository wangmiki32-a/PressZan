# 运行与恢复手册

## 使用原则

真实互动优先通过 `$500px-feedback-growth` 执行。直接调用 CLI 主要用于状态检查、事件持久化、恢复和 Dashboard 重建；浏览器动作仍必须遵守 skill 的可见状态确认和安全停机规则。

每次执行前完整读取：

- [`SKILL.md`](../.agents/skills/500px-feedback-growth/SKILL.md)
- [`browser-workflow.md`](../.agents/skills/500px-feedback-growth/references/browser-workflow.md)
- [`operational-recovery.md`](../.agents/skills/500px-feedback-growth/references/operational-recovery.md)

## 状态根解析

正常 clone 不需要配置路径。CLI 按以下顺序解析唯一状态根：

1. 显式 `--state-root PATH`，用于测试和受控恢复；
2. 环境变量 `PRESSZAN_STATE_ROOT`，用于特殊安装或外部调度；
3. 主仓库 `.local/500px-feedback-growth`。

在 worktree 中运行代码时，resolver 会回到主仓库状态根，不创建 `.worktrees/.../.local` 第二份事实。每轮 Automation 记录的是创建任务机器当时解析出的绝对路径，不能复制到另一台机器继续使用。

## 开始前检查

```bash
python3 .agents/skills/500px-feedback-growth/scripts/feedback_growth.py doctor
python3 .agents/skills/500px-feedback-growth/scripts/feedback_growth.py status --json
```

Windows 原生 Codex 使用同参数的启动器：

```powershell
.\.agents\skills\500px-feedback-growth\scripts\feedback_growth.cmd doctor
.\.agents\skills\500px-feedback-growth\scripts\feedback_growth.cmd status --json
```

启动器优先使用仓库 `.venv`，其次使用 Codex 随附 Python，再回退到系统 `py`、`python3` 或 `python`；解释器切换不改变状态根、日志和业务语义。

`doctor` 必须先通过。它只读验证路径、sealed logs、聚合证据和 Git 边界；不读取 Chrome 凭证、不访问 500px、不修改日志。

根据结果处理：

| 状态 | 动作 |
|---|---|
| `daily_complete` 或 `covered_photographers=200` | 当日停止，不处理第 201 位摄影师 |
| 存在 recoverable run | 使用 `resume --run-id <run_id>`，从最后确认事件继续 |
| `paused_reason` 非空 | 保留断点，人工确认页面和账号已恢复后再继续 |
| 首次尚未批准 | 完整执行只读 preflight，展示摘要并询问“确认执行？” |
| 今日有剩余覆盖且无 active run | 开始一个连续 run，执行到恰好 200 位不同摄影师 |

## 标准日任务

### 周期准备

1. 用户手动完成上传和分享；启动点赞 Skill 时才固定本轮 scope。
2. 从主页确认本人账号与恰好 5 张公开展示作品，记录顺序并冻结；主页后续变化不改写当前 cycle。
3. 逐张读取完整点赞者 baseline。零 liker 也必须写 completion；任一张失败则保持 preparing，不进入点赞。
4. Baseline sealed 后，run 通过 `cycle_id` 绑定；不得把同一 run 映射到多个周期。

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

1. 打开候选主页，只检查当前第一张作品，不扫描其余作品。
2. 第一张已点赞或不可读时记录 `candidate_skipped` 并计入覆盖；未点赞时进入点赞流程。
3. 读取 `before_state=not_liked`，点击一次，再读取 `after_state=liked`。
4. 页面确认成功后立即追加事件；失败或不明确时不得重复点击。
5. 每次确认点赞后在同一作品评论 `👍👍👍`；同文本本人评论已可见时不重复，新增评论只有可见后才记录。
6. 从当前作品评论区选择下一位当日尚未覆盖的候选，链路不足时从本地高分队列重新播种。
7. 同一个 run 持续执行当天剩余覆盖；恰好处理 200 位不同摄影师后封存，并重建 status 和 Dashboard。确认点赞数可以少于 200。

### 自动回顾

1. 点赞结束后，以最后一次确认点赞为基准，创建 `+20h review_1d` 与 `+70h review_3d` 两个一次性任务。
2. 两次任务只读扫描冻结 5 张，逐张记录完整 liker 列表；5/5 后封存并重建 Dashboard。
3. +70h 完成不提前判失败；episode 到 72 小时 expiry 后，下一次重建才显示成熟结果。
4. 任务创建后未绑定日志时，用确定性名称和 payload digest 恢复；禁止盲目再建同名任务。
5. 已结算的历史周期不重建 Automation；只有新的点赞周期在实际执行机器创建两次任务。

## 跨机器串行交接

1. GitHub 仓库保持私有，协作者使用自己的 GitHub 身份 clone。
2. 开始前先 pull，再运行 `doctor`。远端状态、tracked runs 或本地 checkpoint 不明确时禁止开始新的互动。
3. 同一账号同一时间只允许一台机器执行；不使用分支合并来调和两个并发点赞批次。
4. 运行达到终态并生成 sealed log 后，提交并推送新增 `runs/*.md`；另一台机器下次执行前再 pull。
5. 未封存 checkpoint 不进入 Git，只能在原机器恢复。若原机器不可用，不得在另一台机器猜测已执行动作；先人工核对页面并按安全暂停处理。
6. Dashboard 在每台机器本地重建。Automation 不随 Git 迁移，新周期由实际执行机器重新创建。

## 已验证故障与最短恢复

| 现象 | 长期判定 | 最短恢复 |
|---|---|---|
| 批准时 `preview_changed` | 完整复扫引入页面变化，或批准内容确实变化 | 封存旧 approval run；新鲜 preview 只复核已批准候选，不能伪造旧顺序 |
| 点赞数字可见但弹层条目为 0 | 异步加载或首次展开失败 | 只刷新读取一次；仍为空记录 `liker_list_unavailable` |
| 评论区首次为 0，但页面历史上有评论 | 异步加载或点赞弹层遮挡 | 关闭弹层或重新导航，只刷新一次；先读候选再开点赞弹层 |
| 候选主页第一张作品不可读 | 页面临时不可用 | 记录 `candidate_skipped: latest_work_unavailable`，计入覆盖后转下一位 |
| 第一张作品已经点赞 | 当前候选无需重复点赞 | 记录 `candidate_skipped: latest_work_already_liked`，计入覆盖后转下一位 |
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
- 次日旧口径扫描曾写入 51 个 success；它包含最近 30 张的观察，不能直接代表冻结 5 张 scoped 回馈。迁移后由 baseline、冻结 scope 和原始 observation 重新计算，旧 success 仅保留审计。
- 当前迁移回归基线为 14 份 sealed logs、42 个成熟归因回馈、58 个成熟未回馈、0 个 open；它用于验证迁移没有改变事实，不是永久业务常量。

这些结果形成的长期规则已经分别进入 `AGENTS.md`、本手册和 skill references；运行 ID、preview ID、摄影师名单和个人互动细节只保留在本地日志。

## 任务结束检查

1. 覆盖 200 位不同摄影师、安全暂停或候选耗尽时，当前 run 已按真实状态封存；普通可恢复中断保留 active checkpoint。
2. `status --json` 的覆盖数、确认点赞数和评论数与页面确认事件一致。
3. Dashboard 已从日志重建，而不是手工修改。
4. 没有 active checkpoint 遗留；若必须中断，已明确保留 recoverable run。
5. Dashboard 的“最近执行”、互斥 episode 结果和成熟 KPI 符合 [Dashboard 统计语义](../.agents/skills/500px-feedback-growth/references/dashboard-semantics.md)。
6. 新出现的问题只有在重复出现且解法稳定后，才更新长期文档。
7. 有 cycle 时检查冻结 5/5、两个回顾槽、baseline 排除和 maturity tail；不得用旧 raw success 替代 eligible evidence。
8. 生成新的 sealed log 后已提交并推送；checkpoint、Dashboard 和认证信息仍未被 Git 跟踪。
