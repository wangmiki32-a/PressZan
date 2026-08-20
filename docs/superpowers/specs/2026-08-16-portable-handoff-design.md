# PressZan 可迁移交接设计

- 状态：Implemented / Partially Superseded；Git-backed 交接仍有效，未来回顾 Automation 条款由 ADR-0005 替代
- 日期：2026-08-16
- 适用范围：仓库状态边界、路径解析、朋友交接、Git 发布

## 背景与目标

当前仓库的 skill、代码和文档可以通过 Git 获取，但真实运行事实位于被整体忽略的 `.local/500px-feedback-growth/`，并且执行文档要求使用当前维护者机器的绝对路径。朋友即使 clone 仓库，也拿不到历史触达、42/58 回馈结果、摄影师分层和周期证据；照抄命令还会把状态写到不存在的目录。

本次改造要让同一 500px 账号可以在另一台 Mac 上延续执行，同时保留以下不变量：

- sealed Markdown run log 仍是唯一长期运行事实源；
- checkpoint、Dashboard 和认证信息仍是机器本地派生或临时状态；
- 不复制密码、Cookie、token、Chrome profile 或 Codex Automation；
- 换目录、换用户名或从 GitHub clone 后无需修改源码中的绝对路径；
- 当前历史聚合在迁移前后保持一致。

用户明确选择把 sealed runs 以明文提交到私有 Git 仓库，并接受摄影师身份、互动历史和回馈关系进入永久 Git 历史。仓库不得改为公开。

## 方案比较

### 方案一：直接版本化 live sealed runs（采用）

Git 直接跟踪 `.local/500px-feedback-growth/runs/*.md`，运行时继续从同一目录读取和生成 sealed logs。这样只有一个事实源，没有导入、导出或双份同步。

优点是 clone 后即可重建完整状态，新产生的 sealed log 也能自然提交；缺点是每次完成运行后工作区会出现待提交日志，而且两台机器必须通过 pull/commit/push 串行交接。

### 方案二：维护 `state/` 快照并导入

把 runs 复制到仓库内独立快照目录，clone 后再导入 `.local`。它让运行目录保持完全私有，但制造两个事实源，容易漏同步或覆盖较新的日志，因此不采用。

### 方案三：提交单个归档包

把状态压缩成一个文件。它方便下载，但不利于审计、增量 diff、冲突判断和单日志恢复，也不符合 Markdown 日志可读性的原始设计，因此不采用。

## 状态版本化边界

`.gitignore` 改为精确边界：

- 跟踪：`.local/500px-feedback-growth/runs/*.md`；
- 忽略：`.local/500px-feedback-growth/checkpoints/`；
- 忽略：`.local/500px-feedback-growth/dashboard.html` 及其他派生文件；
- 忽略：`.env*`、浏览器认证数据、缓存、临时工作树和编辑器文件。

tracked runs 必须保持 append-only 和 sealed-only。不得手工编辑、覆盖、重命名或删除已经进入 Git 的日志。摄影师分层、42/58 结果、周期状态和 Dashboard 继续从这些日志重建，不提交第二份聚合缓存。

现有全部 sealed runs 一次性加入 Git。提交前用当前 CLI 验证 schema、重复 action、周期重建和聚合结果；迁移提交不得改写日志内容或时间。

## 可迁移路径解析

CLI 使用统一的状态根解析顺序：

1. 显式 `--state-root PATH`，用于测试、恢复和高级覆盖；
2. 环境变量 `PRESSZAN_STATE_ROOT`，用于特殊安装或外部调度；
3. 从 skill 脚本实际位置向上定位仓库根，再使用 `<repo>/.local/500px-feedback-growth`。

默认调用不再要求 `--state-root`。解析后的路径必须是绝对路径，CLI 状态输出和 Automation payload 可以记录该机器当时的绝对路径，但源码、长期操作文档和示例不得出现特定用户目录。

项目 worktree 不得创建第二份运行事实。路径解析发现当前代码位于 `<repo>/.worktrees/<name>` 时，应回到主仓库 common Git directory 对应的工作区，或者要求显式 `PRESSZAN_STATE_ROOT`；实现必须有测试证明不会静默写入 worktree 自己的 `.local`。

## 交接与并发合同

