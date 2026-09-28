// Base UI kit: button, card, badge, modal, drawer, empty state, field,
// stat, toasts, progress, copyable text, spinner. Registered globally.

import { store, toast } from "../store.js";
import { t } from "../i18n.js";

const { computed } = Vue;

/* ---------- status mapping for jobs ---------- */

export function jobStatusMeta(job) {
  const status = String(job?.status || "").toLowerCase();
  const map = {
    queued:               { label: t("status.queued"),       kind: "neutral",  icon: "clock" },
    running:              { label: t("status.running"),      kind: "accent",   icon: "loader", pulse: true },
    detached:             { label: t("status.detached"),     kind: "warning",  icon: "clock" },
    uploading:            { label: t("status.uploading"),    kind: "accent",   icon: "loader", pulse: true },
    downloading:          { label: t("status.downloading"),  kind: "accent",   icon: "loader", pulse: true },
    completed:            { label: t("status.completed"),    kind: "success",  icon: "check-circle" },
    failed:               { label: t("status.failed"),       kind: "danger",   icon: "x-circle" },
    canceled:             { label: t("status.canceled"),     kind: "neutral",  icon: "x" },
    partial_success:      { label: t("status.partial"),      kind: "warning",  icon: "alert" },
    training_success_render_failed:   { label: t("status.renderFailed"),   kind: "warning", icon: "alert" },
    training_success_metrics_failed:  { label: t("status.metricsFailed"),  kind: "warning", icon: "alert" },
    training_success_postprocess_failed: { label: t("status.postprocFailed"), kind: "warning", icon: "alert" },
  };
  return map[status] || { label: status || "-", kind: "neutral", icon: "clock" };
}

export function formatBytes(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value <= 0) return "-";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let index = 0;
  let scaled = value;
  while (scaled >= 1024 && index < units.length - 1) { scaled /= 1024; index += 1; }
  return `${scaled >= 100 ? Math.round(scaled) : scaled.toFixed(1)} ${units[index]}`;
}

export function formatTime(value) {
  const ts = Number(value);
  if (!Number.isFinite(ts) || ts <= 0) return "-";
  const date = new Date(ts * 1000);
  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  const hm = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (sameDay) return hm;
  return `${date.toLocaleDateString([], { month: "short", day: "numeric" })} ${hm}`;
}

/* ---------- UiButton ---------- */

const UiButton = {
  name: "UiButton",
  props: {
    variant: { type: String, default: "secondary" },  // primary|secondary|ghost|danger
    size: { type: String, default: "md" },            // sm|md|lg
    icon: { type: String, default: "" },
    loading: Boolean,
    disabled: Boolean,
  },
  template: `
    <button class="btn" :class="[variant === 'primary' ? 'btn-primary' : variant === 'ghost' ? 'btn-ghost' : variant === 'danger' ? 'btn-danger' : 'btn-secondary', size === 'sm' ? 'btn-sm' : size === 'lg' ? 'btn-lg' : '']"
            :disabled="disabled || loading" v-bind="$attrs">
      <icon v-if="loading" name="loader" class="up-spin"></icon>
      <icon v-else-if="icon" :name="icon"></icon>
      <slot></slot>
    </button>
  `,
};

/* ---------- UiCard ---------- */

const UiCard = {
  name: "UiCard",
  props: { title: { type: String, default: "" }, dense: Boolean },
  template: `
    <div class="card" :class="{ 'card-pad-sm': dense }">
      <div v-if="title || $slots.actions" class="card-head">
        <div class="card-title">{{ title }}</div>
        <div class="row" v-if="$slots.actions"><slot name="actions"></slot></div>
      </div>
      <div class="card-body"><slot></slot></div>
    </div>
  `,
};

/* ---------- UiBadge ---------- */

const UiBadge = {
  name: "UiBadge",
  props: { kind: { type: String, default: "neutral" }, dot: Boolean, pulse: Boolean },
  template: `
    <span class="badge" :class="[kind, { pulse }]">
      <span v-if="dot || pulse" class="dot" :class="{ on: kind === 'success', warn: kind === 'warning' }"></span>
      <slot></slot>
    </span>
  `,
};

/* ---------- job status badge ---------- */

const JobBadge = {
  name: "JobBadge",
  props: { job: { type: Object, required: true } },
  setup(props) {
    const meta = computed(() => jobStatusMeta(props.job));
    return { meta };
  },
  components: { UiBadge },
  template: `
    <ui-badge :kind="meta.kind" :pulse="meta.pulse">
      <icon v-if="meta.pulse" :name="meta.icon" class="up-spin" style="width:11px;height:11px"></icon>
      {{ meta.label }}
    </ui-badge>
  `,
};

/* ---------- UiModal ---------- */

const UiModal = {
  name: "UiModal",
  props: { title: { type: String, default: "" }, width: { type: Number, default: 680 } },
  emits: ["close"],
  template: `
    <teleport to="body">
      <div class="modal-mask" @click.self="$emit('close')">
        <div class="modal" :style="{ width: 'min(' + width + 'px, calc(100vw - 48px))' }">
          <div class="modal-head">
            <div class="modal-title">{{ title }}</div>
            <button class="btn btn-ghost btn-sm btn-icon" @click="$emit('close')"><icon name="x"></icon></button>
          </div>
          <div class="modal-body"><slot></slot></div>
          <div v-if="$slots.footer" class="modal-foot"><slot name="footer"></slot></div>
        </div>
      </div>
    </teleport>
  `,
};

