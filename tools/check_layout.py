"""只用明确标识的合成数据生成多页排版样例，供开发者渲染检查。"""

import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lawstudy.documents import export_topic
from lawstudy.service import Service
sys.path.insert(0, str(ROOT/"tests"))
from support import confirm_formal
from lawstudy.experience import onboarding_action


def main():
    parent = ROOT/"tests"/".tmp"
    parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent) as directory:
        service = Service(directory)
        def version():
            return service.store.snapshot()[1]
        service.configure({"year": 2027, "participation": "participating", "stage_override": 2}, version(), user_instruction="合成用户配置依据")
        onboarding_action(service, "project", {"status": "skipped", "user_instruction": "排版合成验证，明确不创建真实项目"}, version())
        confirm_formal(service)
        material = service.import_material("合成排版样例.md", "仅为排版合成数据，不含真实法律答案。".encode(), "刑法", "questions", version())["material"]["id"]
        q = service.learning("register_question", {"identity": "synthetic-layout/1", "facts": "合成案情：甲乙间发生一项用于验证排版的具体事件。"*28,
            "questions": ["合成设问一：列明成立条件及其分支。", "合成设问二：对应事实说明例外与后果。"], "subject": "刑法",
            "source_name": "排版验收合成题源", "locator": "测试题一", "material_id": material, "complete": True, "verification": "仅用于排版验证的合成数据，不冒称真实题目"}, version())["question_id"]
        service.learning("answer_version", {"question_id": q, "answer_kind": "ai_reference", "content": "合成参考答案：按条件、例外、分支和后果说明。"*70,
            "basis": "排版合成数据，不是法律意见或真实考试标准"}, version())
        service.record({"kind": "answer", "question_id": q, "subject": "刑法", "on": "2027-09-13", "answer": "合成原答：遗漏分支，仅用于测试，不是实际用户作答。", "complete": True}, version())
        service.record({"kind": "error", "question_id": q, "detail": "合成错漏：遗漏一个测试条件分支。"}, version())
        data, _ = service.store.snapshot()
        card_id = data["errors"][0]["id"]
        result = export_topic(service, {"title": "专题排版验收样例（全部为合成数据）", "card_ids": [card_id], "topics": [
            {"title": "合成主题：条件、例外与分支", "knowledge": "合成成立条件：逐项满足甲、乙两项测试条件。未满足甲时，对应否定分支；满足甲但缺乙时，按另一测试分支判断。本页只检查完整说明的排版，不表达真实法律规则。关联旧题：{page:"+card_id+"}。\n"+("充分说明：条件与法律后果一一对应，例外和反向结论分别列明，不能只给提示词或口诀。"*24),
             "tables": [{"headers": ["判断条件", "分支结论与理由", "回溯原题"], "rows": [["合成条件甲、乙同时成立", "对应合成后果一，并逐项说明事实对应关系。", "{page:"+card_id+"}"], ["甲成立，乙不成立", "对应合成后果二，说明缺少条件的影响。", "{page:"+card_id+"}"], ["符合合成例外", "单列例外的条件和后果；不破坏主题完整性。", "{page:"+card_id+"}"]]}],
             "card_ids": [card_id], "navigation": "甲乙同时满足与仅甲满足时分别对应两种合成分支，符合例外时另按例外判断。"}]}, version())
        output = parent/"layout-sample"
        output.mkdir(exist_ok=True)
        for name in ("复习资料.pdf", "复习资料.docx", "复习资料.md", "manifest.json"):
            shutil.copyfile(Path(result["directory"])/name, output/name)
        print(json.dumps({"output": str(output), "pdf_pages": result["pdf_pages"], "page_map": result["page_map"], "synthetic_only": True}, ensure_ascii=True))


if __name__ == "__main__":
    main()
