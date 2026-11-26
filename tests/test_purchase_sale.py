"""采购入库 / 销售出库 / 库存扣减与超卖。"""

import threading
import unittest

from tests.base import ApiTestBase


class PurchaseTest(ApiTestBase):
    def test_purchase_increases_stock(self):
        self.make_product()
        wh = self.make_warehouse()
        status, doc = self.purchase("P-001", wh["id"], [{"sku": "SKU-001", "qty": 10, "price": 5}])
        self.assertEqual(status, 201, doc)
        self.assertEqual(doc["total_amount"], 50)

        _, stock = self.call("GET", f"/api/stock?warehouse_id={wh['id']}", token=self.admin)
        self.assertEqual(stock[0]["qty"], 10)

    def test_duplicate_doc_no_returns_409(self):
        self.make_product()
        wh = self.make_warehouse()
        self.purchase("P-001", wh["id"], [{"sku": "SKU-001", "qty": 1}])
        status, body = self.purchase("P-001", wh["id"], [{"sku": "SKU-001", "qty": 1}])
        self.assertEqual(status, 409)
        self.assertIn("已存在", body["error"]["message"])

    def test_unknown_sku_returns_404(self):
        wh = self.make_warehouse()
        status, body = self.purchase("P-002", wh["id"], [{"sku": "NOPE", "qty": 1}])
        self.assertEqual(status, 404)

    def test_unknown_warehouse_returns_404(self):
        self.make_product()
        status, _ = self.purchase("P-003", 999, [{"sku": "SKU-001", "qty": 1}])
        self.assertEqual(status, 404)

    def test_empty_items_returns_400(self):
        wh = self.make_warehouse()
        status, _ = self.purchase("P-004", wh["id"], [])
        self.assertEqual(status, 400)

    def test_movement_records_before_after(self):
        self.make_product()
        wh = self.make_warehouse()
        self.purchase("P-005", wh["id"], [{"sku": "SKU-001", "qty": 7}])

        _, moves = self.call("GET", "/api/movements", token=self.admin)
        self.assertEqual(moves[0]["type"], "purchase")
        self.assertEqual(moves[0]["qty_delta"], 7)
        self.assertEqual(moves[0]["qty_before"], 0)
        self.assertEqual(moves[0]["qty_after"], 7)


class SaleTest(ApiTestBase):
    def test_sale_decreases_stock(self):
        self.make_product()
        wh = self.make_warehouse()
        self.purchase("P-001", wh["id"], [{"sku": "SKU-001", "qty": 10}])

        status, doc = self.sale("S-001", wh["id"], [{"sku": "SKU-001", "qty": 4, "price": 9}])
        self.assertEqual(status, 201, doc)
        self.assertEqual(doc["total_amount"], 36)

        _, stock = self.call("GET", f"/api/stock?warehouse_id={wh['id']}", token=self.admin)
        self.assertEqual(stock[0]["qty"], 6)

    def test_insufficient_stock_returns_409_and_rolls_back(self):
        self.make_product()
        wh = self.make_warehouse()
        self.purchase("P-001", wh["id"], [{"sku": "SKU-001", "qty": 3}])

        status, body = self.sale("S-001", wh["id"], [{"sku": "SKU-001", "qty": 5}])
        self.assertEqual(status, 409)
        self.assertIn("库存不足", body["error"]["message"])

        # 库存没变
        _, stock = self.call("GET", f"/api/stock?warehouse_id={wh['id']}", token=self.admin)
        self.assertEqual(stock[0]["qty"], 3)

        # 单据没有落库
        status, _ = self.call("GET", "/api/docs/S-001", token=self.admin)
        self.assertEqual(status, 404)

    def test_partial_failure_rolls_back_whole_doc(self):
        self.make_product(sku="A-1", name="够的")
        self.make_product(sku="B-1", name="不够的")
        wh = self.make_warehouse()
        self.purchase("P-001", wh["id"], [{"sku": "A-1", "qty": 10}, {"sku": "B-1", "qty": 1}])

        # 第一条够、第二条不够，整单必须回滚
        status, _ = self.sale("S-002", wh["id"],
                              [{"sku": "A-1", "qty": 5}, {"sku": "B-1", "qty": 5}])
        self.assertEqual(status, 409)

        _, stock = self.call("GET", f"/api/stock?warehouse_id={wh['id']}", token=self.admin)
        quantities = {row["sku"]: row["qty"] for row in stock}
        self.assertEqual(quantities["A-1"], 10)   # 没有被扣
        self.assertEqual(quantities["B-1"], 1)

    def test_concurrent_sale_does_not_oversell(self):
        """10 个并发请求各买 1 件，库存 5：必须只成功 5 个，最终库存 0。"""
        self.make_product()
        wh = self.make_warehouse()
        self.purchase("P-001", wh["id"], [{"sku": "SKU-001", "qty": 5}])

        results = []
        lock = threading.Lock()

        def buy(i):
            status, _ = self.sale(f"S-C{i}", wh["id"], [{"sku": "SKU-001", "qty": 1}])
            with lock:
                results.append(status)

        threads = [threading.Thread(target=buy, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(results.count(201), 5, f"实际结果分布: {results}")
        self.assertEqual(results.count(409), 5)

        _, stock = self.call("GET", f"/api/stock?warehouse_id={wh['id']}", token=self.admin)
        self.assertEqual(stock[0]["qty"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
