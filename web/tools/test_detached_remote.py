#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import stat
import sys
import uuid
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from web.server import api_server, remote_executor
from web.server.remote_executor import (
  RemoteExecutionError,
  _build_remote_download_plan,
  _build_remote_cancel_script,
  _build_remote_detached_run_script,
  _build_remote_tmux_start_command,
  _download_directory,
  _list_remote_datasets,
  _remote_job_paths,
  _tmux_install_candidates,
  _verify_remote_result_marker,
  build_remote_paths,
  build_remote_run_key,
  build_remote_tmux_attach_command,
  build_remote_tmux_session_name,
)


def test_detached_script_contract() -> None:
  validated = {
    "python": "python3",
    "repo_path": "/remote/repo",
    "activate_cmd": "source /opt/env/bin/activate",
  }
  run_key = build_remote_run_key("Demo Dataset", "hac", "2026-05-14T10:25:30Z")
  remote_paths = build_remote_paths(
    workspace_root="/tmp/web_scan/workspaces",
    output_root="/tmp/web_scan/outputs",
    session_id="session",
    family="hac",
    job_id="job",
    run_key=run_key,
  )
  job_paths = _remote_job_paths(remote_paths["output_dir"])
  script = _build_remote_detached_run_script(
    validated=validated,
    job_id="job-1",
    family="hac",
    remote_paths=remote_paths,
    remote_job_paths=job_paths,
    remote_workspace_for_command="/tmp/web_scan/workspaces/datasets/hac/demo/workspace",
    remote_command="python train.py -s /tmp/source -m /tmp/out",
    auto_colmap=True,
    resolved_dataset_id="demo",
    resolved_dataset_name="Demo",
    use_existing_remote_dataset=False,
  )
  tmux_session = build_remote_tmux_session_name(family="hac", job_id="job 1:unsafe/name", run_key=run_key)
  start_command = _build_remote_tmux_start_command(job_paths, tmux_session)
  attach_command = build_remote_tmux_attach_command(
    {
      "host": "10.0.0.5",
      "port": 2222,
      "username": "ubuntu",
      "password": "secret-password",
      "repo_path": "/remote/repo",
      "workspace_root": "/tmp/web_scan/workspaces",
      "output_root": "/tmp/web_scan/outputs",
      "python": "python3",
      "activate_cmd": "",
    },
    tmux_session,
  )

  assert "status.json" in script
  assert "runtime.log" in script
  assert "echo \"$$\" > \"$PID_FILE\"" in script
  assert "os.replace(tmp_path, status_path)" in script
  assert "start_heartbeat" in script
  assert "stop_heartbeat" in script
  assert "EXIT_CODE_FILE" in script
  assert "sequential_matcher" in script
  assert "partial_success" not in script
  assert "tmux new-session" in start_command
  assert '"status": "starting"' in start_command
  assert "status_tmp=" in start_command
  assert "rm -f '/tmp/web_scan/outputs/runs/demo-dataset-hac-20260514T102530Z/output/.wgsc_job/status.json'" not in start_command
  assert "tee -a" in start_command
  assert "setsid" not in start_command
  assert "nohup" not in start_command
  assert run_key == "demo-dataset-hac-20260514T102530Z"
  assert remote_paths["workspace_dir"] == "/tmp/web_scan/workspaces/runs/demo-dataset-hac-20260514T102530Z/workspace"
  assert remote_paths["output_dir"] == "/tmp/web_scan/outputs/runs/demo-dataset-hac-20260514T102530Z/output"
  assert tmux_session == "wgsc-demo-dataset-hac-20260514T102530Z"
  assert "tmux attach -t" in attach_command
  assert "secret-password" not in attach_command


def test_remote_run_key_conflict_appends_job_prefix() -> None:
  run_key = build_remote_run_key("Demo Dataset", "megs2", "2026-05-14T10:25:30Z")
  original_jobs = dict(api_server.JOBS)
  try:
    api_server.JOBS.clear()
    api_server.JOBS["existing"] = {"id": "existing", "remote_run_key": run_key}
    unique_key = api_server.unique_remote_run_key(run_key, "abcdef123456")
  finally:
    api_server.JOBS.clear()
    api_server.JOBS.update(original_jobs)

  assert unique_key == f"{run_key}-abcdef12"


def test_existing_dataset_disables_auto_colmap_contract() -> None:
  validated = {
    "python": "python3",
    "repo_path": "/remote/repo",
    "activate_cmd": "",
  }
  remote_paths = {
    "workspace_dir": "/tmp/web_scan/workspaces/session/hac/job/workspace",
    "output_dir": "/tmp/web_scan/outputs/session/hac/job/output",
  }
  job_paths = _remote_job_paths(remote_paths["output_dir"])
  script = _build_remote_detached_run_script(
    validated=validated,
    job_id="job-existing",
    family="hac",
    remote_paths=remote_paths,
    remote_job_paths=job_paths,
    remote_workspace_for_command="/tmp/web_scan/workspaces/datasets/hac/reuse/workspace",
    remote_command="python train.py -s /tmp/reuse -m /tmp/out",
    auto_colmap=True,
    resolved_dataset_id="reuse",
    resolved_dataset_name="Reuse",
    use_existing_remote_dataset=True,
  )

  assert "AUTO_COLMAP=0" in script
  assert "USE_EXISTING_REMOTE_DATASET=1" in script
  assert "[Dataset] Reusing existing remote dataset" in script
  assert "sequential_matcher" not in script
  assert "feature_extractor" not in script
  assert "executing_remote_colmap" not in script


def test_existing_dataset_colmap_preflight_is_optional() -> None:
  assert api_server.effective_auto_colmap(True, True) is False
  assert api_server.effective_auto_colmap(True, False) is True
  assert api_server.effective_colmap_preflight_required({
    "check_colmap_required": True,
    "use_existing_remote_dataset": True,
  }) is False
  assert api_server.effective_colmap_preflight_required({
    "check_colmap_required": True,
    "use_existing_remote_dataset": False,
  }) is True


def test_tmux_install_candidates_use_mamba_or_noninteractive_sudo_only() -> None:
  candidates = _tmux_install_candidates()
  assert candidates
  assert candidates[0]["manager"] == "mamba"
  assert candidates[0]["command"] == "mamba install -y -c conda-forge tmux"
  for candidate in candidates:
    command = candidate["command"]
    assert "sudo -S" not in command
    assert "secret" not in command.lower()
    assert "password" not in command.lower()
    if candidate["manager"] != "mamba":
      assert "sudo -n" in command


def test_tmux_cancel_script_kills_session_and_preserves_logs() -> None:
  job_paths = _remote_job_paths("/tmp/web_scan/outputs/session/hac/job/output")
  cancel_script = _build_remote_cancel_script(job_paths, "12345", "wgsc-hac-job")

  assert "tmux kill-session -t" in cancel_script
  assert "echo 130" in cancel_script
  assert "runtime.log" in cancel_script
  assert "rm -rf" not in cancel_script


