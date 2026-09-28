// Datasets page — groups runs by dataset, side-by-side comparison of picks.

import { store, navigate, runName, toast } from "../store.js";
import { t } from "../i18n.js";
import { formatBytes, formatTime } from "../components/ui.js";

const { computed, ref } = Vue;

const METRICS = [
  { key: "psnr", label: "PSNR (dB)", higherBetter: true, digits: 2 },
  { key: "ssim", label: "SSIM", higherBetter: true, digits: 4 },
  { key: "lpips", label: "LPIPS", higherBetter: false, digits: 4 },
  { key: "size_mb", labelKey: "ds.sizeMb", higherBetter: false, digits: 1 },
];

function metricValue(job, key) {
  const value = Number(job?.metrics?.[key]);
  return Number.isFinite(value) ? value : null;
}

function datasetLabel(job) {
  // Normalize path-like dataset labels to their leaf so the same remote
  // dataset recorded with different path spellings groups together.
  const raw = String(job.dataset_name || job.dataset || "").trim();
  if (!raw || raw.includes("/")) {
    const parts = raw.split("/").filter(Boolean);
    // …/datasets/<family>/<dataset-id>/workspace → the dataset-id part.
    const withoutTail = parts[parts.length - 1] === "workspace" ? parts.slice(0, -1) : parts;
    return withoutTail[withoutTail.length - 1] || raw || "";
  }
  return raw;
}

