# PressZan 项目知识整合设计

- 状态：Approved
- 日期：2026-08-20
- 范围：项目级长期规则、文档职责、历史设计状态和防漂移验证
- 不变边界：不修改点赞业务逻辑、事件数据、sealed logs、Dashboard 数据或浏览器执行行为

## 背景

项目已经具备 `AGENTS.md`、README、架构、运行手册、知识缺口、ADR、Skill references 和历史 Superpowers 设计稿，但同一规则仍在多个入口重复出现。部分旧设计仍标为 `Approved`，容易把已经失效的 100 赞、冻结 5 张、双回顾、72 小时 episode 或未来 Automation 误认为当前合同。

Codex 官方实践要求保持 `AGENTS.md` 小而实用，把任务型工作流放入 Skill，并通过专门文档和自动检查防止长期规则漂移。本项目采用“单一职责、渐进加载、可执行防漂移”的精简治理方式。

## 目标

1. 新线程只读取与任务范围直接相关的最少文档。
2. 每条长期规则只有一个权威解释位置，其他入口只保留摘要和链接。
3. 历史设计继续可追溯，但不能覆盖当前代码、测试、ADR 和执行合同。
4. 真实运行状态继续只来自页面确认、checkpoint、sealed logs 和 CLI 聚合，不进入静态项目文档。
5. 通过测试发现文档缺失、历史状态遗漏和核心合同明显漂移。

## 文档职责

| 位置 | 唯一职责 |
|---|---|
| `AGENTS.md` | 每次任务自动加载的仓库级长期约束、阅读路由、事实源、安全边界和验证要求 |
| `README.md` | 项目入口、最常用命令、目录和隐私提示 |
| `docs/README.md` | 文档权威矩阵、任务管理方式和维护规则 |
| `docs/architecture.md` | 当前组件边界、数据流、算法合同和系统不变量 |
| `docs/operations.md` | 运行、暂停、恢复、交接及已验证故障的最短处理 |
| `docs/knowledge-gaps.md` | 会影响安全、算法或可靠性且仍缺真实证据的问题 |
| `docs/decisions/` | 重大决策、理由、后果和替代关系 |
| `.agents/skills/500px-feedback-growth/` | 点赞任务的可复用执行工作流、页面细节、事件协议和恢复细节 |
| `docs/superpowers/` | 非权威的设计与实施历史，以及其状态索引 |
| `.local/500px-feedback-growth/` | 真实运行事实和本机派生状态，不承担项目说明职责 |

不新增通用 `lessons-learned.md`、`TODO.md`、`ROADMAP.md` 或 `project-status.md`。长期经验按主题写入上表中的唯一负责文件；当前进度由 `doctor`、`status --json` 和 sealed logs 实时重建。

## `AGENTS.md` 精简合同

`AGENTS.md` 保留以下内容：

- 项目目标和语言约定；
- 按任务范围选择文档的阅读路由；
- sealed logs、checkpoint、Dashboard、Git 和 worktree 的事实源边界；
- 真实互动的授权、可见状态确认和硬停止不变量；
- 最新 3 张、200 位、第一张作品、`120/60/20`、`👍👍👍`、即时结算和跨日恢复等核心合同；
- 最小改动、兼容性、测试和交付基线。

以下内容从 `AGENTS.md` 移出并改为链接：

- 逐步 CLI 命令；
- 页面扫描和恢复步骤；
- Dashboard 字段解释；
- 事件字段清单；
- 已验证故障的完整处理表；
- 历史方案的详细兼容逻辑。

## 历史材料治理

新增 `docs/superpowers/README.md`，列出全部 `specs/*.md` 和 `plans/*.md`，并为每份材料标明：

- 状态；
- 当前权威替代物；
- 仍然有效的局部范围；
- 是否仅供历史审计。

历史文件不删除、不移动、不改写正文，只补充顶部状态说明。至少明确以下替代链：

1. 四次 25 赞被单次连续任务替代；
2. 100 次确认点赞目标被 200 位不同摄影师覆盖替代；
3. 自然日强制终止被 active run 跨日恢复替代；
4. 冻结 5 张、双回顾、72 小时 episode 和未来 Automation 被最新 3 张即时结算替代；
5. 便携交接与 Git-backed sealed runs 仍有效，但不再创建未来回顾任务。

ADR 保留历史正文，只在文件顶部和 `docs/decisions/README.md` 补全部分替代关系，不把旧决定重写成新决定。

## 项目管理方式

`docs/README.md` 使用以下最小任务合同：

```text
Goal:
Context:
Constraints:
Done when:
```

- 线程用于当次协作和临时证据；仓库文件承载长期事实。
- 复杂或模糊变更先设计和计划；局部修复直接采用最小实现。
- `/goal` 只用于目标明确、耗时较长且有可验证完成条件的任务。
- GitHub Issues 仅在存在真实跨线程 backlog 时引入，不建立常驻 Markdown 看板。
- 独立任务可以并行，但不得让两个任务同时写同一状态源或共享浏览器账号。

## 防漂移验证

新增 `tests/test_documentation_contract.py`，只验证稳定、长期的结构合同：

1. 权威文档和关键相对链接存在；
2. `docs/superpowers/README.md` 覆盖全部 spec 和 plan；
3. 每份历史 spec/plan 都有明确状态；
4. 当前权威入口共同保留最新 3 张、200 位、第一张作品、`120/60/20`、固定评论、即时结算和跨日恢复合同；
5. 当前入口不把双回顾或未来 Automation 描述成新运行要求。

测试不锁定文档行数、完整文案、真实日志数量或一次性运行 ID，避免把维护成本转化为脆弱断言。

## Knowledge gaps 整理

保留并更新仍然成立的缺口：

- 完整覆盖 200 位并逐赞评论的限频风险；
- 200 位候选池的持续充足度；
- 长时间 Chrome 会话和中断恢复稳定性；
- 跨机器串行交接的真实可靠性；
- 安全暂停的作用域与显式解除语义；
- 最新 3 张即时反馈积分在多个真实任务后的稳定性。

安全暂停缺口同时记录当前语义差异：没有 active run 时，`status` 可显示新任务尚未开始并保留历史 `paused_reason`，而 Dashboard 选择最近一次产生覆盖的任务。该问题在证据和产品语义明确前不通过文案伪装成已解决。

## 实施边界

本次允许修改：

- `AGENTS.md`
- `README.md`
- `docs/README.md`
- `docs/architecture.md`
- `docs/operations.md`
- `docs/knowledge-gaps.md`
- `docs/decisions/README.md` 及需要补充替代说明的旧 ADR
- `docs/superpowers/README.md`
- 历史 spec/plan 顶部状态说明
- `tests/test_documentation_contract.py`

本次不修改：

- Python 生产代码和选择算法；
- Skill 的业务合同和页面行为；
- 事件 schema；
- `.local/500px-feedback-growth/` 中任何文件；
- Dashboard 模板和统计结果；
- Git 远端、分支或发布状态。

## 验收

1. 文档职责清晰，权威入口之间无现行规则冲突。
2. 旧设计均能从索引确认状态和替代物。
3. `AGENTS.md` 明显缩短，但保留所有必须自动加载的硬约束。
4. 当前知识缺口与 sealed logs、`doctor` 和 `status` 的事实一致。
5. 以下验证通过：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_documentation_contract -v
.\.venv\Scripts\python.exe -m unittest discover -v
.\.agents\skills\500px-feedback-growth\scripts\feedback_growth.cmd doctor
git diff --check
git status --short
git worktree list
```