def test_job_persistence_sanitizes_password() -> None:
  job = {
    "id": "persist-test",
    "status": "running",
    "remote_detached": True,
    "remote": {
      "host": "127.0.0.1",
      "port": 22,
      "username": "chen",
      "password": "secret-password",
      "repo_path": "/remote/repo",
      "workspace_root": "/tmp/workspaces",
      "output_root": "/tmp/outputs",
      "python": "python3",
      "activate_cmd": "",
    },
    "_remote_config": {"password": "secret-password"},
  }
  payload = api_server.sanitize_job_for_persistence(job)
  serialized = json.dumps(payload, ensure_ascii=False)

  assert "secret-password" not in serialized
  assert payload["remote"]["has_password"] is True
  assert "_remote_config" not in payload


def test_apply_remote_poll_statuses() -> None:
  job_id = f"detached-test-{uuid.uuid4().hex[:8]}"
  job = {
    "id": job_id,
    "status": "detached",
    "operation": "remote_train",
    "representation": "sh",
    "output_dir": str(Path(api_server.WEB_DIR) / "generated" / "runs" / job_id),
    "remote_result": {},
  }
  api_server.apply_remote_poll_result(job, {
    "status": "running",
    "stage": "executing_remote_command",
    "return_code": "",
    "remote_pid": "12345",
    "log_text": "iter 1 loss 0.1\n",
    "log_cursor": 18,
    "remote_output_dir": "/tmp/outputs/session/hac/job/output",
    "download": {"files": 0, "bytes": 0, "pending": True},
  })
  assert job["status"] == "running"
  assert job["remote_stage"] == "executing_remote_command"
  assert job["remote_pid"] == "12345"
  assert job["monitor_state"] == "monitoring"

  api_server.apply_remote_poll_result(job, {
    "status": "completed",
    "stage": "completed",
    "return_code": 0,
    "log_text": "done\n",
    "log_cursor": 23,
    "download": {"files": 2, "bytes": 128},
  })
  assert job["status"] == "completed"
  assert job["return_code"] == 0
  assert job["finished_at"]
  assert job["remote_result"]["download"]["files"] == 2


class FakeSftpAttr:
  def __init__(self, filename: str, *, is_dir: bool = False, is_special: bool = False, size: int = 0):
    self.filename = filename
    self.st_size = size
    if is_dir:
      self.st_mode = stat.S_IFDIR
    elif is_special:
      self.st_mode = stat.S_IFLNK
    else:
      self.st_mode = stat.S_IFREG


class FakeRemoteFile:
  def __init__(self, payload: bytes):
    self.payload = payload

  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc, traceback):
    return False

  def read(self):
    return self.payload


class FakeSftp:
  def __init__(self) -> None:
    self.files = {
      "/remote/point_cloud.ply": b"new-point-cloud",
      "/remote/nested/meta.json": b"{\"ok\": true}",
    }
    self.marker_payload = json.dumps({
      "job_id": "job-1",
      "family": "family",
      "remote_output_dir": "/remote",
    }).encode("utf-8")
    self.get_calls: list[str] = []

  def listdir_attr(self, remote_path: str):
    if remote_path == "/remote":
      return [
        FakeSftpAttr("point_cloud.ply", size=len(self.files["/remote/point_cloud.ply"])),
        FakeSftpAttr("nested", is_dir=True),
        FakeSftpAttr("latest", is_special=True),
      ]
    if remote_path == "/remote/nested":
      return [FakeSftpAttr("meta.json", size=len(self.files["/remote/nested/meta.json"]))]
    return []

  def get(self, remote_item: str, local_item: str, callback=None):
    self.get_calls.append(remote_item)
    payload = self.files[remote_item]
    target = Path(local_item)
    target.write_bytes(payload)
    if callback:
      callback(len(payload), len(payload))

  def open(self, remote_path: str, mode: str = "r"):
    if remote_path == "/remote/.wgsc_job/status.json":
      return FakeRemoteFile(self.marker_payload)
    raise FileNotFoundError(remote_path)


class FakeTreeSftp:
  def __init__(self, files: dict[str, bytes]) -> None:
    self.files = files
    self.get_calls: list[str] = []

  def listdir_attr(self, remote_path: str):
    base = str(remote_path or "/").rstrip("/") or "/"
    prefix = "" if base == "/" else base + "/"
    children: dict[str, FakeSftpAttr] = {}
    for file_path, payload in self.files.items():
      if not file_path.startswith(prefix):
        continue
      rest = file_path[len(prefix):]
      if not rest:
        continue
      child_name, _, remainder = rest.partition("/")
      if remainder:
        children[child_name] = FakeSftpAttr(child_name, is_dir=True)
      elif child_name not in children:
        children[child_name] = FakeSftpAttr(child_name, size=len(payload))
    return [children[name] for name in sorted(children)]

  def get(self, remote_item: str, local_item: str, callback=None):
    self.get_calls.append(remote_item)
    payload = self.files[remote_item]
    target = Path(local_item)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    if callback:
      callback(len(payload), len(payload))


def write_ply(path: Path, properties: list[tuple[str, str]]) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  lines = [
    "ply",
    "format ascii 1.0",
    "element vertex 1",
    *(f"property {kind} {name}" for kind, name in properties),
    "end_header",
    " ".join("0" for _ in properties),
    "",
  ]
  path.write_text("\n".join(lines), encoding="ascii")


def write_gaussian_sh_ply(path: Path) -> None:
  write_ply(path, [
    ("float", "x"),
    ("float", "y"),
    ("float", "z"),
    ("float", "f_dc_0"),
    ("float", "f_dc_1"),
    ("float", "f_dc_2"),
    ("float", "f_rest_0"),
    ("float", "opacity"),
    ("float", "scale_0"),
    ("float", "scale_1"),
    ("float", "scale_2"),
    ("float", "rot_0"),
    ("float", "rot_1"),
    ("float", "rot_2"),
    ("float", "rot_3"),
  ])


def write_sg_ply(path: Path) -> None:
  write_ply(path, [
    ("float", "x"),
    ("float", "y"),
    ("float", "z"),
    ("float", "rgb_base_0"),
    ("float", "rgb_base_1"),
    ("float", "rgb_base_2"),
    ("float", "sg_dir_0"),
    ("float", "sg_sharp_0"),
    ("float", "sg_rgb_0"),
  ])


def write_rgb_point_cloud_ply(path: Path) -> None:
  write_ply(path, [
    ("float", "x"),
    ("float", "y"),
    ("float", "z"),
    ("uchar", "red"),
    ("uchar", "green"),
    ("uchar", "blue"),
  ])


