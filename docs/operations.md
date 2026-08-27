# 运行与恢复手册

## 使用原则

真实互动通过 `$500px-feedback-growth` 执行。CLI 用于状态检查、事件持久化、恢复和 Dashboard 重建；浏览器动作遵守 skill 的可见状态确认和安全停机规则。

每次执行前完整读取：

- [`SKILL.md`](../.agents/skills/500px-feedback-growth/SKILL.md)
- [`browser-workflow.md`](../.agents/skills/500px-feedback-growth/references/browser-workflow.md)
- [`operational-recovery.md`](../.agents/skills/500px-feedback-growth/references/operational-recovery.md)
- [`quality.md`](quality.md)

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

`doctor/status` 后为本次任务实例化一次项目级只读 `feedback_supervisor`。子 agent 不可用时由主 Agent执行同格式审计，并在 `Verdict` 标记 `supervisor_degraded`；不得伪称独立监督。

| 状态 | 动作 |
|---|---|
| 本次任务已覆盖 200 位 | 停止，不处理第 201 位 |
| 存在 recoverable run | `resume --run-id <run_id>`，从最后确认事件继续 |
| Recoverable run 的 `paused_reason` 非空 | 停止外发动作，保留证据并按真实状态封存当前 run |
| 无 active run 但仍显示历史 `paused_reason` | 以当前 run 的 status、remaining quota 和事件为准；历史 paused_reason 只作提示，不阻塞已完成或新的安全 run，也不能用来静默解除 active pause |
| 首次尚未批准 | 执行只读 preflight，展示摘要并询问“确认执行？” |
| 本次任务有剩余覆盖且无 active run | 开始连续 run，执行到 200 位或安全终态 |

## 标准任务

### 最新 3 张反馈扫描

1. 用户手动完成上传和分享。
2. 从本人主页确认账号和最新 3 张公开作品。先写 `scan_started` 并确认成功落盘，再为同一 `scan_id` 逐张记录 `work_observed` 并完整读取点赞者；开始事件失败时停止，不得先写作品观察。
3. 首次读取某张作品只建立 baseline；后续扫描只对此前未见 pair 计分。
4. 每张加载失败最多刷新一次。仍失败写 `scan_issue`，不要把它列为 completed 或按零点赞处理。
5. 三张都读取完成后，只调用一次 `feedback-scan-complete`，在同一命令中重复 3 个 `--completed-photo-id`。部分完成 summary 仍保留为“数据不完整”事实，但 `preview` 会返回 `latest_three_scan_incomplete`；必须补齐缺失作品或以新 `scan_id` 完整重建，不能直接进入本轮互动。

### Preflight

最新 3 张反馈扫描之后，先复用这 3 张的点赞者和本地历史生成 preview。不得默认扫描最近 30 幅；候选不足时，从第 4 张本人作品开始按从新到旧顺序增量补充，每次只增加一个来源并重算 preview，达到 200 位即停止。最近 30 幅只是上限。不得点赞、评论、关注或私信。

每个新扩展来源也必须先写合法的 `scan_started` 并确认成功落盘，之后才写同一 `scan_id` 的 `work_observed` 和点赞者观察。不得使用 schema 未定义的 `purpose`；开始事件失败时停止且不补写作品观察。

长列表每批最多 10 位，每页观察立即追加事件。点赞数字存在但列表空白时只刷新一次；仍为空写 `scan_issue: liker_list_unavailable`。

Preflight 封存后、用户确认前，监督员先审计候选数量、首次 preview 充足度、重算/issue 和批准边界；监督员只输出建议，不批准互动。

### 首次批准

每个新 run 只确认一次，run 内不重复询问。用户回复“确认执行”后，skill 解析最新 preview；满足同一 `daily_task_id`、仍在 24 小时有效期且 preview 后没有确认互动，才快速复核。跨过日界线本身不会使 preview 失效。preview 失效时需要确认新的批准对象；运行时或平台强制确认不得绕过。

快速复核只打开候选计划中的唯一 `source_url`，并把本次复核的 `candidate_observed` 写入新的 approval run checkpoint 后再调用 `approve`；空 checkpoint 不得申请批准。稳定字段、顺序、配额或 digest 变化时返回 `preview_changed`，封存 `approval_rejected` 后重新 preflight。

### 点赞执行

