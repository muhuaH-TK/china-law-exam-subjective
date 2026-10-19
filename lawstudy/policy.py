"""无副作用的阶段、题量、完整主题和错题轮候规则。"""

from datetime import date, timedelta

SUBJECTS = ("理论法", "刑法", "刑事诉讼法", "民事综合", "行政法", "商法")
OFFSETS = (2, 4, 7, 14)


def iso(value):
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("日期须为YYYY-MM-DD")
    return parsed


def subjects(elective):
    if elective not in ("administrative", "commercial", "undecided"):
        raise ValueError("选做方向无效")
    excluded = {"administrative": "商法", "commercial": "行政法"}.get(elective)
    return [s for s in SUBJECTS if s != excluded]


def stage(config, day):
    today = iso(day)
    if config["participation"] == "not_participating":
        return {"number": None, "status": "paused", "reason": "本年度暂不参加主观题，等待后续学习目标"}
    if config["participation"] == "undecided":
        return {"number": None, "status": "needs_confirmation", "reason": "先确认本年度备考状态"}
    if config.get("stage_override"):
        return {"number": config["stage_override"], "status": "user_selected", "reason": "按用户明确选择进入阶段"}
    # 未经核验的日期不能驱动自动阶段切换。
    nodes = {k: iso(v["date"]) for k, v in config["exam_dates"].items()
             if v.get("date") and v.get("status") == "verified"}
    end, results, exam = (nodes.get(k) for k in ("objective_end", "results", "subjective"))
    if exam and today >= exam:
        return {"number": None, "status": "finished", "reason": "已到主观题考试日，停止自动学习安排"}
    if exam and exam - timedelta(days=7) <= today < exam:
        number = 4
    elif results and exam and results < today < exam - timedelta(days=7):
        number = 3
    elif end and results and end < today <= results:
        number = 2
    elif end and today <= end:
        number = 1
    else:
        return {"number": None, "status": "dates_pending", "reason": "官方日期尚缺；可先整理资料，或明确选择当前适用阶段"}
    return {"number": number, "status": "verified_dates", "reason": "依据已核验官方日期"}


def stage_alignment(config, day):
    automatic = stage({**config, "stage_override": None}, day)
    selected = config.get("stage_override")
    mismatch = bool(selected and (automatic["status"] == "finished" or
                    (automatic["number"] is not None and selected != automatic["number"])))
    warnings = []
    if mismatch:
        warnings.append("手动阶段与已核验考期不一致；确认实际学习目标后才保留该阶段，第四阶段为考前7日至前1日。")
    elif selected and selected > 1 and config.get("year") and config["year"] > iso(day).year:
        warnings.append("备考年度尚未开始，却选择了第二至四阶段；请核对是否为测试或主动提前采用该模式，不据此推定官方考期。")
    return {"selected": selected, "calendar": automatic, "requires_confirmation": mismatch, "warnings": warnings}


def balanced_subjects(pool, records, limit, errors=None):
    """先覆盖练习较少者，再比较最近完成日；稳定顺序用于平局。"""
    def key(subject):
        rows = [r for r in records if r["subject"] == subject and r.get("completed_on")]
        weak = sum(c["subject"] == subject and c.get("status") not in ("mastered", "paused", "obsolete") for c in (errors or []))
        return len(rows), -weak, max((r["completed_on"] for r in rows), default=""), pool.index(subject)
    return sorted(pool, key=key)[:limit]


def next_due(card):
    if card.get("status") in ("mastered", "paused", "obsolete"):
        return None
    done = len(card.get("reviews", []))
    if done < len(OFFSETS):
        return (iso(card["first_error_on"]) + timedelta(days=OFFSETS[done])).isoformat()
    return (iso(card["reviews"][-1]["on"]) + timedelta(days=14)).isoformat()


def due_cards(cards, day, active):
    iso(day)
    rows = [c for c in cards if c["subject"] in active and next_due(c) and next_due(c) <= day
            and not any(r["on"] == day for r in c.get("reviews", []))
            and c["first_error_on"] != day]
    return sorted(rows, key=lambda c: (
        bool(c.get("reviews")), next_due(c),
        c["reviews"][-1]["on"] if c.get("reviews") else "", c["id"]))


