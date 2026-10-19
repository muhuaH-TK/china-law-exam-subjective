"""本机任务执行器和原生系统任务模板。学习任务只生成当日入口，不伪造交互。"""

from contextlib import contextmanager
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import plistlib
import socket
import subprocess
import sys
import time
from xml.etree import ElementTree as ET

from .policy import daily_plan
from .monitor import monitor_gate
from .store import atomic_bytes, utc_now


def device_id():
    return sha256((platform.system()+":"+socket.gethostname()).encode()).hexdigest()[:24]


@contextmanager
def runner_lock(directory):
    path = directory / "runner.lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError("本实例已有运行器；不删除锁、不并行启动另一个运行器") from exc
    with os.fdopen(fd, "w") as handle:
        handle.write(str(os.getpid()))
    try:
        yield
    finally:
        path.unlink()


def tick(service, now=None):
    from .service import today_in
    with runner_lock(service.store.directory):
        data, _ = service.store.snapshot()
        runtime = data.get("runtime", {})
        if runtime.get("device_id") != device_id():
            return {"status": "device_not_enabled", "instruction": "先在指定的唯一自动化设备上运行schedule-enable；迁移后重新指定，不继承旧机任务。"}
        zone = data["config"]["timezone"]
        from zoneinfo import ZoneInfo
        try:
            local = now or datetime.now(ZoneInfo(zone))
        except (KeyError, ValueError):
            from datetime import timezone, timedelta
            local = now or datetime.now(timezone(timedelta(hours=8)))
        day, clock = local.date().isoformat(), local.strftime("%H:%M")
        config = data["config"]["automation"]
        plan = daily_plan(data, day)
        previous = data.get("job_days", {}).get(day, {})
        monitor_report = None
        if config["monitor_requested"] and clock >= config["monitor_time"] and "monitor" not in previous and monitor_gate(data["config"], day)["eligible"]:
            monitor_report = service.monitor_once(day)
        _, version = service.store.snapshot()
        notifications = []
        def mutate(d):
            from .experience import study_allowed
            jobs = d.setdefault("job_days", {}).setdefault(day, {})
            if monitor_report is not None:
                jobs["monitor"] = {"at": utc_now(), "report": monitor_report}
                pending = [r for r in monitor_report.get("sources", []) if r["status"] in ("requires_client_browser", "failed")]
                processed = sum(len(r.get("processed", [])) for r in monitor_report.get("sources", []))
                if pending or processed:
                    notification = {"id": "monitor:"+day, "kind": "monitor", "on": day,
                                    "status": "pending", "required_sources": pending,
                                    "text": "题源监测有新资料或需由AI浏览器处理的来源；按授权和当日剩余预算继续。"}
                    d.setdefault("inbox", []).append(notification)
                    notifications.append(notification)
            if config["learning_requested"] and clock >= config["learning_time"] and "learning" not in jobs:
                if study_allowed(data) and data["lifecycle"] == "active" and plan["stage"]["number"] in (1, 2, 3, 4) and plan["stage"]["status"] not in ("paused", "ended", "finished"):
                    notice = {"id": "learning:"+day, "kind": "learning", "on": day, "status": "pending",
                              "text": "今日复习入口已准备。请在所选AI打开同一实例，先续接，再按今日阶段安排；第四阶段阅读，不要求做题。"}
                    d.setdefault("inbox", []).append(notice)
                    notifications.append(notice)
                jobs["learning"] = {"at": utc_now(), "status": "prepared" if notifications and notifications[-1]["kind"] == "learning" else "not_applicable"}
            d["runtime"].update(last_tick_at=utc_now(), last_local_day=day, last_status="tick_completed")
        service.store.update(version, mutate)
        if notifications:
            atomic_bytes(service.store.directory / "inbox" / (day+".md"),
                ("# 当日任务\n\n"+"\n\n".join(n["text"] for n in notifications)+"\n").encode())
        return {"status": "tick_completed", "on": day, "notifications": notifications,
                "note": "学习通知和待浏览任务保存在本机收件箱；未配置AI客户端自身定时任务时，不声称已唤醒客户端。"}


