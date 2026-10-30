const state = {
  dashboard: null,
  settingsDirty: false,
  clockOffset: 0,
  clockTimer: null,
  loginPoller: null,
  loginActive: false,
  currentPlatform: "qq",
  busy: false,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  renderIcons();
  refreshDashboard({ initial: true });
  state.clockTimer = window.setInterval(updateClock, 1000);
});

function bindEvents() {
  $("#saveSettingsBtn").addEventListener("click", () => saveSettings());
  $("#refreshDashboardBtn").addEventListener("click", () => refreshDashboard());
  $("#loginBtn").addEventListener("click", () => openLogin("qq"));
  $("#visibleBrowserBtn").addEventListener("click", openVisibleBrowser);
  $("#visibleFromDialogBtn").addEventListener("click", openVisibleBrowser);
  $("#checkLoginBtn").addEventListener("click", checkLogin);
  $("#exchangeBtn").addEventListener("click", executeExchange);
  $("#closeLoginBtn").addEventListener("click", closeLogin);
  $("#closeScreenshotBtn").addEventListener("click", () => {
    $("#screenshotDialog").close();
  });

  $("#loginDialog").addEventListener("close", () => {
    stopLoginPolling();
  });
  $("#screenshotDialog").addEventListener("click", (event) => {
    if (event.target === $("#screenshotDialog")) {
      $("#screenshotDialog").close();
    }
  });

  $("#rewardGrid").addEventListener("click", (event) => {
    const card = event.target.closest("[data-reward-id]");
    if (!card || state.busy) return;
    selectReward(Number(card.dataset.rewardId));
  });

  $("#activityList").addEventListener("click", (event) => {
    const button = event.target.closest("[data-screenshot-url]");
    if (!button) return;
    $("#executionScreenshot").src = button.dataset.screenshotUrl;
    $("#screenshotDialog").showModal();
  });

  $$(".test-push").forEach((button) => {
    button.addEventListener("click", () => testPush(button.dataset.provider));
  });

  $$(".segment[data-platform]").forEach((button) => {
    button.addEventListener("click", () => switchPlatform(button.dataset.platform));
  });

  [
    "#accountLabel",
    "#scheduledTime",
    "#autoExchange",
    "#pushplusEnabled",
    "#pushplusToken",
    "#onebotEnabled",
    "#onebotBaseUrl",
    "#onebotTargetType",
    "#onebotTargetId",
    "#onebotToken",
  ].forEach((selector) => {
    const element = $(selector);
    element.addEventListener("input", markDirty);
    element.addEventListener("change", markDirty);
  });
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  let body = null;
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    body = await response.json();
  }
  if (!response.ok) {
    const detail = body?.detail;
    const message = Array.isArray(detail)
      ? detail.map((item) => item.msg || String(item)).join("；")
      : detail || `请求失败（${response.status}）`;
    throw new Error(message);
  }
  return body;
}

async function refreshDashboard({ initial = false } = {}) {
  try {
    const data = await api("/api/dashboard");
    state.dashboard = data;
    state.clockOffset = new Date(data.server_time).getTime() - Date.now();
    renderDashboard();
    if (!initial && !state.settingsDirty) {
      fillSettings();
    }
    if (initial) {
      state.settingsDirty = false;
      updateSaveState(false);
    }
  } catch (error) {
    toast("读取失败", error.message, "error");
  }
}

function renderDashboard() {
  const data = state.dashboard;
  if (!data) return;
  renderRewards();
  renderLoginState();
  renderSelection();
  renderSchedule();
  renderActivity();
  renderPushState();
  renderTodayResult();
  updateClock();
  renderIcons();
}

function fillSettings() {
  const settings = state.dashboard?.settings;
  if (!settings) return;
  $("#accountLabel").value = settings.account_label || "";
  $("#scheduledTime").value = settings.scheduled_time || "10:05";
  $("#autoExchange").checked = Boolean(settings.auto_exchange);
  $("#pushplusEnabled").checked = Boolean(settings.pushplus.enabled);
  $("#onebotEnabled").checked = Boolean(settings.onebot.enabled);
  $("#onebotBaseUrl").value = settings.onebot.base_url || "";
  $("#onebotTargetType").value = settings.onebot.target_type || "private";
  $("#onebotTargetId").value = settings.onebot.target_id || "";
  $("#pushplusToken").value = "";
  $("#onebotToken").value = "";
}

