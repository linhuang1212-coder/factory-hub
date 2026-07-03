// FactoryHub 工厂端 —— 零构建 vanilla JS。业务同构 AI-ERP：入库→库存→转移商品部
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const esc = (x) => (x == null ? "" : String(x).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;"));

const api = async (method, url, body) => {
  const opt = { method, headers: { "Content-Type": "application/json" } };
  const tk = localStorage.getItem("fh_token");
  if (tk) opt.headers["Authorization"] = "Bearer " + tk;
  if (body !== undefined) opt.body = JSON.stringify(body);
  const r = await fetch(url, opt);
  let data = {};
  try { data = await r.json(); } catch (_) {}
  if (r.status === 401 && !url.startsWith("/api/auth/login")) showLogin();
  return { ok: r.ok, status: r.status, data };
};

let styleMap = {};
let editingInboundId = null;   // 非空=入库表单处于「编辑」模式（保存走 PUT）

function toast(msg, kind = "ok") {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show " + kind;
  setTimeout(() => (t.className = "toast"), 3400);
}
function todayStr() {
  const d = new Date(); const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
const errMsg = (data, fallback) => {
  if (!data) return fallback;
  if (typeof data.detail === "string") return data.detail;
  if (data.detail) return JSON.stringify(data.detail);
  return data.message || fallback;
};

// ---------- 登录 ----------
function showLogin(msg) {
  $("#appLayout").hidden = true;
  $("#loginMask").hidden = false;
  $("#loginHint").textContent = msg || "";
  $("#lPass").value = "";
  setTimeout(() => $("#lUser").focus(), 50);
}
async function doLogin() {
  const username = $("#lUser").value.trim();
  const password = $("#lPass").value;
  if (!username || !password) return ($("#loginHint").textContent = "请输入账号和密码");
  const btn = $("#btnLogin");
  btn.disabled = true;
  try {
    const { ok, data } = await api("POST", "/api/auth/login", { username, password });
    if (!ok) return ($("#loginHint").textContent = errMsg(data, "账号或密码错误"));
    localStorage.setItem("fh_token", data.data.token);
    $("#loginMask").hidden = true;
    await enterApp();
  } catch (e) {
    $("#loginHint").textContent = "登录异常: " + e.message;
  } finally {
    btn.disabled = false;
  }
}
function doLogout() { localStorage.removeItem("fh_token"); location.reload(); }
async function addUser() {
  const username = prompt("新账号用户名：");
  if (!username) return;
  const password = prompt("初始密码（至少6位，建议纯小写字母+数字）：");
  if (!password) return;
  const { ok, data } = await api("POST", "/api/auth/users", { username: username.trim(), password });
  ok ? toast(`账号 ${username} 已创建`) : toast(errMsg(data, "创建失败"), "err");
}

// ---------- 导航 ----------
const PAGE_TITLES = { inbound: "产品入库", stock: "工厂库存", transfer: "转移商品", customers: "客户管理" };
function switchPage(page) {
  $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.page === page));
  ["inbound", "stock", "transfer", "customers"].forEach((p) => ($(`#page-${p}`).hidden = p !== page));
  $("#pageTitle").textContent = PAGE_TITLES[page];
  if (page === "stock") loadStock();
  if (page === "transfer") { loadPick(); loadTransfers(); loadCustomerOptions(); }
  if (page === "inbound") loadInbounds();
  if (page === "customers") loadCustomersPage();
}

