// New Training wizard — three steps: Data → Algorithm → Submit.
// One click on Submit runs precheck + preview + confirmation internally.

import { store, remoteConfig, hasPassword, activeProfile, toast, setRunName, navigate, defaultProfile, pollOnce } from "../store.js";
import { getAlgorithms, remoteCheck, previewRun, submitRun, streamFrame, materializeSession } from "../api.js";
import { parseSshCommand } from "../ssh-parse.js";

const { reactive, computed, watch, onMounted } = Vue;

const TRAINING_DEFAULTS = {
  iterations: 30000,
  voxel_size: 0.001,
  update_init_factor: 16,
  lmbda: 0.001,
  save_interval: 10000,
  feature_lr: 0.0075,
  opacity_lr: 0.02,
  scaling_lr: 0.007,
  rotation_lr: 0.002,
};

const PRESETS = [
  { id: "preview", label: "Quick preview", iterations: 3000, hint: "3k iterations — check the pipeline works, ~10 min" },
  { id: "standard", label: "Standard", iterations: 30000, hint: "30k iterations — paper-quality run" },
  { id: "custom", label: "Custom", iterations: 0, hint: "Set your own parameters" },
];

function newDraft() {
  return reactive({
    step: 1,
    // -- data --
    source: "upload",
    sessionId: "session-" + Date.now().toString(36),
    captureId: "",
    datasetName: "",
    files: [],
    uploading: false,
    uploadDone: 0,
    materialized: null,
    save_interval: 10000,
    remoteDatasets: [],
    loadingRemoteDatasets: false,
    selectedRemoteDatasetId: "",
    remoteDatasetPath: "",
    autoColmap: true,
    // -- algorithm --
    family: "",
    iterations: 30000,
    preset: "standard",
    checkpointPath: "",
    runLabel: "",
    // -- submit --
    precheckResult: null,
    prechecking: false,
    preview: null,
    previewing: false,
    submitting: false,
    confirmOpen: false,
  });
}

