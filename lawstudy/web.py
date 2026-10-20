"""仅监听本机的首次使用页面，不连接外部服务。"""

import base64
import binascii
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
from urllib.parse import unquote

from .service import Service
from .store import ConflictError
from .operations import operate
from .integration import generate

STATIC = Path(__file__).resolve().parent.parent / "static"
MAX_REQUEST = 15 * 1024 * 1024


class RequestBody:
    def __init__(self, stream, length):
        self.stream, self.remaining = stream, length

    def read(self, size=-1):
        chunk = self.stream.read(self.remaining if size < 0 else min(size, self.remaining))
        self.remaining -= len(chunk)
        return chunk


def make_server(directory, port=0):
    service = Service(directory)
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            # 避免把用户原答、路径或授权内容写进访问日志。
            pass

        def reply(self, status, content, content_type="application/json; charset=utf-8"):
            raw = content if isinstance(content, bytes) else json.dumps(content, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(raw)

        def trusted(self, api=False):
            allowed = f"127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") != allowed:
                self.reply(403, {"error": "仅接受本机页面请求"})
                return False
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + allowed:
                self.reply(403, {"error": "请求来源不匹配"})
                return False
            if api and not secrets.compare_digest(self.headers.get("X-App-Token", ""), token):
                self.reply(403, {"error": "页面已失效，请重新打开本机地址"})
                return False
            return True

        def discard_body(self, body):
            if body is None or not body.remaining:
                return
            previous_timeout = self.connection.gettimeout()
            self.connection.settimeout(5)
            try:
                while body.remaining:
                    if not body.read(65536):
                        break
            except OSError:
                pass
            finally:
                self.connection.settimeout(previous_timeout)

        def do_GET(self):
            if not self.trusted(api=self.path.startswith("/api/")):
                return
            if self.path == "/api/state":
                try:
                    self.reply(200, service.status())
                except (ValueError, OSError, KeyError, TypeError) as exc:
                    self.reply(400, {"error": str(exc)})
                return
            if self.path == "/api/learning":
                self.reply(200, service.learning_state())
                return
            mapping = {"/": ("index.html", "text/html; charset=utf-8"),
                       "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                       "/style.css": ("style.css", "text/css; charset=utf-8")}
            if self.path not in mapping:
                self.reply(404, {"error": "页面不存在"})
                return
            name, mime = mapping[self.path]
            raw = (STATIC/name).read_bytes()
            if name == "index.html":
                raw = raw.replace(b"__APP_TOKEN__", token.encode("ascii"))
            self.reply(200, raw, mime)

        def do_POST(self):
            if not self.trusted(api=True):
                return
            body = None
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or self.headers.get("Transfer-Encoding"):
                    raise ValueError("请使用带实际Content-Length的请求")
                body = RequestBody(self.rfile, length)
                if self.path == "/api/material-upload":
                    if self.headers.get("Content-Type", "").split(";")[0] != "application/octet-stream":
                        raise ValueError("文件上传须使用二进制正文")
                    if self.headers.get("Content-Length") is None:
                        raise ValueError("上传须提供实际Content-Length")
                    result = service.import_stream(
                        unquote(self.headers.get("X-File-Name", ""), errors="strict"), body,
                        unquote(self.headers.get("X-File-Subject", "综合"), errors="strict"),
                        self.headers.get("X-File-Category", "other"), self.headers.get("X-Instance-Version"), length=length)
                    self.reply(200, {"result": result, "state": service.status()})
                    return
                if not length:
                    raise ValueError("提交内容为空")
                # 用户资料兼容旧JSON上传也不设大小上限；普通控制请求另行保护。
                if length > MAX_REQUEST and self.path != "/api/material":
                    self.discard_body(body)
                    self.reply(413, {"error": "普通设置请求过大；资料文件请使用上传入口，资料大小不设上限"})
                    return
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError("提交格式应为JSON")
                payload = json.loads(body.read())
                if not isinstance(payload, dict):
                    raise ValueError("请求须为对象")
                version = payload.get("version")
                if self.path == "/api/config":
                    service.configure(payload["config"], version, payload.get('user_instruction'))
                elif self.path == "/api/material":
                    result = service.import_material(payload["name"], base64.b64decode(payload["content"], validate=True),
                        payload["subject"], payload["category"], version)
                    self.reply(200, {"result": result, "state": service.status()})
                    return
                elif self.path == "/api/source":
                    service.add_source(payload["source"], version)
                elif self.path == "/api/record":
                    service.record(payload["record"], version)
                elif self.path == "/api/operate":
                    result = operate(service, payload["operation"], payload.get("payload", {}), version)
                    self.reply(200, {"result": result, "state": service.status()})
                    return
                elif self.path == "/api/integration":
                    self.reply(200, {"result": generate(service), "state": service.status()})
                    return
                elif self.path == "/api/monitor":
                    self.reply(200, {"result": service.monitor_once(service.status()["plan"]["day"]), "state": service.status()})
                    return
                else:
                    self.reply(404, {"error": "操作不存在"})
                    return
                self.reply(200, {"state": service.status()})
            except ConflictError as exc:
                self.discard_body(body)
                self.reply(409, {"error": str(exc)})
            except (ValueError, KeyError, TypeError, OSError, binascii.Error) as exc:
                self.discard_body(body)
                self.reply(400, {"error": str(exc)})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server
