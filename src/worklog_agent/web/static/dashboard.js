const $ = (sel) => document.querySelector(sel);

const state = {
  view: "journals",
  dialogs: [],
  loginStage: "idle",
  authorized: false,
  authenticated: false,
  isAdmin: false,
  username: null,
  authMode: "login",
  journals: [],
  journalSelected: null,
  calendarMonth: null,
  accounts: [],
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
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options && options.headers) },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) {
    showAccountPanel();
    applyAuthVisibility(false);
    const detail = data.detail;
    throw new Error(typeof detail === "string" ? detail : "로그인이 필요합니다.");
  }
  if (response.status === 403 && data.detail === "telegram_required") {
    showTelegramPanel();
    applyAuthVisibility(false);
    throw new Error("텔레그램 연동이 필요합니다.");
  }
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
  if (name === "accounts") {
    loadAccounts();
  }
}

function applyAdminNav(isAdmin) {
  state.isAdmin = Boolean(isAdmin);
  document.querySelectorAll(".admin-only").forEach((el) => {
    el.classList.toggle("hidden", !state.isAdmin);
  });
  if (!state.isAdmin && state.view === "accounts") {
    setView("journals");
  }
}

function applyAuthVisibility(authorized) {
  state.authorized = Boolean(authorized);
  document.body.classList.toggle("login-mode", !state.authorized);
  $("#app-shell")?.classList.toggle("hidden", !state.authorized);
  $("#view-login")?.classList.toggle("hidden", state.authorized);
}

function setAuthMode(mode) {
  state.authMode = mode === "register" ? "register" : "login";
  const submit = $("#account-submit");
  const password = $("#password-input");
  const confirmWrap = $("#password-confirm-wrap");
  const gotoRegister = $("#goto-register");
  const gotoLogin = $("#goto-login");
  const status = $("#login-status");
  const hints = document.querySelectorAll(".field-hint.register-only");
  status?.classList.add("hidden");
  if (state.authMode === "register") {
    if (submit) submit.textContent = "가입하기";
    if (password) password.autocomplete = "new-password";
    confirmWrap?.classList.remove("hidden");
    gotoRegister?.classList.add("hidden");
    gotoLogin?.classList.remove("hidden");
    hints.forEach((el) => el.classList.remove("hidden"));
  } else {
    if (submit) submit.textContent = "로그인";
    if (password) password.autocomplete = "current-password";
    confirmWrap?.classList.add("hidden");
    $("#password-confirm-input") && ($("#password-confirm-input").value = "");
    gotoRegister?.classList.remove("hidden");
    gotoLogin?.classList.add("hidden");
    hints.forEach((el) => el.classList.add("hidden"));
  }
}

function showAccountPanel() {
  state.authenticated = false;
  state.username = null;
  applyAdminNav(false);
  $("#auth-account-panel")?.classList.remove("hidden");
  $("#auth-telegram-panel")?.classList.add("hidden");
  $("#auth-footer")?.classList.remove("hidden");
  setAuthMode(state.authMode === "register" ? "register" : "login");
}

function bindAuthSwitchers() {
  const root = $("#view-login");
  if (!root || root.dataset.switchBound === "1") return;
  root.dataset.switchBound = "1";
  root.addEventListener("click", (event) => {
    const target = event.target.closest("#goto-register, #goto-login");
    if (!target) return;
    event.preventDefault();
    if (target.id === "goto-register") setAuthMode("register");
    if (target.id === "goto-login") setAuthMode("login");
  });
}

bindAuthSwitchers();

