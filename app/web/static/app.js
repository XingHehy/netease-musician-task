// 网易音乐人任务管理 前端逻辑
const $ = (s) => document.querySelector(s);
const api = async (url, opts = {}) => {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    if (res.status === 401 || res.status === 403) {
      location.href = res.status === 403 ? "/change-password" : "/login";
    }
    const t = await res.text();
    throw new Error(t || res.statusText);
  }
  return res.status === 204 ? null : res.json();
};
const escapeHtml = (s) =>
  String(s).replace(
    /[&<>]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c],
  );

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
  if (accountId != null) {
    loadLatestQR(accountId);
  }
}
async function loadLatestQR(accountId) {
  // WebSocket 连接尚未建立或二维码事件早于弹窗打开时，主动回读缓存。
  for (const delay of [0, 500, 1500, 3000]) {
    if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
    if (viewingAccountId !== Number(accountId)) return;
    try {
      const data = await api(`/api/tasks/${accountId}/live`);
      if (viewingAccountId !== Number(accountId)) return;
      if (data.qr && data.qr.qr_url) {
        showQR(data.qr.qr_url, data.qr.tip);
        return;
      }
    } catch (_) {
      // WebSocket/任务启动瞬间接口可能尚未可用，继续下一轮尝试。
    }
  }
}
async function openViewModal(accountId, phone) {
  // 查看：拉取累积日志（不清空），继续接收实时更新
  openRunModal(`账号 ${phone || accountId} 运行日志`, accountId);
  try {
    const data = await api(`/api/tasks/${accountId}/live`);
    for (const m of data.logs || []) {
      if (m.type === "log") appendLog(m.ts, m.line, m.level);
      else if (m.type === "status")
        appendLog(m.ts, `【状态】${m.status} ${m.detail || ""}`, "info");
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
function hideQR() {
  $("#qr-box").classList.add("hidden");
}

// ---------- WebSocket ----------
let ws;
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => setConn(true);
  ws.onclose = () => {
    setConn(false);
    setTimeout(connectWS, 2000);
  };
  ws.onmessage = (e) => handleEvent(JSON.parse(e.data));
}
function setConn(on) {
  $("#ws-dot").className = "dot " + (on ? "on" : "off");
  $("#ws-text").textContent = on ? "已连接" : "重连中...";
}
function handleEvent(msg) {
  const modalOpen = !$("#modal-run").classList.contains("hidden");
  const forThisView =
    viewingAccountId != null && Number(msg.account_id) === viewingAccountId;

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
// 本地互助配额（用于卡片上的帮听进度展示）
let localListenDailyMax = "25";
let localListenMonthlyMax = "650";

async function loadAccounts() {
  const accounts = await api("/api/accounts");
  const body = $("#acc-body");
  body.innerHTML = "";
  $("#empty-hint").classList.toggle("hidden", accounts.length > 0);
  const limitText = (v) => (parseInt(v, 10) > 0 ? parseInt(v, 10) : "不限");
  for (const a of accounts) {
    const status = a.cookie_status || "unknown";
    const statusText =
      { ok: "有效", expired: "过期", unknown: "未知" }[status] || status;
    const running = runningAccountId === a.id;
    const enabled = !!a.enabled;
    const useGlobal = !a.run_time;
    const runTime = useGlobal ? globalSendTime : a.run_time;
    const actionBtn = running
      ? `<button class="btn btn-sm btn-view" data-act="view" data-id="${a.id}" data-phone="${escapeHtml(a.phone)}">查看</button>`
      : `<button class="btn btn-sm" data-act="run" data-id="${a.id}" data-phone="${escapeHtml(a.phone)}" data-role="${a.account_role || "musician"}">执行</button>`;
    const toggleBtn = `<button class="btn btn-sm" data-act="toggle" data-id="${a.id}" data-enabled="${enabled ? 1 : 0}">${enabled ? "暂停" : "启用"}</button>`;
    const chips = [
      `<span class="badge ${status}">Cookie ${statusText}</span>`,
      `<span class="badge ${enabled ? "ok" : "unknown"}">${enabled ? "已启用" : "已暂停"}</span>`,
      running ? `<span class="badge running">运行中</span>` : "",
    ].join("");
    const roleTag = `<span class="tag-role ${a.account_role === "player" ? "tag-player" : "tag-musician"}">${a.account_role === "player" ? "播放账号" : "音乐人"}</span>`;
    const avatarText = (a.nickname || a.phone || "?").trim().charAt(0).toUpperCase() || "♪";
    const lastLogin = a.last_login_at ? a.last_login_at.slice(5, 16) : "-";
    // 音乐人：同步得到的平台进度（本月被听/发布任务）；播放账号的「同步」按钮不显示
    const isMusician = (a.account_role || "musician") === "musician";
    const syncBtn = isMusician
      ? `<button class="btn btn-sm" data-act="sync" data-id="${a.id}" data-phone="${escapeHtml(a.phone)}">同步</button>`
      : "";
    const monthPlayedMeta = isMusician && a.musician_play_progress
      ? `
        <div class="meta-item">
          <span class="meta-label">本月被听</span>
          <span class="meta-value">${escapeHtml(a.musician_play_progress)}</span>
        </div>`
      : "";
    const publishTaskMeta = isMusician && a.musician_publish_progress
      ? `
        <div class="meta-item">
          <span class="meta-label">发布任务</span>
          <span class="meta-value">${escapeHtml(a.musician_publish_progress)}</span>
        </div>`
      : "";
    // 参与本地互助的账号展示帮听进度；被听进度只在音乐人卡片显示（平台同步值）
    const helpedMeta = !isMusician && a.local_listen_enabled
      ? `
        <div class="meta-item">
          <span class="meta-label">今日帮听</span>
          <span class="meta-value">${a.local_listen_helped_today || 0}/${limitText(localListenDailyMax)}</span>
        </div>
        <div class="meta-item">
          <span class="meta-label">本月帮听</span>
          <span class="meta-value">${a.local_listen_helped_month || 0}/${limitText(localListenMonthlyMax)}</span>
        </div>`
      : "";
    const card = document.createElement("div");
    card.className = `acc-card${running ? " running" : ""}${enabled ? "" : " disabled"}`;
    card.innerHTML = `
      <div class="acc-top">
        <div class="acc-id">
          <div class="acc-avatar">${escapeHtml(avatarText)}</div>
          <div class="acc-info">
            <div class="acc-name">${escapeHtml(a.nickname || a.phone)}${roleTag}</div>
            <div class="acc-sub">${escapeHtml(a.phone)}</div>
          </div>
        </div>
        <div class="acc-chips">${chips}</div>
      </div>
      <div class="acc-meta">
        <div class="meta-item">
          <span class="meta-label">运行时间</span>
          <span class="meta-value">${escapeHtml(runTime)}${useGlobal ? '<span class="tag-global">全局</span>' : ""}</span>
        </div>${
          isMusician
            ? `${
                // 已有平台同步的「发布任务」时隐藏本地计数「本月发布」
                a.musician_publish_progress
                  ? ""
                  : `
        <div class="meta-item">
          <span class="meta-label">本月发布</span>
          <span class="meta-value">${a.monthly_sends || 0}</span>
        </div>`
              }${monthPlayedMeta}${publishTaskMeta}
        <div class="meta-item">
          <span class="meta-label">上次登录</span>
          <span class="meta-value">${escapeHtml(lastLogin)}</span>
        </div>${helpedMeta}`
            : `${helpedMeta}
        <div class="meta-item">
          <span class="meta-label">上次登录</span>
          <span class="meta-value">${escapeHtml(lastLogin)}</span>
        </div>`
        }
      </div>
      <div class="acc-actions">
        <button class="btn btn-sm btn-primary" data-act="login" data-id="${a.id}" data-phone="${escapeHtml(a.phone)}">登录</button>
        ${syncBtn}
        ${actionBtn}
        ${toggleBtn}
        <button class="btn btn-sm" data-act="edit" data-id="${a.id}">编辑</button>
        <button class="btn btn-sm btn-danger" data-act="delete" data-id="${a.id}" data-phone="${escapeHtml(a.phone)}">删除</button>
      </div>`;
    body.appendChild(card);
  }
}

async function refreshGlobalSendTime() {
  try {
    const s = await api("/api/settings");
    if (s.default_send_time) globalSendTime = s.default_send_time;
    if (s.local_listen_daily_max) localListenDailyMax = s.local_listen_daily_max;
    if (s.local_listen_monthly_max) localListenMonthlyMax = s.local_listen_monthly_max;
  } catch (e) {
    /* ignore */
  }
}

async function refreshActiveAndList() {
  try {
    const data = await api("/api/tasks/active");
    runningAccountId = data.active ? Number(data.active.account_id) : null;
  } catch (e) {
    /* ignore */
  }
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
    } else if (act === "sync") {
      openRunModal(`账号 ${btn.dataset.phone || id} 同步音乐人数据`, id);
      runningAccountId = Number(id);
      loadAccounts();
      await api(`/api/tasks/${id}/sync-musician`, { method: "POST" });
    } else if (act === "run") {
      openRunSelect(id, btn.dataset.phone, btn.dataset.role);
    } else if (act === "toggle") {
      const next = btn.dataset.enabled === "1" ? false : true;
      await api(`/api/accounts/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: next }),
      });
      await loadAccounts();
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
  const phone = $("#login-phone").textContent || id;
  try {
    $("#modal-login").classList.add("hidden");
    openRunModal(`账号 ${phone} 登录中`, id);
    runningAccountId = Number(id);
    loadAccounts();
    await api(`/api/login/${id}`, { method: "POST" });
  } catch (err) {
    appendLog("", "启动登录失败：" + err.message, "error");
  }
});

// ---------- 执行任务多选 ----------
function openRunSelect(id, phone, role = "musician") {
  $("#run-account-id").value = id;
  $("#run-account-id").dataset.phone = phone || `#${id}`;
  document
    .querySelectorAll(".run-task")
    .forEach((c) => {
      c.disabled = role === "player" && c.value !== "local_listen";
      c.checked = role === "player" ? c.value === "local_listen" : c.value === "checkin";
    });
  $("#modal-run-select").classList.remove("hidden");
}
$("#btn-confirm-run").addEventListener("click", async () => {
  const id = $("#run-account-id").value;
  const phone = $("#run-account-id").dataset.phone || id;
  const tasks = [...document.querySelectorAll(".run-task:checked")].map(
    (c) => c.value,
  );
  if (tasks.length === 0) {
    alert("请至少选择一项任务");
    return;
  }
  try {
    $("#modal-run-select").classList.add("hidden");
    openRunModal(`账号 ${phone} 执行任务`, id);
    runningAccountId = Number(id);
    loadAccounts();
    await api(`/api/tasks/${id}/run`, {
      method: "POST",
      body: JSON.stringify({ tasks }),
    });
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
    await api(`/api/accounts/${id}?delete_profile=${delProfile}`, {
      method: "DELETE",
    });
    $("#modal-delete").classList.add("hidden");
    await loadAccounts();
  } catch (err) {
    alert("删除失败：" + err.message);
  }
});

// ---------- 弹窗通用 ----------
document
  .querySelectorAll("[data-close]")
  .forEach((b) =>
    b.addEventListener("click", () =>
      b.closest(".modal").classList.add("hidden"),
    ),
  );

// ---------- 新增账号 ----------
function refreshAddLoginFields() {
  const method = $("#in-login-method").value;
  const passwordLabel = $("#in-password").closest("label");
  const passwordInput = $("#in-password");
  const qrOnly = method === "qrcode";
  passwordLabel.firstChild.textContent = qrOnly ? "密码（可选） " : "密码 ";
  passwordInput.placeholder = qrOnly ? "扫码登录无需填写" : "自动/密码登录必填";
}

$("#in-login-method").addEventListener("change", refreshAddLoginFields);
function refreshAddLocalFields() {
  const player = $("#in-account-role").value === "player";
  const musicianFields = $("#in-musician-listen-fields");
  const playerFields = $("#in-player-listen-fields");
  musicianFields.classList.toggle("hidden", player);
  playerFields.classList.toggle("hidden", !player);
  musicianFields.hidden = player;
  playerFields.hidden = !player;
  musicianFields.style.display = player ? "none" : "";
  playerFields.style.display = player ? "" : "none";
  $("#in-player-listen-enabled").checked = player;
  $("#in-local-listen-enabled").checked = !player;
}
$("#in-account-role").addEventListener("change", refreshAddLocalFields);

$("#btn-add").addEventListener("click", () => {
  $("#in-phone").value = "";
  $("#in-password").value = "";
  $("#in-login-method").value = "auto";
  $("#in-runtime").value = globalSendTime || "";
  $("#in-account-role").value = "musician";
  $("#in-local-listen-enabled").checked = false;
  $("#in-local-listen-item").value = "";
  $("#in-player-listen-enabled").checked = true;
  refreshAddLoginFields();
  refreshAddLocalFields();
  $("#modal-add").classList.remove("hidden");
});
$("#btn-save-add").addEventListener("click", async () => {
  const phone = $("#in-phone").value.trim();
  const password = $("#in-password").value;
  const login_method = $("#in-login-method").value;
  const run_time = $("#in-runtime").value.trim() || null;
  const account_role = $("#in-account-role").value;
  const local_listen_enabled = account_role === "player"
    ? $("#in-player-listen-enabled").checked
    : $("#in-local-listen-enabled").checked;
  const local_listen_item_id = account_role === "player"
    ? ""
    : $("#in-local-listen-item").value.trim();
  if (!phone) {
    alert("请填写手机号");
    return;
  }
  if (login_method !== "qrcode" && !password) {
    alert("当前登录方式需要填写密码");
    return;
  }
  try {
    const acc = await api("/api/accounts", {
      method: "POST",
      body: JSON.stringify({
        phone, password, login_method, run_time,
        account_role, local_listen_enabled, local_listen_item_id,
      }),
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
  $("#edit-account-role").value = a.account_role || "musician";
  $("#edit-local-listen-enabled").checked = !!a.local_listen_enabled;
  $("#edit-local-listen-item").value = a.local_listen_item_id || "";
  $("#edit-player-listen-enabled").checked = !!a.local_listen_enabled;
  refreshEditLocalFields();
  $("#modal-edit").classList.remove("hidden");
}
function refreshEditLocalFields() {
  const player = $("#edit-account-role").value === "player";
  const musicianFields = $("#edit-musician-listen-fields");
  const playerFields = $("#edit-player-listen-fields");
  musicianFields.classList.toggle("hidden", player);
  playerFields.classList.toggle("hidden", !player);
  musicianFields.hidden = player;
  playerFields.hidden = !player;
  musicianFields.style.display = player ? "none" : "";
  playerFields.style.display = player ? "" : "none";
  $("#edit-player-listen-enabled").checked = player;
  $("#edit-local-listen-enabled").checked = !player;
}
$("#edit-account-role").addEventListener("change", refreshEditLocalFields);
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
  payload.account_role = $("#edit-account-role").value;
  const editRole = $("#edit-account-role").value;
  payload.local_listen_enabled = editRole === "player"
    ? $("#edit-player-listen-enabled").checked
    : $("#edit-local-listen-enabled").checked;
  payload.local_listen_item_id = editRole === "player"
    ? ""
    : $("#edit-local-listen-item").value.trim();
  try {
    await api(`/api/accounts/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
    $("#modal-edit").classList.add("hidden");
    await loadAccounts();
  } catch (err) {
    alert("保存失败：" + err.message);
  }
});

// ---------- 全局设置 ----------
function refreshNotificationFields() {
  const method = $("#set-notification-method").value;
  $("#notify-wecom-fields").classList.toggle("hidden", method !== "wecom" && method !== "auto");
  $("#notify-custom-fields").classList.toggle("hidden", method !== "custom" && method !== "auto");
  $("#notify-pushplus-fields").classList.toggle("hidden", method !== "pushplus" && method !== "auto");
}

$("#set-notification-method").addEventListener("change", refreshNotificationFields);

$("#btn-settings").addEventListener("click", async () => {
  const s = await api("/api/settings");
  $("#set-send-time").value = s.default_send_time || "";
  $("#set-interval").value = s.execution_interval_days || "";
  $("#set-max-sends").value = s.max_monthly_sends || "";
  $("#set-headless").checked = s.headless === "1";
  $("#set-local-listen-daily-max").value = s.local_listen_daily_max || "25";
  $("#set-local-listen-monthly-max").value = s.local_listen_monthly_max || "650";
  $("#set-local-listen-percent").value = s.local_listen_play_percent || "34";
  $("#set-local-listen-start-time").value = s.local_listen_start_time || "10:00";
  $("#set-notification-method").value = s.notification_method || "none";
  $("#set-wecom").value = s.wecom_webhook_key || "";
  $("#set-webhook-url").value = s.custom_webhook_url || "";
  $("#set-webhook-method").value = s.custom_webhook_method || "POST";
  $("#set-webhook-headers").value = s.custom_webhook_headers || "";
  $("#set-webhook-body").value = s.custom_webhook_body || "";
  $("#set-pushplus-token").value = s.pushplus_token || "";
  $("#set-pushplus-topic").value = s.pushplus_topic || "";
  refreshNotificationFields();
  $("#set-current-admin-password").value = "";
  $("#set-new-admin-password").value = "";
  $("#set-confirm-admin-password").value = "";
  $("#modal-settings").classList.remove("hidden");
});
$("#btn-save-settings").addEventListener("click", async () => {
  const values = {
    default_send_time: $("#set-send-time").value.trim(),
    execution_interval_days: $("#set-interval").value.trim(),
    max_monthly_sends: $("#set-max-sends").value.trim(),
    headless: $("#set-headless").checked ? "1" : "0",
    local_listen_daily_max: $("#set-local-listen-daily-max").value.trim() || "0",
    local_listen_monthly_max: $("#set-local-listen-monthly-max").value.trim() || "0",
    local_listen_play_percent: $("#set-local-listen-percent").value.trim() || "34",
    local_listen_start_time: $("#set-local-listen-start-time").value.trim() || "10:00",
    notification_method: $("#set-notification-method").value,
    wecom_webhook_key: $("#set-wecom").value.trim(),
    custom_webhook_url: $("#set-webhook-url").value.trim(),
    custom_webhook_method: $("#set-webhook-method").value,
    custom_webhook_headers: $("#set-webhook-headers").value.trim(),
    custom_webhook_body: $("#set-webhook-body").value.trim(),
    pushplus_token: $("#set-pushplus-token").value.trim(),
    pushplus_topic: $("#set-pushplus-topic").value.trim(),
  };
  try {
    const currentPassword = $("#set-current-admin-password").value;
    const newPassword = $("#set-new-admin-password").value;
    const confirmPassword = $("#set-confirm-admin-password").value;
    if (newPassword) {
      if (!currentPassword) throw new Error("修改管理密码需要填写当前密码");
      if (newPassword !== confirmPassword) throw new Error("两次输入的新管理密码不一致");
      await api("/api/auth/change-password", {
        method: "POST",
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      });
    }
    await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({ values }),
    });
    $("#modal-settings").classList.add("hidden");
    await refreshGlobalSendTime();
    await loadAccounts();
  } catch (err) {
    alert("保存失败：" + err.message);
  }
});

$("#btn-logout").addEventListener("click", async () => {
  try { await api("/api/auth/logout", { method: "POST" }); } catch (_) {}
  location.href = "/login";
});

$("#btn-clear-log").addEventListener("click", () => {
  $("#log-box").innerHTML = "";
});

// ---------- 强制停止 ----------
$("#btn-force-stop").addEventListener("click", () => {
  const id = $("#run-modal-account").value;
  if (!id) {
    alert("当前无可停止的任务");
    return;
  }
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