def enable(service, version, instruction):
    from .learning import required
    required(instruction, "指定本机运行自动化的用户指令", 2000)
    def action(data):
        data["runtime"] = {"device_id": device_id(), "enabled_at": utc_now(), "evidence": instruction, "last_status": "enabled_not_yet_running"}
    service.store.update(version, action)


def schedule_files(service):
    data, _ = service.store.snapshot()
    if data.get("runtime", {}).get("device_id") != device_id():
        raise ValueError("须先指定本机为唯一自动化设备")
    root = Path(__file__).resolve().parent.parent
    output = service.store.directory / "integration"
    output.mkdir(exist_ok=True)
    args = [str(root/"run.py"), "tick", "--data-dir", str(service.store.directory)]
    label = "lawstudy."+data["instance_id"]
    plist = {"Label": label, "ProgramArguments": [sys.executable, *args], "StartInterval": 300,
             "RunAtLoad": True, "WorkingDirectory": str(root),
             "StandardErrorPath": str(output/"runner-error.log")}
    atomic_bytes(output/"macos.plist", plistlib.dumps(plist))
    task = ET.Element("Task", version="1.2", xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task")
    registration = ET.SubElement(task, "RegistrationInfo")
    ET.SubElement(registration, "Description").text = "法考复习本机任务："+data["instance_id"]
    triggers = ET.SubElement(task, "Triggers")
    trigger = ET.SubElement(triggers, "TimeTrigger")
    repetition = ET.SubElement(trigger, "Repetition")
    ET.SubElement(repetition, "Interval").text = "PT5M"
    ET.SubElement(trigger, "StartBoundary").text = datetime.now().replace(microsecond=0).isoformat()
    ET.SubElement(trigger, "Enabled").text = "true"
    settings = ET.SubElement(task, "Settings")
    ET.SubElement(settings, "MultipleInstancesPolicy").text = "IgnoreNew"
    ET.SubElement(settings, "StartWhenAvailable").text = "true"
    ET.SubElement(settings, "Hidden").text = "true"
    ET.SubElement(settings, "ExecutionTimeLimit").text = "PT5M"
    actions = ET.SubElement(task, "Actions")
    execute = ET.SubElement(actions, "Exec")
    # pythonw防止系统计划任务弹出控制台；未安装pythonw时保留普通解释器并如实说明。
    executable = Path(sys.executable)
    pythonw = executable.with_name("pythonw.exe")
    ET.SubElement(execute, "Command").text = str(pythonw if pythonw.exists() else executable)
    ET.SubElement(execute, "Arguments").text = subprocess.list2cmdline(args)
    ET.SubElement(execute, "WorkingDirectory").text = str(root)
    atomic_bytes(output/"windows-task.xml", ET.tostring(task, encoding="utf-16", xml_declaration=True))
    return {"label": label, "directory": str(output), "installed": False,
            "windows_command": subprocess.list2cmdline(["schtasks", "/Create", "/TN", label, "/XML", str(output/"windows-task.xml")]),
            "macos_instruction": "将macos.plist复制到你自己的~/Library/LaunchAgents/"+label+".plist，然后通过launchctl bootstrap gui/你的用户ID 该文件启动。",
            "note": "生成模板未安装系统任务；安装后检查系统任务状态和实例runtime.last_tick_at。不要在第二台设备重复安装。"}


def worker(service):
    # 逐次tick短锁，不独占实例写锁，不抢占交互保存。
    while True:
        report = tick(service)
        if report["status"] == "device_not_enabled":
            raise ValueError(report["instruction"])
        if report.get("notifications"):
            print(json.dumps(report, ensure_ascii=False), flush=True)
        time.sleep(30)
