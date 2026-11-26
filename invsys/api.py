"""HTTP 层：限流 → 鉴权 → 路由 → 业务 → 统一响应与日志。"""

import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .ratelimit import RateLimiter
from .security import AuthError, require_admin, verify_token
from .service import ApiError, Unauthorized, ValidationError

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}

ROUTES = [
    ("GET",    r"^/api/health$",              "health",           "public"),
    ("POST",   r"^/api/login$",               "login",            "public"),
    ("GET",    r"^/api/products$",            "list_products",    "user"),
    ("POST",   r"^/api/products$",            "create_product",   "admin"),
    ("GET",    r"^/api/warehouses$",          "list_warehouses",  "user"),
    ("POST",   r"^/api/warehouses$",          "create_warehouse", "admin"),
    ("GET",    r"^/api/stock$",               "list_stock",       "user"),
    ("POST",   r"^/api/purchase$",            "purchase",         "user"),
    ("POST",   r"^/api/sale$",                "sale",             "user"),
    ("POST",   r"^/api/adjust$",              "adjust",           "admin"),
    ("GET",    r"^/api/docs$",                "list_docs",        "user"),
    ("GET",    r"^/api/docs/([\w\-]+)$",      "get_doc",          "user"),
    ("GET",    r"^/api/movements$",           "list_movements",   "user"),
    ("GET",    r"^/api/report/low-stock$",    "low_stock",        "user"),
    ("GET",    r"^/api/report/summary$",             "summary",          "user"),
    ("GET",    r"^/api/customers$",                  "list_customers",   "user"),
    ("POST",   r"^/api/customers$",                  "create_customer",  "user"),
    ("GET",    r"^/api/orders$",                     "list_orders",      "user"),
    ("POST",   r"^/api/orders$",                     "create_order",     "user"),
    ("GET",    r"^/api/orders/([\w\-]+)$",           "get_order",        "user"),
    ("POST",   r"^/api/orders/([\w\-]+)/confirm$",   "confirm_order",    "user"),
    ("POST",   r"^/api/orders/([\w\-]+)/ship$",      "ship_order",       "user"),
    ("POST",   r"^/api/orders/([\w\-]+)/cancel$",    "cancel_order",     "user"),
    ("GET",    r"^/api/dashboard$",                  "dashboard",        "user"),
]
COMPILED = [(m, re.compile(p), a, r) for m, p, a, r in ROUTES]