def write_anchor_gaussian_ply(path: Path) -> None:
  write_ply(path, [
    ("float", "x"),
    ("float", "y"),
    ("float", "z"),
    ("float", "nx"),
    ("float", "ny"),
    ("float", "nz"),
    ("float", "f_offset_0"),
    ("float", "f_mask_0"),
    ("float", "f_anchor_feat_0"),
    ("float", "opacity"),
    ("float", "scale_0"),
    ("float", "scale_1"),
    ("float", "scale_2"),
    ("float", "rot_0"),
    ("float", "rot_1"),
    ("float", "rot_2"),
    ("float", "rot_3"),
  ])


def test_remote_download_uses_temp_file_and_reports_progress() -> None:
  target_dir = Path(api_server.WEB_DIR) / "generated" / "download_tests" / f"download-{uuid.uuid4().hex[:8]}"
  try:
    target_dir.mkdir(parents=True, exist_ok=True)
    stale_file = target_dir / "point_cloud.ply"
    stale_file.write_bytes(b"old")
    progress = []

    result = _download_directory(FakeSftp(), "/remote", target_dir, progress_callback=progress.append)

    assert result["pending"] is False
    assert result["files"] == 2
    assert result["total_files"] == 2
    assert result["skipped_files"] == 0
    assert result["skipped_special_files"] == 1
    assert stale_file.read_bytes() == b"new-point-cloud"
    assert (target_dir / "nested" / "meta.json").read_bytes() == b"{\"ok\": true}"
    assert not list(target_dir.glob("*.wgsc-download"))
    assert progress
    assert progress[0]["pending"] is True
  finally:
    if target_dir.exists():
      shutil.rmtree(target_dir)


def test_remote_dataset_listing_ignores_missing_manifests() -> None:
  workspace_root = "/root/autodl-tmp/tmp/workspace"
  dataset_id = "dataset2-preview-"
  files = {
    f"{workspace_root}/datasets/megs2/{dataset_id}/workspace/images/0001.png": b"png",
  }
  datasets = _list_remote_datasets(FakeTreeSftp(files), workspace_root=workspace_root, family="megs2")

  assert len(datasets) == 1
  assert datasets[0]["id"] == dataset_id
  assert datasets[0]["name"] == dataset_id
  assert datasets[0]["has_input_images"] is True
  assert datasets[0]["stage_label"] == "raw_only"


def test_remote_download_skips_complete_local_files() -> None:
  target_dir = Path(api_server.WEB_DIR) / "generated" / "download_tests" / f"skip-{uuid.uuid4().hex[:8]}"
  fake = FakeSftp()
  try:
    (target_dir / "nested").mkdir(parents=True, exist_ok=True)
    (target_dir / "point_cloud.ply").write_bytes(fake.files["/remote/point_cloud.ply"])
    (target_dir / "nested" / "meta.json").write_bytes(fake.files["/remote/nested/meta.json"])

    result = _download_directory(fake, "/remote", target_dir, progress_callback=lambda _: None)

    assert result["pending"] is False
    assert result["complete"] is True
    assert result["files"] == 0
    assert result["bytes"] == 0
    assert result["skipped_files"] == 2
    assert result["skipped_special_files"] == 1
    assert fake.get_calls == []
  finally:
    if target_dir.exists():
      shutil.rmtree(target_dir)


def test_remote_download_skips_project_checkpoint_artifacts() -> None:
  files = {
    "/remote/point_cloud/iteration_30000/point_cloud.ply": b"ply",
    "/remote/point_cloud/iteration_30000/color_mlp.pt": b"mlp",
    "/remote/chkpnt30000.pth": b"checkpoint",
    "/remote/checkpoint_foo.pkl": b"checkpoint-pkl",
    "/remote/checkpoints/epoch=300-step=30000.ckpt": b"lightning",
    "/remote/checkpoints/epoch=300-step=30000-xyz_rgb.ply": b"lightning-ply",
    "/remote/ckpts/model.pt": b"ckpt",
    "/remote/nested/model_30000.pth": b"model",
    "/remote/compgs_pretrain/point_cloud/iteration_30000/point_cloud.ply": b"intermediate-ply",
    "/remote/compgs_pretrain/config/webscan_compgs.yaml": b"generated-config",
    "/remote/compgs_point_cloud.ply": b"compgs-ply",
    "/remote/point_cloud.ply": b"viewer-ply",
    "/remote/fcgs_bitstreams/chunk_0.b": b"bits",
    "/remote/eval/results.json": b"{}",
    "/remote/test/ours_30000/renders/000.png": b"png",
    "/remote/output.log": b"log",
  }
  target_dir = Path(api_server.WEB_DIR) / "generated" / "download_tests" / f"checkpoint-filter-{uuid.uuid4().hex[:8]}"
  try:
    plan = _build_remote_download_plan(FakeTreeSftp(files), "/remote", target_dir)
    download_paths = {item["relative_path"] for item in plan["files_to_download"]}

    assert download_paths == {
      "point_cloud/iteration_30000/point_cloud.ply",
      "checkpoints/epoch=300-step=30000-xyz_rgb.ply",
      "compgs_point_cloud.ply",
      "point_cloud.ply",
      "fcgs_bitstreams/chunk_0.b",
      "eval/results.json",
      "test/ours_30000/renders/000.png",
      "output.log",
    }
    assert plan["skipped_checkpoint_files"] == 8
    assert plan["skipped_checkpoint_bytes"] > 0
    assert "compgs_pretrain/" in plan["skipped_checkpoint_samples"]
    assert "checkpoints/epoch=300-step=30000.ckpt" in plan["skipped_checkpoint_samples"]
    assert all(not item.startswith("compgs_pretrain/") for item in download_paths)
    assert all(not item.endswith((".pth", ".pt", ".ckpt", ".pkl")) for item in download_paths)
  finally:
    if target_dir.exists():
      shutil.rmtree(target_dir)


