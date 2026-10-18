from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from lawstudy.service import Service
from lawstudy.store import ConflictError, Store


class ServiceTests(unittest.TestCase):
    def setUp(self):
        temporary_root=Path(__file__).resolve().parent/".tmp"
        temporary_root.mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=temporary_root)
        assert Path(self.tmp.name).resolve().is_relative_to(temporary_root.resolve())
        self.addCleanup(self.tmp.cleanup)
        self.service=Service(Path(self.tmp.name)/"个人实例")
        self.configure({"year":2027,"participation":"participating","elective":"administrative","stage_override":2})

    def version(self):
        return self.service.status()["version"]

    def configure(self,payload):
        self.service.configure(payload,self.version())

    def record(self,payload):
        self.service.record(payload,self.version())

    def test_empty_isolation_no_implicit_grants(self):
        data,_=self.service.store.snapshot()
        for key in ("materials","exercises","errors","sources"):
            self.assertEqual(data[key],[])
        self.assertFalse(data["config"]["authorization"]["online_ai"])
        self.assertEqual(data["config"]["authorization"]["search_mode"],"provided_only")

    def test_year_change_preserves_old_learning_and_duplicate_unit_not_counted(self):
        self.configure({"stage_override":1})
        row={"kind":"course","title":"已完成单元","subject":"刑法","completed_on":"2027-09-01","evidence":"用户已学完"}
        self.record(row)
        with self.assertRaises(ValueError):
            self.record(row)
        self.assertEqual(self.service.status("2027-09-01")["plan"]["case_target"],1)
        with self.assertRaises(ValueError):
            self.configure({"year":2028})
        self.assertEqual(self.service.status()["profile"]["config"]["year"],2027)

    def test_paused_status_does_not_invite_automatic_continuation(self):
        self.record({"kind":"lifecycle","value":"paused","evidence":"用户要求暂停"})
        self.assertIn("暂停",self.service.status()["next_action"])
        self.assertEqual(self.service.status()["plan"]["case_target"],0)

    def test_pdf_failure_keeps_original_and_blank_page_needs_ocr(self):
        result=self.service.import_material("损坏.pdf",b"not-a-pdf","综合","other",self.version())
        self.assertTrue((self.service.store.directory/result["material"]["path"]).exists())
        self.assertFalse(result["material"]["question_ready"])
        from importlib.util import find_spec
        if find_spec("pypdf"):
            from pypdf import PdfWriter
            writer=PdfWriter();writer.add_blank_page(width=100,height=100)
            buffer=io.BytesIO();writer.write(buffer)
            result=self.service.import_material("空白.pdf",buffer.getvalue(),"综合","other",self.version())
            self.assertEqual(result["material"]["status"],"ocr_pending")

    def test_stale_write_and_failed_validation_leave_data_intact(self):
        old=self.version()
        self.configure({"elective":"commercial"})
        before=self.service.store.path.read_bytes()
        with self.assertRaises(ConflictError):
            self.service.configure({"elective":"undecided"},old)
        with self.assertRaises(ValueError):
            self.configure({"authorization":{"search_mode":"search_and_import","scope":"","online_ai":True}})
        self.assertEqual(before,self.service.store.path.read_bytes())
        self.assertTrue((self.service.store.directory/"instance.previous.json").exists())

    def test_material_copy_dedup_and_no_false_question_or_learning_status(self):
        result=self.service.import_material("../../教材.md","完整原文".encode(),"刑法","textbook",self.version())
        self.assertFalse(result["material"]["question_ready"])
        self.assertTrue((self.service.store.directory/result["material"]["path"]).exists())
        self.assertTrue(self.service.import_material("改名.md","完整原文".encode(),"刑法","textbook",self.version())["duplicate"])
        data,_=self.service.store.snapshot()
        self.assertEqual(len(data["materials"]),1)
        self.assertEqual(data["exercises"],[])
        self.assertEqual(data["errors"],[])

    def test_docx_paragraph_extraction(self):
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,"w") as z:
            z.writestr("word/document.xml",'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>跨段测试</w:t></w:r></w:p></w:body></w:document>')
        result=self.service.import_material("课程.docx",buf.getvalue(),"综合","course",self.version())
        extracted=self.service.store.directory/result["material"]["text_path"]
        self.assertEqual(extracted.read_text(encoding="utf-8"),"跨段测试")

    def test_dates_cannot_be_verified_without_evidence(self):
        config=deepcopy(self.service.status()["profile"]["config"])
        config["exam_dates"]["objective_end"].update(date="2027-09-12",status="verified")
        with self.assertRaises(ValueError):
            self.configure({"exam_dates":config["exam_dates"]})
        config["exam_dates"]["objective_end"].update(source_url="https://evil.example/moj.gov.cn",evidence="示例")
        with self.assertRaises(ValueError):
            self.configure({"exam_dates":config["exam_dates"]})

    def test_cross_day_answer_evidence_retained_and_error_requires_real_answer(self):
        with self.assertRaises(ValueError):
            self.record({"kind":"error","question_id":"q1","detail":"不能仅凭材料建错"})
        self.record({"kind":"answer","question_id":"q1","subject":"刑法","on":"2027-09-13","answer":"真实首答一部分","complete":False})
        self.record({"kind":"answer","question_id":"q1","subject":"刑法","on":"2027-09-14","answer":"次日继续答完","complete":True})
        data,_=self.service.store.snapshot()
        self.assertEqual(data["exercises"][0]["first_answer_on"],"2027-09-13")
        self.assertEqual(data["exercises"][0]["completed_on"],"2027-09-14")
        self.assertEqual(len(data["exercises"][0]["answer_history"]),2)
        self.assertEqual(self.service.status("2027-09-14")["plan"]["case_remaining"],2)

    def test_stage_three_review_requires_opt_in(self):
        self.record({"kind":"answer","question_id":"q1","subject":"刑法","on":"2027-09-13","answer":"真实原答","complete":True})
        self.record({"kind":"error","question_id":"q1","detail":"实际遗漏"})
        data,_=self.service.store.snapshot()
        card_id=data["errors"][0]["id"]
        self.configure({"stage_override":3})
        event={"kind":"review","card_id":card_id,"on":"2027-09-20","answer":"用户真实订正"}
        with self.assertRaises(ValueError):
            self.record(event)
        event["authorization"]="用户明确要求开始历史订正"
        self.record(event)
        data,_=self.service.store.snapshot()
        self.assertEqual(len(data["errors"][0]["reviews"]),1)
        self.assertEqual(data["errors"][0]["status"],"waiting")

    def test_stage_four_no_automatic_exercise_and_background_not_connected(self):
        self.configure({"stage_override":4})
        with self.assertRaises(ValueError):
            self.record({"kind":"answer","question_id":"q","subject":"刑法","on":"2027-10-12","answer":"不能自动新增","complete":True})
        self.assertEqual(self.service.status()["capabilities"]["scheduled_execution"],"ready_requires_instance_activation")

    def test_recording_new_answers_stops_at_quota(self):
        for i,subject in enumerate(("刑法","刑事诉讼法","民事综合")):
            self.record({"kind":"answer","question_id":str(i),"subject":subject,"on":"2027-09-15","answer":"真实答复","complete":True})
        with self.assertRaises(ValueError):
            self.record({"kind":"answer","question_id":"extra","subject":"行政法","on":"2027-09-15","answer":"不自动加题","complete":True})


if __name__ == "__main__":
    unittest.main()
