# Markdown 事件 schema

状态根目录默认为项目 `.local/500px-feedback-growth/`。`checkpoints/*.md` 只追加；`runs/*.md` 是 sealed source of truth。每段规范数据使用一个 `json` fenced block，`schema_version` 当前为 `1`。

`runs/*.md` 是 Git-backed sealed event store，只进入私有 Git；已提交日志不得编辑、覆盖、移动或删除。`checkpoints/*.md` 只在创建它的机器保存，不进入 Git；Dashboard、Automation 和浏览器认证也不是事件状态。跨机器只通过 pull/commit/push 交接 sealed runs，不复制 active checkpoint。

## 通用格式

```json
{"kind":"candidate_skipped","occurred_at":"2026-08-12T12:00:00+08:00","data":{"photographer_id":"p1","reason":"all_recent_works_liked"}}
```

时间必须带时区。通过 CLI `event --run-id <run_id> --kind <kind> --field key=value` 写入；CLI 会把合法 JSON 值解析成数组、对象、数字或布尔值。不要直接编辑已写事件。

## 事件字段

| kind | 必填 data 字段 | 可选字段 |
|---|---|---|
| `scan_started` | `scan_id`, `owner_id`, `profile_url` | `purpose`；新启动反馈扫描为 `latest_three_feedback` |
| `work_observed` | `scan_id`, `photo_id`, `photo_url`, `position` | — |
| `received_like_observed` | `scan_id`, `photo_id`, `work_position`, `photographer_id`, `display_name`, `profile_url` | — |
| `candidate_observed` | `photographer_id`, `display_name`, `profile_url`, `source_photo_id`, `source_url`, `page_order` | — |
| `scan_issue` | `scan_id`, `photo_id`, `reason`, `evidence_summary` | — |
| `feedback_scan_completed` | `scan_id`, `photo_ids`, `completed_photo_ids`, `baseline_photo_ids`, `new_pair_count`, `new_feedback_photographer_count`, `new_feedback_points`, `completed_at` | — |
| `preview_created` | `preview_id`, `candidate_digest`, `expires_at`, `seed`, `quota_snapshot`, `candidate_ids`, `candidate_plan` | — |
| `onboarding_approved` | `preview_id`, `candidate_digest`, `approved_at` | — |
| `outgoing_like_confirmed` | `action_id`, `photographer_id`, `photo_id`, `photo_url`, `quota_bucket`, `before_state`, `after_state` | `settlement_mode`；新运行为 `immediate` |
| `outgoing_comment_confirmed` | `action_id`, `photographer_id`, `photo_id`, `content`, `before_state`, `after_state` | — |
| `feedback_episode_opened` | `episode_id`, `photographer_id`, `touch_action_id`, `expires_at` | — |
| `feedback_episode_extended` | `episode_id`, `touch_action_id`, `previous_expires_at`, `expires_at` | — |
| `feedback_episode_succeeded` | `episode_id`, `received_photo_id`, `feedback_first_seen_at`, `received_like_count` | — |
| `feedback_episode_failed` | `episode_id`, `expired_at` | — |
| `candidate_skipped` | `photographer_id`, `reason` | `photo_id` |
| `safety_paused` | `reason`, `page_url`, `evidence_summary`, `last_safe_action_id` | — |
| `run_finished` | `status`, `confirmed_like_count`, `confirmed_comment_count` | — |

### Legacy cycle 与回顾事件

以下事件只用于读取旧 sealed logs；新运行不再生成 cycle、episode 或 review 调度。

