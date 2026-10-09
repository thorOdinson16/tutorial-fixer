// Tiny DOM helpers. Everything is built with createElement and textContent (never markup strings),
// because tutorial text, code and error messages come from strangers.

export function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

export function append(parent, ...children) {
  for (const c of children) if (c) parent.appendChild(c);
  return parent;
}

export function $(id) {
  return document.getElementById(id);
}

export function ago(epochSeconds) {
  const s = Math.max(0, Date.now() / 1000 - epochSeconds);
  if (s < 90) return "just now";
  if (s < 5400) return Math.round(s / 60) + " min ago";
  if (s < 129600) return Math.round(s / 3600) + " h ago";
  return Math.round(s / 86400) + " days ago";
}
