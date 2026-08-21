// Side panel logic.
// Talks to content.js (for DOM scrape) and to chrome.scripting (for the
// Monaco editor's current code) on the active LeetCode tab. Posts to the
// local Python service on 127.0.0.1:8765 for problem registration, hints,
// and attempt lifecycle.

const SERVICE_BASE = "http://127.0.0.1:8765";

// Hints are short, contract-driven, and time-sensitive — use Haiku for ~3–5×
// faster responses. Review and mock keep the default model (Sonnet) where
// depth matters more than latency. Complexity is also short + structured
// so Haiku is a fine match.
const HINT_MODEL = "claude-haiku-4-5-20251001";
const COMPLEXITY_MODEL = "claude-haiku-4-5-20251001";

// --- Tiny markdown renderer ----------------------------------------------
// Handles the subset Claude actually emits in our prompts: **bold**,
// *italic*, `inline code`, ```fenced code blocks```, ## headings,
// paragraph breaks. HTML-escape first; mutate after.
function renderMarkdown(text) {
  if (!text) return "";
  const esc = (s) =>
    s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  let html = esc(text);

  // Fenced code blocks first so their inner ` and * don't get mangled
  html = html.replace(/```([a-zA-Z0-9_-]*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    const safe = code.replace(/\n+$/, "");
    return `<pre><code>${safe}</code></pre>`;
  });

  // Inline code (single backticks, no newlines inside)
  html = html.replace(/`([^`\n]+?)`/g, "<code>$1</code>");

  // Bold (**text**) and italic (*text*); bold first so * inside ** doesn't fire
  html = html.replace(/\*\*([^*\n][^*\n]*?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/(^|[^*])\*([^*\n][^*\n]*?)\*(?!\*)/g, "$1<em>$2</em>");

  // Headings
  html = html.replace(/^### (.+)$/gm, "<h3>$1</h3>");
  html = html.replace(/^## (.+)$/gm, "<h2>$1</h2>");

  // Paragraphs from blank lines; single newlines inside become <br>.
  return html
    .split(/\n{2,}/)
    .map((chunk) => {
      const trimmed = chunk.trim();
      if (!trimmed) return "";
      if (
        trimmed.startsWith("<pre>") ||
        trimmed.startsWith("<h2") ||
        trimmed.startsWith("<h3")
      ) {
        return trimmed;
      }
      return `<p>${trimmed.replace(/\n/g, "<br>")}</p>`;
    })
    .filter(Boolean)
    .join("\n");
}

const els = {
  statusDot: document.getElementById("status-dot"),
  problemEmpty: document.getElementById("problem-empty"),
  problemLoaded: document.getElementById("problem-loaded"),
  problemTitle: document.getElementById("problem-title"),
  problemDifficulty: document.getElementById("problem-difficulty"),
  problemTags: document.getElementById("problem-tags"),
  hintOutput: document.getElementById("hint-output"),
  reviewBtn: document.getElementById("review-btn"),
  complexityBtn: document.getElementById("complexity-btn"),
  mockBtn: document.getElementById("mock-btn"),
  errorCard: document.getElementById("error-card"),
  errorMessage: document.getElementById("error-message"),
  errorRetry: document.getElementById("error-retry"),
  refreshBtn: document.getElementById("refresh-btn"),
  hintButtons: document.querySelectorAll(".hint-btn"),
  attemptCard: document.getElementById("attempt-card"),
  attemptIdle: document.getElementById("attempt-idle"),
  attemptActive: document.getElementById("attempt-active"),
  attemptFinishing: document.getElementById("attempt-finishing"),
  attemptTimer: document.getElementById("attempt-timer"),
  startAttemptBtn: document.getElementById("start-attempt-btn"),
  finishAttemptBtn: document.getElementById("finish-attempt-btn"),
  submitOutcomeBtn: document.getElementById("submit-outcome-btn"),
  cancelOutcomeBtn: document.getElementById("cancel-outcome-btn"),
  attemptCodeStatus: document.getElementById("attempt-code-status"),
  masteryList: document.getElementById("mastery-list"),
  masteryMeta: document.getElementById("mastery-meta"),
  dueCard: document.getElementById("due-card"),
  dueList: document.getElementById("due-list"),
  dueMeta: document.getElementById("due-meta"),
  targetInput: document.getElementById("target-input"),
  targetOptions: document.getElementById("target-options"),
  targetMeta: document.getElementById("target-meta"),
  targetStatus: document.getElementById("target-status"),
  nextBtn: document.getElementById("next-btn"),
  nextResult: document.getElementById("next-result"),
  nextLink: document.getElementById("next-link"),
  nextDifficulty: document.getElementById("next-difficulty"),
  nextRationale: document.getElementById("next-rationale"),
  nextSimilar: document.getElementById("next-similar"),
  skipPremiumBtn: document.getElementById("skip-premium-btn"),
  differentOneBtn: document.getElementById("different-one-btn"),
  modeTabs: document.querySelectorAll(".mode-tab"),
  inputCompany: document.getElementById("target-input-company"),
  inputSkill: document.getElementById("target-input-skill"),
  inputImprove: document.getElementById("target-input-improve"),
  skillSelect: document.getElementById("skill-select"),
};

let currentMode = "company"; // "company" | "skill" | "improve"
let lastRecommendedSlug = null;
// Session-only one-shot exclusion list used by "Show different one".
// Resets when the user changes mode, target, pattern, or hits the main Next.
// Mirrored into chrome.storage.session because the side panel document is
// torn down whenever the panel closes — in-memory state alone gets lost, and
// the recommender then ping-pongs between the same two problems.
const EXCLUDE_KEY = "exclude_state";
let excludeSlugs = new Set();
// Scope tag for the saved list, so a list saved for one pattern never leaks
// into another.
let excludeScope = "";
// Startup restores the saved mode/target, which calls clearExclude(). Hold
// off persisting until the boot sequence has restored the saved list, or that
// wipes it before restoreExclude() ever reads it.
let excludeReady = false;

function currentScope() {
  if (currentMode === "company") {
    return `company:${normalizeCompanyClient(els.targetInput.value)}`;
  }
  if (currentMode === "skill") return `skill:${els.skillSelect.value}`;
  return "improve:";
}

async function saveExclude() {
  if (!excludeReady) return;
  try {
    await chrome.storage.session.set({
      [EXCLUDE_KEY]: {
        scope: currentScope(),
        slugs: Array.from(excludeSlugs),
        last: lastRecommendedSlug,
      },
    });
  } catch {}
}

async function restoreExclude() {
  try {
    const got = await chrome.storage.session.get(EXCLUDE_KEY);
    const saved = got?.[EXCLUDE_KEY];
    if (saved && saved.scope === currentScope()) {
      excludeSlugs = new Set(saved.slugs || []);
      excludeScope = saved.scope;
      if (!lastRecommendedSlug && saved.last) lastRecommendedSlug = saved.last;
      updateDifferentLabel();
    }
  } catch {}
}

function clearExclude() {
  excludeSlugs = new Set();
  excludeScope = currentScope();
  saveExclude();
  updateDifferentLabel();
}

function updateDifferentLabel() {
  if (!els.differentOneBtn) return;
  els.differentOneBtn.textContent =
    excludeSlugs.size > 0
      ? `Show different one (${excludeSlugs.size} skipped)`
      : "Show different one";
}

let currentProblem = null;
let activeAttempt = null; // null | { id, started_at, ... }
let timerInterval = null;
let inflightHint = false;

// --- UI helpers -----------------------------------------------------------

function setStatusDot(state) {
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

function setHintOutput(text, { withSpinner = false, raw = false } = {}) {
  if (withSpinner) {
    els.hintOutput.classList.remove("has-content");
    els.hintOutput.classList.add("muted");
    els.hintOutput.innerHTML = '<span class="spinner"></span>thinking…';
  } else if (text) {
    els.hintOutput.classList.remove("muted");
    els.hintOutput.classList.add("has-content");
    els.hintOutput.innerHTML = raw ? text : renderMarkdown(text);
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
    els.attemptCard.hidden = true;
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
  els.attemptCard.hidden = false;
}

function renderAttemptState() {
  els.attemptIdle.hidden = true;
  els.attemptActive.hidden = true;
  els.attemptFinishing.hidden = true;
  if (!activeAttempt) {
    els.attemptIdle.hidden = false;
    stopTimer();
  } else {
    els.attemptActive.hidden = false;
    startTimer();
  }
}

function showFinishingUI() {
  els.attemptIdle.hidden = true;
  els.attemptActive.hidden = true;
  els.attemptFinishing.hidden = false;
  els.attemptCodeStatus.textContent = "";
  els.submitOutcomeBtn.disabled = true;
  document
    .querySelectorAll('input[name="outcome"]')
    .forEach((r) => (r.checked = false));
}

function startTimer() {
  stopTimer();
  if (!activeAttempt) return;
  const startedAt = new Date(activeAttempt.started_at).getTime();
  const tick = () => {
    const elapsed = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
    const m = Math.floor(elapsed / 60);
    const s = elapsed % 60;
    els.attemptTimer.textContent = `${m}m ${s}s`;
  };
  tick();
  timerInterval = setInterval(tick, 1000);
}

function stopTimer() {
  if (timerInterval) {
    clearInterval(timerInterval);
    timerInterval = null;
  }
}

// --- Service calls --------------------------------------------------------

async function pingService() {
  try {
    const r = await fetch(`${SERVICE_BASE}/health`, { method: "GET" });
    if (!r.ok) throw new Error(`status ${r.status}`);
    setStatusDot("ok");
    return true;
  } catch (err) {
    setStatusDot("bad");
    showError(
      "Local service not reachable on 127.0.0.1:8765. Run `./start.sh` (or `python -m lc_coach`) in a terminal.",
    );
    return false;
  }
}

async function postProblemToService(p) {
  const r = await fetch(`${SERVICE_BASE}/problems`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      slug: p.slug,
      title: p.title,
      statement:
        p.statement ||
        "(statement unavailable; reload the page after the description loads)",
      difficulty: p.difficulty || null,
      tags: p.tags || [],
    }),
  });
  if (!r.ok) {
    throw new Error(`POST /problems failed: ${r.status} ${await r.text()}`);
  }
  return await r.json();
}

async function refreshMastery() {
  try {
    const r = await fetch(`${SERVICE_BASE}/weak?n=5`);
    if (!r.ok) throw new Error(`status ${r.status}`);
    const rows = await r.json();
    if (!rows.length) {
      els.masteryList.classList.add("muted");
      els.masteryList.textContent =
        "No attempts yet — finish one to start tracking.";
      els.masteryMeta.textContent = "";
      return;
    }
    els.masteryList.classList.remove("muted");
    els.masteryList.innerHTML = "";
    rows.forEach((row) => {
      const div = document.createElement("div");
      div.className = "mastery-row";
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = row.name;
      const elo = document.createElement("span");
      elo.className = "elo";
      elo.textContent = Math.round(row.elo ?? 1200);
      const n = document.createElement("span");
      n.className = "n";
      n.textContent = `${row.n_attempts}×`;
      div.appendChild(name);
      div.appendChild(elo);
      div.appendChild(n);
      els.masteryList.appendChild(div);
    });
    const totalAttempts = rows.reduce((a, b) => a + (b.n_attempts || 0), 0);
    els.masteryMeta.textContent = `${totalAttempts} attempt${totalAttempts === 1 ? "" : "s"} logged`;
  } catch (err) {
    els.masteryList.classList.add("muted");
    els.masteryList.textContent = "(mastery unavailable)";
    els.masteryMeta.textContent = "";
  }
}

function normalizeCompanyClient(raw) {
  return (raw || "")
    .trim()
    .toLowerCase()
    .replace(/[\s_]+/g, "-")
    .replace(/[^a-z0-9-]/g, "")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
}

function setMode(mode) {
  currentMode = mode;
  els.modeTabs.forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.mode === mode);
  });
  els.inputCompany.hidden = mode !== "company";
  els.inputSkill.hidden = mode !== "skill";
  els.inputImprove.hidden = mode !== "improve";
  // Mode change resets one-shot exclusions
  clearExclude();
  updateNextEnabled();
  try {
    chrome.storage.local.set({ mode });
  } catch {}
}

function updateNextEnabled() {
  if (currentMode === "company") {
    els.nextBtn.disabled = !els.targetInput.value.trim();
  } else if (currentMode === "skill") {
    els.nextBtn.disabled = !els.skillSelect.value;
  } else {
    els.nextBtn.disabled = false; // improve always enabled
  }
}

async function refreshTargets() {
  try {
    const r = await fetch(`${SERVICE_BASE}/companies`);
    if (!r.ok) throw new Error(`status ${r.status}`);
    const rows = await r.json();
    els.targetOptions.innerHTML = "";
    rows.forEach((row) => {
      const opt = document.createElement("option");
      opt.value = row.name;
      opt.label = `${row.name} (${row.n_problems})`;
      els.targetOptions.appendChild(opt);
    });
    els.targetMeta.textContent = `${rows.length} companies ingested`;
  } catch {
    els.targetMeta.textContent = "(companies unavailable)";
  }
  // Populate skill-mode pattern dropdown from /mastery (full coarse-pattern set)
  try {
    const r = await fetch(`${SERVICE_BASE}/mastery`);
    if (r.ok) {
      const rows = await r.json();
      // Sort: lowest Elo first (your weak ones surface up top)
      rows.sort((a, b) => (a.elo ?? 1200) - (b.elo ?? 1200));
      while (els.skillSelect.options.length > 1) els.skillSelect.remove(1);
      rows.forEach((row) => {
        const opt = document.createElement("option");
        opt.value = row.name;
        const elo = Math.round(row.elo ?? 1200);
        opt.textContent =
          row.n_attempts > 0
            ? `${row.name}  (Elo ${elo}, ${row.n_attempts}×)`
            : `${row.name}  (untouched)`;
        els.skillSelect.appendChild(opt);
      });
    }
  } catch {}
  // Restore last-used mode + inputs
  try {
    const stored = await chrome.storage.local.get([
      "mode",
      "target",
      "skill_pattern",
    ]);
    if (stored.target && !els.targetInput.value) {
      els.targetInput.value = stored.target;
    }
    if (stored.skill_pattern) {
      els.skillSelect.value = stored.skill_pattern;
    }
    if (stored.mode && ["company", "skill", "improve"].includes(stored.mode)) {
      setMode(stored.mode);
    } else {
      setMode("company");
    }
  } catch {
    setMode("company");
  }
  updateNextEnabled();
}

function setTargetStatus(text, { isError = false } = {}) {
  if (!text) {
    els.targetStatus.hidden = true;
    els.targetStatus.textContent = "";
    els.targetStatus.style.color = "";
    return;
  }
  els.targetStatus.hidden = false;
  els.targetStatus.textContent = text;
  els.targetStatus.style.color = isError ? "var(--hard)" : "";
}

function renderNextResult(body) {
  els.nextResult.hidden = false;
  els.nextLink.href = body.leetcode_url;
  els.nextLink.textContent = body.title || body.slug;
  els.nextDifficulty.className = "pill";
  if (body.difficulty) {
    els.nextDifficulty.textContent = body.difficulty;
    els.nextDifficulty.classList.add(body.difficulty.toLowerCase());
  } else {
    els.nextDifficulty.textContent = "?";
  }
  els.nextRationale.textContent = body.rationale || "";
  if (body.cold_start_used && body.similar_companies?.length) {
    const top = body.similar_companies
      .slice(0, 3)
      .map((s) => `${s.name} (${s.score.toFixed(2)})`)
      .join(", ");
    els.nextSimilar.textContent = `cold-start expansion: pool drew from ${top}`;
  } else {
    els.nextSimilar.textContent = "";
  }
  lastRecommendedSlug = body.slug;
  els.skipPremiumBtn.disabled = false;
  els.skipPremiumBtn.textContent = "Mark Premium → skip";
  els.differentOneBtn.disabled = false;
  updateDifferentLabel();
}

async function skipAsPremium() {
  if (!lastRecommendedSlug) return;
  const slug = lastRecommendedSlug;
  if (
    !confirm(
      `Mark "${slug}" as Premium-locked and never recommend it again?\n\n` +
        `Use this only if the LeetCode page actually shows a paywall.\n` +
        `For "just give me a different one", use the other button instead.`,
    )
  ) {
    return;
  }
  els.skipPremiumBtn.disabled = true;
  els.skipPremiumBtn.textContent = `marking '${slug}' premium…`;
  try {
    const r = await fetch(
      `${SERVICE_BASE}/problems/${encodeURIComponent(slug)}/premium`,
      { method: "POST" },
    );
    if (!r.ok) throw new Error(`mark failed: ${r.status} ${await r.text()}`);
  } catch (err) {
    setTargetStatus(`couldn't mark premium: ${err.message}`, { isError: true });
    els.skipPremiumBtn.disabled = false;
    els.skipPremiumBtn.textContent = "Mark Premium → skip";
    return;
  }
  // Marking a problem premium is itself a kind of one-shot exclusion for
  // this call; the persistent premium filter handles future ones.
  clearExclude();
  await requestNext();
}

async function requestDifferent() {
  if (!lastRecommendedSlug) return;
  excludeSlugs.add(lastRecommendedSlug);
  await saveExclude();
  els.differentOneBtn.disabled = true;
  els.differentOneBtn.textContent = "looking…";
  await requestNext({ keepExclude: true });
}

async function requestNext({ keepExclude = false } = {}) {
  // The main "Next" button resets one-shot exclusions; "Show different one"
  // calls requestNext({keepExclude:true}) to preserve them across the call.
  if (!keepExclude) clearExclude();

  let url;
  let lookupMsg;

  if (currentMode === "company") {
    const raw = els.targetInput.value.trim();
    const target = normalizeCompanyClient(raw);
    if (!target) return;
    try {
      await chrome.storage.local.set({ target });
    } catch {}
    url = `${SERVICE_BASE}/next?target=${encodeURIComponent(target)}&auto_ingest=true`;
    lookupMsg = "Looking up… (may auto-ingest if not in DB; ~5–30s on first hit)";
  } else if (currentMode === "skill") {
    const pat = els.skillSelect.value;
    if (!pat) return;
    try {
      await chrome.storage.local.set({ skill_pattern: pat });
    } catch {}
    url = `${SERVICE_BASE}/next?mode=skill&pattern=${encodeURIComponent(pat)}`;
    lookupMsg = `Picking from problems tagged with '${pat}'…`;
  } else if (currentMode === "improve") {
    url = `${SERVICE_BASE}/next?mode=improve`;
    lookupMsg = "Auto-targeting your weakest pattern…";
  } else {
    return;
  }
  if (excludeSlugs.size > 0) {
    const enc = encodeURIComponent(Array.from(excludeSlugs).join(","));
    url += (url.includes("?") ? "&" : "?") + `exclude=${enc}`;
  }

  els.nextBtn.disabled = true;
  els.nextResult.hidden = true;
  setTargetStatus(lookupMsg);
  try {
    const r = await fetch(url);
    if (!r.ok) {
      const detail = await r.text();
      throw new Error(`HTTP ${r.status}: ${detail}`);
    }
    const body = await r.json();
    if (body.mode === "company") {
      setTargetStatus(
        body.cold_start_used
          ? `target pool: ${body.target_pool_size} (cold-start expansion engaged)`
          : `target pool: ${body.target_pool_size}`,
      );
    } else {
      const eloMsg =
        body.pattern_elo != null
          ? ` (your Elo: ${Math.round(body.pattern_elo)})`
          : "";
      setTargetStatus(
        `pattern '${body.pattern}'${eloMsg} · pool size ${body.target_pool_size}`,
      );
    }
    renderNextResult(body);
    refreshTargets();
  } catch (err) {
    setTargetStatus(err.message || String(err), { isError: true });
  } finally {
    updateNextEnabled();
  }
}

async function refreshDue() {
  try {
    const r = await fetch(`${SERVICE_BASE}/due?limit=5`);
    if (!r.ok) throw new Error(`status ${r.status}`);
    const rows = await r.json();
    if (!rows.length) {
      els.dueCard.hidden = true;
      return;
    }
    els.dueCard.hidden = false;
    els.dueList.innerHTML = "";
    const today = new Date().toISOString().slice(0, 10);
    rows.forEach((r) => {
      const div = document.createElement("div");
      div.className = "due-row";
      const a = document.createElement("a");
      a.href = `https://leetcode.com/problems/${r.problem_slug}/`;
      a.target = "_blank";
      a.textContent = r.title || r.problem_slug;
      const when = document.createElement("span");
      when.className = "due-when";
      const dueDate = r.due_date || "";
      when.textContent = dueDate <= today ? "due now" : dueDate.slice(5);
      if (dueDate < today) when.classList.add("due-overdue");
      div.appendChild(a);
      div.appendChild(when);
      els.dueList.appendChild(div);
    });
    els.dueMeta.textContent = `${rows.length} problem${rows.length === 1 ? "" : "s"}`;
  } catch {
    els.dueCard.hidden = true;
  }
}

async function refreshActiveAttempt() {
  if (!currentProblem || !currentProblem.slug) {
    activeAttempt = null;
    renderAttemptState();
    return;
  }
  try {
    const r = await fetch(
      `${SERVICE_BASE}/attempts/active?slug=${encodeURIComponent(currentProblem.slug)}`,
    );
    if (r.ok) {
      const body = await r.json();
      activeAttempt = body || null;
    } else {
      activeAttempt = null;
    }
  } catch {
    activeAttempt = null;
  }
  renderAttemptState();
}

async function startAttempt() {
  if (!currentProblem || !currentProblem.slug) return;
  clearError();
  els.startAttemptBtn.disabled = true;
  try {
    await postProblemToService(currentProblem);
    const r = await fetch(`${SERVICE_BASE}/attempts/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slug: currentProblem.slug }),
    });
    if (!r.ok) throw new Error(`status ${r.status}: ${await r.text()}`);
    activeAttempt = await r.json();
    renderAttemptState();
  } catch (err) {
    showError(err.message || String(err));
  } finally {
    els.startAttemptBtn.disabled = false;
  }
}

async function getMonacoCode() {
  // Read the user's current code from the LeetCode Monaco editor on the
  // active tab. Runs in the page's MAIN world so it can touch the global
  // `monaco` object the LeetCode bundle exposes.
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) return { error: "no active tab" };
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world: "MAIN",
      func: () => {
        try {
          if (typeof monaco === "undefined") return { error: "monaco not loaded yet" };
          const editors =
            (monaco.editor.getEditors && monaco.editor.getEditors()) || [];
          // Pick the first editor that actually has user-editable content.
          for (const e of editors) {
            try {
              const v = e.getValue();
              if (typeof v === "string" && v.length > 0) {
                return { value: v, via: "getEditors" };
              }
            } catch {}
          }
          const models =
            (monaco.editor.getModels && monaco.editor.getModels()) || [];
          for (const m of models) {
            try {
              const v = m.getValue();
              if (typeof v === "string" && v.length > 0) {
                return { value: v, via: "getModels" };
              }
            } catch {}
          }
          return { error: "no monaco editors found" };
        } catch (e) {
          return { error: String(e && e.message ? e.message : e) };
        }
      },
    });
    return results?.[0]?.result || { error: "executeScript returned nothing" };
  } catch (err) {
    return { error: String(err && err.message ? err.message : err) };
  }
}

async function submitOutcome() {
  const radio = document.querySelector('input[name="outcome"]:checked');
  if (!radio || !activeAttempt) return;
  els.submitOutcomeBtn.disabled = true;
  els.attemptCodeStatus.textContent = "Reading your code from the editor…";
  const codeResult = await getMonacoCode();
  let code = null;
  if (codeResult && codeResult.value) {
    code = codeResult.value;
    els.attemptCodeStatus.textContent =
      `Captured ${code.length} chars from the editor.`;
  } else {
    els.attemptCodeStatus.textContent =
      `(Couldn't read editor — saving without code: ${codeResult?.error || "unknown"})`;
  }
  try {
    const r = await fetch(`${SERVICE_BASE}/attempts/done`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        attempt_id: activeAttempt.id,
        outcome: radio.value,
        code_snapshot: code,
        language: null,
      }),
    });
    if (!r.ok) throw new Error(`status ${r.status}: ${await r.text()}`);
    activeAttempt = null;
    renderAttemptState();
    refreshMastery();
    refreshDue();
  } catch (err) {
    showError(err.message || String(err));
    els.submitOutcomeBtn.disabled = false;
  }
}

