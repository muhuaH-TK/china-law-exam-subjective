"""安装、模式、任务对账和迁移回归；宿主及用户证据全部为合成样本。"""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lawstudy.desktop import bootstrap, context, dispatch, service_for
from lawstudy.experience import acceptance_action
from lawstudy.mcp import call
from lawstudy.native_tasks import SPEC_KEYS
from lawstudy.store import ConflictError
from lawstudy import __version__

DAY = "2027-09-15"


class InstallationLifecycleTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parent/".tmp"
        parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)/"workspace"
        bootstrap(self.workspace, "codex", "合成用户指定本地目录")

    def service(self):
        return service_for(self.workspace)[0]

    def d(self, action, **kwargs):
        return dispatch(self.workspace, {"action": action, "version": context(self.workspace)["version"], **kwargs})

    def onboard(self, operation, **payload):
        return self.d("onboarding", operation=operation, payload={"user_instruction": "合成用户明确选择", **payload})

    def formal(self, stage=2, created=False):
        if created:
            self.onboard("project", status="created", project_id="synthetic-project", name="合成桌面项目", tool="synthetic.read_project", evidence="合成项目实际回读")
        else:
            self.onboard("project", status="skipped")
        self.onboard("start_formal")
        self.d("configure", payload={"year": 2027, "participation": "participating", "stage_override": stage,
            "authorization": {"online_ai": True, "search_mode": "search_and_import", "scope": "合成来源"},
            "automation": {"learning_requested": True, "learning_time": "09:30", "monitor_requested": True, "monitor_time": "22:00", "daily_limit": 10}})

    def spec(self, role="learning"):
        return next(r for r in self.d("automation_plan")["tasks"] if r["role"] == role)

    def auto(self, operation, role="learning", **payload):
        return self.d("automation", operation=operation, payload={"role": role, "tool": "synthetic.host", "evidence": "合成宿主实际查询证据", **payload})["result"]

    def observed(self, task_id="synthetic-task", enabled=True, role="learning"):
        return {"task_id": task_id, "enabled": enabled, "spec": {k: self.spec(role)[k] for k in SPEC_KEYS}}

    def create_task(self, role="learning", task_id="synthetic-task"):
        self.auto("claim")
        query = self.auto("reconcile", role, complete=True, candidates=[])
        intent = self.auto("reserve_create", role, discovery_id=query["id"])
        observed = self.observed(task_id=task_id, role=role)
        self.auto("record_task", role, status="verified", task_id=task_id, creation_id=intent["creation_id"], observed=observed)
        return observed

    def begin(self, trigger="chat", **extra):
        with patch("lawstudy.service.today_in", return_value=DAY):
            return self.d("run", operation="begin", payload={"on": DAY, "role": "learning", "trigger": trigger,
                "tool": "synthetic.host.run", "evidence": "合成运行证据", **extra})["result"]

    def chat(self, run, **extra):
        return self.d("run", operation="record_chat", payload={"run_id": run["run"]["id"],
            "chat_id": "synthetic-chat", "title": run["expected_title"], "tool": "synthetic.host.read_chat", "evidence": "合成聊天回读", **extra})

    def finish(self, run):
        return self.d("run", operation="finish", payload={"run_id": run["run"]["id"], "attempt_id": run["attempt_id"],
            "status": "passed", "tool": "synthetic.host.read_run", "evidence": "合成执行结果回读"})

    def test_fresh_bootstrap_does_not_start_teaching_even_with_year(self):
        self.d("configure", payload={"year": 2027, "participation": "participating", "stage_override": 2})
        state = context(self.workspace, DAY)
        self.assertEqual(state["status"], "needs_project_decision")
        self.assertEqual(state["experience"]["mode"], "setup")
        self.assertEqual(state["profile"]["lifecycle"], "setup")
        self.assertEqual(state["plan"]["case_target"], 0)
        with self.assertRaisesRegex(ValueError, "准备"):
            self.d("learning", payload={"action": "issue", "payload": {"on": DAY}})

    def test_project_is_first_but_skip_and_blocked_are_nonblocking_choices(self):
        with self.assertRaisesRegex(ValueError, "项目"):
            self.onboard("start_formal")
        self.onboard("project", status="blocked", tool="synthetic.project_check", evidence="合成已检查实际能力，本机无法创建")
        self.onboard("start_formal")
        state = context(self.workspace)
        self.assertEqual(state["experience"]["host_project"]["status"], "blocked")
        self.assertNotIn("project_id", state["experience"]["host_project"])
        self.assertEqual(state["experience"]["mode"], "formal")

    def test_test_instance_and_skip_materials_do_not_change_formal_bytes(self):
        self.formal()
        parent = self.service()
        before, binding_before = parent.store.path.read_bytes(), (self.workspace/".law-study/project.json").read_bytes()
        result = self.d("create_test", instruction="合成用户只要求功能测试")
        self.workspace = Path(result["test_workspace"])
        self.d("configure", payload={"year": 2027, "participation": "participating", "stage_override": 4})
        self.onboard("test_options", skip_materials=True)
        self.d("record", payload={"kind": "course", "title": "合成测试反馈", "subject": "刑法", "completed_on": DAY, "evidence": "合成反馈"})
        self.assertEqual(context(self.workspace)["experience"]["mode"], "test")
        self.assertIn("测试", context(self.workspace)["next_action"])
        self.assertFalse(self.service().monitor_state(DAY)["gate"]["eligible"])
        self.assertEqual(parent.store.path.read_bytes(), before)
        original_workspace = Path(self.temp.name)/"workspace"
        self.assertEqual((original_workspace/".law-study/project.json").read_bytes(), binding_before)
        with self.assertRaisesRegex(ValueError, "不能转"):
            self.onboard("start_formal")

    def test_formal_instance_cannot_enable_test_material_bypass(self):
        self.formal()
        with self.assertRaisesRegex(ValueError, "独立测试"):
            self.onboard("test_options", skip_materials=True)

    def test_legacy_active_does_not_implicitly_confirm_formal_or_reset_history(self):
        self.formal()
        self.d("record", payload={"kind": "course", "title": "合成旧课程", "subject": "刑法", "completed_on": DAY, "evidence": "合成旧反馈"})
        service = self.service()
        def old_version(data):
            data["config"].pop("usage_mode")
            data["desktop"].pop("host_project")
            data["lifecycle"] = "active"
        service.store.update(service.store.snapshot()[1], old_version)
        before = service.store.path.read_bytes()
        self.assertEqual(context(self.workspace, DAY)["plan"]["case_target"], 0)
        self.assertEqual(service.store.path.read_bytes(), before)
        self.onboard("project", status="skipped")
        self.onboard("start_formal")
        self.assertEqual(service.store.snapshot()[0]["course_units"][0]["title"], "合成旧课程")

    def test_discovery_required_before_create_and_orphan_is_recovered(self):
        self.formal()
        self.auto("claim")
        self.assertEqual(self.spec()["action"], "discover")
        with self.assertRaises(ValueError):
            self.auto("record_task", status="verified", task_id="orphan", observed=self.observed("orphan"))
        self.auto("reconcile", complete=True, candidates=[self.observed("orphan")])
        self.assertEqual(self.spec()["action"], "keep")
        self.assertEqual(self.spec()["task_id"], "orphan")
        self.assertNotIn("learning", self.service().store.snapshot()[0]["desktop"].get("creation_intents", {}))

    def test_ambiguous_and_incomplete_queries_never_allow_create(self):
        self.formal()
        self.auto("claim")
        for complete, candidates in ((False, []), (True, [self.observed("a"), self.observed("b")]), (True, [{"task_id": "unknown", "spec": {}}])):
            with self.subTest(complete=complete, count=len(candidates)):
                query = self.auto("reconcile", complete=complete, candidates=candidates)
                self.assertEqual(self.spec()["action"], "blocked_discovery")
                with self.assertRaises(ValueError):
                    self.auto("reserve_create", discovery_id=query["id"])

    def test_creation_reservation_is_atomic_and_failed_save_is_reconciled(self):
        self.formal()
        self.auto("claim")
        query = self.auto("reconcile", complete=True, candidates=[])
        previous = context(self.workspace)["version"]
        intent = self.auto("reserve_create", discovery_id=query["id"])
        with self.assertRaises(ConflictError):
            dispatch(self.workspace, {"action": "automation", "operation": "reserve_create", "version": previous,
                "payload": {"role": "learning", "discovery_id": query["id"], "tool": "synthetic.host", "evidence": "合成并发创建"}})
        with self.assertRaises(ValueError):
            self.auto("reserve_create", discovery_id=query["id"])
        self.assertEqual(self.spec()["action"], "awaiting_create_receipt")
        self.auto("reconcile", complete=True, candidates=[self.observed("created-before-crash")])
        self.assertEqual(self.spec()["action"], "keep")
        self.assertEqual(self.service().store.snapshot()[0]["desktop"]["creation_intents"]["learning"]["id"], intent["creation_id"])

    def test_config_change_needs_real_spec_readback_not_refreshing_receipt(self):
        self.formal()
        old = self.create_task()
        settings = deepcopy(context(self.workspace)["profile"]["config"]["automation"])
        settings["learning_time"] = "10:15"
        self.d("configure", payload={"automation": settings})
        with self.assertRaisesRegex(ValueError, "规格"):
            self.auto("record_task", status="verified", task_id=old["task_id"], observed=old)
        self.auto("reconcile", complete=True, candidates=[old])
        self.assertEqual(self.spec()["action"], "update")
        self.auto("record_task", status="verified", task_id=old["task_id"], observed=self.observed(old["task_id"]))
        self.assertEqual(self.spec()["action"], "keep")

    def test_created_only_does_not_claim_installed_and_requires_new_query(self):
        self.formal()
        self.auto("claim")
        query = self.auto("reconcile", complete=True, candidates=[])
        intent = self.auto("reserve_create", discovery_id=query["id"])
        self.auto("record_task", status="created", task_id="synthetic-created", creation_id=intent["creation_id"])
        self.assertEqual(self.spec()["action"], "discover")
        self.assertFalse(self.d("automation_plan")["native_installed"])
        self.auto("reconcile", complete=True, candidates=[self.observed("synthetic-created")])
        self.assertEqual(self.spec()["action"], "keep")

    def test_expired_discovery_cannot_authorize_creation(self):
        self.formal()
        self.auto("claim")
        query = self.auto("reconcile", complete=True, candidates=[])
        service = self.service()
        service.store.update(service.store.snapshot()[1], lambda data: data["desktop"]["discoveries"]["learning"].update(at="2000-01-01T00:00:00+00:00"))
        with self.assertRaises(ValueError):
            self.auto("reserve_create", discovery_id=query["id"])
        self.assertEqual(self.spec()["action"], "discover")

    def test_out_of_band_host_change_invalidates_old_good_receipt(self):
        self.formal()
        observed = self.create_task()
        observed["spec"]["time"] = "11:20"
        self.auto("reconcile", complete=True, candidates=[observed])
        self.assertEqual(self.spec()["action"], "update")
        self.assertFalse(self.d("automation_plan")["native_installed"])

    def test_skipping_project_does_not_require_removing_existing_host_group(self):
        self.formal()
        self.auto("claim")
        observed = self.observed("existing-host-task")
        observed["spec"]["host_project_id"] = "existing-host-group"
        self.auto("reconcile", complete=True, candidates=[observed])
        self.assertEqual(self.spec()["action"], "keep")
        self.assertEqual(context(self.workspace)["experience"]["host_project"]["status"], "skipped")

    def test_waiting_stage_one_monitor_does_not_mark_learning_uninstalled(self):
        self.formal(stage=1)
        self.create_task()
        plan = self.d("automation_plan")
        self.assertTrue(plan["native_installed"])
        self.assertEqual(plan["waiting_roles"], ["monitor"])
        self.assertEqual(plan["tasks"][1]["action"], "wait")

    def test_setup_does_not_recommend_disabling_existing_formal_task(self):
        self.formal()
        self.create_task()
        service = self.service()
        service.store.update(service.store.snapshot()[1], lambda data: data["config"].update(usage_mode="setup"))
        self.assertEqual(self.spec()["action"], "await_mode_confirmation")

    def test_legacy_active_runner_in_setup_does_not_prepare_learning_notice(self):
        from datetime import datetime
        from lawstudy.scheduler import enable, tick
        self.formal()
        service = self.service()
        service.store.update(service.store.snapshot()[1], lambda data: data["config"].update(usage_mode="setup"))
        enable(service, service.store.snapshot()[1], "合成用户指定测试本机运行器")
        result = tick(service, datetime.fromisoformat(DAY+"T10:00:00+08:00"))
        self.assertEqual(result["notifications"], [])

    def test_manual_run_is_not_scheduled_acceptance(self):
        self.formal()
        self.create_task()
        run = self.begin("manual", task_id="synthetic-task")
        self.chat(run)
        self.finish(run)
        checks = context(self.workspace)["experience"]["checks"]
        self.assertEqual(checks["manual_trigger"]["status"], "passed")
        self.assertEqual(checks["scheduled_trigger"]["status"], "pending")
        with self.assertRaises(ValueError):
            self.d("acceptance", payload={"check": "scheduled_trigger", "status": "passed", "kind": "client", "tool": "synthetic", "evidence": "合成声称通过"})

    def test_scheduled_event_requires_real_timing_and_chat_receipt(self):
        self.formal()
        self.create_task()
        event = {"trigger": "scheduled", "scheduled_for": DAY+"T09:30:00+08:00", "started_at": DAY+"T09:30:07+08:00", "event_id": "synthetic-scheduler-event"}
        with self.assertRaises(ValueError):
            self.begin("scheduled", task_id="synthetic-task")
        invalid = {**event, "trigger": "manual"}
        with self.assertRaises(ValueError):
            self.begin("scheduled", task_id="synthetic-task", scheduler_event=invalid)
        run = self.begin("scheduled", task_id="synthetic-task", scheduler_event=event)
        with self.assertRaisesRegex(ValueError, "聊天"):
            self.finish(run)
        self.chat(run)
        self.finish(run)
        self.assertEqual(context(self.workspace)["experience"]["checks"]["scheduled_trigger"]["status"], "passed")
        self.assertEqual(self.service().store.snapshot()[0]["exercises"], [])

    def test_daily_chat_title_project_and_cross_day_ids_are_checked(self):
        self.formal(created=True)
        run = self.begin()
        with self.assertRaisesRegex(ValueError, "标题"):
            self.chat(run, title="法考每日学习", project_id="synthetic-project")
        with self.assertRaisesRegex(ValueError, "项目"):
            self.chat(run, project_id="wrong-project")
        self.chat(run, project_id="synthetic-project")
        repeated = self.begin()
        self.assertTrue(repeated["resume"])
        self.assertEqual(repeated["run"]["id"], run["run"]["id"])
        next_day = self.d("run", operation="begin", payload={"on": "2027-09-16", "tool": "synthetic", "evidence": "合成次日聊天"})["result"]
        with self.assertRaisesRegex(ValueError, "每天"):
            self.chat(next_day, project_id="synthetic-project")
        self.assertEqual(self.spec()["name"], "法考每日学习")

    def test_test_task_is_once_and_requires_cleanup_without_affecting_parent(self):
        self.formal()
        parent = self.service()
        original = parent.store.path.read_bytes()
        child = self.d("create_test", instruction="合成测试一次调度")
        self.workspace = Path(child["test_workspace"])
        self.d("configure", payload={"year": 2027, "participation": "participating", "stage_override": 2})
        self.onboard("test_options", native_requested=True, run_at=DAY+"T09:31:00+08:00")
        observed = self.create_task()
        self.assertEqual(self.spec()["schedule_kind"], "once")
        self.assertIsNone(self.spec()["rrule"])
        self.assertIn("测试", self.spec()["name"])
        run = self.begin("manual", task_id="synthetic-task")
        self.assertEqual(self.spec()["action"], "disable")
        self.chat(run)
        self.finish(run)
        self.auto("record_task", status="disabled", task_id=observed["task_id"], observed={**observed, "enabled": False})
        self.assertFalse(context(self.workspace)["experience"]["test_options"]["cleanup_required"])
        self.assertEqual(parent.store.path.read_bytes(), original)

    def test_ui_capability_evidence_requires_before_and_after(self):
        with self.assertRaisesRegex(ValueError, "操作前"):
            self.onboard("capability", capability="project", method="ui", status="supported", tool="synthetic.ui", evidence="合成界面操作")
        self.onboard("capability", capability="project", method="ui", status="failed", tool="synthetic.ui", evidence="合成失败状态",
            before_ref="synthetic-before", after_ref="synthetic-after")
        self.assertEqual(context(self.workspace)["experience"]["capability_checks"][-1]["status"], "failed")

    def test_program_tests_and_other_client_do_not_prove_client_acceptance(self):
        self.formal()
        with self.assertRaisesRegex(ValueError, "程序测试"):
            self.d("acceptance", payload={"check": "client_loaded", "status": "passed", "kind": "program", "tool": "unittest", "evidence": "合成97通过"})
        with self.assertRaisesRegex(ValueError, "loaded_version"):
            self.d("acceptance", payload={"check": "client_loaded", "status": "passed", "kind": "client", "tool": "synthetic.client", "evidence": "合成旧缓存", "client_ref": "synthetic-loaded-chat", "loaded_version": "0.3.1"})
        self.d("acceptance", payload={"check": "client_loaded", "status": "passed", "kind": "client", "tool": "synthetic.client", "evidence": "合成新聊天读取技能", "client_ref": "synthetic-loaded-chat", "loaded_version": __version__})
        self.assertEqual(context(self.workspace)["experience"]["checks"]["client_loaded"]["status"], "passed")
        self.d("configure", payload={"selected_ai": "workbuddy"})
        self.assertEqual(context(self.workspace)["experience"]["checks"]["client_loaded"]["status"], "pending")

    def test_migration_requires_stopped_tasks_and_has_verified_backup(self):
        self.formal()
        observed = self.create_task()
        target = Path(self.temp.name)/"moved"
        args = {"user_instruction": "合成用户指定迁移", "target_workspace": str(target)}
        with self.assertRaisesRegex(ValueError, "停用"):
            self.d("migration", operation="prepare", payload=args)
        self.auto("record_task", status="disabled", task_id=observed["task_id"], observed={**observed, "enabled": False})
        self.d("record", payload={"kind": "course", "title": "合成已学单元", "subject": "刑法", "completed_on": DAY, "evidence": "合成真实反馈"})
        self.service().import_material("合成资料.md", b"synthetic-original", "刑法", "textbook", context(self.workspace)["version"])
        before = self.service().store.snapshot()[0]
        prepared = self.d("migration", operation="prepare", payload=args)["result"]
        self.assertTrue(prepared["backup_verified"])
        with self.assertRaises(ConflictError):
            self.d("configure", payload={"elective": "administrative"})
        self.d("migration", operation="execute", payload={"user_instruction": "合成用户确认按备份迁移"})
        actual = context(self.workspace)
        self.assertEqual(Path(actual["project"]["workspace"]), target)
        self.assertEqual(actual["profile"]["instance_id"], before["instance_id"])
        self.assertEqual(actual["profile"]["course_units"], before["course_units"])
        moved, _ = service_for(target)
        self.assertEqual((moved.store.directory/before["materials"][0]["path"]).read_bytes(), b"synthetic-original")
        self.assertTrue((Path(prepared["source_data_dir"])/"instance.json").exists())
        self.assertTrue((moved.store.directory/"integration/workbuddy-mcp.json").exists())

    def test_failed_migration_can_restore_source_without_deletion(self):
        self.formal()
        service = self.service()
        target = Path(self.temp.name)/"failed-target"
        self.d("migration", operation="prepare", payload={"user_instruction": "合成用户迁移", "target_workspace": str(target)})
        with patch("lawstudy.migration.copy_verified", side_effect=OSError("合成复制失败")), self.assertRaises(OSError):
            self.d("migration", operation="execute", payload={"user_instruction": "合成执行"})
        self.assertEqual(service.store.snapshot()[0]["migration"]["status"], "failed")
        self.d("migration", operation="cancel", payload={"user_instruction": "合成用户恢复原位置"})
        self.d("configure", payload={"elective": "commercial"})
        self.assertTrue(service.store.path.exists())

    def test_external_file_change_after_freeze_is_not_silently_lost(self):
        self.formal()
        source = self.service()
        material = source.import_material("合成原件.md", b"before-freeze", "刑法", "textbook", context(self.workspace)["version"])["material"]
        target = Path(self.temp.name)/"externally-changed-target"
        self.d("migration", operation="prepare", payload={"user_instruction": "合成迁移", "target_workspace": str(target)})
        original = source.store.directory/material["path"]
        original.write_bytes(b"external-change")
        with self.assertRaisesRegex(ValueError, "源资料发生变化"):
            self.d("migration", operation="execute", payload={"user_instruction": "合成执行"})
        self.assertEqual(original.read_bytes(), b"external-change")
        self.assertFalse((target/".law-study/project.json").exists())

    def test_bound_target_after_interruption_can_finalize_but_not_restore_second_writer(self):
        self.formal()
        target = Path(self.temp.name)/"bound-before-failure"
        self.d("migration", operation="prepare", payload={"user_instruction": "合成迁移", "target_workspace": str(target)})
        from lawstudy.desktop import bootstrap as original
        def interrupted(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("合成目标绑定后中断")
        with patch("lawstudy.desktop.bootstrap", side_effect=interrupted), self.assertRaises(OSError):
            self.d("migration", operation="execute", payload={"user_instruction": "合成执行"})
        with self.assertRaisesRegex(ValueError, "两个"):
            self.d("migration", operation="cancel", payload={"user_instruction": "合成尝试恢复"})
        self.d("migration", operation="finalize", payload={"user_instruction": "合成核实目标后完成"})
        self.assertEqual(Path(context(self.workspace)["project"]["workspace"]), target)

    def test_mcp_exposes_onboarding_and_cannot_route_test_into_wrong_instance(self):
        service = self.service()
        call(service, "onboarding_action", {"action": "project", "payload": {"status": "skipped", "user_instruction": "合成用户跳过"}, "version": service.store.snapshot()[1]})
        child = call(service, "create_test_instance", {"workspace": str(self.workspace), "user_instruction": "合成测试要求"})
        with self.assertRaisesRegex(ValueError, "当前MCP"):
            call(service, "create_test_instance", {"workspace": child["test_workspace"], "user_instruction": "合成错误连接"})


if __name__ == "__main__":
    unittest.main()
