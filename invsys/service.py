"""业务层：登录、商品、仓库、库存、采购入库、销售出库、盘点、报表。

真正的业务复杂度集中在这里：
- 销售出库用一条带条件的 UPDATE 原子扣减，并发下不会超卖；
- 单据号唯一，重复提交直接 409（幂等保护）；
- 每次库存变动都写流水，记录变动前后数量，可对账。
"""

from . import security
from .db import (CustomerRepo, DashboardRepo, DocRepo, MovementRepo, OrderRepo,
                 ProductRepo, ReportRepo, StockRepo, UserRepo, WarehouseRepo,
                 migrate, session)


class ApiError(Exception):
    def __init__(self, code, message, status):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class ValidationError(ApiError):
    def __init__(self, message):
        super().__init__("validation_error", message, 400)


class Unauthorized(ApiError):
    def __init__(self, message="未认证"):
        super().__init__("unauthorized", message, 401)


class NotFound(ApiError):
    def __init__(self, message="资源不存在"):
        super().__init__("not_found", message, 404)


class Conflict(ApiError):
    def __init__(self, message="资源冲突"):
        super().__init__("conflict", message, 409)


class InventoryService:
    def __init__(self, config):
        self.config = config
        self.schema_from, self.schema_to = migrate(config.db_path)
        self._bootstrap()

    def _bootstrap(self):
        with session(self.config.db_path) as conn:
            repo = UserRepo(conn)
            if repo.count() == 0:
                salt = security.make_salt("admin")
                repo.create("admin", security.hash_password("admin123", salt), salt, "admin")
                salt2 = security.make_salt("clerk")
                repo.create("clerk", security.hash_password("clerk123", salt2), salt2, "clerk")

    # ---------- 认证 ----------
    def login(self, payload):
        username = (payload.get("username") or "").strip()
        password = payload.get("password") or ""
        if not username or not password:
            raise ValidationError("username 与 password 不能为空")

        with session(self.config.db_path) as conn:
            user = UserRepo(conn).get(username)

        if user is None or security.hash_password(password, user["salt"]) != user["password_hash"]:
            raise Unauthorized("用户名或密码错误")

        token, exp = security.issue_token(user["username"], user["role"],
                                          self.config.secret, self.config.token_ttl)
        return {"token": token, "role": user["role"], "expires_at": exp}

    # ---------- 商品 ----------
    def list_products(self, keyword=None, page=1, size=20):
        page = max(1, int(page or 1))
        size = min(100, max(1, int(size or 20)))
        with session(self.config.db_path) as conn:
            total, data = ProductRepo(conn).list(keyword, (page - 1) * size, size)
        return {"total": total, "page": page, "size": size, "items": data}

    def create_product(self, payload):
        sku = (payload.get("sku") or "").strip()
        name = (payload.get("name") or "").strip()
        unit = (payload.get("unit") or "件").strip()
        category = (payload.get("category") or "").strip()
        try:
            safe_stock = int(payload.get("safe_stock") or 0)
        except (TypeError, ValueError):
            raise ValidationError("safe_stock 必须是整数")
        if not sku or not name:
            raise ValidationError("sku 与 name 不能为空")
        if safe_stock < 0:
            raise ValidationError("safe_stock 不能为负")

        with session(self.config.db_path) as conn:
            repo = ProductRepo(conn)
            if repo.by_sku(sku):
                raise Conflict(f"商品编码 {sku} 已存在")
            pid = repo.create(sku, name, unit, safe_stock, category)
            return repo.get(pid)

    # ---------- 仓库 ----------
    def list_warehouses(self):
        with session(self.config.db_path) as conn:
            return WarehouseRepo(conn).list()

    def create_warehouse(self, payload):
        code = (payload.get("code") or "").strip()
        name = (payload.get("name") or "").strip()
        if not code or not name:
            raise ValidationError("code 与 name 不能为空")
        with session(self.config.db_path) as conn:
            repo = WarehouseRepo(conn)
            if any(w["code"] == code for w in repo.list()):
                raise Conflict(f"仓库编码 {code} 已存在")
            wid = repo.create(code, name)
            return repo.get(wid)

    # ---------- 库存 ----------
    def list_stock(self, warehouse_id=None, keyword=None):
        with session(self.config.db_path) as conn:
            data = StockRepo(conn).list(warehouse_id, keyword)
        for row in data:
            row["below_safe"] = row["qty"] < row["safe_stock"]
        return data

    # ---------- 单据 ----------
    def purchase(self, payload):
        return self._create_doc("purchase", payload)

    def sale(self, payload):
        return self._create_doc("sale", payload)

    def _create_doc(self, doc_type, payload):
        no = (payload.get("no") or "").strip()
        remark = (payload.get("remark") or "").strip()
        items = payload.get("items") or []
        try:
            warehouse_id = int(payload.get("warehouse_id"))
        except (TypeError, ValueError):
            raise ValidationError("warehouse_id 必须是整数")

        if not no:
            raise ValidationError("单据号 no 不能为空")
        if not items:
            raise ValidationError("items 不能为空")

        with session(self.config.db_path) as conn:
            if DocRepo(conn).get_by_no(no):
                raise Conflict(f"单据号 {no} 已存在")
            if WarehouseRepo(conn).get(warehouse_id) is None:
                raise NotFound(f"仓库 {warehouse_id} 不存在")

            products = ProductRepo(conn)
            stocks = StockRepo(conn)
            docs = DocRepo(conn)
            moves = MovementRepo(conn)

            # 先校验全部明细，避免部分成功
            parsed = []
            total = 0.0
            for raw in items:
                sku = (raw.get("sku") or "").strip()
                try:
                    qty = int(raw.get("qty"))
                    price = float(raw.get("price") or 0)
                except (TypeError, ValueError):
                    raise ValidationError("qty 必须是整数、price 必须是数字")
                if qty <= 0:
                    raise ValidationError("qty 必须大于 0")
                if price < 0:
                    raise ValidationError("price 不能为负")

                product = products.by_sku(sku)
                if product is None:
                    raise NotFound(f"商品 {sku} 不存在")
                parsed.append((product, qty, price))
                total += qty * price

            doc_id = docs.create(no, doc_type, warehouse_id, remark, round(total, 2))

            for product, qty, price in parsed:
                before = stocks.qty(product["id"], warehouse_id)

                if doc_type == "sale":
                    if not stocks.try_decrease(product["id"], warehouse_id, qty):
                        raise Conflict(
                            f"库存不足：{product['sku']} 当前 {before}，需要 {qty}")
                    before_after = (before, before - qty)
                else:
                    stocks.increase(product["id"], warehouse_id, qty)
                    before_after = (before, before + qty)

                docs.add_item(doc_id, product["id"], qty, price)
                moves.add(product["id"], warehouse_id, doc_id, doc_type, qty,
                          before_after[0], before_after[1])

            return self._doc_detail(conn, no)

    def _doc_detail(self, conn, no):
        doc = DocRepo(conn).get_by_no(no)
        if doc is None:
            raise NotFound(f"单据 {no} 不存在")
        doc["items"] = DocRepo(conn).items(doc["id"])
        return doc

    def get_doc(self, no):
        with session(self.config.db_path) as conn:
            return self._doc_detail(conn, no)

    def list_docs(self, doc_type=None, limit=20):
        with session(self.config.db_path) as conn:
            return DocRepo(conn).list(doc_type, min(100, max(1, int(limit or 20))))

    # ---------- 盘点 ----------
    def adjust(self, payload):
        """盘点：把库存直接改成实盘数，并记录差异流水。"""
        no = (payload.get("no") or "").strip()
        items = payload.get("items") or []
        try:
            warehouse_id = int(payload.get("warehouse_id"))
        except (TypeError, ValueError):
            raise ValidationError("warehouse_id 必须是整数")

        if not no or not items:
            raise ValidationError("no 与 items 不能为空")

        with session(self.config.db_path) as conn:
            if DocRepo(conn).get_by_no(no):
                raise Conflict(f"单据号 {no} 已存在")
            if WarehouseRepo(conn).get(warehouse_id) is None:
                raise NotFound(f"仓库 {warehouse_id} 不存在")

            products, stocks = ProductRepo(conn), StockRepo(conn)
            docs, moves = DocRepo(conn), MovementRepo(conn)

            parsed = []
            for raw in items:
                sku = (raw.get("sku") or "").strip()
                try:
                    actual = int(raw.get("qty"))
                except (TypeError, ValueError):
                    raise ValidationError("qty 必须是整数")
                if actual < 0:
                    raise ValidationError("实盘数量不能为负")
                product = products.by_sku(sku)
                if product is None:
                    raise NotFound(f"商品 {sku} 不存在")
                parsed.append((product, actual))

            doc_id = docs.create(no, "adjust", warehouse_id, "盘点调整", 0)
            for product, actual in parsed:
                before = stocks.qty(product["id"], warehouse_id)
                delta = actual - before
                stocks.set_qty(product["id"], warehouse_id, actual)
                docs.add_item(doc_id, product["id"], max(1, abs(delta) or 1), 0)
                moves.add(product["id"], warehouse_id, doc_id, "adjust", delta, before, actual)

            return self._doc_detail(conn, no)

    # ---------- 流水与报表 ----------
    def list_movements(self, product_id=None, limit=50):
        with session(self.config.db_path) as conn:
            return MovementRepo(conn).list(product_id, min(200, max(1, int(limit or 50))))

    def low_stock(self):
        with session(self.config.db_path) as conn:
            return ReportRepo(conn).low_stock()

    def summary(self):
        with session(self.config.db_path) as conn:
            return ReportRepo(conn).summary()


    # ---------- 客户 ----------
    def list_customers(self, keyword=None, limit=50):
        with session(self.config.db_path) as conn:
            return CustomerRepo(conn).list(keyword, min(200, max(1, int(limit or 50))))

    def create_customer(self, payload):
        name = (payload.get("name") or "").strip()
        phone = (payload.get("phone") or "").strip()
        address = (payload.get("address") or "").strip()
        if not name:
            raise ValidationError("客户名称不能为空")
        with session(self.config.db_path) as conn:
            return CustomerRepo(conn).create(name, phone, address)

    # ---------- 销售订单（状态机：draft → confirmed → shipped / cancelled） ----------
    def create_order(self, payload):
        no = (payload.get("no") or "").strip()
        items = payload.get("items") or []
        try:
            customer_id = int(payload.get("customer_id"))
        except (TypeError, ValueError):
            raise ValidationError("customer_id 必须是整数")
        if not no:
            raise ValidationError("订单号 no 不能为空")
        if not items:
            raise ValidationError("items 不能为空")

        with session(self.config.db_path) as conn:
            if OrderRepo(conn).get_by_no(no):
                raise Conflict(f"订单号 {no} 已存在")
            if CustomerRepo(conn).get(customer_id) is None:
                raise NotFound(f"客户 {customer_id} 不存在")

            products = ProductRepo(conn)
            orders = OrderRepo(conn)

            parsed, total = [], 0.0
            for raw in items:
                sku = (raw.get("sku") or "").strip()
                try:
                    qty = int(raw.get("qty"))
                    price = float(raw.get("price") or 0)
                except (TypeError, ValueError):
                    raise ValidationError("qty 必须是整数、price 必须是数字")
                if qty <= 0:
                    raise ValidationError("qty 必须大于 0")
                product = products.by_sku(sku)
                if product is None:
                    raise NotFound(f"商品 {sku} 不存在")
                parsed.append((product, qty, price))
                total += qty * price

            order_id = orders.create(no, customer_id, round(total, 2))
            for product, qty, price in parsed:
                orders.add_item(order_id, product["id"], qty, price)

            return self._order_detail(conn, no)

    def confirm_order(self, no):
        with session(self.config.db_path) as conn:
            orders = OrderRepo(conn)
            order = orders.get_by_no(no)
            if order is None:
                raise NotFound(f"订单 {no} 不存在")
            if order["status"] != "draft":
                raise Conflict(f"订单当前状态为 {order['status']}，只有草稿可以确认")
            orders.set_status(order["id"], "confirmed", "confirmed_at")
            return self._order_detail(conn, no)

    def ship_order(self, no):
        """发货：确认态才能发；发货时原子扣库存，任一不足整单回滚。"""
        with session(self.config.db_path) as conn:
            orders = OrderRepo(conn)
            order = orders.get_by_no(no)
            if order is None:
                raise NotFound(f"订单 {no} 不存在")
            if order["status"] != "confirmed":
                raise Conflict(f"订单当前状态为 {order['status']}，只有已确认可以发货")

            warehouse = WarehouseRepo(conn).list()
            if not warehouse:
                raise ValidationError("还没有仓库，无法发货")
            warehouse_id = warehouse[0]["id"]   # 简化：默认发第一个仓库

            stocks, moves = StockRepo(conn), MovementRepo(conn)
            for item in orders.items(order["id"]):
                before = stocks.qty(item["product_id"], warehouse_id)
                if not stocks.try_decrease(item["product_id"], warehouse_id, item["qty"]):
                    raise Conflict(
                        f"库存不足：{item['sku']} 当前 {before}，需要 {item['qty']}")
                moves.add(item["product_id"], warehouse_id, None, "order_ship",
                          -item["qty"], before, before - item["qty"])

            orders.set_status(order["id"], "shipped", "shipped_at")
            return self._order_detail(conn, no)

    def cancel_order(self, no):
        with session(self.config.db_path) as conn:
            orders = OrderRepo(conn)
            order = orders.get_by_no(no)
            if order is None:
                raise NotFound(f"订单 {no} 不存在")
            if order["status"] == "cancelled":
                raise Conflict("订单已经是取消状态")
            if order["status"] == "shipped":
                raise Conflict("已发货的订单不能取消，只能做退货单")
            orders.set_status(order["id"], "cancelled")
            return self._order_detail(conn, no)

    def _order_detail(self, conn, no):
        orders = OrderRepo(conn)
        order = orders.get_by_no(no)
        if order is None:
            raise NotFound(f"订单 {no} 不存在")
        order["items"] = orders.items(order["id"])
        customer = CustomerRepo(conn).get(order["customer_id"])
        order["customer_name"] = customer["name"] if customer else ""
        return order

    def get_order(self, no):
        with session(self.config.db_path) as conn:
            return self._order_detail(conn, no)

    def list_orders(self, status=None, limit=50):
        with session(self.config.db_path) as conn:
            return OrderRepo(conn).list(status, min(200, max(1, int(limit or 50))))

    # ---------- 仪表盘 ----------
    def dashboard(self):
        with session(self.config.db_path) as conn:
            dash = DashboardRepo(conn)
            summary = ReportRepo(conn).summary()
            return {
                "today_orders": dash.today_orders(),
                "month_amount": dash.month_amount(),
                "low_stock_count": dash.low_stock_count(),
                "top_products": dash.top_products(5),
                "products": summary["products"],
                "customers": CustomerRepo(conn).count(),
                "orders": OrderRepo(conn).count(),
                "pending_orders": OrderRepo(conn).count("draft") + OrderRepo(conn).count("confirmed"),
            }