function showTelegramPanel(phone) {
  state.authenticated = true;
  $("#auth-account-panel")?.classList.add("hidden");
  $("#auth-telegram-panel")?.classList.remove("hidden");
  $("#auth-footer")?.classList.add("hidden");
  const status = $("#login-status");
  if (phone && $("#login-phone-input")) $("#login-phone-input").value = phone;
  renderLoginForms(state.loginStage === "authorized" ? "idle" : state.loginStage);
  const message =
    state.loginStage === "code"
      ? "인증코드를 입력하세요."
      : state.loginStage === "password"
        ? "2단계 인증 비밀번호를 입력하세요."
        : "텔레그램 연동이 필요합니다.";
  if (status) {
    status.textContent = message;
    status.classList.remove("hidden");
  }
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

function applyOverviewAuth(data) {
  const tg = data.telegram || {};
  const linked = Boolean(tg.linked || tg.authorized);
  applyAdminNav(Boolean(data.is_admin));
  if (!data.authenticated) {
    showAccountPanel();
    applyAuthVisibility(false);
    return;
  }
  if (!linked) {
    showTelegramPanel(tg.phone);
    applyAuthVisibility(false);
    renderLoginForms(data.login_stage || "idle");
    return;
  }
  applyAuthVisibility(true);
  if (tg.user) {
    $("#login-status").textContent = `연동됨 · ${tg.user.name || ""}`;
  }
}

async function enterDashboard() {
  await loadOverview();
  if (!state.authorized) return;
  setView("journals");
  loadJournals();
  pollJob();
}

async function doLogout() {
  await api("/api/auth/logout", { method: "POST", body: "{}" });
  state.dialogs = [];
  state.loginStage = "idle";
  state.authenticated = false;
  showAccountPanel();
  applyAuthVisibility(false);
  showBanner("로그아웃되었습니다.", "ok");
}

async function loadOverview() {
  const data = await api("/api/overview");
  applyOverviewAuth(data);
  state.username = data.user?.username || null;
  const tg = data.telegram || {};
  const userLabel = data.user?.username
    ? escapeHtml(data.user.username)
    : "미로그인";
  const authLabel = tg.linked || tg.authorized
    ? `TG ${escapeHtml(tg.user?.name || "연동됨")}`
    : data.authenticated
      ? "TG 미연동"
      : "미로그인";
  if ($("#overview-meta")) {
    $("#overview-meta").innerHTML = `
      <div>${escapeHtml(userLabel)}</div>
      <div>${data.timezone || ""}</div>
      <div>모델 ${escapeHtml(data.model || "")}</div>
      <div>업무방 ${data.chat_count || 0} · 일지 ${data.journal_count || 0}</div>
      <div>${authLabel}</div>
    `;
  }
  if (data.job) renderJob(data.job);
}

function parseDay(day) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day || "");
  if (!match) return null;
  return { y: Number(match[1]), m: Number(match[2]), d: Number(match[3]) };
}

function monthKey(y, m) {
  return `${y}-${String(m).padStart(2, "0")}`;
}

function ensureCalendarMonth() {
  if (state.calendarMonth) return;
  const selected = parseDay(state.journalSelected);
  if (selected) {
    state.calendarMonth = { y: selected.y, m: selected.m };
    return;
  }
  const first = parseDay(state.journals[0]?.date);
  if (first) {
    state.calendarMonth = { y: first.y, m: first.m };
    return;
  }
  const now = new Date();
  state.calendarMonth = { y: now.getFullYear(), m: now.getMonth() + 1 };
}

function renderJournalList() {
  const list = $("#journal-list");
  if (!list) return;
  if (!state.journals.length) {
    list.innerHTML = `<li class="empty">아직 생성된 일지가 없습니다.</li>`;
    return;
  }
  list.innerHTML = state.journals
    .map(
      (item) => `
      <li>
        <button class="link${item.date === state.journalSelected ? " selected" : ""}" data-date="${item.date}">
          ${item.date}
          <span class="meta">첨부 ${item.attachments}</span>
        </button>
      </li>`
    )
    .join("");
  list.querySelectorAll("button[data-date]").forEach((btn) => {
    btn.addEventListener("click", () => loadJournal(btn.dataset.date));
  });
}

