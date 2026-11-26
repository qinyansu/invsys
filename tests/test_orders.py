"""客户、销售订单状态机、仪表盘。"""

import unittest

from tests.base import ApiTestBase


class OrderTest(ApiTestBase):
    def setUp(self):
        super().setUp()
        self.product = self.make_product(sku="SKU-001", name="矿泉水", safe_stock=5)
        self.warehouse = self.make_warehouse()
        status, self.customer = self.call("POST", "/api/customers",
                                          {"name": "杭州便利店", "phone": "13800000000"}, token=self.admin)
        self.assertEqual(status, 201)

    def make_order(self, no="SO-001", qty=3, price=10):
        return self.call("POST", "/api/orders",
                         {"no": no, "customer_id": self.customer["id"],
                          "items": [{"sku": "SKU-001", "qty": qty, "price": price}]}, token=self.admin)

    def stock_in(self, qty=10):
        status, _ = self.purchase("P-IN", self.warehouse["id"], [{"sku": "SKU-001", "qty": qty}])
        self.assertEqual(status, 201)

    # ---------- 建单 ----------
    def test_create_order_is_draft(self):
        status, order = self.make_order()
        self.assertEqual(status, 201, order)
        self.assertEqual(order["status"], "draft")
        self.assertEqual(order["total_amount"], 30)
        self.assertEqual(order["customer_name"], "杭州便利店")
        self.assertEqual(len(order["items"]), 1)

    def test_duplicate_order_no(self):
        self.make_order()
        status, body = self.make_order()
        self.assertEqual(status, 409)
        self.assertIn("已存在", body["error"]["message"])

    def test_unknown_customer(self):
        status, body = self.call("POST", "/api/orders",
                                 {"no": "SO-X", "customer_id": 999,
                                  "items": [{"sku": "SKU-001", "qty": 1}]}, token=self.admin)
        self.assertEqual(status, 404)

    def test_unknown_sku(self):
        status, _ = self.call("POST", "/api/orders",
                              {"no": "SO-Y", "customer_id": self.customer["id"],
                               "items": [{"sku": "NOPE", "qty": 1}]}, token=self.admin)
        self.assertEqual(status, 404)

    # ---------- 状态机 ----------
    def test_confirm_draft(self):
        self.make_order()
        status, order = self.call("POST", "/api/orders/SO-001/confirm", token=self.admin)
        self.assertEqual(status, 200)
        self.assertEqual(order["status"], "confirmed")
        self.assertIsNotNone(order["confirmed_at"])

    def test_confirm_twice_returns_409(self):
        self.make_order()
        self.call("POST", "/api/orders/SO-001/confirm", token=self.admin)
        status, body = self.call("POST", "/api/orders/SO-001/confirm", token=self.admin)
        self.assertEqual(status, 409)
        self.assertIn("confirmed", body["error"]["message"])

    def test_ship_requires_confirmed(self):
        self.make_order()
        status, body = self.call("POST", "/api/orders/SO-001/ship", token=self.admin)
        self.assertEqual(status, 409)
        self.assertIn("只有已确认可以发货", body["error"]["message"])

    def test_ship_deducts_stock_and_writes_movement(self):
        self.stock_in(10)
        self.make_order(qty=4)
        self.call("POST", "/api/orders/SO-001/confirm", token=self.admin)

        status, order = self.call("POST", "/api/orders/SO-001/ship", token=self.admin)
        self.assertEqual(status, 200)
        self.assertEqual(order["status"], "shipped")

        _, stock = self.call("GET", f"/api/stock?warehouse_id={self.warehouse['id']}", token=self.admin)
        self.assertEqual(stock[0]["qty"], 6)

        _, moves = self.call("GET", "/api/movements", token=self.admin)
        ship_move = [m for m in moves if m["type"] == "order_ship"][0]
        self.assertEqual(ship_move["qty_delta"], -4)
        self.assertEqual(ship_move["qty_after"], 6)

    def test_ship_insufficient_stock_keeps_status(self):
        self.stock_in(2)
        self.make_order(qty=5)
        self.call("POST", "/api/orders/SO-001/confirm", token=self.admin)

        status, body = self.call("POST", "/api/orders/SO-001/ship", token=self.admin)
        self.assertEqual(status, 409)
        self.assertIn("库存不足", body["error"]["message"])

        _, order = self.call("GET", "/api/orders/SO-001", token=self.admin)
        self.assertEqual(order["status"], "confirmed")   # 发货失败，状态不变

        _, stock = self.call("GET", f"/api/stock?warehouse_id={self.warehouse['id']}", token=self.admin)
        self.assertEqual(stock[0]["qty"], 2)              # 库存没动

    def test_cancel_draft(self):
        self.make_order()
        status, order = self.call("POST", "/api/orders/SO-001/cancel", token=self.admin)
        self.assertEqual(status, 200)
        self.assertEqual(order["status"], "cancelled")

    def test_cancel_shipped_returns_409(self):
        self.stock_in(10)
        self.make_order(qty=1)
        self.call("POST", "/api/orders/SO-001/confirm", token=self.admin)
        self.call("POST", "/api/orders/SO-001/ship", token=self.admin)

        status, body = self.call("POST", "/api/orders/SO-001/cancel", token=self.admin)
        self.assertEqual(status, 409)
        self.assertIn("不能取消", body["error"]["message"])

    def test_list_orders_by_status(self):
        self.make_order(no="SO-001")
        self.make_order(no="SO-002")
        self.call("POST", "/api/orders/SO-002/confirm", token=self.admin)

        status, drafts = self.call("GET", "/api/orders?status=draft", token=self.admin)
        self.assertEqual(status, 200)
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]["no"], "SO-001")


