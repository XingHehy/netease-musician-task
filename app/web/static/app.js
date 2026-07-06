// 网易音乐人任务管理 前端逻辑
const $ = (s) => document.querySelector(s);
const api = async (url, opts = {}) => {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(t || res.statusText);
  }
  return res.status === 204 ? null : res.json();
};
const escapeHtml = (s) =>
  String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

// ---------- 运行日志弹窗 ----------
// 当前「运行日志」弹窗正在查看的账号；WS 日志按此过滤显示
let viewingAccountId = null;
// 当前正在运行浏览器的账号（用于把「执行」按钮切成「查看」）
let runningAccountId = null;

function openRunModal(title, accountId) {
  $("#run-title").textContent = title;
  $("#run-modal-account").value = accountId != null ? accountId : "";
  viewingAccountId = accountId != null ? Number(accountId) : null;
  $("#log-box").innerHTML = "";
  hideQR();
  $("#modal-run").classList.remove("hidden");
}
async function openViewModal(accountId, phone) {
  // 查看：拉取累积日志（不清空），继续接收实时更新
  openRunModal(`账号 ${phone || accountId} 运行日志`, accountId);
  try {
    const data = await api(`/api/tasks/${accountId}/live`);
    for (const m of data.logs || []) {
      if (m.type === "log") appendLog(m.ts, m.line, m.level);
      else if (m.type === "status") appendLog(m.ts, `【状态】${m.status} ${m.detail || ""}`, "info");
    }
    if (data.qr && data.qr.qr_url) showQR(data.qr.qr_url, data.qr.tip);
  } catch (err) {
    appendLog("", "拉取日志失败：" + err.message, "error");
  }
}
function appendLog(ts, line, level) {
  const box = $("#log-box");
  if (!box) return;
  const div = document.createElement("div");
  div.className = "log-line " + (level || "info");
  div.innerHTML = `<span class="t">${ts || ""}</span>${escapeHtml(line)}`;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}
function showQR(url, tip) {
  $("#qr-tip").textContent = tip || "请扫码";
  $("#qr-img").src = url;
  $("#qr-box").classList.remove("hidden");
}
function hideQR() { $("#qr-box").classList.add("hidden"); }

// ---------- WebSocket ----------
let ws;
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => setConn(true);
  ws.onclose = () => { setConn(false); setTimeout(connectWS, 2000); };
  ws.onmessage = (e) => handleEvent(JSON.parse(e.data));
}
function setConn(on) {
  $("#ws-dot").className = "dot " + (on ? "on" : "off");
  $("#ws-text").textContent = on ? "已连接" : "重连中...";
}
function handleEvent(msg) {
  const modalOpen = !$("#modal-run").classList.contains("hidden");
  const forThisView = viewingAccountId != null && Number(msg.account_id) === viewingAccountId;

  if (msg.type === "log") {
    if (modalOpen && forThisView) appendLog(msg.ts, msg.line, msg.level);
  } else if (msg.type === "qrcode") {
    if (modalOpen && forThisView) showQR(msg.qr_url, msg.tip);
  } else if (msg.type === "status") {
    if (modalOpen && forThisView) {
      appendLog(msg.ts, `【状态】${msg.status} ${msg.detail || ""}`, "info");
      if (msg.status === "login_ok") hideQR();
    }
    // 运行态变化 → 直接信任 WS 消息更新按钮（不走 /active，避免与 registry 登记时机竞态）
    const acc = Number(msg.account_id);
    const startStates = ["logging_in", "running", "secondary"];
    const endStates = ["done", "stopped", "login_ok", "login_fail"];
    if (startStates.includes(msg.status)) {
      runningAccountId = acc;
      loadAccounts();
    } else if (endStates.includes(msg.status)) {
      if (runningAccountId === acc) runningAccountId = null;
      loadAccounts();
    }
  }
}

// ---------- 账号列表 ----------
let globalSendTime = "09:30";

