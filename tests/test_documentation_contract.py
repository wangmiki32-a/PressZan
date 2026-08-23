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
            DOCS_ROOT / "quality.md",
            DOCS_ROOT / "knowledge-gaps.md",
            DOCS_ROOT / "decisions" / "README.md",
            SUPERPOWERS_ROOT / "README.md",
            PROJECT_ROOT / ".agents" / "skills" / "500px-feedback-growth" / "SKILL.md",
        )
        for path in expected:
            with self.subTest(path=path):
                self.assertTrue(path.is_file())

    def test_quality_is_the_authority_for_supervision_kpis_and_consolidation(self):
        index = read(DOCS_ROOT / "README.md")
        quality = read(DOCS_ROOT / "quality.md")

        self.assertIn("quality.md", index)
        for value in (
            "feedback_supervisor",
            "read-only",
            "supervisor_degraded",
            "speed_score",
            "first_pass_score",
            "first_preview_fill_score",
            "efficiency_score",
            "50/100/150",
            "10 分",
            "10%",
            "60 次触达",
            "5 个合格批次",
        ):
            self.assertIn(value, quality)
        self.assertIn("评论", quality)
        self.assertIn("不进入", quality)
        self.assertIn("token", quality)
        self.assertIn("不纳入", quality)
        self.assertIn("前次结论不可访问时", quality)
        self.assertIn("权限来自用户批准", quality)
        self.assertIn("daily_task_id 聚合", quality)
        self.assertIn("分片恢复", quality)
        self.assertIn("任一关联 run", quality)

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

    def test_adr_index_records_partial_supersession_chain(self):
        index = read(DOCS_ROOT / "decisions" / "README.md")
        adr_2 = read(DOCS_ROOT / "decisions" / "ADR-0002-single-run-daily-task.md")
        adr_3 = read(DOCS_ROOT / "decisions" / "ADR-0003-git-backed-sealed-runs.md")
        for value in ("ADR-0004", "ADR-0005", "ADR-0006", "部分替代"):
            self.assertIn(value, index)
        self.assertIn("ADR-0004", "\n".join(adr_2.splitlines()[:10]))
        self.assertIn("ADR-0005", "\n".join(adr_3.splitlines()[:12]))


if __name__ == "__main__":
    unittest.main()