// ---------- 页1：产品入库 ----------
function rowHtml(it = {}) {
  const v = esc;
  return `<tr>
    <td><input class="c-style" list="styleList" value="${v(it.style_no)}" placeholder="款号" /></td>
    <td><input class="c-name" value="${v(it.product_name)}" placeholder="如 足金古法戒指" /></td>
    <td><input class="c-fineness" value="${v(it.fineness)}" placeholder="足金999" /></td>
    <td><input class="c-weight num" inputmode="decimal" value="${v(it.weight)}" placeholder="0.0000" /></td>
    <td><input class="c-labor num" inputmode="decimal" value="${v(it.labor_cost)}" placeholder="0.00" /></td>
    <td><input class="c-pcs num" inputmode="numeric" value="${v(it.piece_count ?? 1)}" /></td>
    <td><input class="c-ring" value="${v(it.ring_size)}" /></td>
    <td><input class="c-gp num" inputmode="decimal" value="${v(it.gold_price)}" placeholder="可选" /></td>
    <td><input class="c-remark" value="${v(it.remark)}" /></td>
    <td><button class="btn mini del" title="删除">✕</button></td>
  </tr>`;
}
function addRow(it) {
  $("#itemBody").insertAdjacentHTML("beforeend", rowHtml(it));
  bindRow($("#itemBody").lastElementChild);
  recalcInbound();
}
function bindRow(tr) {
  tr.querySelector(".del").onclick = () => { tr.remove(); recalcInbound(); };
  tr.querySelector(".c-weight").addEventListener("input", recalcInbound);
  const styleInp = tr.querySelector(".c-style");
  styleInp.addEventListener("change", () => {
    const s = styleMap[styleInp.value.trim()];
    if (!s) return;
    const name = tr.querySelector(".c-name"), fin = tr.querySelector(".c-fineness"), labor = tr.querySelector(".c-labor");
    if (!name.value && s.name) name.value = s.name;
    if (!fin.value && s.fineness) fin.value = s.fineness;
    if (!labor.value && s.cost_labor_rate) labor.value = s.cost_labor_rate;
  });
}
function collectItems() {
  const items = [];
  for (const tr of $$("#itemBody tr")) {
    const g = (c) => tr.querySelector(c).value.trim();
    if (!g(".c-name") && !g(".c-weight") && !g(".c-style") && !g(".c-fineness")) continue;
    items.push({
      style_no: g(".c-style") || null, product_name: g(".c-name"), fineness: g(".c-fineness"),
      weight: g(".c-weight"), labor_cost: g(".c-labor") || "0",
      piece_count: parseInt(g(".c-pcs") || "1", 10) || 1,
      ring_size: g(".c-ring") || null, gold_price: g(".c-gp") || null, remark: g(".c-remark") || null,
    });
  }
  return items;
}
function recalcInbound() {
  let cnt = 0, w = 0;
  for (const tr of $$("#itemBody tr")) {
    const wv = tr.querySelector(".c-weight").value.trim();
    if (wv) { w += parseFloat(wv) || 0; cnt++; }
  }
  $("#inTotals").textContent = `合计 ${cnt} 件 / ${w.toFixed(4)} g`;
}
function rowCount() {
  const n = parseInt($("#addRowCount").value, 10) || 1;
  return Math.min(Math.max(n, 1), 50);
}
function addRows() {
  const n = rowCount();
  for (let i = 0; i < n; i++) addRow();
  localStorage.setItem("fh_addrows", String(n));   // 记住习惯的行数
}
function resetInbound() {
  editingInboundId = null;
  $("#btnInSave").textContent = "入 库";
  $("#inDate").value = todayStr();
  $("#inRemark").value = "";
  $("#itemBody").innerHTML = "";
  $("#inHint").textContent = "";
  for (let i = 0; i < rowCount(); i++) addRow();   // 清空后按习惯行数铺好
}
async function saveInbound() {
  const items = collectItems();
  if (!items.length) return toast("先加至少一件货", "err");
  const payload = { order_date: $("#inDate").value || todayStr(), remark: $("#inRemark").value.trim() || null, items };
  const editing = editingInboundId;
  const { ok, data } = editing
    ? await api("PUT", `/api/inbounds/${editing}`, payload)
    : await api("POST", "/api/inbounds", payload);
  if (!ok) { $("#inHint").textContent = errMsg(data, editing ? "保存失败" : "入库失败"); return toast(editing ? "保存失败" : "入库失败", "err"); }
  toast(editing ? `已保存 ✓ ${data.data.order_no}` : `已入库 ✓ ${data.data.order_no}（${data.data.item_count} 件 / ${data.data.total_weight} g）`);
  resetInbound();
  loadInbounds();
}
async function loadInbounds() {
  const { ok, data } = await api("GET", "/api/inbounds");
  const tb = $("#inListBody");
  if (!ok) return (tb.innerHTML = `<tr><td colspan="7" class="muted center">加载失败</td></tr>`);
  const rows = data.data || [];
  if (!rows.length) return (tb.innerHTML = `<tr><td colspan="7" class="muted center">暂无入库单</td></tr>`);
  tb.innerHTML = rows.map((o) => `<tr>
    <td class="mono">${esc(o.order_no)}</td><td>${esc(o.order_date)}</td><td>${esc(o.operator)}</td>
    <td class="center">${o.item_count}</td><td class="num">${esc(o.total_weight)} g</td>
    <td>${esc(o.remark)}</td>
    <td class="acts">
      <button class="btn mini" data-act="edit" data-id="${o.id}" ${o.deletable ? "" : "disabled title='已进转移，不可编辑'"}>编辑</button>
      <button class="btn mini del" data-act="del" data-id="${o.id}" data-deletable="${o.deletable ? 1 : 0}">删除</button>
    </td>
  </tr>`).join("");
  tb.querySelectorAll("button[data-act]").forEach((b) => {
    const id = b.dataset.id;
    if (b.dataset.act === "edit") b.onclick = () => editInbound(id);
    if (b.dataset.act === "del") b.onclick = async () => {
      const deletable = b.dataset.deletable === "1";
      let url = `/api/inbounds/${id}`;
      if (deletable) {
        if (!confirm("删除这张入库单？货将退出工厂库存。")) return;
      } else {
        if (!confirm("该单的货已进转移流程。\n强制删除会一并删掉它的货品和已空的转移单。\n（若门店那边已生成预入库单，需去门店另行删除）\n确定强制删除？")) return;
        url += "?force=true";
      }
      const r = await api("DELETE", url);
      r.ok ? (toast("已删除"), loadInbounds(), (typeof loadTransfers === "function" && loadTransfers())) : toast(errMsg(r.data, "删除失败"), "err");
    };
  });
}

