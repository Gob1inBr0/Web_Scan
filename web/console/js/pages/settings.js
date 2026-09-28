// Settings — connection profiles (multi, ssh-command paste), password per
// session, notifications, access info.

import { store, persistProfiles, persistPrefs, defaultProfile, activeProfile, sessionPasswords, toast, navigate } from "../store.js";
import { parseSshCommand } from "../ssh-parse.js";
import { t } from "../i18n.js";

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
        toast(t("set.parseFail"), "error");
        return;
      }
      if (!editing.value) newProfile();
      if (parsed.host) editing.value.host = parsed.host;
      if (parsed.port) editing.value.port = parsed.port;
      if (parsed.username) editing.value.username = parsed.username;
      if (!editing.value.label) editing.value.label = parsed.host;
      pasteSsh.value = "";
      toast(t("wiz.profileCreated"), "success");
    }

    function saveProfile() {
      const profile = editing.value;
      if (!profile.host.trim()) { toast(t("set.hostRequired"), "error"); return; }
      if (!profile.label.trim()) profile.label = profile.host;
      const index = store.profiles.findIndex((p) => p.id === profile.id);
      if (index >= 0) store.profiles[index] = profile;
      else store.profiles.push(profile);
      store.activeProfileId = profile.id;
      persistProfiles();
      editing.value = null;
      toast(`${t("set.profileSaved")}: ${profile.label}`, "success");
    }

    function removeProfile(profile) {
      if (!confirm(`${t("set.profileRemovedConfirm")}: ${profile.label || profile.host}?`)) return;
      store.profiles = store.profiles.filter((p) => p.id !== profile.id);
      if (store.activeProfileId === profile.id) {
        store.activeProfileId = store.profiles[0]?.id || "";
      }
      persistProfiles();
    }

    function switchProfile(profile) {
      store.activeProfileId = profile.id;
      persistProfiles();
      toast(`${t("set.switchedTo")} "${profile.label || profile.host}"`, "info");
    }

    function savePassword() {
      if (!active.value) { toast(t("set.needProfileFirst"), "error"); return; }
      sessionPasswords.set(active.value.id, passwordInput.value);
      passwordInput.value = "";
      toast(t("set.passwordStored"), "success");
    }

    async function askNotifyPermission() {
      if (!notifySupported) return;
      if (Notification.permission !== "granted") {
        const result = await Notification.requestPermission();
        if (result !== "granted") { toast(t("set.notifyDenied"), "error"); return; }
      }
      store.prefs.notifyOnDone = true;
      persistPrefs();
      toast(t("set.notifyEnabled"), "success");
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
          <div class="page-title">{{ $t("set.title") }}</div>
          <div class="page-sub">{{ $t("set.sub") }}</div>
        </div>
        <div class="page-actions">
          <ui-button variant="primary" icon="plus" @click="newProfile">{{ $t("set.newProfile") }}</ui-button>
        </div>
      </div>

      <!-- profiles -->
      <ui-card :title="$t('set.profiles')" style="margin-bottom:20px">
        <template #actions>
          <span class="faint small">{{ store.profiles.length }} {{ $t("set.saved") }}</span>
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
            <ui-button v-if="profile.id !== store.activeProfileId" size="sm" variant="ghost" @click="switchProfile(profile)">{{ $t("set.use") }}</ui-button>
            <ui-button size="sm" variant="ghost" icon="settings" @click="editProfile(profile)">{{ $t("set.edit") }}</ui-button>
            <ui-button size="sm" variant="ghost" icon="trash" @click="removeProfile(profile)"></ui-button>
          </div>
          <div v-if="!store.profiles.length" class="small muted">{{ $t("set.noProfiles") }}</div>
        </div>

        <template v-if="editing">
          <div class="card" style="background:var(--bg-inset);padding:16px">
            <ui-field :label="$t('set.pasteSsh')" :hint="$t('set.pasteSshHint')"
                      style="margin-bottom:12px">
              <div class="row" style="gap:8px">
                <input class="input mono" style="flex:1" v-model="pasteSsh" placeholder="ssh -p 22 root@your.server" @keydown.enter="applyPaste" />
                <ui-button icon="zap" @click="applyPaste">{{ $t("set.parse") }}</ui-button>
              </div>
            </ui-field>
            <div class="grid-3" style="margin-bottom:12px">
              <ui-field :label="$t('set.label')"><input class="input" v-model="editing.label" placeholder="My GPU box" /></ui-field>
              <ui-field :label="$t('set.host')" required><input class="input" v-model="editing.host" placeholder="connect.gpu-cloud.com" /></ui-field>
              <div class="grid-2" style="gap:8px">
                <ui-field :label="$t('set.port')"><input class="input num" type="number" v-model.number="editing.port" /></ui-field>
                <ui-field :label="$t('set.user')"><input class="input" v-model="editing.username" /></ui-field>
              </div>
            </div>
            <div class="grid-2" style="margin-bottom:12px">
              <ui-field :label="$t('set.repoPath')" :hint="$t('set.repoPathHint')"><input class="input mono" v-model="editing.repo_path" placeholder="/root/autodl-tmp/Web_Scan" /></ui-field>
              <ui-field :label="$t('set.python')" :hint="$t('set.pythonHint')"><input class="input mono" v-model="editing.python" /></ui-field>
            </div>
            <div class="grid-2" style="margin-bottom:12px">
              <ui-field :label="$t('set.workspaceRoot')" :hint="$t('set.workspaceHint')"><input class="input mono" v-model="editing.workspace_root" /></ui-field>
              <ui-field :label="$t('set.outputRoot')" :hint="$t('set.outputHint')"><input class="input mono" v-model="editing.output_root" /></ui-field>
            </div>
            <ui-field :label="$t('set.activateCmd')" :hint="$t('set.activateHint')"
                      style="margin-bottom:14px">
              <input class="input mono" v-model="editing.activate_cmd" placeholder="source … && conda activate env" />
            </ui-field>
            <div class="row" style="justify-content:flex-end;gap:8px">
              <ui-button variant="ghost" @click="editing = null">{{ $t("wiz.cancel") }}</ui-button>
              <ui-button variant="primary" icon="check" @click="saveProfile">{{ $t("set.save") }}</ui-button>
            </div>
          </div>
        </template>
      </ui-card>

      <!-- password for active profile -->
      <ui-card :title="$t('set.password')" style="margin-bottom:20px">
        <p class="small muted" style="margin-bottom:12px">
          {{ $t("set.passwordFor") }} <strong>{{ active ? (active.label || active.host) : "—" }}</strong>.
          {{ $t("set.passwordBody") }}
        </p>
        <div class="row" style="gap:8px;max-width:440px">
          <input class="input" type="password" v-model="passwordInput" :placeholder="$t('set.passwordPlaceholder')"
                 @keydown.enter="savePassword" />
          <ui-button variant="primary" @click="savePassword" :disabled="!passwordInput">{{ $t("set.save") }}</ui-button>
        </div>
        <div class="row" style="gap:6px;margin-top:10px">
          <span class="dot" :class="{ on: hasPassword, warn: !hasPassword }"></span>
          <span class="small" :class="hasPassword ? 'muted' : 'faint'">
            {{ hasPassword ? $t("set.passwordReady") : $t("set.passwordMissing") }}
          </span>
        </div>
      </ui-card>

      <!-- notifications -->
      <ui-card :title="$t('set.notifications')" style="margin-bottom:20px">
        <div class="spread">
          <div>
            <div style="font-weight:650" class="small">{{ $t("set.notifyOnDone") }}</div>
            <div class="small muted">{{ $t("set.notifyHint") }}</div>
          </div>
          <template v-if="!notifySupported">
            <ui-badge kind="neutral">{{ $t("set.notSupported") }}</ui-badge>
          </template>
          <template v-else>
            <ui-button v-if="Notification.permission !== 'granted'" size="sm" icon="bell" @click="askNotifyPermission">{{ $t("set.enable") }}</ui-button>
            <label v-else class="checkbox-row">
              <input type="checkbox" v-model="store.prefs.notifyOnDone" @change="persistPrefs" />
              {{ store.prefs.notifyOnDone ? $t("set.on") : $t("set.off") }}
            </label>
          </template>
        </div>
      </ui-card>

      <!-- access -->
      <ui-card :title="$t('set.access')">
        <dl class="kv">
          <dt>{{ $t("set.consoleUrl") }}</dt><dd class="mono">{{ $t("set.consoleUrlValue") }}</dd>
          <dt>{{ $t("set.accessToken") }}</dt><dd>{{ $t("set.accessTokenValue") }}</dd>
          <dt>{{ $t("set.lan") }}</dt><dd>{{ $t("set.lanValue") }}</dd>
          <dt>{{ $t("set.oldUi") }}</dt><dd>{{ $t("set.oldUiValue") }}</dd>
        </dl>
      </ui-card>
    </div>
  `,
};
