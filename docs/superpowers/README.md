# Superpowers 设计与实施历史

本目录保存已经批准或执行过的设计与实施计划，用于审计决策演进，不是当前运行合同。当前行为依次以仓库根目录 `AGENTS.md`、项目 Skill、代码与测试、现行 ADR 为准；历史材料与这些事实冲突时，以当前权威入口为准。

历史文件不删除、不移动、不改写正文。新方案替代旧方案时，只更新本索引并在旧文件顶部补充状态和替代来源。

## Design Specs

| 文件 | 状态 | 当前权威替代物 | 仍有效范围 |
|---|---|---|---|
| [2026-08-12-500px-feedback-growth-design.md](specs/2026-08-12-500px-feedback-growth-design.md) | Superseded | ADR-0002、ADR-0004、ADR-0005、ADR-0006 | 初版问题背景和审计历史 |
| [2026-08-13-single-run-100-consolidation-design.md](specs/2026-08-13-single-run-100-consolidation-design.md) | Partially Superseded | ADR-0004、ADR-0006 | 零参数入口、自然语言批准和单 run 恢复方向 |
| [2026-08-14-feedback-cycle-automation-design.md](specs/2026-08-14-feedback-cycle-automation-design.md) | Superseded | ADR-0005 | 旧 cycle/review 日志兼容背景 |
| [2026-08-16-portable-handoff-design.md](specs/2026-08-16-portable-handoff-design.md) | Implemented | ADR-0003、ADR-0005 | Git-backed sealed runs、动态 state root 和串行交接；Automation 条款失效 |
| [2026-08-17-200-photographer-comment-contract.md](specs/2026-08-17-200-photographer-comment-contract.md) | Implemented / Partially Superseded | ADR-0004、ADR-0006 | 200 位覆盖、第一张作品和固定评论；自然日终止边界失效 |
| [2026-08-19-immediate-feedback-settlement-design.md](specs/2026-08-19-immediate-feedback-settlement-design.md) | Implemented | ADR-0005、ADR-0006 | 最新 3 张、即时结算、积分与分层合同 |
| [2026-08-20-project-consolidation-design.md](specs/2026-08-20-project-consolidation-design.md) | Approved | 本次 Project Consolidation | 文档职责、历史治理和防漂移验证 |

## Implementation Plans

| 文件 | 状态 | 当前权威替代物 | 仍有效范围 |
|---|---|---|---|
| [2026-08-12-500px-feedback-growth.md](plans/2026-08-12-500px-feedback-growth.md) | Historical / Superseded | 后续全部 ADR | 初版实施审计 |
| [2026-08-13-single-run-100-consolidation.md](plans/2026-08-13-single-run-100-consolidation.md) | Implemented / Partially Superseded | ADR-0004、ADR-0006 | 单入口和单 run 实施历史 |
| [2026-08-14-feedback-cycle-automation.md](plans/2026-08-14-feedback-cycle-automation.md) | Implemented then Superseded | ADR-0005 | 旧 cycle/review 兼容实现历史 |
| [2026-08-16-portable-handoff.md](plans/2026-08-16-portable-handoff.md) | Implemented / Partially Superseded | ADR-0003、ADR-0005 | Git 交接、状态根和 `doctor`；Automation 步骤失效 |
| [2026-08-17-200-photographer-comment-contract.md](plans/2026-08-17-200-photographer-comment-contract.md) | Implemented / Partially Superseded | ADR-0004、ADR-0006 | 200 位和评论实现；自然日终止步骤失效 |
| [2026-08-19-immediate-feedback-settlement.md](plans/2026-08-19-immediate-feedback-settlement.md) | Implemented | ADR-0005、ADR-0006 | 当前即时账本和 Dashboard 实施依据 |
| [2026-08-20-project-consolidation.md](plans/2026-08-20-project-consolidation.md) | Approved for implementation | 2026-08-20 consolidation spec | 本次文档整合实施步骤 |

## 使用规则

- 查当前运行语义：从 `AGENTS.md` 和项目 Skill 开始，不从本目录开始。
- 查重大决策：阅读 `docs/decisions/README.md` 和对应 ADR。
- 查历史上为什么改变方案：再阅读本索引指向的旧 spec/plan。
- 新的替代关系必须同时更新旧文件顶部状态、本索引和相关 ADR；不得靠删除历史消除冲突。
