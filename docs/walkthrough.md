# invsys 讲解稿（面试用）

> 读法：先看「一条下单链路」，再按顺序读五个机制，最后背 20 个问答。
> 每个机制都标了**代码位置**和**一句话解释**，面试卡住时回想这句话。

---

## 0. 一句话介绍

> 一个用 Python 标准库写的进销存系统：商品、客户、销售订单（草稿→确认→发货→取消）、库存扣减、采购入库、盘点、报表，带 9 个页面的网页管理端，52 个测试。

**技术栈**：`http.server` + `sqlite3` + `unittest`，无第三方依赖，无构建。

---

## 1. 目录与分层

```
invsys/
  config.py     配置（端口、数据库、密钥、限流参数）
  db.py         迁移脚本 + 6 个仓储（只写 SQL）
  security.py   PBKDF2 密码哈希 + HMAC 签名 token
  ratelimit.py  按 IP 的滑动窗口
  service.py    业务规则（单据、状态机、扣库存、报表）
  api.py        路由表 + 鉴权 + 异常映射 + 请求日志 + 静态托管
  __main__.py   启动入口
web/            网页管理端（index.html / style.css / app.js）
tests/          52 个用例
```

**分层原则**：

| 层 | 只做 | 不做 |
|---|---|---|
| api | 解析 HTTP、鉴权、把异常映射成状态码 | 不写业务判断 |
| service | 校验、事务、业务规则 | 不碰 HTTP，不拼 SQL 字符串 |
| db | SQL、表结构、迁移 | 不做业务判断 |

---

## 2. 一条下单链路（必须会讲）

以 `POST /api/orders` 为例：

```
1. api.py 的 _handle()
   - 限流：limiter.allow(ip)，超限返回 429
   - 路由：ROUTES 里正则匹配到 ("POST", "^/api/orders$", "create_order", "user")
   - 鉴权：解析 Authorization: Bearer <token>，verify_token 校验签名和过期
   - 进入 _invoke("create_order")

2. service.py 的 create_order(payload)
   - 校验 no / customer_id / items
   - 打开一个数据库会话（session 上下文）
   - 订单号唯一性检查 → 冲突 409
   - 客户是否存在 → 404
   - 逐条校验商品、数量、单价，算出 total
   - 写 sales_order + sales_order_item
   - 返回订单详情（含明细、客户名）

3. 出异常时：
   - ValidationError → 400
   - NotFound → 404
   - Conflict → 409
   - 其它 → 500（不泄露堆栈）
```

**一句话**：「api 只做 HTTP 的事，业务全在 service，SQL 全在 db。」

---

## 3. 机制一：订单状态机

**代码**：`service.py` 的 `confirm_order` / `ship_order` / `cancel_order`

```
draft ──confirm──> confirmed ──ship──> shipped
  │                   │
  └────cancel─────────┴──> cancelled     （shipped 不可取消）
```

```python
if order["status"] != "draft":
    raise Conflict(f"订单当前状态为 {order['status']}，只有草稿可以确认")
```

**为什么要有状态机**：
- 防止「已发货的订单被取消」这种数据错乱；
- 状态跃迁集中在一处，出了问题只查三个方法；
- 每次变更记时间戳（`confirmed_at`、`shipped_at`），可追溯。

**面试追问：「为什么不用布尔字段 is_shipped？」**
> 「布尔只能表达两个状态，实际有四个（草稿/已确认/已发货/已取消），而且将来会有退货、部分发货。用枚举 + 校验比堆一堆布尔字段清楚。」

---

## 4. 机制二：原子扣库存（项目最硬的部分）

**代码**：`db.py` 的 `StockRepo.try_decrease`

```python
def try_decrease(self, product_id, warehouse_id, n) -> bool:
    cur = self.conn.execute(
        "UPDATE stock SET qty = qty - ? "
        "WHERE product_id=? AND warehouse_id=? AND qty >= ?",
        (n, product_id, warehouse_id, n))
    return cur.rowcount > 0
```

**为什么不能这么写**：

```python
qty = get_qty(...)          # 读到 5
if qty >= n:                # 判断够
    update(qty=5-n)         # 两个线程都通过了判断 → 超卖
```

