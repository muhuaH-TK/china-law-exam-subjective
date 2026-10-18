"""页面、CLI和MCP共用的业务路由。"""

from .calendar import refresh
from .documents import export_topic
from .scheduler import enable, schedule_files, tick


def operate(service, operation, payload, version):
    if operation == "learning":
        return service.learning(payload["action"], payload.get("payload", {}), version)
    if operation == "dates":
        return refresh(service, version, payload.get("sources"))
    if operation == "source":
        return service.source_action(payload["action"], payload.get("payload", {}), version)
    if operation == "capture":
        return service.ingest_capture(payload, version)
    if operation == "export":
        return export_topic(service, payload, version)
    if operation == "schedule_enable":
        enable(service, version, payload["instruction"])
        return schedule_files(service)
    raise ValueError("不支持的业务操作")