def test_gaussian_splatting_lightning_train_exports_ply() -> None:
  config_path = Path(api_server.WEB_DIR) / "config" / "algorithm_adapters.json"
  config = json.loads(config_path.read_text(encoding="utf-8"))
  adapter = next(item for item in config["adapters"] if item["family"] == "gaussian-splatting-lightning")
  template = adapter["operations"]["train"]["template"]

  assert "--model.save_ply true" in template

  validated = {
    "python": "python3",
    "repo_path": "/remote/gaussian-splatting-lightning",
    "activate_cmd": "source /opt/env/bin/activate",
  }
  remote_paths = {
    "workspace_dir": "/remote/workspace",
    "output_dir": "/remote/output",
  }
  script = _build_remote_detached_run_script(
    validated=validated,
    job_id="job-gspl",
    family="gaussian-splatting-lightning",
    remote_paths=remote_paths,
    remote_job_paths=_remote_job_paths(remote_paths["output_dir"]),
    remote_workspace_for_command="/remote/dataset",
    remote_command="python3 main.py fit --data.path /remote/dataset --output /remote/output --model.save_ply true",
    auto_colmap=False,
    resolved_dataset_id="dataset",
    resolved_dataset_name="Dataset",
    use_existing_remote_dataset=True,
    post_train_config={"run_render": True, "run_metrics": False},
  )

  assert 'write_status "running" "exporting_gaussian_ply"' in script
  assert "if ! test -f utils/ckpt2ply.py; then" in script
  assert 'REAL_OUTPUT_DIR="$(resolve_gspl_output_dir)"' in script
  assert 'python utils/ckpt2ply.py \\"$REAL_OUTPUT_DIR\\" --override' in script
  assert '"$PYTHON_BIN" utils/ckpt2ply.py "$REAL_OUTPUT_DIR" --override' in script
  assert 'finish_with "failed" "postprocess"' in script
  assert 'REMOTE_REPO_DIR=/remote/gaussian-splatting-lightning' in script
  assert "wgsc_activate_env() {" in script
  assert "source /opt/env/bin/activate" in script
  assert "wgsc_activate_env" in script.split('cd "$REMOTE_REPO_DIR"')[0]
  assert script.count("wgsc_activate_env") >= 3
  assert 'cd "$REMOTE_REPO_DIR" || finish_with "failed" "render"' in script
  assert '"$PYTHON_BIN" main.py validate --data.path "$REMOTE_DATASET_WORKSPACE"' in script
  assert "partial_success" not in script
  assert "render_metrics.json" not in script
  assert "--colored" not in script
  assert "--drop-shs-rest" not in script

  repair_script = remote_executor._build_remote_repair_metrics_script(
    validated=validated,
    job_id="job-gspl",
    family="gaussian-splatting-lightning",
    remote_output_dir="/remote/output",
    remote_dataset_workspace="/remote/dataset",
    remote_command="python3 main.py fit --data.path /remote/dataset --output /remote/output --model.save_ply true",
  )
  assert 'REMOTE_REPO_DIR=/remote/gaussian-splatting-lightning' in repair_script
  assert "wgsc_activate_env() {" in repair_script
  assert "source /opt/env/bin/activate" in repair_script
  assert "wgsc_activate_env" in repair_script.split('cd "$REMOTE_REPO_DIR"')[0]
  assert repair_script.count("wgsc_activate_env") >= 3
  assert 'cd "$REMOTE_REPO_DIR" || finish_with "failed" "render"' in repair_script
  assert '"$PYTHON_BIN" main.py validate --data.path "$REMOTE_DATASET_WORKSPACE"' in repair_script
  assert "render_metrics.json" not in repair_script


def test_training_eval_arg_injection_is_family_aware() -> None:
  assert remote_executor.apply_training_eval_arg(
    "reduced-3dgs",
    "python train.py -s /data -m /out",
  ).endswith("--eval")
  for family in ("gaussianpro", "atomgs"):
    command = remote_executor.apply_training_eval_arg(
      family,
      "python train.py -s /data -m /out",
    )
    assert command.endswith("--eval")
    assert remote_executor.apply_training_eval_arg(
      family,
      "python train.py -s /data -m /out --eval",
    ).count("--eval") == 1
  assert remote_executor.apply_training_eval_arg(
    "gaussian-splatting-lightning",
    "python main.py fit --data.path /data --output /out",
  ) == "python main.py fit --data.path /data --output /out"
  assert remote_executor.apply_training_eval_arg(
    "megs2",
    "python train.py -s /data -m /out --eval",
  ).count("--eval") == 1


def test_reduced_3dgs_post_train_cfg_args_contract() -> None:
  config_path = Path(api_server.WEB_DIR) / "config" / "algorithm_adapters.json"
  config = json.loads(config_path.read_text(encoding="utf-8"))
  adapter = next(item for item in config["adapters"] if item["family"] == "reduced-3dgs")

  assert "--iterations {iterations}" in adapter["operations"]["train"]["template"]
  assert adapter["operations"]["render"]["template"] == "python render.py -m {output_dir} -s {workspace} --iteration {iterations}"

  format_args = {
    "input_path": "",
    "output_dir": "/remote/output/reduced-3dgs",
    "workspace": "/remote/dataset/reduced-3dgs",
    "checkpoint_path": "/remote/output/reduced-3dgs",
    "repo_path": "/remote/reduced-3dgs",
    "source": "/remote/dataset/reduced-3dgs",
    "iterations": 30000,
  }
  post_train = api_server.post_train_config_for_adapter(adapter, format_args, "python3")
  assert post_train["render_command"] == (
    "python3 render.py -m /remote/output/reduced-3dgs "
    "-s /remote/dataset/reduced-3dgs --iteration 30000"
  )
  assert post_train["metrics_command"] == "python3 metrics.py -m /remote/output/reduced-3dgs"
  assert post_train["iteration"] == "30000"

  validated = {
    "python": "python3",
    "repo_path": "/remote/reduced-3dgs",
    "activate_cmd": "source /opt/env/bin/activate",
  }
  remote_paths = {
    "workspace_dir": "/remote/workspace/reduced-3dgs",
    "output_dir": "/remote/output/reduced-3dgs",
  }
  script = _build_remote_detached_run_script(
    validated=validated,
    job_id="job-reduced",
    family="reduced-3dgs",
    remote_paths=remote_paths,
    remote_job_paths=_remote_job_paths(remote_paths["output_dir"]),
    remote_workspace_for_command="/remote/dataset/reduced-3dgs",
    remote_command="python3 train.py -s /remote/dataset/reduced-3dgs -m /remote/output/reduced-3dgs --eval --iterations 30000",
    auto_colmap=False,
    resolved_dataset_id="dataset",
    resolved_dataset_name="Dataset",
    use_existing_remote_dataset=True,
    post_train_config=post_train,
  )
  assert "ensure_3dgs_cfg_args" in script
  assert "resolve_model_iteration" in script
  assert "POST_TRAIN_ITERATION=30000" in script
  assert (
    "POST_TRAIN_RENDER_COMMAND='python3 render.py -m /remote/output/reduced-3dgs "
    "-s /remote/dataset/reduced-3dgs --iteration 30000'"
  ) in script
  assert "POST_TRAIN_METRICS_COMMAND='python3 metrics.py -m /remote/output/reduced-3dgs'" in script


