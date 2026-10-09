// Line diff (LCS) with the changed part of each paired line highlighted.
import { el } from "./dom.js";

const MAX_CELLS = 1000000;

export function splitLines(text) {
  if (!text) return [];
  const parts = text.split("\n");
  if (parts.length && parts[parts.length - 1] === "") parts.pop();
  return parts;
}

function lcsOps(a, b) {
  const n = a.length;
  const m = b.length;
  const ops = [];
  if (n * m > MAX_CELLS) {
    a.forEach((x) => ops.push(["del", x]));
    b.forEach((x) => ops.push(["add", x]));
    return ops;
  }
  const dp = [];
  for (let i = 0; i <= n; i++) dp.push(new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      ops.push(["eq", a[i]]);
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      ops.push(["del", a[i++]]);
    } else {
      ops.push(["add", b[j++]]);
    }
  }
  while (i < n) ops.push(["del", a[i++]]);
  while (j < m) ops.push(["add", b[j++]]);
  return ops;
}

// Common prefix / suffix of two strings: what is left in the middle is what changed.
function middle(a, b) {
  let s = 0;
  while (s < a.length && s < b.length && a[s] === b[s]) s++;
  let e = 0;
  while (e < a.length - s && e < b.length - s && a[a.length - 1 - e] === b[b.length - 1 - e]) e++;
  return [s, e];
}

// Pair runs of deletions and insertions so the two columns stay aligned.
function toRows(ops) {
  const rows = [];
  let dels = [];
  let adds = [];
  const flush = () => {
    const k = Math.max(dels.length, adds.length);
    for (let q = 0; q < k; q++) {
      const left = q < dels.length ? dels[q] : null;
      const right = q < adds.length ? adds[q] : null;
      let span = null;
      if (left !== null && right !== null) span = middle(left, right);
      rows.push({
        left: left === null ? null : { t: left, c: "del", span },
        right: right === null ? null : { t: right, c: "add", span },
      });
    }
    dels = [];
    adds = [];
  };
  for (const [kind, text] of ops) {
    if (kind === "eq") {
      flush();
      rows.push({ left: { t: text, c: "", span: null }, right: { t: text, c: "", span: null } });
    } else if (kind === "del") {
      dels.push(text);
    } else {
      adds.push(text);
    }
  }
  flush();
  return rows;
}

function lineNode(cell) {
  const node = el("div", cell && cell.c ? "ln " + cell.c : "ln");
  if (!cell || cell.t === "") {
    node.textContent = " ";
    return node;
  }
  if (cell.span && cell.c) {
    const [s, e] = cell.span;
    const head = cell.t.slice(0, s);
    const mid = cell.t.slice(s, cell.t.length - e);
    const tail = cell.t.slice(cell.t.length - e);
    node.appendChild(document.createTextNode(head));
    if (mid) node.appendChild(el("mark", null, mid));
    node.appendChild(document.createTextNode(tail));
  } else {
    node.textContent = cell.t;
  }
  return node;
}

function column(title, cells) {
  const col = el("div", "col");
  col.appendChild(el("h4", null, title));
  if (!cells.length) {
    col.appendChild(el("div", "ln", "(empty)"));
    return col;
  }
  cells.forEach((cell) => col.appendChild(lineNode(cell)));
  return col;
}

export function diffView(original, updated, leftTitle = "Original", rightTitle = "Updated") {
  const rows = toRows(lcsOps(splitLines(original), splitLines(updated)));
  const wrap = el("div", "diff");
  wrap.appendChild(column(leftTitle, rows.map((r) => r.left)));
  wrap.appendChild(column(rightTitle, rows.map((r) => r.right)));
  return wrap;
}