export default {
  name: "WizardPage",
  setup() {
    const draft = newDraft();
    const algorithms = Vue.ref([]);
    const loadingAlgorithms = Vue.ref(true);

    onMounted(async () => {
      try {
        const data = await getAlgorithms();
        algorithms.value = (data.algorithms || []).filter(
          (a) => a.operations && a.operations.train && a.operations.train.enabled,
        );
        if (!draft.family && algorithms.value.length) draft.family = algorithms.value[0].family;
      } catch (err) {
        toast(`Failed to load algorithms: ${err.message}`, "error");
      } finally {
        loadingAlgorithms.value = false;
      }
    });

    const trainable = computed(() => algorithms.value);
    const selectedAlgorithm = computed(() => trainable.value.find((a) => a.family === draft.family) || null);
    const profile = computed(() => activeProfile());

    const canNext = computed(() => {
      if (draft.step === 1) {
        if (draft.source === "upload") return draft.materialized !== null;
        return Boolean(draft.selectedRemoteDatasetId || draft.remoteDatasetPath.trim());
      }
      if (draft.step === 2) return Boolean(draft.family);
      return false;
    });

    const uploadLabel = computed(() =>
      draft.uploading ? `Uploading ${draft.uploadDone}/${draft.files.length}…` : `Upload ${draft.files.length} image${draft.files.length === 1 ? "" : "s"}`,
    );

    function addFiles(fileList) {
      const images = [...fileList].filter((f) => /\.(png|jpe?g|bmp|webp|tiff?)$/i.test(f.name));
      if (!images.length) { toast("No image files found (png/jpg/webp/tiff)", "error"); return; }
      draft.files = images;
      if (!draft.datasetName) {
        draft.datasetName = images[0].name.replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9_-]+/g, "-").slice(0, 32) || "scene";
      }
      draft.materialized = null;
    }

    function onDrop(event) {
      draft.source = "upload";
      if (event.dataTransfer?.files?.length) addFiles(event.dataTransfer.files);
    }

    async function pickRemoteDatasets() {
      draft.loadingRemoteDatasets = true;
      draft.remoteDatasets = [];
      try {
        if (!profile.value || !hasPassword()) {
          throw new Error("Set up a connection profile and its password first (Settings).");
        }
        const data = await remoteCheck({
          remote: remoteConfig(),
          timeout_seconds: 20,
          algorithm_family: draft.family || "",
          include_datasets: true,
        });
        draft.remoteDatasets = data.result?.datasets || [];
        if (!draft.remoteDatasets.length) toast("No reusable datasets found on the server", "info");
      } catch (err) {
        toast(`Could not list remote datasets: ${err.message}`, "error", 7000);
      } finally {
        draft.loadingRemoteDatasets = false;
      }
    }

    async function uploadAndMaterialize() {
      if (!draft.files.length) throw new Error("Pick images first.");
      if (!draft.datasetName.trim()) throw new Error("Give the dataset a name.");
      draft.uploading = true;
      draft.uploadDone = 0;
      try {
        for (const file of draft.files) {
          const dataUrl = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result);
            reader.onerror = () => reject(new Error(`Failed to read ${file.name}`));
            reader.readAsDataURL(file);
          });
          const resp = await streamFrame({
            session_id: draft.sessionId,
            capture_id: draft.captureId,
            image_data: dataUrl,
            filename: file.name,
          });
          if (!draft.captureId && resp.capture_id) draft.captureId = resp.capture_id;
          draft.uploadDone += 1;
        }
        const result = await materializeSession({
          session_id: draft.sessionId,
          capture_id: draft.captureId,
          dataset_name: draft.datasetName.trim(),
        });
        draft.materialized = result.result || result;
        toast(`Uploaded ${draft.files.length} images as dataset "${draft.datasetName.trim()}"`, "success");
      } finally {
        draft.uploading = false;
      }
    }

    async function goNext() {
      if (draft.step === 1) {
        if (draft.source === "upload" && !draft.materialized) {
          await uploadAndMaterialize();
        }
        draft.step = 2;
        return;
      }
      if (draft.step === 2) {
        if (!draft.runLabel.trim()) {
          draft.runLabel = `${draft.source === "upload" ? draft.datasetName : draft.selectedRemoteDatasetId || "remote"}-${draft.family}-${new Date().toISOString().slice(5, 16).replace("T", "-")}`;
        }
        draft.step = 3;
        submitAll();
      }
    }

    /* ---- one-click submit: precheck → preview → confirm → submit ---- */

    async function submitAll() {
      try {
        if (!profile.value) throw new Error("No connection profile. Create one in Settings.");
        if (!hasPassword()) throw new Error("Enter the server password for this profile (Settings, stored for this browser session only).");

        draft.prechecking = true;
        draft.precheckResult = null;
        draft.preview = null;
        const check = await remoteCheck({
          remote: remoteConfig(),
          timeout_seconds: 20,
          algorithm_family: draft.family,
          use_existing_remote_dataset: draft.source === "remote",
          check_colmap_required: draft.source === "upload" && draft.autoColmap,
        });
        draft.precheckResult = check.result || {};
        if (check.result?.checks?.some((c) => c.required && !c.ok)) {
          throw new Error("Remote precheck failed — fix the failing items and submit again.");
        }
        draft.prechecking = false;

        draft.previewing = true;
        const previewData = await previewRun(buildPayload({}));
        draft.preview = previewData.preview || {};
      } catch (err) {
        toast(err.message, "error", 9000);
        draft.prechecking = false;
        draft.previewing = false;
      } finally {
        draft.previewing = false;
      }
    }

    function buildPayload(extra) {
      const training = { ...TRAINING_DEFAULTS, iterations: draft.iterations };
      return {
        algorithm_family: draft.family,
        operation: "train",
        session_id: draft.sessionId,
        capture_id: draft.captureId,
        dataset_name: draft.datasetName.trim() || draft.runLabel,
        auto_materialize: draft.source === "upload",
        auto_colmap: draft.autoColmap,
        use_existing_remote_dataset: draft.source === "remote",
        remote_dataset_id: draft.selectedRemoteDatasetId || "",
        remote_dataset_path: draft.remoteDatasetPath.trim(),
        workspace: "",
        output_dir: "",
        checkpoint_path: draft.checkpointPath.trim(),
        input_path: "",
        preview_job_id: "",
        remote: remoteConfig(),
        require_remote_check: true,
        ...training,
        ...extra,
      };
    }

    async function confirmSubmit() {
      draft.confirmOpen = false;
      draft.submitting = true;
      try {
        const pathConfirmation = {
          ...(draft.preview.path_confirmation || {}),
          confirmed: true,
          confirmed_at: new Date().toISOString(),
        };
        const data = await submitRun(buildPayload({
          output_dir: draft.preview.local_output_dir || "",
          path_confirmation: pathConfirmation,
          command_override: draft.preview.command_override || "",
        }));
        if (data.job?.id) {
          setRunName(data.job.id, draft.runLabel.trim() || data.job.id);
          await pollOnce();
          toast(`Submitted: ${draft.runLabel}`, "success", 6000);
          navigate("runs", data.job.id);
        }
      } catch (err) {
        toast(`Submit failed: ${err.message}`, "error", 9000);
      } finally {
        draft.submitting = false;
      }
    }

    function applyPreset(presetId) {
      draft.preset = presetId;
      const preset = PRESETS.find((p) => p.id === presetId);
      if (preset && preset.iterations) draft.iterations = preset.iterations;
    }

    /* connection helper: quick-create profile from pasted ssh command */
    const quickSsh = Vue.ref("");
    function quickConnect() {
      const parsed = parseSshCommand(quickSsh.value);
      if (!parsed || !parsed.host) { toast("Could not parse that — expected something like ssh -p 10526 root@1.2.3.4", "error"); return; }
      const profile = defaultProfile();
      profile.host = parsed.host;
      profile.port = parsed.port || 22;
      profile.username = parsed.username || "root";
      profile.label = profile.host;
      store.profiles.push(profile);
      store.activeProfileId = profile.id;
      store.persistProfiles();
      quickSsh.value = "";
      toast(`Profile "${profile.label}" created — add its password in Settings`, "success", 6000);
    }

    return {
      draft, algorithms, loadingAlgorithms, trainable, selectedAlgorithm, profile,
      canNext, uploadLabel, addFiles, onDrop, pickRemoteDatasets, goNext,
      submitAll, confirmSubmit, applyPreset, PRESETS, TRAINING_DEFAULTS,
      quickSsh, quickConnect, hasPassword,
    };
  },
  template: `
    <div class="page" style="max-width: 860px">
      <div class="stepper">
        <div class="step" :class="{ active: draft.step === 1, done: draft.step > 1 }">
          <span class="step-num">{{ draft.step > 1 ? "✓" : "1" }}</span> Data
        </div>
        <div class="step-line"></div>
        <div class="step" :class="{ active: draft.step === 2, done: draft.step > 2 }">
          <span class="step-num">{{ draft.step > 2 ? "✓" : "2" }}</span> Algorithm
        </div>
        <div class="step-line"></div>
        <div class="step" :class="{ active: draft.step === 3 }">
          <span class="step-num">3</span> Submit
        </div>
      </div>

      <!-- STEP 1: DATA -->
      <ui-card v-if="draft.step === 1" title="Choose your data">
        <div class="grid-2" style="margin-bottom:16px">
          <button class="choice" :class="{ selected: draft.source === 'upload' }" @click="draft.source = 'upload'">
            <div class="choice-title"><icon name="upload"></icon> Upload new images</div>
            <div class="choice-desc">Pick a folder of scene photos; COLMAP preprocessing runs on the server automatically.</div>
          </button>
          <button class="choice" :class="{ selected: draft.source === 'remote' }" @click="draft.source = 'remote'; pickRemoteDatasets()">
            <div class="choice-title"><icon name="database"></icon> Reuse data on the server</div>
            <div class="choice-desc">Train again on a dataset that already lives on the GPU machine.</div>
          </button>
        </div>

        <template v-if="draft.source === 'upload'">
          <div class="dropzone" :class="{ over: false }"
               @click="$refs.fileInput.click()"
               @dragover.prevent="$event.currentTarget.classList.add('over')"
               @dragleave="$event.currentTarget.classList.remove('over')"
               @drop.prevent="onDrop($event)">
            <icon name="upload"></icon>
            <div><strong>Click to choose images</strong> or drop them here</div>
            <div class="small" style="margin-top:2px">8–12+ sharp photos of the scene work best (png / jpg / webp / tiff)</div>
          </div>
          <input ref="fileInput" type="file" multiple accept="image/*" hidden
                 @change="addFiles($event.target.files); $event.target.value = ''" />

          <div v-if="draft.files.length" style="margin-top:14px">
            <div class="row-wrap" style="margin-bottom:10px">
              <ui-field label="Dataset name" style="flex:1;max-width:320px">
                <input class="input" v-model="draft.datasetName" placeholder="e.g. garden-table" />
              </ui-field>
              <label class="checkbox-row" style="margin-top:20px">
                <input type="checkbox" v-model="draft.autoColmap" />
                Run COLMAP on the server when no sparse model exists
              </label>
            </div>
            <div class="chip-list" style="margin-bottom:12px">
              <span class="chip" v-for="f in draft.files.slice(0, 8)" :key="f.name">{{ f.name }}</span>
              <span class="chip" v-if="draft.files.length > 8">+{{ draft.files.length - 8 }} more</span>
            </div>
            <div v-if="draft.materialized" class="check-item ok">
              <icon name="check-circle"></icon>
              <div><span class="ci-name">Dataset ready on the server.</span>
              <span class="ci-msg">{{ draft.files.length }} images staged — continue to the algorithm step.</span></div>
            </div>
          </div>
        </template>

        <template v-else>
          <div class="row-wrap" style="margin-bottom:12px">
            <ui-button size="sm" :loading="draft.loadingRemoteDatasets" icon="refresh" @click="pickRemoteDatasets">Refresh list</ui-button>
            <span class="small faint" v-if="draft.loadingRemoteDatasets">Scanning the server workspace…</span>
          </div>
          <ui-empty v-if="!draft.remoteDatasets.length && !draft.loadingRemoteDatasets" icon="database"
                    title="Nothing listed yet" hint="Click Refresh to scan the server workspace for reusable datasets."></ui-empty>
          <div v-else class="stack" style="gap:8px">
            <button v-for="ds in draft.remoteDatasets" :key="ds.id" class="choice" style="padding:12px 16px"
                    :class="{ selected: draft.selectedRemoteDatasetId === ds.id }"
                    @click="draft.selectedRemoteDatasetId = ds.id; draft.remoteDatasetPath = ds.path || ''">
              <div class="spread">
                <div>
                  <div class="choice-title">{{ ds.name || ds.id }}</div>
                  <div class="choice-desc mono">{{ ds.path }}</div>
                </div>
                <ui-badge kind="neutral">{{ ds.stage_label || "dataset" }}</ui-badge>
              </div>
            </button>
            <ui-field label="Or use a custom path on the server" hint="Points at a workspace directory with images (and optionally a COLMAP sparse model)">
              <input class="input mono" v-model="draft.remoteDatasetPath" placeholder="/root/autodl-tmp/…/workspace" />
            </ui-field>
          </div>
        </template>

        <div class="row" style="justify-content:flex-end;margin-top:20px">
          <ui-button variant="primary" :disabled="!canNext" :loading="draft.uploading"
                     @click="goNext">
            <span v-if="draft.uploading">{{ uploadLabel }}</span>
            <template v-else>Next: Algorithm <icon name="arrow-right"></icon></template>
          </ui-button>
        </div>
      </ui-card>

      <!-- STEP 2: ALGORITHM -->
      <ui-card v-if="draft.step === 2" title="Pick the algorithm and scale">
        <div class="grid-2">
          <ui-field label="Algorithm" hint="Only algorithms with training enabled are listed">
            <select class="select" v-model="draft.family">
              <option v-for="a in trainable" :key="a.family" :value="a.family">{{ a.label || a.family }}</option>
            </select>
          </ui-field>
          <ui-field label="Run name" hint="Shown in the runs list instead of an opaque id">
            <input class="input" v-model="draft.runLabel" placeholder="auto-generated if left empty" />
          </ui-field>
        </div>

        <div class="grid-3" style="margin-top:16px">
          <button v-for="preset in PRESETS" :key="preset.id" class="choice" :class="{ selected: draft.preset === preset.id }"
                  style="padding:12px 14px" @click="applyPreset(preset.id)">
            <div class="choice-title" style="font-size:var(--fs-sm)">{{ preset.label }}</div>
            <div class="choice-desc">{{ preset.hint }}</div>
          </button>
        </div>

        <div v-if="draft.preset === 'custom'" class="grid-4" style="margin-top:14px">
          <ui-field label="Iterations"><input class="input num" type="number" v-model.number="draft.iterations" min="100" /></ui-field>
          <ui-field label="Save interval"><input class="input num" type="number" v-model.number="draft.save_interval" min="500" /></ui-field>
          <ui-field label="λ (rate weight)"><input class="input num" v-model.number="draft.lmbda" /></ui-field>
          <ui-field label="Voxel size"><input class="input num" v-model.number="draft.voxel_size" /></ui-field>
        </div>

        <div style="margin-top:16px">
          <ui-field label="Checkpoint to start from (optional)" hint="Leave empty to train from scratch">
            <input class="input mono" v-model="draft.checkpointPath" placeholder="/path/on/server/chkpth/3000" />
          </ui-field>
        </div>

        <div class="row" style="justify-content:space-between;margin-top:20px">
          <ui-button icon="arrow-left" @click="draft.step = 1">Back</ui-button>
          <ui-button variant="primary" :disabled="!canNext" @click="goNext">
            Submit run <icon name="zap"></icon>
          </ui-button>
        </div>
      </ui-card>

      <!-- STEP 3: SUBMIT -->
      <ui-card v-if="draft.step === 3" title="Submitting your run">
        <div class="check-list" style="margin-bottom:16px">
          <div class="check-item" :class="draft.precheckResult ? 'ok' : 'warn'">
            <icon :name="draft.prechecking ? 'loader' : draft.precheckResult ? 'check-circle' : 'loader'"
                  :class="{ 'up-spin': draft.prechecking }"></icon>
            <div><span class="ci-name">Remote environment check</span>
              <div class="ci-msg" v-if="draft.prechecking">Testing SSH, paths, python, tmux…</div>
              <div class="ci-msg" v-else-if="draft.precheckResult">{{ (draft.precheckResult.checks || []).filter(c => c.ok).length }}/{{ (draft.precheckResult.checks || []).length }} checks passed</div>
              <div class="ci-msg" v-else>queued</div>
            </div>
          </div>
          <div class="check-item" :class="draft.preview ? 'ok' : draft.precheckResult ? 'fail' : 'warn'">
            <icon :name="draft.previewing ? 'loader' : draft.preview ? 'check-circle' : draft.precheckResult ? 'x-circle' : 'loader'"
                  :class="{ 'up-spin': draft.previewing }"></icon>
            <div><span class="ci-name">Command preview</span>
              <div class="ci-msg" v-if="draft.previewing">Building the exact remote command…</div>
              <div class="ci-msg" v-else-if="draft.preview">Ready — confirm below to launch</div>
              <div class="ci-msg" v-else-if="draft.precheckResult">Blocked by the failed check above</div>
            </div>
          </div>
        </div>

        <template v-if="draft.preview">
          <div class="field" style="margin-bottom:14px">
            <div class="field-label">Remote command</div>
            <div class="codeblock">{{ draft.preview.remote_command || draft.preview.shell_command || draft.preview.command_override }}</div>
          </div>
          <dl class="kv" style="margin-bottom:18px">
            <dt>Remote output</dt><dd class="mono">{{ draft.preview.output_dir || "-" }}</dd>
            <dt>Local download</dt><dd class="mono">{{ draft.preview.local_output_dir || "-" }}</dd>
          </dl>
          <div class="row" style="justify-content:flex-end;gap:10px">
            <ui-button icon="arrow-left" @click="draft.step = 2">Back</ui-button>
            <ui-button variant="primary" size="lg" icon="zap" :loading="draft.submitting" @click="draft.confirmOpen = true">
              Launch training
            </ui-button>
          </div>
        </template>
        <div v-else class="row" style="justify-content:flex-start;margin-top:6px">
          <ui-button icon="rotate" @click="submitAll" :disabled="draft.prechecking || draft.previewing">Retry</ui-button>
        </div>
      </ui-card>

      <!-- confirm modal -->
      <ui-modal v-if="draft.confirmOpen" title="Launch this training?" :width="620" @close="draft.confirmOpen = false">
        <p class="small muted" style="margin-bottom:12px">
          The command below will run on <strong>{{ profile ? (profile.label || profile.host) : "the server" }}</strong> inside a tmux session.
          You can close this browser tab afterwards — training keeps running and results download automatically.
        </p>
        <div class="codeblock">{{ draft.preview?.remote_command || draft.preview?.shell_command }}</div>
        <template #footer>
          <ui-button @click="draft.confirmOpen = false">Cancel</ui-button>
          <ui-button variant="primary" icon="zap" :loading="draft.submitting" @click="confirmSubmit">Confirm & run</ui-button>
        </template>
      </ui-modal>
    </div>
  `,
};
