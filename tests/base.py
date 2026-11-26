"""测试基类：整个会话共用一个真实服务。"""

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from urllib.parse import quote

from invsys import security
from invsys.api import create_server
from invsys.config import Config
from invsys.db import UserRepo, session
from invsys.ratelimit import RateLimiter
from invsys.service import InventoryService

_SHARED = {}
_LOCK = threading.Lock()


def shared():
    with _LOCK:
        if "server" in _SHARED:
            return _SHARED
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        config = Config(db_path=tmp.name, secret="test-secret", token_ttl=3600,
                        rate_limit=100000, rate_window=60, log_requests=False)
        service = InventoryService(config)
        server = create_server(service, config, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        _SHARED.update({"config": config, "service": service, "server": server,
                        "base": f"http://127.0.0.1:{server.server_address[1]}"})
        return _SHARED


class ApiTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s = shared()
        cls.config, cls.service, cls.server, cls.base = s["config"], s["service"], s["server"], s["base"]
        cls.server.limiter = RateLimiter(100000, 60)
        cls.admin = cls.login("admin", "admin123")
        cls.clerk = cls.login("clerk", "clerk123")

    def setUp(self):
        self.server.limiter = RateLimiter(100000, 60)
        with session(self.config.db_path) as conn:
            # 删除顺序必须满足外键：先删子表，再删父表
            for table in ("sales_order_item", "sales_order", "customer",
                          "stock_movement", "doc_item", "doc", "stock",
                          "product", "warehouse"):
                conn.execute(f"DELETE FROM {table}")
            conn.execute("DELETE FROM sqlite_sequence")

    @classmethod
    def raw_call(cls, method, path, body=None, token=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(cls.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except json.JSONDecodeError:
                return e.code, raw.decode("utf-8", "replace")

    def call(self, method, path, body=None, token=None):
        return self.raw_call(method, path, body, token)

    @classmethod
    def login(cls, username, password):
        status, body = cls.raw_call("POST", "/api/login",
                                    {"username": username, "password": password})
        assert status == 200, body
        return body["token"]

    # ---------- 便捷构造 ----------
    def make_product(self, sku="SKU-001", name="矿泉水", safe_stock=0):
        status, product = self.call("POST", "/api/products",
                                    {"sku": sku, "name": name, "unit": "箱", "safe_stock": safe_stock},
                                    token=self.admin)
        self.assertEqual(status, 201, product)
        return product

    def make_warehouse(self, code="WH-01", name="主仓"):
        status, wh = self.call("POST", "/api/warehouses", {"code": code, "name": name}, token=self.admin)
        self.assertEqual(status, 201, wh)
        return wh

    def purchase(self, no, warehouse_id, items):
        return self.call("POST", "/api/purchase",
                         {"no": no, "warehouse_id": warehouse_id, "items": items}, token=self.admin)

    def sale(self, no, warehouse_id, items):
        return self.call("POST", "/api/sale",
                         {"no": no, "warehouse_id": warehouse_id, "items": items}, token=self.admin)

    @staticmethod
    def q(text):
        return quote(text)
