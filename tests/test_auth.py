"""认证与权限。"""

import unittest

from tests.base import ApiTestBase


class AuthTest(ApiTestBase):
    def test_health(self):
        status, body = self.call("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertIn("schema", body)

    def test_login_ok(self):
        status, body = self.call("POST", "/api/login", {"username": "clerk", "password": "clerk123"})
        self.assertEqual(status, 200)
        self.assertEqual(body["role"], "clerk")

    def test_login_wrong_password(self):
        status, body = self.call("POST", "/api/login", {"username": "admin", "password": "x"})
        self.assertEqual(status, 401)
        self.assertEqual(body["error"]["code"], "unauthorized")

    def test_no_token(self):
        status, _ = self.call("GET", "/api/products")
        self.assertEqual(status, 401)

    def test_clerk_cannot_create_product(self):
        status, body = self.call("POST", "/api/products", {"sku": "X", "name": "X"}, token=self.clerk)
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "forbidden")

    def test_clerk_can_purchase(self):
        self.make_product()
        wh = self.make_warehouse()
        status, _ = self.call("POST", "/api/purchase",
                              {"no": "P-1", "warehouse_id": wh["id"],
                               "items": [{"sku": "SKU-001", "qty": 1}]}, token=self.clerk)
        self.assertEqual(status, 201)


if __name__ == "__main__":
    unittest.main(verbosity=2)
