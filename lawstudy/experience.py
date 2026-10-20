"""安装/正式/测试边界、桌面项目选择和分项实机验收。"""
from copy import deepcopy
from uuid import uuid4

from .store import utc_now

PROJECT_STATES = ("pending", "created", "skipped", "blocked")
CHECKS = ("source", "environment", "client_loaded", "backend", "binding", "desktop_project", "setup",
          "native_task", "manual_trigger", "scheduled_trigger", "import", "question", "correction",
          "cross_day", "monitor", "export")


def usage_mode(data):
    return data["config"].get("usage_mode", "setup")


def study_allowed(data):
    from .onboarding import setup_state
    return usage_mode(data) in ("formal", "test") and project_choice(data)["status"] != "pending" and not migration_blocked(data) and setup_state(data)['confirmed']


def migration_blocked(data):
    m = data.get("migration", {})
    return bool(m and (m.get("status") in ("prepared", "failed") or
                      (m.get("status") == "completed" and m.get("role") == "source")))


def project_choice(data):
    choice = data.get("desktop", {}).get("host_project", {"status": "pending"})
    if choice.get("client", data["config"]["selected_ai"]) != data["config"]["selected_ai"]:
        return {"status": "pending", "reason": "切换客户端后核验该客户端的项目关联，也可跳过"}
    return choice


def experience_state(data):
    from . import __version__
    mode = usage_mode(data)
    choice = project_choice(data)
    records = data.get("acceptance", [])
    client = data["config"]["selected_ai"]
    # 不混用另一客户端、测试实例或程序自测的结论。
    from .scheduler import device_id
    workspace = data.get("desktop", {}).get("workspace")
    current = [r for r in records if r["client"] == client and r["mode"] == mode
               and r.get("device_id") == device_id() and r.get("workspace") == workspace
               and r.get("software_version") == __version__]
    latest = {r["check"]: r for r in current}
    from .onboarding import setup_state
    return {"mode": mode, "instance_kind": data.get("instance_kind", "formal"), "host_project": choice, "onboarding": setup_state(data),
            "test_options": data.get("test_options", {}), "capability_checks": data.get("capability_checks", []),
            "migration": data.get("migration"),
            "checks": {key: latest.get(key, {"status": "pending"}) for key in CHECKS},
            "instruction": "安装/测试不自动转正式；桌面项目优先创建，用户跳过或受阻仍可继续。实机证据与程序测试分开。"}


