# 接口文档（invsys）

Base URL：`http://127.0.0.1:8000`

认证：除 `/api/health`、`/api/login` 外，都要在请求头带

```
Authorization: Bearer <token>
```

## 错误格式

```json
{"error": {"code": "conflict", "message": "库存不足：SKU-001 当前 3，需要 5"}}
```

| code | HTTP | 含义 |
|---|---|---|
| validation_error | 400 | 参数缺失或格式错误 |
| unauthorized | 401 | 未登录 / token 无效 / 过期 |
| forbidden | 403 | 角色不足（如业务员建商品） |
| not_found | 404 | 商品 / 仓库 / 单据不存在 |
| conflict | 409 | 单号重复、SKU 重复、库存不足 |
| rate_limited | 429 | 请求过于频繁（含 `Retry-After`） |
| internal_error | 500 | 服务端异常 |

## 登录

```
POST /api/login
{"username": "admin", "password": "admin123"}
→ 200 {"token": "...", "role": "admin", "expires_at": 1795662499}
```

## 商品

```
GET  /api/products?page=1&size=20&q=水
→ 200 {"total": 1, "page": 1, "size": 20, "items": [...]}

POST /api/products
{"sku": "SKU-001", "name": "矿泉水", "unit": "箱", "safe_stock": 10, "category": "饮料"}
→ 201 {"id": 1, "sku": "SKU-001", ...}
→ 409 编码重复
```

## 仓库

```
GET  /api/warehouses        → 200 [...]
POST /api/warehouses        {"code": "WH-01", "name": "主仓"} → 201
```

## 库存

```
GET /api/stock?warehouse_id=1&q=水
→ 200 [{"product_id":1, "sku":"SKU-001", "qty":70, "safe_stock":10, "below_safe":false}]
```

## 采购入库

```
POST /api/purchase
{"no": "P-20261126-001", "warehouse_id": 1, "items": [{"sku": "SKU-001", "qty": 100, "price": 5}]}
→ 201 {"no": "...", "type": "purchase", "total_amount": 500, "items": [...]}
→ 409 单号重复
→ 404 商品或仓库不存在
```

## 销售出库

```
POST /api/sale
{"no": "S-20261126-001", "warehouse_id": 1, "items": [{"sku": "SKU-001", "qty": 30, "price": 9}]}
→ 201
→ 409 {"error": {"code": "conflict", "message": "库存不足：SKU-001 当前 3，需要 30"}}
```

库存扣减是**单条条件 UPDATE**，并发下不会超卖；任一明细不足则整单回滚（单据不落库、库存不变）。

## 盘点

```
POST /api/adjust
{"no": "A-001", "warehouse_id": 1, "items": [{"sku": "SKU-001", "qty": 68}]}
→ 201   库存直接改为实盘数，流水记录差值
```

## 单据与流水

```
GET /api/docs?type=sale&limit=20   → 200 [...]
GET /api/docs/S-001                → 200 {"no": "...", "items": [...]}
GET /api/movements?product_id=1    → 200 [{"type":"sale","qty_delta":-30,"qty_before":100,"qty_after":70,...}]
```

## 报表

```
GET /api/report/low-stock  → 200 [{"sku":"SKU-001","safe_stock":10,"total_qty":2}]
GET /api/report/summary    → 200 {"products":1,"warehouses":1,"stock_total":70,"docs":2,"sales_amount":270}
```

## 健康检查

```
GET /api/health → 200 {"status":"ok","version":"invsys/1.0","schema":"0->2"}
```
