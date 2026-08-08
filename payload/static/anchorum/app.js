const caseListEl = document.getElementById("case-list");
const caseDetailEl = document.getElementById("case-detail");
const refreshBtn = document.getElementById("refresh-cases");
const chatLogEl = document.getElementById("chat-log");
const chatInput = document.getElementById("chat-input");
const btnLegal = document.getElementById("btn-legal");
const btnAsk = document.getElementById("btn-ask");

let activeCaseId = null;

function apiUrl(path) {
  return `${window.location.protocol}//${window.location.host}${path}`;
}

async function apiGet(path) {
  const res = await fetch(apiUrl(path), {
    method: "GET",
    credentials: "same-origin",
    headers: { "Accept": "application/json" },
  });
  if (res.status === 401) {
    window.location.href = "/dashboard/login";
    return null;
  }
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function loadCases() {
  caseListEl.innerHTML = '<li class="empty">Loading cases…</li>';
  try {
    const cases = await apiGet("/api/v1/anchorum/cases");
    if (!cases) return;
    caseListEl.innerHTML = "";
    if (!cases.length) {
      caseListEl.innerHTML = '<li class="empty">No cases found.</li>';
      return;
    }
    cases.forEach((id) => {
      const li = document.createElement("li");
      li.textContent = id;
      li.dataset.caseId = id;
      li.addEventListener("click", () => selectCase(id));
      caseListEl.appendChild(li);
    });
  } catch (err) {
    caseListEl.innerHTML = `<li class="empty error-text">Failed to load cases: ${escapeHtml(err.message)}</li>`;
  }
}

async function selectCase(caseId) {
  activeCaseId = caseId;
  document.querySelectorAll(".case-list li").forEach((li) => li.classList.remove("active"));
  const li = caseListEl.querySelector(`[data-case-id="${CSS.escape(caseId)}"]`);
  if (li) li.classList.add("active");

  caseDetailEl.innerHTML = '<p class="loading">Loading case summary…</p>';
  try {
    const summary = await apiGet(`/api/v1/anchorum/cases/${encodeURIComponent(caseId)}/summary`);
    if (!summary) return;
    const generated = summary.generated_at ? new Date(summary.generated_at).toLocaleString() : "N/A";
    caseDetailEl.innerHTML = `
      <div class="case-summary">
        <p><strong>Case ID:</strong> ${escapeHtml(summary.case_id)}</p>
        <p><strong>Generated:</strong> ${escapeHtml(generated)}</p>
        <p><strong>Artifacts:</strong> ${summary.artifact_count} · <strong>Entities:</strong> ${summary.entity_count} · <strong>Anomalies:</strong> ${summary.anomaly_count}</p>
        <p><strong>Severity counts:</strong>
          Critical ${summary.critical_count} · High ${summary.high_count} · Medium ${summary.medium_count} · Low ${summary.low_count}
        </p>
      </div>
    `;
  } catch (err) {
    caseDetailEl.innerHTML = `<p class="error-text">Failed to load case: ${escapeHtml(err.message)}</p>`;
  }
}

function escapeHtml(text) {
  if (text == null) return "";
  const div = document.createElement("div");
  div.textContent = String(text);
  return div.innerHTML;
}

function appendMessage(role, html, meta = "") {
  const div = document.createElement("div");
  div.className = `message ${role}`;
  if (meta) div.innerHTML = `<div class="meta">${escapeHtml(meta)}</div>` + html;
  else div.innerHTML = html;
  chatLogEl.appendChild(div);
  chatLogEl.scrollTop = chatLogEl.scrollHeight;
}

async function sendChat(commandPrefix) {
  const text = chatInput.value.trim();
  if (!text) return;
  const mode = commandPrefix === "/legal" ? "legal" : "ask";
  appendMessage("user", escapeHtml(text), "You");
  chatInput.value = "";
  const thinking = document.createElement("div");
  thinking.className = "message agent";
  thinking.innerHTML = '<span class="meta">Egregore</span>Thinking…';
  chatLogEl.appendChild(thinking);
  chatLogEl.scrollTop = chatLogEl.scrollHeight;
  try {
    const res = await fetch(apiUrl("/api/v1/anchorum/chat"), {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify({ message: text, mode }),
    });
    thinking.remove();
    if (res.status === 401) {
      window.location.href = "/dashboard/login";
      return;
    }
    const data = await res.json();
    if (!res.ok || !data.ok) {
      appendMessage("error", escapeHtml(data.detail || `${res.status} ${res.statusText}`), "System");
      return;
    }
    appendMessage("agent", escapeHtml(data.content || "").replace(/\n/g, "<br>"), `Egregore ${commandPrefix}`);
  } catch (err) {
    thinking.remove();
    appendMessage("error", escapeHtml(err.message), "System");
  }
}

refreshBtn.addEventListener("click", loadCases);
btnLegal.addEventListener("click", () => sendChat("/legal"));
btnAsk.addEventListener("click", () => sendChat("/ask"));
chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendChat("/legal");
});

loadCases();
