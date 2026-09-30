#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import csv
import io
import importlib.util
import json
import logging
import math
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from shutil import which
from typing import Any, Callable, Dict
from urllib.parse import parse_qs, urlparse

ROOT_DIR = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT_DIR / "web"
STREAM_DIR = WEB_DIR / "generated" / "streams"
DATASET_DIR = WEB_DIR / "generated" / "datasets"
WORKSPACE_DIR = WEB_DIR / "generated" / "workspaces"
JOB_LOG_DIR = WEB_DIR / "generated" / "job_logs"
if str(ROOT_DIR) not in sys.path:
  sys.path.insert(0, str(ROOT_DIR))

from web.server.adapter_registry import (
  get_adapter,
  list_adapters,
  supported_operations,
  update_adapter_override,
  validate_adapter,
)
from web.server.remote_executor import (
  ALGORITHM_EVAL_REQUIREMENTS,
  RemoteExecutionError,
  apply_training_eval_arg,
  build_remote_paths,
  build_remote_run_key,
  build_remote_tmux_attach_command,
  build_remote_tmux_session_name,
  cancel_remote_detached_job,
  check_remote_output_download,
  download_remote_output_directory,
  poll_remote_detached_job,
  remote_preflight_check,
  repair_remote_job_metrics,
  sanitize_remote_config,
  sanitize_formatted_command,
  start_remote_algorithm_detached,
  validate_remote_config,
)
from web.tools.export_scene_package import build_scene_package
from web.server.results import (
  _analysis_metric_score,
  _coerce_metric_number,
  _coerce_size_mb,
  _flatten_metric_entries,
  _last_number_match,
  _metric_has_token,
  _metric_number_text,
  _metric_path_id,
  _normalize_slug,
  _size_unit_to_mb,
  add_unique_path,
  analysis_metric_files,
  analysis_result_candidate_dirs,
  analysis_result_json_files,
  analysis_roots_for_job,
  attach_hac_plus_metadata,
  attach_render_images,
  best_result_ply_candidate,
  build_viewer_url,
  classify_ply_render_format,
  compressed_artifact_size_bytes,
  compute_image_pair_metrics,
  dedupe_paths,
  default_run_output_dir,
  direct_ply_candidates,
  directory_size_bytes,
  extract_job_metrics,
  file_brief,
  file_url_for_path,
  find_result_image,
  find_result_images,
  find_result_images_for_file,
  find_result_manifest,
  find_result_ply,
  find_result_ply_in_directory,
  gslightning_checkpoint_ply_candidates,
  hac_plus_failure_reason,
  hac_plus_output_metadata,
  image_file_paths,
  image_pair_dirs,
  image_search_dirs_for_result_file,
  infer_analysis_metrics,
  infer_analysis_metrics_with_sources,
  infer_family_for_path,
  is_image_file,
  is_ply_file,
  latest_iteration_dir,
  load_ply_file,
  load_result_path,
  looks_like_fcgs_bitstream_dir,
  merge_analysis_metric,
  newest_file,
  newest_image_from_globs,
  newest_iteration_ply,
  newest_render_image,
  paired_image_paths,
  path_depth_from,
  path_mtime,
  ply_header_property_names,
  project_for_family,
  read_json_if_present,
  read_ply_header_text,
  read_result_json_metrics,
  read_runtime_artifacts,
  relative_metric_source,
  remote_job_local_output_dir,
  render_image_payloads,
  render_label_for_format,
  rendered_image_count,
  representation_for_family,
  representation_for_ply_format,
  resolve_project_path,
  resolve_workspace_path,
  result_candidate_dirs,
  result_image_patterns_for_family,
  result_json_metric_values,
  result_marker_names_for_family,
  result_payload_for_image,
  result_payload_for_manifest,
  result_payload_for_ply,
  result_ply_candidate_score,
  runtime_log_text_for_job,
  viewer_renderer_for_format,
)
from web.server.jobs import (
  _write_probe,
  adapter_requirement_spec,
  detect_cuda_toolkit,
  discover_runtime_results,
  effective_auto_colmap,
  ANALYSIS_EXPORT_COLUMNS,
  ARTIFACT_RESULT_FIELDS,
  DOWNLOADABLE_RESULT_STATUSES,
  JOBS,
  JOBS_LOCK,
  JOB_LOG_LOCKS,
  JOB_STATE_FILE,
  MAX_JOB_HISTORY,
  MAX_METRICS_HISTORY,
  MAX_PROCESS_FRAME_COMPLETED_HISTORY,
  METRICS_CSV_COLUMNS,
  PARTIAL_SUCCESS_STATUSES,
  REMOTE_MONITOR_MAX_CONSECUTIVE_FAILURES,
  REMOTE_MONITOR_MAX_FAILURE_SECONDS,
  REMOTE_MONITOR_RETRY_MAX_SECONDS,
  REMOTE_MONITOR_RETRY_MIN_SECONDS,
  REMOTE_MONITOR_START_LOCK,
  REMOTE_RESULT_IDENTITY_FIELDS,
  RESULT_DOWNLOAD_LOCK,
  TERMINAL_STATUSES,
  _annotate_datasets_with_training_coverage,
  _append_family_coverage,
  analysis_export_csv,
  append_job_log_line,
  apply_completed_result_check,
  apply_remote_poll_result,
  assert_result_download_remote_identity,
  build_metrics_page,
  cancel_flow_job,
  canonical_job_output_dir,
  check_completed_result_download,
  cleanup_flow_temporary_dirs,
  clear_job_records,
  completed_remote_result_paths,
  completed_remote_status_path,
  derive_dataset_label_from_path,
  enrich_job,
  ensure_job_download_path_valid,
  ensure_job_logging,
  ensure_metrics_csv_header,
  flow_temporary_dirs,
  is_job_terminal,
  is_remote_download_active,
  is_remote_download_pending,
  is_remote_download_starting,
  job_download_path_valid,
  job_log_dir_for_id,
  job_matches_flow,
  load_persisted_jobs,
  mark_stale_remote_download_if_needed,
  normalize_clear_statuses,
  normalize_job_dataset_fields,
  normalize_remote_identity,
  output_dir_has_pending_remote_download,
  persist_job_state,
  prune_job_history,
  read_job_log_since_cursor,
  read_job_log_tail_lines,
  read_runtime_artifacts_cached,
  remote_download_info,
  remote_identity_missing_fields,
  remove_job_record,
  reset_flow,
  reset_job_output_dir_to_canonical,
  result_download_remote_config,
  sanitize_job_for_persistence,
  set_remote_download_progress,
  start_completed_result_download,
  start_completed_result_redownload,
  start_job_metrics_repair,
  start_job_thread,
  start_remote_job_thread,
  start_remote_monitor_thread,
  unfinished_flow_jobs,
  unique_remote_run_key,
  update_job_artifacts,
  utc_timestamp_text,
  validate_completed_result_download_job,
)
from web.server.web_security import (
  DATASET_DIR,
  JOB_LOG_DIR,
  STREAM_DIR,
  WORKSPACE_DIR,
  safe_generated_child,
)
from web.server.web_security import (
  MANUAL_ZH_URL,
  SECURITY_CONFIG_FILE,
  SECURITY_STATE,
  SERVER_TOKEN_FILE,
  STREAM_FRAME_MAX_BYTES,
  UPLOAD_IMAGE_EXTENSIONS,
  allowed_host_values,
  allowed_result_roots,
  build_error_payload,
  ensure_server_token,
  error_response,
  json_response,
  load_security_config,
  path_is_inside,
  request_host_allowed,
  request_origin_allowed,
  request_token_valid,
  result_path_is_allowed,
  security_flag,
)

LOGGER = logging.getLogger("webscan")


def validated_session_id(value: Any) -> str:
  resolved = str(value or "").strip() or "default-session"
  if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,79}", resolved):
    raise ValueError(f"Invalid session_id: {resolved!r}")
  # Also rejects ".." and any path escaping the streams directory.
  safe_generated_child(STREAM_DIR, resolved)
  return resolved


def validated_capture_id(value: Any) -> str:
  resolved = str(value or "").strip()
  if not resolved:
    return ""
  if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", resolved):
    raise ValueError(f"Invalid capture_id: {resolved!r}")
  return resolved


METRIC_NUMBER_PATTERN = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


def _generate_capture_id() -> str:
  return f"capture-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"


def _resolve_capture_input_dir(session_dir: Path, capture_id: str = "") -> tuple[str, Path]:
  captures_root = session_dir / "captures"
  desired_capture = str(capture_id or "").strip()

  if desired_capture:
    desired_input_dir = (captures_root / desired_capture / "input").resolve()
    if desired_input_dir.exists() and desired_input_dir.is_dir():
      return desired_capture, desired_input_dir
    raise FileNotFoundError(f"Capture input directory does not exist: {desired_input_dir}")

  if captures_root.exists() and captures_root.is_dir():
    candidates = []
    for child in captures_root.iterdir():
      if not child.is_dir():
        continue
      input_dir = child / "input"
      if not input_dir.exists() or not input_dir.is_dir():
        continue
      try:
        score = input_dir.stat().st_mtime
      except OSError:
        score = 0
      candidates.append((score, child.name, input_dir))
    if candidates:
      candidates.sort(key=lambda item: item[0], reverse=True)
      _, selected_capture_id, selected_input_dir = candidates[0]
      return selected_capture_id, selected_input_dir.resolve()

  legacy_input_dir = (session_dir / "input").resolve()
  if legacy_input_dir.exists() and legacy_input_dir.is_dir():
    return "legacy", legacy_input_dir

  raise FileNotFoundError(f"No capture input directory found under session: {session_dir}")


def save_stream_frame(session_id: str, image_data: str, filename: str, capture_id: str = "") -> Dict[str, str]:
  resolved_session = str(session_id or "").strip() or "default-session"
  session_dir = safe_generated_child(STREAM_DIR, resolved_session)
  resolved_capture_id = str(capture_id or "").strip() or _generate_capture_id()
  if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", resolved_capture_id):
    resolved_capture_id = _generate_capture_id()

  safe_name = Path(str(filename or "").strip().replace("\\", "/")).name
  if not safe_name or safe_name.startswith("."):
    raise ValueError("Invalid upload filename.")
  if Path(safe_name).suffix.lower() not in UPLOAD_IMAGE_EXTENSIONS:
    raise ValueError("Only .png, .jpg, or .jpeg image uploads are allowed.")

  if "," in image_data:
    _, encoded = image_data.split(",", 1)
  else:
    encoded = image_data
  try:
    binary = base64.b64decode(encoded)
  except Exception as exc:
    raise ValueError("Invalid base64 image payload.") from exc
  if not binary:
    raise ValueError("Empty image payload.")
  if len(binary) > STREAM_FRAME_MAX_BYTES:
    raise ValueError(f"Image exceeds the {STREAM_FRAME_MAX_BYTES // (1024 * 1024)} MB upload limit.")

  input_dir = safe_generated_child(session_dir, "captures", resolved_capture_id, "input")
  output_dir = safe_generated_child(session_dir, "output")
  input_dir.mkdir(parents=True, exist_ok=True)
  output_dir.mkdir(parents=True, exist_ok=True)

  input_path = input_dir / safe_name
  input_path.write_bytes(binary)

  # Default fallback preview: mirror the latest input frame until an algorithm writes output.
  mirrored_output = output_dir / "latest.png"
  mirrored_output.write_bytes(binary)

  return {
    "capture_id": resolved_capture_id,
    "capture_input_dir": str(input_dir.resolve()),
    "input_path": str(input_path.resolve()),
    "output_path": str(mirrored_output.resolve()),
    "input_url": "/" + str(input_path.relative_to(ROOT_DIR)).replace("\\", "/"),
    "output_url": "/" + str(mirrored_output.relative_to(ROOT_DIR)).replace("\\", "/"),
  }


def materialize_stream_session(
  session_id: str,
  title: str | None = None,
  *,
  capture_id: str = "",
  dataset_name: str = "",
) -> Dict[str, Any]:
  session_dir = (STREAM_DIR / session_id).resolve()
  resolved_capture_id, input_dir = _resolve_capture_input_dir(session_dir, capture_id)

  frame_paths = sorted([path for path in input_dir.iterdir() if path.is_file()])
  if not frame_paths:
    raise FileNotFoundError(f"No captured frames found in: {input_dir}")

  resolved_dataset_name = str(dataset_name or "").strip() or title or f"capture-{session_id}"
  dataset_slug = _normalize_slug(resolved_dataset_name, fallback="dataset")
  dataset_id = f"{dataset_slug}-{int(time.time())}-{uuid.uuid4().hex[:6]}"

  dataset_root = (DATASET_DIR / session_id / "datasets" / dataset_id).resolve()
  images_dir = dataset_root / "images"
  input_copy_dir = dataset_root / "input"
  images_dir.mkdir(parents=True, exist_ok=True)
  input_copy_dir.mkdir(parents=True, exist_ok=True)

  copied_frames: list[str] = []
  for index, frame_path in enumerate(frame_paths, start=1):
    suffix = frame_path.suffix.lower() or ".png"
    target_name = f"{index:05d}{suffix}"
    images_target = images_dir / target_name
    input_target = input_copy_dir / target_name
    shutil.copyfile(frame_path, images_target)
    shutil.copyfile(frame_path, input_target)
    copied_frames.append("/" + str(images_target.relative_to(ROOT_DIR)).replace("\\", "/"))

  dataset_manifest = {
    "version": "0.1.0",
    "session_id": session_id,
    "capture_id": resolved_capture_id,
    "dataset_id": dataset_id,
    "dataset_name": resolved_dataset_name,
    "title": title or f"capture-{session_id}",
    "frame_count": len(frame_paths),
    "dataset_root": str(dataset_root),
    "capture_input_dir": str(input_dir),
    "images_dir": str(images_dir),
    "input_dir": str(input_copy_dir),
    "source_hint": "This is a multi-frame capture workspace. Current local Gaussian repos still require COLMAP/Blender scene conversion before training.",
    "generated_at": time.time(),
    "frames": copied_frames,
  }
  manifest_path = dataset_root / "capture_session.json"
  manifest_path.write_text(json.dumps(dataset_manifest, indent=2, ensure_ascii=False), encoding="utf-8")

  return {
    "session_id": session_id,
    "capture_id": resolved_capture_id,
    "dataset_id": dataset_id,
    "dataset_name": resolved_dataset_name,
    "frame_count": len(frame_paths),
    "dataset_root": str(dataset_root),
    "images_dir": str(images_dir),
    "input_dir": str(input_copy_dir),
    "manifest_path": str(manifest_path),
    "manifest_url": "/" + str(manifest_path.relative_to(ROOT_DIR)).replace("\\", "/"),
    "source_hint": dataset_manifest["source_hint"],
  }


