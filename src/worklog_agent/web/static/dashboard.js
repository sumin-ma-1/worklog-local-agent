const $ = (sel) => document.querySelector(sel);

const state = {
  view: "journals",
  dialogs: [],
  loginStage: "idle",
  authorized: false,
};

let toastTimer = null;

function showBanner(message, kind) {
  const el = $("#toast");
  if (!el) return;
  if (toastTimer) {
    clearTimeout(toastTimer);
    toastTimer = null;
  }
  if (!message) {
    el.className = "toast hidden";
    el.innerHTML = "";
    return;
  }
  const icon =
    kind === "ok"
      ? `<span class="toast-icon" aria-hidden="true"><span class="material-symbols-outlined">task_alt</span></span>`
      : "";
  el.innerHTML = `${icon}<span class="toast-text">${escapeHtml(message)}</span>`;
  el.className = `toast show ${kind || "info"}${icon ? " has-icon" : ""}`;
  toastTimer = setTimeout(() => {
    el.classList.remove("show");
    toastTimer = setTimeout(() => {
      el.className = "toast hidden";
      el.innerHTML = "";
      toastTimer = null;
    }, 200);
  }, kind === "error" ? 3200 : 2200);
}

async function api(path, options) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options && options.headers) },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail;
    throw new Error(typeof detail === "string" ? detail : response.statusText);
  }
  return data;
}

function setView(name) {
  if (!state.authorized) return;
  state.view = name;
  document.querySelectorAll("#app-shell .view").forEach((el) => el.classList.add("hidden"));
  document.querySelectorAll(".nav-btn[data-view]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === name);
  });
  $(`#view-${name}`)?.classList.remove("hidden");
  if (name === "chats") {
    requestAnimationFrame(updateDialogScrollFade);
  }
  if (name === "journals") {
    requestAnimationFrame(updateJournalScrollFade);
  }
  if (name === "run") {
    syncRunStepsForSelectedDate();
  }
}

function applyAuthVisibility(authorized) {
  state.authorized = Boolean(authorized);
  document.body.classList.toggle("login-mode", !state.authorized);
  $("#app-shell")?.classList.toggle("hidden", !state.authorized);
  $("#view-login")?.classList.toggle("hidden", state.authorized);
}

function renderLoginForms(stage) {
  state.loginStage = stage || "idle";
  $("#login-form")?.classList.toggle(
    "hidden",
    state.loginStage === "code" || state.loginStage === "password"
  );
  $("#login-code-form")?.classList.toggle("hidden", state.loginStage !== "code");
  $("#login-password-form")?.classList.toggle("hidden", state.loginStage !== "password");
}

function fillCredentials(credentials) {
  if (!credentials) return;
  if (credentials.api_id) $("#api-id-input").value = credentials.api_id;
  if (credentials.phone) $("#login-phone-input").value = credentials.phone;
  $("#api-hash-input").placeholder = credentials.has_api_hash
    ? `저장됨 (${credentials.api_hash_masked}) — 변경 시에만 입력`
    : "API Hash";
  $("#api-hash-input").value = "";
}

function fillTelegramStatus(telegram, stage) {
  if (!telegram) return;
  applyAuthVisibility(Boolean(telegram.authorized));
  if (telegram.authorized && telegram.user) {
    const user = telegram.user;
    $("#login-status").textContent = `로그인됨 · ${user.name}${user.username ? ` (@${user.username})` : ""}`;
  } else {
    $("#login-status").textContent = telegram.message || "로그인 필요";
    renderLoginForms(stage || state.loginStage);
  }
}

async function enterDashboard() {
  await loadOverview();
  setView("journals");
  loadJournals();
  pollJob();
}

async function doLogout() {
  const data = await api("/api/telegram/logout", { method: "POST", body: "{}" });
  state.dialogs = [];
  state.loginStage = "idle";
  applyAuthVisibility(false);
  fillCredentials(data.credentials);
  fillTelegramStatus({ authorized: false, user: null, message: data.message }, "idle");
  renderLoginForms("idle");
  showBanner(data.message, "ok");
}

