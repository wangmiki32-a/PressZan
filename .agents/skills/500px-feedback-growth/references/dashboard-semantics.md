# Dashboard 统计语义

本文件定义 Dashboard 的数据口径和图表选择。重建或解释 Dashboard 前先读本文件；展示层不能自行发明指标。

## 回顾基线

- 有 cycle 日志时，Dashboard 的权威回顾对象是最新 `cycle_like_completed` 周期及其冻结 5 张作品；不得再用最近 30 张中的其他作品补充回馈。
- 没有 cycle 日志时，`最近执行` 兼容为不晚于当前时间、且至少有 1 个 `outgoing_like_confirmed` 的最新 `daily_task_id`。
- 只有 preflight 或 0 次确认点赞的日期不成为回顾基线。
- 新执行一旦产生确认点赞，立即替换旧基线；顶部、episode 结果和首次观察延迟同步切换到新 cohort。
- 历史 Tab 为日志状态已封存完成的日期生成；新运行需要覆盖恰好 200 位不同摄影师，旧 100 赞完成任务保持可见。

## 指标定义

| 指标 | 单位与计算 | 注意事项 |
|---|---|---|
| 确认点赞 | 回顾日 `outgoing_like_confirmed` 数 | 必须有页面 `not_liked → liked` 证据 |
| 覆盖摄影师 | 回顾日点赞与跳过事件中不同 `photographer_id` 的并集 | 新任务以 200 位为完成条件；已点赞或不可读的第一张作品会跳过但仍计入覆盖 |
| 归因回馈 | `last_touch_at` 位于回顾日且 outcome 为 `success` 的独立 episode 数 | 同一摄影师同一窗口内多幅回赞只计 1 |
| 观察窗口中 | 同一 cohort 中 outcome 为 `open` 的 episode 数 | 72 小时未结束，不能提前判失败 |
| 窗口成熟未回馈 | 同一 cohort 中 outcome 为 `failure` 的 episode 数 | 只有完整 episode expiry 到达后才成熟 |
| 30 天独立回馈率 | 最近 30 天成熟 episode 中，独立成功摄影师数 ÷ 对应确认点赞数 | 延长过的 episode 以最新 `expires_at` 判断成熟；分子按摄影师去重，分母按触达计数 |
| 首次观察延迟 | `feedback_first_seen_at - last_touch_at` | 页面不提供精确发生时间，只能解释为观察延迟 |

Cycle 中的 success/failure 必须读取 `eligible_episode_evidence`：只接受冻结 5 张中的 observation，排除 baseline pair、abandoned cycle 和 `attribution_eligible=false`。旧 `feedback_episode_succeeded` 保留审计价值，但一旦 episode 被 cycle 映射，就不能直接驱动层级、Beta、KPI、趋势或 Dashboard。

Dashboard 分别显示 `+20h review_1d` 和 `+70h review_3d` 的 due/status/resolved 时间；两次任务都是只读观察，槽位互相独立。

+70h 不等于 72 小时成熟：它是最后一次主动页面观察。某个 episode 只有在自己的 `expires_at <= now` 后，才从 open 派生为 failure；Dashboard 在下一次重建时反映该成熟尾差。

`归因回馈 / 观察窗口中 / 窗口成熟未回馈` 是互斥结果，不是包含关系。`Verified` 是摄影师滚动分层，也不是漏斗阶段。

## 趋势归属

- 回馈归入 episode 的 `last_touch_at` 所在执行日，不归入扫描发现回馈的日期。
- Preflight-only 日期不生成 0/0 趋势点。
- 同一天多个旧式 run 聚合为一个执行日；run 状态按 `ended_at` 和 `run_id` 的确定性顺序取最新，不依赖文件遍历顺序。

## 图表选择

| 可用执行日 | 图表 | 原因 |
|---|---|---|
| 0 | 空状态 | 没有可比较数据 |
| 1 | 双柱对比 | 单个时间点不能形成趋势 |
| 2-7 | 分组柱状图 | 离散批次比较比短折线诚实 |
| 至少 8 | 双折线 | 时间点足以观察形状和变化 |

Episode 结果使用 100% 堆叠条和精确值列表；首次观察延迟使用从零开始的水平柱。所有图表直接标数值，单一蓝色强调，其余使用中性色，默认浅色主题。

## 发布前检查

1. 顶部日期是最近执行日，不是 Dashboard 生成日。
2. 顶部覆盖数、确认点赞数和评论数与 sealed 日志中的同日事件一致。
3. Episode 三种结果相加等于该 cohort 的 episode 数。
4. 成熟 KPI 不包含 `expires_at > now` 的 episode。
5. 只有 1 个执行日时不得出现折线路径。
6. 页面默认 `data-theme="light"`，切换按钮可进入深色并返回浅色。
7. 不展示事件模型无法重建的字段；尤其不得伪造层级变化、真实点赞时间或严格因果。
8. 有 cycle 时显示冻结作品数必须为 5，1 日/3 日槽位互相独立，逐作品新增 liker 必须排除 baseline。
