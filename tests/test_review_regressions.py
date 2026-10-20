from support import confirm_formal
"""2026-10-19审查回归；只使用隔离实例、合成题面和合成公告。"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lawstudy.desktop import bootstrap, context, dispatch
from lawstudy.documents import build_sections, export_topic
from lawstudy.mcp import call
from lawstudy.policy import due_cards
from lawstudy.service import Service


class ReviewRegressions(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parent/".tmp"
        parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.service = Service(Path(self.temp.name)/"data")
        confirm_formal(self.service)
        self.configure({"year": 2027, "participation": "participating", "stage_override": 2,
                        "authorization": {"online_ai": True, "search_mode": "search_and_import", "scope": "合成测试"}})

    def version(self):
        return self.service.store.snapshot()[1]

    def configure(self, value):
        self.service.configure(value, self.version(), user_instruction="合成用户配置依据")
        confirm_formal(self.service)

    def learn(self, action, value):
        return self.service.learning(action, value, self.version())

    def question(self, subject="刑法", category="questions"):
        material = self.service.import_material(subject+".md", subject.encode(), subject, category, self.version())["material"]["id"]
        q = self.learn("register_question", {"identity": "synthetic/review/"+subject, "subject": subject,
            "facts": "合成案情："+subject+"甲乙发生具体事件。", "questions": ["合成设问？"], "source_name": "合成来源",
            "locator": "第一题", "material_id": material, "verification": "合成完整原件核验", "complete": True})["question_id"]
        self.learn("answer_version", {"question_id": q, "answer_kind": "author", "content": "合成答案",
            "basis": "合成同题全部设问", "material_id": material, "same_question_verified": True})
        return q

    def session(self):
        self.question()
        return self.learn("issue", {"on": "2027-09-13"})["session"]["id"]

    def error(self):
        sid = self.session()
        self.learn("submit_answer", {"session_id": sid, "on": "2027-09-13", "answer": "合成真实首答"})
        self.learn("grade", {"session_id": sid, "on": "2027-09-13", "has_errors": True,
            "explanation": {"knowledge": "合成规则", "facts": "合成案情", "errors": "合成遗漏", "answer": "合成答案"}})
        return self.service.store.snapshot()[0]["errors"][0]

    def dates(self):
        return {k: {"date": d, "status": "verified", "source_url": "https://www.moj.gov.cn/synthetic",
                    "evidence": "合成公告，不是真实考期"} for k, d in
                zip(("objective_end", "results", "subjective"), ("2027-09-13", "2027-09-20", "2027-10-17"))}

    def test_placeholder_rejection_preserves_exact_pending_state(self):
        sid = self.session()
        before = self.service.store.path.read_bytes()
        for answer in ("", "   ", "未作答", "（占位）", "【待答】", "Placeholder", "..."):
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                self.learn("submit_answer", {"session_id": sid, "on": "2027-09-13", "answer": answer})
            self.assertEqual(before, self.service.store.path.read_bytes())

    def test_short_actual_dont_know_is_saved(self):
        sid = self.session()
        self.learn("submit_answer", {"session_id": sid, "on": "2027-09-13", "answer": "不会"})
        data, _ = self.service.store.snapshot()
        self.assertEqual(data["exercises"][0]["answer_history"][0]["answer"], "不会")

    def test_legacy_record_rejects_placeholder(self):
        before = self.service.store.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "占位"):
            self.service.record({"kind": "answer", "question_id": "legacy", "subject": "刑法",
                                 "on": "2027-09-13", "answer": "未作答", "complete": True}, self.version())
        self.assertEqual(before, self.service.store.path.read_bytes())

    def test_legacy_pending_placeholder_cannot_be_graded_into_new_card(self):
        sid = self.session()
        self.learn("submit_answer", {"session_id": sid, "on": "2027-09-13", "answer": "合成真实原答"})
        def legacy(data):
            data["sessions"][0]["answers"][0]["text"] = "未作答"
            data["exercises"][0]["answer_history"][0]["answer"] = "未作答"
        self.service.store.update(self.version(), legacy)
        before = self.service.store.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "占位"):
            self.learn("grade", {"session_id": sid, "on": "2027-09-13", "has_errors": True,
                "explanation": {"knowledge": "合成规则", "facts": "合成案情", "errors": "合成遗漏", "answer": "合成答案"}})
        self.assertEqual(before, self.service.store.path.read_bytes())
        with self.assertRaisesRegex(ValueError, "占位"):
            self.service.record({"kind": "error", "question_id": self.service.store.snapshot()[0]["exercises"][0]["question_id"],
                                 "detail": "未作答"}, self.version())

    def test_legacy_review_placeholder_does_not_consume_due_node(self):
        card = self.error()
        before = self.service.store.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "占位"):
            self.service.record({"kind": "review", "card_id": card["id"], "on": "2027-09-15", "answer": "（占位）",
                                 "authorization": "合成用户要求额外回顾"}, self.version())
        self.assertEqual(before, self.service.store.path.read_bytes())

    def test_void_keeps_history_and_removes_due(self):
        card = self.error()
        before, _ = self.service.store.snapshot()
        self.assertEqual(len(due_cards(before["errors"], "2027-09-15", ["刑法"])), 1)
        with self.assertRaises(ValueError):
            self.learn("void_card", {"card_id": card["id"], "reason": "合成误登记"})
        call(self.service, "learning_action", {"action": "void_card", "payload": {"card_id": card["id"],
            "reason": "合成误登记", "user_instruction": "合成用户要求作废"}, "version": self.version()})
        after, _ = self.service.store.snapshot()
        self.assertEqual(before["exercises"], after["exercises"])
        self.assertEqual(before["sessions"], after["sessions"])
        self.assertEqual(due_cards(after["errors"], "2027-09-15", ["刑法"]), [])
        self.assertEqual(after["errors"][0]["void_history"][0]["previous_status"], "waiting")

    def test_void_skips_active_history_without_deleting_answer(self):
        card = self.error()
        sid = self.service.store.snapshot()[0]["sessions"][0]["id"]
        self.learn("skip", {"session_id": sid, "reason": "合成用户跳过当前订正"})
        self.learn("answer_check", {"question_id": card["question_id"], "on": "2027-09-15", "outcome": "not_found", "evidence": "合成实际检索"})
        s = self.learn("issue", {"on": "2027-09-15", "mode": "history", "authorization": "合成用户要求额外回顾"})["session"]
        self.learn("void_card", {"card_id": card["id"], "reason": "合成误登记", "user_instruction": "合成用户要求作废"})
        data, _ = self.service.store.snapshot()
        self.assertEqual(data["sessions"][-1]["status"], "skipped")
        self.assertEqual(data["checkpoint"]["session_id"], s["id"])
        self.assertTrue(data["exercises"][0]["answer_history"])
        self.assertFalse(self.service.learning_state()["sessions"])

    def test_verified_stage_mismatch_requires_confirmation_and_can_go_back(self):
        with patch("lawstudy.service.today_in", return_value="2027-09-15"):
            self.configure({"exam_dates": self.dates()})
            before = self.service.store.path.read_bytes()
            with self.assertRaisesRegex(ValueError, "stage_confirmation"):
                self.configure({"stage_override": 4})
            self.assertEqual(before, self.service.store.path.read_bytes())
            self.configure({"stage_override": 4, "stage_confirmation": "合成用户明确提前阅读错漏文档"})
            self.assertTrue(self.service.status("2027-09-15")["stage_alignment"]["warnings"])
            self.assertFalse(self.service.status("2027-09-15")["stage_alignment"]["requires_confirmation"])
            self.configure({"stage_override": 1, "stage_confirmation": "合成用户回到课程阶段"})
            self.assertEqual(self.service.status("2027-09-15")["plan"]["stage"]["number"], 1)
            self.configure({"stage_override": None})
            self.assertEqual(self.service.status("2027-09-15")["plan"]["stage"]["number"], 2)

    def test_unknown_dates_warn_without_inventing_calendar(self):
        self.configure({"stage_override": 4})
        alignment = self.service.status("2026-10-19")["stage_alignment"]
        self.assertTrue(alignment["warnings"])
        self.assertIsNone(alignment["calendar"]["number"])
        self.assertFalse(alignment["requires_confirmation"])

    def test_empty_year_change_archives_dates_and_keeps_other_settings(self):
        with patch("lawstudy.service.today_in", return_value="2027-09-15"):
            self.configure({"exam_dates": self.dates()})
            self.configure({"year": 2028, "stage_override": 1, "elective": "administrative"})
        data, _ = self.service.store.snapshot()
        self.assertEqual(data["config"]["year"], 2028)
        self.assertEqual(data["config"]["stage_override"], 1)
        self.assertEqual(data["config"]["elective"], "administrative")
        self.assertTrue(all(n["date"] is None for n in data["config"]["exam_dates"].values()))
        self.assertEqual(data["calendar_manual_history"][-1]["before"], self.dates())

    def test_invalid_year_edit_with_progress_is_atomic(self):
        self.error()
        before = self.service.store.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "已有学习记录"):
            self.configure({"stage_override": 4, "year": 2028})
        self.assertEqual(before, self.service.store.path.read_bytes())

    def test_readiness_counts_slots_and_distinguishes_unprocessed_theory(self):
        for subject in ("刑法", "刑事诉讼法", "民事综合"):
            self.question(subject)
        readiness = self.service.status("2027-09-13")["material_readiness"]
        self.assertEqual(readiness["case_shortfall"], 0)
        self.assertEqual(readiness["verified_themes"], 0)
        self.configure({"stage_override": 3})
        self.assertEqual(self.service.status("2027-09-13")["material_readiness"]["case_shortfall"], 2)
        self.service.import_material("理论.md", "合成完整主题".encode(), "理论法", "theory", self.version())
        readiness = self.service.status("2027-09-13")["material_readiness"]
        self.assertTrue(any("已保存" in m for m in readiness["messages"]))
        self.assertEqual(readiness["theory_materials"], 1)

    def test_first_stage_keeps_true_exam_for_later_and_explains_skip(self):
        self.question()
        self.configure({"stage_override": 1})
        self.service.record({"kind": "course", "title": "合成单元", "subject": "刑法", "completed_on": "2027-09-13", "evidence": "合成已学完反馈"}, self.version())
        result = self.learn("issue", {"on": "2027-09-13"})
        self.assertFalse(result["available"])
        self.assertIn("unit_skip", result["reason"])
        self.assertIn("第二阶段", result["reason"])

    def test_navigation_error_and_both_dependencies_are_actionable(self):
        payload = {"title": "合成导出", "topics": [{"title": "合成主题", "knowledge": "合成完整规则与分支", "card_ids": []}]}
        data, _ = self.service.store.snapshot()
        with self.assertRaisesRegex(ValueError, r"topics\[0\].navigation"):
            build_sections(data, payload)
        payload["topics"][0]["navigation"] = "合成条件成立与不成立两分支"
        with patch("lawstudy.documents.find_spec", return_value=None), self.assertRaisesRegex(ValueError, "reportlab、pypdf"):
            export_topic(self.service, payload, self.version())

    def test_chat_exposes_readiness_and_void_route(self):
        workspace = Path(self.temp.name)/"workspace"
        self.assertIn("新建", context(workspace)["next_action"])
        bootstrap(workspace, "other", "合成用户指定目录", self.service.store.directory)
        card = self.error()
        result = dispatch(workspace, {"action": "learning", "version": self.version(), "payload": {"action": "void_card",
            "payload": {"card_id": card["id"], "reason": "合成误登记", "user_instruction": "合成用户要求作废"}}})
        self.assertEqual(result["result"]["status"], "obsolete")
        self.assertIn("material_readiness", result["context"])
        self.assertIn("stage_alignment", result["context"])


if __name__ == "__main__":
    unittest.main()
