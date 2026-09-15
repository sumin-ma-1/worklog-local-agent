const $ = (sel) => document.querySelector(sel);

const state = {
  view: "settings",
  dialogs: [],
  loginStage: "idle",
  authorized: false,
};

function showBanner(message, kind) {
  const el = $("#banner");
  if (!message) {
    el.className = "banner hidden";
    el.textContent = "";
    return;
  }
  el.className = `banner ${kind || ""}`;
  el.textContent = message;
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
  if (!state.authorized && name !== "settings") {
    name = "settings";
    showBanner("텔레그램 로그인 후 이용할 수 있습니다.", "error");
  }
  state.view = name;
  document.querySelectorAll(".view").forEach((el) => el.classList.add("hidden"));
  document.querySelectorAll(".nav-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === name);
  });
  $(`#view-${name}`).classList.remove("hidden");
}

function applyAuthVisibility(authorized) {
  state.authorized = Boolean(authorized);
  document.querySelectorAll(".nav-btn.auth-only").forEach((btn) => {
    btn.classList.toggle("hidden", !state.authorized);
  });
  const settingsBtn = $("#nav-settings");
  if (settingsBtn) {
    settingsBtn.textContent = state.authorized ? "설정" : "로그인";
  }
  const title = $("#settings-title");
  const help = $("#settings-help");
  if (title) title.textContent = state.authorized ? "텔레그램 설정" : "텔레그램 로그인";
  if (help) {
    help.innerHTML = state.authorized
      ? `<a href="https://my.telegram.org" target="_blank" rel="noreferrer">my.telegram.org</a> 자격 증명과 로그인 상태를 관리합니다.`
      : `<a href="https://my.telegram.org" target="_blank" rel="noreferrer">my.telegram.org</a> 에서 API ID / Hash 를 발급받아 저장한 뒤, 전화번호로 로그인하세요. 로그인되면 일지·업무방·실행 메뉴가 열립니다.`;
  }
  if (!state.authorized) {
    setView("settings");
  }
}

function renderLoginForms(stage) {
  state.loginStage = stage || "idle";
  $("#login-code-form").classList.toggle("hidden", state.loginStage !== "code");
  $("#login-password-form").classList.toggle("hidden", state.loginStage !== "password");
}

function fillCredentials(credentials) {
  if (!credentials) return;
  if (credentials.api_id) $("#api-id-input").value = credentials.api_id;
  if (credentials.phone) {
    $("#phone-input").value = credentials.phone;
    $("#login-phone-input").value = credentials.phone;
  }
  $("#api-hash-input").placeholder = credentials.has_api_hash
    ? `저장됨 (${credentials.api_hash_masked})`
    : "API Hash";
  const parts = [];
  parts.push(credentials.ready ? "API 준비됨" : "API ID / Hash 필요");
  if (credentials.phone) parts.push(credentials.phone);
  parts.push(credentials.env_path);
  $("#cred-status").textContent = parts.join(" · ");
}

function fillTelegramStatus(telegram, stage) {
  if (!telegram) return;
  applyAuthVisibility(Boolean(telegram.authorized));
  if (telegram.authorized && telegram.user) {
    const user = telegram.user;
    $("#login-status").textContent = `로그인됨 · ${user.name}${user.username ? ` (@${user.username})` : ""}`;
  } else {
    $("#login-status").textContent = telegram.message || "로그인 필요";
  }
  renderLoginForms(stage || (telegram.authorized ? "authorized" : state.loginStage));
}

async function loadOverview() {
  const data = await api("/api/overview");
  const tg = data.telegram || {};
  fillCredentials(data.credentials);
  fillTelegramStatus(data.telegram, data.login_stage);
  const authLabel = tg.authorized
    ? `TG ${escapeHtml(tg.user?.name || "로그인됨")}`
    : "TG 미로그인";
  $("#overview-meta").innerHTML = `
    <div>${data.timezone}</div>
    <div>모델 ${escapeHtml(data.model)}</div>
    <div>업무방 ${data.chat_count} · 일지 ${data.journal_count}</div>
    <div>${authLabel}</div>
  `;
  renderJob(data.job);
}

async function loadTelegramStatus() {
  const data = await api("/api/telegram/status");
  fillCredentials(data.credentials);
  fillTelegramStatus(data.telegram, data.login_stage);
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
        <button class="danger" data-id="${escapeHtml(String(chat.id))}">삭제</button>
      </li>`
    )
    .join("");
  list.querySelectorAll("button.danger").forEach((btn) => {
    btn.addEventListener("click", () => removeChat(btn.dataset.id));
  });
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
    return;
  }
  list.innerHTML = rows
    .map((item) => {
      const action = item.watched
        ? `<button class="danger" data-id="${item.id}">삭제</button>`
        : `<button class="link" data-add="${item.id}">추가</button>`;
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
    btn.addEventListener("click", () => addChat(btn.dataset.add));
  });
  list.querySelectorAll("button.danger").forEach((btn) => {
    btn.addEventListener("click", () => removeChat(btn.dataset.id));
  });
}

async function loadDialogs() {
  $("#dialog-list").innerHTML = `<li class="empty">불러오는 중…</li>`;
  try {
    const data = await api("/api/dialogs");
    state.dialogs = data.dialogs || [];
    renderDialogs($("#dialog-filter").value);
    showBanner("");
  } catch (err) {
    state.dialogs = [];
    $("#dialog-list").innerHTML = `<li class="empty">${escapeHtml(err.message)}</li>`;
    showBanner(err.message, "error");
  }
}

async function addChat(id) {
  try {
    await api("/api/chats", { method: "POST", body: JSON.stringify({ id }) });
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

function onAuthorizedNavigate(view) {
  if (view === "chats") {
    loadWatched();
    if (!state.dialogs.length) loadDialogs();
  }
  if (view === "journals") loadJournals();
  if (view === "run") pollJob();
  if (view === "settings") {
    loadTelegramStatus().catch((err) => showBanner(err.message, "error"));
  }
}

document.querySelectorAll(".nav-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    setView(btn.dataset.view);
    onAuthorizedNavigate(btn.dataset.view);
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

$("#credentials-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const payload = {
      api_id: $("#api-id-input").value.trim() || null,
      api_hash: $("#api-hash-input").value.trim() || null,
      phone: $("#phone-input").value.trim() || null,
    };
    const data = await api("/api/telegram/credentials", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    $("#api-hash-input").value = "";
    fillCredentials(data.credentials);
    showBanner(data.message, "ok");
    await loadOverview();
  } catch (err) {
    showBanner(err.message, "error");
  }
});

$("#login-start-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const phone = $("#login-phone-input").value.trim();
    const data = await api("/api/telegram/login/start", {
      method: "POST",
      body: JSON.stringify({ phone }),
    });
    renderLoginForms(data.stage);
    $("#login-status").textContent = data.message;
    showBanner(data.message, data.stage === "authorized" ? "ok" : "");
    if (data.stage === "authorized") {
      await loadOverview();
      setView("journals");
      loadJournals();
    }
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
    showBanner(data.message, data.stage === "authorized" ? "ok" : "");
    $("#login-code-input").value = "";
    if (data.stage === "authorized") {
      await loadOverview();
      setView("journals");
      loadJournals();
    }
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
    await loadOverview();
    setView("journals");
    loadJournals();
  } catch (err) {
    showBanner(err.message, "error");
  }
});

$("#refresh-dialogs").addEventListener("click", loadDialogs);
$("#dialog-filter").addEventListener("input", (event) => renderDialogs(event.target.value));

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
      setView("settings");
    }
  })
  .catch((err) => showBanner(err.message, "error"));
