from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List


ROOT_DIR = Path(__file__).resolve().parents[2]
ADAPTER_CONFIG_PATH = ROOT_DIR / "web" / "config" / "algorithm_adapters.json"
ADAPTER_OVERRIDE_PATH = ROOT_DIR / "web" / "config" / "algorithm_adapters.override.json"


def _load_json(path: Path) -> Dict[str, Any]:
  if not path.exists():
    return {}
  return json.loads(path.read_text(encoding="utf-8"))


def load_adapter_overrides() -> Dict[str, Dict[str, Any]]:
  data = _load_json(ADAPTER_OVERRIDE_PATH)
  return data.get("overrides", {})


def save_adapter_overrides(overrides: Dict[str, Dict[str, Any]]) -> None:
  ADAPTER_OVERRIDE_PATH.write_text(
    json.dumps({"version": "0.2.0", "overrides": overrides}, indent=2, ensure_ascii=False),
    encoding="utf-8",
  )


def load_adapter_registry() -> Dict[str, Dict[str, Any]]:
  data = _load_json(ADAPTER_CONFIG_PATH)
  adapters = data.get("adapters", [])
  overrides = load_adapter_overrides()
  registry = {}
  for adapter in adapters:
    family = adapter["family"]
    merged = dict(adapter)
    override = overrides.get(family, {})
    merged.update(override)
    registry[family] = merged
  return registry


def list_adapters() -> List[Dict[str, Any]]:
  return list(load_adapter_registry().values())


def get_adapter(family: str) -> Dict[str, Any] | None:
  return load_adapter_registry().get(family)


def supported_operations(adapter: Dict[str, Any]) -> List[str]:
  return [
    name for name, config in adapter.get("operations", {}).items()
    if config.get("enabled")
  ]


# Operations that run algorithm code on the training server; they need a repo_path
# (where the administrator placed the source) to produce a runnable command.
REMOTE_EXECUTION_OPERATIONS = {
  "train",
  "render",
  "metrics",
  "compress",
  "decompress",
  "encode",
  "decode",
}


def validate_adapter(adapter: Dict[str, Any]) -> Dict[str, Any]:
  repo_path = adapter.get("repo_path", "")
  default_cwd = adapter.get("default_cwd", "")
  repo_exists = bool(repo_path) and Path(repo_path).exists()
  cwd_exists = bool(default_cwd) and Path(default_cwd).exists()
  suggested_cwd = default_cwd or repo_path
  operations = supported_operations(adapter)
  templates = {
    name: bool((adapter.get("operations", {}).get(name, {}) or {}).get("template"))
    for name in operations
  }
  runnable_operations = [name for name in operations if templates.get(name)]
  warnings: List[str] = []
  if repo_path and not repo_exists:
    warnings.append("Configured repo_path does not exist on this machine.")
  if default_cwd and not cwd_exists:
    warnings.append("Configured default_cwd does not exist on this machine.")
  remote_execution_ops = [name for name in operations if name in REMOTE_EXECUTION_OPERATIONS]
  missing_repo = bool(remote_execution_ops) and not repo_path
  if missing_repo:
    warnings.append(
      "Remote execution operations enabled but repo_path is empty; "
      "runs would fail. Fill repo_path with the server-side source path first."
    )
  return {
    "family": adapter["family"],
    "repo_path": repo_path,
    "default_cwd": default_cwd,
    "repo_exists": repo_exists,
    "cwd_exists": cwd_exists,
    "suggested_cwd": suggested_cwd,
    "operations": operations,
    "runnable_operations": runnable_operations,
    "warnings": warnings,
    # Capability-first readiness: adapter is usable if it exposes at least one enabled
    # operation, AND that operation can actually run (execution ops need a repo_path).
    "is_ready": bool(operations) and not missing_repo,
  }


def update_adapter_override(family: str, patch: Dict[str, Any]) -> Dict[str, Any]:
  overrides = load_adapter_overrides()
  current = dict(overrides.get(family, {}))
  for key, value in patch.items():
    if value is None:
      current.pop(key, None)
    else:
      current[key] = value
  overrides[family] = current
  save_adapter_overrides(overrides)
  return load_adapter_registry()[family]
