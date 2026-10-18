"""首次设置、资料登记与AI工具共用的真实交互记录入口。"""

import base64
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from importlib.util import find_spec
import io
import json
from pathlib import Path
import platform
import re
import sys
from urllib.parse import urlparse
from uuid import uuid4
import zipfile
from xml.etree import ElementTree

from .policy import SUBJECTS, daily_plan, iso, next_due, review_card, subjects
from .store import Store, atomic_bytes, utc_now
from .learning import LearningMixin
from .monitor import MonitorMixin

MAX_MATERIAL = 10 * 1024 * 1024
ALLOWED = {".pdf", ".docx", ".md", ".txt", ".png", ".jpg", ".jpeg", ".webp"}


def object_only(value, name):
    if not isinstance(value, dict):
        raise ValueError(name + "须为对象")


def text(value, name, maximum=20000):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(name + "须为长度合适的文本")
    return value.strip()


def boolean(value, name):
    if type(value) is not bool:
        raise ValueError(name + "须为布尔值")
    return value


def today_in(zone):
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(zone)
    except (KeyError, ValueError):
        if zone not in ("Asia/Hong_Kong", "Asia/Shanghai"):
            raise ValueError("该时区不可用，请安装时区数据或选择Asia/Hong_Kong")
        tz = timezone(timedelta(hours=8))
    return datetime.now(tz).date().isoformat()


def check_url(value, official=False):
    if not value:
        return ""
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not host or parsed.username or parsed.password:
        raise ValueError("网址须为不含账号密码的HTTP或HTTPS地址")
    if official and not (host == "moj.gov.cn" or host.endswith(".moj.gov.cn") or host.endswith(".gov.cn")):
        raise ValueError("核验日期须提供适用的官方政府公告网址")
    return value


