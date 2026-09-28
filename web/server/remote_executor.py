from __future__ import annotations

import json
import shutil
import shlex
import stat
import threading
import os
import re
import time
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict


ALGORITHM_EVAL_REQUIREMENTS: Dict[str, Dict[str, Any]] = {
  "vanilla-3dgs": {
    "needs_eval_for_metrics": True,
    "train_eval_arg": "--eval",
    "cli_supports_eval": True,
    "reason": "test split is required for render.py and metrics.py",
  },
  "reduced-3dgs": {
    "needs_eval_for_metrics": True,
    "train_eval_arg": "--eval",
    "cli_supports_eval": True,
    "reason": "metrics.py requires rendered test images and ground-truth test views",
  },
  "megs2": {
    "needs_eval_for_metrics": True,
    "train_eval_arg": "--eval",
    "cli_supports_eval": True,
    "reason": "ModelParams exposes eval and render.py/metrics.py need test views",
  },
  "scaffold-gs": {
    "needs_eval_for_metrics": True,
    "train_eval_arg": "--eval",
    "cli_supports_eval": True,
    "reason": "ModelParams exposes eval and metrics.py evaluates test renders",
  },
  "hac-plus-plus": {
    "needs_eval_for_metrics": True,
    "train_eval_arg": "--eval",
    "cli_supports_eval": True,
    "reason": "training/render evaluation uses held-out test cameras",
  },
  "contextgs": {
    "needs_eval_for_metrics": True,
    "train_eval_arg": "--eval",
    "cli_supports_eval": True,
    "reason": "training/test evaluation uses held-out test cameras",
  },
  "gaussianpro": {
    "needs_eval_for_metrics": True,
    "train_eval_arg": "--eval",
    "cli_supports_eval": True,
    "reason": "render.py and metrics.py need held-out test cameras for PSNR/SSIM/LPIPS",
  },
  "atomgs": {
    "needs_eval_for_metrics": True,
    "train_eval_arg": "--eval",
    "cli_supports_eval": True,
    "reason": "render.py and metrics.py need held-out test cameras for PSNR/SSIM/LPIPS",
  },
  "gaussian-splatting-lightning": {
    "needs_eval_for_metrics": True,
    "train_eval_arg": "",
    "cli_supports_eval": False,
    "reason": "inspected CLI has no top-level --eval; dataparser split parameters provide val/test views",
  },
  "compgs": {
    "needs_eval_for_metrics": False,
    "train_eval_arg": "",
    "cli_supports_eval": False,
    "reason": "CompGS uses its own Train/Test config workflow rather than a train.py --eval flag",
  },
  "fcgs": {
    "needs_eval_for_metrics": False,
    "train_eval_arg": "",
    "cli_supports_eval": False,
    "reason": "FCGS trains through the CompGS pipeline wrapper (run_fcgs_compgs_pipeline.py), not a train.py --eval flag",
  },
}


def command_has_arg(command: str, option: str) -> bool:
  try:
    return option in shlex.split(command)
  except Exception:
    return f" {option} " in f" {command} "


def looks_like_training_command(command: str) -> bool:
  lowered = f" {command.lower()} "
  return any(
    token in lowered
    for token in (
      " train.py",
      " fit ",
      " main.py fit",
      " train.py ",
      "run_compgs_training.py",
      "run_fcgs_compgs_pipeline.py",
    )
  )


def apply_training_eval_arg(family: str, command: str, *, user_disabled_eval: bool = False) -> str:
  spec = ALGORITHM_EVAL_REQUIREMENTS.get(str(family or "").strip().lower(), {})
  eval_arg = str(spec.get("train_eval_arg") or "").strip()
  if (
    not command
    or user_disabled_eval
    or not spec.get("needs_eval_for_metrics")
    or not spec.get("cli_supports_eval")
    or not eval_arg
    or command_has_arg(command, eval_arg)
    or not looks_like_training_command(command)
  ):
    return command
  return f"{command} {eval_arg}"


def should_run_post_train_eval(command: str, post_train: Dict[str, Any] | None = None) -> bool:
  if not looks_like_training_command(command):
    return False
  config = post_train if isinstance(post_train, dict) else {}
  return bool(config.get("train_produces_eval") or config.get("run_render") or config.get("run_metrics"))


class RemoteExecutionError(RuntimeError):
  def __init__(self, message: str, *, code_hint: str = "", stage: str = "", details: Dict[str, Any] | None = None):
    super().__init__(message)
    self.code_hint = code_hint
    self.stage = stage
    self.details = details or {}


def _load_paramiko():
  try:
    import paramiko
  except Exception as exc:  # pragma: no cover - depends on runtime environment
    raise RemoteExecutionError(
      "Missing dependency: paramiko. Install it with `python -m pip install paramiko`.",
      code_hint="WGSC-STEP4-DEPENDENCY-001",
      stage="dependency",
    ) from exc
  return paramiko


WEB_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
SSH_KNOWN_HOSTS_FILE = WEB_CONFIG_DIR / "ssh_known_hosts.json"
_KNOWN_HOSTS_LOCK = threading.Lock()

# Only conda/venv activation commands are accepted for remote.activate_cmd.
# "source <path>" and "conda activate <env>" may be chained with "&&"; any
# other shell construct (command substitution, pipes, redirection, ...) is
# rejected so a client-provided string can never widen into arbitrary shell.
_ACTIVATE_CMD_PATTERN = re.compile(
  r"^(?:source\s+[\w./:~-]+|conda\s+activate\s+[\w.-]+)"
  r"(?:\s*&&\s*(?:source\s+[\w./:~-]+|conda\s+activate\s+[\w.-]+))*$"
)


def _load_ssh_known_hosts() -> Dict[str, Any]:
  try:
    data = json.loads(SSH_KNOWN_HOSTS_FILE.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}
  except Exception:
    return {}


def _save_ssh_known_hosts(data: Dict[str, Any]) -> None:
  try:
    SSH_KNOWN_HOSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SSH_KNOWN_HOSTS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
      os.chmod(SSH_KNOWN_HOSTS_FILE, 0o600)
    except OSError:
      pass
  except Exception as exc:
    raise RemoteExecutionError(
      f"Failed to save SSH known-hosts file: {SSH_KNOWN_HOSTS_FILE}: {exc}",
      code_hint="WGSC-STEP4-SSH-HOSTKEY-002",
      stage="connect",
    ) from exc


def _host_key_policy(paramiko, host: str, port: int):
  """Trust-on-first-use host key policy.

  The first connection stores the host key fingerprint in ssh_known_hosts.json;
  later connections reject any key whose fingerprint changed (possible MITM).
  Test doubles may ship a paramiko stub without policy classes; fall back to
  AutoAddPolicy there.
  """
  policy_base = getattr(paramiko, "MissingHostKeyPolicy", None)
  if policy_base is None:
    return paramiko.AutoAddPolicy()

  class _TofuHostKeyPolicy(policy_base):
    def missing_host_key(self, client, hostname, key):
      entry_name = f"{hostname}:{port}"
      fingerprint = key.get_fingerprint().hex()
      with _KNOWN_HOSTS_LOCK:
        store = _load_ssh_known_hosts()
        entry = store.get(entry_name)
        if entry is not None:
          if entry.get("type") != key.get_name() or entry.get("fingerprint") != fingerprint:
            raise RemoteExecutionError(
              f"SSH host key for {entry_name} does not match the saved fingerprint "
              f"(expected {entry.get('fingerprint')}, got {fingerprint}). "
              f"If the server key legitimately changed, delete the entry in "
              f"{SSH_KNOWN_HOSTS_FILE} and retry.",
              code_hint="WGSC-STEP4-SSH-HOSTKEY-001",
              stage="connect",
            )
        else:
          store[entry_name] = {
            "type": key.get_name(),
            "fingerprint": fingerprint,
            "added_at": datetime.now(timezone.utc).isoformat(),
          }
          _save_ssh_known_hosts(store)
      client.get_host_keys().add(hostname, key.get_name(), key)

  return _TofuHostKeyPolicy()


def _new_ssh_client(validated: Dict[str, Any]):
  """Create an SSHClient with the trust-on-first-use host key policy applied."""
  paramiko = _load_paramiko()
  client = paramiko.SSHClient()
  client.set_missing_host_key_policy(_host_key_policy(paramiko, validated["host"], validated["port"]))
  return client


def _close_ssh_resources(client, sftp=None) -> None:
  if sftp is not None:
    try:
      sftp.close()
    except Exception:
      pass
  try:
    client.close()
  except Exception:
    pass


def _normalize_remote_path(path_value: str) -> str:
  raw = str(path_value or "").strip().replace("\\", "/")
  if not raw:
    raise RemoteExecutionError(
      "Remote path cannot be empty.",
      code_hint="WGSC-STEP4-CONFIG-001",
      stage="config",
    )
  return str(PurePosixPath(raw))


def validate_remote_config(config: Dict[str, Any]) -> Dict[str, Any]:
  host = str(config.get("host", "")).strip()
  username = str(config.get("username", "")).strip()
  password = str(config.get("password", ""))

  if not host:
    raise RemoteExecutionError("remote.host is required", code_hint="WGSC-STEP4-CONFIG-001", stage="config")
  if not username:
    raise RemoteExecutionError("remote.username is required", code_hint="WGSC-STEP4-CONFIG-001", stage="config")
  if not password:
    raise RemoteExecutionError("remote.password is required", code_hint="WGSC-STEP4-CONFIG-001", stage="config")

  try:
    port = int(config.get("port", 22))
  except Exception as exc:
    raise RemoteExecutionError("remote.port must be a valid integer", code_hint="WGSC-STEP4-CONFIG-001", stage="config") from exc
  if port <= 0 or port > 65535:
    raise RemoteExecutionError("remote.port must be in range 1..65535", code_hint="WGSC-STEP4-CONFIG-001", stage="config")

  remote_repo_path = _normalize_remote_path(config.get("repo_path", ""))
  remote_workspace_root = _normalize_remote_path(config.get("workspace_root", ""))
  remote_output_root = _normalize_remote_path(config.get("output_root", ""))

  remote_python = str(config.get("python", "python3")).strip() or "python3"
  remote_activate_cmd = str(config.get("activate_cmd", "")).strip()
  if remote_activate_cmd and not _ACTIVATE_CMD_PATTERN.match(remote_activate_cmd):
    raise RemoteExecutionError(
      "remote.activate_cmd must be a plain conda/venv activation command, e.g. "
      "'source /root/miniconda3/etc/profile.d/conda.sh && conda activate myenv' or "
      "'conda activate myenv'. Shell metacharacters besides '&&' are not allowed.",
      code_hint="WGSC-STEP4-CONFIG-001",
      stage="config",
    )

  return {
    "host": host,
    "port": port,
    "username": username,
    "password": password,
    "repo_path": remote_repo_path,
    "workspace_root": remote_workspace_root,
    "output_root": remote_output_root,
    "python": remote_python,
    "activate_cmd": remote_activate_cmd,
  }


def _assert_remote_repo_exists(sftp, remote_repo_path: str) -> None:
  try:
    entry = sftp.stat(remote_repo_path)
  except Exception as exc:
    raise RemoteExecutionError(
      f"remote.repo_path not found: {remote_repo_path}",
      code_hint="WGSC-STEP4-PATH-REPO-001",
      stage="path",
    ) from exc
  if stat.S_ISDIR(entry.st_mode) is False:
    raise RemoteExecutionError(
      f"remote.repo_path is not a directory: {remote_repo_path}",
      code_hint="WGSC-STEP4-PATH-REPO-001",
      stage="path",
    )


def _assert_remote_path_writable(client, remote_path: str, label: str) -> None:
  marker = f".wgsc_preflight_{int(time.time() * 1000)}_{threading.get_ident()}"
  marker_path = str(PurePosixPath(remote_path) / marker)
  test_script = (
    f"mkdir -p {_quote(remote_path)}"
    f" && test -w {_quote(remote_path)}"
    f" && touch {_quote(marker_path)}"
    f" && rm -f {_quote(marker_path)}"
  )
  return_code = _run_remote_command(client, f"bash -lc {_quote(test_script)}", None)
  if return_code != 0:
    raise RemoteExecutionError(
      f"remote.{label} is not writable: {remote_path}",
      code_hint="WGSC-STEP4-PATH-WRITE-001",
      stage="path",
    )


def _assert_remote_python_available(client, remote_python: str) -> None:
  test_script = f"{_quote(remote_python)} --version >/dev/null 2>&1"
  return_code = _run_remote_command(client, f"bash -lc {_quote(test_script)}", None)
  if return_code != 0:
    raise RemoteExecutionError(
      f"remote.python is not executable: {remote_python}",
      code_hint="WGSC-STEP4-REMOTE-PY-001",
      stage="path",
    )


def _check_remote_command_available(client, command_name: str) -> bool:
  test_script = f"command -v {_quote(command_name)} >/dev/null 2>&1"
  return _run_remote_command(client, f"bash -lc {_quote(test_script)}", None) == 0


def _remote_colmap_runtime_setup_script() -> str:
  return (
    "OMP_NUM_THREADS_VALUE=\"${OMP_NUM_THREADS:-1}\""
    " && case \"$OMP_NUM_THREADS_VALUE\" in \"\"|*[!0-9]*|0) OMP_NUM_THREADS_VALUE=1 ;; esac"
    " && export OMP_NUM_THREADS=\"$OMP_NUM_THREADS_VALUE\""
    " && export QT_QPA_PLATFORM=xcb"
    " && if command -v vglrun >/dev/null 2>&1; then colmap_headless() { vglrun \"$@\"; }; else colmap_headless() { \"$@\"; }; fi"
  )


def _wrap_remote_colmap_headless_script(command_script: str) -> str:
  runtime_script = _remote_colmap_runtime_setup_script()
  return (
    "command -v xvfb-run >/dev/null 2>&1"
    f" && xvfb-run -a -s \"-screen 0 1280x1024x24\" bash -lc {_quote(runtime_script + ' && ' + command_script)}"
  )


def _check_remote_colmap_available(client) -> bool:
  test_script = _wrap_remote_colmap_headless_script(
    "command -v colmap >/dev/null 2>&1"
    " && colmap_headless colmap feature_extractor -h >/dev/null 2>&1"
  )
  return _run_remote_command(client, f"bash -lc {_quote(test_script)}", None) == 0


def _safe_int(value: Any, default: int = 0) -> int:
  try:
    return int(value)
  except Exception:
    return default


def _read_remote_json_file(sftp, remote_path: str) -> Dict[str, Any]:
  try:
    with sftp.open(remote_path, "r") as handle:
      raw = handle.read()
    if isinstance(raw, bytes):
      raw = raw.decode("utf-8", errors="replace")
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {}
  except Exception:
    return {}


def _remote_path_exists_sftp(sftp, remote_path: str) -> bool:
  try:
    sftp.stat(remote_path)
    return True
  except Exception:
    return False


def _remote_count_files_sftp(sftp, remote_dir: str) -> int:
  try:
    entries = sftp.listdir_attr(remote_dir)
  except Exception:
    return 0
  return sum(1 for item in entries if not stat.S_ISDIR(item.st_mode))


def _list_remote_datasets(
  sftp,
  *,
  workspace_root: str,
  family: str,
  max_items: int = 50,
) -> list[Dict[str, Any]]:
  dataset_base = PurePosixPath(workspace_root) / "datasets"
  if family:
    dataset_base = dataset_base / family
  dataset_base_str = str(dataset_base)

  try:
    entries = sftp.listdir_attr(dataset_base_str)
  except Exception:
    return []

  items: list[Dict[str, Any]] = []
  for entry in entries:
    if not stat.S_ISDIR(entry.st_mode):
      continue

    dataset_id = entry.filename
    dataset_dir = str(dataset_base / dataset_id)
    workspace_dir = str(PurePosixPath(dataset_dir) / "workspace")
    input_dir = str(PurePosixPath(workspace_dir) / "input")
    images_dir = str(PurePosixPath(workspace_dir) / "images")
    sparse_dir = str(PurePosixPath(workspace_dir) / "sparse" / "0")
    undistorted_marker = str(PurePosixPath(workspace_dir) / ".wgsc_colmap_undistorted.ok")

    manifest = {}
    for candidate in (
      str(PurePosixPath(dataset_dir) / "dataset_manifest.json"),
      str(PurePosixPath(dataset_dir) / "capture_session.json"),
      str(PurePosixPath(dataset_dir) / "workspace_manifest.json"),
    ):
      manifest = _read_remote_json_file(sftp, candidate)
      if manifest:
        break

    frame_count = _safe_int(manifest.get("frame_count"), 0)
    dataset_name = str(manifest.get("dataset_name") or manifest.get("title") or dataset_id)

    input_file_count = _remote_count_files_sftp(sftp, input_dir)
    images_file_count = _remote_count_files_sftp(sftp, images_dir)
    sparse_file_count = _remote_count_files_sftp(sftp, sparse_dir)
    has_raw_images = input_file_count > 0 or images_file_count > 0 or frame_count > 0
    has_sparse_model = sparse_file_count > 0
    has_undistorted = _remote_path_exists_sftp(sftp, undistorted_marker)

    stage_label = "raw_only"
    if has_sparse_model:
      stage_label = "sparse_ready"
    if has_sparse_model and has_undistorted:
      stage_label = "undistorted_ready"

    resolved_frame_count = frame_count or input_file_count or images_file_count

    items.append({
      "id": dataset_id,
      "name": dataset_name,
      "path": workspace_dir,
      "dataset_dir": dataset_dir,
      "session_id": str(manifest.get("session_id", "")),
      "capture_id": str(manifest.get("capture_id", "")),
      "frame_count": resolved_frame_count,
      "input_file_count": input_file_count,
      "images_file_count": images_file_count,
      "sparse_file_count": sparse_file_count,
      "has_input_images": has_raw_images,
      "has_sparse_model": has_sparse_model,
      "has_undistorted_marker": has_undistorted,
      "latest_marker": ".wgsc_colmap_undistorted.ok" if has_undistorted else "",
      "stage": {
        "raw": has_raw_images,
        "sparse": has_sparse_model,
        "undistorted": has_undistorted,
        "label": stage_label,
      },
      "stage_label": stage_label,
      "updated_at": _safe_int(getattr(entry, "st_mtime", 0), 0),
    })

  items.sort(key=lambda item: item.get("updated_at", 0), reverse=True)
  return items[: max(1, min(max_items, 200))]