async function requestReview() {
  if (!currentProblem || !currentProblem.slug) {
    showError("No problem loaded.");
    return;
  }
  if (inflightHint) return;
  inflightHint = true;
  clearError();
  els.reviewBtn.disabled = true;
  els.complexityBtn.disabled = true;
  els.mockBtn.disabled = true;
  setHintOutput(null, { withSpinner: true });
  try {
    await postProblemToService(currentProblem);
    const codeResult = await getMonacoCode();
    const code = codeResult?.value || "";
    if (!code) {
      throw new Error(
        "Couldn't read your code from the editor — make sure the LeetCode tab is the active one.",
      );
    }
    const r = await fetch(`${SERVICE_BASE}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        slug: currentProblem.slug,
        code,
        language: null,
      }),
    });
    if (!r.ok) {
      throw new Error(`POST /review failed: ${r.status} ${await r.text()}`);
    }
    const body = await r.json();
    setHintOutput(`[code review · ${code.length} chars submitted]\n\n${body.response}`);
  } catch (err) {
    showError(err.message || String(err));
    setHintOutput(null);
  } finally {
    inflightHint = false;
    els.reviewBtn.disabled = false;
    els.complexityBtn.disabled = false;
    els.mockBtn.disabled = false;
  }
}

async function requestComplexity() {
  if (!currentProblem || !currentProblem.slug) {
    showError("No problem loaded.");
    return;
  }
  if (inflightHint) return;
  inflightHint = true;
  clearError();
  els.reviewBtn.disabled = true;
  els.complexityBtn.disabled = true;
  els.mockBtn.disabled = true;
  setHintOutput(null, { withSpinner: true });
  try {
    await postProblemToService(currentProblem);
    const codeResult = await getMonacoCode();
    const code = codeResult?.value || "";
    if (!code) {
      throw new Error(
        "Couldn't read your code from the editor — make sure the LeetCode tab is the active one.",
      );
    }
    const r = await fetch(`${SERVICE_BASE}/complexity`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        slug: currentProblem.slug,
        code,
        language: null,
        model: COMPLEXITY_MODEL,
      }),
    });
    if (!r.ok) {
      throw new Error(`POST /complexity failed: ${r.status} ${await r.text()}`);
    }
    const body = await r.json();
    setHintOutput(`[complexity · ${code.length} chars submitted]\n\n${body.response}`);
  } catch (err) {
    showError(err.message || String(err));
    setHintOutput(null);
  } finally {
    inflightHint = false;
    els.reviewBtn.disabled = false;
    els.complexityBtn.disabled = false;
    els.mockBtn.disabled = false;
  }
}

async function requestMock() {
  if (!currentProblem || !currentProblem.slug) {
    showError("No problem loaded.");
    return;
  }
  if (inflightHint) return;
  inflightHint = true;
  clearError();
  els.reviewBtn.disabled = true;
  els.complexityBtn.disabled = true;
  els.mockBtn.disabled = true;
  setHintOutput(null, { withSpinner: true });
  try {
    await postProblemToService(currentProblem);
    const r = await fetch(`${SERVICE_BASE}/mock`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slug: currentProblem.slug }),
    });
    if (!r.ok) {
      throw new Error(`POST /mock failed: ${r.status} ${await r.text()}`);
    }
    const body = await r.json();
    setHintOutput(`[mock interview round]\n\n${body.response}`);
  } catch (err) {
    showError(err.message || String(err));
    setHintOutput(null);
  } finally {
    inflightHint = false;
    els.reviewBtn.disabled = false;
    els.complexityBtn.disabled = false;
    els.mockBtn.disabled = false;
  }
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
      body: JSON.stringify({ slug: currentProblem.slug, level, model: HINT_MODEL }),
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

async function loadCurrentProblem() {
  clearError();
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.url || !tab.url.startsWith("https://leetcode.com/problems/")) {
    currentProblem = null;
    activeAttempt = null;
    renderProblem(null);
    renderAttemptState();
    return;
  }
  let response;
  try {
    response = await chrome.tabs.sendMessage(tab.id, { type: "GET_PROBLEM" });
  } catch (err) {
    showError(
      "Couldn't read the LeetCode page. Reload the LeetCode tab and try again.",
    );
    renderProblem(null);
    return;
  }
  currentProblem = response;
  renderProblem(response);
  await refreshActiveAttempt();
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

els.startAttemptBtn.addEventListener("click", startAttempt);
els.finishAttemptBtn.addEventListener("click", showFinishingUI);
els.cancelOutcomeBtn.addEventListener("click", () => renderAttemptState());
els.submitOutcomeBtn.addEventListener("click", submitOutcome);

els.reviewBtn.addEventListener("click", requestReview);
els.complexityBtn.addEventListener("click", requestComplexity);
els.mockBtn.addEventListener("click", requestMock);

els.nextBtn.addEventListener("click", requestNext);
els.skipPremiumBtn.addEventListener("click", skipAsPremium);
els.differentOneBtn.addEventListener("click", requestDifferent);
els.targetInput.addEventListener("input", () => {
  clearExclude(); // changing input invalidates accumulated skips
  updateNextEnabled();
});
els.targetInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && els.targetInput.value.trim()) {
    e.preventDefault();
    requestNext();
  }
});
els.skillSelect.addEventListener("change", () => {
  clearExclude();
  updateNextEnabled();
});
els.modeTabs.forEach((tab) => {
  tab.addEventListener("click", () => setMode(tab.dataset.mode));
});

document.querySelectorAll('input[name="outcome"]').forEach((r) => {
  r.addEventListener("change", () => {
    els.submitOutcomeBtn.disabled = !document.querySelector(
      'input[name="outcome"]:checked',
    );
  });
});

chrome.tabs.onActivated.addListener(() => loadCurrentProblem());
chrome.tabs.onUpdated.addListener((_tabId, info) => {
  if (info.status === "complete") loadCurrentProblem();
});

(async () => {
  await pingService();
  await loadCurrentProblem();
  await restoreExclude();
  excludeReady = true;
  refreshMastery();
  refreshDue();
  refreshTargets();
})();
