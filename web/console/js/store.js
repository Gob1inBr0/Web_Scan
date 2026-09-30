// Global reactive store: connection profiles, jobs polling, notifications,
// toasts, and the hash router. One singleton imported by every page.

import { getHealth, getJobs } from "./api.js";
import { t } from "./i18n.js";

const { reactive, watch } = Vue;

/* ---------- persisted preferences ---------- */

const PROFILES_KEY = "webscan-console-profiles-v1";
const ACTIVE_KEY = "webscan-console-active-profile-v1";
const NAMES_KEY = "webscan-console-run-names-v1";
const PREFS_KEY = "webscan-console-prefs-v1";

function loadJson(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function saveJson(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* quota */ }
}

/* ---------- ssh session password storage (never persisted to disk/localStorage) ---------- */

const SESSION_KEY = "webscan-console-session-passwords-v1";

export const sessionPasswords = {
  get(profileId) {
    try {
      const map = JSON.parse(sessionStorage.getItem(SESSION_KEY) || "{}");
      return String(map[profileId] || "");
    } catch { return ""; }
  },
  set(profileId, password) {
    try {
      const map = JSON.parse(sessionStorage.getItem(SESSION_KEY) || "{}");
      map[profileId] = password;
      sessionStorage.setItem(SESSION_KEY, JSON.stringify(map));
    } catch { /* private mode: password stays in memory only */ }
  },
  clear(profileId) {
    try {
      const map = JSON.parse(sessionStorage.getItem(SESSION_KEY) || "{}");
      delete map[profileId];
      sessionStorage.setItem(SESSION_KEY, JSON.stringify(map));
    } catch { /* ignore */ }
  },
};

/* ---------- store ---------- */

export const store = reactive({
  route: parseHash(),
  apiOnline: null,

  profiles: loadJson(PROFILES_KEY, []),
  activeProfileId: loadJson(ACTIVE_KEY, ""),
  locale: (() => {
    try { return localStorage.getItem("webscan-console-locale-v1") === "zh" ? "zh" : "en"; }
    catch { return "en"; }
  })(),
  prefs: Object.assign(
    { notifyOnDone: true, pollIntervalSec: 5 },
    loadJson(PREFS_KEY, {}),
  ),

  jobs: [],
  jobsLoaded: false,
  pollInFlight: false,
  unseenDone: 0,

  runNames: loadJson(NAMES_KEY, {}),
  toasts: [],
  toastSeq: 0,
});

export function persistProfiles() {
  saveJson(PROFILES_KEY, store.profiles);
  saveJson(ACTIVE_KEY, store.activeProfileId);
}

export function persistPrefs() {
  saveJson(PREFS_KEY, store.prefs);
}

export function setRunName(jobId, name) {
  if (!jobId) return;
  store.runNames[jobId] = name;
  saveJson(NAMES_KEY, store.runNames);
}

export function runName(job) {
  if (!job) return "";
  if (store.runNames[job.id]) return store.runNames[job.id];
  const base = String(job.dataset_name || job.dataset || "").trim();
  if (!base || base.includes("/")) {
    // Old records sometimes carry a server path as the dataset name — show the leaf.
    const leaf = base.split("/").filter(Boolean).pop();
    return leaf || (job.id ? job.id.slice(0, 8) : "");
  }
  return base;
}

/* ---------- connection profiles ---------- */

export function defaultProfile() {
  return {
    id: "p-" + Math.random().toString(36).slice(2, 10),
    label: "",
    host: "",
    port: 22,
    username: "root",
    repo_path: "",
    workspace_root: "/tmp/web_scan/workspaces",
    output_root: "/tmp/web_scan/outputs",
    python: "python3",
    activate_cmd: "",
    key_path: "",
  };
}

export function activeProfile() {
  return store.profiles.find((p) => p.id === store.activeProfileId) || null;
}

/** Remote config payload for API calls (includes session-only password). */
export function remoteConfig() {
  const profile = activeProfile();
  if (!profile) return {};
  return {
    host: profile.host,
    port: Number(profile.port) || 22,
    username: profile.username,
    password: sessionPasswords.get(profile.id),
    key_path: String(profile.key_path || "").trim(),
    repo_path: profile.repo_path,
    workspace_root: profile.workspace_root,
    output_root: profile.output_root,
    python: profile.python,
    activate_cmd: profile.activate_cmd,
  };
}

