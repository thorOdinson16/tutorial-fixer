// Wires the page together: source tabs, preview, settings, submit, progress, result, history.
import { api } from "./api.js";
import { $, clear, el } from "./dom.js";
import { closeDrawer, openDrawer } from "./history.js";
import { hideProgress, setStages, startProgress, stopProgress, update } from "./progress.js";
import { renderResult } from "./results.js";

const POLL_MS = 1500;
const state = { source: "url", fileText: "", preview: null, pollTimer: null, modeKeys: [] };

function safe(fn) {
  try {
    return fn();
  } catch (_) {
    return null;
  }
}

// ---- theme ---------------------------------------------------------------------------------
function applyTheme(theme) {
  if (theme) document.documentElement.setAttribute("data-theme", theme);
  else document.documentElement.removeAttribute("data-theme");
}
applyTheme(safe(() => localStorage.getItem("trf-theme")));
$("theme-btn").addEventListener("click", () => {
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  const now = document.documentElement.getAttribute("data-theme") || (dark ? "dark" : "light");
  const next = now === "dark" ? "light" : "dark";
  applyTheme(next);
  safe(() => localStorage.setItem("trf-theme", next));
});

// ---- errors and status ---------------------------------------------------------------------
function showError(text) {
  $("error").textContent = text || "";
  $("error").hidden = !text;
}

// ---- source tabs ---------------------------------------------------------------------------
const TABS = ["url", "paste", "file"];
function selectTab(name) {
  state.source = name;
  TABS.forEach((t) => {
    $("tab-" + t).setAttribute("aria-selected", String(t === name));
    $("pane-" + t).hidden = t !== name;
  });
  state.preview = null;
  $("preview").hidden = true;
  showError("");
  refreshEstimate();
}
TABS.forEach((t) => $("tab-" + t).addEventListener("click", () => selectTab(t)));

function currentSource() {
  if (state.source === "url") return { url: $("url").value.trim() };
  if (state.source === "paste") return { markdown: $("markdown").value };
  return { markdown: state.fileText };
}

function hasSource() {
  const s = currentSource();
  return Boolean((s.url || s.markdown || "").trim());
}

// ---- file loading --------------------------------------------------------------------------
function loadFile(file) {
  if (!file) return;
  file
    .text()
    .then((text) => {
      state.fileText = text;
      $("file-name").textContent = file.name + " (" + text.length.toLocaleString() + " characters)";
      refreshEstimate();
    })
    .catch(() => showError("Could not read that file."));
}
$("file").addEventListener("change", () => loadFile($("file").files && $("file").files[0]));
const zone = $("dropzone");
["dragenter", "dragover"].forEach((ev) =>
  zone.addEventListener(ev, (e) => {
    e.preventDefault();
    zone.classList.add("over");
  }),
);
["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, () => zone.classList.remove("over")));
zone.addEventListener("drop", (e) => {
  e.preventDefault();
  loadFile(e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]);
});

// ---- preview and estimate ------------------------------------------------------------------
function describePreview(p) {
  $("pv-title").textContent = p.title || "Untitled";
  $("pv-line").textContent =
    p.python_blocks + (p.python_blocks === 1 ? " Python block" : " Python blocks") + " found";
  const libs = $("pv-libs");
  clear(libs);
  if (p.libraries.length) p.libraries.forEach((l) => libs.appendChild(el("span", "chip", l)));
  else libs.appendChild(el("span", "chip plain", "no supported library imported"));
  $("preview").hidden = false;
}

function minutes(seconds) {
  const m = Math.max(1, Math.round(seconds / 60));
  return "about " + m + (m === 1 ? " minute" : " minutes");
}

function refreshEstimate() {
  const box = $("estimate");
  if (state.preview) {
    const p = state.preview;
    if (!p.python_blocks) {
      box.textContent = "No Python code blocks were found, so there is nothing to fix.";
    } else {
      box.textContent =
        "Estimate: " + minutes(p.est_seconds) + ", about $" + p.est_budget_usd.toFixed(2) +
        " in model fees at most. Blocks that already run are left alone.";
    }
  } else if (hasSource()) {
    box.textContent = state.source === "url"
      ? "Press Check page to see what is on it, or just Fix tutorial."
      : "Ready. Press Fix tutorial.";
  } else {
    box.textContent = "Add a tutorial to see a time and cost estimate.";
  }
}

