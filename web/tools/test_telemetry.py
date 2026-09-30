"""Tests for the deployment telemetry store (WebGS plan P1)."""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from web.server.telemetry import (  # noqa: E402
  TelemetryStore,
  classify_user_agent,
  sanitize_client_event,
)


def test_record_and_summary_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = TelemetryStore(directory=Path(tmp), enabled=True)
        store.record("page_view", page="console", client={"browser": "chrome", "os": "macos", "device": "desktop"})
        store.record("http_request", m="GET", p="asset:web_export", s=200, ms=12, b=4096)
        store.record("http_request", m="GET", p="/api/telemetry/summary", s=401, ms=3, b=64)
        store.record("export_web", ok=True, rows=500, chunks=9, seconds=1.25)
        store.record("export_web", ok=False, error="ValueError: not a PLY", seconds=0.1)

        summary = store.summary()
        assert summary["enabled"] is True
        assert summary["events_total"] == 5
        assert summary["events_by_type"]["page_view"] == 1
        assert summary["events_by_type"]["http_request"] == 2
        assert summary["clients"]["browser"] == {"chrome": 1}
        assert summary["clients"]["device"] == {"desktop": 1}
        assert summary["http"]["status"]["2xx"] == 1
        assert summary["http"]["status"]["4xx"] == 1
        assert summary["http"]["bytes_served"] == 4096 + 64
        assert summary["http"]["recent_failures"][0]["status"] == 401
        assert summary["exports"]["total"] == 2
        assert summary["exports"]["ok"] == 1
        assert "ValueError" in "".join(summary["exports"]["failures_by_error"])


def test_serving_load_aggregation_and_device_classes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = TelemetryStore(directory=Path(tmp), enabled=True)
        for ttfr in (500, 700, 900):
            store.record("serving_load", policy="joint", ttfr_ms=ttfr, bytes=1_000_000,
                         p95_frame_ms=20, chunks_done=9, chunk_failures=0)
        store.record("serving_load", policy="naive", ttfr_ms=1500, failed=True, fail_stage="manifest 404")

        summary = store.summary()
        serving = summary["serving"]
        assert serving["loads"] == 4
        assert serving["ttfr_ms"]["p50"] == 900
        assert serving["ttfr_ms"]["min"] == 500
        assert serving["ttfr_ms"]["max"] == 1500
        assert serving["by_policy"] == {"joint": 3, "naive": 1}
        assert serving["failures_by_stage"] == {"manifest 404": 1}

        # device classes must be coarse names only, derived from UA strings
        assert classify_user_agent(
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1"
        ) == {"browser": "safari", "os": "ios", "device": "mobile"}
        assert classify_user_agent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
        ) == {"browser": "chrome", "os": "windows", "device": "desktop"}
        assert classify_user_agent(
            "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/126.0 Mobile Safari/537.36"
        ) == {"browser": "chrome", "os": "android", "device": "mobile"}
        assert classify_user_agent("")["device"] == "desktop"


def test_client_event_sanitization_bounds_fields() -> None:
    good = sanitize_client_event({
        "type": "serving_load",
        "policy": "joint",
        "ttfr_ms": 596,
        "bytes": 123456,
        "failed": False,
        "fail_stage": "x" * 500,           # over-length string gets truncated
        "evil_field": "drop me",           # unknown field must be dropped
        "rows": "999",                     # numeric strings are accepted
        "fps": 58.7,
    })
    assert good is not None
    event_type, fields = good
    assert event_type == "serving_load"
    assert fields["ttfr_ms"] == 596
    assert fields["rows"] == 999
    assert len(fields["fail_stage"]) == 40
    assert "evil_field" not in fields

    assert sanitize_client_event({"type": "unknown_type"}) is None
    assert sanitize_client_event({"type": "serving_load", "ttfr_ms": "not a number"}) is not None
    assert "ttfr_ms" not in sanitize_client_event({"type": "serving_load", "ttfr_ms": "abc"})[1]
    assert sanitize_client_event("not a dict") is None
    # out-of-range numbers get clamped, not rejected
    assert sanitize_client_event({"type": "serving_load", "ttfr_ms": 10**12})[1]["ttfr_ms"] == 24 * 3600 * 1000


def test_retention_prunes_old_event_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        old = root / "events-20200101.jsonl"
        old.write_text(json.dumps({"t": "2020-01-01T00:00:00Z", "type": "page_view"}) + "\n")
        fresh_name = time.strftime("events-%Y%m%d.jsonl", time.gmtime())
        fresh = root / fresh_name
        fresh.write_text(json.dumps({"t": "now", "type": "page_view"}) + "\n")

        store = TelemetryStore(directory=root, retention_days=30, enabled=True)
        assert not old.exists(), "event files older than the retention window must be pruned"
        assert fresh.exists()
        assert store.summary()["events_total"] == 1


def test_disabled_store_writes_nothing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = TelemetryStore(directory=Path(tmp), enabled=False)
        store.record("page_view", page="console")
        assert store.summary()["events_total"] == 0
        assert list(Path(tmp).glob("events-*.jsonl")) == []


def test_telemetry_dir_is_not_static_served() -> None:
    # The static file server must keep refusing to hand out telemetry files.
    from web.server.api_server import ApiHandler

    assert ApiHandler._static_path_denied(["generated", "telemetry", "events-20260101.jsonl"])
    assert ApiHandler._static_path_denied(["server", "telemetry.py"])
    assert not ApiHandler._static_path_denied(["generated", "web_exports", "scene-abc", "manifest.json"])


def main() -> None:
    test_record_and_summary_roundtrip()
    test_serving_load_aggregation_and_device_classes()
    test_client_event_sanitization_bounds_fields()
    test_retention_prunes_old_event_files()
    test_disabled_store_writes_nothing()
    test_telemetry_dir_is_not_static_served()
    print("telemetry tests passed")


if __name__ == "__main__":
    main()
