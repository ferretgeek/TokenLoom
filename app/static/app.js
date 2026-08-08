(() => {
  "use strict";

  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const state = {
    cursor: 0,
    history: [],
    page: 1,
    nextCursor: null,
    hasMore: false,
    rows: [],
    selected: new Set(),
    totalAccounts: 0,
    activeJobs: false,
    pollTimer: null,
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  function responseError(payload, status) {
    const detail = payload?.detail || payload?.error;
    if (Array.isArray(detail)) return detail.map(item => item?.msg || "参数无效").join("；");
    if (typeof detail === "string" && detail) return detail;
    return `请求失败（${status}）`;
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
    if (options.method && !["GET", "HEAD"].includes(options.method.toUpperCase())) headers.set("X-CSRF-Token", csrf);
    const response = await fetch(path, { credentials: "same-origin", ...options, headers });
    if (response.status === 401) {
      location.assign("/login");
      throw new Error("登录已失效");
    }
    let payload = {};
    try { payload = await response.json(); } catch (_) { /* empty response */ }
    if (!response.ok) throw new Error(responseError(payload, response.status));
    return payload;
  }

  function toast(title, message = "", type = "success") {
    const item = document.createElement("div");
    item.className = `toast ${type}`;
    item.innerHTML = `<span class="status-dot"></span><div><b></b><p></p></div>`;
    $("b", item).textContent = title;
    $("p", item).textContent = message;
    $("#toast-stack").append(item);
    setTimeout(() => item.remove(), 4200);
  }

  function number(value) {
    return new Intl.NumberFormat("zh-CN").format(Number(value || 0));
  }

  function dateTime(value, empty = "尚未记录") {
    if (!value) return { main: empty, sub: "—" };
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return { main: empty, sub: "—" };
    return {
      main: new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date),
      sub: new Intl.DateTimeFormat("zh-CN", { year: "numeric" }).format(date),
    };
  }

  function relative(value) {
    if (!value) return "未安排";
    const delta = new Date(value).getTime() - Date.now();
    const days = Math.ceil(Math.abs(delta) / 86400000);
    if (Math.abs(delta) < 3600000) return delta >= 0 ? "一小时内" : "已到期";
    if (Math.abs(delta) < 86400000) return delta >= 0 ? "今天" : "已到期";
    return delta >= 0 ? `${days} 天后` : `逾期 ${days} 天`;
  }

  function jobName(job) {
    const names = { import: "邮箱导入", refresh: "令牌刷新", health: "取件体检" };
    const scopes = { selected: "所选账号", all: "全部账号", due: "到期账号", file: job.source_name || "导入文件" };
    return `${names[job.kind] || job.kind} · ${scopes[job.scope] || job.scope}`;
  }

  function jobState(status) {
    return ({ queued: "等待中", running: "进行中", completed: "已完成", failed: "失败", cancelled: "已取消" })[status] || status;
  }

  function renderJobs(items, detailed = false) {
    if (!items.length) return `<div class="empty-state"><b>暂无任务</b><p>导入或刷新后，进度会显示在这里。</p></div>`;
    return items.map(job => {
      const progress = Math.min(100, Math.max(0, Number(job.progress) || 0));
      return `
      <article class="job-card" data-job-id="${job.id}">
        <div class="job-card-head">
          <span class="job-kind">${job.kind === "health" ? "检" : job.kind === "import" ? "入" : "刷"}</span>
          <div><b>${escapeHtml(jobName(job))}</b><small>${number(job.processed)} / ${number(job.total)} · 成功 ${number(job.succeeded)} · 失败 ${number(job.failed)}</small></div>
          <span class="job-state ${job.status}">${jobState(job.status)}</span>
        </div>
        <progress class="job-progress" max="100" value="${progress}" aria-label="任务进度 ${progress}%"></progress>
        <div class="job-stats"><span>${job.progress || 0}%</span><span>${job.created_at ? dateTime(job.created_at).main : ""}</span></div>
        ${detailed && job.error ? `<p class="job-error">${escapeHtml(job.error)}</p>` : ""}
        ${detailed && ["queued", "running"].includes(job.status) ? `<button class="cancel-job" data-cancel-job="${job.id}">请求取消任务</button>` : ""}
      </article>`;
    }).join("");
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
  }

  async function loadDashboard(silent = false) {
    try {
      const data = await api("/api/dashboard");
      const previousTotal = state.totalAccounts;
      const previousActive = state.activeJobs;
      state.totalAccounts = Number(data.counts.total || 0);
      Object.entries(data.counts).forEach(([key, value]) => {
        $$(`[data-count="${key}"]`).forEach(element => { element.textContent = number(value); });
      });
      $("#policy-days").textContent = data.policy.interval_days;
      const total = Math.max(1, data.counts.total);
      $("#healthy-meter").value = Math.min(100, data.counts.healthy / total * 100);
      $("#recent-jobs").innerHTML = renderJobs(data.jobs);
      const active = data.jobs.some(job => ["queued", "running"].includes(job.status));
      state.activeJobs = active;
      if (previousTotal !== state.totalAccounts || (previousActive && !active)) {
        void loadAccounts({ reset: true });
      }
      clearTimeout(state.pollTimer);
      state.pollTimer = setTimeout(() => loadDashboard(true), active ? 3500 : 15000);
    } catch (error) {
      if (!silent) toast("总览读取失败", error.message, "error");
    }
  }

  function renderAccounts() {
    const target = $("#account-rows");
    if (!state.rows.length) {
      const trulyEmpty = state.totalAccounts === 0;
      target.innerHTML = `<tr><td colspan="7"><div class="empty-state"><b>${trulyEmpty ? "还没有导入邮箱" : "没有符合条件的邮箱"}</b><p>${trulyEmpty ? "先导入 TXT 或粘贴账号，系统会在后台安全处理。" : "请更换状态筛选或输入完整邮箱重新查找。"}</p>${trulyEmpty ? '<button class="button button-primary" data-empty-import>立即导入邮箱</button>' : ""}</div></td></tr>`;
      return;
    }
    target.innerHTML = state.rows.map(account => {
      const refresh = dateTime(account.last_refresh_at);
      const expiry = dateTime(account.token_expires_at, "有效期未知");
      const next = dateTime(account.next_refresh_at, "停止自动刷新");
      const checked = state.selected.has(account.id) ? "checked" : "";
      return `<tr data-account-id="${account.id}">
        <td class="check-cell"><input type="checkbox" data-select-id="${account.id}" ${checked} aria-label="选择账号 ${account.id}"></td>
        <td><div class="email-cell"><span class="email-avatar">${escapeHtml(account.domain.slice(0, 1) || "M")}</span><div>${escapeHtml(account.email)}<small>${escapeHtml(account.source)}</small></div></div></td>
        <td><span class="status-badge ${escapeHtml(account.status)}" title="${escapeHtml(account.error || "")}">${escapeHtml(account.status_label)}</span></td>
        <td><span class="date-main">${refresh.main}</span><span class="date-sub">${refresh.sub}</span></td>
        <td><span class="date-main">${expiry.main}</span><span class="date-sub">${account.token_expires_at ? relative(account.token_expires_at) : "建议抽样检测"}</span></td>
        <td><span class="date-main">${next.main}</span><span class="date-sub">${relative(account.next_refresh_at)}</span></td>
        <td class="right"><div class="row-actions"><button class="row-action" data-row-refresh="${account.id}">刷新</button><button class="row-action" data-row-health="${account.id}">体检</button></div></td>
      </tr>`;
    }).join("");
  }

  function updateSelectionUi() {
    const count = state.selected.size;
    $("#selected-count").textContent = count;
    $("#bulk-line").hidden = count === 0;
    for (const id of ["refresh-selected", "health-selected", "delete-selected"]) $(`#${id}`).disabled = count === 0;
    const pageIds = state.rows.map(item => item.id);
    $("#select-page").checked = pageIds.length > 0 && pageIds.every(id => state.selected.has(id));
    $("#select-page").indeterminate = pageIds.some(id => state.selected.has(id)) && !$("#select-page").checked;
  }

  async function loadAccounts({ reset = false } = {}) {
    if (reset) {
      state.cursor = 0; state.history = []; state.page = 1; state.selected.clear();
    }
    $("#account-rows").innerHTML = `<tr><td colspan="7"><div class="loading-state"><span></span>正在读取邮箱资产…</div></td></tr>`;
    const params = new URLSearchParams({
      cursor: state.cursor,
      limit: $("#page-size").value,
      status: $("#status-filter").value,
      email: $("#email-search").value.trim(),
    });
    try {
      const data = await api(`/api/accounts?${params}`);
      state.rows = data.items;
      state.hasMore = data.has_more;
      state.nextCursor = data.next_cursor;
      renderAccounts();
      updateSelectionUi();
      $("#page-summary").textContent = `第 ${state.page} 页 · 本页 ${data.items.length} 个`;
      $("#prev-page").disabled = state.history.length === 0;
      $("#next-page").disabled = !data.has_more;
    } catch (error) {
      $("#account-rows").innerHTML = `<tr><td colspan="7"><div class="empty-state"><b>读取失败</b><p>${escapeHtml(error.message)}</p></div></td></tr>`;
    }
  }

  async function queueJob(kind, scope, ids = [], confirm = false) {
    const data = await api("/api/jobs", { method: "POST", body: JSON.stringify({ kind, scope, ids, confirm }) });
    toast("任务已进入持久队列", `${jobName(data.job)} · 共 ${number(data.job.total)} 个`);
    state.selected.clear();
    updateSelectionUi();
    await loadDashboard(true);
    return data.job;
  }

  function openLayer(id) {
    $("#modal-backdrop").hidden = false;
    const layer = $(`#${id}`);
    layer.hidden = false;
    document.body.classList.add("layer-open");
    if (id === "settings-modal") loadSettings();
    if (id === "jobs-drawer") loadAllJobs();
  }

  function closeLayers() {
    $("#modal-backdrop").hidden = true;
    $$(".modal, .drawer, .confirm").forEach(layer => { layer.hidden = true; });
    document.body.classList.remove("layer-open");
  }

  function ask(title, message, okLabel = "确认") {
    return new Promise(resolve => {
      $("#confirm-title").textContent = title;
      $("#confirm-message").textContent = message;
      $("#confirm-ok").textContent = okLabel;
      $("#modal-backdrop").hidden = false;
      $("#confirm-dialog").hidden = false;
      document.body.classList.add("layer-open");
      const finish = value => {
        $("#confirm-ok").onclick = null;
        $("#confirm-cancel").onclick = null;
        closeLayers(); resolve(value);
      };
      $("#confirm-ok").onclick = () => finish(true);
      $("#confirm-cancel").onclick = () => finish(false);
    });
  }

  async function loadSettings() {
    try {
      const data = await api("/api/settings");
      const form = $("#settings-form");
      Object.entries(data).forEach(([key, value]) => { if (form.elements[key]) form.elements[key].value = value; });
    } catch (error) { toast("策略读取失败", error.message, "error"); }
  }

  async function loadAllJobs() {
    $("#all-jobs").innerHTML = `<div class="loading-state"><span></span>读取任务…</div>`;
    try {
      const data = await api("/api/jobs?limit=50");
      $("#all-jobs").innerHTML = renderJobs(data.items, true);
    } catch (error) { $("#all-jobs").innerHTML = `<div class="empty-state"><b>读取失败</b><p>${escapeHtml(error.message)}</p></div>`; }
  }

  function initTheme() {
    const availableThemes = new Set(["jade", "sky", "sunset"]);
    const storedTheme = localStorage.getItem("token-loom-theme");
    const savedTheme = availableThemes.has(storedTheme) ? storedTheme : "jade";
    const savedMode = localStorage.getItem("token-loom-mode") === "dark" ? "dark" : "light";
    document.documentElement.dataset.theme = savedTheme;
    if (savedMode === "dark") document.documentElement.dataset.mode = "dark";
    const toggle = $("#dark-toggle");
    if (toggle) {
      const label = savedMode === "dark" ? "切换到浅色模式" : "切换到深灰模式";
      toggle.setAttribute("aria-label", label);
      toggle.setAttribute("title", label);
    }
    $$('[data-theme-choice]').forEach(button => button.classList.toggle("active", button.dataset.themeChoice === savedTheme));
  }

  function bindEvents() {
    $("#palette-button").addEventListener("click", event => {
      event.stopPropagation(); $("#theme-popover").hidden = !$("#theme-popover").hidden;
    });
    document.addEventListener("click", event => {
      if (!event.target.closest(".theme-wrap")) $("#theme-popover").hidden = true;
    });
    $$('[data-theme-choice]').forEach(button => button.addEventListener("click", () => {
      const theme = button.dataset.themeChoice;
      document.documentElement.dataset.theme = theme;
      localStorage.setItem("token-loom-theme", theme);
      $$('[data-theme-choice]').forEach(item => item.classList.toggle("active", item === button));
    }));
    $("#dark-toggle").addEventListener("click", () => {
      const dark = document.documentElement.dataset.mode !== "dark";
      if (dark) document.documentElement.dataset.mode = "dark"; else delete document.documentElement.dataset.mode;
      localStorage.setItem("token-loom-mode", dark ? "dark" : "light");
      $("#dark-toggle").setAttribute("aria-label", dark ? "切换到浅色模式" : "切换到深灰模式");
      $("#dark-toggle").setAttribute("title", dark ? "切换到浅色模式" : "切换到深灰模式");
    });

    $$('[data-open]').forEach(button => button.addEventListener("click", () => {
      const map = { import: "import-modal", settings: "settings-modal", jobs: "jobs-drawer" };
      openLayer(map[button.dataset.open]);
    }));
    $$('[data-close]').forEach(button => button.addEventListener("click", closeLayers));
    $("#modal-backdrop").addEventListener("click", closeLayers);
    document.addEventListener("keydown", event => { if (event.key === "Escape") closeLayers(); });

    $("#mobile-menu").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
    $("#logout-button").addEventListener("click", async () => {
      try { await api("/logout", { method: "POST" }); } finally { location.assign("/login"); }
    });
    $$('[data-focus="accounts"]').forEach(button => button.addEventListener("click", () => {
      $("#accounts-section").scrollIntoView({ behavior: "smooth" }); $("#sidebar").classList.remove("open");
      if (state.totalAccounts === 0) {
        toast("尚未导入邮箱", "请先粘贴账号或选择 TXT 文件。", "info");
        setTimeout(() => openLayer("import-modal"), 280);
      }
    }));

    $$('[data-import-tab]').forEach(button => button.addEventListener("click", () => {
      $$('[data-import-tab]').forEach(item => item.classList.toggle("active", item === button));
      $$('[data-import-pane]').forEach(pane => pane.classList.toggle("active", pane.dataset.importPane === button.dataset.importTab));
    }));
    $("#import-file").addEventListener("change", event => {
      $("#file-name").textContent = event.target.files[0]?.name || "支持流式导入，适合大体量数据";
    });
    for (const eventName of ["dragenter", "dragover"]) $("#drop-zone").addEventListener(eventName, event => { event.preventDefault(); $("#drop-zone").classList.add("dragging"); });
    for (const eventName of ["dragleave", "drop"]) $("#drop-zone").addEventListener(eventName, () => $("#drop-zone").classList.remove("dragging"));

    $("#import-form").addEventListener("submit", async event => {
      event.preventDefault();
      const button = $("#import-submit");
      button.disabled = true; button.textContent = "正在安全上传…";
      try {
        const form = event.currentTarget;
        const pasteMode = $('[data-import-tab="paste"]').classList.contains("active");
        let data;
        if (pasteMode) {
          data = await api("/api/import/paste", {
            method: "POST",
            body: JSON.stringify({
              text: form.elements.text.value,
              source_name: form.elements.source_name.value,
              duplicate_mode: form.elements.duplicate_mode.value,
            }),
          });
        } else {
          const upload = new FormData(form);
          upload.delete("text");
          data = await api("/api/import", { method: "POST", body: upload });
        }
        closeLayers(); form.reset(); $("#file-name").textContent = "支持流式导入，适合大体量数据";
        toast("导入任务已创建", `正在后台处理 ${number(data.job.total)} 行，页面可以安全关闭。`);
        await loadDashboard(true);
      } catch (error) { toast("导入失败", error.message, "error"); }
      finally { button.disabled = false; button.textContent = "开始安全导入"; }
    });

    $("#settings-form").addEventListener("submit", async event => {
      event.preventDefault();
      const raw = Object.fromEntries(new FormData(event.currentTarget));
      for (const key of ["interval_days", "refresh_concurrency", "health_concurrency", "max_retries"]) raw[key] = Number(raw[key]);
      try {
        await api("/api/settings", { method: "PUT", body: JSON.stringify(raw) });
        closeLayers(); toast("刷新策略已保存", `今后每 ${raw.interval_days} 天自动续期。`); await loadDashboard(true);
      } catch (error) { toast("保存失败", error.message, "error"); }
    });

    $("#status-filter").addEventListener("change", () => loadAccounts({ reset: true }));
    $("#page-size").addEventListener("change", () => loadAccounts({ reset: true }));
    $("#search-submit").addEventListener("click", () => loadAccounts({ reset: true }));
    $("#email-search").addEventListener("keydown", event => { if (event.key === "Enter") loadAccounts({ reset: true }); });
    $("#next-page").addEventListener("click", () => {
      if (!state.hasMore) return;
      state.history.push(state.cursor); state.cursor = state.nextCursor; state.page += 1; loadAccounts();
    });
    $("#prev-page").addEventListener("click", () => {
      if (!state.history.length) return;
      state.cursor = state.history.pop(); state.page = Math.max(1, state.page - 1); loadAccounts();
    });
    $("#select-page").addEventListener("change", event => {
      state.rows.forEach(item => event.target.checked ? state.selected.add(item.id) : state.selected.delete(item.id));
      renderAccounts(); updateSelectionUi();
    });
    $("#clear-selection").addEventListener("click", () => { state.selected.clear(); renderAccounts(); updateSelectionUi(); });
    $("#account-rows").addEventListener("change", event => {
      const id = Number(event.target.dataset.selectId);
      if (!id) return;
      event.target.checked ? state.selected.add(id) : state.selected.delete(id); updateSelectionUi();
    });
    $("#account-rows").addEventListener("click", async event => {
      if (event.target.closest("[data-empty-import]")) {
        openLayer("import-modal");
        return;
      }
      const refreshId = Number(event.target.dataset.rowRefresh);
      const healthId = Number(event.target.dataset.rowHealth);
      try {
        if (refreshId) await queueJob("refresh", "selected", [refreshId]);
        if (healthId) await queueJob("health", "selected", [healthId]);
      } catch (error) { toast("任务创建失败", error.message, "error"); }
    });
    $("#refresh-selected").addEventListener("click", async () => {
      try { await queueJob("refresh", "selected", [...state.selected]); } catch (error) { toast("任务创建失败", error.message, "error"); }
    });
    $("#health-selected").addEventListener("click", async () => {
      try { await queueJob("health", "selected", [...state.selected]); } catch (error) { toast("任务创建失败", error.message, "error"); }
    });
    $("#delete-selected").addEventListener("click", async () => {
      const count = state.selected.size;
      if (!await ask("删除所选邮箱？", `将永久删除 ${count} 个邮箱及其加密凭据，此操作不可撤销。`, "确认删除")) return;
      try {
        const data = await api("/api/accounts", { method: "DELETE", body: JSON.stringify({ ids: [...state.selected], confirm: true }) });
        state.selected.clear(); toast("邮箱已删除", `共删除 ${number(data.deleted)} 个账号。`); await Promise.all([loadAccounts({ reset: true }), loadDashboard(true)]);
      } catch (error) { toast("删除失败", error.message, "error"); }
    });
    $$('[data-action="refresh-due"]').forEach(button => button.addEventListener("click", async () => {
      try { await queueJob("refresh", "due"); } catch (error) { toast("任务创建失败", error.message, "error"); }
    }));
    $$('[data-action="health-all"]').forEach(button => button.addEventListener("click", async () => {
      if (!await ask("开始全部取件体检？", "系统会逐个刷新令牌并真实连接 IMAP，只读打开收件箱。大批量体检可能持续较久。", "开始体检")) return;
      try { await queueJob("health", "all", [], true); } catch (error) { toast("任务创建失败", error.message, "error"); }
    }));
    $("#all-jobs").addEventListener("click", async event => {
      const id = event.target.dataset.cancelJob;
      if (!id || !await ask("取消任务？", "已完成的批次会保留，任务将在当前小批次结束后停止。", "请求取消")) return;
      try { await api(`/api/jobs/${id}/cancel`, { method: "POST" }); toast("已请求取消", "Worker 会在安全检查点停止。" ); await loadAllJobs(); }
      catch (error) { toast("取消失败", error.message, "error"); }
    });
  }

  function initialize() {
    initTheme();
    const date = new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric", weekday: "long" }).format(new Date());
    $("#today-label").textContent = `${date} · 续航状态`;
    bindEvents();
    loadDashboard();
    loadAccounts({ reset: true });
  }

  initialize();
})();