// 编辑入库单：把该单载入上方入库表单，保存即覆盖（PUT）
async function editInbound(id) {
  const { ok, data } = await api("GET", `/api/inbounds/${id}`);
  if (!ok) return toast(errMsg(data, "加载失败"), "err");
  const o = data.data;
  if (!(o.items || []).every((it) => it.status === "in_stock"))
    return toast("该单有货已进转移，不能编辑", "err");
  switchPage("inbound");
  editingInboundId = id;
  $("#inDate").value = o.order_date || todayStr();
  $("#inRemark").value = o.remark || "";
  $("#itemBody").innerHTML = "";
  (o.items || []).forEach((it) => addRow(it));
  if (!(o.items || []).length) addRow();
  recalcInbound();
  $("#inHint").textContent = `正在编辑 ${o.order_no}（保存即覆盖原单）`;
  $("#btnInSave").textContent = "保存修改";
  window.scrollTo(0, 0);
}

// ---------- 页2：工厂库存 ----------
const ST_LABEL = { in_stock: "在库", reserved: "待转移", transferred: "已转移" };
async function loadStock() {
  const q = $("#stQ").value.trim(), status = $("#stStatus").value;
  const { ok, data } = await api("GET", `/api/stock?q=${encodeURIComponent(q)}&status=${status}`);
  const tb = $("#stBody");
  if (!ok) return (tb.innerHTML = `<tr><td colspan="10" class="muted center">加载失败</td></tr>`);
  const s = data.summary;
  $("#stSummary").innerHTML =
    `在库 <b>${s.in_stock.count}</b> 件 / <b>${s.in_stock.weight}</b> g　·　` +
    `待转移 ${s.reserved.count} 件 / ${s.reserved.weight} g　·　` +
    `已转移 ${s.transferred.count} 件 / ${s.transferred.weight} g`;
  const rows = data.data || [];
  if (!rows.length) return (tb.innerHTML = `<tr><td colspan="10" class="muted center">没有匹配的货品</td></tr>`);
  tb.innerHTML = rows.map((it) => `<tr>
    <td class="muted">#${it.id}</td><td class="mono">${esc(it.style_no) || "—"}</td><td>${esc(it.product_name)}</td>
    <td>${esc(it.fineness)}</td><td class="num">${esc(it.weight)}</td><td class="num">${esc(it.labor_cost)}</td>
    <td class="center">${it.piece_count ?? 1}</td><td>${esc(it.ring_size) || "—"}</td>
    <td><span class="badge ${it.status}">${ST_LABEL[it.status] || it.status}</span></td>
    <td class="muted">#${it.inbound_id}</td>
  </tr>`).join("");
}

