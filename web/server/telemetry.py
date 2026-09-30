"""Operational telemetry for a deployed Web_Scan server (WebGS plan, P1).

The WWW Industry Track plan asks for deployment evidence: session counts,
scene loads, device/browser distribution, bytes served, response latency,
uptime and failure rates. This module records anonymous operational events
to daily JSONL files under web/generated/telemetry/ and aggregates them
into a summary suitable for the paper's deployment section.

Privacy: no IP addresses, no raw User-Agent strings, no scene file names
and no user content are stored — only coarse device classes (browser
family, OS family, device class) and numeric counters.

Reliability: telemetry must never break a request. Every write is wrapped
in try/except and failures are logged at debug level.

Disable with env WEBSGS_TELEMETRY=0 or web/config/security.json
{"telemetry_enabled": false} (checked once at first use; restart to apply).
Events older than WEBSGS_TELEMETRY_RETENTION_DAYS (default 30) are pruned
when the store is created and on every summary() call.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from web.server.web_security import WEB_DIR, load_security_config

LOGGER = logging.getLogger("webscan.telemetry")

TELEMETRY_DIR = WEB_DIR / "generated" / "telemetry"
DEFAULT_RETENTION_DAYS = 30
MAX_FIELD_STRING = 200
MAX_EVENT_JSON_BYTES = 4096
EVENT_FILE_RE = re.compile(r"^events-(\d{8})\.jsonl$")

# Client-reported event types accepted by /api/telemetry/event, with the
# fields each may carry and hard bounds. Everything else is dropped.
CLIENT_EVENT_FIELDS = {
  "serving_load": {
    "client_id": (str, 64),
    "policy": (str, 16),
    "ttfr_ms": (int, 0, 24 * 3600 * 1000),
    "load_ms": (int, 0, 24 * 3600 * 1000),
    "bytes": (int, 0, 1 << 40),
    "budget_bytes": (int, 0, 1 << 40),
    "rows": (int, 0, 1 << 32),
    "scene_rows": (int, 0, 1 << 32),
    "chunks_done": (int, 0, 1 << 20),
    "chunks_total": (int, 0, 1 << 20),
    "chunk_failures": (int, 0, 1 << 20),
    "p95_frame_ms": (int, 0, 3600 * 1000),
    "fps": (float, 0.0, 10000.0),
    "failed": (bool,),
    "fail_stage": (str, 40),
  },
}


def telemetry_enabled() -> bool:
  if str(os.environ.get("WEBSGS_TELEMETRY", "")).strip().lower() in ("0", "false", "off", "no"):
    return False
  return bool(load_security_config().get("telemetry_enabled", True))


def classify_user_agent(user_agent: str) -> dict:
  """Coarse client class from a User-Agent header. Never stores the raw UA."""
  ua = str(user_agent or "")
  if "Firefox" in ua and "Seamonkey" not in ua:
    browser = "firefox"
  elif "Edg/" in ua or "Edge" in ua:
    browser = "edge"
  elif "Chrome" in ua or "Chromium" in ua:
    browser = "chrome"
  elif "Safari" in ua:
    browser = "safari"
  else:
    browser = "other"
  if "Android" in ua:
    os_name = "android"
  elif "iPhone" in ua or "iPod" in ua:
    os_name = "ios"
  elif "iPad" in ua:
    os_name = "ipados"
  elif "Mac OS X" in ua or "Macintosh" in ua:
    os_name = "macos"
  elif "Windows" in ua:
    os_name = "windows"
  elif "Linux" in ua:
    os_name = "linux"
  else:
    os_name = "other"
  if "iPad" in ua or ("Android" in ua and "Mobile" not in ua):
    device = "tablet"
  elif "Mobile" in ua or "iPhone" in ua or "iPod" in ua:
    device = "mobile"
  else:
    device = "desktop"
  return {"browser": browser, "os": os_name, "device": device}


def _percentile(sorted_values: list, fraction: float) -> float | None:
  if not sorted_values:
    return None
  index = min(len(sorted_values) - 1, max(0, round(fraction * (len(sorted_values) - 1))))
  return sorted_values[index]


def _stats(values: list) -> dict:
  ordered = sorted(values)
  return {
    "count": len(ordered),
    "min": ordered[0] if ordered else None,
    "p50": _percentile(ordered, 0.50),
    "p95": _percentile(ordered, 0.95),
    "max": ordered[-1] if ordered else None,
  }


class TelemetryStore:
  def __init__(self, directory: Path | None = None, retention_days: int = DEFAULT_RETENTION_DAYS,
               enabled: bool | None = None, now=time.time):
    self.directory = Path(directory) if directory else TELEMETRY_DIR
    self.retention_days = max(1, int(retention_days))
    self.enabled = telemetry_enabled() if enabled is None else bool(enabled)
    self._lock = threading.Lock()
    self._now = now
    self.started_monotonic = time.monotonic()
    try:
      self.directory.mkdir(parents=True, exist_ok=True)
      self._prune()
    except OSError as exc:
      LOGGER.debug("telemetry init failed: %s", exc)

  # -- writing ---------------------------------------------------------------

  def record(self, event_type: str, **fields) -> None:
    if not self.enabled or not event_type:
      return
    event = {"t": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "type": str(event_type)[:40]}
    for key, value in fields.items():
      if value is None:
        continue
      if isinstance(value, bool):
        event[key] = value
      elif isinstance(value, int):
        event[key] = value
      elif isinstance(value, float):
        event[key] = round(value, 3)
      elif isinstance(value, dict):
        event[key] = {str(k)[:40]: str(v)[:40] for k, v in value.items() if v is not None}
      else:
        event[key] = str(value)[:MAX_FIELD_STRING]
    try:
      line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
      if len(line.encode("utf-8")) > MAX_EVENT_JSON_BYTES:
        return
      stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
      path = self.directory / f"events-{stamp}.jsonl"
      with self._lock:
        with open(path, "a", encoding="utf-8") as handle:
          handle.write(line + "\n")
    except Exception as exc:  # telemetry must never break a request
      LOGGER.debug("telemetry write failed: %s", exc)

  def _prune(self) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
    for path in self.directory.glob("events-*.jsonl"):
      match = EVENT_FILE_RE.match(path.name)
      if not match:
        continue
      try:
        file_day = datetime.strptime(match.group(1), "%Y%m%d").replace(tzinfo=timezone.utc)
      except ValueError:
        continue
      if file_day < cutoff:
        try:
          path.unlink()
        except OSError:
          pass

  # -- reading ---------------------------------------------------------------

  def _load_events(self) -> list:
    events = []
    try:
      paths = sorted(self.directory.glob("events-*.jsonl"))
    except OSError:
      return events
    for path in paths:
      try:
        with open(path, "r", encoding="utf-8") as handle:
          for line in handle:
            line = line.strip()
            if not line:
              continue
            try:
              event = json.loads(line)
            except json.JSONDecodeError:
              continue
            if isinstance(event, dict):
              events.append(event)
      except OSError:
        continue
    return events

  def summary(self) -> dict:
    self._prune()
    events = self._load_events()
    by_type: dict = {}
    by_day: dict = {}
    devices: dict = {"browser": {}, "os": {}, "device": {}}
    http_durations: list = []
    http_bytes = 0
    status_counts = {"2xx": 0, "4xx": 0, "5xx": 0, "other": 0}
    recent_failures: list = []
    serving_events = [e for e in events if e.get("type") == "serving_load"]
    export_events = [e for e in events if e.get("type") == "export_web"]

    for event in events:
      event_type = str(event.get("type", "unknown"))
      by_type[event_type] = by_type.get(event_type, 0) + 1
      day = str(event.get("t", ""))[:10]
      if day:
        by_day.setdefault(day, {})
        by_day[day][event_type] = by_day[day].get(event_type, 0) + 1
      client = event.get("client")
      if isinstance(client, dict):
        for dimension in devices:
          value = client.get(dimension)
          if value:
            devices[dimension][value] = devices[dimension].get(value, 0) + 1
      if event_type == "http_request":
        status = int(event.get("s", 0) or 0)
        bucket = f"{status // 100}xx" if status // 100 in (2, 4, 5) else "other"
        status_counts[bucket] = status_counts.get(bucket, 0) + 1
        http_bytes += int(event.get("b", 0) or 0)
        if isinstance(event.get("ms"), (int, float)):
          http_durations.append(float(event["ms"]))
        if status >= 400 and len(recent_failures) < 20:
          recent_failures.append({
            "t": event.get("t"), "path": event.get("p"), "status": status,
          })

    ttfr_values = [int(e["ttfr_ms"]) for e in serving_events if isinstance(e.get("ttfr_ms"), int)]
    byte_values = [int(e["bytes"]) for e in serving_events if isinstance(e.get("bytes"), int)]
    frame_values = [int(e["p95_frame_ms"]) for e in serving_events if isinstance(e.get("p95_frame_ms"), int)]
    fail_reasons: dict = {}
    for event in serving_events:
      if event.get("failed") and event.get("fail_stage"):
        reason = str(event["fail_stage"])
        fail_reasons[reason] = fail_reasons.get(reason, 0) + 1
    policy_counts: dict = {}
    for event in serving_events:
      policy = str(event.get("policy", "unknown"))
      policy_counts[policy] = policy_counts.get(policy, 0) + 1

    export_ok = sum(1 for e in export_events if e.get("ok"))
    export_errors: dict = {}
    for event in export_events:
      if not event.get("ok") and event.get("error"):
        reason = str(event["error"])
        export_errors[reason] = export_errors.get(reason, 0) + 1

    return {
      "enabled": self.enabled,
      "uptime_seconds": round(time.monotonic() - self.started_monotonic),
      "retention_days": self.retention_days,
      "events_total": len(events),
      "events_by_type": dict(sorted(by_type.items())),
      "events_by_day": dict(sorted(by_day.items())),
      "clients": {key: dict(sorted(value.items())) for key, value in devices.items()},
      "http": {
        "requests": status_counts["2xx"] + status_counts["4xx"] + status_counts["5xx"] + status_counts["other"],
        "status": status_counts,
        "bytes_served": http_bytes,
        "latency_ms": _stats(http_durations),
        "recent_failures": recent_failures[-20:],
      },
      "serving": {
        "loads": len(serving_events),
        "ttfr_ms": _stats(ttfr_values),
        "bytes": _stats(byte_values),
        "p95_frame_ms": _stats(frame_values),
        "by_policy": dict(sorted(policy_counts.items())),
        "failures_by_stage": dict(sorted(fail_reasons.items())),
      },
      "exports": {
        "total": len(export_events),
        "ok": export_ok,
        "failures_by_error": dict(sorted(export_errors.items())),
      },
    }


_STORE: TelemetryStore | None = None
_STORE_LOCK = threading.Lock()


def get_telemetry() -> TelemetryStore:
  """Process-wide store; created on first use so tests can stay isolated."""
  global _STORE
  if _STORE is None:
    with _STORE_LOCK:
      if _STORE is None:
        try:
          retention = int(os.environ.get("WEBSGS_TELEMETRY_RETENTION_DAYS", DEFAULT_RETENTION_DAYS))
        except ValueError:
          retention = DEFAULT_RETENTION_DAYS
        _STORE = TelemetryStore(retention_days=retention)
  return _STORE


def sanitize_client_event(payload: dict) -> tuple[str, dict] | None:
  """Validate a client-reported event; returns (type, fields) or None."""
  if not isinstance(payload, dict):
    return None
  event_type = str(payload.get("type", "")).strip()
  spec = CLIENT_EVENT_FIELDS.get(event_type)
  if spec is None:
    return None
  fields: dict = {}
  for name, rule in spec.items():
    if name not in payload or payload[name] is None:
      continue
    value = payload[name]
    if rule[0] is str:
      text = str(value).strip()
      if text:
        fields[name] = text[: rule[1]]
    elif rule[0] is bool:
      if isinstance(value, bool):
        fields[name] = value
    elif rule[0] is int:
      try:
        fields[name] = max(rule[1], min(rule[2], int(round(float(value)))))
      except (TypeError, ValueError):
        continue
    elif rule[0] is float:
      try:
        fields[name] = max(rule[1], min(rule[2], float(value)))
      except (TypeError, ValueError):
        continue
  return event_type, fields
