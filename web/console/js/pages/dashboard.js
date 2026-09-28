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
          <div class="page-title">Remote 3DGS training, end to end</div>
          <div class="page-sub">Upload a scene, train on your GPU server, compare results — from one console.</div>
        </div>
        <div class="page-actions">
          <ui-button variant="primary" icon="plus" @click="navigate('wizard')">New Training</ui-button>
        </div>
      </div>

      <div v-if="!hasProfiles" class="card" style="margin-bottom:20px;border-color:var(--accent-border)">
        <div class="card-body row-wrap" style="justify-content:space-between">
          <div class="row" style="gap:12px">
            <icon name="link" style="width:18px;height:18px;color:var(--accent)"></icon>
            <div>
              <div style="font-weight:650">Connect your GPU server to get started</div>
              <div class="small muted">Create a connection profile — paste an SSH command and you are mostly done.</div>
            </div>
          </div>
          <ui-button variant="primary" size="sm" icon="settings" @click="navigate('settings')">Set up connection</ui-button>
        </div>
      </div>

      <div class="grid-4" style="margin-bottom:20px">
        <ui-stat label="Active" :value="active.length" hint="running or queued" />
        <ui-stat label="Completed" :value="completed" />
        <ui-stat label="Failed" :value="failed" />
        <ui-stat label="API" :value="store.apiOnline ? 'Online' : store.apiOnline === false ? 'Offline' : '…'" :hint="store.apiOnline ? 'local server reachable' : ''" />
      </div>

      <div class="grid-2">
        <ui-card title="Recent runs" dense>
          <template #actions>
            <ui-button size="sm" variant="ghost" icon="arrow-right" @click="navigate('runs')">All runs</ui-button>
          </template>
          <ui-empty v-if="!recent.length" icon="list" title="No runs yet"
                    hint="Start your first training from the New Training page.">
            <ui-button variant="primary" size="sm" icon="plus" @click="navigate('wizard')">New Training</ui-button>
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
          <ui-card title="Try the 3D viewer now" dense>
            <div class="card-body" style="padding-top:2px">
              <p class="small muted" style="margin-bottom:12px">
                No GPU server needed for this part — two built-in Gaussian scenes render right in the browser.
              </p>
              <div class="row-wrap">
                <ui-button size="sm" icon="box" @click="navigate('viewer', 'demo')">Open demo scenes</ui-button>
              </div>
            </div>
          </ui-card>
          <ui-card title="How it works" dense>
            <div class="card-body" style="display:flex;flex-direction:column;gap:10px;font-size:var(--fs-sm);color:var(--text-2)">
              <div class="row" style="gap:10px"><icon name="link" style="width:14px;height:14px;color:var(--accent)"></icon>1. Save a connection profile for your GPU server</div>
              <div class="row" style="gap:10px"><icon name="upload" style="width:14px;height:14px;color:var(--accent)"></icon>2. Upload scene images or reuse data already on the server</div>
              <div class="row" style="gap:10px"><icon name="zap" style="width:14px;height:14px;color:var(--accent)"></icon>3. Pick an algorithm, submit — precheck runs automatically</div>
              <div class="row" style="gap:10px"><icon name="layers" style="width:14px;height:14px;color:var(--accent)"></icon>4. Compare runs per dataset and view results in 3D</div>
            </div>
          </ui-card>
        </div>
      </div>
    </div>
  `,
};
