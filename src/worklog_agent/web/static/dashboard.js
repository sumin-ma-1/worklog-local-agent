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
    el.textContent = "";
    return;
  }
  el.textContent = message;
  el.className = `toast show ${kind || "info"}`;
  toastTimer = setTimeout(() => {
    el.classList.remove("show");
    toastTimer = setTimeout(() => {
      el.className = "toast hidden";
      el.textContent = "";
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

async function loadJournals() {
  if (!state.authorized) return;
  const data = await api("/api/journals");
  const list = $("#journal-list");
  if (!data.journals.length) {
    list.innerHTML = `<li class="empty">아직 생성된 일지가 없습니다.</li>`;
    return;
  }
  list.innerHTML = data.journals
    .map(
      (item) => `
      <li>
        <button class="link" data-date="${item.date}">
          ${item.date}
          <span class="meta">첨부 ${item.attachments} · ${item.has_journal ? "일지" : "정리본만"}</span>
        </button>
      </li>`
    )
    .join("");
  list.querySelectorAll("button[data-date]").forEach((btn) => {
    btn.addEventListener("click", () => loadJournal(btn.dataset.date));
  });
}

async function loadJournal(day) {
  const data = await api(`/api/journals/${day}`);
  const attach = (data.attachments || [])
    .map(
      (file) =>
        `<li><a href="/api/attachments/file?path=${encodeURIComponent(file.relative)}" target="_blank" rel="noreferrer">${escapeHtml(file.name)}</a></li>`
    )
    .join("");
  $("#journal-detail").innerHTML = `
    <h3>${escapeHtml(data.date)}</h3>
    <pre class="journal-body">${escapeHtml(data.markdown || "일지 파일이 없습니다.")}</pre>
    <h3>첨부</h3>
    <ul class="attach-list">${attach || `<li class="empty">첨부 없음</li>`}</ul>
  `;
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

function updateDialogScrollFade() {
  const scroll = $("#dialog-scroll");
  const wrap = $("#dialog-scroll-wrap");
  if (!scroll || !wrap) return;
  const more = scroll.scrollTop + scroll.clientHeight < scroll.scrollHeight - 2;
  wrap.classList.toggle("has-more", more);
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

function renderJob(job) {
  const el = $("#job-status");
  const button = $("#run-button");
  if (!el) return;
  const label = {
    idle: "대기 중",
    running: "실행 중…",
    done: job.path ? `완료: ${job.path}` : "완료",
    error: `실패: ${job.message || ""}`,
  }[job.status] || job.status;
  el.textContent = label;
  if (button) button.disabled = job.status === "running";
  if (job.status === "error") showBanner(job.message, "error");
  if (job.status === "done") showBanner(job.message || "실행을 마쳤습니다.", "ok");
}

async function pollJob() {
  if (!state.authorized) return;
  const job = await api("/api/job");
  renderJob(job);
  if (job.status === "running") {
    setTimeout(pollJob, 1500);
  } else if (job.status === "done") {
    loadJournals();
    loadOverview();
  }
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
  if (!shell) return;
  shell.classList.toggle("sidebar-collapsed", Boolean(collapsed));
  if (toggle) {
    toggle.setAttribute("aria-label", collapsed ? "사이드바 펼치기" : "사이드바 접기");
    toggle.title = collapsed ? "사이드바 펼치기" : "사이드바 접기";
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
window.addEventListener("resize", updateDialogScrollFade);

$("#run-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/run", {
      method: "POST",
      body: JSON.stringify({ date: $("#run-date").value || null }),
    });
    pollJob();
  } catch (err) {
    showBanner(err.message, "error");
  }
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