/** Auth ready: a session password OR a configured private key path. */
export function hasPassword() {
  const profile = activeProfile();
  return Boolean(profile && (sessionPasswords.get(profile.id) || String(profile.key_path || "").trim()));
}

/* ---------- routing ---------- */

function parseHash() {
  const raw = (location.hash || "#/dashboard").replace(/^#\/?/, "");
  const [page, param] = raw.split("/");
  return { page: page || "dashboard", param: param ? decodeURIComponent(param) : "" };
}

export function navigate(page, param = "") {
  location.hash = "#/" + page + (param ? "/" + encodeURIComponent(param) : "");
}

window.addEventListener("hashchange", () => {
  store.route = parseHash();
});

/* ---------- toasts ---------- */

export function toast(message, kind = "info", timeoutMs = 4200) {
  const id = ++store.toastSeq;
  store.toasts.push({ id, message, kind });
  setTimeout(() => {
    const index = store.toasts.findIndex((t) => t.id === id);
    if (index >= 0) store.toasts.splice(index, 1);
  }, timeoutMs);
}

/* ---------- job polling + notifications ---------- */

const TERMINAL = new Set(["completed", "failed", "canceled", "partial_success",
  "training_success_render_failed", "training_success_metrics_failed", "training_success_postprocess_failed"]);

export function isTerminal(job) {
  return TERMINAL.has(String(job?.status || "").toLowerCase());
}

const knownStatus = new Map();

function announceTransitions(jobs) {
  for (const job of jobs) {
    const prev = knownStatus.get(job.id);
    knownStatus.set(job.id, job.status);
    if (prev === undefined || prev === job.status) continue;
    const wasRunning = !TERMINAL.has(prev);
    const nowDone = isTerminal(job);
    if (wasRunning && nowDone) {
      const ok = job.status === "completed" || String(job.status).startsWith("training_success");
      const name = runName(job);
      store.unseenDone += 1;
      if (store.prefs.notifyOnDone && typeof Notification !== "undefined" && Notification.permission === "granted") {
        try {
          new Notification(ok ? t("notify.finished") : t("notify.failed"), {
            body: `${name} · ${job.algorithm_family || ""} · ${job.status}`,
            tag: job.id,
          });
        } catch { /* notification failed silently */ }
      }
      toast(
        ok ? `${t("notify.finished")}: ${name}` : `${t("notify.failed")}: ${name} (${job.status})`,
        ok ? "success" : "error",
        8000,
      );
    }
  }
}

let pollTimer = null;

export async function pollOnce() {
  if (store.pollInFlight) return;
  store.pollInFlight = true;
  try {
    const data = await getJobs();
    store.jobs = data.jobs || [];
    store.jobsLoaded = true;
    store.apiOnline = true;
    announceTransitions(store.jobs);
  } catch {
    store.apiOnline = false;
  } finally {
    store.pollInFlight = false;
  }
}

export function startPolling() {
  pollOnce();
  clearInterval(pollTimer);
  pollTimer = setInterval(pollOnce, Math.max(2, store.prefs.pollIntervalSec) * 1000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      pollOnce();
      document.title = store.unseenDone
        ? `(${store.unseenDone}) WebScan Console`
        : "WebScan Console";
    }
  });
}

/* unseen-completion badge + title flash */
watch(() => [store.unseenDone, store.route.page], () => {
  if (store.unseenDone > 0 && store.route.page !== "runs") {
    document.title = `(${store.unseenDone}) WebScan Console`;
  } else {
    document.title = "WebScan Console";
  }
});

/* title flash while any job runs and the tab is hidden */
setInterval(() => {
  if (!document.hidden) return;
  const running = store.jobs.some((j) => !isTerminal(j) && j.status !== "detached");
  if (running && document.title.indexOf("▶") !== 0) {
    document.title = "▶ " + document.title;
  }
}, 4000);

/* ---------- health check ---------- */

export async function checkHealth() {
  try {
    await getHealth();
    store.apiOnline = true;
  } catch {
    store.apiOnline = false;
  }
}
