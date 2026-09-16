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
  explorerOpen: {},
  accounts: [],
  showChatIds: false,
  watchedChats: [],
  watchedEditMode: false,
  watchedFilter: "",
  watchedSelected: {},
  journalSections: [],
  journalPromptDefault: true,
  journalPromptEditing: false,
  journalSectionsDraft: [],
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
    loadSchedules();
    loadJournalPrompt();
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
  state.journalSelected = null;
  state.calendarMonth = null;
  state.explorerOpen = {};
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
  const now = new Date();
  state.calendarMonth = { y: now.getFullYear(), m: now.getMonth() + 1 };
}

function weekdayLabel(y, m, d) {
  return ["일", "월", "화", "수", "목", "금", "토"][new Date(y, m - 1, d).getDay()];
}

function formatJournalDayTitle(day) {
  const parsed = parseDay(day);
  if (!parsed) return day || "";
  return `${parsed.y}년 ${parsed.m}월 ${parsed.d}일 ${weekdayLabel(parsed.y, parsed.m, parsed.d)}요일`;
}

function stripJournalLeadHeading(markdown) {
  return String(markdown || "").replace(/^#\s*업무\s*일지\s*\([^)]+\)\s*\n+/m, "");
}

const FILE_ICON_KEYS = new Set([
  "7z", "aac", "ai", "avi", "bmp", "bz2", "c", "conf", "cpp", "cs", "css", "csv",
  "db", "deb", "dmg", "doc", "dockerfile", "docm", "docx", "dropbox", "eml", "eps",
  "etc", "exe", "flac", "flv", "gdrive", "gif", "go", "gz", "htm", "html", "hwp",
  "ics", "img", "indd", "ini", "ipynb", "iso", "java", "jpeg", "jpg", "js", "json",
  "key", "log", "m4a", "mat", "mbox", "md", "mkv", "mov", "mp3", "mp4", "msg", "msi",
  "numbers", "ogg", "one", "onex", "pages", "pdf", "php", "pkg", "png", "ppt", "pptm",
  "pptx", "psd", "pst", "py", "r", "rar", "rb", "rdata", "rpm", "sql", "sqlite", "svg",
  "tar", "tiff", "tsv", "txt", "url", "vcf", "wav", "webm", "webp", "wmv", "xls",
  "xlsm", "xlsx", "xml", "yaml", "yml", "zip",
]);

const FILE_ICON_ALIASES = {
  tif: "tiff",
  htm: "html",
  jpeg: "jpg",
  markdown: "md",
  yml: "yaml",
};

function fileIconKey(filename) {
  const name = String(filename || "").split(/[/\\]/).pop() || "";
  const dot = name.lastIndexOf(".");
  if (dot <= 0 || dot === name.length - 1) return "etc";
  const ext = name.slice(dot + 1).toLowerCase();
  const key = FILE_ICON_ALIASES[ext] || ext;
  return FILE_ICON_KEYS.has(key) ? key : "etc";
}

function fileIconSrc(filename) {
  return `/static/file-icons/${fileIconKey(filename)}.svg`;
}

function renderAttachmentItems(attachments) {
  return (attachments || [])
    .map(
      (file) =>
        `<li>
          <img class="attach-icon" src="${escapeHtml(fileIconSrc(file.name))}" alt="" width="20" height="20">
          <a href="/api/attachments/file?path=${encodeURIComponent(file.relative)}" target="_blank" rel="noreferrer">${escapeHtml(file.name)}</a>
        </li>`
    )
    .join("");
}

function weekIndexInMonth(y, m, d) {
  const firstWeekday = new Date(y, m - 1, 1).getDay();
  return Math.floor((d + firstWeekday - 1) / 7) + 1;
}

function weekRangeInMonth(y, m, weekIndex) {
  const firstWeekday = new Date(y, m - 1, 1).getDay();
  const daysInMonth = new Date(y, m, 0).getDate();
  const start = Math.max(1, (weekIndex - 1) * 7 - firstWeekday + 1);
  const end = Math.min(daysInMonth, start + 6);
  return { start, end };
}

function sortJournalsDescending(items) {
  return [...(items || [])].sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")));
}

function renderJournalPlaceholder() {
  const panel = $("#journal-detail");
  if (!panel) return;
  panel.innerHTML = `
    <div class="journal-placeholder">
      <img class="journal-placeholder-art" src="/static/worklog-place.png" alt="" width="280" height="280">
      <h3 class="journal-placeholder-title">선택된 일지 없음</h3>
      <p class="journal-placeholder-hint">달력이나 목록에서 날짜를 선택하세요.</p>
    </div>`;
}

function explorerKey(...parts) {
  return parts.join(":");
}

function isExplorerOpen(key, fallback = false) {
  if (Object.prototype.hasOwnProperty.call(state.explorerOpen, key)) {
    return Boolean(state.explorerOpen[key]);
  }
  return fallback;
}

function setExplorerOpen(key, open) {
  state.explorerOpen = { ...state.explorerOpen, [key]: Boolean(open) };
}

function ensureExplorerPathForDay(day) {
  const parsed = parseDay(day);
  if (!parsed) return;
  const yearKey = explorerKey("y", parsed.y);
  const monthKeyId = explorerKey("ym", parsed.y, parsed.m);
  const week = weekIndexInMonth(parsed.y, parsed.m, parsed.d);
  const weekKey = explorerKey("yw", parsed.y, parsed.m, week);
  setExplorerOpen(yearKey, true);
  setExplorerOpen(monthKeyId, true);
  setExplorerOpen(weekKey, true);
}

function todayDateKey() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(
    now.getDate()
  ).padStart(2, "0")}`;
}

function applyInitialJournalFocus() {
  const now = new Date();
  const y = now.getFullYear();
  const m = now.getMonth() + 1;
  const d = now.getDate();
  const today = todayDateKey();
  state.calendarMonth = { y, m };

  const dateSet = new Set((state.journals || []).map((item) => item.date));
  const yearKey = explorerKey("y", y);
  const monthKeyId = explorerKey("ym", y, m);
  const week = weekIndexInMonth(y, m, d);
  const weekKey = explorerKey("yw", y, m, week);
  const range = weekRangeInMonth(y, m, week);

  const hasToday = dateSet.has(today);
  const hasWeek = [...dateSet].some((date) => {
    const parsed = parseDay(date);
    return Boolean(
      parsed &&
        parsed.y === y &&
        parsed.m === m &&
        parsed.d >= range.start &&
        parsed.d <= range.end
    );
  });
  const hasMonth = [...dateSet].some((date) => String(date).startsWith(`${monthKey(y, m)}-`));
  const hasYear = [...dateSet].some((date) => String(date).startsWith(`${y}-`));

  if (hasToday || hasWeek || hasMonth || hasYear) {
    setExplorerOpen(yearKey, true);
  } else if (state.journals[0]?.date) {
    const latest = parseDay(state.journals[0].date);
    if (latest) setExplorerOpen(explorerKey("y", latest.y), true);
  }

  if (hasToday || hasWeek || hasMonth) {
    setExplorerOpen(monthKeyId, true);
  }
  if (hasToday || hasWeek) {
    setExplorerOpen(weekKey, true);
  }

  return hasToday ? today : null;
}

function buildJournalExplorerTree(items) {
  const years = new Map();
  for (const item of sortJournalsDescending(items)) {
    const parsed = parseDay(item.date);
    if (!parsed) continue;
    if (!years.has(parsed.y)) years.set(parsed.y, new Map());
    const months = years.get(parsed.y);
    if (!months.has(parsed.m)) months.set(parsed.m, new Map());
    const weeks = months.get(parsed.m);
    const week = weekIndexInMonth(parsed.y, parsed.m, parsed.d);
    if (!weeks.has(week)) weeks.set(week, []);
    weeks.get(week).push(item);
  }
  return [...years.entries()]
    .sort((a, b) => b[0] - a[0])
    .map(([year, months]) => ({
      year,
      months: [...months.entries()]
        .sort((a, b) => b[0] - a[0])
        .map(([month, weeks]) => ({
          month,
          weeks: [...weeks.entries()]
            .sort((a, b) => b[0] - a[0])
            .map(([week, days]) => ({
              week,
              days: sortJournalsDescending(days),
            })),
        })),
    }));
}

function countTreeDays(node) {
  if (node.days) return node.days.length;
  if (node.weeks) return node.weeks.reduce((sum, week) => sum + week.days.length, 0);
  if (node.months) {
    return node.months.reduce(
      (sum, month) => sum + month.weeks.reduce((inner, week) => inner + week.days.length, 0),
      0
    );
  }
  return 0;
}

function renderExplorerFolder({ key, depth, label, count, open, childrenHtml }) {
  const icon = open ? "folder_open" : "folder";
  const chevron = open ? "expand_more" : "chevron_right";
  return `
    <li class="explorer-item explorer-folder" style="--depth:${depth}">
      <button type="button" class="explorer-row" data-explorer-toggle="${escapeHtml(key)}" aria-expanded="${open ? "true" : "false"}">
        <span class="material-symbols-outlined explorer-chevron" aria-hidden="true">${chevron}</span>
        <span class="material-symbols-outlined explorer-icon" aria-hidden="true">${icon}</span>
        <span class="explorer-label">${escapeHtml(label)}</span>
        <span class="meta">${count}</span>
      </button>
      ${open ? `<ul class="explorer-children">${childrenHtml}</ul>` : ""}
    </li>`;
}

function panelEmptyHtml({ src, title, hint = "", size = 160 } = {}) {
  return `<li class="panel-empty">
    <img class="panel-empty-art" src="${escapeHtml(src)}" alt="" width="${size}" height="${size}">
    <h3 class="journal-placeholder-title">${escapeHtml(title)}</h3>
    ${hint ? `<p class="journal-placeholder-hint">${escapeHtml(hint)}</p>` : ""}
  </li>`;
}

function renderJournalList() {
  const list = $("#journal-list");
  if (!list) return;
  if (!state.journals.length) {
    list.classList.remove("explorer-list");
    list.innerHTML = panelEmptyHtml({
      src: "/static/worklog-list.png",
      title: "일지 목록 없음",
      hint: "일지 생성 탭에서 만들 수 있습니다.",
      size: 180,
    });
    updateJournalScrollFade();
    return;
  }

  const tree = buildJournalExplorerTree(state.journals);
  list.classList.add("explorer-list");
  list.innerHTML = tree
    .map((yearNode) => {
      const yearKey = explorerKey("y", yearNode.year);
      const yearOpen = isExplorerOpen(yearKey, false);
      const monthsHtml = yearNode.months
        .map((monthNode) => {
          const monthKeyId = explorerKey("ym", yearNode.year, monthNode.month);
          const monthOpen = isExplorerOpen(monthKeyId, false);
          const weeksHtml = monthNode.weeks
            .map((weekNode) => {
              const weekKey = explorerKey("yw", yearNode.year, monthNode.month, weekNode.week);
              const weekOpen = isExplorerOpen(weekKey, false);
              const range = weekRangeInMonth(yearNode.year, monthNode.month, weekNode.week);
              const daysHtml = weekNode.days
                .map((item) => {
                  const parsed = parseDay(item.date);
                  const dayLabel = parsed
                    ? `${parsed.d}일 (${weekdayLabel(parsed.y, parsed.m, parsed.d)})`
                    : item.date;
                  const selected = item.date === state.journalSelected ? " selected" : "";
                  return `
                    <li class="explorer-item explorer-file" style="--depth:3">
                      <button type="button" class="explorer-row link${selected}" data-date="${item.date}">
                        <span class="explorer-chevron-spacer" aria-hidden="true"></span>
                        <span class="material-symbols-outlined explorer-icon" aria-hidden="true">description</span>
                        <span class="explorer-label">
                          <strong>${escapeHtml(dayLabel)}</strong>
                          <span class="meta">첨부 ${item.attachments || 0}</span>
                        </span>
                      </button>
                    </li>`;
                })
                .join("");
              return renderExplorerFolder({
                key: weekKey,
                depth: 2,
                label: `${weekNode.week}주 (${monthNode.month}/${range.start}–${monthNode.month}/${range.end})`,
                count: weekNode.days.length,
                open: weekOpen,
                childrenHtml: daysHtml,
              });
            })
            .join("");
          return renderExplorerFolder({
            key: monthKeyId,
            depth: 1,
            label: `${monthNode.month}월`,
            count: countTreeDays(monthNode),
            open: monthOpen,
            childrenHtml: weeksHtml,
          });
        })
        .join("");
      return renderExplorerFolder({
        key: yearKey,
        depth: 0,
        label: `${yearNode.year}년`,
        count: countTreeDays(yearNode),
        open: yearOpen,
        childrenHtml: monthsHtml,
      });
    })
    .join("");

  list.querySelectorAll("[data-explorer-toggle]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.dataset.explorerToggle;
      const currentlyOpen = isExplorerOpen(key, false);
      setExplorerOpen(key, !currentlyOpen);
      renderJournalList();
      updateJournalScrollFade();
    });
  });
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

  const shiftMonth = (delta) => {
    let { y: cy, m: cm } = state.calendarMonth;
    cm += delta;
    if (cm < 1) {
      cm = 12;
      cy -= 1;
    } else if (cm > 12) {
      cm = 1;
      cy += 1;
    }
    state.calendarMonth = { y: cy, m: cm };
    renderJournalCalendar();
  };

  $("#cal-prev")?.addEventListener("click", () => shiftMonth(-1));
  $("#cal-next")?.addEventListener("click", () => shiftMonth(1));
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
  state.journals = sortJournalsDescending(data.journals || []);
  if (selectDay) {
    state.journalSelected = selectDay;
    ensureExplorerPathForDay(selectDay);
  }
  if (!state.journals.length) {
    state.calendarMonth = null;
    ensureCalendarMonth();
    renderJournalBrowse();
    if (!state.journalSelected) {
      renderJournalPlaceholder();
    }
    updateJournalScrollFade();
    return;
  }

  const isFirstEntry = !selectDay && !state.journalSelected && Object.keys(state.explorerOpen).length === 0;
  if (isFirstEntry) {
    const focusDay = applyInitialJournalFocus();
    renderJournalBrowse();
    updateJournalScrollFade();
    if (focusDay) {
      await loadJournal(focusDay);
      return;
    }
    renderJournalPlaceholder();
    return;
  }

  const selected = parseDay(state.journalSelected);
  if (selected) {
    state.calendarMonth = { y: selected.y, m: selected.m };
  } else {
    ensureCalendarMonth();
  }
  renderJournalBrowse();
  updateJournalScrollFade();
  if (selectDay) {
    await loadJournal(selectDay);
    return;
  }
  if (!state.journalSelected) {
    renderJournalPlaceholder();
  }
}

function updateJournalScrollFade() {
  updateScrollFade($("#journal-list-scroll"), $("#journal-list-wrap"));
  updateScrollFade($("#journal-detail-scroll"), $("#journal-detail-wrap"));
  updateScrollFade($("#journal-editor"), $("#journal-editor-wrap"));
}

function renderJournalDetail(data, { editing = false } = {}) {
  const attach = renderAttachmentItems(data.attachments);
  const markdown = data.markdown || "";
  const hasJournal = Boolean(data.has_journal || markdown);
  const body = editing
    ? `<div class="journal-editor-wrap" id="journal-editor-wrap">
        <textarea id="journal-editor" class="journal-editor" spellcheck="false">${escapeHtml(markdown)}</textarea>
      </div>`
    : `<div class="list-scroll-wrap" id="journal-detail-wrap">
        <div class="list-scroll" id="journal-detail-scroll">
          <div class="journal-body markdown-body">${renderMarkdown(stripJournalLeadHeading(markdown))}</div>
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
      <button type="button" id="journal-print" class="icon-btn" title="인쇄" aria-label="인쇄" ${hasJournal ? "" : "disabled"}>
        <span class="material-symbols-outlined" aria-hidden="true">print</span>
      </button>
      <button type="button" id="journal-copy" class="icon-btn" title="복사" aria-label="복사" ${hasJournal ? "" : "disabled"}>
        <span class="material-symbols-outlined" aria-hidden="true">content_copy</span>
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
      <h3>${escapeHtml(formatJournalDayTitle(data.date))}</h3>
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
  $("#journal-copy")?.addEventListener("click", () => copyJournal(data));
  $("#journal-print")?.addEventListener("click", () => printJournal());
  $("#journal-share")?.addEventListener("click", () => shareJournal(data.date));
  $("#journal-edit")?.addEventListener("click", () => renderJournalDetail(data, { editing: true }));
  $("#journal-delete")?.addEventListener("click", () => deleteJournal(data.date));
  updateJournalScrollFade();
}

