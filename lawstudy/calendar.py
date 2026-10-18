"""年度官方公告发现、正文提取与可追溯日期合并。"""

from datetime import date
from hashlib import sha256
from html.parser import HTMLParser
import re
import time
import unicodedata
from urllib.parse import urljoin, urlsplit

from .network import fetch
from .store import utc_now

KEYS = ("objective_end", "results", "subjective")
SEEDS = ("https://www.moj.gov.cn/pub/sfbgw/zwgkztzl/",
         "https://www.moj.gov.cn/jgsz/jgszzsdw/zsdwgjsfkszx/",
         "https://legalinfo.moj.gov.cn/zhfxfzzx/fzzxyw/")


def official(url):
    p = urlsplit(url)
    host = (p.hostname or "").lower()
    return p.scheme == "https" and not p.username and not p.password and (host == "moj.gov.cn" or host.endswith(".moj.gov.cn"))


class Page(HTMLParser):
    def __init__(self, body):
        super().__init__()
        self.parts, self.links, self.hidden, self.in_title, self.title = [], [], 0, False, ""
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.hidden += 1
        if tag == "title":
            self.in_title = True
        if tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3"):
            self.parts.append("\n")
        if tag == "a":
            link = dict(attrs).get("href")
            if link:
                self.links.append(link)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.hidden = max(0, self.hidden-1)
        if tag == "title":
            self.in_title = False
        if tag in ("p", "div", "li", "tr"):
            self.parts.append("\n")

    def handle_data(self, text):
        if not self.hidden:
            self.parts.append(text)
            if self.in_title:
                self.title += text

    @property
    def text(self):
        return unicodedata.normalize("NFKC", "".join(self.parts))


def dates_in(fragment, year):
    result = []
    # 一个片段里的明确年度必须适用，不使用往年日期或毕业年份。
    for m in re.finditer(r"(?:(20\d{2})年)?(\d{1,2})月(\d{1,2})日?(?:[、至—-](\d{1,2})日?)?", fragment):
        if m[1] and int(m[1]) != year:
            continue
        for day in (m[3], m[4]):
            if day:
                try:
                    result.append(date(year, int(m[2]), int(day)).isoformat())
                except ValueError:
                    pass
    return result


def extract(year, source):
    """只解析适用年度、全国性公告；地区调整保留为候选而不覆盖全国日期。"""
    if not official(source["url"]):
        raise ValueError("自动核验仅接受司法部及所属官方平台")
    page = Page(source["body"])
    title = unicodedata.normalize("NFKC", source.get("title") or page.title)
    if str(year) not in title or not any(w in title for w in ("法律职业资格", "法考")):
        return []
    if any(w in title for w in ("考区", "延期", "延考", "各省", "北京", "广西", "上海", "西藏", "新疆")):
        return []
    text = page.text
    # 专题、索引同时包含多篇新闻，不能当作单篇有归属的公告。
    if any(w in title for w in ("专题", "中心动态", "新闻列表")):
        return []
    found, context = [], None
    for raw in re.split(r"[。！？\n]", text):
        sentence = re.sub(r"\s+", "", raw)
        if not sentence or len(sentence) > 1200:
            continue
        if re.search(r"20\d{2}年", sentence) and str(year)+"年" not in sentence:
            continue
        if sentence.startswith("问:") or sentence.startswith("问："):
            context = None
        if len(sentence) < 35 and re.fullmatch(r"[一二三四五六七八九十0-9、.()]*客观题考试[:：]?", sentence):
            context = "objective"
        elif len(sentence) < 35 and re.fullmatch(r"[一二三四五六七八九十0-9、.()]*主观题考试[:：]?", sentence):
            context = "subjective"
        key = None
        # 先识别成绩，避免把同句“客观题”误认为考试。
        if "成绩" in sentence and any(w in sentence for w in ("公布", "查询", "发布")) and ("客观" in sentence or "客观" in title or context == "objective"):
            key = "results"
        elif ("客观题" in sentence or context == "objective") and "考试" in sentence and any(w in sentence for w in ("时间为", "时间是", "定于", "举行", "月")):
            if not any(w in sentence for w in ("报名", "准考证", "缴费", "交费", "成绩")):
                key = "objective_end"
        elif ("主观题" in sentence or context == "subjective") and "考试" in sentence and not any(w in sentence for w in ("报名", "准考证", "缴费", "交费", "成绩")):
            key = "subjective"
        if key:
            # 同一成绩段落可能还含报名截止；优先成绩动词附近的日期。
            fragment = sentence
            if key == "results":
                match = re.search(r"(?:将于|自|于)?((?:20\d{2}年)?\d{1,2}月\d{1,2}日)(?:起|\d{1,2}时|\d{1,2}:\d{2})?[^，；]{0,18}(?:公布|查询|发布)", sentence)
                if match:
                    fragment = match[1]
                else:
                    match = re.search(r"(?:成绩公布|公布成绩|成绩查询)[^，；]{0,12}?((?:20\d{2}年)?\d{1,2}月\d{1,2}日)", sentence)
                    if match:
                        fragment = match[1]
            dates = dates_in(fragment, year)
            if not dates:
                continue
            # 多个不同的非客观批次日期，避免随意选择。
            if key != "objective_end" and len(set(dates)) != 1:
                continue
            found.append({"key": key, "date": max(dates), "source_url": source["url"],
                          "title": title, "published_at": source.get("published_at"),
                          "evidence": sentence, "retrieved_at": utc_now(),
                          "retrieval_method": source.get("retrieval_method", "local_public_http"),
                          "evidence_ref": source.get("evidence_ref"),
                          "body_sha256": sha256(source["body"].encode()).hexdigest(), "applicability": "national"})
    return found