def test_atomgs_gaussianpro_adapters_and_post_train_contract() -> None:
  config_path = Path(api_server.WEB_DIR) / "config" / "algorithm_adapters.json"
  config = json.loads(config_path.read_text(encoding="utf-8"))
  adapters = {item["family"]: item for item in config["adapters"]}

  for family, repo_path in {
    "gaussianpro": "../GaussianPro-version1.0",
    "atomgs": "../AtomGS-main",
  }.items():
    adapter = adapters[family]
    assert adapter["repo_type"] == "local"
    assert adapter["repo_path"] == repo_path
    assert adapter["default_cwd"] == repo_path
    assert adapter["operations"]["train"]["enabled"] is True
    assert "-s {workspace}" in adapter["operations"]["train"]["template"]
    assert "-m {output_dir}" in adapter["operations"]["train"]["template"]
    assert "--eval" in adapter["operations"]["train"]["template"]
    assert adapter["operations"]["render"]["enabled"] is True
    assert adapter["operations"]["metrics"]["enabled"] is True

    validated = {
      "python": "python3",
      "repo_path": f"/remote/{family}",
      "activate_cmd": "source /opt/env/bin/activate",
    }
    remote_paths = {
      "workspace_dir": f"/remote/workspace/{family}",
      "output_dir": f"/remote/output/{family}",
    }
    script = _build_remote_detached_run_script(
      validated=validated,
      job_id=f"job-{family}",
      family=family,
      remote_paths=remote_paths,
      remote_job_paths=_remote_job_paths(remote_paths["output_dir"]),
      remote_workspace_for_command=f"/remote/dataset/{family}",
      remote_command=f"python train.py -s /remote/dataset/{family} -m /remote/output/{family} --eval",
      auto_colmap=False,
      resolved_dataset_id="dataset",
      resolved_dataset_name="Dataset",
      use_existing_remote_dataset=True,
      post_train_config={
        "run_render": True,
        "run_metrics": True,
        "render_template": adapter["operations"]["render"]["template"],
        "metrics_template": adapter["operations"]["metrics"]["template"],
      },
    )
    assert "POST_TRAIN_RENDER_COMMAND='python3 render.py" in script
    assert "POST_TRAIN_METRICS_COMMAND='python3 metrics.py" in script
    assert "write_canonical_result_json" in script
    assert "LPIPS" in script
    assert "partial_success" not in script


def test_ply_header_classifier_selects_viewer_format() -> None:
  target_dir = Path(api_server.WEB_DIR) / "generated" / "download_tests" / f"ply-format-{uuid.uuid4().hex[:8]}"
  try:
    sh_path = target_dir / "sh.ply"
    sg_path = target_dir / "sg.ply"
    rgb_path = target_dir / "rgb.ply"
    anchor_path = target_dir / "anchor.ply"
    write_gaussian_sh_ply(sh_path)
    write_sg_ply(sg_path)
    write_rgb_point_cloud_ply(rgb_path)
    write_anchor_gaussian_ply(anchor_path)

    assert api_server.classify_ply_render_format(sh_path) == "gaussian-sh"
    assert api_server.classify_ply_render_format(sg_path) == "gaussian-sg"
    assert api_server.classify_ply_render_format(rgb_path) == "rgb-point-cloud"
    assert api_server.classify_ply_render_format(anchor_path) == "anchor-gaussian"

    sh_payload = api_server.result_payload_for_ply(sh_path, family="gaussian-splatting-lightning", representation="sg")
    sg_payload = api_server.result_payload_for_ply(sg_path, family="megs2", representation="sh")
    rgb_payload = api_server.result_payload_for_ply(rgb_path, family="gaussian-splatting-lightning", representation="sg")
    anchor_payload = api_server.result_payload_for_ply(anchor_path, family="contextgs", representation="sh")

    assert sh_payload["render_format"] == "gaussian-sh"
    assert sh_payload["representation"] == "sh"
    assert "/web/viewers/sh.html" in sh_payload["viewer_url"]
    assert sg_payload["render_format"] == "gaussian-sg"
    assert sg_payload["representation"] == "sg"
    assert "/web/viewers/sg.html" in sg_payload["viewer_url"]
    assert rgb_payload["render_format"] == "rgb-point-cloud"
    assert rgb_payload["render_label"] == "RGB Point Cloud"
    assert "/web/viewers/sh.html" in rgb_payload["viewer_url"]
    assert anchor_payload["render_format"] == "anchor-gaussian"
    assert anchor_payload["render_label"] == "Anchor Gaussian PLY"
    assert "/web/viewers/sh.html" in anchor_payload["viewer_url"]
  finally:
    if target_dir.exists():
      shutil.rmtree(target_dir)


def test_gaussian_splatting_lightning_prefers_exported_gaussian_ply() -> None:
  target_dir = Path(api_server.WEB_DIR) / "generated" / "download_tests" / f"gspl-artifacts-{uuid.uuid4().hex[:8]}"
  try:
    rgb_checkpoint_ply = target_dir / "checkpoints" / "epoch=429-step=30000-xyz_rgb.ply"
    exported_ply = target_dir / "point_cloud" / "iteration_30000" / "point_cloud.ply"
    write_rgb_point_cloud_ply(rgb_checkpoint_ply)
    write_gaussian_sh_ply(exported_ply)

    selected = api_server.find_result_ply(target_dir, "gaussian-splatting-lightning")
    assert selected == exported_ply

    payload = api_server.load_result_path(
      str(target_dir),
      family="gaussian-splatting-lightning",
      representation="sh",
    )
    assert payload["ok"] is True
    assert payload["resolved_path"] == str(exported_ply.resolve())
    assert payload["render_format"] == "gaussian-sh"
    assert payload["render_label"] == "Gaussian SH PLY"
    assert "/web/viewers/sh.html" in payload["viewer_url"]
  finally:
    if target_dir.exists():
      shutil.rmtree(target_dir)


def test_gaussian_splatting_lightning_finds_nested_existing_dataset_output() -> None:
  target_dir = Path(api_server.WEB_DIR) / "generated" / "download_tests" / f"gspl-nested-{uuid.uuid4().hex[:8]}"
  try:
    nested_dir = target_dir / "contextgs_test1_dataset-preview-preview-_workspace"
    exported_ply = nested_dir / "point_cloud" / "iteration_30000" / "point_cloud.ply"
    write_gaussian_sh_ply(exported_ply)

    payload = api_server.load_result_path(
      str(target_dir),
      family="gaussian-splatting-lightning",
      representation="sh",
    )
    assert payload["ok"] is True
    assert payload["resolved_path"] == str(exported_ply.resolve())
    assert payload["render_format"] == "gaussian-sh"
    assert payload["point_cloud_url"].endswith(
      "/contextgs_test1_dataset-preview-preview-_workspace/point_cloud/iteration_30000/point_cloud.ply"
    )
  finally:
    if target_dir.exists():
      shutil.rmtree(target_dir)


