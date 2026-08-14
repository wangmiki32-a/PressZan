from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1] / ".agents" / "skills" / "500px-feedback-growth"


class SkillContractTest(unittest.TestCase):
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
            'default_prompt: "使用 $500px-feedback-growth 恢复或开始今天的任务，安全执行到当日累计 100 个确认点赞。"',
            text,
        )
        self.assertIn("allow_implicit_invocation: false", text)


if __name__ == "__main__":
    unittest.main()