function renderRewards() {
  const rewards = state.dashboard?.rewards || [];
  const selectedId = state.dashboard?.settings?.reward_id || 1;
  $("#rewardGrid").innerHTML = rewards
    .map(
      (reward, index) => `
        <button
          class="reward-card reveal ${reward.id === selectedId ? "is-selected" : ""}"
          type="button"
          data-reward-id="${reward.id}"
          aria-pressed="${reward.id === selectedId}"
          style="animation-delay:${35 + index * 28}ms"
        >
          <span class="reward-visual">
            <img src="${escapeAttr(reward.image)}" alt="" loading="lazy">
            <span class="selected-check"><i data-lucide="check"></i></span>
          </span>
          <span class="reward-info">
            <strong>${escapeHtml(reward.name)}</strong>
            <span>${escapeHtml(reward.description)}</span>
          </span>
          <span class="reward-cost">
            <span>体验币</span>
            <b>${reward.cost}</b>
          </span>
        </button>
      `,
    )
    .join("");
}

function renderLoginState() {
  const login = state.dashboard?.login || {};
  const settings = state.dashboard?.settings || {};
  const element = $("#loginState");
  const authenticated = Boolean(login.authenticated);
  const bound = Boolean(login.bound);
  element.className = "status-pill";

  if (authenticated && bound) {
    element.textContent = "登录有效";
    element.classList.add("is-active");
    $("#nickname").textContent =
      settings.last_login_nickname || settings.account_label || "腾讯账号";
    $("#partition").textContent = "Cookie 已保存";
  } else if (authenticated) {
    element.textContent = "待绑定";
    element.classList.add("is-warning");
    $("#nickname").textContent =
      settings.last_login_nickname || settings.account_label || "腾讯账号";
    $("#partition").textContent = "需要绑定正式服角色";
  } else if (login.state === "login_required") {
    element.textContent = "需登录";
    element.classList.add("is-warning");
    $("#nickname").textContent = settings.last_login_nickname || "登录已失效";
    $("#partition").textContent = "请重新扫码登录";
  } else {
    element.textContent = "未检查";
    $("#nickname").textContent =
      settings.last_login_nickname || "尚未登录";
    $("#partition").textContent = "目标页扫码后自动保存 Cookie";
  }
}

function renderSelection() {
  const selected = currentReward();
  if (!selected) return;
  $("#selectedRewardName").textContent = selected.name;
  $("#selectedRewardCost").textContent = `${selected.cost} 体验币`;
  $("#executeRewardName").textContent = selected.name;
  $("#executeRewardCost").textContent = `${selected.cost} 体验币`;
}

function renderSchedule() {
  const settings = state.dashboard?.settings || {};
  const nextRun = state.dashboard?.next_run;
  if (!$("#scheduledTime").value) {
    $("#scheduledTime").value = settings.scheduled_time || "10:05";
  }
  if (!state.settingsDirty) {
    $("#autoExchange").checked = Boolean(settings.auto_exchange);
  }
  if (!settings.auto_exchange || !nextRun) {
    $("#nextRun").textContent = "未启用";
    $("#nextRunDate").textContent = "开启每日任务后显示";
    return;
  }
  const date = new Date(nextRun);
  $("#nextRun").textContent = formatClock(date);
  $("#nextRunDate").textContent = relativeDateText(date);
}

function renderActivity() {
  const recent = state.dashboard?.recent || [];
  if (!recent.length) {
    $("#activityList").innerHTML = `
      <div class="empty-state">
        <i data-lucide="history"></i>
        <p>暂无执行记录</p>
      </div>
    `;
    return;
  }
  $("#activityList").innerHTML = recent
    .map((item) => {
      const status = activityVisual(item.status);
      const screenshot = item.screenshot_url
        ? `<button type="button" data-screenshot-url="${escapeAttr(item.screenshot_url)}">查看截图</button>`
        : "";
      return `
        <article class="activity-item ${status.className}">
          <span class="activity-symbol"><i data-lucide="${status.icon}"></i></span>
          <div class="activity-copy">
            <strong>${escapeHtml(item.reward_name)} · ${escapeHtml(status.label)}</strong>
            <p>${escapeHtml(item.message || item.package_name || "无附加信息")}</p>
          </div>
          <div class="activity-meta">
            <time>${formatDateTime(item.created_at)}</time>
            <span>${item.trigger === "schedule" ? "自动" : "手动"}</span>
            ${screenshot}
          </div>
        </article>
      `;
    })
    .join("");
}