async function loadOverview() {
  const data = await api("/api/overview");
  const tg = data.telegram || {};
  fillCredentials(data.credentials);
  fillTelegramStatus(data.telegram, data.login_stage);
  const authLabel = tg.authorized
    ? `TG ${escapeHtml(tg.user?.name || "로그인됨")}`
    : "TG 미로그인";
  if ($("#overview-meta")) {
    $("#overview-meta").innerHTML = `
      <div>${data.timezone}</div>
      <div>모델 ${escapeHtml(data.model)}</div>
      <div>업무방 ${data.chat_count} · 일지 ${data.journal_count}</div>
      <div>${authLabel}</div>
    `;
  }
  renderJob(data.job);
}

async function loadJournals(selectDay) {
  if (!state.authorized) return;
  const data = await api("/api/journals");
  const list = $("#journal-list");
  if (!data.journals.length) {
    list.innerHTML = `<li class="empty">아직 생성된 일지가 없습니다.</li>`;
    $("#journal-detail").innerHTML = `<p class="empty">왼쪽에서 날짜를 선택하세요.</p>`;
    return;
  }
  list.innerHTML = data.journals
    .map(
      (item) => `
      <li>
        <button class="link" data-date="${item.date}">
          ${item.date}
          <span class="meta">첨부 ${item.attachments}</span>
        </button>
      </li>`
    )
    .join("");
  list.querySelectorAll("button[data-date]").forEach((btn) => {
    btn.addEventListener("click", () => loadJournal(btn.dataset.date));
  });
  updateJournalScrollFade();
  if (selectDay) {
    await loadJournal(selectDay);
  }
}

function updateJournalScrollFade() {
  updateScrollFade($("#journal-list-scroll"), $("#journal-list-wrap"));
  updateScrollFade($("#journal-detail-scroll"), $("#journal-detail-wrap"));
  updateScrollFade($("#journal-editor"), $("#journal-editor-wrap"));
}

function renderJournalDetail(data, { editing = false } = {}) {
  const attach = (data.attachments || [])
    .map(
      (file) =>
        `<li><a href="/api/attachments/file?path=${encodeURIComponent(file.relative)}" target="_blank" rel="noreferrer">${escapeHtml(file.name)}</a></li>`
    )
    .join("");
  const markdown = data.markdown || "";
  const hasJournal = Boolean(data.has_journal || markdown);
  const body = editing
    ? `<div class="journal-editor-wrap" id="journal-editor-wrap">
        <textarea id="journal-editor" class="journal-editor" spellcheck="false">${escapeHtml(markdown)}</textarea>
      </div>`
    : `<div class="list-scroll-wrap" id="journal-detail-wrap">
        <div class="list-scroll" id="journal-detail-scroll">
          <div class="journal-body markdown-body">${renderMarkdown(markdown)}</div>
        </div>
      </div>`;
  const actions = editing
    ? `
      <button type="button" id="journal-save" class="icon-btn ok-icon" title="저장" aria-label="저장">
        <span class="material-symbols-outlined" aria-hidden="true">save</span>
      </button>
      <button type="button" id="journal-cancel" class="icon-btn" title="취소" aria-label="취소">
        <span class="material-symbols-outlined" aria-hidden="true">undo</span>
      </button>
    `
    : `
      <button type="button" id="journal-edit" class="icon-btn" title="수정" aria-label="수정">
        <span class="material-symbols-outlined" aria-hidden="true">edit</span>
      </button>
      <button type="button" id="journal-delete" class="icon-btn danger-icon" title="삭제" aria-label="삭제" ${hasJournal ? "" : "disabled"}>
        <span class="material-symbols-outlined" aria-hidden="true">delete</span>
      </button>
    `;
  $("#journal-detail").innerHTML = `
    <div class="row-head">
      <h3>${escapeHtml(data.date)}</h3>
      <div class="journal-actions">${actions}</div>
    </div>
    ${body}
    <div class="journal-attach">
      <h3>첨부</h3>
      <ul class="attach-list">${attach || `<li class="empty">첨부 없음</li>`}</ul>
    </div>
  `;

  $("#journal-detail-scroll")?.addEventListener("scroll", updateJournalScrollFade, { passive: true });
  $("#journal-editor")?.addEventListener("scroll", updateJournalScrollFade, { passive: true });

  if (editing) {
    $("#journal-save")?.addEventListener("click", () => saveJournal(data.date));
    $("#journal-cancel")?.addEventListener("click", () => loadJournal(data.date));
    $("#journal-editor")?.addEventListener("input", updateJournalScrollFade);
    $("#journal-editor")?.focus();
    requestAnimationFrame(updateJournalScrollFade);
    return;
  }
  $("#journal-edit")?.addEventListener("click", () => renderJournalDetail(data, { editing: true }));
  $("#journal-delete")?.addEventListener("click", () => deleteJournal(data.date));
  updateJournalScrollFade();
}

