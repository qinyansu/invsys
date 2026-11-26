"""商品与仓库。"""

import unittest

from tests.base import ApiTestBase


class CatalogTest(ApiTestBase):
    def test_create_product(self):
        p = self.make_product()
        self.assertEqual(p["sku"], "SKU-001")
        self.assertEqual(p["unit"], "箱")

    def test_duplicate_sku_returns_409(self):
        self.make_product()
        status, body = self.call("POST", "/api/products",
                                 {"sku": "SKU-001", "name": "重复"}, token=self.admin)
        self.assertEqual(status, 409)

    def test_missing_fields_returns_400(self):
        status, _ = self.call("POST", "/api/products", {"sku": "A"}, token=self.admin)
        self.assertEqual(status, 400)

    def test_pagination(self):
        for i in range(3):
            self.make_product(sku=f"S-{i}", name=f"商品{i}")
        status, page = self.call("GET", "/api/products?page=1&size=2", token=self.admin)
        self.assertEqual(status, 200)
        self.assertEqual(page["total"], 3)
        self.assertEqual(len(page["items"]), 2)

    def test_keyword_filter(self):
        self.make_product(sku="A-1", name="矿泉水")
        self.make_product(sku="B-1", name="方便面")
        _, page = self.call("GET", "/api/products?q=" + self.q("水"), token=self.admin)
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["items"][0]["sku"], "A-1")

    def test_create_warehouse_and_duplicate(self):
        self.make_warehouse()
        status, _ = self.call("POST", "/api/warehouses", {"code": "WH-01", "name": "另一个"}, token=self.admin)
        self.assertEqual(status, 409)

    def test_warehouse_list(self):
        self.make_warehouse()
        status, whs = self.call("GET", "/api/warehouses", token=self.admin)
        self.assertEqual(status, 200)
        self.assertEqual(len(whs), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