function renderJournalCalendar() {
  const root = $("#journal-calendar");
  if (!root) return;
  ensureCalendarMonth();
  const { y, m } = state.calendarMonth;
  const firstWeekday = new Date(y, m - 1, 1).getDay();
  const daysInMonth = new Date(y, m, 0).getDate();
  const today = new Date();
  const todayKey = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  const byDate = new Map(state.journals.map((item) => [item.date, item]));
  const weekdayLabels = ["일", "월", "화", "수", "목", "금", "토"];

  let cells = "";
  for (let i = 0; i < firstWeekday; i += 1) {
    cells += `<div class="cal-cell empty" aria-hidden="true"></div>`;
  }
  for (let day = 1; day <= daysInMonth; day += 1) {
    const key = `${monthKey(y, m)}-${String(day).padStart(2, "0")}`;
    const item = byDate.get(key);
    const classes = ["cal-day"];
    if (item) classes.push("has-entry");
    if (item?.has_journal) classes.push("has-journal");
    if (key === todayKey) classes.push("is-today");
    if (key === state.journalSelected) classes.push("is-selected");
    const title = item
      ? `${key} · 첨부 ${item.attachments}`
      : key;
    cells += `
      <button type="button" class="${classes.join(" ")}" data-date="${key}" title="${title}" aria-label="${title}">
        <span class="cal-num">${day}</span>
        ${item ? `<span class="cal-dot" aria-hidden="true"></span>` : ""}
      </button>`;
  }

  const yearStart = Math.min(y - 5, today.getFullYear() - 5);
  const yearEnd = Math.max(y + 5, today.getFullYear() + 1);
  let yearOptions = "";
  for (let year = yearStart; year <= yearEnd; year += 1) {
    yearOptions += `<option value="${year}"${year === y ? " selected" : ""}>${year}</option>`;
  }
  let monthOptions = "";
  for (let month = 1; month <= 12; month += 1) {
    monthOptions += `<option value="${month}"${month === m ? " selected" : ""}>${month}</option>`;
  }

  root.innerHTML = `
    <div class="cal-head">
      <button type="button" class="icon-btn" id="cal-prev" title="이전 달" aria-label="이전 달">
        <span class="material-symbols-outlined" aria-hidden="true">chevron_left</span>
      </button>
      <div class="cal-title">
        <label class="cal-picker">
          <select id="cal-year" aria-label="년도 선택">${yearOptions}</select>
          <span class="cal-unit" aria-hidden="true">년</span>
        </label>
        <label class="cal-picker">
          <select id="cal-month" aria-label="월 선택">${monthOptions}</select>
          <span class="cal-unit" aria-hidden="true">월</span>
        </label>
      </div>
      <button type="button" class="icon-btn" id="cal-next" title="다음 달" aria-label="다음 달">
        <span class="material-symbols-outlined" aria-hidden="true">chevron_right</span>
      </button>
    </div>
    <div class="cal-weekdays">${weekdayLabels.map((label) => `<span>${label}</span>`).join("")}</div>
    <div class="cal-grid">${cells}</div>
  `;

  $("#cal-prev")?.addEventListener("click", () => {
    let { y: cy, m: cm } = state.calendarMonth;
    cm -= 1;
    if (cm < 1) {
      cm = 12;
      cy -= 1;
    }
    state.calendarMonth = { y: cy, m: cm };
    renderJournalCalendar();
  });
  $("#cal-next")?.addEventListener("click", () => {
    let { y: cy, m: cm } = state.calendarMonth;
    cm += 1;
    if (cm > 12) {
      cm = 1;
      cy += 1;
    }
    state.calendarMonth = { y: cy, m: cm };
    renderJournalCalendar();
  });
  $("#cal-year")?.addEventListener("change", (event) => {
    const nextY = Number(event.target.value);
    if (!Number.isFinite(nextY)) return;
    state.calendarMonth = { y: nextY, m: state.calendarMonth.m };
    renderJournalCalendar();
  });
  $("#cal-month")?.addEventListener("change", (event) => {
    const nextM = Number(event.target.value);
    if (!Number.isFinite(nextM) || nextM < 1 || nextM > 12) return;
    state.calendarMonth = { y: state.calendarMonth.y, m: nextM };
    renderJournalCalendar();
  });
  root.querySelectorAll("button.cal-day").forEach((btn) => {
    btn.addEventListener("click", () => loadJournal(btn.dataset.date));
  });
}