function renderPushState() {
  const settings = state.dashboard?.settings;
  if (!settings) return;
  const enabledCount =
    Number(settings.pushplus.enabled) + Number(settings.onebot.enabled);
  $("#channelCount").textContent = `${enabledCount} / 2`;
  $("#channelCount").className = `status-pill ${enabledCount ? "is-active" : ""}`;

  const pushplus = $("#pushplusSecretState");
  pushplus.textContent = settings.pushplus.token_set
    ? `已保存 ${settings.pushplus.token_hint}`
    : "未配置";
  pushplus.classList.toggle("is-set", settings.pushplus.token_set);

  const onebot = $("#onebotSecretState");
  onebot.textContent = settings.onebot.access_token_set
    ? `Token 已保存 ${settings.onebot.access_token_hint}`
    : "HTTP API 无 Token";
  onebot.classList.toggle("is-set", settings.onebot.access_token_set);
}

function renderTodayResult() {
  const today = state.dashboard?.today;
  const element = $("#todayResult");
  if (!today) {
    element.textContent = "今天尚未执行";
    return;
  }
  element.textContent = `${activityVisual(today.status).label} · ${formatDateTime(today.created_at)}`;
}

function currentReward() {
  const id = state.dashboard?.settings?.reward_id || 1;
  return (state.dashboard?.rewards || []).find((item) => item.id === id);
}

async function selectReward(rewardId) {
  if (!state.dashboard) return;
  state.dashboard.settings.reward_id = rewardId;
  renderRewards();
  renderSelection();
  renderIcons();
  await saveSettings({ silent: true });
}

function markDirty() {
  state.settingsDirty = true;
  updateSaveState(true);
}

function updateSaveState(dirty) {
  const button = $("#saveSettingsBtn");
  button.classList.toggle("has-changes", dirty);
  button.querySelector("span").textContent = dirty ? "保存更改" : "保存配置";
}

async function saveSettings({ silent = false } = {}) {
  if (state.busy) return false;
  const selected = state.dashboard?.settings?.reward_id || 1;
  const payload = {
    account_label: $("#accountLabel").value.trim(),
    scheduled_time: $("#scheduledTime").value || "10:05",
    auto_exchange: $("#autoExchange").checked,
    reward_id: selected,
    onebot_enabled: $("#onebotEnabled").checked,
    onebot_base_url: $("#onebotBaseUrl").value.trim(),
    onebot_access_token: $("#onebotToken").value,
    clear_onebot_access_token: false,
    onebot_target_type: $("#onebotTargetType").value,
    onebot_target_id: $("#onebotTargetId").value.trim(),
    pushplus_enabled: $("#pushplusEnabled").checked,
    pushplus_token: $("#pushplusToken").value,
    clear_pushplus_token: false,
  };
  setBusy(true, $("#saveSettingsBtn"));
  try {
    await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    state.settingsDirty = false;
    updateSaveState(false);
    if (!silent) toast("配置已保存", "每日任务与推送参数已经更新。");
    await refreshDashboard();
    return true;
  } catch (error) {
    toast("保存失败", error.message, "error");
    return false;
  } finally {
    setBusy(false, $("#saveSettingsBtn"));
  }
}

async function openLogin(platform = "qq") {
  state.currentPlatform = platform;
  updatePlatformSegments();
  const dialog = $("#loginDialog");
  if (!dialog.open) dialog.showModal();
  $("#loginLoading").classList.remove("is-hidden");
  $("#loginPollLabel").textContent = "正在打开登录页";
  try {
    const data = await api("/api/login/start", {
      method: "POST",
      body: JSON.stringify({ platform }),
    });
    state.loginActive = true;
    handleLoginStatus(data.login);
    $("#loginScreenshot").src = `${data.screenshot_url}&v=${Date.now()}`;
    $("#loginLoading").classList.add("is-hidden");
    startLoginPolling();
  } catch (error) {
    $("#loginLoading").innerHTML = `
      <i data-lucide="triangle-alert"></i>
      <span>${escapeHtml(error.message)}</span>
    `;
    renderIcons();
    toast("登录页打开失败", error.message, "error");
  }
}