async function loadJournal(day) {
  const data = await api(`/api/journals/${day}`);
  renderJournalDetail(data);
}

async function saveJournal(day) {
  const editor = $("#journal-editor");
  if (!editor) return;
  try {
    await api(`/api/journals/${day}`, {
      method: "PUT",
      body: JSON.stringify({ markdown: editor.value }),
    });
    showBanner(`${day} 일지를 저장했습니다.`, "ok");
    await loadJournals(day);
    await loadOverview();
  } catch (err) {
    showBanner(err.message, "error");
  }
}

async function deleteJournal(day) {
  if (!window.confirm(`${day} 일지를 삭제할까요?`)) return;
  try {
    await api(`/api/journals/${day}`, { method: "DELETE" });
    showBanner(`${day} 일지를 삭제했습니다.`, "ok");
    $("#journal-detail").innerHTML = `<p class="empty">왼쪽에서 날짜를 선택하세요.</p>`;
    await loadJournals();
    await loadOverview();
    await syncRunStepsForSelectedDate();
  } catch (err) {
    showBanner(err.message, "error");
  }
}

async function loadWatched() {
  const data = await api("/api/chats");
  const list = $("#watched-list");
  if (!data.chats.length) {
    list.innerHTML = `<li class="empty">등록된 업무방이 없습니다.</li>`;
    return;
  }
  list.innerHTML = data.chats
    .map(
      (chat) => `
      <li>
        <div>
          <strong>${escapeHtml(String(chat.title))}</strong>
          <span class="meta">${escapeHtml(String(chat.id))}</span>
        </div>
        <button class="danger icon-action" data-id="${escapeHtml(String(chat.id))}" title="삭제" aria-label="삭제">
          <span class="material-symbols-outlined" aria-hidden="true">delete</span>
        </button>
      </li>`
    )
    .join("");
  list.querySelectorAll("button.danger").forEach((btn) => {
    btn.addEventListener("click", () => removeChat(btn.dataset.id));
  });
}

function updateScrollFade(scroll, wrap) {
  if (!scroll || !wrap) return;
  const more = scroll.scrollTop + scroll.clientHeight < scroll.scrollHeight - 2;
  wrap.classList.toggle("has-more", more);
}

function updateDialogScrollFade() {
  updateScrollFade($("#dialog-scroll"), $("#dialog-scroll-wrap"));
}