def prepare_colmap_workspace(
  session_id: str,
  family: str,
  repo_path: str | None = None,
  *,
  capture_id: str = "",
  dataset_name: str = "",
) -> Dict[str, Any]:
  dataset_info = materialize_stream_session(
    session_id,
    session_id,
    capture_id=capture_id,
    dataset_name=dataset_name,
  )
  dataset_root = Path(dataset_info["dataset_root"]).resolve()
  dataset_id = str(dataset_info.get("dataset_id", "dataset")).strip() or "dataset"
  workspace_root = (WORKSPACE_DIR / session_id / family / dataset_id).resolve()
  input_dir = workspace_root / "input"
  images_dir = workspace_root / "images"
  sparse_dir = workspace_root / "sparse" / "0"
  distorted_sparse_dir = workspace_root / "distorted" / "sparse"
  input_dir.mkdir(parents=True, exist_ok=True)
  images_dir.mkdir(parents=True, exist_ok=True)
  sparse_dir.mkdir(parents=True, exist_ok=True)
  distorted_sparse_dir.mkdir(parents=True, exist_ok=True)

  copied_frames: list[str] = []
  for image_path in sorted((dataset_root / "images").iterdir()):
    if not image_path.is_file():
      continue
    input_target = input_dir / image_path.name
    images_target = images_dir / image_path.name
    shutil.copyfile(image_path, input_target)
    shutil.copyfile(image_path, images_target)
    copied_frames.append(str(input_target))

  resolved_repo = resolve_workspace_path(repo_path)
  repo_root = Path(resolved_repo) if resolved_repo else None
  convert_script = repo_root / "convert.py" if repo_root else None
  if convert_script and convert_script.exists():
    suggested_command = f"python {convert_script} -s {workspace_root}"
  else:
    database_path = workspace_root / "distorted" / "database.db"
    distorted_sparse_path = workspace_root / "distorted" / "sparse"
    distorted_sparse_0 = distorted_sparse_path / "0"
    sparse_root = workspace_root / "sparse"
    sparse_0 = sparse_root / "0"
    marker_path = workspace_root / ".wgsc_colmap_undistorted.ok"

    colmap_prepare_script = (
      "OMP_NUM_THREADS_VALUE=\"${OMP_NUM_THREADS:-1}\""
      " && case \"$OMP_NUM_THREADS_VALUE\" in \"\"|*[!0-9]*|0) OMP_NUM_THREADS_VALUE=1 ;; esac"
      " && export OMP_NUM_THREADS=\"$OMP_NUM_THREADS_VALUE\""
      " && export QT_QPA_PLATFORM=xcb"
      " && if command -v vglrun >/dev/null 2>&1; then colmap_headless() { vglrun \"$@\"; }; else colmap_headless() { \"$@\"; }; fi"
      " && command -v colmap >/dev/null 2>&1"
      f" && rm -f {shlex.quote(str(marker_path))} {shlex.quote(str(database_path))}"
      f" && mkdir -p {shlex.quote(str(distorted_sparse_path))} {shlex.quote(str(sparse_root))}"
      f" && colmap_headless colmap feature_extractor --database_path {shlex.quote(str(database_path))} --image_path {shlex.quote(str(input_dir))} "
      "--ImageReader.single_camera 1 --ImageReader.camera_model SIMPLE_PINHOLE"
      f" && colmap_headless colmap sequential_matcher --database_path {shlex.quote(str(database_path))}"
      f" && colmap_headless colmap mapper --database_path {shlex.quote(str(database_path))} --image_path {shlex.quote(str(input_dir))} --output_path {shlex.quote(str(distorted_sparse_path))}"
      f" && rm -rf {shlex.quote(str(images_dir))} {shlex.quote(str(sparse_root))}"
      f" && mkdir -p {shlex.quote(str(images_dir))} {shlex.quote(str(sparse_root))} {shlex.quote(str(sparse_0))}"
      f" && colmap_headless colmap image_undistorter --image_path {shlex.quote(str(input_dir))} --input_path {shlex.quote(str(distorted_sparse_0))} --output_path {shlex.quote(str(workspace_root))} --output_type COLMAP"
      f" && if [ -d {shlex.quote(str(sparse_root))} ]; then find {shlex.quote(str(sparse_root))} -maxdepth 1 -type f -exec mv -f {{}} {shlex.quote(str(sparse_0))} \\; ; fi"
      f" && touch {shlex.quote(str(marker_path))}"
    )
    suggested_command = f'xvfb-run -a -s "-screen 0 1280x1024x24" bash -lc {shlex.quote(colmap_prepare_script)}'

  manifest = {
    "version": "0.1.0",
    "session_id": session_id,
    "family": family,
    "workspace_root": str(workspace_root),
    "input_dir": str(input_dir),
    "images_dir": str(images_dir),
    "sparse_dir": str(sparse_dir),
    "distorted_sparse_dir": str(distorted_sparse_dir),
    "frame_count": len(copied_frames),
    "suggested_command": suggested_command,
    "generated_at": time.time(),
  }
  manifest_path = workspace_root / "workspace_manifest.json"
  manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
  run_script = workspace_root / "run_colmap_prepare.sh"
  run_script.write_text("#!/usr/bin/env bash\n" + suggested_command + "\n", encoding="utf-8")

  return {
    "session_id": session_id,
    "capture_id": dataset_info.get("capture_id", ""),
    "dataset_id": dataset_info.get("dataset_id", ""),
    "dataset_name": dataset_info.get("dataset_name", ""),
    "family": family,
    "workspace_root": str(workspace_root),
    "input_dir": str(input_dir),
    "images_dir": str(images_dir),
    "sparse_dir": str(sparse_dir),
    "manifest_path": str(manifest_path),
    "manifest_url": "/" + str(manifest_path.relative_to(ROOT_DIR)).replace("\\", "/"),
    "suggested_command": suggested_command,
    "run_script": str(run_script),
    "frame_count": len(copied_frames),
  }


def format_operation_command(
  adapter: Dict[str, Any],
  operation: str,
  *,
  input_path: str = "",
  output_dir: str = "",
  workspace: str = "",
  checkpoint_path: str = "",
  repo_path: str = "",
  training_args: Dict[str, Any] | None = None,
) -> str | None:
  operation_config = adapter.get("operations", {}).get(operation, {})
  command_template = operation_config.get("template")
  if not operation_config.get("enabled") or not command_template:
    return None
  format_args = {
    "input_path": input_path,
    "output_dir": output_dir,
    "workspace": workspace,
    "checkpoint_path": checkpoint_path,
    "repo_path": repo_path or adapter.get("repo_path", ""),
    "source": input_path or workspace,
    **(training_args or {}),
  }
  return command_template.format(**format_args)


def strip_empty_repo_path_flag(command: str, repo_path: str) -> str:
  if repo_path:
    return command
  cleaned = command
  for needle in (' --repo-path ""', " --repo-path ''", " --repo-path"):
    cleaned = cleaned.replace(needle, "")
  return cleaned.strip()




def find_missing_python_modules(module_names: list[str]) -> list[str]:
  missing: list[str] = []
  for module_name in module_names:
    if not module_name:
      continue
    try:
      if importlib.util.find_spec(module_name) is None:
        missing.append(module_name)
    except Exception:
      missing.append(module_name)
  return missing


def missing_template_fields(command_template: str, format_args: Dict[str, Any]) -> list[str]:
  required_keys = ("workspace", "input_path", "output_dir", "checkpoint_path", "source")
  missing: list[str] = []
  for key in required_keys:
    if f"{{{key}}}" in command_template and not str(format_args.get(key, "")).strip():
      missing.append(key)
  return missing






def _runtime_check_item(name: str, ok: bool, *, required: bool, message: str, hint: str = "", details: Dict[str, Any] | None = None) -> Dict[str, Any]:
  return {
    "name": name,
    "ok": ok,
    "required": required,
    "message": message,
    "hint": hint,
    "details": details or {},
  }


def _python_probe(script: str, timeout: int = 15) -> Dict[str, Any]:
  try:
    result = subprocess.run(
      [sys.executable, "-c", script],
      cwd=str(ROOT_DIR),
      capture_output=True,
      text=True,
      timeout=timeout,
      check=False,
    )
  except Exception as exc:
    return {"ok": False, "stdout": "", "stderr": str(exc), "returncode": -1}
  return {
    "ok": result.returncode == 0,
    "stdout": result.stdout.strip(),
    "stderr": result.stderr.strip(),
    "returncode": result.returncode,
  }


ALGORITHM_CUDA_CHECK_SPECS: Dict[str, Dict[str, Any]] = {
  "gaussian-splatting-lightning": {
    "label": "Gaussian Splatting Lightning",
    "root": ROOT_DIR / "gaussian-splatting-lightning-main",
    "env_name": "gspl",
    "required_modules": [
      ("torchvision", "torchvision"),
      ("lightning", "lightning"),
      ("jsonargparse", "jsonargparse"),
      ("wandb", "wandb"),
      ("viser", "viser"),
      ("plyfile", "plyfile"),
      ("PIL", "pillow"),
      ("cv2", "opencv-python-headless"),
      ("mediapy", "mediapy"),
      ("diff_gaussian_rasterization", "diff_gaussian_rasterization"),
      ("simple_knn", "simple_knn"),
      ("gsplat", "gsplat"),
    ],
  },
  "contextgs": {
    "label": "ContextGS",
    "root": ROOT_DIR / "ContextGS-main",
    "env_name": "contextgs",
    "required_modules": [
      ("torchvision", "torchvision"),
      ("diff_gaussian_rasterization", "diff_gaussian_rasterization"),
      ("simple_knn", "simple_knn"),
      ("torch_scatter", "torch_scatter"),
      ("compressai", "compressai"),
      ("torchac", "torchac"),
      ("lpips", "lpips"),
      ("plyfile", "plyfile"),
      ("einops", "einops"),
      ("cv2", "opencv-python"),
    ],
  },
  "hac-plus-plus": {
    "label": "HAC++",
    "root": ROOT_DIR / "HAC-plus-main",
    "env_name": "HAC_env",
    "required_modules": [
      ("torchvision", "torchvision"),
      ("diff_gaussian_rasterization", "diff_gaussian_rasterization"),
      ("simple_knn", "simple_knn"),
      ("_gridencoder", "gridencoder"),
      ("arithmetic", "arithmetic"),
      ("torch_scatter", "torch_scatter"),
      ("lpips", "lpips"),
      ("plyfile", "plyfile"),
      ("einops", "einops"),
      ("cv2", "opencv-python"),
    ],
  },
  "compgs": {
    "label": "CompGS",
    "root": ROOT_DIR / "CompGS-main",
    "env_name": "CompGS_env",
    "required_modules": [
      ("torchvision", "torchvision"),
      ("diff_gaussian_rasterization", "diff_gaussian_rasterization"),
      ("knn_dist", "knn_dist"),
      ("torch_scatter", "torch_scatter"),
      ("compressai", "compressai"),
      ("lpips", "lpips"),
      ("pytorch_msssim", "pytorch-msssim"),
      ("plyfile", "plyfile"),
      ("einops", "einops"),
      ("yaml", "pyyaml"),
      ("PIL", "pillow"),
    ],
  },
  "megs2": {
    "label": "MEGS2",
    "root": ROOT_DIR / "MEGS-2-main",
    "env_name": "MEGS2",
    "required_modules": [
      ("torchvision", "torchvision"),
      ("diff_gaussian_rasterization", "diff_gaussian_rasterization"),
      ("diff_gaussian_rasterization_ms", "diff_gaussian_rasterization_ms"),
      ("diff_gaussian_rasterization_ms_light", "diff_gaussian_rasterization_ms_light"),
      ("simple_knn", "simple_knn"),
      ("plyfile", "plyfile"),
      ("PIL", "pillow"),
      ("tqdm", "tqdm"),
    ],
  },
  "reduced-3dgs": {
    "label": "Reduced-3DGS",
    "root": ROOT_DIR / "reduced-3dgs-main",
    "env_name": "gaussian_splatting",
    "required_modules": [
      ("torchvision", "torchvision"),
      ("diff_gaussian_rasterization", "diff_gaussian_rasterization"),
      ("simple_knn", "simple_knn"),
      ("pandas", "pandas"),
      ("plyfile", "plyfile"),
      ("PIL", "pillow"),
      ("tqdm", "tqdm"),
    ],
  },
  "gaussianpro": {
    "label": "GaussianPro",
    "root": ROOT_DIR / "GaussianPro-version1.0",
    "env_name": "gaussianpro",
    "required_modules": [
      ("torchvision", "torchvision"),
      ("diff_gaussian_rasterization", "diff_gaussian_rasterization"),
      ("simple_knn", "simple_knn"),
      ("gaussianpro", "gaussianpro"),
      ("cv2", "opencv-python"),
      ("imageio", "imageio"),
      ("plyfile", "plyfile"),
      ("lpips", "lpips"),
    ],
  },
  "atomgs": {
    "label": "AtomGS",
    "root": ROOT_DIR / "AtomGS-main",
    "env_name": "AtomGS",
    "required_modules": [
      ("torchvision", "torchvision"),
      ("diff_gaussian_rasterization", "diff_gaussian_rasterization"),
      ("simple_knn", "simple_knn"),
      ("plyfile", "plyfile"),
      ("lpips", "lpips"),
      ("open3d", "open3d"),
    ],
  },
}


