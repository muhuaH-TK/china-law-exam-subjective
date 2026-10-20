from support import confirm_formal
"""WorkBuddy报告回归；仅使用合成资料和隔离实例，不读取真实学习数据。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from lawstudy.documents import build_sections
from lawstudy.learning import exercise_answer_history
from lawstudy.mcp import call
from lawstudy.monitor import monitor_gate
from lawstudy.scheduler import enable, tick
from lawstudy.service import Service

ROOT = Path(__file__).resolve().parent.parent


class ReportRegressions(unittest.TestCase):
    def setUp(self):
        folder = ROOT / "tests/.tmp"
        folder.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=folder)
        self.addCleanup(self.temp.cleanup)
        self.service = Service(Path(self.temp.name)/"instance")
        confirm_formal(self.service)
        self.configure({"year": 2027, "participation": "participating", "stage_override": 2,
                        "elective": "administrative", "authorization": {"search_mode": "search_and_import", "scope": "合成来源", "online_ai": True}})

    def version(self):
        return self.service.store.snapshot()[1]

    def configure(self, config):
        self.service.configure(config, self.version(), user_instruction="合成用户配置依据")
        confirm_formal(self.service)

    def learn(self, action, payload):
        return self.service.learning(action, payload, self.version())

    def flow(self):
        material = self.service.import_material("合成.md", b"synthetic", "刑法", "questions", self.version())["material"]["id"]
        q = self.learn("register_question", {"identity": "synthetic/report/1", "subject": "刑法", "facts": "合成甲乙事实", "questions": ["合成设问？"],
            "source_name": "合成题源", "locator": "题一", "material_id": material, "verification": "合成原件完整核验", "complete": True})["question_id"]
        self.learn("answer_version", {"question_id": q, "answer_kind": "author", "content": "合成答案", "basis": "合成同题版本", "material_id": material, "same_question_verified": True})
        sid = self.learn("issue", {"on": "2027-09-13"})["session"]["id"]
        self.learn("submit_answer", {"session_id": sid, "on": "2027-09-13", "answer": "合成真实首答"})
        explanation = {"knowledge": "合成条件及分支", "facts": "甲乙合成案情", "errors": "合成首答遗漏", "answer": "合成充分答案"}
        self.learn("grade", {"session_id": sid, "on": "2027-09-13", "has_errors": True, "explanation": explanation})
        self.learn("submit_answer", {"session_id": sid, "on": "2027-09-14", "answer": "合成真实订正"})
        self.learn("grade", {"session_id": sid, "on": "2027-09-14", "has_errors": False, "explanation": explanation})
        return q

    def test_correction_recorded_without_changing_first_completion_or_quota(self):
        self.flow()
        data, _ = self.service.store.snapshot()
        row = data["exercises"][0]
        self.assertEqual([a["phase"] for a in row["answer_history"]], ["first", "correction"])
        self.assertEqual([a["prompted"] for a in row["answer_history"]], [False, True])
        self.assertEqual(row["first_answer_on"], "2027-09-13")
        self.assertEqual(row["completed_on"], "2027-09-13")
        self.assertEqual(self.service.status("2027-09-14")["plan"]["case_completed"], 0)
        self.assertEqual(data["errors"][0]["reviews"], [])

    def test_export_includes_new_and_legacy_corrections_without_duplicate_first(self):
        self.flow()
        data, _ = self.service.store.snapshot()
        payload = {"topics": [{"title": "合成主题", "knowledge": "合成规则与分支", "card_ids": [data["errors"][0]["id"]], "navigation": "合成导航"}]}
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                sample = deepcopy(data)
                if legacy:
                    first = sample["exercises"][0]["answer_history"][0]
                    sample["exercises"][0]["answer_history"] = [{"on": first["on"], "answer": first["answer"]}]
                before = deepcopy(sample)
                content = json.dumps(build_sections(sample, payload), ensure_ascii=False)
                self.assertEqual(content.count("合成真实首答"), 1)
                self.assertEqual(content.count("合成真实订正"), 1)
                self.assertIn("知识点提示后，非独立通过", content)
                self.assertEqual(sample, before)

    def test_identical_real_answers_are_not_collapsed(self):
        self.flow()
        data, _ = self.service.store.snapshot()
        row = data["exercises"][0]
        row["answer_history"] = [{"on": "2027-09-13", "answer": "相同文本"}]
        for answer in data["sessions"][0]["answers"]:
            answer.update(on="2027-09-13", text="相同文本")
        self.assertEqual(len(exercise_answer_history(data, row)), 2)

    def source(self):
        self.service.add_source({"name": "合成来源", "url": "https://example.com/feed", "subject": "刑法"}, self.version())
        source = self.service.store.snapshot()[0]["sources"][-1]
        self.service.source_action("configure", {"source_id": source["id"], "mode": "json", "enabled": True,
            "authorization": "合成用户授权", "identity_verification": "合成身份核验"}, self.version())
        auto = deepcopy(self.service.store.snapshot()[0]["config"]["automation"])
        self.configure({"automation": {**auto, "monitor_requested": True}})
        return source["id"]

    def feed(self, _url):
        return {"url": "https://example.com/feed", "content_type": "application/json", "body": json.dumps({"items": [{"id": "a", "url": "https://example.com/a", "text": "合成题源内容"}]})}

    def test_first_stage_blocks_fetch_capture_and_backlog_but_keeps_configuration(self):
        source = self.source()
        self.configure({"stage_override": 1})
        before, version = self.service.store.snapshot()
        reader = unittest.mock.Mock(side_effect=AssertionError("第一阶段不得联网"))
        result = self.service.monitor_once("2027-09-13", reader)
        self.assertEqual(result["status"], "waiting_stage2")
        with self.assertRaisesRegex(ValueError, "第二阶段"):
            self.service.ingest_capture({"source_id": source, "on": "2027-09-13", "method": "public_feed", "evidence": "合成响应", "entries": []}, version)
        after, _ = self.service.store.snapshot()
        self.assertEqual(before, after)
        reader.assert_not_called()

    def test_stage_two_fetch_and_stage_three_four_are_eligible(self):
        self.source()
        result = self.service.monitor_once("2027-09-13", self.feed)
        self.assertEqual(result["sources"][0]["status"], "fetched")
        self.assertEqual(self.service.store.snapshot()[0]["monitor_budget"]["2027-09-13"]["used"], 1)
        for number in (2, 3, 4):
            self.configure({"stage_override": number})
            self.assertTrue(self.service.monitor_state("2027-09-13")["gate"]["eligible"])

    def test_official_objective_end_boundary_and_unknown_dates(self):
        config = deepcopy(self.service.store.snapshot()[0]["config"])
        config["stage_override"] = None
        self.assertFalse(monitor_gate(config, "2027-09-13")["eligible"])
        config["exam_dates"]["objective_end"].update(date="2027-09-13", status="verified")
        self.assertFalse(monitor_gate(config, "2027-09-13")["eligible"])
        self.assertTrue(monitor_gate(config, "2027-09-14")["eligible"])
        config["stage_override"] = 1
        self.assertFalse(monitor_gate(config, "2027-09-14")["eligible"])

    def test_scheduler_stage_one_does_not_mark_monitor_done_then_stage_two_runs_same_day(self):
        self.source()
        self.configure({"stage_override": 1})
        enable(self.service, self.version(), "合成指定本机")
        now = datetime(2027, 9, 13, 23, 0, tzinfo=timezone(timedelta(hours=8)))
        with patch.object(self.service, "monitor_once", side_effect=AssertionError("不得监测")):
            self.assertEqual(tick(self.service, now)["notifications"], [])
        self.assertNotIn("monitor", self.service.store.snapshot()[0]["job_days"]["2027-09-13"])
        self.configure({"stage_override": 2})
        with patch.object(self.service, "monitor_once", return_value={"status": "completed", "sources": []}) as monitor:
            tick(self.service, now)
            monitor.assert_called_once_with("2027-09-13")

    def test_three_preset_websites_and_old_instance_custom_sources_are_preserved(self):
        self.source()
        for _ in range(2):
            self.service.source_action("presets", {}, self.version())
        sources = self.service.store.snapshot()[0]["sources"]
        self.assertEqual(len(sources), 4)
        for uid in ("1552849431", "2342740757", "3153511812"):
            source = next(s for s in sources if uid in s["url"])
            self.assertFalse(source["requested"])
            self.assertIsNone(source["cursor"])

    def test_no_history_quota_is_unavailable_instead_of_exception(self):
        result = self.learn("issue", {"on": "2027-09-13", "mode": "history"})
        self.assertFalse(result["available"])
        self.assertIn("尚无当日案例", result["reason"])

    def test_unrestricted_files_preserve_unknown_format_excluded_subject_and_empty_file(self):
        for name, raw, subject in (("任意.zip", b"arbitrary-file", "商法"), ("无后缀", b"", "用户补充科目")):
            result = self.service.import_material(name, raw, subject, "other", self.version())["material"]
            self.assertEqual((self.service.store.directory/result["path"]).read_bytes(), raw)
            self.assertEqual(result["sha256"], sha256(raw).hexdigest())
            self.assertEqual(result["status"], "original_saved")
            self.assertFalse(result["question_ready"])

    def test_large_file_import_streamed_and_kept_when_auto_parsing_deferred(self):
        path = Path(self.temp.name)/"大资料.bin"
        with path.open("wb") as handle:
            for _ in range(34):
                handle.write(b"x"*1024*1024)
        original_read = Path.read_bytes
        def guarded_read(p):
            if p.stat().st_size > 32*1024*1024:
                raise AssertionError("大原件不得整文件读入内存")
            return original_read(p)
        with patch.object(Path, "read_bytes", guarded_read):
            result = self.service.import_file(path, "综合", "other", self.version())["material"]
        self.assertEqual(result["size"], 34*1024*1024)
        self.assertEqual(result["status"], "processing_pending")
        self.assertEqual((self.service.store.directory/result["path"]).stat().st_size, path.stat().st_size)

    def test_cli_configure_record_bare_and_wrapped_and_mcp_large_file(self):
        path = Path(self.temp.name)/"配置.json"
        for payload in ({"elective": "undecided"}, {"config": {"elective": "administrative"}}):
            path.write_text(json.dumps(payload), encoding="utf-8")
            result = subprocess.run([sys.executable, "run.py", "configure", "--instruction", "合成用户明确配置", "--data-dir", str(self.service.store.directory), "--file", str(path), "--version", self.version()], cwd=ROOT, capture_output=True, encoding="utf-8", timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        confirm_formal(self.service)
        for i, wrapped in enumerate((False, True)):
            record = {"kind": "course", "title": "合成单元"+str(i), "subject": "刑法", "completed_on": "2027-09-13", "evidence": "合成用户反馈"}
            path.write_text(json.dumps({"record": record} if wrapped else record), encoding="utf-8")
            result = subprocess.run([sys.executable, "run.py", "record", "--data-dir", str(self.service.store.directory), "--file", str(path), "--version", self.version()], cwd=ROOT, capture_output=True, encoding="utf-8", timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        material = Path(self.temp.name)/"大文件.7z"
        material.write_bytes(b"z"*(11*1024*1024))
        result = call(self.service, "import_selected_material", {"path": str(material), "subject": "商法", "category": "other", "version": self.version(), "user_instruction": "合成用户选定此文件"})
        self.assertEqual(result["material"]["size"], 11*1024*1024)
        result = subprocess.run([sys.executable, "run.py", "import", "--data-dir", str(self.service.store.directory), "--file", str(material), "--version", self.version()], cwd=ROOT, capture_output=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertTrue(json.loads(result.stdout)["duplicate"])

    def test_incomplete_upload_does_not_register_file_and_cleans_staging(self):
        before = self.service.store.snapshot()
        with self.assertRaisesRegex(ValueError, "尚未完成"):
            self.service.import_stream("未传完.bin", io.BytesIO(b"short"), "综合", "other", self.version(), length=100)
        self.assertEqual(self.service.store.snapshot(), before)
        self.assertEqual(list(self.service.store.directory.glob(".upload-*")), [])


if __name__ == "__main__":
    unittest.main()
