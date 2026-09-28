// WebScan Console — app shell: sidebar nav, topbar, hash router, polling.

import { store, startPolling, checkHealth, navigate, activeProfile } from "./store.js";
import Icon from "./icons.js";
import { uiComponents } from "./components/ui.js";
import UiChart from "./components/chart.js";
import DashboardPage from "./pages/dashboard.js";
import WizardPage from "./pages/wizard.js";
import RunsPage from "./pages/runs.js";
import DatasetsPage from "./pages/datasets.js";
import ViewerPage from "./pages/viewer-page.js";
import SettingsPage from "./pages/settings.js";

const { createApp, computed, onMounted } = Vue;

const NAV = [
  { page: "dashboard", label: "Dashboard", icon: "dashboard" },
  { page: "wizard",    label: "New Training", icon: "plus" },
  { page: "runs",      label: "Runs", icon: "list" },
  { page: "datasets",  label: "Datasets", icon: "layers" },
  { page: "viewer",    label: "3D Viewer", icon: "box" },
  { page: "settings",  label: "Settings", icon: "settings" },
];

const PAGE_TITLES = {
  dashboard: "Dashboard",
  wizard: "New Training",
  runs: "Runs",
  datasets: "Datasets",
  viewer: "3D Viewer",
  settings: "Settings",
};

const App = {
  name: "App",
  setup() {
    const runningCount = computed(() =>
      store.jobs.filter((j) => !["completed", "failed", "canceled", "partial_success",
        "training_success_render_failed", "training_success_metrics_failed", "training_success_postprocess_failed"]
        .includes(String(j.status || "").toLowerCase())).length,
    );
    const profileLabel = computed(() => {
      const profile = activeProfile();
      return profile ? (profile.label || profile.host || "No label") : "No connection profile";
    });
    const pageTitle = computed(() => PAGE_TITLES[store.route.page] || "");
    onMounted(() => {
      startPolling();
      checkHealth();
    });
    return { store, navigate, NAV, runningCount, profileLabel, pageTitle };
  },
  template: `
    <div class="shell">
      <aside class="shell-sidebar">
        <div class="sidebar-brand">
          <div class="brand-logo">WS</div>
          <div>
            <div class="brand-name">WebScan</div>
            <div class="brand-tag">3DGS console</div>
          </div>
        </div>
        <nav class="nav-group">
          <div class="nav-label">Workspace</div>
          <button v-for="item in NAV" :key="item.page" class="nav-item"
                  :class="{ active: store.route.page === item.page }"
                  @click="navigate(item.page)">
            <icon :name="item.icon"></icon>
            <span>{{ item.label }}</span>
            <span v-if="item.page === 'runs' && runningCount" class="nav-pill">{{ runningCount }}</span>
          </button>
        </nav>
        <div class="sidebar-footer">
          <div class="row" style="gap:8px">
            <span class="dot" :class="{ on: store.apiOnline, off: store.apiOnline === false }"></span>
            <span class="small muted">{{ store.apiOnline ? "API online" : store.apiOnline === false ? "API offline" : "Checking…" }}</span>
          </div>
        </div>
      </aside>

      <header class="shell-topbar">
        <div class="topbar-title">{{ pageTitle }}</div>
        <div class="topbar-spacer"></div>
        <button class="conn-chip" @click="navigate('settings')" :title="'Active connection profile'">
          <span class="dot" :class="{ on: store.profiles.length && store.apiOnline, warn: store.profiles.length && !store.apiOnline, off: !store.profiles.length }"></span>
          {{ profileLabel }}
          <icon name="chevron-down" style="width:13px;height:13px"></icon>
        </button>
        <button class="bell" title="Notifications" @click="navigate('settings')">
          <icon name="bell" style="width:16px;height:16px"></icon>
          <span v-if="store.unseenDone" class="ping"></span>
        </button>
      </header>

      <main class="shell-main">
        <component :is="'page-' + (NAV.some(n => n.page === store.route.page) ? store.route.page : 'dashboard')"></component>
      </main>

      <ui-toasts></ui-toasts>
    </div>
  `,
};

const app = createApp(App);
app.config.errorHandler = (err, _inst, info) => {
  window.__errors.push(`vue: ${err && err.message} (${info})`);
  console.error(err, info);
};
// Global registration: page components and the UI kit are used across pages.
app.component("icon", Icon);
app.component("ui-chart", UiChart);
for (const [name, definition] of Object.entries(uiComponents)) {
  if (name === "icon") continue;  // real Icon registered above
  app.component(name, definition);
}
app.component("page-dashboard", DashboardPage);
app.component("page-wizard", WizardPage);
app.component("page-runs", RunsPage);
app.component("page-datasets", DatasetsPage);
app.component("page-viewer", ViewerPage);
app.component("page-settings", SettingsPage);
window.__app = app;  // debug handle
app.mount("#app");
