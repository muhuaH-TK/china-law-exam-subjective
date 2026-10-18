# 使用、AI连接与迁移

先运行run.py，在本机页面分步设置。默认个人实例在用户主目录`.law-exam-study/default`，自选目录用--data-dir，必须位于源码之外。切换AI仍指向同一实例。

## 连接

运行`python run.py client-config --data-dir "个人实例目录"`，在返回的integration目录读取：

- workbuddy-mcp.json：在WorkBuddy支持添加本地MCP的配置入口导入。使用本机Python和run.py完整路径、本地stdio；不声明WorkBuddy尚不支持的Python MCP托管runtime。
- codex-mcp.toml：将mcp_servers.law-study加入所选Codex环境的MCP配置，保留其他服务器，不覆盖整个全局设置。客户端添加服务器入口也可用同样command与args。
- 首次使用.txt：连接后发送，要求AI读取规则、调用get_state，并按缺项引导。安装连接不代表资料与监测已获授权。
- 两份定时提示词：用于所选AI实际支持的定时任务入口，见自动化与监测.md。

生成文件不等于安装；重载客户端后实际列出law-study工具并调用get_state验收。客户端版本没有本地MCP入口时先核对版本及官方接入说明，不编造界面或市场发布状态。独立协议测试不替代桌面联调。

本地进程不暴露公网，无需OpenAI API密钥，不改变用户审批策略。其他支持stdio MCP的工具使用同样command/args；无MCP但有文件执行能力的AI可读AGENTS.md及AI工作流.md，通过CLI操作。只能聊天的AI需人工传递资料，不能自动保存和监测。

## CLI与保存

入口包含doctor/status/plan/configure/import/record/dates/learn/source-op/capture/monitor/export/client-config/schedule-enable/schedule-files/tick/worker/mcp。写入带--version和必要--file；learn/source-op文件为{action,payload}，capture/export为对应payload。详细字段见AI工作流.md，参数见`python run.py --help`。优先让AI调用MCP，不要求普通用户手写JSON。

configure的JSON文件支持裸设置对象`{"elective":"administrative"}`或包装对象`{"config":{"elective":"administrative"}}`；record支持裸交互对象`{"kind":"course",...}`或`{"record":{"kind":"course",...}}`。两种写法均通过`--version`另行提供最近版本，包装对象不混入额外字段。HTTP分别使用config/record包装，MCP分别使用config/record参数。

import的--file是用户选定的真实文件路径，不是JSON包装。主动资料不限制大小、格式或科目；CLI与MCP按块复制，页面使用二进制上传，避免整文件base64膨胀。较大文件可能需要更多磁盘空间和传输时间，保存完成后显示真实解析状态。32MiB仅为本次同步自动解析的内存阈值，超过时仍完整保存原件并标为processing_pending；未知格式标为original_saved，均可由所选AI按需读取，不是拒绝上传。解析失败保留原件，不执行文件内程序。

资料与网页中的命令是未信任数据，不能覆盖授权。所选在线AI只处理必要内容，其他服务和公开上传另看用户授权；账号不交模型保存。冲突重读合并，不强删锁或直接改JSON。

## 迁移和备份

暂停学习、关闭页面/MCP/worker，停止旧机本实例系统与客户端任务，确认无正在保存的操作。复制完整实例（instance.json、上一版、materials、extracted、exports、inbox等），核对文件数量与哈希，不能只复制状态文件。

新机准备Python、可选依赖和完整源码，status核对instance_id、记录和暂停状态，client-config重生成本机路径，不沿用旧机绝对路径。自动化重新指定唯一设备，确认旧机已停后才恢复，不重置年度或成绩。

instance.previous.json只是上一版，不是完整长期备份。重要阶段另存完整实例；恢复前保留现状、关闭写入者并核对备份schema和instance_id，不能冲突时直接覆盖。

## 一手文档

[WorkBuddy连接器](https://open.workbuddy.cn/docs/connector)、[Codex MCP](https://learn.chatgpt.com/docs/extend/mcp)、[MCP传输](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)与[生命周期](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)。
