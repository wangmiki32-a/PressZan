# 500px 运行恢复手册

本文件记录可复用的故障判定与最短恢复路径。先保留当前 checkpoint，再做一次最小恢复；不要从头重跑已确认步骤。

## 快速路由

| 现象 | 原因判断 | 最短处理 |
|---|---|---|
| Chrome 进程存在但扩展连接失败 | 没有可接管窗口或扩展通信未建立 | 保留现有 runtime，打开一个 Chrome 窗口后重连；读取 Chrome troubleshooting 后最多重试一次 |
| `chrome.tabs.list()` 有目标页，但旧 tab binding 不确定 | 上轮 finalize 后页面仍存在 | 优先复用已保存的 tab binding；不要调用不存在的 `chrome.tabs.claim` |
| 导航后等待方法报不存在 | 使用了未支持的包装 API | `goto()` 已等待导航；不要调用 `playwright.domcontentloaded`，后续用可见 DOM 条件读取 |
| 点赞数字可见、弹层条目为 0 | 弹层异步加载或首次展开失败 | 刷新读取一次；仍为 0 才追加 `scan_issue: liker_list_unavailable` |
| 评论区首次为 0、历史或当前页面明显有评论 | 评论异步加载或点赞弹层遮挡 | 关闭弹层/重新导航并只刷新一次；先读评论候选，再打开点赞弹层 |
| 批准返回 `preview_changed` | 候选、顺序或 quota snapshot 已变化 | 封存为 `approval_rejected`，生成新 preflight；不得继续旧名单 |
| 主分支与 worktree 都有 `.local` | 状态根目录漂移 | 所有 CLI 显式使用主工作区绝对 `--state-root`，不得从 worktree 继续累计 |
| checkpoint 事件落入旧运行 | 临时脚本写死旧 `run_id` | 每次生成脚本后先核对 `run_id`、`scan_id`、`state-root`；每页写入后检查返回 position |
| `resume` 返回 `run_not_recoverable` | run 已 sealed、ID 过期或不是当前 active run | 回到 `status --json`；只恢复返回的 recoverable run，不向 retained checkpoint 追加 |
| `begin` 返回 `stale_recoverable_run` | 旧日 active run 跨过 Asia/Shanghai 日界线 | 不 resume、不追加动作；封存旧日为 `paused_incomplete`，再开始新日任务 |
| Automation 已创建但 `review_scheduled` 未写入 | host create 与日志 bind 之间中断 | 按确定性任务名读取现有任务；payload digest 一致则补 bind，不一致立即停止 |
| 回顾只完成 1-4 张 | 页面或工具中断 | 保留相同 `(cycle_id, review_kind, attempt)` checkpoint，跨日仍可 resume，只补缺失作品 |
| `+70h` 已完成但 episode 未到 72 小时 | 观察时间与成熟时间不同 | 保持 open；到 expiry 后通过下一次 status/dashboard 重建派生 failure |

## 避免重复扫描

同日新鲜 preview 已包含完整 30 幅作品、点赞来源和评论候选。首次批准时：

1. 从 preview 读取当天剩余额度对应的完整 `candidate_plan`。
2. 按 `source_url` 分组并访问每个唯一来源一次。
3. 只追加仍可见的批准候选，不追加其他评论者，也不打开点赞者列表。
4. 执行 `approve` 校验 digest；通过后立即进入候选主页。

完整 30 幅扫描只属于 preflight。批准复核再次扫描 30 幅既增加延迟，也会把页面分钟级变化引入候选池。

## 连续日任务恢复

- 正常运行不按固定动作数切分；一个 run 持续到当日累计 100。
- 每个确认动作已经独立 checkpoint，因此中断时恢复同一 `run_id`，不需要从头重放候选。
- Sealed 后 retained checkpoint 只作审计证据；CLI 拒绝继续 append 或 resume。
- 浏览器连接丢失但未出现安全警告时保留 active checkpoint；重新连接后先读 `resume` 输出和页面当前状态。
- 只有确认达到 100、安全暂停或候选耗尽时才封存 run。未知中断不得伪写 `completed`。
- 跨日 active run 是例外：旧日额度不结转，`resume`/`event` 返回 `daily_task_expired`，需封存旧日未完成状态后再开始新日。

## 临时回顾任务恢复

- 每个点赞周期只创建两个一次性只读任务：`+20h review_1d` 和 `+70h review_3d`；不得创建周期性轮询任务。
- Automation 只携带 `cycle_id`、`review_kind`、`attempt`、`due_at` 和主工作区绝对 `state_root`，不携带作品、摄影师、Cookie 或页面认证数据。
- 任务执行前先匹配 schedule intent 的 payload digest；同名同 payload 是幂等恢复，同名不同 payload 是冲突，禁止覆盖。
- Review checkpoint 可以跨 Asia/Shanghai 日界线恢复，但只能恢复完全相同的 cycle/kind/attempt。每张冻结作品的完整 liker 列表写入一次，5/5 后才完成。
- 回顾失败要写 `review_failed` 并通知用户；不得把未扫描作品当作零点赞。用户明确授权 retry 后使用 `attempt+1` 新建一次性任务。
- 当前历史迁移已经人工完成 1 日回顾时，只补建一次 +70h 任务；1 日 slot 只做事件映射，不创建 Automation。

## 页面与日志不变量

- 每次页面变化后重新读取可见状态；不保存 element index 或坐标。
- `outgoing_like_confirmed` 必须紧跟同一控件的 `not_liked → liked` 读取。
- 普通加载失败最多刷新一次；CAPTCHA、限频、登录失效、平台警告或状态不明立即 `safety_paused`。
- retry 只补缺失数据：点赞者重读不重复追加候选，候选重读不覆盖已记录的稳定观察。
- 浏览器工作完成前不要 finalize；需用户继续时用 `handoff` 保留 500px 页面。