class DashboardTest(ApiTestBase):
    def test_dashboard_counts(self):
        product = self.make_product(sku="SKU-001", name="矿泉水", safe_stock=100)  # 会低于安全库存
        wh = self.make_warehouse()
        _, customer = self.call("POST", "/api/customers", {"name": "客户A"}, token=self.admin)
        self.purchase("P-001", wh["id"], [{"sku": "SKU-001", "qty": 10}])

        self.call("POST", "/api/orders",
                  {"no": "SO-001", "customer_id": customer["id"],
                   "items": [{"sku": "SKU-001", "qty": 3, "price": 10}]}, token=self.admin)
        self.call("POST", "/api/orders/SO-001/confirm", token=self.admin)
        self.call("POST", "/api/orders/SO-001/ship", token=self.admin)
        self.call("POST", "/api/orders",
                  {"no": "SO-002", "customer_id": customer["id"],
                   "items": [{"sku": "SKU-001", "qty": 1, "price": 10}]}, token=self.admin)

        status, board = self.call("GET", "/api/dashboard", token=self.admin)
        self.assertEqual(status, 200)
        self.assertGreaterEqual(board["today_orders"], 2)
        self.assertEqual(board["orders"], 2)
        self.assertEqual(board["pending_orders"], 1)     # 只有 SO-002 是草稿
        self.assertEqual(board["customers"], 1)
        self.assertGreater(board["month_amount"], 0)
        self.assertEqual(board["top_products"][0]["sku"], "SKU-001")
        self.assertEqual(board["low_stock_count"], 1)     # 安全库存 100，实际 7


class CustomerTest(ApiTestBase):
    def test_create_and_list(self):
        status, c = self.call("POST", "/api/customers",
                              {"name": "客户B", "phone": "139"}, token=self.admin)
        self.assertEqual(status, 201)
        self.assertEqual(c["name"], "客户B")

        status, rows = self.call("GET", "/api/customers?q=" + self.q("客户B"), token=self.admin)
        self.assertEqual(status, 200)
        self.assertEqual(len(rows), 1)

    def test_missing_name_returns_400(self):
        status, _ = self.call("POST", "/api/customers", {"phone": "1"}, token=self.admin)
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