def _algorithm_cuda_environment_checks(family_key: str) -> tuple[list[Dict[str, Any]], list[str], Dict[str, Any]]:
  checks: list[Dict[str, Any]] = []
  warnings: list[str] = []
  metadata: Dict[str, Any] = {}
  spec = ALGORITHM_CUDA_CHECK_SPECS[family_key]
  label = str(spec["label"])
  check_prefix = family_key.replace("-", "_")
  repo_root = spec["root"]
  env_name = str(spec["env_name"])
  required_modules = spec["required_modules"]

  repo_ok = repo_root.exists()
  checks.append(_runtime_check_item(
    f"{check_prefix}_repo",
    repo_ok,
    required=True,
    message=f"{label} repository exists." if repo_ok else f"Missing {repo_root.name} repository.",
    hint=f"Place {repo_root.name} next to web/ or update the adapter repo path.",
    details={"path": str(repo_root)},
  ))

  python_ok = sys.version_info[:2] == (3, 10)
  checks.append(_runtime_check_item(
    f"{check_prefix}_python310",
    python_ok,
    required=True,
    message=f"Python {sys.version_info.major}.{sys.version_info.minor} is active." if python_ok else f"Expected Python 3.10, got {sys.version.split()[0]}.",
    hint=f"Activate the {env_name} conda environment before launching the API server.",
    details={"python_executable": sys.executable, "version": sys.version.split()[0]},
  ))

  torch_probe = _python_probe(
    "import json, torch; "
    "info={'torch_version': torch.__version__, 'cuda_version': torch.version.cuda, "
    "'cuda_available': torch.cuda.is_available(), 'device_count': torch.cuda.device_count(), "
    "'device_name': torch.cuda.get_device_name(0) if torch.cuda.is_available() else '', "
    "'capability': torch.cuda.get_device_capability(0) if torch.cuda.is_available() else None}; "
    "print(json.dumps(info))"
  )
  torch_info: Dict[str, Any] = {}
  if torch_probe["ok"]:
    try:
      torch_info = json.loads(torch_probe["stdout"] or "{}")
    except Exception:
      torch_info = {}
  metadata["torch"] = torch_info

  torch_version = str(torch_info.get("torch_version", ""))
  torch_ok = torch_probe["ok"] and torch_version.startswith("2.2")
  checks.append(_runtime_check_item(
    f"{check_prefix}_torch22",
    torch_ok,
    required=True,
    message=f"PyTorch {torch_version} is active." if torch_ok else f"Expected PyTorch 2.2.x for {label}.",
    hint=f"Install PyTorch 2.2.2 with pytorch-cuda=12.1 in the {env_name} environment.",
    details={"probe": torch_probe, "torch": torch_info},
  ))

  cuda_version = str(torch_info.get("cuda_version", "") or "")
  cuda_ok = torch_probe["ok"] and cuda_version.startswith("12.1")
  checks.append(_runtime_check_item(
    f"{check_prefix}_cuda121",
    cuda_ok,
    required=True,
    message=f"PyTorch CUDA runtime is {cuda_version}." if cuda_ok else f"Expected torch.version.cuda to be 12.1, got {cuda_version or 'unavailable'}.",
    hint="Reinstall PyTorch with `pytorch-cuda=12.1`.",
    details={"cuda_version": cuda_version},
  ))

  cuda_available = bool(torch_info.get("cuda_available"))
  checks.append(_runtime_check_item(
    f"{check_prefix}_cuda_available",
    cuda_available,
    required=True,
    message="CUDA is available to PyTorch." if cuda_available else "PyTorch cannot access CUDA.",
    hint="Check NVIDIA driver, nvidia-smi, CUDA_VISIBLE_DEVICES, and conda environment activation.",
    details={"torch": torch_info},
  ))

  device_name = str(torch_info.get("device_name", "") or "")
  rtx4090_ok = cuda_available and "4090" in device_name
  checks.append(_runtime_check_item(
    f"{check_prefix}_rtx4090",
    rtx4090_ok,
    required=True,
    message=f"First CUDA device is {device_name}." if rtx4090_ok else f"Expected RTX 4090, got {device_name or 'no CUDA device'}.",
    hint=f"Run {label} on the remote Linux RTX 4090 host, or set CUDA_VISIBLE_DEVICES to the 4090.",
    details={"device_name": device_name, "capability": torch_info.get("capability")},
  ))

  toolkit = detect_cuda_toolkit()
  checks.append(_runtime_check_item(
    f"{check_prefix}_cuda_toolkit",
    bool(toolkit.get("toolkit_ready")),
    required=True,
    message="CUDA Toolkit and nvcc are available." if toolkit.get("toolkit_ready") else "CUDA Toolkit or nvcc was not found.",
    hint=f"Install CUDA Toolkit 12.1 and set CUDA_HOME before rebuilding {label} CUDA extensions.",
    details=toolkit,
  ))

  missing_modules: list[str] = []
  if not torch_probe["ok"]:
    missing_modules.append("torch")
  for module_name, package_name in required_modules:
    probe = _python_probe(f"import {module_name}")
    if not probe["ok"]:
      missing_modules.append(module_name)
    checks.append(_runtime_check_item(
      f"{check_prefix}_module_{module_name}",
      probe["ok"],
      required=True,
      message=f"Python module {module_name} imports successfully." if probe["ok"] else f"Missing or broken Python module {module_name}.",
      hint=f"Install/rebuild {package_name} in the {env_name} environment.",
      details={"probe": probe},
    ))

  if missing_modules:
    warnings.append(f"{label} missing modules: " + ", ".join(missing_modules))

  metadata["missing_python_modules"] = missing_modules
  metadata["python_modules"] = ["torch"] + [name for name, _package in required_modules]
  return checks, warnings, metadata


def environment_check(family: str | None = None) -> Dict[str, Any]:
  checks: list[Dict[str, Any]] = []
  warnings: list[str] = []

  web_index = WEB_DIR / "index.html"
  viewer_sh = WEB_DIR / "viewers" / "sh.html"
  viewer_sg = WEB_DIR / "viewers" / "sg.html"
  generated_dir = WEB_DIR / "generated"

  checks.append(_runtime_check_item(
    "api_runtime",
    True,
    required=True,
    message="API service runtime is active.",
    details={"python_executable": sys.executable},
  ))

  web_index_ok = web_index.exists()
  checks.append(_runtime_check_item(
    "web_index",
    web_index_ok,
    required=True,
    message="Web index page exists." if web_index_ok else "Missing web/index.html.",
    hint="Ensure the web assets are present under /web.",
    details={"path": str(web_index)},
  ))

  viewer_assets_ok = viewer_sh.exists() and viewer_sg.exists()
  checks.append(_runtime_check_item(
    "viewer_assets",
    viewer_assets_ok,
    required=True,
    message="Viewer pages are available." if viewer_assets_ok else "Missing viewer pages (sh.html/sg.html).",
    hint="Check /web/viewers/sh.html and /web/viewers/sg.html.",
    details={"sh": str(viewer_sh), "sg": str(viewer_sg)},
  ))

  generated_writable, generated_error = _write_probe(generated_dir)
  checks.append(_runtime_check_item(
    "generated_dir_writable",
    generated_writable,
    required=True,
    message="web/generated is writable." if generated_writable else "web/generated is not writable.",
    hint="Grant write permission to web/generated for runtime artifacts.",
    details={"path": str(generated_dir), "error": generated_error},
  ))

  python3_path = which("python3")
  python3_ok = bool(python3_path)
  checks.append(_runtime_check_item(
    "python3_command",
    python3_ok,
    required=False,
    message="python3 command is available." if python3_ok else "python3 command was not found in PATH.",
    hint="Install python3 or adjust PATH when using shell helpers.",
    details={"path": python3_path or ""},
  ))
  if not python3_ok:
    warnings.append("python3 is not in PATH; some shell-based operations may fail.")

  paramiko_available = importlib.util.find_spec("paramiko") is not None
  checks.append(_runtime_check_item(
    "paramiko_dependency",
    paramiko_available,
    required=False,
    message="paramiko is installed." if paramiko_available else "paramiko is not installed.",
    hint="Install with `python -m pip install paramiko` before remote SSH checks.",
  ))
  if not paramiko_available:
    warnings.append("Remote SSH features are unavailable until paramiko is installed.")

  family_key = (family or "").strip().lower()
  algorithm_metadata: Dict[str, Any] = {}
  cuda_check_spec = ALGORITHM_CUDA_CHECK_SPECS.get(family_key)
  algorithm_label = str(cuda_check_spec.get("label", family_key)) if cuda_check_spec else family_key
  if cuda_check_spec:
    algorithm_checks, algorithm_warnings, algorithm_metadata = _algorithm_cuda_environment_checks(family_key)
    checks.extend(algorithm_checks)
    warnings.extend(algorithm_warnings)

  required_checks_ok = all(item["ok"] for item in checks if item["required"])
  web_required_names = {"api_runtime", "web_index", "viewer_assets", "generated_dir_writable"}
  web_required_ok = all(item["ok"] for item in checks if item["required"] and item["name"] in web_required_names)
  runtime_ready = web_required_ok
  algorithm_ready = required_checks_ok
  remote_ready = runtime_ready and paramiko_available

  summary = "Web runtime checks passed."
  if not runtime_ready:
    summary = "Web runtime checks failed. Fix required items before continuing."
  elif cuda_check_spec and not algorithm_ready:
    summary = f"Web runtime is ready, but {algorithm_label} Python/CUDA checks failed."
  elif not remote_ready:
    summary = "Web runtime is ready, but remote SSH checks require paramiko."
  elif cuda_check_spec:
    summary = f"Web runtime and {algorithm_label} Python/CUDA checks passed."

  adapter = get_adapter(family_key) if family_key else None
  requirement_spec = adapter_requirement_spec(adapter) if adapter else {"python_modules": [], "install_commands": {}}

  return {
    "family": family or "",
    "runtime_ready": runtime_ready,
    "algorithm_ready": algorithm_ready,
    "remote_ready": remote_ready,
    "checks": checks,
    "warnings": warnings,
    "summary": summary,
    # Backward-compatible fields used by previous UI versions.
    "web_runnable": runtime_ready,
    "pipeline_ready": algorithm_ready,
    "repo_exists": True,
    "gpu_visible": bool(which("nvidia-smi")),
    "python_executable": sys.executable,
    "python_modules": algorithm_metadata.get("python_modules", requirement_spec.get("python_modules", [])),
    "missing_python_modules": algorithm_metadata.get("missing_python_modules", []),
    "install_commands": requirement_spec.get("install_commands", {}),
    "cuda_toolkit": detect_cuda_toolkit(),
    "commands": {
      "python3": {"found": python3_ok, "path": python3_path},
      "paramiko": {"found": paramiko_available, "path": "python module" if paramiko_available else ""},
      "nvidia-smi": {"found": bool(which("nvidia-smi")), "path": which("nvidia-smi")},
    },
    "scripts": {
      "index.html": {"exists": web_index_ok, "path": str(web_index)},
      "viewers/sh.html": {"exists": viewer_sh.exists(), "path": str(viewer_sh)},
      "viewers/sg.html": {"exists": viewer_sg.exists(), "path": str(viewer_sg)},
    },
  }


