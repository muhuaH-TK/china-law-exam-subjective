from support import confirm_formal
"""跨模块行为测试；所有题干和公告均为明确的合成测试数据。"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from lawstudy.calendar import extract, refresh
from lawstudy.integration import generate
from lawstudy.mcp import serve
from lawstudy.network import public_url
from lawstudy.scheduler import enable, schedule_files, tick
from lawstudy.service import Service
from lawstudy.store import ConflictError

TMP = Path(__file__).resolve().parent/".tmp"


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        TMP.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=TMP)
        self.service = Service(self.temp.name)
        confirm_formal(self.service)
        self.configure({"year": 2027, "participation": "participating", "stage_override": 2,
                        "authorization": {"search_mode": "search_and_import", "scope": "合成测试题源", "online_ai": True}})
        self.material = self.service.import_material("合成测试.md", "合成测试：甲与乙的具体事件及原设问。".encode(), "刑法", "questions", self.version())["material"]["id"]

    def tearDown(self):
        self.assertTrue(Path(self.temp.name).resolve().is_relative_to(TMP.resolve()))
        self.temp.cleanup()

    def version(self):
        return self.service.store.snapshot()[1]

    def configure(self, payload):
        self.service.configure(payload, self.version())

    def learn(self, action, payload):
        return self.service.learning(action, payload, self.version())

    def question(self, number="1", subject="刑法", **kwargs):
        result = self.learn("register_question", {"identity": "synthetic/2027/"+number, "facts": "合成测试事件编号"+number+"：甲乙发生一项具体行为，保留全部事实。", "questions": ["合成测试设问：分析该行为的效果。"],
            "source_name": "合成测试题源", "locator": "题"+number, "material_id": self.material, "subject": subject,
            "complete": True, "verification": "核验合成测试原文及全部设问，无真实法律结论", **kwargs})
        return result["question_id"]

    def answer_version(self, q, kind="author", **kwargs):
        return self.learn("answer_version", {"question_id": q, "answer_kind": kind, "content": "合成参考答案，仅用于验证流程。", "basis": "已核验合成题身份、版本和全部设问", "material_id": self.material, "same_question_verified": True, **kwargs})

    def issue(self, **kwargs):
        return self.learn("issue", {"on": "2027-09-13", **kwargs})

    def explanation(self):
        return {"knowledge": "合成知识点：条件成立与不成立分支、例外和后果，用于流程测试。", "facts": "甲乙的合成事件。", "errors": "真实测试答复遗漏了测试条件。", "answer": "合成题完整答案，不能据此解释真实法律。"}

    def grade(self, s, errors=True, day="2027-09-13", **kwargs):
        return self.learn("grade", {"session_id": s, "on": day, "has_errors": errors, "explanation": self.explanation(), **kwargs})

    def test_complete_flow_waits_for_real_reanswer_and_retains_first_answer(self):
        q = self.question()
        self.answer_version(q)
        issued = self.issue()
        view = json.dumps(issued, ensure_ascii=False)
        self.assertNotIn("合成参考答案", view)
        self.assertNotIn("knowledge", view)
        s = issued["session"]["id"]
        with self.assertRaises(ValueError):
            self.learn("grading_context", {"session_id": s})
        self.learn("submit_answer", {"session_id": s, "on": "2027-09-13", "answer": "真实测试首答"})
        result = self.grade(s)
        self.assertEqual(result["status"], "awaiting_correction")
        self.assertNotIn("explanation", result)
        with self.assertRaises(ValueError):
            self.grade(s)
        self.learn("submit_answer", {"session_id": s, "on": "2027-09-13", "answer": "用户实际重答"})
        result = self.grade(s, False)
        self.assertEqual(result["status"], "completed")
        data, _ = self.service.store.snapshot()
        self.assertEqual(data["exercises"][0]["answer_history"][0]["answer"], "真实测试首答")
        self.assertEqual(len(data["errors"]), 1)
        self.assertEqual(data["errors"][0]["reviews"], [])
        self.assertEqual(self.service.status("2027-09-13")["plan"]["historical_target"], 1)

    def test_complete_original_dedup_does_not_depend_on_title(self):
        q = self.question()
        data, _ = self.service.store.snapshot()
        original = data["questions"][0]
        result = self.learn("register_question", {**original, "identity": "renamed/44", "source_name": "另一文件名", "complete": True})
        self.assertTrue(result["duplicate"])
        self.assertEqual(result["question_id"], q)

    def test_missing_question_and_missing_answer_do_not_start_exercise(self):
        with self.assertRaises(ValueError):
            self.question(complete=False)
        self.question()
        self.assertFalse(self.issue()["available"])

    def test_ai_reference_cannot_get_formal_score_or_impersonate_official(self):
        q = self.question()
        self.answer_version(q, "ai_reference")
        with self.assertRaises(ValueError):
            self.issue()
        s = self.issue(no_standard_authorization="用户明确选择无标准答案练习")["session"]["id"]
        self.learn("submit_answer", {"session_id": s, "on": "2027-09-13", "answer": "真实测试答复"})
        with self.assertRaises(ValueError):
            self.grade(s, False, score=7, maximum=10, scoring_basis="AI估算不可用")
        result = self.grade(s, False)
        self.assertIn("非已公布标准答案", result["label"])
        self.answer_version(q, "author")
        data, _ = self.service.store.snapshot()
        self.assertEqual(len(data["questions"][0]["answers"]), 2)
        self.assertEqual(data["sessions"][0]["grades"][0]["label"], "自拟／AI参考，非已公布标准答案")

    def test_third_stage_rejects_authored_cases_even_if_explicit(self):
        self.configure({"stage_override": 3})
        q = self.question(kind="authored", authorization="用户要求编具体案例", concrete_case=True)
        self.answer_version(q, "ai_reference")
        self.assertFalse(self.issue(authorization="仍不得在第三阶段凑自拟题")["available"])

    def test_first_stage_missing_original_is_skipped_without_question_debt(self):
        self.configure({"stage_override": 1})
        self.service.record({"kind": "course", "title": "合成已完成单元", "subject": "刑法", "completed_on": "2027-09-13", "evidence": "合成反馈已学完"}, self.version())
        data, _ = self.service.store.snapshot()
        uid = data["course_units"][0]["id"]
        q = self.question(unit_ids=[uid])
        self.answer_version(q)
        # 一般题库不是对应教材原题，不自动替代。
        self.assertFalse(self.issue()["available"])
        self.learn("unit_skip", {"unit_id": uid, "evidence": "实际核对合成教材，此单元无适配原题"})
        self.assertEqual(self.service.status("2027-09-13")["plan"]["case_remaining"], 0)
        self.learn("unit_reopen", {"unit_id": uid, "authorization": "用户明确要求继续，允许自拟具体案例"})
        self.assertEqual(self.service.status("2027-09-13")["plan"]["case_remaining"], 1)

    def test_theory_cycles_continue_after_first_complete_round(self):
        self.configure({"stage_override": 4})
        mid = self.service.import_material("合成背诵轮换.md", "合成原文".encode(), "理论法", "theory", self.version())["material"]["id"]
        self.learn("themes", {"material_id": mid, "themes": [{"title": name, "text": name*1200, "locator": "合成主题位置"} for name in ("甲", "乙")]})
        titles = []
        for day in ("2027-10-11", "2027-10-12", "2027-10-13", "2027-10-14"):
            result = self.learn("theory_start", {"on": day})
            titles.append(result["activity"]["titles"])
            self.learn("theory_feedback", {"activity_id": result["activity"]["id"], "on": day, "feedback": "合成用户反馈已背诵完整主题"})
        self.assertEqual(titles, [["甲"], ["乙"], ["甲"], ["乙"]])

    def test_cross_day_pending_resumes_and_counts_completion_day(self):
        q = self.question()
        self.answer_version(q)
        s = self.issue()["session"]["id"]
        self.learn("submit_answer", {"session_id": s, "on": "2027-09-13", "answer": "首日一部分", "complete": False})
        self.assertTrue(self.issue(on="2027-09-14")["resume"])
        self.learn("submit_answer", {"session_id": s, "on": "2027-09-14", "answer": "次日答完"})
        self.assertEqual(self.service.status("2027-09-13")["plan"]["case_completed"], 0)
        self.assertEqual(self.service.status("2027-09-14")["plan"]["case_completed"], 1)

    def test_third_history_opt_in_and_same_day_answer_search_gate(self):
        q = self.question()
        self.answer_version(q)
        s = self.issue()["session"]["id"]
        self.learn("submit_answer", {"session_id": s, "on": "2027-09-13", "answer": "真实首答"})
        self.grade(s)
        self.learn("submit_answer", {"session_id": s, "on": "2027-09-13", "answer": "真实订正"})
        self.grade(s, False)
        self.configure({"stage_override": 3})
        with self.assertRaises(ValueError):
            self.issue(on="2027-09-15", mode="history")
        with self.assertRaises(ValueError):
            self.issue(on="2027-09-15", mode="history", authorization="用户选择历史订正")
        self.learn("answer_check", {"question_id": q, "on": "2027-09-15", "outcome": "not_found", "evidence": "本次授权实际检索未找到新版本；合成测试引用"})
        issued = self.issue(on="2027-09-15", mode="history", authorization="用户选择历史订正")
        self.assertTrue(issued["available"])
        with self.assertRaises(ValueError):
            self.issue(on="2027-09-15")

    def test_theory_complete_theme_and_cloze_is_original_text(self):
        material = self.service.import_material("合成背诵.txt", "甲乙丙丁戊".encode(), "理论法", "theory", self.version())["material"]["id"]
        self.learn("themes", {"material_id": material, "themes": [{"title": "完整长主题", "text": "甲乙"*700, "locator": "原件主题一"}, {"title": "短主题", "text": "丙丁", "locator": "原件主题二"}]})
        started = self.learn("theory_start", {"on": "2027-09-13"})
        self.assertEqual(len(started["themes"]), 1)
        self.assertEqual(len(started["themes"][0]["text"]), 1400)
        aid = started["activity"]["id"]
        test = self.learn("theory_test", {"activity_id": aid, "user_feedback": "用户反馈已背完", "spans": [[0, 2], [4, 6]]})
        self.assertEqual(test["blanks"], 2)
        self.assertTrue(test["text"].startswith("[1]甲乙[2]"))
        self.assertNotIn("cloze_keys", test)
        resumed = self.learn("theory_start", {"on": "2027-09-13"})
        self.assertNotIn("cloze_keys", json.dumps(resumed))
        self.assertNotIn("themes", resumed)
        self.learn("theory_feedback", {"activity_id": aid, "on": "2027-09-13", "feedback": "[1]甲乙 [2]甲乙"})
        self.learn("theory_evaluate", {"activity_id": aid, "evaluation": "按真实语义核对，无实质遗漏。"})
        self.assertEqual(self.service.status("2027-09-13")["plan"]["case_completed"], 0)

    def source(self):
        self.service.add_source({"name": "合成题源", "url": "https://example.com/feed", "subject": "刑法"}, self.version())
        d, _ = self.service.store.snapshot()
        source = d["sources"][-1]
        self.service.source_action("configure", {"source_id": source["id"], "mode": "json", "enabled": True,
            "authorization": "用户授权持续监测合成来源", "identity_verification": "合成测试身份已核验"}, self.version())
        return source["id"]

    def capture(self, source, entries, day="2027-09-13"):
        return self.service.ingest_capture({"source_id": source, "on": day, "entries": entries, "method": "public_feed", "evidence": "合成测试实际响应引用"}, self.version())

    def test_global_monitor_budget_versions_cursor_backlog_and_no_false_learning(self):
        source = self.source()
        auto = deepcopy(self.service.status()["profile"]["config"]["automation"])
        auto["daily_limit"] = 2
        self.configure({"automation": auto})
        entries = [{"external_id": str(i), "url": "https://example.com/"+str(i), "text": "合成原帖"+str(i)} for i in range(3)]
        result = self.capture(source, entries)
        self.assertEqual(len(result["processed"]), 2)
        self.assertEqual(result["deferred"], 1)
        self.assertEqual(self.capture(source, entries)["daily_used"], 2)
        data, _ = self.service.store.snapshot()
        self.assertEqual(len(data["monitor_backlog"]), 1)
        self.assertEqual(data["sources"][0]["cursor"]["last_processed_id"], "1")
        self.assertEqual(data["errors"], [])
        self.assertEqual(data["exercises"], [])
        updated = [{**entries[0], "text": "同帖后来补充答案"}]
        self.assertEqual(len(self.capture(source, updated, "2027-09-14")["processed"]), 1)
        data, _ = self.service.store.snapshot()
        self.assertEqual(len(data["source_items"][0]["versions"]), 2)

    def test_search_only_capture_waits_import_approval(self):
        source = self.source()
        self.configure({"authorization": {"search_mode": "search_only", "scope": "合成题源", "online_ai": True}})
        result = self.capture(source, [{"external_id": "a", "url": "https://example.com/a", "text": "候选原帖"}])
        data, _ = self.service.store.snapshot()
        self.assertEqual(data["source_items"][0]["status"], "candidate")
        with self.assertRaises(ValueError):
            self.question(material_id=None, source_item_id=result["processed"][0])

    def test_source_correction_holds_question_until_recheck_and_preserves_original(self):
        source = self.source()
        entry = {"external_id": "original", "url": "https://example.com/original", "text": "合成原帖正文"}
        result = self.capture(source, [entry])
        q = self.question(material_id=None, source_item_id=result["processed"][0])
        self.answer_version(q)
        self.capture(source, [{**entry, "text": "更正了一项实际案情"}])
        self.assertFalse(self.issue()["available"])
        self.learn("source_recheck", {"question_id": q, "facts_unchanged": False, "verification": "合成核验：更正已改变原题事实，原答案不能沿用"})
        data, _ = self.service.store.snapshot()
        self.assertEqual(data["questions"][0]["status"], "obsolete_source_changed")
        self.assertIn("保留全部事实", data["questions"][0]["facts"])
        self.assertEqual(len(data["source_items"][0]["versions"]), 2)

    def test_three_presets_disabled_and_do_not_inherit_old_authorization(self):
        self.service.source_action("presets", {}, self.version())
        data, _ = self.service.store.snapshot()
        self.assertEqual(len(data["sources"]), 3)
        self.assertTrue(all(not s["requested"] for s in data["sources"]))
        self.assertIn("2342740757", json.dumps(data["sources"]))
        self.assertTrue(all(not s.get("authorization") for s in data["sources"]))

    def test_history_teaching_does_not_require_extra_first_attempt(self):
        q = self.question()
        self.answer_version(q)
        self.service.record({"kind": "answer", "question_id": q, "subject": "刑法", "on": "2027-09-10", "answer": "真实旧原答", "complete": True}, self.version())
        self.service.record({"kind": "error", "question_id": q, "detail": "实际旧错漏"}, self.version())
        # 本日完成一套别科，获得一个历史订正名额。
        self.service.record({"kind": "answer", "question_id": "other", "subject": "民事综合", "on": "2027-09-13", "answer": "今日实际答完", "complete": True}, self.version())
        self.learn("answer_check", {"question_id": q, "on": "2027-09-13", "outcome": "not_found", "evidence": "合成同步检索证据"})
        issued = self.issue(mode="history")
        sid = issued["session"]["id"]
        self.assertEqual(issued["session"]["status"], "awaiting_knowledge")
        self.learn("teach", {"session_id": sid, "on": "2027-09-13", "knowledge": "先说明全部合成条件与例外。"})
        self.learn("submit_answer", {"session_id": sid, "on": "2027-09-13", "answer": "用户一次实际重答"})
        result = self.grade(sid, False)
        self.assertEqual(result["status"], "completed")
        data, _ = self.service.store.snapshot()
        self.assertEqual(len(data["sessions"][0]["answers"]), 1)
        original = next(r for r in data["exercises"] if r["question_id"] == q)
        self.assertEqual(len(original["answer_history"]), 2)
        self.assertEqual(original["answer_history"][-1]["answer"], "用户一次实际重答")
        self.assertTrue(original["answer_history"][-1]["prompted"])
        self.assertEqual(original["completed_on"], "2027-09-10")
        self.assertEqual(len(data["errors"][0]["reviews"]), 1)
        self.assertFalse(data["errors"][0]["reviews"][0]["independent_pass"])

    def test_monitor_backlog_recovered_next_day_without_duplicate_budget(self):
        source = self.source()
        auto = deepcopy(self.service.status()["profile"]["config"]["automation"])
        auto.update(daily_limit=1, monitor_requested=True)
        self.configure({"automation": auto})
        rows = [{"external_id": str(i), "url": "https://example.com/"+str(i), "text": "测试帖"+str(i)} for i in range(2)]
        self.capture(source, rows)
        def empty_feed(*args, **kwargs):
            return {"url": "https://example.com/feed", "body": '{"items":[]}', "content_type": "application/json"}
        self.service.monitor_once("2027-09-14", empty_feed)
        data, _ = self.service.store.snapshot()
        self.assertEqual(len(data["source_items"]), 2)
        self.assertEqual(data["monitor_backlog"], [])
        self.assertEqual(data["monitor_budget"]["2027-09-14"]["used"], 1)

    def test_searched_material_keeps_provenance_and_search_only_needs_import_choice(self):
        origin = {"kind": "authorized_search", "url": "https://example.com/book", "evidence": "实际工具检索引用", "author": "合成作者", "year_version": "2027合成版"}
        self.configure({"authorization": {"search_mode": "search_only", "scope": "测试范围", "online_ai": True}})
        with self.assertRaises(ValueError):
            self.service.import_material("检索.md", "合成检索内容".encode(), "刑法", "questions", self.version(), origin)
        result = self.service.import_material("检索.md", "合成检索内容".encode(), "刑法", "questions", self.version(), {**origin, "import_authorization": "用户选择导入"})
        self.assertEqual(result["material"]["source_kind"], "authorized_search")
        self.assertEqual(result["material"]["origin"]["url"], origin["url"])

    def test_mcp_real_subprocess_has_clean_stdout_and_valid_handshake(self):
        import subprocess
        import sys
        requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "synthetic-test", "version": "1"}}},
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "record_interaction", "arguments": {"version": self.version(), "user_instruction": "真实UTF8中文传输测试", "record": {"kind": "course", "title": "中文课程名原样保存", "subject": "刑法", "completed_on": "2027-09-13", "evidence": "合成反馈"}}}},
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_state", "arguments": {}}}]
        process = subprocess.run([sys.executable, "run.py", "mcp", "--data-dir", str(self.service.store.directory)],
            input="\n".join(json.dumps(r, ensure_ascii=False) for r in requests)+"\n", text=True, encoding="utf-8", capture_output=True, timeout=15,
            cwd=Path(__file__).resolve().parent.parent)
        self.assertEqual(process.returncode, 0, process.stderr)
        rows = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1]["result"]["isError"], False)
        actual = json.loads(rows[2]["result"]["content"][0]["text"])
        self.assertEqual(actual["profile"]["course_units"][0]["title"], "中文课程名原样保存")

    def test_scheduler_current_day_idempotent_and_does_not_fake_client_wakeup(self):
        auto = deepcopy(self.service.status()["profile"]["config"]["automation"])
        auto["learning_requested"] = True
        self.configure({"automation": auto})
        self.assertEqual(tick(self.service)["status"], "device_not_enabled")
        enable(self.service, self.version(), "用户指定本机")
        now = datetime(2027, 9, 13, 10, 0, tzinfo=timezone(timedelta(hours=8)))
        result = tick(self.service, now)
        self.assertEqual(len(result["notifications"]), 1)
        self.assertEqual(tick(self.service, now)["notifications"], [])
        data, _ = self.service.store.snapshot()
        self.assertEqual(len(data["inbox"]), 1)
        self.assertEqual(data["exercises"], [])
        self.assertEqual(set(data["job_days"]), {"2027-09-13"})
        self.assertIn("未配置AI客户端", result["note"])
        files = schedule_files(self.service)
        self.assertFalse(files["installed"])
        self.assertTrue((Path(files["directory"])/"windows-task.xml").exists())

    def test_client_configs_precise_paths_are_valid_and_global_config_untouched(self):
        import tomllib
        generated = generate(self.service)
        folder = Path(generated["directory"])
        codex = tomllib.loads((folder/"codex-mcp.toml").read_text(encoding="utf-8"))
        workbuddy = json.loads((folder/"workbuddy-mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(codex["mcp_servers"]["law-study"]["args"], workbuddy["mcpServers"]["law-study"]["args"])
        self.assertEqual(codex["mcp_servers"]["law-study"]["args"][-1], str(self.service.store.directory))
        self.assertNotIn("runtime", workbuddy["mcpServers"]["law-study"])

    def test_mcp_handshake_tools_call_and_privacy(self):
        self.configure({"authorization": {"search_mode": "provided_only", "scope": "", "online_ai": False}})
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "clientInfo": {"name": "synthetic", "version": "1"}, "capabilities": {}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_state", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "read_material", "arguments": {"material_id": self.material}}},
            {"jsonrpc": "2.0", "id": 5, "method": "ping"}]
        out = io.StringIO()
        serve(self.service, io.StringIO("\n".join(json.dumps(m) for m in messages)+"\n"), out)
        rows = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["result"]["protocolVersion"], "2025-11-25")
        self.assertGreaterEqual(len(rows[1]["result"]["tools"]), 15)
        self.assertNotIn("合成测试.md", rows[2]["result"]["content"][0]["text"])
        self.assertTrue(rows[3]["result"]["isError"])
        self.assertEqual(rows[4]["result"], {})

    def test_calendar_body_not_publish_date_and_cache_preserved_when_offline(self):
        source = {"url": "https://www.moj.gov.cn/2027/test.html", "body": "<title>2027年国家统一法律职业资格考试公告（合成测试）</title><p>客观题考试时间为9月12日、13日。</p><p>客观题考试成绩将于9月19日公布。</p><p>主观题考试时间为10月18日。</p>", "published_at": "2027-06-05", "retrieval_method": "client_web_tool", "evidence_ref": "synthetic-test-ref"}
        report = refresh(self.service, self.version(), [source])
        self.assertEqual(report["outcomes"]["results"], "verified")
        data, _ = self.service.store.snapshot()
        self.assertEqual(data["config"]["exam_dates"]["objective_end"]["date"], "2027-09-13")
        self.assertEqual(data["config"]["exam_dates"]["results"]["date"], "2027-09-19")
        def offline(*a, **k):
            raise OSError("合成断网")
        report = refresh(self.service, self.version(), reader=offline)
        self.assertEqual(report["outcomes"]["results"], "cached_verified_fetch_failed")
        self.assertEqual(self.service.store.snapshot()[0]["config"]["exam_dates"]["results"]["date"], "2027-09-19")
        conflict = {**source, "body": source["body"].replace("9月19日", "9月20日")}
        report = refresh(self.service, self.version(), [conflict])
        self.assertEqual(report["outcomes"]["results"], "conflict")
        self.assertEqual(self.service.store.snapshot()[0]["config"]["exam_dates"]["results"]["status"], "conflict")

    def test_calendar_rejects_other_year_regions_and_url_spoof(self):
        for title in ("2026年国家统一法律职业资格考试公告", "2027年北京市法律职业资格考试考区公告"):
            self.assertEqual(extract(2027, {"url": "https://www.moj.gov.cn/test.html", "body": "<title>"+title+"</title><p>客观题考试时间为9月12日。</p>"}), [])
        with self.assertRaises(ValueError):
            extract(2027, {"url": "https://moj.gov.cn.evil.example/test", "body": ""})
        for url in ("http://example.com", "https://127.0.0.1/", "https://user:password@example.com/"):
            with self.assertRaises(ValueError):
                public_url(url)

    def test_calendar_separate_section_headings_and_conflict_never_silently_clears(self):
        source = {"url": "https://legalinfo.moj.gov.cn/2027/test.html", "body": "<title>2027年法律职业资格考试政策问答（合成测试）</title><p>客观题考试：</p><p>网上报名时间为6月15日至6月29日；</p><p>下载打印准考证时间为9月2日至9月11日；</p><p>考试时间为9月12日、13日两个批次；</p><p>考试成绩及合格分数线公布时间为9月19日。</p><p>主观题考试：</p><p>考试时间为10月18日。</p>", "retrieval_method": "client_web_tool", "evidence_ref": "synthetic"}
        self.assertEqual({r["key"]: r["date"] for r in extract(2027, source)}, {"objective_end": "2027-09-13", "results": "2027-09-19", "subjective": "2027-10-18"})
        refresh(self.service, self.version(), [source])
        changed = {**source, "body": source["body"].replace("9月19日", "9月20日")}
        refresh(self.service, self.version(), [changed])
        refresh(self.service, self.version(), [changed])
        self.assertEqual(self.service.store.snapshot()[0]["config"]["exam_dates"]["results"]["status"], "conflict")

    def test_topic_export_real_answer_pdf_pages_and_word_structure(self):
        from importlib.util import find_spec
        if not (find_spec("reportlab") and find_spec("pypdf")):
            self.skipTest("可选文档导出依赖未安装")
        from lawstudy.documents import export_topic
        q = self.question()
        self.answer_version(q)
        s = self.issue()["session"]["id"]
        self.learn("submit_answer", {"session_id": s, "on": "2027-09-13", "answer": "必须保留的真实测试原答"})
        self.grade(s)
        self.learn("submit_answer", {"session_id": s, "on": "2027-09-13", "answer": "必须保留的真实测试订正"})
        self.grade(s, False)
        data, _ = self.service.store.snapshot()
        card = data["errors"][0]["id"]
        result = export_topic(self.service, {"title": "合成专题导出验证", "card_ids": [card], "topics": [{"title": "合成知识点", "knowledge": "测试条件、否定分支、例外和后果，均为合成数据。", "navigation": "条件成立与不成立的合成分支。", "card_ids": [card], "tables": [{"headers": ["判断条件", "分支后果"], "rows": [["合成条件一", "合成后果一"], ["不成立", "另一合成后果"]]}]}]}, self.version())
        self.assertTrue(result["pdf_page_numbers_verified"])
        self.assertFalse(result["learning_completed"])
        output = Path(result["directory"])
        text = (output/"复习资料.md").read_text(encoding="utf-8")
        self.assertIn("必须保留的真实测试原答", text)
        self.assertIn("必须保留的真实测试订正", text)
        self.assertIn("知识点提示后，非独立通过", text)
        self.assertIn("PDF第", text)
        with zipfile.ZipFile(output/"复习资料.docx") as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn("word/document.xml", archive.namelist())


if __name__ == "__main__":
    unittest.main()