function startLoginPolling() {
  stopLoginPolling();
  state.loginPoller = window.setInterval(refreshLoginFrame, 3200);
}

function stopLoginPolling() {
  if (state.loginPoller) {
    window.clearInterval(state.loginPoller);
    state.loginPoller = null;
  }
}

async function refreshLoginFrame() {
  if (!state.loginActive || !$("#loginDialog").open) return;
  $("#loginScreenshot").src = `/api/login/screenshot?t=${Date.now()}`;
  try {
    const data = await api("/api/login/status");
    handleLoginStatus(data.login);
  } catch (error) {
    $("#loginPollLabel").textContent = "连接中断";
  }
}

function handleLoginStatus(login) {
  if (!login) return;
  if (login.state === "logged_in") {
    $("#loginPollLabel").textContent = "登录成功";
    $("#loginHint").textContent = "登录态和 Cookie 已保存，可以关闭窗口。";
    state.loginActive = false;
    stopLoginPolling();
    toast("登录成功", login.nickname || "腾讯账号登录态已保存。");
    refreshDashboard();
    window.setTimeout(() => {
      if ($("#loginDialog").open) $("#loginDialog").close();
    }, 1200);
    return;
  }
  if (login.state === "needs_binding") {
    $("#loginPollLabel").textContent = "等待正式服绑定";
    $("#loginHint").textContent =
      "已登录，但还没有正式服角色。请打开完整窗口完成角色绑定。";
    return;
  }
  if (login.state === "error") {
    $("#loginPollLabel").textContent = "检查失败";
    $("#loginHint").textContent = login.message;
    return;
  }
  $("#loginPollLabel").textContent = "等待扫码";
}

async function switchPlatform(platform) {
  state.currentPlatform = platform;
  updatePlatformSegments();
  try {
    const data = await api("/api/login/switch", {
      method: "POST",
      body: JSON.stringify({ platform }),
    });
    handleLoginStatus(data.login);
    $("#loginScreenshot").src = `/api/login/screenshot?t=${Date.now()}`;
    $("#loginLoading").classList.add("is-hidden");
  } catch (error) {
    toast("切换失败", error.message, "error");
  }
}

function updatePlatformSegments() {
  $$(".segment[data-platform]").forEach((button) => {
    button.classList.toggle(
      "active",
      button.dataset.platform === state.currentPlatform,
    );
  });
}

async function closeLogin() {
  stopLoginPolling();
  state.loginActive = false;
  const dialog = $("#loginDialog");
  if (dialog.open) dialog.close();
  try {
    await api("/api/login/close", { method: "POST", body: "{}" });
  } catch (error) {
    // The browser session may already have been closed by the user.
  }
}

async function openVisibleBrowser() {
  const button = $("#visibleFromDialogBtn");
  setBusy(true, button);
  try {
    await api("/api/browser/visible", { method: "POST", body: "{}" });
    toast(
      "完整窗口已打开",
      "请在弹出的 Chromium 窗口中完成登录或正式服角色绑定。",
      "warning",
      6500,
    );
  } catch (error) {
    toast("窗口打开失败", error.message, "error");
  } finally {
    setBusy(false, button);
  }
}

async function checkLogin() {
  const button = $("#checkLoginBtn");
  setBusy(true, button);
  try {
    const data = await api("/api/login/check", {
      method: "POST",
      body: "{}",
    });
    renderLoginStateFromStatus(data.login);
    if (data.login.state === "logged_in") {
      toast("登录有效", data.login.nickname || "Cookie 仍然有效。");
    } else if (data.login.state === "needs_binding") {
      toast("需要绑定", data.login.message, "warning");
    } else {
      toast("需要重新登录", data.login.message, "warning");
    }
    await refreshDashboard();
  } catch (error) {
    toast("检查失败", error.message, "error");
  } finally {
    setBusy(false, button);
  }
}

function renderLoginStateFromStatus(login) {
  if (!state.dashboard) return;
  state.dashboard.login = {
    state: login.state,
    authenticated: login.authenticated,
    bound: login.bound,
    checked_at: login.checked_at,
  };
  renderLoginState();
}

