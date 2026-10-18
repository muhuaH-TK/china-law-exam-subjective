"""有界公开网页读取；不接收Cookie，不访问本机或内网。"""

import ipaddress
import socket
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


def public_url(url):
    p = urlsplit(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password or p.port not in (None, 443):
        raise ValueError("联网来源须为不含凭证的公开HTTPS网址")
    if p.hostname.lower() == "localhost" or p.hostname.endswith(".local"):
        raise ValueError("不能读取本机或内网来源")
    addresses = socket.getaddrinfo(p.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("来源未解析到公开网络地址")
    return url


class PublicRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, timeout=6, maximum=2*1024*1024):
    public_url(url)
    request = Request(url, headers={"User-Agent": "LawStudy/0.2 (+local personal study)",
                                    "Accept": "text/html,application/rss+xml,application/json,text/plain"})
    with build_opener(PublicRedirect()).open(request, timeout=timeout) as response:
        public_url(response.url)
        if response.headers.get("Content-Encoding", "identity") != "identity":
            raise ValueError("来源压缩编码不受支持，请由已授权浏览器读取")
        raw = response.read(maximum+1)
        if len(raw) > maximum:
            raise ValueError("来源响应过大，请缩小范围")
        charset = response.headers.get_content_charset() or "utf-8"
        return {"url": response.url, "body": raw.decode(charset, errors="replace"),
                "content_type": response.headers.get_content_type()}
