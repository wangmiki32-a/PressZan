# ADR-0003：在私有 Git 中版本化 sealed runs

- 状态：Accepted
- 日期：2026-08-16
- 适用范围：运行事实源、跨机器交接、路径解析和隐私边界
- 覆盖：ADR-0001 中“个人运行日志不进入 Git”和“固定机器绝对 state root”两项决定

> 修订说明：Git-backed sealed runs、动态 state root 和串行交接继续有效；“每个新周期创建一次性回顾 Automation”条款已由 [ADR-0005](ADR-0005-immediate-feedback-settlement.md) 替代。

## 背景

项目代码可以通过 Git 迁移，但历史触达、周期证据、摄影师分层输入和 42/58 成熟回馈结果只存在于原机器 `.local/500px-feedback-growth/runs/*.md`。另一位执行者 clone 后无法延续算法证据，文档中的机器绝对路径也不能复用。

用户选择把 sealed runs 以明文放入私有 Git，接受摄影师身份、作品链接、互动历史和回馈关系进入永久 Git 历史，以换取最直接、单一事实源的交接方式。

## 决定

1. Git 只跟踪 `.local/500px-feedback-growth/runs/*.md`；它们继续是 sealed source of truth。
2. `checkpoints/`、Dashboard、Automation、`.env`、Cookie、token、Chrome profile 和其他认证材料不进入 Git。
3. 仓库必须保持私有，只给实际执行者最小 collaborator 权限。
4. sealed logs 不得手工编辑、覆盖、移动或删除；新历史通过新增 sealed 文件进入 Git。
5. 默认状态根按 `--state-root`、`PRESSZAN_STATE_ROOT`、主仓库 `.local/500px-feedback-growth` 的顺序解析。
6. worktree 使用当前 checkout 做 Git 边界检查，但运行事实回到主仓库状态根。
7. 同一账号只允许串行执行：开始前 pull 和 `doctor`，封存后 commit/push 新 runs。
8. 未封存 checkpoint 只能在原机器恢复；不能通过 Git 在另一台机器猜测续跑。
9. Automation 不迁移。每个新周期由实际执行机器使用自己的 resolved state root 创建一次性回顾任务。

## 理由

- 直接跟踪 live sealed runs 保持单一事实源，没有 snapshot/import 双写。
- Markdown 日志仍可审计、增量 diff 和按 run 恢复。
- 精确 `.gitignore` 把长期证据与机器临时状态分开。
- 动态 resolver 消除用户名、clone 目录和 worktree 路径耦合。
- `doctor` 在页面互动前验证迁移包和 Git 边界，避免空历史或第二份状态静默运行。

## 后果

正面影响：

- 授权协作者 clone 后可直接重建历史分层、周期和 Dashboard。
- 新 sealed logs 能通过普通 Git 流程交接，不需要数据库或额外服务。
- 任意 clone 路径都使用同一套命令。

成本与限制：

- 摄影师互动明细永久存在于 Git 历史；仓库一旦公开，不能靠后续删除完全撤回。
- 两台机器不能并发执行同一账号，否则页面动作、配额和 checkpoint 无法安全合并。
- 未封存运行和已创建 Automation 仍依赖原机器。
- 每次完成运行后必须及时提交并推送新增 logs，另一位执行者必须先 pull。

## 验证

- `tests.test_workspace` 验证 clone、环境覆盖和 worktree 的路径解析。
- `tests.test_repository_state` 验证只有 sealed Markdown runs 被跟踪、日志逐字节一致并重建 42/58/0。
- `doctor` 验证日志 schema、聚合结果和 Git ignore 边界。
- 干净 clone smoke test 验证零硬编码路径执行 `doctor`、`status` 和 `dashboard`。
