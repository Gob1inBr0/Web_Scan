"""HTTP response helpers and security controls for the Web_Scan server.

Extracted from api_server.py (v0.3 split, step 1): access token, same-origin
enforcement, allowed result roots, and the shared JSON/error response builders.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any, Dict
from urllib.parse import parse_qs, urlparse

LOGGER = logging.getLogger("webscan")

ROOT_DIR = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT_DIR / "web"
STREAM_DIR = WEB_DIR / "generated" / "streams"
DATASET_DIR = WEB_DIR / "generated" / "datasets"
WORKSPACE_DIR = WEB_DIR / "generated" / "workspaces"
JOB_LOG_DIR = WEB_DIR / "generated" / "job_logs"

MANUAL_ZH_URL = "/web/WEB_TRAINING_MANUAL_ZH.md"

# ---------------------------------------------------------------------------
# Security (v0.2): API access token, same-origin enforcement, allowed result roots.
# The token is generated on first start, persisted with 0600 permissions, printed
# to the console, and injected into the served index.html so the browser UI can
# attach it to every API request. See README "Access Token" for CLI/curl usage.
# ---------------------------------------------------------------------------
WEB_CONFIG_DIR = WEB_DIR / "config"
SERVER_TOKEN_FILE = WEB_CONFIG_DIR / "server.token"
SECURITY_CONFIG_FILE = WEB_CONFIG_DIR / "security.json"
STREAM_FRAME_MAX_BYTES = 25 * 1024 * 1024
UPLOAD_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}

SECURITY_STATE: Dict[str, Any] = {
  "auth_enabled": True,
  "token": "",
  "allowed_host_values": set(),
}


def load_security_config() -> Dict[str, Any]:
  try:
    data = json.loads(SECURITY_CONFIG_FILE.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}
  except Exception:
    return {}


def security_flag(name: str, default: bool = False) -> bool:
  return bool(load_security_config().get(name, default))


def ensure_server_token(cli_token: str = "") -> str:
  token = str(cli_token or "").strip()
  if token:
    _write_server_token(token)
    SECURITY_STATE["token"] = token
    return token
  if SERVER_TOKEN_FILE.exists():
    try:
      token = SERVER_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
      token = ""
    if token:
      SECURITY_STATE["token"] = token
      return token
  token = secrets.token_urlsafe(32)
  _write_server_token(token)
  SECURITY_STATE["token"] = token
  return token


def _write_server_token(token: str) -> None:
  WEB_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
  SERVER_TOKEN_FILE.write_text(token + "\n", encoding="utf-8")
  try:
    os.chmod(SERVER_TOKEN_FILE, 0o600)
  except OSError:
    pass


def allowed_host_values(host: str, port: int, extra_hosts: list[str]) -> set:
  values = {
    f"{host}:{port}".lower(),
    f"localhost:{port}",
    f"127.0.0.1:{port}",
    f"[::1]:{port}",
    f"::1:{port}",
  }
  for item in extra_hosts:
    text = str(item or "").strip().lower()
    if not text:
      continue
    values.add(text if ":" in text else f"{text}:{port}")
  return values


def request_host_allowed(handler: "ApiHandler") -> bool:
  host_header = str(handler.headers.get("Host", "")).strip().lower()
  if not host_header:
    return False
  return host_header in SECURITY_STATE["allowed_host_values"]


def request_origin_allowed(handler: "ApiHandler") -> bool:
  origin = str(handler.headers.get("Origin", "")).strip()
  if not origin:
    return True
  origin_host = str(urlparse(origin).netloc or "").strip().lower()
  return bool(origin_host) and origin_host in SECURITY_STATE["allowed_host_values"]


def request_token_valid(handler: "ApiHandler") -> bool:
  if not SECURITY_STATE["auth_enabled"]:
    return True
  header_token = str(handler.headers.get("X-Auth-Token", "")).strip()
  if header_token and header_token == SECURITY_STATE["token"]:
    return True
  # Links opened via <a href> (log/csv downloads) cannot send headers; accept a query token.
  query = parse_qs(urlparse(handler.path).query)
  query_token = str(query.get("access_token", [""])[0]).strip()
  return bool(query_token) and query_token == SECURITY_STATE["token"]


def allowed_result_roots() -> list:
  roots = [ROOT_DIR.resolve()]
  for raw in load_security_config().get("extra_allowed_roots", []):
    if not isinstance(raw, str) or not raw.strip():
      continue
    try:
      candidate = Path(raw).expanduser().resolve()
    except OSError:
      continue
    if candidate.is_dir() and candidate not in roots:
      roots.append(candidate)
  return roots


def result_path_is_allowed(resolved: Path) -> bool:
  return any(path_is_inside(resolved, root) for root in allowed_result_roots())


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


def path_is_inside(path: Path, parent: Path) -> bool:
  try:
    path.resolve().relative_to(parent.resolve())
    return True
  except ValueError:
    return False


def json_response(handler: "ApiHandler", payload: Dict[str, Any], status: int = 200) -> None:
  body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
  handler.send_response(status)
  handler.send_header("Content-Type", "application/json; charset=utf-8")
  handler.send_header("Content-Length", str(len(body)))
  handler.end_headers()
  handler.wfile.write(body)


def build_error_payload(
  *,
  code: str,
  step: str,
  message: str,
  reason: str = "",
  details: Dict[str, Any] | None = None,
  manual_anchor: str = "",
  next_action: str = "",
) -> Dict[str, Any]:
  return {
    "ok": False,
    "error": message,
    "code": code,
    "step": step,
    "message": message,
    "reason": reason or message,
    "details": details or {},
    "manual_url": MANUAL_ZH_URL,
    "manual_anchor": manual_anchor,
    "next_action": next_action,
  }


def error_response(
  handler: "ApiHandler",
  *,
  code: str,
  step: str,
  message: str,
  status: int = 400,
  reason: str = "",
  details: Dict[str, Any] | None = None,
  manual_anchor: str = "",
  next_action: str = "",
) -> None:
  json_response(
    handler,
    build_error_payload(
      code=code,
      step=step,
      message=message,
      reason=reason,
      details=details,
      manual_anchor=manual_anchor,
      next_action=next_action,
    ),
    status=status,
  )
