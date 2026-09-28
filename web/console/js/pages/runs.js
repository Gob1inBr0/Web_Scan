// Runs list + detail drawer (live log, metrics chart, result actions).

import { store, navigate, runName, isTerminal, pollOnce, toast, remoteConfig, hasPassword, setRunName } from "../store.js";
import { t } from "../i18n.js";
import { getJob, getJobLogDelta, getJobLogsPage, cancelJob, deleteJob, checkResult, downloadResult, redownloadResult, jobLogDownloadUrl, jobMetricsCsvUrl } from "../api.js";
import { formatBytes, formatTime } from "../components/ui.js";

const { ref, computed, onMounted, onUnmounted, watch } = Vue;

const METRIC_COLORS = { psnr: "#4d8dff", ssim: "#34b374", loss: "#dfa239" };

export default {
  name: "RunsPage",
  setup() {
    const filter = ref("");
    const drawerId = ref(store.route.param || "");
    const drawerJob = ref(null);
    const logText = ref("");
    const logCursor = ref(0);
    const metricsRows = ref([]);
    const drawerBusy = ref(false);
    const compareFileInput = ref(null);

    const filtered = computed(() => {
      const query = filter.value.trim().toLowerCase();
      const jobs = [...store.jobs].sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
      if (!query) return jobs;
      return jobs.filter((j) =>
        `${runName(j)} ${j.algorithm_family || ""} ${j.status}`.toLowerCase().includes(query),
      );
    });

    const drawerName = computed(() => (drawerJob.value ? runName(drawerJob.value) : ""));
    const metricsSeries = computed(() => {
      const rows = metricsRows.value.filter((r) => Number.isFinite(Number(r.iter)) && Number(r.iter) > 0);
      const series = [];
      for (const key of ["psnr", "ssim", "loss"]) {
        const points = rows
          .map((r) => ({ x: Number(r.iter), y: Number(r[key]) }))
          .filter((p) => Number.isFinite(p.y) && p.y > 0);
        if (points.length > 1) {
          series.push({ name: key.toUpperCase(), color: METRIC_COLORS[key], points });
        }
      }
      return series;
    });
    const renderImages = computed(() => {
      const job = drawerJob.value;
      if (!job) return [];
      const urls = job.result_urls || (job.result_url ? [job.result_url] : []);
      return urls.filter((u) => /\.(png|jpe?g|webp)$/i.test(u)).slice(0, 12);
    });
    const downloadPct = computed(() => {
      const dl = drawerJob.value?.remote_result?.download;
      if (!dl || !dl.total_bytes) return null;
      return Math.round(((dl.bytes || 0) / dl.total_bytes) * 100);
    });

    let logTimer = null;

    function openDrawer(id) {
      drawerId.value = id;
      if (id) navigate("runs", id);
    }

    function closeDrawer() {
      drawerId.value = "";
      drawerJob.value = null;
      logText.value = "";
      metricsRows.value = [];
      navigate("runs");
      clearInterval(logTimer);
      logTimer = null;
    }

    async function loadDetail() {
      const id = drawerId.value;
      if (!id) return;
      try {
        const fresh = await getJob(id);
        drawerJob.value = fresh;
        const index = store.jobs.findIndex((j) => j.id === id);
        if (index >= 0) store.jobs[index] = fresh;
      } catch (err) {
        if (String(err.status) === "404") { toast(t("runs.notFound"), "error"); closeDrawer(); }
      }
    }

    async function pollLog() {
      const id = drawerId.value;
      if (!id) return;
      try {
        const delta = await getJobLogDelta(id, logCursor.value);
        logCursor.value = Number(delta.cursor || logCursor.value || 0);
        if (delta.log_text) {
          logText.value = (logText.value + delta.log_text).slice(-400000);
        }
        if (!isTerminal(drawerJob.value) || delta.log_text) {
          await loadDetail();
        }
        if (isTerminal(drawerJob.value) && !logTimer) return;
      } catch { /* transient */ }
    }

    function startLogLoop() {
      clearInterval(logTimer);
      logTimer = setInterval(() => {
        if (document.hidden) return;
        if (drawerJob.value && isTerminal(drawerJob.value)) {
          clearInterval(logTimer);
          logTimer = null;
          return;
        }
        pollLog();
      }, 3000);
      pollLog();
    }

    async function loadMetricsPage() {
      const id = drawerId.value;
      if (!id) return;
      try {
        const data = await getJobLogsPage(id, { pageSize: 200, tailLines: 60 });
        metricsRows.value = data.metrics_page?.rows || [];
      } catch { /* metrics optional */ }
    }

    watch(drawerId, (id) => {
      if (!id) return;
      logCursor.value = 0;
      logText.value = "";
      loadDetail().then(() => { startLogLoop(); loadMetricsPage(); });
    });

    onMounted(() => {
      if (drawerId.value) {
        loadDetail().then(() => { startLogLoop(); loadMetricsPage(); });
      }
      store.unseenDone = 0;
    });
    onUnmounted(() => clearInterval(logTimer));

    async function doCancel() {
      drawerBusy.value = true;
      try {
        await cancelJob(drawerId.value, remoteConfig());
        toast(t("runs.cancelRequested"), "info");
        await loadDetail();
      } catch (err) { toast(`${t("runs.cancelFailed")} ${err.message}`, "error"); }
      finally { drawerBusy.value = false; }
    }

    async function doDownload(redownload = false) {
      if (!hasPassword()) { toast(t("runs.needPasswordDownload"), "error", 7000); return; }
      drawerBusy.value = true;
      try {
        const data = redownload
          ? await redownloadResult(drawerId.value, remoteConfig())
          : await downloadResult(drawerId.value, remoteConfig());
        if (data.already_running) toast(t("runs.alreadyRunning"), "info");
        else if (data.check?.complete) toast(t("runs.alreadyComplete"), "success");
        else toast(t("runs.downloadStarted"), "success");
        await loadDetail();
      } catch (err) { toast(`${t("runs.downloadFailed")} ${err.message}`, "error", 8000); }
      finally { drawerBusy.value = false; }
    }

    async function doDelete() {
      if (!confirm(t("runs.deleteConfirm"))) return;
      try {
        await deleteJob(drawerId.value);
        toast(t("runs.deleted"), "success");
        closeDrawer();
        await pollOnce();
      } catch (err) { toast(`${t("runs.deleteFailed")} ${err.message}`, "error"); }
    }

    function renameRun() {
      const job = drawerJob.value;
      if (!job) return;
      const name = prompt(t("runs.renamePrompt"), runName(job));
      if (name && name.trim()) setRunName(job.id, name.trim());
    }

    function openInViewer() {
      navigate("viewer", encodeURIComponent(drawerJob.value?.point_cloud_url || drawerJob.value?.result_path || ""));
    }

    function openImage(url) {
      window.open(url, "_blank");
    }

    return {
      store, filter, filtered, drawerId, drawerJob, drawerName, logText, metricsSeries,
      renderImages, downloadPct, drawerBusy, formatBytes, formatTime, jobLogDownloadUrl,
      openDrawer, closeDrawer, doCancel, doDownload, doDelete, renameRun, openInViewer, openImage, navigate, pollOnce, isTerminal, runName,
    };
  },
  template: `
    <div class="page">
      <div class="page-head">
        <div>
          <div class="page-title">{{ $t("runs.title") }}</div>
          <div class="page-sub">{{ $t("runs.sub") }}</div>
        </div>
        <div class="page-actions">
          <input class="input" style="width:220px" v-model="filter" :placeholder="$t('runs.filter')" />
          <ui-button icon="refresh" @click="pollOnce()" :title="$t('runs.refresh')">{{ $t("runs.refresh") }}</ui-button>
        </div>
      </div>

      <div class="card">
        <ui-empty v-if="store.jobsLoaded && !filtered.length" icon="list"
                  :title="$t('runs.noMatch')" :hint="$t('runs.noMatchHint')">
          <ui-button variant="primary" size="sm" icon="plus" @click="navigate('wizard')">{{ $t("nav.wizard") }}</ui-button>
        </ui-empty>
        <div v-else-if="!store.jobsLoaded" style="padding:16px">
          <div class="skel" style="height:44px;margin-bottom:8px"></div>
          <div class="skel" style="height:44px;margin-bottom:8px"></div>
          <div class="skel" style="height:44px"></div>
        </div>
        <table v-else class="tbl">
          <thead>
            <tr><th>{{ $t("runs.colRun") }}</th><th>{{ $t("runs.colAlgorithm") }}</th><th>{{ $t("runs.colStatus") }}</th><th>{{ $t("runs.colPsnr") }}</th><th>{{ $t("runs.colSize") }}</th><th>{{ $t("runs.colStarted") }}</th></tr>
          </thead>
          <tbody>
            <tr v-for="job in filtered" :key="job.id" class="clickable" @click="openDrawer(job.id)">
              <td style="max-width:260px">
                <div class="cell-main ellipsis">{{ runName(job) }}</div>
                <div class="cell-sub mono">{{ job.id.slice(0, 8) }}</div>
              </td>
              <td class="muted">{{ job.algorithm_family || "-" }}</td>
              <td><job-badge :job="job"></job-badge></td>
              <td class="num">{{ job.metrics?.psnr ? Number(job.metrics.psnr).toFixed(2) : "-" }}</td>
              <td class="num muted">{{ formatBytes(job.metrics?.size_bytes || job.file_size) }}</td>
              <td class="faint nowrap">{{ formatTime(job.created_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- DETAIL DRAWER -->
      <template v-if="drawerJob">
        <ui-drawer :title="drawerName" :subtitle="drawerJob.id" @close="closeDrawer">
          <div class="row-wrap" style="margin-bottom:4px">
            <job-badge :job="drawerJob"></job-badge>
            <ui-badge kind="neutral" v-if="drawerJob.algorithm_family">{{ drawerJob.algorithm_family }}</ui-badge>
            <button class="btn btn-ghost btn-sm" @click.stop="renameRun" :title="$t('runs.rename')"><icon name="file"></icon> {{ $t("runs.rename") }}</button>
          </div>

          <div class="grid-3">
            <ui-stat label="PSNR" :value="drawerJob.metrics?.psnr ? Number(drawerJob.metrics.psnr).toFixed(2) : '-'" />
            <ui-stat label="SSIM" :value="drawerJob.metrics?.ssim ? Number(drawerJob.metrics.ssim).toFixed(4) : '-'" />
            <ui-stat label="Size" :value="formatBytes(drawerJob.metrics?.size_bytes || drawerJob.file_size)" />
          </div>

          <div v-if="downloadPct !== null && downloadPct < 100" class="card" style="padding:12px 14px">
            <div class="spread small" style="margin-bottom:6px"><span class="muted">{{ $t("runs.downloadingResults") }}</span><span class="num">{{ downloadPct }}%</span></div>
            <ui-progress :value="downloadPct"></ui-progress>
          </div>

          <ui-card :title="$t('runs.metricsChart')" dense>
            <ui-chart :series="metricsSeries"></ui-chart>
          </ui-card>

          <ui-card :title="$t('runs.liveLog')" dense>
            <template #actions>
              <a class="btn btn-ghost btn-sm" :href="jobLogDownloadUrl(drawerJob.id)" target="_blank"><icon name="download"></icon> {{ $t("runs.log") }}</a>
              <a class="btn btn-ghost btn-sm" :href="jobMetricsCsvUrl(drawerJob.id)" target="_blank"><icon name="download"></icon> CSV</a>
            </template>
            <div class="logview" style="max-height:300px">{{ logText || $t("runs.waitingLog") }}</div>
          </ui-card>

          <ui-card v-if="renderImages.length" :title="$t('runs.renderedViews')" dense>
            <div class="shot-wall">
              <img v-for="url in renderImages" :key="url" class="shot" :src="url" loading="lazy"
                   @click="openImage(url)" />
            </div>
          </ui-card>

          <ui-card :title="$t('runs.paths')" dense>
            <dl class="kv">
              <dt>{{ $t("runs.remoteOutput") }}</dt><dd class="mono">{{ drawerJob.output_dir || "-" }}</dd>
              <dt>{{ $t("runs.localResult") }}</dt><dd class="mono">{{ drawerJob.local_result_dir || drawerJob.output_dir || "-" }}</dd>
            </dl>
          </ui-card>

          <template #footer>
            <ui-button v-if="!isTerminal(drawerJob)" variant="danger" icon="stop" :loading="drawerBusy" @click="doCancel">{{ $t("runs.cancelRun") }}</ui-button>
            <template v-else>
              <ui-button variant="primary" icon="download" :loading="drawerBusy" @click="doDownload(false)">{{ $t("runs.downloadResults") }}</ui-button>
              <ui-button icon="rotate" :loading="drawerBusy" @click="doDownload(true)">{{ $t("runs.redownload") }}</ui-button>
              <ui-button v-if="drawerJob.point_cloud_url || drawerJob.viewer_url" icon="box" @click="openInViewer">{{ $t("runs.view3d") }}</ui-button>
            </template>
            <span style="flex:1"></span>
            <ui-button variant="ghost" icon="trash" @click="doDelete">{{ $t("runs.delete") }}</ui-button>
          </template>
        </ui-drawer>
      </template>
    </div>
  `,
};
