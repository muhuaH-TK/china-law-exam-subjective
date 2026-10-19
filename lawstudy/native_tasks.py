"""宿主任务对账与回执。仅提供受约束计划，不代替真实客户端工具。"""
from datetime import datetime, timezone
from hashlib import sha256
import json
from uuid import uuid4

from .experience import usage_mode, project_choice, migration_blocked
from .learning import required
from .monitor import monitor_gate
from .scheduler import device_id
from .store import utc_now

SPEC_KEYS = ("instance_id", "role", "mode", "client", "device_id", "workspace", "data_dir",
             "idempotency_key", "name", "timezone", "time", "rrule", "prompt", "host_project_id",
             "schedule_kind", "run_at")


def digest(spec):
    return sha256(json.dumps({k: spec[k] for k in SPEC_KEYS}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def fresh(discovery, fingerprint):
    if not discovery or discovery.get("fingerprint") != fingerprint:
        return False
    try:
        age = (datetime.now(timezone.utc)-datetime.fromisoformat(discovery["at"])).total_seconds()
        return 0 <= age <= 900
    except (KeyError, ValueError, TypeError):
        return False


def identity_matches(spec, expected):
    return all(spec.get(k) == expected[k] for k in ("instance_id", "role", "mode", "client", "device_id"))


def observed_task(value, expected, exact=False):
    if not isinstance(value, dict) or not isinstance(value.get("spec"), dict) or type(value.get("enabled")) is not bool:
        raise ValueError("须提供实际宿主查询的task_id、enabled与规范化spec，不能只回填计划指纹")
    required(value.get("task_id"), "真实任务ID", 1000)
    spec = value["spec"]
    if not identity_matches(spec, expected):
        raise ValueError("任务实例、角色、模式、客户端或设备身份不符")
    comparable = dict(spec)
    if expected.get("host_project_id") is None:
        comparable["host_project_id"] = None  # 用户跳过专用项目时，不强制移出客户端已有分组。
    if exact and (any(k not in spec for k in SPEC_KEYS) or digest(comparable) != expected["fingerprint"]):
        raise ValueError("宿主实际任务规格与当前设置不符；先真实更新并回读，不以重登记刷新指纹")
    return {"task_id": value["task_id"], "enabled": value["enabled"], "spec": {k: spec[k] for k in SPEC_KEYS if k in spec}}


def automation_plan(service, on=None, project=None):
    from .desktop import task_prompt
    state = service.status(on)
    data, _ = service.store.snapshot()
    config, desktop = data["config"], data.get("desktop", {})
    owner = desktop.get("automation_owner")
    ours = {"client": config["selected_ai"], "device_id": device_id()}
    blocked_owner = bool(owner and any(owner.get(k) != v for k, v in ours.items()))
    workspace = project.get("workspace") if project else desktop.get("workspace")
    mode, host_project = usage_mode(data), project_choice(data)
    gate = monitor_gate(config, state["plan"]["day"])
    options = data.get("test_options", {})
    rows = []
    for role in ("learning", "monitor"):
        requested = config["automation"][role+"_requested"] if mode == "formal" else (role == "learning" and options.get("native_requested", False)) if mode == "test" else False
        eligible = bool(requested and workspace and host_project["status"] != "pending" and not migration_blocked(data))
        if role == "monitor":
            eligible = eligible and gate["eligible"] and config["authorization"]["search_mode"] != "provided_only"
        if mode == "test" and options.get("cleanup_required"):
            eligible = False
        clock = config["automation"][role+"_time"]
        hour, minute = map(int, clock.split(":"))
        spec = {"instance_id": data["instance_id"], "role": role, "mode": mode, **ours,
                "name": ("测试｜" if mode == "test" else "")+("法考每日学习" if role == "learning" else "法考题源监测与维护"),
                "workspace": workspace, "data_dir": str(service.store.directory),
                "host_project_id": host_project.get("project_id") if host_project["status"] == "created" else None,
                "idempotency_key": data["instance_id"]+":"+mode+":"+role,
                "timezone": config["timezone"], "time": clock,
                "schedule_kind": "once" if mode == "test" else "daily", "run_at": options.get("run_at") if mode == "test" else None,
                "rrule": None if mode == "test" else f"FREQ=DAILY;BYHOUR={hour};BYMINUTE={minute};BYSECOND=0",
                "prompt": task_prompt(service, role, workspace)}
        fp = digest(spec)
        receipt = desktop.get("tasks", {}).get(role)
        active = bool(receipt and receipt["status"] != "disabled")
        query = desktop.get("discoveries", {}).get(role)
        reservation = desktop.get("creation_intents", {}).get(role)
        reason = "先查询宿主任务并登记完整对账结果；查询失败不能创建"
        task_id = receipt.get("task_id") if receipt else None
        if blocked_owner:
            action, reason = "blocked_owner", "只指定一个客户端和设备运行自动化"
        elif mode == "setup" and active:
            action, reason = "await_mode_confirmation", "已有正式任务；安装准备不擅自改动，先确认使用模式并对账"
        elif not eligible:
            action = "disable" if active else "wait"
            reason = "测试任务待实际停用核验" if options.get("cleanup_required") else "首次先创建、跳过或登记桌面项目受阻" if host_project["status"] == "pending" else "尚未进入正式复习或未要求任务" if not requested else gate["reason"] if role == "monitor" else "工作空间未绑定或正在迁移"
        elif receipt and receipt["status"] == "verified" and receipt.get("fingerprint") == fp and receipt.get("observed"):
            action, reason = "keep", "已保存同一规格的真实宿主回读证据；配置变化后须重新核验"
        elif reservation and reservation.get("status") == "pending":
            action, reason = "awaiting_create_receipt", "已有创建意图，可能已在宿主创建；先查询恢复，不再创建第二个任务"
        elif not fresh(query, fp):
            action = "discover"
        elif query["outcome"] in ("blocked", "conflict"):
            action, reason = "blocked_discovery", "查询失败、候选归属不明或存在多个匹配；先处理对账，不创建"
        elif query["outcome"] == "found":
            action, reason = "update", "复用已核实身份的任务ID，实际同步规格并回读后登记"
            task_id = query["task_id"]
        elif active:
            action, reason = "missing_registered_task", "后台有活动任务但宿主未找到；先查明或确认停用，不直接重建"
        else:
            action, reason = "reserve_create", "完整查询未发现对应任务；先原子登记创建意图，再调用宿主创建"
        rows.append({**spec, "fingerprint": fp, "requested": bool(requested), "eligible": bool(eligible),
                     "action": action, "reason": reason, "task_id": task_id,
                     "discovery_id": query.get("id") if query else None,
                     "creation_id": reservation.get("id") if reservation and reservation.get("status") == "pending" else None})
    applicable = [r for r in rows if r["requested"] and r["eligible"]]
    waiting = [r["role"] for r in rows if r["requested"] and not r["eligible"]]
    return {"client": ours["client"], "owner": owner, "needs_claim": owner is None, "tasks": rows,
            "native_installed": bool(applicable) and all(r["action"] == "keep" for r in applicable),
            "waiting_roles": waiting, "instruction": "任务计划不证明客户端已运行。先claim、查询对账；创建前reserve_create，更新后真实回读。固定任务名，每日新聊天以当地日期命名。"}


def automation_action(service, action, payload, version, project=None):
    evidence = required(payload.get("evidence"), "实际用户指令或宿主操作证据", 20000)
    planned = automation_plan(service, project=project)
    result = {}
    def mutate(data):
        desktop = data.setdefault("desktop", {})
        ours = {"client": data["config"]["selected_ai"], "device_id": device_id()}
        owner = desktop.get("automation_owner")
        if action == "release":
            if any(r["status"] != "disabled" for r in desktop.get("tasks", {}).values()) or any(r["status"] == "pending" for r in desktop.get("creation_intents", {}).values()):
                raise ValueError("先核验旧任务均已停用、未决创建意图已查明，再迁移自动化")
            desktop.setdefault("owner_history", []).append({"previous": owner, "evidence": evidence, "at": utc_now()})
            desktop.pop("automation_owner", None)
            result["released"] = True
            return
        if owner and any(owner.get(k) != v for k, v in ours.items()):
            raise ValueError("自动化已有其他客户端或设备负责，先停旧任务并确认迁移")
        if action == "claim":
            if project:
                desktop["workspace"] = project["workspace"]
            elif payload.get("workspace"):
                from .desktop import binding
                from pathlib import Path
                bound = binding(payload["workspace"])
                if not bound or bound["instance_id"] != data["instance_id"] or Path(bound["data_dir"]).resolve() != service.store.directory:
                    raise ValueError("工作空间须已绑定同一实例")
                desktop["workspace"] = bound["workspace"]
            if not desktop.get("workspace"):
                raise ValueError("先绑定本地复习目录")
            desktop["automation_owner"] = {**ours, "evidence": evidence, "at": utc_now()}
            result["claimed"] = True
            return
        if owner is None:
            raise ValueError("先claim唯一客户端和设备，再查询、创建或登记任务")
        role = payload.get("role")
        if role not in ("learning", "monitor"):
            raise ValueError("任务角色无效")
        spec = next(r for r in planned["tasks"] if r["role"] == role)
        tool = required(payload.get("tool"), "实际宿主工具或界面核查方式", 1000)
        tasks = desktop.setdefault("tasks", {})
        old = tasks.get(role)
        if action == "reconcile":
            if type(payload.get("complete")) is not bool or not isinstance(payload.get("candidates"), list) or len(payload["candidates"]) > 100:
                raise ValueError("对账须包含完整查询标记和候选列表")
            candidates, unknown = [], False
            for candidate in payload["candidates"]:
                value = candidate.get("spec", {}) if isinstance(candidate, dict) else {}
                if not all(k in value for k in ("instance_id", "role", "mode", "client", "device_id")):
                    unknown = True
                elif identity_matches(value, spec):
                    candidates.append(observed_task(candidate, spec))
            outcome = "blocked" if not payload["complete"] or payload.get("error") else "conflict" if unknown or len(candidates) > 1 else "found" if candidates else "not_found"
            row = {"id": str(uuid4()), "outcome": outcome, "at": utc_now(), "fingerprint": spec["fingerprint"],
                   "tool": tool, "evidence": evidence, "candidates": candidates}
            if outcome == "found":
                observed = candidates[0]
                row["task_id"] = observed["task_id"]
                if old and old["status"] != "disabled" and old["task_id"] != row["task_id"]:
                    row["outcome"] = "conflict"
                else:
                    try:
                        verified = observed_task(observed, spec, exact=True)
                    except ValueError:
                        verified = None
                    if verified and verified["enabled"] and spec["eligible"]:
                        tasks[role] = {"role": role, **ours, "task_id": row["task_id"], "status": "verified",
                            "fingerprint": spec["fingerprint"], "observed": verified, "tool": tool, "evidence": evidence, "at": utc_now()}
                        desktop.setdefault("task_history", []).append(dict(tasks[role]))
                        intent = desktop.get("creation_intents", {}).get(role)
                        if intent:
                            intent["status"] = "recovered"
            desktop.setdefault("discoveries", {})[role] = row
            if old and not (row["outcome"] == "found" and tasks.get(role) is not old):
                old["fingerprint"] = None
            desktop.setdefault("discovery_history", []).append(dict(row))
            result.update(row)
            return
        if action == "reserve_create":
            if spec["action"] != "reserve_create" or payload.get("discovery_id") != spec["discovery_id"]:
                raise ValueError("须有当前规格的完整无匹配查询；失败、冲突或未决创建不允许再创建")
            row = {"id": str(uuid4()), "status": "pending", "fingerprint": spec["fingerprint"],
                   "at": utc_now(), "evidence": evidence, "tool": tool}
            desktop.setdefault("creation_intents", {})[role] = row
            result.update({"creation_id": row["id"], "spec": {k: spec[k] for k in SPEC_KEYS}})
            return
        if action == "abandon_creation":
            required(payload.get("user_instruction"), "查询查明未创建后用户允许重试的实际依据", 5000)
            query = desktop.get("discoveries", {}).get(role)
            intent = desktop.get("creation_intents", {}).get(role)
            if not fresh(query, spec["fingerprint"]) or query["outcome"] != "not_found" or not intent or intent["status"] != "pending":
                raise ValueError("先完整查询并查明未创建；不能盲目撤销未决创建意图")
            intent.update(status="cancelled", evidence=evidence, cancelled_at=utc_now())
            result["cancelled"] = True
            return
        if action != "record_task":
            raise ValueError("未知原生任务操作")
        status = payload.get("status")
        if status not in ("created", "verified", "disabled", "failed"):
            raise ValueError("任务回执状态无效")
        task_id = required(payload.get("task_id"), "宿主真实任务ID", 1000)
        if any(k != role and r["task_id"] == task_id and r["status"] != "disabled" for k, r in tasks.items()):
            raise ValueError("学习与监测不能共用活动任务ID")
        if old and old["status"] != "disabled" and old["task_id"] != task_id:
            raise ValueError("已有活动任务，不登记第二个ID；先核对并停用旧任务")
        intent = desktop.get("creation_intents", {}).get(role)
        discovered = desktop.get("discoveries", {}).get(role, {})
        known = bool(old and old["task_id"] == task_id) or (discovered.get("outcome") == "found" and discovered.get("task_id") == task_id)
        if not known and not (intent and intent["status"] == "pending" and payload.get("creation_id") == intent["id"] and intent["fingerprint"] == spec["fingerprint"]):
            raise ValueError("新任务须关联原子创建意图，已有任务须先核实身份，不靠任务名认领")
        observed = None
        if status in ("verified", "disabled"):
            observed = observed_task(payload.get("observed"), spec, exact=status == "verified")
            if observed["task_id"] != task_id or observed["enabled"] != (status == "verified"):
                raise ValueError("回读任务ID或启停状态与回执不符")
        if status in ("created", "verified") and not spec["eligible"]:
            raise ValueError("当前模式、阶段或授权不允许创建该任务")
        row = {"role": role, **ours, "task_id": task_id, "status": status,
               "fingerprint": spec["fingerprint"] if status == "verified" else None,
               "observed": observed, "tool": tool, "evidence": evidence, "at": utc_now()}
        tasks[role] = row
        desktop.setdefault("task_history", []).append(dict(row))
        if intent and status in ("created", "verified", "disabled"):
            intent["status"] = "recorded"
        if status in ("created", "failed"):
            desktop.setdefault("discoveries", {}).pop(role, None)
        if status == "disabled" and usage_mode(data) == "test":
            data.setdefault("test_options", {}).update(cleanup_required=False, native_requested=False)
        result["receipt"] = row
    service.store.update(version, mutate)
    return result
