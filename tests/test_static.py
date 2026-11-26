"""静态前端托管测试。"""

import unittest
import urllib.error
import urllib.request

from tests.base import ApiTestBase


class StaticTest(ApiTestBase):
    def fetch(self, path):
        try:
            with urllib.request.urlopen(self.base + path) as resp:
                return resp.status, resp.headers.get("Content-Type", ""), resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type", ""), e.read().decode("utf-8", "replace")

    def test_index_page(self):
        status, ctype, body = self.fetch("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn("进销存", body)
        self.assertIn("/static/app.js", body)

    def test_app_js(self):
        status, ctype, body = self.fetch("/static/app.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", ctype)
        self.assertIn("/api/login", body)

    def test_style_css(self):
        status, ctype, _ = self.fetch("/static/style.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", ctype)

    def test_unknown_static_returns_404(self):
        status, _, _ = self.fetch("/static/nope.js")
        self.assertEqual(status, 404)

    def test_path_traversal_blocked(self):
        status, _, _ = self.fetch("/static/../invsys/api.py")
        self.assertIn(status, (400, 404))


if __name__ == "__main__":
    unittest.main(verbosity=2)
