"""受控复制迁移：先停任务、冻结原实例、核验备份，保留源目录和回退依据。"""
from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import uuid4

from .learning import required
from .store import Store, atomic_bytes, utc_now


def file_digest(path):
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inventory(folder):
    result = {}
    for path in sorted(folder.rglob("*")):
        relative = path.relative_to(folder)
        if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
            raise ValueError("迁移目录含符号链接或越界路径，先核实，不扩展复制范围")
        if path.is_file() and str(relative) not in ("instance.lock", "runner.lock") and not (len(relative.parts) == 1 and path.name.startswith(".upload-")):
            result[relative.as_posix()] = file_digest(path)
    return result


def copy_verified(source, target, manifest):
    for relative, expected in manifest.items():
        a, b = source/relative, target/relative
        if not a.resolve().is_relative_to(source.resolve()) or not b.resolve().is_relative_to(target.resolve()):
            raise ValueError("迁移清单路径越界")
        b.parent.mkdir(parents=True, exist_ok=True)
        with a.open("rb") as original, b.open("xb") as copied:
            for block in iter(lambda: original.read(1024*1024), b""):
                copied.write(block)
            copied.flush()
            os.fsync(copied.fileno())
        if file_digest(b) != expected or file_digest(a) != expected:
            raise ValueError("迁移文件摘要不符，保留原目录及失败现场")


def check_destination(source, workspace, data_dir):
    source, workspace, data_dir = source.resolve(), workspace.resolve(), data_dir.resolve()
    if workspace == workspace.parent or data_dir == data_dir.parent:
        raise ValueError("迁移目标须为专用目录，不能为磁盘根目录")
    if data_dir.is_relative_to(source) or source.is_relative_to(data_dir) or workspace.is_relative_to(source):
        raise ValueError("源实例与目标目录不能相互嵌套")
    root = Path(__file__).resolve().parent.parent
    if data_dir.is_relative_to(root) and not data_dir.is_relative_to(root/"tests/.tmp"):
        raise ValueError("正式迁移实例不能写入源码目录")
    if (workspace/".law-study/project.json").exists() or (data_dir.exists() and any(data_dir.iterdir())):
        raise ValueError("目标已有绑定或数据，不覆盖；请选择新目标目录")


