function withApiBase(baseUrl, path) {
  return new URL(path, baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`).toString();
}

function apiToken() {
  // Injected by the server into index.html (window.__WEBSCAN_API_TOKEN__).
  return (typeof window !== "undefined" && String(window.__WEBSCAN_API_TOKEN__ || "")) || "";
}

async function apiFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  const token = apiToken();
  if (token) headers.set("X-Auth-Token", token);
  const timeoutMs = Number(options.timeoutMs || 30000);
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, headers, signal: controller.signal });
  } catch (err) {
    if (err && err.name === "AbortError") {
      throw new ApiRequestError(`Request timed out after ${Math.round(timeoutMs / 1000)}s`, {
        code: "WGSC-CLIENT-TIMEOUT",
      });
    }
    throw err;
  } finally {
    window.clearTimeout(timer);
  }
}

export class ApiRequestError extends Error {
  constructor(message, payload = {}, status = 0, statusText = "") {
    super(message);
    this.name = "ApiRequestError";
    this.payload = payload || {};
    this.status = status;
    this.statusText = statusText;
    this.code = this.payload.code ?? "";
    this.step = this.payload.step ?? "";
    this.reason = this.payload.reason ?? "";
    this.details = this.payload.details ?? {};
    this.manualUrl = this.payload.manual_url ?? "";
    this.manualAnchor = this.payload.manual_anchor ?? "";
    this.nextAction = this.payload.next_action ?? "";
  }
}

async function parseJson(response) {
  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {
      error: `Failed to parse server response (${response.status} ${response.statusText}).`,
    };
  }
  if (!response.ok) {
    throw new ApiRequestError(
      data.error ?? data.message ?? `${response.status} ${response.statusText}`,
      data,
      response.status,
      response.statusText,
    );
  }
  return data;
}

export async function fetchHealth(apiBaseUrl) {
  const response = await apiFetch(withApiBase(apiBaseUrl, "api/health"));
  return parseJson(response);
}

export async function fetchAlgorithms(apiBaseUrl) {
  const response = await apiFetch(withApiBase(apiBaseUrl, "api/algorithms"));
  return parseJson(response);
}

export async function fetchEnvironmentCheck(apiBaseUrl, family = "") {
  const response = await apiFetch(withApiBase(apiBaseUrl, "api/environment-check"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ family }),
  });
  return parseJson(response);
}

export async function updateAdapter(apiBaseUrl, payload) {
  const response = await apiFetch(withApiBase(apiBaseUrl, "api/adapters/update"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  return parseJson(response);
}

export async function validateAdapter(apiBaseUrl, family) {
  const response = await apiFetch(withApiBase(apiBaseUrl, "api/adapters/validate"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ family }),
  });
  return parseJson(response);
}

export async function exportScenePackage(apiBaseUrl, payload) {
  const response = await apiFetch(withApiBase(apiBaseUrl, "api/export-scene"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
    timeoutMs: 300000,
  });
  return parseJson(response);
}

export async function submitAlgorithmJob(apiBaseUrl, payload) {
  const response = await apiFetch(withApiBase(apiBaseUrl, "api/run-algorithm"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  return parseJson(response);
}

export async function runRemoteAlgorithm(apiBaseUrl, payload) {
  const response = await apiFetch(withApiBase(apiBaseUrl, "api/run-remote-algorithm"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  return parseJson(response);
}

export async function previewRemoteAlgorithm(apiBaseUrl, payload) {
  const response = await apiFetch(withApiBase(apiBaseUrl, "api/run-remote-algorithm/preview"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  return parseJson(response);
}

export async function remoteCheck(apiBaseUrl, payload) {
  const response = await apiFetch(withApiBase(apiBaseUrl, "api/remote-check"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  return parseJson(response);
}

export async function cancelJob(apiBaseUrl, jobId, remote = {}) {
  const response = await apiFetch(withApiBase(apiBaseUrl, `api/jobs/${jobId}/cancel`), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ remote }),
  });
  return parseJson(response);
}

export async function reattachRemoteJob(apiBaseUrl, jobId, remote = {}) {
  const response = await apiFetch(withApiBase(apiBaseUrl, `api/jobs/${jobId}/reattach`), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ remote }),
  });
  return parseJson(response);
}

export async function checkJobResultDownload(apiBaseUrl, jobId, remote = {}) {
  const response = await apiFetch(withApiBase(apiBaseUrl, `api/jobs/${jobId}/results/check`), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ remote }),
  });
  return parseJson(response);
}

export async function downloadJobResult(apiBaseUrl, jobId, remote = {}) {
  const response = await apiFetch(withApiBase(apiBaseUrl, `api/jobs/${jobId}/results/download`), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ remote }),
    timeoutMs: 300000,
  });
  return parseJson(response);
}

export async function redownloadJobResult(apiBaseUrl, jobId, remote = {}) {
  const response = await apiFetch(withApiBase(apiBaseUrl, `api/jobs/${jobId}/redownload-result`), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ remote }),
    timeoutMs: 300000,
  });
  return parseJson(response);
}

export async function repairJobMetrics(apiBaseUrl, jobId, remote = {}, options = {}) {
  const response = await apiFetch(withApiBase(apiBaseUrl, `api/jobs/${jobId}/repair-metrics`), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ remote, force: Boolean(options.force) }),
  });
  return parseJson(response);
}

export async function resetFlow(apiBaseUrl, payload) {
  const response = await apiFetch(withApiBase(apiBaseUrl, "api/flow/reset"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  return parseJson(response);
}

export async function fetchFlowData(apiBaseUrl, options = {}) {
  const params = new URLSearchParams({
    session_id: options.sessionId || "",
    capture_id: options.captureId || "",
    family: options.family || "",
  });
  const response = await apiFetch(withApiBase(apiBaseUrl, `api/flow/data?${params.toString()}`));
  return parseJson(response);
}

export async function deleteFlowData(apiBaseUrl, payload) {
  const response = await apiFetch(withApiBase(apiBaseUrl, "api/flow/data/delete"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  return parseJson(response);
}

export async function clearJobs(apiBaseUrl, payload = {}) {
  const response = await apiFetch(withApiBase(apiBaseUrl, "api/jobs/clear"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  return parseJson(response);
}

export async function deleteJob(apiBaseUrl, jobId) {
  const response = await apiFetch(withApiBase(apiBaseUrl, `api/jobs/${jobId}/delete`), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({}),
  });
  return parseJson(response);
}

export async function fetchJobs(apiBaseUrl) {
  const response = await apiFetch(withApiBase(apiBaseUrl, "api/jobs"));
  return parseJson(response);
}

export async function fetchDiscoveredResults(apiBaseUrl, options = {}) {
  const limit = Number(options.limit || 30);
  const maxScanDirs = Number(options.maxScanDirs || 1800);
  const params = new URLSearchParams({
    limit: String(Number.isFinite(limit) && limit > 0 ? limit : 30),
    max_scan_dirs: String(Number.isFinite(maxScanDirs) && maxScanDirs > 0 ? maxScanDirs : 1800),
  });
  const response = await apiFetch(withApiBase(apiBaseUrl, `api/results/discover?${params.toString()}`));
  return parseJson(response);
}

export async function fetchJob(apiBaseUrl, jobId) {
  const response = await apiFetch(withApiBase(apiBaseUrl, `api/jobs/${jobId}`));
  return parseJson(response);
}

export async function fetchJobLogs(apiBaseUrl, jobId, options = {}) {
  const page = Number(options.page || 1);
  const pageSize = Number(options.pageSize || 20);
  const tailLines = Number(options.tailLines || 120);
  const params = new URLSearchParams({
    page: String(Number.isFinite(page) && page > 0 ? page : 1),
    page_size: String(Number.isFinite(pageSize) && pageSize > 0 ? pageSize : 20),
    tail_lines: String(Number.isFinite(tailLines) && tailLines > 0 ? tailLines : 120),
  });
  const response = await apiFetch(withApiBase(apiBaseUrl, `api/jobs/${jobId}/logs?${params.toString()}`));
  return parseJson(response);
}

export async function fetchJobLogDelta(apiBaseUrl, jobId, cursor = 0) {
  const params = new URLSearchParams({
    cursor: String(Math.max(0, Number(cursor) || 0)),
  });
  const response = await apiFetch(withApiBase(apiBaseUrl, `api/jobs/${jobId}/logs?${params.toString()}`));
  return parseJson(response);
}

export function buildJobLogDownloadUrl(apiBaseUrl, jobId) {
  const url = new URL(withApiBase(apiBaseUrl, `api/jobs/${jobId}/logs/download`));
  const token = apiToken();
  if (token) url.searchParams.set("access_token", token);
  return url.toString();
}

export function buildJobMetricsCsvUrl(apiBaseUrl, jobId) {
  const url = new URL(withApiBase(apiBaseUrl, `api/jobs/${jobId}/metrics.csv`));
  const token = apiToken();
  if (token) url.searchParams.set("access_token", token);
  return url.toString();
}

export async function exportAnalysisCsv(apiBaseUrl, jobIds = []) {
  const response = await apiFetch(withApiBase(apiBaseUrl, "api/jobs/analysis/export"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ job_ids: jobIds }), timeoutMs: 300000,
  });
  if (!response.ok) {
    let payload = {};
    try {
      payload = await response.json();
    } catch {
      payload = { error: `${response.status} ${response.statusText}` };
    }
    throw new ApiRequestError(
      payload.error ?? payload.message ?? `${response.status} ${response.statusText}`,
      payload,
      response.status,
      response.statusText,
    );
  }
  return response.blob();
}

export async function streamFrame(apiBaseUrl, payload) {
  const response = await apiFetch(withApiBase(apiBaseUrl, "api/stream-frame"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  return parseJson(response);
}

export async function materializeSession(apiBaseUrl, payload) {
  const response = await apiFetch(withApiBase(apiBaseUrl, "api/materialize-session"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  return parseJson(response);
}

export async function prepareColmapWorkspace(apiBaseUrl, payload) {
  const response = await apiFetch(withApiBase(apiBaseUrl, "api/prepare-colmap-workspace"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  return parseJson(response);
}

export async function runCapturePipeline(apiBaseUrl, payload) {
  const response = await apiFetch(withApiBase(apiBaseUrl, "api/run-capture-pipeline"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  return parseJson(response);
}

export async function loadPlyFile(apiBaseUrl, payload) {
  const response = await apiFetch(withApiBase(apiBaseUrl, "api/load-ply"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  return parseJson(response);
}

export async function loadResultPath(apiBaseUrl, payload) {
  const response = await apiFetch(withApiBase(apiBaseUrl, "api/load-result"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  return parseJson(response);
}
