#!/usr/bin/env python3
"""无需安装包的统一入口，供人或有本地执行能力的AI调用。"""

import argparse
import json
import os
from pathlib import Path
import sys
import webbrowser

if sys.version_info < (3, 11):
    raise SystemExit("此项目需要Python 3.11或以上版本，请先更新Python。")

from lawstudy.service import Service, capabilities
from lawstudy.store import ConflictError
from lawstudy.web import make_server


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description="法考复习通用项目：首次设置与学习记录核心")
    parser.add_argument("command", nargs="?", default="serve", choices=("serve", "doctor", "status", "plan", "configure", "import", "record", "mcp", "client-config", "dates", "learn", "source-op", "capture", "monitor", "export", "schedule-enable", "schedule-files", "tick", "worker"))
    parser.add_argument("--data-dir", default=os.environ.get("LAW_STUDY_HOME", str(Path.home()/".law-exam-study"/"default")))
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--date", help="明确查询日期YYYY-MM-DD；不修改主机时钟")
    parser.add_argument("--file", help="设置JSON、真实交互JSON或主动提供的资料文件")
    parser.add_argument("--version", help="status返回的内容版本；保存前必须带上")
    parser.add_argument("--subject", default="综合")
    parser.add_argument("--category", default="other")
    parser.add_argument("--instruction", help="用户明确指令的真实依据；用于指定自动化设备")
    args = parser.parse_args(argv)
    if args.command == "doctor":
        emit(capabilities())
        return 0
    try:
        if args.command == "serve":
            server = make_server(args.data_dir, args.port)
            url = f"http://127.0.0.1:{server.server_port}/"
            print("法考复习准备页面：" + url, flush=True)
            print("个人实例：" + str(Path(args.data_dir).expanduser().resolve()), flush=True)
            print("关闭此窗口或按Ctrl+C停止页面服务。", flush=True)
            if not args.no_browser:
                webbrowser.open(url)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return 0
        service = Service(args.data_dir)
        if args.command == "mcp":
            from lawstudy.mcp import serve
            serve(service)
            return 0
        if args.command == "client-config":
            from lawstudy.integration import generate
            emit(generate(service))
            return 0
        if args.command in ("tick", "worker", "schedule-files", "schedule-enable"):
            from lawstudy import scheduler
            if args.command == "tick":
                emit(scheduler.tick(service))
            elif args.command == "worker":
                scheduler.worker(service)
            elif args.command == "schedule-enable":
                scheduler.enable(service, args.version, args.instruction)
                emit(scheduler.schedule_files(service))
            else:
                emit(scheduler.schedule_files(service))
            return 0
        if args.command == "monitor":
            emit(service.monitor_once(args.date or service.status()["plan"]["day"]))
            return 0
        if args.command in ("dates", "learn", "source-op", "capture", "export"):
            from lawstudy.operations import operate
            if not args.version:
                raise ValueError("保存须提供读取时的--version")
            payload = json.loads(Path(args.file).read_text(encoding="utf-8-sig")) if args.file else {}
            operation = {"learn": "learning", "source-op": "source"}.get(args.command, args.command)
            emit({"result": operate(service, operation, payload, args.version), "version": service.status()["version"]})
            return 0
        if args.command in ("configure", "record", "import"):
            if not args.file or not args.version:
                raise ValueError("保存须提供--file和读取时的--version")
            path = Path(args.file).expanduser()
            if args.command == "import":
                if path.stat().st_size > 10*1024*1024:
                    raise ValueError("单份资料上限10MB，请分批提供")
                emit(service.import_material(path.name, path.read_bytes(), args.subject, args.category, args.version))
                return 0
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            if args.command == "configure":
                service.configure(payload, args.version)
            else:
                service.record(payload, args.version)
        state = service.status(args.date)
        emit(state["plan"] if args.command == "plan" else state)
        return 0
    except (ValueError, OSError, KeyError, TypeError, ImportError) as exc:
        emit({"error": str(exc), "conflict": isinstance(exc, ConflictError)})
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