def remote_preflight_check(
  *,
  remote_config: Dict[str, Any],
  timeout_seconds: int = 20,
  family: str = "",
  include_datasets: bool = True,
  check_colmap_required: bool = False,
) -> Dict[str, Any]:
  validated = validate_remote_config(remote_config)
  paramiko = _load_paramiko()
  client = _new_ssh_client(validated)
  sftp = None
  checks: list[Dict[str, Any]] = []

  try:
    try:
      client.connect(
        hostname=validated["host"],
        port=validated["port"],
        username=validated["username"],
        password=validated["password"],
        timeout=timeout_seconds,
        look_for_keys=False,
        allow_agent=False,
      )
      checks.append({
        "name": "ssh_connect",
        "ok": True,
        "message": "SSH connection established",
      })
    except paramiko.AuthenticationException as exc:
      raise RemoteExecutionError(
        "SSH authentication failed. Check remote username/password.",
        code_hint="WGSC-STEP4-SSH-AUTH-001",
        stage="auth",
      ) from exc
    except Exception as exc:
      raise RemoteExecutionError(
        f"SSH connection failed: {exc}",
        code_hint="WGSC-STEP4-SSH-NET-001",
        stage="connect",
      ) from exc

    sftp = client.open_sftp()
    _assert_remote_repo_exists(sftp, validated["repo_path"])
    checks.append({
      "name": "repo_path",
      "ok": True,
      "message": "Remote repo path exists",
      "path": validated["repo_path"],
    })

    _assert_remote_path_writable(client, validated["workspace_root"], "workspace_root")
    checks.append({
      "name": "workspace_root",
      "ok": True,
      "message": "Remote workspace root is writable",
      "path": validated["workspace_root"],
    })

    _assert_remote_path_writable(client, validated["output_root"], "output_root")
    checks.append({
      "name": "output_root",
      "ok": True,
      "message": "Remote output root is writable",
      "path": validated["output_root"],
    })

    _assert_remote_python_available(client, validated["python"])
    checks.append({
      "name": "remote_python",
      "ok": True,
      "message": "Remote python executable is available",
      "python": validated["python"],
    })

    tmux_available = _check_remote_command_available(client, "tmux")
    tmux_install_candidate = {} if tmux_available else _detect_remote_tmux_install_candidate(client)
    checks.append({
      "name": "remote_tmux",
      "ok": tmux_available,
      "message": "Remote tmux executable is available" if tmux_available else "Remote tmux executable not found; submit will try mamba or sudo -n installation",
      "required": False,
      "details": {
        "install_manager": tmux_install_candidate.get("manager", ""),
        "install_command": tmux_install_candidate.get("command", ""),
        "manual_command": tmux_install_candidate.get("manual_command", ""),
        "uses_password_sudo": False,
      },
    })

    xvfb_run_available = _check_remote_command_available(client, "xvfb-run")
    checks.append({
      "name": "remote_xvfb_run",
      "ok": xvfb_run_available,
      "message": "Remote xvfb-run executable is available" if xvfb_run_available else "Remote xvfb-run executable not found in PATH",
      "required": bool(check_colmap_required),
    })
    if check_colmap_required and not xvfb_run_available:
      raise RemoteExecutionError(
        "Remote xvfb-run is not available. Install Xvfb/xvfb-run before auto COLMAP workflow.",
        code_hint="WGSC-STEP5-COLMAP-TOOL-001",
        stage="colmap",
      )

    vglrun_available = _check_remote_command_available(client, "vglrun")
    checks.append({
      "name": "remote_vglrun",
      "ok": vglrun_available,
      "message": "Remote VirtualGL vglrun executable is available" if vglrun_available else "Remote VirtualGL vglrun not found in PATH; Xvfb fallback will be used",
      "required": False,
    })

    colmap_available = _check_remote_colmap_available(client)
    checks.append({
      "name": "remote_colmap",
      "ok": colmap_available,
      "message": "Remote colmap can run under Xvfb" if colmap_available else "Remote colmap failed headless preflight",
      "required": bool(check_colmap_required),
    })
    if check_colmap_required and not colmap_available:
      raise RemoteExecutionError(
        "Remote colmap cannot start under Xvfb. Install Xvfb/xvfb-run and ensure colmap is in PATH before auto COLMAP workflow.",
        code_hint="WGSC-STEP5-COLMAP-TOOL-001",
        stage="colmap",
      )

    datasets: list[Dict[str, Any]] = []
    if include_datasets:
      datasets = _list_remote_datasets(
        sftp,
        workspace_root=validated["workspace_root"],
        family=str(family or "").strip(),
      )

    return {
      "remote": sanitize_remote_config(validated),
      "checks": checks,
      "datasets": datasets,
      "tmux": {
        "available": tmux_available,
        "install_manager": tmux_install_candidate.get("manager", ""),
        "install_command": tmux_install_candidate.get("command", ""),
        "manual_command": tmux_install_candidate.get("manual_command", ""),
        "uses_password_sudo": False,
      },
      "summary": (
        f"SSH and remote path preflight checks passed. "
        f"Found {len(datasets)} reusable remote dataset(s)."
      ),
    }
  finally:
    _close_ssh_resources(client, sftp)


def sanitize_remote_config(config: Dict[str, Any]) -> Dict[str, Any]:
  return {
    "host": str(config.get("host", "")),
    "port": int(config.get("port", 22) or 22),
    "username": str(config.get("username", "")),
    "repo_path": str(config.get("repo_path", "")),
    "workspace_root": str(config.get("workspace_root", "")),
    "output_root": str(config.get("output_root", "")),
    "python": str(config.get("python", "python3") or "python3"),
    "activate_cmd": str(config.get("activate_cmd", "")),
    "has_password": bool(config.get("password")),
  }


def build_remote_paths(
  *,
  workspace_root: str,
  output_root: str,
  session_id: str,
  family: str,
  job_id: str,
  dataset_name: str = "",
  started_at: float | int | str | None = None,
  run_key: str = "",
) -> Dict[str, str]:
  canonical_run_key = str(run_key or "").strip()
  if not canonical_run_key and (dataset_name or started_at is not None):
    canonical_run_key = build_remote_run_key(dataset_name or session_id or job_id, family, started_at)
  if canonical_run_key:
    workspace_dir = str(PurePosixPath(workspace_root) / "runs" / canonical_run_key / "workspace")
    output_dir = str(PurePosixPath(output_root) / "runs" / canonical_run_key / "output")
    return {
      "workspace_dir": workspace_dir,
      "output_dir": output_dir,
      "run_key": canonical_run_key,
    }
  workspace_dir = str(PurePosixPath(workspace_root) / session_id / family / job_id / "workspace")
  output_dir = str(PurePosixPath(output_root) / session_id / family / job_id / "output")
  return {
    "workspace_dir": workspace_dir,
    "output_dir": output_dir,
  }


def _quote(value: str) -> str:
  return shlex.quote(str(value))


def _canonical_slug(value: str, *, fallback: str, max_length: int = 48, lower: bool = True) -> str:
  text = str(value or "").strip()
  if lower:
    text = text.lower()
  normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", text)
  normalized = re.sub(r"[-_.]{2,}", "-", normalized).strip("-_.")
  return (normalized[:max_length].strip("-_.") or fallback)


def _remote_run_timestamp(started_at: float | int | str | None = None) -> str:
  if started_at is None or started_at == "":
    dt = datetime.now(timezone.utc)
  elif isinstance(started_at, (int, float)):
    dt = datetime.fromtimestamp(float(started_at), timezone.utc)
  else:
    raw = str(started_at).strip()
    try:
      dt = datetime.fromtimestamp(float(raw), timezone.utc)
    except Exception:
      try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
      except Exception:
        dt = datetime.now(timezone.utc)
  return dt.strftime("%Y%m%dT%H%M%SZ")


def build_remote_run_key(dataset_name_or_id: str, family: str, started_at: float | int | str | None = None) -> str:
  dataset_slug = _canonical_slug(dataset_name_or_id, fallback="dataset", max_length=48)
  algorithm_slug = _canonical_slug(family, fallback="algorithm", max_length=32)
  return f"{dataset_slug}-{algorithm_slug}-{_remote_run_timestamp(started_at)}"


def build_remote_tmux_session_name(*, family: str = "", job_id: str = "", run_key: str = "") -> str:
  if run_key:
    normalized_key = _canonical_slug(run_key, fallback="remote-job", max_length=120, lower=False)
    raw = f"wgsc-{normalized_key}".strip("-")
  else:
    raw = f"wgsc-{family}-{job_id}".strip("-")
  normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-")
  normalized = re.sub(r"-{2,}", "-", normalized)
  if len(normalized) <= 80:
    return normalized or "wgsc-remote-job"
  parts = normalized.split("-")
  timestamp_index = next((index for index, part in enumerate(parts) if re.fullmatch(r"\d{8}T\d{6}Z", part)), -1)
  if timestamp_index > 1:
    suffix = "-".join(parts[timestamp_index - 1:])
    prefix_limit = max(8, 80 - len(suffix) - 1)
    return f"{normalized[:prefix_limit].strip('-')}-{suffix}"[:80]
  return normalized[:80].strip("-") or "wgsc-remote-job"


def build_remote_tmux_attach_command(remote_config: Dict[str, Any], tmux_session: str) -> str:
  validated = validate_remote_config(remote_config)
  remote_target = f"{validated['username']}@{validated['host']}"
  remote_command = f"tmux attach -t {_quote(tmux_session)}"
  return f"ssh -t -p {validated['port']} {_quote(remote_target)} {_quote(remote_command)}"


def _tmux_install_candidates() -> list[Dict[str, str]]:
  return [
    {
      "manager": "mamba",
      "detect": "command -v mamba >/dev/null 2>&1",
      "command": "mamba install -y -c conda-forge tmux",
      "manual_command": "mamba install -y -c conda-forge tmux",
    },
    {
      "manager": "apt-get",
      "detect": "command -v apt-get >/dev/null 2>&1",
      "command": "sudo -n apt-get update && sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y tmux",
      "manual_command": "sudo apt-get update && sudo apt-get install -y tmux",
    },
    {
      "manager": "dnf",
      "detect": "command -v dnf >/dev/null 2>&1",
      "command": "sudo -n dnf install -y tmux",
      "manual_command": "sudo dnf install -y tmux",
    },
    {
      "manager": "yum",
      "detect": "command -v yum >/dev/null 2>&1",
      "command": "sudo -n yum install -y tmux",
      "manual_command": "sudo yum install -y tmux",
    },
    {
      "manager": "pacman",
      "detect": "command -v pacman >/dev/null 2>&1",
      "command": "sudo -n pacman -Sy --noconfirm tmux",
      "manual_command": "sudo pacman -Sy tmux",
    },
    {
      "manager": "zypper",
      "detect": "command -v zypper >/dev/null 2>&1",
      "command": "sudo -n zypper --non-interactive install tmux",
      "manual_command": "sudo zypper install tmux",
    },
  ]


def _detect_remote_tmux_install_candidate(client) -> Dict[str, str]:
  for candidate in _tmux_install_candidates():
    if _run_remote_command(client, f"bash -lc {_quote(candidate['detect'])}", None) == 0:
      return dict(candidate)
  return {}


def _assert_safe_tmux_install_command(command: str) -> None:
  if "sudo -S" in command or "--stdin" in command:
    raise RemoteExecutionError(
      "Unsafe tmux install command rejected: password-based sudo is not allowed.",
      code_hint="WGSC-STEP5-TMUX-INSTALL-UNSAFE",
      stage="tmux",
    )
  if command.strip() == "mamba install -y -c conda-forge tmux":
    return
  if "sudo -n" not in command:
    raise RemoteExecutionError(
      "Unsafe tmux install command rejected: only mamba or non-interactive sudo -n is allowed.",
      code_hint="WGSC-STEP5-TMUX-INSTALL-UNSAFE",
      stage="tmux",
    )


def _ensure_remote_tmux_available(client, log_callback: Callable[[str, str], None] | None = None) -> Dict[str, Any]:
  if _check_remote_command_available(client, "tmux"):
    return {"available": True, "installed": False, "manager": "", "command": "", "manual_command": ""}

  candidate = _detect_remote_tmux_install_candidate(client)
  if not candidate:
    raise RemoteExecutionError(
      "Remote tmux is not installed and no supported package manager was detected. Install tmux manually, then retry.",
      code_hint="WGSC-STEP5-TMUX-001",
      stage="tmux",
    )

  install_command = str(candidate.get("command", ""))
  _assert_safe_tmux_install_command(install_command)
  if log_callback:
    log_callback("stdout", f"[Tmux] tmux not found. Trying non-interactive install with {candidate['manager']}.\n")
    log_callback("stdout", f"[Tmux] Install command: {install_command}\n")

  install_rc = _run_remote_command(client, f"bash -lc {_quote(install_command)}", log_callback)
  if install_rc != 0 or not _check_remote_command_available(client, "tmux"):
    manual = str(candidate.get("manual_command", "")).strip()
    raise RemoteExecutionError(
      (
        "Remote tmux is not installed and automatic non-interactive sudo install failed. "
        f"Run manually on the remote host: {manual or 'install tmux with your package manager'}"
      ),
      code_hint="WGSC-STEP5-TMUX-001",
      stage="tmux",
    )

  return {
    "available": True,
    "installed": True,
    "manager": candidate.get("manager", ""),
    "command": install_command,
    "manual_command": candidate.get("manual_command", ""),
  }


def _sftp_mkdir_p(sftp, remote_dir: str) -> None:
  path = PurePosixPath(remote_dir)
  current = PurePosixPath("/") if path.is_absolute() else PurePosixPath(path.parts[0])
  start_index = 1 if path.is_absolute() else 1

  if not path.parts:
    return

  if path.is_absolute():
    try:
      sftp.stat("/")
    except Exception:
      pass

  for part in path.parts[start_index:]:
    current = current / part
    current_str = str(current)
    try:
      sftp.stat(current_str)
    except Exception:
      sftp.mkdir(current_str)


def _upload_directory(sftp, local_dir: Path, remote_dir: str) -> Dict[str, Any]:
  uploaded_files = 0
  uploaded_bytes = 0
  _sftp_mkdir_p(sftp, remote_dir)

  for root_raw, _, files in os.walk(local_dir):
    root = Path(root_raw)
    relative = root.relative_to(local_dir)
    remote_root = PurePosixPath(remote_dir)
    if str(relative) != ".":
      remote_root = remote_root / relative.as_posix()
      _sftp_mkdir_p(sftp, str(remote_root))
    for file_name in files:
      local_file = root / file_name
      remote_file = str(remote_root / file_name)
      sftp.put(str(local_file), remote_file)
      uploaded_files += 1
      try:
        uploaded_bytes += local_file.stat().st_size
      except OSError:
        pass

  return {
    "files": uploaded_files,
    "bytes": uploaded_bytes,
  }


CHECKPOINT_DOWNLOAD_TREE_SKIP_DIR_NAMES = {"compgs_pretrain"}
CHECKPOINT_DOWNLOAD_FILE_PATTERNS = (
  "*.ckpt",
  "*.pth",
  "*.pt",
  "checkpoint_*.pkl",
  "chkpnt*.pth",
  "model_*.pth",
)


