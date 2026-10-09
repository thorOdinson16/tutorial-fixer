// Renders a finished job: summary tiles, per-block cards with filters, whole-tutorial diff, notes.
import { api } from "./api.js";
import { append, clear, el } from "./dom.js";
import { diffView } from "./diff.js";

const BROKEN = (b) => b.status === "failing" || b.status === "unresolved";

const BADGE = {
  passing: ["passing", "passing"],
  unresolved: ["unresolved", "could not fix"],
  failing: ["failing", "failing"],
  pending: ["pending", "not executed"],
};

function tile(num, label, kind) {
  const t = el("div", "tile " + (kind || ""));
  append(t, el("div", "num", num), el("div", "lbl", label));
  return t;
}

function changeItem(c) {
  const li = el("li");
  if (c.kind === "defect_fix") {
    li.appendChild(el("span", "tag cited", "defect fix"));
  } else if (c.cited) {
    li.appendChild(el("span", "tag cited", "cited: " + c.kind));
  } else {
    li.appendChild(el("span", "tag uncited", "uncited: model knowledge, no retrieval"));
  }
  li.appendChild(document.createTextNode(" "));
  if (c.symbol) li.appendChild(el("code", null, c.symbol));
  li.appendChild(document.createTextNode(" " + c.description));
  if (c.error) li.appendChild(el("div", "evidence", "removes the error: " + c.error));
  if (c.evidence_chunk_ids && c.evidence_chunk_ids.length) {
    li.appendChild(el("div", "evidence", "evidence: " + c.evidence_chunk_ids.join(", ")));
  }
  (c.source_urls || []).forEach((url) => {
    if (!/^https?:\/\//i.test(url)) return;
    const a = el("a", "evidence", url);
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    const wrap = el("div");
    wrap.appendChild(a);
    li.appendChild(wrap);
  });
  return li;
}

function blockCard(block, changes) {
  const card = el("details", "card block");
  card.dataset.status = block.status;
  card.dataset.changed = block.changed ? "1" : "0";
  if (BROKEN(block) && !block.downstream_of) card.open = true;
  const head = el("summary");
  head.appendChild(el("span", "block-title", "Block " + block.id));
  head.appendChild(el("span", "muted", block.lang || "-"));
  const badge = BADGE[block.status] || BADGE.pending;
  head.appendChild(el("span", "badge " + badge[0], badge[1]));
  if (block.changed) head.appendChild(el("span", "tag", "edited"));
  if (block.downstream_of) head.appendChild(el("span", "tag uncited", "follows " + block.downstream_of));
  if (block.output_matches === true) head.appendChild(el("span", "tag cited", "same output as before"));
  if (block.output_matches === false) head.appendChild(el("span", "badge failing", "output differs from the original"));
  card.appendChild(head);

  const body = el("div", "block-body");
  if (block.diagnosis) body.appendChild(el("p", "muted", "Likely cause: " + block.diagnosis + "."));
  if (block.last_error) body.appendChild(el("pre", "lasterr", block.last_error));
  if (block.changed) {
    body.appendChild(diffView(block.original_source, block.source));
  } else {
    body.appendChild(el("pre", "codeblock", block.source));
  }
  body.appendChild(el("h4", null, "Changes"));
  if (!changes.length) {
    body.appendChild(el("p", "muted", "No edits for this block."));
  } else {
    const list = el("ul", "changes");
    changes.forEach((c) => list.appendChild(changeItem(c)));
    body.appendChild(list);
  }
  card.appendChild(body);
  return card;
}

function blocksPane(result) {
  const pane = el("div");
  const byBlock = {};
  result.changes.forEach((c) => (byBlock[c.block_id] = byBlock[c.block_id] || []).push(c));
  const py = result.blocks;
  if (!py.length) {
    pane.appendChild(el("p", "empty", "No code blocks were found in this tutorial."));
    return pane;
  }
  const filters = el("div", "filters");
  const list = el("div");
  const defs = [
    ["all", "All", () => true],
    ["problem", "Needs attention", (b) => BROKEN(b) && !b.downstream_of],
    ["after", "After a failure", (b) => Boolean(b.downstream_of)],
    ["changed", "Edited", (b) => b.changed],
    ["same", "Untouched", (b) => !b.changed],
  ];
  let active = result.blocks.some((b) => BROKEN(b) && !b.downstream_of) ? "problem" : "all";
  const cards = py.map((b) => ({ b, node: blockCard(b, byBlock[b.id] || []) }));
  const buttons = [];
  const apply = () => {
    const test = defs.find((d) => d[0] === active)[2];
    cards.forEach(({ b, node }) => (node.hidden = !test(b)));
    buttons.forEach(([key, btn]) => btn.setAttribute("aria-pressed", String(key === active)));
  };
  defs.forEach(([key, label, test]) => {
    const n = py.filter(test).length;
    const btn = el("button", "pill", label + " (" + n + ")");
    btn.type = "button";
    btn.addEventListener("click", () => {
      active = key;
      apply();
    });
    buttons.push([key, btn]);
    filters.appendChild(btn);
  });
  cards.forEach(({ node }) => list.appendChild(node));
  append(pane, filters, list);
  apply();
  return pane;
}

function wholePane(result) {
  const pane = el("div");
  pane.appendChild(el("p", "muted", "The whole tutorial, original on the left and the fixed version on the right."));
  pane.style.marginTop = "12px";
  pane.appendChild(diffView(result.original_md, result.output_md));
  return pane;
}

function notesPane(result) {
  const pane = el("div");
  let any = false;
  if (result.prose_edits && result.prose_edits.length) {
    any = true;
    pane.appendChild(el("h3", "section-title", "Prose changes"));
    result.prose_edits.forEach((e) => {
      const box = el("div", "prose-edit");
      append(box, el("del", null, e.old), document.createTextNode(" "), el("ins", null, e.new));
      box.appendChild(el("div", "evidence", e.reason + " (linked to " + e.linked + ")"));
      pane.appendChild(box);
    });
  }
  const sug = result.suggestions || {};
  if (Object.keys(sug).length) {
    any = true;
    pane.appendChild(el("h3", "section-title", "Suggested fixes (not applied)"));
    Object.keys(sug).forEach((id) => {
      const box = el("div", "prose-edit");
      box.appendChild(document.createTextNode("Block " + id + ": "));
      box.appendChild(el("code", null, sug[id]));
      pane.appendChild(box);
    });
  }
  if (result.warnings && result.warnings.length) {
    any = true;
    pane.appendChild(el("h3", "section-title", "Warnings (" + result.warnings.length + ")"));
    const ul = el("ul", "changes");
    result.warnings.forEach((w) => ul.appendChild(el("li", null, w)));
    pane.appendChild(ul);
  }
  pane.appendChild(el("h3", "section-title", "Change log"));
  pane.appendChild(el("pre", "codeblock", result.change_log_md || "(empty)"));
  if (!any) pane.insertBefore(el("p", "muted", "No prose edits, suggestions or warnings."), pane.firstChild);
  return pane;
}

function tabs(panes) {
  const wrap = el("div");
  const bar = el("div", "subtabs");
  bar.setAttribute("role", "tablist");
  const body = el("div");
  const tabButtons = [];
  panes.forEach(([label, build], i) => {
    const btn = el("button", "tab", label);
    btn.type = "button";
    btn.setAttribute("role", "tab");
    let node = null;
    btn.addEventListener("click", () => {
      tabButtons.forEach((b) => b.setAttribute("aria-selected", String(b === btn)));
      clear(body);
      node = node || build();
      body.appendChild(node);
    });
    tabButtons.push(btn);
    bar.appendChild(btn);
    if (i === 0) queueMicrotask(() => btn.click());
  });
  append(wrap, bar, body);
  return wrap;
}

export function renderResult(container, jobId, title, result) {
  clear(container);
  const s = result.summary;
  const head = el("div", "result-head");
  head.appendChild(el("h2", null, title || "Result"));
  const dl = el("a", "btn primary", "Download updated .md");
  dl.href = api.downloadUrl(jobId);
  dl.setAttribute("download", "tutorial-fixed.md");
  const copy = el("button", "btn", "Copy Markdown");
  copy.type = "button";
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(result.output_md);
      copy.textContent = "Copied";
    } catch (_) {
      copy.textContent = "Copy failed";
    }
    setTimeout(() => (copy.textContent = "Copy Markdown"), 1800);
  });
  append(head, copy, dl);
  container.appendChild(head);

  const edited = result.blocks.filter((b) => b.changed && b.lang === "python").length;
  const tiles = el("div", "summary");
  tiles.style.marginTop = "14px";
  const allGood = s.total_python_blocks > 0 && s.passing === s.total_python_blocks;
  append(
    tiles,
    tile(s.passing + " / " + s.total_python_blocks, "code blocks that run", allGood ? "good" : ""),
    tile(
      String(s.unresolved - (s.downstream || 0)),
      s.downstream ? "could not be fixed (" + s.downstream + " more follow them)" : "could not be fixed",
      s.unresolved ? "bad" : "",
    ),
    tile(String(edited), "blocks edited"),
    tile(String(result.changes.length), "changes listed"),
  );
  if (s.output_compared) {
    tiles.appendChild(tile(s.output_same + " / " + s.output_compared, "same output as the original run"));
  }
  container.appendChild(tiles);

  const libs = Object.keys(result.migrated || {});
  if (libs.length) {
    const chips = el("div", "chips");
    chips.style.marginTop = "12px";
    libs.forEach((lib) => {
      const m = result.migrated[lib];
      chips.appendChild(el("span", "chip", lib + ": " + m.from + " → " + m.to));
    });
    container.appendChild(chips);
  }

  container.appendChild(
    tabs([
      ["Blocks", () => blocksPane(result)],
      ["Whole tutorial", () => wholePane(result)],
      ["Notes and change log", () => notesPane(result)],
    ]),
  );
  container.hidden = false;
  container.focus();
}