def migration_action(service, project, action, payload, version):
    instruction = required(payload.get("user_instruction"), "用户指定迁移或恢复的实际依据", 10000)
    data, current = service.store.snapshot()
    if version != current:
        raise ValueError("内容已变化，请重读迁移状态")
    if action == "prepare":
        if data.get("migration", {}).get("status") in ("prepared", "failed"):
            raise ValueError("已有未收口迁移，先继续或cancel恢复")
        desktop = data.get("desktop", {})
        if any(r["status"] != "disabled" for r in desktop.get("tasks", {}).values()) or any(r["status"] == "pending" for r in desktop.get("creation_intents", {}).values()):
            raise ValueError("先实际停用旧任务并核验，查明未决创建，再迁移")
        if (service.store.directory/"runner.lock").exists():
            raise ValueError("旧本机运行器仍持锁；先正常停止，不删除锁")
        if data.get("runtime"):
            required(payload.get("runner_stopped_evidence"), "实际停止旧系统任务/运行器的证据", 10000)
        workspace = Path(required(payload.get("target_workspace"), "用户指定目标工作空间", 4000)).expanduser().resolve()
        target = Path(payload["target_data_dir"]).expanduser().resolve() if payload.get("target_data_dir") else workspace/".law-study/data"
        check_destination(service.store.directory, workspace, target)
        migration_id = str(uuid4())
        backup = service.store.directory.parent/".law-study-backups"/migration_id
        row = {"id": migration_id, "status": "prepared", "role": "source", "source_workspace": project["workspace"],
               "source_data_dir": str(service.store.directory), "target_workspace": str(workspace), "target_data_dir": str(target),
               "backup": str(backup), "instance_id": data["instance_id"], "backup_verified": False,
               "at": utc_now(), "user_instruction": instruction}
        def freeze(d):
            backup.mkdir(parents=True, exist_ok=False)
            atomic_bytes(backup/"instance-before.json", service.store.path.read_bytes())
            d.setdefault("migration_history", []).append(dict(row))
            d["migration"] = row
        service.store.update(version, freeze)
        try:
            manifest = inventory(service.store.directory)
            copy_verified(service.store.directory, backup/"files", manifest)
            atomic_bytes(backup/"manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode())
            def verified(d):
                d["migration"]["backup_verified"] = True
                d["migration"]["manifest_sha256"] = file_digest(backup/"manifest.json")
            service.store.update(service.store.snapshot()[1], verified, allow_migration=True)
        except (OSError, ValueError):
            def failed(d):
                d["migration"].update(status="failed", failure="备份未核验完成；保留源目录，使用cancel恢复")
            service.store.update(service.store.snapshot()[1], failed, allow_migration=True)
            raise
        return {**service.store.snapshot()[0]["migration"], "instruction": "备份已逐文件核验，源实例暂冻结；execute复制，cancel恢复；不删除源目录。"}
    row = data.get("migration", {})
    if row.get("status") not in ("prepared", "failed") or row.get("role") != "source":
        raise ValueError("没有待执行或待恢复的源迁移")
    workspace, target, backup = (Path(row[k]) for k in ("target_workspace", "target_data_dir", "backup"))
    if action == "cancel":
        if (workspace/".law-study/project.json").exists():
            target_binding = json.loads((workspace/".law-study/project.json").read_text(encoding="utf-8"))
            if target_binding.get("instance_id") == row["instance_id"]:
                raise ValueError("目标已绑定；先finalize核实，不能恢复出两个可写实例")
        def cancel(d):
            d["migration"].update(status="cancelled", restored_at=utc_now(), recovery_instruction=instruction)
        service.store.update(version, cancel, allow_migration=True)
        return {"status": "cancelled", "source_preserved": True, "instruction": "源实例已恢复可写，旧任务仍保持停用；失败副本和备份保留，不自动启用任务。"}
    if action not in ("execute", "finalize"):
        raise ValueError("迁移操作须为prepare/execute/finalize/cancel")
    from .desktop import binding, bootstrap, service_for
    if action == "execute":
        if not row["backup_verified"] or file_digest(backup/"manifest.json") != row["manifest_sha256"]:
            raise ValueError("完整备份尚未核验或清单已变，不能迁移")
        check_destination(service.store.directory, workspace, target)
        manifest = json.loads((backup/"manifest.json").read_text(encoding="utf-8"))
        source_files = inventory(service.store.directory)
        if {k: v for k, v in source_files.items() if k not in ("instance.json", "instance.previous.json")} != {k: v for k, v in manifest.items() if k not in ("instance.json", "instance.previous.json")}:
            raise ValueError("冻结后源资料发生变化，先核实，不复制不一致资料")
        try:
            copy_verified(backup/"files", target, manifest)
            target_store = Store(target)
            def relocate(d):
                if d["instance_id"] != row["instance_id"]:
                    raise ValueError("迁移实例身份不符")
                d["migration"] = {**row, "role": "target", "status": "completed", "completed_at": utc_now()}
                desktop = d.setdefault("desktop", {})
                desktop.setdefault("owner_history", []).append({"previous": desktop.pop("automation_owner", None), "at": utc_now(), "evidence": "受控迁移后重新认领"})
                desktop["workspace"] = str(workspace)
                desktop["host_project"] = {"status": "pending", "previous": desktop.get("host_project"), "reason": "新位置须核验桌面项目关联，也可跳过"}
                desktop["discoveries"] = {}
                desktop["creation_intents"] = {}
                if d.get("runtime"):
                    d.setdefault("runtime_history", []).append(d.pop("runtime"))
            target_store.update(target_store.snapshot()[1], relocate, allow_migration=True)
            bootstrap(workspace, data["config"]["selected_ai"], instruction, target)
        except (OSError, ValueError):
            def failed(d):
                d["migration"].update(status="failed", failure="目标可能已有部分副本；源资料保留。已绑定用finalize，未绑定可cancel恢复。")
            service.store.update(service.store.snapshot()[1], failed, allow_migration=True)
            raise
    bound = binding(workspace)
    if not bound or bound["instance_id"] != row["instance_id"] or Path(bound["data_dir"]).resolve() != target.resolve():
        raise ValueError("目标绑定尚未核验，不能结束源迁移")
    moved, _ = service_for(workspace)
    target_data, _ = moved.store.snapshot()
    if target_data.get("migration", {}).get("id") != row["id"] or target_data["migration"].get("role") != "target" or target_data["migration"].get("status") != "completed":
        raise ValueError("目标迁移身份或完成状态未核验")
    from .integration import generate
    generate(moved)
    def complete(d):
        d["migration"].update(status="completed", completed_at=utc_now())
    service.store.update(service.store.snapshot()[1], complete, allow_migration=True)
    return {"status": "completed", "instance_id": row["instance_id"], "workspace": str(workspace),
            "data_dir": str(target), "backup": str(backup), "source_preserved": True,
            "instruction": "原资料保留，旧入口按受控记录续接新位置；在新位置核验或跳过桌面项目、重新claim并对账同步原任务，不直接启用旧任务。"}