def build_capture_pipeline(
  session_id: str,
  family: str,
  *,
  output_dir: str,
  repo_path: str = "",
  checkpoint_path: str = "",
  training_args: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
  adapter = get_adapter(family)
  if not adapter:
    raise ValueError(f"Unknown algorithm family: {family}")

  resolved_repo = resolve_workspace_path(repo_path or adapter.get("repo_path", ""))
  workspace_result = prepare_colmap_workspace(session_id, family, resolved_repo)
  workspace_root = workspace_result["workspace_root"]
  resolved_output_dir = output_dir or str((WEB_DIR / "generated" / "runs" / session_id / family).resolve())

  commands: list[dict[str, str]] = []
  convert_command = workspace_result.get("suggested_command")
  if convert_command:
    commands.append({"name": "prepare_colmap", "command": convert_command})

  for operation in ("train", "render"):
    command = format_operation_command(
      adapter,
      operation,
      input_path=workspace_result["input_dir"],
      output_dir=resolved_output_dir,
      workspace=workspace_root,
      checkpoint_path=checkpoint_path,
      repo_path=resolved_repo,
      training_args=training_args,
    )
    if command:
      if operation == "train":
        command = apply_training_eval_arg(
          family,
          command,
          user_disabled_eval=bool((training_args or {}).get("disable_eval") or (training_args or {}).get("no_eval")),
        )
      commands.append({"name": operation, "command": command})

  resolved_cwd = resolve_workspace_path(adapter.get("default_cwd") or resolved_repo)
  requires_repo_main = any("python main.py" in item["command"] for item in commands)
  if requires_repo_main and not resolved_cwd:
    raise ValueError(
      f"{family} capture pipeline requires repo_path/default_cwd because train/render use 'python main.py'."
    )

  pipeline_script = Path(workspace_root) / "run_capture_pipeline.sh"
  script_lines = ["#!/usr/bin/env bash", "set -e"]
  script_lines.extend(item["command"] for item in commands)
  pipeline_script.write_text("\n".join(script_lines) + "\n", encoding="utf-8")

  pipeline_script_cmd = Path(workspace_root) / "run_capture_pipeline.cmd"
  cmd_lines = ["@echo off", "setlocal"]
  for item in commands:
    cmd_lines.append(item["command"])
    cmd_lines.append("if errorlevel 1 exit /b %errorlevel%")
  cmd_lines.append("exit /b 0")
  pipeline_script_cmd.write_text("\r\n".join(cmd_lines) + "\r\n", encoding="utf-8")

  return {
    "session_id": session_id,
    "family": family,
    "workspace": workspace_result,
    "output_dir": resolved_output_dir,
    "repo_path": resolved_repo,
    "checkpoint_path": checkpoint_path,
    "commands": commands,
    "script_path": str(pipeline_script),
    "script_path_cmd": str(pipeline_script_cmd),
  }


def safe_generated_child(root: Path, *parts: str) -> Path:
  root_resolved = root.resolve()
  candidate = root_resolved
  for part in parts:
    raw = str(part or "").strip()
    if not raw:
      continue
    candidate = candidate / raw
  candidate = candidate.resolve()
  if root_resolved == candidate or root_resolved not in candidate.parents:
    raise ValueError(f"Unsafe generated path: {candidate}")
  return candidate


def inspect_flow_data(session_id: str, capture_id: str = "", family: str = "") -> Dict[str, Any]:
  resolved_session = str(session_id or "").strip()
  resolved_capture = str(capture_id or "").strip()
  resolved_family = str(family or "").strip()
  if not resolved_session:
    raise ValueError("session_id is required")

  stream_session_dir = safe_generated_child(STREAM_DIR, resolved_session)
  dataset_session_dir = safe_generated_child(DATASET_DIR, resolved_session)
  workspace_session_dir = safe_generated_child(WORKSPACE_DIR, resolved_session)

  captures_root = stream_session_dir / "captures"
  captures: list[Dict[str, Any]] = []
  if captures_root.exists() and captures_root.is_dir():
    for child in sorted(captures_root.iterdir(), key=lambda item: item.name):
      if not child.is_dir():
        continue
      input_dir = child / "input"
      frames = image_file_paths(input_dir)
      latest_frame = file_brief(frames[-1]) if frames else None
      captures.append({
        "capture_id": child.name,
        "input_dir": str(input_dir.resolve()),
        "frame_count": len(frames),
        "latest_frame": latest_frame,
        "frames": [file_brief(path) for path in frames[-12:]],
        "mtime": max((path.stat().st_mtime for path in frames), default=child.stat().st_mtime),
      })

  selected_capture = None
  if resolved_capture:
    selected_capture = next((item for item in captures if item["capture_id"] == resolved_capture), None)
  if selected_capture is None and captures:
    selected_capture = sorted(captures, key=lambda item: item.get("mtime", 0), reverse=True)[0]
    resolved_capture = str(selected_capture.get("capture_id", resolved_capture))

  output_dir = stream_session_dir / "output"
  output_files = image_file_paths(output_dir)
  latest_output = file_brief(output_files[-1]) if output_files else None

  dataset_root = dataset_session_dir / "datasets"
  datasets: list[Dict[str, Any]] = []
  if dataset_root.exists() and dataset_root.is_dir():
    for child in sorted(dataset_root.iterdir(), key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True):
      if not child.is_dir():
        continue
      manifest_path = child / "capture_session.json"
      manifest = read_json_if_present(manifest_path)
      datasets.append({
        "dataset_id": str(manifest.get("dataset_id") or child.name),
        "dataset_name": str(manifest.get("dataset_name") or child.name),
        "capture_id": str(manifest.get("capture_id") or ""),
        "frame_count": int(manifest.get("frame_count") or 0),
        "dataset_root": str(child.resolve()),
        "images_dir": str((child / "images").resolve()),
        "manifest_url": file_url_for_path(manifest_path) if manifest_path.exists() else "",
        "generated_at": manifest.get("generated_at") or child.stat().st_mtime,
      })

  workspace_roots: list[Path] = []
  if resolved_family:
    family_root = workspace_session_dir / resolved_family
    if family_root.exists():
      workspace_roots.append(family_root)
  elif workspace_session_dir.exists():
    workspace_roots = [path for path in workspace_session_dir.iterdir() if path.is_dir()]

  workspaces: list[Dict[str, Any]] = []
  for root in workspace_roots:
    for manifest_path in sorted(root.glob("*/workspace_manifest.json"), key=lambda item: item.stat().st_mtime, reverse=True):
      manifest = read_json_if_present(manifest_path)
      workspace_root = manifest_path.parent
      workspaces.append({
        "dataset_id": str(manifest.get("dataset_id") or workspace_root.name),
        "dataset_name": str(manifest.get("dataset_name") or workspace_root.name),
        "family": str(manifest.get("family") or root.name),
        "capture_id": str(manifest.get("capture_id") or ""),
        "frame_count": int(manifest.get("frame_count") or 0),
        "workspace_root": str(workspace_root.resolve()),
        "input_dir": str((workspace_root / "input").resolve()),
        "manifest_url": file_url_for_path(manifest_path),
        "generated_at": manifest.get("generated_at") or manifest_path.stat().st_mtime,
      })

  return {
    "ok": True,
    "session_id": resolved_session,
    "capture_id": resolved_capture,
    "family": resolved_family,
    "capture": {
      "exists": bool(selected_capture),
      "current": selected_capture,
      "captures": captures,
      "capture_count": len(captures),
      "frame_count": int(selected_capture.get("frame_count", 0)) if selected_capture else 0,
    },
    "upload": {
      "exists": stream_session_dir.exists(),
      "stream_dir": str(stream_session_dir),
      "output_dir": str(output_dir.resolve()),
      "latest_output": latest_output,
      "output_count": len(output_files),
      "uploaded_frame_count": sum(int(item.get("frame_count", 0)) for item in captures),
    },
    "session_prep": {
      "exists": bool(datasets or workspaces),
      "dataset_count": len(datasets),
      "workspace_count": len(workspaces),
      "datasets": datasets,
      "workspaces": workspaces,
    },
  }


def delete_flow_data_stage(
  payload: Dict[str, Any],
  *,
  remover: Callable[[Path], None] | None = None,
  exists: Callable[[Path], bool] | None = None,
) -> Dict[str, Any]:
  session_id = str(payload.get("session_id", "")).strip()
  capture_id = str(payload.get("capture_id", "")).strip()
  family = str(payload.get("family", "")).strip()
  stage = str(payload.get("stage", "")).strip().lower()
  if not session_id:
    raise ValueError("session_id is required")
  if stage not in {"capture", "upload", "session_prep", "all_staging"}:
    raise ValueError("stage must be one of: capture, upload, session_prep, all_staging")

  remover = remover or (lambda path: shutil.rmtree(path))
  exists = exists or (lambda path: path.exists())

  targets: list[Path] = []
  stream_session_dir = safe_generated_child(STREAM_DIR, session_id)
  if stage == "capture":
    if capture_id:
      targets.append(safe_generated_child(STREAM_DIR, session_id, "captures", capture_id))
    else:
      targets.append(safe_generated_child(STREAM_DIR, session_id, "captures"))
    targets.append(safe_generated_child(STREAM_DIR, session_id, "output"))
  elif stage == "upload":
    targets.append(stream_session_dir)
  elif stage == "session_prep":
    targets.append(safe_generated_child(DATASET_DIR, session_id))
    targets.append(safe_generated_child(WORKSPACE_DIR, session_id))
  elif stage == "all_staging":
    targets.extend(flow_temporary_dirs(session_id))

  deleted: list[str] = []
  for target in targets:
    if not exists(target):
      continue
    remover(target)
    deleted.append(str(target))

  return {
    "ok": True,
    "stage": stage,
    "session_id": session_id,
    "capture_id": capture_id,
    "deleted_paths": deleted,
    "data": inspect_flow_data(session_id, capture_id, family),
  }


def _training_format_args(payload: Dict[str, Any]) -> Dict[str, Any]:
  return {
    "iterations": payload.get("iterations", 30_000),
    "image_folder": payload.get("image_folder", ""),
    "save_interval": payload.get("save_interval", 10_000),
    "gpcc_codec_path": payload.get("gpcc_codec_path", os.environ.get("GSC_GPCC_CODEC_PATH", "tmc3")),
    "fcgs_lmd": payload.get("fcgs_lmd", payload.get("lmd", 1e-4)),
    "voxel_size": payload.get("voxel_size", 0.001),
    "update_init_factor": payload.get("update_init_factor", 16),
    "lmbda": payload.get("lmbda", 0.001),
    "mask_lr_final": payload.get("mask_lr_final", 0.0001),
    "position_lr_init": payload.get("position_lr_init", 0.0),
    "position_lr_final": payload.get("position_lr_final", 0.0),
    "position_lr_delay_mult": payload.get("position_lr_delay_mult", 0.01),
    "position_lr_max_steps": payload.get("position_lr_max_steps", 30_000),
    "offset_lr_init": payload.get("offset_lr_init", 0.01),
    "offset_lr_final": payload.get("offset_lr_final", 0.0001),
    "offset_lr_delay_mult": payload.get("offset_lr_delay_mult", 0.01),
    "offset_lr_max_steps": payload.get("offset_lr_max_steps", 30_000),
    "mask_lr_init": payload.get("mask_lr_init", 0.01),
    "mask_lr_delay_mult": payload.get("mask_lr_delay_mult", 0.01),
    "mask_lr_max_steps": payload.get("mask_lr_max_steps", 30_000),
    "feature_lr": payload.get("feature_lr", 0.0075),
    "opacity_lr": payload.get("opacity_lr", 0.02),
    "scaling_lr": payload.get("scaling_lr", 0.007),
    "rotation_lr": payload.get("rotation_lr", 0.002),
  }


def extract_remote_config_input(payload: Dict[str, Any]) -> Dict[str, Any]:
  remote_payload = payload.get("remote", {}) if isinstance(payload.get("remote"), dict) else {}
  return {
    "host": remote_payload.get("host", payload.get("remote_host", "")),
    "port": remote_payload.get("port", payload.get("remote_port", 22)),
    "username": remote_payload.get("username", payload.get("remote_username", "")),
    "password": remote_payload.get("password", payload.get("remote_password", "")),
    "repo_path": remote_payload.get("repo_path", payload.get("remote_repo_path", "")),
    "workspace_root": remote_payload.get("workspace_root", payload.get("remote_workspace_root", "")),
    "output_root": remote_payload.get("output_root", payload.get("remote_output_root", "")),
    "python": remote_payload.get("python", payload.get("remote_python", "python3")),
    "activate_cmd": remote_payload.get("activate_cmd", payload.get("remote_activate_cmd", "")),
  }


def validate_path_confirmation_payload(
  payload: Dict[str, Any],
  *,
  command_template: str,
  expected_checkpoint_path: str,
  expected_output_dir: str,
) -> tuple[bool, str, Dict[str, Any]]:
  confirmation = payload.get("path_confirmation")
  if not isinstance(confirmation, dict):
    return (
      False,
      "Missing path_confirmation. Please confirm checkpoint_path/output_dir in UI before submit.",
      {
        "required": {
          "path_confirmation.confirmed": True,
          "path_confirmation.checkpoint_path": "string",
          "path_confirmation.output_dir": "string",
        },
      },
    )

  if confirmation.get("confirmed") is not True:
    return (
      False,
      "path_confirmation.confirmed must be true.",
      {"path_confirmation": confirmation},
    )

  requires_checkpoint = "{checkpoint_path}" in command_template
  requires_output = "{output_dir}" in command_template

  confirmed_checkpoint = str(confirmation.get("checkpoint_path", "")).strip()
  confirmed_output_raw = str(confirmation.get("output_dir", "")).strip()

  expected_checkpoint = str(expected_checkpoint_path or "").strip()
  expected_output = str(expected_output_dir or "").strip()
  resolved_confirmed_output = resolve_project_path(confirmed_output_raw) if confirmed_output_raw else ""

  mismatches: Dict[str, Any] = {}
  if requires_checkpoint and confirmed_checkpoint != expected_checkpoint:
    mismatches["checkpoint_path"] = {
      "confirmed": confirmed_checkpoint,
      "expected": expected_checkpoint,
    }

  if requires_output:
    if not expected_output:
      mismatches["output_dir"] = {
        "confirmed": confirmed_output_raw,
        "expected": "<non-empty>",
      }
    elif confirmed_output_raw != expected_output and resolved_confirmed_output != expected_output:
      mismatches["output_dir"] = {
        "confirmed": confirmed_output_raw,
        "confirmed_resolved": resolved_confirmed_output,
        "expected": expected_output,
      }

  if mismatches:
    return (
      False,
      "Path confirmation mismatch. Please re-confirm checkpoint_path/output_dir in UI and retry.",
      {
        "required_fields": {
          "checkpoint_path": requires_checkpoint,
          "output_dir": requires_output,
        },
        "mismatches": mismatches,
      },
    )

  return (
    True,
    "",
    {
      "required_fields": {
        "checkpoint_path": requires_checkpoint,
        "output_dir": requires_output,
      },
      "confirmed_at": confirmation.get("confirmed_at", ""),
    },
  )


def operation_command_template(adapter: Dict[str, Any], operation: str) -> str:
  operation_config = adapter.get("operations", {}).get(operation, {})
  if not operation_config.get("enabled"):
    raise ValueError(f"Operation {operation} is not enabled for {adapter.get('family', 'unknown')}")
  command_template = operation_config.get("template")
  if not command_template:
    raise ValueError(f"Operation {operation} does not define command template")
  return command_template


def format_remote_operation_command(
  adapter: Dict[str, Any],
  operation: str,
  format_args: Dict[str, Any],
  python_bin: str,
) -> str:
  command_template = operation_command_template(adapter, operation)
  command = command_template.format(**format_args)
  command = sanitize_formatted_command(command_template, command, format_args)
  if command.lstrip().startswith("python "):
    command = f"{python_bin}{command.lstrip()[len('python') :]}"
  return command


def post_train_config_for_adapter(
  adapter: Dict[str, Any],
  format_args: Dict[str, Any],
  python_bin: str,
  *,
  format_commands: bool = True,
) -> Dict[str, Any]:
  explicit = adapter.get("post_train") if isinstance(adapter.get("post_train"), dict) else {}
  operations = adapter.get("operations", {}) if isinstance(adapter.get("operations"), dict) else {}
  family = str(adapter.get("family", "")).strip()
  train_produces_eval = bool(explicit.get("train_produces_eval"))
  run_render = bool(explicit.get("run_render", operations.get("render", {}).get("enabled", False)))
  run_metrics = bool(explicit.get("run_metrics", operations.get("metrics", {}).get("enabled", False)))
  render_template = str((operations.get("render", {}) or {}).get("template", "") or "")
  metrics_template = str((operations.get("metrics", {}) or {}).get("template", "") or "")
  render_command = str(explicit.get("render_command", "") or "").strip()
  metrics_command = str(explicit.get("metrics_command", "") or "").strip()
  iteration = str(explicit.get("iteration", "") or format_args.get("iterations") or "30000")

  if format_commands and run_render and not render_command and family != "gaussian-splatting-lightning" and operations.get("render", {}).get("enabled"):
    render_command = format_remote_operation_command(adapter, "render", format_args, python_bin)
  if format_commands and run_metrics and not metrics_command and operations.get("metrics", {}).get("enabled"):
    metrics_command = format_remote_operation_command(adapter, "metrics", format_args, python_bin)

  return {
    "train_produces_eval": train_produces_eval,
    "run_render": run_render,
    "run_metrics": run_metrics,
    "render_template": render_template,
    "metrics_template": metrics_template,
    "render_command": render_command,
    "metrics_command": metrics_command,
    "iteration": iteration,
  }




def effective_colmap_preflight_required(payload: Dict[str, Any]) -> bool:
  return effective_auto_colmap(
    payload.get("check_colmap_required", False),
    payload.get("use_existing_remote_dataset", False),
  )


def build_remote_algorithm_preview(payload: Dict[str, Any]) -> Dict[str, Any]:
  family = str(payload.get("algorithm_family", "")).strip()
  if not family:
    raise ValueError("algorithm_family is required")

  adapter = get_adapter(family)
  if not adapter:
    raise ValueError(f"Unknown algorithm family: {family}")

  operation = str(payload.get("operation", "train")).strip() or "train"
  command_template = operation_command_template(adapter, operation)
  remote_config = validate_remote_config(extract_remote_config_input(payload))

  session_id = validated_session_id(payload.get("session_id", "default-session"))
  dataset_name = str(payload.get("dataset_name", "")).strip() or session_id
  preview_job_id = str(payload.get("preview_job_id", "")).strip() or f"preview-{uuid.uuid4().hex[:8]}"
  remote_run_key = build_remote_run_key(dataset_name, family, time.time())
  use_existing_remote_dataset = bool(payload.get("use_existing_remote_dataset", False))
  remote_dataset_id = str(payload.get("remote_dataset_id", "")).strip()
  remote_dataset_path = str(payload.get("remote_dataset_path", "")).strip()

  if use_existing_remote_dataset:
    if remote_dataset_path:
      remote_workspace_for_command = str(PurePosixPath(remote_dataset_path))
      if not remote_dataset_id:
        remote_dataset_id = PurePosixPath(remote_workspace_for_command).parent.name
    elif remote_dataset_id:
      remote_workspace_for_command = str(
        PurePosixPath(remote_config["workspace_root"]) / "datasets" / family / remote_dataset_id / "workspace"
      )
    else:
      raise ValueError("remote_dataset_id or remote_dataset_path is required when use_existing_remote_dataset=true")
  else:
    dataset_slug = _normalize_slug(dataset_name, fallback="dataset")
    remote_dataset_id = remote_dataset_id or f"{dataset_slug}-{preview_job_id.replace('preview-', '')}"
    remote_workspace_for_command = str(
      PurePosixPath(remote_config["workspace_root"]) / "datasets" / family / remote_dataset_id / "workspace"
    )

  remote_paths = build_remote_paths(
    workspace_root=remote_config["workspace_root"],
    output_root=remote_config["output_root"],
    session_id=session_id,
    family=family,
    job_id=preview_job_id,
    run_key=remote_run_key,
  )
  resolved_output_dir = remote_job_local_output_dir(session_id, family, preview_job_id)
  remote_tmux_session = build_remote_tmux_session_name(family=family, job_id=preview_job_id, run_key=remote_run_key)
  remote_tmux_attach_command = build_remote_tmux_attach_command(remote_config, remote_tmux_session)

  checkpoint_path = str(payload.get("remote_checkpoint_path", payload.get("checkpoint_path", ""))).strip()
  input_path = str(payload.get("input_path", "")).strip()
  source_path = str(payload.get("source", "")).strip()
  format_args = {
    "input_path": input_path,
    "output_dir": remote_paths["output_dir"],
    "workspace": remote_workspace_for_command,
    "checkpoint_path": checkpoint_path,
    "repo_path": remote_config["repo_path"],
    "source": source_path or remote_workspace_for_command,
    **_training_format_args(payload),
  }
  remote_command = command_template.format(**format_args)
  remote_command = sanitize_formatted_command(command_template, remote_command, format_args)
  remote_command = apply_training_eval_arg(
    family,
    remote_command,
    user_disabled_eval=bool(payload.get("disable_eval") or payload.get("no_eval")),
  )
  if remote_command.lstrip().startswith("python "):
    remote_command = f"{remote_config['python']}{remote_command.lstrip()[len('python') :]}"
  post_train_config = post_train_config_for_adapter(adapter, format_args, remote_config["python"])

  steps = [
    f"mkdir -p {shlex.quote(remote_paths['output_dir'])}",
    f"cd {shlex.quote(remote_config['repo_path'])}",
  ]
  if remote_config["activate_cmd"]:
    steps.append(remote_config["activate_cmd"])
  steps.append(remote_command)
  remote_script = " && ".join(steps)
  missing_inputs = missing_template_fields(command_template, format_args)

  return {
    "preview_job_id": preview_job_id,
    "algorithm_family": family,
    "operation": operation,
    "execution_backend": "tmux",
    "remote_tmux_session": remote_tmux_session,
    "remote_tmux_attach_command": remote_tmux_attach_command,
    "remote_run_key": remote_run_key,
    "tmux_available": None,
    "tmux_install": {
      "policy": "submit_time_mamba_or_sudo_n",
      "uses_password_sudo": False,
    },
    "dataset_name": dataset_name,
    "remote_dataset_id": remote_dataset_id,
    "remote_workspace": remote_workspace_for_command,
    "remote_output_dir": remote_paths["output_dir"],
    "local_output_dir": resolved_output_dir,
    "checkpoint_path": checkpoint_path,
    "command_template": command_template,
    "remote_command": remote_command,
    "post_train": post_train_config,
    "shell_command": f"bash -lc {shlex.quote(remote_script)}",
    "missing_inputs": missing_inputs,
    "remote": sanitize_remote_config(remote_config),
    "path_confirmation": {
      "confirmed": True,
      "family": family,
      "operation": operation,
      "checkpoint_path": checkpoint_path,
      "output_dir": resolved_output_dir,
    },
  }


def safe_int(raw_value: Any, default: int, *, minimum: int, maximum: int) -> int:
  try:
    value = int(raw_value)
  except Exception:
    value = default
  return max(minimum, min(maximum, value))


def file_download_response(
  handler: "ApiHandler",
  file_path: Path,
  *,
  content_type: str,
  download_name: str,
) -> None:
  if not file_path.exists() or not file_path.is_file():
    json_response(handler, {"error": f"File not found: {file_path}"}, status=404)
    return

  body = file_path.read_bytes()
  handler.send_response(HTTPStatus.OK)
  handler.send_header("Content-Type", content_type)
  handler.send_header("Content-Length", str(len(body)))
  handler.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
  handler.end_headers()
  handler.wfile.write(body)


def csv_text_response(handler: "ApiHandler", text: str, *, download_name: str) -> None:
  body = text.encode("utf-8")
  handler.send_response(HTTPStatus.OK)
  handler.send_header("Content-Type", "text/csv; charset=utf-8")
  handler.send_header("Content-Length", str(len(body)))
  handler.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
  handler.end_headers()
  handler.wfile.write(body)




class ApiHandler(SimpleHTTPRequestHandler):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, directory=str(WEB_DIR), **kwargs)

  def translate_path(self, path: str) -> str:
    # Static serving is restricted to WEB_DIR. Both "/x" and legacy "/web/x"
    # style URLs resolve to WEB_DIR/x; ".." segments are dropped.
    parsed = urlparse(path)
    parts = [part for part in parsed.path.split("/") if part and part not in (".", "..")]
    if parts and parts[0] == "web":
      parts = parts[1:]
    if not parts:
      parts = ["index.html"]
    if self._static_path_denied(parts):
      return str(WEB_DIR / "__denied__")
    return str(WEB_DIR.joinpath(*parts))

  @staticmethod
  def _static_path_denied(parts: list) -> bool:
    # Never serve credentials, server code, internal tooling, or job state files.
    if any(part.startswith(".") for part in parts):
      return True
    if parts[0] in {"server", "tools", "config", "project_md", "_backup"}:
      return True
    if len(parts) >= 2 and parts[0] == "generated" and parts[1] == "job_logs":
      return True
    return False

  def _security_gate(self, *, require_api_token: bool) -> bool:
    if not request_host_allowed(self):
      allowed = ", ".join(sorted(SECURITY_STATE["allowed_host_values"]))
      LOGGER.warning("Rejected request with unknown Host header: %s %s", self.command, self.path)
      json_response(
        self,
        {"ok": False, "error": f"Unknown Host header. Allowed hosts: {allowed}"},
        status=403,
      )
      return False
    if not request_origin_allowed(self):
      LOGGER.warning("Rejected cross-origin request: %s %s", self.command, self.path)
      json_response(self, {"ok": False, "error": "Cross-origin requests are not allowed."}, status=403)
      return False
    if require_api_token and not request_token_valid(self):
      LOGGER.warning("Rejected request without valid token: %s %s", self.command, self.path)
      json_response(
        self,
        {
          "ok": False,
          "error": (
            "Missing or invalid access token. Open the workbench via the served web page, "
            "or pass the X-Auth-Token header (the token is printed at server start and "
            "stored in web/config/server.token)."
          ),
        },
        status=401,
      )
      return False
    return True

  def _serve_index_with_token(self) -> None:
    console_entry = self.path.startswith("/web/console") or self.path == "/console"
    html_path = WEB_DIR / "console" / "index.html" if console_entry else WEB_DIR / "index.html"
    try:
      html = html_path.read_text(encoding="utf-8")
    except OSError:
      self.send_error(HTTPStatus.NOT_FOUND)
      return
    token = SECURITY_STATE["token"] if SECURITY_STATE["auth_enabled"] else ""
    snippet = f"<script>window.__WEBSCAN_API_TOKEN__ = {json.dumps(token)};</script>"
    if "</head>" in html:
      html = html.replace("</head>", f"  {snippet}\n</head>", 1)
    else:
      html = snippet + html
    body = html.encode("utf-8")
    self.send_response(HTTPStatus.OK)
    self.send_header("Content-Type", "text/html; charset=utf-8")
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)

  def _disable_static_cache(self) -> None:
    for header in ("If-Modified-Since", "If-None-Match"):
      if header in self.headers:
        del self.headers[header]

  def end_headers(self) -> None:
    self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
    self.send_header("Pragma", "no-cache")
    self.send_header("Expires", "0")
    super().end_headers()

  def do_OPTIONS(self) -> None:
    # Same-origin requests never preflight; cross-origin preflights get no
    # Access-Control headers and are therefore rejected by the browser.
    self.send_response(HTTPStatus.NO_CONTENT)
    self.end_headers()

  def do_HEAD(self) -> None:
    self._disable_static_cache()
    super().do_HEAD()

  def do_GET(self) -> None:
    parsed = urlparse(self.path)
    query = parse_qs(parsed.query)
    if not self._security_gate(require_api_token=parsed.path.startswith("/api/")):
      return
    if parsed.path in ("/", "/index.html", "/web/", "/web/index.html",
                       "/web/console", "/web/console/", "/web/console/index.html",
                       "/console", "/console/", "/console/index.html"):
      self._serve_index_with_token()
      return
    if parsed.path == "/api/health":
      json_response(self, {
        "ok": True,
        "root_dir": str(ROOT_DIR),
        "web_dir": str(WEB_DIR),
      })
      return
    if parsed.path == "/api/algorithms":
      algorithms = list_adapters()
      json_response(self, {"algorithms": algorithms, "validation": [validate_adapter(item) for item in algorithms]})
      return
    if parsed.path.startswith("/api/algorithms/"):
      family = parsed.path.split("/")[-1]
      adapter = get_adapter(family)
      if not adapter:
        json_response(self, {"error": f"Unknown algorithm family: {family}"}, status=404)
        return
      json_response(self, {"algorithm": adapter, "operations": supported_operations(adapter), "validation": validate_adapter(adapter)})
      return
    if parsed.path == "/api/jobs":
      ordered_jobs = sorted(
        (enrich_job(job) for job in list(JOBS.values())),
        key=lambda item: (
          -float(item.get("created_at") or 0),
          str(item.get("dataset") or ""),
          str(item.get("algorithm_family") or ""),
          str(item.get("id") or ""),
        ),
      )
      json_response(self, {"jobs": ordered_jobs})
      return
    if parsed.path == "/api/flow/data":
      try:
        result = inspect_flow_data(
          session_id=query.get("session_id", [""])[0],
          capture_id=query.get("capture_id", [""])[0],
          family=query.get("family", [""])[0],
        )
        json_response(self, result)
      except Exception as exc:
        error_response(
          self,
          code="WGSC-FLOW-DATA-001",
          step="flow_data",
          message=str(exc),
          status=400,
        )
      return
    if parsed.path == "/api/results/discover":
      limit = safe_int(query.get("limit", ["30"])[0], 30, minimum=1, maximum=100)
      max_scan_dirs = safe_int(query.get("max_scan_dirs", ["1800"])[0], 1800, minimum=200, maximum=8000)
      results = discover_runtime_results(limit=limit, max_scan_dirs=max_scan_dirs)
      json_response(self, {
        "ok": True,
        "results": results,
        "latest": results[0] if results else None,
        "count": len(results),
      })
      return
    if parsed.path.startswith("/api/jobs/"):
      parts = parsed.path.strip("/").split("/")
      if len(parts) < 3:
        json_response(self, {"error": f"Unsupported endpoint: {parsed.path}"}, status=404)
        return
      job_id = parts[2]
      job = JOBS.get(job_id)
      if not job:
        json_response(self, {"error": f"Unknown job: {job_id}"}, status=404)
        return

      ensure_job_logging(job)

      if len(parts) == 3:
        json_response(self, enrich_job(job))
        return

      if len(parts) == 4 and parts[3] == "logs":
        if "cursor" in query:
          cursor = safe_int(query.get("cursor", ["0"])[0], 0, minimum=0, maximum=10_000_000_000)
          delta = read_job_log_since_cursor(job, cursor)
          json_response(self, {
            "ok": True,
            "job_id": job_id,
            "status": job.get("status", "-"),
            "cursor": delta["cursor"],
            "from_cursor": delta["from_cursor"],
            "log_lines": delta["lines"],
            "log_text": "".join(delta["lines"]),
            "truncated": delta["truncated"],
            "logs_download_url": f"/api/jobs/{job_id}/logs/download",
            "metrics_csv_url": f"/api/jobs/{job_id}/metrics.csv",
          })
          return

        page = safe_int(query.get("page", ["1"])[0], 1, minimum=1, maximum=100000)
        page_size = safe_int(query.get("page_size", ["20"])[0], 20, minimum=1, maximum=200)
        tail_lines = safe_int(query.get("tail_lines", ["120"])[0], 120, minimum=1, maximum=3000)
        metrics_page = build_metrics_page(job, page, page_size)
        log_tail = read_job_log_tail_lines(job, tail_lines)
        json_response(self, {
          "ok": True,
          "job_id": job_id,
          "status": job.get("status", "-"),
          "metrics_page": metrics_page,
          "log_tail_lines": log_tail,
          "logs_download_url": f"/api/jobs/{job_id}/logs/download",
          "metrics_csv_url": f"/api/jobs/{job_id}/metrics.csv",
        })
        return

      if len(parts) == 5 and parts[3] == "logs" and parts[4] == "download":
        log_file_value = str(job.get("log_file", "")).strip()
        if not log_file_value:
          json_response(self, {"error": f"No log file for job: {job_id}"}, status=404)
          return
        file_download_response(
          self,
          Path(log_file_value),
          content_type="text/plain; charset=utf-8",
          download_name=f"{job_id}.runtime.log",
        )
        return

      if len(parts) == 4 and parts[3] == "metrics.csv":
        csv_file_value = str(job.get("metrics_csv_file", "")).strip()
        if not csv_file_value:
          json_response(self, {"error": f"No metrics csv for job: {job_id}"}, status=404)
          return
        file_download_response(
          self,
          Path(csv_file_value),
          content_type="text/csv; charset=utf-8",
          download_name=f"{job_id}.metrics.csv",
        )
        return

      json_response(self, {"error": f"Unsupported endpoint: {parsed.path}"}, status=404)
      return
    self._disable_static_cache()
    super().do_GET()

  def do_POST(self) -> None:
    parsed = urlparse(self.path)
    if not self._security_gate(require_api_token=True):
      return
    length = int(self.headers.get("Content-Length", "0"))
    raw = self.rfile.read(length) if length else b"{}"
    try:
      payload = json.loads(raw.decode("utf-8") or "{}")
    except Exception as exc:
      error_response(
        self,
        code="WGSC-REQUEST-JSON-001",
        step="request",
        message="Invalid JSON payload.",
        reason=str(exc),
        status=400,
        manual_anchor="#9-%E5%B8%B8%E8%A7%81%E9%94%99%E8%AF%AF%E7%A0%81%E4%B8%8E%E8%A7%A3%E5%86%B3%E6%96%B9%E6%A1%88",
      )
      return

    if parsed.path == "/api/jobs/analysis/export":
      raw_job_ids = payload.get("job_ids", [])
      job_ids = [str(item).strip() for item in raw_job_ids if str(item).strip()] if isinstance(raw_job_ids, list) else []
      if not job_ids:
        error_response(
          self,
          code="WGSC-ANALYSIS-EXPORT-001",
          step="analysis",
          message="job_ids must be a non-empty list.",
          status=400,
        )
        return
      try:
        csv_text_response(self, analysis_export_csv(job_ids), download_name="analysis-metrics.csv")
      except Exception as exc:
        error_response(
          self,
          code="WGSC-ANALYSIS-EXPORT-001",
          step="analysis",
          message=str(exc),
          status=400,
        )
      return

    if parsed.path.startswith("/api/jobs/") and not "/results/" in parsed.path:
      parts = parsed.path.strip("/").split("/")
      job_id = parts[2] if len(parts) >= 3 else ""
      action = parts[3] if len(parts) == 4 else ""
      if action in {"repair-metrics", "redownload-result"}:
        job = JOBS.get(job_id)
        if not job:
          json_response(self, {"ok": False, "error": f"Unknown job: {job_id}"}, status=404)
          return
        try:
          remote_config = result_download_remote_config(job, payload)
          if action == "repair-metrics":
            result = start_job_metrics_repair(job, remote_config, force=bool(payload.get("force")))
          else:
            result = start_completed_result_redownload(job, remote_config)
          json_response(self, result)
          return
        except RemoteExecutionError as exc:
          error_response(
            self,
            code=exc.code_hint or "WGSC-JOB-REPAIR-REMOTE-001",
            step="job",
            message=str(exc),
            status=400,
            details={
              "job_id": job_id,
              "stage": exc.stage,
              **(exc.details if isinstance(getattr(exc, "details", None), dict) else {}),
            },
          )
          return
        except Exception as exc:
          error_response(
            self,
            code="WGSC-JOB-REPAIR-001",
            step="job",
            message=str(exc),
            status=400,
            details={"job_id": job_id},
          )
          return

    if parsed.path.startswith("/api/jobs/") and "/results/" in parsed.path:
      parts = parsed.path.strip("/").split("/")
      job_id = parts[2] if len(parts) >= 3 else ""
      action = parts[4] if len(parts) == 5 and parts[3] == "results" else ""
      job = JOBS.get(job_id)
      if not job:
        json_response(self, {"ok": False, "error": f"Unknown job: {job_id}"}, status=404)
        return
      if action not in {"check", "download"}:
        json_response(self, {"ok": False, "error": f"Unsupported endpoint: {parsed.path}"}, status=404)
        return
      try:
        validate_completed_result_download_job(job)
        remote_config = result_download_remote_config(job, payload)
        if action == "check":
          result = check_completed_result_download(job, remote_config)
          json_response(self, {"ok": True, "check": result, "job": enrich_job(job)})
          return
        result = start_completed_result_download(job, remote_config)
        json_response(self, result)
        return
      except RemoteExecutionError as exc:
        error_response(
          self,
          code=exc.code_hint or "WGSC-JOB-RESULTS-REMOTE-001",
          step="job",
          message=str(exc),
          status=400,
          details={
            "job_id": job_id,
            "stage": exc.stage,
            **(exc.details if isinstance(getattr(exc, "details", None), dict) else {}),
          },
        )
        return
      except Exception as exc:
        error_response(
          self,
          code="WGSC-JOB-RESULTS-001",
          step="job",
          message=str(exc),
          status=400,
          details={"job_id": job_id},
        )
        return

    if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/delete"):
      parts = parsed.path.strip("/").split("/")
      job_id = parts[2] if len(parts) >= 3 else ""
      job = JOBS.get(job_id)
      if not job:
        json_response(self, {"ok": False, "error": f"Unknown job: {job_id}"}, status=404)
        return
      if not is_job_terminal(job):
        error_response(
          self,
          code="WGSC-JOB-DELETE-RUNNING",
          step="job",
          message="Only completed, failed, or canceled job records can be removed. Cancel running jobs first.",
          status=400,
          details={"job_id": job_id, "status": job.get("status", "")},
        )
        return
      try:
        removed = remove_job_record(job_id, require_terminal=True)
      except Exception as exc:
        error_response(
          self,
          code="WGSC-JOB-DELETE-001",
          step="job",
          message=str(exc),
          status=400,
          details={"job_id": job_id},
        )
        return
      json_response(self, {
        "ok": True,
        "code": "WGSC-JOB-RECORD-REMOVED",
        "removed": removed,
        "remaining": len(JOBS),
      })
      return

    if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/cancel"):
      parts = parsed.path.strip("/").split("/")
      job_id = parts[2] if len(parts) >= 3 else ""
      job = JOBS.get(job_id)
      if not job:
        json_response(self, {"ok": False, "error": f"Unknown job: {job_id}"}, status=404)
        return

      status = str(job.get("status", "")).strip().lower()
      if status in {"completed", "failed", "canceled"}:
        json_response(self, {"ok": True, "job": enrich_job(job), "message": f"Job is already {status}."})
        return

      job["cancel_requested"] = True
      append_job_log_line(job, "stderr", "[Cancel] User requested job cancellation.\n")
      if job.get("remote_detached"):
        remote_config = payload.get("remote") if isinstance(payload.get("remote"), dict) else job.get("_remote_config")
        if not isinstance(remote_config, dict) or not str(remote_config.get("password", "")).strip():
          job["monitor_state"] = "needs_remote_config"
          persist_job_state(job)
          error_response(
            self,
            code="WGSC-JOB-CANCEL-REMOTE-CONFIG",
            step="job",
            message="Remote credentials are required to cancel this detached remote job.",
            status=400,
            details={"job_id": job_id},
          )
          return
        try:
          cancel_remote_detached_job(
            remote_config=remote_config,
            remote_job_dir=str(job.get("remote_job_dir", "")),
            remote_pid=str(job.get("remote_pid", "")),
            remote_tmux_session=str(job.get("remote_tmux_session", "")),
          )
        except Exception as exc:
          job["cancel_requested"] = False
          append_job_log_line(job, "stderr", f"[Cancel] Failed to cancel detached remote job: {exc}\n")
          persist_job_state(job)
          error_response(
            self,
            code=getattr(exc, "code_hint", "") or "WGSC-JOB-CANCEL-REMOTE-001",
            step="job",
            message=str(exc),
            status=500,
            details={"job_id": job_id},
          )
          return
        job["status"] = "canceled"
        job["remote_stage"] = "canceled"
        job["monitor_state"] = "completed"
        job["return_code"] = 130
        job["finished_at"] = time.time()
        persist_job_state(job)
        json_response(self, {
          "ok": True,
          "code": "WGSC-JOB-CANCELED",
          "job": enrich_job(job),
        })
        return

      job["status"] = "canceled"
      job["remote_stage"] = "canceled"
      process = job.get("_process")
      if process is not None:
        try:
          if process.poll() is None:
            process.terminate()
        except Exception as exc:
          append_job_log_line(job, "stderr", f"[Cancel] Failed to terminate local process: {exc}\n")
      json_response(self, {
        "ok": True,
        "code": "WGSC-JOB-CANCELED",
        "job": enrich_job(job),
      })
      persist_job_state(job)
      return

    if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/reattach"):
      parts = parsed.path.strip("/").split("/")
      job_id = parts[2] if len(parts) >= 3 else ""
      job = JOBS.get(job_id)
      if not job:
        json_response(self, {"ok": False, "error": f"Unknown job: {job_id}"}, status=404)
        return
      if not job.get("remote_detached"):
        json_response(self, {"ok": True, "job": enrich_job(job), "message": "Job is not detached."})
        return
      remote_config = payload.get("remote") if isinstance(payload.get("remote"), dict) else {}
      if not str(remote_config.get("password", "")).strip():
        error_response(
          self,
          code="WGSC-JOB-REATTACH-REMOTE-CONFIG",
          step="job",
          message="Remote credentials are required to reattach detached job monitoring.",
          status=400,
          details={"job_id": job_id},
        )
        return
      try:
        validate_remote_config(remote_config)
      except RemoteExecutionError as exc:
        error_response(
          self,
          code=exc.code_hint or "WGSC-JOB-REATTACH-CONFIG",
          step="job",
          message=str(exc),
          status=400,
          details={"stage": exc.stage},
        )
        return
      job["monitor_state"] = "monitoring"
      job["remote_stage"] = "reattaching_monitor"
      job["_remote_config"] = dict(remote_config)
      persist_job_state(job)
      start_remote_monitor_thread(job, remote_config)
      json_response(self, {
        "ok": True,
        "code": "WGSC-JOB-REATTACHED",
        "job": enrich_job(job),
      })
      return

    if parsed.path == "/api/flow/reset":
      try:
        result = reset_flow(payload)
        json_response(self, result)
      except RemoteExecutionError as exc:
        error_response(
          self,
          code=exc.code_hint or "WGSC-FLOW-RESET-001",
          step="flow_reset",
          message=str(exc),
          status=400,
          details={"stage": exc.stage},
        )
      except Exception as exc:
        error_response(
          self,
          code="WGSC-FLOW-RESET-001",
          step="flow_reset",
          message=str(exc),
          status=500,
        )
      return

    if parsed.path == "/api/flow/data/delete":
      try:
        result = delete_flow_data_stage(payload)
        json_response(self, result)
      except Exception as exc:
        error_response(
          self,
          code="WGSC-FLOW-DATA-DELETE-001",
          step="flow_data",
          message=str(exc),
          status=400,
        )
      return

    if parsed.path == "/api/jobs/clear":
      try:
        result = clear_job_records(payload.get("statuses"))
        json_response(self, result)
      except Exception as exc:
        error_response(
          self,
          code="WGSC-JOBS-CLEAR-001",
          step="job",
          message=str(exc),
          status=400,
        )
      return

    if parsed.path == "/api/export-scene":
      try:
        result = build_scene_package(
          input_path=payload["input_path"],
          output_dir=payload["output_dir"],
          scene_id=payload["scene_id"],
          title=payload.get("title", payload["scene_id"]),
          representation=payload["representation"],
          algorithm_family=payload.get("algorithm_family", "unknown"),
          extra_metadata={
            "preprocess": payload.get("preprocess", {}),
            "editor": payload.get("editor", {}),
            "viewerBackend": payload.get("viewer_backend", "webgl2"),
          },
        )
        manifest_path = Path(result["manifest_path"])
        if ROOT_DIR in manifest_path.parents or manifest_path == ROOT_DIR:
          web_manifest_path = "/" + str(manifest_path.relative_to(ROOT_DIR)).replace("\\", "/")
        else:
          web_manifest_path = str(manifest_path)
        share_url = f"{payload.get('web_base_url', '')}/web/?manifest={web_manifest_path}" if payload.get("web_base_url") else web_manifest_path
        json_response(self, {
          "ok": True,
          "result": result,
          "manifest_url": web_manifest_path,
          "share_url": share_url,
        })
      except Exception as exc:
        json_response(self, {"error": str(exc)}, status=400)
      return

    if parsed.path == "/api/adapters/update":
      family = payload.get("family")
      if not family:
        json_response(self, {"error": "family is required"}, status=400)
        return
      adapter = get_adapter(family)
      if not adapter:
        json_response(self, {"error": f"Unknown algorithm family: {family}"}, status=404)
        return
      updated = update_adapter_override(
        family,
        {
          "repo_path": payload.get("repo_path"),
          "default_cwd": payload.get("default_cwd"),
          "repo_url": payload.get("repo_url"),
        },
      )
      json_response(self, {"ok": True, "adapter": updated, "validation": validate_adapter(updated)})
      return

    if parsed.path == "/api/adapters/validate":
      family = payload.get("family")
      adapter = get_adapter(family) if family else None
      if not adapter:
        error_response(
          self,
          code="WGSC-STEP2-ADAPTER-404",
          step="step2",
          message=f"Unknown algorithm family: {family}",
          status=404,
          manual_anchor="#5-%E4%B8%80%E9%94%AE%E6%B5%81%E7%A8%8B%E6%8E%A8%E8%8D%90",
          next_action="Select a valid algorithm family from the dropdown and retry.",
        )
        return
      json_response(self, {
        "ok": True,
        "code": "WGSC-STEP2-ADAPTER-OK",
        "step": "step2",
        "validation": validate_adapter(adapter),
      })
      return

    if parsed.path == "/api/environment-check":
      family = payload.get("family", "")
      report = environment_check(family or None)
      json_response(self, {
        "ok": True,
        "code": "WGSC-STEP1-RUNTIME-OK" if report.get("runtime_ready") else "WGSC-STEP1-RUNTIME-FAIL",
        "step": "step1",
        "report": report,
      })
      return

    if parsed.path == "/api/remote-check":
      try:
        remote_config_input = extract_remote_config_input(payload)
        family = str(payload.get("algorithm_family", "")).strip()
        check_colmap_required = effective_colmap_preflight_required(payload)
        result = remote_preflight_check(
          remote_config=remote_config_input,
          timeout_seconds=int(payload.get("timeout_seconds", 20) or 20),
          family=family,
          include_datasets=True,
          check_colmap_required=check_colmap_required,
        )
      except RemoteExecutionError as exc:
        error_response(
          self,
          code=exc.code_hint or "WGSC-STEP4-SSH-UNKNOWN-001",
          step="step4",
          message=str(exc),
          reason=f"stage={exc.stage or 'unknown'}",
          status=400,
          details={"stage": exc.stage or "unknown"},
          manual_anchor="#9-%E5%B8%B8%E8%A7%81%E9%94%99%E8%AF%AF%E7%A0%81%E4%B8%8E%E8%A7%A3%E5%86%B3%E6%96%B9%E6%A1%88",
          next_action="Fix the remote configuration or SSH connectivity and run 'Check SSH' again.",
        )
        return
      except Exception as exc:
        error_response(
          self,
          code="WGSC-STEP4-SSH-UNKNOWN-001",
          step="step4",
          message=f"Remote SSH preflight failed: {exc}",
          status=400,
          manual_anchor="#9-%E5%B8%B8%E8%A7%81%E9%94%99%E8%AF%AF%E7%A0%81%E4%B8%8E%E8%A7%A3%E5%86%B3%E6%96%B9%E6%A1%88",
        )
        return

      raw_datasets = result.get("datasets")
      datasets: list[Dict[str, Any]] = [item for item in raw_datasets if isinstance(item, dict)] if isinstance(raw_datasets, list) else []
      result["datasets"] = _annotate_datasets_with_training_coverage(datasets, family_scope=family)

      json_response(self, {
        "ok": True,
        "code": "WGSC-STEP4-SSH-OK",
        "step": "step4",
        "result": result,
      })
      return

    if parsed.path == "/api/run-remote-algorithm/preview":
      try:
        preview = build_remote_algorithm_preview(payload)
      except RemoteExecutionError as exc:
        error_response(
          self,
          code=exc.code_hint or "WGSC-STEP5-PREVIEW-001",
          step="preview",
          message=str(exc),
          reason=f"stage={exc.stage or 'preview'}",
          status=400,
          details={"stage": exc.stage or "preview"},
          next_action="Fix the remote configuration or command inputs, then preview again.",
        )
        return
      except Exception as exc:
        error_response(
          self,
          code="WGSC-STEP5-PREVIEW-001",
          step="preview",
          message=str(exc),
          status=400,
          next_action="Fix the algorithm, dataset, output, or remote configuration, then preview again.",
        )
        return

      json_response(self, {
        "ok": True,
        "code": "WGSC-STEP5-PREVIEW-OK",
        "step": "preview",
        "preview": preview,
      })
      return

    if parsed.path == "/api/run-remote-algorithm":
      family = payload.get("algorithm_family", "")
      if not family:
        error_response(
          self,
          code="WGSC-STEP5-PAYLOAD-001",
          step="step5",
          message="algorithm_family is required",
          status=400,
          manual_anchor="#5-%E4%B8%80%E9%94%AE%E6%B5%81%E7%A8%8B%E6%8E%A8%E8%8D%90",
        )
        return

      definition = get_adapter(family)
      if not definition:
        error_response(
          self,
          code="WGSC-STEP5-ADAPTER-404",
          step="step5",
          message=f"Unknown algorithm family: {family}",
          status=400,
          manual_anchor="#5-%E4%B8%80%E9%94%AE%E6%B5%81%E7%A8%8B%E6%8E%A8%E8%8D%90",
        )
        return

      if str(payload.get("command_override", "")).strip() and not security_flag("allow_command_override"):
        error_response(
          self,
          code="WGSC-STEP5-OVERRIDE-001",
          step="step5",
          message=(
            "command_override is disabled by the security configuration. "
            "Set \"allow_command_override\": true in web/config/security.json to enable it."
          ),
          status=403,
        )
        return

      operation = payload.get("operation", "train")
      try:
        command_template = operation_command_template(definition, operation)
      except ValueError as exc:
        error_response(
          self,
          code="WGSC-STEP5-OP-UNSUPPORTED-001",
          step="step5",
          message=str(exc),
          status=400,
          details={"family": family, "operation": operation},
          manual_anchor="#9-%E5%B8%B8%E8%A7%81%E9%94%99%E8%AF%AF%E7%A0%81%E4%B8%8E%E8%A7%A3%E5%86%B3%E6%96%B9%E6%A1%88",
          next_action="Switch operation/family or update adapter template and retry.",
        )
        return

      try:
        session_id = validated_session_id(payload.get("session_id", "default-session"))
      except ValueError as exc:
        error_response(
          self,
          code="WGSC-REQUEST-SESSION-001",
          step="request",
          message=str(exc),
          status=400,
        )
        return
      capture_id = validated_capture_id(payload.get("capture_id", ""))
      dataset_name = str(payload.get("dataset_name", "")).strip()
      auto_materialize = bool(payload.get("auto_materialize", True))
      auto_colmap = bool(payload.get("auto_colmap", True))
      use_existing_remote_dataset = bool(payload.get("use_existing_remote_dataset", False))
      auto_colmap = effective_auto_colmap(auto_colmap, use_existing_remote_dataset)
      remote_dataset_id = str(payload.get("remote_dataset_id", "")).strip()
      remote_dataset_path = str(payload.get("remote_dataset_path", "")).strip()
      workspace_input = str(payload.get("workspace", "")).strip()
      materialized_result = None
      workspace_path: Path | None = None

      if use_existing_remote_dataset:
        if not remote_dataset_id and not remote_dataset_path:
          error_response(
            self,
            code="WGSC-STEP5-DATASET-SELECT-001",
            step="step5",
            message="Choose an existing remote dataset or provide remote_dataset_path before running Step 5.",
            status=400,
            manual_anchor="#5-%E4%B8%80%E9%94%AE%E6%B5%81%E7%A8%8B%E6%8E%A8%E8%8D%90",
          )
          return
        auto_materialize = False
        auto_colmap = False
      else:
        if auto_materialize and not workspace_input:
          try:
            materialized_result = materialize_stream_session(
              session_id,
              payload.get("title", session_id),
              capture_id=capture_id,
              dataset_name=dataset_name or str(payload.get("title", "")).strip() or session_id,
            )
            workspace_input = materialized_result["dataset_root"]
            dataset_name = dataset_name or str(materialized_result.get("dataset_name", "")).strip()
            capture_id = capture_id or str(materialized_result.get("capture_id", "")).strip()
          except Exception as exc:
            error_response(
              self,
              code="WGSC-STEP3-MATERIALIZE-001",
              step="step3",
              message=f"Failed to materialize session {session_id}: {exc}",
              status=400,
              manual_anchor="#6-%E5%88%86%E6%AD%A5%E6%B5%81%E7%A8%8B%E8%B0%83%E8%AF%95%E5%85%9C%E5%BA%95",
            )
            return

        resolved_workspace = resolve_project_path(workspace_input)
        if not resolved_workspace:
          error_response(
            self,
            code="WGSC-STEP5-WORKSPACE-001",
            step="step5",
            message="workspace is required (or enable auto_materialize, or choose existing remote dataset)",
            status=400,
            manual_anchor="#5-%E4%B8%80%E9%94%AE%E6%B5%81%E7%A8%8B%E6%8E%A8%E8%8D%90",
          )
          return

        workspace_path = Path(resolved_workspace).resolve()
        if not workspace_path.exists() or not workspace_path.is_dir():
          error_response(
            self,
            code="WGSC-STEP5-WORKSPACE-002",
            step="step5",
            message=f"workspace directory not found: {workspace_path}",
            status=400,
            manual_anchor="#9-%E5%B8%B8%E8%A7%81%E9%94%99%E8%AF%AF%E7%A0%81%E4%B8%8E%E8%A7%A3%E5%86%B3%E6%96%B9%E6%A1%88",
          )
          return

      requested_job_id = str(payload.get("job_id", payload.get("preview_job_id", ""))).strip()
      job_id = requested_job_id if requested_job_id and requested_job_id not in JOBS else str(uuid.uuid4())
      resolved_output_dir = remote_job_local_output_dir(session_id, family, job_id)
      Path(resolved_output_dir).mkdir(parents=True, exist_ok=True)

      remote_config_input = extract_remote_config_input(payload)

      try:
        validated_remote = validate_remote_config(remote_config_input)
      except RemoteExecutionError as exc:
        error_response(
          self,
          code=exc.code_hint or "WGSC-STEP4-CONFIG-001",
          step="step4",
          message=str(exc),
          reason=f"stage={exc.stage or 'config'}",
          status=400,
          details={"stage": exc.stage or "config"},
          manual_anchor="#4.3-Remote-Training-Config",
        )
        return

      command_override = str(payload.get("command_override", "")).strip()
      require_preflight = bool(payload.get("require_remote_check", True))
      preflight_report = None
      if require_preflight:
        try:
          preflight_report = remote_preflight_check(
            remote_config=validated_remote,
            timeout_seconds=20,
            family=family,
            include_datasets=True,
            check_colmap_required=auto_colmap,
          )
        except RemoteExecutionError as exc:
          error_response(
            self,
            code=exc.code_hint or "WGSC-STEP4-SSH-UNKNOWN-001",
            step="step4",
            message=str(exc),
            reason=f"stage={exc.stage or 'unknown'}",
            status=400,
            details={"stage": exc.stage or "unknown"},
            manual_anchor="#9-%E5%B8%B8%E8%A7%81%E9%94%99%E8%AF%AF%E7%A0%81%E4%B8%8E%E8%A7%A3%E5%86%B3%E6%96%B9%E6%A1%88",
            next_action="Run Step 4 SSH check, fix the issue, then retry Step 5.",
          )
          return

      remote_checkpoint_path = str(payload.get("remote_checkpoint_path", payload.get("checkpoint_path", "")))
      input_path = str(payload.get("input_path", ""))
      source_path = str(payload.get("source", ""))
      workspace_for_template = (
        str(workspace_path)
        if workspace_path
        else (remote_dataset_path or (f"remote-dataset/{remote_dataset_id}" if remote_dataset_id else ""))
      )

      remote_format_args = {
        "input_path": input_path,
        "output_dir": resolved_output_dir,
        "workspace": workspace_for_template,
        "checkpoint_path": remote_checkpoint_path,
        "source": source_path or workspace_for_template,
        **_training_format_args(payload),
      }

      confirmed_ok, confirmed_message, confirmed_details = validate_path_confirmation_payload(
        payload,
        command_template=command_template,
        expected_checkpoint_path=remote_checkpoint_path,
        expected_output_dir=resolved_output_dir,
      )
      if not confirmed_ok:
        error_response(
          self,
          code="WGSC-STEP5-CONFIRM-001",
          step="step5",
          message=confirmed_message,
          status=400,
          details=confirmed_details,
          manual_anchor="#5-%E4%B8%80%E9%94%AE%E6%B5%81%E7%A8%8B%E6%8E%A8%E8%8D%90",
          next_action="Confirm checkpoint_path/output_dir in UI and retry Step 5.",
        )
        return

      missing_inputs = missing_template_fields(command_template, remote_format_args)
      if missing_inputs:
        error_response(
          self,
          code="WGSC-STEP5-TEMPLATE-001",
          step="step5",
          message=(
            f"{family} operation {operation} is missing required inputs: "
            + ", ".join(missing_inputs)
          ),
          status=400,
          details={"missing_fields": missing_inputs, "command_template": command_template},
          manual_anchor="#9-%E5%B8%B8%E8%A7%81%E9%94%99%E8%AF%AF%E7%A0%81%E4%B8%8E%E8%A7%A3%E5%86%B3%E6%96%B9%E6%A1%88",
        )
        return

      command_override = str(payload.get("command_override", "")).strip()
      job_created_at = time.time()
      remote_run_key = unique_remote_run_key(
        build_remote_run_key(dataset_name or remote_dataset_id or session_id, family, job_created_at),
        job_id,
      )
      post_train_config = post_train_config_for_adapter(
        definition,
        {},
        validated_remote["python"],
        format_commands=False,
      )
      job_tmux_session = build_remote_tmux_session_name(family=family, job_id=job_id, run_key=remote_run_key)
      job = {
        "id": job_id,
        "status": "queued",
        "algorithm_family": family,
        "representation": definition.get("representation", "sh"),
        "operation": "remote_train",
        "requested_operation": operation,
        "command": command_override or command_template,
        "command_override": command_override,
        "created_at": job_created_at,
        "workspace": workspace_for_template,
        "output_dir": resolved_output_dir,
        "session_id": session_id,
        "capture_id": capture_id,
        "dataset_name": dataset_name,
        "use_existing_remote_dataset": use_existing_remote_dataset,
        "remote_dataset_id": remote_dataset_id,
        "remote_dataset_path": remote_dataset_path,
        "repo_path": definition.get("repo_path", ""),
        "cwd": definition.get("default_cwd", ""),
        "remote": sanitize_remote_config(validated_remote),
        "remote_run_key": remote_run_key,
        "execution_backend": "tmux",
        "remote_tmux_session": job_tmux_session,
        "remote_tmux_attach_command": build_remote_tmux_attach_command(validated_remote, job_tmux_session),
        "tmux_available": None,
        "tmux_install": {
          "policy": "submit_time_mamba_or_sudo_n",
          "uses_password_sudo": False,
        },
        "remote_stage": "queued",
        "remote_detached": False,
        "safe_to_close_web": False,
        "monitor_state": "queued",
        "metrics": {},
        "step_result": {
          "step": "step5",
          "code": "WGSC-STEP5-QUEUED",
          "message": "Remote job queued.",
        },
      }
      ensure_job_logging(job)
      JOBS[job_id] = job
      persist_job_state(job)
      prune_job_history()

      start_remote_job_thread(
        job,
        remote_config=validated_remote,
        local_workspace_dir=str(workspace_path) if workspace_path else None,
        local_output_dir=resolved_output_dir,
        command_template=command_template,
        checkpoint_path=remote_checkpoint_path,
        input_path=input_path,
        source_path=source_path,
        session_id=session_id,
        auto_colmap=auto_colmap,
        dataset_name=dataset_name,
        remote_dataset_id=remote_dataset_id,
        remote_dataset_path=remote_dataset_path,
        use_existing_remote_dataset=use_existing_remote_dataset,
        training_args=_training_format_args(payload),
        command_override=command_override,
        remote_run_key=remote_run_key,
        post_train_config=post_train_config,
      )
      json_response(
        self,
        {
          "ok": True,
          "code": "WGSC-STEP5-QUEUED",
          "step": "step5",
          "job": enrich_job(job),
          "materialized": materialized_result,
          "remote_check": preflight_report,
          "remote_detached": bool(job.get("remote_detached")),
          "execution_backend": job.get("execution_backend", "tmux"),
          "remote_pid": job.get("remote_pid", ""),
          "remote_tmux_session": job.get("remote_tmux_session", ""),
          "remote_tmux_attach_command": job.get("remote_tmux_attach_command", ""),
          "tmux_available": job.get("tmux_available"),
          "tmux_install": job.get("tmux_install", {}),
          "remote_log_path": job.get("remote_log_path", ""),
          "remote_status_path": job.get("remote_status_path", ""),
          "safe_to_close_web": bool(job.get("safe_to_close_web")),
          "monitor_state": job.get("monitor_state", "queued"),
        },
        status=202,
      )
      return

    if parsed.path == "/api/run-algorithm":
      family = payload["algorithm_family"]
      definition = get_adapter(family)
      if not definition:
        json_response(self, {"error": f"Unknown algorithm family: {family}"}, status=400)
        return

      operation = payload.get("operation", "train")
      operation_config = definition.get("operations", {}).get(operation, {})
      command_template = operation_config.get("template")
      if not operation_config.get("enabled"):
        json_response(self, {"error": f"Operation {operation} is not enabled for {family}"}, status=400)
        return
      if not command_template:
        json_response(self, {"error": f"Algorithm {family} operation {operation} does not have a command template"}, status=400)
        return

      resolved_repo = resolve_workspace_path(payload.get("repo_path") or definition.get("repo_path", ""))
      resolved_cwd = resolve_workspace_path(payload.get("cwd") or definition.get("default_cwd") or resolved_repo)
      if "python main.py" in command_template and not resolved_cwd:
        json_response(
          self,
          {
            "error": (
              f"{family} requires repo_path/default_cwd for operation {operation}. "
              "Current command is python main.py, but no working directory was configured."
            )
          },
          status=400,
        )
        return

      format_args = {
        "input_path": payload.get("input_path", ""),
        "output_dir": payload.get("output_dir", ""),
        "workspace": payload.get("workspace", ""),
        "checkpoint_path": payload.get("checkpoint_path", ""),
        "repo_path": resolved_repo,
        "source": payload.get("source", payload.get("workspace", "")),
        **_training_format_args(payload),
      }

      confirmed_ok, confirmed_message, confirmed_details = validate_path_confirmation_payload(
        payload,
        command_template=command_template,
        expected_checkpoint_path=str(format_args.get("checkpoint_path", "")),
        expected_output_dir=str(format_args.get("output_dir", "")),
      )
      if not confirmed_ok:
        error_response(
          self,
          code="WGSC-STEP5-CONFIRM-001",
          step="step5",
          message=confirmed_message,
          status=400,
          details=confirmed_details,
          manual_anchor="#5-%E4%B8%80%E9%94%AE%E6%B5%81%E7%A8%8B%E6%8E%A8%E8%8D%90",
          next_action="Confirm checkpoint_path/output_dir in UI and retry.",
        )
        return

      missing_inputs = missing_template_fields(command_template, format_args)
      if missing_inputs:
        json_response(
          self,
          {
            "error": (
              f"{family} operation {operation} is missing required inputs: "
              + ", ".join(missing_inputs)
            ),
            "missing_fields": missing_inputs,
            "command_template": command_template,
          },
          status=400,
        )
        return

      requirement_spec = adapter_requirement_spec(definition)
      missing_modules = find_missing_python_modules(requirement_spec.get("python_modules", []))
      if missing_modules:
        cuda_toolkit = detect_cuda_toolkit()
        dependency_hint = ""
        if any(module in missing_modules for module in ("diff_gaussian_rasterization", "simple_knn")) and not cuda_toolkit.get("toolkit_ready"):
          dependency_hint = (
            "Missing CUDA Toolkit (nvcc/CUDA_HOME). "
            "Install CUDA Toolkit, set CUDA_HOME, and then reinstall extension modules."
          )
        json_response(
          self,
          {
            "error": f"Missing Python dependencies for {family}: {', '.join(missing_modules)}",
            "missing_modules": missing_modules,
            "python_executable": sys.executable,
            "install_commands": requirement_spec.get("install_commands", {}),
            "cuda_toolkit": cuda_toolkit,
            "hint": dependency_hint,
          },
          status=400,
        )
        return

      command = strip_empty_repo_path_flag(command_template.format(**format_args), resolved_repo)
      try:
        command = sanitize_formatted_command(command_template, command, format_args)
      except Exception:
        pass
      command = apply_training_eval_arg(
        family,
        command,
        user_disabled_eval=bool(payload.get("disable_eval") or payload.get("no_eval")),
      )
      job_id = str(uuid.uuid4())
      job = {
        "id": job_id,
        "status": "queued",
        "algorithm_family": family,
        "representation": definition["representation"],
        "operation": operation,
        "command": command,
        "cwd": resolved_cwd,
        "created_at": time.time(),
        "repo_path": resolved_repo,
        "output_dir": payload.get("output_dir", ""),
        "preprocess": payload.get("preprocess", {}),
        "editor": payload.get("editor", {}),
        "viewer_backend": payload.get("viewer_backend", "webgl2"),
        "metrics": {},
      }
      ensure_job_logging(job)
      JOBS[job_id] = job
      prune_job_history()
      start_job_thread(job, command=command, cwd=job["cwd"])
      json_response(self, {"ok": True, "job": enrich_job(job)}, status=202)
      return

    if parsed.path == "/api/stream-frame":
      try:
        session_id = validated_session_id(payload.get("session_id", "default-session"))
        capture_id = validated_capture_id(payload.get("capture_id", ""))
      except ValueError as exc:
        error_response(
          self,
          code="WGSC-REQUEST-SESSION-001",
          step="request",
          message=str(exc),
          status=400,
        )
        return
      family = payload.get("algorithm_family", "")
      if not payload.get("image_data"):
        error_response(
          self,
          code="WGSC-STEP3-PAYLOAD-001",
          step="step3",
          message="image_data is required",
          status=400,
          manual_anchor="#6-%E5%88%86%E6%AD%A5%E6%B5%81%E7%A8%8B%E8%B0%83%E8%AF%95%E5%85%9C%E5%BA%95",
        )
        return
      try:
        frame_info = save_stream_frame(
          session_id=session_id,
          image_data=payload["image_data"],
          filename=payload.get("filename", f"{int(time.time() * 1000)}.png"),
          capture_id=capture_id,
        )
      except Exception as exc:
        error_response(
          self,
          code="WGSC-STEP3-FRAME-SAVE-001",
          step="step3",
          message=f"Failed to save frame: {exc}",
          status=400,
          manual_anchor="#9-%E5%B8%B8%E8%A7%81%E9%94%99%E8%AF%AF%E7%A0%81%E4%B8%8E%E8%A7%A3%E5%86%B3%E6%96%B9%E6%A1%88",
        )
        return

      job = None
      adapter = get_adapter(family) if family else None
      if adapter:
        resolved_repo = resolve_workspace_path(payload.get("repo_path") or adapter.get("repo_path", ""))
        resolved_cwd = resolve_workspace_path(payload.get("cwd") or adapter.get("default_cwd") or resolved_repo)
        operation = "process_frame"
        operation_config = adapter.get("operations", {}).get(operation, {})
        command_template = operation_config.get("template")
        if operation_config.get("enabled") and command_template:
          job_id = str(uuid.uuid4())
          command = command_template.format(
            input_path=frame_info["input_path"],
            output_dir=str((STREAM_DIR / session_id / "output").resolve()),
            workspace=payload.get("workspace", ""),
            checkpoint_path=payload.get("checkpoint_path", ""),
            repo_path=resolved_repo,
            source=frame_info["input_path"],
          )
          command = strip_empty_repo_path_flag(command, resolved_repo)
          try:
            command = sanitize_formatted_command(command_template, command, {
              "input_path": frame_info["input_path"],
              "output_dir": str((STREAM_DIR / session_id / "output").resolve()),
              "workspace": payload.get("workspace", ""),
              "checkpoint_path": payload.get("checkpoint_path", ""),
              "repo_path": resolved_repo,
              "source": frame_info["input_path"],
            })
          except Exception:
            pass
          job = {
            "id": job_id,
            "status": "queued",
            "algorithm_family": family,
            "representation": adapter["representation"],
            "operation": operation,
            "command": command,
            "cwd": resolved_cwd,
            "created_at": time.time(),
            "repo_path": resolved_repo,
            "metrics": {},
            "stream_session_id": session_id,
            "output_dir": str((STREAM_DIR / session_id / "output").resolve()),
            "result_url": frame_info["output_url"],
          }
          ensure_job_logging(job)
          JOBS[job_id] = job
          prune_job_history()
          start_job_thread(job, command=command, cwd=job["cwd"])

      stream_artifacts = read_runtime_artifacts(
        str((STREAM_DIR / session_id / "output").resolve()),
        adapter.get("representation") if adapter else None,
        family,
      )

      json_response(self, {
        "ok": True,
        "session_id": session_id,
        "capture_id": frame_info.get("capture_id", capture_id),
        "input_url": frame_info["input_url"],
        "output_url": stream_artifacts.get("result_url", frame_info["output_url"]),
        "result_url": stream_artifacts.get("result_url"),
        "result_urls": stream_artifacts.get("result_urls", []),
        "render_images": stream_artifacts.get("render_images", []),
        "render_image_count": stream_artifacts.get("render_image_count", 0),
        "viewer_url": stream_artifacts.get("viewer_url"),
        "job": enrich_job(job) if job else None,
      }, status=202 if job else 200)
      return

    if parsed.path == "/api/materialize-session":
      try:
        session_id = validated_session_id(payload.get("session_id", "default-session"))
        capture_id = validated_capture_id(payload.get("capture_id", ""))
      except ValueError as exc:
        error_response(
          self,
          code="WGSC-REQUEST-SESSION-001",
          step="request",
          message=str(exc),
          status=400,
        )
        return
      try:
        result = materialize_stream_session(
          session_id,
          payload.get("title"),
          capture_id=str(payload.get("capture_id", "")).strip(),
          dataset_name=str(payload.get("dataset_name", "")).strip(),
        )
        json_response(self, {"ok": True, "code": "WGSC-STEP3-MATERIALIZE-OK", "step": "step3", "result": result})
      except Exception as exc:
        error_response(
          self,
          code="WGSC-STEP3-MATERIALIZE-001",
          step="step3",
          message=str(exc),
          status=400,
          manual_anchor="#6-%E5%88%86%E6%AD%A5%E6%B5%81%E7%A8%8B%E8%B0%83%E8%AF%95%E5%85%9C%E5%BA%95",
        )
      return

    if parsed.path == "/api/prepare-colmap-workspace":
      try:
        session_id = validated_session_id(payload.get("session_id", "default-session"))
      except ValueError as exc:
        error_response(
          self,
          code="WGSC-REQUEST-SESSION-001",
          step="request",
          message=str(exc),
          status=400,
        )
        return
      family = payload.get("algorithm_family", "vanilla-3dgs")
      try:
        result = prepare_colmap_workspace(
          session_id,
          family,
          payload.get("repo_path"),
          capture_id=validated_capture_id(payload.get("capture_id", "")),
          dataset_name=str(payload.get("dataset_name", "")).strip(),
        )
        json_response(self, {"ok": True, "code": "WGSC-STEP3-WORKSPACE-OK", "step": "step3", "result": result})
      except Exception as exc:
        error_response(
          self,
          code="WGSC-STEP3-WORKSPACE-001",
          step="step3",
          message=str(exc),
          status=400,
          manual_anchor="#6-%E5%88%86%E6%AD%A5%E6%B5%81%E7%A8%8B%E8%B0%83%E8%AF%95%E5%85%9C%E5%BA%95",
        )
      return

    if parsed.path == "/api/run-capture-pipeline":
      try:
        session_id = validated_session_id(payload.get("session_id", "default-session"))
      except ValueError as exc:
        error_response(
          self,
          code="WGSC-REQUEST-SESSION-001",
          step="request",
          message=str(exc),
          status=400,
        )
        return
      family = payload.get("algorithm_family", "vanilla-3dgs")
      output_dir = payload.get("output_dir", "")
      repo_path = resolve_workspace_path(payload.get("repo_path", ""))
      checkpoint_path = payload.get("checkpoint_path", "")
      execute = bool(payload.get("execute_immediately", True))
      try:
        pipeline = build_capture_pipeline(
          session_id,
          family,
          output_dir=output_dir,
          repo_path=repo_path,
          checkpoint_path=checkpoint_path,
          training_args=_training_format_args(payload),
        )
        job = None
        if execute:
          adapter = get_adapter(family)
          job_id = str(uuid.uuid4())
          pipeline_command = (
            f'cmd /c "{pipeline["script_path_cmd"]}"'
            if os.name == "nt"
            else f"bash {pipeline['script_path']}"
          )
          job = {
            "id": job_id,
            "status": "queued",
            "algorithm_family": family,
            "representation": adapter.get("representation", "sh") if adapter else "sh",
            "operation": "capture_pipeline",
            "command": pipeline_command,
            "cwd": pipeline["repo_path"] or resolve_workspace_path(adapter.get("default_cwd") if adapter else "") or pipeline["workspace"]["workspace_root"],
            "created_at": time.time(),
            "repo_path": pipeline["repo_path"],
            "output_dir": pipeline["output_dir"],
            "workspace": pipeline["workspace"]["workspace_root"],
            "metrics": {},
            "pipeline": pipeline,
          }
          ensure_job_logging(job)
          JOBS[job_id] = job
          prune_job_history()
          start_job_thread(job, command=job["command"], cwd=job["cwd"])
        json_response(self, {"ok": True, "pipeline": pipeline, "job": enrich_job(job) if job else None}, status=202 if job else 200)
      except Exception as exc:
        error_response(
          self,
          code="WGSC-STEP5-PIPELINE-001",
          step="step5",
          message=str(exc),
          status=400,
          manual_anchor="#6-%E5%88%86%E6%AD%A5%E6%B5%81%E7%A8%8B%E8%B0%83%E8%AF%95%E5%85%9C%E5%BA%95",
        )
      return

    if parsed.path == "/api/load-result":
      result_path = str(payload.get("path", "") or payload.get("result_path", "") or "").strip()
      family = str(payload.get("family", "")).strip() or None
      representation = str(payload.get("representation", "")).strip() or None

      if not result_path:
        json_response(self, {
          "ok": False,
          "type": "",
          "error": "Missing path parameter",
          "reason": "Missing path parameter",
        }, status=400)
        return

      result = load_result_path(result_path, family=family, representation=representation)
      status = 200 if result.get("ok") else 400
      json_response(self, result, status=status)
      return

    if parsed.path == "/api/export-web":
      # Module A of the WebGS serving pipeline: convert a local PLY into
      # progressive Web assets (manifest + base/refinement/region chunks).
      ply_path = str(payload.get("ply_path", "")).strip()
      if not ply_path:
        json_response(self, {"ok": False, "error": "Missing ply_path parameter"}, status=400)
        return
      try:
        from web.tools.export_web_assets import export_web_assets
        resolved = Path(ply_path).expanduser().resolve()
        if not result_path_is_allowed(resolved):
          raise PermissionError(
            "Path is outside the allowed result roots. Add directories to "
            "web/config/security.json (extra_allowed_roots) if needed."
          )
        if not resolved.is_file() or resolved.suffix.lower() != ".ply":
          raise ValueError(f"Not a PLY file: {resolved}")
        digest = uuid.uuid5(uuid.NAMESPACE_URL, str(resolved)).hex[:10]
        out_dir = WEB_DIR / "generated" / "web_exports" / f"{resolved.stem}-{digest}"
        manifest = export_web_assets(resolved, out_dir)
        json_response(self, {
          "ok": True,
          "manifest_url": "/" + str((out_dir / "manifest.json").relative_to(ROOT_DIR)).replace("\\", "/"),
          "viewer_url": f"/web/viewers/progressive.html?url=/{str((out_dir / 'manifest.json').relative_to(ROOT_DIR)).replace(chr(92), '/')}",
          "vertexCount": manifest["vertexCount"],
          "baseRows": manifest["baseRows"],
          "representation": manifest["representation"],
          "chunkCount": len(manifest["chunks"]),
          "output_dir": str(out_dir),
        })
      except Exception as exc:
        error_response(
          self,
          code="WGSC-EXPORT-WEB-001",
          step="export_web",
          message=str(exc),
          status=400,
        )
      return

    if parsed.path == "/api/load-ply":
      ply_path = str(payload.get("ply_path", "")).strip()
      representation = str(payload.get("representation", "")).strip() or None

      if not ply_path:
        json_response(self, {
          "ok": False,
          "error": "Missing ply_path parameter",
        }, status=400)
        return

      result = load_ply_file(ply_path, representation)
      status = 200 if result.get("ok") else 400
      json_response(self, result, status=status)
      return

    json_response(self, {"error": f"Unsupported endpoint: {parsed.path}"}, status=404)