async function executeExchange() {
  if (state.busy) return;
  const reward = currentReward();
  if (!reward) return;
  const confirmed = window.confirm(
    `立即兑换「${reward.name}」？\n活动限制每个礼包每天一次。`,
  );
  if (!confirmed) return;
  const button = $("#exchangeBtn");
  setBusy(true, button);
  try {
    const data = await api("/api/exchange", {
      method: "POST",
      body: "{}",
    });
    const result = data.exchange;
    if (result.status === "success") {
      toast(
        "兑换完成",
        result.package_name
          ? `获得 ${result.package_name}`
          : result.message,
      );
    } else if (result.status === "login_expired") {
      toast("登录已过期", result.message, "warning", 6000);
      openLogin("qq");
    } else {
      toast("兑换未完成", result.message, "error", 6000);
      if (result.status === "needs_binding") {
        toast(
          "需要绑定正式服",
          "打开完整窗口完成正式服角色绑定后再执行。",
          "warning",
          6500,
        );
      }
    }
    await refreshDashboard();
  } catch (error) {
    toast("执行失败", error.message, "error");
  } finally {
    setBusy(false, button);
  }
}

async function testPush(provider) {
  const button = $(
    `.test-push[data-provider="${CSS.escape(provider)}"]`,
  );
  if (!button) return;
  const saved = await saveSettings({ silent: true });
  if (!saved) return;
  setBusy(true, button);
  try {
    const data = await api("/api/push/test", {
      method: "POST",
      body: JSON.stringify({ provider }),
    });
    const result = data.results[0];
    if (data.ok) {
      toast("测试已发送", result.detail);
    } else {
      toast("测试失败", result.detail, "error", 6000);
    }
  } catch (error) {
    toast("测试失败", error.message, "error");
  } finally {
    setBusy(false, button);
  }
}

function setBusy(busy, button) {
  state.busy = busy;
  document.body.classList.toggle("is-busy", busy);
  if (button) {
    button.disabled = busy;
    if (busy) {
      button.dataset.originalHtml = button.innerHTML;
      button.innerHTML = `
        <i data-lucide="loader-circle"></i>
        <span>处理中</span>
      `;
      renderIcons();
    } else if (button.dataset.originalHtml) {
      button.innerHTML = button.dataset.originalHtml;
      delete button.dataset.originalHtml;
      renderIcons();
    }
  }
}

function updateClock() {
  const date = new Date(Date.now() + state.clockOffset);
  $("#serverClock").textContent = date.toLocaleTimeString("zh-CN", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function activityVisual(status) {
  if (status === "success") {
    return { label: "成功", icon: "circle-check", className: "is-success" };
  }
  if (status === "login_expired") {
    return { label: "登录过期", icon: "log-in", className: "is-warning" };
  }
  if (status === "needs_binding") {
    return { label: "需要绑定", icon: "link-2-off", className: "is-warning" };
  }
  return { label: "失败", icon: "circle-x", className: "is-failed" };
}

function formatDateTime(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function formatClock(date) {
  if (!date || Number.isNaN(date.getTime())) return "--";
  return date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function relativeDateText(date) {
  const diff = date.getTime() - (Date.now() + state.clockOffset);
  if (diff <= 0) return "等待调度";
  const minutes = Math.ceil(diff / 60000);
  if (minutes < 60) return `约 ${minutes} 分钟后`;
  const hours = Math.floor(minutes / 60);
  const remain = minutes % 60;
  if (hours < 24) return `约 ${hours} 小时 ${remain} 分后`;
  return date.toLocaleDateString("zh-CN", {
    month: "long",
    day: "numeric",
    weekday: "short",
  });
}

function toast(title, message, type = "success", duration = 4200) {
  const icons = {
    success: "circle-check",
    error: "circle-x",
    warning: "triangle-alert",
  };
  const element = document.createElement("div");
  element.className = `toast ${type === "error" ? "is-error" : type === "warning" ? "is-warning" : ""}`;
  element.innerHTML = `
    <i data-lucide="${icons[type] || icons.success}"></i>
    <div>
      <strong>${escapeHtml(title)}</strong>
      <p>${escapeHtml(message || "")}</p>
    </div>
  `;
  $("#toastRegion").append(element);
  renderIcons();
  window.setTimeout(() => {
    element.style.opacity = "0";
    element.style.transform = "translateY(4px)";
    window.setTimeout(() => element.remove(), 180);
  }, duration);
}

function renderIcons() {
  if (window.lucide) {
    window.lucide.createIcons({
      attrs: {
        "stroke-width": 1.8,
      },
    });
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value);
}