function renderDialogs(filter) {
  const q = (filter || "").trim().toLowerCase();
  const rows = state.dialogs.filter((item) => {
    const hay = `${item.title} ${item.id}`.toLowerCase();
    return !q || hay.includes(q);
  });
  const list = $("#dialog-list");
  if (!rows.length) {
    list.innerHTML = `<li class="empty">표시할 대화가 없습니다.</li>`;
    updateDialogScrollFade();
    return;
  }
  list.innerHTML = rows
    .map((item) => {
      const action = item.watched
        ? `<button class="danger icon-action" data-id="${item.id}" title="삭제" aria-label="삭제"><span class="material-symbols-outlined" aria-hidden="true">delete</span></button>`
        : `<button class="link btn-with-icon icon-action" data-add="${item.id}" title="추가" aria-label="추가"><span class="material-symbols-outlined" aria-hidden="true">add</span></button>`;
      return `
        <li>
          <div>
            <strong>${escapeHtml(item.title || "(제목 없음)")}</strong>
            <span class="meta">${item.id} · ${escapeHtml(item.type)}</span>
          </div>
          ${action}
        </li>`;
    })
    .join("");
  list.querySelectorAll("[data-add]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.add;
      const match = state.dialogs.find((item) => String(item.id) === String(id));
      addChat(id, match?.title);
    });
  });
  list.querySelectorAll("button.danger").forEach((btn) => {
    btn.addEventListener("click", () => removeChat(btn.dataset.id));
  });
  updateDialogScrollFade();
}

async function loadDialogs() {
  $("#dialog-list").innerHTML = `<li class="empty">불러오는 중…</li>`;
  updateDialogScrollFade();
  try {
    const data = await api("/api/dialogs");
    state.dialogs = data.dialogs || [];
    renderDialogs($("#dialog-filter").value);
    await loadWatched();
  } catch (err) {
    state.dialogs = [];
    $("#dialog-list").innerHTML = `<li class="empty">${escapeHtml(err.message)}</li>`;
    updateDialogScrollFade();
    showBanner(err.message, "error");
  }
}

async function addChat(id, title) {
  try {
    const known =
      title ||
      state.dialogs.find((item) => String(item.id) === String(id))?.title ||
      null;
    await api("/api/chats", {
      method: "POST",
      body: JSON.stringify({ id, title: known || null }),
    });
    showBanner(`업무방 ${id} 을(를) 추가했습니다.`, "ok");
    await Promise.all([loadWatched(), loadOverview()]);
    if (state.dialogs.length) {
      state.dialogs = state.dialogs.map((item) =>
        String(item.id) === String(id) ? { ...item, watched: true } : item
      );
      renderDialogs($("#dialog-filter").value);
    }
  } catch (err) {
    showBanner(err.message, "error");
  }
}

async function removeChat(id) {
  try {
    await api("/api/chats/delete", { method: "POST", body: JSON.stringify({ id }) });
    showBanner(`업무방 ${id} 을(를) 삭제했습니다.`, "ok");
    await Promise.all([loadWatched(), loadOverview()]);
    if (state.dialogs.length) {
      state.dialogs = state.dialogs.map((item) =>
        String(item.id) === String(id) ? { ...item, watched: false } : item
      );
      renderDialogs($("#dialog-filter").value);
    }
  } catch (err) {
    showBanner(err.message, "error");
  }
}

let lastJobStatus = null;
const JOB_STEP_ORDER = ["collect", "archive", "organize", "model", "journal"];

function setRunButtonBusy(running) {
  const button = $("#run-button");
  if (!button) return;
  button.disabled = running;
  button.classList.toggle("is-busy", running);
  button.setAttribute("aria-busy", running ? "true" : "false");
}

function renderJobSteps({ running = false, current = null, complete = false, error = false } = {}) {
  const steps = $("#job-steps");
  if (!steps) return;
  const currentIndex = JOB_STEP_ORDER.indexOf(current);
  steps.classList.toggle("is-active", running || complete || error);
  steps.querySelectorAll("li[data-step]").forEach((li) => {
    const step = li.dataset.step;
    const index = JOB_STEP_ORDER.indexOf(step);
    li.classList.toggle("is-current", running && step === current);
    li.classList.toggle(
      "is-done",
      complete || (running && currentIndex > index) || (error && currentIndex > index)
    );
    li.classList.toggle("is-error", error && step === current);
  });
}

