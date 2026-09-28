// 3D Viewer page — iframe host for the merged megs-viewer, with demo scenes
// and quick-load of any local PLY path.

import { store, navigate, toast } from "../store.js";
import { t } from "../i18n.js";
import { loadPly, loadResult } from "../api.js";

const { ref, computed, onMounted } = Vue;

const DEMOS = [
  { id: "demo-sh", label: "Sphere · SH", sub: "spherical-harmonics Gaussians", url: "/web/console/demo/sphere-sh.ply", fmt: "sh" },
  { id: "demo-sg", label: "Sphere · SG", sub: "spherical-Gaussian Gaussians", url: "/web/console/demo/sphere-sg.ply", fmt: "sg" },
];

export default {
  name: "ViewerPage",
  setup() {
    const mode = ref("demos");       // demos | path | scene
    const pathInput = ref("");
    const loading = ref(false);
    const frameUrl = ref("");
    const activeLabel = ref("");

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
      mode.value = "demos";
    }

    return { store, DEMOS, mode, pathInput, loading, frameUrl, activeLabel, openDemo, loadPath, back, navigate };
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
          <div class="faint small">{{ $t("view.controls") }}</div>
        </div>
        <iframe v-if="frameUrl" :src="frameUrl"
                style="width:100%;height:calc(100vh - var(--topbar-h) - 150px);border:none;display:block;background:#000"
                allowfullscreen></iframe>
      </div>
    </div>
  `,
};
