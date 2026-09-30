// 3D Viewer page — iframe host for the merged megs-viewer, with demo scenes
// and quick-load of any local PLY path.

import { store, navigate, toast } from "../store.js";
import { t } from "../i18n.js";
import { loadPly, loadResult, exportWeb } from "../api.js";

const { ref, reactive, computed, onMounted } = Vue;

const DEMOS = [
  { id: "demo-sh", labelKey: "view.demoSh", subKey: "view.demoShSub", url: "/web/console/demo/sphere-sh.ply", fmt: "sh" },
  { id: "demo-sg", labelKey: "view.demoSg", subKey: "view.demoSgSub", url: "/web/console/demo/sphere-sg.ply", fmt: "sg" },
];

const POLICIES = ["joint", "bandwidth", "viewport", "gpu", "naive", "full"];

export default {
  name: "ViewerPage",
  setup() {
    const mode = ref("demos");       // demos | path | scene
    const pathInput = ref("");
    const loading = ref(false);
    const frameUrl = ref("");
    const activeLabel = ref("");
    const progressive = reactive({ open: false, exporting: false, plyPath: "", manifestUrl: "", policy: "joint", exportInfo: null, error: "" });

    onMounted(() => {
      const param = decodeURIComponent(store.route.param || "");
      if (param && param !== "demo") {
        // Deep link from a run: a point_cloud_url or result path.
        if (/^\/web\//.test(param)) {
          mode.value = "scene";
          frameUrl.value = guessViewerUrl(param);
          activeLabel.value = param.split("/").pop();
        } else {
          mode.value = "path";
          pathInput.value = param;
          loadPath();
        }
      }
    });

    function guessViewerUrl(url) {
      const isSg = /sg|spherical/i.test(url);
      return `/web/viewers/${isSg ? "sg" : "sh"}.html?url=${encodeURIComponent(url)}`;
    }

    function openDemo(demo) {
      mode.value = "scene";
      activeLabel.value = t(demo.labelKey);
      frameUrl.value = `/web/viewers/${demo.fmt}.html?url=${encodeURIComponent(demo.url)}`;
    }

    async function loadPath() {
      const raw = pathInput.value.trim();
      if (!raw) { toast(t("view.needPath"), "error"); return; }
      loading.value = true;
      try {
        const result = await loadPly(raw);
        if (!result.ok) throw new Error(result.error || "Could not load that PLY");
        const url = result.point_cloud_url || result.result_url || (result.url ?? "");
        const representation = result.representation || "";
        const fmt = String(representation).includes("sg") ? "sg" : "sh";
        mode.value = "scene";
        activeLabel.value = raw.split("/").pop();
        frameUrl.value = `/web/viewers/${fmt}.html?url=${encodeURIComponent(url)}`;
      } catch (err) {
        toast(`${t("view.loadFailed")} ${err.message}`, "error", 7000);
      } finally {
        loading.value = false;
      }
    }

    function back() {
      frameUrl.value = "";
      mode.value = progressive.open ? "progressive" : "demos";
    }

    function openProgressiveDemo() {
      progressive.open = true;
      progressive.manifestUrl = "/web/viewers/demo-progressive/sh/manifest.json";
      progressive.exportInfo = { vertexCount: 1500 };
      startProgressive();
    }

    async function exportAndPlay() {
      const raw = progressive.plyPath.trim();
      if (!raw) { toast(t("view.needPath"), "error"); return; }
      progressive.exporting = true;
      progressive.error = "";
      try {
        const data = await exportWeb(raw);
        progressive.manifestUrl = data.manifest_url;
        progressive.exportInfo = data;
        startProgressive();
      } catch (err) {
        progressive.error = err.message;
        toast(`${t("view.loadFailed")} ${err.message}`, "error", 8000);
      } finally {
        progressive.exporting = false;
      }
    }

    function startProgressive() {
      mode.value = "scene";
      activeLabel.value = `progressive · ${progressive.policy}`;
      const base = progressive.manifestUrl.split("?")[0];
      frameUrl.value = `/web/viewers/progressive.html?url=${encodeURIComponent(base)}&policy=${progressive.policy}`;
    }

    function switchPolicy(policy) {
      progressive.policy = policy;
      if (progressive.manifestUrl) startProgressive();
    }

    return {
      store, DEMOS, POLICIES, mode, pathInput, loading, frameUrl, activeLabel,
      progressive, openDemo, loadPath, back, navigate, openProgressiveDemo, exportAndPlay, switchPolicy,
    };
  },
  template: `
    <div class="page" style="max-width:none">
      <div class="page-head">
        <div>
          <div class="page-title">{{ $t("view.title") }}</div>
          <div class="page-sub">{{ $t("view.sub") }}</div>
        </div>
        <div class="page-actions" v-if="mode === 'scene'">
          <ui-button icon="arrow-left" @click="back">{{ $t("view.back") }}</ui-button>
        </div>
      </div>

      <!-- progressive streaming panel -->
      <div v-if="mode === 'demos'" class="card" style="margin-bottom:16px;border-color:var(--accent-border)">
        <div class="card-body">
          <div class="choice-title"><icon name="zap"></icon> Progressive streaming (WebGS serving)</div>
          <div class="choice-desc" style="margin-bottom:10px">
            Base layer first, refinement chunks adaptively scheduled by bandwidth, viewport and GPU state.
            TTFR, downloaded bytes and frame stats are shown in the HUD — switch policies to compare baselines.
          </div>
          <div class="row-wrap">
            <ui-button variant="primary" size="sm" icon="play" @click="openProgressiveDemo">Stream the demo scene</ui-button>
            <input class="input mono" style="flex:1;min-width:220px" v-model="progressive.plyPath"
                   placeholder="/abs/path/point_cloud.ply — export to chunks, then stream" />
            <ui-button size="sm" icon="upload" :loading="progressive.exporting" @click="exportAndPlay">Export &amp; stream</ui-button>
          </div>
          <div v-if="progressive.open" class="row-wrap" style="margin-top:10px">
            <span class="small muted">policy:</span>
            <button v-for="p in POLICIES" :key="p" class="chip" :style="progressive.policy === p ? 'border-color:var(--accent);color:var(--accent)' : ''"
                    @click="switchPolicy(p)">{{ p }}</button>
          </div>
          <div v-if="progressive.error" class="small" style="color:var(--danger);margin-top:8px">{{ progressive.error }}</div>
        </div>
      </div>

      <!-- picker -->
      <div v-if="mode !== 'scene'" class="grid-3">
        <button v-for="demo in DEMOS" :key="demo.id" class="choice" @click="openDemo(demo)">
          <div class="choice-title"><icon name="box"></icon> {{ $t(demo.labelKey) }}</div>
          <div class="choice-desc">{{ $t(demo.subKey) }}</div>
        </button>
        <div class="card">
          <div class="card-body">
            <div class="choice-title" style="margin-bottom:8px"><icon name="file"></icon> {{ $t("view.loadPly") }}</div>
            <div class="row" style="gap:8px">
              <input class="input mono" style="flex:1" v-model="pathInput"
                     :placeholder="$t('view.loadPlaceholder')" />
              <ui-button variant="primary" icon="eye" :loading="loading" @click="loadPath">{{ $t("view.load") }}</ui-button>
            </div>
            <div class="field-hint" style="margin-top:8px">
              {{ $t("view.loadHint") }}
            </div>
          </div>
        </div>
      </div>

      <!-- viewer frame -->
      <div v-else class="card" style="overflow:hidden">
        <div class="card-head">
          <div class="card-title ellipsis">{{ activeLabel }}</div>
          <div class="faint small" v-if="activeLabel.startsWith('progressive')">{{ activeLabel }} · TTFR / bytes / policy live in the HUD</div>
          <div class="faint small" v-else>{{ $t("view.controls") }}</div>
        </div>
        <iframe v-if="frameUrl" :src="frameUrl"
                style="width:100%;height:calc(100vh - var(--topbar-h) - 150px);border:none;display:block;background:#000"
                allowfullscreen></iframe>
      </div>
    </div>
  `,
};
