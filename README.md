# 500px 正向反馈增长

这是一个项目级 Codex skill：通过可恢复、可审计的 500px 点赞任务，扫描本人最新 3 张作品中的增量反馈，逐步识别更常反馈的摄影师，并用本地事件日志持续优化候选选择。

项目默认由个人长期维护，Codex 负责执行和维护。线程用于讨论当次任务；仓库中的文档、代码、测试和决策记录才是长期事实源。

## 快速入口

- 执行工作流：用户先手动上传/分享，再调用 `$500px-feedback-growth`。当前合同是扫描本人最新 3 张、按 `120/60/20` 覆盖 200 位摄影师、每位只看第一张作品、确认点赞后评论 `👍👍👍`，完成后即时结算；active run 可跨日续跑。
- 每个新 run 只需对有效预览回复一次“确认执行”，run 内不重复询问；无需复制 preview ID。
- 查看项目规则：阅读 [AGENTS.md](AGENTS.md)。
- 了解系统边界：阅读 [架构说明](docs/architecture.md)。
- 执行、恢复或排查：阅读 [运行手册](docs/operations.md)。
- 查看尚待真实证据验证的问题：阅读 [知识缺口](docs/knowledge-gaps.md)。
- 查找文档职责和维护方式：阅读 [文档索引](docs/README.md)。

状态检查命令：

```bash
python3 .agents/skills/500px-feedback-growth/scripts/feedback_growth.py doctor
python3 .agents/skills/500px-feedback-growth/scripts/feedback_growth.py status --json
```

Windows 原生 Codex/PowerShell 使用项目启动器（优先 `.venv`，并兼容 Codex 随附 Python）：

```powershell
.\.agents\skills\500px-feedback-growth\scripts\feedback_growth.cmd doctor
.\.agents\skills\500px-feedback-growth\scripts\feedback_growth.cmd status --json
```

重建 Dashboard：

```bash
python3 .agents/skills/500px-feedback-growth/scripts/feedback_growth.py dashboard
```

Windows：

```powershell
.\.agents\skills\500px-feedback-growth\scripts\feedback_growth.cmd dashboard
```

## 跨机器执行

仓库必须保持私有，同一账号只能串行执行。新机器 clone 后使用自己的 Chrome 登录，先 pull 并通过 `doctor`；Git 只交接 sealed `runs/*.md`，不复制 checkpoint、Dashboard、Chrome profile、Cookie 或 token。完整步骤见 [运行手册](docs/operations.md)。

运行测试：

```bash
python3 -m unittest discover -v
git diff --check
```

Windows 本地部署后使用：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
git diff --check
```

## 目录结构

```text
.
├── AGENTS.md                         # Codex 自动读取的项目级长期规则
├── README.md                         # 项目入口和常用命令
├── .agents/skills/500px-feedback-growth/
│   ├── SKILL.md                      # 工作流入口、授权边界和硬停止规则
│   ├── references/                   # 浏览器流程、事件 schema、恢复细节
│   ├── scripts/feedback_growth/      # 状态、算法、CLI 和 Dashboard 实现
│   ├── assets/dashboard.html         # 自包含 Dashboard 模板
│   └── agents/openai.yaml            # Skill 发现与调用策略
├── docs/
│   ├── README.md                     # 文档索引和维护规则
│   ├── architecture.md               # 架构、数据流和系统不变量
│   ├── operations.md                 # 日常执行与故障恢复手册
│   ├── knowledge-gaps.md             # 待真实运行补足的证据缺口
│   ├── decisions/                    # 长期架构决策记录
│   └── superpowers/                  # 非权威设计历史及状态索引
├── tests/                             # 无真实外部互动的确定性测试
└── .local/500px-feedback-growth/
    ├── runs/*.md                      # 私有 Git 中共享的 sealed 历史
    ├── checkpoints/                   # 当前机器恢复状态，不进入 Git
    └── dashboard.html                 # 本地派生视图，不进入 Git
```

## 安全与数据边界

- `runs/*.md` 包含摄影师身份、互动记录和回馈关系，只允许进入私有 Git；不得公开仓库。
- Checkpoint、Dashboard、浏览器认证和其他机器状态不进入 Git。
- 浏览器只读取可见状态；不读取凭证，不绕过验证，状态不明确立即停止。

## 文档治理

权威位置、更新边界和任务管理方式见 [文档索引](docs/README.md)。历史 spec/plan 只用于审计设计演进，状态见 [Superpowers 历史索引](docs/superpowers/README.md)。
