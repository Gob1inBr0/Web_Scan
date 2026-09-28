import {
  buildJobLogDownloadUrl,
  buildJobMetricsCsvUrl,
  cancelJob,
  checkJobResultDownload,
  clearJobs,
  deleteFlowData,
  deleteJob,
  downloadJobResult,
  exportAnalysisCsv,
  fetchAlgorithms,
  fetchDiscoveredResults,
  fetchFlowData,
  fetchEnvironmentCheck,
  fetchHealth,
  fetchJob,
  fetchJobLogDelta,
  fetchJobLogs,
  fetchJobs,
  loadResultPath,
  materializeSession,
  prepareColmapWorkspace,
  previewRemoteAlgorithm,
  redownloadJobResult,
  reattachRemoteJob,
  repairJobMetrics,
  remoteCheck,
  resetFlow,
  runRemoteAlgorithm,
  streamFrame,
  validateAdapter,
} from "../api/server-client.js?v=20260428-detached";

const root = document.getElementById("app-root");

const STATE_KEY = "gaussvision-workbench-state-v1";
const REMOTE_KEY = "gaussvision-remote-config-v1";
const MAX_LOG_CHARS = 180_000;
const MAX_REATTACH_ATTEMPTS = 5;
const IMAGE_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"]);
const MODEL_EXTENSIONS = new Set([".ply"]);
const RENDER_FORMAT_LABELS = {
  "gaussian-sh": "Gaussian SH PLY",
  "gaussian-sg": "Spherical Gaussian PLY",
  "anchor-gaussian": "Anchor Gaussian PLY",
  "rgb-point-cloud": "RGB Point Cloud",
  "unknown-ply": "PLY 3D Model",
};
const ANALYSIS_METRICS = [
  { key: "psnr", field: "psnr", label: "PSNR(dB)" },
  { key: "ssim", field: "ssim", label: "SSIM" },
  { key: "lpips", field: "lpips", label: "LPIPS" },
  { key: "sizeMb", field: "size_mb", label: "Size(MB)", staticSummary: true },
];

const NAV_ITEMS = [
  { id: "overview", label: "Overview" },
  { id: "data", label: "Data" },
  { id: "algorithm", label: "Algorithms" },
  { id: "browser", label: "Browser" },
  { id: "analysis", label: "Analysis" },
];

const PAGE_ALIASES = {
  train: "algorithm",
  monitor: "algorithm",
  debug: "data",
  realtime: "data",
  viewer: "browser",
  "Off Line": "data",
  "On Line": "data",
  "Remote Data": "data",
  "Local Upload": "data",
};

const DEFAULT_REMOTE = {
  host: "",
  port: 22,
  username: "",
  password: "",
  repo_path: "",
  workspace_root: "/tmp/web_scan/workspaces",
  output_root: "/tmp/web_scan/outputs",
  python: "python3",
  activate_cmd: "",
};

const DEFAULT_TRAINING = {
  iterations: 30000,
  voxel_size: 0.001,
  update_init_factor: 16,
  lmbda: 0.001,
  mask_lr_final: 0.0001,
  position_lr_init: 0,
  position_lr_final: 0,
  position_lr_delay_mult: 0.01,
  position_lr_max_steps: 30000,
  offset_lr_init: 0.01,
  offset_lr_final: 0.0001,
  offset_lr_delay_mult: 0.01,
  offset_lr_max_steps: 30000,
  mask_lr_init: 0.01,
  mask_lr_delay_mult: 0.01,
  mask_lr_max_steps: 30000,
  feature_lr: 0.0075,
  opacity_lr: 0.02,
  scaling_lr: 0.007,
  rotation_lr: 0.002,
};

let algorithms = [];
let validation = new Map();
let jobs = [];
let discoveredResults = [];
let environmentReport = null;
let cameraStream = null;
let streamTimer = null;
let busy = false;
let toastTimer = null;
let pendingActionKey = "";
let pressedActionKey = "";
let pressTimer = null;
const reattachInFlight = new Set();
const reattachFailures = new Map();

const state = loadState();

function defaultState() {
  return {
    activePage: initialPage(),
    apiBaseUrl: window.location.origin,
    apiOnline: null,
    sessionId: "session-demo",
    captureId: "",
    datasetName: "",
    uploadedCount: 0,
    useExistingRemoteDataset: false,
    selectedRemoteDatasetId: "",
    remoteDatasetPath: "",
    autoColmap: true,
    algorithmFamily: "",
    operation: "train",
    outputDir: "",
    checkpointPath: "",
    renderMode: "ply",
    selectedJobId: "",
    jobsDatasetFilter: "",
    jobsAlgorithmFilter: "",
    jobsCompletedOnly: false,
    jobsSortOrder: "created_desc",
    quickPlyPath: "",
    loadedPly: null,
    flowData: null,
    remoteConfig: { ...DEFAULT_REMOTE },
    training: { ...DEFAULT_TRAINING },
    sshChecked: false,
    remoteReport: null,
    lastPreview: null,
    commandReviewEditing: false,
    commandDraft: "",
    lastError: null,
    logCursor: 0,
    logText: "",
    metricsRows: [],
    selectedFiles: [],
  };
}

function initialPage() {
  const params = new URLSearchParams(window.location.search);
  const raw = params.get("page") || "overview";
  return PAGE_ALIASES[raw] || raw;
}

function canonicalizePageUrl() {
  const params = new URLSearchParams(window.location.search);
  const raw = params.get("page") || "overview";
  const resolved = PAGE_ALIASES[raw] || raw;
  if (raw === resolved) return;
  const url = new URL(window.location.href);
  url.searchParams.set("page", resolved);
  window.history.replaceState({}, "", url);
}

function storedRemotePassword() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(REMOTE_KEY) || "{}");
    return String(saved.password || "");
  } catch {
    return "";
  }
}

function migrateRemotePasswordToSession() {
  // v0.2: SSH passwords must never persist in localStorage (survives browser restarts).
  // Move any legacy copy into sessionStorage (cleared when the browser closes).
  for (const storage of [localStorage, sessionStorage]) {
    try {
      const saved = JSON.parse(storage.getItem(REMOTE_KEY) || "{}");
      if (typeof saved.password === "string" && saved.password) {
        sessionStorage.setItem(REMOTE_KEY, JSON.stringify({ ...saved, password: saved.password }));
      }
      if ("password" in saved) {
        delete saved.password;
        storage.setItem(REMOTE_KEY, JSON.stringify(saved));
      }
    } catch {
      /* ignore malformed storage */
    }
  }
}

function loadState() {
  const base = defaultState();
  try {
    migrateRemotePasswordToSession();
    const stored = JSON.parse(localStorage.getItem(STATE_KEY) || "{}");
    const remoteStored = JSON.parse(localStorage.getItem(REMOTE_KEY) || "{}");
    if (stored.remoteConfig && typeof stored.remoteConfig === "object") {
      delete stored.remoteConfig.password;
    }
    return {
      ...base,
      ...stored,
      activePage: initialPage(),
      remoteConfig: {
        ...DEFAULT_REMOTE,
        ...stored.remoteConfig,
        ...remoteStored,
        password: storedRemotePassword(),
      },
      training: {
        ...DEFAULT_TRAINING,
        ...stored.training,
      },
      logText: "",
      logCursor: 0,
      lastError: null,
      selectedFiles: [],
      commandReviewEditing: false,
      commandDraft: "",
    };
  } catch {
    return base;
  }
}

function persistState() {
  const payload = {
    ...state,
    logText: "",
    logCursor: 0,
    lastError: null,
    selectedFiles: [],
    remoteReport: state.remoteReport,
    lastPreview: state.lastPreview,
    // The password lives in sessionStorage only; never write it into localStorage.
    remoteConfig: { ...state.remoteConfig, password: "" },
  };
  localStorage.setItem(STATE_KEY, JSON.stringify(payload));
}

function setPage(page, options = {}) {
  const resolvedPage = PAGE_ALIASES[page] || page;
  if (resolvedPage !== state.activePage && state.activePage === "data") {
    // Leaving the data page must stop the camera and the auto stream upload timer.
    stopCamera({ silent: true });
  }
  state.activePage = resolvedPage;
  persistState();
  if (!options.silent) {
    const url = new URL(window.location.href);
    url.searchParams.set("page", resolvedPage);
    if (resolvedPage !== "result") {
      url.searchParams.delete("job");
    }
    window.history.pushState({}, "", url);
  }
  render();
  afterRender();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function summarize(value, length = 64) {
  const text = String(value ?? "").trim();
  if (text.length <= length) return text || "-";
  return `${text.slice(0, length - 1)}…`;
}

function formatTimestamp(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return "-";
  return new Date(number * 1000).toLocaleString();
}

function fileExtension(value) {
  const text = String(value ?? "").split("?")[0].split("#")[0].trim().toLowerCase();
  const name = text.slice(text.lastIndexOf("/") + 1);
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot) : "";
}

function classifyAssetUrl(url) {
  const ext = fileExtension(url);
  if (MODEL_EXTENSIONS.has(ext)) return "ply";
  if (IMAGE_EXTENSIONS.has(ext)) return "image";
  return "";
}

function sameResolvedUrl(left, right) {
  const leftText = String(left || "").trim();
  const rightText = String(right || "").trim();
  if (!leftText || !rightText) return false;
  try {
    return new URL(leftText, window.location.href).href === new URL(rightText, window.location.href).href;
  } catch {
    return leftText === rightText;
  }
}

function getRenderablePlyAsset(asset = {}) {
  const sourceUrl = asset.point_cloud_url || asset.ply_url || asset.ply_path || asset.manifest_url || "";
  const isModelAsset = classifyAssetUrl(sourceUrl) === "ply"
    || (asset.manifest_url && asset.viewer_url)
    || asset.type === "ply"
    || asset.type === "manifest";
  if (!isModelAsset) return null;
  const renderFormat = asset.render_format || (asset.type === "manifest" ? "manifest" : "unknown-ply");
  const renderLabel = asset.render_label || RENDER_FORMAT_LABELS[renderFormat] || RENDER_FORMAT_LABELS["unknown-ply"];
  return {
    type: asset.type === "manifest" ? "manifest" : "ply",
    renderFormat,
    renderLabel,
    sourceUrl,
    viewerUrl: asset.viewer_url || "",
  };
}

function uniqueRenderableImageUrls(asset = {}) {
  const urls = [];
  const add = (value) => {
    const url = String(value || "").trim();
    if (url && classifyAssetUrl(url) === "image" && !urls.includes(url)) {
      urls.push(url);
    }
  };
  add(asset.result_url);
  add(asset.output_url);
  add(asset.image_url);
  if (Array.isArray(asset.result_urls)) {
    asset.result_urls.forEach(add);
  }
  if (Array.isArray(asset.render_images)) {
    asset.render_images.forEach((item) => add(item?.url || item?.result_url || item?.image_url));
  }
  return urls;
}

function getRenderableImageAsset(asset = {}) {
  const imageUrls = uniqueRenderableImageUrls(asset);
  const imageUrl = imageUrls[0] || "";
  if (!imageUrl) return null;
  return {
    type: "image",
    imageUrl,
    imageUrls,
    sourceUrl: imageUrl,
  };
}

function classifyResultAsset(asset = {}) {
  return getRenderablePlyAsset(asset) || getRenderableImageAsset(asset) || {
    type: "",
    sourceUrl: asset.point_cloud_url || asset.result_url || asset.viewer_url || asset.output_url || "",
  };
}

function activeResultViewerIsStable() {
  if (state.activePage !== "result") return false;
  const params = new URLSearchParams(window.location.search);
  const jobId = params.get("job") || state.selectedJobId;
  const job = getJob(jobId);
  const plyAsset = job ? getRenderablePlyAsset(job) : null;
  if (!plyAsset?.viewerUrl) return false;
  if (state.renderMode === "image") return false;
  const frame = document.querySelector(".result-surface iframe.viewer-frame");
  return Boolean(frame && sameResolvedUrl(frame.getAttribute("src"), plyAsset.viewerUrl));
}

function shouldPausePollingForResultViewer() {
  if (state.activePage !== "result") return false;
  if (state.renderMode === "image") return false;
  return Boolean(document.querySelector(".result-surface iframe.viewer-frame"));
}