// ---------- 页3：转移商品部 ----------
function recalcPick() {
  let cnt = 0, w = 0;
  $$("#trPickBody input[type=checkbox]:checked").forEach((c) => { cnt++; w += parseFloat(c.dataset.w) || 0; });
  $("#trTotals").textContent = `已选 ${cnt} 件 / ${w.toFixed(4)} g`;
}
async function loadPick() {
  const { ok, data } = await api("GET", "/api/stock?status=available");
  const tb = $("#trPickBody");
  $("#trCheckAll").checked = false;
  if (!ok) return (tb.innerHTML = `<tr><td colspan="8" class="muted center">加载失败</td></tr>`);
  const rows = data.data || [];
  if (!rows.length) { tb.innerHTML = `<tr><td colspan="8" class="muted center">工厂库存没有在库货品，先去「产品入库」</td></tr>`; recalcPick(); return; }
  tb.innerHTML = rows.map((it) => `<tr>
    <td><input type="checkbox" value="${it.id}" data-w="${esc(it.weight)}" /></td>
    <td class="mono">${esc(it.style_no) || "—"}</td><td>${esc(it.product_name)}</td><td>${esc(it.fineness)}</td>
    <td class="num">${esc(it.weight)}</td><td class="num">${esc(it.labor_cost)}</td>
    <td class="center">${it.piece_count ?? 1}</td><td>${esc(it.ring_size) || "—"}</td>
  </tr>`).join("");
  tb.querySelectorAll("input[type=checkbox]").forEach((c) => c.addEventListener("change", recalcPick));
  recalcPick();
}
async function loadCustomerOptions() {
  const { ok, data } = await api("GET", "/api/customers");
  const sel = $("#trCustomer");
  if (!ok) return (sel.innerHTML = `<option value="">加载失败</option>`);
  const rows = (data.data || []).filter((c) => c.enabled);
  if (!rows.length) return (sel.innerHTML = `<option value="">尚无客户，先去「客户管理」添加</option>`);
  sel.innerHTML = rows.map((c) =>
    `<option value="${c.id}">${esc(c.name)}${c.key_configured ? "" : "（未接通）"}</option>`).join("");
}
async function createTransfer() {
  const ids = $$("#trPickBody input[type=checkbox]:checked").map((c) => parseInt(c.value, 10));
  if (!ids.length) return toast("先勾选要转移的货品", "err");
  const customerId = parseInt($("#trCustomer").value, 10);
  if (!customerId) return toast("请选择转移给哪个客户", "err");
  const { ok, data } = await api("POST", "/api/transfers",
    { customer_id: customerId, item_ids: ids, remark: $("#trRemark").value.trim() || null });
  if (!ok) return toast(errMsg(data, "创建失败"), "err");
  toast(`转移单 ${data.data.transfer_no} 已创建（${data.data.item_count} 件 → ${data.data.customer_name}），点「转移」推送`);
  $("#trRemark").value = "";
  loadPick(); loadTransfers();
}
async function loadTransfers() {
  const { ok, data } = await api("GET", "/api/transfers");
  const tb = $("#trListBody");
  if (!ok) return (tb.innerHTML = `<tr><td colspan="8" class="muted center">加载失败</td></tr>`);
  const rows = data.data || [];
  if (!rows.length) return (tb.innerHTML = `<tr><td colspan="8" class="muted center">暂无转移单</td></tr>`);
  tb.innerHTML = rows.map((t) => {
    const acts = [];
    if (t.status === "draft") {
      acts.push(`<button class="btn mini ship" data-act="push" data-id="${t.id}">转移</button>`);
      acts.push(`<button class="btn mini del" data-act="del" data-id="${t.id}">删除</button>`);
    } else if (t.status === "pushed") {
      acts.push(`<button class="btn mini" data-act="push" data-id="${t.id}">重推</button>`);
      acts.push(`<button class="btn mini del" data-act="delf" data-id="${t.id}">删除</button>`);
    } else {
      acts.push(`<button class="btn mini del" data-act="delf" data-id="${t.id}">删除</button>`);
    }
    return `<tr>
      <td class="mono">${esc(t.transfer_no)}</td><td>${(t.created_at || "").slice(0, 10)}</td>
      <td>${esc(t.customer_name) || "—"}</td>
      <td class="center">${t.item_count}</td><td class="num">${esc(t.total_weight)}</td>
      <td><span class="badge ${t.status}">${esc(t.status_label)}</span></td>
      <td class="mono">${esc(t.store_order_no) || "—"}</td>
      <td class="acts">${acts.join("")}</td>
    </tr>`;
  }).join("");
  tb.querySelectorAll("button[data-act]").forEach((b) => {
    const id = b.dataset.id;
    if (b.dataset.act === "del") b.onclick = async () => {
      if (!confirm("删除转移单？货将解锁回在库。")) return;
      const r = await api("DELETE", `/api/transfers/${id}`);
      r.ok ? (toast("已删除，货已回在库"), loadPick(), loadTransfers()) : toast(errMsg(r.data, "删除失败"), "err");
    };
    if (b.dataset.act === "delf") b.onclick = async () => {
      if (!confirm("强制删除这张已推送的转移单？\n货会解锁回工厂在库。\n（门店那边若已生成预入库单，需去门店另行删除）")) return;
      const r = await api("DELETE", `/api/transfers/${id}?force=true`);
      r.ok ? (toast("已删除，货已回在库"), loadPick(), loadTransfers()) : toast(errMsg(r.data, "删除失败"), "err");
    };
    if (b.dataset.act === "push") b.onclick = async () => {
      b.disabled = true;
      const { ok, data } = await api("POST", `/api/transfers/${id}/push`);
      b.disabled = false;
      if (ok && data.success) {
        toast(`已转移 ✓ 门店预入库单 ${data.data.store_order_no || ""}`);
        $("#trHint").textContent = "";
      } else {
        toast("转移未成功", "err");
        $("#trHint").textContent = "转移未成功：" + (data.message || "");
      }
      loadTransfers();
    };
  });
}

