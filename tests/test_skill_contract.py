from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1] / ".agents" / "skills" / "500px-feedback-growth"
PROJECT_ROOT = Path(__file__).parents[1]


class SkillContractTest(unittest.TestCase):
    def test_portable_handoff_contract_is_explicit(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        recovery = (ROOT / "references" / "operational-recovery.md").read_text(encoding="utf-8")
        schema = (ROOT / "references" / "event-schema.md").read_text(encoding="utf-8")
        agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        operations = (PROJECT_ROOT / "docs" / "operations.md").read_text(encoding="utf-8")

        for text in (skill, agents, readme, operations):
            self.assertNotIn("/Users/pony/Documents/ChatGPT/PressZan", text)
        self.assertIn("doctor", skill)
        self.assertLess(skill.index("doctor"), skill.index("status --json"))
        self.assertIn("PRESSZAN_STATE_ROOT", operations)
        self.assertIn("runs/*.md", agents)
        self.assertIn("私有", agents)
        self.assertIn("串行", recovery)
        self.assertIn("未封存", recovery)
        self.assertIn("Automation 不随 Git 迁移", recovery)
        self.assertIn("Git-backed", schema)

    def test_entrypoint_routes_all_public_operations_and_cli_lifecycle(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("$500px-feedback-growth", text)
        self.assertIn("确认执行", text)
        for operation in ("preflight", "status", "dashboard"):
            self.assertIn(f"`{operation}`", text)
        for command in (
            "status --json",
            "begin --mode preflight",
            "begin --mode run --approve-preview",
            "preview --run-id",
            "latest-preview",
            "approve --run-id",
            "finish --run-id",
            "resume --run-id",
        ):
            self.assertIn(command, text)
        for approval_error in ("preview_not_latest", "preview_changed", "preview_expired"):
            self.assertIn(approval_error, text)
        self.assertNotIn("run --approve <preview_id>", text)
        self.assertNotIn("最多 25", text)
        self.assertNotIn("四个 25", text)
        self.assertIn("approved=true", text)

    def test_entrypoint_declares_limits_confirmation_and_hard_stops(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("computer-use:computer-use", text)
        self.assertIn("references/browser-workflow.md", text)
        self.assertIn("references/event-schema.md", text)
        self.assertIn("references/dashboard-semantics.md", text)
        for value in ("30", "12", "100", "80", "72", "7"):
            self.assertIn(value, text)
        self.assertIn("当日累计 100", text)
        self.assertIn("每次确认后立即", text)
        self.assertIn("拍的真棒👍", text)
        self.assertIn("before_state", text)
        self.assertIn("after_state", text)
        for stop in ("CAPTCHA", "限频", "登录失效", "平台警告", "账号不匹配", "状态不明确"):
            self.assertIn(stop, text)

    def test_browser_reference_has_stable_semantic_sequence(self):
        text = (ROOT / "references" / "browser-workflow.md").read_text(encoding="utf-8")

        self.assertIn("Dora0125", text)
        self.assertIn("f43fc656a435b8f41e84d05b0123c2485", text)
        self.assertIn("最近 30", text)
        self.assertIn("最近 12", text)
        self.assertIn("第一幅", text)
        self.assertIn("一次", text)
        self.assertIn("本地高分队列", text)
        self.assertIn("before_state", text)
        self.assertIn("after_state", text)

    def test_event_schema_documents_every_valid_event(self):
        text = (ROOT / "references" / "event-schema.md").read_text(encoding="utf-8")
        expected = (
            "scan_started",
            "work_observed",
            "received_like_observed",
            "candidate_observed",
            "scan_issue",
            "preview_created",
            "onboarding_approved",
            "outgoing_like_confirmed",
            "outgoing_comment_confirmed",
            "feedback_episode_opened",
            "feedback_episode_extended",
            "feedback_episode_succeeded",
            "feedback_episode_failed",
            "candidate_skipped",
            "safety_paused",
            "run_finished",
        )
        for event_name in expected:
            self.assertIn(f"`{event_name}`", text)

    def test_discovery_metadata_is_explicit_only(self):
        text = (ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")

        self.assertIn('display_name: "500px Feedback Growth"', text)
        self.assertIn('short_description: "用可归因反馈持续优化 500px 摄影师点赞互动与回馈增长"', text)
        self.assertIn(
            'default_prompt: "使用 $500px-feedback-growth 冻结当前 5 张公开作品，恢复或开始本轮点赞，并自动安排两次只读回顾。"',
            text,
        )
        self.assertIn("allow_implicit_invocation: false", text)

    def test_cycle_contract_freezes_five_and_schedules_two_read_only_reviews(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        browser = (ROOT / "references" / "browser-workflow.md").read_text(encoding="utf-8")
        dashboard = (ROOT / "references" / "dashboard-semantics.md").read_text(encoding="utf-8")
        recovery = (ROOT / "references" / "operational-recovery.md").read_text(encoding="utf-8")

        for text in (skill, browser):
            self.assertIn("冻结 5 张", text)
            self.assertIn("baseline", text)
        for text in (skill, dashboard, recovery):
            self.assertIn("+20h", text)
            self.assertIn("+70h", text)
            self.assertIn("只读", text)
        self.assertIn("上传和分享由用户手动完成", skill)
        self.assertIn("+70h 不等于 72 小时成熟", dashboard)
        self.assertIn("不得创建周期性轮询任务", recovery)

    def test_event_schema_documents_cycle_events(self):
        text = (ROOT / "references" / "event-schema.md").read_text(encoding="utf-8")
        for event_name in (
            "cycle_started",
            "cycle_showcase_frozen",
            "cycle_baseline_completed",
            "cycle_run_bound",
            "cycle_like_completed",
            "review_schedule_requested",
            "review_scheduled",
            "review_started",
            "review_photo_observed",
            "review_completed",
            "cycle_attribution_scope_mapped",
        ):
            self.assertIn(f"`{event_name}`", text)


if __name__ == "__main__":
    unittest.main()