/* ---------- UiDrawer ---------- */

const UiDrawer = {
  name: "UiDrawer",
  props: { title: { type: String, default: "" }, subtitle: { type: String, default: "" } },
  emits: ["close"],
  template: `
    <teleport to="body">
      <div class="drawer-mask" @click="$emit('close')"></div>
      <div class="drawer">
        <div class="drawer-head">
          <div style="min-width:0">
            <div class="modal-title ellipsis">{{ title }}</div>
            <div v-if="subtitle" class="small faint ellipsis mono">{{ subtitle }}</div>
          </div>
          <button class="btn btn-ghost btn-sm btn-icon" @click="$emit('close')"><icon name="x"></icon></button>
        </div>
        <div class="drawer-body"><slot></slot></div>
        <div v-if="$slots.footer" class="drawer-foot"><slot name="footer"></slot></div>
      </div>
    </teleport>
  `,
};

/* ---------- UiEmpty ---------- */

const UiEmpty = {
  name: "UiEmpty",
  props: { icon: { type: String, default: "box" }, title: { type: String, default: "" }, hint: { type: String, default: "" } },
  template: `
    <div class="empty">
      <icon :name="icon"></icon>
      <div class="empty-title">{{ title }}</div>
      <div class="empty-hint" v-if="hint">{{ hint }}</div>
      <slot></slot>
    </div>
  `,
};

/* ---------- UiField ---------- */

const UiField = {
  name: "UiField",
  props: { label: { type: String, default: "" }, hint: { type: String, default: "" }, required: Boolean },
  template: `
    <div class="field">
      <label class="field-label" v-if="label">{{ label }} <span v-if="required" class="req">*</span></label>
      <slot></slot>
      <div class="field-hint" v-if="hint">{{ hint }}</div>
    </div>
  `,
};

/* ---------- UiStat ---------- */

const UiStat = {
  name: "UiStat",
  props: { label: { type: String, required: true }, value: { type: [String, Number], default: "-" }, hint: { type: String, default: "" } },
  template: `
    <div class="stat">
      <div class="stat-label">{{ label }}</div>
      <div class="stat-value">{{ value }}</div>
      <div class="stat-hint" v-if="hint">{{ hint }}</div>
    </div>
  `,
};

/* ---------- UiProgress ---------- */

const UiProgress = {
  name: "UiProgress",
  props: { value: { type: Number, default: 0 }, success: Boolean },
  computed: {
    clamped() { return Math.max(0, Math.min(100, this.value)); },
  },
  template: `
    <div class="progress" :class="{ success }"><div :style="{ width: clamped + '%' }"></div></div>
  `,
};

/* ---------- UiCopy (click-to-copy text) ---------- */

const UiCopy = {
  name: "UiCopy",
  props: { text: { type: String, default: "" }, label: { type: String, default: "" } },
  data() { return { copied: false }; },
  methods: {
    async copy() {
      try {
        await navigator.clipboard.writeText(this.text);
        this.copied = true;
        setTimeout(() => { this.copied = false; }, 1400);
      } catch {
        toast("Copy failed — select the text manually", "error");
      }
    },
  },
  template: `
    <button class="btn btn-ghost btn-sm" :title="text" @click.stop="copy" style="max-width:100%">
      <icon :name="copied ? 'check' : 'copy'"></icon>
      <span class="ellipsis" v-if="label">{{ label }}</span>
    </button>
  `,
};

/* ---------- UiToasts ---------- */

const UiToasts = {
  name: "UiToasts",
  setup() { return { store }; },
  template: `
    <teleport to="body">
      <div class="toasts">
        <div v-for="item in store.toasts" :key="item.id" class="toast" :class="item.kind">
          <icon :name="item.kind === 'success' ? 'check-circle' : item.kind === 'error' ? 'x-circle' : 'info'"></icon>
          <div style="min-width:0">{{ item.message }}</div>
          <button class="toast-close" @click="store.toasts = store.toasts.filter(t => t.id !== item.id)">×</button>
        </div>
      </div>
    </teleport>
  `,
};

/* ---------- UiSpinner (page loading) ---------- */

const UiSpinner = {
  name: "UiSpinner",
  props: { label: { type: String, default: "" } },
  computed: { text() { return this.label || t("common.loading"); } },
  template: `
    <div class="empty">
      <icon name="loader" class="up-spin" style="width:26px;height:26px"></icon>
      <div class="small muted">{{ text }}</div>
    </div>
  `,
};

export const uiComponents = {
  "icon": { template: `<span></span>` },  // replaced in app.js with the real Icon
  "ui-button": UiButton,
  "ui-card": UiCard,
  "ui-badge": UiBadge,
  "job-badge": JobBadge,
  "ui-modal": UiModal,
  "ui-drawer": UiDrawer,
  "ui-empty": UiEmpty,
  "ui-field": UiField,
  "ui-stat": UiStat,
  "ui-progress": UiProgress,
  "ui-copy": UiCopy,
  "ui-toasts": UiToasts,
  "ui-spinner": UiSpinner,
};

export const uiHelpers = { jobStatusMeta, formatBytes, formatTime };