1. 候选主页只检查可见“公开作品”语义区域中公开作品网格的第一张可见作品卡片。禁止用全页第一个、`main` 内第一个或通用 `/community/photo-details/` 链接替代；推荐、影集、相关内容和装饰图片一律排除。无法可靠定位公开作品网格时按 `latest_work_unavailable` 跳过，不回退通用选择器。
2. 身份校验要求账号正确、页面无阻断信号且候选主页 URL 含稳定摄影师 ID；作品页以上传者稳定 actor 链接或图片资源 URL 中的稳定摄影师 ID 满足任一作为正向 owner 证据。两者都缺失或任一可见证据冲突时安全暂停；vanity slug 不同或 CDN 路径缺少 ID 本身不是冲突。
3. 已点赞或不可读时记录 `candidate_skipped`，写入批准计划中的 `quota_bucket` 并计入对应策略桶与总覆盖；未点赞才点击。
4. 只接受同一控件 `before_state=not_liked -> after_state=liked`。
5. 页面确认后立即追加 `outgoing_like_confirmed`；新运行自动使用 `settlement_mode=immediate`。
6. 每次确认点赞后评论 `👍👍👍`；相同本人评论已可见时不重复，新增评论按本人稳定身份确认可见后，才以 `not_visible -> visible` 记录。
7. 从评论链或本地队列继续候选。同一个 run 持续到恰好 200 位，不按固定点赞数拆分。
8. 配额为 `120 exploit_first / 60 new / 20 retest`；确认点赞数可少于 200。
9. 浏览器每批最多 10 位；每批后读取同一 run 的覆盖和最后事件进行对账。批次只是工具与恢复边界，不创建新 run、不重新批准、不延迟逐动作 checkpoint。
10. 覆盖达到 50/100/150 位时只向同一 `feedback_supervisor` 发送压缩状态；普通 10 位 checkpoint 不启动新的监督模型。

### 即时结算

1. 新触达在任务封存后立即进入账本，初始为未反馈轻负样本。
2. 不创建新的 cycle、72 小时 episode 或未来 review Automation。
3. 下次启动扫描发现某摄影师的新点赞时，每张作品计 1 分并归到其最近触达，单次触达最多 3 分；该触达不再同时保留未反馈状态。
4. 原始分不封顶，有效分按 30 天半衰期衰减并封顶 12。
5. terminal 后先读取 sealed 事实生成效率 KPI，再由监督员完成最终审计和 Consolidation 判断；规则见 [`quality.md`](quality.md)。

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
| `latest_three_scan_incomplete` | 最新 3 张 summary 未同时确认 3 张完成 | 保留 checkpoint；补齐缺失作品或用新 `scan_id` 完整重建，再生成 preview |
| 点赞数字可见但弹层条目为 0 | 异步加载或首次展开失败 | 刷新读取一次；仍为空记录 `liker_list_unavailable` |
| 最新 3 张只完成 1-2 张 | 扫描不完整 | 保留同一 checkpoint，只补缺失作品；不得把缺失作品按零反馈结算 |
| 候选主页第一张不可读 | 页面临时不可用 | 记录 `latest_work_unavailable`，计入覆盖后转下一位 |
| 第一张已经点赞 | 不应重复点赞 | 记录 `latest_work_already_liked`，计入覆盖后转下一位 |
| 全页第一个作品链接属于推荐、影集或装饰内容 | 选择器范围漂移，未限定公开作品网格 | 仅取公开作品网格第一张作品卡片；无法定位时记 `latest_work_unavailable`，不得回退通用选择器，owner 校验仍作为独立安全门 |
| `work_observed` 早于同一扫描的 `scan_started` | 事件写入顺序错误 | 必须先让合法 `scan_started` 成功落盘；失败即停止，不写或补写 `work_observed` |
| 合法作品页缺少一种 owner 标记 | 页面存在两种已验证资源结构 | 核对上传者稳定 actor 链接与图片资源 URL 中的稳定摄影师 ID；满足任一且无冲突即可继续，两者都缺失或任一证据冲突则安全暂停 |
| checkpoint 写入旧 run | 临时命令复用旧 ID | 写入前核对当前 `run_id`、`scan_id` 和绝对 state root，写后检查返回值 |
| Chrome 有进程但无法连接 | 没有可接管窗口或通信未建立 | 保留 runtime，打开 Chrome 窗口后最多重连一次 |
| 点赞或评论状态不明确 | 可能重复动作 | 立即 `safety_paused: ambiguous_state`，不重按 |
| 浏览器调用超时或结果无法解析 | 页面可能已变化，外层没有可靠结果 | 先对账，再决定是否补动作；检查页面、最近 action 和 checkpoint，禁止盲目重按 |
| 页面动作已确认但 CLI 写入中断 | 子进程环境、编码或调用通道失败 | 立即停批；同一页面 after state 明确且 checkpoint 缺 action 时只补记一次，否则安全暂停；子进程须继承环境并先用 `status --json` 验证 |
| GitHub TLS handshake 间歇失败 | Meta Tunnel/fake-IP 或直连链路抖动 | 不得关闭 SSL 校验；验证本地代理后仅设置仓库级 `http.proxy`，端口不得跨机器硬编码 |

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
7. 最终监督审计已完成；若降级则明确记录 `supervisor_degraded`，触发的低风险文档/Skill/测试改进已验证。

## GitHub 网络恢复

1. `fetch`/`push` 出现 TLS handshake 错误时，先运行 `git ls-remote origin HEAD` 做最小只读探测；不要反复执行写操作。
2. 若启用了 Clash/Meta Tunnel 或 DNS fake-IP，确认本机实际监听的 HTTP 代理端口，再用临时 `git -c http.proxy=http://127.0.0.1:<PORT> ls-remote origin HEAD` 验证。
3. 临时路径稳定后才写仓库级 `http.proxy`。不得关闭 SSL 校验，不得把本机端口写入共享配置或文档常量。
4. 推送后再次 fetch 并比较远端 HEAD；偶发抖动使用少量有界重试，不无限循环。
