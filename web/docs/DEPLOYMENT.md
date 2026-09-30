# Deployment Guide (WebGS P1)

How to run Web_Scan as a publicly reachable serving demo and collect the
operational telemetry that the WWW Industry Track submission needs as
deployment evidence. Everything below works with zero code changes; the
telemetry pipeline is built in (`web/server/telemetry.py`).

## 1. Run the server for real use

```bash
./start_webserver.sh                       # local machine, port 8080
python3 web/server/api_server.py \
  --host 0.0.0.0 --port 8080 \
  --allowed-host your.domain.example       # accept the public Host header
```

- The API access token is generated once into `web/config/server.token`
  and injected into the served pages automatically. Keep token auth ON for
  anything reachable from the internet (`--no-auth` is for local tests only).
- Put a reverse proxy with HTTPS in front for a public demo, e.g. Caddy:

```
your.domain.example {
    reverse_proxy 127.0.0.1:8080
}
```

- `./stop_webserver.sh` (or the console's shutdown button) stops it.
- Run it under a service manager for multi-week uptime evidence, e.g. a
  launchd plist / systemd unit that runs `start_webserver.sh` and restarts
  on exit. The telemetry summary reports `uptime_seconds` per process.

## 2. What telemetry is collected (and what is not)

Recorded anonymously to daily JSONL files in `web/generated/telemetry/`:

| Event | When | Key fields |
|---|---|---|
| `server_start` | process start | host, port, auth mode |
| `page_view` | console / workbench opened | coarse device class (browser/OS/mobile-desktop) |
| `http_request` | every response | method, path **bucket** (never the raw path), status, latency ms, bytes out |
| `serving_load` | progressive viewer: first render, all chunks settled, or failure | policy, TTFR, bytes, rows, chunk failures, fail stage, device class |
| `export_web` | `POST /api/export-web` | ok/fail, error class, rows, chunks, duration |

Not recorded: IP addresses, raw User-Agent strings, scene/file names,
user content, anything from other machines the server touches.

Disable with `WEBSGS_TELEMETRY=0` or `web/config/security.json`:
`{"telemetry_enabled": false}` (restart to apply). Retention defaults to
30 days (`WEBSGS_TELEMETRY_RETENTION_DAYS` to change). Client beacons need
no token but are whitelist-and-bounds validated and still pass the
same-origin checks; the summary endpoint is token-gated like every `/api`
route, and the raw JSONL files are not served statically.

## 3. Pulling the deployment evidence

```bash
TOKEN=$(cat web/config/server.token)
curl -H "X-Auth-Token: $TOKEN" http://127.0.0.1:8080/api/telemetry/summary | python3 -m json.tool
```

The summary answers the Industry Track questions directly:

- sessions & audience — `events_by_day`, `clients.*` (device/browser/OS mix)
- what was served — `http.requests`, `http.bytes_served`, `events_by_type`
- responsiveness — `http.latency_ms` (p50/p95), `serving.ttfr_ms` (p50/p95),
  `serving.p95_frame_ms`
- reliability — `http.status` (4xx/5xx counts), `serving.failures_by_stage`,
  `exports.failures_by_error`, `http.recent_failures`, `uptime_seconds`

For the paper: snapshot this JSON periodically (cron + append to a file) so
you can plot weeks of operation. Failure-case analysis (P1) uses
`serving.failures_by_stage` + `exports.failures_by_error` + the viewer's
own telemetry export (`window.__webgsTelemetry()` in the browser console)
for per-load traces.

## 4. Smoke checklist for a new deployment

1. Open the console, submit a training/compression job on a connected GPU host.
2. Export a scene (`POST /api/export-web` or the console button) and open the
   progressive viewer link; confirm first render appears before chunks finish.
3. `curl -H "X-Auth-Token: $TOKEN" .../api/telemetry/summary` shows the
   `serving_load` event you just created, with a plausible TTFR.
4. Confirm `https://.../web/generated/telemetry/...` returns 404 (files are
   server-side only).