def test_gaussian_splatting_lightning_uses_rgb_checkpoint_ply_as_fallback() -> None:
  target_dir = Path(api_server.WEB_DIR) / "generated" / "download_tests" / f"gspl-rgb-fallback-{uuid.uuid4().hex[:8]}"
  try:
    rgb_checkpoint_ply = target_dir / "checkpoints" / "epoch=429-step=30000-xyz_rgb.ply"
    write_rgb_point_cloud_ply(rgb_checkpoint_ply)

    payload = api_server.load_result_path(
      str(target_dir),
      family="gaussian-splatting-lightning",
      representation="sh",
    )
    assert payload["ok"] is True
    assert payload["resolved_path"] == str(rgb_checkpoint_ply.resolve())
    assert payload["render_format"] == "rgb-point-cloud"
    assert payload["render_label"] == "RGB Point Cloud"
  finally:
    if target_dir.exists():
      shutil.rmtree(target_dir)


def test_result_download_validation_allows_completed_or_partial_success() -> None:
  output_dir = api_server.remote_job_local_output_dir("session", "family", "job-1")
  base = {
    "id": "job-1",
    "session_id": "session",
    "algorithm_family": "family",
    "remote_detached": True,
    "remote_output_dir": "/remote/output",
    "output_dir": output_dir,
  }
  assert api_server.validate_completed_result_download_job({**base, "status": "completed"}) == ("/remote/output", output_dir)
  assert api_server.validate_completed_result_download_job({**base, "status": "partial_success"}) == ("/remote/output", output_dir)

  for status in ("failed", "canceled", "running", "detached"):
    try:
      api_server.validate_completed_result_download_job({**base, "status": status})
    except ValueError as exc:
      assert "Only completed or partial-success jobs can download results" in str(exc)
    else:
      raise AssertionError(f"status should be rejected: {status}")

  try:
    api_server.validate_completed_result_download_job({
      **base,
      "status": "completed",
      "remote_detached": False,
    })
  except ValueError as exc:
    assert "Only completed remote jobs" in str(exc)
  else:
    raise AssertionError("local completed job should be rejected")

  legacy_job = {**base, "status": "completed", "output_dir": "/local/wrong-family"}
  assert api_server.validate_completed_result_download_job(legacy_job) == ("/remote/output", output_dir)
  assert legacy_job["legacy_output_dir_migrated"] is True
  assert legacy_job["previous_output_dir"] == "/local/wrong-family"


def remote_config(**overrides):
  base = {
    "host": "server.example.com",
    "port": 2222,
    "username": "root",
    "password": "secret",
    "repo_path": "/repo",
    "workspace_root": "/workspace",
    "output_root": "/outputs",
    "python": "python3",
    "activate_cmd": "",
  }
  base.update(overrides)
  return base


def result_download_job(**overrides):
  output_dir = api_server.remote_job_local_output_dir("session", "family", "job-1")
  base = {
    "id": "job-1",
    "session_id": "session",
    "algorithm_family": "family",
    "status": "completed",
    "remote_detached": True,
    "remote_output_dir": "/outputs/session/family/job-1/output",
    "output_dir": output_dir,
    "remote": {
      "host": "server.example.com",
      "port": 2222,
      "username": "root",
      "repo_path": "/repo",
      "workspace_root": "/workspace",
      "output_root": "/outputs",
      "python": "python3",
      "activate_cmd": "",
      "has_password": True,
    },
  }
  base.update(overrides)
  return base


def test_result_download_remote_identity_requires_original_server() -> None:
  matched = api_server.result_download_remote_config(result_download_job(), {"remote": remote_config()})
  assert matched["host"] == "server.example.com"
  assert matched["output_root"] == "/outputs"

  for field, value in (
    ("host", "other.example.com"),
    ("port", 2223),
    ("username", "ubuntu"),
    ("output_root", "/other-outputs"),
  ):
    try:
      api_server.result_download_remote_config(result_download_job(), {"remote": remote_config(**{field: value})})
    except RemoteExecutionError as exc:
      assert exc.code_hint == "WGSC-JOB-RESULTS-REMOTE-MISMATCH"
      assert field in exc.details["mismatch_fields"]
    else:
      raise AssertionError(f"remote identity mismatch should be rejected: {field}")

  try:
    api_server.result_download_remote_config(result_download_job(remote={}), {"remote": remote_config()})
  except RemoteExecutionError as exc:
    assert exc.code_hint == "WGSC-JOB-RESULTS-REMOTE-MISSING"
    assert "host" in exc.details["missing_fields"]
  else:
    raise AssertionError("missing saved remote identity should be rejected")


def test_remote_result_marker_must_match_job_and_output() -> None:
  marker = _verify_remote_result_marker(
    FakeSftp(),
    remote_output_dir="/remote",
    expected_job_id="job-1",
    expected_family="family",
    expected_remote_output_dir="/remote",
  )
  assert marker["verified"] is True
  assert marker["job_id"] == "job-1"
  assert marker["family"] == "family"

  wrong_job = FakeSftp()
  wrong_job.marker_payload = json.dumps({"job_id": "other-job", "family": "family", "remote_output_dir": "/remote"}).encode("utf-8")
  try:
    _verify_remote_result_marker(
      wrong_job,
      remote_output_dir="/remote",
      expected_job_id="job-1",
      expected_remote_output_dir="/remote",
    )
  except RemoteExecutionError as exc:
    assert exc.code_hint == "WGSC-JOB-RESULTS-MARKER-MISMATCH"
    assert exc.details["actual_job_id"] == "other-job"
  else:
    raise AssertionError("wrong marker job_id should be rejected")

  wrong_family = FakeSftp()
  wrong_family.marker_payload = json.dumps({"job_id": "job-1", "family": "contextgs", "remote_output_dir": "/remote"}).encode("utf-8")
  try:
    _verify_remote_result_marker(
      wrong_family,
      remote_output_dir="/remote",
      expected_job_id="job-1",
      expected_family="family",
      expected_remote_output_dir="/remote",
    )
  except RemoteExecutionError as exc:
    assert exc.code_hint == "WGSC-JOB-RESULTS-MARKER-MISMATCH"
    assert exc.details["actual_family"] == "contextgs"
  else:
    raise AssertionError("wrong marker family should be rejected")

  wrong_output = FakeSftp()
  wrong_output.marker_payload = json.dumps({"job_id": "job-1", "family": "family", "remote_output_dir": "/other"}).encode("utf-8")
  try:
    _verify_remote_result_marker(
      wrong_output,
      remote_output_dir="/remote",
      expected_job_id="job-1",
      expected_remote_output_dir="/remote",
    )
  except RemoteExecutionError as exc:
    assert exc.code_hint == "WGSC-JOB-RESULTS-MARKER-MISMATCH"
    assert exc.details["actual_remote_output_dir"] == "/other"
  else:
    raise AssertionError("wrong marker output dir should be rejected")

  missing = FakeSftp()
  try:
    _verify_remote_result_marker(
      missing,
      remote_output_dir="/remote",
      remote_status_path="/remote/missing-status.json",
      expected_job_id="job-1",
      expected_remote_output_dir="/remote",
    )
  except RemoteExecutionError as exc:
    assert exc.code_hint == "WGSC-JOB-RESULTS-MARKER-MISSING"
  else:
    raise AssertionError("missing marker should be rejected")


