"""Result discovery, PLY/image parsing, metric extraction, and path resolution.

Extracted from api_server.py (v0.3 module split): everything that reads
training output directories, classifies PLY payloads, computes metrics, and
resolves workspace paths. Pure filesystem logic — no job state, no HTTP.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from web.server.adapter_registry import get_adapter
from web.server.web_security import (
  LOGGER,
  STREAM_DIR,
  ROOT_DIR,
  WEB_DIR,
  path_is_inside,
  result_path_is_allowed,
)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}
PLY_EXTENSIONS = {".ply"}
PLY_HEADER_READ_BYTES = 256 * 1024
PLY_RENDER_LABELS = {
  "gaussian-sh": "Gaussian SH PLY",
  "gaussian-sg": "Spherical Gaussian PLY",
  "anchor-gaussian": "Anchor Gaussian PLY",
  "rgb-point-cloud": "RGB Point Cloud",
  "unknown-ply": "PLY 3D Model",
}
METRIC_NUMBER_PATTERN = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"

ARTIFACT_RESULT_FIELDS = (
  "manifest_url",
  "viewer_url",
  "point_cloud_url",
  "result_url",
  "result_urls",
  "render_images",
  "render_image_count",
  "render_format",
  "render_label",
  "viewer_renderer",
  "file_size",
  "hac_plus",
)

RESULT_SCAN_EXCLUDED_DIRS = {
  ".git",
  "__pycache__",
  "assets",
  "docs",
  "node_modules",
  "submodules",
  "SIBR_viewers",
}
LOCAL_RESULT_PROJECTS = [
  {
    "family": "compgs",
    "label": "CompGS",
    "root": ROOT_DIR / "CompGS-main",
    "representation": "sh",
  },
  {
    "family": "contextgs",
    "label": "ContextGS",
    "root": ROOT_DIR / "ContextGS-main",
    "representation": "sh",
  },
  {
    "family": "fcgs",
    "label": "FCGS",
    "root": ROOT_DIR / "FCGS-main",
    "representation": "sh",
  },
  {
    "family": "hac-plus-plus",
    "label": "HAC++",
    "root": ROOT_DIR / "HAC-plus-main",
    "representation": "sh",
  },
  {
    "family": "megs2",
    "label": "MEGS2",
    "root": ROOT_DIR / "MEGS-2-main",
    "representation": "sg",
  },
  {
    "family": "scaffold-gs",
    "label": "Scaffold-GS",
    "root": ROOT_DIR / "Scaffold-GS-main",
    "representation": "sh",
  },
  {
    "family": "reduced-3dgs",
    "label": "Reduced 3DGS",
    "root": ROOT_DIR / "reduced-3dgs-main",
    "representation": "sh",
  },
  {
    "family": "gaussianpro",
    "label": "GaussianPro",
    "root": ROOT_DIR / "GaussianPro-version1.0",
    "representation": "sh",
  },
  {
    "family": "atomgs",
    "label": "AtomGS",
    "root": ROOT_DIR / "AtomGS-main",
    "representation": "sh",
  },
]


def _metric_number_text(value: float) -> str:
  if not math.isfinite(value):
    return ""
  return f"{value:.12g}"


def _coerce_metric_number(value: Any) -> float | None:
  if isinstance(value, bool) or value is None:
    return None
  if isinstance(value, (int, float)):
    number = float(value)
    return number if math.isfinite(number) else None
  match = re.search(METRIC_NUMBER_PATTERN, str(value).replace(",", ""))
  if not match:
    return None
  try:
    number = float(match.group(0))
  except ValueError:
    return None
  return number if math.isfinite(number) else None


def _size_unit_to_mb(number: float, unit: str) -> float:
  normalized = unit.lower()
  if normalized in {"b", "byte", "bytes"}:
    return number / 1024 / 1024
  if normalized in {"kb", "kib"}:
    return number / 1024
  if normalized in {"gb", "gib"}:
    return number * 1024
  return number


def _coerce_size_mb(value: Any, key_path: str = "") -> float | None:
  text = str(value or "").replace(",", "")
  unit_match = re.search(
    rf"({METRIC_NUMBER_PATTERN})\s*(b|bytes?|kb|kib|mb|mib|gb|gib)\b",
    text,
    flags=re.IGNORECASE,
  )
  if unit_match:
    try:
      return _size_unit_to_mb(float(unit_match.group(1)), unit_match.group(2))
    except ValueError:
      return None

  number = _coerce_metric_number(value)
  if number is None:
    return None
  key_id = re.sub(r"[^a-z0-9]+", "", key_path.lower())
  if "bytes" in key_id or key_id.endswith("byte") or "filesize" in key_id:
    return number / 1024 / 1024
  if "gb" in key_id or "gib" in key_id:
    return number * 1024
  if "kb" in key_id or "kib" in key_id:
    return number / 1024
  if number > 1024 * 1024 and any(token in key_id for token in ("size", "file", "byte")):
    return number / 1024 / 1024
  return number


def _flatten_metric_entries(payload: Any, prefix: str = "", depth: int = 0) -> list[tuple[str, Any]]:
  if payload is None or depth > 6:
    return []
  if isinstance(payload, dict):
    entries: list[tuple[str, Any]] = []
    for key, value in payload.items():
      path = f"{prefix}.{key}" if prefix else str(key)
      entries.extend(_flatten_metric_entries(value, path, depth + 1))
    return entries
  if isinstance(payload, list):
    entries: list[tuple[str, Any]] = []
    for item in payload:
      entries.extend(_flatten_metric_entries(item, prefix, depth + 1))
    return entries
  return [(prefix, payload)]


def _metric_path_id(path: str) -> str:
  return re.sub(r"[^a-z0-9]+", "", path.lower())


def _metric_has_token(path: str, token: str) -> bool:
  return bool(re.search(rf"(^|[^a-z0-9]){re.escape(token)}([^a-z0-9]|$)", path, flags=re.IGNORECASE))


def _analysis_metric_score(path: str, key: str) -> int:
  key_id = _metric_path_id(path)
  if not key_id:
    return 0
  if key == "psnr":
    return 100 if _metric_has_token(path, "psnr") or "psnrdb" in key_id else 0
  if key == "ssim":
    if _metric_has_token(path, "ssim"):
      return 100
    if key_id.endswith("ssim") and "msssim" not in key_id:
      return 70
    return 0
  if key == "lpips":
    return 100 if _metric_has_token(path, "lpips") else 0
  if key == "size_mb":
    is_component_size = bool(re.search(r"feat|feature|offset|opacity|scaling|rotation|mask|anchor", key_id))
    if key_id in {"ttlsizemb", "totalsizemb", "totalmodelsize", "totalmodelsizeinmb"}:
      return 140
    if ("totalsize" in key_id or "ttlsize" in key_id) and "mb" in key_id and not is_component_size:
      return 135
    if key_id in {"sizemb", "sizeinmb", "modelmb", "modelsizeinmb"}:
      return 120
    if any(token in key_id for token in ("sizemb", "modelsize", "compressedsize")):
      return 110
    if "bitstream" in key_id and "size" in key_id:
      return 105
    if any(token in key_id for token in ("filesize", "storagesize", "disksize")) and "image" not in key_id:
      return 95
    if is_component_size and "size" in key_id:
      return 50
    if "size" in key_id and re.search(r"mb|mib|byte|bytes|gb|gib|kb|kib|ply|checkpoint|ckpt|model|storage|disk|result|pointcloud", key_id):
      return 75
    return 0
  return 0


def infer_analysis_metrics(payload: Any) -> Dict[str, Any]:
  metrics, _ = infer_analysis_metrics_with_sources(payload)
  return metrics


def infer_analysis_metrics_with_sources(payload: Any, source_prefix: str = "") -> tuple[Dict[str, Any], Dict[str, str]]:
  entries = [
    (path, value)
    for path, value in _flatten_metric_entries(payload)
    if str(value or "").strip() and str(value).strip() != "-"
  ]
  inferred: Dict[str, Any] = {}
  sources: Dict[str, str] = {}
  for key in ("psnr", "ssim", "lpips", "size_mb"):
    scored = [
      (_analysis_metric_score(path, key), path, value)
      for path, value in entries
      if _analysis_metric_score(path, key) > 0
    ]
    if not scored:
      continue
    best_score = max(score for score, _, _ in scored)
    best = [(path, value) for score, path, value in scored if score == best_score]
    numbers = [
      _coerce_size_mb(value, path) if key == "size_mb" else _coerce_metric_number(value)
      for path, value in best
    ]
    numbers = [value for value in numbers if value is not None and math.isfinite(value)]
    if numbers:
      inferred[key] = _metric_number_text(sum(numbers) / len(numbers))
    else:
      inferred[key] = best[-1][1]
    source_path = best[0][0]
    sources[key] = f"{source_prefix}.{source_path}" if source_prefix and source_path else source_path or source_prefix
  return inferred, sources


def merge_analysis_metric(
  metrics: Dict[str, Any],
  sources: Dict[str, str],
  key: str,
  value: Any,
  source: str,
  *,
  overwrite: bool = False,
) -> None:
  if value is None:
    return
  text = str(value).strip()
  if not text or text == "-":
    return
  if key in metrics and str(metrics.get(key, "")).strip() and not overwrite:
    return
  number = _coerce_size_mb(value, key) if key == "size_mb" else _coerce_metric_number(value)
  metrics[key] = _metric_number_text(number) if number is not None else value
  sources[key] = source


def _last_number_match(regexes: list[str], text: str) -> str:
  for regex in regexes:
    matches = re.findall(regex, text, flags=re.IGNORECASE)
    if matches:
      value = matches[-1]
      if isinstance(value, tuple):
        value = next((item for item in value if item), "")
      return str(value)
  return ""


def extract_job_metrics(text: str) -> Dict[str, Any]:
  metrics: Dict[str, Any] = {}
  number = METRIC_NUMBER_PATTERN
  patterns = {
    "iter": [
      r"\biter(?:ation)?[:=\s]+([0-9]+)\b",
      r"\b([0-9]+)\s*/\s*[0-9]+\b",
    ],
    "loss": [
      rf"\bloss[:=\s]+({number})\b",
    ],
    "lpips": [
      rf"\blpips[:=\s]+({number})\b",
    ],
  }

  for key, regexes in patterns.items():
    value = _last_number_match(regexes, text)
    if value:
      metrics[key] = value

  size_patterns = [
    rf"(?:^|[^a-z0-9])(?:ttl[_ -]*)?size[_ -]*mb\s*[:=]\s*({number})\b",
    rf"(?:^|[^a-z0-9])(?:total[_ -]*)?size[_ -]*mb\s*[:=]\s*({number})\b",
    rf"\b(?:size\s*\(?\s*mb\s*\)?|size_mb|sizemb|model_size_mb|model\s+size\s+mb)[:=\s]+({number})\b",
    rf"(?:^|[^a-z0-9])(?:[a-z0-9]+[_ -]+)*size[_ -]*mb\s*[:=]\s*({number})\b",
    rf"\b(?:model|checkpoint|ckpt|compressed|compression|file|ply|point[_ -]?cloud|storage|disk|result|bitstreams?)[^\n]{{0,60}}?\b({number})\s*(b|bytes?|kb|kib|mb|mib|gb|gib)\b",
    rf"\b({number})\s*(b|bytes?|kb|kib|mb|mib|gb|gib)\s+(?:model|checkpoint|ckpt|compressed|file|ply|point[_ -]?cloud|storage|disk|result|bitstreams?|size)\b",
  ]
  for regex in size_patterns:
    matches = re.findall(regex, text, flags=re.IGNORECASE)
    if not matches:
      continue
    match = matches[-1]
    if isinstance(match, tuple):
      value = match[0]
      unit = match[1] if len(match) > 1 and match[1] else "mb"
    else:
      value = match
      unit = "mb"
    try:
      metrics["size_mb"] = _metric_number_text(_size_unit_to_mb(float(value), unit))
    except ValueError:
      pass
    break

  return metrics


def viewer_renderer_for_format(render_format: str | None = None, representation: str | None = None) -> str:
  normalized_format = str(render_format or "").strip().lower()
  if normalized_format == "gaussian-sg":
    return "sg"
  if normalized_format in {"gaussian-sh", "anchor-gaussian", "rgb-point-cloud"}:
    return "sh"
  return "sg" if (representation or "").lower() == "sg" else "sh"


def build_viewer_url(relative_url: str, representation: str | None = None, render_format: str | None = None) -> str:
  renderer = viewer_renderer_for_format(render_format, representation)
  return f"/web/viewers/{renderer}.html?url={relative_url}"


def file_url_for_path(path: Path, cache_group: str = "files") -> str:
  resolved = path.resolve()
  if path_is_inside(resolved, WEB_DIR):
    # Static serving is rooted at WEB_DIR (see ApiHandler.translate_path).
    return "/" + str(resolved.relative_to(ROOT_DIR)).replace("\\", "/")

  cache_dir = WEB_DIR / "generated" / "result_cache" / cache_group
  cache_dir.mkdir(parents=True, exist_ok=True)
  stat = resolved.stat()
  suffix = resolved.suffix.lower()
  digest = uuid.uuid5(
    uuid.NAMESPACE_URL,
    f"{resolved}:{stat.st_mtime_ns}:{stat.st_size}",
  ).hex[:12]
  cache_path = cache_dir / f"{resolved.stem}_{digest}{suffix}"
  if not cache_path.exists():
    try:
      os.link(resolved, cache_path)
    except OSError:
      shutil.copy2(resolved, cache_path)
  return "/" + str(cache_path.relative_to(ROOT_DIR)).replace("\\", "/")


def image_file_paths(directory: Path) -> list[Path]:
  if not directory.exists() or not directory.is_dir():
    return []
  files = [
    path
    for path in directory.iterdir()
    if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
  ]
  return sorted(files, key=lambda path: (path.stat().st_mtime, path.name))


def file_brief(path: Path) -> Dict[str, Any]:
  stat = path.stat()
  return {
    "name": path.name,
    "path": str(path.resolve()),
    "url": file_url_for_path(path),
    "size": stat.st_size,
    "mtime": stat.st_mtime,
  }


def read_json_if_present(path: Path) -> Dict[str, Any]:
  if not path.exists() or not path.is_file():
    return {}
  try:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}
  except Exception:
    return {}


def _normalize_slug(value: str, *, fallback: str) -> str:
  text = str(value or "").strip().lower()
  if not text:
    return fallback
  normalized = "".join(ch if (ch.isalnum() or ch in {"-", "_"}) else "-" for ch in text)
  while "--" in normalized:
    normalized = normalized.replace("--", "-")
  normalized = normalized.strip("-_")
  return normalized[:48] or fallback


def resolve_workspace_path(path_value: str | None) -> str:
  if not path_value:
    return ""
  raw = str(path_value).strip().strip('"').strip("'")
  if not raw:
    return ""

  candidate = Path(raw)
  if candidate.is_absolute():
    return str(candidate.resolve())

  # Prefer web-relative resolution first because existing adapter JSON uses paths like ../MEGS-2-main.
  web_relative = (WEB_DIR / raw).resolve()
  root_relative = (ROOT_DIR / raw).resolve()
  if web_relative.exists():
    return str(web_relative)
  if root_relative.exists():
    return str(root_relative)
  return str(web_relative)


def resolve_project_path(path_value: str | None) -> str:
  if not path_value:
    return ""
  raw = str(path_value).strip().strip('"').strip("'")
  if not raw:
    return ""

  if raw.startswith("/web/"):
    return str((ROOT_DIR / raw.lstrip("/")).resolve())
  if raw.startswith("web/"):
    return str((ROOT_DIR / raw).resolve())

  return resolve_workspace_path(raw)


def default_run_output_dir(session_id: str, family: str) -> str:
  return str((WEB_DIR / "generated" / "runs" / session_id / family).resolve())


def remote_job_local_output_dir(session_id: str, family: str, job_id: str) -> str:
  safe_session = _normalize_slug(str(session_id or "").strip(), fallback="session")
  safe_family = _normalize_slug(str(family or "").strip(), fallback="algorithm")
  safe_job = _normalize_slug(str(job_id or "").strip(), fallback="job")
  return str((WEB_DIR / "generated" / "runs" / safe_session / safe_family / safe_job / "output").resolve())


def project_for_family(family: str | None) -> Dict[str, Any] | None:
  normalized = str(family or "").strip().lower()
  if not normalized:
    return None
  for project in LOCAL_RESULT_PROJECTS:
    if project["family"] == normalized:
      return project
  return None


def infer_family_for_path(path: Path, explicit_family: str | None = None) -> str:
  if explicit_family:
    return str(explicit_family).strip()
  resolved = path.resolve()
  for project in LOCAL_RESULT_PROJECTS:
    root = Path(project["root"]).resolve()
    if root.exists() and path_is_inside(resolved, root):
      return str(project["family"])
  path_parts = {part.lower() for part in resolved.parts}
  for project in LOCAL_RESULT_PROJECTS:
    if str(project["family"]).lower() in path_parts:
      return str(project["family"])
  return ""


def representation_for_family(family: str | None, fallback: str | None = None) -> str:
  if fallback:
    return fallback
  project = project_for_family(family)
  if project:
    return str(project.get("representation") or "sh")
  adapter = get_adapter(str(family or ""))
  return str((adapter or {}).get("representation") or "sh")


def is_image_file(path: Path) -> bool:
  return path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS


def is_ply_file(path: Path) -> bool:
  return path.is_file() and path.suffix.lower() in PLY_EXTENSIONS


def read_ply_header_text(path: Path, max_bytes: int = PLY_HEADER_READ_BYTES) -> str:
  if not is_ply_file(path):
    return ""
  data = b""
  try:
    with path.open("rb") as handle:
      while len(data) < max_bytes:
        chunk = handle.read(min(8192, max_bytes - len(data)))
        if not chunk:
          break
        data += chunk
        if b"end_header" in data.lower():
          break
  except OSError:
    return ""
  marker_index = data.lower().find(b"end_header")
  if marker_index >= 0:
    data = data[: marker_index + len(b"end_header")]
  try:
    return data.decode("ascii", errors="ignore")
  except Exception:
    return ""


def ply_header_property_names(path: Path) -> set[str]:
  properties: set[str] = set()
  for raw_line in read_ply_header_text(path).splitlines():
    line = raw_line.strip()
    if not line.startswith("property "):
      continue
    parts = line.split()
    if len(parts) >= 3:
      properties.add(parts[-1].lower())
  return properties


def classify_ply_render_format(path: Path) -> str:
  properties = ply_header_property_names(path)
  if not properties:
    return "unknown-ply"

  has_sg = any(
    name == "sg_axis_count"
    or name.startswith("sg_")
    or name.startswith("rgb_base_")
    for name in properties
  )
  if has_sg:
    return "gaussian-sg"

  has_gaussian_sh = (
    any(name.startswith("scale_") for name in properties)
    and any(name.startswith("rot_") for name in properties)
    and "opacity" in properties
    and (
      any(name.startswith("f_dc_") for name in properties)
      or any(name.startswith("f_rest_") for name in properties)
    )
  )
  if has_gaussian_sh:
    return "gaussian-sh"

  has_anchor_gaussian = (
    {"x", "y", "z", "opacity"}.issubset(properties)
    and any(name.startswith("scale_") for name in properties)
    and any(name.startswith("rot_") for name in properties)
    and (
      any(name.startswith("f_anchor_feat_") for name in properties)
      or any(name.startswith("f_offset_") for name in properties)
      or any(name.startswith("f_mask_") for name in properties)
    )
  )
  if has_anchor_gaussian:
    return "anchor-gaussian"

  has_xyz = {"x", "y", "z"}.issubset(properties)
  has_rgb = {"red", "green", "blue"}.issubset(properties) or {"r", "g", "b"}.issubset(properties)
  if has_xyz and has_rgb:
    return "rgb-point-cloud"

  return "unknown-ply"


def render_label_for_format(render_format: str | None) -> str:
  return PLY_RENDER_LABELS.get(str(render_format or "").strip().lower(), PLY_RENDER_LABELS["unknown-ply"])


def representation_for_ply_format(render_format: str | None, fallback: str | None = None) -> str:
  normalized = str(render_format or "").strip().lower()
  if normalized == "gaussian-sg":
    return "sg"
  if normalized in {"gaussian-sh", "anchor-gaussian", "rgb-point-cloud"}:
    return "sh"
  return str(fallback or "sh")


def newest_file(paths: list[Path]) -> Path | None:
  existing = [path for path in paths if path.exists() and path.is_file()]
  if not existing:
    return None
  return max(existing, key=lambda item: (item.stat().st_mtime, str(item)))


def newest_image_from_globs(directory: Path, patterns: list[str]) -> Path | None:
  candidates: list[Path] = []
  for pattern in patterns:
    for image_path in directory.glob(pattern):
      if is_image_file(image_path):
        candidates.append(image_path)
  return newest_file(candidates)


def path_mtime(path: Path) -> float:
  try:
    return path.stat().st_mtime
  except OSError:
    return 0.0


def result_candidate_dirs(directory: Path, family: str | None = None) -> list[Path]:
  family = str(family or "").strip()
  if not directory.is_dir():
    return [directory]

  if family not in {"compgs", "gaussian-splatting-lightning", "hac-plus-plus"}:
    return [directory]

  nested: list[Path] = []
  if family == "compgs":
    result_markers = ("point_cloud", "eval", "eval_training", "Log")
    excluded = {"config"}
  elif family == "gaussian-splatting-lightning":
    result_markers = ("point_cloud", "checkpoints", "test", "train")
    excluded = set()
  else:
    result_markers = ("point_cloud", "bitstreams", "test", "train", "results.json", "per_view.json", "outputs.log")
    excluded = {"config"}

  for child in directory.iterdir():
    if not child.is_dir() or child.name.startswith("."):
      continue
    if child.name in RESULT_SCAN_EXCLUDED_DIRS or child.name in excluded:
      continue
    if any((child / marker).exists() for marker in result_markers):
      nested.append(child)

  nested.sort(key=lambda path: (path_mtime(path), path.name), reverse=True)
  return [directory, *nested]


def result_marker_names_for_family(family: str | None = None) -> tuple[str, ...]:
  family = str(family or "").strip()
  if family == "compgs":
    return ("point_cloud", "eval", "eval_training", "Log")
  if family == "gaussian-splatting-lightning":
    return ("point_cloud", "checkpoints", "test", "train", "results.json", "per_view.json")
  if family == "hac-plus-plus":
    return ("point_cloud", "bitstreams", "test", "train", "results.json", "per_view.json", "outputs.log")
  if family in {"gaussianpro", "atomgs"}:
    return ("point_cloud", "test", "train", "results.json", "per_view.json", "result.json")
  return ("point_cloud", "test", "train", "results.json", "per_view.json", "metrics.json")


def path_depth_from(root: Path, path: Path) -> int:
  try:
    return len(path.resolve().relative_to(root.resolve()).parts)
  except Exception:
    return 999


def add_unique_path(paths: list[Path], path: Path) -> None:
  try:
    resolved = path.resolve()
  except OSError:
    return
  if resolved not in paths:
    paths.append(resolved)


def analysis_roots_for_job(job: Dict[str, Any] | None, directory: Path, family: str | None = None) -> list[Path]:
  roots: list[Path] = []
  if directory.exists():
    add_unique_path(roots, directory)

  if not job:
    return roots

  remote_result = job.get("remote_result") if isinstance(job.get("remote_result"), dict) else {}
  dataset_candidates = [
    remote_result.get("remote_dataset_id"),
    job.get("remote_dataset_id"),
  ]
  if directory.parent.exists():
    add_unique_path(roots, directory.parent)
    for dataset_id in dataset_candidates:
      dataset_name = Path(str(dataset_id or "").strip()).name
      if dataset_name:
        candidate = directory.parent / dataset_name
        if candidate.exists():
          add_unique_path(roots, candidate)

  family_text = str(family or job.get("algorithm_family") or "").strip()
  if family_text:
    local_root = WEB_DIR / "generated" / "runs" / str(job.get("session_id") or "") / family_text
    if local_root.exists():
      add_unique_path(roots, local_root)

  return roots


def analysis_result_candidate_dirs(
  directory: Path,
  family: str | None = None,
  *,
  job: Dict[str, Any] | None = None,
  max_depth: int = 4,
  limit: int = 80,
) -> list[Path]:
  markers = result_marker_names_for_family(family)
  candidates: list[Path] = []
  for root in analysis_roots_for_job(job, directory, family):
    for candidate in result_candidate_dirs(root, family):
      if candidate.exists():
        add_unique_path(candidates, candidate)

    if not root.is_dir():
      continue
    scanned = 0
    for current_root, dirnames, filenames in os.walk(root):
      current = Path(current_root)
      depth = path_depth_from(root, current)
      if depth > max_depth:
        dirnames[:] = []
        continue
      dirnames[:] = [
        dirname for dirname in dirnames
        if dirname not in RESULT_SCAN_EXCLUDED_DIRS and not dirname.startswith(".") and dirname != "config"
      ]
      marker_found = any((current / marker).exists() for marker in markers) or (
        "results.json" in filenames or "per_view.json" in filenames or "metrics.json" in filenames
      )
      if marker_found:
        add_unique_path(candidates, current)
      scanned += 1
      if scanned >= limit:
        break

  dataset_names = []
  if job:
    remote_result = job.get("remote_result") if isinstance(job.get("remote_result"), dict) else {}
    for value in (remote_result.get("remote_dataset_id"), job.get("remote_dataset_id")):
      text = Path(str(value or "").strip()).name
      if text:
        dataset_names.append(text)

  def candidate_score(path: Path) -> tuple[int, float, str]:
    path_text = str(path)
    dataset_score = 0
    for dataset_name in dataset_names:
      if dataset_name and dataset_name in path_text:
        dataset_score = max(dataset_score, 1000)
    try:
      if path.resolve() == directory.resolve():
        dataset_score = max(dataset_score, 500)
    except Exception:
      pass
    return dataset_score, path_mtime(path), path_text

  candidates.sort(key=candidate_score, reverse=True)
  if directory.exists():
    resolved = directory.resolve()
    candidates = [resolved, *[path for path in candidates if path != resolved]]
  return candidates[:limit]


def latest_iteration_dir(point_cloud_dir: Path) -> Path | None:
  if not point_cloud_dir.exists() or not point_cloud_dir.is_dir():
    return None
  candidates: list[tuple[int, Path]] = []
  for child in point_cloud_dir.iterdir():
    if not child.is_dir() or not child.name.startswith("iteration_"):
      continue
    try:
      iteration = int(child.name.split("_")[-1])
    except ValueError:
      continue
    candidates.append((iteration, child))
  if not candidates:
    return None
  candidates.sort(key=lambda item: item[0], reverse=True)
  return candidates[0][1]


def direct_ply_candidates(directory: Path, names: list[str] | None = None) -> list[Path]:
  target_names = names or ["point_cloud.ply", "latest.ply", "scene.ply"]
  return [directory / name for name in target_names]


def result_ply_candidate_score(path: Path) -> int:
  render_format = classify_ply_render_format(path)
  score = 0
  if render_format in {"gaussian-sh", "gaussian-sg", "anchor-gaussian"}:
    score += 1000
  elif render_format == "rgb-point-cloud":
    score += 100
  else:
    score += 200

  lower_parts = [part.lower() for part in path.parts]
  lower_name = path.name.lower()
  if lower_name in {"point_cloud.ply", "point_cloud_quantised.ply", "point_cloud_quantised_half.ply"}:
    score += 300
  if "point_cloud" in lower_parts and any(part.startswith("iteration_") for part in lower_parts):
    score += 300
  if "checkpoints" in lower_parts:
    score -= 200
  if lower_name.endswith("-xyz_rgb.ply"):
    score -= 200
  return score


def best_result_ply_candidate(paths: list[Path]) -> Path | None:
  existing = [path for path in paths if is_ply_file(path)]
  if not existing:
    return None
  existing.sort(key=lambda path: (result_ply_candidate_score(path), path_mtime(path), str(path)), reverse=True)
  return existing[0]


def gslightning_checkpoint_ply_candidates(directory: Path) -> list[Path]:
  checkpoint_dir = directory / "checkpoints"
  if not checkpoint_dir.exists() or not checkpoint_dir.is_dir():
    return []
  return [path for path in checkpoint_dir.glob("*.ply") if is_ply_file(path)]


def find_result_ply_in_directory(directory: Path, names: list[str], family: str | None = None) -> Path | None:
  candidates: list[Path] = [path for path in direct_ply_candidates(directory, names) if is_ply_file(path)]

  iteration_dir = latest_iteration_dir(directory / "point_cloud")
  if iteration_dir:
    for candidate in direct_ply_candidates(iteration_dir, names):
      if is_ply_file(candidate):
        candidates.append(candidate)

  if directory.name.startswith("iteration_"):
    for candidate in direct_ply_candidates(directory, names):
      if is_ply_file(candidate):
        candidates.append(candidate)

  if str(family or "").strip() == "gaussian-splatting-lightning":
    candidates.extend(gslightning_checkpoint_ply_candidates(directory))

  return best_result_ply_candidate(candidates)


def find_result_ply(directory: Path, family: str | None = None) -> Path | None:
  family = str(family or "").strip()
  if is_ply_file(directory):
    return directory

  names = ["point_cloud.ply", "latest.ply", "scene.ply"]
  if family == "reduced-3dgs":
    names = ["point_cloud_quantised_half.ply", "point_cloud_quantised.ply", "point_cloud.ply", "latest.ply", "scene.ply"]

  if directory.is_dir():
    for candidate_dir in result_candidate_dirs(directory, family):
      result = find_result_ply_in_directory(candidate_dir, names, family)
      if result:
        return result

  return None


def find_result_manifest(directory: Path) -> Path | None:
  if directory.is_file() and directory.name == "scene_manifest.json":
    return directory
  if not directory.is_dir():
    return None
  candidate = directory / "scene_manifest.json"
  return candidate if candidate.exists() and candidate.is_file() else None


def directory_size_bytes(directory: Path) -> int:
  if not directory.exists() or not directory.is_dir():
    return 0
  total = 0
  for path in directory.rglob("*"):
    if not path.is_file():
      continue
    try:
      total += path.stat().st_size
    except OSError:
      continue
  return total


def hac_plus_output_metadata(directory: Path, family: str | None = None) -> Dict[str, Any]:
  if str(family or "").strip() != "hac-plus-plus" or not directory.is_dir():
    return {}

  candidate_dirs = result_candidate_dirs(directory, family)
  bitstream_dir = next((candidate / "bitstreams" for candidate in candidate_dirs if (candidate / "bitstreams").is_dir()), None)
  latest_ply = find_result_ply(directory, family)
  render_images = find_result_images(directory, family)

  metrics_files: Dict[str, str] = {}
  for name in ("results.json", "per_view.json", "outputs.log"):
    match = next((candidate / name for candidate in candidate_dirs if (candidate / name).is_file()), None)
    if match:
      metrics_files[name] = str(match.resolve())

  metadata: Dict[str, Any] = {
    "viewer_source": "point_cloud_ply" if latest_ply else ("render_images" if render_images else ""),
    "has_point_cloud_ply": bool(latest_ply),
    "point_cloud_ply": str(latest_ply.resolve()) if latest_ply else "",
    "has_bitstreams": bool(bitstream_dir),
    "bitstreams_dir": str(bitstream_dir.resolve()) if bitstream_dir else "",
    "bitstreams_size_bytes": directory_size_bytes(bitstream_dir) if bitstream_dir else 0,
    "render_image_count": len(render_images),
    "metrics_files": metrics_files,
  }
  return metadata


def attach_hac_plus_metadata(payload: Dict[str, Any], directory: Path, family: str | None = None) -> Dict[str, Any]:
  metadata = hac_plus_output_metadata(directory, family)
  if metadata:
    payload["hac_plus"] = metadata
  return payload


def hac_plus_failure_reason(directory: Path, family: str | None = None) -> str:
  if str(family or "").strip() != "hac-plus-plus" or not directory.is_dir():
    return ""
  log_candidates = [
    directory / "outputs.log",
    directory / "output.log",
    directory / ".wgsc_job" / "runtime.log",
  ]
  for path in log_candidates:
    if not path.is_file():
      continue
    try:
      text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
      continue
    if "only undistorted datasets (PINHOLE or SIMPLE_PINHOLE cameras) supported" in text:
      return (
        "HAC++ requires an undistorted COLMAP dataset using PINHOLE or SIMPLE_PINHOLE cameras. "
        "This run did not produce a loadable PLY because the source camera model is unsupported."
      )
  return ""


def result_image_patterns_for_family(family: str | None = None) -> list[str]:
  family = str(family or "").strip()
  if family == "compgs":
    return [
      "eval/rendered/*",
      "eval_training/rendered/*",
    ]
  if family == "reduced-3dgs":
    return [
      "test/*/renders/*",
      "train/*/renders/*",
    ]
  if family == "atomgs":
    return [
      "test/ours_*/renders/*",
      "train/ours_*/renders/*",
      "test/ours_*/rgb/*",
      "train/ours_*/rgb/*",
    ]
  return [
    "test/ours_*/renders/*",
    "train/ours_*/renders/*",
  ]


def dedupe_paths(paths: list[Path]) -> list[Path]:
  deduped: list[Path] = []
  seen: set[str] = set()
  for path in paths:
    try:
      resolved = path.resolve()
    except OSError:
      continue
    key = str(resolved)
    if key in seen:
      continue
    seen.add(key)
    deduped.append(resolved)
  return deduped


def find_result_images(directory: Path, family: str | None = None) -> list[Path]:
  if is_image_file(directory):
    return [directory.resolve()]
  if not directory.is_dir():
    return []

  patterns = result_image_patterns_for_family(family)
  candidates: list[Path] = []

  for candidate_dir in result_candidate_dirs(directory, family):
    candidates.extend(candidate_dir / f"latest{suffix}" for suffix in IMAGE_EXTENSIONS)
    for pattern in patterns:
      candidates.extend(path for path in candidate_dir.glob(pattern) if is_image_file(path))

  images = [path for path in dedupe_paths(candidates) if is_image_file(path)]
  images.sort(key=lambda path: (path_mtime(path), str(path)), reverse=True)
  return images


def find_result_image(directory: Path, family: str | None = None) -> Path | None:
  images = find_result_images(directory, family)
  return images[0] if images else None


def analysis_metric_files(candidate_dirs: list[Path]) -> list[Path]:
  files: list[Path] = []
  names = ("metrics.json", "results.json", "per_view.json")
  for candidate_dir in candidate_dirs:
    if candidate_dir.is_file() and candidate_dir.name in names:
      add_unique_path(files, candidate_dir)
      continue
    if not candidate_dir.is_dir():
      continue
    for name in names:
      metric_file = candidate_dir / name
      if metric_file.is_file():
        add_unique_path(files, metric_file)
    for eval_dir in candidate_dir.glob("eval*"):
      if not eval_dir.is_dir():
        continue
      for name in names:
        metric_file = eval_dir / name
        if metric_file.is_file():
          add_unique_path(files, metric_file)
  files.sort(key=lambda path: (path_mtime(path), str(path)), reverse=True)
  return files


def analysis_result_json_files(directory: Path, family: str | None = None, limit: int = 40) -> list[Path]:
  files: list[Path] = []
  if directory.is_file() and directory.name == "result.json":
    add_unique_path(files, directory)
    return files
  if not directory.is_dir():
    return []

  for candidate_dir in result_candidate_dirs(directory, family):
    candidate = candidate_dir / "result.json"
    if candidate.is_file():
      add_unique_path(files, candidate)

  scanned = 0
  for current_root, dirnames, filenames in os.walk(directory):
    current = Path(current_root)
    depth = path_depth_from(directory, current)
    if depth > 6:
      dirnames[:] = []
      continue
    dirnames[:] = [
      dirname for dirname in dirnames
      if dirname not in RESULT_SCAN_EXCLUDED_DIRS and not dirname.startswith(".")
    ]
    if "result.json" in filenames:
      add_unique_path(files, current / "result.json")
    scanned += 1
    if scanned >= 300:
      break

  files.sort(key=lambda path: (path_mtime(path), str(path)), reverse=True)
  return files[:limit]


def result_json_metric_values(payload: Any) -> Dict[str, Any]:
  psnr_values: list[float] = []
  ssim_values: list[float] = []
  lpips_values: list[float] = []

  def visit(value: Any, key: str = "") -> None:
    if isinstance(value, dict):
      for child_key, child_value in value.items():
        visit(child_value, str(child_key))
      return
    if isinstance(value, list):
      for child in value:
        visit(child, key)
      return
    number = _coerce_metric_number(value)
    if number is None:
      return
    key_id = _metric_path_id(key)
    if key_id in {"psnr", "psnrdb"}:
      psnr_values.append(number)
    if key_id == "ssim":
      ssim_values.append(number)
    if key_id == "lpips":
      lpips_values.append(number)

  visit(payload)
  metrics: Dict[str, Any] = {}
  if psnr_values:
    psnr = sum(psnr_values) / len(psnr_values)
    metrics["psnr"] = _metric_number_text(psnr)
    metrics["psnr_db"] = _metric_number_text(psnr)
  if ssim_values:
    metrics["ssim"] = _metric_number_text(sum(ssim_values) / len(ssim_values))
  if lpips_values:
    metrics["lpips"] = _metric_number_text(sum(lpips_values) / len(lpips_values))
  return metrics


def read_result_json_metrics(directory: Path, family: str | None = None) -> tuple[Dict[str, Any], Path | None]:
  for result_path in analysis_result_json_files(directory, family):
    try:
      payload = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception:
      continue
    metrics = result_json_metric_values(payload)
    if metrics:
      return metrics, result_path
  return {}, None


def relative_metric_source(root: Path, path: Path, suffix: str = "") -> str:
  try:
    label = str(path.resolve().relative_to(root.resolve()))
  except Exception:
    label = f"{path.parent.name}/{path.name}" if path.parent.name.startswith("eval") else path.name
  return f"{label}.{suffix}" if suffix else label


def image_pair_dirs(candidate_dirs: list[Path]) -> tuple[Path | None, Path | None]:
  for candidate_dir in candidate_dirs:
    if not candidate_dir.is_dir():
      continue
    for eval_dir in [candidate_dir, *[path for path in candidate_dir.glob("eval*") if path.is_dir()]]:
      original = eval_dir / "original"
      rendered = eval_dir / "rendered"
      if original.is_dir() and rendered.is_dir():
        return original, rendered
  return None, None


def paired_image_paths(original_dir: Path, rendered_dir: Path, limit: int = 120) -> list[tuple[Path, Path]]:
  original_by_name = {
    path.name: path for path in original_dir.iterdir()
    if path.is_file() and is_image_file(path)
  }
  pairs: list[tuple[Path, Path]] = []
  for rendered in sorted(rendered_dir.iterdir(), key=lambda path: path.name):
    if not rendered.is_file() or not is_image_file(rendered):
      continue
    original = original_by_name.get(rendered.name)
    if original:
      pairs.append((original, rendered))
    if len(pairs) >= limit:
      break
  return pairs


def compute_image_pair_metrics(original_dir: Path, rendered_dir: Path) -> Dict[str, float]:
  try:
    import numpy as np
    from PIL import Image
  except Exception:
    return {}

  pairs = paired_image_paths(original_dir, rendered_dir)
  if not pairs:
    return {}

  psnrs: list[float] = []
  ssims: list[float] = []
  c1 = 0.01 ** 2
  c2 = 0.03 ** 2
  for original_path, rendered_path in pairs:
    try:
      original = np.asarray(Image.open(original_path).convert("RGB"), dtype=np.float64) / 255.0
      rendered = np.asarray(Image.open(rendered_path).convert("RGB"), dtype=np.float64) / 255.0
    except Exception:
      continue
    if original.shape != rendered.shape:
      continue

    mse = float(np.mean((original - rendered) ** 2))
    psnrs.append(100.0 if mse <= 0 else float(10.0 * math.log10(1.0 / mse)))

    channel_ssims: list[float] = []
    for channel in range(3):
      left = original[:, :, channel]
      right = rendered[:, :, channel]
      left_mean = float(np.mean(left))
      right_mean = float(np.mean(right))
      left_var = float(np.mean((left - left_mean) ** 2))
      right_var = float(np.mean((right - right_mean) ** 2))
      covariance = float(np.mean((left - left_mean) * (right - right_mean)))
      denominator = (left_mean ** 2 + right_mean ** 2 + c1) * (left_var + right_var + c2)
      if denominator <= 0:
        continue
      channel_ssims.append(((2 * left_mean * right_mean + c1) * (2 * covariance + c2)) / denominator)
    if channel_ssims:
      ssims.append(float(max(-1.0, min(1.0, sum(channel_ssims) / len(channel_ssims)))))

  result: Dict[str, float] = {}
  if psnrs:
    result["psnr"] = sum(psnrs) / len(psnrs)
  if ssims:
    result["ssim"] = sum(ssims) / len(ssims)
  return result


def rendered_image_count(candidate_dirs: list[Path]) -> int:
  original_dir, rendered_dir = image_pair_dirs(candidate_dirs)
  if rendered_dir is None:
    return 0
  return len([path for path in rendered_dir.iterdir() if path.is_file() and is_image_file(path)])


def runtime_log_text_for_job(job: Dict[str, Any] | None) -> str:
  if not job:
    return ""
  candidates: list[Path] = []
  for key in ("log_file",):
    value = str(job.get(key) or "").strip()
    if value:
      candidates.append(Path(value))
  log_dir = str(job.get("log_dir") or "").strip()
  if log_dir:
    candidates.append(Path(log_dir) / "runtime.log")
  for path in candidates:
    if not path.is_file():
      continue
    try:
      return path.read_text(encoding="utf-8", errors="ignore")[-240000:]
    except Exception:
      continue
  return str(job.get("stdout") or "") + "\n" + str(job.get("stderr") or "")


def image_search_dirs_for_result_file(path: Path) -> list[Path]:
  if is_image_file(path):
    return [path]
  candidates = [path.parent]
  current = path.parent
  for ancestor in current.parents:
    if ancestor == ROOT_DIR.parent:
      break
    candidates.append(ancestor)
    if len(candidates) >= 5:
      break
  return dedupe_paths(candidates)


def find_result_images_for_file(path: Path, family: str | None = None) -> list[Path]:
  images: list[Path] = []
  for candidate_dir in image_search_dirs_for_result_file(path):
    images.extend(find_result_images(candidate_dir, family))
  images = dedupe_paths(images)
  images.sort(key=lambda item: (path_mtime(item), str(item)), reverse=True)
  return images


def render_image_payloads(paths: list[Path]) -> list[Dict[str, Any]]:
  items: list[Dict[str, Any]] = []
  for path in paths:
    if not is_image_file(path):
      continue
    resolved = path.resolve()
    stat = resolved.stat()
    items.append({
      "name": resolved.name,
      "url": file_url_for_path(resolved, "images"),
      "path": str(resolved),
      "file_size": stat.st_size,
      "mtime": stat.st_mtime,
    })
  return items


def attach_render_images(payload: Dict[str, Any], image_paths: list[Path]) -> Dict[str, Any]:
  image_items = render_image_payloads(image_paths)
  if not image_items:
    return payload
  image_urls = [item["url"] for item in image_items]
  payload["result_url"] = payload.get("result_url") or image_urls[0]
  payload["result_urls"] = image_urls
  payload["render_images"] = image_items
  payload["render_image_count"] = len(image_items)
  return payload


def result_payload_for_ply(path: Path, *, family: str | None = None, representation: str | None = None) -> Dict[str, Any]:
  resolved = path.resolve()
  resolved_family = infer_family_for_path(resolved, family)
  fallback_representation = representation_for_family(resolved_family, representation)
  render_format = classify_ply_render_format(resolved)
  resolved_representation = representation_for_ply_format(render_format, fallback_representation)
  viewer_renderer = viewer_renderer_for_format(render_format, resolved_representation)
  point_cloud_url = file_url_for_path(resolved, "ply")
  return {
    "ok": True,
    "type": "ply",
    "family": resolved_family,
    "representation": resolved_representation,
    "render_format": render_format,
    "render_label": render_label_for_format(render_format),
    "viewer_renderer": viewer_renderer,
    "point_cloud_url": point_cloud_url,
    "viewer_url": build_viewer_url(point_cloud_url, resolved_representation, render_format),
    "resolved_path": str(resolved),
    "path": str(resolved),
    "file_size": resolved.stat().st_size,
  }


def result_payload_for_image(path: Path, *, family: str | None = None) -> Dict[str, Any]:
  resolved = path.resolve()
  resolved_family = infer_family_for_path(resolved, family)
  image_url = file_url_for_path(resolved, "images")
  return {
    "ok": True,
    "type": "image",
    "family": resolved_family,
    "result_url": image_url,
    "resolved_path": str(resolved),
    "path": str(resolved),
    "file_size": resolved.stat().st_size,
  }


def result_payload_for_manifest(path: Path, *, family: str | None = None, representation: str | None = None) -> Dict[str, Any]:
  resolved = path.resolve()
  resolved_family = infer_family_for_path(resolved, family)
  resolved_representation = representation_for_family(resolved_family, representation)
  manifest_url = file_url_for_path(resolved, "manifests")
  return {
    "ok": True,
    "type": "manifest",
    "family": resolved_family,
    "representation": resolved_representation,
    "manifest_url": manifest_url,
    "viewer_url": f"/web/?manifest={manifest_url}",
    "resolved_path": str(resolved),
    "path": str(resolved),
    "file_size": resolved.stat().st_size,
  }


def looks_like_fcgs_bitstream_dir(directory: Path) -> bool:
  if not directory.is_dir():
    return False
  for child in directory.iterdir():
    if not child.is_dir():
      continue
    try:
      float(child.name)
    except ValueError:
      continue
    return True
  return False


def load_result_path(path_value: str, family: str | None = None, representation: str | None = None) -> Dict[str, Any]:
  resolved_text = resolve_project_path(path_value)
  resolved = Path(resolved_text).resolve() if resolved_text else Path(str(path_value or "")).expanduser().resolve()
  resolved_family = infer_family_for_path(resolved, family)
  resolved_representation = representation_for_family(resolved_family, representation)

  if not result_path_is_allowed(resolved):
    reason = (
      f"Path is outside the allowed result roots: {resolved}. "
      "Add extra directories to web/config/security.json (extra_allowed_roots) if needed."
    )
    return {
      "ok": False,
      "type": "",
      "family": resolved_family,
      "reason": reason,
      "error": reason,
      "path": str(path_value or ""),
      "resolved_path": str(resolved),
    }

  if not resolved.exists():
    reason = f"Result path does not exist: {path_value}"
    return {
      "ok": False,
      "type": "",
      "family": resolved_family,
      "reason": reason,
      "error": reason,
      "path": str(path_value or ""),
      "resolved_path": str(resolved),
    }

  if is_ply_file(resolved):
    return attach_render_images(
      result_payload_for_ply(resolved, family=resolved_family, representation=resolved_representation),
      find_result_images_for_file(resolved, resolved_family),
    )
  if is_image_file(resolved):
    return attach_render_images(result_payload_for_image(resolved, family=resolved_family), [resolved])
  if resolved.is_file() and resolved.name == "scene_manifest.json":
    return attach_render_images(
      result_payload_for_manifest(resolved, family=resolved_family, representation=resolved_representation),
      find_result_images_for_file(resolved, resolved_family),
    )
  if resolved.is_file():
    reason = f"Unsupported result file type: {resolved.suffix or resolved.name}"
    return {
      "ok": False,
      "type": "",
      "family": resolved_family,
      "reason": reason,
      "error": reason,
      "path": str(path_value or ""),
      "resolved_path": str(resolved),
    }

  render_image_paths = find_result_images(resolved, resolved_family)

  ply_path = find_result_ply(resolved, resolved_family)
  if ply_path:
    payload = attach_render_images(
      result_payload_for_ply(ply_path, family=resolved_family, representation=resolved_representation),
      render_image_paths,
    )
    return attach_hac_plus_metadata(payload, resolved, resolved_family)

  manifest_path = find_result_manifest(resolved)
  if manifest_path:
    payload = attach_render_images(
      result_payload_for_manifest(manifest_path, family=resolved_family, representation=resolved_representation),
      render_image_paths,
    )
    return attach_hac_plus_metadata(payload, resolved, resolved_family)

  if render_image_paths:
    payload = attach_render_images(
      result_payload_for_image(render_image_paths[0], family=resolved_family),
      render_image_paths,
    )
    return attach_hac_plus_metadata(payload, resolved, resolved_family)

  reason = "No supported PLY, scene manifest, or rendered image was found in this result directory."
  hac_failure_reason = hac_plus_failure_reason(resolved, resolved_family)
  if hac_failure_reason:
    reason = hac_failure_reason
  elif resolved_family == "hac-plus-plus" and hac_plus_output_metadata(resolved, resolved_family).get("has_bitstreams"):
    reason = "HAC++ bitstreams were found, but no point_cloud/iteration_*/point_cloud.ply or rendered images were found for Web preview."
  if resolved_family == "fcgs" and looks_like_fcgs_bitstream_dir(resolved):
    reason = "FCGS bitstreams were found, but no decoded PLY exists yet. Decode to point_cloud.ply, latest.ply, or scene.ply first."
  return {
    "ok": False,
    "type": "",
    "family": resolved_family,
    "reason": reason,
    "error": reason,
    "path": str(path_value or ""),
    "resolved_path": str(resolved),
  }


def load_ply_file(ply_path: str, representation: str | None = None) -> Dict[str, Any]:
  """
  Load a PLY file independently from the training flow.
  Supports absolute paths and paths relative to ROOT_DIR.
  """
  try:
    ply_file = Path(ply_path).resolve()

    # Keep path handling explicit so load failures can be explained clearly.
    if not result_path_is_allowed(ply_file):
      return {
        "ok": False,
        "error": (
          f"Path is outside the allowed result roots: {ply_file}. "
          "Add extra directories to web/config/security.json (extra_allowed_roots) if needed."
        ),
        "path": ply_path,
      }

    if not (ply_file.exists()):
      return {
        "ok": False,
        "error": f"PLY file does not exist: {ply_path}",
        "path": ply_path,
      }

    if not ply_file.suffix.lower() == ".ply":
      return {
        "ok": False,
        "error": f"File is not a PLY file: {ply_file.suffix}",
        "path": str(ply_file),
      }

    return result_payload_for_ply(ply_file, representation=representation)
  except Exception as e:
    return {
      "ok": False,
      "error": str(e),
      "path": ply_path,
    }


def newest_iteration_ply(directory: Path) -> Path | None:
  point_cloud_dir = directory / "point_cloud"
  if not point_cloud_dir.exists():
    return None
  candidates: list[tuple[int, Path]] = []
  for child in point_cloud_dir.iterdir():
    if not child.is_dir() or not child.name.startswith("iteration_"):
      continue
    try:
      iteration = int(child.name.split("_")[-1])
    except ValueError:
      continue
    ply_path = child / "point_cloud.ply"
    if ply_path.exists():
      candidates.append((iteration, ply_path))
  if not candidates:
    return None
  candidates.sort(key=lambda item: item[0], reverse=True)
  return candidates[0][1]


def newest_render_image(directory: Path) -> Path | None:
  render_roots = [
    directory / "test",
    directory / "train",
  ]
  candidates: list[tuple[float, Path]] = []
  for root in render_roots:
    if not root.exists():
      continue
    for image_path in root.glob("ours_*/renders/*.png"):
      try:
        score = image_path.stat().st_mtime
      except OSError:
        continue
      candidates.append((score, image_path))
  if not candidates:
    return None
  candidates.sort(key=lambda item: item[0], reverse=True)
  return candidates[0][1]


def compressed_artifact_size_bytes(candidate_dirs: list[Path]) -> tuple[int, str]:
  for candidate_dir in candidate_dirs:
    if not candidate_dir.is_dir():
      continue
    for bitstream_dir in [candidate_dir / "bitstreams", candidate_dir / "bitstream"]:
      if bitstream_dir.is_dir():
        size = directory_size_bytes(bitstream_dir)
        if size > 0:
          return size, f"artifact:{bitstream_dir.name}"

  suffixes = {".bin", ".npz"}
  for candidate_dir in candidate_dirs:
    if not candidate_dir.is_dir():
      continue
    total = 0
    for current_root, dirnames, filenames in os.walk(candidate_dir):
      current = Path(current_root)
      if path_depth_from(candidate_dir, current) > 3:
        dirnames[:] = []
        continue
      dirnames[:] = [
        dirname for dirname in dirnames
        if dirname not in RESULT_SCAN_EXCLUDED_DIRS and not dirname.startswith(".")
      ]
      for filename in filenames:
        path = current / filename
        lowered = filename.lower()
        if path.suffix.lower() in suffixes or "bitstream" in lowered or "compressed" in lowered:
          try:
            total += path.stat().st_size
          except OSError:
            continue
    if total > 0:
      return total, "artifact:compressed_files"
  return 0, ""


def read_runtime_artifacts(
  output_dir: str | None,
  representation: str | None = None,
  family: str | None = None,
  job: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
  if not output_dir:
    return {}
  directory = Path(output_dir).resolve()
  payload: Dict[str, Any] = {}
  metrics: Dict[str, Any] = {}
  sources: Dict[str, str] = {}
  candidate_dirs = result_candidate_dirs(directory, family)

  result_json_files = analysis_result_json_files(directory, family)
  payload["result_json_exists"] = bool(result_json_files)
  if result_json_files:
    payload["result_path"] = str(result_json_files[0].resolve())
  result_metrics, result_path = read_result_json_metrics(directory, family)
  if result_path:
    payload["result_path"] = str(result_path.resolve())
    payload["result_json_exists"] = True
    merge_analysis_metric(metrics, sources, "psnr", result_metrics.get("psnr"), "artifact:result.json", overwrite=True)
    merge_analysis_metric(metrics, sources, "psnr_db", result_metrics.get("psnr_db") or result_metrics.get("psnr"), "artifact:result.json", overwrite=True)
    merge_analysis_metric(metrics, sources, "ssim", result_metrics.get("ssim"), "artifact:result.json", overwrite=True)
    merge_analysis_metric(metrics, sources, "lpips", result_metrics.get("lpips"), "artifact:result.json", overwrite=True)

  artifact: Dict[str, Any] = {}
  for candidate_dir in candidate_dirs:
    artifact = load_result_path(str(candidate_dir), family=family, representation=representation)
    if artifact.get("ok"):
      break
  if artifact.get("ok"):
    for key in ("type", "family", "representation", "resolved_path", *ARTIFACT_RESULT_FIELDS):
      if artifact.get(key):
        payload[key] = artifact[key]

  if "size_mb" not in metrics:
    compressed_size, compressed_source = compressed_artifact_size_bytes(candidate_dirs)
    if compressed_size > 0:
      metrics.setdefault("size_bytes", compressed_size)
      merge_analysis_metric(metrics, sources, "size_mb", compressed_size / 1024 / 1024, compressed_source)

  if "size_mb" not in metrics and artifact.get("ok"):
    size_bytes = 0
    hac_plus = artifact.get("hac_plus") if isinstance(artifact.get("hac_plus"), dict) else {}
    if hac_plus:
      size_bytes = int(hac_plus.get("bitstreams_size_bytes") or 0)
    if not size_bytes:
      size_bytes = int(artifact.get("file_size") or 0)
    if size_bytes > 0:
      metrics.setdefault("size_bytes", size_bytes)
      artifact_name = Path(str(artifact.get("resolved_path") or "point_cloud.ply")).name
      merge_analysis_metric(metrics, sources, "size_mb", size_bytes / 1024 / 1024, f"artifact:{artifact_name}")

  if "size_mb" not in metrics and isinstance(job, dict):
    download = remote_download_info(job)
    size_bytes = int(download.get("remote_total_bytes") or download.get("total_bytes") or download.get("bytes") or 0)
    if size_bytes > 0:
      metrics.setdefault("size_bytes", size_bytes)
      merge_analysis_metric(metrics, sources, "size_mb", size_bytes / 1024 / 1024, "artifact:remote_download.bytes")

  if sources:
    metrics["analysis_metric_sources"] = sources
    metrics["psnr_source"] = sources.get("psnr") or sources.get("psnr_db")
    metrics["ssim_source"] = sources.get("ssim")
    metrics["lpips_source"] = sources.get("lpips")
    metrics["size_source"] = sources.get("size_mb")
  if metrics:
    payload["metrics"] = metrics
  return payload








def remote_download_info(job: Dict[str, Any]) -> Dict[str, Any]:
  remote_result = job.get("remote_result")
  if not isinstance(remote_result, dict):
    return {}
  download = remote_result.get("download")
  return download if isinstance(download, dict) else {}
