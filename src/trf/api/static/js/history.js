// The "Past runs" drawer.
import { api } from "./api.js";
import { $, ago, clear, el } from "./dom.js";

export function openDrawer(onPick) {
  $("drawer").classList.add("open");
  $("drawer").setAttribute("aria-hidden", "false");
  $("scrim").hidden = false;
  $("history-btn").setAttribute("aria-expanded", "true");
  refresh(onPick);
}

export function closeDrawer() {
  $("drawer").classList.remove("open");
  $("drawer").setAttribute("aria-hidden", "true");
  $("scrim").hidden = true;
  $("history-btn").setAttribute("aria-expanded", "false");
}

async function refresh(onPick) {
  const list = $("history-list");
  clear(list);
  let rows = [];
  try {
    rows = await api.jobs();
  } catch (err) {
    list.appendChild(el("p", "empty", err.message));
    return;
  }
  if (!rows.length) {
    list.appendChild(el("p", "empty", "Nothing yet. Finished runs show up here."));
    return;
  }
  rows.forEach((r) => {
    const item = el("button", "hist-item");
    item.type = "button";
    item.appendChild(el("span", "h-title", r.title || "Untitled"));
    const done = r.status === "done" && r.total !== null;
    const meta = done ? r.passing + " of " + r.total + " blocks run" : r.status;
    item.appendChild(el("span", "h-meta", meta + " · " + ago(r.created)));
    item.addEventListener("click", () => {
      closeDrawer();
      onPick(r);
    });
    list.appendChild(item);
  });
}