def _is_checkpoint_download_path(relative_path: str) -> bool:
  normalized = str(relative_path or "").replace("\\", "/").strip("/")
  if not normalized:
    return False
  filename = PurePosixPath(normalized).name.lower()
  return any(fnmatch(filename, pattern) for pattern in CHECKPOINT_DOWNLOAD_FILE_PATTERNS)


def _count_remote_regular_files(sftp, remote_path: PurePosixPath) -> tuple[int, int]:
  files = 0
  bytes_total = 0
  for entry in sftp.listdir_attr(str(remote_path)):
    child = remote_path / entry.filename
    if stat.S_ISDIR(entry.st_mode):
      child_files, child_bytes = _count_remote_regular_files(sftp, child)
      files += child_files
      bytes_total += child_bytes
    elif stat.S_ISREG(entry.st_mode):
      files += 1
      bytes_total += int(getattr(entry, "st_size", 0) or 0)
  return files, bytes_total


def _collect_remote_download_files(sftp, remote_dir: str, local_dir: Path) -> tuple[list[Dict[str, Any]], int, int, int, list[str]]:
  remote_root = PurePosixPath(remote_dir)
  local_root = Path(local_dir)
  files: list[Dict[str, Any]] = []
  skipped_special_files = 0
  skipped_checkpoint_files = 0
  skipped_checkpoint_bytes = 0
  skipped_checkpoint_samples: list[str] = []

  def walk(remote_path: PurePosixPath, relative_parts: tuple[str, ...]) -> None:
    nonlocal skipped_special_files, skipped_checkpoint_files, skipped_checkpoint_bytes
    entries = sftp.listdir_attr(str(remote_path))
    for entry in entries:
      remote_item = remote_path / entry.filename
      child_parts = (*relative_parts, entry.filename)
      relative_path = PurePosixPath(*child_parts).as_posix()
      if stat.S_ISDIR(entry.st_mode):
        if entry.filename.lower() in CHECKPOINT_DOWNLOAD_TREE_SKIP_DIR_NAMES:
          child_files, child_bytes = _count_remote_regular_files(sftp, remote_item)
          skipped_checkpoint_files += child_files
          skipped_checkpoint_bytes += child_bytes
          if len(skipped_checkpoint_samples) < 20:
            skipped_checkpoint_samples.append(relative_path + "/")
          continue
        walk(remote_item, child_parts)
        continue
      if not stat.S_ISREG(entry.st_mode):
        skipped_special_files += 1
        continue
      size = int(getattr(entry, "st_size", 0) or 0)
      if _is_checkpoint_download_path(relative_path):
        skipped_checkpoint_files += 1
        skipped_checkpoint_bytes += size
        if len(skipped_checkpoint_samples) < 20:
          skipped_checkpoint_samples.append(relative_path)
        continue
      files.append({
        "remote_path": str(remote_item),
        "local_path": str(local_root.joinpath(*child_parts)),
        "relative_path": relative_path,
        "size": size,
      })

  walk(remote_root, ())
  return files, skipped_special_files, skipped_checkpoint_files, skipped_checkpoint_bytes, skipped_checkpoint_samples


def _build_remote_download_plan(sftp, remote_dir: str, local_dir: Path) -> Dict[str, Any]:
  local_root = Path(local_dir).expanduser().resolve()
  (
    remote_files,
    skipped_special_files,
    skipped_checkpoint_files,
    skipped_checkpoint_bytes,
    skipped_checkpoint_samples,
  ) = _collect_remote_download_files(sftp, remote_dir, local_root)
  files_to_download: list[Dict[str, Any]] = []
  skipped_files = 0
  skipped_bytes = 0
  missing_files = 0
  mismatched_files = 0
  bytes_to_download = 0
  total_bytes = 0

  for item in remote_files:
    size = int(item.get("size", 0) or 0)
    total_bytes += size
    local_path = Path(str(item["local_path"]))
    local_size = -1
    if local_path.exists() and local_path.is_file():
      try:
        local_size = local_path.stat().st_size
      except OSError:
        local_size = -1
    if local_size == size:
      skipped_files += 1
      skipped_bytes += size
      continue

    reason = "missing" if local_size < 0 else "size_mismatch"
    if reason == "missing":
      missing_files += 1
    else:
      mismatched_files += 1
    bytes_to_download += size
    files_to_download.append({
      **item,
      "reason": reason,
      "local_size": None if local_size < 0 else local_size,
    })

  return {
    "ok": True,
    "complete": not files_to_download,
    "remote_output_dir": str(PurePosixPath(remote_dir)),
    "local_output_dir": str(local_root),
    "total_files": len(remote_files),
    "total_bytes": total_bytes,
    "files_to_download": files_to_download,
    "download_files": len(files_to_download),
    "download_bytes": bytes_to_download,
    "missing_files": missing_files,
    "mismatched_files": mismatched_files,
    "skipped_files": skipped_files,
    "skipped_bytes": skipped_bytes,
    "skipped_special_files": skipped_special_files,
    "skipped_checkpoint_files": skipped_checkpoint_files,
    "skipped_checkpoint_bytes": skipped_checkpoint_bytes,
    "skipped_checkpoint_samples": skipped_checkpoint_samples,
    "sample_files": [item["relative_path"] for item in files_to_download[:20]],
  }


def _public_download_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
  return {
    "ok": plan.get("ok", True),
    "complete": plan.get("complete", False),
    "remote_output_dir": plan.get("remote_output_dir", ""),
    "local_output_dir": plan.get("local_output_dir", ""),
    "remote_total_files": int(plan.get("total_files", 0) or 0),
    "remote_total_bytes": int(plan.get("total_bytes", 0) or 0),
    "total_files": int(plan.get("download_files", 0) or 0),
    "total_bytes": int(plan.get("download_bytes", 0) or 0),
    "download_files": int(plan.get("download_files", 0) or 0),
    "download_bytes": int(plan.get("download_bytes", 0) or 0),
    "missing_files": int(plan.get("missing_files", 0) or 0),
    "mismatched_files": int(plan.get("mismatched_files", 0) or 0),
    "skipped_files": int(plan.get("skipped_files", 0) or 0),
    "skipped_bytes": int(plan.get("skipped_bytes", 0) or 0),
    "skipped_special_files": int(plan.get("skipped_special_files", 0) or 0),
    "skipped_checkpoint_files": int(plan.get("skipped_checkpoint_files", 0) or 0),
    "skipped_checkpoint_bytes": int(plan.get("skipped_checkpoint_bytes", 0) or 0),
    "skipped_checkpoint_samples": plan.get("skipped_checkpoint_samples", []),
    "sample_files": plan.get("sample_files", []),
  }


def _remote_result_marker_path(remote_output_dir: str, remote_status_path: str = "") -> str:
  raw_status_path = str(remote_status_path or "").strip()
  if raw_status_path:
    return str(PurePosixPath(raw_status_path))
  return str(PurePosixPath(remote_output_dir) / ".wgsc_job" / "status.json")


def _read_remote_json_file_strict(sftp, remote_path: str) -> Dict[str, Any]:
  try:
    with sftp.open(remote_path, "r") as handle:
      raw = handle.read()
  except Exception as exc:
    raise RemoteExecutionError(
      f"Remote result marker not found: {remote_path}",
      code_hint="WGSC-JOB-RESULTS-MARKER-MISSING",
      stage="result_marker",
      details={"remote_status_path": remote_path},
    ) from exc
  if isinstance(raw, bytes):
    raw_text = raw.decode("utf-8", errors="replace")
  else:
    raw_text = str(raw)
  try:
    payload = json.loads(raw_text or "{}")
  except Exception as exc:
    raise RemoteExecutionError(
      f"Remote result marker is invalid JSON: {remote_path}",
      code_hint="WGSC-JOB-RESULTS-MARKER-INVALID",
      stage="result_marker",
      details={"remote_status_path": remote_path},
    ) from exc
  if not isinstance(payload, dict):
    raise RemoteExecutionError(
      f"Remote result marker is not an object: {remote_path}",
      code_hint="WGSC-JOB-RESULTS-MARKER-INVALID",
      stage="result_marker",
      details={"remote_status_path": remote_path},
    )
  return payload


def _verify_remote_result_marker(
  sftp,
  *,
  remote_output_dir: str,
  remote_status_path: str = "",
  expected_job_id: str = "",
  expected_family: str = "",
  expected_remote_output_dir: str = "",
) -> Dict[str, Any]:
  marker_path = _remote_result_marker_path(remote_output_dir, remote_status_path)
  payload = _read_remote_json_file_strict(sftp, marker_path)
  marker_job_id = str(payload.get("job_id", "")).strip()
  expected_job_id = str(expected_job_id or "").strip()
  if expected_job_id and marker_job_id != expected_job_id:
    raise RemoteExecutionError(
      "Remote result marker belongs to a different job.",
      code_hint="WGSC-JOB-RESULTS-MARKER-MISMATCH",
      stage="result_marker",
      details={
        "remote_status_path": marker_path,
        "expected_job_id": expected_job_id,
        "actual_job_id": marker_job_id,
      },
    )
  marker_family = str(payload.get("family", "")).strip()
  expected_family = str(expected_family or "").strip()
  if expected_family and marker_family != expected_family:
    raise RemoteExecutionError(
      "Remote result marker belongs to a different algorithm family.",
      code_hint="WGSC-JOB-RESULTS-MARKER-MISMATCH",
      stage="result_marker",
      details={
        "remote_status_path": marker_path,
        "expected_family": expected_family,
        "actual_family": marker_family,
      },
    )
  marker_output_dir = str(PurePosixPath(str(payload.get("remote_output_dir", "") or "")))
  expected_output_dir = str(PurePosixPath(str(expected_remote_output_dir or remote_output_dir)))
  if marker_output_dir != expected_output_dir:
    raise RemoteExecutionError(
      "Remote result marker points to a different output directory.",
      code_hint="WGSC-JOB-RESULTS-MARKER-MISMATCH",
      stage="result_marker",
      details={
        "remote_status_path": marker_path,
        "expected_remote_output_dir": expected_output_dir,
        "actual_remote_output_dir": marker_output_dir,
      },
    )
  return {
    "verified": True,
    "remote_status_path": marker_path,
    "job_id": marker_job_id,
    "family": marker_family,
    "remote_output_dir": marker_output_dir,
  }


def _download_directory(
  sftp,
  remote_dir: str,
  local_dir: Path,
  progress_callback: Callable[[Dict[str, Any]], None] | None = None,
) -> Dict[str, Any]:
  downloaded_files = 0
  downloaded_bytes = 0
  local_root = Path(local_dir).expanduser().resolve()
  local_root.mkdir(parents=True, exist_ok=True)
  plan = _build_remote_download_plan(sftp, remote_dir, local_root)
  files_to_download = plan["files_to_download"]
  total_files = int(plan.get("download_files", 0) or 0)
  total_bytes = int(plan.get("download_bytes", 0) or 0)

  def emit_progress(extra: Dict[str, Any] | None = None) -> None:
    if not progress_callback:
      return
    payload = {
      "files": downloaded_files,
      "bytes": downloaded_bytes,
      "total_files": total_files,
      "total_bytes": total_bytes,
      "remote_total_files": plan["total_files"],
      "remote_total_bytes": plan["total_bytes"],
      "skipped_files": plan["skipped_files"],
      "skipped_bytes": plan["skipped_bytes"],
      "skipped_special_files": plan["skipped_special_files"],
      "skipped_checkpoint_files": plan["skipped_checkpoint_files"],
      "skipped_checkpoint_bytes": plan["skipped_checkpoint_bytes"],
      "skipped_checkpoint_samples": plan["skipped_checkpoint_samples"],
      "missing_files": plan["missing_files"],
      "mismatched_files": plan["mismatched_files"],
      "complete": plan["complete"],
      "phase": "downloading",
      "pending": True,
    }
    if extra:
      payload.update(extra)
    progress_callback(payload)

  if not files_to_download:
    if progress_callback:
      progress_callback({
        **_public_download_plan(plan),
        "files": 0,
        "bytes": 0,
        "pending": False,
        "phase": "complete",
      })
    return {
      **_public_download_plan(plan),
      "files": 0,
      "bytes": 0,
      "pending": False,
      "phase": "complete",
    }

  try:
    available_bytes = int(shutil.disk_usage(local_root).free)
  except OSError:
    available_bytes = -1
  if available_bytes >= 0 and available_bytes < total_bytes:
    raise RemoteExecutionError(
      "Not enough local disk space to download completed job results.",
      code_hint="WGSC-JOB-RESULTS-DISK-SPACE",
      stage="download",
      details={
        "local_output_dir": str(local_root),
        "required_bytes": total_bytes,
        "available_bytes": available_bytes,
      },
    )

  emit_progress({"current_file": ""})

  for file_item in files_to_download:
    remote_item = str(file_item["remote_path"])
    local_item = Path(str(file_item["local_path"]))
    remote_size = int(file_item.get("size", 0) or 0)
    local_item.parent.mkdir(parents=True, exist_ok=True)
    temp_item = local_item.with_name(f".{local_item.name}.wgsc-download")
    try:
      if temp_item.exists():
        temp_item.unlink()
    except OSError:
      pass

    last_emit = 0.0

    def on_file_progress(transferred: int, total: int) -> None:
      nonlocal last_emit
      now = time.time()
      if transferred < total and now - last_emit < 1.0:
        return
      last_emit = now
      emit_progress({
        "bytes": downloaded_bytes + int(transferred or 0),
        "current_file": str(file_item["relative_path"]),
        "current_bytes": int(transferred or 0),
        "current_total": int(total or remote_size or 0),
      })

    try:
      sftp.get(remote_item, str(temp_item), callback=on_file_progress)
      try:
        temp_size = temp_item.stat().st_size
      except OSError as exc:
        raise RemoteExecutionError(
          f"Downloaded file is not readable: {file_item['relative_path']}",
          code_hint="WGSC-JOB-RESULTS-FILE-SIZE",
          stage="download",
          details={"relative_path": file_item["relative_path"]},
        ) from exc
      if temp_size != remote_size:
        raise RemoteExecutionError(
          f"Downloaded file size mismatch: {file_item['relative_path']}",
          code_hint="WGSC-JOB-RESULTS-FILE-SIZE",
          stage="download",
          details={
            "relative_path": file_item["relative_path"],
            "expected_bytes": remote_size,
            "actual_bytes": temp_size,
          },
        )
      temp_item.replace(local_item)
    except Exception:
      try:
        if temp_item.exists():
          temp_item.unlink()
      except OSError:
        pass
      raise

    try:
      written_size = local_item.stat().st_size
    except OSError:
      written_size = remote_size
    completed_size = int(written_size or remote_size or 0)
    downloaded_files += 1
    downloaded_bytes += completed_size
    emit_progress({
      "current_file": str(file_item["relative_path"]),
      "current_bytes": completed_size,
      "current_total": int(remote_size or completed_size),
    })

  return {
    **_public_download_plan(plan),
    "files": downloaded_files,
    "bytes": downloaded_bytes,
    "pending": False,
    "phase": "complete",
  }


def check_remote_output_download(
  *,
  remote_config: Dict[str, Any],
  remote_output_dir: str,
  local_output_dir: str,
  remote_status_path: str = "",
  expected_job_id: str = "",
  expected_family: str = "",
  expected_remote_output_dir: str = "",
) -> Dict[str, Any]:
  validated = validate_remote_config(remote_config)
  client = _new_ssh_client(validated)
  sftp = None
  try:
    client.connect(
      hostname=validated["host"],
      port=validated["port"],
      username=validated["username"],
      password=validated["password"],
      timeout=20,
      look_for_keys=False,
      allow_agent=False,
    )
    sftp = client.open_sftp()
    marker = _verify_or_recover_remote_result_marker(
      sftp,
      remote_output_dir=remote_output_dir,
      remote_status_path=remote_status_path,
      expected_job_id=expected_job_id,
      expected_family=expected_family,
      expected_remote_output_dir=expected_remote_output_dir,
    )
    plan = _build_remote_download_plan(sftp, remote_output_dir, Path(local_output_dir))
    return {**_public_download_plan(plan), "remote_marker": marker}
  finally:
    _close_ssh_resources(client, sftp)


