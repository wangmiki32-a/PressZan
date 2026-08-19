# 500px 运行恢复手册

先保留当前 checkpoint，再做一次最小恢复；不要从头重跑已确认步骤。

## 快速路由

| 现象 | 原因判断 | 最短处理 |
|---|---|---|
| Chrome 进程存在但扩展连接失败 | 没有可接管窗口或扩展通信未建立 | 保留 runtime，打开 Chrome 窗口后重连；读取 Chrome troubleshooting 后最多重试一次 |
| 点赞数字可见、弹层条目为 0 | 异步加载或首次展开失败 | 刷新读取一次；仍为 0 写 `scan_issue: liker_list_unavailable` |
| 最新 3 张只完成 1-2 张 | 页面或工具中断 | 保留同一 preflight checkpoint；恢复后只补缺失作品，再执行一次 `feedback-scan-complete` |
| 最新作品更换 | 本人主页展示发生变化 | 以本次 scan 冻结的 3 个 `photo_id` 完成本次扫描；下一次启动重新读取最新 3 张 |
| 扫描缺少作品 | 不完整证据 | 只把实际完整读取的作品列入 `--completed-photo-id`；Dashboard 显示“数据不完整” |
| 批准返回 `preview_changed` | 候选、顺序或 quota snapshot 已变化 | 封存为 `approval_rejected` 并重新 preflight；不得继续旧名单 |
| worktree 出现独立 `.local` | 状态根漂移 | 运行 `doctor`；默认 resolver 必须回到主仓库，必要时用 `PRESSZAN_STATE_ROOT` 指向唯一状态根 |
| `doctor` 返回 `untracked_sealed_runs` | 当前状态未提交或 clone 未拉到最新 runs | 不进入页面；确认无未封存 checkpoint 后提交或 pull sealed runs |
| checkpoint 事件落入旧运行 | 临时脚本复用旧 ID | 每次写入前核对 `run_id`、`scan_id`、`state-root`，写后检查返回 position |
| `resume` 返回 `run_not_recoverable` | run 已 sealed 或不是当前 active run | 回到 `status --json`；只恢复返回的 recoverable run |
| `begin` 返回 `stale_recoverable_run` | active run 跨过 Asia/Shanghai 日界线 | 不追加旧日动作；封存为 `paused_incomplete` 后开始新日任务 |

## 扫描恢复

1. `scan_started.purpose=latest_three_feedback` 标识启动反馈扫描；同一 `scan_id` 必须保持同一组 3 张作品。
2. 每张作品的 `work_observed`、`received_like_observed` 和 `scan_issue` 都只追加。恢复时先从 checkpoint 找出已完整读取和缺失的作品。
3. 点赞者列表为空只有两种合法表达：已确认零点赞并把作品列为 completed，或读取失败写 `scan_issue` 且不列为 completed。
4. `feedback_scan_completed` 只能写一次。若 CLI 返回 `invalid_feedback_scan`，检查 completed ID 是否属于本次 3 张、是否重复或已经完成；不得手工修摘要计数。
5. 首次完整出现的 `photo_id` 自动成为 baseline。恢复时不得把 baseline 作品现有点赞重写成新增反馈。

## Preview 与连续日任务恢复

- 同日新鲜 preview 的批准只复核候选计划中的唯一来源页，不重复最新 3 张扫描或完整 30 幅候选扫描。
- 正常运行不按固定动作数切分；一个 run 持续到恰好覆盖 200 位不同摄影师。
- 每个确认动作独立 checkpoint；中断时恢复同一 `run_id`，不重放已确认动作。
- Sealed 后 retained checkpoint 只作审计；CLI 拒绝继续 append 或 resume。
- 浏览器连接丢失但未出现安全警告时保留 active checkpoint；重连后先读 `resume` 输出和页面当前状态。
- 只有覆盖 200 位、安全暂停或候选耗尽时才封存；未知中断不得伪写 `completed`。
- 跨日 active run 不结转额度，需先封存旧日未完成状态。

## 跨机器恢复边界

- 同一账号只能串行执行：新机器开始前 pull 并通过 `doctor`，原机器生成 sealed log 后先 commit/push。
- Git 只同步 sealed runs。未封存 checkpoint 不进入 Git，只能在创建它的原机器恢复。
- 原机器不可用且存在未封存运行时，不得在另一台机器创建替代 run 或猜测动作；保持暂停并人工核对页面。
- Dashboard 在各机器从日志重建。Automation 不随 Git 迁移；新流程也不再创建未来回顾 Automation。
- 固定账号身份检查不随执行者变化；朋友必须登录同一账号，账号不匹配立即停止。

## 旧日志兼容

- 历史 cycle、baseline、review 和 episode 事件继续由解析器只读重建，不能修改 sealed log。
- 旧 success、failure、open 各映射一次到新积分账本；不会因为旧周期仍 open 而阻止新运行。
- 已存在的旧回顾 Automation 不补跑、不重建；上线时只停用尚未执行的 active 任务，保留历史记录。

## 页面与日志不变量

- 页面变化后重新读取可见状态；不保存 element index 或坐标。
- `outgoing_like_confirmed` 必须紧跟同一控件的 `not_liked -> liked` 读取。
- 普通加载失败最多刷新一次；CAPTCHA、限频、登录失效、平台警告或状态不明立即 `safety_paused`。
- retry 只补缺失数据，不重复追加候选或覆盖稳定观察。
- 浏览器工作完成前不要 finalize；需用户继续时保留当前 500px 页面。
