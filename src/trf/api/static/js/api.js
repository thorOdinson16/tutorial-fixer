// The only place that talks to the server.

function detailText(body) {
  const d = body && body.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    return d
      .map((x) => {
        const msg = String(x.msg || "").replace(/^Value error, /, "");
        return msg;
      })
      .join("\n");
  }
  return "";
}

async function request(path, options) {
  const resp = await fetch(path, options);
  let body = {};
  try {
    body = await resp.json();
  } catch (_) {
    /* not JSON */
  }
  if (!resp.ok) {
    throw new Error(detailText(body) || "The request failed (HTTP " + resp.status + ").");
  }
  return body;
}

function post(path, payload) {
  return request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export const api = {
  config: () => request("/api/config"),
  spend: () => request("/api/spend"),
  preview: (source) => post("/api/preview", source),
  submit: (payload) => post("/api/jobs", payload),
  job: (id) => request("/api/jobs/" + encodeURIComponent(id), { cache: "no-store" }),
  jobs: () => request("/api/jobs", { cache: "no-store" }),
  downloadUrl: (id) => "/api/jobs/" + encodeURIComponent(id) + "/download",
};
