/* 进销存前端：原生 JS，无框架、无构建。 */

const state = { token: localStorage.getItem("invsys_token") || "", role: "", tab: "products" };

const $ = (id) => document.getElementById(id);
const money = (n) => "¥" + Number(n || 0).toFixed(2);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function toast(msg, isError = false) {
  const el = $("toast");
  el.textContent = msg;
  el.className = "toast" + (isError ? " error" : "");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.add("hidden"), 2600);
}

async function api(method, path, body) {
  const headers = { "Content-Type": "application/json" };
  if (state.token) headers.Authorization = "Bearer " + state.token;
  const resp = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  const text = await resp.text();
  const data = text ? JSON.parse(text) : null;
  if (!resp.ok) {
    const err = new Error((data && data.error && data.error.message) || `请求失败 ${resp.status}`);
    err.status = resp.status;
    throw err;
  }
  return data;
}

/* ---------- 登录 ---------- */
$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const data = await api("POST", "/api/login", {
      username: $("username").value.trim(),
      password: $("password").value,
    });
    state.token = data.token;
    state.role = data.role;
    localStorage.setItem("invsys_token", data.token);
    enterApp();
  } catch (err) {
    toast(err.message, true);
  }
});

$("logout").addEventListener("click", () => {
  state.token = "";
  localStorage.removeItem("invsys_token");
  $("app-view").classList.add("hidden");
  $("login-view").classList.remove("hidden");
});

function decodeRole(token) {
  try {
    const body = token.split(".")[0].replace(/-/g, "+").replace(/_/g, "/");
    return JSON.parse(atob(body)).r || "";
  } catch { return ""; }
}

async function enterApp() {
  $("login-view").classList.add("hidden");
  $("app-view").classList.remove("hidden");
  const role = state.role || decodeRole(state.token);
  $("whoami").textContent = role === "admin" ? "管理员" : "业务员";
  await loadWarehouses();
  const wanted = new URLSearchParams(location.search).get("tab");
  switchTab(wanted || "dashboard");
}

/* ---------- 导航 ---------- */
document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

function switchTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll(".tab").forEach((s) => s.classList.add("hidden"));
  $("tab-" + tab).classList.remove("hidden");
  const titles = { dashboard: "仪表盘", products: "商品", customers: "客户", orders: "销售订单",
                   stock: "库存", purchase: "采购入库", sale: "销售出库", movements: "库存流水", report: "报表" };
  $("page-title").textContent = titles[tab];
  $("stat").textContent = "";

  if (tab === "dashboard") loadDashboard();
  if (tab === "customers") loadCustomers();
  if (tab === "orders") loadOrders();
  if (tab === "products") loadProducts();
  if (tab === "stock") loadStock();
  if (tab === "purchase") { loadDocs("purchase"); fillNo("purchase"); }
  if (tab === "sale") { loadDocs("sale"); fillNo("sale"); }
  if (tab === "movements") loadMovements();
  if (tab === "report") loadReport();
}

/* ---------- 仓库 ---------- */
async function loadWarehouses() {
  const list = await api("GET", "/api/warehouses");
  const options = list.map((w) => `<option value="${w.id}">${esc(w.name)}</option>`).join("");
  ["stock-warehouse"].forEach((id) => {
    $(id).innerHTML = `<option value="">全部仓库</option>` + options;
  });
  ["purchase-warehouse", "sale-warehouse"].forEach((id) => {
    $(id).innerHTML = options || `<option value="">（先建仓库）</option>`;
  });
}

/* ---------- 商品 ---------- */
async function loadProducts(keyword = "") {
  const q = keyword ? "?q=" + encodeURIComponent(keyword) : "";
  const data = await api("GET", "/api/products" + q);
  $("stat").textContent = `共 ${data.total} 个商品`;
  $("product-body").innerHTML = data.items.map((p) => `
    <tr><td>${p.id}</td><td>${esc(p.sku)}</td><td>${esc(p.name)}</td>
    <td>${esc(p.unit)}</td><td>${p.safe_stock}</td><td>${esc(p.category || "-")}</td></tr>`).join("")
    || `<tr><td colspan="6" style="color:#6b7280">还没有商品，点右上角「新建商品」</td></tr>`;
}

$("product-search-btn").addEventListener("click", () => loadProducts($("product-search").value.trim()));
$("product-new").addEventListener("click", async () => {
  const sku = prompt("SKU（唯一，例如 SKU-001）");
  if (!sku) return;
  const name = prompt("商品名称");
  if (!name) return;
  const safe = prompt("安全库存（低于此值会预警）", "10") || "0";
  try {
    await api("POST", "/api/products", { sku, name, unit: "件", safe_stock: Number(safe), category: "" });
    toast("商品已创建");
    loadProducts();
  } catch (err) { toast(err.message, true); }
});