// ---------- 页4：客户管理（仅管理员） ----------
async function loadCustomersPage() {
  const { ok, data } = await api("GET", "/api/customers");
  const tb = $("#cuListBody");
  if (!ok) return (tb.innerHTML = `<tr><td colspan="6" class="muted center">加载失败</td></tr>`);
  const rows = data.data || [];
  if (!rows.length) return (tb.innerHTML = `<tr><td colspan="6" class="muted center">暂无客户，用上方表单添加</td></tr>`);
  tb.innerHTML = rows.map((c) => `<tr>
    <td><b>${esc(c.name)}</b></td><td class="mono">${esc(c.store_base_url)}</td>
    <td>${esc(c.supplier_name)}</td>
    <td>${c.key_configured ? '<span class="badge confirmed">已配置</span>' : '<span class="badge reserved">未配置</span>'}</td>
    <td>${c.enabled ? '<span class="badge in_stock">启用</span>' : '<span class="badge draft">停用</span>'}</td>
    <td class="acts">
      <button class="btn mini" data-act="key" data-id="${c.id}">${c.key_configured ? "换Key" : "填Key"}</button>
      <button class="btn mini" data-act="toggle" data-id="${c.id}" data-en="${c.enabled ? 0 : 1}">${c.enabled ? "停用" : "启用"}</button>
      <button class="btn mini del" data-act="del" data-id="${c.id}">删除</button>
    </td>
  </tr>`).join("");
  tb.querySelectorAll("button[data-act]").forEach((b) => {
    const id = b.dataset.id;
    if (b.dataset.act === "key") b.onclick = async () => {
      const key = prompt("粘贴对方发的 API Key（只写不读，旧值不会显示）：");
      if (!key || !key.trim()) return;
      const r = await api("PUT", `/api/customers/${id}`, { store_api_key: key.trim() });
      r.ok ? (toast("Key 已更新"), loadCustomersPage()) : toast(errMsg(r.data, "更新失败"), "err");
    };
    if (b.dataset.act === "toggle") b.onclick = async () => {
      const r = await api("PUT", `/api/customers/${id}`, { enabled: b.dataset.en === "1" });
      r.ok ? loadCustomersPage() : toast(errMsg(r.data, "操作失败"), "err");
    };
    if (b.dataset.act === "del") b.onclick = async () => {
      if (!confirm("删除该客户档案？（已有转移单记录的客户只能停用）")) return;
      const r = await api("DELETE", `/api/customers/${id}`);
      r.ok ? (toast("已删除"), loadCustomersPage()) : toast(errMsg(r.data, "删除失败"), "err");
    };
  });
}
async function addCustomer() {
  const payload = {
    name: $("#cuName").value.trim(),
    store_base_url: $("#cuUrl").value.trim(),
    supplier_name: $("#cuSupplier").value.trim(),
    store_api_key: $("#cuKey").value.trim() || null,
    remark: $("#cuRemark").value.trim() || null,
  };
  if (!payload.name || !payload.store_base_url || !payload.supplier_name)
    return ($("#cuHint").textContent = "客户名、对方系统地址、本厂供应商名 都必填");
  const { ok, data } = await api("POST", "/api/customers", payload);
  if (!ok) return ($("#cuHint").textContent = errMsg(data, "添加失败"));
  $("#cuHint").textContent = "";
  ["cuName", "cuUrl", "cuSupplier", "cuKey", "cuRemark"].forEach((i) => ($("#" + i).value = ""));
  toast(`客户「${payload.name}」已添加`);
  loadCustomersPage();
}