def review_card(card, day, answer, prompted=True, independent_pass=False):
    """消费一个真实回顾节点；提示后订正不标掌握，不重置D2。"""
    iso(day)
    if not answer.strip():
        raise ValueError("必须保留真实回顾答复")
    if independent_pass and prompted:
        raise ValueError("有提示的订正不能记为独立通过")
    if not next_due(card) or day < next_due(card) or day == card["first_error_on"]:
        raise ValueError("该错题尚未到期或已退出轮候")
    if any(r["on"] >= day for r in card.get("reviews", [])):
        raise ValueError("同题一天只处理一个节点，不倒写旧检测")
    card.setdefault("reviews", []).append({"on": day, "answer": answer,
        "prompted": prompted, "independent_pass": independent_pass})
    # 保留完整D2/D4/D7/D14节奏，D14及后续独立通过才退出。
    if len(card["reviews"]) >= 4 and independent_pass:
        card["status"] = "mastered"
    return card


def theme_group(themes, target=1000):
    """只能组合完整主题，长主题绝不切片。输入顺序即材料顺序。"""
    if not themes:
        return []
    chosen, total = [], 0
    for theme in themes:
        size = len("".join(theme["text"].split()))
        if not size:
            raise ValueError("背诵主题正文不能为空")
        if chosen and abs(total - target) <= abs(total + size - target):
            break
        chosen.append(theme)
        total += size
        if total >= target:
            break
    return chosen


def daily_plan(data, day):
    config = data["config"]
    result = stage(config, day)
    number = result["number"]
    active = subjects(config["elective"])
    # 暂停优先，不因未达到题量继续安排。
    if data["lifecycle"] in ("paused", "ended"):
        result = {"number": number, "status": data["lifecycle"], "reason": "用户已暂停或结束学习"}
        number = None
    records = data["exercises"]
    completed = [r for r in records if r.get("completed_on") == day and r["subject"] in active]
    pending = [r["id"] for r in records if not r.get("completed_on") and r["subject"] in active]
    limit = 3 if number == 2 else 5 if number == 3 else 0
    if number == 1:
        units = [u for u in data["course_units"] if u.get("completed_on") and u["subject"] in active and u.get("assessment_status") != "skipped_no_original"]
        limit = sum(max(0, config["questions_per_unit"] - sum(
            r.get("unit_id") == u["id"] for r in records)) for u in units) + len(completed) + len(pending)
    pool = [s for s in active if number != 2 or s != "理论法"]
    history = [r for r in records if r.get("completed_on") and r["completed_on"] <= day]
    candidates = due_cards(data["errors"], day, active) if number in (1, 2) else []
    done_reviews = sum(any(r["on"] == day for r in c.get("reviews", []))
                       for c in data["errors"] if c["subject"] in active and c.get("status") != "obsolete")
    historical_target = len(completed) if number in (1, 2) else 0
    remaining = max(0, limit - len(completed))
    if number in (2, 3):
        used_today = {r["subject"] for r in completed}
        # 当日已发出的题先续接并保留其名额，不再重复推荐该科。
        reserved = {r["subject"] for r in records if not r.get("completed_on") and r["subject"] in pool}
        available = [s for s in pool if s not in used_today | reserved]
        recommendations = balanced_subjects(available, history, max(0, remaining-len(reserved)), data["errors"])
    else:
        recommendations = []
    return {"day": day, "stage": result, "subjects": active, "case_target": limit,
        "case_completed": len(completed), "case_remaining": remaining,
        "resume_first": pending if number in (1, 2, 3) else [],
        "recommended_subjects": recommendations,
        "historical_target": historical_target,
        "historical_completed": done_reviews,
        "historical_candidates": [c["id"] for c in candidates[:max(0, historical_target-done_reviews)]],
        "historical_optional": number == 3,
        "theory_completed": sum(a.get("completed_on") == day for a in data.get("theory_activities", [])),
        "theory_pending": sum(a.get("on") == day and a.get("status") != "completed" for a in data.get("theory_activities", [])),
        "theory_mode": {1: "暂不背诵", 2: "完整主题背诵与填空", 3: "完整主题背诵与全文默写", 4: "自主背诵"}.get(number, "待确认"),
        "may_ask_continue": number in (1, 2, 3) and remaining == 0 and not pending
            and (number == 3 or done_reviews >= historical_target),
        "notes": ["缺少可用题源时留缺，不自动出题凑数。", "理论与案例分别推进，不设理论待答门槛。",
                  "推荐科目不代表题目已发出、用户已作答或已完成学习。"]}