朋友的标准流程为：

1. 获得私有 GitHub 仓库 collaborator 权限并 clone 仓库；
2. 用 Codex 打开仓库根目录；
3. 在自己的 Chrome 中手动登录同一 500px 账号；
4. 运行迁移自检，确认仓库、状态日志、账号约定和 Python 兼容性；
5. 执行 `$500px-feedback-growth`；
6. 运行结束并产生 sealed log 后，提交并推送新增 runs；
7. 另一位执行者下次开始前先 pull。

同一账号禁止两台机器并发执行。开始真实互动前，skill 除现有 `status --json` 外还要检查 Git 工作区中 tracked runs 是否与 `HEAD` 一致、当前分支是否落后远端（能检查时）以及是否存在 recoverable checkpoint。无法确认同步状态时不开始新 run，并给出明确恢复动作。

checkpoint 是机器本地恢复证据，不随 Git 迁移。若任务在封存前中断，只能在原机器恢复；不得在另一台机器根据不完整信息重建动作。Dashboard 是派生物，clone 后通过命令重建。

Codex Automation 也是机器本地资源，不进入 Git。每轮点赞完成后，由实际执行机器按 resolved state root 创建新的 +20h/+70h 一次性回顾任务。clone 历史仓库不会重建已完成或过期 Automation。

## 迁移自检

新增只读 `doctor` 命令，至少报告：

- repository root 与 resolved state root；
- Python 版本是否满足 3.9+；
- runs 数量、schema 是否完整、是否存在重复或损坏日志；
- 当前周期、最近执行和成熟回馈聚合摘要；
- tracked runs 是否被 `.gitignore` 意外排除；
- checkpoint、Dashboard 和敏感文件是否仍被忽略；
- 是否检测到 worktree 状态漂移风险。

`doctor` 不读取 Chrome 凭证，不产生 500px 页面互动，也不修改日志。账号登录和页面兼容性仍由 skill 的浏览器 preflight 验证。

## 文档与规则调整

- `AGENTS.md`：把“状态绝不进入 Git”和“必须写死主工作区绝对路径”替换为精确版本化边界、动态解析和串行交接规则。
- `README.md`：使用零路径参数命令，增加 clone、授权、自检、执行和同步的最短交接流程。
- `docs/architecture.md`：说明 Git-backed sealed event store、机器本地 checkpoint 和派生视图边界。
- `docs/operations.md`：替换固定路径章节，加入两台机器的串行 handoff、未封存中断和 Automation 限制。
- `SKILL.md` 与 references：所有内部命令使用 resolver；真实互动开始前执行迁移/同步检查；保留固定账号身份验证。
- 新增 ADR：记录明文 sealed runs 进入私有 Git、隐私后果及禁止公开仓库的决定。
- 历史 spec 和 plan 保持历史记录，不批量重写其中已经发生过的命令；只在当前权威文档中明确其已被新决策覆盖。

## 测试与验收

实施采用测试先行，至少覆盖：

1. 任意 clone 路径下默认解析 `<repo>/.local/500px-feedback-growth`；
2. `--state-root` 优先于环境变量，环境变量优先于默认路径；
3. worktree 不会静默创建第二份事实源；
4. `.gitignore` 只允许 `runs/*.md`，仍拒绝 checkpoint、Dashboard、env 和凭证类文件；
5. `doctor` 对正常迁移包成功，对损坏日志、路径漂移和忽略规则错误明确失败；
6. 当前 committed runs 在干净 checkout 中能重建与迁移前一致的 42 success、58 failure、0 open；
7. 完整单元测试、skill contract、结构验证和 `git diff --check` 通过；
8. 从临时 clone 运行 `status`、`doctor`、`dashboard`，确认无需本机硬编码路径；
9. Git staged 内容中不包含 checkpoint、Dashboard、`.env`、Cookie、token 或浏览器 profile。

## 发布

实现先在 `codex/portable-handoff` 分支完成并验证，再合并到 `main`，提交后推送到 `origin/main`。推送前必须恢复有效 GitHub 认证，并再次确认远端仓库保持 private。

交付时报告：提交哈希、远端分支状态、tracked runs 数量、42/58 聚合校验、未迁移的机器本地资源，以及朋友首次执行的最短步骤。