class ShortWriteSftp(FakeSftp):
  def get(self, remote_item: str, local_item: str, callback=None):
    self.get_calls.append(remote_item)
    payload = b"short"
    Path(local_item).write_bytes(payload)
    if callback:
      callback(len(payload), len(self.files[remote_item]))


def test_remote_download_checks_disk_space_and_downloaded_size() -> None:
  target_dir = Path(api_server.WEB_DIR) / "generated" / "download_tests" / f"hardening-{uuid.uuid4().hex[:8]}"
  original_disk_usage = remote_executor.shutil.disk_usage

  class TinyDisk:
    free = 1

  try:
    target_dir.mkdir(parents=True, exist_ok=True)
    remote_executor.shutil.disk_usage = lambda _: TinyDisk()
    try:
      _download_directory(FakeSftp(), "/remote", target_dir)
    except RemoteExecutionError as exc:
      assert exc.code_hint == "WGSC-JOB-RESULTS-DISK-SPACE"
      assert exc.details["required_bytes"] > exc.details["available_bytes"]
    else:
      raise AssertionError("insufficient disk space should be rejected")
  finally:
    remote_executor.shutil.disk_usage = original_disk_usage
    if target_dir.exists():
      shutil.rmtree(target_dir)

  target_dir = Path(api_server.WEB_DIR) / "generated" / "download_tests" / f"size-{uuid.uuid4().hex[:8]}"
  try:
    target_dir.mkdir(parents=True, exist_ok=True)
    stale_file = target_dir / "point_cloud.ply"
    stale_file.write_bytes(b"old")
    try:
      _download_directory(ShortWriteSftp(), "/remote", target_dir)
    except RemoteExecutionError as exc:
      assert exc.code_hint == "WGSC-JOB-RESULTS-FILE-SIZE"
      assert exc.details["relative_path"] == "point_cloud.ply"
    else:
      raise AssertionError("downloaded size mismatch should be rejected")
    assert stale_file.read_bytes() == b"old"
    assert not list(target_dir.glob("*.wgsc-download"))
  finally:
    if target_dir.exists():
      shutil.rmtree(target_dir)


class AliveDownloadThread:
  def is_alive(self) -> bool:
    return True


def test_result_download_repeated_request_returns_already_running() -> None:
  base = {
    "id": f"result-download-{uuid.uuid4().hex[:8]}",
    "status": "completed",
    "remote_detached": True,
    "remote_output_dir": "/remote/output",
    "output_dir": str(Path(api_server.WEB_DIR) / "generated" / "download_tests" / "already-running"),
  }

  active = api_server.start_completed_result_download({**base, "_result_download_thread": AliveDownloadThread()}, {})
  starting = api_server.start_completed_result_download({**base, "_result_download_starting": True}, {})

  assert active["started"] is False
  assert active["already_running"] is True
  assert "_result_download_thread" not in active["job"]
  json.dumps(active["job"])
  assert starting["started"] is False
  assert starting["already_running"] is True


def test_pending_remote_download_hides_partial_artifacts() -> None:
  target_dir = Path(api_server.WEB_DIR) / "generated" / "download_tests" / f"pending-{uuid.uuid4().hex[:8]}"
  try:
    (target_dir / "point_cloud" / "iteration_1").mkdir(parents=True, exist_ok=True)
    (target_dir / "point_cloud" / "iteration_1" / "point_cloud.ply").write_bytes(b"partial")
    job = {
      "id": f"pending-{uuid.uuid4().hex[:8]}",
      "status": "completed",
      "remote_detached": True,
      "remote_result": {"download": {"pending": True, "files": 0, "bytes": 0}},
      "output_dir": str(target_dir),
      "representation": "sh",
      "_result_download_thread": AliveDownloadThread(),
    }

    enriched = api_server.enrich_job(job)

    assert "point_cloud_url" not in enriched
    assert api_server.is_remote_download_pending(enriched)
  finally:
    if target_dir.exists():
      shutil.rmtree(target_dir)


def test_stale_pending_remote_download_becomes_retryable() -> None:
  job = {
    "id": f"stale-pending-{uuid.uuid4().hex[:8]}",
    "status": "completed",
    "remote_detached": True,
    "remote_result": {"download": {"pending": True, "files": 1, "bytes": 10, "total_files": 2, "total_bytes": 20}},
    "output_dir": str(Path(api_server.WEB_DIR) / "generated" / "download_tests" / "stale-pending"),
    "representation": "sh",
  }

  changed = api_server.mark_stale_remote_download_if_needed(job)
  download = job["remote_result"]["download"]

  assert changed is True
  assert download["pending"] is False
  assert download["phase"] == "error"
  assert download["interrupted"] is True
  assert "Retry Download" in download["error"]
  assert job["remote_stage"] == "result_download_failed"
  assert job["monitor_state"] == "completed"
  assert api_server.is_remote_download_pending(job) is False


class PollFakeFile:
  def __init__(self, payload: bytes):
    self.payload = payload
    self.cursor = 0

  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc, traceback):
    return False

  def seek(self, cursor: int):
    self.cursor = max(0, int(cursor or 0))

  def read(self):
    return self.payload[self.cursor:]


class PollFakeStat:
  def __init__(self, size: int):
    self.st_size = size


class PollFallbackSftp:
  def __init__(self, files: dict[str, bytes]):
    self.files = files

  def open(self, remote_path: str, mode: str = "r"):
    if remote_path not in self.files:
      raise FileNotFoundError(remote_path)
    return PollFakeFile(self.files[remote_path])

  def stat(self, remote_path: str):
    if remote_path not in self.files:
      raise FileNotFoundError(remote_path)
    return PollFakeStat(len(self.files[remote_path]))

  def close(self):
    pass


class PollFallbackClient:
  def __init__(self, sftp: PollFallbackSftp):
    self.sftp = sftp

  def set_missing_host_key_policy(self, policy):
    pass

  def connect(self, **kwargs):
    pass

  def open_sftp(self):
    return self.sftp

  def close(self):
    pass


class PollFallbackParamiko:
  class AutoAddPolicy:
    pass

  def __init__(self, sftp: PollFallbackSftp):
    self.sftp = sftp

  def SSHClient(self):
    return PollFallbackClient(self.sftp)