function resultTypeLabel(asset = {}) {
  const plyAsset = getRenderablePlyAsset(asset);
  if (plyAsset?.type === "manifest") return "Scene Manifest";
  if (plyAsset) return plyAsset.renderLabel || "PLY 3D Model";
  if (getRenderableImageAsset(asset)) return "Image";
  return "Unknown";
}

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (!Number.isFinite(value) || value <= 0) return "0 B";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function metricPathId(path) {
  return String(path || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
}

function metricHasToken(path, token) {
  return new RegExp(`(^|[^a-z0-9])${token}([^a-z0-9]|$)`, "i").test(String(path || ""));
}

function flattenMetricEntries(source, prefix = "", entries = [], depth = 0) {
  if (source == null || depth > 6) return entries;
  if (Array.isArray(source)) {
    source.forEach((item) => flattenMetricEntries(item, prefix, entries, depth + 1));
    return entries;
  }
  if (typeof source === "object") {
    Object.entries(source).forEach(([key, value]) => {
      const path = prefix ? `${prefix}.${key}` : key;
      flattenMetricEntries(value, path, entries, depth + 1);
    });
    return entries;
  }
  entries.push({ path: prefix, value: source });
  return entries;
}

function parseMetricNumber(value) {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  const text = String(value ?? "").replace(/,/g, "").trim();
  if (!text) return null;
  const match = text.match(/[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?/i);
  if (!match) return null;
  const number = Number(match[0]);
  return Number.isFinite(number) ? number : null;
}

function parseSizeMb(value, path = "") {
  const text = String(value ?? "").replace(/,/g, "").trim();
  const unitMatch = text.match(/([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?)\s*(b|bytes?|kb|kib|mb|mib|gb|gib)\b/i);
  if (unitMatch) {
    const number = Number(unitMatch[1]);
    const unit = unitMatch[2].toLowerCase();
    if (!Number.isFinite(number)) return null;
    if (unit === "b" || unit.startsWith("byte")) return number / 1024 / 1024;
    if (unit === "kb" || unit === "kib") return number / 1024;
    if (unit === "gb" || unit === "gib") return number * 1024;
    return number;
  }

  const number = parseMetricNumber(value);
  if (number == null) return null;
  const id = metricPathId(path);
  if (id.includes("bytes") || id.endsWith("byte") || id.includes("filesize")) return number / 1024 / 1024;
  if (id.includes("gb") || id.includes("gib")) return number * 1024;
  if (id.includes("kb") || id.includes("kib")) return number / 1024;
  if (number > 1024 * 1024 && (id.includes("size") || id.includes("file") || id.includes("byte"))) {
    return number / 1024 / 1024;
  }
  return number;
}

function analysisMetricScore(path, key) {
  const id = metricPathId(path);
  if (!id) return 0;
  if (key === "psnr") return metricHasToken(path, "psnr") || id.includes("psnrdb") ? 100 : 0;
  if (key === "ssim") {
    if (metricHasToken(path, "ssim")) return 100;
    if (id.endsWith("ssim") && !id.includes("msssim")) return 70;
    return 0;
  }
  if (key === "lpips") return metricHasToken(path, "lpips") ? 100 : 0;
  if (key === "sizeMb") {
    const isComponentSize = /(feat|feature|offset|opacity|scaling|rotation|mask|anchor)/.test(id);
    if (["ttlsizemb", "totalsizemb", "totalmodelsize", "totalmodelsizeinmb"].includes(id)) return 140;
    if ((id.includes("totalsize") || id.includes("ttlsize")) && id.includes("mb") && !isComponentSize) return 135;
    if (["sizemb", "sizeinmb", "modelmb", "modelsizeinmb"].includes(id)) return 120;
    if (id.includes("sizemb") || id.includes("modelsize") || id.includes("compressedsize")) return 110;
    if (id.includes("bitstream") && id.includes("size")) return 105;
    if ((id.includes("filesize") || id.includes("storagesize") || id.includes("disksize")) && !id.includes("image")) return 95;
    if (isComponentSize && id.includes("size")) return 50;
    if (id.includes("size") && /(mb|mib|byte|bytes|gb|gib|kb|kib|ply|checkpoint|ckpt|model|storage|disk|result|pointcloud)/.test(id)) return 75;
    return 0;
  }
  return 0;
}

function extractAnalysisMetric(source, key) {
  const entries = flattenMetricEntries(source).filter((entry) => {
    const text = String(entry.value ?? "").trim();
    return text && text !== "-";
  });
  let bestScore = 0;
  let bestEntries = [];
  entries.forEach((entry) => {
    const score = analysisMetricScore(entry.path, key);
    if (!score) return;
    if (score > bestScore) {
      bestScore = score;
      bestEntries = [entry];
    } else if (score === bestScore) {
      bestEntries.push(entry);
    }
  });
  if (!bestEntries.length) return { value: "", raw: "", source: "" };

  const numbers = bestEntries
    .map((entry) => key === "sizeMb" ? parseSizeMb(entry.value, entry.path) : parseMetricNumber(entry.value))
    .filter((value) => Number.isFinite(value));
  if (numbers.length) {
    return {
      value: numbers.reduce((total, value) => total + value, 0) / numbers.length,
      raw: bestEntries.map((entry) => entry.value).join(", "),
      source: bestEntries[0].path,
    };
  }
  return { value: bestEntries[0].value, raw: bestEntries[0].value, source: bestEntries[0].path };
}

function formatAnalysisNumber(value, key) {
  if (value == null || value === "") return "-";
  const number = Number(value);
  if (!Number.isFinite(number)) return summarize(value, 32);
  const digits = key === "ssim" || key === "lpips" ? 4 : number >= 100 ? 1 : 2;
  return number.toFixed(digits).replace(/\.?0+$/, "");
}

function latestMetricsRow(rows = []) {
  if (!rows.length) return {};
  const firstTime = Date.parse(rows[0]?.timestamp || "");
  const lastTime = Date.parse(rows[rows.length - 1]?.timestamp || "");
  const newestFirst = Number.isFinite(firstTime) && Number.isFinite(lastTime) && firstTime > lastTime;
  const ordered = newestFirst ? rows : [...rows].reverse();
  return ordered.find((row) => ANALYSIS_METRICS.some((metric) => extractAnalysisMetric(row, metric.key).value !== "")) || {};
}

function analysisMetricSource(job, rows = []) {
  const metrics = job?.metrics || {};
  return {
    ...job,
    ...metrics,
    analysis_metric_sources: job?.analysis_metric_sources || metrics.analysis_metric_sources || {},
    psnr: metrics.psnr_db ?? metrics.psnr,
    psnr_db: metrics.psnr_db ?? metrics.psnr,
    ssim: metrics.ssim,
    lpips: metrics.lpips,
    sizeMb: metrics.size_mb ?? job?.size_mb,
  };
}

function analysisMetricItems(job, rows = []) {
  const source = analysisMetricSource(job || {}, rows);
  return ANALYSIS_METRICS.map((metric) => {
    const sourceMap = source.analysis_metric_sources || {};
    const mappedSource = sourceMap[metric.field] || sourceMap[metric.key] || "";
    let rawValue = "";
    if (metric.key === "psnr") {
      rawValue = mappedSource === "artifact:result.json" ? (source.psnr_db ?? source.psnr ?? "") : "";
    } else if (metric.key === "ssim") {
      rawValue = mappedSource === "artifact:result.json" ? (source.ssim ?? "") : "";
    } else if (metric.key === "lpips") {
      rawValue = mappedSource === "artifact:result.json" ? (source.lpips ?? "") : "";
    } else if (metric.key === "sizeMb") {
      rawValue = String(mappedSource || "").startsWith("artifact:") ? (source.size_mb ?? source.sizeMb ?? "") : "";
    }
    if (rawValue === "" && !mappedSource) {
      const inferred = extractAnalysisMetric(source, metric.key);
      rawValue = inferred.raw || inferred.value || "";
    }
    return {
      ...metric,
      value: formatAnalysisNumber(rawValue, metric.key),
      raw: rawValue,
      numeric: Number(rawValue),
      source: mappedSource || extractAnalysisMetric(source, metric.key).source,
    };
  });
}

function hasAnalysisValue(item) {
  return item.value !== "-";
}

function analysisSummaryItems(job, rows = []) {
  return analysisMetricItems(job, rows);
}

function downloadInfo(job = {}) {
  return job.remote_result?.download || {};
}

function isRemoteDownloadPending(job = {}) {
  return Boolean(job.remote_detached && downloadInfo(job).pending);
}

function isRemoteDownloadFailed(job = {}) {
  return String(downloadInfo(job).phase || "") === "error";
}

function canDownloadCompletedResult(job = {}) {
  return ["completed", "partial_success", "training_success_render_failed", "training_success_metrics_failed", "training_success_postprocess_failed"]
    .includes(String(job.status || "").toLowerCase()) && Boolean(job.remote_detached);
}

function normalizeRemotePath(value = "") {
  const raw = String(value || "").trim().replaceAll("\\", "/");
  if (!raw) return "";
  const normalized = raw.replace(/\/+/g, "/").replace(/\/+$/, "");
  return normalized || "/";
}

function remoteIdentity(config = {}) {
  const port = Number(config.port || 22);
  return {
    host: String(config.host || "").trim(),
    port: Number.isFinite(port) ? port : 0,
    username: String(config.username || "").trim(),
    output_root: normalizeRemotePath(config.output_root || ""),
  };
}

function remoteIdentityMissingFields(identity = {}) {
  return ["host", "port", "username", "output_root"].filter((field) => {
    if (field === "port") return !Number(identity.port || 0);
    return !String(identity[field] || "").trim();
  });
}

function formatRemoteIdentity(identity = {}) {
  return `${identity.username || "-"}@${identity.host || "-"}:${identity.port || "-"} · ${identity.output_root || "-"}`;
}

function resultDownloadServerMatch(job = {}) {
  const expected = remoteIdentity(job.remote || {});
  const current = remoteIdentity(state.remoteConfig || {});
  const missingExpected = remoteIdentityMissingFields(expected);
  if (missingExpected.length) {
    return {
      ok: false,
      message: "Cannot verify this job's original remote server.",
      expected,
      current,
      fields: missingExpected,
    };
  }
  const mismatchFields = ["host", "port", "username", "output_root"].filter((field) => expected[field] !== current[field]);
  if (mismatchFields.length) {
    return {
      ok: false,
      message: "Remote config does not match this job's server.",
      expected,
      current,
      fields: mismatchFields,
    };
  }
  return { ok: true, expected, current, fields: [] };
}

function renderRemoteServerMatchWarning(job = {}, options = {}) {
  if (!canDownloadCompletedResult(job)) return "";
  const match = resultDownloadServerMatch(job);
  const ready = remoteConfigReadyForReattach();
  if (match.ok && ready) return "";
  const message = match.ok ? "Remote credentials are incomplete." : match.message;
  const fields = match.fields?.length ? ` · ${match.fields.join(", ")}` : "";
  const details = options.details === false ? "" : `
    <p class="panel-copy">Expected: ${escapeHtml(formatRemoteIdentity(match.expected))}</p>
    <p class="panel-copy">Current: ${escapeHtml(formatRemoteIdentity(match.current))}</p>
  `;
  return `
    <div class="status-dot bad">${escapeHtml(message)}${escapeHtml(fields)}</div>
    ${details}
  `;
}

function formatDownloadProgress(info = {}) {
  const bytes = Number(info.bytes || 0);
  const totalBytes = Number(info.total_bytes || 0);
  const files = Number(info.files || 0);
  const totalFiles = Number(info.total_files || 0);
  if (info.pending && bytes === 0 && totalBytes === 0 && files === 0 && totalFiles === 0) {
    return "Waiting for local download to start";
  }
  const byteText = totalBytes > 0 ? `${formatBytes(bytes)} / ${formatBytes(totalBytes)}` : formatBytes(bytes);
  const fileText = totalFiles > 0 ? `${files} / ${totalFiles} files` : `${files} files`;
  const percent = totalBytes > 0 ? ` · ${Math.min(100, Math.max(0, (bytes / totalBytes) * 100)).toFixed(1)}%` : "";
  return `${byteText} · ${fileText}${percent}`;
}

function downloadProgressPercent(info = {}) {
  const bytes = Number(info.bytes || 0);
  const totalBytes = Number(info.total_bytes || 0);
  if (!Number.isFinite(bytes) || !Number.isFinite(totalBytes) || totalBytes <= 0) {
    return info.complete ? 100 : 0;
  }
  return Math.min(100, Math.max(0, (bytes / totalBytes) * 100));
}

function renderRemoteDownloadStatus(job = {}, options = {}) {
  if (!job.remote_detached) return "";
  const info = downloadInfo(job);
  if (!info || info.pending === undefined) return "";
  const pending = Boolean(info.pending);
  const complete = Boolean(info.complete);
  const failed = String(info.phase || "") === "error";
  const currentFile = String(info.current_file || "").trim();
  const errorText = String(info.error || "").trim();
  const label = failed
    ? "Result download failed"
    : pending
      ? "Result files downloading"
      : complete
        ? "Result files complete"
        : "Result files incomplete";
  const detail = `${formatDownloadProgress(info)}${currentFile ? ` · ${currentFile}` : ""}${failed && errorText ? ` · ${errorText}` : ""}`;
  const statusClassName = failed ? "bad" : pending ? "warn" : complete ? "ok" : "warn";
  const progress = pending ? `
    <div class="download-progress" aria-label="Result download progress">
      <div style="width: ${downloadProgressPercent(info).toFixed(1)}%;"></div>
    </div>
  ` : "";
  const remoteOutput = options.paths && job.remote_result?.remote_output_dir
    ? `<p class="panel-copy">Remote Output: ${escapeHtml(job.remote_result.remote_output_dir)}</p>`
    : "";
  const localOutput = options.paths && job.output_dir
    ? `<p class="panel-copy">Local Output: ${escapeHtml(job.output_dir)}</p>`
    : "";
  return `
    <div class="status-dot ${statusClassName}">
      ${escapeHtml(label)} · ${escapeHtml(detail)}
    </div>
    ${progress}
    ${remoteOutput}
    ${localOutput}
  `;
}

function renderResultDownloadButton(job = {}, compact = false) {
  if (!canDownloadCompletedResult(job)) return "";
  const pending = isRemoteDownloadPending(job);
  const failed = isRemoteDownloadFailed(job);
  const match = resultDownloadServerMatch(job);
  const ready = remoteConfigReadyForReattach();
  const label = pending ? "Downloading" : failed ? "Retry Download" : "Check Result";
  const disabled = pending || !match.ok || !ready;
  const title = !match.ok
    ? `${match.message} Expected ${formatRemoteIdentity(match.expected)}; current ${formatRemoteIdentity(match.current)}.`
    : !ready
      ? "Remote credentials are incomplete."
      : "";
  return `<button class="${compact ? "compact-button" : ""}" data-action="check-result-download" data-job-id="${escapeHtml(job.id || "")}" type="button" ${disabled ? "disabled" : ""} title="${escapeHtml(title)}">${label}</button>`;
}

function renderRepairMetricsButton(job = {}, compact = false) {
  if (!job.can_repair_metrics) return "";
  const ready = remoteConfigReadyForReattach();
  return `<button class="${compact ? "compact-button" : ""}" data-action="repair-metrics" data-job-id="${escapeHtml(job.id || "")}" type="button" ${ready ? "" : "disabled"} title="${ready ? "" : "Remote credentials are incomplete."}">Repair Metrics</button>`;
}

function renderRedownloadButton(job = {}, compact = false) {
  if (!job.can_redownload_result || job.download_path_valid !== false) return "";
  const ready = remoteConfigReadyForReattach();
  return `<button class="${compact ? "compact-button" : ""}" data-action="redownload-result" data-job-id="${escapeHtml(job.id || "")}" type="button" ${ready ? "" : "disabled"} title="${ready ? "" : "Remote credentials are incomplete."}">Redownload</button>`;
}

function statusClass(status) {
  const normalized = String(status || "").toLowerCase();
  if (["completed", "ok", "ready", "canceled"].includes(normalized)) return "ok";
  if (["running", "queued", "detached", "partial_success", "training_success_render_failed", "training_success_metrics_failed", "training_success_postprocess_failed"].includes(normalized)) return "warn";
  if (["failed", "error"].includes(normalized)) return "bad";
  return "";
}

function isTerminal(status) {
  return ["completed", "failed", "canceled", "partial_success", "training_success_render_failed", "training_success_metrics_failed", "training_success_postprocess_failed"].includes(String(status || "").toLowerCase());
}

function selectedAlgorithm() {
  return algorithms.find((item) => item.family === state.algorithmFamily) || algorithms[0] || null;
}

function supportedOperations(adapter = selectedAlgorithm()) {
  if (!adapter?.operations) return [];
  return Object.entries(adapter.operations)
    .filter(([, config]) => config?.enabled)
    .map(([name]) => name);
}

function selectedOperationConfig() {
  const adapter = selectedAlgorithm();
  return adapter?.operations?.[state.operation] || {};
}

function operationSupportsRemote() {
  return Boolean(selectedOperationConfig()?.template);
}

function selectedDataset() {
  const datasets = Array.isArray(state.remoteReport?.datasets) ? state.remoteReport.datasets : [];
  return datasets.find((item) => String(item.id || "") === String(state.selectedRemoteDatasetId || "")) || null;
}

function datasetHasColmap(dataset) {
  if (!dataset) return false;
  const stage = dataset.stage || {};
  return Boolean(
    stage.sparse
    || stage.undistorted
    || dataset.has_sparse_model
    || dataset.has_undistorted_marker
    || dataset.sparse_file_count > 0,
  );
}

function effectiveAutoColmap() {
  return Boolean(state.autoColmap) && !state.useExistingRemoteDataset;
}

function datasetNameForPayload() {
  if (state.useExistingRemoteDataset) {
    const dataset = selectedDataset();
    return String(dataset?.name || dataset?.id || state.remoteDatasetPath || state.datasetName || "remote-dataset").trim();
  }
  return String(state.datasetName || `${state.sessionId}-${state.captureId || "capture"}`).trim();
}

function ensureCaptureId(force = false) {
  if (!state.captureId || force) {
    state.captureId = `capture-${Date.now()}-${Math.random().toString(16).slice(2, 6)}`;
    persistState();
  }
  return state.captureId;
}

function outputDirForPayload() {
  if (state.outputDir) return state.outputDir;
  const suffix = state.useExistingRemoteDataset
    ? (state.selectedRemoteDatasetId || "existing-dataset")
    : (state.captureId || "capture");
  return `/web/generated/runs/${state.sessionId}/${state.algorithmFamily || "algorithm"}/${suffix}`;
}

function getJob(jobId = state.selectedJobId) {
  return jobs.find((item) => item.id === jobId) || null;
}

function jobDatasetLabel(job = {}) {
  const direct = String(job.dataset || job.remote_result?.remote_dataset_id || job.remote_dataset_id || job.dataset_name || "").trim();
  if (direct) return direct;
  const pathText = String(job.dataset_path || job.remote_result?.remote_dataset_workspace || job.remote_dataset_path || job.workspace || "").replace(/\/+$/, "");
  if (!pathText) return "-";
  const parts = pathText.split("/").filter(Boolean);
  if (parts.at(-1) === "workspace" && parts.length >= 2) return `${parts.at(-2)}/workspace`;
  return parts.at(-1) || "-";
}

function sortedJobsList(items = jobs) {
  const direction = state.jobsSortOrder === "created_asc" ? 1 : -1;
  return [...items].sort((left, right) => {
    const timeDelta = (Number(left.created_at || 0) - Number(right.created_at || 0)) * direction;
    if (timeDelta) return timeDelta;
    return `${jobDatasetLabel(left)} ${left.algorithm_family || ""} ${left.id || ""}`
      .localeCompare(`${jobDatasetLabel(right)} ${right.algorithm_family || ""} ${right.id || ""}`);
  });
}

function jobDatasetOptions() {
  return [...new Set(jobs.map(jobDatasetLabel).filter((item) => item && item !== "-"))].sort();
}

function jobAlgorithmOptions() {
  return [...new Set(jobs.map((job) => String(job.algorithm_family || "").trim()).filter(Boolean))].sort();
}

function visibleJobs() {
  const dataset = String(state.jobsDatasetFilter || "").trim();
  const algorithm = String(state.jobsAlgorithmFilter || "").trim();
  const filtered = jobs.filter((job) => {
    if (dataset && jobDatasetLabel(job) !== dataset) return false;
    if (algorithm && String(job.algorithm_family || "").trim() !== algorithm) return false;
    if (state.jobsCompletedOnly && String(job.status || "").toLowerCase() !== "completed") return false;
    return true;
  });
  return sortedJobsList(filtered);
}

function syncJobFilters() {
  if (state.jobsDatasetFilter && !jobDatasetOptions().includes(state.jobsDatasetFilter)) {
    state.jobsDatasetFilter = "";
  }
  if (state.jobsAlgorithmFilter && !jobAlgorithmOptions().includes(state.jobsAlgorithmFilter)) {
    state.jobsAlgorithmFilter = "";
  }
}

function setBusy(next) {
  busy = Boolean(next);
}

function buttonActionKey(node) {
  if (!node) return "";
  if (node.dataset.page) return `page:${node.dataset.page}`;
  if (!node.dataset.action) return "";
  const parts = [node.dataset.action];
  for (const key of ["jobId", "datasetId", "outputDir", "mode", "stage", "statuses", "source"]) {
    if (node.dataset[key]) parts.push(node.dataset[key]);
  }
  return parts.join(":");
}

function setPressedAction(key) {
  if (!key) return;
  pressedActionKey = key;
  window.clearTimeout(pressTimer);
  pressTimer = window.setTimeout(() => {
    pressedActionKey = "";
    decorateButtonFeedback();
  }, 1800);
  decorateButtonFeedback();
}

function setPendingAction(key) {
  pendingActionKey = key || "";
  render();
  afterRender();
}

function decorateButtonFeedback() {
  root.querySelectorAll("button[data-action], button[data-page]").forEach((button) => {
    const key = buttonActionKey(button);
    const isPending = key && key === pendingActionKey;
    const isPressed = key && key === pressedActionKey;
    button.classList.toggle("is-loading", Boolean(isPending));
    button.classList.toggle("is-pressed", Boolean(isPressed));
    if (isPending) {
      button.setAttribute("aria-busy", "true");
      button.disabled = true;
    } else {
      button.removeAttribute("aria-busy");
    }
  });
}

function showToast(message) {
  const node = document.getElementById("toast");
  if (!node) return;
  node.textContent = message;
  node.hidden = false;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    node.hidden = true;
  }, 3600);
}

function setError(error, fallback = "Action failed") {
  const payload = error?.payload || {};
  state.lastError = {
    code: payload.code || error?.code || "",
    message: payload.message || payload.error || error?.message || fallback,
    details: payload.details || {},
    nextAction: payload.next_action || "",
    status: error?.status || "",
  };
  persistState();
  render();
  afterRender();
  const errorMessage = state.lastError.code
    ? `${state.lastError.code}: ${state.lastError.message}`
    : state.lastError.message;
  showToast(errorMessage);
  window.requestAnimationFrame(() => {
    const node = document.getElementById("global-error-banner");
    if (!node) return;
    node.focus({ preventScroll: true });
    node.scrollIntoView({ block: "start", behavior: "smooth" });
  });
}

async function guarded(action, fallback, actionKey = "") {
  let handledError = false;
  try {
    setBusy(true);
    setPendingAction(actionKey);
    state.lastError = null;
    const result = await action();
    persistState();
    render();
    afterRender();
    return result;
  } catch (error) {
    handledError = true;
    setBusy(false);
    pendingActionKey = "";
    setError(error, fallback);
    return null;
  } finally {
    if (!handledError) {
      setBusy(false);
      pendingActionKey = "";
      render();
      afterRender();
    }
  }
}

function buildRunPayload(extra = {}) {
  const dataset = selectedDataset();
  const customPath = String(state.remoteDatasetPath || "").trim();
  const selectedPath = String(dataset?.path || "").trim();
  const remoteDatasetPath = customPath || selectedPath;
  return {
    algorithm_family: state.algorithmFamily,
    operation: state.operation || "train",
    session_id: state.sessionId || "default-session",
    capture_id: state.captureId,
    dataset_name: datasetNameForPayload(),
    auto_materialize: !state.useExistingRemoteDataset,
    auto_colmap: effectiveAutoColmap(),
    use_existing_remote_dataset: Boolean(state.useExistingRemoteDataset),
    remote_dataset_id: customPath ? "" : (state.selectedRemoteDatasetId || ""),
    remote_dataset_path: remoteDatasetPath,
    workspace: "",
    output_dir: outputDirForPayload(),
    checkpoint_path: state.checkpointPath || "",
    input_path: "",
    preview_job_id: state.lastPreview?.preview_job_id || "",
    remote: { ...state.remoteConfig },
    require_remote_check: true,
    ...state.training,
    ...extra,
  };
}

function render() {
  root.innerHTML = `
    <div class="workbench">
      ${renderTopbar()}
      <div class="workspace-frame">
        <div class="layout">
          <aside class="rail">${renderLeftRail()}</aside>
          <main class="main-column">${renderMain()}</main>
        </div>
      </div>
    </div>
    <div id="modal-layer" class="modal-layer" hidden></div>
    <div id="toast" class="toast" hidden></div>
  `;
}

function renderTopbar() {
  return `
    <header class="topbar">
      <div class="brand-mark">
        <div class="brand-cube">GSC</div>
        <div>
          <h1 class="brand-title">Web-GSC</h1>
          <p class="brand-subtitle">Remote training and 3D results workbench</p>
        </div>
      </div>
      <nav class="nav-tabs">
        ${NAV_ITEMS.map((item) => `
          <button class="nav-tab ${state.activePage === item.id ? "active" : ""}" data-page="${item.id}" type="button">${item.label}</button>
        `).join("")}
      </nav>
      <div class="api-chip">
        <span class="status-dot ${state.apiOnline ? "ok" : state.apiOnline === false ? "bad" : "warn"}">${state.apiOnline ? "Online" : state.apiOnline === false ? "Offline" : "Checking"}</span>
        <button data-action="refresh-all" type="button" ${busy ? "disabled" : ""}>Refresh</button>
      </div>
    </header>
  `;
}

function renderLeftRail() {
  const activeJobs = jobs.filter((job) => ["queued", "running"].includes(String(job.status || "").toLowerCase()));
  return `
    <section class="panel">
      <div class="panel-head">
        <h3>Current Data</h3>
        <span class="badge ${state.useExistingRemoteDataset ? "ok" : "warn"}">${state.useExistingRemoteDataset ? "Remote reuse" : "New upload"}</span>
      </div>
      <div class="card rail-facts">
        <p class="panel-copy">Session: ${escapeHtml(state.sessionId || "-")}</p>
        <p class="panel-copy">Capture: ${escapeHtml(state.captureId || "-")}</p>
        <p class="panel-copy">Dataset: ${escapeHtml(datasetNameForPayload())}</p>
        <p class="panel-copy">Uploaded: ${stagedUploadFrameCount()} image(s)</p>
      </div>
    </section>
    <section class="panel">
      <div class="panel-head">
        <h3>Active Jobs</h3>
        <span class="badge warn">${activeJobs.length}</span>
      </div>
      <div class="job-list card">
        ${activeJobs.length ? activeJobs.slice(0, 4).map((job) => renderMiniJob(job)).join("") : `<p class="panel-copy">No jobs are running.</p>`}
      </div>
    </section>
    ${renderStatusRail()}
  `;
}

function renderMiniJob(job) {
  return `
    <article class="selectable-card">
      <strong>${escapeHtml(job.algorithm_family || "-")}</strong>
      <p class="panel-copy">${escapeHtml(job.status || "-")} · ${escapeHtml(job.remote_stage || job.operation || "-")}</p>
      <button data-action="select-job" data-job-id="${escapeHtml(job.id)}" type="button">View</button>
    </article>
  `;
}

function renderStatusRail() {
  return `
    <section class="panel">
      <div class="panel-head">
        <h3>Platform Status</h3>
        <span class="status-dot ${state.apiOnline ? "ok" : state.apiOnline === false ? "bad" : "warn"}">${state.apiOnline ? "Online" : state.apiOnline === false ? "Offline" : "Checking"}</span>
      </div>
      <div class="card grid">
        <p class="panel-copy">API: ${escapeHtml(state.apiBaseUrl)}</p>
        <p class="panel-copy">SSH: ${state.sshChecked ? "Precheck passed" : "Not checked or expired"}</p>
        <p class="panel-copy">COLMAP: ${effectiveAutoColmap() ? "Auto for new upload" : state.useExistingRemoteDataset ? "Skipped for existing data" : "Manual"}</p>
        ${state.lastError ? `<p class="panel-copy">Last error: ${escapeHtml(state.lastError.code || "ERROR")}</p>` : ""}
      </div>
    </section>
  `;
}

function renderEnvironmentChecks() {
  const checks = environmentReport?.checks || [];
  if (!checks.length) return `<p class="panel-copy">Click Check to read the local web runtime and algorithm environment.</p>`;
  return checks.map((item) => `
    <div class="status-dot ${item.ok ? "ok" : item.required ? "bad" : "warn"}">
      ${escapeHtml(item.name || "-")} · ${escapeHtml(item.message || (item.ok ? "OK" : "Failed"))}
    </div>
  `).join("");
}

function renderCurrentJobSummary() {
  const job = getJob();
  if (!job) return `<p class="panel-copy">No job selected. The latest submitted job will be selected automatically.</p>`;
  return `
    <div class="grid">
      <span class="badge ${statusClass(job.status)}">${escapeHtml(job.status || "-")}</span>
      <strong>${escapeHtml(job.algorithm_family || "-")}</strong>
      <p class="panel-copy">ID: ${escapeHtml(job.id)}</p>
      <p class="panel-copy">Stage: ${escapeHtml(job.remote_stage || "-")}</p>
      ${renderRemoteDownloadStatus(job)}
      <div class="button-row">
        <button data-action="select-job" data-job-id="${escapeHtml(job.id)}" type="button">Logs</button>
        <button data-action="open-result" data-job-id="${escapeHtml(job.id)}" type="button">Results</button>
      </div>
    </div>
  `;
}

function renderMain() {
  let page = renderOverviewPage();
  if (state.activePage === "data") page = renderDataPage();
  if (state.activePage === "algorithm") page = renderAlgorithmPage();
  if (state.activePage === "browser") page = renderBrowserPage();
  if (state.activePage === "analysis") page = renderAnalysisPage();
  if (state.activePage === "result") page = renderResultPage();
  return `${renderGlobalErrorBanner()}${page}`;
}

function renderGlobalErrorBanner() {
  if (!state.lastError) return "";
  const details = state.lastError.details || {};
  const hasDetails = Object.keys(details).length > 0;
  return `
    <section id="global-error-banner" class="panel error-banner" tabindex="-1">
      <div class="panel-head">
        <div>
          <h2>${escapeHtml(state.lastError.code || "Action failed")}</h2>
          <p class="panel-copy">${escapeHtml(state.lastError.message)}</p>
        </div>
        <button class="compact-button" data-action="clear-error" type="button">Dismiss</button>
      </div>
      ${state.lastError.nextAction || hasDetails ? `
        <div class="error-banner-body">
          ${state.lastError.nextAction ? `<p class="panel-copy"><strong>Next action:</strong> ${escapeHtml(state.lastError.nextAction)}</p>` : ""}
          ${hasDetails ? `
            <details>
              <summary>Details</summary>
              <pre>${escapeHtml(JSON.stringify(details, null, 2))}</pre>
            </details>
          ` : ""}
        </div>
      ` : ""}
    </section>
  `;
}

function renderOverviewPage() {
  const running = jobs.filter((job) => ["queued", "running"].includes(String(job.status || "").toLowerCase())).length;
  const completed = jobs.filter((job) => job.status === "completed").length;
  return `
    <section class="panel hero">
      <p class="eyebrow">Remote-first Gaussian Workflow</p>
      <h1>The web training page is now a progressive 3D training workbench</h1>
      <div class="action-row">
        <button class="primary" data-page="data" type="button">Prepare Data</button>
        <button data-page="algorithm" type="button">Open Training</button>
        <button data-page="browser" type="button">Open PLY Browser</button>
      </div>
    </section>
    <section class="metric-grid">
      <div class="metric"><span class="muted">Platform</span><strong>${state.apiOnline ? "OK" : state.apiOnline === false ? "Offline" : "..."}</strong></div>
      <div class="metric"><span class="muted">Total Jobs</span><strong>${jobs.length}</strong></div>
      <div class="metric"><span class="muted">Running</span><strong>${running}</strong></div>
      <div class="metric"><span class="muted">Completed</span><strong>${completed}</strong></div>
    </section>
    <section class="panel">
      <div class="panel-head">
        <h2>Workflow</h2>
        ${renderProgressChips()}
      </div>
    </section>
    <section class="panel">
      <div class="panel-head">
        <h2>Recent Jobs</h2>
        <div class="button-row">
          <button data-action="clear-jobs" data-statuses="canceled" type="button">Clear Canceled</button>
          <button data-action="clear-jobs" data-statuses="finished" type="button">Clear Finished</button>
          <button data-page="algorithm" type="button">Open Job Monitor</button>
        </div>
      </div>
      <div class="card">${renderJobsTable(sortedJobsList(jobs).slice(0, 6), { controls: false })}</div>
    </section>
  `;
}

function pipelineProgressIndex() {
  const job = getJob();
  if (job?.status === "completed") return 6;
  if (job?.status === "running") return 4;
  if (state.sshChecked) return 3;
  if (stagedUploadFrameCount() > 0 || state.useExistingRemoteDataset) return 2;
  return 0;
}

function pipelineHint(label) {
  const hints = {
    "Capture": "Camera or image input",
    "Upload": "Write into session/capture",
    "Session Prep": "Auto-materialize dataset",
    "COLMAP": "Remote sparse recovery",
    "Train": "Remote queue execution",
    "Render": "Return results and metrics",
    "View": "PLY first, images as fallback",
    "Data Input": "Capture or select input data",
    "Data Prep": "Materialize and prepare workspace",
    "SSH Precheck": "Verify remote access and dependencies",
    "Command Review": "Preview paths and shell command",
    "Remote Submit": "Create remote training job",
    "Logs and Metrics": "Follow logs and metrics",
    "Result Page": "Open PLY or image result",
  };
  return hints[label] || "";
}

function compactProgressIndex() {
  const job = getJob();
  const status = String(job?.status || "").toLowerCase();
  if (status === "completed") return 4;
  if (["queued", "running", "detached"].includes(status)) return 3;
  if (state.lastPreview) return 2;
  if (state.sshChecked) return 1;
  return 0;
}

function renderProgressChips() {
  const activeIndex = compactProgressIndex();
  const stages = [
    ["Data", stagedUploadFrameCount() > 0 || state.useExistingRemoteDataset],
    ["SSH", state.sshChecked],
    ["Command", Boolean(state.lastPreview)],
    ["Train", ["queued", "running", "detached"].includes(String(getJob()?.status || "").toLowerCase())],
    ["Results", getJob()?.status === "completed"],
  ];
  return `
    <div class="mini-flow" aria-label="Workflow progress">
      ${stages.map(([label, done], index) => `
        <span class="mini-step ${done ? "done" : index === activeIndex ? "active" : ""}">${escapeHtml(label)}</span>
      `).join("")}
    </div>
  `;
}

function renderDataPage() {
  const uploadDisabled = state.sshChecked ? "" : "disabled";
  const autoUploadDisabled = state.sshChecked || streamTimer ? "" : "disabled";
  const uploadedFrames = stagedUploadFrameCount();
  const prepDisabled = state.sshChecked && uploadedFrames ? "" : "disabled";
  const continueDisabled = uploadedFrames || state.useExistingRemoteDataset ? "" : "disabled";
  return `
    <section class="panel">
      <div class="panel-head">
        <div>
          <h2>Data Source</h2>
          <p class="panel-copy">Choose the data path before remote precheck.</p>
        </div>
        <span class="badge ${state.useExistingRemoteDataset ? "ok" : "warn"}">${state.useExistingRemoteDataset ? "Existing remote data" : "New image upload"}</span>
      </div>
      <div class="card">
        ${renderDataSourceSelector()}
      </div>
    </section>
    <section class="panel">
      <div class="panel-head">
        <div>
          <h2>Remote Connection</h2>
          <p class="panel-copy">Connect to the remote machine and validate the paths used by this data source.</p>
        </div>
        <span class="badge ${state.sshChecked ? "ok" : "warn"}">${state.sshChecked ? "SSH READY" : "SSH REQUIRED"}</span>
      </div>
      <div class="card grid">
        ${renderDataPrecheckFields()}
        ${renderRemoteConfigForm()}
        <div class="button-row">
          <button data-action="save-remote-config" type="button">Save Remote Config</button>
          <button data-action="restore-remote-config" type="button">Restore Last Config</button>
          <button class="primary" data-action="check-remote" type="button" ${busy ? "disabled" : ""}>Remote Precheck</button>
        </div>
        ${renderRemoteChecks()}
      </div>
    </section>
    <section class="panel">
      <div class="panel-head">
        <div>
          <h2>${state.useExistingRemoteDataset ? "Existing Remote Dataset" : "New Image Upload"}</h2>
          <p class="panel-copy">${state.useExistingRemoteDataset ? "Select a reusable remote workspace or enter its path." : "Photo upload and camera capture write into the same session."}</p>
        </div>
        <div class="button-row">
          <button class="primary" data-page="algorithm" type="button" ${continueDisabled}>Continue to Training</button>
        </div>
      </div>
      <div class="card grid">
        ${state.useExistingRemoteDataset
          ? renderRemoteDatasetControls()
          : `
            ${state.sshChecked ? "" : `<p class="panel-copy">Run Remote Precheck first. Upload methods unlock after the remote connection succeeds.</p>`}
            <div class="upload-method-grid">
              ${renderPhotoUploadCard(uploadDisabled)}
              ${renderCameraUploadCard(uploadDisabled)}
            </div>
            ${renderCurrentFlowData()}
            <details class="panel pad">
              <summary>Session and Advanced Data Options</summary>
              <div class="grid" style="margin-top: 14px;">
                ${renderSessionFields()}
                <div class="button-row">
                  <button data-action="open-dataset-modal" type="button">Name Dataset</button>
                  <button data-action="new-capture" type="button">New Capture</button>
                  <button data-action="restart-upload-training" type="button">Restart Upload Training</button>
                </div>
              </div>
            </details>
          `}
      </div>
    </section>
    ${state.useExistingRemoteDataset ? "" : `
      <details class="panel">
        <summary class="panel-head">
          <h2>Manual Session Prep</h2>
          <span class="badge ${uploadedFrames ? "ok" : "warn"}">${uploadedFrames ? `${uploadedFrames} frame(s)` : "Waiting for upload"}</span>
        </summary>
        <div class="card grid two">
          <button data-action="materialize-session" type="button" ${prepDisabled}>Manual Session Materialize</button>
          <button data-action="prepare-colmap" type="button" ${prepDisabled}>Manual COLMAP Workspace</button>
          <button data-action="toggle-stream" type="button" ${autoUploadDisabled}>${streamTimer ? "Stop Auto Upload" : "Auto Stream Upload"}</button>
        </div>
      </details>
    `}
    <section class="panel">
      <div class="panel-head">
        <h2>Workflow</h2>
        ${renderProgressChips()}
      </div>
    </section>
  `;
}

function renderDataSourceSelector() {
  return `
    <div class="source-choice-grid">
      <button class="source-choice ${state.useExistingRemoteDataset ? "" : "active"}" data-action="set-data-source" data-source="upload" type="button">
        <strong>Upload New Images</strong>
        <span>Upload images, then optionally run COLMAP preprocessing.</span>
      </button>
      <button class="source-choice ${state.useExistingRemoteDataset ? "active" : ""}" data-action="set-data-source" data-source="existing" type="button">
        <strong>Use Existing Remote Data</strong>
        <span>Use the selected remote workspace directly; COLMAP is skipped.</span>
      </button>
    </div>
  `;
}

function renderPhotoUploadCard(uploadDisabled) {
  return `
    <section class="upload-method-card">
      <div>
        <p class="eyebrow">Photo Upload</p>
        <h3>Upload Photos</h3>
        <p class="panel-copy">Select one or more images, then upload them into this session.</p>
      </div>
      ${renderUploadBox()}
      <button class="primary upload-main-button" data-action="upload-selected-photos" type="button" ${uploadDisabled}>Upload Selected Photos</button>
    </section>
  `;
}

function renderCameraUploadCard(uploadDisabled) {
  return `
    <section class="upload-method-card">
      <div>
        <p class="eyebrow">Camera Capture</p>
        <h3>Use Camera</h3>
        <p class="panel-copy">Start the camera, frame the scene, then upload the current camera frame.</p>
      </div>
      <video id="local-stream" class="video-frame camera-frame" autoplay playsinline muted></video>
      <div class="button-row">
        <button data-action="start-camera" type="button" ${uploadDisabled}>Start Camera</button>
        <button data-action="stop-camera" type="button">Stop</button>
      </div>
      <button class="primary upload-main-button" data-action="upload-camera-frame" type="button" ${uploadDisabled}>Upload Camera Frame</button>
    </section>
  `;
}

function renderDataPrecheckFields() {
  return `
    <div class="form-grid">
      <label>Algorithm
        <select data-bind="algorithmFamily">
          ${algorithms.map((item) => `<option value="${escapeHtml(item.family)}" ${state.algorithmFamily === item.family ? "selected" : ""}>${escapeHtml(item.label || item.family)}</option>`).join("")}
        </select>
      </label>
      ${state.useExistingRemoteDataset
        ? `<p class="panel-copy wide">Existing remote data is used directly. COLMAP precheck and auto-COLMAP are skipped.</p>`
        : `<label class="wide">
            <span><input data-bind="autoColmap" type="checkbox" ${state.autoColmap ? "checked" : ""} /> Run remote COLMAP automatically when sparse/undistorted data is missing</span>
          </label>`}
    </div>
  `;
}

function stagedUploadFrameCount() {
  return Number(state.flowData?.upload?.uploaded_frame_count || state.uploadedCount || 0);
}

function renderCurrentFlowData() {
  const data = state.flowData || {};
  const capture = data.capture || {};
  const upload = data.upload || {};
  const prep = data.session_prep || {};
  const currentCapture = capture.current || {};
  const latestCapture = currentCapture.latest_frame || null;
  const capturePath = currentCapture.input_dir || "";
  const streamPath = upload.stream_dir || "";
  const dataset = Array.isArray(prep.datasets) ? prep.datasets[0] : null;
  const workspace = Array.isArray(prep.workspaces) ? prep.workspaces[0] : null;
  return `
    <section class="panel pad">
      <div class="panel-head">
        <h3>Current Staged Data</h3>
        <button data-action="refresh-flow-data" type="button">Refresh</button>
      </div>
      <div class="flow-stage-grid">
        ${renderFlowStageCard({
          index: 1,
          stage: "capture",
          title: "Capture",
          badge: `${Number(capture.frame_count || 0)} frame(s)`,
          detail: capturePath ? summarize(capturePath, 72) : "No capture input yet.",
          fullDetail: capturePath,
          enabled: Number(capture.frame_count || 0) > 0,
          deletable: Number(capture.frame_count || 0) > 0,
          preview: latestCapture?.url || "",
        })}
        ${renderFlowStageCard({
          index: 2,
          stage: "upload",
          title: "Upload",
          badge: `${Number(upload.uploaded_frame_count || 0)} uploaded`,
          detail: streamPath ? summarize(streamPath, 72) : "No stream session yet.",
          fullDetail: streamPath,
          enabled: Number(upload.uploaded_frame_count || 0) > 0,
          deletable: Number(upload.uploaded_frame_count || 0) > 0 || Boolean(upload.latest_output),
          preview: upload.latest_output?.url || "",
        })}
        ${renderFlowStageCard({
          index: 3,
          stage: "session_prep",
          title: "Session Prep",
          badge: `${Number(prep.dataset_count || 0)} dataset / ${Number(prep.workspace_count || 0)} workspace`,
          detail: summarize(workspace?.workspace_root || dataset?.dataset_root || "No materialized dataset or workspace yet.", 72),
          fullDetail: workspace?.workspace_root || dataset?.dataset_root || "",
          enabled: Boolean(prep.exists),
          deletable: Boolean(prep.exists),
          preview: "",
        })}
      </div>
    </section>
  `;
}

function renderFlowStageCard({ index, stage, title, badge, detail, fullDetail, enabled, deletable, preview }) {
  return `
    <article class="flow-stage-row">
      <span class="flow-stage-index">${escapeHtml(index)}</span>
      <div class="flow-stage-main">
        <strong>${escapeHtml(title)}</strong>
        <p class="panel-copy" title="${escapeHtml(fullDetail || detail)}">${escapeHtml(detail)}</p>
      </div>
      ${preview ? `<img class="flow-preview" src="${escapeHtml(preview)}" alt="${escapeHtml(title)} preview" />` : ""}
      <span class="badge ${enabled ? "ok" : "warn"}">${enabled ? escapeHtml(badge) : "Empty"}</span>
      ${deletable ? `<button class="danger compact-button" data-action="delete-flow-data" data-stage="${escapeHtml(stage)}" type="button">Delete</button>` : `<span class="stage-empty">No data</span>`}
    </article>
  `;
}

function renderSessionFields() {
  return `
    <div class="form-grid">
      <label>Session ID
        <input data-bind="sessionId" value="${escapeHtml(state.sessionId)}" />
      </label>
      <label>Capture ID
        <input data-bind="captureId" value="${escapeHtml(state.captureId)}" placeholder="Auto-generated" />
      </label>
      <label class="wide">Dataset Name
        <input data-bind="datasetName" value="${escapeHtml(state.datasetName)}" placeholder="Click Name Dataset, or confirm before submit" />
      </label>
    </div>
  `;
}

function renderUploadBox() {
  return `
    <div class="dropzone">
      <strong>Select images</strong>
      <p class="panel-copy">Multiple images are uploaded into the current session together.</p>
      <input id="image-upload-input" type="file" accept="image/*" multiple />
      <div id="upload-preview-grid" class="preview-grid"></div>
    </div>
  `;
}

function renderRemoteDatasetControls() {
  const datasets = Array.isArray(state.remoteReport?.datasets) ? state.remoteReport.datasets : [];
  return `
    <p class="panel-copy">Existing remote data will be passed straight to the selected algorithm. COLMAP is not checked or run for this path.</p>
    <label>Remote Dataset
      <select data-bind="selectedRemoteDatasetId">
        <option value="">Not selected</option>
        ${datasets.map((item) => `
          <option value="${escapeHtml(item.id || "")}" ${state.selectedRemoteDatasetId === item.id ? "selected" : ""}>
            ${escapeHtml(item.name || item.id || "unnamed")} · ${datasetHasColmap(item) ? "Sparse marker found" : "No sparse marker"}
          </option>
        `).join("")}
      </select>
    </label>
    <label>Custom Remote Data Path
      <input data-bind="remoteDatasetPath" value="${escapeHtml(state.remoteDatasetPath)}" placeholder="/tmp/web_scan/workspaces/datasets/.../workspace" />
    </label>
    <div class="dataset-list">
      ${datasets.length ? datasets.slice(0, 6).map((item) => renderDatasetCard(item)).join("") : `<p class="panel-copy">Remote datasets appear here after Remote Precheck. A custom path can be entered now.</p>`}
    </div>
  `;
}

function renderDatasetCard(dataset) {
  const active = dataset.id && dataset.id === state.selectedRemoteDatasetId;
  const colmapOk = datasetHasColmap(dataset);
  return `
    <article class="selectable-card ${active ? "active" : ""}">
      <strong>${escapeHtml(dataset.name || dataset.id || "Unnamed dataset")}</strong>
      <p class="panel-copy">${escapeHtml(summarize(dataset.path, 76))}</p>
      <div class="button-row">
        <span class="badge ${colmapOk ? "ok" : "warn"}">${colmapOk ? "Sparse marker found" : "No sparse marker"}</span>
        <span class="badge">${Number(dataset.frame_count || 0) || "?"} frames</span>
      </div>
      <button data-action="use-dataset" data-dataset-id="${escapeHtml(dataset.id || "")}" type="button">Use This Dataset</button>
    </article>
  `;
}

function renderAlgorithmPage() {
  const canSubmit = canSubmitRemoteJob();
  return `
    <section class="panel">
      <div class="panel-head">
        <div>
          <h2>Algorithm Management and Remote Job Scheduling</h2>
          <p class="panel-copy">Remote training is the only training entry. Local APIs stay available for debug compatibility only.</p>
          ${renderProgressChips()}
        </div>
        <span class="badge ${operationSupportsRemote() ? "ok" : "bad"}">${operationSupportsRemote() ? "Remote Template OK" : "No Remote Template"}</span>
      </div>
      <div class="card grid">
        <div class="split">
          <div class="grid">
            <div class="form-grid">
              <label>Algorithm
                <select data-bind="algorithmFamily">
                  ${algorithms.map((item) => `<option value="${escapeHtml(item.family)}" ${state.algorithmFamily === item.family ? "selected" : ""}>${escapeHtml(item.label || item.family)}</option>`).join("")}
                </select>
              </label>
              <label>Local Result Output Directory
                <input data-bind="outputDir" value="${escapeHtml(state.outputDir)}" placeholder="${escapeHtml(outputDirForPayload())}" />
              </label>
            </div>
            ${renderTrainingFields()}
            ${renderCommandPreview()}
          </div>
          <div class="grid">
            ${renderRemoteExecutionSummary()}
            <div class="button-row">
              <button data-page="data" type="button">Edit Data and Remote Config</button>
              <button data-action="restart-upload-training" type="button">Restart Upload Training</button>
              <button data-action="check-remote" type="button" ${busy ? "disabled" : ""}>Remote Precheck</button>
              <button data-action="preview-command" type="button" ${state.sshChecked ? "" : "disabled"}>Generate Command Preview</button>
              <button class="primary" data-action="submit-remote" type="button" ${canSubmit ? "" : "disabled"}>Submit Remote Job</button>
            </div>
            ${renderRemoteChecks()}
          </div>
        </div>
      </div>
    </section>
    <section class="panel">
      <div class="panel-head">
        <h2>Job Monitor</h2>
        <div class="button-row">
          <button data-action="clear-jobs" data-statuses="canceled" type="button">Clear Canceled</button>
          <button data-action="clear-jobs" data-statuses="finished" type="button">Clear Finished</button>
          <button data-action="refresh-jobs" type="button">Refresh Jobs</button>
          ${state.selectedJobId ? `<button data-action="load-log-reset" type="button">Reload Logs</button>` : ""}
        </div>
      </div>
      <div class="card grid">
        ${renderJobsTable(visibleJobs(), { controls: true })}
        ${renderLogPanel()}
      </div>
    </section>
  `;
}

function renderRemoteExecutionSummary() {
  const remote = state.remoteConfig || {};
  const dataset = selectedDataset();
  const datasetText = state.useExistingRemoteDataset
    ? (state.remoteDatasetPath || dataset?.path || state.selectedRemoteDatasetId || "No existing dataset selected")
    : `${stagedUploadFrameCount()} uploaded image(s)`;
  return `
    <section class="panel pad">
      <h3>Remote Execution Summary</h3>
      <div class="grid" style="margin-top: 14px;">
        <p class="panel-copy">Data Source: ${state.useExistingRemoteDataset ? "Existing remote dataset" : "New image upload"}</p>
        <p class="panel-copy">Dataset: ${escapeHtml(summarize(datasetText, 96))}</p>
        <p class="panel-copy">Host: ${escapeHtml(remote.host || "-")} · User: ${escapeHtml(remote.username || "-")}</p>
        <p class="panel-copy">Repo: ${escapeHtml(summarize(remote.repo_path || "-", 96))}</p>
        <p class="panel-copy">Workspace Root: ${escapeHtml(summarize(remote.workspace_root || "-", 96))}</p>
        <p class="panel-copy">COLMAP: ${effectiveAutoColmap() ? "Auto for new upload" : state.useExistingRemoteDataset ? "Skipped for existing data" : "Manual"}</p>
        <span class="badge ${state.sshChecked ? "ok" : "warn"}">${state.sshChecked ? "Remote precheck passed" : "Remote precheck needed"}</span>
      </div>
    </section>
  `;
}

function renderTrainingFields() {
  return `
    <details class="panel pad">
      <summary>Detailed Parameters</summary>
      <div class="form-grid" style="margin-top: 14px;">
        ${Object.entries(DEFAULT_TRAINING).map(([key]) => `
          <label>${escapeHtml(key)}
            <input data-training="${escapeHtml(key)}" type="number" step="any" value="${escapeHtml(state.training[key])}" />
          </label>
        `).join("")}
      </div>
      <details class="nested-detail">
        <summary>Checkpoint</summary>
        <div class="form-grid" style="margin-top: 12px;">
          <label class="wide">Checkpoint Path
            <input data-bind="checkpointPath" value="${escapeHtml(state.checkpointPath)}" placeholder="Only required by some templates" />
          </label>
        </div>
      </details>
    </details>
  `;
}

function renderRemoteConfigForm() {
  const remote = state.remoteConfig;
  return `
    <section class="panel pad">
      <h3>Remote Environment Config</h3>
      <div class="form-grid" style="margin-top: 14px;">
        <label>Host<input data-remote="host" value="${escapeHtml(remote.host)}" placeholder="192.168.1.20" /></label>
        <label>Port<input data-remote="port" type="number" value="${escapeHtml(remote.port)}" /></label>
        <label>Username<input data-remote="username" value="${escapeHtml(remote.username)}" placeholder="ubuntu" /></label>
        <label>Password<input data-remote="password" type="password" value="${escapeHtml(remote.password)}" /></label>
        <label class="wide">Repo Path<input data-remote="repo_path" value="${escapeHtml(remote.repo_path)}" placeholder="/home/ubuntu/HAC-plus-main" /></label>
        <label>Workspace Root<input data-remote="workspace_root" value="${escapeHtml(remote.workspace_root)}" /></label>
        <label>Output Root<input data-remote="output_root" value="${escapeHtml(remote.output_root)}" /></label>
        <label>Python<input data-remote="python" value="${escapeHtml(remote.python)}" /></label>
        <label class="wide">Activate Command<input data-remote="activate_cmd" value="${escapeHtml(remote.activate_cmd)}" placeholder="source ~/.bashrc && conda activate env" /></label>
      </div>
    </section>
  `;
}

function renderCommandPreview() {
  const preview = state.lastPreview;
  if (!preview) {
    return `
      <section class="panel pad">
        <div class="button-row" style="justify-content: space-between;">
          <h3>Command Review</h3>
          <button type="button" disabled>Edit</button>
        </div>
        <p class="panel-copy">Generate a command preview after SSH precheck. Submission opens a custom confirmation dialog instead of the native browser confirm.</p>
      </section>
    `;
  }
  const editableCommand = preview.command_override || preview.remote_command || preview.shell_command || "";
  const commandDraft = state.commandDraft || editableCommand;
  const commandMarkup = state.commandReviewEditing
    ? `<textarea class="command-editor" data-command-draft spellcheck="false">${escapeHtml(commandDraft)}</textarea>`
    : `<pre>${escapeHtml(editableCommand)}</pre>`;
  return `
    <section class="panel pad">
      <div class="button-row" style="justify-content: space-between;">
        <h3>Command Review</h3>
        <div class="button-row">
          ${preview.command_override ? `<span class="badge warn">Edited</span>` : `<span class="badge ok">Preview Ready</span>`}
          ${
            state.commandReviewEditing
              ? `<button class="primary" data-action="confirm-command-edit" type="button">OK</button>`
              : `<button data-action="edit-command" type="button">Edit</button>`
          }
        </div>
      </div>
      <div class="command-box grid" style="margin-top: 12px;">
        <p class="panel-copy">Remote Workspace: ${escapeHtml(preview.remote_workspace)}</p>
        <p class="panel-copy">Remote Output: ${escapeHtml(preview.remote_output_dir)}</p>
        <p class="panel-copy">Local Output: ${escapeHtml(preview.local_output_dir)}</p>
        ${renderTmuxAttachInfo(preview)}
        ${preview.missing_inputs?.length ? `<p class="badge bad">Missing template inputs: ${escapeHtml(preview.missing_inputs.join(", "))}</p>` : `<p class="badge ok">Template inputs complete</p>`}
        ${commandMarkup}
      </div>
    </section>
  `;
}

function renderTmuxAttachInfo(source) {
  const attachCommand = String(source?.remote_tmux_attach_command || "").trim();
  if (!attachCommand) return "";
  const session = String(source?.remote_tmux_session || "").trim();
  return `
    <div class="tmux-attach-box">
      <p class="panel-copy">Tmux Session: ${escapeHtml(session || "-")}</p>
      <p class="panel-copy">Attach from another SSH tool to enter the same remote window:</p>
      <pre>${escapeHtml(attachCommand)}</pre>
    </div>
  `;
}

function renderRemoteChecks() {
  const checks = state.remoteReport?.checks || [];
  if (!checks.length) {
    return `<section class="panel pad"><p class="panel-copy">No remote precheck result yet.</p></section>`;
  }
  return `
    <section class="panel pad">
      <h3>Remote Precheck Result</h3>
      <div class="grid" style="margin-top: 12px;">
        ${checks.map((item) => `
          <div class="status-dot ${item.ok ? "ok" : item.required ? "bad" : "warn"}">${escapeHtml(item.name)} · ${escapeHtml(item.message)}</div>
        `).join("")}
      </div>
    </section>
  `;
}

function canSubmitRemoteJob() {
  if (!state.sshChecked || !operationSupportsRemote()) return false;
  if (state.useExistingRemoteDataset) {
    const hasDataset = Boolean(state.remoteDatasetPath || state.selectedRemoteDatasetId);
    if (!hasDataset) return false;
    return Boolean(state.lastPreview);
  }
  return stagedUploadFrameCount() > 0 && Boolean(state.lastPreview);
}

function renderJobListControls() {
  const datasetOptions = jobDatasetOptions();
  const algorithmOptions = jobAlgorithmOptions();
  return `
    <div class="form-grid compact-form">
      <label>Dataset
        <select data-bind="jobsDatasetFilter">
          <option value="">All</option>
          ${datasetOptions.map((item) => `<option value="${escapeHtml(item)}" ${state.jobsDatasetFilter === item ? "selected" : ""}>${escapeHtml(item)}</option>`).join("")}
        </select>
      </label>
      <label>Algorithm
        <select data-bind="jobsAlgorithmFilter">
          <option value="">All</option>
          ${algorithmOptions.map((item) => `<option value="${escapeHtml(item)}" ${state.jobsAlgorithmFilter === item ? "selected" : ""}>${escapeHtml(item)}</option>`).join("")}
        </select>
      </label>
      <label>Sort by
        <select data-bind="jobsSortOrder">
          <option value="created_desc" ${state.jobsSortOrder !== "created_asc" ? "selected" : ""}>Newest first</option>
          <option value="created_asc" ${state.jobsSortOrder === "created_asc" ? "selected" : ""}>Oldest first</option>
        </select>
      </label>
      <label>Completed
        <span><input data-bind="jobsCompletedOnly" type="checkbox" ${state.jobsCompletedOnly ? "checked" : ""} /> Completed only</span>
      </label>
    </div>
  `;
}

function renderJobsTable(items, options = {}) {
  const controls = options.controls ? renderJobListControls() : "";
  if (!items.length) return `${controls}<p class="panel-copy">No jobs yet.</p>`;
  return `
    ${controls}
    <div class="table-wrap">
      <table class="job-table">
        <thead>
          <tr>
            <th>Job</th><th>Status</th><th>Dataset</th><th>Created</th><th>Output</th><th>Actions</th>
          </tr>
        </thead>
        <tbody>
          ${items.map((job) => `
            <tr>
              <td>
                <strong>${escapeHtml(job.algorithm_family || "-")}</strong>
                <p class="panel-copy">${escapeHtml(summarize(job.id, 18))}</p>
              </td>
              <td>
                <span class="badge ${statusClass(job.status)}">${escapeHtml(job.status || "-")}</span>
                ${job.safe_to_close_web && !isTerminal(job.status) ? `<span class="badge ok">Safe to close page</span>` : ""}
                <p class="panel-copy">${escapeHtml(job.remote_stage || "-")}</p>
                ${renderRemoteDownloadStatus(job)}
                ${renderRemoteServerMatchWarning(job, { details: false })}
                ${needsRemoteReattach(job) ? renderReattachStatus(job) : ""}
              </td>
              <td>${escapeHtml(summarize(jobDatasetLabel(job), 36))}</td>
              <td>${escapeHtml(formatTimestamp(job.created_at))}</td>
              <td>${escapeHtml(summarize(job.result_path || job.output_dir || "-", 42))}</td>
              <td>
                <div class="table-actions">
                  <button class="compact-button" data-action="select-job" data-job-id="${escapeHtml(job.id)}" type="button">Logs</button>
                  <button class="compact-button" data-action="open-result" data-job-id="${escapeHtml(job.id)}" type="button">Results</button>
                  ${renderRepairMetricsButton(job, true)}
                  ${renderResultDownloadButton(job, true)}
                  ${renderRedownloadButton(job, true)}
                  <button class="compact-button" data-action="rerun-job" data-job-id="${escapeHtml(job.id)}" type="button">Rerun</button>
                  ${isTerminal(job.status)
                    ? `<button class="danger compact-button" data-action="delete-job" data-job-id="${escapeHtml(job.id)}" type="button">Remove</button>`
                    : `<button class="danger compact-button" data-action="cancel-job" data-job-id="${escapeHtml(job.id)}" type="button">Cancel</button>`}
                </div>
              </td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderLogPanel() {
  const job = getJob();
  if (!job) return `<p class="panel-copy">Select a job to stream incremental logs here.</p>`;
  const logUrl = buildJobLogDownloadUrl(state.apiBaseUrl, job.id);
  const csvUrl = buildJobMetricsCsvUrl(state.apiBaseUrl, job.id);
  const downloadsEnabled = isTerminal(job.status);
  return `
    <section class="panel pad">
      <div class="button-row" style="justify-content: space-between;">
        <div>
          <h3>Logs and Metrics</h3>
          <p class="panel-copy">${escapeHtml(job.id)} · ${escapeHtml(job.status || "-")}</p>
          ${job.safe_to_close_web && !isTerminal(job.status) ? `<p class="status-dot ok">Safe to close page · remote training keeps running.</p>` : ""}
          ${renderRemoteDownloadStatus(job, { paths: true })}
          ${needsRemoteReattach(job) ? renderReattachStatus(job, "status-dot warn") : ""}
        </div>
        <div class="button-row">
          <a class="button-link ${downloadsEnabled ? "" : "disabled"}" href="${downloadsEnabled ? escapeHtml(logUrl) : "#"}" download>Download Logs</a>
          <a class="button-link ${downloadsEnabled ? "" : "disabled"}" href="${downloadsEnabled ? escapeHtml(csvUrl) : "#"}" download>Download metrics.csv</a>
        </div>
      </div>
      ${renderTmuxAttachInfo(job)}
      <pre id="log-tail" class="logs">${escapeHtml(state.logText || "Waiting for log output...")}</pre>
    </section>
  `;
}

function renderBrowserPage() {
  return `
    <section class="panel">
      <div class="panel-head">
        <div>
          <h2>PLY / Image Result Browser</h2>
          <p class="panel-copy">Open a project result folder or a single PLY/image file. PLY is used first, then image fallback.</p>
        </div>
        <button data-action="discover-results" type="button">Discover Results</button>
      </div>
      <div class="card grid">
        <div class="form-grid">
          <label class="wide">Result Folder / File Path
            <input data-bind="quickPlyPath" value="${escapeHtml(state.quickPlyPath)}" placeholder="/abs/path/to/project-output-or-model.ply" />
          </label>
        </div>
        <div class="button-row">
          <button class="primary" data-action="load-result" type="button">Open Result</button>
          <button data-action="open-selected-result" type="button" ${state.selectedJobId ? "" : "disabled"}>Open Current Job Result</button>
        </div>
        ${renderResultSurface(state.loadedPly, { tall: true })}
      </div>
    </section>
    <section class="panel">
      <div class="panel-head"><h2>Discovered Results</h2><span class="badge">${discoveredResults.length}</span></div>
      <div class="card dataset-list">
        ${discoveredResults.length ? discoveredResults.map((item) => {
          const asset = classifyResultAsset(item);
          return `
            <article class="selectable-card">
              <div class="button-row">
                <strong>${escapeHtml(summarize(item.output_dir, 84))}</strong>
                <span class="badge ${asset.type ? "ok" : "warn"}">${escapeHtml(resultTypeLabel(item))}</span>
              </div>
              <p class="panel-copy">${escapeHtml(item.family ? `${item.family} · ${asset.sourceUrl || item.resolved_path || "-"}` : asset.sourceUrl || item.resolved_path || "-")}</p>
              <button data-action="open-discovered-result" data-output-dir="${escapeHtml(item.output_dir)}" type="button">Open</button>
            </article>
          `;
        }).join("") : `<p class="panel-copy">Click Discover Results to scan generated outputs and local project result folders.</p>`}
      </div>
    </section>
  `;
}

function renderImageGallery(imageUrls = []) {
  if (!imageUrls.length) return "";
  return `
    <div class="render-image-grid">
      ${imageUrls.map((url, index) => `
        <a class="render-image-thumb" href="${escapeHtml(url)}" target="_blank" rel="noreferrer">
          <img src="${escapeHtml(url)}" alt="render ${index + 1}" loading="lazy" />
        </a>
      `).join("")}
    </div>
  `;
}

function renderResultSurface(asset, options = {}) {
  const sourceAsset = asset || {};
  const classified = classifyResultAsset(sourceAsset);
  const imageAsset = getRenderableImageAsset(sourceAsset);
  const imageUrls = imageAsset?.imageUrls || [];
  const frameClass = `viewer-frame${options.tall === false ? "" : " tall"}`;
  if (classified.type === "ply" || classified.type === "manifest") {
    const modelLabel = classified.type === "manifest" ? "Scene Manifest" : classified.renderLabel || "PLY 3D Model";
    const modelHint = classified.renderFormat === "rgb-point-cloud"
      ? "Displayed as an RGB point cloud."
      : classified.renderFormat === "anchor-gaussian"
        ? "Displayed as anchor Gaussian geometry."
      : "Use the 3D viewer controls to rotate and zoom.";
    if (!classified.viewerUrl) {
      return `
        <div class="${frameClass} result-placeholder">
          <div>
            <h3>${escapeHtml(modelLabel)} Detected</h3>
            <p class="panel-copy">${escapeHtml(classified.sourceUrl)}</p>
            <p class="panel-copy">A PLY file must be opened through the 3D viewer. No viewer URL was returned for this model.</p>
          </div>
        </div>
      `;
    }
    return `
      <section class="result-surface">
        <div class="result-toolbar">
          <span class="badge ok">${escapeHtml(modelLabel)}</span>
          <span class="panel-copy">${escapeHtml(modelHint)}</span>
        </div>
        <iframe class="${frameClass}" src="${escapeHtml(classified.viewerUrl)}" title="PLY 3D model viewer"></iframe>
        ${renderImageGallery(imageUrls)}
      </section>
    `;
  }
  if (classified.type === "image") {
    return `
      <section class="result-surface result-surface-image">
        <div class="result-toolbar">
          <span class="badge ok">Image Result${imageUrls.length > 1 ? ` · ${imageUrls.length}` : ""}</span>
          <span class="panel-copy">${imageUrls.length > 1 ? "Render images" : "Displayed as an image."}</span>
        </div>
        <img class="result-image" src="${escapeHtml(classified.imageUrl)}" alt="model result" />
        ${imageUrls.length > 1 ? renderImageGallery(imageUrls) : ""}
      </section>
    `;
  }
  return `
    <div class="${frameClass} result-placeholder">
      <div>
        <h3>Waiting for supported result assets</h3>
        <p class="panel-copy">${classified.sourceUrl ? escapeHtml(classified.sourceUrl) : escapeHtml(options.emptyText || "PLY and image files are handled by separate viewers.")}</p>
      </div>
    </div>
  `;
}

function renderAnalysisPage() {
  const job = getJob();
  const metricItems = analysisSummaryItems(job, state.metricsRows);
  return `
    <section class="panel">
      <div class="panel-head">
        <div>
          <h2>Training Metrics Analysis</h2>
          <p class="panel-copy">Parse job data into PSNR(dB), SSIM, LPIPS, and Size(MB).</p>
        </div>
        <div class="button-row">
          <button data-action="load-analysis" type="button" ${job ? "" : "disabled"}>Load Metrics</button>
          <button data-action="export-analysis" type="button" ${jobs.length ? "" : "disabled"}>Export CSV</button>
        </div>
      </div>
      <div class="card grid">
        <label>Select Job
          <select data-bind="selectedJobId">
            <option value="">Not selected</option>
            ${sortedJobsList(jobs).map((item) => `<option value="${escapeHtml(item.id)}" ${state.selectedJobId === item.id ? "selected" : ""}>${escapeHtml(jobDatasetLabel(item))} · ${escapeHtml(item.algorithm_family || "-")} · ${escapeHtml(item.status || "-")} · ${escapeHtml(item.id.slice(0, 8))}</option>`).join("")}
          </select>
        </label>
        <div class="metric-grid analysis-metric-grid">
          ${metricItems.map((item) => `
            <div class="metric analysis-metric">
              <span class="muted">${escapeHtml(item.label)}</span>
              <strong title="${escapeHtml(item.raw || item.value)}">${escapeHtml(item.value)}</strong>
              <p class="panel-copy">${escapeHtml(item.source || "-")}</p>
            </div>
          `).join("") || `<p class="panel-copy">No jobs to compare yet.</p>`}
        </div>
        <canvas id="metrics-chart" class="chart"></canvas>
        ${renderMetricsTable(state.metricsRows, job)}
      </div>
    </section>
  `;
}

function renderMetricsTable(rows, job) {
  const summaryItems = analysisSummaryItems(job, rows);
  const hasSummary = summaryItems.some(hasAnalysisValue);
  if (!rows.length && !hasSummary) return `<p class="panel-copy">No metrics yet. While running, use the log panel for live tail output; after completion, download metrics.csv.</p>`;
  return `
    <div class="table-wrap">
      <table class="analysis-table">
        <thead><tr><th>Time</th><th>PSNR(dB)</th><th>SSIM</th><th>LPIPS</th><th>Size(MB)</th></tr></thead>
        <tbody>
          ${hasSummary ? `
            <tr class="analysis-summary-row">
              <td>Result Summary</td>
              ${summaryItems.map((item) => `<td title="${escapeHtml(item.source || item.raw || item.value)}">${escapeHtml(item.value)}</td>`).join("")}
            </tr>
          ` : ""}
          ${rows.slice(-80).reverse().map((row) => {
            const items = analysisMetricItems(row, []);
            const filledItems = items.map((item, index) => {
              if (hasAnalysisValue(item)) return item;
              const summary = summaryItems[index];
              return item.staticSummary && summary && hasAnalysisValue(summary)
                ? { ...summary, source: summary.source || "Result Summary" }
                : item;
            });
            return `
              <tr>
                <td>${escapeHtml(row.timestamp || "-")}</td>
                ${filledItems.map((item) => `<td title="${escapeHtml(item.source || item.raw || item.value)}">${escapeHtml(item.value)}</td>`).join("")}
              </tr>
            `;
          }).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderResultPage() {
  const params = new URLSearchParams(window.location.search);
  const jobId = params.get("job") || state.selectedJobId;
  const job = getJob(jobId);
  const plyAsset = job ? getRenderablePlyAsset(job) : null;
  const imageAsset = job ? getRenderableImageAsset(job) : null;
  const downloadPending = job ? isRemoteDownloadPending(job) : false;
  const preferredMode = state.renderMode || "ply";
  const mode = preferredMode === "image" && imageAsset ? "image" : plyAsset ? "ply" : imageAsset ? "image" : preferredMode;
  const imageFields = { result_url: imageAsset?.imageUrl || "", result_urls: imageAsset?.imageUrls || [], render_images: job?.render_images || [] };
  const surfaceAsset = mode === "image"
    ? imageFields
    : {
        ...imageFields,
        viewer_url: plyAsset?.viewerUrl || "",
        point_cloud_url: plyAsset?.sourceUrl || "",
        render_format: plyAsset?.renderFormat || "",
        render_label: plyAsset?.renderLabel || "",
      };
  return `
    <section class="panel">
      <div class="panel-head">
        <div>
          <h2>Result Page</h2>
          <p class="panel-copy">${job ? `${job.algorithm_family} · ${job.id}` : "Job not found. Try opening it from discovered results."}</p>
        </div>
        <div class="button-row">
          <button data-bind-render="ply" class="${mode === "ply" ? "primary" : ""}" data-action="set-render-mode" data-mode="ply" type="button" ${plyAsset ? "" : "disabled"}>PLY</button>
          <button data-bind-render="image" class="${mode === "image" ? "primary" : ""}" data-action="set-render-mode" data-mode="image" type="button" ${imageAsset ? "" : "disabled"}>Image</button>
          ${job ? renderResultDownloadButton(job) : ""}
          ${job ? renderRepairMetricsButton(job) : ""}
          ${job ? renderRedownloadButton(job) : ""}
          <button data-page="algorithm" type="button">Back to Jobs</button>
        </div>
      </div>
      <div class="card grid">
        ${job ? renderRemoteServerMatchWarning(job) : ""}
        ${job ? renderRemoteDownloadStatus(job, { paths: true }) : ""}
        ${job ? renderResultSurface(surfaceAsset, {
          tall: true,
          emptyText: downloadPending
            ? "Remote training has finished. Result files are still downloading to the local output folder."
            : undefined,
        }) : `<p class="panel-copy">No renderable job yet. Finish training first or open a result from the Browser page.</p>`}
        ${job && !downloadPending && !plyAsset && !imageAsset ? `<div class="error-box"><p>No supported result file was found. Supported model files use .ply, while image results use .png, .jpg, .jpeg, .bmp, .gif, .webp, .tif, or .tiff.</p></div>` : ""}
      </div>
    </section>
  `;
}

function bindStaticEvents() {
  root.addEventListener("click", handleClick);
  root.addEventListener("change", handleChange);
  root.addEventListener("input", handleInput);
  window.addEventListener("popstate", () => {
    state.activePage = initialPage();
    canonicalizePageUrl();
    const params = new URLSearchParams(window.location.search);
    if (params.get("job")) state.selectedJobId = params.get("job");
    render();
    afterRender();
  });
}

async function handleClick(event) {
  const pageButton = event.target.closest("[data-page]");
  if (pageButton) {
    setPressedAction(buttonActionKey(pageButton));
    setPage(pageButton.dataset.page);
    return;
  }

  const actionNode = event.target.closest("[data-action]");
  if (!actionNode) return;
  const action = actionNode.dataset.action;
  const jobId = actionNode.dataset.jobId || "";
  if (busy) return;
  const actionKey = buttonActionKey(actionNode);
  setPressedAction(actionKey);

  if (action === "refresh-all") await refreshAll(actionKey);
  if (action === "refresh-jobs") await guarded(refreshJobs, "Failed to refresh jobs", actionKey);
  if (action === "check-runtime") await guarded(checkRuntime, "Environment check failed", actionKey);
  if (action === "select-algorithm") selectAlgorithm(actionNode.dataset.family);
  if (action === "clear-error") clearError();
  if (action === "set-data-source") setDataSource(actionNode.dataset.source);
  if (action === "open-dataset-modal") await askDatasetName();
  if (action === "new-capture") newCapture();
  if (action === "restart-upload-training") await guarded(restartUploadTraining, "Failed to restart upload training", actionKey);
  if (action === "refresh-flow-data") await guarded(() => refreshFlowData(true), "Failed to refresh staged data", actionKey);
  if (action === "delete-flow-data") await guarded(() => deleteCurrentFlowData(actionNode.dataset.stage), "Failed to delete staged data", actionKey);
  if (action === "upload-selected-photos") await guarded(() => uploadImages("files"), "Photo upload failed", actionKey);
  if (action === "upload-camera-frame") await guarded(() => uploadImages("camera"), "Camera upload failed", actionKey);
  if (action === "upload-images") await guarded(uploadImages, "Image upload failed", actionKey);
  if (action === "materialize-session") await guarded(materializeCurrentSession, "Session materialize failed", actionKey);
  if (action === "prepare-colmap") await guarded(prepareCurrentColmap, "COLMAP workspace prep failed", actionKey);
  if (action === "use-dataset") useDataset(actionNode.dataset.datasetId);
  if (action === "start-camera") await guarded(startCamera, "Failed to start camera", actionKey);
  if (action === "stop-camera") stopCamera();
  if (action === "toggle-stream") await guarded(toggleStream, "Auto upload failed", actionKey);
  if (action === "save-remote-config") saveRemoteConfig();
  if (action === "restore-remote-config") restoreRemoteConfig();
  if (action === "check-remote") await guarded(checkRemote, "Remote precheck failed", actionKey);
  if (action === "preview-command") await guarded(previewCommand, "Command preview failed", actionKey);
  if (action === "edit-command") editCommandPreview();
  if (action === "confirm-command-edit") confirmCommandPreviewEdit();
  if (action === "submit-remote") await guarded(submitRemote, "Remote job submission failed", actionKey);
  if (action === "select-job") selectJob(jobId);
  if (action === "clear-jobs") await guarded(() => clearJobRecords(actionNode.dataset.statuses), "Failed to clear jobs", actionKey);
  if (action === "delete-job") await guarded(() => deleteJobRecord(jobId), "Failed to remove job", actionKey);
  if (action === "load-log-reset") await guarded(() => loadLog(true), "Failed to load logs", actionKey);
  if (action === "cancel-job") await guarded(() => cancelSelectedJob(jobId), "Failed to cancel job", actionKey);
  if (action === "check-result-download") await guarded(() => checkAndDownloadResult(jobId), "Failed to check result download", actionKey);
  if (action === "repair-metrics") await guarded(() => repairMetrics(jobId), "Failed to repair metrics", actionKey);
  if (action === "redownload-result") await guarded(() => redownloadResult(jobId), "Failed to redownload result", actionKey);
  if (action === "open-result") openResult(jobId);
  if (action === "open-selected-result") openResult(state.selectedJobId);
  if (action === "rerun-job") rerunJob(jobId);
  if (action === "load-result") await guarded(loadQuickResult, "Failed to open result", actionKey);
  if (action === "load-ply") await guarded(loadQuickResult, "Failed to open result", actionKey);
  if (action === "discover-results") await guarded(discoverResults, "Failed to discover results", actionKey);
  if (action === "open-discovered-result") openDiscovered(actionNode.dataset.outputDir);
  if (action === "load-analysis") await guarded(loadAnalysisRows, "Failed to load metrics", actionKey);
  if (action === "export-analysis") await guarded(downloadAnalysisCsv, "Failed to export analysis CSV", actionKey);
  if (action === "set-render-mode") {
    state.renderMode = actionNode.dataset.mode || "ply";
    persistState();
    render();
    afterRender();
  }
}

function handleInput(event) {
  const node = event.target;
  if (node.matches("[data-bind]")) {
    updateBoundValue(node);
  }
  if (node.matches("[data-remote]")) {
    const key = node.dataset.remote;
    state.remoteConfig[key] = key === "port" ? Number(node.value || 22) : node.value;
    invalidateRemoteState();
    persistState();
  }
  if (node.matches("[data-training]")) {
    state.training[node.dataset.training] = Number(node.value);
    state.lastPreview = null;
    persistState();
  }
  if (node.matches("[data-command-draft]")) {
    state.commandDraft = node.value;
    persistState();
  }
}

function handleChange(event) {
  const node = event.target;
  if (node.matches("[data-bind]")) {
    updateBoundValue(node);
    if (node.dataset.bind === "algorithmFamily") {
      normalizeOperation();
      invalidateRemoteState();
      render();
      afterRender();
    }
    if (node.dataset.bind === "operation" || node.dataset.bind === "useExistingRemoteDataset" || node.dataset.bind === "autoColmap") {
      invalidateRemoteState();
      render();
      afterRender();
    }
    if (["jobsDatasetFilter", "jobsAlgorithmFilter", "jobsCompletedOnly", "jobsSortOrder"].includes(node.dataset.bind)) {
      render();
      afterRender();
    }
    if (node.dataset.bind === "selectedJobId") {
      state.logText = "";
      state.logCursor = 0;
      loadLog(true).catch(() => {});
      if (state.activePage === "analysis") {
        loadAnalysisRows()
          .then(() => {
            render();
            afterRender();
          })
          .catch(() => {});
      }
    }
  }
  if (node.id === "image-upload-input") {
    state.selectedFiles = Array.from(node.files || []);
    renderSelectedFiles(state.selectedFiles);
    persistState();
  }
}

function updateBoundValue(node) {
  const key = node.dataset.bind;
  if (node.type === "checkbox") {
    state[key] = node.checked;
  } else {
    state[key] = node.value;
  }
  if (["sessionId", "captureId", "datasetName", "selectedRemoteDatasetId", "remoteDatasetPath", "outputDir", "checkpointPath"].includes(key)) {
    state.lastPreview = null;
  }
  persistState();
}

function afterRender() {
  decorateButtonFeedback();
  const video = document.getElementById("local-stream");
  if (video && cameraStream) {
    video.srcObject = cameraStream;
  }
  const logNode = document.getElementById("log-tail");
  if (logNode) {
    logNode.scrollTop = logNode.scrollHeight;
  }
  if (state.activePage === "analysis") {
    drawMetricsChart();
  }
}

function selectAlgorithm(family) {
  state.algorithmFamily = family || state.algorithmFamily;
  normalizeOperation();
  invalidateRemoteState();
  persistState();
  render();
  afterRender();
}

function clearError() {
  state.lastError = null;
  persistState();
  render();
  afterRender();
}

function setDataSource(source) {
  const useExisting = source === "existing";
  if (state.useExistingRemoteDataset === useExisting) return;
  state.useExistingRemoteDataset = useExisting;
  state.lastPreview = null;
  invalidateRemoteState();
  persistState();
  render();
  afterRender();
}

function normalizeOperation() {
  const ops = supportedOperations();
  state.operation = ops.includes("train") ? "train" : (ops[0] || "train");
}

function invalidateRemoteState() {
  state.sshChecked = false;
  state.lastPreview = null;
  state.lastError = null;
  reattachFailures.clear();
}

async function refreshAll(actionKey = "") {
  await guarded(async () => {
    await Promise.allSettled([refreshHealth(), refreshAlgorithms(), refreshJobs(), discoverResults(false), refreshFlowData(false)]);
    if (state.algorithmFamily) {
      await checkRuntime(false);
    }
  }, "Failed to refresh workbench", actionKey);
}

async function refreshHealth() {
  try {
    await fetchHealth(state.apiBaseUrl);
    state.apiOnline = true;
  } catch (error) {
    state.apiOnline = false;
    throw error;
  }
}

async function refreshAlgorithms() {
  const data = await fetchAlgorithms(state.apiBaseUrl);
  algorithms = data.algorithms || [];
  validation = new Map((data.validation || []).map((item) => [item.family, item]));
  if (!state.algorithmFamily && algorithms[0]) {
    state.algorithmFamily = algorithms[0].family;
  }
  normalizeOperation();
}

async function checkRuntime(shouldRender = true) {
  const data = await fetchEnvironmentCheck(state.apiBaseUrl, state.algorithmFamily);
  environmentReport = data.report;
  if (state.algorithmFamily) {
    try {
      const adapterResult = await validateAdapter(state.apiBaseUrl, state.algorithmFamily);
      validation.set(state.algorithmFamily, adapterResult.validation);
    } catch {
      // Runtime checks should remain visible even if adapter validation fails.
    }
  }
  if (shouldRender) {
    render();
    afterRender();
  }
}

async function refreshJobs() {
  const data = await fetchJobs(state.apiBaseUrl);
  jobs = sortedJobsList(data.jobs || []);
  syncJobFilters();
  if (state.selectedJobId && !jobs.some((job) => job.id === state.selectedJobId)) {
    state.selectedJobId = "";
    state.logText = "";
    state.logCursor = 0;
  }
  if (!state.selectedJobId && jobs[0] && state.activePage !== "data") {
    state.selectedJobId = jobs[0].id;
  }
  await autoReattachDetachedJobs();
  persistState();
}

function syncFlowData(data) {
  state.flowData = data || null;
  const uploadedFrames = Number(data?.upload?.uploaded_frame_count || 0);
  state.uploadedCount = uploadedFrames;
  if (data?.capture_id && !state.captureId) {
    state.captureId = data.capture_id;
  }
}

async function refreshFlowData(shouldRender = false) {
  if (!state.sessionId) return null;
  const data = await fetchFlowData(state.apiBaseUrl, {
    sessionId: state.sessionId,
    captureId: state.captureId,
    family: state.algorithmFamily,
  });
  syncFlowData(data);
  if (shouldRender) {
    persistState();
    render();
    afterRender();
    showToast("Staged data refreshed.");
  }
  return data;
}

function remoteConfigReadyForReattach() {
  const remote = state.remoteConfig || {};
  return Boolean(
    String(remote.host || "").trim()
    && String(remote.username || "").trim()
    && String(remote.password || "").trim()
    && String(remote.repo_path || "").trim()
    && String(remote.workspace_root || "").trim()
    && String(remote.output_root || "").trim(),
  );
}

function needsRemoteReattach(job) {
  if (!job?.remote_detached || isTerminal(job.status)) return false;
  const monitorState = String(job.monitor_state || "").toLowerCase();
  const status = String(job.status || "").toLowerCase();
  return ["needs_remote_config", "disconnected"].includes(monitorState) || ["detached"].includes(status);
}

function reattachKey(job) {
  const remote = state.remoteConfig || {};
  return [
    job?.id || "",
    String(remote.host || "").trim(),
    String(remote.username || "").trim(),
  ].join(":");
}

function reattachFailureCount(job) {
  return reattachFailures.get(reattachKey(job)) || 0;
}

function reattachLimitReached(job) {
  return reattachFailureCount(job) >= MAX_REATTACH_ATTEMPTS;
}

function renderReattachStatus(job, className = "panel-copy") {
  if (reattachLimitReached(job)) {
    return `<p class="${className}">Auto reattach stopped after ${MAX_REATTACH_ATTEMPTS} failed attempts. Check SSH or remote tmux manually.</p>`;
  }
  return `<p class="${className}">Remote tmux monitor needs reattach with the saved remote config.</p>`;
}

async function autoReattachDetachedJobs() {
  if (!state.sshChecked) return;
  if (!remoteConfigReadyForReattach()) return;
  const candidates = jobs.filter(needsRemoteReattach);
  await Promise.all(candidates.map(async (job) => {
    const key = reattachKey(job);
    if (reattachInFlight.has(key) || reattachLimitReached(job)) return;
    const attempts = reattachFailureCount(job) + 1;
    reattachFailures.set(key, attempts);
    reattachInFlight.add(key);
    try {
      const data = await reattachRemoteJob(state.apiBaseUrl, job.id, state.remoteConfig);
      const updated = data.job;
      if (updated?.id) {
        jobs = jobs.map((item) => item.id === updated.id ? updated : item);
        showToast(`Reattached remote monitor: ${updated.id}`);
      }
    } catch {
      if (attempts >= MAX_REATTACH_ATTEMPTS) {
        showToast(`Auto reattach stopped after ${MAX_REATTACH_ATTEMPTS} failed attempts: ${job.id}`);
      }
    } finally {
      reattachInFlight.delete(key);
    }
  }));
}

async function discoverResults(shouldRender = true) {
  const data = await fetchDiscoveredResults(state.apiBaseUrl, { limit: 40, maxScanDirs: 2500 });
  discoveredResults = data.results || [];
  if (shouldRender) {
    render();
    afterRender();
  }
}

async function askDatasetName() {
  const value = await openInputModal({
    title: "Name Dataset",
    description: "Give this dataset a clear name. Remote datasets will keep the same name when possible for reuse and reruns.",
    defaultValue: datasetNameForPayload(),
    placeholder: "office-apr24-batch01",
  });
  if (value) {
    state.datasetName = value;
    state.lastPreview = null;
    persistState();
    render();
    afterRender();
  }
}

function newCapture() {
  ensureCaptureId(true);
  state.uploadedCount = 0;
  state.flowData = null;
  state.lastPreview = null;
  persistState();
  render();
  afterRender();
}

function resetClientStateForUploadTraining() {
  localStorage.removeItem(STATE_KEY);
  localStorage.removeItem(REMOTE_KEY);
  try {
    sessionStorage.removeItem(REMOTE_KEY);
  } catch {
    /* ignore */
  }
  const fresh = defaultState();
  Object.assign(state, fresh, {
    activePage: "data",
    sessionId: `session-${Date.now()}`,
    captureId: "",
    datasetName: "",
    algorithmFamily: "",
    operation: "train",
    selectedJobId: "",
    logText: "",
    logCursor: 0,
    flowData: null,
    lastError: null,
    lastPreview: null,
    remoteReport: null,
  });
  environmentReport = null;
  discoveredResults = [];
  reattachFailures.clear();
  reattachInFlight.clear();
  persistState();
}

async function restartUploadTraining() {
  const confirmed = await openResetFlowConfirmModal();
  if (!confirmed) return;
  stopCamera();
  const resetResult = await resetFlow(state.apiBaseUrl, {
    session_id: state.sessionId,
    capture_id: state.captureId,
    selected_job_id: state.selectedJobId,
    remote: { ...state.remoteConfig },
    reset_scope: "all",
    cancel_unfinished: true,
  });
  resetClientStateForUploadTraining();
  const url = new URL(window.location.href);
  url.searchParams.set("page", "data");
  url.searchParams.delete("job");
  window.history.pushState({}, "", url);
  await refreshAlgorithms();
  await refreshJobs();
  state.selectedJobId = "";
  persistState();
  render();
  afterRender();
  showToast("Current flow cleared. Ready for new upload training.");
  return resetResult;
}

function requireSshReadyForUpload() {
  if (!state.sshChecked) {
    throw new Error("Run Remote Precheck before uploading data.");
  }
}

async function deleteCurrentFlowData(stage) {
  const normalizedStage = String(stage || "").trim();
  const confirmed = await openDeleteFlowDataConfirmModal(normalizedStage);
  if (!confirmed) return null;
  const result = await deleteFlowData(state.apiBaseUrl, {
    session_id: state.sessionId,
    capture_id: state.captureId,
    family: state.algorithmFamily,
    stage: normalizedStage,
  });
  syncFlowData(result.data);
  state.lastPreview = null;
  if (normalizedStage === "capture" || normalizedStage === "upload") {
    state.captureId = "";
  }
  persistState();
  render();
  afterRender();
  showToast("Selected staged data cleared.");
  return result;
}

function parseStatuses(value) {
  const raw = String(value || "").trim();
  const finished = ["completed", "failed", "canceled", "partial_success", "training_success_render_failed", "training_success_metrics_failed", "training_success_postprocess_failed"];
  if (!raw) return finished;
  if (raw === "finished") return finished;
  return raw.split(",").map((item) => item.trim()).filter(Boolean);
}

async function clearJobRecords(statusesValue) {
  const statuses = parseStatuses(statusesValue);
  const confirmed = await openJobCleanupConfirmModal({
    mode: "clear",
    statuses,
  });
  if (!confirmed) return null;
  const result = await clearJobs(state.apiBaseUrl, { statuses });
  const removedIds = new Set((result.removed || []).map((item) => item.id));
  if (removedIds.has(state.selectedJobId)) {
    state.selectedJobId = "";
    state.logText = "";
    state.logCursor = 0;
  }
  await refreshJobs();
  render();
  afterRender();
  showToast(`Cleared ${result.removed_count || 0} job record(s). Logs were kept.`);
  return result;
}

async function deleteJobRecord(jobId) {
  const job = getJob(jobId);
  if (!job) throw new Error(`Unknown job: ${jobId}`);
  if (!isTerminal(job.status)) {
    throw new Error("Cancel running jobs before removing the record.");
  }
  const confirmed = await openJobCleanupConfirmModal({
    mode: "delete",
    job,
  });
  if (!confirmed) return null;
  const result = await deleteJob(state.apiBaseUrl, jobId);
  if (state.selectedJobId === jobId) {
    state.selectedJobId = "";
    state.logText = "";
    state.logCursor = 0;
  }
  await refreshJobs();
  render();
  afterRender();
  showToast("Job record removed. Logs were kept.");
  return result;
}

async function startCamera() {
  if (cameraStream) return;
  cameraStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
  afterRender();
}

function stopCamera(options = {}) {
  if (cameraStream) {
    cameraStream.getTracks().forEach((track) => track.stop());
    cameraStream = null;
  }
  if (streamTimer) {
    window.clearInterval(streamTimer);
    streamTimer = null;
  }
  if (!options.silent) {
    render();
    afterRender();
  }
}

async function toggleStream() {
  if (streamTimer) {
    window.clearInterval(streamTimer);
    streamTimer = null;
    render();
    afterRender();
    return;
  }
  await startCamera();
  await uploadImages();
  streamTimer = window.setInterval(() => {
    uploadImages().catch((error) => setError(error, "Auto upload failed"));
  }, 5000);
}

async function imageFileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

async function captureCameraFrame() {
  await startCamera();
  const video = document.getElementById("local-stream");
  if (video && (!video.videoWidth || !video.videoHeight)) {
    await new Promise((resolve) => {
      const timer = window.setTimeout(resolve, 900);
      video.addEventListener("loadedmetadata", () => {
        window.clearTimeout(timer);
        resolve();
      }, { once: true });
    });
  }
  const canvas = document.createElement("canvas");
  canvas.width = video?.videoWidth || 1280;
  canvas.height = video?.videoHeight || 720;
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  return {
    imageData: canvas.toDataURL("image/png"),
    filename: `frame_${Date.now()}.png`,
  };
}

async function uploadImages(mode = "auto") {
  requireSshReadyForUpload();
  ensureCaptureId();
  const files = mode === "camera" ? [] : state.selectedFiles || [];
  const uploadMode = String(mode || "auto");
  if (uploadMode === "files" && !files.length) {
    throw new Error("Select one or more photos before uploading.");
  }
  if (!state.datasetName) {
    await askDatasetName();
  }
  const frames = uploadMode !== "camera" && files.length
    ? await Promise.all(files.map(async (file) => ({
      imageData: await imageFileToDataUrl(file),
      filename: file.name || `upload_${Date.now()}.png`,
    })))
    : [await captureCameraFrame()];

  let lastResult = null;
  for (let index = 0; index < frames.length; index += 1) {
    const frame = frames[index];
    lastResult = await streamFrame(state.apiBaseUrl, {
      session_id: state.sessionId,
      capture_id: state.captureId,
      algorithm_family: "",
      image_data: frame.imageData,
      filename: frame.filename,
    });
    if (lastResult.capture_id) state.captureId = lastResult.capture_id;
  }
  state.uploadedCount += frames.length;
  state.useExistingRemoteDataset = false;
  state.lastPreview = null;
  state.selectedFiles = [];
  await refreshFlowData(false);
  showToast(`Uploaded ${frames.length} image(s) to ${state.sessionId}/${state.captureId}`);
}

function renderSelectedFiles(files) {
  const grid = document.getElementById("upload-preview-grid");
  if (!grid) return;
  const safeFiles = Array.from(files || []);
  if (!safeFiles.length) {
    grid.innerHTML = `<p class="panel-copy">No images selected. Upload will use the current camera frame.</p>`;
    return;
  }
  grid.innerHTML = safeFiles.slice(0, 12).map((file) => {
    const url = URL.createObjectURL(file);
    window.setTimeout(() => URL.revokeObjectURL(url), 20_000);
    return `<img src="${url}" alt="${escapeHtml(file.name)}" title="${escapeHtml(file.name)}" />`;
  }).join("");
}

async function materializeCurrentSession() {
  requireSshReadyForUpload();
  if (!state.datasetName) await askDatasetName();
  const data = await materializeSession(state.apiBaseUrl, {
    session_id: state.sessionId,
    capture_id: state.captureId,
    dataset_name: datasetNameForPayload(),
    title: datasetNameForPayload(),
  });
  await refreshFlowData(false);
  showToast(`Materialize completed: ${data.result?.dataset_root || "-"}`);
}

async function prepareCurrentColmap() {
  requireSshReadyForUpload();
  if (!state.datasetName) await askDatasetName();
  const data = await prepareColmapWorkspace(state.apiBaseUrl, {
    session_id: state.sessionId,
    capture_id: state.captureId,
    dataset_name: datasetNameForPayload(),
    algorithm_family: state.algorithmFamily,
  });
  await refreshFlowData(false);
  showToast(`COLMAP workspace ready: ${data.result?.workspace_root || "-"}`);
}

function useDataset(datasetId) {
  state.selectedRemoteDatasetId = datasetId || "";
  state.useExistingRemoteDataset = Boolean(datasetId);
  state.lastPreview = null;
  persistState();
  render();
  afterRender();
}

function saveRemoteConfig() {
  const { password, ...nonSecret } = state.remoteConfig;
  localStorage.setItem(REMOTE_KEY, JSON.stringify(nonSecret));
  try {
    sessionStorage.setItem(REMOTE_KEY, JSON.stringify({ password: password || "" }));
  } catch {
    /* sessionStorage unavailable (e.g. some private modes); password stays in memory only */
  }
  showToast("Remote config saved. The password is kept for this browser session only.");
}

function restoreRemoteConfig() {
  const saved = JSON.parse(localStorage.getItem(REMOTE_KEY) || "{}");
  const sessionSaved = JSON.parse(sessionStorage.getItem(REMOTE_KEY) || "{}");
  state.remoteConfig = { ...DEFAULT_REMOTE, ...saved, ...sessionSaved };
  invalidateRemoteState();
  persistState();
  render();
  afterRender();
  showToast("Restored the last remote config.");
}

async function checkRemote() {
  if (!state.algorithmFamily) throw new Error("Select an algorithm first.");
  const data = await remoteCheck(state.apiBaseUrl, {
    remote: state.remoteConfig,
    timeout_seconds: 20,
    algorithm_family: state.algorithmFamily,
    use_existing_remote_dataset: Boolean(state.useExistingRemoteDataset),
    check_colmap_required: effectiveAutoColmap(),
  });
  state.remoteReport = data.result;
  state.sshChecked = true;
  state.lastPreview = null;
  reattachFailures.clear();
  await refreshFlowData(false);
  showToast("Remote precheck passed.");
}

async function previewCommand() {
  validateDatasetReadiness();
  if (!state.datasetName && !state.useExistingRemoteDataset) {
    await askDatasetName();
  }
  const payload = buildRunPayload();
  const data = await previewRemoteAlgorithm(state.apiBaseUrl, payload);
  state.lastPreview = data.preview;
  state.commandReviewEditing = false;
  state.commandDraft = "";
  state.outputDir = data.preview?.local_output_dir || state.outputDir;
  showToast("Command preview generated.");
}

function editCommandPreview() {
  if (!state.lastPreview) return;
  state.commandDraft = state.lastPreview.command_override || state.lastPreview.remote_command || state.lastPreview.shell_command || "";
  state.commandReviewEditing = true;
  persistState();
  render();
  afterRender();
}

function confirmCommandPreviewEdit() {
  if (!state.lastPreview) return;
  const editor = document.querySelector("[data-command-draft]");
  const command = String(editor?.value ?? state.commandDraft ?? "").trim();
  if (!command) {
    setError(new Error("Command cannot be empty."), "Command edit failed");
    return;
  }
  state.lastPreview = {
    ...state.lastPreview,
    command_override: command,
    remote_command: command,
  };
  state.commandDraft = command;
  state.commandReviewEditing = false;
  persistState();
  showToast("Command updated.");
  render();
  afterRender();
}

function validateDatasetReadiness() {
  if (!state.useExistingRemoteDataset && stagedUploadFrameCount() <= 0) {
    throw new Error("Upload images from the Data page before submitting remote training.");
  }
  if (state.useExistingRemoteDataset) {
    if (!state.selectedRemoteDatasetId && !state.remoteDatasetPath) {
      throw new Error("Select an existing remote dataset or enter a custom remote data path first.");
    }
  }
}

async function submitRemote() {
  validateDatasetReadiness();
  if (!state.sshChecked) throw new Error("Complete the SSH / remote environment precheck first.");
  if (!state.lastPreview) await previewCommand();
  const confirmed = await openConfirmModal(state.lastPreview);
  if (!confirmed) return;

  const pathConfirmation = {
    ...state.lastPreview.path_confirmation,
    confirmed: true,
    confirmed_at: new Date().toISOString(),
  };
  const data = await runRemoteAlgorithm(state.apiBaseUrl, buildRunPayload({
    output_dir: state.lastPreview.local_output_dir || outputDirForPayload(),
    path_confirmation: pathConfirmation,
    command_override: state.lastPreview.command_override || "",
  }));
  if (data.job?.id) {
    state.selectedJobId = data.job.id;
    state.logCursor = 0;
    state.logText = "";
    await refreshJobs();
    await loadLog(true);
  }
  showToast(`Remote job submitted: ${data.job?.id || "-"}`);
}

function selectJob(jobId) {
  state.selectedJobId = jobId;
  state.logCursor = 0;
  state.logText = "";
  persistState();
  render();
  afterRender();
  loadLog(true).catch(() => {});
}

let loadLogInFlight = false;

async function loadLog(reset = false) {
  if (!state.selectedJobId) return;
  // Two overlapping deltas would both read the same cursor and append the log twice.
  if (loadLogInFlight) return;
  loadLogInFlight = true;
  try {
    await appendLogDelta(reset);
  } finally {
    loadLogInFlight = false;
  }
}

async function appendLogDelta(reset = false) {
  if (reset) {
    state.logCursor = 0;
    state.logText = "";
  }
  const data = await fetchJobLogDelta(state.apiBaseUrl, state.selectedJobId, state.logCursor);
  state.logCursor = Number(data.cursor || state.logCursor || 0);
  if (data.log_text) {
    state.logText = `${state.logText}${data.log_text}`.slice(-MAX_LOG_CHARS);
  }
  persistState();
  const logNode = document.getElementById("log-tail");
  if (logNode) {
    logNode.textContent = state.logText || "Waiting for log output...";
    logNode.scrollTop = logNode.scrollHeight;
  }
}

async function cancelSelectedJob(jobId) {
  const id = jobId || state.selectedJobId;
  if (!id) return;
  await cancelJob(state.apiBaseUrl, id, state.remoteConfig);
  await refreshJobs();
  await loadLog(false);
  showToast("Cancel requested. Logs and generated outputs will be retained.");
}

function upsertJob(updated) {
  if (!updated?.id) return;
  const existing = jobs.some((job) => job.id === updated.id);
  jobs = existing
    ? jobs.map((job) => job.id === updated.id ? updated : job)
    : [updated, ...jobs];
  jobs = sortedJobsList(jobs);
  syncJobFilters();
}

async function checkAndDownloadResult(jobId) {
  const id = jobId || state.selectedJobId;
  const job = getJob(id);
  if (!job) throw new Error("Select a completed remote job first.");
  if (!canDownloadCompletedResult(job)) throw new Error("Only completed jobs can download results.");
  const match = resultDownloadServerMatch(job);
  if (!match.ok) {
    throw new Error(`${match.message} Expected ${formatRemoteIdentity(match.expected)}; current ${formatRemoteIdentity(match.current)}.`);
  }
  if (!remoteConfigReadyForReattach()) {
    throw new Error("Remote credentials are required. Fill the Remote Config fields before downloading results.");
  }

  const checkData = await checkJobResultDownload(state.apiBaseUrl, id, state.remoteConfig);
  upsertJob(checkData.job);
  const check = checkData.check || {};
  if (check.complete) {
    persistState();
    render();
    afterRender();
    showToast("Local result is already complete. No duplicate download needed.");
    return;
  }

  const confirmed = await openResultDownloadConfirmModal(check);
  if (!confirmed) {
    await refreshJobs();
    render();
    afterRender();
    return;
  }

  const downloadData = await downloadJobResult(state.apiBaseUrl, id, state.remoteConfig);
  upsertJob(downloadData.job);
  await refreshJobs();
  persistState();
  render();
  afterRender();
  if (downloadData.already_running) {
    showToast("Result download is already running.");
  } else if (downloadData.complete) {
    showToast("Local result is already complete. No duplicate download needed.");
  } else {
    showToast("Result download started.");
  }
}

async function repairMetrics(jobId) {
  const id = jobId || state.selectedJobId;
  const job = getJob(id);
  if (!job) throw new Error("Select a partial-success job first.");
  if (!job.can_repair_metrics) throw new Error("This job is not eligible for metrics repair.");
  if (!remoteConfigReadyForReattach()) {
    throw new Error("Remote credentials are required. Fill the Remote Config fields before repairing metrics.");
  }
  const data = await repairJobMetrics(state.apiBaseUrl, id, state.remoteConfig);
  upsertJob(data.job);
  await refreshJobs();
  render();
  afterRender();
  showToast(data.already_running ? "Metrics repair is already running." : "Metrics repair started.");
}

async function redownloadResult(jobId) {
  const id = jobId || state.selectedJobId;
  const job = getJob(id);
  if (!job) throw new Error("Select a completed remote job first.");
  if (!remoteConfigReadyForReattach()) {
    throw new Error("Remote credentials are required. Fill the Remote Config fields before redownloading results.");
  }
  const data = await redownloadJobResult(state.apiBaseUrl, id, state.remoteConfig);
  upsertJob(data.job);
  await refreshJobs();
  render();
  afterRender();
  showToast(data.already_running ? "Result download is already running." : "Result redownload started.");
}

function openResult(jobId) {
  if (!jobId) return;
  state.selectedJobId = jobId;
  state.activePage = "result";
  persistState();
  const url = new URL(window.location.href);
  url.searchParams.set("page", "result");
  url.searchParams.set("job", jobId);
  window.history.pushState({}, "", url);
  render();
  afterRender();
}

function rerunJob(jobId) {
  const job = getJob(jobId);
  if (!job) return;
  state.algorithmFamily = job.algorithm_family || state.algorithmFamily;
  state.operation = job.requested_operation || "train";
  state.sessionId = job.session_id || state.sessionId;
  state.captureId = job.capture_id || state.captureId;
  state.datasetName = job.dataset_name || state.datasetName;
  state.useExistingRemoteDataset = Boolean(job.use_existing_remote_dataset);
  state.selectedRemoteDatasetId = job.remote_dataset_id || "";
  state.remoteDatasetPath = job.remote_dataset_path || "";
  state.outputDir = "";
  state.lastPreview = null;
  setPage("algorithm");
}

async function loadQuickResult() {
  if (!state.quickPlyPath) throw new Error("Enter a result folder or file path.");
  const data = await loadResultPath(state.apiBaseUrl, {
    path: state.quickPlyPath,
  });
  state.loadedPly = data;
  if (!data.ok) throw new Error(data.reason || data.error || "Result open failed");
  showToast("Result opened.");
}

function openDiscovered(outputDir) {
  const item = discoveredResults.find((result) => result.output_dir === outputDir);
  if (!item) return;
  state.loadedPly = { ok: true, ...item };
  state.activePage = "browser";
  persistState();
  render();
  afterRender();
}

async function loadAnalysisRows() {
  const job = getJob();
  if (!job) throw new Error("Select a job.");
  try {
    const fresh = await fetchJob(state.apiBaseUrl, job.id);
    if (fresh?.id) {
      const index = jobs.findIndex((item) => item.id === fresh.id);
      if (index >= 0) jobs[index] = fresh;
      else jobs.unshift(fresh);
    }
  } catch {
    // Metrics CSV can still be loaded when the job detail endpoint is temporarily unavailable.
  }
  try {
    const url = buildJobMetricsCsvUrl(state.apiBaseUrl, job.id);
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    state.metricsRows = parseCsv(await response.text());
  } catch {
    const data = await fetchJobLogs(state.apiBaseUrl, job.id, { page: 1, pageSize: 200, tailLines: 80 });
    state.metricsRows = data.metrics_page?.rows || [];
  }
  persistState();
}

async function downloadAnalysisCsv() {
  const jobIds = sortedJobsList(jobs).map((job) => job.id).filter(Boolean);
  if (!jobIds.length) throw new Error("No jobs to export.");
  const blob = await exportAnalysisCsv(state.apiBaseUrl, jobIds);
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "analysis-metrics.csv";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function parseCsv(text) {
  const lines = String(text || "").trim().split(/\r?\n/);
  if (lines.length < 2) return [];
  const headers = lines[0].split(",").map((item) => item.trim());
  return lines.slice(1).map((line) => {
    const cells = line.split(",");
    return Object.fromEntries(headers.map((header, index) => [header, cells[index] || ""]));
  });
}

function drawMetricsChart() {
  const canvas = document.getElementById("metrics-chart");
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(640, Math.floor(rect.width * dpr));
  canvas.height = Math.floor(260 * dpr);
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, rect.width, 260);
  ctx.fillStyle = "#030913";
  ctx.fillRect(0, 0, rect.width, 260);
  const rows = state.metricsRows
    .map((row) => ({
      row,
      values: Object.fromEntries(ANALYSIS_METRICS.map((metric) => [metric.key, extractAnalysisMetric(row, metric.key).value])),
    }))
    .filter((item) => ANALYSIS_METRICS.some((metric) => Number.isFinite(Number(item.values[metric.key]))));
  if (!rows.length) {
    ctx.fillStyle = "#8ea4c5";
    ctx.fillText("No drawable metrics yet", 24, 38);
    return;
  }
  const series = [
    { key: "psnr", label: "PSNR(dB)", color: "#2f7dff" },
    { key: "ssim", label: "SSIM", color: "#ffb44c" },
    { key: "lpips", label: "LPIPS", color: "#43c58b" },
    { key: "sizeMb", label: "Size(MB)", color: "#ff5570" },
  ];
  ctx.strokeStyle = "rgba(142,164,197,0.2)";
  for (let y = 40; y < 230; y += 38) {
    ctx.beginPath();
    ctx.moveTo(42, y);
    ctx.lineTo(rect.width - 20, y);
    ctx.stroke();
  }
  series.forEach((serie, serieIndex) => {
    const values = rows.map((item) => Number(item.values[serie.key])).filter((value) => Number.isFinite(value));
    if (!values.length) return;
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    ctx.strokeStyle = serie.color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    rows.forEach((item, index) => {
      const value = Number(item.values[serie.key]);
      if (!Number.isFinite(value)) return;
      const x = 42 + (index / Math.max(1, rows.length - 1)) * (rect.width - 70);
      const y = 220 - ((value - min) / span) * 170;
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.fillStyle = serie.color;
    ctx.fillText(serie.label, 48 + serieIndex * 112, 24);
  });
}

function saveRemoteField(key, value) {
  state.remoteConfig[key] = key === "port" ? Number(value || 22) : value;
  invalidateRemoteState();
  persistState();
}

function openInputModal({ title, description, defaultValue, placeholder }) {
  const layer = document.getElementById("modal-layer");
  layer.hidden = false;
  layer.innerHTML = `
    <div class="modal">
      <div class="panel-head"><h2>${escapeHtml(title)}</h2></div>
      <div class="modal-body grid">
        <p class="panel-copy">${escapeHtml(description)}</p>
        <label>Name
          <input id="modal-input" value="${escapeHtml(defaultValue)}" placeholder="${escapeHtml(placeholder)}" />
        </label>
      </div>
      <div class="modal-footer">
        <button data-modal-cancel type="button">Cancel</button>
        <button class="primary" data-modal-ok type="button">Confirm</button>
      </div>
    </div>
  `;
  return new Promise((resolve) => {
    const input = document.getElementById("modal-input");
    input.focus();
    layer.querySelector("[data-modal-cancel]").addEventListener("click", () => {
      layer.hidden = true;
      resolve("");
    });
    layer.querySelector("[data-modal-ok]").addEventListener("click", () => {
      const value = input.value.trim();
      layer.hidden = true;
      resolve(value);
    });
  });
}

function openConfirmModal(preview) {
  const layer = document.getElementById("modal-layer");
  const reviewCommand = preview.command_override || preview.remote_command || preview.shell_command || "";
  layer.hidden = false;
  layer.innerHTML = `
    <div class="modal">
      <div class="panel-head"><h2>Pre-submit Review</h2><span class="badge warn">Path and command review</span></div>
      <div class="modal-body grid">
        <p>Review the paths and remote command below. Confirming will submit the remote training job.</p>
        <div class="command-box grid">
          <p class="panel-copy">Dataset: ${escapeHtml(preview.dataset_name || datasetNameForPayload())}</p>
          <p class="panel-copy">Algorithm: ${escapeHtml(preview.algorithm_family)}</p>
          <p class="panel-copy">Local Output: ${escapeHtml(preview.local_output_dir)}</p>
          <p class="panel-copy">Remote Workspace: ${escapeHtml(preview.remote_workspace)}</p>
          <p class="panel-copy">Remote Output: ${escapeHtml(preview.remote_output_dir)}</p>
          <pre>${escapeHtml(reviewCommand)}</pre>
        </div>
      </div>
      <div class="modal-footer">
        <button data-modal-cancel type="button">Cancel</button>
        <button class="primary" data-modal-ok type="button">Confirm and Submit</button>
      </div>
    </div>
  `;
  return new Promise((resolve) => {
    layer.querySelector("[data-modal-cancel]").addEventListener("click", () => {
      layer.hidden = true;
      resolve(false);
    });
    layer.querySelector("[data-modal-ok]").addEventListener("click", () => {
      layer.hidden = true;
      resolve(true);
    });
  });
}

function openResultDownloadConfirmModal(check = {}) {
  const layer = document.getElementById("modal-layer");
  const sampleFiles = Array.isArray(check.sample_files) ? check.sample_files.slice(0, 8) : [];
  const skippedCheckpointSamples = Array.isArray(check.skipped_checkpoint_samples)
    ? check.skipped_checkpoint_samples.slice(0, 8)
    : [];
  layer.hidden = false;
  layer.innerHTML = `
    <div class="modal">
      <div class="panel-head"><h2>Download Result Files</h2><span class="badge warn">Incomplete local result</span></div>
      <div class="modal-body grid">
        <p>Local result files are incomplete. Confirming will download only missing or size-mismatched result files from the remote output directory. Checkpoints and intermediate training artifacts are skipped.</p>
        <div class="command-box grid">
          <p class="panel-copy">Remote Output: ${escapeHtml(check.remote_output_dir || "-")}</p>
          <p class="panel-copy">Local Output: ${escapeHtml(check.local_output_dir || "-")}</p>
          <p class="panel-copy">To download: ${escapeHtml(check.total_files || 0)} file(s), ${escapeHtml(formatBytes(check.total_bytes || 0))}</p>
          <p class="panel-copy">Missing: ${escapeHtml(check.missing_files || 0)} · Size mismatch: ${escapeHtml(check.mismatched_files || 0)} · Skipped: ${escapeHtml(check.skipped_files || 0)}</p>
          <p class="panel-copy">Skipped checkpoints: ${escapeHtml(check.skipped_checkpoint_files || 0)} · ${escapeHtml(formatBytes(check.skipped_checkpoint_bytes || 0))}</p>
          ${sampleFiles.length ? `<pre>${escapeHtml(sampleFiles.join("\n"))}</pre>` : ""}
          ${skippedCheckpointSamples.length ? `<pre>${escapeHtml(`Skipped checkpoints:\n${skippedCheckpointSamples.join("\n")}`)}</pre>` : ""}
        </div>
      </div>
      <div class="modal-footer">
        <button data-modal-cancel type="button">Cancel</button>
        <button class="primary" data-modal-ok type="button">Download Missing Files</button>
      </div>
    </div>
  `;
  return new Promise((resolve) => {
    layer.querySelector("[data-modal-cancel]").addEventListener("click", () => {
      layer.hidden = true;
      resolve(false);
    });
    layer.querySelector("[data-modal-ok]").addEventListener("click", () => {
      layer.hidden = true;
      resolve(true);
    });
  });
}

function openResetFlowConfirmModal() {
  const layer = document.getElementById("modal-layer");
  layer.hidden = false;
  layer.innerHTML = `
    <div class="modal">
      <div class="panel-head"><h2>Restart Upload Training</h2><span class="badge warn">Reset current flow</span></div>
      <div class="modal-body grid">
        <p>This will cancel unfinished jobs for the current flow, clear current upload/training staging data, and reset all local configuration before starting a new upload training session.</p>
        <div class="command-box grid">
          <p class="panel-copy">Session: ${escapeHtml(state.sessionId || "-")}</p>
          <p class="panel-copy">Capture: ${escapeHtml(state.captureId || "-")}</p>
          <p class="panel-copy">Selected job: ${escapeHtml(state.selectedJobId || "-")}</p>
        </div>
      </div>
      <div class="modal-footer">
        <button data-modal-cancel type="button">Cancel</button>
        <button class="danger" data-modal-ok type="button">Restart Upload Training</button>
      </div>
    </div>
  `;
  return new Promise((resolve) => {
    layer.querySelector("[data-modal-cancel]").addEventListener("click", () => {
      layer.hidden = true;
      resolve(false);
    });
    layer.querySelector("[data-modal-ok]").addEventListener("click", () => {
      layer.hidden = true;
      resolve(true);
    });
  });
}

function openDeleteFlowDataConfirmModal(stage) {
  const labels = {
    capture: "Capture",
    upload: "Upload",
    session_prep: "Session Prep",
  };
  const label = labels[stage] || "Staged Data";
  const layer = document.getElementById("modal-layer");
  layer.hidden = false;
  layer.innerHTML = `
    <div class="modal">
      <div class="panel-head"><h2>Delete ${escapeHtml(label)}</h2><span class="badge warn">Current session only</span></div>
      <div class="modal-body grid">
        <p>This clears the selected staged data for the current upload training flow. Job logs and completed outputs are kept.</p>
        <div class="command-box grid">
          <p class="panel-copy">Session: ${escapeHtml(state.sessionId || "-")}</p>
          <p class="panel-copy">Capture: ${escapeHtml(state.captureId || "-")}</p>
          <p class="panel-copy">Stage: ${escapeHtml(label)}</p>
        </div>
      </div>
      <div class="modal-footer">
        <button data-modal-cancel type="button">Cancel</button>
        <button class="danger" data-modal-ok type="button">Delete ${escapeHtml(label)}</button>
      </div>
    </div>
  `;
  return new Promise((resolve) => {
    layer.querySelector("[data-modal-cancel]").addEventListener("click", () => {
      layer.hidden = true;
      resolve(false);
    });
    layer.querySelector("[data-modal-ok]").addEventListener("click", () => {
      layer.hidden = true;
      resolve(true);
    });
  });
}

function openJobCleanupConfirmModal({ mode, statuses = [], job = null }) {
  const layer = document.getElementById("modal-layer");
  const isDelete = mode === "delete";
  const title = isDelete ? "Remove Job Record" : "Clear Job Records";
  const statusText = isDelete
    ? `${job?.id || "-"} · ${job?.status || "-"}`
    : statuses.join(", ");
  layer.hidden = false;
  layer.innerHTML = `
    <div class="modal">
      <div class="panel-head"><h2>${escapeHtml(title)}</h2><span class="badge warn">Records only</span></div>
      <div class="modal-body grid">
        <p>This removes job records from the page and prevents them from reappearing after API restart. Runtime logs, metrics files, and training outputs are kept.</p>
        <div class="command-box grid">
          <p class="panel-copy">${isDelete ? "Job" : "Statuses"}: ${escapeHtml(statusText || "-")}</p>
          <p class="panel-copy">Running jobs are not removed. Cancel them first if needed.</p>
        </div>
      </div>
      <div class="modal-footer">
        <button data-modal-cancel type="button">Cancel</button>
        <button class="danger" data-modal-ok type="button">${isDelete ? "Remove" : "Clear"}</button>
      </div>
    </div>
  `;
  return new Promise((resolve) => {
    layer.querySelector("[data-modal-cancel]").addEventListener("click", () => {
      layer.hidden = true;
      resolve(false);
    });
    layer.querySelector("[data-modal-ok]").addEventListener("click", () => {
      layer.hidden = true;
      resolve(true);
    });
  });
}

let pollInFlight = false;

async function poll() {
  if (pollInFlight || busy) return;
  pollInFlight = true;
  try {
    if (shouldPausePollingForResultViewer()) return;
    await refreshJobs();
    if (state.selectedJobId) {
      await loadLog(false);
    }
    const editing = document.activeElement?.matches?.("input, textarea, select");
    if (!editing && ["overview", "algorithm", "result", "analysis"].includes(state.activePage)) {
      if (activeResultViewerIsStable()) return;
      const scrollY = window.scrollY;
      const workspaceFrame = document.querySelector('.workspace-frame');
      const frameScrollY = workspaceFrame ? workspaceFrame.scrollTop : 0;

      render();
      afterRender();

      window.scrollTo(0, scrollY);
      const newWorkspaceFrame = document.querySelector('.workspace-frame');
      if (newWorkspaceFrame && frameScrollY > 0) {
        newWorkspaceFrame.scrollTop = frameScrollY;
      }
    }
  } catch {
    // Keep the UI usable when the backend is temporarily offline.
  } finally {
    pollInFlight = false;
  }
}

async function bootstrap() {
  bindStaticEvents();
  canonicalizePageUrl();
  render();
  afterRender();
  await refreshAll();
  render();
  afterRender();
  window.setInterval(poll, 5000);
}

bootstrap().catch((error) => setError(error, "Workbench startup failed"));
