"""按真实记录生成专题Markdown、可编辑Word与带实际页码的中文PDF。"""

from hashlib import sha256
from html import escape
from importlib.util import find_spec
import io
import json
import re
from uuid import uuid4
from xml.etree import ElementTree as ET
import zipfile

from .learning import answer_current, exercise_answer_history, find, required, full_explanation
from .store import atomic_bytes, utc_now


def word_bytes(title, sections):
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    ET.register_namespace("w", ns)
    def tag(name):
        return "{"+ns+"}"+name
    root = ET.Element(tag("document"))
    body = ET.SubElement(root, tag("body"))
    def paragraph(parent, text, heading=False):
        p = ET.SubElement(parent, tag("p"))
        if heading:
            props = ET.SubElement(p, tag("pPr"))
            ET.SubElement(props, tag("pStyle"), {tag("val"): "Heading1"})
        for line in text.split("\n"):
            r = ET.SubElement(p, tag("r"))
            props = ET.SubElement(r, tag("rPr"))
            ET.SubElement(props, tag("rFonts"), {tag("eastAsia"): "宋体", tag("ascii"): "Calibri"})
            ET.SubElement(r, tag("t"), {"{http://www.w3.org/XML/1998/namespace}space": "preserve"}).text = line
            ET.SubElement(r, tag("br"))
    paragraph(body, title, True)
    paragraph(body, "本文件可编辑；文中页码指同次导出的PDF物理页。Word修改或重新排版后页码可能不同，请以PDF导航为准。")
    for section in sections:
        paragraph(body, section["title"], True)
        for block in section["blocks"]:
            if isinstance(block, str):
                paragraph(body, block)
            else:
                table = ET.SubElement(body, tag("tbl"))
                props = ET.SubElement(table, tag("tblPr"))
                borders = ET.SubElement(props, tag("tblBorders"))
                for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
                    ET.SubElement(borders, tag(side), {tag("val"): "single", tag("sz"): "4"})
                for row in [block["headers"], *block["rows"]]:
                    tr = ET.SubElement(table, tag("tr"))
                    for cell in row:
                        paragraph(ET.SubElement(tr, tag("tc")), str(cell))
    sect = ET.SubElement(body, tag("sectPr"))
    ET.SubElement(sect, tag("pgSz"), {tag("w"): "11906", tag("h"): "16838"})
    ET.SubElement(sect, tag("pgMar"), {tag("top"): "1134", tag("bottom"): "1134", tag("left"): "1134", tag("right"): "1134"})
    types = '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>'
    relationships = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'
    styles = '<w:styles xmlns:w="'+ns+'"><w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="30"/></w:rPr></w:style></w:styles>'
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("word/document.xml", ET.tostring(root, encoding="utf-8", xml_declaration=True))
        archive.writestr("word/styles.xml", styles)
        archive.writestr("word/_rels/document.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
    return out.getvalue()


def pdf_bytes(title, sections):
    if not find_spec("reportlab"):
        raise ValueError("PDF导出需要可选依赖reportlab，请先安装requirements-export.txt")
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
    if "STSong-Light" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    style = ParagraphStyle("body", fontName="STSong-Light", fontSize=10.5, leading=17, wordWrap="CJK", spaceAfter=7)
    heading = ParagraphStyle("heading", parent=style, fontSize=16, leading=22, spaceBefore=8, spaceAfter=12)
    small = ParagraphStyle("small", parent=style, fontSize=9, leading=14)
    def paragraph(value, styling=style):
        return Paragraph(escape(str(value)).replace("\n", "<br/>"), styling)
    mapping = {}
    class Document(SimpleDocTemplate):
        def afterFlowable(self, flowable):
            key = getattr(flowable, "section_key", None)
            if key:
                mapping[key] = self.page
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(getattr(flowable, "section_title", key), key, 0)
    def footer(canvas, doc):
        canvas.setFont("STSong-Light", 9)
        canvas.drawRightString(A4[0]-42, 24, str(doc.page))
    out = io.BytesIO()
    doc = Document(out, pagesize=A4, leftMargin=42, rightMargin=42, topMargin=40, bottomMargin=42, title=title, author="")
    story = [paragraph(title, heading), paragraph("资料交付不代表已经学习或独立通过。历史答复和首答评价按原记录保留。", small)]
    for index, section in enumerate(sections):
        if section.get("new_page") and index:
            story.append(PageBreak())
        h = paragraph(section["title"], heading)
        h.section_key, h.section_title = section["key"], section["title"]
        story.append(h)
        for block in section["blocks"]:
            if isinstance(block, str):
                story.append(paragraph(block))
            else:
                cells = [[paragraph(c, small) for c in row] for row in [block["headers"], *block["rows"]]]
                table = Table(cells, colWidths=[(A4[0]-84)/len(block["headers"])]*len(block["headers"]), repeatRows=1, hAlign="LEFT")
                table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eaf1f0")),
                                           ("GRID", (0, 0), (-1, -1), .4, colors.HexColor("#adbdb9")), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
                story.append(table)
        story.append(Spacer(1, 8))
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return out.getvalue(), mapping


def build_sections(data, payload, pages=None):
    sections = []
    selected = payload.get("topics")
    if not isinstance(selected, list) or not selected:
        raise ValueError("专题须有完整知识点列表")
    included, required_cards = set(), set(payload.get("card_ids", []))
    for i, topic in enumerate(selected):
        title = required(topic.get("title"), "知识点标题", 500)
        knowledge = required(topic.get("knowledge"), "完整规则、条件、否定分支、例外、后果及法条")
        def references(value):
            # AI可把标记直接写在相应条件、表格行或分支旁；程序按最终PDF映射替换。
            def replace(match):
                card_id = match[1]
                if card_id not in topic.get("card_ids", []):
                    raise ValueError("页码标记指向未关联的历史错题")
                page = (pages or {}).get("error-"+card_id)
                return "PDF第"+str(page)+"页" if page else "PDF待分页"
            return re.sub(r"\{page:([^}]+)\}", replace, str(value))
        knowledge = references(knowledge)
        blocks = [knowledge]
        for table in topic.get("tables", []):
            if not isinstance(table, dict) or not isinstance(table.get("headers"), list) or not 1 <= len(table["headers"]) <= 6:
                raise ValueError("分类表须有1至6列标题")
            if not isinstance(table.get("rows"), list) or any(not isinstance(r, list) or len(r) != len(table["headers"]) for r in table["rows"]):
                raise ValueError("分类表各行列数须对应")
            blocks.append({"headers": [references(h) for h in table["headers"]], "rows": [[references(c) for c in row] for row in table["rows"]]})
        for card_id in topic.get("card_ids", []):
            card = find(data["errors"], card_id)
            q = find(data.get("questions", []), card["question_id"])
            row = find(data["exercises"], q["id"], "question_id")
            answer = answer_current(q)
            if not answer:
                raise ValueError("关联错题缺少核验答案，不生成看似完整的专题")
            key = "error-"+card_id
            page = (pages or {}).get(key)
            blocks.append(f"关联原题：{q['source_name']} / {q['locator']}"+(f"（PDF第{page}页）" if page else ""))
            included.add(card_id)
        sections.append({"key": f"topic-{i}", "title": title, "blocks": blocks})
    if required_cards-included:
        raise ValueError("有指定历史错漏尚未关联到知识点，不能遗漏后交付")
    for card_id in sorted(included):
        card = find(data["errors"], card_id)
        q = find(data["questions"], card["question_id"])
        row = find(data["exercises"], q["id"], "question_id")
        answer = answer_current(q)
        blocks = ["案情\n"+q["facts"], "原设问\n"+"\n".join(q["questions"]),
                  "真实历史作答（首答、订正与复测分别保留）\n"+"\n\n".join(
                      r["on"]+" · "+{"first": "首答", "correction": "订正"}.get(r.get("phase"), "历史作答（旧记录未标阶段）")
                      +(" · 历史回顾" if r.get("mode") == "history" else "")
                      +(" · 知识点提示后，非独立通过" if r.get("prompted") else "")
                      +(" · 本次答复未完成" if r.get("complete") is False else "")+"："+r["answer"]
                      for r in exercise_answer_history(data, row)),
                  "原错漏\n"+card["detail"], "正确答案\n"+answer["label"]+"\n"+answer["content"]+"\n依据："+answer["basis"],
                  "后续表现\n"+("\n".join(r["on"]+"："+("独立通过" if r["independent_pass"] else "教学回顾，未记独立通过")+"；实际答复："+r["answer"] for r in card.get("reviews", [])) or "尚无到期回顾记录，不补造学习完成。")]
        sections.append({"key": "error-"+card_id, "title": q["source_name"]+" / "+q["locator"], "blocks": blocks, "new_page": True})
    nav = []
    for i, topic in enumerate(selected):
        page = (pages or {}).get(f"topic-{i}")
        nav.append(topic["title"]+(f"：PDF第{page}页。" if page else "。")+required(topic.get("navigation"), "浏览导航中的实质规则与分支摘要", 10000))
    sections.append({"key": "navigation", "title": "知识点浏览导航", "blocks": nav, "new_page": True})
    return sections


def export_topic(service, payload, version):
    data, current = service.store.snapshot()
    if current != version:
        raise ValueError("实例已变化，请重读后整理")
    title = required(payload.get("title"), "专题名称", 200)
    sections = build_sections(data, payload)
    pdf, pages = pdf_bytes(title, sections)
    # 页码文字会改变分页；固定点迭代后再核验最终映射，而非沿用第一次页数。
    for _ in range(6):
        sections = build_sections(data, payload, pages)
        pdf, actual = pdf_bytes(title, sections)
        if actual == pages:
            break
        pages = actual
    else:
        raise ValueError("页码引用未稳定，不交付未经核验的导航")
    if not find_spec("pypdf"):
        raise ValueError("最终PDF页码核验需要可选依赖pypdf")
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(pdf))
    for section in sections:
        p = pages[section["key"]]
        extracted = reader.pages[p-1].extract_text() or ""
        if re.sub(r"\s", "", section["title"]) not in re.sub(r"\s", "", extracted):
            raise ValueError("PDF标题与实际页码不匹配，已停止交付")
    folder = "exports/"+str(uuid4())
    output = service.store.directory/folder
    markdown = "# "+title+"\n\n"+"\n\n".join("## "+s["title"]+"\n\n"+"\n\n".join(b if isinstance(b, str) else "\n".join(" | ".join(map(str, r)) for r in [b["headers"], *b["rows"]]) for b in s["blocks"]) for s in sections)
    files = {"复习资料.pdf": pdf, "复习资料.docx": word_bytes(title, sections), "复习资料.md": markdown.encode()}
    manifest = {"title": title, "created_at": utc_now(), "pdf_pages": len(reader.pages), "page_map": pages,
                "pdf_page_numbers_verified": True, "word_page_numbers_verified": False,
                "visual_review": "pending", "learning_completed": False,
                "files": {name: sha256(raw).hexdigest() for name, raw in files.items()}}
    def action(d):
        for name, raw in files.items():
            atomic_bytes(output/name, raw)
        atomic_bytes(output/"manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode())
        d.setdefault("exports", []).append({"id": folder.split("/")[-1], "path": folder, **manifest})
    service.store.update(version, action)
    return {"directory": str(output), **manifest, "instruction": "正文与实际PDF页码已核对；还须由当前AI渲染检查后交付。Word为可编辑版，页码引用明确指PDF，不能称Word已分页核验。"}
