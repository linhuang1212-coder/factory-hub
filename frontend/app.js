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
let currentUser = "";          // 登录账号（打印出库单「制单」兜底用）

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
const PAGE_TITLES = { inbound: "收货入库", stock: "在手货", transfer: "发货门店", shiprec: "出货记录", stylebook: "电子板房", recycle: "回收站", customers: "客户" };
function switchPage(page) {
  $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.page === page));
  ["inbound", "stock", "transfer", "shiprec", "stylebook", "recycle", "customers"].forEach((p) => ($(`#page-${p}`).hidden = p !== page));
  $("#pageTitle").textContent = PAGE_TITLES[page];
  if (page === "stock") loadStock();
  if (page === "transfer") { loadPick(); loadTransfers(); loadCustomerOptions(); }
  if (page === "shiprec") loadShipRecords();
  if (page === "inbound") { loadInbounds(); loadInboundTargets(); }
  if (page === "stylebook") loadStylebook();
  if (page === "recycle") loadRecycle();
  if (page === "customers") loadCustomersPage();
  const lay = document.getElementById("appLayout");
  if (lay) lay.classList.remove("nav-open");   // 移动端点导航后收起抽屉
}

// ---------- 页1：产品入库 ----------
function rowHtml(it = {}) {
  const v = esc;
  return `<tr data-item-id="${it.id != null ? it.id : ''}">
    <td><input class="c-style" list="styleList" value="${v(it.style_no)}" placeholder="款号" /></td>
    <td><input class="c-name" list="nameList" value="${v(it.product_name)}" placeholder="如 足金古法戒指" /></td>
    <td><input class="c-fineness" value="${v(it.fineness || '足金999')}" placeholder="足金999" /></td>
    <td><input class="c-weight num" inputmode="decimal" value="${v(it.weight)}" placeholder="0.0000" /></td>
    <td><input class="c-labor num" inputmode="decimal" value="${v(it.labor_cost)}" placeholder="0.00" /></td>
    <td><input class="c-plabor num" inputmode="decimal" value="${v(it.piece_labor_cost)}" placeholder="0.00" title="附加费(元/件),与门店口径一致按件数计" /></td>
    <td><input class="c-pcs num" inputmode="numeric" value="${v(it.piece_count ?? 1)}" /></td>
    <td><input class="c-ring" value="${v(it.ring_size)}" /></td>
    <td style="text-align:center"><input type="checkbox" class="c-uniq" ${it.is_unique === 0 ? "checked" : ""} title="勾选=称重货(不发一码一件码);默认一码一件" /></td>
    <td><input class="c-remark" value="${v(it.remark)}" /></td>
    <td class="c-fee num" style="white-space:nowrap;color:#b8860b;font-weight:600;text-align:right"></td>
    <td><button class="btn mini del" title="删除">✕</button></td>
  </tr>`;
}
function addRow(it) {
  const prev = $("#itemBody").lastElementChild;   // 加行前的末行
  $("#itemBody").insertAdjacentHTML("beforeend", rowHtml(it));
  const tr = $("#itemBody").lastElementChild;
  if (!it && prev) {   // 空白新行(非载入数据)→ 继承上一行品名/成色,重复录入只需填一次
    const inh = (c) => { const p = prev.querySelector(c), n = tr.querySelector(c); if (p && n && p.value) n.value = p.value; };
    inh(".c-name"); inh(".c-fineness");
  }
  bindRow(tr);
  recalcInbound();
}
function bindRow(tr) {
  tr.querySelector(".del").onclick = () => { tr.remove(); recalcInbound(); };
  tr.querySelector(".c-weight").addEventListener("input", recalcInbound);
  tr.querySelector(".c-labor").addEventListener("input", recalcInbound);
  tr.querySelector(".c-plabor").addEventListener("input", recalcInbound);
  tr.querySelector(".c-pcs").addEventListener("input", recalcInbound);
  const styleInp = tr.querySelector(".c-style");
  const nameInp = tr.querySelector(".c-name");
  const fitStyle = () => fitInput(styleInp, 100, 260);   // 款号:内容长→自动变宽(显示全)
  const fitName = () => fitInput(nameInp, 90, 240);       // 品名:短→收窄,长→变宽
  styleInp.addEventListener("input", fitStyle);
  nameInp.addEventListener("input", fitName);
  nameInp.addEventListener("change", () => addNameToList(nameInp.value.trim()));  // 录过的品名即时进联想
  styleInp.addEventListener("change", () => {
    const s = styleMap[styleInp.value.trim()];
    if (!s) return;
    const name = tr.querySelector(".c-name"), fin = tr.querySelector(".c-fineness"), labor = tr.querySelector(".c-labor");
    if (!name.value && s.name) { name.value = s.name; fitName(); }
    if (!fin.value && s.fineness) fin.value = s.fineness;
    if (!labor.value && s.cost_labor_rate) labor.value = s.cost_labor_rate;
  });
  fitStyle(); fitName();   // 初次按内容定宽
  // 键盘导航:Enter 逐框往后(末框→下一行,没有就新建);方向键上下换行、左右到光标边界换列;Ctrl+D 复制上一行同列
  const _inps = tr.querySelectorAll("input:not([type=checkbox])");   // 称重复选框不进导航流
  const colOf = (row, i) => row.querySelectorAll("input:not([type=checkbox])")[i];
  const _goNextRow = () => {
    let next = tr.nextElementSibling;
    if (!next) { addRow(); next = $("#itemBody").lastElementChild; }
    const f = next && next.querySelector("input");
    if (f) f.focus();
  };
  _inps.forEach((inp, idx) => {
    inp.addEventListener("keydown", (e) => {
      const isLast = idx === _inps.length - 1;
      const atStart = inp.selectionStart === 0 && inp.selectionEnd === 0;
      const atEnd = inp.selectionStart === inp.value.length && inp.selectionEnd === inp.value.length;
      if (e.key === "Enter") {
        e.preventDefault();
        if (isLast) _goNextRow(); else _inps[idx + 1].focus();
      } else if (e.key === "Tab" && !e.shiftKey && isLast) {
        e.preventDefault();
        _goNextRow();
      } else if ((e.ctrlKey || e.metaKey) && (e.key === "d" || e.key === "D")) {
        e.preventDefault();   // Ctrl+D 同上:复制上一行同列的值
        const src = tr.previousElementSibling && colOf(tr.previousElementSibling, idx);
        if (src) { inp.value = src.value; inp.dispatchEvent(new Event("input")); }
      } else if (e.key === "ArrowDown") {
        const t = tr.nextElementSibling && colOf(tr.nextElementSibling, idx);
        if (t) { e.preventDefault(); t.focus(); }
      } else if (e.key === "ArrowUp") {
        const t = tr.previousElementSibling && colOf(tr.previousElementSibling, idx);
        if (t) { e.preventDefault(); t.focus(); }
      } else if (e.key === "ArrowLeft") {
        if (idx > 0) { e.preventDefault(); _inps[idx - 1].focus(); }   // 按一下直接跳上一格(不在字符间挪)
      } else if (e.key === "ArrowRight") {
        if (!isLast) { e.preventDefault(); _inps[idx + 1].focus(); }   // 按一下直接跳下一格
      }
    });
  });
}
function collectItems() {
  const items = [];
  for (const tr of $$("#itemBody tr")) {
    const g = (c) => tr.querySelector(c).value.trim();
    if (!g(".c-name") && !g(".c-weight") && !g(".c-style") && !g(".c-fineness")) continue;
    items.push({
      style_no: g(".c-style") || null, product_name: g(".c-name"), fineness: g(".c-fineness"),
      weight: g(".c-weight"), labor_cost: g(".c-labor") || "0", piece_labor_cost: g(".c-plabor") || null,
      piece_count: parseInt(g(".c-pcs") || "1", 10) || 1,
      ring_size: g(".c-ring") || null, remark: g(".c-remark") || null,
      is_unique: tr.querySelector(".c-uniq") && tr.querySelector(".c-uniq").checked ? 0 : 1,
      id: tr.dataset.itemId ? parseInt(tr.dataset.itemId, 10) : null,   // 已发货单就地更新按此 id 匹配
    });
  }
  return items;
}
function recalcInbound() {
  let cnt = 0, w = 0, labor = 0;
  for (const tr of $$("#itemBody tr")) {
    const wStr = tr.querySelector(".c-weight").value.trim();
    const wv = parseFloat(wStr) || 0;
    const lv = parseFloat(tr.querySelector(".c-labor").value.trim()) || 0;
    const pv = parseFloat(tr.querySelector(".c-plabor").value.trim()) || 0;   // 附加费(元/件)
    const pc = parseInt(tr.querySelector(".c-pcs").value.trim(), 10) || 1;
    const rowFee = wv * lv + pc * pv;             // 单行工费合计 = 克重×克工费 + 件数×附加费
    const feeCell = tr.querySelector(".c-fee");
    if (feeCell) feeCell.textContent = wStr ? `¥${rowFee.toFixed(2)}` : "";
    if (wStr) { w += wv; cnt++; labor += rowFee; }
  }
  $("#inTotals").textContent = `合计 ${cnt} 件 / ${w.toFixed(4)} g · 合计工费 ¥${labor.toFixed(2)}`;
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
  $("#btnInSave").textContent = "收 货";
  $("#inDate").value = todayStr();
  $("#inReceiver").value = "";        // 收货单位留空(必填,由用户填)
  { const _tc = $("#inTargetCustomer"); if (_tc) _tc.value = ""; }   // 发货门店重置为「暂不指定」
  $("#inRemark").value = "";
  $("#itemBody").innerHTML = "";
  $("#inHint").textContent = "";
  for (let i = 0; i < rowCount(); i++) addRow();   // 清空后按习惯行数铺好
}
async function saveInbound() {
  const items = collectItems();
  if (!items.length) return toast("先加至少一件货", "err");
  const receiver = $("#inReceiver").value.trim();
  if (!receiver) { $("#inReceiver").focus(); return toast("请先填写收货单位", "err"); }
  const payload = { order_date: $("#inDate").value || todayStr(), receiver, target_customer_id: parseInt($("#inTargetCustomer").value, 10) || null, remark: $("#inRemark").value.trim() || null, items };
  const editing = editingInboundId;
  const { ok, data } = editing
    ? await api("PUT", `/api/inbounds/${editing}`, payload)
    : await api("POST", "/api/inbounds", payload);
  if (!ok) { $("#inHint").textContent = errMsg(data, editing ? "保存失败" : "入库失败"); return toast(editing ? "保存失败" : "入库失败", "err"); }
  if (editing && data.shipped_edit)
    toast(`已保存 ✓ ${data.data.order_no}（此单已发货：请到出货单点【重推】把改动同步给门店）`);
  else
    toast(editing ? `已保存 ✓ ${data.data.order_no}` : `已入库 ✓ ${data.data.order_no}（${data.data.item_count} 件 / ${data.data.total_weight} g）`);
  resetInbound();
  loadInbounds();
}
// 收货登记「发货门店」下拉：填启用客户，保留当前选择
async function loadInboundTargets() {
  const sel = $("#inTargetCustomer");
  if (!sel) return;
  const { ok, data } = await api("GET", "/api/customers");
  const rows = (ok ? (data.data || []) : []).filter((c) => c.enabled);
  const cur = sel.value;
  sel.innerHTML = `<option value="">暂不指定</option>` +
    rows.map((c) => `<option value="${c.id}">${esc(c.name)}${c.key_configured ? "" : "（未接通）"}</option>`).join("");
  sel.value = cur;
}
let inboundAllRows = [];
let lastFilteredInbounds = [];   // 收货记录当前筛选后的行,供「导出」用
let lastTransferRows = [];       // 出货单列表当前行,供「导出」用
async function loadInbounds() {
  const { ok, data } = await api("GET", "/api/inbounds");
  const tb = $("#inListBody");
  if (!ok) return (tb.innerHTML = `<tr><td colspan="11" class="muted center">加载失败</td></tr>`);
  inboundAllRows = data.data || [];
  const _sel = $("#recvFOperator");
  if (_sel) {
    const _cur = _sel.value;
    const _ops = [...new Set(inboundAllRows.map((o) => o.operator).filter(Boolean))].sort();
    _sel.innerHTML = `<option value="">全部录入人</option>` + _ops.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join("");
    _sel.value = _cur;
  }
  renderInbounds();
}
// 导出当前筛选后的收货记录为 CSV（Excel 可直接打开）。零依赖客户端生成，UTF-8 带 BOM。
function exportInbounds() {
  const rows = lastFilteredInbounds || [];
  if (!rows.length) return toast("当前没有可导出的记录", "err");
  const SL = { pending: "待发货", partial: "部分已发", shipped: "已发货·等门店收", received: "门店已收货", empty: "" };
  const header = ["收货单号", "日期", "录入人", "件数", "克重(g)", "工费合计", "发往门店", "门店单号", "状态", "备注"];
  const cell = (s) => { s = (s == null ? "" : String(s)); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
  const lines = [header.join(",")];
  for (const o of rows) {
    lines.push([
      o.order_no, o.order_date, o.operator, o.item_count, o.total_weight,
      (o.total_labor == null ? "" : Number(o.total_labor).toFixed(2)),
      o.target_customer_name || "", o.store_order_no || "",
      SL[o.ship_status] || "", o.remark || "",
    ].map(cell).join(","));
  }
  const csv = "﻿" + lines.join("\r\n");   // BOM：让 Excel 正确识别 UTF-8 中文
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const _q = (($("#recvFQ") || {}).value || "").trim();
  a.href = url;
  a.download = `收货记录${_q ? "_" + _q : ""}_${todayStr()}.csv`;
  document.body.appendChild(a); a.click();
  setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(url); }, 100);
  toast(`已导出 ${rows.length} 条`);
}
// 触发浏览器下载（HTML 内核的 .xls，Excel/WPS 打开带边框/底色/合计——比 CSV 好看）
function downloadXls(html, filename) {
  const blob = new Blob(["﻿" + html], { type: "application/vnd.ms-excel;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click();
  setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(url); }, 100);
}
const _xEsc = (s) => (s == null ? "" : String(s)).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
// 生成一张美化的单据 Excel。opts:{title, info:[[标签,值],…], cols:[{h,cls}], rows:[[…]], totalRow:[…]}
function buildDocXls(opts) {
  const n = opts.cols.length;
  let h = `<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:x="urn:schemas-microsoft-com:office:excel"><head><meta charset="utf-8">`
    + `<style>`
    + `table{border-collapse:collapse;font-family:'宋体',SimSun;font-size:11pt}`
    + `td{border:.5pt solid #444;padding:4px 8px;vertical-align:middle;mso-number-format:'\\@'}`
    + `.title{font-size:18pt;font-weight:bold;text-align:center;border:none;letter-spacing:4px}`
    + `.info{border:none;font-size:10.5pt;padding:3px 6px}`
    + `.hd{background:#f4e3c1;font-weight:bold;text-align:center}`
    + `.num{text-align:right;mso-number-format:'General'}.c{text-align:center}`
    + `.tot{font-weight:bold;background:#fbf5e8}`
    + `</style></head><body><table>`;
  h += `<tr><td class="title" colspan="${n}">${_xEsc(opts.title)}</td></tr>`;
  if (opts.info && opts.info.length) {
    const info = opts.info.map(([k, v]) => `<b>${_xEsc(k)}：</b>${_xEsc(v)}`).join("　　　　");
    h += `<tr><td class="info" colspan="${n}">${info}</td></tr>`;
  }
  h += `<tr>` + opts.cols.map((c) => `<td class="hd">${_xEsc(c.h)}</td>`).join("") + `</tr>`;
  for (const r of opts.rows) {
    h += `<tr>` + r.map((cell, i) => `<td class="${opts.cols[i].cls || ""}">${_xEsc(cell)}</td>`).join("") + `</tr>`;
  }
  if (opts.totalRow) {
    h += `<tr class="tot">` + opts.totalRow.map((cell, i) => `<td class="${opts.cols[i].cls || ""}">${_xEsc(cell)}</td>`).join("") + `</tr>`;
  }
  return h + `</table></body></html>`;
}
// 导出单张【出货单】明细为美化 Excel
async function exportTransferDoc(id) {
  const { ok, data } = await api("GET", `/api/transfers/${id}`);
  if (!ok || !data.data) return toast("加载失败", "err");
  const t = data.data, its = t.items || [];
  const cols = [{ h: "序号", cls: "c" }, { h: "收货单号（包）", cls: "" }, { h: "编码", cls: "c" }, { h: "款号", cls: "" }, { h: "品名", cls: "" },
    { h: "成色", cls: "c" }, { h: "克重(g)", cls: "num" }, { h: "克工费", cls: "num" }, { h: "件工费", cls: "num" },
    { h: "件数", cls: "c" }, { h: "金额(元)", cls: "num" }, { h: "手寸", cls: "c" }, { h: "备注", cls: "" }];
  let sw = 0, sc = 0, sa = 0;
  let rows = its.map((it, i) => {
    const w = parseFloat(it.weight) || 0, lc = parseFloat(it.labor_cost) || 0;
    const pc = parseInt(it.piece_count, 10) || 1, plc = parseFloat(it.piece_labor_cost) || 0;
    const amt = w * lc + pc * plc;
    sw += w; sc += pc; sa += amt;
    return [i + 1, it.inbound_order_no || "", it.product_code || "", it.style_no || "", it.product_name || "", it.fineness || "",
      it.weight || "", it.labor_cost || "", it.piece_labor_cost || "", it.piece_count ?? 1, amt.toFixed(2), it.ring_size || "", it.remark || ""];
  });
  let totalRow = ["", "", "", "", "合计", "", sw.toFixed(4), "", "", sc, sa.toFixed(2), "", ""];
  let useCols = cols;
  if (!its.some((it) => it.product_code)) {   // 整批都没码(不发码门店，由门店自发) → 编码列整列隐藏
    const CI = 2;
    useCols = cols.filter((_, j) => j !== CI);
    rows = rows.map((r) => r.filter((_, j) => j !== CI));
    totalRow = totalRow.filter((_, j) => j !== CI);
  }
  const html = buildDocXls({
    title: "梵贝琳出货单·明细",
    info: [["出货单号", t.transfer_no], ["门店", t.customer_name || ""], ["日期", (t.created_at || "").slice(0, 10)], ["门店单号", t.store_order_no || ""]],
    cols: useCols, rows, totalRow,
  });
  downloadXls(html, `出货单明细_${t.transfer_no}.xls`);
  toast("已导出出货单明细");
}
// 导出单张【收货单】明细为美化 Excel
async function exportInboundDoc(id) {
  const { ok, data } = await api("GET", `/api/inbounds/${id}`);
  if (!ok || !data.data) return toast("加载失败", "err");
  const o = data.data, its = o.items || [];
  const cols = [{ h: "序号", cls: "c" }, { h: "款号", cls: "" }, { h: "品名", cls: "" }, { h: "成色", cls: "c" },
    { h: "克重(g)", cls: "num" }, { h: "克工费", cls: "num" }, { h: "件工费", cls: "num" },
    { h: "件数", cls: "c" }, { h: "手寸", cls: "c" }, { h: "备注", cls: "" }];
  let sw = 0, sc = 0;
  const rows = its.map((it, i) => { sw += parseFloat(it.weight) || 0; sc += (parseInt(it.piece_count, 10) || 1);
    return [i + 1, it.style_no || "", it.product_name || "", it.fineness || "", it.weight || "", it.labor_cost || "", it.piece_labor_cost || "", it.piece_count ?? 1, it.ring_size || "", it.remark || ""]; });
  const html = buildDocXls({
    title: "梵贝琳收货单",
    info: [["收货单号", o.order_no], ["收货单位", o.receiver || ""], ["日期", o.order_date || ""], ["发往门店", o.target_customer_name || ""]],
    cols, rows, totalRow: ["", "", "合计", "", sw.toFixed(4), "", "", sc, "", ""],
  });
  downloadXls(html, `收货单_${o.order_no}.xls`);
  toast("已导出收货单");
}
// 收货记录里"没被合并"的单张收货单一行（点开看它自己的货明细）
function _recvRowHtml(o, SHIP_LABEL) {
  return `<tr class="grp recv-row" data-id="${o.id}" style="cursor:pointer">
    <td class="center" onclick="event.stopPropagation()">${o.can_ship ? `<input type="checkbox" class="recv-pick" data-id="${o.id}" data-tid="${o.target_customer_id || ""}" data-target="${o.target_customer_name ? esc(o.target_customer_name) : ""}" title="勾选合并发货">` : ""}</td>
    <td class="center tgl">▸</td>
    <td class="mono">${esc(o.order_no)}</td><td>${esc(o.order_date)}</td><td>${esc(o.operator)}</td>
    <td class="center">${o.item_count}</td><td class="num">${esc(o.total_weight)} g</td>
    <td class="num">${o.total_labor == null ? "" : "¥" + Number(o.total_labor).toFixed(2)}</td>
    <td>${o.target_customer_name ? esc(o.target_customer_name) : '<span class="muted">未指定</span>'} <span class="muted" style="font-size:11px;white-space:nowrap">${SHIP_LABEL[o.ship_status] || ""}</span>${o.store_order_no ? `<br><span class="muted mono" style="font-size:11px">门店单 ${esc(o.store_order_no)}</span>` : ""}</td>
    <td>${esc(o.remark)}</td>
    <td class="acts">
      ${o.can_ship ? `<button class="btn mini ship" data-act="ship" data-id="${o.id}" data-target="${esc(o.target_customer_name) || ""}">🚀 发货</button>` : ""}
      <button class="btn mini" data-act="print" data-id="${o.id}">🖨 打印</button>
      <button class="btn mini" data-act="export" data-id="${o.id}">⬇ 导出</button>
      <button class="btn mini" data-act="edit" data-id="${o.id}" ${o.ship_status === 'received' ? "disabled title='门店已收货，不可改'" : (o.deletable ? "" : "title='已发货：可改现有明细内容，改后到出货单点重推同步门店'")}>编辑</button>
      <button class="btn mini del" data-act="del" data-id="${o.id}" data-deletable="${o.deletable ? 1 : 0}">删除</button>
    </td>
  </tr>
  <tr class="det" data-det="${o.id}" hidden><td colspan="11" style="padding:0 0 0 34px;background:#fafafa"><div class="recv-det muted" style="padding:8px">加载中…</div></td></tr>`;
}
// 多张收货单合并进【同一张出货单】→ 收拢成一行（🔗合并），点开看是哪几张；行上按钮=打印/导出合并后的出货单
function _recvGroupHtml(grp, SHIP_LABEL) {
  const n = grp.length;
  const pcs = grp.reduce((s, o) => s + (o.item_count || 0), 0);
  const w = grp.reduce((s, o) => s + (parseFloat(o.total_weight) || 0), 0);
  const labor = grp.reduce((s, o) => s + (o.total_labor == null ? 0 : Number(o.total_labor)), 0);
  const trId = grp[0].ship_transfer_id, trNo = grp[0].ship_transfer_no || "";
  const cn = grp[0].target_customer_name || "", st = grp[0].ship_status, sno = grp[0].store_order_no || "";
  const orderNos = grp.map((o) => o.order_no).join("、");
  const members = grp.map((o) => `<tr>
    <td class="mono">${esc(o.order_no)}</td><td>${esc(o.order_date)}</td>
    <td class="center">${o.item_count}</td><td class="num">${esc(o.total_weight)} g</td>
    <td class="num">${o.total_labor == null ? "" : "¥" + Number(o.total_labor).toFixed(2)}</td>
    <td><span class="muted" style="font-size:11px">${SHIP_LABEL[o.ship_status] || ""}</span></td>
    <td class="acts"><button class="btn mini" data-mact="print" data-id="${o.id}">🖨 打印</button>
      <button class="btn mini" data-mact="export" data-id="${o.id}">⬇ 导出</button>
      <button class="btn mini" data-mact="edit" data-id="${o.id}" ${o.ship_status === 'received' ? "disabled title='门店已收货，不可改'" : ""}>编辑</button></td>
  </tr>`).join("");
  return `<tr class="grp recvgrp-row" data-tr="${trId}" style="cursor:pointer;background:#fff8ec">
    <td class="center"></td>
    <td class="center tgl">▸</td>
    <td class="mono"><b>🔗 合并 ${n} 张</b>${trNo ? `<br><span class="muted" style="font-size:11px">出货单 ${esc(trNo)}</span>` : ""}</td>
    <td>${esc(grp[0].order_date)}</td><td>${esc(grp[0].operator)}</td>
    <td class="center">${pcs}</td><td class="num">${w.toFixed(2)} g</td>
    <td class="num">${labor ? "¥" + labor.toFixed(2) : ""}</td>
    <td>${cn ? esc(cn) : '<span class="muted">未指定</span>'} <span class="muted" style="font-size:11px;white-space:nowrap">${SHIP_LABEL[st] || ""}</span>${sno ? `<br><span class="muted mono" style="font-size:11px">门店单 ${esc(sno)}</span>` : ""}</td>
    <td><span class="muted" style="font-size:11px">点开看这 ${n} 张收货单</span></td>
    <td class="acts">${trId ? `<button class="btn mini" data-gact="print" data-tr="${trId}">🖨 打印出货单</button>
      <button class="btn mini" data-gact="expsum" data-tr="${trId}">⬇ 汇总</button>
      <button class="btn mini" data-gact="expdet" data-tr="${trId}">⬇ 明细</button>` : ""}</td>
  </tr>
  <tr class="det" data-detg="${trId}" hidden><td colspan="11" style="padding:0 0 0 34px;background:#fffdf6">
    <div class="muted" style="padding:6px 8px;font-size:12px">这张出货单由下面这 ${n} 张收货单合并而成（${esc(orderNos)}）：</div>
    <table class="list sub"><thead><tr><th>收货单号</th><th>日期</th><th class="num">件数</th><th class="num">克重(g)</th><th class="num">工费</th><th>状态</th><th>操作</th></tr></thead><tbody>${members}</tbody></table>
  </td></tr>`;
}
// 收货记录：按 单号/备注 搜、录入人、日期 筛选 + 合计 + 点行展开明细
function renderInbounds() {
  const tb = $("#inListBody");
  if (!tb) return;
  const _op = ($("#recvFOperator") || {}).value || "";
  const _from = ($("#recvFFrom") || {}).value || "";
  const _to = ($("#recvFTo") || {}).value || "";
  const _q = (($("#recvFQ") || {}).value || "").trim().toLowerCase();
  const _status = ($("#recvFStatus") || {}).value || "";
  const rows = inboundAllRows.filter((o) => {
    if (_op && o.operator !== _op) return false;
    const d = (o.order_date || "").slice(0, 10);
    if (_from && d < _from) return false;
    if (_to && d > _to) return false;
    if (_q && !(((o.search_text || "") + " " + (o.order_no || "") + " " + (o.remark || "")).toLowerCase().includes(_q))) return false;  // 全局搜：单号/门店单号/出货单号/发往门店/款号/编码/品名
    if (_status) {
      const st = o.ship_status;
      if (_status === "pending" && !(st === "pending" || st === "partial")) return false;
      if (_status === "shipped" && st !== "shipped") return false;
      if (_status === "received" && st !== "received") return false;
    }
    return true;
  });
  lastFilteredInbounds = rows;
  { const se = $("#recvSummary"); if (se) se.innerHTML = `共 <b>${rows.length}</b> 单 · <b>${rows.reduce((s, o) => s + (o.item_count || 0), 0)}</b> 件 · 克重合计 <b>${rows.reduce((s, o) => s + (parseFloat(o.total_weight) || 0), 0).toFixed(4)}</b> g<br><span class="muted" style="font-size:12px">勾选框只出现在「待发货」的单上——勾几张可一起「合并发货」；已发货的单没有勾选框。🔗开头的是已合并发货的，点开看是哪几张。</span>`; }
  if (!rows.length) return (tb.innerHTML = `<tr><td colspan="11" class="muted center">无匹配记录</td></tr>`);
  const SHIP_LABEL = { pending: "🟡待发货", partial: "🟠部分已发", shipped: "🟢已发货·等门店收", received: "✅门店已收货", empty: "" };
  // 把"合并发货进同一张出货单"的多张收货单收拢成一行(🔗)——只对已发货/已收货的合并生效
  const byTr = {};
  for (const o of rows) {
    const k = (o.ship_transfer_id && (o.ship_status === "shipped" || o.ship_status === "received")) ? o.ship_transfer_id : null;
    if (k) (byTr[k] = byTr[k] || []).push(o);
  }
  const emitted = new Set();
  tb.innerHTML = rows.map((o) => {
    const k = (o.ship_transfer_id && (o.ship_status === "shipped" || o.ship_status === "received")) ? o.ship_transfer_id : null;
    if (k && byTr[k].length > 1) {                 // 这张进了一张"多单合并"的出货单 → 归到合并行
      if (emitted.has(k)) return "";               // 该合并行已在首个成员处输出，其余成员不再单列
      emitted.add(k);
      return _recvGroupHtml(byTr[k], SHIP_LABEL);
    }
    return _recvRowHtml(o, SHIP_LABEL);            // 没被合并 → 单张一行
  }).join("");
  // 合并组：点行展开看成员收货单
  tb.querySelectorAll("tr.recvgrp-row").forEach((r) => {
    r.onclick = (e) => {
      if (e.target.closest(".acts")) return;
      const det = tb.querySelector(`tr.det[data-detg="${r.dataset.tr}"]`);
      const tgl = r.querySelector(".tgl");
      if (!det) return;
      det.hidden = !det.hidden;
      if (tgl) tgl.textContent = det.hidden ? "▸" : "▾";
    };
  });
  // 合并行按钮：打印/导出【合并后的出货单】
  tb.querySelectorAll("button[data-gact]").forEach((b) => {
    const tr = b.dataset.tr;
    if (b.dataset.gact === "print") b.onclick = (e) => { e.stopPropagation(); printTransfer(tr); };
    if (b.dataset.gact === "expsum") b.onclick = (e) => { e.stopPropagation(); exportTransferSummary(tr); };
    if (b.dataset.gact === "expdet") b.onclick = (e) => { e.stopPropagation(); exportTransferDoc(tr); };
  });
  // 合并组内每张收货单的单独操作
  tb.querySelectorAll("button[data-mact]").forEach((b) => {
    const id = b.dataset.id;
    if (b.dataset.mact === "print") b.onclick = (e) => { e.stopPropagation(); printInbound(id); };
    if (b.dataset.mact === "export") b.onclick = (e) => { e.stopPropagation(); exportInboundDoc(id); };
    if (b.dataset.mact === "edit") b.onclick = (e) => { e.stopPropagation(); editInbound(id); };
  });
  tb.querySelectorAll("button[data-act]").forEach((b) => {
    const id = b.dataset.id;
    if (b.dataset.act === "ship") b.onclick = (e) => { e.stopPropagation(); shipInbound(id, b.dataset.target || ""); };
    if (b.dataset.act === "print") b.onclick = (e) => { e.stopPropagation(); printInbound(id); };
    if (b.dataset.act === "export") b.onclick = (e) => { e.stopPropagation(); exportInboundDoc(id); };
    if (b.dataset.act === "edit") b.onclick = (e) => { e.stopPropagation(); editInbound(id); };
    if (b.dataset.act === "del") b.onclick = async (e) => {
      e.stopPropagation();
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
  tb.querySelectorAll("tr.recv-row").forEach((r) => {
    r.onclick = async () => {
      const id = r.dataset.id;
      const det = tb.querySelector(`tr.det[data-det="${id}"]`);
      const tgl = r.querySelector(".tgl");
      if (!det) return;
      det.hidden = !det.hidden;
      if (tgl) tgl.textContent = det.hidden ? "▸" : "▾";
      if (!det.hidden && !det.dataset.loaded) {
        const box = det.querySelector(".recv-det");
        const res = await api("GET", `/api/inbounds/${id}`);
        if (res.ok && res.data.data) {
          const its = res.data.data.items || [];
          box.innerHTML = `<table class="list sub"><thead><tr><th>款号</th><th>品名</th><th>成色</th><th class="num">克重(g)</th><th class="num">克工费</th><th class="num">附加费</th><th>件数</th><th>手寸</th><th>备注</th></tr></thead><tbody>`
            + its.map((it) => `<tr><td class="mono">${esc(it.style_no) || "—"}</td><td>${esc(it.product_name)}</td><td>${esc(it.fineness)}</td>`
              + `<td class="num">${esc(it.weight)}</td><td class="num">${esc(it.labor_cost)}</td><td class="num">${esc(it.piece_labor_cost) || ""}</td>`
              + `<td class="center">${it.piece_count ?? 1}</td><td>${esc(it.ring_size) || "—"}</td><td>${esc(it.remark) || ""}</td></tr>`).join("")
            + `</tbody></table>`;
          det.dataset.loaded = "1";
        } else { box.textContent = "加载失败"; }
      }
    };
  });
  // 合并发货：勾选框联动「合并发货」按钮计数
  tb.querySelectorAll(".recv-pick").forEach((c) => c.addEventListener("change", updateMergeCount));
  updateMergeCount();
}

// 勾选数 → 更新「合并发货（N 单）」按钮显隐与计数、同步表头全选框
function updateMergeCount() {
  const all = document.querySelectorAll("#inListBody .recv-pick");
  const on = document.querySelectorAll("#inListBody .recv-pick:checked");
  const btn = $("#btnRecvMergeShip"), cnt = $("#recvMergeCount"), ca = $("#recvCheckAll");
  if (cnt) cnt.textContent = on.length;
  if (btn) btn.hidden = on.length < 1;
  if (ca) ca.checked = all.length > 0 && on.length === all.length;
}

// 合并发货：勾选的多张收货单 → 合成一张出货单 → 一次推门店（门店只入一次）
async function mergeShipSelected() {
  const checks = [...document.querySelectorAll("#inListBody .recv-pick:checked")];
  if (!checks.length) return toast("请先勾选要合并发货的收货单", "err");
  const ids = checks.map((c) => parseInt(c.dataset.id, 10));
  const tids = [...new Set(checks.map((c) => c.dataset.tid).filter(Boolean))];
  if (tids.length > 1) return toast("勾选的收货单发往门店不一致，不能合并成一张；请只选发往同一门店的", "err");
  let customerId = tids.length === 1 ? parseInt(tids[0], 10) : null;
  let tname = checks[0].dataset.target || "";
  if (!customerId) {   // 都没指定门店 → 现选一个
    const cr = await api("GET", "/api/customers");
    const custs = (cr.ok ? (cr.data.data || []) : []).filter((c) => c.enabled);
    if (!custs.length) return toast("没有可选门店，先去「客户」添加", "err");
    const menu = custs.map((c, i) => `${i + 1}. ${c.name}`).join("\n");
    const pick = prompt(`这些收货单还没指定门店，合并后发给哪个门店？输入序号：\n${menu}`);
    if (pick == null) return;
    const idx = parseInt(pick, 10) - 1;
    if (isNaN(idx) || idx < 0 || idx >= custs.length) return toast("序号无效", "err");
    customerId = custs[idx].id; tname = custs[idx].name;
  }
  if (!confirm(`把勾选的 ${ids.length} 张收货单合并成【一张】出货单${tname ? "，发给「" + tname + "」" : ""}并推送门店？\n门店只会生成一张预入库单、过秤入库一次。`)) return;
  const btn = $("#btnRecvMergeShip"); if (btn) btn.disabled = true;
  const { ok, data } = await api("POST", "/api/transfers/from-inbounds", { inbound_ids: ids, customer_id: customerId });
  if (btn) btn.disabled = false;
  if (!ok) return toast(errMsg(data, "合并发货未成功"), "err");
  if (!data.success) return toast(data.message || "推送门店未成功，收货单保持待发货可重发", "err");
  const t = data.data || {};
  toast(`已把 ${data.merged_count || ids.length} 张收货单合并成 1 张出货单 ${t.transfer_no || ""}（共 ${t.item_count || ""} 件），门店预入库单 ${t.store_order_no || ""} ✓`);
  loadInbounds();
  if (typeof loadTransfers === "function") loadTransfers();
  if (t.id && confirm("已发货 ✓ 现在打开这张出货单的【汇总单】打印吗？（每件明细在出货单点「导出」）")) printTransfer(t.id);
}

// 一键发货（收完即发）：整张收货单在库货 → 一张出货单 → 直接推门店
async function shipInbound(id, targetName) {
  let customerId = null;
  if (!targetName) {   // 收货时没指定门店 → 现选一个
    const cr = await api("GET", "/api/customers");
    const custs = (cr.ok ? (cr.data.data || []) : []).filter((c) => c.enabled);
    if (!custs.length) return toast("没有可选门店，先去「客户」添加", "err");
    const menu = custs.map((c, i) => `${i + 1}. ${c.name}`).join("\n");
    const pick = prompt(`这张收货单还没指定发货门店，发给哪个？输入序号：\n${menu}`);
    if (pick == null) return;
    const idx = parseInt(pick, 10) - 1;
    if (isNaN(idx) || idx < 0 || idx >= custs.length) return toast("序号无效", "err");
    customerId = custs[idx].id;
    targetName = custs[idx].name;
  }
  if (!confirm(`把整张收货单发给「${targetName}」并推送门店？`)) return;
  const { ok, data } = await api("POST", `/api/transfers/from-inbound/${id}`, customerId ? { customer_id: customerId } : {});
  if (ok && data.success) {
    toast(`已发货 ✓ 门店预入库单 ${(data.data && data.data.store_order_no) || ""}`);
  } else {
    toast(data.message || errMsg(data, "发货未成功"), "err");
  }
  loadInbounds();
}

// 编辑入库单：把该单载入上方入库表单，保存即覆盖（PUT）
async function editInbound(id) {
  const { ok, data } = await api("GET", `/api/inbounds/${id}`);
  if (!ok) return toast(errMsg(data, "加载失败"), "err");
  const o = data.data;
  const _shipped = !(o.items || []).every((it) => it.status === "in_stock");
  if (o.ship_status === 'received')
    return toast("该单的货门店已确认收货，不能再改", "err");
  switchPage("inbound");
  await loadInboundTargets();   // 先把门店下拉填好，再回填选中值
  editingInboundId = id;
  $("#inDate").value = o.order_date || todayStr();
  $("#inReceiver").value = o.receiver || "";
  $("#inTargetCustomer").value = o.target_customer_id ? String(o.target_customer_id) : "";
  $("#inRemark").value = o.remark || "";
  $("#itemBody").innerHTML = "";
  (o.items || []).forEach((it) => addRow(it));
  if (!(o.items || []).length) addRow();
  recalcInbound();
  $("#inHint").textContent = _shipped
    ? `正在编辑已发货单 ${o.order_no}：只能改现有明细的内容（不能加/删行）；保存后请到出货单点【重推】把改动同步给门店`
    : `正在编辑 ${o.order_no}（保存即覆盖原单）`;
  $("#btnInSave").textContent = "保存修改";
  window.scrollTo(0, 0);
}

// ---------- 页2：工厂库存 ----------
const ST_LABEL = { in_stock: "在库", reserved: "待转移", transferred: "已转移" };
async function loadStock() {
  const q = $("#stQ").value.trim(), status = $("#stStatus").value;
  const { ok, data } = await api("GET", `/api/stock?q=${encodeURIComponent(q)}&status=${status}`);
  const tb = $("#stBody");
  if (!ok) return (tb.innerHTML = `<tr><td colspan="6" class="muted center">加载失败</td></tr>`);
  const s = data.summary;
  $("#stSummary").innerHTML =
    `在库 <b>${s.in_stock.count}</b> 件 / <b>${s.in_stock.weight}</b> g　·　` +
    `待转移 ${s.reserved.count} 件 / ${s.reserved.weight} g　·　` +
    `已转移 ${s.transferred.count} 件 / ${s.transferred.weight} g`;
  const rows = data.data || [];
  if (!rows.length) return (tb.innerHTML = `<tr><td colspan="6" class="muted center">没有匹配的货品</td></tr>`);
  // 按入库单折叠：一张入库单一行(汇总)，点击展开看明细——防几十件一次全铺开
  const groups = new Map();
  rows.forEach((it) => {
    const k = it.inbound_id ?? 0;
    if (!groups.has(k)) groups.set(k, { id: k, order_no: it.inbound_order_no || ("#" + k), items: [] });
    groups.get(k).items.push(it);
  });
  const arr = [...groups.values()].sort((a, b) => b.id - a.id);
  tb.innerHTML = arr.map((g) => {
    const cnt = g.items.reduce((n, it) => n + (it.piece_count ?? 1), 0);
    const wt = g.items.reduce((n, it) => n + (parseFloat(it.weight) || 0), 0);
    const byStatus = {};
    g.items.forEach((it) => (byStatus[it.status] = (byStatus[it.status] || 0) + 1));
    const statusHtml = Object.entries(byStatus)
      .map(([st, n]) => `<span class="badge ${st}">${ST_LABEL[st] || st} ${n}</span>`).join(" ");
    const nameSet = [...new Set(g.items.map((it) => it.product_name))];
    const names = nameSet.slice(0, 3).map(esc).join("、") + (nameSet.length > 3 ? "…" : "");
    const detRows = g.items.map((it) => `<tr>
      <td class="muted">#${it.id}</td><td class="mono">${esc(it.style_no) || "—"}</td><td>${esc(it.product_name)}</td>
      <td>${esc(it.fineness)}</td><td class="num">${esc(it.weight)}</td><td class="num">${esc(it.labor_cost)}</td>
      <td class="center">${it.piece_count ?? 1}</td><td>${esc(it.ring_size) || "—"}</td>
      <td><span class="badge ${it.status}">${ST_LABEL[it.status] || it.status}</span></td>
    </tr>`).join("");
    const detail = `<table class="list sub"><thead><tr><th>#</th><th>款号</th><th>品名</th><th>成色</th>`
      + `<th class="num">克重(g)</th><th class="num">克工费</th><th>件数</th><th>手寸</th><th>状态</th></tr></thead>`
      + `<tbody>${detRows}</tbody></table>`;
    return `<tr class="grp" data-g="${g.id}" style="cursor:pointer">
      <td class="center tgl">▸</td>
      <td class="mono">${esc(g.order_no)}</td>
      <td>${names} <span class="muted">(${g.items.length} 行)</span></td>
      <td class="center">${cnt}</td>
      <td class="num">${wt.toFixed(4)}</td>
      <td>${statusHtml}</td>
    </tr>
    <tr class="det" data-g="${g.id}" hidden><td colspan="6" style="padding:0 0 0 34px;background:#fafafa">${detail}</td></tr>`;
  }).join("");
  tb.querySelectorAll("tr.grp").forEach((r) => {
    r.onclick = () => {
      const g = r.dataset.g;
      const det = tb.querySelector(`tr.det[data-g="${g}"]`);
      const tgl = r.querySelector(".tgl");
      if (det) { det.hidden = !det.hidden; if (tgl) tgl.textContent = det.hidden ? "▸" : "▾"; }
    };
  });
}

// ---------- 页3：转移商品部 ----------
function recalcPick() {
  let cnt = 0, w = 0;
  $$("#trPickBody input.pick:checked").forEach((c) => { cnt++; w += parseFloat(c.dataset.w) || 0; });
  $("#trTotals").textContent = `已选 ${cnt} 件 / ${w.toFixed(4)} g`;
}
function syncGrpCheck(tb, g) {   // 按该收货单已勾件数更新组复选框:全勾=√、部分=半勾、都没勾=空
  const items = [...tb.querySelectorAll(`.pick[data-g="${g}"]`)];
  const gc = tb.querySelector(`.grp-check[data-g="${g}"]`);
  if (!gc) return;
  const n = items.filter((c) => c.checked).length;
  gc.checked = items.length > 0 && n === items.length;
  gc.indeterminate = n > 0 && n < items.length;
}
async function loadPick() {
  const { ok, data } = await api("GET", "/api/stock?status=available");
  const tb = $("#trPickBody");
  $("#trCheckAll").checked = false;
  if (!ok) return (tb.innerHTML = `<tr><td colspan="8" class="muted center">加载失败</td></tr>`);
  const rows = data.data || [];
  if (!rows.length) { tb.innerHTML = `<tr><td colspan="8" class="muted center">工厂库存没有在库货品，先去「产品入库」</td></tr>`; recalcPick(); return; }
  // 按收货单折叠(同「在手货」)：一张收货单一条汇总行,点开才逐件勾选,不再一件一行平铺
  const groups = new Map();
  rows.forEach((it) => {
    const k = it.inbound_id ?? 0;
    if (!groups.has(k)) groups.set(k, { id: k, order_no: it.inbound_order_no || ("#" + k), items: [] });
    groups.get(k).items.push(it);
  });
  const arr = [...groups.values()].sort((a, b) => b.id - a.id);
  tb.innerHTML = arr.map((g) => {
    const cnt = g.items.reduce((n, it) => n + (it.piece_count ?? 1), 0);
    const wt = g.items.reduce((n, it) => n + (parseFloat(it.weight) || 0), 0);
    const nameSet = [...new Set(g.items.map((it) => it.product_name))];
    const names = nameSet.slice(0, 2).map(esc).join("、") + (nameSet.length > 2 ? "…" : "");
    const finSet = [...new Set(g.items.map((it) => it.fineness))];
    const grp = `<tr class="grp" data-g="${g.id}" style="cursor:pointer">
      <td class="center"><input type="checkbox" class="grp-check" data-g="${g.id}" title="选中整张收货单" /></td>
      <td class="mono"><span class="tgl">▸</span> ${esc(g.order_no)}</td>
      <td>${names} <span class="muted">(${g.items.length} 件)</span></td>
      <td>${finSet.slice(0, 2).map(esc).join("、")}</td>
      <td class="num">${wt.toFixed(4)}</td>
      <td class="num muted">—</td>
      <td class="center">${cnt}</td>
      <td class="center muted">—</td>
    </tr>`;
    const dets = g.items.map((it) => `<tr class="det-item" data-g="${g.id}" hidden>
      <td class="center"><input type="checkbox" class="pick" value="${it.id}" data-w="${esc(it.weight)}" data-g="${g.id}" /></td>
      <td class="mono" style="padding-left:22px">${esc(it.style_no) || "—"}</td><td>${esc(it.product_name)}</td><td>${esc(it.fineness)}</td>
      <td class="num">${esc(it.weight)}</td><td class="num">${esc(it.labor_cost)}</td>
      <td class="center">${it.piece_count ?? 1}</td><td class="center">${esc(it.ring_size) || "—"}</td>
    </tr>`).join("");
    return grp + dets;
  }).join("");
  // 点汇总行展开/收起(点组复选框不触发)
  tb.querySelectorAll("tr.grp").forEach((r) => {
    r.onclick = (e) => {
      if (e.target.classList.contains("grp-check")) return;
      const g = r.dataset.g, tgl = r.querySelector(".tgl");
      let open = false;
      tb.querySelectorAll(`tr.det-item[data-g="${g}"]`).forEach((d) => { d.hidden = !d.hidden; open = !d.hidden; });
      if (tgl) tgl.textContent = open ? "▾" : "▸";
    };
  });
  // 组复选框=选中该收货单全部件
  tb.querySelectorAll(".grp-check").forEach((gc) => gc.addEventListener("change", () => {
    tb.querySelectorAll(`.pick[data-g="${gc.dataset.g}"]`).forEach((c) => (c.checked = gc.checked));
    recalcPick();
  }));
  // 逐件复选框:回头同步组复选框态
  tb.querySelectorAll(".pick").forEach((c) => c.addEventListener("change", () => { syncGrpCheck(tb, c.dataset.g); recalcPick(); }));
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
  const ids = $$("#trPickBody input.pick:checked").map((c) => parseInt(c.value, 10));
  if (!ids.length) return toast("先勾选要转移的货品", "err");
  const customerId = parseInt($("#trCustomer").value, 10);
  if (!customerId) return toast("请选择转移给哪个客户", "err");
  const { ok, data } = await api("POST", "/api/transfers",
    { customer_id: customerId, item_ids: ids, remark: $("#trRemark").value.trim() || null });
  if (!ok) return toast(errMsg(data, "创建失败"), "err");
  const d = data.data || {};
  // 挑中的多包货合并成【一张】出货单,返回 {count, transfers:[...]};兼容旧的单张返回
  const trs = d.transfers || (d.transfer_no ? [d] : []);
  const nItems = trs.reduce((s, t) => s + (t.item_count || 0), 0);
  const nCoded = trs.reduce((s, t) => s + (t.items || []).filter((it) => it.product_code).length, 0);
  const cn = (trs[0] || {}).customer_name || "";
  const _no = (trs[0] || {}).transfer_no || "";
  toast(`已合并成 1 张出货单 ${_no}（共 ${nItems} 件 → ${cn}）${nCoded ? `，已发码 ${nCoded} 件` : ""}，点「发货」推给门店（门店只入一次）`);
  $("#trRemark").value = "";
  loadPick(); loadTransfers();
}
// 一键把所有草稿出货单推送到门店（按入库单拆分后通常有多张）
async function pushAllDrafts() {
  const { ok, data } = await api("GET", "/api/transfers");
  if (!ok) return toast("加载失败", "err");
  const drafts = (data.data || []).filter((t) => t.status === "draft");
  if (!drafts.length) return toast("没有待发货的草稿出货单", "err");
  if (!confirm(`把 ${drafts.length} 张草稿出货单全部发货到门店？`)) return;
  const btn = $("#btnTrPushAll"); if (btn) btn.disabled = true;
  let good = 0, bad = 0;
  for (const t of drafts) {
    try {
      const r = await api("POST", `/api/transfers/${t.id}/push`);
      if (r.ok && r.data && r.data.success) good++; else bad++;
    } catch (e) { bad++; }
  }
  if (btn) btn.disabled = false;
  toast(`推送完成：成功 ${good} 张${bad ? `，失败 ${bad} 张` : ""}`, bad ? "err" : "ok");
  loadTransfers();
}
// 改门店：反确认解锁后可用；选新门店 → 改客户+按新门店重发一码一件码+退回草稿待重推
async function changeTransferStore(id) {
  const cr = await api("GET", "/api/customers");
  const custs = (cr.ok ? (cr.data.data || []) : []).filter((c) => c.enabled);
  if (!custs.length) return toast("没有可选门店", "err");
  const menu = custs.map((c, i) => `${i + 1}. ${c.name}`).join("\n");
  const pick = prompt(`改推到哪个门店？输入序号：\n${menu}`);
  if (pick == null) return;
  const idx = parseInt(pick, 10) - 1;
  if (isNaN(idx) || idx < 0 || idx >= custs.length) return toast("序号无效", "err");
  const { ok, data } = await api("PUT", `/api/transfers/${id}/customer`, { customer_id: custs[idx].id });
  if (!ok) return toast(errMsg(data, "改门店失败"), "err");
  toast(data.note || `已改到「${custs[idx].name}」，请点「发货」推送`, "ok");
  loadTransfers();
}
async function loadTransfers() {
  const { ok, data } = await api("GET", "/api/transfers");
  const tb = $("#trListBody");
  if (!ok) return (tb.innerHTML = `<tr><td colspan="8" class="muted center">加载失败</td></tr>`);
  const rows = data.data || [];
  if (!rows.length) return (tb.innerHTML = `<tr><td colspan="8" class="muted center">暂无转移单</td></tr>`);
  tb.innerHTML = rows.map((t) => {
    const acts = [
      `<button class="btn mini" data-act="print" data-id="${t.id}">🖨 打印</button>`,
      `<button class="btn mini" data-act="expsum" data-id="${t.id}" title="导出汇总表：按单/包分组，工费不同分行">⬇ 汇总</button>`,
      `<button class="btn mini" data-act="export" data-id="${t.id}" title="导出明细表：每件货一行">⬇ 明细</button>`,
    ];
    if (t.status === "draft") {
      acts.push(`<button class="btn mini" data-act="chstore" data-id="${t.id}">改门店</button>`);
      acts.push(`<button class="btn mini ship" data-act="push" data-id="${t.id}">发货</button>`);
      acts.push(`<button class="btn mini del" data-act="del" data-id="${t.id}">删除</button>`);
    } else {
      // 已发货/门店已收货：锁定态只给「反确认」；反确认后才出「改门店」+「确认」+「删除」
      if (t.status === "pushed") acts.push(`<button class="btn mini" data-act="push" data-id="${t.id}">重发</button>`);
      if (t.locked) {
        acts.push(`<button class="btn mini" data-act="unconfirm" data-id="${t.id}">反确认</button>`);
      } else {
        acts.push(`<button class="btn mini" data-act="chstore" data-id="${t.id}">改门店</button>`);
        acts.push(`<button class="btn mini ship" data-act="confirm" data-id="${t.id}">确认</button>`);
        acts.push(`<button class="btn mini del" data-act="delf" data-id="${t.id}">删除</button>`);
      }
    }
    return `<tr class="grp tr-row" data-id="${t.id}" style="cursor:pointer">
      <td class="center tgl">▸</td>
      <td class="mono">${esc(t.transfer_no)}</td><td>${(t.created_at || "").slice(0, 10)}</td>
      <td>${esc(t.customer_name) || "—"}</td>
      <td class="center">${t.item_count}</td><td class="num">${esc(t.total_weight)}</td>
      <td><span class="badge ${t.status}">${esc(t.status_label)}</span>${t.locked ? ' <span title="已确认锁定，须反确认才可改门店/删除">🔒</span>' : ''}</td>
      <td class="mono">${esc(t.store_order_no) || "—"}</td>
      <td class="acts">${acts.join("")}</td>
    </tr>
    <tr class="det" data-det="${t.id}" hidden><td colspan="9" style="padding:0 0 0 34px;background:#fafafa"><div class="tr-det muted" style="padding:8px">加载中…</div></td></tr>`;
  }).join("");
  tb.querySelectorAll("button[data-act]").forEach((b) => {
    const id = b.dataset.id;
    if (b.dataset.act === "print") b.onclick = () => printTransfer(id);
    if (b.dataset.act === "expsum") b.onclick = () => exportTransferSummary(id);
    if (b.dataset.act === "export") b.onclick = () => exportTransferDoc(id);
    if (b.dataset.act === "chstore") b.onclick = () => changeTransferStore(id);
    if (b.dataset.act === "del") b.onclick = async () => {
      if (!confirm("删除出货单？货将退回在手（在库）。")) return;
      const r = await api("DELETE", `/api/transfers/${id}`);
      r.ok ? (toast("已删除，货已退回在手"), loadPick(), loadTransfers()) : toast(errMsg(r.data, "删除失败"), "err");
    };
    if (b.dataset.act === "delf") b.onclick = async () => {
      if (!confirm("强制删除这张已解锁的出货单？\n货会退回工厂在手。\n（门店那边若已生成预入库单，需去门店另行删除）")) return;
      const r = await api("DELETE", `/api/transfers/${id}?force=true`);
      r.ok ? (toast("已删除，货已退回在手"), loadPick(), loadTransfers()) : toast(errMsg(r.data, "删除失败"), "err");
    };
    if (b.dataset.act === "confirm") b.onclick = async () => {
      const r = await api("POST", `/api/transfers/${id}/confirm`);
      r.ok ? (toast("已确认锁定（防误删；要改门店/删除先「反确认」）"), loadTransfers()) : toast(errMsg(r.data, "确认失败"), "err");
    };
    if (b.dataset.act === "unconfirm") b.onclick = async () => {
      if (!confirm("反确认这张出货单？\n反确认后即可改门店或删除（删除会把货退回工厂在手；门店那边的预入库单需另行处理）。")) return;
      const r = await api("POST", `/api/transfers/${id}/unconfirm`);
      r.ok ? (toast("已反确认，现在可改门店/删除"), loadTransfers()) : toast(errMsg(r.data, "反确认失败"), "err");
    };
    if (b.dataset.act === "push") b.onclick = async () => {
      b.disabled = true;
      const { ok, data } = await api("POST", `/api/transfers/${id}/push`);
      b.disabled = false;
      if (ok && data.success) {
        toast(`已发货 ✓ 门店预入库单 ${data.data.store_order_no || ""}`);
        $("#trHint").textContent = "";
      } else {
        toast("发货未成功", "err");
        $("#trHint").textContent = "发货未成功：" + (data.message || "");
      }
      loadTransfers();
    };
  });
  // 点行展开：显示这张出货单的每件明细（点操作按钮不触发）
  tb.querySelectorAll("tr.tr-row").forEach((r) => {
    r.onclick = async (e) => {
      if (e.target.closest(".acts")) return;
      const id = r.dataset.id;
      const det = tb.querySelector(`tr.det[data-det="${id}"]`);
      const tgl = r.querySelector(".tgl");
      if (!det) return;
      det.hidden = !det.hidden;
      if (tgl) tgl.textContent = det.hidden ? "▸" : "▾";
      if (!det.hidden && !det.dataset.loaded) {
        const box = det.querySelector(".tr-det");
        const res = await api("GET", `/api/transfers/${id}`);
        if (res.ok && res.data.data) {
          const its = res.data.data.items || [];
          box.innerHTML = _renderTransferDetailByBao(its);
          det.dataset.loaded = "1";
        } else { box.textContent = "加载失败"; }
      }
    };
  });
}

// ---------- 页4：出货记录（已出货的出货单台账；打印即出货） ----------
async function refreshRecvStatus() {
  const btn = $("#btnRefreshRecv");
  if (btn) { btn.disabled = true; btn.textContent = "刷新中…"; }
  const { ok, data } = await api("POST", "/api/transfers/refresh-status");
  if (btn) { btn.disabled = false; btn.textContent = "↻ 刷新收货状态"; }
  if (ok && data.success) {
    toast(`已核对 ${data.checked} 单：${data.updated} 单门店已收货` + (data.store_missing ? `，${data.store_missing} 单门店已删` : ""));
    loadShipRecords();
    if (typeof loadInbounds === "function") loadInbounds();
    if (typeof loadTransfers === "function") loadTransfers();
  } else {
    toast(errMsg(data, "刷新收货状态失败"), "err");
  }
}
let shipAllRows = [];
async function loadShipRecords() {
  const { ok, data } = await api("GET", "/api/transfers");
  const tb = $("#shiprecBody");
  if (!ok) return (tb.innerHTML = `<tr><td colspan="9" class="muted center">加载失败</td></tr>`);
  shipAllRows = (data.data || []).filter((t) => t.status === "pushed" || t.status === "confirmed");
  // 门店下拉去重填充（保留当前选择）
  const _sel = $("#shipFStore");
  if (_sel) {
    const _cur = _sel.value;
    const _stores = [...new Set(shipAllRows.map((t) => t.customer_name).filter(Boolean))].sort();
    _sel.innerHTML = `<option value="">全部门店</option>` + _stores.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join("");
    _sel.value = _cur;
  }
  renderShipRecords();
}
// 按 门店/日期/克重 筛选后渲染 + 合计（纯前端，即时）
function renderShipRecords() {
  const tb = $("#shiprecBody");
  if (!tb) return;
  const _store = ($("#shipFStore") || {}).value || "";
  const _from = ($("#shipFFrom") || {}).value || "";
  const _to = ($("#shipFTo") || {}).value || "";
  const _wmin = parseFloat(($("#shipFWmin") || {}).value);
  const _wmax = parseFloat(($("#shipFWmax") || {}).value);
  const _dOf = (t) => (t.pushed_at || t.created_at || "").slice(0, 10);
  const rows = shipAllRows.filter((t) => {
    if (_store && t.customer_name !== _store) return false;
    const d = _dOf(t);
    if (_from && d < _from) return false;
    if (_to && d > _to) return false;
    const w = parseFloat(t.total_weight) || 0;
    if (!isNaN(_wmin) && w < _wmin) return false;
    if (!isNaN(_wmax) && w > _wmax) return false;
    return true;
  });
  { const se = $("#shiprecSummary"); if (se) se.innerHTML = `共 <b>${rows.length}</b> 单 · <b>${rows.reduce((s, t) => s + (t.item_count || 0), 0)}</b> 件 · 克重合计 <b>${rows.reduce((s, t) => s + (parseFloat(t.total_weight) || 0), 0).toFixed(4)}</b> g`; }
  if (!rows.length) return (tb.innerHTML = `<tr><td colspan="9" class="muted center">无匹配记录</td></tr>`);
  tb.innerHTML = rows.map((t) => `<tr class="grp shiprec-row" data-id="${t.id}" style="cursor:pointer">
    <td class="center tgl">▸</td>
    <td class="mono">${esc(t.transfer_no)}</td><td>${(t.pushed_at || t.created_at || "").slice(0, 10)}</td>
    <td>${esc(t.customer_name) || "—"}</td>
    <td class="center">${t.item_count}</td><td class="num">${esc(t.total_weight)}</td>
    <td><span class="badge ${t.status}">${esc(t.status_label)}</span>${t.locked ? " 🔒" : ""}</td>
    <td class="mono">${esc(t.store_order_no) || "—"}</td>
    <td class="acts"><button class="btn mini" data-print="${t.id}">🖨 打印</button><button class="btn mini" data-expsum="${t.id}" title="导出汇总表：按单/包分组，工费不同分行">⬇ 汇总</button><button class="btn mini" data-exportd="${t.id}" title="导出明细表：每件货一行">⬇ 明细</button></td>
  </tr>
  <tr class="det" data-det="${t.id}" hidden><td colspan="9" style="padding:0 0 0 34px;background:#fafafa"><div class="ship-det muted" style="padding:8px">加载中…</div></td></tr>`).join("");
  tb.querySelectorAll("button[data-print]").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); printTransfer(b.dataset.print); }));
  tb.querySelectorAll("button[data-expsum]").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); exportTransferSummary(b.dataset.expsum); }));
  tb.querySelectorAll("button[data-exportd]").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); exportTransferDoc(b.dataset.exportd); }));
  tb.querySelectorAll("tr.shiprec-row").forEach((r) => {
    r.onclick = async () => {
      const id = r.dataset.id;
      const det = tb.querySelector(`tr.det[data-det="${id}"]`);
      const tgl = r.querySelector(".tgl");
      if (!det) return;
      det.hidden = !det.hidden;
      if (tgl) tgl.textContent = det.hidden ? "▸" : "▾";
      if (!det.hidden && !det.dataset.loaded) {
        const box = det.querySelector(".ship-det");
        const res = await api("GET", `/api/transfers/${id}`);
        if (res.ok && res.data.data) {
          const its = res.data.data.items || [];
          box.innerHTML = _renderTransferDetailByBao(its);
          det.dataset.loaded = "1";
        } else { box.textContent = "加载失败"; }
      }
    };
  });
}

