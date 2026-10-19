#!/usr/bin/env python3
"""由AI后台调用；源码插件与可单独导入的技能包均可运行。"""
import argparse
import json
from pathlib import Path
import sys


def main():
    if sys.version_info < (3, 11):
        print(json.dumps({"error": "需Python3.11以上；请由AI引导准备环境，不让用户手写配置"}, ensure_ascii=False))
        return 1
    skill = Path(__file__).resolve().parents[1]
    candidates = [skill/"runtime", skill.parents[1]]
    root = next((p for p in candidates if (p/"lawstudy/desktop.py").is_file()), None)
    if root is None:
        print(json.dumps({"error": "技能包缺少运行核心；请使用完整安装包，保留原学习目录"}, ensure_ascii=False))
        return 1
    sys.path.insert(0, str(root))
    from lawstudy.desktop import dispatch
    parser = argparse.ArgumentParser(description="法考桌面聊天后台；由AI调用")
    parser.add_argument("--workspace", default=str(Path.cwd()))
    parser.add_argument("--request", help="AI生成的JSON请求文件，-表示stdin；不提供时读取当前停靠点")
    args = parser.parse_args()
    try:
        request = json.load(sys.stdin) if args.request == "-" else json.loads(Path(args.request).read_text(encoding="utf-8-sig")) if args.request else {"action": "context"}
        print(json.dumps(dispatch(args.workspace, request), ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError, ImportError) as exc:
        print(json.dumps({"error": str(exc), "instruction": "冲突重读合并；缺资料或授权则保存停靠点并等必要反馈，不打开设置网页"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
    raise SystemExit(main())