def download_remote_output_directory(
  *,
  remote_config: Dict[str, Any],
  remote_output_dir: str,
  local_output_dir: str,
  remote_status_path: str = "",
  expected_job_id: str = "",
  expected_family: str = "",
  expected_remote_output_dir: str = "",
  progress_callback: Callable[[Dict[str, Any]], None] | None = None,
) -> Dict[str, Any]:
  validated = validate_remote_config(remote_config)
  client = _new_ssh_client(validated)
  sftp = None
  try:
    client.connect(
      hostname=validated["host"],
      port=validated["port"],
      username=validated["username"],
      password=validated["password"],
      timeout=20,
      look_for_keys=False,
      allow_agent=False,
    )
    sftp = client.open_sftp()
    marker = _verify_or_recover_remote_result_marker(
      sftp,
      remote_output_dir=remote_output_dir,
      remote_status_path=remote_status_path,
      expected_job_id=expected_job_id,
      expected_family=expected_family,
      expected_remote_output_dir=expected_remote_output_dir,
    )
    result = _download_directory(
      sftp,
      remote_output_dir,
      Path(local_output_dir),
      progress_callback=progress_callback,
    )
    return {**result, "remote_marker": marker}
  finally:
    _close_ssh_resources(client, sftp)


def _remote_job_paths(remote_output_dir: str) -> Dict[str, str]:
  job_dir = str(PurePosixPath(remote_output_dir) / ".wgsc_job")
  return {
    "job_dir": job_dir,
    "run_script": str(PurePosixPath(job_dir) / "run.sh"),
    "runtime_log": str(PurePosixPath(job_dir) / "runtime.log"),
    "status_file": str(PurePosixPath(job_dir) / "status.json"),
    "pid_file": str(PurePosixPath(job_dir) / "pid"),
    "launcher_pid_file": str(PurePosixPath(job_dir) / "launcher_pid"),
    "exit_code_file": str(PurePosixPath(job_dir) / "exit_code"),
  }


def _write_remote_text(sftp, remote_path: str, content: str) -> None:
  parent = str(PurePosixPath(remote_path).parent)
  _sftp_mkdir_p(sftp, parent)
  with sftp.open(remote_path, "w") as handle:
    handle.write(content)


def _read_remote_text_file(sftp, remote_path: str) -> str:
  try:
    with sftp.open(remote_path, "r") as handle:
      raw = handle.read()
    if isinstance(raw, bytes):
      return raw.decode("utf-8", errors="replace")
    return str(raw or "")
  except Exception:
    return ""


def _remote_path_exists(sftp, remote_path: str) -> bool:
  try:
    sftp.stat(remote_path)
    return True
  except Exception:
    return False


def _write_remote_json_marker(sftp, remote_path: str, payload: Dict[str, Any]) -> None:
  tmp_path = f"{remote_path}.tmp.{int(time.time() * 1000)}"
  _write_remote_text(sftp, tmp_path, json.dumps(payload, ensure_ascii=False, indent=2))
  try:
    sftp.rename(tmp_path, remote_path)
  except Exception:
    _write_remote_text(sftp, remote_path, json.dumps(payload, ensure_ascii=False, indent=2))


def _recover_missing_status_marker(
  sftp,
  *,
  remote_status_path: str,
  remote_log_path: str,
  remote_output_dir: str,
  expected_job_id: str = "",
  expected_family: str = "",
  status: str = "running",
  stage: str = "executing_remote_command",
  return_code: Any = "",
  message: str = "Remote status marker was recovered from job sidecar files.",
) -> None:
  try:
    payload: Dict[str, Any] = {
      "job_id": str(expected_job_id or ""),
      "family": str(expected_family or ""),
      "status": status,
      "stage": stage,
      "message": message,
      "remote_log_path": remote_log_path,
      "remote_output_dir": remote_output_dir,
      "updated_at": time.time(),
    }
    if return_code not in ("", None):
      payload["return_code"] = return_code
    _write_remote_json_marker(sftp, remote_status_path, payload)
  except Exception:
    pass


def _read_remote_log_delta(sftp, remote_path: str, cursor: int) -> Dict[str, Any]:
  try:
    stat_result = sftp.stat(remote_path)
    size = int(getattr(stat_result, "st_size", 0) or 0)
  except Exception:
    return {"cursor": cursor, "text": "", "available": False}

  start = max(0, int(cursor or 0))
  if start > size:
    start = 0

  try:
    with sftp.open(remote_path, "rb") as handle:
      handle.seek(start)
      raw = handle.read()
  except Exception:
    return {"cursor": start, "text": "", "available": False}

  if isinstance(raw, str):
    raw_bytes = raw.encode("utf-8", errors="replace")
    text = raw
  else:
    raw_bytes = raw or b""
    text = raw_bytes.decode("utf-8", errors="replace")

  return {
    "cursor": start + len(raw_bytes),
    "text": text,
    "available": True,
  }


def _remote_exit_code_path_from_status_path(remote_status_path: str) -> str:
  if not str(remote_status_path or "").strip():
    return ""
  return str(PurePosixPath(remote_status_path).parent / "exit_code")


def _status_from_remote_return_code(return_code: int) -> str:
  if return_code == 0:
    return "completed"
  if return_code == 130:
    return "canceled"
  return "failed"


def _downloadable_remote_status(status: str) -> bool:
  return status in {
    "completed",
    "partial_success",
    "training_success_render_failed",
    "training_success_metrics_failed",
    "training_success_postprocess_failed",
  }


def _remote_output_has_result_evidence(sftp, remote_output_dir: str) -> bool:
  for relative in ("result.json", "scene_manifest.json", "point_cloud.ply", "point_cloud"):
    if _remote_path_exists(sftp, str(PurePosixPath(remote_output_dir) / relative)):
      return True
  try:
    return bool(sftp.listdir_attr(str(PurePosixPath(remote_output_dir))))
  except Exception:
    return False


def _verify_or_recover_remote_result_marker(
  sftp,
  *,
  remote_output_dir: str,
  remote_status_path: str = "",
  expected_job_id: str = "",
  expected_family: str = "",
  expected_remote_output_dir: str = "",
) -> Dict[str, Any]:
  try:
    return _verify_remote_result_marker(
      sftp,
      remote_output_dir=remote_output_dir,
      remote_status_path=remote_status_path,
      expected_job_id=expected_job_id,
      expected_family=expected_family,
      expected_remote_output_dir=expected_remote_output_dir,
    )
  except RemoteExecutionError as exc:
    if exc.code_hint != "WGSC-JOB-RESULTS-MARKER-MISSING":
      raise
    marker_path = _remote_result_marker_path(remote_output_dir, remote_status_path)
    exit_code_text = _read_remote_text_file(sftp, _remote_exit_code_path_from_status_path(marker_path)).strip()
    if exit_code_text != "0" or not _remote_output_has_result_evidence(sftp, remote_output_dir):
      raise
    payload = {
      "job_id": str(expected_job_id or ""),
      "family": str(expected_family or ""),
      "status": "completed",
      "stage": "completed",
      "return_code": 0,
      "message": "Remote status marker was recovered before result download.",
      "remote_log_path": str(PurePosixPath(marker_path).parent / "runtime.log"),
      "remote_output_dir": str(PurePosixPath(expected_remote_output_dir or remote_output_dir)),
      "train_status": "success",
      "updated_at": time.time(),
    }
    _write_remote_json_marker(sftp, marker_path, payload)
    return _verify_remote_result_marker(
      sftp,
      remote_output_dir=remote_output_dir,
      remote_status_path=marker_path,
      expected_job_id=expected_job_id,
      expected_family=expected_family,
      expected_remote_output_dir=expected_remote_output_dir,
    )


def _remote_sparse_test_script(workspace_dir: str) -> str:
  sparse_dir = str(PurePosixPath(workspace_dir) / "sparse" / "0")
  marker_path = str(PurePosixPath(workspace_dir) / ".wgsc_colmap_undistorted.ok")
  return (
    f"test -f {_quote(marker_path)}"
    f" && test -d {_quote(sparse_dir)}"
    f" && [ \"$(find {_quote(sparse_dir)} -maxdepth 1 -type f | wc -l)\" -gt 0 ]"
  )


def _read_stream(stream, channel: str, log_callback: Callable[[str, str], None] | None) -> None:
  for raw_line in iter(stream.readline, ""):
    if raw_line is None:
      break
    line = raw_line
    if isinstance(line, bytes):
      line = line.decode("utf-8", errors="replace")
    if not line:
      break
    if log_callback:
      log_callback(channel, line)
  stream.close()


def _run_remote_command(client, command: str, log_callback: Callable[[str, str], None] | None) -> int:
  stdin, stdout, stderr = client.exec_command(command, get_pty=True)
  stdin.close()

  stdout_thread = threading.Thread(target=_read_stream, args=(stdout, "stdout", log_callback), daemon=True)
  stderr_thread = threading.Thread(target=_read_stream, args=(stderr, "stderr", log_callback), daemon=True)
  stdout_thread.start()
  stderr_thread.start()
  return_code = stdout.channel.recv_exit_status()
  stdout_thread.join(timeout=1)
  stderr_thread.join(timeout=1)
  return return_code


def _run_remote_command_cancellable(
  client,
  command: str,
  log_callback: Callable[[str, str], None] | None,
  cancel_checker: Callable[[], bool] | None = None,
) -> int:
  stdin, stdout, stderr = client.exec_command(command, get_pty=True)
  stdin.close()

  stdout_thread = threading.Thread(target=_read_stream, args=(stdout, "stdout", log_callback), daemon=True)
  stderr_thread = threading.Thread(target=_read_stream, args=(stderr, "stderr", log_callback), daemon=True)
  stdout_thread.start()
  stderr_thread.start()

  channel = stdout.channel
  while not channel.exit_status_ready():
    if cancel_checker and cancel_checker():
      if log_callback:
        log_callback("stderr", "[Cancel] Cancellation requested. Closing remote command channel.\n")
      try:
        channel.close()
      except Exception:
        pass
      stdout_thread.join(timeout=1)
      stderr_thread.join(timeout=1)
      return 130
    time.sleep(0.25)

  return_code = channel.recv_exit_status()
  stdout_thread.join(timeout=1)
  stderr_thread.join(timeout=1)
  return return_code


def sanitize_formatted_command(command_template: str, formatted_command: str, format_args: Dict[str, Any]) -> str:
  """Remove flags from a formatted command when their template placeholders were empty.

  This scans `command_template` for occurrences like "--flag {key}" and if
  `format_args[key]` is empty, removes the corresponding flag token (and
  variants like "--flag=") from `formatted_command`.
  """
  import re
  # map flags -> placeholder keys (e.g. '--model-dir' -> 'checkpoint_path')
  flag_key_map: Dict[str, str] = {}
  for m in re.finditer(r"\{([^}]+)\}", command_template):
    key = m.group(1).strip()
    left = command_template[: m.start()].rstrip()
    last = re.search(r"([^\s]+)\s*$", left)
    if not last:
      continue
    last_token = last.group(1)
    if last_token.endswith("="):
      flag = last_token[:-1]
    else:
      flag = last_token
    if flag.startswith("-"):
      flag_key_map[flag] = key

  if not flag_key_map:
    return formatted_command

  # Tokenize and remove tokens that match flags whose placeholder value is empty.
  tokens = shlex.split(formatted_command)
  cleaned: list[str] = []
  i = 0
  n = len(tokens)
  while i < n:
    tok = tokens[i]
    removed = False
    for flag, key in flag_key_map.items():
      if not str(format_args.get(key, "")).strip():
        # match forms: "--flag", "--flag=", "--flag=VALUE"
        if tok == flag or tok.startswith(flag + "=") or tok == flag + "=":
          removed = True
          break
        # also handle when flag is a token followed immediately by another flag
        if tok == flag and (i + 1 >= n or tokens[i + 1].startswith("-")):
          removed = True
          break
    if removed:
      i += 1
      continue
    cleaned.append(tok)
    i += 1

  try:
    return shlex.join(cleaned)
  except AttributeError:
    return " ".join(shlex.quote(t) for t in cleaned)


def _format_remote_command_template(command_template: str, format_args: Dict[str, Any], python_bin: str) -> str:
  command = command_template.format(**format_args)
  try:
    command = sanitize_formatted_command(command_template, command, format_args)
  except Exception:
    pass
  if command.lstrip().startswith("python "):
    command = f"{python_bin}{command.lstrip()[len('python') :]}"
  return command


def _prepare_post_train_config(
  post_train_config: Dict[str, Any] | None,
  format_args: Dict[str, Any],
  python_bin: str,
  family: str,
) -> Dict[str, Any]:
  config = dict(post_train_config or {})
  family_key = str(family or "").strip().lower()
  if not str(config.get("iteration", "")).strip():
    config["iteration"] = str(format_args.get("iterations") or "30000")
  if config.get("run_render") and not str(config.get("render_command", "")).strip():
    template = str(config.get("render_template", "") or "").strip()
    if template and family_key != "gaussian-splatting-lightning":
      config["render_command"] = _format_remote_command_template(template, format_args, python_bin)
  if config.get("run_metrics") and not str(config.get("metrics_command", "")).strip():
    template = str(config.get("metrics_template", "") or "").strip()
    if template:
      config["metrics_command"] = _format_remote_command_template(template, format_args, python_bin)
  return config


def _normalize_dataset_id(dataset_name: str, job_id: str) -> str:
  raw = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(dataset_name or "").strip()).strip("-_")
  base = raw[:48] if raw else "dataset"
  return f"{base}-{job_id[:8]}"


def _remote_directory_exists(client, remote_dir: str) -> bool:
  script = f"test -d {_quote(remote_dir)}"
  return _run_remote_command(client, f"bash -lc {_quote(script)}", None) == 0


def _remote_has_sparse_workspace(client, workspace_dir: str) -> bool:
  sparse_dir = str(PurePosixPath(workspace_dir) / "sparse" / "0")
  marker_path = str(PurePosixPath(workspace_dir) / ".wgsc_colmap_undistorted.ok")
  script = (
    f"test -f {_quote(marker_path)}"
    f" && test -d {_quote(sparse_dir)}"
    f" && [ \"$(find {_quote(sparse_dir)} -maxdepth 1 -type f | wc -l)\" -gt 0 ]"
  )
  return _run_remote_command(client, f"bash -lc {_quote(script)}", None) == 0


def _build_remote_colmap_script(workspace_dir: str) -> str:
  input_dir = str(PurePosixPath(workspace_dir) / "input")
  images_dir = str(PurePosixPath(workspace_dir) / "images")
  distorted_dir = str(PurePosixPath(workspace_dir) / "distorted")
  database_path = str(PurePosixPath(distorted_dir) / "database.db")
  distorted_sparse = str(PurePosixPath(distorted_dir) / "sparse")
  sparse_root = str(PurePosixPath(workspace_dir) / "sparse")
  distorted_sparse_0 = str(PurePosixPath(distorted_sparse) / "0")
  sparse_0 = str(PurePosixPath(sparse_root) / "0")
  marker_path = str(PurePosixPath(workspace_dir) / ".wgsc_colmap_undistorted.ok")

  steps = [
    "command -v colmap >/dev/null 2>&1",
    f"rm -f {_quote(marker_path)} {_quote(database_path)}",
    f"mkdir -p {_quote(distorted_sparse)} {_quote(sparse_root)}",
    (
      f"colmap_headless colmap feature_extractor "
      f"--database_path {_quote(database_path)} "
      f"--image_path {_quote(input_dir)} "
      "--ImageReader.single_camera 1 --ImageReader.camera_model SIMPLE_PINHOLE"
    ),
    f"colmap_headless colmap sequential_matcher --database_path {_quote(database_path)}",
    (
      f"colmap_headless colmap mapper "
      f"--database_path {_quote(database_path)} "
      f"--image_path {_quote(input_dir)} "
      f"--output_path {_quote(distorted_sparse)}"
    ),
    f"rm -rf {_quote(images_dir)} {_quote(sparse_root)}",
    f"mkdir -p {_quote(images_dir)} {_quote(sparse_root)} {_quote(sparse_0)}",
    (
      f"colmap_headless colmap image_undistorter "
      f"--image_path {_quote(input_dir)} "
      f"--input_path {_quote(distorted_sparse_0)} "
      f"--output_path {_quote(workspace_dir)} "
      "--output_type COLMAP"
    ),
    f"if [ -d {_quote(sparse_root)} ]; then find {_quote(sparse_root)} -maxdepth 1 -type f -exec mv -f {{}} {_quote(sparse_0)} \\; ; fi",
    f"touch {_quote(marker_path)}",
  ]
  return _wrap_remote_colmap_headless_script(" && ".join(steps))


