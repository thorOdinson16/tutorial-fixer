// The stage list shown while a job runs. Stages the job skips (opt-in ones switched off) simply
// never light up; the bar follows the furthest stage reached.
import { $, clear, el } from "./dom.js";

let stages = [];
let timer = null;
let started = 0;

export function setStages(list) {
  stages = list;
}

export function startProgress(title) {
  const box = $("progress");
  $("progress-title").textContent = title || "Working";
  box.hidden = false;
  started = Date.now();
  stopTimer();
  const tick = () => {
    const s = Math.round((Date.now() - started) / 1000);
    $("elapsed").textContent = Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  };
  tick();
  timer = setInterval(tick, 1000);
  update(null, "queued");
}

function stopTimer() {
  if (timer !== null) clearInterval(timer);
  timer = null;
}

export function stopProgress() {
  stopTimer();
}

export function hideProgress() {
  stopTimer();
  $("progress").hidden = true;
}

export function update(stage, status) {
  const list = $("steps");
  clear(list);
  const at = stage ? stages.findIndex((s) => s[0] === stage) : -1;
  stages.forEach(([key, label], i) => {
    const li = el("li", "step" + (i < at || status === "done" ? " done" : i === at ? " now" : ""));
    li.appendChild(el("span", "dot"));
    li.appendChild(el("span", null, label));
    list.appendChild(li);
  });
  const pct = status === "done" ? 100 : at < 0 ? 3 : Math.round(((at + 0.5) / stages.length) * 100);
  $("bar-fill").style.width = pct + "%";
  if (status === "queued") $("progress-title").textContent = "Waiting for a free worker";
  else if (status === "running") $("progress-title").textContent = "Working";
}