async function copyJournal(data) {
  const title = formatJournalDayTitle(data.date);
  const body = stripJournalLeadHeading(data.markdown || "").trim();
  const text = body ? `${title}\n\n${body}` : title;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.left = "-9999px";
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
    showBanner("일지를 복사했습니다.", "ok");
  } catch (err) {
    showBanner(err.message || "복사에 실패했습니다.", "error");
  }
}

function printJournal() {
  document.body.classList.add("printing-journal");
  const cleanup = () => {
    document.body.classList.remove("printing-journal");
    window.removeEventListener("afterprint", cleanup);
  };
  window.addEventListener("afterprint", cleanup);
  window.print();
  // Safari / some browsers may not fire afterprint reliably
  setTimeout(cleanup, 1000);
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
  ensureExplorerPathForDay(day);
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
    renderJournalPlaceholder();
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
    body.innerHTML = `<tr><td colspan="7" class="empty">가입된 계정이 없습니다.</td></tr>`;
    return;
  }
  body.innerHTML = state.accounts
    .map((user) => {
      const self = state.username && user.username === state.username;
      const linked = user.telegram_linked ? "연동" : "미연동";
      const chats = Number.isFinite(Number(user.chat_count)) ? String(user.chat_count) : "0";
      const journals = Number.isFinite(Number(user.journal_count)) ? String(user.journal_count) : "0";
      const action = self
        ? `<span class="meta">본인</span>`
        : `<button type="button" class="danger icon-action" data-delete-user="${escapeHtml(user.id)}" title="삭제" aria-label="삭제"><span class="material-symbols-outlined" aria-hidden="true">delete</span></button>`;
      return `
        <tr>
          <td><strong>${escapeHtml(user.username || "")}</strong></td>
          <td>${escapeHtml(formatAccountDate(user.created_at))}</td>
          <td>${escapeHtml(formatAccountDate(user.last_seen))}</td>
          <td>${escapeHtml(chats)}</td>
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
  if (body) body.innerHTML = `<tr><td colspan="7" class="empty">불러오는 중…</td></tr>`;
  try {
    const data = await api("/api/admin/users");
    renderAccounts(data.users || []);
  } catch (err) {
    if (body) body.innerHTML = `<tr><td colspan="7" class="empty">${escapeHtml(err.message)}</td></tr>`;
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

function syncWatchedEditUi() {
  const editing = state.watchedEditMode;
  document.body.classList.toggle("watched-editing", editing);
  $("#watched-search-wrap")?.classList.toggle("hidden", !editing);
  $("#watched-select-all")?.classList.toggle("hidden", !editing);
  $("#watched-delete-selected")?.classList.toggle("hidden", !editing);
  const editBtn = $("#watched-edit");
  if (editBtn) {
    editBtn.title = editing ? "취소" : "수정";
    editBtn.setAttribute("aria-label", editing ? "취소" : "수정");
    editBtn.classList.toggle("is-active", editing);
    const icon = editBtn.querySelector(".material-symbols-outlined");
    if (icon) icon.textContent = editing ? "undo" : "edit";
  }
  updateWatchedSelectionUi();
}

function updateWatchedSelectionUi() {
  const selectedCount = Object.keys(state.watchedSelected).length;
  const deleteBtn = $("#watched-delete-selected");
  if (deleteBtn) {
    deleteBtn.disabled = selectedCount === 0;
    deleteBtn.title = selectedCount ? `선택 ${selectedCount}개 삭제` : "선택 삭제";
  }
}

function filteredWatchedChats() {
  const q = (state.watchedFilter || "").trim().toLowerCase();
  return (state.watchedChats || []).filter((chat) => {
    if (!q) return true;
    return `${chat.title || ""} ${chat.id}`.toLowerCase().includes(q);
  });
}

function renderWatched() {
  const list = $("#watched-list");
  if (!list) return;
  const chats = state.watchedChats || [];
  if (!chats.length) {
    if (state.watchedEditMode) {
      state.watchedEditMode = false;
      state.watchedSelected = {};
      state.watchedFilter = "";
      const filter = $("#watched-filter");
      if (filter) filter.value = "";
      syncWatchedEditUi();
    }
    list.innerHTML = panelEmptyHtml({
      src: "/static/worklog-coll.png",
      title: "등록된 업무방 없음",
      hint: "참여 대화 목록에서 대화를 추가하세요.",
      size: 140,
    });
    updateWatchedScrollFade();
    return;
  }

  const rows = filteredWatchedChats();
  if (!rows.length) {
    list.innerHTML = `<li class="empty">표시할 업무방이 없습니다.</li>`;
    updateWatchedScrollFade();
    return;
  }

  const editing = state.watchedEditMode;
  list.innerHTML = rows
    .map((chat) => {
      const id = String(chat.id);
      const checked = Boolean(state.watchedSelected[id]);
      const select = editing
        ? `<label class="watched-check">
            <input type="checkbox" data-watched-check="${escapeHtml(id)}" ${checked ? "checked" : ""}>
          </label>`
        : "";
      const action = editing
        ? ""
        : `<button class="danger icon-action" data-id="${escapeHtml(id)}" title="삭제" aria-label="삭제">
            <span class="material-symbols-outlined" aria-hidden="true">delete</span>
          </button>`;
      return `
        <li class="watched-item${editing ? " is-editing" : ""}${checked ? " is-selected" : ""}">
          ${select}
          <div class="watched-item-main">
            <strong>${escapeHtml(String(chat.title))}</strong>
            ${state.showChatIds ? `<span class="meta">${escapeHtml(id)}</span>` : ""}
          </div>
          ${action}
        </li>`;
    })
    .join("");

  if (editing) {
    list.querySelectorAll("[data-watched-check]").forEach((input) => {
      input.addEventListener("change", () => {
        const id = input.dataset.watchedCheck;
        if (!id) return;
        if (input.checked) state.watchedSelected[id] = true;
        else delete state.watchedSelected[id];
        input.closest(".watched-item")?.classList.toggle("is-selected", input.checked);
        updateWatchedSelectionUi();
      });
    });
  } else {
    list.querySelectorAll("button.danger").forEach((btn) => {
      btn.addEventListener("click", () => removeChat(btn.dataset.id));
    });
  }
  updateWatchedSelectionUi();
  updateWatchedScrollFade();
}

function updateWatchedScrollFade() {
  updateScrollFade($("#watched-scroll"), $("#watched-scroll-wrap"));
}

async function loadWatched() {
  const data = await api("/api/chats");
  state.watchedChats = data.chats || [];
  const valid = new Set(state.watchedChats.map((chat) => String(chat.id)));
  state.watchedSelected = Object.fromEntries(
    Object.keys(state.watchedSelected)
      .filter((id) => valid.has(id))
      .map((id) => [id, true])
  );
  renderWatched();
}

async function removeSelectedWatched() {
  const ids = Object.keys(state.watchedSelected);
  if (!ids.length) return;
  if (!window.confirm(`선택한 업무방 ${ids.length}개를 삭제할까요?`)) return;
  try {
    for (const id of ids) {
      await api("/api/chats/delete", { method: "POST", body: JSON.stringify({ id }) });
    }
    showBanner(`업무방 ${ids.length}개를 삭제했습니다.`, "ok");
    state.watchedSelected = {};
    await Promise.all([loadWatched(), loadOverview()]);
    if (state.dialogs.length) {
      const removed = new Set(ids.map(String));
      state.dialogs = state.dialogs.map((item) =>
        removed.has(String(item.id)) ? { ...item, watched: false } : item
      );
      renderDialogs($("#dialog-filter")?.value);
    }
  } catch (err) {
    showBanner(err.message, "error");
    await loadWatched();
  }
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
    if (!state.dialogs.length) {
      list.innerHTML = panelEmptyHtml({
        src: "/static/worklog-conv.png",
        title: "참여 대화 목록 없음",
        hint: "새로고침으로 텔레그램 대화를 불러오세요.",
        size: 140,
      });
    } else {
      list.innerHTML = `<li class="empty">표시할 대화가 없습니다.</li>`;
    }
    updateDialogScrollFade();
    return;
  }
  list.innerHTML = rows
    .map((item) => {
      const action = item.watched
        ? `<button class="danger icon-action" data-id="${item.id}" title="삭제" aria-label="삭제"><span class="material-symbols-outlined" aria-hidden="true">delete</span></button>`
        : `<button class="link btn-with-icon icon-action" data-add="${item.id}" title="추가" aria-label="추가"><span class="material-symbols-outlined" aria-hidden="true">add</span></button>`;
      const meta = state.showChatIds
        ? `${item.id} · ${escapeHtml(item.type)}`
        : escapeHtml(item.type);
      return `
        <li>
          <div>
            <strong>${escapeHtml(item.title || "(제목 없음)")}</strong>
            <span class="meta">${meta}</span>
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
let runMode = "single";
const runPickDates = new Set();
const RUN_PICK_MAX = 31;
const JOB_STEP_ORDER = ["collect", "archive", "organize", "model", "journal"];
const PLAN_ACTION_LABEL = { run: "생성", skip: "건너뜀" };
const PLAN_REASON_LABEL = {
  force: "강제 재생성",
  already_exists: "일지 있음",
  missing_journal: "일지 없음",
  regenerate: "재생성",
  stale: "소스 변경됨",
  up_to_date: "최신",
  legacy_no_meta: "메타 없음(건너뜀)",
};

function setRunMode(mode) {
  runMode = mode;
  document.querySelectorAll(".run-mode-tab").forEach((btn) => {
    const active = btn.dataset.runMode === mode;
    btn.classList.toggle("is-active", active);
    btn.setAttribute("aria-selected", active ? "true" : "false");
  });
  $("#run-single-fields")?.classList.toggle("hidden", mode !== "single");
  $("#run-range-fields")?.classList.toggle("hidden", mode !== "range");
  $("#run-pick-fields")?.classList.toggle("hidden", mode !== "pick");
  syncRunStepsForSelectedDate();
}

function renderRunPickList() {
  const list = $("#run-pick-list");
  const hint = $("#run-pick-hint");
  if (!list) return;
  const dates = [...runPickDates].sort().reverse();
  if (!dates.length) {
    list.innerHTML = "";
    if (hint) hint.textContent = "날짜를 추가하세요. 최대 31일.";
    return;
  }
  list.innerHTML = dates
    .map(
      (day) =>
        `<li><button type="button" class="run-pick-chip" data-day="${escapeHtml(day)}" title="클릭하여 제거">${escapeHtml(day)}<span class="material-symbols-outlined" aria-hidden="true">close</span></button></li>`
    )
    .join("");
  if (hint) hint.textContent = `${dates.length}일 선택 · 최대 ${RUN_PICK_MAX}일`;
}

function addRunPickDate(day) {
  if (!day) throw new Error("날짜를 선택하세요.");
  if (runPickDates.has(day)) throw new Error("이미 추가된 날짜입니다.");
  if (runPickDates.size >= RUN_PICK_MAX) throw new Error(`최대 ${RUN_PICK_MAX}일까지 선택할 수 있습니다.`);
  runPickDates.add(day);
  renderRunPickList();
  hideRunPlan();
}

function removeRunPickDate(day) {
  runPickDates.delete(day);
  renderRunPickList();
  hideRunPlan();
}

function selectedRunPolicy() {
  const selected = document.querySelector('input[name="run-policy"]:checked');
  return selected?.value || "skip";
}

function runOptionsBody() {
  const policy = selectedRunPolicy();
  return {
    skip_existing: policy === "skip",
    regenerate_if_stale: policy === "stale",
    force: policy === "force",
  };
}

function buildRunBody() {
  const opts = runOptionsBody();
  if (runMode === "range") {
    const start = $("#run-start")?.value;
    const end = $("#run-end")?.value;
    if (!start || !end) throw new Error("시작·종료 날짜를 선택하세요.");
    return { start, end, ...opts };
  }
  if (runMode === "pick") {
    if (!runPickDates.size) throw new Error("날짜를 하나 이상 추가하세요.");
    return { dates: [...runPickDates], ...opts };
  }
  const day = $("#run-date")?.value;
  if (!day) throw new Error("날짜를 선택하세요.");
  return { date: day, ...opts };
}

function updateRunPlanScrollFade() {
  updateScrollFade($("#run-plan-scroll"), $("#run-plan-scroll-wrap"));
}

function syncRunPreviewButton() {
  const btn = $("#run-preview-button");
  const wrap = $("#run-plan-wrap");
  if (!btn) return;
  const open = Boolean(wrap && !wrap.classList.contains("hidden"));
  btn.classList.toggle("is-active", open);
  btn.setAttribute("aria-pressed", open ? "true" : "false");
  btn.title = open ? "미리보기 닫기" : "미리보기";
  btn.setAttribute("aria-label", open ? "미리보기 닫기" : "미리보기");
  btn.querySelector(".run-preview-on")?.classList.toggle("hidden", open);
  btn.querySelector(".run-preview-off")?.classList.toggle("hidden", !open);
}

function hideRunPlan() {
  $("#run-plan-wrap")?.classList.add("hidden");
  syncRunPreviewButton();
  updateRunPlanScrollFade();
}

function renderRunPlan(data) {
  const wrap = $("#run-plan-wrap");
  const tbody = $("#run-plan-tbody");
  const summary = $("#run-plan-summary");
  if (!wrap || !tbody || !summary) return;
  const plan = data.plan || [];
  if (!plan.length) {
    wrap.classList.add("hidden");
    tbody.innerHTML = "";
    syncRunPreviewButton();
    updateRunPlanScrollFade();
    return;
  }
  wrap.classList.remove("hidden");
  summary.textContent = `생성 ${data.run_count} · 건너뜀 ${data.skip_count} · 총 ${plan.length}일`;
  tbody.innerHTML = plan
    .map((item) => {
      const action = PLAN_ACTION_LABEL[item.action] || item.action;
      const reason = PLAN_REASON_LABEL[item.reason] || item.reason || "";
      return `<tr class="run-plan-row run-plan-${item.action}">
        <td>${escapeHtml(item.date)}</td>
        <td>${item.has_journal ? "있음" : "없음"}</td>
        <td>
          <span class="run-plan-action run-plan-action-${item.action}">${escapeHtml(action)}</span>
          ${reason ? `<span class="run-plan-reason">${escapeHtml(reason)}</span>` : ""}
        </td>
      </tr>`;
    })
    .join("");
  syncRunPreviewButton();
  requestAnimationFrame(updateRunPlanScrollFade);
}

const SCHEDULE_TARGET_LABEL = { yesterday: "어제", today: "오늘" };
const SCHEDULE_STATUS_LABEL = {
  started: "시작됨",
  skipped: "건너뜀",
  error: "오류",
  busy: "실행 중",
};

async function loadSchedules() {
  if (!state.authorized) return;
  try {
    const data = await api("/api/schedules");
    renderSchedules(data.schedules || []);
  } catch (err) {
    showBanner(err.message, "error");
  }
}

function syncPromptEditUi() {
  const editing = state.journalPromptEditing;
  $("#prompt-edit")?.classList.toggle("hidden", editing);
  $("#prompt-save")?.classList.toggle("hidden", !editing);
  $("#prompt-cancel")?.classList.toggle("hidden", !editing);
  $("#prompt-reset")?.classList.toggle("hidden", !editing);
  $("#prompt-sections")?.classList.toggle("is-editing", editing);
}

function currentPromptSections() {
  return state.journalPromptEditing
    ? state.journalSectionsDraft
    : state.journalSections;
}

function syncPromptDraftFromInputs() {
  const labels = document.querySelectorAll("#prompt-sections .prompt-section-label.is-editable");
  if (!labels.length) return;
  state.journalSectionsDraft = Array.from(labels).map((el) => (el.textContent || "").replace(/\u200b/g, "").trim());
}

function renderJournalPrompt(focusIndex = null) {
  $("#prompt-meta")?.remove();
  const list = $("#prompt-sections");
  const sections = currentPromptSections();
  const editing = state.journalPromptEditing;
  if (list) {
    const chips = sections.length
      ? sections
          .map((name, index) => {
            if (editing) {
              return `<li class="prompt-section-chip is-editing" data-index="${index}">
              <span class="prompt-section-label is-editable" contenteditable="true" role="textbox" data-index="${index}" data-placeholder="이름" aria-label="섹션 이름">${escapeHtml(name)}</span>
              <button type="button" class="prompt-section-remove" data-index="${index}" title="삭제" aria-label="삭제">
                <span class="material-symbols-outlined" aria-hidden="true">close</span>
              </button>
            </li>`;
            }
            return `<li class="prompt-section-chip"><span class="prompt-section-label">${escapeHtml(name)}</span></li>`;
          })
          .join("")
      : editing
        ? ""
        : `<li class="empty">섹션이 없습니다.</li>`;
    const addChip = editing
      ? `<li class="prompt-section-chip prompt-section-add">
          <button type="button" class="prompt-section-add-btn" title="섹션 추가" aria-label="섹션 추가">
            <span class="material-symbols-outlined" aria-hidden="true">add</span>
          </button>
        </li>`
      : "";
    list.innerHTML = chips + addChip;
  }
  syncPromptEditUi();
  if (focusIndex != null) {
    const label = document.querySelector(
      `#prompt-sections .prompt-section-label.is-editable[data-index="${focusIndex}"]`
    );
    if (label) {
      label.focus();
      const range = document.createRange();
      range.selectNodeContents(label);
      const sel = window.getSelection();
      sel?.removeAllRanges();
      sel?.addRange(range);
    }
  }
}

async function loadJournalPrompt() {
  if (!state.authorized) return;
  try {
    const data = await api("/api/journal-prompt");
    state.journalSections = Array.isArray(data.sections) ? data.sections.slice() : [];
    state.journalPromptDefault = Boolean(data.is_default);
    state.journalPromptEditing = false;
    state.journalSectionsDraft = state.journalSections.slice();
    renderJournalPrompt();
  } catch (err) {
    showBanner(err.message, "error");
  }
}

async function saveJournalPrompt() {
  syncPromptDraftFromInputs();
  const sections = state.journalSectionsDraft
    .map((name) => String(name || "").trim())
    .filter(Boolean);
  try {
    const data = await api("/api/journal-prompt", {
      method: "PUT",
      body: JSON.stringify({ sections }),
    });
    state.journalSections = Array.isArray(data.sections) ? data.sections.slice() : [];
    state.journalPromptDefault = Boolean(data.is_default);
    state.journalPromptEditing = false;
    state.journalSectionsDraft = state.journalSections.slice();
    renderJournalPrompt();
    showBanner("일지 형식을 저장했습니다.", "ok");
  } catch (err) {
    showBanner(err.message, "error");
  }
}

async function resetJournalPrompt() {
  if (!window.confirm("섹션을 기본값으로 되돌릴까요?")) return;
  try {
    const data = await api("/api/journal-prompt/reset", { method: "POST", body: "{}" });
    state.journalSections = Array.isArray(data.sections) ? data.sections.slice() : [];
    state.journalPromptDefault = Boolean(data.is_default);
    state.journalPromptEditing = false;
    state.journalSectionsDraft = state.journalSections.slice();
    renderJournalPrompt();
    showBanner("기본 섹션으로 복원했습니다.", "ok");
  } catch (err) {
    showBanner(err.message, "error");
  }
}

function addPromptSection() {
  if (!state.journalPromptEditing) return;
  syncPromptDraftFromInputs();
  if (state.journalSectionsDraft.length >= 20) {
    showBanner("섹션은 20개까지 가능합니다.", "error");
    return;
  }
  state.journalSectionsDraft.push("");
  renderJournalPrompt(state.journalSectionsDraft.length - 1);
}

function renderSchedules(items) {
  const list = $("#schedule-list");
  if (!list) return;
  if (!items.length) {
    list.innerHTML = `<li class="empty">등록된 예약이 없습니다.</li>`;
    return;
  }
  list.innerHTML = items
    .map((item) => {
      const target = SCHEDULE_TARGET_LABEL[item.target] || item.target;
      const status = item.last_status
        ? SCHEDULE_STATUS_LABEL[item.last_status] || item.last_status
        : "—";
      const last = item.last_run_at ? item.last_run_at.replace("T", " ").slice(0, 16) : "—";
      return `<li class="schedule-item${item.enabled ? "" : " is-disabled"}">
        <div class="schedule-item-main">
          <strong>${escapeHtml(item.name)}</strong>
          <span class="schedule-meta">${escapeHtml(item.time)} · ${escapeHtml(target)}</span>
          <span class="schedule-meta">최근 ${escapeHtml(last)} · ${escapeHtml(status)}</span>
          ${item.last_message ? `<span class="schedule-meta">${escapeHtml(item.last_message)}</span>` : ""}
        </div>
        <div class="schedule-item-actions">
          <label class="run-check schedule-toggle" title="예약 사용">
            <input type="checkbox" data-schedule-toggle="${escapeHtml(item.id)}" ${item.enabled ? "checked" : ""}>
            사용
          </label>
          <button type="button" class="icon-btn danger-icon schedule-delete" data-schedule-delete="${escapeHtml(item.id)}" title="삭제" aria-label="삭제">
            <span class="material-symbols-outlined" aria-hidden="true">delete</span>
          </button>
        </div>
      </li>`;
    })
    .join("");
}

function renderJobBatch(job) {
  const batch = $("#job-batch");
  const fill = $("#job-batch-fill");
  const text = $("#job-batch-text");
  if (!batch || !fill || !text) return;
  const total = job.total || 0;
  const showBatch = total > 1;
  batch.classList.toggle("hidden", !showBatch);
  if (!showBatch) return;
  const done = job.done || 0;
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
  fill.style.width = `${pct}%`;
  const current = job.date ? ` · ${job.date}` : "";
  if (job.status === "running") {
    text.textContent = `${done}/${total} 처리 중${current}${job.message ? ` — ${job.message}` : ""}`;
    return;
  }
  text.textContent = job.message || `${done}/${total} 완료`;
}

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
  renderJobBatch(job);
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
  const day = runMode === "single" ? $("#run-date")?.value : null;
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
    renderJob(job);
    showBanner(job.message || "일지 생성을 마쳤습니다.", "ok");
    setRunButtonBusy(false);
    await syncRunStepsForSelectedDate();
    loadJournals();
    loadOverview();
  } else if (prev === "running" && job.status === "error") {
    showBanner(job.message || "일지 생성 중 오류가 발생했습니다.", "error");
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

function syncChatIdToggle() {
  const btn = $("#toggle-chat-ids");
  if (!btn) return;
  btn.classList.toggle("is-active", state.showChatIds);
  btn.setAttribute("aria-pressed", state.showChatIds ? "true" : "false");
  btn.title = state.showChatIds ? "ID 숨기기" : "ID 표시";
  btn.setAttribute("aria-label", state.showChatIds ? "ID 숨기기" : "ID 표시");
}

$("#toggle-chat-ids")?.addEventListener("click", () => {
  state.showChatIds = !state.showChatIds;
  syncChatIdToggle();
  renderDialogs($("#dialog-filter")?.value);
  renderWatched();
});

$("#watched-edit")?.addEventListener("click", () => {
  state.watchedEditMode = !state.watchedEditMode;
  if (!state.watchedEditMode) {
    state.watchedSelected = {};
    state.watchedFilter = "";
    const filter = $("#watched-filter");
    if (filter) filter.value = "";
  }
  syncWatchedEditUi();
  renderWatched();
});

$("#watched-select-all")?.addEventListener("click", () => {
  if (!state.watchedEditMode) return;
  const rows = filteredWatchedChats();
  const ids = rows.map((chat) => String(chat.id));
  const allSelected = ids.length > 0 && ids.every((id) => state.watchedSelected[id]);
  if (allSelected) {
    ids.forEach((id) => delete state.watchedSelected[id]);
  } else {
    ids.forEach((id) => {
      state.watchedSelected[id] = true;
    });
  }
  renderWatched();
});

$("#watched-delete-selected")?.addEventListener("click", () => {
  removeSelectedWatched().catch((err) => showBanner(err.message, "error"));
});

$("#watched-filter")?.addEventListener("input", (event) => {
  state.watchedFilter = event.target.value || "";
  renderWatched();
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
$("#watched-scroll")?.addEventListener("scroll", updateWatchedScrollFade, { passive: true });
$("#run-plan-scroll")?.addEventListener("scroll", updateRunPlanScrollFade, { passive: true });
$("#journal-list-scroll")?.addEventListener("scroll", updateJournalScrollFade, { passive: true });
window.addEventListener("resize", () => {
  updateDialogScrollFade();
  updateJournalScrollFade();
  updateRunPlanScrollFade();
  updateWatchedScrollFade();
});

document.querySelectorAll(".run-mode-tab").forEach((btn) => {
  btn.addEventListener("click", () => setRunMode(btn.dataset.runMode || "single"));
});

$("#run-preview-button")?.addEventListener("click", async () => {
  const wrap = $("#run-plan-wrap");
  if (wrap && !wrap.classList.contains("hidden")) {
    hideRunPlan();
    return;
  }
  try {
    const data = await api("/api/run/plan", {
      method: "POST",
      body: JSON.stringify(buildRunBody()),
    });
    renderRunPlan(data);
  } catch (err) {
    showBanner(err.message, "error");
  }
});

$("#run-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const body = buildRunBody();
    const job = await api("/api/run", {
      method: "POST",
      body: JSON.stringify(body),
    });
    lastJobStatus = "running";
    renderJob(job);
    pollJob();
  } catch (err) {
    showBanner(err.message, "error");
  }
});

$("#run-pick-add")?.addEventListener("click", () => {
  try {
    addRunPickDate($("#run-pick-date")?.value);
  } catch (err) {
    showBanner(err.message, "error");
  }
});

$("#run-pick-list")?.addEventListener("click", (event) => {
  const chip = event.target.closest(".run-pick-chip");
  if (!chip?.dataset.day) return;
  removeRunPickDate(chip.dataset.day);
});

for (const id of ["run-date", "run-start", "run-end"]) {
  $(`#${id}`)?.addEventListener("change", () => syncRunStepsForSelectedDate());
  $(`#${id}`)?.addEventListener("input", () => syncRunStepsForSelectedDate());
}
document.querySelectorAll('input[name="run-policy"]').forEach((input) => {
  input.addEventListener("change", () => {
    hideRunPlan();
  });
});

$("#prompt-edit")?.addEventListener("click", () => {
  state.journalPromptEditing = true;
  state.journalSectionsDraft = state.journalSections.slice();
  renderJournalPrompt();
});

$("#prompt-cancel")?.addEventListener("click", () => {
  state.journalPromptEditing = false;
  state.journalSectionsDraft = state.journalSections.slice();
  renderJournalPrompt();
});

$("#prompt-save")?.addEventListener("click", () => {
  saveJournalPrompt().catch((err) => showBanner(err.message, "error"));
});

$("#prompt-reset")?.addEventListener("click", () => {
  resetJournalPrompt().catch((err) => showBanner(err.message, "error"));
});

$("#prompt-sections")?.addEventListener("input", (event) => {
  if (!state.journalPromptEditing) return;
  const label = event.target.closest?.(".prompt-section-label.is-editable");
  if (!label) return;
  const index = Number(label.dataset.index);
  if (!Number.isInteger(index) || index < 0 || index >= state.journalSectionsDraft.length) return;
  const text = (label.textContent || "").replace(/\u200b/g, "");
  if (text.length > 40) {
    label.textContent = text.slice(0, 40);
    // keep caret at end
    const range = document.createRange();
    range.selectNodeContents(label);
    range.collapse(false);
    const sel = window.getSelection();
    sel?.removeAllRanges();
    sel?.addRange(range);
  }
  state.journalSectionsDraft[index] = (label.textContent || "").replace(/\u200b/g, "");
});

$("#prompt-sections")?.addEventListener("keydown", (event) => {
  if (!state.journalPromptEditing) return;
  const label = event.target.closest?.(".prompt-section-label.is-editable");
  if (!label) return;
  if (event.key === "Enter") {
    event.preventDefault();
    addPromptSection();
  }
});

$("#prompt-sections")?.addEventListener("click", (event) => {
  if (!state.journalPromptEditing) return;
  if (event.target.closest(".prompt-section-add-btn")) {
    addPromptSection();
    return;
  }
  const btn = event.target.closest(".prompt-section-remove");
  if (!btn) return;
  syncPromptDraftFromInputs();
  const index = Number(btn.dataset.index);
  if (!Number.isInteger(index) || index < 0 || index >= state.journalSectionsDraft.length) return;
  state.journalSectionsDraft.splice(index, 1);
  renderJournalPrompt();
});

$("#schedule-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const name = $("#schedule-name")?.value?.trim();
    const time = $("#schedule-time")?.value;
    if (!name) throw new Error("예약 이름을 입력하세요.");
    if (!time) throw new Error("실행 시각을 선택하세요.");
    await api("/api/schedules", {
      method: "POST",
      body: JSON.stringify({
        name,
        time,
        target: $("#schedule-target")?.value || "yesterday",
        skip_existing: Boolean($("#schedule-skip-existing")?.checked),
        enabled: true,
      }),
    });
    $("#schedule-name").value = "";
    showBanner("예약을 추가했습니다.", "ok");
    loadSchedules();
  } catch (err) {
    showBanner(err.message, "error");
  }
});

$("#schedule-list")?.addEventListener("click", async (event) => {
  const deleteBtn = event.target.closest("[data-schedule-delete]");
  if (deleteBtn?.dataset.scheduleDelete) {
    try {
      await api(`/api/schedules/${deleteBtn.dataset.scheduleDelete}`, { method: "DELETE" });
      showBanner("예약을 삭제했습니다.", "ok");
      loadSchedules();
    } catch (err) {
      showBanner(err.message, "error");
    }
    return;
  }
  const toggle = event.target.closest("[data-schedule-toggle]");
  if (toggle?.dataset.scheduleToggle) {
    try {
      await api(`/api/schedules/${toggle.dataset.scheduleToggle}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: toggle.checked }),
      });
      loadSchedules();
    } catch (err) {
      showBanner(err.message, "error");
      loadSchedules();
    }
  }
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
