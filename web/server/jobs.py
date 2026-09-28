"""Job state, persistence, orchestration threads, and download lifecycle.

Extracted from api_server.py (v0.3 module split): the JOBS registry and its
locks, job.json persistence, log/metrics readers, enrichment, the remote
monitor and download threads, and the flow reset/cancel machinery.
"""
from __future__ import annotations

import csv
from collections import deque
import json
from datetime import datetime, timezone
import io
import shutil
from shutil import which
import uuid
import logging
import os
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict

from web.server.adapter_registry import get_adapter, list_adapters
from web.server.remote_executor import (
  RemoteExecutionError,
  build_remote_paths,
  build_remote_run_key,
  build_remote_tmux_attach_command,
  build_remote_tmux_session_name,
  cancel_remote_detached_job,
  check_remote_output_download,
  download_remote_output_directory,
  poll_remote_detached_job,
  repair_remote_job_metrics,
  sanitize_remote_config,
  start_remote_algorithm_detached,
  validate_remote_config,
)
from web.server.results import (
  ARTIFACT_RESULT_FIELDS,
  _normalize_slug,
  LOCAL_RESULT_PROJECTS,
  RESULT_SCAN_EXCLUDED_DIRS,
  load_result_path,
  remote_download_info,
  remote_job_local_output_dir,
  resolve_workspace_path,
  extract_job_metrics,
  infer_analysis_metrics_with_sources,
  read_result_json_metrics,
  read_runtime_artifacts,
  result_candidate_dirs,
)
from web.server.web_security import (
  DATASET_DIR,
  ROOT_DIR,
  WEB_DIR,
  path_is_inside,
  JOB_LOG_DIR,
  LOGGER,
  STREAM_DIR,
  WORKSPACE_DIR,
)

JOBS: Dict[str, Dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()
JOB_LOG_LOCKS: Dict[str, threading.Lock] = {}
RESULT_DOWNLOAD_LOCK = threading.Lock()
REMOTE_MONITOR_START_LOCK = threading.Lock()
MAX_JOB_HISTORY = 300
MAX_PROCESS_FRAME_COMPLETED_HISTORY = 80
MAX_METRICS_HISTORY = 5000
JOB_STATE_FILE = "job.json"
PARTIAL_SUCCESS_STATUSES = {
  "partial_success",
  "training_success_render_failed",
  "training_success_metrics_failed",
  "training_success_postprocess_failed",
}
TERMINAL_STATUSES = {"completed", "failed", "canceled", *PARTIAL_SUCCESS_STATUSES}
DOWNLOADABLE_RESULT_STATUSES = {"completed", *PARTIAL_SUCCESS_STATUSES}
REMOTE_MONITOR_MAX_CONSECUTIVE_FAILURES = 20
REMOTE_MONITOR_MAX_FAILURE_SECONDS = 300
REMOTE_MONITOR_RETRY_MIN_SECONDS = 3
REMOTE_MONITOR_RETRY_MAX_SECONDS = 10
REMOTE_RESULT_IDENTITY_FIELDS = ("host", "port", "username", "output_root")


METRICS_CSV_COLUMNS = ["timestamp", "channel", "iter", "loss", "psnr", "ssim", "lpips", "size_mb"]


ENRICH_ARTIFACTS_CACHE: Dict[tuple, tuple] = {}
ENRICH_ARTIFACTS_TTL_SECONDS = 2.0
ENRICH_CACHE_LOCK = threading.Lock()


ANALYSIS_EXPORT_COLUMNS = [
  "job_id",
  "algorithm_family",
  "status",
  "operation",
  "created_at",
  "dataset",
  "output_dir",
  "result_path",
  "psnr_db",
  "ssim",
  "lpips",
  "size_mb",
  "psnr_source",
  "ssim_source",
  "lpips_source",
  "size_source",
  "metrics_csv_url",
  "logs_download_url",
]


def utc_timestamp_text(now: float | None = None) -> str:
  moment = datetime.fromtimestamp(now or time.time(), tz=timezone.utc)
  return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def ensure_metrics_csv_header(metrics_csv: Path) -> None:
  if not metrics_csv.exists() or metrics_csv.stat().st_size == 0:
    with metrics_csv.open("w", newline="", encoding="utf-8") as handle:
      writer = csv.DictWriter(handle, fieldnames=METRICS_CSV_COLUMNS)
      writer.writeheader()
    return

  try:
    with metrics_csv.open("r", newline="", encoding="utf-8") as handle:
      reader = csv.DictReader(handle)
      current = list(reader.fieldnames or [])
      rows = list(reader)
  except Exception:
    return

  if current == METRICS_CSV_COLUMNS:
    return
  if all(column in current for column in METRICS_CSV_COLUMNS):
    return

  with metrics_csv.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=METRICS_CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
      row.pop(None, None)
      writer.writerow(row)


def ensure_job_logging(job: Dict[str, Any]) -> None:
  job_id = str(job.get("id", "")).strip()
  if not job_id:
    return

  JOB_LOG_DIR.mkdir(parents=True, exist_ok=True)
  job_dir = (JOB_LOG_DIR / job_id).resolve()
  job_dir.mkdir(parents=True, exist_ok=True)

  log_file = job_dir / "runtime.log"
  metrics_csv = job_dir / "metrics.csv"

  if not log_file.exists():
    log_file.write_text("", encoding="utf-8")

  ensure_metrics_csv_header(metrics_csv)

  history = job.get("metrics_history")
  if not isinstance(history, list):
    history = []
  job["metrics_history"] = history[-MAX_METRICS_HISTORY:]

  job["log_dir"] = str(job_dir)
  job["log_file"] = str(log_file)
  job["metrics_csv_file"] = str(metrics_csv)
  job["logs_api_url"] = f"/api/jobs/{job_id}/logs"
  job["logs_download_url"] = f"/api/jobs/{job_id}/logs/download"
  job["metrics_csv_url"] = f"/api/jobs/{job_id}/metrics.csv"

  if job_id not in JOB_LOG_LOCKS:
    JOB_LOG_LOCKS[job_id] = threading.Lock()


def sanitize_job_for_persistence(job: Dict[str, Any]) -> Dict[str, Any]:
  persisted: Dict[str, Any] = {}
  for key, value in job.items():
    if key.startswith("_"):
      continue
    if key in {"log_dir", "log_file", "metrics_csv_file"}:
      continue
    if key == "remote":
      if isinstance(value, dict):
        remote_value = dict(value)
        if "password" in remote_value:
          remote_value = sanitize_remote_config(remote_value)
        else:
          remote_value.pop("password", None)
        persisted[key] = remote_value
      else:
        persisted[key] = value
      continue
    try:
      json.dumps(value)
    except TypeError:
      continue
    persisted[key] = value
  if isinstance(persisted.get("remote"), dict):
    persisted["remote"].pop("password", None)
  return persisted


def persist_job_state(job: Dict[str, Any]) -> None:
  job_id = str(job.get("id", "")).strip()
  if not job_id:
    return
  try:
    ensure_job_logging(job)
    job_dir = Path(str(job.get("log_dir", "")))
    if not job_dir:
      return
    payload = sanitize_job_for_persistence(job)
    target = job_dir / JOB_STATE_FILE
    # Unique tmp name: two threads persisting the same job must not clobber each other's temp file.
    tmp = job_dir / f".{JOB_STATE_FILE}.{os.getpid()}.{threading.get_ident()}.tmp"
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)
  except Exception:
    # Persistence must never take down a running job thread; surface it in server logs.
    LOGGER.exception("Failed to persist job state for %s", job_id)


def load_persisted_jobs() -> None:
  if not JOB_LOG_DIR.exists():
    return
  for state_path in sorted(JOB_LOG_DIR.glob(f"*/{JOB_STATE_FILE}")):
    try:
      payload = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
      LOGGER.warning("Skipping unreadable job state file: %s", state_path)
      continue
    if not isinstance(payload, dict):
      continue
    job_id = str(payload.get("id", "")).strip()
    if not job_id or job_id in JOBS:
      continue
    status = str(payload.get("status", "")).strip().lower()
    if payload.get("remote_detached") and status not in TERMINAL_STATUSES:
      payload["status"] = "detached"
      payload["remote_stage"] = "needs_monitor"
      payload["monitor_state"] = "needs_remote_config"
      payload["safe_to_close_web"] = True
    ensure_job_logging(payload)
    JOBS[job_id] = payload


def append_job_log_line(job: Dict[str, Any], channel: str, line: str) -> None:
  if not line:
    return

  ensure_job_logging(job)
  job_id = str(job.get("id", "")).strip()
  if not job_id:
    return

  target_key = "stdout" if channel == "stdout" else "stderr"
  normalized = line if line.endswith("\n") else f"{line}\n"
  job[target_key] = (job.get(target_key, "") + normalized)[-12000:]

  timestamp = utc_timestamp_text()
  lock = JOB_LOG_LOCKS.setdefault(job_id, threading.Lock())
  log_file_value = str(job.get("log_file", "")).strip()
  if log_file_value:
    log_path = Path(log_file_value)
    with lock:
      with log_path.open("a", encoding="utf-8", errors="replace") as handle:
        handle.write(f"[{timestamp}] [{channel.upper()}] {normalized}")

  metrics_update = extract_job_metrics(normalized)
  if not metrics_update:
    return

  job["metrics"] = {
    **job.get("metrics", {}),
    **metrics_update,
  }

  entry = {
    "timestamp": timestamp,
    "channel": channel.upper(),
    "iter": metrics_update.get("iter", ""),
    "loss": metrics_update.get("loss", ""),
    "psnr": "",
    "ssim": "",
    "lpips": metrics_update.get("lpips", ""),
    "size_mb": metrics_update.get("size_mb", ""),
  }
  history = job.setdefault("metrics_history", [])
  history.append(entry)
  if len(history) > MAX_METRICS_HISTORY:
    del history[: len(history) - MAX_METRICS_HISTORY]

  csv_file_value = str(job.get("metrics_csv_file", "")).strip()
  if csv_file_value:
    csv_path = Path(csv_file_value)
    with lock:
      with csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=METRICS_CSV_COLUMNS)
        writer.writerow(entry)


