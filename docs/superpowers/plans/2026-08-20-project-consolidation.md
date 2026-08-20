# Project Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> 状态：Approved for implementation。实施依据为已批准的 Project Consolidation spec。

**Goal:** 精简 PressZan 的项目级长期指令，建立历史设计状态索引，并用自动测试防止权威文档和当前点赞合同再次漂移。

**Architecture:** 保留现有文档分层，不新增通用经验汇总或动态项目状态文件。`AGENTS.md` 只承载自动加载的硬约束，Skill/references 承载执行工作流，架构和运行文档各守职责，Superpowers 材料通过非权威状态索引保留历史；一份标准库 `unittest` 合同测试验证链接、历史覆盖和现行合同。

**Tech Stack:** Markdown、Python 3.9+ 标准库 `unittest`、Git、项目 Windows launcher

**Spec:** `docs/superpowers/specs/2026-08-20-project-consolidation-design.md`

## Global Constraints

- 不修改点赞业务逻辑、选择算法、事件 schema、Dashboard 模板或浏览器行为。
- 不编辑、移动或删除 `.local/500px-feedback-growth/` 中的任何文件。
- 历史 spec/plan 不删除、不移动、不改写正文，只补顶部状态说明。
- `AGENTS.md` 保留事实源、安全边界、核心业务合同、修改边界和验证要求；操作细节改为链接。
- 不新增生产依赖；测试只使用 Python 标准库。
- 所有文档使用简体中文，代码标识符、路径、CLI 参数和既有技术名词保持英文。
- 每个提交只表达一个可独立审查的完整变更。

---

## File Map

**Create:**

- `docs/superpowers/README.md`：历史 spec/plan 的非权威状态索引。
- `tests/test_documentation_contract.py`：文档结构、历史覆盖和现行合同防漂移测试。

**Modify:**

- `AGENTS.md`：精简为仓库级自动加载约束。
- `README.md`：保留项目入口、常用命令、目录和隐私提示。
- `docs/README.md`：增加权威矩阵、最小任务合同和历史材料边界。
- `docs/architecture.md`：消除与执行手册重复的步骤，仅保留系统合同。
- `docs/operations.md`：集中已验证故障、恢复和当前运行边界。
- `docs/knowledge-gaps.md`：按最新 sealed 状态更新真实缺口。
- `docs/decisions/README.md`：补充 ADR 替代关系索引。
- `docs/decisions/ADR-0002-single-run-daily-task.md`：注明 100 点赞目标被 ADR-0004 替代。
- `docs/decisions/ADR-0003-git-backed-sealed-runs.md`：注明未来 Automation 条款被 ADR-0005 替代。
- `docs/superpowers/specs/*.md`：为旧材料补状态横幅；本次 consolidation spec 保持 `Approved`。
- `docs/superpowers/plans/*.md`：为旧材料补状态横幅；本计划保持当前实施计划状态。

**Unchanged authoritative implementation:**

- `.agents/skills/500px-feedback-growth/SKILL.md`
- `.agents/skills/500px-feedback-growth/references/*.md`
- `.agents/skills/500px-feedback-growth/scripts/feedback_growth/*.py`

---

### Task 1: 建立文档合同测试和历史状态索引

**Files:**

- Create: `tests/test_documentation_contract.py`
- Create: `docs/superpowers/README.md`
- Modify: `docs/superpowers/specs/2026-08-12-500px-feedback-growth-design.md`
- Modify: `docs/superpowers/specs/2026-08-13-single-run-100-consolidation-design.md`
- Modify: `docs/superpowers/specs/2026-08-14-feedback-cycle-automation-design.md`
- Modify: `docs/superpowers/specs/2026-08-16-portable-handoff-design.md`
- Modify: `docs/superpowers/specs/2026-08-17-200-photographer-comment-contract.md`
- Modify: `docs/superpowers/specs/2026-08-19-immediate-feedback-settlement-design.md`
- Modify: `docs/superpowers/plans/2026-08-12-500px-feedback-growth.md`
- Modify: `docs/superpowers/plans/2026-08-13-single-run-100-consolidation.md`
- Modify: `docs/superpowers/plans/2026-08-14-feedback-cycle-automation.md`
- Modify: `docs/superpowers/plans/2026-08-16-portable-handoff.md`
- Modify: `docs/superpowers/plans/2026-08-17-200-photographer-comment-contract.md`
- Modify: `docs/superpowers/plans/2026-08-19-immediate-feedback-settlement.md`