def discover(year, reader=fetch):
    started, pending, seen, sources, failures = time.monotonic(), list(SEEDS), set(), [], []
    while pending and len(seen) < 10 and time.monotonic()-started < 18:
        url = pending.pop(0)
        if url in seen or not official(url):
            continue
        seen.add(url)
        try:
            source = reader(url, timeout=min(4, max(1, 18-(time.monotonic()-started))))
            if not official(source["url"]):
                raise ValueError("公告重定向后不在司法部范围")
            sources.append(source)
            page = Page(source["body"])
            candidates = [urljoin(url, link) for link in page.links]
            # 优先适用年度路径，不穷举站点或回溯整个新闻库。
            pending.extend(link for link in candidates if official(link) and str(year) in link and link not in seen)
        except (OSError, ValueError) as exc:
            failures.append({"url": url, "error": str(exc)[:300]})
    return sources, failures


def refresh(service, version, supplied=None, reader=fetch):
    before, actual = service.store.snapshot()
    if actual != version:
        raise ValueError("内容已变化，请重新读取版本")
    year = before["config"]["year"]
    if not year:
        raise ValueError("先确认备考年度")
    if supplied is None:
        sources, failures = discover(year, reader)
    else:
        if not isinstance(supplied, list) or len(supplied) > 10:
            raise ValueError("每次最多提供10篇实际检索公告")
        for source in supplied:
            if source.get("retrieval_method") != "client_web_tool" or not source.get("evidence_ref"):
                raise ValueError("客户端检索必须保留实际工具引用，不能伪装为程序已联网")
            if not isinstance(source.get("body"), str) or len(source["body"]) > 2000000:
                raise ValueError("公告正文无效")
        sources, failures = supplied, []
    candidates = [c for source in sources for c in extract(year, source)]
    report = {"at": utc_now(), "year": year, "failures": failures, "candidates": candidates, "outcomes": {}}
    def action(data):
        nodes = data["config"]["exam_dates"]
        for key in KEYS:
            rows = [c for c in candidates if c["key"] == key]
            choices = set(r["date"] for r in rows)
            old = dict(nodes[key])
            if old.get("status") in ("verified", "conflict") and old.get("date"):
                choices.add(old["date"])
            if len(choices) > 1 or old.get("status") == "conflict":
                nodes[key] = {**old, "status": "conflict"}
                outcome = "conflict"
            elif rows:
                picked = rows[-1]
                nodes[key] = {"date": picked["date"], "status": "verified", "source_url": picked["source_url"], "evidence": picked["evidence"]}
                outcome = "verified"
            elif old.get("status") == "verified":
                outcome = "cached_verified_fetch_failed" if failures else "cached_verified"
            else:
                # 未查到不等于尚未公布，失败不清空此前人工提供日期。
                nodes[key] = {**old, "status": "failed" if failures else "not_found"}
                outcome = nodes[key]["status"]
            report["outcomes"][key] = outcome
        confirmed = [nodes[k]["date"] for k in KEYS if nodes[k]["status"] == "verified"]
        if confirmed != sorted(set(confirmed)):
            for k in KEYS:
                if nodes[k]["status"] == "verified":
                    nodes[k]["status"] = "conflict"
            report["order_conflict"] = True
        data.setdefault("calendar_history", []).append(report)
    service.store.update(version, action)
    return report