def main() -> None:
  parser = argparse.ArgumentParser(description="Run the Web_Scan web server.")
  parser.add_argument("--host", default="127.0.0.1")
  parser.add_argument("--port", type=int, default=8080)
  parser.add_argument(
    "--token",
    default="",
    help="Set the API access token (default: generated once and persisted in web/config/server.token).",
  )
  parser.add_argument("--no-auth", action="store_true", help="Disable API token checks (not recommended).")
  parser.add_argument(
    "--allowed-host",
    default="",
    help="Comma-separated extra Host values to accept, e.g. --allowed-host 192.168.1.10,myserver.local (for LAN access).",
  )
  args = parser.parse_args()

  token = ensure_server_token(args.token)
  SECURITY_STATE["auth_enabled"] = not args.no_auth
  extra_hosts = [item for item in load_security_config().get("allowed_hosts", []) if isinstance(item, str)]
  extra_hosts += [item for item in str(args.allowed_host or "").split(",")]
  SECURITY_STATE["allowed_host_values"] = allowed_host_values(args.host, args.port, extra_hosts)

  load_persisted_jobs()
  server = ThreadingHTTPServer((args.host, args.port), ApiHandler)
  logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
  )
  print(f"Serving web app at http://{args.host}:{args.port}/web/")
  if SECURITY_STATE["auth_enabled"]:
    print(f"API access token: {token}")
    print("The token is stored in web/config/server.token and injected into the served page automatically.")
    print(f"curl example: curl -H 'X-Auth-Token: {token}' http://{args.host}:{args.port}/api/health")
  else:
    print("WARNING: API token auth is DISABLED (--no-auth). Do not expose this server beyond loopback.")
  server.serve_forever()


if __name__ == "__main__":
  main()