class Handler(BaseHTTPRequestHandler):
    server_version = "invsys/1.0"
    protocol_version = "HTTP/1.0"

    @property
    def service(self):
        return self.server.service

    @property
    def config(self):
        return self.server.config

    @property
    def limiter(self):
        return self.server.limiter

    def _send(self, status, payload=None, headers=None):
        body = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode()
        self._last_status = status
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode())
        except json.JSONDecodeError:
            raise ValidationError("请求体不是合法 JSON")
        if not isinstance(data, dict):
            raise ValidationError("请求体必须是 JSON 对象")
        return data

    def log_message(self, fmt, *args):
        pass

    def _log(self, status, started, user):
        if not getattr(self.config, "log_requests", True):
            return
        sys.stderr.write(json.dumps({
            "method": self.command, "path": self.path, "status": status,
            "ms": round((time.perf_counter() - started) * 1000, 1),
            "ip": self.client_address[0], "user": user,
        }, ensure_ascii=False) + "\n")

    def _handle(self):
        started = time.perf_counter()
        user = "-"
        try:
            allowed, retry = self.limiter.allow(self.client_address[0])
            if not allowed:
                return self._send(429, {"error": {"code": "rate_limited", "message": "请求过于频繁"}},
                                  {"Retry-After": str(retry)})

            parsed = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}

            if not parsed.path.startswith("/api"):
                if self._serve_static(parsed.path):
                    return
                return self._send(404, {"error": {"code": "not_found", "message": "页面不存在"}})

            for method, pattern, action, need in COMPILED:
                match = pattern.match(parsed.path)
                if not match or method != self.command:
                    continue
                if need != "public":
                    claims = self._authenticate()
                    user = claims.get("u", "-")
                    if need == "admin":
                        require_admin(claims)
                return self._invoke(action, match.groups(), query)

            self._send(404, {"error": {"code": "not_found",
                                       "message": f"无此接口：{self.command} {parsed.path}"}})
        except AuthError as e:
            self._send(e.status, {"error": {"code": "unauthorized" if e.status == 401 else "forbidden",
                                            "message": e.message}})
        except ApiError as e:
            self._send(e.status, {"error": {"code": e.code, "message": e.message}})
        except Exception as e:
            self._send(500, {"error": {"code": "internal_error", "message": "服务器内部错误"}})
            sys.stderr.write(f"[error] {e!r}\n")
        finally:
            self._log(getattr(self, "_last_status", "-"), started, user)

    def _authenticate(self):
        header = self.headers.get("Authorization") or ""
        if not header.lower().startswith("bearer "):
            raise Unauthorized("缺少 Authorization: Bearer <token>")
        return verify_token(header[7:].strip(), self.config.secret)

    def _send_bytes(self, status, body, content_type):
        self._last_status = status
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, path):
        if path in ("/", "/index.html"):
            name = "index.html"
        elif path.startswith("/static/"):
            name = path[len("/static/"):]
        else:
            return False

        root = os.path.normpath(self.config.web_dir)
        full = os.path.normpath(os.path.join(root, name))
        # 防目录穿越：解析后的路径必须仍在 web 目录内
        if not full.startswith(root + os.sep) or not os.path.isfile(full):
            return False

        with open(full, "rb") as f:
            body = f.read()
        self._send_bytes(200, body, MIME.get(os.path.splitext(full)[1].lower(), "application/octet-stream"))
        return True

    def _invoke(self, action, path_args, query):
        svc = self.service

        if action == "health":
            return self._send(200, {"status": "ok", "version": self.server_version,
                                    "schema": f"{svc.schema_from}->{svc.schema_to}"})
        if action == "login":
            return self._send(200, svc.login(self._json_body()))

        if action == "list_products":
            return self._send(200, svc.list_products(query.get("q"), query.get("page", 1), query.get("size", 20)))
        if action == "create_product":
            return self._send(201, svc.create_product(self._json_body()))

        if action == "list_warehouses":
            return self._send(200, svc.list_warehouses())
        if action == "create_warehouse":
            return self._send(201, svc.create_warehouse(self._json_body()))

        if action == "list_stock":
            wid = query.get("warehouse_id")
            return self._send(200, svc.list_stock(int(wid) if wid else None, query.get("q")))

        if action == "purchase":
            return self._send(201, svc.purchase(self._json_body()))
        if action == "sale":
            return self._send(201, svc.sale(self._json_body()))
        if action == "adjust":
            return self._send(201, svc.adjust(self._json_body()))

        if action == "list_docs":
            return self._send(200, svc.list_docs(query.get("type"), query.get("limit", 20)))
        if action == "get_doc":
            return self._send(200, svc.get_doc(path_args[0]))

        if action == "list_movements":
            pid = query.get("product_id")
            return self._send(200, svc.list_movements(int(pid) if pid else None, query.get("limit", 50)))

        if action == "low_stock":
            return self._send(200, svc.low_stock())
        if action == "summary":
            return self._send(200, svc.summary())

        if action == "list_customers":
            return self._send(200, svc.list_customers(query.get("q"), query.get("limit", 50)))
        if action == "create_customer":
            return self._send(201, svc.create_customer(self._json_body()))

        if action == "list_orders":
            return self._send(200, svc.list_orders(query.get("status"), query.get("limit", 50)))
        if action == "create_order":
            return self._send(201, svc.create_order(self._json_body()))
        if action == "get_order":
            return self._send(200, svc.get_order(path_args[0]))
        if action == "confirm_order":
            return self._send(200, svc.confirm_order(path_args[0]))
        if action == "ship_order":
            return self._send(200, svc.ship_order(path_args[0]))
        if action == "cancel_order":
            return self._send(200, svc.cancel_order(path_args[0]))

        if action == "dashboard":
            return self._send(200, svc.dashboard())

        self._send(404, {"error": {"code": "not_found", "message": "未实现的接口"}})

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()


class ApiServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, service, config):
        self.service = service
        self.config = config
        self.limiter = RateLimiter(config.rate_limit, config.rate_window)
        super().__init__(address, Handler)


def create_server(service, config, port=None, host="127.0.0.1"):
    actual = config.port if port is None else port   # 注意：0 是合法的"随机端口"
    return ApiServer((host, actual), service, config)