function renderJob(job) {
  if (!job) return;
  const running = job.status === "running";
  setRunButtonBusy(running);
  if (running) {
    renderJobSteps({
      running: true,
      current: job.step || null,
      error: false,
      complete: false,
    });
    return;
  }
  if (job.status === "error") {
    renderJobSteps({
      running: false,
      current: job.step || null,
      error: true,
      complete: false,
    });
    return;
  }
  // idle/done: 선택 날짜의 일지 유무에 맞춤
  syncRunStepsForSelectedDate();
}

async function syncRunStepsForSelectedDate() {
  if (lastJobStatus === "running") return;
  const day = $("#run-date")?.value;
  if (!day) {
    renderJobSteps({ complete: false });
    return;
  }
  try {
    const data = await api(`/api/journals/${day}`);
    renderJobSteps({ complete: Boolean(data.has_journal) });
  } catch (_) {
    renderJobSteps({ complete: false });
  }
}

async function pollJob() {
  if (!state.authorized) return;
  const job = await api("/api/job");
  const prev = lastJobStatus;
  lastJobStatus = job.status;
  if (job.status === "running") {
    renderJob(job);
    setTimeout(pollJob, 400);
    return;
  }
  if (prev === "running" && job.status === "done") {
    showBanner("일지 생성을 마쳤습니다.", "ok");
    setRunButtonBusy(false);
    await syncRunStepsForSelectedDate();
    loadJournals();
    loadOverview();
  } else if (prev === "running" && job.status === "error") {
    showBanner(job.message, "error");
    renderJob(job);
  } else {
    setRunButtonBusy(false);
    await syncRunStepsForSelectedDate();
  }
}

function renderMarkdown(source) {
  const text = String(source || "").trim();
  if (!text) {
    return `<p class="empty">일지 파일이 없습니다.</p>`;
  }
  if (typeof marked !== "undefined" && typeof marked.parse === "function") {
    marked.setOptions({ breaks: true, gfm: true });
    return marked.parse(text);
  }
  return `<pre class="journal-body">${escapeHtml(text)}</pre>`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

const SIDEBAR_KEY = "worklog.sidebarCollapsed";

function applySidebarCollapsed(collapsed) {
  const shell = $("#app-shell");
  const toggle = $("#sidebar-toggle");
  const logo = $(".brand-logo");
  if (!shell) return;
  shell.classList.toggle("sidebar-collapsed", Boolean(collapsed));
  if (toggle) {
    toggle.setAttribute("aria-label", collapsed ? "사이드바 펼치기" : "사이드바 접기");
    toggle.title = collapsed ? "사이드바 펼치기" : "사이드바 접기";
  }
  if (logo) {
    logo.title = collapsed ? "사이드바 펼치기" : "";
    logo.style.cursor = collapsed ? "pointer" : "";
  }
  try {
    localStorage.setItem(SIDEBAR_KEY, collapsed ? "1" : "0");
  } catch (_) {
    /* ignore */
  }
}

function initSidebarToggle() {
  let collapsed = false;
  try {
    collapsed = localStorage.getItem(SIDEBAR_KEY) === "1";
  } catch (_) {
    collapsed = false;
  }
  applySidebarCollapsed(collapsed);
  $("#sidebar-toggle")?.addEventListener("click", () => {
    const next = !$("#app-shell")?.classList.contains("sidebar-collapsed");
    applySidebarCollapsed(next);
  });
  $(".brand-logo")?.addEventListener("click", () => {
    if ($("#app-shell")?.classList.contains("sidebar-collapsed")) {
      applySidebarCollapsed(false);
    }
  });
}

document.querySelectorAll(".nav-btn[data-view]").forEach((btn) => {
  btn.addEventListener("click", () => {
    setView(btn.dataset.view);
    if (btn.dataset.view === "chats") {
      loadWatched();
      if (!state.dialogs.length) loadDialogs();
    }
    if (btn.dataset.view === "journals") loadJournals();
    if (btn.dataset.view === "run") pollJob();
  });
});

$("#add-chat-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const id = $("#chat-id-input").value.trim();
  if (!id) return;
  addChat(id).then(() => {
    $("#chat-id-input").value = "";
  });
});

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const payload = {
      api_id: $("#api-id-input").value.trim() || null,
      api_hash: $("#api-hash-input").value.trim() || null,
      phone: $("#login-phone-input").value.trim(),
    };
    const data = await api("/api/telegram/login/start", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    $("#api-hash-input").value = "";
    renderLoginForms(data.stage);
    $("#login-status").textContent = data.message;
    showBanner(data.message, data.stage === "authorized" ? "ok" : "info");
    if (data.stage === "authorized") await enterDashboard();
  } catch (err) {
    showBanner(err.message, "error");
  }
});