**Interfaces:**

- Consumes: `docs/superpowers/specs/*.md`、`docs/superpowers/plans/*.md` 的文件名和顶部状态。
- Produces: `DocumentationContractTest`，以及后续文档可链接的 `docs/superpowers/README.md`。

- [ ] **Step 1: 写入失败的文档合同测试**

创建 `tests/test_documentation_contract.py`：

```python
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).parents[1]
DOCS_ROOT = PROJECT_ROOT / "docs"
SUPERPOWERS_ROOT = DOCS_ROOT / "superpowers"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class DocumentationContractTest(unittest.TestCase):
    def test_canonical_documents_exist(self):
        expected = (
            PROJECT_ROOT / "AGENTS.md",
            PROJECT_ROOT / "README.md",
            DOCS_ROOT / "README.md",
            DOCS_ROOT / "architecture.md",
            DOCS_ROOT / "operations.md",
            DOCS_ROOT / "knowledge-gaps.md",
            DOCS_ROOT / "decisions" / "README.md",
            SUPERPOWERS_ROOT / "README.md",
            PROJECT_ROOT / ".agents" / "skills" / "500px-feedback-growth" / "SKILL.md",
        )
        for path in expected:
            with self.subTest(path=path):
                self.assertTrue(path.is_file())

    def test_superpowers_index_covers_every_artifact_and_each_has_status(self):
        index = read(SUPERPOWERS_ROOT / "README.md")
        for folder_name in ("specs", "plans"):
            for path in sorted((SUPERPOWERS_ROOT / folder_name).glob("*.md")):
                with self.subTest(path=path):
                    self.assertIn(path.name, index)
                    header = "\n".join(read(path).splitlines()[:10])
                    self.assertIn("状态", header)

    def test_current_contract_is_present_in_authoritative_entrypoints(self):
        paths = (
            PROJECT_ROOT / "AGENTS.md",
            PROJECT_ROOT / "README.md",
            DOCS_ROOT / "architecture.md",
            DOCS_ROOT / "operations.md",
            PROJECT_ROOT / ".agents" / "skills" / "500px-feedback-growth" / "SKILL.md",
        )
        combined = "\n".join(read(path) for path in paths)
        for value in (
            "最新 3 张",
            "200 位",
            "第一张",
            "120/60/20",
            "👍👍👍",
            "即时结算",
            "跨日",
        ):
            self.assertIn(value, combined)
        for obsolete in ("+20h", "+70h", "冻结 5 张"):
            self.assertNotIn(obsolete, combined)

    def test_agents_routes_details_instead_of_embedding_cli_workflow(self):
        agents = read(PROJECT_ROOT / "AGENTS.md")
        for link in (
            "README.md",
            "docs/architecture.md",
            "docs/operations.md",
            ".agents/skills/500px-feedback-growth/SKILL.md",
        ):
            self.assertIn(link, agents)
        for internal_detail in (
            "feedback-scan-complete",
            "preview_not_latest",
            "preview_changed",
            "preview_expired",
        ):
            self.assertNotIn(internal_detail, agents)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试并确认失败原因正确**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_documentation_contract -v
```

Expected: FAIL，首先报告 `docs/superpowers/README.md` 不存在；现有 `AGENTS.md` 仍包含 preview 错误码时，路由测试也应失败。

- [ ] **Step 3: 创建历史状态索引并补齐状态横幅**

`docs/superpowers/README.md` 必须包含两个表格：Design Specs 和 Implementation Plans。每行写文件链接、状态、当前权威替代物和仍有效范围。状态按下列映射填写：

```text
2026-08-12 初版：Superseded；仅保留历史审计
2026-08-13 单次 100：Partially Superseded；入口简化仍有效，100 目标被 ADR-0004 替代
2026-08-14 双回顾周期：Superseded；只读兼容由 ADR-0005 保留
2026-08-16 便携交接：Implemented；Git-backed 交接仍有效，Automation 条款失效
2026-08-17 200 位与评论：Implemented / Partially Superseded；覆盖和评论仍有效，自然日边界被 ADR-0006 替代
2026-08-19 即时结算：Implemented；当前结算和积分合同
2026-08-20 项目整合设计：Approved；本次实施依据
```

每个历史文件标题后十行内加入一段 `> 状态：...` 横幅，并链接对应 ADR 或当前 spec；不修改其余正文。