/* ---------- 库存 ---------- */
async function loadStock() {
  const wid = $("stock-warehouse").value;
  const kw = $("stock-search").value.trim();
  const params = new URLSearchParams();
  if (wid) params.set("warehouse_id", wid);
  if (kw) params.set("q", kw);
  const list = await api("GET", "/api/stock?" + params.toString());
  $("stat").textContent = `${list.length} 条库存记录`;
  $("stock-body").innerHTML = list.map((s) => `
    <tr><td>${esc(s.sku)}</td><td>${esc(s.name)}</td><td>${s.warehouse_id}</td>
    <td>${s.qty}</td><td>${s.safe_stock}</td>
    <td>${s.below_safe ? '<span class="tag low">低于安全库存</span>' : '<span class="tag ok">正常</span>'}</td></tr>`).join("")
    || `<tr><td colspan="6" style="color:#6b7280">暂无库存，先去采购入库</td></tr>`;
}

$("stock-search-btn").addEventListener("click", loadStock);
$("stock-warehouse").addEventListener("change", loadStock);

/* ---------- 单据 ---------- */
function fillNo(kind) {
  const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, "");
  const rand = String(Math.floor(Math.random() * 900) + 100);
  $(kind + "-no").value = (kind === "purchase" ? "P-" : "S-") + stamp + "-" + rand;
}

async function loadDocs(kind) {
  const list = await api("GET", "/api/docs?type=" + kind);
  $(kind + "-body").innerHTML = list.map((d) => `
    <tr><td>${esc(d.no)}</td><td><span class="tag ${d.type}">${d.type}</span></td>
    <td>${money(d.total_amount)}</td><td>${esc(d.created)}</td></tr>`).join("")
    || `<tr><td colspan="4" style="color:#6b7280">还没有单据</td></tr>`;
}

async function submitDoc(kind) {
  const wid = Number($(kind + "-warehouse").value);
  if (!wid) return toast("请先在商品页建仓库（或直接调接口）", true);
  const payload = {
    no: $(kind + "-no").value.trim(),
    warehouse_id: wid,
    items: [{
      sku: $(kind + "-sku").value.trim(),
      qty: Number($(kind + "-qty").value),
      price: Number($(kind + "-price").value),
    }],
  };
  try {
    const doc = await api("POST", "/api/" + kind, payload);
    toast(`${kind === "purchase" ? "入库" : "出库"}成功，金额 ${money(doc.total_amount)}`);
    loadDocs(kind);
    fillNo(kind);
  } catch (err) {
    toast(err.message, true);
  }
}

$("purchase-submit").addEventListener("click", () => submitDoc("purchase"));
$("sale-submit").addEventListener("click", () => submitDoc("sale"));

/* ---------- 流水 ---------- */
async function loadMovements() {
  const list = await api("GET", "/api/movements?limit=50");
  $("stat").textContent = `最近 ${list.length} 条变动`;
  $("movement-body").innerHTML = list.map((m) => `
    <tr><td>${esc(m.created)}</td><td>${esc(m.sku)}</td>
    <td><span class="tag ${m.type}">${m.type}</span></td>
    <td style="color:${m.qty_delta >= 0 ? "#067647" : "#b42318"}">${m.qty_delta > 0 ? "+" : ""}${m.qty_delta}</td>
    <td>${m.qty_before}</td><td>${m.qty_after}</td></tr>`).join("")
    || `<tr><td colspan="6" style="color:#6b7280">暂无流水</td></tr>`;
}
$("movement-refresh").addEventListener("click", loadMovements);