def extract_material(raw, suffix):
    if suffix in (".md", ".txt"):
        return raw.decode("utf-8-sig"), "text_extracted"
    if suffix == ".docx":
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if sum(i.file_size for i in archive.infolist()) > 30 * 1024 * 1024:
                raise ValueError("文档解压内容过大，请分批提供资料")
            xml = archive.read("word/document.xml")
            if b"<!DOCTYPE" in xml.upper():
                raise ValueError("不支持含外部文档声明的Word文件")
            root = ElementTree.fromstring(xml)
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            return "\n".join("".join(p.itertext()) for p in root.findall(".//w:p", ns)), "text_extracted_layout_unverified"
    if suffix == ".pdf" and find_spec("pypdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            raise ValueError("PDF已加密，请提供可读取版本")
        pages = [p.extract_text() or "" for p in reader.pages]
        result = "\n\n".join(f"[物理第{i+1}页]\n{value}" for i, value in enumerate(pages))
        return (result, "text_extracted_layout_unverified") if any(p.strip() for p in pages) else ("", "ocr_pending")
    return "", "pdf_extractor_pending" if suffix == ".pdf" else "ocr_pending"


class Service(LearningMixin, MonitorMixin):
    def __init__(self, directory):
        self.store = Store(directory)

    def status(self, day=None):
        data, version = self.store.snapshot()
        day = day or today_in(data["config"]["timezone"])
        plan = daily_plan(data, day)
        config = data["config"]
        steps = [
            {"name": "年度与备考状态", "ready": bool(config["year"]) and config["participation"] != "undecided"},
            {"name": "选做方向", "ready": True, "detail": "未确定时保留六科轮换" if config["elective"] == "undecided" else "已明确"},
            {"name": "学习资料", "ready": bool(data["materials"])},
            {"name": "在线AI与检索范围", "ready": any(e["kind"] == "settings" and "authorization" in e.get("fields", []) for e in data["events"]),
             "detail": "未允许在线AI时仍可本地整理，不外发资料"},
            {"name": "阶段确认", "ready": plan["stage"]["number"] is not None},
        ]
        if data["lifecycle"] in ("paused", "ended"):
            next_action = "学习已暂停或结束，进度保留；等待你明确要求恢复。"
        elif not config["year"]:
            next_action = "先填写备考年度，并确认是否继续本年度主观题备考。"
        elif not data["materials"]:
            next_action = "提供一本教材、课程讲义或背诵材料；可以分批补充。"
        elif plan["stage"]["number"] is None:
            next_action = plan["stage"]["reason"]
        elif plan["stage"]["number"] == 1:
            next_action = "登记已经完成的课程单元；之后按对应教材核验适配原题。"
        elif plan["stage"]["number"] == 4:
            next_action = "整理已导入材料和真实历史错漏，不要求开始做题。"
        else:
            next_action = "在所选AI中连接本实例，核验资料里的完整原题和答案，再开始今日学习；也可在页面续答已发出的题。"
        # 不把完整原答或资料正文暴露在每日计划里。
        safe = {k: data[k] for k in ("schema_version", "instance_id", "revision", "lifecycle", "config", "materials", "course_units", "sources", "checkpoint")}
        safe["checkpoints"] = data.get("checkpoints", {})
        return {"version": version, "profile": safe, "plan": plan, "setup_steps": steps,
                "next_action": next_action, "capabilities": capabilities()}

    def configure(self, payload, version):
        object_only(payload, "设置")
        allowed = {"year", "elective", "participation", "selected_ai", "timezone", "stage_override", "questions_per_unit", "theory_target_chars", "exam_dates", "authorization", "automation"}
        if set(payload)-allowed:
            raise ValueError("设置含未知字段")
        def action(data):
            config = data["config"]
            for k, value in payload.items():
                if k == "year" and (type(value) is not int or not 2000 <= value <= 2100):
                    raise ValueError("请填写2000至2100之间的备考年度")
                if k == "year" and config["year"] not in (None, value) and any(data[key] for key in ("exercises", "errors", "course_units")):
                    raise ValueError("此实例已有学习记录；新年度请使用新的个人实例目录，不覆盖旧年度")
                if k == "elective":
                    subjects(value)
                if k == "participation" and value not in ("undecided", "participating", "not_participating"):
                    raise ValueError("备考状态无效")
                if k == "selected_ai" and value not in ("workbuddy", "codex", "other"):
                    raise ValueError("AI工具选项无效")
                if k == "timezone":
                    today_in(text(value, "时区", 80))
                if k == "stage_override" and value is not None and (type(value) is not int or value not in (1, 2, 3, 4)):
                    raise ValueError("阶段须为1至4，或留空按官方日期推荐")
                if k == "questions_per_unit" and (type(value) is not int or not 1 <= value <= 20):
                    raise ValueError("每单元题量须为1至20")
                if k == "theory_target_chars" and (type(value) is not int or not 200 <= value <= 10000):
                    raise ValueError("背诵参考字数须为200至10000，完整长主题仍不拆分")
                if k == "authorization":
                    object_only(value, "授权")
                    if set(value) != {"search_mode", "scope", "online_ai"}:
                        raise ValueError("授权须包含检索模式、范围和在线AI选择")
                    if value["search_mode"] not in ("provided_only", "search_only", "search_and_import"):
                        raise ValueError("检索模式无效")
                    boolean(value["online_ai"], "在线AI选择")
                    value["scope"] = text(value["scope"], "检索范围", 2000)
                    if value["search_mode"] != "provided_only" and not value["scope"]:
                        raise ValueError("允许检索时，请填写科目、年份或来源等范围")
                if k == "automation":
                    object_only(value, "后台设置")
                    if set(value) != {"learning_requested", "learning_time", "monitor_requested", "monitor_time", "daily_limit"}:
                        raise ValueError("后台设置字段不完整")
                    for field in ("learning_requested", "monitor_requested"):
                        boolean(value[field], field)
                    for field in ("learning_time", "monitor_time"):
                        if not isinstance(value[field], str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value[field]):
                            raise ValueError("后台时间须为HH:MM")
                    if type(value["daily_limit"]) is not int or not 1 <= value["daily_limit"] <= 100:
                        raise ValueError("每日监测上限须为1至100")
                if k == "exam_dates":
                    object_only(value, "考试节点")
                    if set(value) != {"objective_end", "results", "subjective"}:
                        raise ValueError("考试节点不完整")
                    for node in value.values():
                        object_only(node, "日期节点")
                        if set(node) != {"date", "status", "source_url", "evidence"}:
                            raise ValueError("日期须包含状态、网址和核验依据")
                        if node["status"] not in ("pending", "failed", "provided", "verified", "not_found", "conflict", "unpublished"):
                            raise ValueError("日期状态无效")
                        if node["date"]:
                            iso(node["date"])
                        check_url(text(node["source_url"], "公告网址", 2000), official=True)
                        text(node["evidence"], "日期核验依据", 10000)
                        if node["status"] == "verified" and not all(node.get(f) for f in ("date", "source_url", "evidence")):
                            raise ValueError("已核验日期必须有日期、官方网址与依据")
                    data.setdefault("calendar_manual_history", []).append({"at": utc_now(), "before": config["exam_dates"], "after": value})
                config[k] = value
            dates = config["exam_dates"]
            present = [dates[k]["date"] for k in ("objective_end", "results", "subjective") if dates[k]["date"]]
            if present != sorted(set(present)):
                raise ValueError("考试日期应按客观结束、成绩公布、主观考试依次递增")
            if any(d[:4] != str(config["year"]) for d in present):
                raise ValueError("考试日期年度与备考年度不符，请核对")
            if data["lifecycle"] == "setup" and config["year"]:
                data["lifecycle"] = "active"
            data["events"].append({"kind": "settings", "at": utc_now(), "fields": sorted(payload)})
        self.store.update(version, action)

    def import_material(self, name, raw, subject, category, version, origin=None):
        name = text(name, "文件名", 300)
        suffix = Path(name.replace("\\", "/")).suffix.lower()
        if suffix not in ALLOWED:
            raise ValueError("当前仅接收PDF、DOCX、TXT、Markdown和常见图片")
        if not raw or len(raw) > MAX_MATERIAL:
            raise ValueError("文件为空或超过10MB，请分批提供资料")
        if subject not in SUBJECTS and subject != "综合":
            raise ValueError("资料科目无效")
        if category not in ("textbook", "course", "theory", "questions", "other"):
            raise ValueError("资料用途无效")
        digest = sha256(raw).hexdigest()
        if origin is not None:
            object_only(origin, "资料出处")
            if set(origin)-{"kind", "url", "evidence", "author", "year_version", "import_authorization"}:
                raise ValueError("资料出处含未知字段，不保存账号或凭证")
            if origin.get("kind") != "authorized_search":
                raise ValueError("检索资料须明确authorized_search出处")
            check_url(text(origin.get("url"), "检索原始网址", 2000))
            if not origin.get("url") or not text(origin.get("evidence"), "实际检索与导入依据", 10000):
                raise ValueError("检索导入须有原始网址和实际依据")
        result = {}
        def action(data):
            if origin is not None:
                auth = data["config"]["authorization"]
                if auth["search_mode"] == "provided_only":
                    raise ValueError("未授权检索资料导入")
                if auth["search_mode"] == "search_only" and not origin.get("import_authorization"):
                    raise ValueError("仅允许搜索时，候选导入须先有用户选择")
            existing = next((m for m in data["materials"] if m["sha256"] == digest), None)
            if existing:
                result.update({"duplicate": True, "material": existing})
                return
            extraction_error = None
            try:
                extracted, status = extract_material(raw, suffix)
            except Exception as exc:
                # 外部格式解析失败仍保留原件；不把失败冒称已读到完整内容。
                extracted, status = "", "extraction_failed"
                extraction_error = str(exc)[:200]
            relative = f"materials/{digest}{suffix}"
            atomic_bytes(self.store.directory / relative, raw)
            text_path = None
            if extracted:
                text_path = f"extracted/{digest}.txt"
                atomic_bytes(self.store.directory / text_path, extracted.encode("utf-8"))
            material = {"id": str(uuid4()), "name": name, "subject": subject, "category": category,
                "sha256": digest, "size": len(raw), "format": suffix, "path": relative,
                "text_path": text_path, "status": status, "error": extraction_error,
                "question_ready": False, "source_kind": "user_provided", "year_version": "待核实",
                "author": None, "imported_at": utc_now()}
            if origin:
                material.update(source_kind="authorized_search", origin=origin,
                                year_version=text(origin.get("year_version", "待核实"), "资料版本", 300),
                                author=text(origin.get("author", "待核实"), "资料作者", 300))
            data["materials"].append(material)
            result.update({"duplicate": False, "material": material})
        self.store.update(version, action)
        return result

    def add_source(self, payload, version):
        object_only(payload, "来源")
        name = text(payload.get("name"), "来源名称", 200)
        url = check_url(text(payload.get("url"), "来源网址", 2000))
        subject = payload.get("subject")
        if not name or not url or subject not in SUBJECTS:
            raise ValueError("来源须有名称、网址和科目")
        def action(data):
            if any(s["url"].rstrip("/") == url.rstrip("/") for s in data["sources"]):
                raise ValueError("该来源已登记")
            data["sources"].append({"id": str(uuid4()), "name": name, "url": url,
                "subject": subject, "requested": boolean(payload.get("requested", False), "监测选择"),
                "execution_status": "disabled", "mode": "browser", "cursor": None})
        self.store.update(version, action)

    def record(self, payload, version):
        """只收真实交互。此入口不执行出题、批改或联网。"""
        object_only(payload, "交互记录")
        kind = payload.get("kind")
        def action(data):
            if kind == "course":
                title = text(payload.get("title"), "单元名称", 500)
                if not title or payload.get("subject") not in subjects(data["config"]["elective"]):
                    raise ValueError("单元名称或科目无效")
                iso(payload["completed_on"])
                if not text(payload.get("evidence"), "用户完成反馈", 2000):
                    raise ValueError("课程完成须有用户实际反馈")
                if any(u["title"] == title and u["subject"] == payload["subject"] for u in data["course_units"]):
                    raise ValueError("该课程单元已登记，不重复增加考查数量")
                data["course_units"].append({"id": str(uuid4()), "title": title,
                    "subject": payload["subject"], "completed_on": payload["completed_on"], "evidence": payload["evidence"]})
            elif kind == "answer":
                if data["lifecycle"] != "active":
                    raise ValueError("学习尚未开始、已暂停或已结束，不能自动记录新作答")
                subject = payload.get("subject")
                if subject not in subjects(data["config"]["elective"]):
                    raise ValueError("题目超出生效科目")
                identity = text(payload.get("question_id"), "原题身份", 500)
                answer = text(payload.get("answer"), "用户原答")
                if not identity or not answer:
                    raise ValueError("须有原题身份和用户真实原答")
                iso(payload["on"])
                current_stage = daily_plan(data, payload["on"])["stage"]["number"]
                explicit_extra = text(payload.get("authorization", ""), "额外练习依据", 2000)
                if current_stage is None or (current_stage == 4 and not explicit_extra):
                    raise ValueError("当前没有常规答题安排；额外练习须有用户明确指令")
                if current_stage == 2 and subject == "理论法" and not explicit_extra:
                    raise ValueError("第二阶段暂不安排理论法主观题")
                row = next((r for r in data["exercises"] if r["question_id"] == identity), None)
                if row and row.get("completed_on"):
                    raise ValueError("该原题已作答，复测应走错题回顾，不能冒充新题")
                if row is None:
                    plan = daily_plan(data, payload["on"])
                    if plan["case_remaining"] <= 0 and not explicit_extra:
                        raise ValueError("当日已达题量，继续新题须有用户明确指令")
                    if current_stage == 1:
                        unit = next((u for u in data["course_units"] if u["id"] == payload.get("unit_id")), None)
                        if not unit or unit["subject"] != subject or unit["completed_on"] > payload["on"]:
                            raise ValueError("第一阶段须关联已完成的同科课程单元")
                    row = {"id": str(uuid4()), "question_id": identity, "subject": subject,
                           "unit_id": payload.get("unit_id"), "first_answer_on": payload["on"],
                           "answer_history": [], "completed_on": None}
                    data["exercises"].append(row)
                if row["subject"] != subject:
                    raise ValueError("同题科目不一致")
                if payload["on"] < row["first_answer_on"] or (row["answer_history"] and payload["on"] < row["answer_history"][-1]["on"]):
                    raise ValueError("不能倒写较新的答复")
                row["answer_history"].append({"on": payload["on"], "answer": answer})
                if boolean(payload.get("complete", False), "完成选择"):
                    row["completed_on"] = payload["on"]
                data["checkpoint"] = {"kind": "answer", "item_id": row["id"], "on": payload["on"]}
            elif kind == "error":
                question_id = text(payload.get("question_id"), "关联原题", 500)
                row = next((r for r in data["exercises"] if r["question_id"] == question_id), None)
                if row is None:
                    raise ValueError("须关联已有真实作答，不能因导入材料建错")
                detail = text(payload.get("detail"), "实际错漏", 5000)
                if not detail:
                    raise ValueError("须记录具体错漏")
                if any(c["question_id"] == question_id for c in data["errors"]):
                    raise ValueError("该题已有错题卡，请关联原卡回顾，不重复建卡")
                first_error_on = payload.get("first_error_on", row.get("completed_on") or row["first_answer_on"])
                iso(first_error_on)
                if first_error_on < row["first_answer_on"]:
                    raise ValueError("首次错误不能早于真实作答")
                data["errors"].append({"id": str(uuid4()), "question_id": question_id,
                    "subject": row["subject"], "first_error_on": first_error_on,
                    "detail": detail, "status": "waiting", "reviews": []})
            elif kind == "review":
                day = payload["on"]
                iso(day)
                plan = daily_plan(data, day)
                stage_number = plan["stage"]["number"]
                if plan["stage"]["status"] in ("paused", "ended") or stage_number not in (1, 2, 3):
                    raise ValueError("当前阶段不开展必答错题回顾")
                if stage_number == 3 and not text(payload.get("authorization"), "用户历史订正选择", 2000):
                    raise ValueError("第三阶段须先有用户明确选择历史订正")
                card = next((c for c in data["errors"] if c["id"] == payload.get("card_id")), None)
                if card is None or card["subject"] not in plan["subjects"]:
                    raise ValueError("错题不存在或超出生效范围")
                if stage_number in (1, 2) and plan["historical_completed"] >= plan["historical_target"]:
                    if not text(payload.get("authorization", ""), "额外订正依据", 2000):
                        raise ValueError("已达历史订正量，额外订正须有用户明确选择")
                if payload.get("prompted") is False or payload.get("independent_pass"):
                    if not text(payload.get("authorization", ""), "用户要求独立检测的依据", 2000):
                        raise ValueError("正式独立检测须有用户明确指令")
                review_card(card, day, text(payload.get("answer"), "用户订正原答"),
                    boolean(payload.get("prompted", True), "提示状态"),
                    boolean(payload.get("independent_pass", False), "独立通过状态"))
            elif kind == "lifecycle":
                value = payload.get("value")
                if value not in ("active", "paused", "ended") or not text(payload.get("evidence"), "用户指令依据", 2000):
                    raise ValueError("启停必须有真实用户指令及有效状态")
                data["lifecycle"] = value
            else:
                raise ValueError("不支持的交互类型")
            data["events"].append({"kind": kind, "at": utc_now()})
        self.store.update(version, action)


def capabilities():
    return {"python": platform.python_version(), "os": platform.system(),
        "setup": "ready", "local_material_import": "ready",
        "stage_and_quota_rules": "ready", "json_recording": "ready",
        "theme_grouping": "ready", "error_rotation": "ready",
        "pdf_text": "available_layout_unverified" if find_spec("pypdf") else "optional_dependency_missing",
        "date_auto_search": "ready_http_or_client_tool_evidence", "question_selection_and_grading": "ready_ai_mediated",
        "scheduled_execution": "ready_requires_instance_activation", "source_monitoring": "ready_feed_or_client_browser",
        "mcp_stdio": "ready", "topic_export": "available" if find_spec("reportlab") and find_spec("pypdf") else "optional_dependency_missing",
        "workbuddy_client": "not_tested", "codex_client": "not_tested", "macos": "not_tested"}