**正确做法**：判断和扣减放在**同一条 UPDATE** 里，数据库保证原子性；`rowcount == 0` 说明库存不够。

**发货时**（`ship_order`）：任一条明细扣失败，抛 `Conflict`，整个 `with session(...)` 回滚——订单状态、库存、流水全部复原。

**证据**：`tests/test_purchase_sale.py::test_concurrent_sale_does_not_oversell`
10 个线程各买 1 件、库存 5 件 → 恰好 5 个 201、5 个 409、最终库存 0。

---

## 5. 机制三：数据库迁移

**代码**：`db.py` 的 `MIGRATIONS` + `migrate()`

```python
MIGRATIONS = [
    (1, "CREATE TABLE user ... device ..."),
    (2, "ALTER TABLE product ADD COLUMN category ..."),
    (3, "CREATE TABLE customer ... sales_order ..."),
]

def migrate(path):
    current = conn.execute("SELECT IFNULL(MAX(version),0) FROM schema_version").fetchone()[0]
    for version, sql in MIGRATIONS:
        if version <= current:   # 已执行过就跳过
            continue
        conn.executescript(sql)
        conn.execute("INSERT INTO schema_version(version) VALUES(?)", (version,))
```

**为什么**：
- 团队协作时，别人的库可能还是旧版本，直接改表会崩；
- 迁移可重复执行（幂等），部署脚本不用关心当前版本；
- 启动日志会打印 `schema: 0->3`，运维一眼知道升了没。

**面试追问：「为什么不用 ORM 的自动建表？」**
> 「自动建表只能处理新增，处理不了改字段、加索引、数据回填。真实项目一定要版本化迁移。」

---

## 6. 机制四：两套主体（人 vs 设备）

当前项目只有「人」登录：

```python
token, exp = issue_token(username, role, secret, ttl)   # base64(payload) + HMAC 签名
claims = verify_token(header[7:], secret)                # 校验签名 + 过期
require_admin(claims)                                    # 403 越权
```

**要点**：
- 签名用 `hmac.compare_digest` 防时序攻击；
- 密码用 PBKDF2（12 万轮）加盐，不存明文；
- 角色只有 admin / clerk，写操作（建商品、盘点）限管理员。

**面试追问：「token 放 localStorage 有 XSS 风险吗？」**
> 「有。生产更稳的是 HttpOnly Cookie + CSRF token；这里是无构建的内网工具，做了取舍，已在 README 的已知限制里写明。」

---

## 7. 机制五：限流与请求日志

```python
# ratelimit.py：滑动窗口
stamps = [t for t in self._hits.get(key, []) if now - t < self.window]
if len(stamps) >= self.limit:
    return False, max(1, int(self.window - (now - stamps[0])))
```

```python
# api.py：每个请求一行 JSON
{"method": "POST", "path": "/api/orders", "status": 201, "ms": 3.2, "ip": "127.0.0.1", "user": "admin"}
```

**为什么**：出问题时能查「谁在什么时候调了什么、花了多久」；限流防止脚本把库打崩。

---

## 8. 前端（三个文件，无构建）

```
web/index.html   结构：登录页 + 侧边导航 + 9 个 section
web/style.css    样式：卡片、表格、状态标签
web/app.js       逻辑：登录、token、tab 切换、表单、错误提示
```

关键点：

- `api()` 统一包装 fetch：自动带 token、统一解析 `{error:{message}}`、失败 throw；
- `toast()` 统一提示，后端中文错误直接显示给用户；
- `switchTab()` 控制 section 显示，支持 `?tab=orders` 直达；
- 静态资源由 `api.py` 的 `_serve_static` 托管，做了目录穿越防护。

**面试追问：「为什么不用 Vue？」**
> 「这是内网工具，要求单进程、无依赖、拷过去就能跑。Vue 要 node 和构建产物，反而不合适。我另一个项目（销售系统）是用 Vue 的，两边都做过。」

---

## 9. 测试策略

```bash
python -m unittest discover -s tests -t .
# Ran 52 tests in 1.7s
```

