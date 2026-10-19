"""固定任务、每日运行与实际聊天分别登记，不把运行成功记作学完。"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .experience import usage_mode, project_choice
from .learning import required, find
from .policy import iso
from .store import utc_now


def local_time(value, zone):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("原生调度时间必须带时区")
    try:
        from zoneinfo import ZoneInfo
        target = ZoneInfo(zone)
    except (ValueError, KeyError):
        if zone not in ("Asia/Shanghai", "Asia/Hong_Kong"):
            raise ValueError("时区未准备好，不能确认定时触发")
        target = timezone(timedelta(hours=8))
    return result.astimezone(target)


def run_action(service, action, payload, version):
    evidence = required(payload.get("evidence"), "实际运行或聊天操作证据", 20000)
    tool = required(payload.get("tool"), "实际宿主工具或界面核查方式", 1000)
    result = {}
    def mutate(data):
        mode, client = usage_mode(data), data["config"]["selected_ai"]
        if mode not in ("formal", "test"):
            raise ValueError("安装准备不开始正式运行；先确认正式复习或创建独立测试实例")
        runs = data.setdefault("desktop_runs", [])
        if action == "begin":
            day, role, trigger = payload.get("on"), payload.get("role", "learning"), payload.get("trigger", "chat")
            iso(day)
            if role not in ("learning", "monitor") or trigger not in ("chat", "manual", "scheduled"):
                raise ValueError("运行角色或触发类型无效")
            if role == "monitor":
                from .monitor import monitor_gate
                if not monitor_gate(data["config"], day)["eligible"]:
                    raise ValueError("当前模式或阶段不能运行真实监测")
            receipt = data.get("desktop", {}).get("tasks", {}).get(role)
            trigger_record = {"trigger": trigger, "tool": tool, "evidence": evidence, "at": utc_now()}
            if trigger != "chat":
                if day != service.status()["plan"]["day"]:
                    raise ValueError("原生触发须记录实际当地当天，不预填未来或补造旧日运行")
                from .native_tasks import automation_plan
                spec = next(r for r in automation_plan(service, day)["tasks"] if r["role"] == role)
                if not receipt or receipt["status"] != "verified" or not receipt.get("observed") or receipt["task_id"] != payload.get("task_id") or receipt.get("fingerprint") != spec["fingerprint"]:
                    raise ValueError("先核对当前真实任务，配置尚未同步的任务不能通过触发验收")
                trigger_record["task_id"] = receipt["task_id"]
            if trigger == "scheduled":
                event = payload.get("scheduler_event")
                if not isinstance(event, dict) or event.get("trigger") != "scheduled":
                    raise ValueError("缺少真实原生调度事件，立即运行不能充当到点触发")
                scheduled = local_time(event.get("scheduled_for", ""), data["config"]["timezone"])
                started = local_time(event.get("started_at", ""), data["config"]["timezone"])
                if scheduled.date().isoformat() != day or started.date().isoformat() != day or started < scheduled:
                    raise ValueError("调度日期或实际开始时间不符，不补记离线旧日触发")
                expected = data["config"]["automation"][role+"_time"]
                if mode == "test":
                    target = local_time(data["test_options"]["run_at"], data["config"]["timezone"])
                    if scheduled != target:
                        raise ValueError("测试调度时刻不符合约定的一次测试时间")
                elif scheduled.strftime("%H:%M") != expected:
                    raise ValueError("原生调度时刻与已核验任务不符")
                trigger_record["scheduler_event"] = {"event_id": required(event.get("event_id"), "实际调度运行ID", 1000),
                    "trigger": "scheduled", "scheduled_for": scheduled.isoformat(), "started_at": started.isoformat()}
            row = next((r for r in runs if r["on"] == day and r["role"] == role and r["mode"] == mode and r["client"] == client), None)
            if row is None:
                row = {"id": str(uuid4()), "on": day, "role": role, "mode": mode, "client": client,
                       "status": "pending", "attempts": [], "chats": [], "learning_completed": False}
                runs.append(row)
            # 同日重试保留原聊天和运行，不重复生成正式学习量。
            trigger_record["attempt_id"] = str(uuid4())
            row["attempts"].append(trigger_record)
            if mode == "test" and trigger != "chat":
                data["test_options"]["cleanup_required"] = True
            result.update({"run": row, "attempt_id": trigger_record["attempt_id"], "resume": bool(row["chats"]),
                           "expected_title": ("测试 " if mode == "test" else "")+day,
                           "instruction": "使用真实宿主工具核验每日聊天及标题和项目归属，再record_chat；运行记录不代表已学习完成。"})
            return
        row = find(runs, payload.get("run_id"))
        if row["client"] != client or row["mode"] != mode:
            raise ValueError("运行记录不属于当前客户端或模式")
        if action == "record_chat":
            chat_id = required(payload.get("chat_id"), "实际聊天ID", 1000)
            title = required(payload.get("title"), "回读的聊天标题", 500)
            expected = ("测试 " if mode == "test" else "")+row["on"] if row["role"] == "learning" else ("测试｜" if mode == "test" else "")+"法考题源监测与维护"
            if title != expected:
                raise ValueError("聊天标题未核验为"+expected+"；固定任务名不能代替日期聊天标题")
            choice = project_choice(data)
            project_id = payload.get("project_id")
            if choice["status"] == "created" and project_id != choice.get("project_id"):
                raise ValueError("聊天未核验归入已选桌面项目")
            if choice["status"] == "pending":
                raise ValueError("先创建项目或登记用户跳过/实际受阻")
            if row["chats"] and row["chats"][-1]["chat_id"] != chat_id:
                raise ValueError("当日已有学习聊天；先续接，不在重试时登记另一个主聊天")
            if any(r["id"] != row["id"] and r["role"] == "learning" and any(c["chat_id"] == chat_id for c in r["chats"]) for r in runs) and row["role"] == "learning":
                raise ValueError("每天须新建日期学习聊天，不能仅重命名昨日聊天")
            chat = {"chat_id": chat_id, "title": title, "project_id": project_id, "tool": tool, "evidence": evidence, "at": utc_now()}
            row["chats"].append(chat)
            result.update(chat)
        elif action == "finish":
            status = payload.get("status")
            if status not in ("passed", "failed", "blocked"):
                raise ValueError("运行结果无效")
            attempt = find(row["attempts"], payload.get("attempt_id"), "attempt_id")
            if attempt.get("result"):
                raise ValueError("该次运行已登记结果，不重写旧验收")
            if status == "passed" and not row["chats"]:
                raise ValueError("未核验实际聊天，不能标运行通过")
            attempt["result"] = {"status": status, "tool": tool, "evidence": evidence, "at": utc_now()}
            row["status"] = status
            if status == "passed" and attempt["trigger"] in ("manual", "scheduled"):
                from .scheduler import device_id
                data.setdefault("acceptance", []).append({"id": str(uuid4()), "check": attempt["trigger"]+"_trigger",
                    "status": "passed", "kind": "client", "client": client, "mode": mode, "device_id": device_id(),
                    "instance_id": data["instance_id"], "run_id": row["id"], "attempt_id": attempt["attempt_id"],
                    "tool": tool, "evidence": evidence, "client_ref": row["chats"][-1]["chat_id"], "at": utc_now()})
                data["acceptance"][-1]["workspace"] = data.get("desktop", {}).get("workspace")
                from . import __version__
                data["acceptance"][-1]["software_version"] = __version__
            result.update({"status": status, "learning_completed": False, "test_cleanup_required": bool(data.get("test_options", {}).get("cleanup_required"))})
        else:
            raise ValueError("未知运行操作")
    service.store.update(version, mutate)
    return result
