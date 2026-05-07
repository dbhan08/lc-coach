// Side panel logic.
// Talks to content.js (in the active LeetCode tab) to read the current
// problem, and to the local Python service on 127.0.0.1:8765 for hints.

const SERVICE_BASE = "http://127.0.0.1:8765";

const els = {
  statusDot: document.getElementById("status-dot"),
  problemEmpty: document.getElementById("problem-empty"),
  problemLoaded: document.getElementById("problem-loaded"),
  problemTitle: document.getElementById("problem-title"),
  problemDifficulty: document.getElementById("problem-difficulty"),
  problemTags: document.getElementById("problem-tags"),
  hintOutput: document.getElementById("hint-output"),
  errorCard: document.getElementById("error-card"),
  errorMessage: document.getElementById("error-message"),
  errorRetry: document.getElementById("error-retry"),
  refreshBtn: document.getElementById("refresh-btn"),
  hintButtons: document.querySelectorAll(".hint-btn"),
};

let currentProblem = null;
let inflightHint = false;

function setStatusDot(state) {
  // 'ok' | 'bad' | 'unknown'
  els.statusDot.className = "dot dot-" + state;
}

function showError(msg) {
  els.errorMessage.textContent = msg;
  els.errorCard.hidden = false;
}

function clearError() {
  els.errorCard.hidden = true;
  els.errorMessage.textContent = "";
}

function setHintOutput(text, { withSpinner = false } = {}) {
  if (withSpinner) {
    els.hintOutput.classList.remove("has-content");
    els.hintOutput.classList.add("muted");
    els.hintOutput.innerHTML = '<span class="spinner"></span>thinking…';
  } else if (text) {
    els.hintOutput.classList.remove("muted");
    els.hintOutput.classList.add("has-content");
    els.hintOutput.textContent = text;
  } else {
    els.hintOutput.classList.add("muted");
    els.hintOutput.classList.remove("has-content");
    els.hintOutput.textContent = "Click a hint level when you're stuck.";
  }
}

function disableHintButtons(disabled) {
  els.hintButtons.forEach((b) => (b.disabled = disabled));
}

function renderProblem(p) {
  if (!p || !p.slug || !p.title) {
    els.problemLoaded.hidden = true;
    els.problemEmpty.hidden = false;
    disableHintButtons(true);
    return;
  }
  els.problemEmpty.hidden = true;
  els.problemLoaded.hidden = false;
  els.problemTitle.textContent = p.title;

  els.problemDifficulty.className = "pill";
  if (p.difficulty) {
    els.problemDifficulty.textContent = p.difficulty;
    els.problemDifficulty.classList.add(p.difficulty.toLowerCase());
  } else {
    els.problemDifficulty.textContent = "—";
  }

  els.problemTags.innerHTML = "";
  (p.tags || []).slice(0, 8).forEach((t) => {
    const span = document.createElement("span");
    span.className = "tag";
    span.textContent = t;
    els.problemTags.appendChild(span);
  });

  disableHintButtons(false);
}

async function pingService() {
  try {
    const r = await fetch(`${SERVICE_BASE}/health`, { method: "GET" });
    if (!r.ok) throw new Error(`status ${r.status}`);
    setStatusDot("ok");
    return true;
  } catch (err) {
    setStatusDot("bad");
    showError(
      "Local service not reachable on 127.0.0.1:8765. Run `python -m lc_coach` in a terminal.",
    );
    return false;
  }
}

async function loadCurrentProblem() {
  clearError();
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.url || !tab.url.startsWith("https://leetcode.com/problems/")) {
    currentProblem = null;
    renderProblem(null);
    return;
  }
  let response;
  try {
    response = await chrome.tabs.sendMessage(tab.id, { type: "GET_PROBLEM" });
  } catch (err) {
    // Content script may not be injected yet (tab opened before extension load)
    showError(
      "Couldn't read the LeetCode page. Reload the LeetCode tab and try again.",
    );
    renderProblem(null);
    return;
  }
  currentProblem = response;
  renderProblem(response);
}

async function postProblemToService(p) {
  const r = await fetch(`${SERVICE_BASE}/problems`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      slug: p.slug,
      title: p.title,
      statement: p.statement || "(statement unavailable; reload the page after the description loads)",
      difficulty: p.difficulty || null,
      tags: p.tags || [],
    }),
  });
  if (!r.ok) {
    throw new Error(`POST /problems failed: ${r.status} ${await r.text()}`);
  }
  return await r.json();
}

async function requestHint(level) {
  if (!currentProblem || !currentProblem.slug) {
    showError("No problem loaded.");
    return;
  }
  if (inflightHint) return;
  inflightHint = true;
  clearError();
  disableHintButtons(true);
  setHintOutput(null, { withSpinner: true });
  try {
    await postProblemToService(currentProblem);
    const r = await fetch(`${SERVICE_BASE}/hint`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slug: currentProblem.slug, level }),
    });
    if (!r.ok) {
      const detail = await r.text();
      throw new Error(`POST /hint failed: ${r.status} ${detail}`);
    }
    const body = await r.json();
    setHintOutput(body.response);
  } catch (err) {
    setStatusDot("bad");
    showError(err.message || String(err));
    setHintOutput(null);
  } finally {
    inflightHint = false;
    disableHintButtons(false);
  }
}

// --- Wiring ---------------------------------------------------------------

els.hintButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    const level = parseInt(btn.dataset.level, 10);
    requestHint(level);
  });
});

els.refreshBtn.addEventListener("click", () => {
  loadCurrentProblem();
});

els.errorRetry.addEventListener("click", async () => {
  clearError();
  const ok = await pingService();
  if (ok) await loadCurrentProblem();
});

// Re-read the problem when the user switches tabs or activates the panel.
chrome.tabs.onActivated.addListener(() => loadCurrentProblem());
chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (info.status === "complete") loadCurrentProblem();
});

// Boot.
(async () => {
  await pingService();
  await loadCurrentProblem();
})();