- [ ] **Step 4: 运行历史索引相关测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_documentation_contract.DocumentationContractTest.test_superpowers_index_covers_every_artifact_and_each_has_status -v
```

Expected: PASS。

- [ ] **Step 5: 提交历史治理变更**

```powershell
git add -- tests/test_documentation_contract.py docs/superpowers
git commit -m "docs: 建立历史设计状态索引"
```

---

### Task 2: 精简自动加载规则和权威入口

**Files:**

- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `docs/README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/operations.md`
- Test: `tests/test_documentation_contract.py`

**Interfaces:**

- Consumes: Task 1 的 `DocumentationContractTest` 和历史索引链接。
- Produces: 精简的自动加载入口、唯一职责文档矩阵和无冲突的现行合同。

- [ ] **Step 1: 运行路由测试并确认现有重复内容被捕获**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_documentation_contract.DocumentationContractTest.test_agents_routes_details_instead_of_embedding_cli_workflow -v
```

Expected: FAIL，报告 `AGENTS.md` 中仍存在 preview 错误码等执行细节。

- [ ] **Step 2: 精简 `AGENTS.md`**

按 spec 保留项目目标、阅读路由、事实源、互动硬边界、核心业务合同、代码修改规则、验证要求和文档维护规则。删除 preview 错误码、逐条恢复命令和 Dashboard 字段级解释，并分别链接：

```markdown
- 真实运行、恢复和故障处理以 [运行手册](docs/operations.md) 为准。
- 浏览器步骤和事件级协议以 [Skill](.agents/skills/500px-feedback-growth/SKILL.md) 及其 references 为准。
- 算法、派生状态和 Dashboard 口径以 [架构说明](docs/architecture.md) 为准。
```

`AGENTS.md` 仍必须显式保留：sealed logs 优先、私有 Git、单账号串行、页面确认、硬停止、最新 3 张、第一张作品、200 位、`120/60/20`、固定评论、即时结算、active run 跨日、Python 3.9 标准库兼容和全量验证命令。

- [ ] **Step 3: 收敛 README 和 docs 职责**

`README.md` 只保留项目一句话说明、快速入口、Windows/跨平台常用命令、目录图和隐私边界；删除完整文档优先级和重复页面操作规则。

`docs/README.md` 增加：

```text
Goal:
Context:
Constraints:
Done when:
```

并明确 `/plan`、`/goal`、GitHub Issues 和并行写入边界。将 `docs/superpowers/` 定义为非权威历史材料，链接 Task 1 的索引。

`docs/architecture.md` 删除逐步人工操作说明，只保留生命周期合同、组件边界和不变量；`docs/operations.md` 承接具体运行、恢复和已验证故障。

- [ ] **Step 4: 运行文档合同和既有 Skill 合同测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_documentation_contract tests.test_skill_contract -v
```

Expected: PASS。

- [ ] **Step 5: 检查同义规则是否仍重复或冲突**

Run:

```powershell
rg -n --hidden --glob '!docs/superpowers/**' --glob '!**/.local/**' "\+20h|\+70h|冻结 5 张|四个 25|每日恰好 100|未来 review Automation" AGENTS.md README.md docs .agents/skills/500px-feedback-growth
```

Expected: 只允许 legacy/被替代说明；不得出现把这些词描述为新运行要求的结果。

- [ ] **Step 6: 提交权威文档精简**

```powershell
git add -- AGENTS.md README.md docs/README.md docs/architecture.md docs/operations.md tests/test_documentation_contract.py
git commit -m "docs: 精简项目权威入口"
```

---

### Task 3: 对齐 ADR 替代关系和真实 knowledge gaps

**Files:**

- Modify: `docs/decisions/README.md`
- Modify: `docs/decisions/ADR-0002-single-run-daily-task.md`
- Modify: `docs/decisions/ADR-0003-git-backed-sealed-runs.md`
- Modify: `docs/knowledge-gaps.md`
- Test: `tests/test_documentation_contract.py`

**Interfaces:**

- Consumes: 当前 sealed logs 的只读聚合结果、ADR-0004、ADR-0005、ADR-0006。
- Produces: 可搜索的部分替代链和与当前事实一致的未决问题。

- [ ] **Step 1: 扩展 ADR 替代关系测试并确认失败**

在 `tests/test_documentation_contract.py` 增加：

```python
    def test_adr_index_records_partial_supersession_chain(self):
        index = read(DOCS_ROOT / "decisions" / "README.md")
        adr_2 = read(DOCS_ROOT / "decisions" / "ADR-0002-single-run-daily-task.md")
        adr_3 = read(DOCS_ROOT / "decisions" / "ADR-0003-git-backed-sealed-runs.md")
        for value in ("ADR-0004", "ADR-0005", "ADR-0006", "部分替代"):
            self.assertIn(value, index)
        self.assertIn("ADR-0004", "\n".join(adr_2.splitlines()[:10]))
        self.assertIn("ADR-0005", "\n".join(adr_3.splitlines()[:12]))
