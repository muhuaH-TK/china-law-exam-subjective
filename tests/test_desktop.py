from support import confirm_formal
from lawstudy.native_tasks import SPEC_KEYS
"""桌面聊天后端和安装包验收。所有用户、宿主回执与资料均为合成隔离样本。"""
import base64
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from lawstudy.desktop import bootstrap, context, dispatch, guide, service_for
from lawstudy.mcp import serve
from lawstudy.store import ConflictError
from tools.package_release import build_packages
import run

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT/"skills/law-exam-study/scripts/assistant.py"


class DesktopTests(unittest.TestCase):
    def setUp(self):
        folder = ROOT/"tests/.tmp"
        folder.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=folder)
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)/"复习项目"

    def start(self, client="workbuddy"):
        bootstrap(self.workspace, client, "合成用户选定本工作空间")
        service, _ = service_for(self.workspace)
        confirm_formal(service)
        return context(self.workspace)

    def call(self, action, **kwargs):
        request = {"action": action, **kwargs}
        if action not in ("context", "guide", "bootstrap", "automation_plan"):
            request.setdefault("version", context(self.workspace)["version"])
        if action == 'configure':request.setdefault('user_instruction', '合成用户明确配置')
        result = dispatch(self.workspace, request)
        if action == 'configure':
            confirm_formal(service_for(self.workspace)[0])
            result['context'] = context(self.workspace)
        return result

    def settings(self, stage=2, client="workbuddy", automation=False):
        config = {"year": 2027, "participation": "participating", "stage_override": stage, "selected_ai": client,
                  "authorization": {"online_ai": True, "search_mode": "search_and_import", "scope": "合成测试来源"}}
        if automation:
            config["automation"] = {"learning_requested": True, "learning_time": "09:30", "monitor_requested": True, "monitor_time": "22:00", "daily_limit": 10}
        return self.call("configure", payload=config)

    def learn(self, action, **payload):
        return self.call("learning", payload={"action": action, "payload": payload})["result"]

    def claim(self):
        return self.call("automation", operation="claim", payload={"evidence": "合成用户指定本客户端及本机运行自动化"})

    def receipt(self, role="learning", status="verified", task_id="synthetic-native-task"):
        spec = next(r for r in self.call("automation_plan")["tasks"] if r["role"] == role)
        service, _ = service_for(self.workspace)
        old = service.store.snapshot()[0].get("desktop", {}).get("tasks", {}).get(role)
        creation_id = spec.get("creation_id")
        if not old and not creation_id:
            query = self.call("automation", operation="reconcile", payload={"role": role, "complete": True, "candidates": [],
                "tool": "synthetic_host.list", "evidence": "合成实际完整查询，无匹配"})["result"]
            creation_id = self.call("automation", operation="reserve_create", payload={"role": role, "discovery_id": query["id"],
                "tool": "synthetic_host.list", "evidence": "合成创建意图"})["result"]["creation_id"]
        observed = {"task_id": task_id, "enabled": status != "disabled", "spec": {k: spec[k] for k in SPEC_KEYS}}
        return self.call("automation", operation="record_task", payload={"role": role, "status": status, "task_id": task_id,
            "creation_id": creation_id, "observed": observed, "tool": "synthetic_host.read_task", "evidence": "合成真实规格回读，不是实际客户端"})

    def test_default_entry_never_opens_browser_or_creates_a_personal_instance(self):
        self.workspace.mkdir()
        previous = Path.cwd()
        try:
            os.chdir(self.workspace)
            output = io.StringIO()
            with patch("webbrowser.open", side_effect=AssertionError("默认入口不应打开浏览器")), redirect_stdout(output):
                self.assertEqual(run.main([]), 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "needs_project")
            self.assertEqual(list(self.workspace.iterdir()), [])
        finally:
            os.chdir(previous)

    def test_bootstrap_chat_switching_keeps_instance_and_progress(self):
        self.workspace.mkdir()
        (self.workspace/"AGENTS.md").write_text("用户原有项目规则", encoding="utf-8")
        initial = self.start("codex")
        self.assertEqual(initial["profile"]["config"]["selected_ai"], "codex")
        self.settings(client="codex")
        self.call("record", payload={"kind": "course", "title": "合成已学单元", "subject": "刑法", "completed_on": "2027-09-13", "evidence": "合成真实反馈"})
        subsequent = self.start("workbuddy")
        self.assertEqual(initial["profile"]["instance_id"], subsequent["profile"]["instance_id"])
        self.assertEqual(len(subsequent["profile"]["course_units"]), 1)
        self.assertTrue((self.workspace/"开始复习.md").is_file())
        self.assertEqual((self.workspace/"AGENTS.md").read_text(encoding="utf-8"), "用户原有项目规则")
        self.assertEqual((self.workspace/".law-study/.gitignore").read_bytes(), b"*\n!.gitignore\n")

    def test_missing_bound_instance_is_not_recreated(self):
        self.start()
        service, _ = service_for(self.workspace)
        original = service.store.path.with_suffix(".saved")
        service.store.path.rename(original)
        with self.assertRaisesRegex(ValueError, "缺失"):
            self.start()
        self.assertFalse(service.store.path.exists())
        self.assertTrue(original.exists())

    def test_existing_binding_rejects_silent_instance_switch_and_stale_write(self):
        initial = self.start()
        with self.assertRaises(ValueError):
            bootstrap(self.workspace, "workbuddy", "合成请求", Path(self.temp.name)/"另一实例")
        self.settings()
        with self.assertRaises(ConflictError):
            self.call("configure", version=initial["version"], payload={"elective": "commercial"})

    def test_context_hides_private_feedback_before_online_ai_authorization(self):
        self.start()
        self.call("configure", payload={"year": 2027, "participation": "participating", "stage_override": 1})
        self.call("record", payload={"kind": "course", "title": "私密合成课程", "subject": "刑法", "completed_on": "2027-09-13", "evidence": "私密合成反馈"})
        state = self.call("context")
        self.assertNotIn("私密合成", json.dumps(state, ensure_ascii=False))
        self.assertIsNone(state["learning"])

    def test_selected_directory_registers_attachments_without_recursing_into_instance(self):
        self.start()
        self.settings()
        folder = self.workspace/"用户指定资料"
        (folder/"章节").mkdir(parents=True)
        (folder/"教材.md").write_text("合成教材", encoding="utf-8")
        (folder/"章节/原件.anyformat").write_bytes(b"any-format-original")
        (folder/".workbuddy").mkdir()
        (folder/".workbuddy/mcp.json").write_text("excluded-config", encoding="utf-8")
        result = self.call("import", path=str(folder), instruction="合成用户明确指定此资料目录")["result"]
        self.assertEqual(result["saved"], 2)
        result = self.call("import", path=str(self.workspace), instruction="合成用户指定整个复习工作空间")["result"]
        self.assertTrue(result["complete"])
        service, _ = service_for(self.workspace)
        data, _ = service.store.snapshot()
        self.assertNotIn("instance.json", [m["name"] for m in data["materials"]])
        self.assertNotIn("mcp.json", [m["name"] for m in data["materials"]])
        self.assertGreaterEqual(result["duplicates"], 2)

    def test_directory_partial_failure_keeps_successes_and_reports_failed_file(self):
        self.start()
        self.settings()
        folder = self.workspace/"资料"
        folder.mkdir()
        (folder/"a.md").write_bytes(b"saved-first")
        (folder/"b.md").write_bytes(b"failed-second")
        service, _ = service_for(self.workspace)
        original = service.import_file
        def importer(path, *args, **kwargs):
            if Path(path).name == "b.md":
                raise OSError("合成读取失败")
            return original(path, *args, **kwargs)
        with patch.object(service, "import_file", side_effect=importer):
            result = service.import_selected_path(folder, "综合", "other", service.store.snapshot()[1], "合成指定目录")
        self.assertEqual(result["saved"], 1)
        self.assertFalse(result["complete"])
        self.assertEqual(result["failed"][0]["file"], "b.md")
        self.assertEqual(len(service.store.snapshot()[0]["materials"]), 1)

    def test_chat_helper_preserves_search_provenance_and_reads_without_mcp(self):
        self.start()
        self.settings()
        file = self.workspace/"检索保存.md"
        file.write_text("合成实际检索保存内容", encoding="utf-8")
        origin = {"kind": "authorized_search", "url": "https://example.com/book", "evidence": "合成工具引用，范围内实际保存", "author": "合成作者", "year_version": "2027合成版"}
        result = self.call("import", path=str(file), instruction="合成授权检索并导入依据", origin=origin)["result"]
        self.assertEqual(result["material"]["source_kind"], "authorized_search")
        content = self.call("read_material", material_id=result["material_ids"][0], offset=0, limit=100)
        self.assertEqual(content["content"], "合成实际检索保存内容")

    def test_first_stage_native_plan_waits_for_monitor_and_requires_actual_receipt(self):
        self.start()
        self.settings(stage=1, automation=True)
        plan = self.call("automation_plan")
        self.assertEqual([r["action"] for r in plan["tasks"]], ["discover", "wait"])
        self.assertFalse(plan["native_installed"])
        self.assertEqual(self.call("monitor")["result"]["status"], "waiting_stage2")
        with self.assertRaises(ValueError):
            self.receipt()
        self.claim()
        with self.assertRaises(ValueError):
            self.receipt("monitor")
        self.receipt()
        plan = self.call("automation_plan")
        self.assertEqual([r["action"] for r in plan["tasks"]], ["keep", "wait"])
        self.settings(stage=2, automation=True)
        self.assertEqual(self.call("automation_plan")["tasks"][1]["action"], "reserve_create")
        with self.assertRaises(ValueError):
            self.receipt("monitor")
        self.receipt("monitor", task_id="synthetic-monitor-task")
        self.assertTrue(all(r["action"] == "keep" for r in self.call("automation_plan")["tasks"]))

    def test_native_task_updates_use_same_id_and_failures_do_not_create_duplicates(self):
        self.start()
        self.settings(automation=True)
        self.claim()
        self.receipt()
        auto = deepcopy(context(self.workspace)["profile"]["config"]["automation"])
        auto["learning_time"] = "10:15"
        self.call("configure", payload={"automation": auto})
        self.assertEqual(self.call("automation_plan")["tasks"][0]["action"], "discover")
        with self.assertRaises(ValueError):
            self.receipt(task_id="duplicate-task")
        self.receipt(status="failed")
        self.assertEqual(self.call("automation_plan")["tasks"][0]["action"], "discover")
        self.receipt(status="verified")
        self.assertEqual(self.call("automation_plan")["tasks"][0]["action"], "keep")

    def test_other_client_cannot_claim_the_same_automation(self):
        self.start()
        self.settings(automation=True)
        self.claim()
        self.call("configure", payload={"selected_ai": "codex"})
        self.assertTrue(all(r["action"] == "blocked_owner" for r in self.call("automation_plan")["tasks"]))
        with self.assertRaises(ValueError):
            self.claim()

    def test_automation_can_migrate_only_after_old_tasks_are_stopped(self):
        self.start()
        self.settings(automation=True)
        self.claim()
        self.receipt()
        with self.assertRaises(ValueError):
            self.call("automation", operation="release", payload={"evidence": "合成迁移请求"})
        self.receipt(status="disabled")
        self.call("automation", operation="release", payload={"evidence": "合成宿主已实际停用旧任务，用户确认迁移"})
        self.call("configure", payload={"selected_ai": "codex"})
        self.claim()
        self.assertNotEqual(self.call("automation_plan")["tasks"][0]["action"], "blocked_owner")

    def mcp(self, service, requests):
        head = [{"jsonrpc": "2.0", "id": "init", "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"}]
        output = io.StringIO()
        serve(service, io.StringIO("\n".join(json.dumps(r) for r in head+requests)), output)
        return [json.loads(line) for line in output.getvalue().splitlines()]

    def test_mcp_delivers_rules_and_entry_prompts_without_local_file_tools(self):
        self.start()
        service, _ = service_for(self.workspace)
        rows = self.mcp(service, [{"jsonrpc": "2.0", "id": 1, "method": "resources/list"},
            {"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {"uri": "lawstudy://guide/desktop"}},
            {"jsonrpc": "2.0", "id": 3, "method": "prompts/list"},
            {"jsonrpc": "2.0", "id": 4, "method": "prompts/get", "params": {"name": "daily-study"}}])
        self.assertIn("lawstudy://guide/project", {r["uri"] for r in rows[1]["result"]["resources"]})
        self.assertEqual(rows[2]["result"]["contents"][0]["text"], guide()["content"])
        self.assertEqual(len(rows[3]["result"]["prompts"]), 3)
        self.assertTrue(rows[4]["result"]["messages"])
        invalid = self.mcp(service, [{"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {"uri": "file:///private"}}])
        self.assertIn("error", invalid[-1])

    def test_mcp_image_content_is_real_and_requires_processing_authorization(self):
        self.start()
        service, _ = service_for(self.workspace)
        raw = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aSdcAAAAASUVORK5CYII=")
        material = service.import_material("合成.png", raw, "综合", "other", service.store.snapshot()[1])["material"]
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "read_material_image", "arguments": {"material_id": material["id"]}}}
        self.assertTrue(self.mcp(service, [request])[-1]["result"]["isError"])
        self.settings()
        content = self.mcp(service, [request])[-1]["result"]["content"][0]
        self.assertEqual(content["type"], "image")
        self.assertEqual(base64.b64decode(content["data"]), raw)

    def test_full_chat_learning_and_cross_chat_resume_uses_backend_without_web(self):
        self.start()
        self.settings()
        file = self.workspace/"合成题源.md"
        file.write_text("合成原件：甲乙完整事实与问题", encoding="utf-8")
        mid = self.call("import", path=str(file), instruction="合成用户在聊天提供这份附件", subject="刑法", category="questions")["result"]["material_ids"][0]
        qid = self.learn("register_question", identity="desktop/synthetic/1", source_name="合成题源", locator="第1题", subject="刑法", facts="甲乙完整合成事实", questions=["合成设问？"], material_id=mid, complete=True, verification="合成原件已核验")["question_id"]
        self.learn("answer_version", question_id=qid, answer_kind="author", content="合成参考答案", basis="合成同题核验", material_id=mid, same_question_verified=True)
        issued = self.learn("issue", on="2027-09-13")
        self.assertNotIn("合成参考答案", json.dumps(issued, ensure_ascii=False))
        sid = issued["session"]["id"]
        self.learn("submit_answer", session_id=sid, on="2027-09-13", answer="合成真实首答")
        explanation = {"knowledge": "合成充分知识点", "facts": "合成案情", "errors": "合成遗漏", "answer": "合成完整答案"}
        graded = self.learn("grade", session_id=sid, on="2027-09-13", has_errors=True, explanation=explanation)
        self.assertEqual(graded["status"], "awaiting_correction")
        self.assertNotIn("explanation", graded)
        resumed = context(self.workspace, "2027-09-14")
        self.assertEqual(resumed["learning"]["sessions"][0]["id"], sid)
        self.learn("submit_answer", session_id=sid, on="2027-09-14", answer="合成用户真实重答")
        self.learn("grade", session_id=sid, on="2027-09-14", has_errors=False, explanation=explanation)
        service, _ = service_for(self.workspace)
        self.assertEqual(len(service.store.snapshot()[0]["exercises"][0]["answer_history"]), 2)
        self.assertEqual(context(self.workspace, "2027-09-14")["plan"]["case_completed"], 0)

    def test_helper_real_process_reads_same_binding(self):
        state = self.start()
        process = subprocess.run([sys.executable, str(HELPER), "--workspace", str(self.workspace)], cwd=self.temp.name, capture_output=True, encoding="utf-8", timeout=15)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)["profile"]["instance_id"], state["profile"]["instance_id"])

    def test_install_packages_are_self_contained_and_have_valid_catalog_paths(self):
        result = build_packages(Path(self.temp.name)/"packages")
        for key in ("source", "skill", "codex_plugin"):
            self.assertTrue(result[key]["manifest_verified"])
            with zipfile.ZipFile(result[key]["path"]) as archive:
                self.assertIsNone(archive.testzip())
                license_path = {'source': 'LICENSE', 'skill': 'law-exam-study/LICENSE',
                                'codex_plugin': 'plugins/china-law-exam-subjective/LICENSE'}[key]
                self.assertEqual(archive.read(license_path), (ROOT/'LICENSE').read_bytes())
                self.assertFalse(any("/.law-study/" in name or "tests/.tmp/" in name for name in archive.namelist()))
                folder = Path(self.temp.name)/key
                archive.extractall(folder)
            if key == "skill":
                helper = folder/"law-exam-study/scripts/assistant.py"
            elif key == "codex_plugin":
                catalog = json.loads((folder/".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
                plugin = folder/catalog["plugins"][0]["source"]["path"]
                self.assertTrue((plugin/"plugin.json").is_file())
                helper = plugin/"skills/law-exam-study/scripts/assistant.py"
            else:
                continue
            request = json.dumps({"action": "guide"})
            process = subprocess.run([sys.executable, str(helper), "--workspace", str(self.workspace), "--request", "-"], input=request, cwd=self.temp.name, capture_output=True, encoding="utf-8", timeout=15)
            self.assertEqual(process.returncode, 0, process.stdout+process.stderr)
            self.assertEqual(json.loads(process.stdout)["content"], guide()["content"])


if __name__ == "__main__":
    unittest.main()