def onboarding_action(service, action, payload, version):
    from .learning import required
    result = {}
    def mutate(data):
        instruction = required(payload.get("user_instruction"), "用户实际选择或操作依据", 10000)
        if action == "project":
            status = payload.get("status")
            if status not in PROJECT_STATES:
                raise ValueError("项目状态须为pending/created/skipped/blocked")
            record = {"status": status, "client": data["config"]["selected_ai"], "at": utc_now(), "user_instruction": instruction}
            if status in ("created", "blocked"):
                record["evidence"] = required(payload.get("evidence"), "实际项目操作或能力核查证据", 10000)
                record["tool"] = required(payload.get("tool"), "实际工具或界面检查方式", 1000)
            if status == "created":
                record["project_id"] = required(payload.get("project_id"), "真实桌面项目ID", 1000)
                record["name"] = required(payload.get("name"), "桌面项目名称", 500)
            desktop = data.setdefault("desktop", {})
            desktop.setdefault("project_history", []).append(deepcopy(record))
            desktop["host_project"] = record
            result.update(record)
        elif action == "answer":
            from .onboarding import record_choice
            result.update(record_choice(data, payload))
        elif action in ("start_formal", "start_test"):
            if action == 'start_formal' and data.get("instance_kind", "formal") == "test":
                raise ValueError("测试实例不能转为正式；回到原正式实例确认，不迁移测试作答或任务")
            if action == 'start_test' and data.get('instance_kind') != 'test':
                raise ValueError('仅独立测试实例可开始测试')
            if project_choice(data)["status"] == "pending":
                raise ValueError("首次先创建桌面项目，或登记用户跳过/实际受阻；本地目录不等于桌面项目")
            from .onboarding import confirm_setup
            confirm_setup(data, payload)
            mode = 'test' if action == 'start_test' else 'formal'
            data["config"]["usage_mode"] = mode
            data.setdefault("mode_history", []).append({"mode": mode, "at": utc_now(), "user_instruction": instruction})
            if data["lifecycle"] == "setup" and data["config"]["year"]:
                data["lifecycle"] = "active"
            result["mode"] = mode
        elif action == "test_options":
            if data.get("instance_kind") != "test":
                raise ValueError("免教材和临时测试任务只适用于独立测试实例")
            allowed = {"user_instruction", "skip_materials", "native_requested", "run_at"}
            if set(payload)-allowed:
                raise ValueError("未知测试选项")
            options = dict(data.get("test_options", {}))
            for key in ("skip_materials", "native_requested"):
                if key in payload:
                    if type(payload[key]) is not bool:
                        raise ValueError("测试选项须为布尔值")
                    options[key] = payload[key]
            if "run_at" in payload:
                from datetime import datetime
                scheduled = datetime.fromisoformat(payload["run_at"])
                if scheduled.tzinfo is None:
                    raise ValueError("测试触发时间须含时区")
                options["run_at"] = scheduled.isoformat()
            if options.get("native_requested") and not options.get("run_at"):
                raise ValueError("启用临时测试任务须明确一次测试时间run_at")
            options["user_instruction"] = instruction
            data["test_options"] = options
            result.update(options)
        elif action == "capability":
            capability = payload.get("capability")
            if capability not in ("project", "task_query", "task_create", "chat_create", "chat_title", "browser"):
                raise ValueError("未知客户端能力")
            method, status = payload.get("method"), payload.get("status")
            if method not in ("native_tool", "official_api", "ui", "tool_inventory") or status not in ("supported", "failed", "unavailable"):
                raise ValueError("能力检查方式或结果无效")
            if method == "tool_inventory" and status != "unavailable":
                raise ValueError("仅有工具列表不能证明能力已可用")
            row = {"capability": capability, "method": method, "status": status,
                   "client": data["config"]["selected_ai"], "at": utc_now(),
                   "evidence": required(payload.get("evidence"), "实际能力检查证据", 10000),
                   "tool": required(payload.get("tool"), "实际工具或界面检查方式", 1000), "user_instruction": instruction}
            if method == "ui":
                row["before_ref"] = required(payload.get("before_ref"), "操作前窗口与菜单状态证据", 2000)
                row["after_ref"] = required(payload.get("after_ref"), "操作后实际结果或失败状态证据", 2000)
            data.setdefault("capability_checks", []).append(row)
            result.update(row)
        else:
            raise ValueError("未知首次使用操作")
        data["events"].append({"kind": "onboarding_"+action, "at": utc_now()})
    service.store.update(version, mutate)
    return result


def acceptance_action(service, payload, version):
    from . import __version__
    from .learning import required
    check, status = payload.get("check"), payload.get("status")
    if check not in CHECKS or status not in ("passed", "failed", "blocked", "skipped"):
        raise ValueError("验收项目或结果无效")
    if check == "scheduled_trigger":
        raise ValueError("定时触发只能由含原生调度证据的实际运行记录确认，不能手写通过")
    tool = required(payload.get("tool"), "实际检查工具", 1000)
    evidence = required(payload.get("evidence"), "实际验收证据", 20000)
    kind = payload.get("kind")
    if kind not in ("program", "client") or (status == "passed" and check not in ("source", "environment") and kind != "client"):
        raise ValueError("程序测试不能代替真实客户端验收")
    if check == "client_loaded" and status == "passed" and payload.get("loaded_version") != __version__:
        raise ValueError("须从实际客户端核对loaded_version与当前版本一致，旧缓存不算新版本已加载")
    result = {}
    def mutate(data):
        from .scheduler import device_id
        row = {"id": str(uuid4()), "check": check, "status": status, "kind": kind,
               "tool": tool, "evidence": evidence, "at": utc_now(), "device_id": device_id(),
               "client": data["config"]["selected_ai"], "mode": usage_mode(data), "instance_id": data["instance_id"]}
        row["workspace"] = data.get("desktop", {}).get("workspace")
        row["software_version"] = __version__
        if kind == "client" and status == "passed":
            row["client_ref"] = required(payload.get("client_ref"), "实际聊天、运行或界面证据引用", 2000)
        data.setdefault("acceptance", []).append(row)
        result.update(row)
    service.store.update(version, mutate)
    return result