def read_job_log_tail_lines(job: Dict[str, Any], limit: int) -> list[str]:
  ensure_job_logging(job)
  log_file_value = str(job.get("log_file", "")).strip()
  if not log_file_value:
    return []
  log_file = Path(log_file_value)
  if not log_file.exists():
    return []

  safe_limit = max(1, min(limit, 3000))
  with log_file.open("r", encoding="utf-8", errors="replace") as handle:
    return list(deque(handle, maxlen=safe_limit))


def read_job_log_since_cursor(job: Dict[str, Any], cursor: int, max_bytes: int = 200_000) -> Dict[str, Any]:
  ensure_job_logging(job)
  log_file_value = str(job.get("log_file", "")).strip()
  if not log_file_value:
    return {"cursor": 0, "from_cursor": 0, "lines": [], "truncated": False}

  log_file = Path(log_file_value)
  if not log_file.exists():
    return {"cursor": 0, "from_cursor": 0, "lines": [], "truncated": False}

  file_size = log_file.stat().st_size
  safe_cursor = max(0, int(cursor or 0))
  if safe_cursor > file_size:
    safe_cursor = 0

  read_from = safe_cursor
  truncated = False
  if file_size - read_from > max_bytes:
    read_from = max(0, file_size - max_bytes)
    truncated = True

  with log_file.open("rb") as handle:
    handle.seek(read_from)
    raw = handle.read(max_bytes)

  text = raw.decode("utf-8", errors="replace")
  return {
    "cursor": file_size,
    "from_cursor": read_from,
    "lines": text.splitlines(keepends=True),
    "truncated": truncated,
  }