$("#login-code-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const data = await api("/api/telegram/login/code", {
      method: "POST",
      body: JSON.stringify({ code: $("#login-code-input").value.trim() }),
    });
    renderLoginForms(data.stage);
    $("#login-status").textContent = data.message;
    showBanner(data.message, data.stage === "authorized" ? "ok" : "info");
    $("#login-code-input").value = "";
    if (data.stage === "authorized") await enterDashboard();
  } catch (err) {
    showBanner(err.message, "error");
  }
});

$("#login-password-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const data = await api("/api/telegram/login/password", {
      method: "POST",
      body: JSON.stringify({ password: $("#login-password-input").value }),
    });
    renderLoginForms(data.stage);
    $("#login-status").textContent = data.message;
    showBanner(data.message, "ok");
    $("#login-password-input").value = "";
    if (data.stage === "authorized") await enterDashboard();
  } catch (err) {
    showBanner(err.message, "error");
  }
});

async function restartLogin() {
  state.loginStage = "idle";
  renderLoginForms("idle");
  $("#login-status").textContent = "API ID / Hash / 전화번호를 입력해 로그인하세요.";
  $("#login-code-input").value = "";
  $("#login-password-input").value = "";
}

$("#login-restart")?.addEventListener("click", restartLogin);
$("#login-restart-password")?.addEventListener("click", restartLogin);
$("#sidebar-logout")?.addEventListener("click", () => {
  doLogout().catch((err) => showBanner(err.message, "error"));
});

initSidebarToggle();

$("#refresh-dialogs").addEventListener("click", loadDialogs);
$("#dialog-filter").addEventListener("input", (event) => renderDialogs(event.target.value));
$("#dialog-scroll")?.addEventListener("scroll", updateDialogScrollFade, { passive: true });
$("#journal-list-scroll")?.addEventListener("scroll", updateJournalScrollFade, { passive: true });
window.addEventListener("resize", () => {
  updateDialogScrollFade();
  updateJournalScrollFade();
});

$("#run-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const job = await api("/api/run", {
      method: "POST",
      body: JSON.stringify({ date: $("#run-date").value || null }),
    });
    lastJobStatus = "running";
    renderJob(job);
    pollJob();
  } catch (err) {
    showBanner(err.message, "error");
  }
});

$("#run-date")?.addEventListener("change", () => {
  syncRunStepsForSelectedDate();
});
$("#run-date")?.addEventListener("input", () => {
  syncRunStepsForSelectedDate();
});

loadOverview()
  .then(() => {
    if (state.authorized) {
      setView("journals");
      loadJournals();
      pollJob();
    } else {
      renderLoginForms(state.loginStage === "authorized" ? "idle" : state.loginStage);
    }
  })
  .catch((err) => showBanner(err.message, "error"));