def _remote_post_train_helper_functions() -> str:
  return r'''
resolve_gspl_explicit_name() {
  "$PYTHON_BIN" - "$REMOTE_COMMAND_TEXT" <<'PY'
import shlex
import sys

try:
  args = shlex.split(sys.argv[1])
except Exception:
  args = []
name = ""
for index, token in enumerate(args):
  if token in ("-n", "--name") and index + 1 < len(args):
    name = args[index + 1]
  elif token.startswith("--name="):
    name = token.split("=", 1)[1]
print(name)
PY
}

resolve_gspl_output_dir() {
  "$PYTHON_BIN" - "$OUTPUT_DIR" "$REMOTE_COMMAND_TEXT" "$REMOTE_DATASET_WORKSPACE" <<'PY'
from pathlib import Path
import os
import shlex
import sys

root = Path(sys.argv[1])
command = sys.argv[2]
workspace = sys.argv[3]
try:
  args = shlex.split(command)
except Exception:
  args = []

def explicit_name():
  for index, token in enumerate(args):
    if token in ("-n", "--name") and index + 1 < len(args):
      return args[index + 1]
    if token.startswith("--name="):
      return token.split("=", 1)[1]
  return ""

def auto_name():
  parts = Path(str(workspace).strip("/")).parts[-3:]
  return "_".join(parts)

def add_candidate(items, path):
  try:
    if path.exists() and path.is_dir():
      items.append((path.stat().st_mtime, path.resolve()))
  except Exception:
    pass

candidates = []
for name in (explicit_name(), auto_name()):
  if name:
    add_candidate(candidates, root / name)
add_candidate(candidates, root)

try:
  for ckpt in root.rglob("checkpoints/*.ckpt"):
    add_candidate(candidates, ckpt.parent.parent)
  for ply in root.rglob("point_cloud/iteration_*/point_cloud.ply"):
    add_candidate(candidates, ply.parents[2])
except Exception:
  pass

filtered = []
seen = set()
for mtime, path in candidates:
  key = str(path)
  if key in seen:
    continue
  seen.add(key)
  if (path / "checkpoints").exists() or (path / "point_cloud").exists():
    filtered.append((mtime, path))

if filtered:
  filtered.sort(key=lambda item: (item[0], str(item[1])), reverse=True)
  print(filtered[0][1])
PY
}

latest_gspl_checkpoint() {
  "$PYTHON_BIN" - "$1" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
items = []
try:
  for path in root.rglob("checkpoints/*.ckpt"):
    items.append((path.stat().st_mtime, path.resolve()))
except Exception:
  pass
if items:
  items.sort(key=lambda item: (item[0], str(item[1])), reverse=True)
  print(items[0][1])
PY
}

resolve_model_iteration() {
  "$PYTHON_BIN" - "$1" "$2" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
requested = str(sys.argv[2] or "").strip()
if requested and requested != "-1":
  print(requested)
  raise SystemExit(0)

candidates = []
point_cloud_dir = root / "point_cloud"
try:
  for child in point_cloud_dir.iterdir():
    if not child.is_dir() or not child.name.startswith("iteration_"):
      continue
    try:
      iteration = int(child.name.split("_")[-1])
    except Exception:
      continue
    if (child / "point_cloud.ply").is_file():
      candidates.append(iteration)
except Exception:
  pass

if candidates:
  print(max(candidates))
PY
}

ensure_3dgs_cfg_args() {
  "$PYTHON_BIN" - "$1" "$2" "$3" "$4" <<'PY'
from argparse import Namespace
from pathlib import Path
import os
import shlex
import sys

model_arg = str(sys.argv[1] or "").strip()
source_arg = str(sys.argv[2] or "").strip()
command_text = str(sys.argv[3] or "")
iteration_arg = str(sys.argv[4] or "").strip()

try:
  command_tokens = shlex.split(command_text)
except Exception:
  command_tokens = []

def cli_value(*names):
  for index, token in enumerate(command_tokens):
    for name in names:
      if token == name and index + 1 < len(command_tokens):
        return command_tokens[index + 1]
      if token.startswith(name + "="):
        return token.split("=", 1)[1]
  return ""

def cli_flag(*names):
  return any(token in names for token in command_tokens)

model_value = model_arg or cli_value("-m", "--model_path")
if not model_value:
  print("[Reduced-3DGS] Unable to determine model_path. render.py needs -m pointing at the trained output directory.", file=sys.stderr)
  raise SystemExit(70)

model_dir = Path(model_value).expanduser()
if not model_dir.is_dir():
  print(
    f"[Reduced-3DGS] model_path does not exist: {model_dir}. "
    "-m must point to the output directory containing point_cloud and cfg_args.",
    file=sys.stderr,
  )
  raise SystemExit(70)

point_cloud_dir = model_dir / "point_cloud"
if not point_cloud_dir.is_dir():
  print(
    f"[Reduced-3DGS] Missing point_cloud directory under {model_dir}. "
    "Do not pass the parent run directory as -m; pass the real output directory.",
    file=sys.stderr,
  )
  raise SystemExit(71)

def latest_iteration():
  candidates = []
  for child in point_cloud_dir.iterdir():
    if not child.is_dir() or not child.name.startswith("iteration_"):
      continue
    try:
      iteration = int(child.name.split("_")[-1])
    except Exception:
      continue
    if (child / "point_cloud.ply").is_file():
      candidates.append(iteration)
  return str(max(candidates)) if candidates else ""

iteration = iteration_arg if iteration_arg and iteration_arg != "-1" else ""
iteration = iteration or cli_value("--iteration") or cli_value("--iterations") or latest_iteration()
if not iteration:
  print(f"[Reduced-3DGS] No point_cloud/iteration_*/point_cloud.ply found under {model_dir}.", file=sys.stderr)
  raise SystemExit(71)

point_cloud_path = point_cloud_dir / f"iteration_{iteration}" / "point_cloud.ply"
if not point_cloud_path.is_file():
  print(f"[Reduced-3DGS] Missing trained point cloud: {point_cloud_path}", file=sys.stderr)
  raise SystemExit(71)

cfg_path = model_dir / "cfg_args"
if cfg_path.is_file():
  print(f"[Reduced-3DGS] cfg_args exists: {cfg_path}")
  raise SystemExit(0)

source_value = source_arg or cli_value("-s", "--source_path")
if not source_value:
  print(
    f"[Reduced-3DGS] Missing cfg_args at {cfg_path} and source_path could not be inferred. "
    "Run render.py with -s/--source_path so cfg_args can be regenerated.",
    file=sys.stderr,
  )
  raise SystemExit(72)

def int_value(value, default):
  try:
    return int(value)
  except Exception:
    return default

values = {
  "sh_degree": int_value(cli_value("--sh_degree"), 3),
  "source_path": os.path.abspath(source_value),
  "model_path": os.path.abspath(str(model_dir)),
  "images": cli_value("--images") or "images",
  "resolution": int_value(cli_value("--resolution"), -1),
  "white_background": cli_flag("--white_background"),
  "data_device": cli_value("--data_device") or "cuda",
  "eval": True,
}
cfg_path.write_text(str(Namespace(**values)), encoding="utf-8")
print(f"[Reduced-3DGS] Created missing cfg_args: {cfg_path}")
PY
}

write_canonical_result_json() {
  "$PYTHON_BIN" - "$1" <<'PY'
from pathlib import Path
import csv
import json
import math
import re
import sys
import time

root = Path(sys.argv[1])
target = root / "result.json"
number_re = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")

def number(value):
  if isinstance(value, bool) or value is None:
    return None
  if isinstance(value, (int, float)):
    value = float(value)
    return value if math.isfinite(value) else None
  match = number_re.search(str(value).replace(",", ""))
  if not match:
    return None
  try:
    value = float(match.group(0))
  except Exception:
    return None
  return value if math.isfinite(value) else None

def key_id(value):
  return re.sub(r"[^a-z0-9]+", "", str(value).lower())

def collect_json_metrics(payload):
  psnrs = []
  ssims = []
  lpipss = []
  def walk(obj, key=""):
    if isinstance(obj, dict):
      for child_key, child_value in obj.items():
        walk(child_value, child_key)
      return
    if isinstance(obj, list):
      for child in obj:
        walk(child, key)
      return
    value = number(obj)
    if value is None:
      return
    normalized = key_id(key)
    if normalized in {"psnr", "psnrdb"}:
      psnrs.append(value)
    if normalized == "ssim":
      ssims.append(value)
    if normalized == "lpips":
      lpipss.append(value)
  walk(payload)
  return (
    sum(psnrs) / len(psnrs) if psnrs else None,
    sum(ssims) / len(ssims) if ssims else None,
    sum(lpipss) / len(lpipss) if lpipss else None,
  )

def read_json(path):
  try:
    with open(path, "r", encoding="utf-8") as handle:
      payload = json.load(handle)
    return payload if isinstance(payload, dict) else {}
  except Exception:
    return {}

def csv_metrics(path):
  try:
    with open(path, "r", encoding="utf-8", newline="") as handle:
      rows = list(csv.reader(handle))
  except Exception:
    return None, None, None
  if not rows:
    return None, None, None
  header = rows[0]
  data_rows = rows[1:]
  selected = next((row for row in data_rows if row and str(row[0]).strip().upper() == "MEAN"), None)
  if selected is None and data_rows:
    selected = data_rows[-1]
  if selected is None:
    return None, None, None
  psnr = None
  ssim = None
  lpips = None
  for index, name in enumerate(header):
    if index >= len(selected):
      continue
    normalized = key_id(name)
    value = number(selected[index])
    if value is None:
      continue
    if "psnr" in normalized:
      psnr = value
    if normalized.endswith("ssim") and "msssim" not in normalized:
      ssim = value
    if normalized == "lpips":
      lpips = value
  return psnr, ssim, lpips

payload = read_json(target)
psnr, ssim, lpips = collect_json_metrics(payload)
source = "result.json" if payload else ""

if psnr is None or ssim is None or lpips is None:
  candidates = []
  for path in root.rglob("results.json"):
    try:
      candidates.append((path.stat().st_mtime, path))
    except Exception:
      pass
  candidates.sort(key=lambda item: (item[0], str(item[1])), reverse=True)
  for _, path in candidates:
    candidate_psnr, candidate_ssim, candidate_lpips = collect_json_metrics(read_json(path))
    if candidate_psnr is not None or candidate_ssim is not None or candidate_lpips is not None:
      psnr = psnr if psnr is not None else candidate_psnr
      ssim = ssim if ssim is not None else candidate_ssim
      lpips = lpips if lpips is not None else candidate_lpips
      try:
        source = str(path.relative_to(root))
      except Exception:
        source = str(path)
      break

if psnr is None or ssim is None or lpips is None:
  candidates = []
  metric_dir = root / "metrics"
  if metric_dir.exists():
    for path in metric_dir.glob("*.csv"):
      try:
        candidates.append((path.stat().st_mtime, path))
      except Exception:
        pass
  candidates.sort(key=lambda item: (item[0], str(item[1])), reverse=True)
  for _, path in candidates:
    candidate_psnr, candidate_ssim, candidate_lpips = csv_metrics(path)
    if candidate_psnr is not None or candidate_ssim is not None or candidate_lpips is not None:
      psnr = psnr if psnr is not None else candidate_psnr
      ssim = ssim if ssim is not None else candidate_ssim
      lpips = lpips if lpips is not None else candidate_lpips
      try:
        source = str(path.relative_to(root))
      except Exception:
        source = str(path)
      break

if psnr is not None:
  payload["psnr"] = psnr
  payload["psnr_db"] = psnr
  payload["PSNR"] = psnr
if ssim is not None:
  payload["ssim"] = ssim
  payload["SSIM"] = ssim
if lpips is not None:
  payload["lpips"] = lpips
  payload["LPIPS"] = lpips
if psnr is not None or ssim is not None or lpips is not None:
  payload["source"] = source
  payload["generated_at"] = time.time()
  with open(target, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False, indent=2)
if psnr is not None and ssim is not None and lpips is not None:
  sys.exit(0)
if target.exists():
  sys.exit(2)
sys.exit(3)
PY
}
'''


def _remote_post_train_eval_block(enabled: bool, post_train: Dict[str, Any] | None = None) -> str:
  if not enabled:
    return ""
  config = post_train if isinstance(post_train, dict) else {}
  run_render = "1" if config.get("run_render") and not config.get("train_produces_eval") else "0"
  run_metrics = "1" if config.get("run_metrics") and not config.get("train_produces_eval") else "0"
  render_command = _quote(str(config.get("render_command", "") or ""))
  metrics_command = _quote(str(config.get("metrics_command", "") or ""))
  iteration = _quote(str(config.get("iteration") or "30000"))
  return r'''
  POST_TRAIN_RUN_RENDER=''' + _quote(run_render) + r'''
  POST_TRAIN_RUN_METRICS=''' + _quote(run_metrics) + r'''
  POST_TRAIN_RENDER_COMMAND=''' + render_command + r'''
  POST_TRAIN_METRICS_COMMAND=''' + metrics_command + r'''
  POST_TRAIN_ITERATION=''' + iteration + r'''
  if command -v wgsc_activate_env >/dev/null 2>&1; then
    wgsc_activate_env
  fi
  cd "$REMOTE_REPO_DIR" || finish_with "failed" "render" "65" "Unable to enter remote repository for render/metrics."
  EVAL_OUTPUT_DIR="$OUTPUT_DIR"
  if [ "$FAMILY" = "gaussian-splatting-lightning" ]; then
    EVAL_OUTPUT_DIR="$(resolve_gspl_output_dir)"
    if [ -z "$EVAL_OUTPUT_DIR" ]; then
      POSTPROCESS_STATUS="${POSTPROCESS_STATUS:-skipped}"
      RENDER_STATUS="failed"
      METRICS_STATUS="skipped"
      RESULT_JSON_EXISTS="false"
      FAILURE_STAGE="render"
      write_status "failed" "render" "67" "Unable to locate GSLightning experiment output dir for render/metrics."
      finish_with "failed" "render" "67" "Unable to locate GSLightning experiment output dir for render/metrics."
    fi
  fi

  if [ "$FAMILY" = "reduced-3dgs" ] && { [ "$POST_TRAIN_RUN_RENDER" = "1" ] || [ "$POST_TRAIN_RUN_METRICS" = "1" ]; }; then
    ensure_3dgs_cfg_args "$EVAL_OUTPUT_DIR" "$REMOTE_DATASET_WORKSPACE" "$REMOTE_COMMAND_TEXT" "$POST_TRAIN_ITERATION"
    cfg_rc=$?
    if [ "$cfg_rc" -ne 0 ]; then
      RENDER_STATUS="failed"
      METRICS_STATUS="skipped"
      RESULT_JSON_EXISTS="false"
      FAILURE_STAGE="render"
      write_status "failed" "render" "$cfg_rc" "Reduced-3DGS output is missing cfg_args or point_cloud; render.py needs -m output and -s source_path."
      finish_with "failed" "render" "$cfg_rc" "Reduced-3DGS output is missing cfg_args or point_cloud; pass -s/--source_path and ensure -m points at the real output directory."
    fi
    resolved_iteration="$(resolve_model_iteration "$EVAL_OUTPUT_DIR" "$POST_TRAIN_ITERATION")"
    if [ -n "$resolved_iteration" ]; then
      POST_TRAIN_ITERATION="$resolved_iteration"
    fi
  fi

  if [ "$POST_TRAIN_RUN_RENDER" = "1" ]; then
    write_status "running" "render" "" "Training completed; running render stage."
    if [ -n "$POST_TRAIN_RENDER_COMMAND" ]; then
      eval "$POST_TRAIN_RENDER_COMMAND"
      render_rc=$?
    else
      case "$FAMILY" in
        "gaussian-splatting-lightning")
          latest_ckpt="$(latest_gspl_checkpoint "$EVAL_OUTPUT_DIR")"
          if [ -z "$latest_ckpt" ]; then
            render_rc=68
          else
            gspl_name="$(resolve_gspl_explicit_name)"
            if [ -n "$gspl_name" ]; then
              "$PYTHON_BIN" main.py validate --data.path "$REMOTE_DATASET_WORKSPACE" --output "$OUTPUT_DIR" -n "$gspl_name" --ckpt_path "$latest_ckpt" --save_val
            else
              "$PYTHON_BIN" main.py validate --data.path "$REMOTE_DATASET_WORKSPACE" --output "$OUTPUT_DIR" --ckpt_path "$latest_ckpt" --save_val
            fi
            render_rc=$?
          fi
          ;;
        "hac-plus-plus")
          "$PYTHON_BIN" render.py -s "$REMOTE_DATASET_WORKSPACE" -m "$EVAL_OUTPUT_DIR"
          render_rc=$?
          ;;
        "contextgs")
          "$PYTHON_BIN" test.py -s "$REMOTE_DATASET_WORKSPACE" -m "$EVAL_OUTPUT_DIR"
          render_rc=$?
          ;;
        "reduced-3dgs")
          "$PYTHON_BIN" render.py -m "$EVAL_OUTPUT_DIR" -s "$REMOTE_DATASET_WORKSPACE" --iteration "$POST_TRAIN_ITERATION"
          render_rc=$?
          ;;
        *)
          "$PYTHON_BIN" render.py -m "$EVAL_OUTPUT_DIR" --skip_train
          render_rc=$?
          ;;
      esac
    fi
    if [ "$render_rc" -ne 0 ]; then
      RENDER_STATUS="failed"
      METRICS_STATUS="skipped"
      RESULT_JSON_EXISTS="false"
      FAILURE_STAGE="render"
      write_status "failed" "render" "$render_rc" "Training succeeded, but render failed."
      finish_with "failed" "render" "$render_rc" "Training succeeded, but render failed."
    fi
    RENDER_STATUS="success"
  fi

  if [ "$POST_TRAIN_RUN_METRICS" = "1" ]; then
    write_status "running" "metrics" "" "Render completed; running metrics stage."
    if command -v wgsc_activate_env >/dev/null 2>&1; then
      wgsc_activate_env
    fi
    cd "$REMOTE_REPO_DIR" || finish_with "failed" "metrics" "65" "Unable to enter remote repository for metrics."
    if [ -n "$POST_TRAIN_METRICS_COMMAND" ]; then
      eval "$POST_TRAIN_METRICS_COMMAND"
      metrics_rc=$?
    else
      case "$FAMILY" in
        "gaussian-splatting-lightning"|"hac-plus-plus"|"contextgs")
          metrics_rc=0
          ;;
        *)
          if [ -f metrics.py ]; then
            "$PYTHON_BIN" metrics.py -m "$EVAL_OUTPUT_DIR"
            metrics_rc=$?
          else
            metrics_rc=69
          fi
          ;;
      esac
    fi
    if [ "$metrics_rc" -ne 0 ]; then
      METRICS_STATUS="failed"
      RESULT_JSON_EXISTS="false"
      FAILURE_STAGE="metrics"
      write_status "failed" "metrics" "$metrics_rc" "Training/render succeeded, but metrics failed."
      finish_with "failed" "metrics" "$metrics_rc" "Training/render succeeded, but metrics failed."
    fi
    METRICS_STATUS="success"
  fi

  write_status "running" "postprocess" "" "Checking result.json and canonical metrics."
  write_canonical_result_json "$EVAL_OUTPUT_DIR"
  canonical_rc=$?
  if [ -f "$EVAL_OUTPUT_DIR/result.json" ]; then
    RESULT_JSON_EXISTS="true"
    RESULT_JSON_PATH="$EVAL_OUTPUT_DIR/result.json"
  else
    RESULT_JSON_EXISTS="false"
    RESULT_JSON_PATH=""
  fi
  if [ "$canonical_rc" -ne 0 ]; then
    POSTPROCESS_STATUS="failed"
    FAILURE_STAGE="postprocess"
    write_status "failed" "postprocess" "$canonical_rc" "result.json missing because result processing did not complete."
    finish_with "failed" "postprocess" "$canonical_rc" "result.json missing because result processing did not complete."
  fi
  POSTPROCESS_STATUS="success"
  write_status "running" "postprocess_completed" "" "Result processing completed; result.json is available."
'''