def build_metrics_page(job: Dict[str, Any], page: int, page_size: int) -> Dict[str, Any]:
  history = list(job.get("metrics_history", []))
  history.reverse()

  safe_page_size = max(1, min(page_size, 200))
  total = len(history)
  total_pages = max(1, (total + safe_page_size - 1) // safe_page_size)
  safe_page = max(1, min(page, total_pages))
  start = (safe_page - 1) * safe_page_size
  end = start + safe_page_size

  return {
    "page": safe_page,
    "page_size": safe_page_size,
    "total": total,
    "total_pages": total_pages,
    "rows": history[start:end],
  }


def unique_remote_run_key(base_run_key: str, job_id: str) -> str:
  run_key = str(base_run_key or "").strip()
  if not run_key:
    return ""
  # Check-then-act must be atomic: two concurrent submissions could else pick the same key.
  with JOBS_LOCK:
    if not any(str(job.get("remote_run_key", "")).strip() == run_key for job in JOBS.values()):
      return run_key
    suffix = str(job_id or "").strip().replace("_", "-")[:8].strip("-") or uuid.uuid4().hex[:8]
    candidate = f"{run_key}-{suffix}"
    if not any(str(job.get("remote_run_key", "")).strip() == candidate for job in JOBS.values()):
      return candidate
    return f"{run_key}-{uuid.uuid4().hex[:8]}"


def read_runtime_artifacts_cached(job: Dict[str, Any]) -> Dict[str, Any]:
  """read_runtime_artifacts with a short TTL.

  /api/jobs enriches every job on every request (each a full output-dir walk),
  and the training log reader rescanned artifacts per output line. A 2s TTL
  removes the duplicated scans while keeping polled metrics effectively fresh.
  """
  key = (
    str(job.get("output_dir", "")),
    str(job.get("representation", "")),
    str(job.get("algorithm_family", "")),
    str(job.get("id", "")),
  )
  now = time.monotonic()
  with ENRICH_CACHE_LOCK:
    cached = ENRICH_ARTIFACTS_CACHE.get(key)
    if cached and now - cached[0] < ENRICH_ARTIFACTS_TTL_SECONDS:
      return cached[1]
  artifacts = read_runtime_artifacts(
    job.get("output_dir"), job.get("representation"), job.get("algorithm_family"), job
  )
  with ENRICH_CACHE_LOCK:
    if len(ENRICH_ARTIFACTS_CACHE) > 512:
      ENRICH_ARTIFACTS_CACHE.clear()
    ENRICH_ARTIFACTS_CACHE[key] = (now, artifacts)
  return artifacts


def enrich_job(job: Dict[str, Any]) -> Dict[str, Any]:
  ensure_job_logging(job)
  if mark_stale_remote_download_if_needed(job):
    persist_job_state(job)
  enriched = dict(job)
  if isinstance(enriched.get("metrics"), dict):
    cleaned_metrics = dict(enriched["metrics"])
    for removed_key in ("fps", "render_fps", "render_fps_source"):
      cleaned_metrics.pop(removed_key, None)
    if isinstance(cleaned_metrics.get("analysis_metric_sources"), dict):
      cleaned_sources = dict(cleaned_metrics["analysis_metric_sources"])
      cleaned_sources.pop("render_fps", None)
      cleaned_metrics["analysis_metric_sources"] = cleaned_sources
    enriched["metrics"] = cleaned_metrics
  if isinstance(enriched.get("analysis_metric_sources"), dict):
    cleaned_top_sources = dict(enriched["analysis_metric_sources"])
    cleaned_top_sources.pop("render_fps", None)
    enriched["analysis_metric_sources"] = cleaned_top_sources
  if not is_remote_download_pending(enriched):
    artifacts = read_runtime_artifacts_cached(enriched)
    existing_metrics = dict(enriched.get("metrics", {})) if isinstance(enriched.get("metrics"), dict) else {}
    for strict_key in (
      "psnr",
      "psnr_db",
      "ssim",
      "lpips",
      "size_mb",
      "size_bytes",
      "psnr_source",
      "ssim_source",
      "lpips_source",
      "size_source",
    ):
      existing_metrics.pop(strict_key, None)
    if artifacts.get("metrics"):
      enriched["metrics"] = {
        **existing_metrics,
        **artifacts["metrics"],
      }
      if isinstance(artifacts["metrics"].get("analysis_metric_sources"), dict):
        enriched["analysis_metric_sources"] = artifacts["metrics"]["analysis_metric_sources"]
    for key in ARTIFACT_RESULT_FIELDS:
      if artifacts.get(key):
        enriched[key] = artifacts[key]
    for key in ("result_json_exists", "result_path"):
      if key in artifacts:
        enriched[key] = artifacts[key]
  else:
    for key in ARTIFACT_RESULT_FIELDS:
      enriched.pop(key, None)
    enriched["result_json_exists"] = False
  job_id = str(enriched.get("id", "")).strip()
  if job_id:
    enriched["logs_api_url"] = f"/api/jobs/{job_id}/logs"
    enriched["logs_download_url"] = f"/api/jobs/{job_id}/logs/download"
    enriched["metrics_csv_url"] = f"/api/jobs/{job_id}/metrics.csv"
  enriched.update(normalize_job_dataset_fields(enriched))
  enriched["updated_at"] = enriched.get("updated_at") or enriched.get("finished_at") or enriched.get("started_at") or enriched.get("created_at")
  metric_sources = enriched.get("analysis_metric_sources") if isinstance(enriched.get("analysis_metric_sources"), dict) else {}
  enriched["psnr_source"] = metric_sources.get("psnr") or metric_sources.get("psnr_db")
  enriched["ssim_source"] = metric_sources.get("ssim")
  enriched["lpips_source"] = metric_sources.get("lpips")
  enriched["size_source"] = metric_sources.get("size_mb")
  status = str(enriched.get("status", "")).strip().lower()
  enriched["download_path_valid"] = job_download_path_valid(enriched)
  enriched["can_repair_metrics"] = bool(status in PARTIAL_SUCCESS_STATUSES and enriched.get("remote_detached"))
  enriched["can_redownload_result"] = bool(status in DOWNLOADABLE_RESULT_STATUSES and enriched.get("remote_detached"))
  enriched["analysis_metrics_ready"] = bool(enriched.get("result_json_exists"))
  if isinstance(enriched.get("metrics_history"), list):
    enriched["metrics_history_count"] = len(enriched["metrics_history"])
    enriched.pop("metrics_history", None)
  private_fields = [key for key in enriched if key.startswith("_")]
  for private_field in ("log_dir", "log_file", "metrics_csv_file", "stdout", "stderr", *private_fields):
    enriched.pop(private_field, None)
  return enriched


def derive_dataset_label_from_path(path_value: str) -> str:
  text = str(path_value or "").strip().replace("\\", "/")
  if not text:
    return ""
  path = PurePosixPath(text)
  parts = [part for part in path.parts if part and part != "/"]
  if not parts:
    return ""
  if parts[-1] == "workspace" and len(parts) >= 2:
    return "/".join(parts[-2:])
  return parts[-1]


def normalize_job_dataset_fields(job: Dict[str, Any]) -> Dict[str, str]:
  remote_result = job.get("remote_result") if isinstance(job.get("remote_result"), dict) else {}
  dataset_path = str(
    remote_result.get("remote_dataset_workspace")
    or job.get("remote_dataset_path")
    or job.get("dataset_path")
    or job.get("workspace")
    or ""
  ).strip()
  dataset = str(
    remote_result.get("remote_dataset_id")
    or job.get("remote_dataset_id")
    or job.get("dataset")
    or job.get("dataset_id")
    or job.get("dataset_name")
    or ""
  ).strip()
  if not dataset:
    dataset = derive_dataset_label_from_path(dataset_path)
  return {
    "dataset": dataset or "-",
    "dataset_path": dataset_path,
  }


def _append_family_coverage(coverage: Dict[str, set[str]], job_status: str, family: str) -> None:
  normalized_status = str(job_status or "").strip().lower()
  if normalized_status == "completed" or normalized_status in PARTIAL_SUCCESS_STATUSES:
    coverage["trained"].add(family)
    return
  if normalized_status == "failed":
    coverage["failed"].add(family)
    return
  if normalized_status in {"queued", "running"}:
    coverage["running"].add(family)


def _annotate_datasets_with_training_coverage(
  datasets: list[Dict[str, Any]],
  *,
  family_scope: str,
) -> list[Dict[str, Any]]:
  if not datasets:
    return []

  available_families = sorted({
    str(item.get("family", "")).strip()
    for item in list_adapters()
    if str(item.get("family", "")).strip()
  })

  coverage_by_id: Dict[str, Dict[str, set[str]]] = {}
  coverage_by_id_family: Dict[str, Dict[str, set[str]]] = {}
  coverage_by_path: Dict[str, Dict[str, set[str]]] = {}

  for raw_job in list(JOBS.values()):
    job = enrich_job(raw_job)
    if str(job.get("operation", "")).strip() != "remote_train":
      continue

    trained_family = str(job.get("algorithm_family", "")).strip()
    if not trained_family:
      continue

    job_status = str(job.get("status", "")).strip()
    remote_result_raw = job.get("remote_result")
    remote_result: Dict[str, Any] = remote_result_raw if isinstance(remote_result_raw, dict) else {}
    dataset_id = str(
      remote_result.get("remote_dataset_id")
      or job.get("remote_dataset_id")
      or ""
    ).strip()
    dataset_path = _normalize_remote_dataset_path(
      str(
        remote_result.get("remote_dataset_workspace")
        or job.get("remote_dataset_path")
        or ""
      )
    )

    if dataset_id:
      bucket = coverage_by_id.setdefault(dataset_id, {"trained": set(), "failed": set(), "running": set()})
      _append_family_coverage(bucket, job_status, trained_family)

      family_bucket_key = f"{trained_family}::{dataset_id}"
      family_bucket = coverage_by_id_family.setdefault(family_bucket_key, {"trained": set(), "failed": set(), "running": set()})
      _append_family_coverage(family_bucket, job_status, trained_family)

    if dataset_path:
      path_bucket = coverage_by_path.setdefault(dataset_path, {"trained": set(), "failed": set(), "running": set()})
      _append_family_coverage(path_bucket, job_status, trained_family)

  normalized_scope = str(family_scope or "").strip()
  annotated: list[Dict[str, Any]] = []
  for dataset in datasets:
    annotated_item = dict(dataset)
    dataset_id = str(dataset.get("id", "")).strip()
    dataset_path = _normalize_remote_dataset_path(str(dataset.get("path", "")))

    trained_families: set[str] = set()
    failed_families: set[str] = set()
    running_families: set[str] = set()

    if dataset_id and normalized_scope:
      scoped_key = f"{normalized_scope}::{dataset_id}"
      scoped = coverage_by_id_family.get(scoped_key)
      if scoped:
        trained_families.update(scoped["trained"])
        failed_families.update(scoped["failed"])
        running_families.update(scoped["running"])

    if dataset_id:
      generic = coverage_by_id.get(dataset_id)
      if generic:
        trained_families.update(generic["trained"])
        failed_families.update(generic["failed"])
        running_families.update(generic["running"])

    if dataset_path:
      by_path = coverage_by_path.get(dataset_path)
      if by_path:
        trained_families.update(by_path["trained"])
        failed_families.update(by_path["failed"])
        running_families.update(by_path["running"])

    missing_families = sorted(f for f in available_families if f not in trained_families)

    annotated_item["trained_families"] = sorted(trained_families)
    annotated_item["failed_families"] = sorted(failed_families)
    annotated_item["running_families"] = sorted(running_families)
    annotated_item["missing_families"] = missing_families
    annotated_item["training_coverage"] = {
      "available_families": available_families,
      "trained_families": sorted(trained_families),
      "failed_families": sorted(failed_families),
      "running_families": sorted(running_families),
      "missing_families": missing_families,
    }
    annotated.append(annotated_item)

  return annotated


def prune_job_history() -> None:
  ordered_jobs = sorted(list(JOBS.values()), key=lambda item: item.get("created_at", 0), reverse=True)
  keep_ids: set[str] = set()
  completed_process_frame_count = 0

  for job in ordered_jobs:
    job_id = job.get("id")
    if not job_id:
      continue
    if job.get("operation") == "process_frame" and job.get("status") == "completed":
      if completed_process_frame_count >= MAX_PROCESS_FRAME_COMPLETED_HISTORY:
        continue
      completed_process_frame_count += 1
    keep_ids.add(job_id)
    if len(keep_ids) >= MAX_JOB_HISTORY:
      break

  with JOBS_LOCK:
    for job_id in list(JOBS.keys()):
      if job_id not in keep_ids:
        del JOBS[job_id]
        JOB_LOG_LOCKS.pop(job_id, None)


def is_job_terminal(job: Dict[str, Any]) -> bool:
  return str(job.get("status", "")).strip().lower() in TERMINAL_STATUSES




def is_remote_download_pending(job: Dict[str, Any]) -> bool:
  download = remote_download_info(job)
  return bool(job.get("remote_detached") and download.get("pending"))


def is_remote_download_active(job: Dict[str, Any]) -> bool:
  thread = job.get("_result_download_thread")
  return bool(thread is not None and getattr(thread, "is_alive", lambda: False)())


def is_remote_download_starting(job: Dict[str, Any]) -> bool:
  return bool(job.get("_result_download_starting"))


def mark_stale_remote_download_if_needed(job: Dict[str, Any]) -> bool:
  if not is_remote_download_pending(job):
    return False
  if is_remote_download_active(job) or is_remote_download_starting(job):
    return False
  set_remote_download_progress(job, {
    "pending": False,
    "phase": "error",
    "error": "Result download was interrupted before completion. Click Retry Download to check and resume missing files.",
    "interrupted": True,
  })
  job["remote_stage"] = "result_download_failed"
  job["monitor_state"] = "completed"
  return True


def completed_remote_result_paths(job: Dict[str, Any]) -> tuple[str, str]:
  remote_result = job.get("remote_result") if isinstance(job.get("remote_result"), dict) else {}
  remote_output_dir = str(job.get("remote_output_dir") or remote_result.get("remote_output_dir") or "").strip()
  local_output_dir = str(job.get("output_dir") or "").strip()
  return remote_output_dir, local_output_dir


def canonical_job_output_dir(job: Dict[str, Any]) -> str:
  return remote_job_local_output_dir(
    str(job.get("session_id") or "default-session"),
    str(job.get("algorithm_family") or "algorithm"),
    str(job.get("id") or "job"),
  )


def job_download_path_valid(job: Dict[str, Any]) -> bool:
  local_output_dir = str(job.get("output_dir") or "").strip()
  if not local_output_dir:
    return False
  try:
    return Path(local_output_dir).expanduser().resolve() == Path(canonical_job_output_dir(job)).resolve()
  except Exception:
    return False


def ensure_job_download_path_valid(job: Dict[str, Any]) -> None:
  if job_download_path_valid(job):
    job["download_path_valid"] = True
    return
  job["download_path_valid"] = False
  job["remote_stage"] = "download_path_mismatch"
  raise ValueError(
    "Local output_dir does not match this job/family. Refusing to download results into a potentially stale directory."
  )


def reset_job_output_dir_to_canonical(job: Dict[str, Any]) -> str:
  output_dir = canonical_job_output_dir(job)
  job["output_dir"] = output_dir
  job["download_path_valid"] = True
  Path(output_dir).mkdir(parents=True, exist_ok=True)
  return output_dir


def normalize_remote_identity(config: Dict[str, Any]) -> Dict[str, Any]:
  if not isinstance(config, dict):
    return {}
  raw_output_root = str(config.get("output_root", "") or "").strip().replace("\\", "/")
  output_root = str(PurePosixPath(raw_output_root)) if raw_output_root else ""
  try:
    port_value = int(config.get("port", 22))
  except Exception:
    port_value = 0
  return {
    "host": str(config.get("host", "") or "").strip(),
    "port": port_value,
    "username": str(config.get("username", "") or "").strip(),
    "output_root": output_root,
  }


def remote_identity_missing_fields(identity: Dict[str, Any]) -> list[str]:
  missing: list[str] = []
  for field in REMOTE_RESULT_IDENTITY_FIELDS:
    value = identity.get(field)
    if field == "port":
      if not isinstance(value, int) or value <= 0:
        missing.append(field)
    elif not str(value or "").strip():
      missing.append(field)
  return missing


def assert_result_download_remote_identity(job: Dict[str, Any], remote_config: Dict[str, Any]) -> None:
  expected = normalize_remote_identity(job.get("remote") if isinstance(job.get("remote"), dict) else {})
  current = normalize_remote_identity(remote_config)
  missing_fields = remote_identity_missing_fields(expected)
  if missing_fields:
    raise RemoteExecutionError(
      "Cannot verify this job's original remote server. Refusing to download results.",
      code_hint="WGSC-JOB-RESULTS-REMOTE-MISSING",
      stage="config",
      details={
        "job_id": job.get("id", ""),
        "expected": expected,
        "current": current,
        "missing_fields": missing_fields,
      },
    )

  mismatch_fields = [
    field for field in REMOTE_RESULT_IDENTITY_FIELDS
    if expected.get(field) != current.get(field)
  ]
  if mismatch_fields:
    raise RemoteExecutionError(
      "Remote config does not match this job's server.",
      code_hint="WGSC-JOB-RESULTS-REMOTE-MISMATCH",
      stage="config",
      details={
        "job_id": job.get("id", ""),
        "expected": expected,
        "current": current,
        "mismatch_fields": mismatch_fields,
      },
    )


def result_download_remote_config(job: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
  remote_config = payload.get("remote") if isinstance(payload.get("remote"), dict) else job.get("_remote_config")
  if not isinstance(remote_config, dict) or not str(remote_config.get("password", "")).strip():
    raise RemoteExecutionError(
      "Remote credentials are required to download completed job results.",
      code_hint="WGSC-JOB-RESULTS-REMOTE-CONFIG",
      stage="config",
    )
  validated = validate_remote_config(remote_config)
  assert_result_download_remote_identity(job, validated)
  return validated


def validate_completed_result_download_job(job: Dict[str, Any]) -> tuple[str, str]:
  status = str(job.get("status", "")).strip().lower()
  if status not in DOWNLOADABLE_RESULT_STATUSES:
    raise ValueError("Only completed or partial-success jobs can download results.")
  if not job.get("remote_detached"):
    raise ValueError("Only completed remote jobs can download results.")
  remote_output_dir, local_output_dir = completed_remote_result_paths(job)
  if not remote_output_dir:
    raise ValueError("Completed remote job has no remote_output_dir to download.")
  if not local_output_dir or not job_download_path_valid(job):
    previous_output_dir = local_output_dir
    local_output_dir = reset_job_output_dir_to_canonical(job)
    job["legacy_output_dir_migrated"] = True
    if previous_output_dir:
      job["previous_output_dir"] = previous_output_dir
    job["remote_stage"] = "download_path_migrated"
  else:
    job["download_path_valid"] = True
  return remote_output_dir, local_output_dir


def completed_remote_status_path(job: Dict[str, Any], remote_output_dir: str) -> str:
  remote_status_path = str(job.get("remote_status_path") or "").strip()
  if remote_status_path:
    return remote_status_path
  return str(PurePosixPath(remote_output_dir) / ".wgsc_job" / "status.json")


def set_remote_download_progress(job: Dict[str, Any], progress: Dict[str, Any]) -> None:
  remote_result = job.setdefault("remote_result", {})
  if not isinstance(remote_result, dict):
    remote_result = {}
    job["remote_result"] = remote_result
  download = {
    **remote_download_info(job),
    **progress,
    "updated_at": time.time(),
  }
  remote_result["download"] = download
  job["safe_to_close_web"] = True
  if download.get("pending"):
    job["remote_stage"] = "remote_downloading"
    job["monitor_state"] = "downloading"


def apply_completed_result_check(job: Dict[str, Any], check: Dict[str, Any]) -> None:
  set_remote_download_progress(job, {
    **check,
    "pending": False,
    "phase": "complete" if check.get("complete") else "incomplete",
  })
  if check.get("complete"):
    update_job_artifacts(job)
  job["remote_stage"] = "completed"
  job["monitor_state"] = "completed"


def check_completed_result_download(job: Dict[str, Any], remote_config: Dict[str, Any]) -> Dict[str, Any]:
  remote_output_dir, local_output_dir = validate_completed_result_download_job(job)
  assert_result_download_remote_identity(job, remote_config)
  check = check_remote_output_download(
    remote_config=remote_config,
    remote_output_dir=remote_output_dir,
    local_output_dir=local_output_dir,
    remote_status_path=completed_remote_status_path(job, remote_output_dir),
    expected_job_id=str(job.get("id", "")),
    expected_family=str(job.get("algorithm_family", "")),
    expected_remote_output_dir=remote_output_dir,
  )
  apply_completed_result_check(job, check)
  persist_job_state(job)
  return check


def start_completed_result_download(job: Dict[str, Any], remote_config: Dict[str, Any]) -> Dict[str, Any]:
  with RESULT_DOWNLOAD_LOCK:
    if is_remote_download_active(job) or is_remote_download_starting(job):
      return {"ok": True, "started": False, "already_running": True, "job": enrich_job(job)}
    remote_output_dir, local_output_dir = validate_completed_result_download_job(job)
    assert_result_download_remote_identity(job, remote_config)
    job["_result_download_starting"] = True

  try:
    check = check_remote_output_download(
      remote_config=remote_config,
      remote_output_dir=remote_output_dir,
      local_output_dir=local_output_dir,
      remote_status_path=completed_remote_status_path(job, remote_output_dir),
      expected_job_id=str(job.get("id", "")),
      expected_family=str(job.get("algorithm_family", "")),
      expected_remote_output_dir=remote_output_dir,
    )
  except Exception:
    with RESULT_DOWNLOAD_LOCK:
      job.pop("_result_download_starting", None)
    raise

  if check.get("complete"):
    with RESULT_DOWNLOAD_LOCK:
      apply_completed_result_check(job, check)
      job.pop("_result_download_starting", None)
    persist_job_state(job)
    return {"ok": True, "started": False, "complete": True, "check": check, "job": enrich_job(job)}

  set_remote_download_progress(job, {
    **check,
    "pending": True,
    "phase": "queued",
    "files": 0,
    "bytes": 0,
    "current_file": "",
  })
  persist_job_state(job)

  def runner() -> None:
    try:
      def record_download_progress(progress: Dict[str, Any]) -> None:
        set_remote_download_progress(job, progress)
        persist_job_state(job)

      result = download_remote_output_directory(
        remote_config=remote_config,
        remote_output_dir=remote_output_dir,
        local_output_dir=local_output_dir,
        remote_status_path=completed_remote_status_path(job, remote_output_dir),
        expected_job_id=str(job.get("id", "")),
        expected_family=str(job.get("algorithm_family", "")),
        expected_remote_output_dir=remote_output_dir,
        progress_callback=record_download_progress,
      )
      set_remote_download_progress(job, {
        **result,
        "pending": False,
        "phase": "complete",
      })
      job["remote_stage"] = "completed"
      job["monitor_state"] = "completed"
      update_job_artifacts(job)
      persist_job_state(job)
    except Exception as exc:  # pragma: no cover - network dependent
      set_remote_download_progress(job, {
        "pending": False,
        "phase": "error",
        "error": str(exc),
      })
      job["remote_stage"] = "result_download_failed"
      job["monitor_state"] = "completed"
      append_job_log_line(job, "stderr", f"[ResultDownload] Failed to download completed result: {exc}\n")
      persist_job_state(job)
    finally:
      with RESULT_DOWNLOAD_LOCK:
        job.pop("_result_download_thread", None)
        job.pop("_result_download_starting", None)

  thread = threading.Thread(target=runner, daemon=True)
  with RESULT_DOWNLOAD_LOCK:
    job["_result_download_thread"] = thread
    job.pop("_result_download_starting", None)
  thread.start()
  return {"ok": True, "started": True, "complete": False, "check": check, "job": enrich_job(job)}


def start_completed_result_redownload(job: Dict[str, Any], remote_config: Dict[str, Any]) -> Dict[str, Any]:
  reset_job_output_dir_to_canonical(job)
  persist_job_state(job)
  return start_completed_result_download(job, remote_config)


def start_job_metrics_repair(job: Dict[str, Any], remote_config: Dict[str, Any], *, force: bool = False) -> Dict[str, Any]:
  status = str(job.get("status", "")).strip().lower()
  if status not in PARTIAL_SUCCESS_STATUSES and not force:
    raise ValueError("Only partial-success jobs can repair metrics unless force=true.")
  if not job.get("remote_detached"):
    raise ValueError("Only remote detached jobs can repair metrics.")
  if job.get("_repair_metrics_running"):
    return {"ok": True, "started": False, "already_running": True, "job": enrich_job(job)}

  remote_result = job.get("remote_result") if isinstance(job.get("remote_result"), dict) else {}
  remote_output_dir = str(job.get("remote_output_dir") or remote_result.get("remote_output_dir") or "").strip()
  remote_dataset_workspace = str(remote_result.get("remote_dataset_workspace") or job.get("remote_dataset_path") or "").strip()
  if not remote_output_dir:
    raise ValueError("Job has no remote_output_dir for metrics repair.")
  if not remote_dataset_workspace:
    raise ValueError("Job has no remote_dataset_workspace for metrics repair.")

  job["_repair_metrics_running"] = True
  job["remote_stage"] = "repair_metrics_queued"
  job["monitor_state"] = "monitoring"
  job["safe_to_close_web"] = True
  persist_job_state(job)

  def runner() -> None:
    try:
      append_job_log_line(job, "stdout", "[Repair] Starting render/metrics repair for partial-success job.\n")

      def on_log(channel: str, line: str) -> None:
        append_job_log_line(job, channel, line)

      result = repair_remote_job_metrics(
        remote_config=remote_config,
        job_id=str(job.get("id", "")),
        family=str(job.get("algorithm_family", "")),
        remote_output_dir=remote_output_dir,
        remote_dataset_workspace=remote_dataset_workspace,
        remote_command=str(job.get("remote_command") or remote_result.get("remote_command") or job.get("command") or ""),
        log_callback=on_log,
      )
      for key in (
        "train_status",
        "render_status",
        "metrics_status",
        "postprocess_status",
        "result_json_exists",
        "result_path",
        "failure_stage",
      ):
        if key in result:
          job[key] = result[key]
      if result.get("ok") and result.get("result_json_exists"):
        job["status"] = "completed"
        job["partial_success"] = False
        job["failure_stage"] = ""
        job["remote_stage"] = "completed"
        job["monitor_state"] = "completed"
        job["return_code"] = 0
        reset_job_output_dir_to_canonical(job)
        try:
          remote_download_dir, local_download_dir = completed_remote_result_paths(job)

          def record_download_progress(progress: Dict[str, Any]) -> None:
            set_remote_download_progress(job, progress)
            persist_job_state(job)

          download_result = download_remote_output_directory(
            remote_config=remote_config,
            remote_output_dir=remote_download_dir,
            local_output_dir=local_download_dir,
            remote_status_path=completed_remote_status_path(job, remote_download_dir),
            expected_job_id=str(job.get("id", "")),
            expected_family=str(job.get("algorithm_family", "")),
            expected_remote_output_dir=remote_download_dir,
            progress_callback=record_download_progress,
          )
          set_remote_download_progress(job, {
            **download_result,
            "pending": False,
            "phase": "complete",
          })
        except Exception as exc:
          set_remote_download_progress(job, {
            "pending": False,
            "phase": "error",
            "error": str(exc),
          })
          append_job_log_line(job, "stderr", f"[Repair] Metrics repair succeeded, but result download failed: {exc}\n")
      else:
        job["status"] = "partial_success"
        job["partial_success"] = True
        job["remote_stage"] = result.get("stage") or "post_train_render_or_metrics"
        job["monitor_state"] = "completed"
        job["return_code"] = result.get("return_code", job.get("return_code", ""))
      job["finished_at"] = time.time()
      update_job_artifacts(job)
      persist_job_state(job)
    except Exception as exc:  # pragma: no cover - network dependent
      job["status"] = "partial_success"
      job["partial_success"] = True
      job["failure_stage"] = "post_train_render_or_metrics"
      job["remote_stage"] = "repair_metrics_failed"
      job["monitor_state"] = "completed"
      append_job_log_line(job, "stderr", f"[Repair] Metrics repair failed: {exc}\n")
      persist_job_state(job)
    finally:
      job.pop("_repair_metrics_running", None)
      persist_job_state(job)

  threading.Thread(target=runner, daemon=True).start()
  return {"ok": True, "started": True, "job": enrich_job(job)}




def job_log_dir_for_id(job_id: str) -> Path:
  raw = str(job_id or "").strip()
  if not raw:
    raise ValueError("job_id is required")
  candidate = (JOB_LOG_DIR / raw).resolve()
  root = JOB_LOG_DIR.resolve()
  if root == candidate or root not in candidate.parents:
    raise ValueError(f"Unsafe job log path: {candidate}")
  return candidate


def remove_job_record(job_id: str, *, require_terminal: bool = True) -> Dict[str, Any]:
  raw = str(job_id or "").strip()
  if not raw:
    raise ValueError("job_id is required")
  job = JOBS.get(raw)
  if not job:
    raise KeyError(f"Unknown job: {raw}")
  if require_terminal and not is_job_terminal(job):
    raise ValueError("Only completed, failed, or canceled job records can be removed. Cancel running jobs first.")

  log_dir_value = str(job.get("log_dir", "")).strip()
  try:
    log_dir = Path(log_dir_value).resolve() if log_dir_value else job_log_dir_for_id(raw)
  except Exception:
    log_dir = job_log_dir_for_id(raw)
  job_log_root = JOB_LOG_DIR.resolve()
  if log_dir == job_log_root or not path_is_inside(log_dir, job_log_root):
    raise ValueError(f"Unsafe job log path: {log_dir}")

  state_file = log_dir / JOB_STATE_FILE
  removed_state_file = False
  if state_file.exists() and state_file.is_file():
    state_file.unlink()
    removed_state_file = True

  removed = JOBS.pop(raw, None)
  JOB_LOG_LOCKS.pop(raw, None)
  return {
    "id": raw,
    "status": removed.get("status", "") if isinstance(removed, dict) else "",
    "removed_state_file": removed_state_file,
    "logs_retained": str(log_dir),
  }


def normalize_clear_statuses(value: Any) -> set[str]:
  if value in (None, "", []):
    return set(TERMINAL_STATUSES)
  if isinstance(value, str):
    raw_items = [value]
  elif isinstance(value, list):
    raw_items = value
  else:
    raw_items = []

  statuses: set[str] = set()
  for item in raw_items:
    text = str(item or "").strip().lower()
    if not text:
      continue
    if text in {"finished", "terminal", "done"}:
      statuses.update(TERMINAL_STATUSES)
    else:
      statuses.add(text)
  return {status for status in statuses if status in TERMINAL_STATUSES}


def clear_job_records(statuses_value: Any = None) -> Dict[str, Any]:
  statuses = normalize_clear_statuses(statuses_value)
  removed: list[Dict[str, Any]] = []
  skipped: list[Dict[str, str]] = []
  for job_id, job in list(JOBS.items()):
    status = str(job.get("status", "")).strip().lower()
    if status not in statuses:
      continue
    if not is_job_terminal(job):
      skipped.append({"id": job_id, "status": status})
      continue
    removed.append(remove_job_record(job_id, require_terminal=True))
  return {
    "ok": True,
    "statuses": sorted(statuses),
    "removed": removed,
    "removed_count": len(removed),
    "skipped": skipped,
    "remaining": len(JOBS),
  }


def update_job_artifacts(job: Dict[str, Any]) -> None:
  if is_remote_download_pending(job):
    return
  artifacts = read_runtime_artifacts(job.get("output_dir"), job.get("representation"), job.get("algorithm_family"), job)
  existing_metrics = dict(job.get("metrics", {})) if isinstance(job.get("metrics"), dict) else {}
  for strict_key in (
    "psnr",
    "psnr_db",
    "ssim",
    "lpips",
    "size_mb",
    "size_bytes",
    "fps",
    "render_fps",
    "psnr_source",
    "ssim_source",
    "lpips_source",
    "size_source",
    "render_fps_source",
  ):
    existing_metrics.pop(strict_key, None)
  if artifacts.get("metrics"):
    job["metrics"] = {
      **existing_metrics,
      **artifacts["metrics"],
    }
    if isinstance(artifacts["metrics"].get("analysis_metric_sources"), dict):
      job["analysis_metric_sources"] = artifacts["metrics"]["analysis_metric_sources"]
  for key in ARTIFACT_RESULT_FIELDS:
    if artifacts.get(key):
      job[key] = artifacts[key]
  for key in ("result_json_exists", "result_path"):
    if key in artifacts:
      job[key] = artifacts[key]


def apply_remote_poll_result(job: Dict[str, Any], poll_result: Dict[str, Any]) -> None:
  log_text = str(poll_result.get("log_text", ""))
  if log_text:
    append_job_log_line(job, "stdout", log_text)
  job["remote_log_cursor"] = int(poll_result.get("log_cursor", job.get("remote_log_cursor", 0)) or 0)
  job["status"] = str(poll_result.get("status") or job.get("status") or "running")
  job["remote_stage"] = str(poll_result.get("stage") or job.get("remote_stage") or "remote_detached_running")
  job["monitor_state"] = "monitoring" if not is_job_terminal(job) else "completed"
  job["safe_to_close_web"] = True
  if poll_result.get("return_code") not in (None, ""):
    job["return_code"] = poll_result.get("return_code")
  for key in (
    "train_status",
    "render_status",
    "metrics_status",
    "postprocess_status",
    "result_json_exists",
    "result_path",
    "partial_success",
    "failure_stage",
    "updated_at",
  ):
    if key in poll_result:
      job[key] = poll_result[key]
  if poll_result.get("remote_pid"):
    job["remote_pid"] = poll_result.get("remote_pid")
  if poll_result.get("remote_tmux_session"):
    job["remote_tmux_session"] = poll_result.get("remote_tmux_session")
  remote_result = job.setdefault("remote_result", {})
  for target_key, source_key in (
    ("remote_output_dir", "remote_output_dir"),
    ("remote_dataset_id", "remote_dataset_id"),
    ("remote_dataset_name", "remote_dataset_name"),
    ("remote_dataset_workspace", "remote_dataset_workspace"),
  ):
    if poll_result.get(source_key):
      remote_result[target_key] = poll_result[source_key]
  if poll_result.get("download"):
    remote_result["download"] = poll_result["download"]
  update_job_artifacts(job)
  if is_job_terminal(job) and not job.get("finished_at"):
    job["finished_at"] = time.time()


def start_remote_monitor_thread(job: Dict[str, Any], remote_config: Dict[str, Any]) -> None:
  with REMOTE_MONITOR_START_LOCK:
    if job.get("_monitoring"):
      return
    job["_monitoring"] = True
    job["_remote_config"] = dict(remote_config)

  def monitor() -> None:
    consecutive_failures = 0
    first_failure_at = 0.0
    try:
      while True:
        if job.get("cancel_requested"):
          break
        try:
          poll_result = poll_remote_detached_job(
            remote_config=remote_config,
            remote_status_path=str(job.get("remote_status_path", "")),
            remote_log_path=str(job.get("remote_log_path", "")),
            remote_output_dir=str(job.get("remote_output_dir", "")),
            local_output_dir=str(job.get("output_dir", "")),
            log_cursor=int(job.get("remote_log_cursor", 0) or 0),
            download_output=False,
            expected_job_id=str(job.get("id", "")),
            expected_family=str(job.get("algorithm_family", "")),
          )
          consecutive_failures = 0
          first_failure_at = 0.0
          job.pop("remote_monitor_error", None)
          job.pop("remote_monitor_failures", None)
          apply_remote_poll_result(job, poll_result)
          persist_job_state(job)
          if is_job_terminal(job):
            set_remote_download_progress(job, {
              "files": 0,
              "bytes": 0,
              "pending": True,
              "current_file": "",
            })
            persist_job_state(job)

            def record_download_progress(progress: Dict[str, Any]) -> None:
              set_remote_download_progress(job, progress)
              persist_job_state(job)

            final_poll = poll_remote_detached_job(
              remote_config=remote_config,
              remote_status_path=str(job.get("remote_status_path", "")),
              remote_log_path=str(job.get("remote_log_path", "")),
              remote_output_dir=str(job.get("remote_output_dir", "")),
              local_output_dir=str(job.get("output_dir", "")),
              log_cursor=int(job.get("remote_log_cursor", 0) or 0),
              download_output=True,
              download_progress_callback=record_download_progress,
              expected_job_id=str(job.get("id", "")),
              expected_family=str(job.get("algorithm_family", "")),
            )
            apply_remote_poll_result(job, final_poll)
            if str(job.get("monitor_state", "")) != "completed":
              job["monitor_state"] = "completed"
            persist_job_state(job)
            break
        except Exception as exc:  # pragma: no cover - network dependent
          now = time.time()
          consecutive_failures += 1
          if not first_failure_at:
            first_failure_at = now
          elapsed = now - first_failure_at
          job["monitor_state"] = "monitoring"
          job["remote_stage"] = "detached_monitor_retrying"
          job["remote_monitor_error"] = str(exc)
          job["remote_monitor_failures"] = consecutive_failures
          if consecutive_failures == 1 or consecutive_failures % 5 == 0:
            append_job_log_line(
              job,
              "stderr",
              f"[Monitor] Detached remote monitor transient error "
              f"({consecutive_failures}/{REMOTE_MONITOR_MAX_CONSECUTIVE_FAILURES}): {exc}\n",
            )
          persist_job_state(job)
          if (
            consecutive_failures >= REMOTE_MONITOR_MAX_CONSECUTIVE_FAILURES
            or elapsed >= REMOTE_MONITOR_MAX_FAILURE_SECONDS
          ):
            job["monitor_state"] = "needs_remote_config"
            job["remote_stage"] = "detached_monitor_disconnected"
            append_job_log_line(job, "stderr", f"[Monitor] Detached remote monitor disconnected: {exc}\n")
            persist_job_state(job)
            break
          time.sleep(min(
            REMOTE_MONITOR_RETRY_MAX_SECONDS,
            REMOTE_MONITOR_RETRY_MIN_SECONDS + max(0, consecutive_failures - 1),
          ))
          continue
        time.sleep(3)
    finally:
      job["_monitoring"] = False

  threading.Thread(target=monitor, daemon=True).start()


def job_matches_flow(job: Dict[str, Any], *, session_id: str, capture_id: str, selected_job_id: str) -> bool:
  if selected_job_id and str(job.get("id", "")).strip() == selected_job_id:
    return True
  if session_id:
    job_session = str(job.get("session_id") or job.get("stream_session_id") or "").strip()
    if job_session == session_id:
      return True
  if capture_id:
    job_capture = str(job.get("capture_id", "")).strip()
    if job_capture == capture_id:
      return True
  return False


def unfinished_flow_jobs(*, session_id: str, capture_id: str, selected_job_id: str) -> list[Dict[str, Any]]:
  if not any((session_id, capture_id, selected_job_id)):
    return []
  return [
    job
    for job in list(JOBS.values())
    if not is_job_terminal(job)
    and job_matches_flow(job, session_id=session_id, capture_id=capture_id, selected_job_id=selected_job_id)
  ]


def cancel_flow_job(job: Dict[str, Any], remote_config: Dict[str, Any] | None = None) -> Dict[str, Any]:
  if is_job_terminal(job):
    return {"id": job.get("id", ""), "status": job.get("status", ""), "already_terminal": True}

  append_job_log_line(job, "stderr", "[FlowReset] Canceling unfinished job before restarting upload training.\n")
  job["cancel_requested"] = True

  if job.get("remote_detached"):
    if not isinstance(remote_config, dict) or not str(remote_config.get("password", "")).strip():
      job["monitor_state"] = "needs_remote_config"
      persist_job_state(job)
      raise RemoteExecutionError(
        "Remote credentials are required to cancel detached jobs before resetting the flow.",
        code_hint="WGSC-FLOW-RESET-REMOTE-CONFIG",
        stage="flow_reset",
      )
    cancel_remote_detached_job(
      remote_config=remote_config,
      remote_job_dir=str(job.get("remote_job_dir", "")),
      remote_pid=str(job.get("remote_pid", "")),
      remote_tmux_session=str(job.get("remote_tmux_session", "")),
    )
  else:
    process = job.get("_process")
    if process is not None:
      try:
        if process.poll() is None:
          process.terminate()
      except Exception as exc:
        append_job_log_line(job, "stderr", f"[FlowReset] Failed to terminate local process: {exc}\n")

  job["status"] = "canceled"
  job["remote_stage"] = "flow_reset"
  job["monitor_state"] = "completed"
  job["return_code"] = 130
  job["finished_at"] = time.time()
  persist_job_state(job)
  return {"id": job.get("id", ""), "status": "canceled", "remote_detached": bool(job.get("remote_detached"))}


def flow_temporary_dirs(session_id: str) -> list[Path]:
  raw = str(session_id or "").strip()
  if not raw:
    return []
  roots = [STREAM_DIR, DATASET_DIR, WORKSPACE_DIR]
  candidates: list[Path] = []
  for root in roots:
    root_resolved = root.resolve()
    candidate = (root_resolved / raw).resolve()
    if root_resolved == candidate or root_resolved not in candidate.parents:
      raise ValueError(f"Refusing to clean unsafe flow path: {candidate}")
    candidates.append(candidate)
  return candidates


def cleanup_flow_temporary_dirs(
  session_id: str,
  *,
  remover: Callable[[Path], None] | None = None,
  exists: Callable[[Path], bool] | None = None,
) -> list[str]:
  remover = remover or (lambda path: shutil.rmtree(path))
  exists = exists or (lambda path: path.exists())
  cleaned: list[str] = []
  for path in flow_temporary_dirs(session_id):
    if not exists(path):
      continue
    remover(path)
    cleaned.append(str(path))
  return cleaned


def reset_flow(payload: Dict[str, Any]) -> Dict[str, Any]:
  session_id = str(payload.get("session_id", "")).strip()
  capture_id = str(payload.get("capture_id", "")).strip()
  selected_job_id = str(payload.get("selected_job_id", "")).strip()
  cancel_unfinished = bool(payload.get("cancel_unfinished", True))
  remote_config = payload.get("remote") if isinstance(payload.get("remote"), dict) else {}

  targets = unfinished_flow_jobs(
    session_id=session_id,
    capture_id=capture_id,
    selected_job_id=selected_job_id,
  )
  if cancel_unfinished:
    needs_remote = [job for job in targets if job.get("remote_detached")]
    if needs_remote and not str(remote_config.get("password", "")).strip():
      raise RemoteExecutionError(
        "Remote credentials are required to cancel detached jobs before resetting the flow.",
        code_hint="WGSC-FLOW-RESET-REMOTE-CONFIG",
        stage="flow_reset",
      )

  canceled: list[Dict[str, Any]] = []
  if cancel_unfinished:
    for job in targets:
      canceled.append(cancel_flow_job(job, remote_config))

  cleaned_paths = cleanup_flow_temporary_dirs(session_id)
  return {
    "ok": True,
    "code": "WGSC-FLOW-RESET-OK",
    "canceled_jobs": canceled,
    "cleaned_paths": cleaned_paths,
    "session_id": session_id,
    "capture_id": capture_id,
    "reset_scope": str(payload.get("reset_scope", "all")),
  }


def start_job_thread(job: Dict[str, Any], command: str, cwd: str | None = None) -> None:
  def runner() -> None:
    ensure_job_logging(job)
    if job.get("cancel_requested"):
      job["status"] = "canceled"
      job["finished_at"] = time.time()
      return
    job["status"] = "running"
    job["started_at"] = time.time()
    job["metrics"] = {}
    job["stdout"] = ""
    job["stderr"] = ""
    combined_log: list[str] = []

    def consume_stream(stream, channel: str) -> None:
      for line in iter(stream.readline, ""):
        if not line:
          break
        append_job_log_line(job, channel, line)
        combined_log.append(line)
        job["metrics"] = extract_job_metrics("".join(combined_log[-2000:]))
        artifacts = read_runtime_artifacts_cached(job)
        if artifacts.get("metrics"):
          job["metrics"] = {
            **job.get("metrics", {}),
            **artifacts["metrics"],
          }
        for key in ARTIFACT_RESULT_FIELDS:
          if artifacts.get(key):
            job[key] = artifacts[key]
      stream.close()

    try:
      effective_cwd = resolve_workspace_path(cwd) or None
      args = shlex.split(command, posix=not sys.platform.startswith("win"))
      if args and args[0] == "python":
        args[0] = sys.executable
      # Remove any empty trailing arguments or literal empty quotes that shlex on posix=False might leave
      args = [a.strip('"').strip("'") if a in ('""', "''") else a for a in args]
      cleaned_args: list[str] = []
      idx = 0
      while idx < len(args):
        token = args[idx]
        if token == "--repo-path" and (idx + 1 >= len(args) or args[idx + 1].startswith("--")):
          idx += 1
          continue
        cleaned_args.append(token)
        idx += 1
      args = cleaned_args
      if len(args) >= 2 and args[1].lower().endswith(".py"):
        script_arg = args[1].strip('"').strip("'")
        script_path = Path(script_arg)
        if not script_path.is_absolute():
          base_dir = Path(effective_cwd) if effective_cwd else ROOT_DIR
          candidate = (base_dir / script_path).resolve()
          if not candidate.exists() and script_arg.replace("\\", "/").startswith("../web/"):
            candidate = (ROOT_DIR / script_arg.replace("\\", "/")[3:]).resolve()
          if candidate.exists():
            args[1] = str(candidate)
      process = subprocess.Popen(
        args,
        cwd=effective_cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
      )
      job["_process"] = process
      stdout_thread = threading.Thread(target=consume_stream, args=(process.stdout, "stdout"), daemon=True)
      stderr_thread = threading.Thread(target=consume_stream, args=(process.stderr, "stderr"), daemon=True)
      stdout_thread.start()
      stderr_thread.start()
      return_code = process.wait()
      stdout_thread.join(timeout=1)
      stderr_thread.join(timeout=1)
      artifacts = read_runtime_artifacts(job.get("output_dir"), job.get("representation"), job.get("algorithm_family"), job)
      if artifacts.get("metrics"):
        job["metrics"] = {
          **job.get("metrics", {}),
          **artifacts["metrics"],
        }
      for key in ARTIFACT_RESULT_FIELDS:
        if artifacts.get(key):
          job[key] = artifacts[key]
      job["return_code"] = return_code
      if return_code != 0:
        missing_module = extract_missing_module_from_stderr(job.get("stderr", ""))
        if missing_module:
          adapter = get_adapter(job.get("algorithm_family", "")) or {}
          hint_text = build_dependency_hint(adapter, missing_module)
          if hint_text:
            append_job_log_line(job, "stderr", hint_text)
            merged_stderr = f"{job.get('stderr', '').rstrip()}\n\n{hint_text}".strip()
            job["stderr"] = merged_stderr[-12000:]
      if job.get("cancel_requested"):
        job["status"] = "canceled"
      else:
        job["status"] = "completed" if return_code == 0 else "failed"
    except Exception as exc:  # pragma: no cover
      job["status"] = "canceled" if job.get("cancel_requested") else "failed"
      LOGGER.exception("Local job %s failed", job.get("id", ""))
      append_job_log_line(job, "stderr", str(exc))
      job["stderr"] = str(exc)
    finally:
      job.pop("_process", None)
      job["finished_at"] = time.time()

  threading.Thread(target=runner, daemon=True).start()


def start_remote_job_thread(
  job: Dict[str, Any],
  *,
  remote_config: Dict[str, Any],
  local_workspace_dir: str | None,
  local_output_dir: str,
  command_template: str,
  checkpoint_path: str,
  input_path: str,
  source_path: str,
  session_id: str,
  auto_colmap: bool,
  dataset_name: str,
  remote_dataset_id: str,
  remote_dataset_path: str,
  use_existing_remote_dataset: bool,
  training_args: Dict[str, Any] | None = None,
  command_override: str = "",
  remote_run_key: str = "",
  post_train_config: Dict[str, Any] | None = None,
) -> None:
  auto_colmap = effective_auto_colmap(auto_colmap, use_existing_remote_dataset)

  def runner() -> None:
    ensure_job_logging(job)
    if job.get("cancel_requested"):
      job["status"] = "canceled"
      job["remote_stage"] = "canceled"
      job["finished_at"] = time.time()
      return
    job["status"] = "running"
    job["started_at"] = time.time()
    job["metrics"] = {}
    job["stdout"] = ""
    job["stderr"] = ""
    job["remote_stage"] = "queued"
    job["monitor_state"] = "starting"
    persist_job_state(job)
    combined_log: list[str] = []

    def update_artifacts() -> None:
      artifacts = read_runtime_artifacts(job.get("output_dir"), job.get("representation"), job.get("algorithm_family"), job)
      if artifacts.get("metrics"):
        job["metrics"] = {
          **job.get("metrics", {}),
          **artifacts["metrics"],
        }
      for key in ARTIFACT_RESULT_FIELDS:
        if artifacts.get(key):
          job[key] = artifacts[key]

    def on_log(channel: str, line: str) -> None:
      append_job_log_line(job, channel, line)
      combined_log.append(line)
      job["metrics"] = extract_job_metrics("".join(combined_log[-2000:]))
      update_artifacts()

    def on_stage(stage: str) -> None:
      job["remote_stage"] = stage

    try:
      result = start_remote_algorithm_detached(
        remote_config=remote_config,
        local_workspace_dir=local_workspace_dir or "",
        local_output_dir=local_output_dir,
        session_id=session_id,
        family=job.get("algorithm_family", "unknown"),
        job_id=job.get("id", "remote-job"),
        command_template=command_template,
        checkpoint_path=checkpoint_path,
        input_path=input_path,
        source_path=source_path,
        auto_colmap=auto_colmap,
        dataset_name=dataset_name,
        remote_dataset_id=remote_dataset_id,
        remote_dataset_path=remote_dataset_path,
        use_existing_remote_dataset=use_existing_remote_dataset,
        training_args=training_args or {},
        command_override=command_override,
        remote_run_key=remote_run_key,
        post_train_config=post_train_config or {},
        log_callback=on_log,
        stage_callback=on_stage,
        cancel_checker=lambda: bool(job.get("cancel_requested")),
      )
      job["remote_detached"] = True
      job["execution_backend"] = result.get("execution_backend", "tmux")
      job["safe_to_close_web"] = True
      job["monitor_state"] = result.get("monitor_state", "monitoring")
      job["remote_pid"] = result.get("remote_pid", "")
      job["remote_tmux_session"] = result.get("remote_tmux_session", job.get("remote_tmux_session", ""))
      job["remote_tmux_attach_command"] = result.get("remote_tmux_attach_command", job.get("remote_tmux_attach_command", ""))
      job["remote_run_key"] = result.get("remote_run_key", job.get("remote_run_key", ""))
      job["tmux_available"] = result.get("tmux_available", True)
      job["tmux_install"] = result.get("tmux_install", job.get("tmux_install", {}))
      job["remote_job_dir"] = result.get("remote_job_dir", "")
      job["remote_run_script"] = result.get("remote_run_script", "")
      job["remote_log_path"] = result.get("remote_log_path", "")
      job["remote_status_path"] = result.get("remote_status_path", "")
      job["remote_exit_code_path"] = result.get("remote_exit_code_path", "")
      job["remote_output_dir"] = result.get("remote_output_dir", "")
      job["remote_log_cursor"] = 0
      job["remote_result"] = {
        "remote_workspace_dir": result.get("remote_workspace_dir", ""),
        "remote_output_dir": result.get("remote_output_dir", ""),
        "remote_dataset_id": result.get("remote_dataset_id", ""),
        "remote_dataset_name": result.get("remote_dataset_name", ""),
        "remote_dataset_workspace": result.get("remote_dataset_workspace", ""),
        "upload": result.get("upload", {}),
        "download": result.get("download", {}),
      }
      update_artifacts()
      if job.get("cancel_requested"):
        job["status"] = "canceled"
        job["remote_stage"] = "canceled"
      else:
        job["status"] = "running"
        job["remote_stage"] = "remote_detached_running"
        append_job_log_line(job, "stdout", "[Tmux] Safe to close page. Remote training will continue inside the tmux session.\n")
        persist_job_state(job)
        start_remote_monitor_thread(job, remote_config)
    except Exception as exc:  # pragma: no cover - network/runtime dependent
      LOGGER.exception("Remote job %s failed", job.get("id", ""))
      if job.get("cancel_requested") or getattr(exc, "code_hint", "") == "WGSC-JOB-CANCELED":
        job["status"] = "canceled"
        job["remote_stage"] = "canceled"
      else:
        job["status"] = "failed"
      append_job_log_line(job, "stderr", str(exc))
      merged_stderr = f"{job.get('stderr', '').rstrip()}\n{exc}".strip()
      job["stderr"] = merged_stderr[-12000:]
    finally:
      if not job.get("remote_detached") or is_job_terminal(job):
        job["finished_at"] = time.time()
      persist_job_state(job)

  threading.Thread(target=runner, daemon=True).start()


def analysis_export_csv(job_ids: list[str]) -> str:
  rows: list[Dict[str, Any]] = []
  for job_id in job_ids:
    job = JOBS.get(job_id)
    if not job:
      raise ValueError(f"Unknown job id: {job_id}")
    enriched = enrich_job(job)
    metrics = enriched.get("metrics") if isinstance(enriched.get("metrics"), dict) else {}
    sources = enriched.get("analysis_metric_sources") if isinstance(enriched.get("analysis_metric_sources"), dict) else {}
    psnr_source = sources.get("psnr") or sources.get("psnr_db") or ""
    ssim_source = sources.get("ssim") or ""
    lpips_source = sources.get("lpips") or ""
    if psnr_source != "artifact:result.json":
      psnr_source = ""
    if ssim_source != "artifact:result.json":
      ssim_source = ""
    if lpips_source != "artifact:result.json":
      lpips_source = ""
    psnr_value = (metrics.get("psnr_db") or metrics.get("psnr") or "") if psnr_source else ""
    ssim_value = (metrics.get("ssim") or "") if ssim_source else ""
    lpips_value = (metrics.get("lpips") or "") if lpips_source else ""
    rows.append({
      "job_id": enriched.get("id", ""),
      "algorithm_family": enriched.get("algorithm_family", ""),
      "status": enriched.get("status", ""),
      "operation": enriched.get("operation", ""),
      "created_at": enriched.get("created_at", ""),
      "dataset": enriched.get("dataset", ""),
      "output_dir": enriched.get("output_dir", ""),
      "result_path": enriched.get("result_path", ""),
      "psnr_db": psnr_value,
      "ssim": ssim_value,
      "lpips": lpips_value,
      "size_mb": metrics.get("size_mb", "") if sources.get("size_mb") else "",
      "psnr_source": psnr_source,
      "ssim_source": ssim_source,
      "lpips_source": lpips_source,
      "size_source": sources.get("size_mb", ""),
      "metrics_csv_url": enriched.get("metrics_csv_url", ""),
      "logs_download_url": enriched.get("logs_download_url", ""),
    })

  buffer = io.StringIO()
  writer = csv.DictWriter(buffer, fieldnames=ANALYSIS_EXPORT_COLUMNS, extrasaction="ignore")
  writer.writeheader()
  writer.writerows(rows)
  return buffer.getvalue()


def _write_probe(path: Path) -> tuple[bool, str]:
  probe = path / f".wgsc_write_probe_{uuid.uuid4().hex}"
  try:
    path.mkdir(parents=True, exist_ok=True)
    probe.write_text("ok", encoding="utf-8")
    probe.unlink(missing_ok=True)
    return True, ""
  except Exception as exc:
    return False, str(exc)


def detect_cuda_toolkit() -> Dict[str, Any]:
  cuda_home = os.environ.get("CUDA_HOME") or os.environ.get("CUDA_PATH") or ""
  nvcc_path = which("nvcc") or ""

  if cuda_home:
    try:
      cuda_home = str(Path(cuda_home).resolve())
    except Exception:
      cuda_home = str(cuda_home)

  if cuda_home and not nvcc_path:
    nvcc_candidate = Path(cuda_home) / "bin" / ("nvcc.exe" if os.name == "nt" else "nvcc")
    if nvcc_candidate.exists():
      nvcc_path = str(nvcc_candidate.resolve())

  if not cuda_home and os.name == "nt":
    for candidate in (
      Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.1"),
      Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.4"),
      Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v11.8"),
    ):
      if candidate.exists():
        cuda_home = str(candidate.resolve())
        if not nvcc_path:
          nvcc_candidate = candidate / "bin" / "nvcc.exe"
          if nvcc_candidate.exists():
            nvcc_path = str(nvcc_candidate.resolve())
        break

  cuda_home_exists = bool(cuda_home and Path(cuda_home).exists())
  toolkit_ready = bool(cuda_home_exists and nvcc_path)
  return {
    "cuda_home": cuda_home,
    "cuda_home_exists": cuda_home_exists,
    "nvcc_path": nvcc_path,
    "toolkit_ready": toolkit_ready,
  }


def adapter_requirement_spec(adapter: Dict[str, Any]) -> Dict[str, Any]:
  requirements = adapter.get("requirements", {}) or {}
  python_modules = [
    str(name).strip()
    for name in requirements.get("python_modules", [])
    if str(name).strip()
  ]
  install_commands = requirements.get("install_commands", {}) or {}

  if adapter.get("family") == "gaussian-splatting-lightning":
    if not python_modules:
      python_modules = [
        "torch",
        "lightning",
        "torchvision",
        "jsonargparse",
        "wandb",
        "viser",
        "plyfile",
        "PIL",
        "cv2",
        "mediapy",
        "diff_gaussian_rasterization",
        "simple_knn",
        "gsplat",
      ]
    if not install_commands:
      install_commands = {
        "cuda121": [
          "conda create -y -n gspl python=3.10 pip",
          "conda activate gspl",
          "conda install -y -c pytorch -c nvidia -c conda-forge pytorch==2.2.2 torchvision==0.17.2 torchaudio==2.2.2 pytorch-cuda=12.1 numpy=1.26.* pillow=10.* plyfile=1.1.* tqdm matplotlib pyyaml",
          "python -m pip install \"lightning[pytorch-extra]==2.3.*\" \"pytorch-lightning==2.3.*\" \"jsonargparse[signatures]\" \"bitsandbytes==0.45.*\" wandb tensorboard viser==0.2.3 mediapy==1.2.2 opencv-python-headless==4.10.* splines==0.3.0",
          "TORCH_CUDA_ARCH_LIST=8.9 CUDA_HOME=/usr/local/cuda-12.1 python -m pip install --no-build-isolation git+https://github.com/graphdeco-inria/diff-gaussian-rasterization.git@59f5f77e3ddbac3ed9db93ec2cfe99ed6c5d121d",
          "TORCH_CUDA_ARCH_LIST=8.9 CUDA_HOME=/usr/local/cuda-12.1 python -m pip install --no-build-isolation git+https://github.com/yzslab/simple-knn.git@44f764299fa305faf6ec5ebd99939e0508331503",
          "TORCH_CUDA_ARCH_LIST=8.9 CUDA_HOME=/usr/local/cuda-12.1 python -m pip install --no-build-isolation git+https://github.com/yzslab/gsplat.git@c27a44d4ad72ece2c32f99702083ac3d911d4ced",
        ],
        "cpu": [
          "python -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu",
          "python -m pip install \"lightning[pytorch-extra]==2.3.*\" \"pytorch-lightning==2.3.*\" \"jsonargparse[signatures]\" wandb tensorboard plyfile==1.1.* viser==0.2.3 mediapy==1.2.2 opencv-python-headless==4.10.* splines==0.3.0",
        ],
      }

  return {
    "python_modules": python_modules,
    "install_commands": install_commands,
  }


def extract_missing_module_from_stderr(stderr_text: str) -> str:
  import re

  match = re.search(r"ModuleNotFoundError:\\s+No module named ['\"]([^'\"]+)['\"]", stderr_text or "")
  return match.group(1) if match else ""


def build_dependency_hint(adapter: Dict[str, Any], missing_module: str) -> str:
  requirement_spec = adapter_requirement_spec(adapter)
  install_commands = requirement_spec.get("install_commands", {})

  lines = [
    f"[DependencyHint] Missing module: {missing_module}",
    f"[DependencyHint] Python executable: {sys.executable}",
  ]

  cuda121_commands = install_commands.get("cuda121", [])
  cpu_commands = install_commands.get("cpu", [])
  if cuda121_commands:
    lines.append("[DependencyHint] Install commands (CUDA 12.1):")
    lines.extend(f"  {command}" for command in cuda121_commands)
  if cpu_commands:
    lines.append("[DependencyHint] Install commands (CPU-only):")
    lines.extend(f"  {command}" for command in cpu_commands)

  if missing_module in {"diff_gaussian_rasterization", "simple_knn"}:
    cuda_toolkit = detect_cuda_toolkit()
    lines.append("[DependencyHint] This module is a CUDA extension and requires CUDA Toolkit (nvcc).")
    lines.append(f"[DependencyHint] CUDA_HOME: {cuda_toolkit.get('cuda_home') or '(not set)'}")
    lines.append(f"[DependencyHint] nvcc: {cuda_toolkit.get('nvcc_path') or '(not found)'}")

  return "\n".join(lines)


def effective_auto_colmap(auto_colmap: Any, use_existing_remote_dataset: Any) -> bool:
  return bool(auto_colmap) and not bool(use_existing_remote_dataset)


def _normalize_remote_dataset_path(path: str) -> str:
  value = str(path or "").strip().replace("\\", "/")
  while value.endswith("/"):
    value = value[:-1]
  return value


def output_dir_has_pending_remote_download(directory: Path) -> bool:
  try:
    resolved = directory.resolve()
  except OSError:
    return False
  for job in list(JOBS.values()):
    if not is_remote_download_pending(job):
      continue
    output_dir = str(job.get("output_dir") or "").strip()
    if not output_dir:
      continue
    try:
      root = Path(output_dir).resolve()
    except OSError:
      continue
    if resolved == root or root in resolved.parents:
      return True
  return False


def _artifact_score(directory: Path) -> float:
  """Use the freshest file timestamp under a candidate output dir as ranking score."""
  targets = [
    directory / "scene_manifest.json",
    directory / "latest.png",
    directory / "point_cloud.ply",
    directory / "latest.ply",
    directory / "scene.ply",
    directory / "metrics.json",
  ]
  scores: list[float] = []
  for target in targets:
    if target.exists():
      try:
        scores.append(target.stat().st_mtime)
      except OSError:
        continue
  try:
    scores.append(directory.stat().st_mtime)
  except OSError:
    pass
  return max(scores) if scores else 0.0


def result_scan_roots() -> list[tuple[Path, str, str]]:
  roots: list[tuple[Path, str, str]] = [
    ((WEB_DIR / "generated" / "runs").resolve(), "", "generated"),
    ((STREAM_DIR).resolve(), "", "generated"),
    ((WEB_DIR / "generated" / "datasets").resolve(), "", "generated"),
  ]
  for project in LOCAL_RESULT_PROJECTS:
    project_root = Path(project["root"]).resolve()
    if not project_root.exists():
      continue
    roots.append((project_root, str(project["family"]), "project"))
  return roots


def discover_runtime_results(*, limit: int = 30, max_scan_dirs: int = 1800) -> list[Dict[str, Any]]:
  """Scan local server output roots and return mountable runtime artifacts."""
  safe_limit = max(1, min(limit, 100))
  safe_max_scan_dirs = max(200, min(max_scan_dirs, 8000))

  discovered: Dict[str, Dict[str, Any]] = {}
  scanned_dirs = 0

  for root, family, source in result_scan_roots():
    if not root.exists() or not root.is_dir():
      continue

    for current_root, dirnames, _ in os.walk(root):
      dirnames[:] = [
        dirname for dirname in dirnames
        if dirname not in RESULT_SCAN_EXCLUDED_DIRS and not dirname.startswith(".")
      ]
      scanned_dirs += 1
      if scanned_dirs > safe_max_scan_dirs:
        break
      directory = Path(current_root)
      if output_dir_has_pending_remote_download(directory):
        continue
      artifact = load_result_path(str(directory), family=family)
      if not artifact.get("ok"):
        continue

      output_dir = str(directory.resolve())
      asset_path = str(artifact.get("resolved_path") or output_dir)
      try:
        score = Path(asset_path).stat().st_mtime
      except OSError:
        score = _artifact_score(directory)
      item = {
        "id": f"discovered::{uuid.uuid5(uuid.NAMESPACE_URL, output_dir).hex[:12]}",
        "source": source,
        "output_dir": output_dir,
        "resolved_path": asset_path,
        "type": artifact.get("type", ""),
        "family": artifact.get("family", family),
        "representation": artifact.get("representation") or ("sg" if str(artifact.get("viewer_url", "")).find("/viewers/sg.html") >= 0 else "sh"),
        "score": score,
      }
      for key in (*ARTIFACT_RESULT_FIELDS, "reason"):
        if artifact.get(key):
          item[key] = artifact[key]

      metrics = read_runtime_artifacts(str(directory)).get("metrics")
      if isinstance(metrics, dict):
        item["metrics"] = metrics

      discovered.setdefault(asset_path, item)

    if scanned_dirs > safe_max_scan_dirs:
      break

  ordered = sorted(discovered.values(), key=lambda value: float(value.get("score", 0.0)), reverse=True)
  return ordered[:safe_limit]