| 文件 | 数量 | 重点 |
|---|---|---|
| test_auth.py | 6 | 登录、错误密码、无 token、越权 403 |
| test_catalog.py | 7 | 商品/仓库 CRUD、重复编码、分页、搜索 |
| test_purchase_sale.py | 10 | 入库出库、重复单号、**库存不足回滚**、**并发不超卖** |
| test_orders.py | 15 | **状态机**、发货扣库存、库存不足状态不变 |
| test_adjust_report.py | 9 | 盘点差异流水、低库存预警、汇总 |
| test_static.py | 5 | 首页/JS/CSS、404、目录穿越拦截 |

**为什么真实起服务而不是 mock**：
> 「mock 只能验证调用关系，验证不了状态码、事务回滚、并发。我的测试是真的开端口、真的发 HTTP。」

---

## 10. 20 个面试问答（背答案要点）

1. **为什么不用框架？** 岗位要求标准库；且我想弄清框架替我做了什么。生产我会用 FastAPI。
2. **怎么防超卖？** 一条 `UPDATE ... WHERE qty >= ?`，判断和扣减原子完成，`rowcount=0` 就是不够。
3. **事务怎么保证？** `session()` 上下文：正常 commit，异常 rollback，finally 关连接。
4. **订单状态怎么管？** 四个状态集中校验，非法跃迁返回 409，变更记时间戳。
5. **发货失败会怎样？** 整单回滚：库存不动、状态仍是 confirmed、流水不写。
6. **重复提交怎么防？** 单据号/订单号唯一约束，重复返回 409。
7. **为什么写库存流水？** 对账：期初 + 入库 - 出库 = 期末，记录 before/after。
8. **数据库怎么演进？** `schema_version` + 迁移列表，只执行未应用的。
9. **密码怎么存？** PBKDF2 + 每用户盐，不存明文。
10. **token 怎么做的？** base64(payload) + HMAC-SHA256 签名，校验签名和过期，等价于简化 JWT。
11. **权限怎么控制？** 角色 admin/clerk，写接口在路由表里标 `admin`，越权 403。
12. **限流怎么实现？** 进程内滑动窗口按 IP 计数，超限 429 + `Retry-After`。
13. **日志怎么打？** 每请求一行 JSON，含方法、路径、状态码、耗时、用户。
14. **为什么用 SQLite？** 单机工具型系统合适；换 PostgreSQL 只改连接和自增语法。
15. **并发写会怎样？** SQLite 单写者会排队，是已知限制，已写进 README。
16. **前端为什么不用框架？** 内网无依赖部署，原生 JS 三个文件即可。
17. **静态资源怎么托管？** 自己实现，带 MIME 表和目录穿越防护。
18. **测试为什么不用 mock？** 要验证状态码、事务回滚、并发行为。
19. **项目里最难的一块？** 并发扣库存 + 整单回滚，有专门的并发测试证明。
20. **如果重做会改什么？** 上 PostgreSQL、加 Alembic 迁移、token 加刷新、限流换 Redis。

---

## 11. 一天读代码路线

| 时间 | 做什么 | 目标 |
|---|---|---|
| 30 min | 跑 `python -m invsys --port 8000`，浏览器走一遍：建客户 → 建商品 → 采购入库 → 建订单 → 确认 → 发货 | 知道系统长什么样 |
| 40 min | 读 `api.py`：ROUTES 表、`_handle`、`_invoke` | 知道请求怎么进来、怎么出去 |
| 60 min | 读 `service.py`：`create_order` / `confirm_order` / `ship_order` | 能口述状态机和扣库存 |
| 40 min | 读 `db.py`：`try_decrease`、`migrate`、`MIGRATIONS` | 能讲原子扣减和迁移 |
| 30 min | 读 `tests/test_purchase_sale.py` 的并发用例 | 能讲测试怎么证明不超卖 |
| 30 min | 读 `web/app.js` 的 `api()` 和 `switchTab()` | 能讲前端怎么带 token |
| 30 min | 对着第 10 节自测 20 问 | 卡住的回去看代码 |

**验收标准**：随便抽 5 个问题，不看稿能各讲 30 秒。