// ---------- 款式 ----------
async function loadStyles() {
  const { ok, data } = await api("GET", "/api/styles");
  if (!ok) return;
  styleMap = {};
  $("#styleList").innerHTML = (data.data || []).map((s) => {
    styleMap[s.style_no] = s;
    return `<option value="${esc(s.style_no)}">${esc(s.name)} ${esc(s.fineness)}</option>`;
  }).join("");
}
async function syncStyles() {
  $("#btnSync").disabled = true; $("#btnSync").textContent = "同步中…";
  const { data } = await api("POST", "/api/styles/sync");
  data.success ? (toast(`已同步 ${data.synced} 个款式`), await loadStyles()) : toast(data.message || "同步失败", "err");
  $("#btnSync").disabled = false; $("#btnSync").textContent = "↻ 同步款式";
}

// ---------- 初始化 ----------
async function enterApp() {
  const { ok, data } = await api("GET", "/api/auth/me");
  if (!ok) return showLogin();
  const me = data.data;
  $("#appLayout").hidden = false;
  $("#brandSub").textContent = me.supplier_name || "工厂端";
  $("#whoami").textContent = `👤 ${me.username}`;
  $("#btnAddUser").hidden = !me.is_admin;
  $("#navCustomers").hidden = !me.is_admin;
  $("#btnSync").hidden = !me.style_sync_enabled;
  const cu = await api("GET", "/api/customers");
  if (cu.ok) {
    const rows = (cu.data.data || []).filter((c) => c.enabled);
    const on = rows.filter((c) => c.key_configured).length;
    $("#storeStatus").innerHTML = rows.length
      ? `客户 ${rows.length} 家 · <span class="dot ${on ? "ok" : "warn"}"></span>${on} 家已接通`
      : `<span class="dot warn"></span>尚未添加客户`;
  }
  if (me.style_sync_enabled) await loadStyles();
  resetInbound();
  switchPage("inbound");
}
function bindStatic() {
  $$(".nav-item").forEach((b) => (b.onclick = () => switchPage(b.dataset.page)));
  $("#addRowCount").value = localStorage.getItem("fh_addrows") || "1";
  $("#btnAddRow").onclick = addRows;
  $("#btnInReset").onclick = resetInbound;
  $("#btnInSave").onclick = saveInbound;
  $("#btnStSearch").onclick = loadStock;
  $("#stQ").addEventListener("keydown", (e) => { if (e.key === "Enter") loadStock(); });
  $("#btnTrReload").onclick = () => { loadPick(); loadCustomerOptions(); };
  $("#btnTrCreate").onclick = createTransfer;
  $("#btnCuAdd").onclick = addCustomer;
  $("#trCheckAll").onchange = (e) => {
    $$("#trPickBody input[type=checkbox]").forEach((c) => (c.checked = e.target.checked));
    recalcPick();
  };
  $("#btnLogin").onclick = doLogin;
  $("#btnLogout").onclick = doLogout;
  $("#btnAddUser").onclick = addUser;
  $("#btnSync").onclick = syncStyles;
  $("#lPass").addEventListener("keydown", (e) => { if (e.key === "Enter") doLogin(); });
}
async function boot() {
  bindStatic();
  if (!localStorage.getItem("fh_token")) return showLogin();
  await enterApp();
}
boot();
