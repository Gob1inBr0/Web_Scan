// Settings — connection profiles (multi, ssh-command paste), password per
// session, notifications, access info.

import { store, persistProfiles, persistPrefs, defaultProfile, activeProfile, sessionPasswords, toast, navigate } from "../store.js";
import { parseSshCommand } from "../ssh-parse.js";

const { ref, computed } = Vue;

export default {
  name: "SettingsPage",
  setup() {
    const editing = ref(null);       // profile being edited (copy)
    const pasteSsh = ref("");
    const passwordInput = ref("");
    const notifySupported = typeof Notification !== "undefined";

    const active = computed(() => activeProfile());
    const hasPassword = computed(() => Boolean(active.value && sessionPasswords.get(active.value.id)));

    function newProfile() {
      editing.value = defaultProfile();
    }

    function editProfile(profile) {
      editing.value = { ...profile };
    }

    function applyPaste() {
      const parsed = parseSshCommand(pasteSsh.value);
      if (!parsed || !parsed.host) {
        toast("Could not parse it — expected e.g. ssh -p 10526 root@1.2.3.4", "error");
        return;
      }
      if (!editing.value) newProfile();
      if (parsed.host) editing.value.host = parsed.host;
      if (parsed.port) editing.value.port = parsed.port;
      if (parsed.username) editing.value.username = parsed.username;
      if (!editing.value.label) editing.value.label = parsed.host;
      pasteSsh.value = "";
      toast("Parsed — review the fields and save", "success");
    }

    function saveProfile() {
      const profile = editing.value;
      if (!profile.host.trim()) { toast("Host is required", "error"); return; }
      if (!profile.label.trim()) profile.label = profile.host;
      const index = store.profiles.findIndex((p) => p.id === profile.id);
      if (index >= 0) store.profiles[index] = profile;
      else store.profiles.push(profile);
      store.activeProfileId = profile.id;
      persistProfiles();
      editing.value = null;
      toast(`Profile "${profile.label}" saved`, "success");
    }

    function removeProfile(profile) {
      if (!confirm(`Delete profile "${profile.label || profile.host}"?`)) return;
      store.profiles = store.profiles.filter((p) => p.id !== profile.id);
      if (store.activeProfileId === profile.id) {
        store.activeProfileId = store.profiles[0]?.id || "";
      }
      persistProfiles();
    }

    function switchProfile(profile) {
      store.activeProfileId = profile.id;
      persistProfiles();
      toast(`Switched to "${profile.label || profile.host}"`, "info");
    }

    function savePassword() {
      if (!active.value) { toast("Create and select a profile first", "error"); return; }
      sessionPasswords.set(active.value.id, passwordInput.value);
      passwordInput.value = "";
      toast("Password stored for this browser session (cleared when the browser closes)", "success");
    }

    async function askNotifyPermission() {
      if (!notifySupported) return;
      if (Notification.permission !== "granted") {
        const result = await Notification.requestPermission();
        if (result !== "granted") { toast("Notification permission denied by the browser", "error"); return; }
      }
      store.prefs.notifyOnDone = true;
      persistPrefs();
      toast("Notifications enabled", "success");
    }

    return {
      store, editing, pasteSsh, passwordInput, notifySupported, active, hasPassword,
      newProfile, editProfile, applyPaste, saveProfile, removeProfile, switchProfile,
      savePassword, askNotifyPermission, persistPrefs, navigate,
    };
  },
  template: `
    <div class="page" style="max-width: 860px">
      <div class="page-head">
        <div>
          <div class="page-title">Settings</div>
          <div class="page-sub">Connection profiles, notifications, and access controls.</div>
        </div>
        <div class="page-actions">
          <ui-button variant="primary" icon="plus" @click="newProfile">New profile</ui-button>
        </div>
      </div>

      <!-- profiles -->
      <ui-card title="Connection profiles" style="margin-bottom:20px">
        <template #actions>
          <span class="faint small">{{ store.profiles.length }} saved</span>
        </template>

        <div style="display:flex;flex-direction:column;gap:8px;margin-bottom:14px">
          <div v-for="profile in store.profiles" :key="profile.id"
               class="check-item" :style="profile.id === store.activeProfileId ? 'border-color:var(--accent-border)' : ''">
            <icon :name="profile.id === store.activeProfileId ? 'check-circle' : 'link'"
                  :style="profile.id === store.activeProfileId ? 'color:var(--accent)' : ''"></icon>
            <div style="flex:1;min-width:0">
              <div class="ci-name">{{ profile.label || profile.host }}
                <ui-badge v-if="profile.id === store.activeProfileId" kind="accent" style="margin-left:6px">active</ui-badge>
              </div>
              <div class="ci-msg mono">{{ profile.username }}@{{ profile.host }}:{{ profile.port }}</div>
            </div>
            <ui-button v-if="profile.id !== store.activeProfileId" size="sm" variant="ghost" @click="switchProfile(profile)">Use</ui-button>
            <ui-button size="sm" variant="ghost" icon="settings" @click="editProfile(profile)">Edit</ui-button>
            <ui-button size="sm" variant="ghost" icon="trash" @click="removeProfile(profile)"></ui-button>
          </div>
          <div v-if="!store.profiles.length" class="small muted">
            No profiles yet — paste the SSH command your cloud provider gave you below to create one in seconds.
          </div>
        </div>

        <template v-if="editing">
          <div class="card" style="background:var(--bg-inset);padding:16px">
            <ui-field label="Paste SSH command" hint="Like: ssh -p 10526 root@connect.gpu-cloud.com — we fill the fields for you"
                      style="margin-bottom:12px">
              <div class="row" style="gap:8px">
                <input class="input mono" style="flex:1" v-model="pasteSsh" placeholder="ssh -p 22 root@your.server" @keydown.enter="applyPaste" />
                <ui-button icon="zap" @click="applyPaste">Parse</ui-button>
              </div>
            </ui-field>
            <div class="grid-3" style="margin-bottom:12px">
              <ui-field label="Label"><input class="input" v-model="editing.label" placeholder="My GPU box" /></ui-field>
              <ui-field label="Host" required><input class="input" v-model="editing.host" placeholder="connect.gpu-cloud.com" /></ui-field>
              <div class="grid-2" style="gap:8px">
                <ui-field label="Port"><input class="input num" type="number" v-model.number="editing.port" /></ui-field>
                <ui-field label="User"><input class="input" v-model="editing.username" /></ui-field>
              </div>
            </div>
            <div class="grid-2" style="margin-bottom:12px">
              <ui-field label="Repo path on server" hint="Where the Web_Scan algorithm folders live"><input class="input mono" v-model="editing.repo_path" placeholder="/root/autodl-tmp/Web_Scan" /></ui-field>
              <ui-field label="Python" hint="Interpreter used to launch training"><input class="input mono" v-model="editing.python" /></ui-field>
            </div>
            <div class="grid-2" style="margin-bottom:12px">
              <ui-field label="Workspace root" hint="Uploaded datasets land here"><input class="input mono" v-model="editing.workspace_root" /></ui-field>
              <ui-field label="Output root" hint="Training outputs are written here"><input class="input mono" v-model="editing.output_root" /></ui-field>
            </div>
            <ui-field label="Conda activate command (optional)" hint="Only plain activation is allowed, e.g. source ~/miniconda3/etc/profile.d/conda.sh && conda activate hac"
                      style="margin-bottom:14px">
              <input class="input mono" v-model="editing.activate_cmd" placeholder="source … && conda activate env" />
            </ui-field>
            <div class="row" style="justify-content:flex-end;gap:8px">
              <ui-button variant="ghost" @click="editing = null">Cancel</ui-button>
              <ui-button variant="primary" icon="check" @click="saveProfile">Save profile</ui-button>
            </div>
          </div>
        </template>
      </ui-card>

      <!-- password for active profile -->
      <ui-card title="Server password" style="margin-bottom:20px">
        <p class="small muted" style="margin-bottom:12px">
          For <strong>{{ active ? (active.label || active.host) : "—" }}</strong>.
          Kept in this browser tab's session only — never written to disk, cleared when you close the browser.
          Needed again after restarting the WebScan server.
        </p>
        <div class="row" style="gap:8px;max-width:440px">
          <input class="input" type="password" v-model="passwordInput" placeholder="Server password"
                 @keydown.enter="savePassword" />
          <ui-button variant="primary" @click="savePassword" :disabled="!passwordInput">Save</ui-button>
        </div>
        <div class="row" style="gap:6px;margin-top:10px">
          <span class="dot" :class="{ on: hasPassword, warn: !hasPassword }"></span>
          <span class="small" :class="hasPassword ? 'muted' : 'faint'">
            {{ hasPassword ? "Password available for this session" : "No password for this session yet" }}
          </span>
        </div>
      </ui-card>

      <!-- notifications -->
      <ui-card title="Notifications" style="margin-bottom:20px">
        <div class="spread">
          <div>
            <div style="font-weight:650" class="small">Notify when a training finishes</div>
            <div class="small muted">Desktop notification + toast, even when this tab is in the background.</div>
          </div>
          <template v-if="!notifySupported">
            <ui-badge kind="neutral">Not supported here</ui-badge>
          </template>
          <template v-else>
            <ui-button v-if="Notification.permission !== 'granted'" size="sm" icon="bell" @click="askNotifyPermission">Enable</ui-button>
            <label v-else class="checkbox-row">
              <input type="checkbox" v-model="store.prefs.notifyOnDone" @change="persistPrefs" />
              {{ store.prefs.notifyOnDone ? "On" : "Off" }}
            </label>
          </template>
        </div>
      </ui-card>

      <!-- access -->
      <ui-card title="Access & security">
        <dl class="kv">
          <dt>Console URL</dt><dd class="mono">this page — share only inside your machine or LAN</dd>
          <dt>Access token</dt><dd>Every API call is authenticated with a token the server injected into this page. CLI calls need the <span class="mono">X-Auth-Token</span> header (token lives in <span class="mono">web/config/server.token</span>).</dd>
          <dt>LAN access</dt><dd>Other devices are rejected by default. Start the server with <span class="mono">--allowed-host your-ip</span> to allow them.</dd>
          <dt>Old interface</dt><dd>Still available at <a :href="'/web/'">/web/</a> while this console matures.</dd>
        </dl>
      </ui-card>
    </div>
  `,
};