/* ---------- 报表 ---------- */
async function loadReport() {
  const [sum, low] = await Promise.all([api("GET", "/api/report/summary"), api("GET", "/api/report/low-stock")]);
  $("summary-cards").innerHTML = [
    ["商品数", sum.products], ["仓库数", sum.warehouses], ["库存总量", sum.stock_total],
    ["单据数", sum.docs], ["销售总额", money(sum.sales_amount)],
  ].map(([k, v]) => `<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");

  $("low-stock-body").innerHTML = low.map((r) => `
    <tr><td>${esc(r.sku)}</td><td>${esc(r.name)}</td><td>${r.safe_stock}</td><td>${r.total_qty}</td></tr>`).join("")
    || `<tr><td colspan="4" style="color:#6b7280">没有低于安全库存的商品</td></tr>`;
  $("stat").textContent = "";
}

/* ---------- 启动 ---------- */
(async function boot() {
  if (!state.token) return;
  try {
    await enterApp();
  } catch {
    state.token = "";
    localStorage.removeItem("invsys_token");
  }
})();


/* ---------- 仪表盘 ---------- */
async function loadDashboard() {
  const d = await api("GET", "/api/dashboard");
  const cards = [
    ["今日订单", d.today_orders], ["本月销售额", money(d.month_amount)],
    ["待处理订单", d.pending_orders], ["客户数", d.customers],
    ["低库存商品", d.low_stock_count],
  ];
  $("dashboard-cards").innerHTML = cards
    .map(([k, v]) => `<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");
  $("top-body").innerHTML = (d.top_products || []).map((p) => `
    <tr><td>${esc(p.sku)}</td><td>${esc(p.name)}</td><td>${p.qty}</td><td>${money(p.amount)}</td></tr>`).join("")
    || `<tr><td colspan="4" style="color:#6b7280">还没有已发货订单</td></tr>`;
  $("stat").textContent = `共 ${d.products} 个商品 / ${d.orders} 张订单`;
}

/* ---------- 客户 ---------- */
async function loadCustomers(keyword = "") {
  const q = keyword ? "?q=" + encodeURIComponent(keyword) : "";
  const list = await api("GET", "/api/customers" + q);
  $("stat").textContent = `${list.length} 个客户`;
  $("customer-body").innerHTML = list.map((c) => `
    <tr><td>${c.id}</td><td>${esc(c.name)}</td><td>${esc(c.phone || "-")}</td>
    <td>${esc(c.address || "-")}</td><td>${esc(c.created)}</td></tr>`).join("")
    || `<tr><td colspan="5" style="color:#6b7280">还没有客户，点右上角「新建客户」</td></tr>`;
}

$("customer-search-btn").addEventListener("click", () => loadCustomers($("customer-search").value.trim()));
$("customer-new").addEventListener("click", async () => {
  const name = prompt("客户名称");
  if (!name) return;
  const phone = prompt("电话（可空）", "") || "";
  const address = prompt("地址（可空）", "") || "";
  try {
    await api("POST", "/api/customers", { name, phone, address });
    toast("客户已创建");
    loadCustomers();
  } catch (err) { toast(err.message, true); }
});

/* ---------- 订单 ---------- */
const STATUS_TEXT = { draft: "草稿", confirmed: "已确认", shipped: "已发货", cancelled: "已取消" };

async function loadOrders() {
  const status = $("order-status").value;
  const list = await api("GET", "/api/orders" + (status ? "?status=" + status : ""));
  $("stat").textContent = `${list.length} 张订单`;
  $("order-body").innerHTML = list.map((o) => {
    const ops = [];
    if (o.status === "draft") ops.push(`<button class="op" data-act="confirm" data-no="${o.no}">确认</button>`);
    if (o.status === "confirmed") ops.push(`<button class="op" data-act="ship" data-no="${o.no}">发货</button>`);
    if (o.status === "draft" || o.status === "confirmed") ops.push(`<button class="op" data-act="cancel" data-no="${o.no}">取消</button>`);
    return `<tr><td>${esc(o.no)}</td><td>${esc(o.customer_name)}</td><td>${money(o.total_amount)}</td>
      <td><span class="tag ${o.status}">${STATUS_TEXT[o.status] || o.status}</span></td>
      <td>${esc(o.created)}</td><td>${ops.join("") || "-"}</td></tr>`;
  }).join("") || `<tr><td colspan="6" style="color:#6b7280">还没有订单</td></tr>`;
}

$("order-refresh").addEventListener("click", loadOrders);
$("order-status").addEventListener("change", loadOrders);

$("order-body").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const { act, no } = btn.dataset;
  try {
    const order = await api("POST", `/api/orders/${no}/${act}`);
    toast(`${no} 已${{ confirm: "确认", ship: "发货", cancel: "取消" }[act]}（${STATUS_TEXT[order.status]}）`);
    loadOrders();
  } catch (err) { toast(err.message, true); }
});

$("order-new").addEventListener("click", async () => {
  const customers = await api("GET", "/api/customers");
  if (!customers.length) return toast("请先新建客户", true);
  const customerName = prompt(`客户（可选：${customers.map((c) => c.name).join("、")}）`, customers[0].name);
  if (!customerName) return;
  const customer = customers.find((c) => c.name === customerName) || customers[0];
  const sku = prompt("商品 SKU", "SKU-001");
  if (!sku) return;
  const qty = Number(prompt("数量", "1") || 0);
  const price = Number(prompt("单价", "10") || 0);
  const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, "");
  const no = "SO-" + stamp + "-" + String(Math.floor(Math.random() * 900) + 100);
  try {
    await api("POST", "/api/orders", { no, customer_id: customer.id, items: [{ sku, qty, price }] });
    toast("订单已创建（草稿）");
    loadOrders();
  } catch (err) { toast(err.message, true); }
});
