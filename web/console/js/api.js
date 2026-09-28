// API client for the WebScan console.
// Token comes from index.html (injected by the server); every request
// carries it as a header, with a 30s default timeout (5min for transfers).

const BASE = "";

function apiToken() {
  return String(window.__WEBSCAN_API_TOKEN__ || "");
}

export class ApiError extends Error {
  constructor(message, { status = 0, code = "", step = "", details = {}, nextAction = "" } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.step = step;
    this.details = details;
    this.nextAction = nextAction;
  }
}

async function request(path, { method = "GET", body, timeoutMs = 30000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const headers = {};
  const token = apiToken();
  if (token) headers["X-Auth-Token"] = token;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  try {
    const response = await fetch(BASE + path, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
    let data = {};
    try { data = await response.json(); } catch { /* non-json */ }
    if (!response.ok) {
      throw new ApiError(data.error ?? data.message ?? `${response.status} ${response.statusText}`, {
        status: response.status,
        code: data.code || "",
        step: data.step || "",
        details: data.details || {},
        nextAction: data.next_action || "",
      });
    }
    return data;
  } catch (err) {
    if (err.name === "AbortError") {
      throw new ApiError(`Request timed out after ${Math.round(timeoutMs / 1000)}s`, { code: "TIMEOUT" });
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

function withTokenQuery(url) {
  const token = apiToken();
  if (!token) return url;
  return url + (url.includes("?") ? "&" : "?") + "access_token=" + encodeURIComponent(token);
}

/* ---------- health & algorithms ---------- */

export const getHealth = () => request("/api/health", { timeoutMs: 8000 });
export const getAlgorithms = () => request("/api/algorithms");
export const environmentCheck = (family) =>
  request("/api/environment-check", { method: "POST", body: { family } });

/* ---------- jobs ---------- */

export const getJobs = () => request("/api/jobs");
export const getJob = (id) => request(`/api/jobs/${id}`);
export const getJobLogDelta = (id, cursor) =>
  request(`/api/jobs/${id}/logs?cursor=${Math.max(0, Number(cursor) || 0)}`);
export const getJobLogsPage = (id, { page = 1, pageSize = 30, tailLines = 300 } = {}) =>
  request(`/api/jobs/${id}/logs?page=${page}&page_size=${pageSize}&tail_lines=${tailLines}`);

export function jobLogDownloadUrl(id) {
  return withTokenQuery(`/api/jobs/${id}/logs/download`);
}
export function jobMetricsCsvUrl(id) {
  return withTokenQuery(`/api/jobs/${id}/metrics.csv`);
}

export const cancelJob = (id, remote) =>
  request(`/api/jobs/${id}/cancel`, { method: "POST", body: { remote: remote || {} }, timeoutMs: 60000 });
export const deleteJob = (id) =>
  request(`/api/jobs/${id}/delete`, { method: "POST", body: {} });
export const clearJobs = (statuses) =>
  request("/api/jobs/clear", { method: "POST", body: { statuses: statuses || [] } });

export const checkResult = (id, remote) =>
  request(`/api/jobs/${id}/results/check`, { method: "POST", body: { remote: remote || {} }, timeoutMs: 120000 });
export const downloadResult = (id, remote) =>
  request(`/api/jobs/${id}/results/download`, { method: "POST", body: { remote: remote || {} }, timeoutMs: 300000 });
export const redownloadResult = (id, remote) =>
  request(`/api/jobs/${id}/redownload-result`, { method: "POST", body: { remote: remote || {} }, timeoutMs: 300000 });
export const repairMetrics = (id, remote, force = false) =>
  request(`/api/jobs/${id}/repair-metrics`, { method: "POST", body: { remote: remote || {}, force }, timeoutMs: 300000 });

/* ---------- remote training flow ---------- */

export const remoteCheck = (payload) =>
  request("/api/remote-check", { method: "POST", body: payload, timeoutMs: 60000 });
export const previewRun = (payload) =>
  request("/api/run-remote-algorithm/preview", { method: "POST", body: payload, timeoutMs: 120000 });
export const submitRun = (payload) =>
  request("/api/run-remote-algorithm", { method: "POST", body: payload, timeoutMs: 300000 });
export const reattachJob = (id, remote) =>
  request(`/api/jobs/${id}/reattach`, { method: "POST", body: { remote: remote || {} }, timeoutMs: 60000 });

/* ---------- data upload ---------- */

export const streamFrame = (payload) =>
  request("/api/stream-frame", { method: "POST", body: payload, timeoutMs: 60000 });
export const materializeSession = (payload) =>
  request("/api/materialize-session", { method: "POST", body: payload, timeoutMs: 120000 });
export const prepareColmap = (payload) =>
  request("/api/prepare-colmap-workspace", { method: "POST", body: payload, timeoutMs: 600000 });
export const getFlowData = (sessionId, captureId = "", family = "") =>
  request(`/api/flow/data?session_id=${encodeURIComponent(sessionId)}&capture_id=${encodeURIComponent(captureId)}&family=${encodeURIComponent(family)}`);
export const resetFlow = (payload) =>
  request("/api/flow/reset", { method: "POST", body: payload, timeoutMs: 120000 });

/* ---------- results & viewer ---------- */

export const loadPly = (plyPath, representation = "") =>
  request("/api/load-ply", { method: "POST", body: { ply_path: plyPath, representation }, timeoutMs: 120000 });
export const loadResult = (path, family = "", representation = "") =>
  request("/api/load-result", { method: "POST", body: { path, family, representation }, timeoutMs: 120000 });
export const discoverResults = (limit = 40, maxScanDirs = 1800) =>
  request(`/api/results/discover?limit=${limit}&max_scan_dirs=${maxScanDirs}`, { timeoutMs: 120000 });
