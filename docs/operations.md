# 运行与恢复手册

## 使用原则

真实互动通过 `$500px-feedback-growth` 执行。CLI 用于状态检查、事件持久化、恢复和 Dashboard 重建；浏览器动作遵守 skill 的可见状态确认和安全停机规则。

每次执行前完整读取：

- [`SKILL.md`](../.agents/skills/500px-feedback-growth/SKILL.md)
- [`browser-workflow.md`](../.agents/skills/500px-feedback-growth/references/browser-workflow.md)
- [`operational-recovery.md`](../.agents/skills/500px-feedback-growth/references/operational-recovery.md)

## 状态根解析

CLI 按以下顺序解析唯一状态根：

1. 显式 `--state-root PATH`，用于测试和受控恢复；
2. 环境变量 `PRESSZAN_STATE_ROOT`，用于特殊安装；
3. 主仓库 `.local/500px-feedback-growth`。

在 worktree 运行代码时，resolver 回到主仓库状态根，不创建第二份事实。

## 开始前检查

```bash
python3 .agents/skills/500px-feedback-growth/scripts/feedback_growth.py doctor
python3 .agents/skills/500px-feedback-growth/scripts/feedback_growth.py status --json
```

Windows 原生 Codex 使用同参数启动器：

```powershell
.\.agents\skills\500px-feedback-growth\scripts\feedback_growth.cmd doctor
.\.agents\skills\500px-feedback-growth\scripts\feedback_growth.cmd status --json
```

启动器优先仓库 `.venv`，其次 Codex 随附 Python，再回退到系统 Python；解释器切换不改变业务语义。

`doctor` 必须先通过。它只读验证路径、sealed logs、聚合证据和 Git 边界，不访问 500px 或读取凭证。

| 状态 | 动作 |
|---|---|
| 当日已覆盖 200 位 | 停止，不处理第 201 位 |
| 存在 recoverable run | `resume --run-id <run_id>`，从最后确认事件继续 |
| `paused_reason` 非空 | 保留断点，确认页面和账号恢复后继续 |
| 首次尚未批准 | 执行只读 preflight，展示摘要并询问“确认执行？” |
| 今日有剩余覆盖且无 active run | 开始连续 run，执行到 200 位或安全终态 |

## 标准日任务

### 最新 3 张反馈扫描

1. 用户手动完成上传和分享。
2. 从本人主页确认账号和最新 3 张公开作品，逐张记录 `work_observed` 并完整读取点赞者。
3. 首次读取某张作品只建立 baseline；后续扫描只对此前未见 pair 计分。
4. 每张加载失败最多刷新一次。仍失败写 `scan_issue`，不要把它列为 completed 或按零点赞处理。
5. 使用 `feedback-scan-complete` 生成重建器校验过的 summary。3/3 才进入正常候选流程；部分完成封存为数据不完整。

### Preflight

最新 3 张反馈扫描之后，可只读扫描本人最近 30 幅作品、点赞来源和评论候选，生成 preview。不得点赞、评论、关注或私信。

每页观察立即追加事件。点赞数字存在但列表空白时只刷新一次；仍为空写 `scan_issue: liker_list_unavailable`。

### 首次批准

用户回复“确认执行”后，skill 解析最新 preview；满足同一 `daily_task_id`、24 小时有效且 preview 后没有确认互动，才快速复核。

快速复核只打开候选计划中的唯一 `source_url`。稳定字段、顺序、配额或 digest 变化时返回 `preview_changed`，封存 `approval_rejected` 后重新 preflight。

### 点赞执行

1. 候选主页只检查当前第一张作品。
2. 已点赞或不可读时记录 `candidate_skipped` 并计覆盖；未点赞才点击。
3. 只接受同一控件 `before_state=not_liked -> after_state=liked`。
4. 页面确认后立即追加 `outgoing_like_confirmed`；新运行自动使用 `settlement_mode=immediate`。
5. 每次确认点赞后评论 `👍👍👍`；相同本人评论已可见时不重复，新增评论可见后才记录。
6. 从评论链或本地队列继续候选。同一个 run 持续到恰好 200 位，不按固定点赞数拆分。
7. 配额为 `120 exploit_first / 60 new / 20 retest`；确认点赞数可少于 200。

### 即时结算

1. 新触达当天封存后立即进入账本，初始为未反馈轻负样本。
2. 不创建新的 cycle、72 小时 episode 或未来 review Automation。
3. 下次启动扫描发现某摄影师的新点赞时，每张作品计 1 分并归到其最近触达，单次触达最多 3 分；该触达不再同时保留未反馈状态。
4. 原始分不封顶，有效分按 30 天半衰期衰减并封顶 12。

## 跨机器串行交接

1. GitHub 仓库保持私有，协作者使用自己的 GitHub 身份 clone。
2. 开始前 pull 并运行 `doctor`。tracked runs 或 checkpoint 不明确时禁止互动。
3. 同一账号同一时间只允许一台机器执行。
4. 运行达到终态并生成 sealed log 后，提交并推送新增 `runs/*.md`；另一台机器执行前再 pull。
5. 未封存 checkpoint 不进入 Git，只能在原机器恢复。另一台机器不得猜测动作或创建替代 run。
6. Dashboard 在每台机器本地重建。浏览器认证不迁移。

## 已验证故障与最短恢复

| 现象 | 判定 | 最短恢复 |
|---|---|---|
| `preview_changed` | 候选或批准事实发生变化 | 封存旧 approval run；重新 preflight，不伪造旧顺序 |
| 点赞数字可见但弹层条目为 0 | 异步加载或首次展开失败 | 刷新读取一次；仍为空记录 `liker_list_unavailable` |
| 最新 3 张只完成 1-2 张 | 扫描不完整 | 保留同一 checkpoint，只补缺失作品；不得把缺失作品按零反馈结算 |
| 候选主页第一张不可读 | 页面临时不可用 | 记录 `latest_work_unavailable`，计入覆盖后转下一位 |
| 第一张已经点赞 | 不应重复点赞 | 记录 `latest_work_already_liked`，计入覆盖后转下一位 |
| checkpoint 写入旧 run | 临时命令复用旧 ID | 写入前核对当前 `run_id`、`scan_id` 和绝对 state root，写后检查返回值 |
| Chrome 有进程但无法连接 | 没有可接管窗口或通信未建立 | 保留 runtime，打开 Chrome 窗口后最多重连一次 |
| 点赞或评论状态不明确 | 可能重复动作 | 立即 `safety_paused: ambiguous_state`，不重按 |

更细恢复步骤见 [`operational-recovery.md`](../.agents/skills/500px-feedback-growth/references/operational-recovery.md)。

## Legacy 状态

- 历史 100 赞、旧 200 覆盖、cycle、review 和 episode 日志只读保留；新流程不重写它们。
- 旧 success/failure/open 各映射一次到即时账本，旧 open 不再等待未来成熟。
- 已存在的旧回顾 Automation 不补跑；上线时停用仍 active 的未执行项，保留其历史记录。
- 旧日志数量和聚合计数不是永久测试常量；验证使用可重建不变量。

## 任务结束检查

1. 当前 run 已按真实状态封存，或明确保留 recoverable checkpoint。
2. `status --json` 的覆盖、点赞和评论与页面确认事件一致。
3. 最新 3 张扫描完整性明确；有 issue 时 Dashboard 显示“数据不完整”。
4. Dashboard 已从日志重建，而不是手工修改。
5. 不存在第 201 位摄影师，也没有重复动作或未确认互动。
6. 新 sealed log 已提交并推送；checkpoint、Dashboard 和认证信息仍未被 Git 跟踪。
