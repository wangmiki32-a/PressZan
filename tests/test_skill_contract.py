from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1] / ".agents" / "skills" / "500px-feedback-growth"
PROJECT_ROOT = Path(__file__).parents[1]


class SkillContractTest(unittest.TestCase):
    def test_feedback_supervisor_is_project_scoped_read_only_and_has_fixed_output(self):
        path = PROJECT_ROOT / ".codex" / "agents" / "feedback-supervisor.toml"
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        try:
            import tomllib
        except ModuleNotFoundError:
            data = None
        else:
            data = tomllib.loads(text)
        if data is not None:
            self.assertEqual(data["name"], "feedback_supervisor")
            self.assertEqual(data["model"], "gpt-5.6-terra")
            self.assertEqual(data["model_reasoning_effort"], "high")
            self.assertEqual(data["sandbox_mode"], "read-only")
            instructions = data["developer_instructions"]
            self.assertIsInstance(instructions, str)
        else:
            for field in (
                'name = "feedback_supervisor"',
                'model = "gpt-5.6-terra"',
                'model_reasoning_effort = "high"',
                'sandbox_mode = "read-only"',
            ):
                self.assertIn(field, text)
            instructions = text

        for contract in (
            "Verdict",
            "KPI",
            "Problems",
            "Actions",
            "Consolidation",
            "不得操作 Chrome",
            "不得修改项目文件",
            "不得写日志",
            "不得改变候选",
            "不得改变算法",
            "不得改变配额",
            "不得批准",
            "不得改变安全边界",
        ):
            self.assertIn(contract, instructions)
        for forbidden in ("摄影师身份", "候选名单", "run ID", "preview ID"):
            self.assertIn(forbidden, instructions)

    def test_supervisor_workflow_is_mandatory_and_degrades_explicitly(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        operations = (PROJECT_ROOT / "docs" / "operations.md").read_text(encoding="utf-8")
        quality = (PROJECT_ROOT / "docs" / "quality.md").read_text(encoding="utf-8")

        for text in (skill, agents, operations, quality):
            self.assertIn("feedback_supervisor", text)
            self.assertIn("supervisor_degraded", text)
        self.assertLess(skill.index("doctor"), skill.index("feedback_supervisor"))
        self.assertLess(skill.index("status --json"), skill.index("feedback_supervisor"))
        for checkpoint in ("50", "100", "150"):
            self.assertIn(checkpoint, skill)
        self.assertIn("压缩状态", skill)
        self.assertIn("sealed", skill)
        self.assertIn("每批最多 10 位", skill)
        self.assertIn("不启动新的监督模型", skill)
        dashboard = (ROOT / "references" / "dashboard-semantics.md").read_text(encoding="utf-8")
        self.assertIn("执行效率", dashboard)
        self.assertIn("docs/quality.md", dashboard)
        self.assertIn("初始化，不输出审计结论", quality)

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

    def test_windows_launcher_is_repo_relative_and_documented(self):
        launcher = ROOT / "scripts" / "feedback_growth.cmd"
        content = launcher.read_text(encoding="utf-8")
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        operations = (PROJECT_ROOT / "docs" / "operations.md").read_text(encoding="utf-8")

        self.assertTrue(launcher.is_file())
        self.assertIn(".venv\\Scripts\\python.exe", content)
        self.assertIn("codex-primary-runtime", content)
        self.assertIn('set "PYTHONUTF8=1"', content)
        self.assertNotIn("mimi4", content)
        for text in (skill, readme, operations):
            self.assertIn("feedback_growth.cmd", text)

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
        for value in ("3", "30", "200"):
            self.assertIn(value, text)
        self.assertIn("200 位不同摄影师", text)
        self.assertIn("第一张", text)
        self.assertIn("每次确认后立即", text)
        self.assertIn("👍👍👍", text)
        self.assertNotIn("拍的真棒👍", text)
        self.assertNotIn("verified 距上次确认评论", text)
        self.assertIn("before_state", text)
        self.assertIn("after_state", text)
        for stop in ("CAPTCHA", "限频", "登录失效", "平台警告", "账号不匹配", "状态不明确"):
            self.assertIn(stop, text)

    def test_browser_reference_has_stable_semantic_sequence(self):
        text = (ROOT / "references" / "browser-workflow.md").read_text(encoding="utf-8")

        self.assertIn("Dora0125", text)
        self.assertIn("f43fc656a435b8f41e84d05b0123c2485", text)
        self.assertIn("最新 3 张", text)
        self.assertIn("最近 30", text)
        self.assertIn("第一张", text)
        self.assertNotIn("最近 12", text)
        self.assertIn("一次", text)
        self.assertIn("本地高分队列", text)
        self.assertIn("before_state", text)
        self.assertIn("after_state", text)

    def test_owner_identity_accepts_two_verified_page_shapes_but_fails_closed_on_conflict(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        browser = (ROOT / "references" / "browser-workflow.md").read_text(encoding="utf-8")
        recovery = (ROOT / "references" / "operational-recovery.md").read_text(encoding="utf-8")
        operations = (PROJECT_ROOT / "docs" / "operations.md").read_text(encoding="utf-8")

        for text in (skill, browser, recovery, operations):
            self.assertIn("上传者稳定 actor 链接", text)
            self.assertIn("图片资源 URL 中的稳定摄影师 ID", text)
            self.assertIn("任一", text)
            self.assertIn("冲突", text)

    def test_event_schema_documents_every_valid_event(self):
        text = (ROOT / "references" / "event-schema.md").read_text(encoding="utf-8")
        expected = (
            "scan_started",
            "work_observed",
            "received_like_observed",
            "candidate_observed",
            "scan_issue",
            "feedback_scan_completed",
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
            'default_prompt: "使用 $500px-feedback-growth 扫描本人最新 3 张公开作品，处理 200 位摄影师，并在每次确认点赞后评论 👍👍👍。"',
            text,
        )
        self.assertIn("allow_implicit_invocation: false", text)

    def test_immediate_feedback_contract_scans_three_and_settles_same_day(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        browser = (ROOT / "references" / "browser-workflow.md").read_text(encoding="utf-8")
        schema = (ROOT / "references" / "event-schema.md").read_text(encoding="utf-8")
        for text in (skill, browser):
            self.assertIn("最新 3 张", text)
            self.assertIn("200 位", text)
            self.assertIn("👍👍👍", text)
            self.assertNotIn("+20h", text)
            self.assertNotIn("+70h", text)
        self.assertIn("上传和分享由用户手动完成", skill)
        self.assertIn("feedback_scan_completed", schema)
        for quota in ("120", "60", "20"):
            self.assertIn(quota, skill)

    def test_incomplete_feedback_scan_blocks_preview_until_complete(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        browser = (ROOT / "references" / "browser-workflow.md").read_text(encoding="utf-8")
        operations = (PROJECT_ROOT / "docs" / "operations.md").read_text(encoding="utf-8")

        for text in (skill, browser, operations):
            self.assertIn("latest_three_scan_incomplete", text)
            self.assertNotIn("不阻止本轮互动", text)

    def test_entrypoint_orders_scan_before_preview_and_run(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        workflow = text.split("## 标准执行顺序", 1)[1].split("## 本人最新 3 张反馈扫描", 1)[0]

        commands = (
            "doctor",
            "status --json",
            "begin --mode preflight",
            "feedback-scan-complete",
            "preview --run-id",
            "begin --mode run --approve-preview",
            "finish --run-id",
            "dashboard",
        )
        positions = [workflow.index(command) for command in commands]
        self.assertEqual(positions, sorted(positions))

    def test_candidate_discovery_expands_only_until_plan_is_full(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        browser = (ROOT / "references" / "browser-workflow.md").read_text(encoding="utf-8")
        operations = (PROJECT_ROOT / "docs" / "operations.md").read_text(encoding="utf-8")

        for text in (skill, browser, operations):
            self.assertIn("增量补充", text)
            self.assertIn("达到 200 位即停止", text)
        self.assertIn("不得默认扫描最近 30 幅", skill)
        self.assertNotIn("读取最近 30 幅作品", browser)

    def test_browser_work_is_bounded_without_splitting_the_business_run(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        browser = (ROOT / "references" / "browser-workflow.md").read_text(encoding="utf-8")
        recovery = (ROOT / "references" / "operational-recovery.md").read_text(encoding="utf-8")
        operations = (PROJECT_ROOT / "docs" / "operations.md").read_text(encoding="utf-8")

        for text in (skill, browser, recovery, operations):
            self.assertIn("每批最多 10 位", text)
            self.assertIn("同一 run", text)
        self.assertIn("JSON.stringify", browser)
        self.assertIn("不得使用浏览器剪贴板", browser)
        self.assertIn("先对账，再决定是否补动作", recovery)

    def test_confirmation_and_git_recovery_have_single_clear_paths(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        recovery = (ROOT / "references" / "operational-recovery.md").read_text(encoding="utf-8")
        agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        operations = (PROJECT_ROOT / "docs" / "operations.md").read_text(encoding="utf-8")

        for text in (skill, agents, operations):
            self.assertIn("每个新 run 只确认一次", text)
            self.assertIn("run 内不重复询问", text)
        for text in (recovery, operations):
            self.assertIn("http.proxy", text)
            self.assertIn("不得关闭 SSL 校验", text)
        self.assertIn("历史 paused_reason", operations)

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