def _build_remote_detached_run_script(
  *,
  validated: Dict[str, Any],
  job_id: str,
  family: str,
  remote_paths: Dict[str, str],
  remote_job_paths: Dict[str, str],
  remote_workspace_for_command: str,
  remote_command: str,
  auto_colmap: bool,
  resolved_dataset_id: str,
  resolved_dataset_name: str,
  use_existing_remote_dataset: bool,
  post_train_config: Dict[str, Any] | None = None,
) -> str:
  auto_colmap = bool(auto_colmap) and not bool(use_existing_remote_dataset)
  colmap_block = ""
  if auto_colmap:
    colmap_script = _build_remote_colmap_script(remote_workspace_for_command)
    sparse_test = _remote_sparse_test_script(remote_workspace_for_command)
    colmap_block = f"""if [ "$AUTO_COLMAP" = "1" ]; then
  if {sparse_test}; then
    echo "[AutoCOLMAP] Skip: sparse workspace already exists at $REMOTE_DATASET_WORKSPACE"
  else
    echo "[AutoCOLMAP] Running remote COLMAP preprocessing"
    write_status "running" "executing_remote_colmap" "" "Running remote COLMAP preprocessing."
    {colmap_script}
    colmap_rc=$?
    if [ "$colmap_rc" -ne 0 ]; then
      finish_with "failed" "colmap" "$colmap_rc" "Remote COLMAP preprocessing failed."
    fi
    write_status "running" "remote_colmap_completed" "" "Remote COLMAP preprocessing completed."
  fi
fi"""
  elif use_existing_remote_dataset:
    colmap_block = 'echo "[Dataset] Reusing existing remote dataset: $REMOTE_DATASET_ID"'

  activate_cmd = str(validated.get("activate_cmd", "")).strip()
  environment_lines = [
    f"cd {_quote(validated['repo_path'])}",
  ]
  if activate_cmd:
    environment_lines.append("[ -f ~/.bash_profile ] && source ~/.bash_profile 2>/dev/null || true")
    environment_lines.append("[ -f ~/.bashrc ] && source ~/.bashrc 2>/dev/null || true")
    environment_lines.append(
      '{ if command -v conda >/dev/null 2>&1; then '
      'eval "$(conda shell.bash hook)" || true; '
      'elif [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then '
      'source "$HOME/miniconda3/etc/profile.d/conda.sh" || true; '
      'elif [ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]; then '
      'source "$HOME/anaconda3/etc/profile.d/conda.sh" || true; '
      'elif [ -f "/opt/conda/etc/profile.d/conda.sh" ]; then '
      'source "/opt/conda/etc/profile.d/conda.sh" || true; '
      'fi; }'
    )
    environment_lines.append(activate_cmd)
  activate_body = "\n".join(environment_lines[1:]) if len(environment_lines) > 1 else ":"
  training_lines = [*environment_lines, remote_command]
  training_body = "\n".join(training_lines)
  post_train_helpers = _remote_post_train_helper_functions()
  post_train = _prepare_post_train_config(
    post_train_config if isinstance(post_train_config, dict) else {},
    {
      "input_path": "",
      "output_dir": remote_paths["output_dir"],
      "workspace": remote_workspace_for_command,
      "checkpoint_path": remote_paths["output_dir"],
      "repo_path": validated["repo_path"],
      "source": remote_workspace_for_command,
    },
    validated["python"],
    family,
  )
  post_train_eval_block = _remote_post_train_eval_block(should_run_post_train_eval(remote_command, post_train), post_train)

  post_train_export_block = ""
  if str(family or "").strip().lower() == "gaussian-splatting-lightning":
    export_lines = [
      *environment_lines,
      'if ! test -f utils/ckpt2ply.py; then',
      '  echo "[GSLightning] Missing utils/ckpt2ply.py in remote repo: $(pwd)" >&2',
      '  exit 66',
      'fi',
      'REAL_OUTPUT_DIR="$(resolve_gspl_output_dir)"',
      'if [ -z "$REAL_OUTPUT_DIR" ]; then',
      '  echo "[GSLightning] Unable to locate experiment output dir under $OUTPUT_DIR" >&2',
      '  exit 67',
      'fi',
      'echo "[GSLightning] Running python utils/ckpt2ply.py \\"$REAL_OUTPUT_DIR\\" --override"',
      '"$PYTHON_BIN" utils/ckpt2ply.py "$REAL_OUTPUT_DIR" --override',
    ]
    export_body = "\n".join(export_lines)
    post_train_export_block = f"""
  write_status "running" "exporting_gaussian_ply" "" "Exporting complete Gaussian PLY from GSLightning checkpoint."
  (
{export_body}
  )
	  export_rc=$?
	  if [ "$export_rc" -ne 0 ]; then
	    POSTPROCESS_STATUS="failed"
	    FAILURE_STAGE="postprocess"
	    write_status "failed" "postprocess" "$export_rc" "GSLightning checkpoint to PLY export failed."
	    finish_with "failed" "postprocess" "$export_rc" "GSLightning checkpoint to PLY export failed."
	  fi"""

  return f"""#!/usr/bin/env bash
set +e

PYTHON_BIN={_quote(validated["python"])}
REMOTE_REPO_DIR={_quote(validated["repo_path"])}
JOB_ID={_quote(job_id)}
FAMILY={_quote(family)}
JOB_DIR={_quote(remote_job_paths["job_dir"])}
STATUS_FILE={_quote(remote_job_paths["status_file"])}
EXIT_CODE_FILE={_quote(remote_job_paths["exit_code_file"])}
LOG_FILE={_quote(remote_job_paths["runtime_log"])}
PID_FILE={_quote(remote_job_paths["pid_file"])}
OUTPUT_DIR={_quote(remote_paths["output_dir"])}
REMOTE_DATASET_ID={_quote(resolved_dataset_id)}
REMOTE_DATASET_NAME={_quote(resolved_dataset_name)}
REMOTE_DATASET_WORKSPACE={_quote(remote_workspace_for_command)}
REMOTE_COMMAND_TEXT={_quote(remote_command)}
AUTO_COLMAP={_quote("1" if auto_colmap else "0")}
USE_EXISTING_REMOTE_DATASET={_quote("1" if use_existing_remote_dataset else "0")}
TRAIN_STATUS="pending"
RENDER_STATUS="skipped"
METRICS_STATUS="skipped"
	POSTPROCESS_STATUS="skipped"
	RESULT_JSON_EXISTS="false"
	RESULT_JSON_PATH=""
	FAILURE_STAGE=""

mkdir -p "$JOB_DIR" "$OUTPUT_DIR"
echo "$$" > "$PID_FILE"

write_status() {{
  local status="$1"
  local stage="$2"
  local return_code="${{3:-}}"
  local message="${{4:-}}"
  WGSC_STATUS="$status" \\
  WGSC_STAGE="$stage" \\
  WGSC_RETURN_CODE="$return_code" \\
  WGSC_MESSAGE="$message" \\
  WGSC_JOB_ID="$JOB_ID" \\
  WGSC_FAMILY="$FAMILY" \\
  WGSC_PID="$$" \\
  WGSC_LOG_PATH="$LOG_FILE" \\
  WGSC_OUTPUT_DIR="$OUTPUT_DIR" \\
  WGSC_DATASET_ID="$REMOTE_DATASET_ID" \\
  WGSC_DATASET_NAME="$REMOTE_DATASET_NAME" \\
  WGSC_DATASET_WORKSPACE="$REMOTE_DATASET_WORKSPACE" \\
  WGSC_REMOTE_COMMAND="$REMOTE_COMMAND_TEXT" \\
  WGSC_TRAIN_STATUS="$TRAIN_STATUS" \\
  WGSC_RENDER_STATUS="$RENDER_STATUS" \\
	  WGSC_METRICS_STATUS="$METRICS_STATUS" \\
	  WGSC_POSTPROCESS_STATUS="$POSTPROCESS_STATUS" \\
	  WGSC_RESULT_JSON_EXISTS="$RESULT_JSON_EXISTS" \\
	  WGSC_RESULT_JSON_PATH="$RESULT_JSON_PATH" \\
	  WGSC_FAILURE_STAGE="$FAILURE_STAGE" \\
  "$PYTHON_BIN" - "$STATUS_FILE" <<'PY'
import json
import os
import sys
import time

status_path = sys.argv[1]
payload = {{
  "job_id": os.environ.get("WGSC_JOB_ID", ""),
  "family": os.environ.get("WGSC_FAMILY", ""),
  "status": os.environ.get("WGSC_STATUS", ""),
  "stage": os.environ.get("WGSC_STAGE", ""),
  "pid": os.environ.get("WGSC_PID", ""),
  "message": os.environ.get("WGSC_MESSAGE", ""),
  "remote_log_path": os.environ.get("WGSC_LOG_PATH", ""),
  "remote_output_dir": os.environ.get("WGSC_OUTPUT_DIR", ""),
  "remote_dataset_id": os.environ.get("WGSC_DATASET_ID", ""),
  "remote_dataset_name": os.environ.get("WGSC_DATASET_NAME", ""),
  "remote_dataset_workspace": os.environ.get("WGSC_DATASET_WORKSPACE", ""),
  "remote_command": os.environ.get("WGSC_REMOTE_COMMAND", ""),
  "train_status": os.environ.get("WGSC_TRAIN_STATUS", ""),
  "render_status": os.environ.get("WGSC_RENDER_STATUS", ""),
  "metrics_status": os.environ.get("WGSC_METRICS_STATUS", ""),
	  "postprocess_status": os.environ.get("WGSC_POSTPROCESS_STATUS", ""),
	  "result_json_exists": os.environ.get("WGSC_RESULT_JSON_EXISTS", "").lower() == "true",
	  "result_path": os.environ.get("WGSC_RESULT_JSON_PATH", ""),
	  "failure_stage": os.environ.get("WGSC_FAILURE_STAGE", ""),
  "updated_at": time.time(),
}}
return_code = os.environ.get("WGSC_RETURN_CODE", "")
if return_code not in ("", None):
  try:
    payload["return_code"] = int(return_code)
  except Exception:
    payload["return_code"] = return_code
os.makedirs(os.path.dirname(status_path), exist_ok=True)
tmp_path = f"{{status_path}}.tmp.{{os.getpid()}}"
with open(tmp_path, "w", encoding="utf-8") as handle:
  json.dump(payload, handle, ensure_ascii=False, indent=2)
os.replace(tmp_path, status_path)
PY
}}

start_heartbeat() {{
  stop_heartbeat
  (
    while true; do
      sleep 30
      "$PYTHON_BIN" - "$STATUS_FILE" <<'PY' || true
import json
import os
import sys
import time

status_path = sys.argv[1]
try:
  with open(status_path, "r", encoding="utf-8") as handle:
    payload = json.load(handle)
except Exception:
  payload = {{}}
if str(payload.get("status", "")).lower() in ("completed", "failed", "canceled"):
  sys.exit(0)
payload["updated_at"] = time.time()
payload.setdefault("status", "running")
payload.setdefault("stage", "executing_remote_command")
os.makedirs(os.path.dirname(status_path), exist_ok=True)
tmp_path = f"{{status_path}}.tmp.heartbeat.{{os.getpid()}}"
with open(tmp_path, "w", encoding="utf-8") as handle:
  json.dump(payload, handle, ensure_ascii=False, indent=2)
os.replace(tmp_path, status_path)
PY
    done
  ) &
  HEARTBEAT_PID="$!"
}}

stop_heartbeat() {{
  if [ -n "${{HEARTBEAT_PID:-}}" ]; then
    kill "$HEARTBEAT_PID" >/dev/null 2>&1 || true
    wait "$HEARTBEAT_PID" >/dev/null 2>&1 || true
    HEARTBEAT_PID=""
  fi
}}

finish_with() {{
  local status="$1"
  local stage="$2"
  local return_code="$3"
  local message="${{4:-}}"
  stop_heartbeat
  echo "$return_code" > "$EXIT_CODE_FILE"
  write_status "$status" "$stage" "$return_code" "$message"
  exit "$return_code"
}}

{post_train_helpers}

wgsc_activate_env() {{
{activate_body}
}}

echo "[Web-GSC] Tmux remote job started at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
write_status "running" "remote_detached_running" "" "Remote tmux job is running. It is safe to close the web page."

{colmap_block}

write_status "running" "executing_remote_command" "" "Running remote training command."
start_heartbeat
(
{training_body}
)
train_rc=$?
stop_heartbeat
if [ "$train_rc" -eq 0 ]; then
  TRAIN_STATUS="success"
{post_train_export_block}
{post_train_eval_block}
  finish_with "completed" "completed" "$train_rc" "Remote training completed."
fi
TRAIN_STATUS="failed"
finish_with "failed" "failed" "$train_rc" "Remote training failed."
"""


def _build_remote_detached_start_command(remote_job_paths: Dict[str, str]) -> str:
  starting_status = json.dumps({
    "status": "starting",
    "stage": "remote_detached_starting",
    "message": "Remote detached job is starting.",
    "remote_log_path": remote_job_paths["runtime_log"],
    "remote_output_dir": str(PurePosixPath(remote_job_paths["job_dir"]).parent),
    "updated_at": 0,
  }, ensure_ascii=False)
  return (
    f"mkdir -p {_quote(remote_job_paths['job_dir'])}"
    f" && rm -f {_quote(remote_job_paths['pid_file'])} {_quote(remote_job_paths['launcher_pid_file'])} "
    f"{_quote(remote_job_paths['exit_code_file'])}"
    f" && status_tmp={_quote(remote_job_paths['status_file'] + '.tmp.start')}-$$"
    f" && printf '%s\\n' {_quote(starting_status)} > \"$status_tmp\""
    f" && mv -f \"$status_tmp\" {_quote(remote_job_paths['status_file'])}"
    f" && chmod +x {_quote(remote_job_paths['run_script'])}"
    f" && (cd {_quote(remote_job_paths['job_dir'])}"
    f" && (setsid nohup bash {_quote(remote_job_paths['run_script'])} >> {_quote(remote_job_paths['runtime_log'])} 2>&1 < /dev/null & echo $! > {_quote(remote_job_paths['launcher_pid_file'])}))"
    " && sleep 0.5"
  )


