"""盘点、报表、单据查询。"""

import unittest

from tests.base import ApiTestBase


class AdjustTest(ApiTestBase):
    def test_adjust_sets_actual_qty_and_logs_delta(self):
        self.make_product()
        wh = self.make_warehouse()
        self.purchase("P-001", wh["id"], [{"sku": "SKU-001", "qty": 10}])

        status, doc = self.call("POST", "/api/adjust",
                                {"no": "A-001", "warehouse_id": wh["id"],
                                 "items": [{"sku": "SKU-001", "qty": 8}]}, token=self.admin)
        self.assertEqual(status, 201, doc)
        self.assertEqual(doc["type"], "adjust")

        _, stock = self.call("GET", f"/api/stock?warehouse_id={wh['id']}", token=self.admin)
        self.assertEqual(stock[0]["qty"], 8)

        _, moves = self.call("GET", "/api/movements", token=self.admin)
        adjust_move = [m for m in moves if m["type"] == "adjust"][0]
        self.assertEqual(adjust_move["qty_delta"], -2)
        self.assertEqual(adjust_move["qty_before"], 10)
        self.assertEqual(adjust_move["qty_after"], 8)

    def test_adjust_requires_admin(self):
        self.make_product()
        wh = self.make_warehouse()
        status, _ = self.call("POST", "/api/adjust",
                              {"no": "A-002", "warehouse_id": wh["id"],
                               "items": [{"sku": "SKU-001", "qty": 1}]}, token=self.clerk)
        self.assertEqual(status, 403)

    def test_adjust_negative_qty_rejected(self):
        self.make_product()
        wh = self.make_warehouse()
        status, _ = self.call("POST", "/api/adjust",
                              {"no": "A-003", "warehouse_id": wh["id"],
                               "items": [{"sku": "SKU-001", "qty": -1}]}, token=self.admin)
        self.assertEqual(status, 400)


class ReportTest(ApiTestBase):
    def test_low_stock_alert(self):
        self.make_product(sku="A-1", name="安全库存高", safe_stock=10)
        self.make_product(sku="B-1", name="库存充足", safe_stock=1)
        wh = self.make_warehouse()
        self.purchase("P-001", wh["id"], [{"sku": "A-1", "qty": 2}, {"sku": "B-1", "qty": 50}])

        status, rows = self.call("GET", "/api/report/low-stock", token=self.admin)
        self.assertEqual(status, 200)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sku"], "A-1")
        self.assertEqual(rows[0]["total_qty"], 2)

    def test_summary(self):
        self.make_product()
        wh = self.make_warehouse()
        self.purchase("P-001", wh["id"], [{"sku": "SKU-001", "qty": 10, "price": 5}])
        self.sale("S-001", wh["id"], [{"sku": "SKU-001", "qty": 3, "price": 9}])

        status, s = self.call("GET", "/api/report/summary", token=self.admin)
        self.assertEqual(status, 200)
        self.assertEqual(s["products"], 1)
        self.assertEqual(s["warehouses"], 1)
        self.assertEqual(s["stock_total"], 7)
        self.assertEqual(s["docs"], 2)
        self.assertEqual(s["sales_amount"], 27)

    def test_stock_marks_below_safe(self):
        self.make_product(sku="A-1", name="低库存", safe_stock=10)
        wh = self.make_warehouse()
        self.purchase("P-001", wh["id"], [{"sku": "A-1", "qty": 3}])

        _, stock = self.call("GET", "/api/stock", token=self.admin)
        self.assertTrue(stock[0]["below_safe"])


class DocQueryTest(ApiTestBase):
    def test_get_doc_with_items(self):
        self.make_product()
        wh = self.make_warehouse()
        self.purchase("P-001", wh["id"], [{"sku": "SKU-001", "qty": 2, "price": 3}])

        status, doc = self.call("GET", "/api/docs/P-001", token=self.admin)
        self.assertEqual(status, 200)
        self.assertEqual(doc["items"][0]["sku"], "SKU-001")
        self.assertEqual(doc["items"][0]["qty"], 2)

    def test_unknown_doc_returns_404(self):
        status, _ = self.call("GET", "/api/docs/NOPE", token=self.admin)
        self.assertEqual(status, 404)

    def test_list_docs_by_type(self):
        self.make_product()
        wh = self.make_warehouse()
        self.purchase("P-001", wh["id"], [{"sku": "SKU-001", "qty": 5}])
        self.sale("S-001", wh["id"], [{"sku": "SKU-001", "qty": 1}])

        status, sales = self.call("GET", "/api/docs?type=sale", token=self.admin)
        self.assertEqual(status, 200)
        self.assertEqual(len(sales), 1)
        self.assertEqual(sales[0]["no"], "S-001")


if __name__ == "__main__":
    unittest.main(verbosity=2)