function renderJournalBrowse() {
  renderJournalCalendar();
  renderJournalList();
}

async function loadJournals(selectDay) {
  if (!state.authorized) return;
  const data = await api("/api/journals");
  state.journals = data.journals || [];
  if (selectDay) state.journalSelected = selectDay;
  if (!state.journals.length) {
    renderJournalBrowse();
    if (!selectDay) {
      $("#journal-detail").innerHTML = `<p class="empty">왼쪽에서 날짜를 선택하세요.</p>`;
    }
    updateJournalScrollFade();
    return;
  }
  renderJournalBrowse();
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
      <button type="button" id="journal-share" class="icon-btn" title="공유 링크" aria-label="공유 링크" ${hasJournal ? "" : "disabled"}>
        <span class="material-symbols-outlined" aria-hidden="true">link</span>
      </button>
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
  $("#journal-share")?.addEventListener("click", () => shareJournal(data.date));
  $("#journal-edit")?.addEventListener("click", () => renderJournalDetail(data, { editing: true }));
  $("#journal-delete")?.addEventListener("click", () => deleteJournal(data.date));
  updateJournalScrollFade();
}

async function shareJournal(day) {
  try {
    const data = await api(`/api/journals/${day}/share`, { method: "POST", body: "{}" });
    const url = `${window.location.origin}${data.url}`;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(url);
      showBanner("공유 링크를 복사했습니다.", "ok");
    } else {
      window.prompt("공유 링크", url);
      showBanner("공유 링크를 만들었습니다.", "ok");
    }
  } catch (err) {
    showBanner(err.message, "error");
  }
}

async function loadJournal(day) {
  state.journalSelected = day;
  const parsed = parseDay(day);
  if (parsed) state.calendarMonth = { y: parsed.y, m: parsed.m };
  renderJournalBrowse();
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
    state.journalSelected = null;
    $("#journal-detail").innerHTML = `<p class="empty">왼쪽에서 날짜를 선택하세요.</p>`;
    await loadJournals();
    await loadOverview();
    await syncRunStepsForSelectedDate();
  } catch (err) {
    showBanner(err.message, "error");
  }
}

function formatAccountDate(value) {
  if (!value) return "-";
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})/.exec(String(value));
  if (match) return `${match[1]} ${match[2]}:${match[3]}`;
  const dayOnly = /^(\d{4}-\d{2}-\d{2})/.exec(String(value));
  return dayOnly ? dayOnly[1] : String(value);
}

function renderAccounts(users) {
  const body = $("#accounts-tbody");
  if (!body) return;
  state.accounts = users || [];
  if (!state.accounts.length) {
    body.innerHTML = `<tr><td colspan="6" class="empty">가입된 계정이 없습니다.</td></tr>`;
    return;
  }
  body.innerHTML = state.accounts
    .map((user) => {
      const self = state.username && user.username === state.username;
      const linked = user.telegram_linked ? "연동" : "미연동";
      const journals = Number.isFinite(Number(user.journal_count)) ? String(user.journal_count) : "0";
      const action = self
        ? `<span class="meta">본인</span>`
        : `<button type="button" class="danger icon-action" data-delete-user="${escapeHtml(user.id)}" title="삭제" aria-label="삭제"><span class="material-symbols-outlined" aria-hidden="true">delete</span></button>`;
      return `
        <tr>
          <td><strong>${escapeHtml(user.username || "")}</strong></td>
          <td>${escapeHtml(formatAccountDate(user.created_at))}</td>
          <td>${escapeHtml(formatAccountDate(user.last_seen))}</td>
          <td>${escapeHtml(journals)}</td>
          <td>${linked}</td>
          <td class="accounts-actions">${action}</td>
        </tr>`;
    })
    .join("");
  body.querySelectorAll("[data-delete-user]").forEach((btn) => {
    btn.addEventListener("click", () => deleteAccount(btn.dataset.deleteUser));
  });
}