async function checkSource() {
  showError("");
  if (!hasSource()) {
    showError(state.source === "url" ? "Enter a web address first." : "Add a tutorial first.");
    return null;
  }
  const btn = $("check-btn");
  btn.disabled = true;
  btn.textContent = "Checking…";
  try {
    const p = await api.preview(currentSource());
    state.preview = p;
    describePreview(p);
    refreshEstimate();
    return p;
  } catch (err) {
    state.preview = null;
    $("preview").hidden = true;
    showError(err.message);
    return null;
  } finally {
    btn.disabled = false;
    btn.textContent = "Check page";
  }
}
$("check-btn").addEventListener("click", checkSource);
$("url").addEventListener("input", () => {
  state.preview = null;
  $("preview").hidden = true;
  refreshEstimate();
});
let typing = null;
$("markdown").addEventListener("input", () => {
  clearTimeout(typing);
  typing = setTimeout(async () => {
    if (!hasSource()) return refreshEstimate();
    try {
      state.preview = await api.preview(currentSource());
      describePreview(state.preview);
    } catch (_) {
      state.preview = null;
    }
    refreshEstimate();
  }, 600);
});

// ---- settings ------------------------------------------------------------------------------
function buildToggles(modes) {
  const box = $("toggles");
  clear(box);
  state.modeKeys = Object.keys(modes);
  const saved = safe(() => JSON.parse(localStorage.getItem("trf-modes") || "{}")) || {};
  state.modeKeys.forEach((key) => {
    const row = el("label", "toggle");
    const cb = el("input");
    cb.type = "checkbox";
    cb.id = "mode-" + key;
    cb.checked = key in saved ? Boolean(saved[key]) : true;
    cb.addEventListener("change", () => safe(() => localStorage.setItem("trf-modes", JSON.stringify(readModes()))));
    const text = el("span");
    text.appendChild(el("div", "t-title", modes[key]));
    row.appendChild(cb);
    row.appendChild(text);
    box.appendChild(row);
  });
}

function readModes() {
  const out = {};
  state.modeKeys.forEach((k) => (out[k] = $("mode-" + k).checked));
  return out;
}

// ---- spend meter ---------------------------------------------------------------------------
async function refreshMeter() {
  try {
    const s = await api.spend();
    if (!s.cap) return;
    const pct = Math.min(100, Math.round((s.used / s.cap) * 100));
    $("meter-text").textContent = "Model budget used: " + pct + "%";
    $("meter-fill").style.width = pct + "%";
    $("meter-fill").classList.toggle("hot", pct >= 90);
    $("meter").hidden = false;
  } catch (_) {
    /* the meter is optional */
  }
}

// ---- running a job -------------------------------------------------------------------------
function stopPolling() {
  if (state.pollTimer !== null) clearTimeout(state.pollTimer);
  state.pollTimer = null;
}

function fail(message) {
  stopPolling();
  stopProgress();
  hideProgress();
  $("submit").disabled = false;
  showError(message || "Something went wrong.");
}

function poll(jobId) {
  const tick = async () => {
    try {
      const job = await api.job(jobId);
      update(job.stage, job.status);
      if (job.status === "done" && job.result) {
        stopPolling();
        stopProgress();
        update(null, "done");
        $("submit").disabled = false;
        setTimeout(hideProgress, 400);
        renderResult($("result"), jobId, job.title, job.result);
        $("result").scrollIntoView({ behavior: "smooth", block: "start" });
        refreshMeter();
      } else if (job.status === "failed") {
        fail(job.error || "The job failed.");
      } else {
        state.pollTimer = setTimeout(tick, POLL_MS);
      }
    } catch (err) {
      fail(err.message);
    }
  };
  tick();
}

$("fix-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  stopPolling();
  showError("");
  clear($("result"));
  $("result").hidden = true;
  if (!hasSource()) {
    showError(state.source === "url" ? "Enter a web address first." : "Add a tutorial first.");
    return;
  }
  const loops = parseInt($("max-loops").value, 10);
  if (Number.isNaN(loops) || loops < 0 || loops > 8) {
    showError("Retry rounds must be a whole number from 0 to 8.");
    return;
  }
  $("submit").disabled = true;
  startProgress(state.preview ? state.preview.title : "Starting");
  $("progress").scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    const job = await api.submit({ ...currentSource(), max_loops: loops, modes: readModes() });
    poll(job.id);
  } catch (err) {
    fail(err.message);
  }
});

// ---- history -------------------------------------------------------------------------------
async function openPast(row) {
  showError("");
  stopPolling();
  hideProgress();
  clear($("result"));
  try {
    const job = await api.job(row.id);
    if (job.status === "done" && job.result) renderResult($("result"), row.id, job.title, job.result);
    else if (job.status === "failed") showError(job.error || "That run failed.");
    else {
      $("submit").disabled = true;
      startProgress(job.title);
      poll(row.id);
    }
  } catch (err) {
    showError(err.message);
  }
}
$("history-btn").addEventListener("click", () => openDrawer(openPast));
$("drawer-close").addEventListener("click", closeDrawer);
$("scrim").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeDrawer();
});

// ---- start ---------------------------------------------------------------------------------
selectTab("url");
api
  .config()
  .then((cfg) => {
    setStages(cfg.stages);
    buildToggles(cfg.modes);
  })
  .catch(() => showError("Could not reach the service."));
refreshMeter();
