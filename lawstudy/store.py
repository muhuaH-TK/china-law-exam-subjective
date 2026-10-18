"""单实例JSON：锁、内容版本校验、备份、原子替换。"""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4


class ConflictError(ValueError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def blank_instance():
    return {"schema_version": 1, "instance_id": str(uuid4()), "revision": 0,
        "created_at": utc_now(), "updated_at": utc_now(), "lifecycle": "setup",
        "config": {"year": None, "elective": "undecided", "participation": "undecided",
            "selected_ai": "workbuddy", "timezone": "Asia/Hong_Kong", "stage_override": None,
            "questions_per_unit": 1, "theory_target_chars": 1000,
            "exam_dates": {k: {"date": None, "status": "pending", "source_url": "", "evidence": ""}
                           for k in ("objective_end", "results", "subjective")},
            "authorization": {"search_mode": "provided_only", "scope": "", "online_ai": False},
            "automation": {"learning_requested": False, "learning_time": "09:30",
                           "monitor_requested": False, "monitor_time": "22:00", "daily_limit": 10}},
        "materials": [], "course_units": [], "exercises": [], "errors": [],
        "sources": [], "checkpoint": None, "events": []}


def atomic_bytes(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, suffix=".tmp") as handle:
        temporary = Path(handle.name)
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).expanduser().resolve()
        source_root = Path(__file__).resolve().parent.parent
        if self.directory.is_relative_to(source_root) and not self.directory.is_relative_to(source_root/"tests"/".tmp"):
            raise ValueError("个人实例须放在源码目录之外，避免随GitHub发布；测试只使用tests/.tmp隔离目录")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "instance.json"
        if not self.path.exists():
            with self.lock():
                if not self.path.exists():
                    self._write(blank_instance())

    @contextmanager
    def lock(self):
        path = self.directory / "instance.lock"
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise ConflictError("实例正由其他操作保存，请稍后重读；不要删除已有锁") from exc
        os.close(fd)
        try:
            yield
        finally:
            path.unlink()

    def snapshot(self):
        raw = self.path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
        if data.get("schema_version") != 1:
            raise ValueError("实例版本不受支持，已停止读取和写入")
        return data, sha256(raw).hexdigest()

    def _write(self, data):
        atomic_bytes(self.path, (json.dumps(data, ensure_ascii=False, indent=2)+"\n").encode("utf-8"))

    def update(self, expected_version, action):
        if not expected_version:
            raise ConflictError("保存须带上读取时的版本")
        with self.lock():
            data, version = self.snapshot()
            if version != expected_version:
                raise ConflictError("内容已变化，请重新读取并合并后保存")
            changed = deepcopy(data)
            result = action(changed)
            changed["revision"] += 1
            changed["updated_at"] = utc_now()
            atomic_bytes(self.directory / "instance.previous.json", self.path.read_bytes())
            self._write(changed)
        return result
