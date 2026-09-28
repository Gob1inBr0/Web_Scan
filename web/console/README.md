# WebScan Console

The new frontend for Web_Scan — a rebuilt, focused UI for the remote 3DGS
training workflow. Served by the same server as the legacy UI:

- **Console (this folder, recommended):** `http://127.0.0.1:8080/web/console/`
- **Legacy workbench (kept as fallback):** `http://127.0.0.1:8080/web/`

## What it covers

- **Wizard-driven training** — Data → Algorithm → Submit. The remote
  environment check and command preview run automatically behind a single
  confirmation modal.
- **Connection profiles** — save multiple servers, paste a raw SSH command
  (`ssh -p 10526 root@host`) and the fields fill themselves. Passwords live in
  the browser session only.
- **Runs** — named runs, live log tail, metrics chart, download progress,
  cancel/rename/delete, desktop notification when training finishes.
- **Datasets** — every run grouped by its dataset with a side-by-side compare
  view (render images + metric deltas against the best pick).
- **3D viewer** — built-in demo scenes plus quick load of local PLYs.
- **English / 中文** — toggle in the top bar; the choice is remembered.

## Under the hood

No build step. Vue 3 is vendored at `assets/vue.global.prod.js`; everything
else is plain ES modules:

```
js/app.js          entry: sidebar, topbar, hash router
js/store.js        reactive store: profiles, polling, notifications, locale
js/api.js          typed wrappers around the backend /api endpoints
js/i18n.js         en/zh dictionary (t() reads the store, so switches are live)
js/ssh-parse.js    parses pasted SSH commands
js/components/     UI kit + SVG line chart
js/pages/          dashboard, wizard, runs, datasets, viewer, settings
```

The server injects the API access token into this page automatically; CLI
calls still need the `X-Auth-Token` header (see the main README).

## Scope notes

Legacy-only features still on the roadmap (P2): camera capture with auto
stream upload, bulk job cleanup, cross-run CSV export, detached-run reattach
UI, flow reset / staged-data management, command override editing. The legacy
UI at `/web/` remains the fallback for those until they land here.