async function loadAccounts() {
  const accounts = await api("/api/accounts");
  const body = $("#acc-body");
  body.innerHTML = "";
  $("#empty-hint").classList.toggle("hidden", accounts.length > 0);
  for (const a of accounts) {
    const status = a.cookie_status || "unknown";
    const statusText = { ok: "有效", expired: "过期", unknown: "未知" }[status] || status;
    const runTime = a.run_time
      ? escapeHtml(a.run_time)
      : `${escapeHtml(globalSendTime)} <span class="tag-global">全局</span>`;
    const running = runningAccountId === a.id;
    const actionBtn = running
      ? `<button class="btn btn-sm btn-view" data-act="view" data-id="${a.id}" data-phone="${escapeHtml(a.phone)}">查看</button>`
      : `<button class="btn btn-sm" data-act="run" data-id="${a.id}">执行</button>`;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td data-label="手机号">${escapeHtml(a.phone)}</td>
      <td data-label="昵称">${escapeHtml(a.nickname || "-")}</td>
      <td data-label="Cookie 状态"><span class="badge ${status}">${statusText}</span></td>
      <td data-label="运行时间">${runTime}</td>
      <td data-label="本月发布">${a.monthly_sends || 0}</td>
      <td data-label="操作" class="cell-actions">
        <button class="btn btn-sm btn-primary" data-act="login" data-id="${a.id}" data-phone="${escapeHtml(a.phone)}">登录</button>
        ${actionBtn}
        <button class="btn btn-sm" data-act="edit" data-id="${a.id}">编辑</button>
        <button class="btn btn-sm btn-danger" data-act="delete" data-id="${a.id}" data-phone="${escapeHtml(a.phone)}">删除</button>
      </td>`;
    body.appendChild(tr);
  }
}

async function refreshGlobalSendTime() {
  try {
    const s = await api("/api/settings");
    if (s.default_send_time) globalSendTime = s.default_send_time;
  } catch (e) { /* ignore */ }
}

async function refreshActiveAndList() {
  try {
    const data = await api("/api/tasks/active");
    runningAccountId = data.active ? Number(data.active.account_id) : null;
  } catch (e) { /* ignore */ }
  await loadAccounts();
}

$("#acc-body").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const id = btn.dataset.id;
  const act = btn.dataset.act;
  try {
    if (act === "login") {
      openLoginConfirm(id, btn.dataset.phone);
    } else if (act === "run") {
      openRunSelect(id);
    } else if (act === "view") {
      openViewModal(id, btn.dataset.phone);
    } else if (act === "edit") {
      openEdit(id);
    } else if (act === "delete") {
      onDelete(id, btn.dataset.phone);
    }
  } catch (err) {
    appendLog("", "操作失败：" + err.message, "error");
    alert("操作失败：" + err.message);
  }
});

// ---------- 登录确认 ----------
function openLoginConfirm(id, phone) {
  $("#login-account-id").value = id;
  $("#login-phone").textContent = phone || `#${id}`;
  $("#modal-login").classList.remove("hidden");
}
$("#btn-confirm-login").addEventListener("click", async () => {
  const id = $("#login-account-id").value;
  try {
    $("#modal-login").classList.add("hidden");
    openRunModal(`账号 ${id} 登录中`, id);
    runningAccountId = Number(id);
    loadAccounts();
    await api(`/api/login/${id}`, { method: "POST" });
  } catch (err) {
    appendLog("", "启动登录失败：" + err.message, "error");
  }
});

// ---------- 执行任务多选 ----------
function openRunSelect(id) {
  $("#run-account-id").value = id;
  document.querySelectorAll(".run-task").forEach((c) => (c.checked = c.value === "checkin"));
  $("#modal-run-select").classList.remove("hidden");
}
$("#btn-confirm-run").addEventListener("click", async () => {
  const id = $("#run-account-id").value;
  const tasks = [...document.querySelectorAll(".run-task:checked")].map((c) => c.value);
  if (tasks.length === 0) { alert("请至少选择一项任务"); return; }
  try {
    $("#modal-run-select").classList.add("hidden");
    openRunModal(`账号 ${id} 执行任务`, id);
    runningAccountId = Number(id);
    loadAccounts();
    await api(`/api/tasks/${id}/run`, { method: "POST", body: JSON.stringify({ tasks }) });
  } catch (err) {
    appendLog("", "启动失败：" + err.message, "error");
  }
});

async function onDelete(id, phone) {
  $("#del-id").value = id;
  $("#del-phone").textContent = phone;
  $("#del-profile").checked = false;
  $("#modal-delete").classList.remove("hidden");
}
$("#btn-confirm-delete").addEventListener("click", async () => {
  const id = $("#del-id").value;
  const delProfile = $("#del-profile").checked;
  try {
    await api(`/api/accounts/${id}?delete_profile=${delProfile}`, { method: "DELETE" });
    $("#modal-delete").classList.add("hidden");
    await loadAccounts();
  } catch (err) {
    alert("删除失败：" + err.message);
  }
});

// ---------- 弹窗通用 ----------
document.querySelectorAll("[data-close]").forEach((b) =>
  b.addEventListener("click", () => b.closest(".modal").classList.add("hidden"))
);

// ---------- 新增账号 ----------
$("#btn-add").addEventListener("click", () => {
  $("#in-phone").value = "";
  $("#in-password").value = "";
  $("#in-runtime").value = globalSendTime || "";
  $("#modal-add").classList.remove("hidden");
});
$("#btn-save-add").addEventListener("click", async () => {
  const phone = $("#in-phone").value.trim();
  const password = $("#in-password").value;
  const run_time = $("#in-runtime").value.trim() || null;
  if (!phone || !password) { alert("请填写手机号和密码"); return; }
  try {
    const acc = await api("/api/accounts", {
      method: "POST",
      body: JSON.stringify({ phone, password, run_time }),
    });
    $("#modal-add").classList.add("hidden");
    openRunModal(`账号 ${phone} 登录中`, acc.id);
    runningAccountId = Number(acc.id);
    await loadAccounts();
    await api(`/api/login/${acc.id}`, { method: "POST" });
  } catch (err) {
    alert("创建失败：" + err.message);
  }
});

// ---------- 编辑账号 ----------
async function openEdit(id) {
  const a = await api(`/api/accounts/${id}`);
  $("#edit-id").value = a.id;
  $("#edit-password").value = "";
  $("#edit-runtime").value = a.run_time || "";
  $("#edit-interval").value = a.interval_days || "";
  $("#edit-enabled").checked = !!a.enabled;
  $("#modal-edit").classList.remove("hidden");
}
$("#btn-save-edit").addEventListener("click", async () => {
  const id = $("#edit-id").value;
  const payload = {};
  const pw = $("#edit-password").value;
  const rt = $("#edit-runtime").value.trim();
  const iv = $("#edit-interval").value.trim();
  if (pw) payload.password = pw;
  if (rt) payload.run_time = rt;
  if (iv) payload.interval_days = parseInt(iv, 10);
  payload.enabled = $("#edit-enabled").checked;
  try {
    await api(`/api/accounts/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
    $("#modal-edit").classList.add("hidden");
    await loadAccounts();
  } catch (err) {
    alert("保存失败：" + err.message);
  }
});

// ---------- 全局设置 ----------
$("#btn-settings").addEventListener("click", async () => {
  const s = await api("/api/settings");
  $("#set-send-time").value = s.default_send_time || "";
  $("#set-interval").value = s.execution_interval_days || "";
  $("#set-max-sends").value = s.max_monthly_sends || "";
  $("#set-headless").checked = s.headless === "1";
  $("#set-wecom").value = s.wecom_webhook_key || "";
  $("#set-webhook-url").value = s.custom_webhook_url || "";
  $("#set-webhook-method").value = s.custom_webhook_method || "POST";
  $("#set-webhook-headers").value = s.custom_webhook_headers || "";
  $("#set-webhook-body").value = s.custom_webhook_body || "";
  $("#modal-settings").classList.remove("hidden");
});
$("#btn-save-settings").addEventListener("click", async () => {
  const values = {
    default_send_time: $("#set-send-time").value.trim(),
    execution_interval_days: $("#set-interval").value.trim(),
    max_monthly_sends: $("#set-max-sends").value.trim(),
    headless: $("#set-headless").checked ? "1" : "0",
    wecom_webhook_key: $("#set-wecom").value.trim(),
    custom_webhook_url: $("#set-webhook-url").value.trim(),
    custom_webhook_method: $("#set-webhook-method").value,
    custom_webhook_headers: $("#set-webhook-headers").value.trim(),
    custom_webhook_body: $("#set-webhook-body").value.trim(),
  };
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify({ values }) });
    $("#modal-settings").classList.add("hidden");
    await refreshGlobalSendTime();
    await loadAccounts();
  } catch (err) {
    alert("保存失败：" + err.message);
  }
});

$("#btn-clear-log").addEventListener("click", () => { $("#log-box").innerHTML = ""; });

// ---------- 强制停止 ----------
$("#btn-force-stop").addEventListener("click", () => {
  const id = $("#run-modal-account").value;
  if (!id) { alert("当前无可停止的任务"); return; }
  $("#stop-account-id").value = id;
  $("#modal-stop").classList.remove("hidden");
});
$("#btn-confirm-stop").addEventListener("click", async () => {
  const id = $("#stop-account-id").value;
  try {
    const res = await api(`/api/tasks/${id}/stop`, { method: "POST" });
    $("#modal-stop").classList.add("hidden");
    appendLog("", res.message || "已发送停止指令", "warn");
    await refreshActiveAndList();
  } catch (err) {
    alert("停止失败：" + err.message);
  }
});

// ---------- 初始化 ----------
connectWS();
refreshGlobalSendTime().then(refreshActiveAndList);
setInterval(refreshActiveAndList, 30000);