def _build_remote_tmux_start_command(remote_job_paths: Dict[str, str], tmux_session: str) -> str:
  starting_status = json.dumps({
    "status": "starting",
    "stage": "remote_detached_starting",
    "message": "Remote tmux job is starting.",
    "remote_log_path": remote_job_paths["runtime_log"],
    "remote_tmux_session": tmux_session,
    "remote_output_dir": str(PurePosixPath(remote_job_paths["job_dir"]).parent),
    "updated_at": 0,
  }, ensure_ascii=False)
  launcher_script = f"""tmux set-window-option remain-on-exit on >/dev/null 2>&1 || true
echo "[Tmux] Session {tmux_session} started at $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a {_quote(remote_job_paths['runtime_log'])}
set -o pipefail
bash {_quote(remote_job_paths['run_script'])} 2>&1 | tee -a {_quote(remote_job_paths['runtime_log'])}
run_rc=${{PIPESTATUS[0]}}
echo "[Tmux] run.sh exited with code $run_rc at $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a {_quote(remote_job_paths['runtime_log'])}
exit "$run_rc"
"""
  tmux_shell_command = f"bash -lc {_quote(launcher_script)}"
  return (
    f"mkdir -p {_quote(remote_job_paths['job_dir'])}"
    f" && rm -f {_quote(remote_job_paths['pid_file'])} {_quote(remote_job_paths['launcher_pid_file'])} "
    f"{_quote(remote_job_paths['exit_code_file'])}"
    f" && : > {_quote(remote_job_paths['runtime_log'])}"
    f" && status_tmp={_quote(remote_job_paths['status_file'] + '.tmp.start')}-$$"
    f" && printf '%s\\n' {_quote(starting_status)} > \"$status_tmp\""
    f" && mv -f \"$status_tmp\" {_quote(remote_job_paths['status_file'])}"
    f" && chmod +x {_quote(remote_job_paths['run_script'])}"
    f" && if tmux has-session -t {_quote(tmux_session)} 2>/dev/null; then tmux kill-session -t {_quote(tmux_session)}; fi"
    f" && tmux new-session -d -s {_quote(tmux_session)} -c {_quote(remote_job_paths['job_dir'])} {_quote(tmux_shell_command)}"
    f" && tmux display-message -p -t {_quote(tmux_session)} {_quote('#{pane_pid}')} > {_quote(remote_job_paths['launcher_pid_file'])}"
    " && sleep 0.5"
  )


def start_remote_algorithm_detached(
  *,
  remote_config: Dict[str, Any],
  local_workspace_dir: str,
  local_output_dir: str,
  session_id: str,
  family: str,
  job_id: str,
  command_template: str,
  checkpoint_path: str,
  input_path: str,
  source_path: str,
  auto_colmap: bool = True,
  dataset_name: str = "",
  remote_dataset_id: str = "",
  remote_dataset_path: str = "",
  use_existing_remote_dataset: bool = False,
  training_args: Dict[str, Any] | None = None,
  command_override: str = "",
  remote_run_key: str = "",
  post_train_config: Dict[str, Any] | None = None,
  log_callback: Callable[[str, str], None] | None = None,
  stage_callback: Callable[[str], None] | None = None,
  cancel_checker: Callable[[], bool] | None = None,
) -> Dict[str, Any]:
  validated = validate_remote_config(remote_config)
  auto_colmap = bool(auto_colmap) and not bool(use_existing_remote_dataset)

  workspace_dir: Path | None = None
  if not use_existing_remote_dataset:
    workspace_dir = Path(local_workspace_dir).expanduser().resolve()
    if not workspace_dir.exists() or not workspace_dir.is_dir():
      raise RemoteExecutionError(f"Local workspace directory not found: {workspace_dir}")

  output_dir = Path(local_output_dir).expanduser().resolve()
  output_dir.mkdir(parents=True, exist_ok=True)

  resolved_dataset_id = str(remote_dataset_id or "").strip()
  resolved_dataset_name = str(dataset_name or "").strip() or resolved_dataset_id

  if use_existing_remote_dataset:
    selected_path = str(remote_dataset_path or "").strip()
    if selected_path:
      remote_workspace_for_command = str(PurePosixPath(selected_path))
      if not resolved_dataset_id:
        resolved_dataset_id = PurePosixPath(remote_workspace_for_command).parent.name
    elif resolved_dataset_id:
      remote_workspace_for_command = str(
        PurePosixPath(validated["workspace_root"]) / "datasets" / family / resolved_dataset_id / "workspace"
      )
    else:
      raise RemoteExecutionError(
        "No remote dataset was selected. Choose an existing dataset before running this operation.",
        code_hint="WGSC-STEP5-DATASET-SELECT-001",
        stage="dataset",
      )
  else:
    resolved_dataset_id = resolved_dataset_id or _normalize_dataset_id(resolved_dataset_name, job_id)
    resolved_dataset_name = resolved_dataset_name or resolved_dataset_id
    remote_workspace_for_command = str(
      PurePosixPath(validated["workspace_root"]) / "datasets" / family / resolved_dataset_id / "workspace"
    )

  run_key = str(remote_run_key or "").strip() or build_remote_run_key(
    resolved_dataset_name or resolved_dataset_id or session_id or job_id,
    family,
    time.time(),
  )
  remote_paths = build_remote_paths(
    workspace_root=validated["workspace_root"],
    output_root=validated["output_root"],
    session_id=session_id,
    family=family,
    job_id=job_id,
    run_key=run_key,
  )
  remote_job_paths = _remote_job_paths(remote_paths["output_dir"])
  remote_tmux_session = build_remote_tmux_session_name(family=family, job_id=job_id, run_key=run_key)
  remote_tmux_attach_command = build_remote_tmux_attach_command(validated, remote_tmux_session)

  format_args = {
    "input_path": input_path,
    "output_dir": remote_paths["output_dir"],
    "workspace": remote_workspace_for_command,
    "checkpoint_path": checkpoint_path,
    "repo_path": validated["repo_path"],
    "source": source_path or remote_workspace_for_command,
    **(training_args or {}),
  }
  if str(command_override or "").strip():
    remote_command = str(command_override).strip()
  else:
    remote_command = command_template.format(**format_args)
    try:
      remote_command = sanitize_formatted_command(command_template, remote_command, format_args)
    except Exception:
      pass
    remote_command = apply_training_eval_arg(
      family,
      remote_command,
      user_disabled_eval=bool((training_args or {}).get("disable_eval") or (training_args or {}).get("no_eval")),
    )

  if remote_command.lstrip().startswith("python "):
    remote_command = f"{validated['python']}{remote_command.lstrip()[len('python') :]}"
  prepared_post_train_config = _prepare_post_train_config(
    post_train_config,
    format_args,
    validated["python"],
    family,
  )

  client = _new_ssh_client(validated)
  sftp = None

  try:
    def assert_not_cancelled() -> None:
      if cancel_checker and cancel_checker():
        raise RemoteExecutionError(
          "Remote job was canceled by user.",
          code_hint="WGSC-JOB-CANCELED",
          stage="canceled",
        )

    if stage_callback:
      stage_callback("connecting")
    client.connect(
      hostname=validated["host"],
      port=validated["port"],
      username=validated["username"],
      password=validated["password"],
      timeout=20,
      look_for_keys=False,
      allow_agent=False,
    )

    sftp = client.open_sftp()
    assert_not_cancelled()

    if stage_callback:
      stage_callback("checking_tmux")
    tmux_install = _ensure_remote_tmux_available(client, log_callback)
    assert_not_cancelled()

    if use_existing_remote_dataset:
      if stage_callback:
        stage_callback("using_existing_dataset")
      if not _remote_directory_exists(client, remote_workspace_for_command):
        raise RemoteExecutionError(
          f"Selected remote dataset workspace not found: {remote_workspace_for_command}",
          code_hint="WGSC-STEP5-DATASET-SELECT-001",
          stage="dataset",
        )
      upload_info = {
        "files": 0,
        "bytes": 0,
        "skipped": True,
      }
    else:
      if stage_callback:
        stage_callback("uploading_workspace")
      upload_info = _upload_directory(sftp, workspace_dir, remote_paths["workspace_dir"])
      assert_not_cancelled()

      if stage_callback:
        stage_callback("saving_named_dataset")
      remote_dataset_dir = str(PurePosixPath(remote_workspace_for_command).parent)
      persist_script = (
        f"mkdir -p {_quote(remote_dataset_dir)}"
        f" && rm -rf {_quote(remote_workspace_for_command)}"
        f" && cp -a {_quote(remote_paths['workspace_dir'])} {_quote(remote_workspace_for_command)}"
      )
      persist_rc = _run_remote_command_cancellable(
        client,
        f"bash -lc {_quote(persist_script)}",
        log_callback,
        cancel_checker,
      )
      if persist_rc != 0:
        raise RemoteExecutionError(
          "Failed to persist named remote dataset workspace.",
          code_hint="WGSC-STEP5-DATASET-NAME-001",
          stage="dataset",
        )

    if stage_callback:
      stage_callback("starting_detached_remote_job")
    run_script = _build_remote_detached_run_script(
      validated=validated,
      job_id=job_id,
      family=family,
      remote_paths=remote_paths,
      remote_job_paths=remote_job_paths,
      remote_workspace_for_command=remote_workspace_for_command,
      remote_command=remote_command,
      auto_colmap=auto_colmap,
      resolved_dataset_id=resolved_dataset_id,
      resolved_dataset_name=resolved_dataset_name,
      use_existing_remote_dataset=use_existing_remote_dataset,
      post_train_config=prepared_post_train_config,
    )
    _write_remote_text(sftp, remote_job_paths["run_script"], run_script)
    start_command = _build_remote_tmux_start_command(remote_job_paths, remote_tmux_session)
    start_rc = _run_remote_command(
      client,
      f"bash -lc {_quote(start_command)}",
      log_callback,
    )
    if start_rc != 0:
      raise RemoteExecutionError(
        "Failed to start detached remote job.",
        code_hint="WGSC-STEP5-REMOTE-DETACH-001",
        stage="detach",
      )

    remote_pid = _read_remote_text_file(sftp, remote_job_paths["pid_file"]).strip()
    launcher_pid = _read_remote_text_file(sftp, remote_job_paths["launcher_pid_file"]).strip()
    remote_pid = remote_pid or launcher_pid
    if log_callback:
      log_callback(
        "stdout",
        (
          f"[Tmux] Remote job started in tmux session {remote_tmux_session}. "
          f"PID: {remote_pid or '-'}; log: {remote_job_paths['runtime_log']}\n"
          f"[Tmux] Attach command: {remote_tmux_attach_command}\n"
        ),
      )
    if stage_callback:
      stage_callback("remote_detached_running")

    return {
      "return_code": None,
      "execution_backend": "tmux",
      "remote_detached": True,
      "safe_to_close_web": True,
      "monitor_state": "monitoring",
      "remote_pid": remote_pid,
      "remote_tmux_session": remote_tmux_session,
      "remote_tmux_attach_command": remote_tmux_attach_command,
      "tmux_available": True,
      "tmux_install": tmux_install,
      "remote_run_key": run_key,
      "remote_workspace_dir": remote_workspace_for_command,
      "remote_output_dir": remote_paths["output_dir"],
      "remote_job_dir": remote_job_paths["job_dir"],
      "remote_run_script": remote_job_paths["run_script"],
      "remote_log_path": remote_job_paths["runtime_log"],
      "remote_status_path": remote_job_paths["status_file"],
      "remote_exit_code_path": remote_job_paths["exit_code_file"],
      "remote_dataset_id": resolved_dataset_id,
      "remote_dataset_name": resolved_dataset_name,
      "remote_dataset_workspace": remote_workspace_for_command,
      "remote_command": remote_command,
      "upload": upload_info,
      "download": {"files": 0, "bytes": 0, "pending": True},
    }
  finally:
    _close_ssh_resources(client, sftp)


def poll_remote_detached_job(
  *,
  remote_config: Dict[str, Any],
  remote_status_path: str,
  remote_log_path: str,
  remote_output_dir: str,
  local_output_dir: str,
  log_cursor: int = 0,
  download_output: bool = False,
  download_progress_callback: Callable[[Dict[str, Any]], None] | None = None,
  expected_job_id: str = "",
  expected_family: str = "",
) -> Dict[str, Any]:
  validated = validate_remote_config(remote_config)
  client = _new_ssh_client(validated)
  sftp = None
  try:
    client.connect(
      hostname=validated["host"],
      port=validated["port"],
      username=validated["username"],
      password=validated["password"],
      timeout=20,
      look_for_keys=False,
      allow_agent=False,
    )
    sftp = client.open_sftp()
    status_read_error: Exception | None = None
    try:
      status_payload = _read_remote_json_file_strict(sftp, remote_status_path)
    except Exception as exc:
      status_read_error = exc
      status_payload = {}
    log_delta = _read_remote_log_delta(sftp, remote_log_path, log_cursor)

    if status_read_error is not None:
      exit_code_text = _read_remote_text_file(sftp, _remote_exit_code_path_from_status_path(remote_status_path)).strip()
      if exit_code_text:
        try:
          return_code: Any = int(exit_code_text)
        except Exception:
          return_code = exit_code_text
        status = _status_from_remote_return_code(return_code) if isinstance(return_code, int) else "failed"
        _recover_missing_status_marker(
          sftp,
          remote_status_path=remote_status_path,
          remote_log_path=remote_log_path,
          remote_output_dir=remote_output_dir,
          expected_job_id=expected_job_id,
          expected_family=expected_family,
          status=status,
          stage=status,
          return_code=return_code,
          message="Remote status marker was recovered from exit_code.",
        )
        download_info: Dict[str, Any] = {}
        if download_output and _downloadable_remote_status(status):
          download_info = _download_directory(
            sftp,
            remote_output_dir,
            Path(local_output_dir).expanduser().resolve(),
            progress_callback=download_progress_callback,
          )
        return {
          "ok": True,
          "status": status,
          "stage": status,
          "return_code": return_code,
          "remote_pid": "",
          "remote_tmux_session": "",
          "message": "Remote status marker was unavailable; recovered terminal state from exit_code.",
          "remote_log_path": remote_log_path,
          "remote_output_dir": remote_output_dir,
          "remote_dataset_id": "",
          "remote_dataset_name": "",
          "remote_dataset_workspace": "",
          "remote_command": "",
          "train_status": "success" if status == "completed" else "",
          "render_status": "",
          "metrics_status": "",
          "postprocess_status": "",
          "result_json_exists": False,
          "result_path": "",
          "partial_success": False,
          "failure_stage": "result_marker" if status == "failed" else "",
          "updated_at": time.time(),
          "log_text": log_delta["text"],
          "log_cursor": log_delta["cursor"],
          "log_available": log_delta["available"],
          "download": download_info,
          "status_fallback": "exit_code",
        }
      if log_delta["available"]:
        _recover_missing_status_marker(
          sftp,
          remote_status_path=remote_status_path,
          remote_log_path=remote_log_path,
          remote_output_dir=remote_output_dir,
          expected_job_id=expected_job_id,
          expected_family=expected_family,
          status="running",
          stage="executing_remote_command",
          message="Remote status marker was recovered from runtime.log.",
        )
        return {
          "ok": True,
          "status": "running",
          "stage": "executing_remote_command",
          "return_code": "",
          "remote_pid": "",
          "remote_tmux_session": "",
          "message": f"Remote status marker unavailable; continuing from runtime log: {status_read_error}",
          "remote_log_path": remote_log_path,
          "remote_output_dir": remote_output_dir,
          "remote_dataset_id": "",
          "remote_dataset_name": "",
          "remote_dataset_workspace": "",
          "remote_command": "",
          "train_status": "",
          "render_status": "",
          "metrics_status": "",
          "postprocess_status": "",
          "result_json_exists": False,
          "result_path": "",
          "partial_success": False,
          "failure_stage": "",
          "updated_at": time.time(),
          "log_text": log_delta["text"],
          "log_cursor": log_delta["cursor"],
          "log_available": log_delta["available"],
          "download": {},
          "status_fallback": "runtime_log",
        }
      job_dir = str(PurePosixPath(remote_status_path).parent)
      if _remote_path_exists(sftp, job_dir):
        _recover_missing_status_marker(
          sftp,
          remote_status_path=remote_status_path,
          remote_log_path=remote_log_path,
          remote_output_dir=remote_output_dir,
          expected_job_id=expected_job_id,
          expected_family=expected_family,
          status="running",
          stage="executing_remote_command",
          message="Remote status marker was recovered from existing job directory.",
        )
        return {
          "ok": True,
          "status": "running",
          "stage": "executing_remote_command",
          "return_code": "",
          "remote_pid": "",
          "remote_tmux_session": "",
          "message": f"Remote status marker unavailable; continuing from job directory: {status_read_error}",
          "remote_log_path": remote_log_path,
          "remote_output_dir": remote_output_dir,
          "remote_dataset_id": "",
          "remote_dataset_name": "",
          "remote_dataset_workspace": "",
          "remote_command": "",
          "train_status": "",
          "render_status": "",
          "metrics_status": "",
          "postprocess_status": "",
          "result_json_exists": False,
          "result_path": "",
          "partial_success": False,
          "failure_stage": "",
          "updated_at": time.time(),
          "log_text": "",
          "log_cursor": log_delta["cursor"],
          "log_available": False,
          "download": {},
          "status_fallback": "job_dir",
        }
      raise status_read_error

    status = str(status_payload.get("status") or "running")
    stage = str(status_payload.get("stage") or "remote_detached_running")
    return_code = status_payload.get("return_code")
    download_info: Dict[str, Any] = {}
    if download_output and _downloadable_remote_status(status):
      marker_job_id = str(status_payload.get("job_id", "")).strip()
      marker_family = str(status_payload.get("family", "")).strip()
      if expected_job_id and marker_job_id != str(expected_job_id).strip():
        raise RemoteExecutionError(
          "Remote result marker belongs to a different job.",
          code_hint="WGSC-JOB-RESULTS-MARKER-MISMATCH",
          stage="result_marker",
          details={"expected_job_id": expected_job_id, "actual_job_id": marker_job_id},
        )
      if expected_family and marker_family != str(expected_family).strip():
        raise RemoteExecutionError(
          "Remote result marker belongs to a different algorithm family.",
          code_hint="WGSC-JOB-RESULTS-MARKER-MISMATCH",
          stage="result_marker",
          details={"expected_family": expected_family, "actual_family": marker_family},
        )
      download_info = _download_directory(
        sftp,
        remote_output_dir,
        Path(local_output_dir).expanduser().resolve(),
        progress_callback=download_progress_callback,
      )

    return {
      "ok": True,
      "status": status,
      "stage": stage,
      "return_code": return_code,
      "remote_pid": str(status_payload.get("pid", "")),
      "remote_tmux_session": str(status_payload.get("remote_tmux_session", "")),
      "message": str(status_payload.get("message", "")),
      "remote_log_path": str(status_payload.get("remote_log_path") or remote_log_path),
      "remote_output_dir": str(status_payload.get("remote_output_dir") or remote_output_dir),
      "remote_dataset_id": str(status_payload.get("remote_dataset_id", "")),
      "remote_dataset_name": str(status_payload.get("remote_dataset_name", "")),
      "remote_dataset_workspace": str(status_payload.get("remote_dataset_workspace", "")),
      "remote_command": str(status_payload.get("remote_command", "")),
      "train_status": str(status_payload.get("train_status", "")),
      "render_status": str(status_payload.get("render_status", "")),
      "metrics_status": str(status_payload.get("metrics_status", "")),
      "postprocess_status": str(status_payload.get("postprocess_status", "")),
      "result_json_exists": bool(status_payload.get("result_json_exists")),
      "result_path": str(status_payload.get("result_path", "")),
      "partial_success": bool(status_payload.get("partial_success")),
      "failure_stage": str(status_payload.get("failure_stage", "")),
      "updated_at": status_payload.get("updated_at", ""),
      "log_text": log_delta["text"],
      "log_cursor": log_delta["cursor"],
      "log_available": log_delta["available"],
      "download": download_info,
    }
  finally:
    _close_ssh_resources(client, sftp)