```

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_documentation_contract.DocumentationContractTest.test_adr_index_records_partial_supersession_chain -v
```

Expected: FAIL，因为 ADR-0002 顶部尚未标记 ADR-0004，ADR-0003 顶部尚未标记 ADR-0005。

- [ ] **Step 2: 补全 ADR 替代链**

在 ADR-0002 顶部说明：100 次确认点赞目标由 ADR-0004 替代，跨日恢复边界由 ADR-0006 替代，单入口与自然语言批准仍有效。

在 ADR-0003 顶部说明：Git-backed sealed runs、动态 state root 和串行交接仍有效；“每个新周期创建 Automation”条款由 ADR-0005 替代。

在 `docs/decisions/README.md` 增加状态表，分别列出每份 ADR 的当前有效范围和部分替代来源；不改写 ADR 历史正文。

- [ ] **Step 3: 更新 knowledge gaps 的当前证据**

保留六项缺口，并把 200 位运行证据更新为：最近一次任务覆盖 181 位、142 次确认点赞、141 条确认评论、39 次跳过，以 `ambiguous_comment_state` 封存为 `paused_incomplete`；不得写成已完成 200 位。

在“安全暂停后的显式解除语义”中加入：无 active run 时 `status` 可以显示新任务尚未开始并保留历史 `paused_reason`，Dashboard 则选择最近一次产生覆盖的任务；关闭条件要求明确、测试 pause 的作用域和清除协议。

- [ ] **Step 4: 运行 ADR、文档和聚合基线检查**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_documentation_contract tests.test_repository_state tests.test_analytics -v
.\.agents\skills\500px-feedback-growth\scripts\feedback_growth.cmd doctor
.\.agents\skills\500px-feedback-growth\scripts\feedback_growth.cmd status --json
```

Expected: 所有测试 PASS；`doctor` 返回 `ok=true`；`status` 不被文档改动影响。

- [ ] **Step 5: 提交 ADR 和 knowledge gaps 对齐**

```powershell
git add -- docs/decisions docs/knowledge-gaps.md tests/test_documentation_contract.py
git commit -m "docs: 对齐决策替代链和知识缺口"
```

---

### Task 4: 全量验证和交付检查

**Files:**

- Verify only: repository-wide documentation, tests, Git state, state-store boundary

**Interfaces:**

- Consumes: Tasks 1-3 的三个独立提交。
- Produces: 可复现的完成证据和剩余 knowledge gaps 报告。

- [ ] **Step 1: 运行文档和 Skill 聚焦测试**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_documentation_contract tests.test_skill_contract -v
```

Expected: PASS。

- [ ] **Step 2: 运行全量单元测试**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

Expected: 全部 PASS，无真实浏览器互动。

- [ ] **Step 3: 验证运行事实源没有受到影响**

```powershell
.\.agents\skills\500px-feedback-growth\scripts\feedback_growth.cmd doctor
.\.agents\skills\500px-feedback-growth\scripts\feedback_growth.cmd status --json
```

Expected: `doctor.ok=true`；sealed run 数量、Git 边界和聚合状态可重建；命令不创建新 run。

- [ ] **Step 4: 检查 Markdown、Git 和 worktree 边界**

```powershell
git diff --check HEAD~3..HEAD
git status --short
git worktree list
git log -4 --oneline
```

Expected: `git diff --check` 无输出；工作区干净；只有主 worktree；提交历史依次包含设计、历史索引、权威文档精简、ADR/knowledge gaps 对齐。

- [ ] **Step 5: 输出 Project Consolidation 报告**

最终报告必须明确：

- 更新了哪些项目级长期经验；
- 删除、替换或降级了哪些旧规则；
- 当前六项 knowledge gaps；
- 测试、`doctor`、Git 和 worktree 的验证结果；
- 未修改业务逻辑、真实日志和 Dashboard 数据。