// ---------- 出库单打印（针式 241mm 宽，横向直排不旋转，同 fblerp 针式标准） ----------
// 打印编号 = YYMMDD + 当日序号(3位)，从单据号(FRK/ZY-YYYYMMDD-NNN)推出
function _deliveryNo(docNo) {
  const s = docNo || "";
  const dm = s.match(/(\d{4})(\d{2})(\d{2})/);       // 首个 8 位日期
  const sm = s.match(/(\d+)\s*$/);                    // 末尾序号
  return dm ? (dm[1].slice(2) + dm[2] + dm[3] + (sm ? sm[1].padStart(3, "0") : "")) : s;
}
function _fmtDT(iso) {
  if (!iso) return "";
  const d = new Date(iso), p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}/${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function _openPrint(html) {
  const w = window.open("", "_blank", "width=1040,height=720");
  if (!w) return toast("请允许弹出窗口后再打印", "err");
  w.document.write(html); w.document.close(); w.focus();
}
// 通用出库单文档：opts={no,dateTime,items,defRecv,operator}
function _deliveryDoc(opts) {
  const items = opts.items || [];
  let sumQ = 0, sumW = 0, sumA = 0;
  const body = items.map((it, i) => {
    const w = parseFloat(it.weight) || 0;
    const lc = parseFloat(it.labor_cost) || 0;
    const pc = parseInt(it.piece_count, 10) || 1;
    const plc = parseFloat(it.piece_labor_cost) || 0;
    const amt = w * lc + pc * plc;                   // 金额 = 重量×工费 + 件数×附加费（同 fblerp total_cost 口径）
    sumQ += pc; sumW += w; sumA += amt;
    return `<tr><td>${i + 1}</td><td class="l">${esc(it.style_no)}</td><td class="l">${esc(it.product_name)}</td>`
      + `<td>${pc}</td><td class="r">${w.toFixed(3)}</td><td class="r">${lc || ""}</td>`
      + `<td class="r">${plc || ""}</td><td class="r">${amt.toFixed(2)}</td></tr>`;
  }).join("");
  const defRecv = esc(opts.defRecv || "");
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>出库单 ${opts.no}</title>
<style>
  @page { size: 241mm auto; margin: 0; }   /* 针式 241mm 宽 × 自动高，横向直排、不旋转 */
  * { box-sizing: border-box; }
  body { font-family:"SimHei","黑体","Microsoft YaHei",sans-serif; color:#000; margin:0; font-size:14px; font-weight:bold; }  /* 针式打印:黑体+加粗+14px,笔画粗打出来更清晰(宋体细易发虚) */
  .page { width:241mm; padding:5mm 10mm; margin:0 auto; }   /* 屏幕预览 = 打印，241mm 横向 */
  .bar { text-align:center; margin-bottom:8px; }
  .bar button { padding:6px 20px; margin:0 6px; font-size:14px; cursor:pointer; }
  h1 { text-align:center; font-size:25px; margin:0 0 8px; letter-spacing:6px; }
  .hd { display:flex; justify-content:space-between; align-items:flex-end; margin-bottom:4px; }
  .hd .no { text-align:right; font-size:13px; line-height:1.6; white-space:nowrap; }
  #recv { border:none; border-bottom:1px solid #000; font-size:14px; width:260px; font-family:inherit; }
  table { width:100%; border-collapse:collapse; table-layout:fixed; }
  th,td { border:1px solid #000; padding:5px 5px; font-size:14px; text-align:center; overflow:hidden; }
  td.l { text-align:left; } td.r { text-align:right; }
  tfoot td { font-weight:bold; }
  .sign { display:flex; justify-content:space-between; margin-top:14px; font-size:14px; }
  @media print {
    .bar { display:none; }
    html, body { margin:0; }
    .page { width:241mm; padding:5mm 10mm; margin:0; }   /* 241mm 直排，无旋转 */
  }
</style></head><body>
<div class="bar">
  <button onclick="localStorage.setItem('fh_receiver',document.getElementById('recv').value);window.print()">🖨 打印</button>
  <button onclick="window.close()">关闭</button>
</div>
<div class="page">
<h1>梵贝琳出库单</h1>
<div class="hd">
  <div>收货单位：<input id="recv" value="${defRecv}" placeholder="工厂自填，如 梵贝琳展厅（林源）"></div>
  <div class="no">NO：${opts.no}<br>${opts.dateTime || ""}</div>
</div>
<table>
  <colgroup><col style="width:6%"><col style="width:34%"><col style="width:14%"><col style="width:6%"><col style="width:11%"><col style="width:8%"><col style="width:9%"><col style="width:12%"></colgroup>
  <thead><tr><th>序号</th><th>单号</th><th>名称</th><th>数量</th><th>重量</th><th>工费</th><th>附加费</th><th>金额(元)</th></tr></thead>
  <tbody>${body}</tbody>
  <tfoot><tr><td colspan="3">合计</td><td>${sumQ}</td><td class="r">${sumW.toFixed(3)}</td><td></td><td></td><td class="r">${sumA.toFixed(2)}</td></tr></tfoot>
</table>
<div class="sign"><span>制单：梵贝琳收发</span><span>复核：</span><span>送货：</span><span>收货：</span></div>
</div>
</body></html>`;
}
// 出货单【汇总版】打印：按"包"(来源收货单)汇总,一包一行,版式照梵贝琳出库单;逐件明细走「导出」的表格。
function _transferSummaryDoc(t) {
  const { rows, baoCount } = _groupByBao(t.items);   // 同包不同工费拆行;打印与「导出汇总」共用同一分组口径
  let sumP = 0, sumW = 0, sumA = 0;
  const body = rows.map((g) => {
    sumP += g.pcs; sumW += g.w; sumA += g.amt;
    // 序号列按"单/包"合并单元格(rowspan),单号每行都印;同一单里每个品名/工费一行
    return `<tr>${g.span ? `<td rowspan="${g.span}">${g.baoIdx}</td>` : ""}<td class="l">${esc(g.no)}</td><td class="l">${esc(g.name)}</td>`
      + `<td>${g.pcs}</td><td class="r">${g.w.toFixed(2)}</td><td class="r">${g.fee}</td><td class="r">${g.addl}</td><td class="r">${g.amt.toFixed(2)}</td></tr>`;
  }).join("");
  const defRecv = esc(localStorage.getItem("fh_receiver") || t.customer_name || "");
  const no = _deliveryNo(t.transfer_no), dt = _fmtDT(t.created_at);
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>出货单 ${no}</title>
<style>
  @page { size: 241mm auto; margin: 0; }
  * { box-sizing: border-box; }
  body { font-family:"SimHei","黑体","Microsoft YaHei",sans-serif; color:#000; margin:0; font-size:14px; font-weight:bold; }
  .page { width:241mm; padding:5mm 10mm; margin:0 auto; }
  .bar { text-align:center; margin-bottom:8px; } .bar button { padding:6px 20px; margin:0 6px; font-size:14px; cursor:pointer; }
  h1 { text-align:center; font-size:25px; margin:0 0 8px; letter-spacing:6px; }
  .hd { display:flex; justify-content:space-between; align-items:flex-end; margin-bottom:4px; }
  .hd .no { text-align:right; font-size:13px; line-height:1.6; white-space:nowrap; }
  #recv { border:none; border-bottom:1px solid #000; font-size:14px; width:260px; font-family:inherit; font-weight:bold; }
  table { width:100%; border-collapse:collapse; table-layout:fixed; }
  th,td { border:1px solid #000; padding:6px 5px; font-size:14px; text-align:center; overflow:hidden; word-break:break-all; }
  thead th { background:#d9ead3; }
  td.l { text-align:left; } td.r { text-align:right; }
  tfoot td { font-weight:bold; background:#f0f7ec; }
  .note { margin-top:6px; font-size:12px; }
  .sign { display:flex; justify-content:space-between; margin-top:14px; font-size:14px; }
  @media print { .bar { display:none; } html, body { margin:0; } .page { width:241mm; padding:5mm 10mm; margin:0; } thead th{ -webkit-print-color-adjust:exact; print-color-adjust:exact; } }
</style></head><body>
<div class="bar">
  <button onclick="localStorage.setItem('fh_receiver',document.getElementById('recv').value);window.print()">🖨 打印</button>
  <button onclick="window.close()">关闭</button>
</div>
<div class="page">
<h1>梵贝琳出货单</h1>
<div class="hd">
  <div>发往门店：<input id="recv" value="${defRecv}" placeholder="门店名"></div>
  <div class="no">NO：${no}<br>${dt || ""}</div>
</div>
<table>
  <colgroup><col style="width:6%"><col style="width:26%"><col style="width:22%"><col style="width:8%"><col style="width:13%"><col style="width:8%"><col style="width:8%"><col style="width:11%"></colgroup>
  <thead><tr><th>序号</th><th>单号</th><th>名称</th><th>数量</th><th>重量(g)</th><th>工费</th><th>附加费</th><th>金额(元)</th></tr></thead>
  <tbody>${body}</tbody>
  <tfoot><tr><td colspan="3" class="l">合计（共 ${baoCount} 单）</td><td>${sumP}</td><td class="r">${sumW.toFixed(2)}</td><td></td><td></td><td class="r">${sumA.toFixed(2)}</td></tr></tfoot>
</table>
<div class="note">＊本单为汇总（同一单里品名/工费不同的货分行列示，工费为每克单价）；每件货的逐件明细见「导出」的表格。</div>
<div class="sign"><span>制单：梵贝琳收发</span><span>复核：</span><span>送货：</span><span>收货：</span></div>
</div>
</body></html>`;
}
// 数字去掉多余小数尾零（2.80→2.8, 3.00→3, 2.85→2.85）——工费单价列显示用
function _numTrim(x) {
  const n = parseFloat(x);
  if (!isFinite(n) || n === 0) return "";
  return String(parseFloat(n.toFixed(4)));
}
// 出货单按"包"(来源收货单)分组汇总——打印汇总单与「导出汇总」共用,保证两处口径一致。
// 同一包里品名/工费不同的产品拆成多行,每行标各自工费(工费全都体现,不再因"整包不一致"留空)。
// 返回 { rows: [{no:收货单号, name:品名, pcs, w, amt, fee:克工费, addl:件工费, span:该包第一行=包内行数(供rowspan)其余=0, baoIdx:第几单}], baoCount:共几单 }
function _groupByBao(items) {
  const baoOrder = [], baos = {};
  for (const it of (items || [])) {
    const baoKey = it.inbound_order_no || ("包#" + (it.inbound_id || "?"));
    if (!baos[baoKey]) { baos[baoKey] = { subOrder: [], subs: {} }; baoOrder.push(baoKey); }
    const b = baos[baoKey];
    const nm = (it.product_name || "").trim();
    const w = parseFloat(it.weight) || 0, lc = parseFloat(it.labor_cost) || 0;
    const pc = parseInt(it.piece_count, 10) || 1, plc = parseFloat(it.piece_labor_cost) || 0;
    const subKey = nm + "|" + lc + "|" + plc;
    if (!b.subs[subKey]) { b.subs[subKey] = { name: nm, pcs: 0, w: 0, amt: 0, lc, plc }; b.subOrder.push(subKey); }
    const s = b.subs[subKey];
    s.pcs += pc; s.w += w; s.amt += w * lc + pc * plc;
  }
  const rows = [];
  baoOrder.forEach((baoKey, bi) => {
    const b = baos[baoKey];
    b.subOrder.forEach((subKey, i) => {
      const s = b.subs[subKey];
      rows.push({
        no: baoKey, name: s.name, pcs: s.pcs, w: s.w, amt: s.amt,
        fee: _numTrim(s.lc), addl: s.plc > 0 ? _numTrim(s.plc) : "",
        span: i === 0 ? b.subOrder.length : 0, baoIdx: bi + 1,
      });
    });
  });
  return { rows, baoCount: baoOrder.length };
}
// 导出单张出货单的【汇总表】（一行=一个单/一包），美化 Excel——与打印汇总单同口径
async function exportTransferSummary(id) {
  const { ok, data } = await api("GET", `/api/transfers/${id}`);
  if (!ok || !data.data) return toast("加载失败", "err");
  const t = data.data, { rows: grpRows, baoCount } = _groupByBao(t.items);
  const cols = [{ h: "序号", cls: "c" }, { h: "单号", cls: "" }, { h: "名称", cls: "" },
    { h: "数量", cls: "c" }, { h: "重量(g)", cls: "num" }, { h: "工费", cls: "num" }, { h: "附加费", cls: "num" }, { h: "金额(元)", cls: "num" }];
  let sp = 0, sw = 0, sa = 0;
  const rows = grpRows.map((g) => { sp += g.pcs; sw += g.w; sa += g.amt;
    return [g.baoIdx, g.no, g.name, g.pcs, g.w.toFixed(2), g.fee, g.addl, g.amt.toFixed(2)]; });  // 序号=第几单;同单不同工费拆多行
  const html = buildDocXls({
    title: "梵贝琳出货单·汇总",
    info: [["出货单号", t.transfer_no], ["门店", t.customer_name || ""], ["日期", (t.created_at || "").slice(0, 10)], ["门店单号", t.store_order_no || ""]],
    cols, rows, totalRow: ["", `合计（共 ${baoCount} 单）`, "", sp, sw.toFixed(2), "", "", sa.toFixed(2)],
  });
  downloadXls(html, `出货单汇总_${t.transfer_no}.xls`);
  toast("已导出出货单汇总");
}
// 出货单展开明细：按"包"(来源收货单)分组——合并单点开能看到它是由哪几张收货单(FRK…)合成的，每包下列逐件货
function _renderTransferDetailByBao(items) {
  const order = [], groups = {};
  for (const it of (items || [])) {
    const key = it.inbound_order_no || ("包#" + (it.inbound_id || "?"));
    if (!groups[key]) { groups[key] = []; order.push(key); }
    groups[key].push(it);
  }
  const hasCode = (items || []).some((it) => it.product_code);   // 整批都没码 → 编码列整列隐藏(不发码门店由门店自发)
  const nCols = hasCode ? 8 : 7;
  let html = `<table class="list sub"><thead><tr>${hasCode ? "<th>编码</th>" : ""}<th>款号</th><th>品名</th><th>成色</th>`
    + `<th class="num">克重(g)</th><th class="num">克工费</th><th>件数</th><th>手寸</th></tr></thead><tbody>`;
  for (const key of order) {
    const its = groups[key];
    const pcs = its.reduce((n, it) => n + (it.piece_count ?? 1), 0);
    const w = its.reduce((n, it) => n + (parseFloat(it.weight) || 0), 0);
    const hdr = order.length > 1 ? `📦 收货单 ${esc(key)}　·　${pcs} 件　·　${w.toFixed(2)} g（合并的其中一单）`
      : `📦 收货单 ${esc(key)}　·　${pcs} 件　·　${w.toFixed(2)} g`;
    html += `<tr style="background:#e8f2e0"><td colspan="${nCols}" style="font-weight:bold">${hdr}</td></tr>`;
    html += its.map((it) => `<tr>${hasCode ? `<td class="mono">${esc(it.product_code) || "—"}</td>` : ""}<td class="mono">${esc(it.style_no) || "—"}</td><td>${esc(it.product_name)}</td>`
      + `<td>${esc(it.fineness)}</td><td class="num">${esc(it.weight)}</td><td class="num">${esc(it.labor_cost)}</td>`
      + `<td class="center">${it.piece_count ?? 1}</td><td>${esc(it.ring_size) || "—"}</td></tr>`).join("");
  }
  return html + `</tbody></table>`;
}
async function printTransfer(id) {
  const { ok, data } = await api("GET", `/api/transfers/${id}`);
  if (!ok) return toast(errMsg(data, "加载失败"), "err");
  _openPrint(_transferSummaryDoc(data.data));
}
// 按入库单打印出库单：一整张入库单的货 → 一张出库单
async function printInbound(id) {
  const { ok, data } = await api("GET", `/api/inbounds/${id}`);
  if (!ok) return toast(errMsg(data, "加载失败"), "err");
  const o = data.data;
  _openPrint(_deliveryDoc({
    no: _deliveryNo(o.order_no), dateTime: _fmtDT(o.created_at), items: o.items,
    defRecv: o.receiver || "", operator: o.operator || currentUser,   // 收货单位=本收货单存的备注(打印预览框仍可临时改)
  }));
}

// ---------- 页4：客户管理（仅管理员） ----------
async function loadCustomersPage() {
  const { ok, data } = await api("GET", "/api/customers");
  const tb = $("#cuListBody");
  if (!ok) return (tb.innerHTML = `<tr><td colspan="7" class="muted center">加载失败</td></tr>`);
  const rows = data.data || [];
  if (!rows.length) return (tb.innerHTML = `<tr><td colspan="7" class="muted center">暂无客户，用上方表单添加</td></tr>`);
  tb.innerHTML = rows.map((c) => `<tr>
    <td><b>${esc(c.name)}</b></td><td class="mono">${esc(c.store_base_url)}</td>
    <td>${esc(c.supplier_name)}</td>
    <td>${c.code_prefix ? `<span class="badge confirmed">${esc(c.code_prefix)}</span>` : '<span class="muted">不发码</span>'}</td>
    <td>${c.key_configured ? '<span class="badge confirmed">已配置</span>' : '<span class="badge reserved">未配置</span>'}</td>
    <td>${c.enabled ? '<span class="badge in_stock">启用</span>' : '<span class="badge draft">停用</span>'}</td>
    <td class="acts">
      <button class="btn mini" data-act="prefix" data-id="${c.id}" data-cur="${esc(c.code_prefix)}">发码前缀</button>
      <button class="btn mini" data-act="key" data-id="${c.id}">${c.key_configured ? "换Key" : "填Key"}</button>
      <button class="btn mini" data-act="toggle" data-id="${c.id}" data-en="${c.enabled ? 0 : 1}">${c.enabled ? "停用" : "启用"}</button>
      <button class="btn mini del" data-act="del" data-id="${c.id}">删除</button>
    </td>
  </tr>`).join("");
  tb.querySelectorAll("button[data-act]").forEach((b) => {
    const id = b.dataset.id;
    if (b.dataset.act === "prefix") b.onclick = async () => {
      const val = prompt("设该门店的工厂发码前缀：\nJD 填 TF、fblerp 填 FF、留空=不发码\n（发货时按此前缀给一码一件货发码）", b.dataset.cur || "");
      if (val === null) return;                 // 取消
      const p = val.trim().toUpperCase();
      const r = await api("PUT", `/api/customers/${id}`, { code_prefix: p });   // 空串=清除前缀
      r.ok ? (toast(p ? `发码前缀设为 ${p}` : "已清除发码前缀"), loadCustomersPage()) : toast(errMsg(r.data, "设置失败"), "err");
    };
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
    code_prefix: $("#cuPrefix").value || null,
    remark: $("#cuRemark").value.trim() || null,
  };
  if (!payload.name || !payload.store_base_url || !payload.supplier_name)
    return ($("#cuHint").textContent = "客户名、对方系统地址、本厂供应商名 都必填");
  const { ok, data } = await api("POST", "/api/customers", payload);
  if (!ok) return ($("#cuHint").textContent = errMsg(data, "添加失败"));
  $("#cuHint").textContent = "";
  ["cuName", "cuUrl", "cuSupplier", "cuKey", "cuRemark"].forEach((i) => ($("#" + i).value = ""));
  $("#cuPrefix").value = "";
  toast(`客户「${payload.name}」已添加`);
  loadCustomersPage();
}

// ---------- 款式 ----------
async function loadStyles() {
  styleMap = {};
  const opts = [];
  const r1 = await api("GET", "/api/styles");
  if (r1.ok) (r1.data.data || []).forEach((s) => { styleMap[s.style_no] = s; opts.push(`<option value="${esc(s.style_no)}">${esc(s.name)} ${esc(s.fineness)}</option>`); });
  const r2 = await api("GET", "/api/style-book");
  if (r2.ok) (r2.data.data || []).forEach((s) => { if (!styleMap[s.style_no]) { styleMap[s.style_no] = s; opts.push(`<option value="${esc(s.style_no)}">${esc(s.name)} ${esc(s.fineness)}</option>`); } });
  $("#styleList").innerHTML = opts.join("");
}
// 品名联想:电子板房/门店款品名 ∪ 录过的历史品名(stock_items)
async function loadNames() {
  const seen = new Set(), out = [];
  Object.values(styleMap || {}).forEach((s) => { if (s && s.name && !seen.has(s.name)) { seen.add(s.name); out.push(s.name); } });
  const r = await api("GET", "/api/inbounds/product-names");
  if (r.ok) (r.data.data || []).forEach((n) => { if (n && !seen.has(n)) { seen.add(n); out.push(n); } });
  const dl = $("#nameList");
  if (dl) dl.innerHTML = out.map((n) => `<option value="${esc(n)}"></option>`).join("");
}
function addNameToList(name) {   // 本次录入的新品名即时进联想(后端提交后也会持久化)
  if (!name) return;
  const dl = $("#nameList"); if (!dl) return;
  if ([...dl.options].some((o) => o.value === name)) return;
  const o = document.createElement("option"); o.value = name; dl.appendChild(o);
}
// 已改固定列宽(table-layout:fixed,列宽由表头 th 定),不再自适应。此函数只清掉可能残留的 inline width。
function fitInput(inp) {
  if (inp) inp.style.width = "";
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
  currentUser = me.username || "";
  $("#appLayout").hidden = false;
  $("#brandSub").textContent = me.supplier_name || "工厂端";
  $("#whoami").textContent = `👤 ${me.username}`;
  $("#btnAddUser").hidden = !me.is_admin;
  $("#navCustomers").hidden = !me.is_admin;
  $("#btnSync").hidden = true;   // 同步款式已弃用(工厂用自有电子板房),永久隐藏
  const cu = await api("GET", "/api/customers");
  if (cu.ok) {
    const rows = (cu.data.data || []).filter((c) => c.enabled);
    const on = rows.filter((c) => c.key_configured).length;
    $("#storeStatus").innerHTML = rows.length
      ? `客户 ${rows.length} 家 · <span class="dot ${on ? "ok" : "warn"}"></span>${on} 家已接通`
      : `<span class="dot warn"></span>尚未添加客户`;
  }
  await loadStyles();
  await loadNames();
  resetInbound();
  switchPage("inbound");
}
// ---------- 电子板房（工厂自有款号资料库 + 以图搜款） ----------
let editingStyleId = null;
let sbUploadUrl = "";
async function loadStylebook() {
  const q = $("#sbQ") ? $("#sbQ").value.trim() : "";
  const { ok, data } = await api("GET", `/api/style-book?q=${encodeURIComponent(q)}`);
  const grid = $("#sbGrid");
  if (!ok) return (grid.innerHTML = `<div class="muted center" style="padding:24px">加载失败</div>`);
  const rows = data.data || [];
  $("#sbSummary").innerHTML = `共 <b>${rows.length}</b> 个款号　·　以图搜款 ${data.model_ready ? "可用" : "未就绪"}`;
  if (!rows.length) return (grid.innerHTML = `<div class="muted center" style="padding:30px">还没有款号，点右上「＋ 新增款号」建第一个</div>`);
  renderStyleCards(rows, false);
}
function renderStyleCards(rows, withSim) {
  const grid = $("#sbGrid");
  grid.innerHTML = rows.map((s) => `<div class="style-card" data-id="${s.id}">
    <div class="sc-img">${s.main_image ? `<img src="${esc(s.main_image)}" loading="lazy" />` : `<span class="sc-noimg">无图</span>`}${withSim && s.similarity != null ? `<span class="sc-sim">${(s.similarity * 100).toFixed(0)}%</span>` : ""}</div>
    <div class="sc-body">
      <div class="sc-no mono">${esc(s.style_no)}</div>
      <div class="sc-name">${esc(s.name) || "—"}</div>
      <div class="sc-meta">${esc(s.fineness) || ""}${s.ref_weight ? " · " + esc(s.ref_weight) + "g" : ""}${s.labor_rate ? " · 工费" + esc(s.labor_rate) : ""}</div>
    </div>
  </div>`).join("");
  grid.querySelectorAll(".style-card").forEach((c) => (c.onclick = () => openStyleModal(rows.find((r) => String(r.id) === c.dataset.id))));
}
function openStyleModal(style) {
  editingStyleId = style ? style.id : null;
  sbUploadUrl = style ? (style.main_image || "") : "";
  $("#styleModalTitle").textContent = style ? "编辑款号" : "新增款号";
  const g = (id, v) => ($(id).value = v || "");
  g("#sfStyleNo", style && style.style_no); g("#sfName", style && style.name);
  g("#sfCategory", style && style.category); g("#sfFineness", style && style.fineness);
  g("#sfRefWeight", style && style.ref_weight); g("#sfLaborRate", style && style.labor_rate);
  g("#sfExtraFee", style && style.extra_fee); g("#sfRemark", style && style.remark);
  const img = $("#styleImgPreview");
  img.src = sbUploadUrl || ""; img.style.display = sbUploadUrl ? "block" : "none";
  $("#btnStyleDelete").hidden = !style;
  $("#styleModal").hidden = false;
}
function closeStyleModal() { $("#styleModal").hidden = true; editingStyleId = null; sbUploadUrl = ""; }
async function uploadStyleImage(file) {
  const fd = new FormData(); fd.append("file", file);
  const tk = localStorage.getItem("fh_token");
  const r = await fetch("/api/style-book/upload", { method: "POST", headers: tk ? { Authorization: "Bearer " + tk } : {}, body: fd });
  const data = await r.json().catch(() => ({}));
  if (!r.ok || !data.url) return toast(errMsg(data, "上传失败"), "err");
  sbUploadUrl = data.url;
  const img = $("#styleImgPreview"); img.src = data.url; img.style.display = "block";
  toast("图片已上传");
}
async function saveStyle() {
  const styleNo = $("#sfStyleNo").value.trim();
  if (!styleNo) return toast("款号必填", "err");
  const val = (id) => $(id).value.trim() || null;
  const payload = { style_no: styleNo, name: val("#sfName"), category: val("#sfCategory"),
    fineness: val("#sfFineness"), ref_weight: val("#sfRefWeight"), labor_rate: val("#sfLaborRate"),
    extra_fee: val("#sfExtraFee"), remark: val("#sfRemark"), main_image: sbUploadUrl || null };
  const { ok, data } = editingStyleId
    ? await api("PUT", `/api/style-book/${editingStyleId}`, payload)
    : await api("POST", "/api/style-book", payload);
  if (!ok) return toast(errMsg(data, "保存失败"), "err");
  toast("已保存 ✓");
  closeStyleModal(); loadStylebook(); loadStyles();
}
async function deleteStyle() {
  if (!editingStyleId) return;
  if (!confirm("删除这个款号？")) return;
  const r = await api("DELETE", `/api/style-book/${editingStyleId}`);
  r.ok ? (toast("已删除"), closeStyleModal(), loadStylebook(), loadStyles()) : toast(errMsg(r.data, "删除失败"), "err");
}
async function searchByImage(file) {
  const fd = new FormData(); fd.append("file", file); fd.append("top_n", "12");
  const tk = localStorage.getItem("fh_token");
  toast("识别中…");
  const r = await fetch("/api/style-book/search-by-image", { method: "POST", headers: tk ? { Authorization: "Bearer " + tk } : {}, body: fd });
  const data = await r.json().catch(() => ({}));
  if (!r.ok || !data.success) return toast(errMsg(data, "以图搜款失败"), "err");
  const rows = data.data || [];
  $("#sbSummary").innerHTML = `以图搜款：找到 <b>${rows.length}</b> 个相似款（从 ${data.candidates || 0} 个带图款号里比）· <a href="#" id="sbBackAll">返回全部</a>`;
  const back = $("#sbBackAll"); if (back) back.onclick = (e) => { e.preventDefault(); loadStylebook(); };
  if (!rows.length) return ($("#sbGrid").innerHTML = `<div class="muted center" style="padding:30px">没找到相似款（款号要先上传主图，才能被以图搜到）</div>`);
  renderStyleCards(rows, true);
}

// ---------- 回收站（软删单据保留30天可恢复，到期自动真删） ----------
async function loadRecycle() {
  const { ok, data } = await api("GET", "/api/recycle-bin");
  const tb = $("#recycleBody");
  if (!ok) return (tb.innerHTML = `<tr><td colspan="6" class="muted center">加载失败</td></tr>`);
  const rows = data.data || [];
  $("#recycleHint").innerHTML = `删除的单据在这里保留 <b>${data.retention_days || 30}</b> 天，可「恢复」；到期自动彻底删除。出货单彻底删后货会退回在手库存。`;
  if (!rows.length) return (tb.innerHTML = `<tr><td colspan="6" class="muted center" style="padding:26px">回收站是空的</td></tr>`);
  tb.innerHTML = rows.map((r) => `<tr>
    <td><span class="badge ${r.type === "inbound" ? "in_stock" : "pushed"}">${esc(r.type_label)}</span></td>
    <td class="mono">${esc(r.order_no)}</td>
    <td>${esc(r.customer_name) || "—"}</td>
    <td class="center">${r.item_count} 件 / ${esc(r.total_weight)}g</td>
    <td>${(r.deleted_at || "").slice(0, 16).replace("T", " ")} <span class="muted" style="font-size:11px">· 剩 ${r.days_left} 天</span></td>
    <td class="acts">
      <button class="btn mini ship" data-act="restore" data-type="${r.type}" data-id="${r.id}">恢复</button>
      <button class="btn mini del" data-act="purge" data-type="${r.type}" data-id="${r.id}">彻底删</button>
    </td>
  </tr>`).join("");
  tb.querySelectorAll("button[data-act]").forEach((b) => {
    b.onclick = async () => {
      const { act, type, id } = b.dataset;
      if (act === "restore") {
        const r = await api("POST", `/api/recycle-bin/restore/${type}/${id}`);
        r.ok ? (toast(r.data.message || "已恢复"), loadRecycle()) : toast(errMsg(r.data, "恢复失败"), "err");
      } else {
        if (!confirm("立即彻底删除这张单？不可恢复！\n（出货单彻底删后，货会退回在手库存）")) return;
        const r = await api("DELETE", `/api/recycle-bin/purge/${type}/${id}`);
        r.ok ? (toast("已彻底删除"), loadRecycle()) : toast(errMsg(r.data, "删除失败"), "err");
      }
    };
  });
}

function bindStatic() {
  $$(".nav-item").forEach((b) => (b.onclick = () => switchPage(b.dataset.page)));
  const _sb = (id, fn) => { const el = $(id); if (el) el.onclick = fn; };
  _sb("#btnStyleNew", () => openStyleModal());
  _sb("#btnStyleSearch", () => loadStylebook());
  const _sbq = $("#sbQ"); if (_sbq) _sbq.addEventListener("keydown", (e) => { if (e.key === "Enter") loadStylebook(); });
  _sb("#btnStyleImgSearch", () => $("#sbImgInput").click());
  const _sbi = $("#sbImgInput"); if (_sbi) _sbi.addEventListener("change", (e) => { if (e.target.files[0]) searchByImage(e.target.files[0]); e.target.value = ""; });
  _sb("#btnStyleSave", saveStyle);
  _sb("#btnStyleCancel", closeStyleModal); _sb("#btnStyleCancel2", closeStyleModal);
  _sb("#btnStyleDelete", deleteStyle);
  _sb("#btnStyleUpload", () => $("#styleImgFile").click());
  const _sif = $("#styleImgFile"); if (_sif) _sif.addEventListener("change", (e) => { if (e.target.files[0]) uploadStyleImage(e.target.files[0]); e.target.value = ""; });
  const _sm = $("#styleModal"); if (_sm) _sm.addEventListener("click", (e) => { if (e.target.id === "styleModal") closeStyleModal(); });
  $("#addRowCount").value = localStorage.getItem("fh_addrows") || "1";
  $("#btnAddRow").onclick = addRows;
  $("#btnInReset").onclick = resetInbound;
  $("#btnInSave").onclick = saveInbound;
  $("#btnStSearch").onclick = loadStock;
  $("#stQ").addEventListener("keydown", (e) => { if (e.key === "Enter") loadStock(); });
  const shipReload = $("#btnShipReload"); if (shipReload) shipReload.onclick = loadShipRecords;
  const _rr = $("#btnRefreshRecv"); if (_rr) _rr.onclick = refreshRecvStatus;
  ["shipFStore", "shipFFrom", "shipFTo", "shipFWmin", "shipFWmax"].forEach((id) => { const el = $("#" + id); if (el) el.addEventListener("input", () => (typeof renderShipRecords === "function") && renderShipRecords()); });
  { const rb = $("#shipFReset"); if (rb) rb.onclick = () => { ["shipFStore", "shipFFrom", "shipFTo", "shipFWmin", "shipFWmax"].forEach((id) => { const el = $("#" + id); if (el) el.value = ""; }); renderShipRecords(); }; }
  ["recvFStatus", "recvFQ", "recvFOperator", "recvFFrom", "recvFTo"].forEach((id) => { const el = $("#" + id); if (el) el.addEventListener("input", () => (typeof renderInbounds === "function") && renderInbounds()); });
  { const rb = $("#recvFReset"); if (rb) rb.onclick = () => { ["recvFStatus", "recvFQ", "recvFOperator", "recvFFrom", "recvFTo"].forEach((id) => { const el = $("#" + id); if (el) el.value = ""; }); renderInbounds(); }; }
  // 今天/本周/本月 快捷日期（设 recvFFrom~recvFTo 后重渲染）
  { const _ymd = (d) => { const p = (n) => String(n).padStart(2, "0"); return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`; };
    const _setR = (from, to) => { const f = $("#recvFFrom"), t = $("#recvFTo"); if (f) f.value = from; if (t) t.value = to; renderInbounds(); };
    const _qt = $("#recvQToday"); if (_qt) _qt.onclick = () => _setR(todayStr(), todayStr());
    const _qw = $("#recvQWeek"); if (_qw) _qw.onclick = () => { const d = new Date(); const day = (d.getDay() + 6) % 7; const mon = new Date(d); mon.setDate(d.getDate() - day); _setR(_ymd(mon), todayStr()); };
    const _qm = $("#recvQMonth"); if (_qm) _qm.onclick = () => { const d = new Date(); _setR(_ymd(new Date(d.getFullYear(), d.getMonth(), 1)), todayStr()); };
  }
  { const _rrs = $("#btnRecvRefreshStatus"); if (_rrs) _rrs.onclick = refreshRecvStatus; }
  { const _ms = $("#btnRecvMergeShip"); if (_ms) _ms.onclick = mergeShipSelected; }
  { const _ca = $("#recvCheckAll"); if (_ca) _ca.onclick = () => {
      document.querySelectorAll("#inListBody .recv-pick").forEach((c) => (c.checked = _ca.checked));
      updateMergeCount();
    }; }
  const _rc = $("#btnRecycleReload"); if (_rc) _rc.onclick = loadRecycle;
  const navToggle = $("#btnNavToggle");
  if (navToggle) navToggle.onclick = () => document.getElementById("appLayout").classList.toggle("nav-open");
  const backdrop = $("#sidebarBackdrop");
  if (backdrop) backdrop.onclick = () => document.getElementById("appLayout").classList.remove("nav-open");
  $("#btnTrReload").onclick = () => { loadPick(); loadCustomerOptions(); };
  $("#btnTrCreate").onclick = createTransfer;
  { const _pa = $("#btnTrPushAll"); if (_pa) _pa.onclick = pushAllDrafts; }
  $("#btnCuAdd").onclick = addCustomer;
  $("#trCheckAll").onchange = (e) => {
    $$("#trPickBody input.pick, #trPickBody .grp-check").forEach((c) => { c.checked = e.target.checked; c.indeterminate = false; });
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
