import importlib.util
import pathlib
import unittest


SERVER_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "mcp_server.py"
SPEC = importlib.util.spec_from_file_location("mergehub_requirement_workflow_mcp_server", SERVER_PATH)
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)


class RequirementToolTests(unittest.TestCase):
    def setUp(self):
        self.original_request_json = server.request_json

    def tearDown(self):
        server.request_json = self.original_request_json

    def test_server_info_version_matches_plugin_manifest(self):
        manifest = server.json_loads(
            (SERVER_PATH.parents[1] / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"),
            {},
        )

        response = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})

        self.assertEqual(response["result"]["serverInfo"]["version"], manifest["version"])

    def test_build_development_branch_uses_required_pattern(self):
        self.assertEqual(
            server.build_development_branch("feature", "order_export", "REQ000001"),
            "feature_order_export_REQ000001",
        )
        self.assertEqual(
            server.build_development_branch("hotfix", "login_ip", "REQ000002"),
            "hotfix_login_ip_REQ000002",
        )
        with self.assertRaisesRegex(ValueError, "functionSlug"):
            server.build_development_branch("feature", "订单导出", "REQ000001")

    def test_validate_commit_message_requires_requirement_number(self):
        self.assertTrue(server.validate_commit_message("[REQ000001] order export", "REQ000001"))
        with self.assertRaisesRegex(ValueError, "REQ000001"):
            server.validate_commit_message("order export", "REQ000001")

    def test_requirement_tools_require_runtime_token_value(self):
        with self.assertRaisesRegex(ValueError, "tokenValue"):
            server.require_runtime_credentials({"apiBaseUrl": "http://127.0.0.1:8080"})

    def test_start_development_run_blocks_when_latest_plan_is_not_approved(self):
        calls = []

        def fake_request_json(method, url, headers, payload=None):
            calls.append((method, url, payload))
            self.assertEqual(headers, {"satoken": "token-1"})
            if method == "GET":
                return {
                    "id": 12,
                    "requirementId": 1,
                    "requirementNo": "REQ000001",
                    "taskStatus": "planning",
                    "latestPlan": {
                        "id": 50,
                        "taskId": 12,
                        "planVersion": 1,
                        "planStatus": "submitted",
                        "planContent": "实现方案",
                        "riskSummary": "低风险",
                    },
                }
            raise AssertionError("development run should not be created before user confirmation")

        server.request_json = fake_request_json

        with self.assertRaisesRegex(ValueError, "方案未通过"):
            server.tool_start_development_run({
                "apiBaseUrl": "http://127.0.0.1:8080",
                "tokenValue": "token-1",
                "taskId": 12,
                "branchType": "feature",
                "functionSlug": "order_export",
            })

        self.assertEqual(calls, [
            ("GET", "http://127.0.0.1:8080/api/codex/tasks/12", None),
        ])

    def test_start_development_run_calls_mergehub_after_plan_is_approved(self):
        calls = []

        def fake_request_json(method, url, headers, payload=None):
            calls.append((method, url, payload))
            if method == "GET":
                return {
                    "id": 12,
                    "requirementId": 1,
                    "requirementNo": "REQ000001",
                    "taskStatus": "approved_to_develop",
                    "latestPlan": {
                        "id": 50,
                        "taskId": 12,
                        "planVersion": 1,
                        "planStatus": "approved",
                        "planContent": "实现方案",
                        "riskSummary": "低风险",
                    },
                }
            return {"id": 90, "developmentBranch": "feature_order_export_REQ000001", "runStatus": "running"}

        server.request_json = fake_request_json

        response = server.tool_start_development_run({
            "apiBaseUrl": "http://127.0.0.1:8080",
            "tokenValue": "token-1",
            "taskId": 12,
            "branchType": "feature",
            "functionSlug": "order_export",
        })

        self.assertEqual(response["id"], 90)
        self.assertEqual(calls[0], ("GET", "http://127.0.0.1:8080/api/codex/tasks/12", None))
        self.assertEqual(calls[1][0], "POST")
        self.assertEqual(calls[1][1], "http://127.0.0.1:8080/api/codex/tasks/12/development-runs")
        self.assertEqual(calls[1][2]["branchType"], "feature")
        self.assertEqual(calls[1][2]["functionSlug"], "order_export")

    def test_start_development_run_returns_direct_development_context_after_user_override(self):
        calls = []

        def fake_request_json(method, url, headers, payload=None):
            calls.append((method, url, payload))
            if method == "GET":
                return {
                    "id": 12,
                    "requirementId": 1,
                    "requirementNo": "REQ000001",
                    "taskStatus": "planning",
                    "latestPlan": {
                        "id": 50,
                        "taskId": 12,
                        "planVersion": 1,
                        "planStatus": "rejected",
                        "planContent": "实现方案",
                        "riskSummary": "需要补充回滚策略",
                    },
                }
            raise AssertionError("MergeHub development run should not be created when overriding an unapproved plan")

        server.request_json = fake_request_json

        response = server.tool_start_development_run({
            "apiBaseUrl": "http://127.0.0.1:8080",
            "tokenValue": "token-1",
            "taskId": 12,
            "branchType": "hotfix",
            "functionSlug": "task_page",
            "ignoreUnapprovedPlan": True,
        })

        self.assertEqual(response["mergeHubDevelopmentRunCreated"], False)
        self.assertEqual(response["directDevelopmentAllowed"], True)
        self.assertEqual(response["developmentBranch"], "hotfix_task_page_REQ000001")
        self.assertEqual(calls, [
            ("GET", "http://127.0.0.1:8080/api/codex/tasks/12", None),
        ])

    def test_record_development_artifact_requires_complete_content_for_changed_artifact(self):
        calls = []

        def fake_request_json(method, url, headers, payload=None):
            calls.append((method, url, headers, payload))
            return {"id": 41}

        server.request_json = fake_request_json

        with self.assertRaisesRegex(ValueError, "complete artifact content"):
            server.tool_record_development_artifact({
                "apiBaseUrl": "http://127.0.0.1:8080",
                "tokenValue": "token-1",
                "runId": 90,
                "artifactType": "sql",
                "artifactName": "migration.sql",
                "hasChange": True,
                "artifactContent": "   ",
            })

        self.assertEqual(calls, [])

    def test_record_development_artifact_preserves_full_content_without_truncation(self):
        calls = []
        complete_content = "-- full migration\n" + ("INSERT INTO audit_log(message) VALUES ('complete');\n" * 1500)

        def fake_request_json(method, url, headers, payload=None):
            calls.append((method, url, headers, payload))
            return {"id": 41, "artifactContent": payload["artifactContent"]}

        server.request_json = fake_request_json

        response = server.tool_record_development_artifact({
            "apiBaseUrl": "http://127.0.0.1:8080",
            "tokenValue": "token-1",
            "runId": 90,
            "artifactType": "sql",
            "artifactName": "migration.sql",
            "artifactContent": complete_content,
        })

        self.assertGreater(len(complete_content), 50000)
        self.assertEqual(response["artifactContent"], complete_content)
        self.assertEqual(calls[0][3]["artifactContent"], complete_content)

    def test_record_development_artifact_allows_empty_content_only_for_no_change(self):
        calls = []

        def fake_request_json(method, url, headers, payload=None):
            calls.append((method, url, headers, payload))
            return {"id": 42}

        server.request_json = fake_request_json

        server.tool_record_development_artifact({
            "apiBaseUrl": "http://127.0.0.1:8080",
            "tokenValue": "token-1",
            "runId": 90,
            "artifactType": "nacos",
            "artifactName": "application.yml",
            "hasChange": False,
        })

        self.assertEqual(calls[0][3]["hasChange"], False)
        self.assertIsNone(calls[0][3]["artifactContent"])

    def test_record_development_artifact_tool_contract_requires_full_deliverable_text(self):
        tool = server.TOOLS["record_development_artifact"]

        self.assertIn("full deliverable text", tool["description"])
        self.assertIn("GitHub-Flavored Markdown", tool["description"])
        self.assertIn("complete native format", tool["description"])
        self.assertIn("*_DOC/*_GUIDE", tool["schema"]["properties"]["artifactType"]["description"])
        self.assertIn(".md filename", tool["schema"]["properties"]["artifactName"]["description"])
        self.assertIn("Complete artifact text", tool["schema"]["properties"]["artifactContent"]["description"])

    def test_submit_development_self_test_posts_to_codex_self_test_endpoint(self):
        calls = []

        def fake_request_json(method, url, headers, payload=None):
            calls.append((method, url, headers, payload))
            return {"id": 33, "taskId": 12, "developmentRunId": 90, "result": "passed"}

        server.request_json = fake_request_json

        response = server.tool_submit_development_self_test({
            "apiBaseUrl": "http://127.0.0.1:8080",
            "tokenValue": "token-1",
            "taskId": 12,
            "developmentRunId": 90,
            "result": "passed",
            "environment": "local",
            "scopeSummary": "任务列表分页",
            "testPoints": "priority 筛选",
            "evidenceUrl": "http://example.test/self-test",
            "commitSummary": "2 commits",
            "artifactSummary": "无 SQL",
        })

        self.assertEqual(response["id"], 33)
        self.assertEqual(calls, [(
            "POST",
            "http://127.0.0.1:8080/api/codex/tasks/12/self-tests",
            {"satoken": "token-1"},
            {
                "developmentRunId": 90,
                "result": "passed",
                "environment": "local",
                "scopeSummary": "任务列表分页",
                "testPoints": "priority 筛选",
                "evidenceUrl": "http://example.test/self-test",
                "commitSummary": "2 commits",
                "artifactSummary": "无 SQL",
            },
        )])

    def test_mark_development_done_posts_to_codex_dev_done_endpoint(self):
        calls = []

        def fake_request_json(method, url, headers, payload=None):
            calls.append((method, url, headers, payload))
            return {"id": 12, "taskStatus": "dev_done"}

        server.request_json = fake_request_json

        response = server.tool_mark_development_done({
            "apiBaseUrl": "http://127.0.0.1:8080",
            "tokenValue": "token-1",
            "taskId": 12,
        })

        self.assertEqual(response["taskStatus"], "dev_done")
        self.assertEqual(calls, [(
            "POST",
            "http://127.0.0.1:8080/api/codex/tasks/12/dev-done",
            {"satoken": "token-1"},
            None,
        )])

    def test_get_requirement_document_fetches_requirement_by_task_id(self):
        calls = []

        def fake_request_json(method, url, headers, payload=None):
            calls.append((method, url, headers, payload))
            if url.endswith("/api/codex/tasks/12"):
                return {"id": 12, "requirementId": 3, "requirementNo": "REQ000003"}
            if url.endswith("/api/codex/requirements/3/document"):
                return {
                    "id": 3,
                    "requirementNo": "REQ000003",
                    "title": "算法添加重名验证",
                    "description": "新增和编辑算法时校验名称唯一。",
                    "documentUrl": "https://alidocs.dingtalk.com/i/example",
                }
            raise AssertionError(f"unexpected url: {url}")

        server.request_json = fake_request_json

        response = server.tool_get_requirement_document({
            "apiBaseUrl": "http://127.0.0.1:8080",
            "tokenValue": "token-1",
            "taskId": 12,
        })

        self.assertEqual(response["requirementDocument"]["requirementNo"], "REQ000003")
        self.assertEqual(response["sourceTask"]["id"], 12)
        self.assertEqual(calls, [
            ("GET", "http://127.0.0.1:8080/api/codex/tasks/12", {"satoken": "token-1"}, None),
            ("GET", "http://127.0.0.1:8080/api/codex/requirements/3/document", {"satoken": "token-1"}, None),
        ])

    def test_get_requirement_document_fetches_requirement_by_requirement_no(self):
        calls = []

        def fake_request_json(method, url, headers, payload=None):
            calls.append((method, url, headers, payload))
            return {"id": 3, "requirementNo": "REQ000003", "description": "需求正文"}

        server.request_json = fake_request_json

        response = server.tool_get_requirement_document({
            "apiBaseUrl": "http://127.0.0.1:8080",
            "tokenValue": "token-1",
            "requirementNo": "REQ000003",
        })

        self.assertEqual(response["requirementDocument"]["description"], "需求正文")
        self.assertIsNone(response["sourceTask"])
        self.assertEqual(calls, [
            ("GET", "http://127.0.0.1:8080/api/codex/requirements/REQ000003/document", {"satoken": "token-1"}, None),
        ])


if __name__ == "__main__":
    unittest.main()
