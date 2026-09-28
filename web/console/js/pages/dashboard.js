// Dashboard: current status, recent runs, quick actions, demo scene entry.

import { store, navigate, runName, isTerminal } from "../store.js";
import { formatBytes, formatTime } from "../components/ui.js";

const { computed } = Vue;

export default {
  name: "DashboardPage",
  setup() {
    const active = computed(() => store.jobs.filter((j) => !isTerminal(j)));
    const recent = computed(() =>
      [...store.jobs]
        .sort((a, b) => (b.created_at || 0) - (a.created_at || 0))
        .slice(0, 8),
    );
    const completed = computed(() => store.jobs.filter((j) => j.status === "completed").length);
    const failed = computed(() => store.jobs.filter((j) => j.status === "failed").length);
    const hasProfiles = computed(() => store.profiles.length > 0);

    return { store, navigate, runName, formatBytes, formatTime, active, recent, completed, failed, hasProfiles };
  },
  template: `
    <div class="page">
      <div class="page-head">
        <div>
          <div class="page-title">{{ $t("dash.title") }}</div>
          <div class="page-sub">{{ $t("dash.sub") }}</div>
        </div>
        <div class="page-actions">
          <ui-button variant="primary" icon="plus" @click="navigate('wizard')">{{ $t("nav.wizard") }}</ui-button>
        </div>
      </div>

      <div v-if="!hasProfiles" class="card" style="margin-bottom:20px;border-color:var(--accent-border)">
        <div class="card-body row-wrap" style="justify-content:space-between">
          <div class="row" style="gap:12px">
            <icon name="link" style="width:18px;height:18px;color:var(--accent)"></icon>
            <div>
              <div style="font-weight:650">{{ $t("dash.connectTitle") }}</div>
              <div class="small muted">{{ $t("dash.connectHint") }}</div>
            </div>
          </div>
          <ui-button variant="primary" size="sm" icon="settings" @click="navigate('settings')">{{ $t("dash.connectBtn") }}</ui-button>
        </div>
      </div>

      <div class="grid-4" style="margin-bottom:20px">
        <ui-stat :label="$t('dash.statActive')" :value="active.length" :hint="$t('dash.statActiveHint')" />
        <ui-stat :label="$t('dash.statCompleted')" :value="completed" />
        <ui-stat :label="$t('dash.statFailed')" :value="failed" />
        <ui-stat :label="$t('dash.statApi')" :value="store.apiOnline ? $t('dash.online') : store.apiOnline === false ? $t('dash.offline') : '…'" />
      </div>

      <div class="grid-2">
        <ui-card :title="$t('dash.recent')" dense>
          <template #actions>
            <ui-button size="sm" variant="ghost" icon="arrow-right" @click="navigate('runs')">{{ $t("dash.allRuns") }}</ui-button>
          </template>
          <ui-empty v-if="!recent.length" icon="list" :title="$t('dash.noRuns')"
                    :hint="$t('dash.noRunsHint')">
            <ui-button variant="primary" size="sm" icon="plus" @click="navigate('wizard')">{{ $t("nav.wizard") }}</ui-button>
          </ui-empty>
          <table v-else class="tbl">
            <tbody>
              <tr v-for="job in recent" :key="job.id" class="clickable" @click="navigate('runs', job.id)">
                <td style="max-width:220px">
                  <div class="cell-main ellipsis">{{ runName(job) }}</div>
                  <div class="cell-sub">{{ job.algorithm_family || "-" }}</div>
                </td>
                <td><job-badge :job="job"></job-badge></td>
                <td class="faint small nowrap" style="text-align:right">{{ formatTime(job.created_at) }}</td>
              </tr>
            </tbody>
          </table>
        </ui-card>

        <div class="stack">
          <ui-card :title="$t('dash.tryViewer')" dense>
            <div class="card-body" style="padding-top:2px">
              <p class="small muted" style="margin-bottom:12px">{{ $t("dash.tryViewerHint") }}</p>
              <div class="row-wrap">
                <ui-button size="sm" icon="box" @click="navigate('viewer', 'demo')">{{ $t("dash.openDemos") }}</ui-button>
              </div>
            </div>
          </ui-card>
          <ui-card :title="$t('dash.how')" dense>
            <div class="card-body" style="display:flex;flex-direction:column;gap:10px;font-size:var(--fs-sm);color:var(--text-2)">
              <div class="row" style="gap:10px"><icon name="link" style="width:14px;height:14px;color:var(--accent)"></icon>{{ $t("dash.how1") }}</div>
              <div class="row" style="gap:10px"><icon name="upload" style="width:14px;height:14px;color:var(--accent)"></icon>{{ $t("dash.how2") }}</div>
              <div class="row" style="gap:10px"><icon name="zap" style="width:14px;height:14px;color:var(--accent)"></icon>{{ $t("dash.how3") }}</div>
              <div class="row" style="gap:10px"><icon name="layers" style="width:14px;height:14px;color:var(--accent)"></icon>{{ $t("dash.how4") }}</div>
            </div>
          </ui-card>
        </div>
      </div>
    </div>
  `,
};
