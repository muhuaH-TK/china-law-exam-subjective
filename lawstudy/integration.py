"""生成当前设备精确路径的客户端配置，不修改用户全局AI设置。"""

import json
from pathlib import Path
import sys

from .store import atomic_bytes


def generate(service):
    root = Path(__file__).resolve().parent.parent
    folder = service.store.directory/"integration"
    command = str(Path(sys.executable).resolve())
    args = [str(root/"run.py"), "mcp", "--data-dir", str(service.store.directory)]
    workbuddy = {"mcpServers": {"law-study": {"type": "stdio", "command": command, "args": args, "timeout": 30000}}}
    # JSON字符串也是TOML基本字符串，处理Windows路径反斜线和Unicode。
    codex = "[mcp_servers.law-study]\ncommand = "+json.dumps(command, ensure_ascii=False)+"\nargs = "+json.dumps(args, ensure_ascii=False)+"\nstartup_timeout_sec = 15\ntool_timeout_sec = 60\n"
    files = {"workbuddy-mcp.json": json.dumps(workbuddy, ensure_ascii=False, indent=2), "codex-mcp.toml": codex,
             "首次使用.txt": f"请读取本项目{root / 'AGENTS.md'}和{root / 'docs' / 'AI工作流.md'}，通过law-study MCP读取我的个人实例。资料目录是{service.store.directory}。先引导我完善设置和资料；不要继承作者的日期、账号、选科、成绩或授权。",
             "学习定时提示词.txt": f"只处理{service.store.directory}这个个人实例。先get_state，检查暂停及考试阶段；只续接今日学习，不补漏做题债。已暂停/结束或无适用阶段时保持安静。理论和案例分开，第四阶段只背诵与资料阅读，第三阶段历史订正只询问，不主动选题。真实用户答复到来前停止，不代答。按docs/AI工作流.md执行。",
             "监测定时提示词.txt": f"只处理{service.store.directory}实例。先get_state、monitor_state/get_inbox。监测从第二阶段开始；monitor_state.gate.eligible为false时不浏览、不抓取、不补处理欠项，保持安静。预置李佳、孟献贵、左宁主页，按当前选科筛选，只读取使用者已启用且核验身份的来源，按游标检查，全部来源每日共享预算；run_monitor执行公开订阅，浏览器来源用本客户端已有且获授权的浏览器真实读取，再ingest_source_capture。不能读取时记录失败，不改权限或借账号。新题、补答案、更正均占预算。先各活动来源查新增再补缺；核验完整原帖、图片、设问和同题答案，未完整则留候选。孟献贵来源涉及民法民诉及商法，排除的商法内容不得用于出题。不得将采集视为已学习或自动建错。无变化且无需用户行动时保持安静；只在实质更新、失败或需用户操作时通知。"}
    for name, value in files.items():
        atomic_bytes(folder/name, (value+"\n").encode())
    return {"directory": str(folder), "files": list(files), "installed": False,
            "note": "在对应客户端添加本地stdio MCP配置。两客户端连接同一实例；不要给彼此复制私人资料。定时提示词须由各客户端实际定时任务执行，保存文件不代表已经创建任务。"}
