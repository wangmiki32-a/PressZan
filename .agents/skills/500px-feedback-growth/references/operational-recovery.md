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

## 避免重复扫描

同日新鲜 preview 已包含完整 30 幅作品、点赞来源和评论候选。首次批准时：

1. 从 preview 读取 25 位 `candidate_plan`。
2. 按 `source_url` 分组并访问每个唯一来源一次。
3. 只追加仍可见的批准候选，不追加其他评论者，也不打开点赞者列表。
4. 执行 `approve` 校验 digest；通过后立即进入候选主页。

完整 30 幅扫描只属于 preflight。批准复核再次扫描 30 幅既增加延迟，也会把页面分钟级变化引入候选池。

## 页面与日志不变量

- 每次页面变化后重新读取可见状态；不保存 element index 或坐标。
- `outgoing_like_confirmed` 必须紧跟同一控件的 `not_liked → liked` 读取。
- 普通加载失败最多刷新一次；CAPTCHA、限频、登录失效、平台警告或状态不明立即 `safety_paused`。
- retry 只补缺失数据：点赞者重读不重复追加候选，候选重读不覆盖已记录的稳定观察。
- 浏览器工作完成前不要 finalize；需用户继续时用 `handoff` 保留 500px 页面。