def test_poll_remote_detached_job_falls_back_to_runtime_log_when_status_missing() -> None:
  sftp = PollFallbackSftp({
    "/remote/.wgsc_job/runtime.log": b"Training progress: 10/100\n",
  })
  original_loader = remote_executor._load_paramiko
  try:
    remote_executor._load_paramiko = lambda: PollFallbackParamiko(sftp)
    result = remote_executor.poll_remote_detached_job(
      remote_config=remote_config(),
      remote_status_path="/remote/.wgsc_job/status.json",
      remote_log_path="/remote/.wgsc_job/runtime.log",
      remote_output_dir="/remote",
      local_output_dir="/local",
      log_cursor=0,
    )
  finally:
    remote_executor._load_paramiko = original_loader

  assert result["status"] == "running"
  assert result["stage"] == "executing_remote_command"
  assert result["status_fallback"] == "runtime_log"
  assert "Training progress" in result["log_text"]


def test_poll_remote_detached_job_stays_running_when_status_missing_and_log_unchanged() -> None:
  log_text = b"Training progress: 10/100\n"
  sftp = PollFallbackSftp({
    "/remote/.wgsc_job/runtime.log": log_text,
  })
  original_loader = remote_executor._load_paramiko
  try:
    remote_executor._load_paramiko = lambda: PollFallbackParamiko(sftp)
    result = remote_executor.poll_remote_detached_job(
      remote_config=remote_config(),
      remote_status_path="/remote/.wgsc_job/status.json",
      remote_log_path="/remote/.wgsc_job/runtime.log",
      remote_output_dir="/remote",
      local_output_dir="/local",
      log_cursor=len(log_text),
    )
  finally:
    remote_executor._load_paramiko = original_loader

  assert result["status"] == "running"
  assert result["stage"] == "executing_remote_command"
  assert result["status_fallback"] == "runtime_log"
  assert result["log_text"] == ""


def test_poll_remote_detached_job_falls_back_to_exit_code_when_status_missing() -> None:
  sftp = PollFallbackSftp({
    "/remote/.wgsc_job/exit_code": b"0\n",
  })
  original_loader = remote_executor._load_paramiko
  try:
    remote_executor._load_paramiko = lambda: PollFallbackParamiko(sftp)
    result = remote_executor.poll_remote_detached_job(
      remote_config=remote_config(),
      remote_status_path="/remote/.wgsc_job/status.json",
      remote_log_path="/remote/.wgsc_job/runtime.log",
      remote_output_dir="/remote",
      local_output_dir="/local",
      log_cursor=0,
    )
  finally:
    remote_executor._load_paramiko = original_loader

  assert result["status"] == "completed"
  assert result["stage"] == "completed"
  assert result["return_code"] == 0
  assert result["status_fallback"] == "exit_code"


def test_remote_monitor_retries_transient_poll_errors() -> None:
  job = {
    "id": f"monitor-retry-{uuid.uuid4().hex[:8]}",
    "status": "running",
    "algorithm_family": "family",
    "output_dir": str(Path(api_server.WEB_DIR) / "generated" / "download_tests" / "monitor-retry"),
    "remote_status_path": "/remote/.wgsc_job/status.json",
    "remote_log_path": "/remote/.wgsc_job/runtime.log",
    "remote_output_dir": "/remote",
    "remote_log_cursor": 0,
  }
  calls: list[int] = []
  logs: list[str] = []
  from web.server import jobs as jobs_module
  original_poll = jobs_module.poll_remote_detached_job
  original_persist = jobs_module.persist_job_state
  original_append = jobs_module.append_job_log_line
  original_sleep = api_server.time.sleep

  def fake_poll(**kwargs):
    calls.append(1)
    if len(calls) <= 2:
      raise RemoteExecutionError(
        "Remote result marker not found: /remote/.wgsc_job/status.json",
        code_hint="WGSC-JOB-RESULTS-MARKER-MISSING",
        stage="result_marker",
      )
    job["cancel_requested"] = True
    return {
      "status": "running",
      "stage": "executing_remote_command",
      "return_code": "",
      "log_text": "still running\n",
      "log_cursor": 14,
      "remote_output_dir": "/remote",
      "download": {},
    }

  try:
    jobs_module.poll_remote_detached_job = fake_poll
    jobs_module.persist_job_state = lambda _job: None
    jobs_module.append_job_log_line = lambda _job, _channel, line: logs.append(line)
    api_server.time.sleep = lambda _seconds: None
    api_server.start_remote_monitor_thread(job, remote_config())
    deadline = api_server.time.time() + 2
    while job.get("_monitoring") and api_server.time.time() < deadline:
      original_sleep(0.01)
  finally:
    jobs_module.poll_remote_detached_job = original_poll
    jobs_module.persist_job_state = original_persist
    jobs_module.append_job_log_line = original_append
    api_server.time.sleep = original_sleep

  assert len(calls) == 3
  assert job["monitor_state"] == "monitoring"
  assert job["remote_stage"] == "executing_remote_command"
  assert any("transient error" in line for line in logs)


def main() -> None:
  test_detached_script_contract()
  test_remote_run_key_conflict_appends_job_prefix()
  test_existing_dataset_disables_auto_colmap_contract()
  test_existing_dataset_colmap_preflight_is_optional()
  test_tmux_install_candidates_use_mamba_or_noninteractive_sudo_only()
  test_tmux_cancel_script_kills_session_and_preserves_logs()
  test_job_persistence_sanitizes_password()
  test_apply_remote_poll_statuses()
  test_remote_download_uses_temp_file_and_reports_progress()
  test_remote_download_skips_complete_local_files()
  test_remote_download_skips_project_checkpoint_artifacts()
  test_gaussian_splatting_lightning_train_exports_ply()
  test_training_eval_arg_injection_is_family_aware()
  test_reduced_3dgs_post_train_cfg_args_contract()
  test_atomgs_gaussianpro_adapters_and_post_train_contract()
  test_ply_header_classifier_selects_viewer_format()
  test_gaussian_splatting_lightning_prefers_exported_gaussian_ply()
  test_gaussian_splatting_lightning_finds_nested_existing_dataset_output()
  test_gaussian_splatting_lightning_uses_rgb_checkpoint_ply_as_fallback()
  test_result_download_validation_allows_completed_or_partial_success()
  test_result_download_remote_identity_requires_original_server()
  test_remote_result_marker_must_match_job_and_output()
  test_remote_download_checks_disk_space_and_downloaded_size()
  test_result_download_repeated_request_returns_already_running()
  test_pending_remote_download_hides_partial_artifacts()
  test_stale_pending_remote_download_becomes_retryable()
  test_poll_remote_detached_job_falls_back_to_runtime_log_when_status_missing()
  test_poll_remote_detached_job_stays_running_when_status_missing_and_log_unchanged()
  test_poll_remote_detached_job_falls_back_to_exit_code_when_status_missing()
  test_remote_monitor_retries_transient_poll_errors()
  print("detached remote tests passed")


if __name__ == "__main__":
  main()