| kind | 必填 data 字段 |
|---|---|
| `cycle_started` | `cycle_id`, `attribution_eligible` |
| `cycle_showcase_observed` | `cycle_id`, `photo_id`, `photo_url`, `owner_id`, `visibility`, `position`, `evidence_summary` |
| `cycle_showcase_frozen` | `cycle_id`, `photo_ids`, `showcase_digest` |
| `cycle_baseline_scan_started` | `cycle_id`, `scan_id` |
| `cycle_baseline_like_observed` | `cycle_id`, `scan_id`, `photo_id`, `photographer_id`, `display_name`, `profile_url` |
| `cycle_baseline_photo_completed` | `cycle_id`, `scan_id`, `photo_id`, `liker_count` |
| `cycle_baseline_completed` | `cycle_id`, `scan_id`, `baseline_digest` |
| `cycle_run_bound` | `cycle_id`, `run_id`, `baseline_digest`, `bound_at` |
| `cycle_like_completed` | `cycle_id`, `mapped_run_ids`, `touch_action_ids`, `episode_ids`, `like_completed_at`, `terminal_status` |
| `review_schedule_requested` | `cycle_id`, `review_kind`, `attempt`, `due_at`, `state_root`, `automation_name`, `payload_digest` |
| `review_scheduled` | `cycle_id`, `review_kind`, `attempt`, `automation_id`, `payload_digest` |
| `review_started` | `cycle_id`, `review_kind`, `attempt`, `due_at`, `started_at` |
| `review_photo_observed` | `cycle_id`, `review_kind`, `attempt`, `scan_id`, `photo_id`, `photographer_ids`, `observed_at` |
| `review_completed` | `cycle_id`, `review_kind`, `attempt`, `scan_id`, `completed_at` |
| `review_failed` | `cycle_id`, `review_kind`, `attempt`, `reason`, `failed_at` |
| `review_superseded` | `cycle_id`, `review_kind`, `attempt`, `superseded_at` |
| `cycle_abandoned` | `cycle_id`, `reason`, `abandoned_at` |
| `cycle_attribution_scope_mapped` | `cycle_id`, `mapped_run_ids`, `showcase_photo_ids`, `touch_action_ids`, `episode_ids`, `observation_refs`, `attribution_eligible`, `mapping_digest` |

## ID 与即时生命周期

- 点赞 `action_id = sha256(daily_task_id + photographer_id + photo_id + "outgoing_like_confirmed")`。
- 新 `outgoing_like_confirmed.settlement_mode=immediate` 不创建 episode，当天即成为未反馈轻负样本。旧事件缺少该字段时按 `legacy` 解析并保留原 episode 生命周期。
- 启动反馈扫描固定本人最新 3 张。首次完整扫描的 `photo_id` 进入 `baseline_photo_ids`；以后相同作品的新 pair 逐条计分，同一触达最多 3 分。
- `feedback_scan_completed` 的三个 ID 列表必须无重复；`completed_photo_ids` 是 `photo_ids` 子集，`baseline_photo_ids` 是 completed 子集。三个计数字段必须是非负整数，`completed_at` 必须带时区。
- 不完整扫描也写 summary，但只列实际完成的作品；缺失作品必须有 `scan_issue`，不能按零点赞解释。
- CLI 从事件重算 `feedback_scan_completed` 全部派生字段并逐项校验；不得手工编辑计数或直接写积分。
- 相同 action ID 会被拒绝；sealed run 与保留 checkpoint 同时存在时，重建只采用 sealed run。
- Sealed run 不可恢复，也不可继续向 retained checkpoint 追加事件；`resume` 只接受当前 effective state 中的 active run。
- Legacy `transaction_context` 保持可读：cycle/run 用 `cycle_id`，review 用 `cycle_id + review_kind + attempt`。
- List 字段必须是无重复的非空字符串序列。`review_photo_observed.photographer_ids=[]` 与实际 `liker_count=0` 配套表示“已完整扫描且无人点赞”。

## 安全值

- `before_state` / `after_state` 必须来自同一可见控件的前后读取。
- 新运行的 `quota_bucket` 仅用 `exploit_first`、`new`、`retest`；旧 `verified_second` 只读兼容。
- 新运行的摄影师覆盖由 `outgoing_like_confirmed` 与 `candidate_skipped` 中不同 `photographer_id` 的并集重建；同一摄影师每天只计一次，恰好 200 位才完成。历史日志中的 `verified_second` 继续可读，新运行不再生成该桶。
- 新运行每次确认点赞后使用 `outgoing_comment_confirmed` 记录可见的固定评论 `👍👍👍`；历史评论内容保持原样，不补写、不迁移。
- `safety_paused.reason` 使用可搜索值：`captcha`、`rate_limit`、`login_lost`、`platform_warning`、`account_mismatch`、`ambiguous_state`。
- `scan_issue.reason` 用于一次刷新后仍无法读取的只读页面：例如 `liker_list_unavailable`；它不代表账号被安全暂停。
- 日志不得包含密码、Cookie、token、local storage、私信正文或无关个人资料。