async function loadAccounts() {
  if (!state.isAdmin) return;
  const body = $("#accounts-tbody");
  if (body) body.innerHTML = `<tr><td colspan="6" class="empty">불러오는 중…</td></tr>`;
  try {
    const data = await api("/api/admin/users");
    renderAccounts(data.users || []);
  } catch (err) {
    if (body) body.innerHTML = `<tr><td colspan="6" class="empty">${escapeHtml(err.message)}</td></tr>`;
    showBanner(err.message, "error");
  }
}

async function deleteAccount(userId) {
  if (!userId || !state.isAdmin) return;
  const user = state.accounts.find((item) => String(item.id) === String(userId));
  const label = user?.username || userId;
  if (!window.confirm(`계정 "${label}" 을(를) 삭제할까요? 데이터도 함께 삭제됩니다.`)) return;
  try {
    await api(`/api/admin/users/${encodeURIComponent(userId)}`, { method: "DELETE" });
    showBanner(`계정 ${label} 을(를) 삭제했습니다.`, "ok");
    await loadAccounts();
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
    if (btn.dataset.view === "accounts") loadAccounts();
  });
});

$("#refresh-accounts")?.addEventListener("click", () => {
  loadAccounts();
});

$("#add-chat-form")?.addEventListener("submit", (event) => {
  event.preventDefault();
  const id = $("#chat-id-input").value.trim();
  if (!id) return;
  addChat(id).then(() => {
    $("#chat-id-input").value = "";
  });
});

$("#account-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const username = $("#username-input")?.value.trim() || "";
  const password = $("#password-input")?.value || "";
  if (state.authMode === "register") {
    const confirm = $("#password-confirm-input")?.value || "";
    if (password !== confirm) {
      showBanner("비밀번호 확인이 일치하지 않습니다.", "error");
      return;
    }
  }
  const path = state.authMode === "register" ? "/api/auth/register" : "/api/auth/login";
  try {
    const data = await api(path, {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    $("#password-input").value = "";
    if ($("#password-confirm-input")) $("#password-confirm-input").value = "";
    const linked = Boolean(data.telegram?.linked || data.telegram?.authorized);
    if (linked) {
      showBanner(state.authMode === "register" ? "가입되었습니다." : "로그인되었습니다.", "ok");
      await enterDashboard();
    } else {
      showTelegramPanel(data.telegram?.phone);
      applyAuthVisibility(false);
      if (!data.telegram?.api_ready) {
        showBanner("서버에 TELEGRAM_API_ID / HASH 설정이 필요합니다.", "error");
      } else {
        showBanner("텔레그램을 연동하세요.", "info");
      }
    }
  } catch (err) {
    showBanner(err.message, "error");
  }
});

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const data = await api("/api/telegram/login/start", {
      method: "POST",
      body: JSON.stringify({ phone: $("#login-phone-input").value.trim() }),
    });
    renderLoginForms(data.stage);
    const status = $("#login-status");
    if (status) {
      status.textContent = data.message;
      status.classList.remove("hidden");
    }
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
    const status = $("#login-status");
    if (status) {
      status.textContent = data.message;
      status.classList.remove("hidden");
    }
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
    const status = $("#login-status");
    if (status) {
      status.textContent = data.message;
      status.classList.remove("hidden");
    }
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
  const status = $("#login-status");
  if (status) {
    status.textContent = "전화번호를 입력해 텔레그램을 연동하세요.";
    status.classList.remove("hidden");
  }
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

setAuthMode("login");
loadOverview()
  .then(() => {
    if (state.authorized) {
      setView("journals");
      loadJournals();
      pollJob();
    }
  })
  .catch((err) => showBanner(err.message, "error"));