def _build_remote_cancel_script(remote_job_paths: Dict[str, str], resolved_pid: str = "", tmux_session: str = "") -> str:
  return (
    f"pid={_quote(resolved_pid)}; "
    f"tmux_session={_quote(tmux_session)}; "
    f"if [ -n \"$tmux_session\" ] && command -v tmux >/dev/null 2>&1; then "
    "tmux kill-session -t \"$tmux_session\" >/dev/null 2>&1 || true; "
    "fi; "
    f"if [ -z \"$pid\" ] && [ -f {_quote(remote_job_paths['pid_file'])} ]; then pid=$(cat {_quote(remote_job_paths['pid_file'])}); fi; "
    "if [ -n \"$pid\" ]; then "
    "kill -TERM -- -$pid >/dev/null 2>&1 || kill -TERM $pid >/dev/null 2>&1 || true; "
    "sleep 1; "
    "kill -KILL -- -$pid >/dev/null 2>&1 || true; "
    "fi; "
    f"echo 130 > {_quote(remote_job_paths['exit_code_file'])}; "
    f"printf '%s\\n' '[Cancel] Remote tmux job cancellation requested.' >> {_quote(remote_job_paths['runtime_log'])}"
  )


def cancel_remote_detached_job(
  *,
  remote_config: Dict[str, Any],
  remote_job_dir: str,
  remote_pid: str = "",
  remote_tmux_session: str = "",
) -> Dict[str, Any]:
  validated = validate_remote_config(remote_config)
  remote_job_paths = {
    "job_dir": str(remote_job_dir),
    "pid_file": str(PurePosixPath(remote_job_dir) / "pid"),
    "exit_code_file": str(PurePosixPath(remote_job_dir) / "exit_code"),
    "status_file": str(PurePosixPath(remote_job_dir) / "status.json"),
    "runtime_log": str(PurePosixPath(remote_job_dir) / "runtime.log"),
  }
  client = _new_ssh_client(validated)
  sftp = None
  try:
    client.connect(
      hostname=validated["host"],
      port=validated["port"],
      username=validated["username"],
      password=validated["password"],
      timeout=20,
      look_for_keys=False,
      allow_agent=False,
    )
    sftp = client.open_sftp()
    resolved_pid = str(remote_pid or "").strip() or _read_remote_text_file(sftp, remote_job_paths["pid_file"]).strip()
    tmux_session = str(remote_tmux_session or "").strip()
    cancel_script = _build_remote_cancel_script(remote_job_paths, resolved_pid, tmux_session)
    _run_remote_command(client, f"bash -lc {_quote(cancel_script)}", None)
    status_payload = {
      "status": "canceled",
      "stage": "canceled",
      "return_code": 130,
      "pid": resolved_pid,
      "message": "Remote tmux job cancellation requested.",
      "remote_log_path": remote_job_paths["runtime_log"],
      "remote_tmux_session": tmux_session,
      "updated_at": time.time(),
    }
    _write_remote_text(sftp, remote_job_paths["status_file"], json.dumps(status_payload, ensure_ascii=False, indent=2))
    return {"ok": True, "remote_pid": resolved_pid, "remote_tmux_session": tmux_session, "status": "canceled"}
  finally:
    _close_ssh_resources(client, sftp)


def _build_remote_repair_metrics_script(
  *,
  validated: Dict[str, Any],
  job_id: str,
  family: str,
  remote_output_dir: str,
  remote_dataset_workspace: str,
  remote_command: str = "",
) -> str:
  remote_job_paths = _remote_job_paths(remote_output_dir)
  helpers = _remote_post_train_helper_functions()
  eval_block = _remote_post_train_eval_block(True, {"run_render": True, "run_metrics": True})
  activate_cmd = str(validated.get("activate_cmd", "")).strip()
  activate_lines = []
  if activate_cmd:
    activate_lines.append("[ -f ~/.bash_profile ] && source ~/.bash_profile 2>/dev/null || true")
    activate_lines.append("[ -f ~/.bashrc ] && source ~/.bashrc 2>/dev/null || true")
    activate_lines.append(
      '{ if command -v conda >/dev/null 2>&1; then '
      'eval "$(conda shell.bash hook)" || true; '
      'elif [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then '
      'source "$HOME/miniconda3/etc/profile.d/conda.sh" || true; '
      'elif [ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]; then '
      'source "$HOME/anaconda3/etc/profile.d/conda.sh" || true; '
      'elif [ -f "/opt/conda/etc/profile.d/conda.sh" ]; then '
      'source "/opt/conda/etc/profile.d/conda.sh" || true; '
      'fi; }'
    )
    activate_lines.append(activate_cmd)
  activate_body = "\n".join(activate_lines) if activate_lines else ":"
  return f"""#!/usr/bin/env bash
set +e

PYTHON_BIN={_quote(validated["python"])}
REMOTE_REPO_DIR={_quote(validated["repo_path"])}
JOB_ID={_quote(job_id)}
FAMILY={_quote(family)}
JOB_DIR={_quote(remote_job_paths["job_dir"])}
STATUS_FILE={_quote(remote_job_paths["status_file"])}
EXIT_CODE_FILE={_quote(remote_job_paths["exit_code_file"])}
LOG_FILE={_quote(remote_job_paths["runtime_log"])}
PID_FILE={_quote(remote_job_paths["pid_file"])}
OUTPUT_DIR={_quote(remote_output_dir)}
REMOTE_DATASET_ID=""
REMOTE_DATASET_NAME=""
REMOTE_DATASET_WORKSPACE={_quote(remote_dataset_workspace)}
REMOTE_COMMAND_TEXT={_quote(remote_command)}
TRAIN_STATUS="success"
RENDER_STATUS="pending"
METRICS_STATUS="pending"
POSTPROCESS_STATUS="skipped"
RESULT_JSON_EXISTS="false"
RESULT_JSON_PATH=""
PARTIAL_SUCCESS="true"
FAILURE_STAGE="post_train_render_or_metrics"

mkdir -p "$JOB_DIR" "$OUTPUT_DIR"
echo "$$" > "$PID_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1

write_status() {{
  local status="$1"
  local stage="$2"
  local return_code="${{3:-}}"
  local message="${{4:-}}"
  WGSC_STATUS="$status" \\
  WGSC_STAGE="$stage" \\
  WGSC_RETURN_CODE="$return_code" \\
  WGSC_MESSAGE="$message" \\
  WGSC_JOB_ID="$JOB_ID" \\
  WGSC_FAMILY="$FAMILY" \\
  WGSC_PID="$$" \\
  WGSC_LOG_PATH="$LOG_FILE" \\
  WGSC_OUTPUT_DIR="$OUTPUT_DIR" \\
  WGSC_DATASET_ID="$REMOTE_DATASET_ID" \\
  WGSC_DATASET_NAME="$REMOTE_DATASET_NAME" \\
  WGSC_DATASET_WORKSPACE="$REMOTE_DATASET_WORKSPACE" \\
  WGSC_REMOTE_COMMAND="$REMOTE_COMMAND_TEXT" \\
  WGSC_TRAIN_STATUS="$TRAIN_STATUS" \\
  WGSC_RENDER_STATUS="$RENDER_STATUS" \\
  WGSC_METRICS_STATUS="$METRICS_STATUS" \\
  WGSC_POSTPROCESS_STATUS="$POSTPROCESS_STATUS" \\
  WGSC_RESULT_JSON_EXISTS="$RESULT_JSON_EXISTS" \\
  WGSC_RESULT_JSON_PATH="$RESULT_JSON_PATH" \\
  WGSC_PARTIAL_SUCCESS="$PARTIAL_SUCCESS" \\
  WGSC_FAILURE_STAGE="$FAILURE_STAGE" \\
  "$PYTHON_BIN" - "$STATUS_FILE" <<'PY'
import json
import os
import sys
import time

status_path = sys.argv[1]
payload = {{
  "job_id": os.environ.get("WGSC_JOB_ID", ""),
  "family": os.environ.get("WGSC_FAMILY", ""),
  "status": os.environ.get("WGSC_STATUS", ""),
  "stage": os.environ.get("WGSC_STAGE", ""),
  "pid": os.environ.get("WGSC_PID", ""),
  "message": os.environ.get("WGSC_MESSAGE", ""),
  "remote_log_path": os.environ.get("WGSC_LOG_PATH", ""),
  "remote_output_dir": os.environ.get("WGSC_OUTPUT_DIR", ""),
  "remote_dataset_id": os.environ.get("WGSC_DATASET_ID", ""),
  "remote_dataset_name": os.environ.get("WGSC_DATASET_NAME", ""),
  "remote_dataset_workspace": os.environ.get("WGSC_DATASET_WORKSPACE", ""),
  "remote_command": os.environ.get("WGSC_REMOTE_COMMAND", ""),
  "train_status": os.environ.get("WGSC_TRAIN_STATUS", ""),
  "render_status": os.environ.get("WGSC_RENDER_STATUS", ""),
  "metrics_status": os.environ.get("WGSC_METRICS_STATUS", ""),
  "postprocess_status": os.environ.get("WGSC_POSTPROCESS_STATUS", ""),
  "result_json_exists": os.environ.get("WGSC_RESULT_JSON_EXISTS", "").lower() == "true",
  "result_path": os.environ.get("WGSC_RESULT_JSON_PATH", ""),
  "partial_success": os.environ.get("WGSC_PARTIAL_SUCCESS", "").lower() == "true",
  "failure_stage": os.environ.get("WGSC_FAILURE_STAGE", ""),
  "updated_at": time.time(),
}}
return_code = os.environ.get("WGSC_RETURN_CODE", "")
if return_code not in ("", None):
  try:
    payload["return_code"] = int(return_code)
  except Exception:
    payload["return_code"] = return_code
os.makedirs(os.path.dirname(status_path), exist_ok=True)
tmp_path = f"{{status_path}}.tmp.{{os.getpid()}}"
with open(tmp_path, "w", encoding="utf-8") as handle:
  json.dump(payload, handle, ensure_ascii=False, indent=2)
os.replace(tmp_path, status_path)
PY
}}

finish_with() {{
  local status="$1"
  local stage="$2"
  local return_code="$3"
  local message="${{4:-}}"
  echo "$return_code" > "$EXIT_CODE_FILE"
  write_status "$status" "$stage" "$return_code" "$message"
  exit "$return_code"
}}

{helpers}

wgsc_activate_env() {{
{activate_body}
}}

echo "[Repair] Starting render/metrics repair at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
write_status "running" "repair_metrics" "" "Repairing render/metrics for partial-success job."
{eval_block}
"""


def repair_remote_job_metrics(
  *,
  remote_config: Dict[str, Any],
  job_id: str,
  family: str,
  remote_output_dir: str,
  remote_dataset_workspace: str,
  remote_command: str = "",
  log_callback: Callable[[str, str], None] | None = None,
) -> Dict[str, Any]:
  validated = validate_remote_config(remote_config)
  if not str(remote_output_dir or "").strip():
    raise RemoteExecutionError("remote_output_dir is required to repair metrics.", code_hint="WGSC-REPAIR-REMOTE-OUTPUT", stage="repair")
  if not str(remote_dataset_workspace or "").strip():
    raise RemoteExecutionError("remote_dataset_workspace is required to repair metrics.", code_hint="WGSC-REPAIR-DATASET", stage="repair")

  remote_job_paths = _remote_job_paths(remote_output_dir)
  repair_script_path = str(PurePosixPath(remote_job_paths["job_dir"]) / "repair_metrics.sh")
  script = _build_remote_repair_metrics_script(
    validated=validated,
    job_id=job_id,
    family=family,
    remote_output_dir=remote_output_dir,
    remote_dataset_workspace=remote_dataset_workspace,
    remote_command=remote_command,
  )

  client = _new_ssh_client(validated)
  sftp = None
  try:
    client.connect(
      hostname=validated["host"],
      port=validated["port"],
      username=validated["username"],
      password=validated["password"],
      timeout=20,
      look_for_keys=False,
      allow_agent=False,
    )
    sftp = client.open_sftp()
    _write_remote_text(sftp, repair_script_path, script)
    rc = _run_remote_command(client, f"bash -lc {_quote(f'bash {repair_script_path}')}", log_callback)
    status_payload = _read_remote_json_file_strict(sftp, remote_job_paths["status_file"])
    return {
      "ok": rc == 0 and str(status_payload.get("status", "")) == "completed",
      "return_code": rc,
      "status": str(status_payload.get("status", "")),
      "stage": str(status_payload.get("stage", "")),
      "remote_log_path": str(status_payload.get("remote_log_path", remote_job_paths["runtime_log"])),
      "remote_output_dir": str(status_payload.get("remote_output_dir", remote_output_dir)),
      "remote_dataset_workspace": str(status_payload.get("remote_dataset_workspace", remote_dataset_workspace)),
      "train_status": str(status_payload.get("train_status", "")),
      "render_status": str(status_payload.get("render_status", "")),
      "metrics_status": str(status_payload.get("metrics_status", "")),
      "postprocess_status": str(status_payload.get("postprocess_status", "")),
      "result_json_exists": bool(status_payload.get("result_json_exists")),
      "result_path": str(status_payload.get("result_path", "")),
      "partial_success": bool(status_payload.get("partial_success")),
      "failure_stage": str(status_payload.get("failure_stage", "")),
      "updated_at": status_payload.get("updated_at", ""),
    }
  finally:
    _close_ssh_resources(client, sftp)