export default {
  name: "DatasetsPage",
  setup() {
    const groups = computed(() => {
      const map = new Map();
      for (const job of store.jobs) {
        const label = datasetLabel(job);
        if (!label) continue;
        if (!map.has(label)) map.set(label, []);
        map.get(label).push(job);
      }
      return [...map.entries()]
        .map(([label, jobs]) => ({
          label,
          jobs: jobs.sort((a, b) => (b.created_at || 0) - (a.created_at || 0)),
        }))
        .sort((a, b) => Math.max(...b.jobs.map((j) => j.created_at || 0)) - Math.max(...a.jobs.map((j) => j.created_at || 0)));
    });

    const expanded = ref("");
    const picks = ref(new Set());
    const compareJobs = computed(() =>
      [...picks.value]
        .map((id) => store.jobs.find((j) => j.id === id))
        .filter(Boolean),
    );

    function togglePick(job) {
      const next = new Set(picks.value);
      if (next.has(job.id)) next.delete(job.id);
      else {
        if (next.size >= 4) { toast(t("ds.maxFour"), "info"); return; }
        next.add(job.id);
      }
      picks.value = next;
    }

    function penalty(job, metric) {
      const value = metricValue(job, metric.key);
      if (value === null) return null;
      const values = compareJobs.value.map((j) => metricValue(j, metric.key)).filter((v) => v !== null);
      if (values.length < 2) return null;
      const best = metric.higherBetter ? Math.max(...values) : Math.min(...values);
      if (value === best) return "best";
      return Math.abs(value - best);
    }

    const firstRender = (job) => {
      const urls = job.result_urls || (job.result_url ? [job.result_url] : []);
      return urls.find((u) => /\.(png|jpe?g|webp)$/i.test(u)) || "";
    };

    return {
      store, navigate, runName, groups, expanded, picks, togglePick, compareJobs,
      METRICS, metricValue, penalty, firstRender, formatBytes, formatTime, toast,
    };
  },
  template: `
    <div class="page">
      <div class="page-head">
        <div>
          <div class="page-title">{{ $t("ds.title") }}</div>
          <div class="page-sub">{{ $t("ds.sub") }}</div>
        </div>
      </div>

      <ui-empty v-if="store.jobsLoaded && !groups.length" icon="layers"
                :title="$t('ds.none')"
                :hint="$t('ds.noneHint')">
        <ui-button variant="primary" size="sm" icon="plus" @click="navigate('wizard')">{{ $t("nav.wizard") }}</ui-button>
      </ui-empty>

      <div v-else class="stack">
        <div v-if="compareJobs.length" class="card" style="border-color:var(--accent-border); position:sticky; top:0; z-index:5">
          <div class="card-body" style="padding:12px 16px">
            <div class="spread" style="margin-bottom:10px">
              <div class="row" style="gap:8px">
                <icon name="layers" style="width:15px;height:15px;color:var(--accent)"></icon>
                <strong class="small">{{ $t("ds.comparing") }} {{ compareJobs.length }} {{ compareJobs.length === 1 ? $t("ds.run") : $t("ds.runs") }}</strong>
                <button class="btn btn-ghost btn-sm" @click="picks = new Set()">{{ $t("ds.clear") }}</button>
              </div>
            </div>
            <div class="compare-grid">
              <div v-for="job in compareJobs" :key="job.id" class="compare-col">
                <img v-if="firstRender(job)" class="compare-img" :src="firstRender(job)" loading="lazy" />
                <div v-else class="compare-img" style="display:grid;place-items:center" ><icon name="image" style="width:22px;height:22px;opacity:.4"></icon></div>
                <div class="small ellipsis" style="margin-top:6px;font-weight:650">{{ runName(job) }}</div>
                <div class="small faint">{{ job.algorithm_family }}</div>
              </div>
            </div>
            <table class="tbl" style="margin-top:10px">
              <thead><tr><th>{{ $t("ds.colMetric") }}</th><th v-for="job in compareJobs" :key="job.id">{{ runName(job) }}</th></tr></thead>
              <tbody>
                <tr v-for="metric in METRICS" :key="metric.key">
                  <td class="muted">{{ metric.labelKey ? $t(metric.labelKey) : metric.label }}</td>
                  <td v-for="job in compareJobs" :key="job.id" class="num">
                    <template v-if="metricValue(job, metric.key) !== null">
                      {{ metricValue(job, metric.key).toFixed(metric.digits) }}
                      <span v-if="penalty(job, metric) === 'best'" class="delta-pos" :title="$t('ds.best')">★</span>
                      <span v-else-if="typeof penalty(job, metric) === 'number'" class="delta-neg small"
                            :title="$t('ds.worse') + ' ' + penalty(job, metric).toFixed(metric.digits)">
                        (−{{ penalty(job, metric).toFixed(metric.digits) }})
                      </span>
                    </template>
                    <span v-else class="faint">-</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div v-for="group in groups" :key="group.label" class="card">
          <div class="card-head" style="cursor:pointer" @click="expanded = expanded === group.label ? '' : group.label">
            <div class="row" style="gap:10px">
              <icon :name="expanded === group.label ? 'chevron-down' : 'chevron-right'" style="width:15px;height:15px;color:var(--text-3)"></icon>
              <div class="card-title">{{ group.label }}</div>
              <ui-badge kind="neutral">{{ group.jobs.length }} {{ $t("ds.runs") }}</ui-badge>
            </div>
            <div class="faint small">{{ group.jobs.filter(j => j.status === 'completed').length }} {{ $t("ds.completed") }}</div>
          </div>
          <table v-if="expanded === group.label" class="tbl">
            <thead>
              <tr><th style="width:34px"></th><th>{{ $t("runs.colRun") }}</th><th>{{ $t("runs.colStatus") }}</th><th>{{ $t("ds.colPsnr") }}</th><th>{{ $t("ds.colSsim") }}</th><th>{{ $t("ds.colLpips") }}</th><th>{{ $t("ds.colSize") }}</th><th></th></tr>
            </thead>
            <tbody>
              <tr v-for="job in group.jobs" :key="job.id">
                <td><input type="checkbox" :checked="picks.has(job.id)" @change="togglePick(job)" :disabled="!job.metrics" style="accent-color:var(--accent)" /></td>
                <td class="clickable" @click="navigate('runs', job.id)">
                  <div class="cell-main ellipsis" style="max-width:260px">{{ runName(job) }}</div>
                  <div class="cell-sub">{{ formatTime(job.created_at) }}</div>
                </td>
                <td><job-badge :job="job"></job-badge></td>
                <td class="num">{{ metricValue(job, 'psnr') !== null ? metricValue(job, 'psnr').toFixed(2) : "-" }}</td>
                <td class="num">{{ metricValue(job, 'ssim') !== null ? metricValue(job, 'ssim').toFixed(4) : "-" }}</td>
                <td class="num">{{ metricValue(job, 'lpips') !== null ? metricValue(job, 'lpips').toFixed(4) : "-" }}</td>
                <td class="num muted">{{ formatBytes(job.metrics?.size_bytes) }}</td>
                <td>
                  <ui-button v-if="job.point_cloud_url || job.viewer_url" size="sm" variant="ghost" icon="box"
                             @click.stop="navigate('viewer', encodeURIComponent(job.point_cloud_url || ''))">3D</ui-button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  `,
};

