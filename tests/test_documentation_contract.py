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
