const $ = (sel) => document.querySelector(sel);

const state = {
  view: "journals",
  dialogs: [],
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
    throw new Error(data.detail || response.statusText);
  }
  return data;
}

function setView(name) {
  state.view = name;
  document.querySelectorAll(".view").forEach((el) => el.classList.add("hidden"));
  document.querySelectorAll(".nav-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === name);
  });
  $(`#view-${name}`).classList.remove("hidden");
}

async function loadOverview() {
  const data = await api("/api/overview");
  $("#overview-meta").innerHTML = `
    <div>${data.timezone}</div>
    <div>모델 ${escapeHtml(data.model)}</div>
    <div>업무방 ${data.chat_count} · 일지 ${data.journal_count}</div>
  `;
  renderJob(data.job);
}

async function loadJournals() {
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

document.querySelectorAll(".nav-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    setView(btn.dataset.view);
    if (btn.dataset.view === "chats") {
      loadWatched();
      if (!state.dialogs.length) loadDialogs();
    }
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

loadOverview();
loadJournals();
pollJob();
