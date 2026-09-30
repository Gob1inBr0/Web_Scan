# Web_Scan — A Web Workbench for 3D Gaussian Splatting

![Python](https://img.shields.io/badge/Python-3.10-blue.svg) ![PyTorch](https://img.shields.io/badge/PyTorch-2.2%2B-red.svg) ![License](https://img.shields.io/badge/License-MIT-green.svg)

English | [简体中文](README_zh-CN.md)

## Project Overview

**Web_Scan** is a web workbench for 3D Gaussian Splatting (3DGS) algorithms. It provides a unified workflow covering data upload, remote training, log monitoring, result download, fast PLY loading, and interactive result inspection.

The platform uses a lightweight front/back-end architecture: the browser hosts the operation UI and result display, while a Python API service handles data materialization, job orchestration, SSH/SFTP remote execution, and result retrieval. Algorithm environments are not configured manually — they are set up uniformly with the `setup_env.sh` script inside each algorithm directory.

## Key Features

- **Multi-algorithm support**: training, rendering, compression, and evaluation tasks are managed uniformly through adapter configurations.
- **Web data flow**: image upload, camera capture, remote data reuse, and COLMAP preprocessing.
- **Remote training**: jobs are dispatched over SSH/SFTP, run in the background with `tmux`, and results are downloaded automatically.
- **Live monitoring**: job status, training logs, loss, PSNR, iteration count, FPS, and other metrics.
- **Result inspection**: fast PLY loading, interactive 3D browsing, result bundle export, and metric analysis.

## Web UI Screenshots

Screenshots are located in [`web照片/`](web照片/).

| Workbench overview | Data source configuration |
|---|---|
| ![Web-GSC workbench overview](<web照片/截屏2026-05-19 13.38.15.png>) | ![Remote data source and connection configuration](<web照片/截屏2026-05-19 13.38.29.png>) |

| New image upload | Remote training command preview |
|---|---|
| ![New image upload and camera capture](<web照片/截屏2026-05-19 13.38.44.png>) | ![Algorithm training configuration and remote precheck](<web照片/截屏2026-05-19 13.38.59.png>) |

| Logs and metrics | Interactive result browsing |
|---|---|
| ![Training logs and live metrics](<web照片/截屏2026-05-19 13.39.06.png>) | ![3D result browser](<web照片/截屏2026-05-19 13.39.29.png>) |

| Training metric analysis |
|---|
| ![PSNR, SSIM, LPIPS, and model size analysis](<web照片/截屏2026-05-19 13.39.37.png>) |

## Supported Algorithms

| Algorithm | Main Use | Setup Script |
|---|---|---|
| **HAC++** | High-compression Gaussian representation and compression evaluation | `HAC-plus-main/setup_env.sh` |
| **ContextGS** | Context modeling and compression | `ContextGS-main/setup_env.sh` |
| **CompGS** | Rate–distortion optimized compression | `CompGS-main/setup_env.sh` |
| **GaussianPro** | Progressive propagation training, suited to texture-poor scenes | `GaussianPro-version1.0/setup_env.sh` |
| **GSLightning** | PyTorch Lightning training framework | `gaussian-splatting-lightning-main/setup_env.sh` |
| **MEGS-2** | Memory-efficient Gaussian representation | `MEGS-2-main/setup_env.sh` |
| **reduced-3dgs** | Reduced VRAM and storage footprint | `reduced-3dgs-main/setup_env.sh` |
| **AtomGS** | Atomized Gaussian representation | `AtomGS-main/setup_env.sh` |
| **FCGS** | Feature-compressed Gaussian bitstream pipeline (train via CompGS wrapper; encode/decode) | `FCGS-main/` (see `web/project_md/fcgs/`) |

## Project Structure

```text
Web_Scan/
├── web/                                  # Web application core
│   ├── index.html                       # Main page entry
│   ├── styles.css                       # Page styles
│   ├── src/                             # Front-end modules
│   ├── server/                          # Python API service
│   ├── tools/                           # Data processing, export, and test tools (run_tests.py runs all tests)
│   ├── config/                          # Algorithm adapter configurations
│   ├── docs/                            # Feature guides (e.g. PLY quick load)
│   ├── scenes/                          # Example scenes
│   └── viewers/                         # Result viewers
├── web照片/                              # UI screenshots used by the README
├── HAC-plus-main/                       # HAC++ algorithm directory
├── ContextGS-main/                      # ContextGS algorithm directory
├── CompGS-main/                         # CompGS algorithm directory
├── GaussianPro-version1.0/              # GaussianPro algorithm directory
├── gaussian-splatting-lightning-main/   # GSLightning algorithm directory
├── MEGS-2-main/                         # MEGS-2 algorithm directory
├── reduced-3dgs-main/                   # reduced-3dgs algorithm directory
└── AtomGS-main/                         # AtomGS algorithm directory
```

## Requirements

### Local Web Service

| Item | Requirement |
|---|---|
| OS | macOS / Linux / Windows + WSL |
| Python | Python 3.10 |
| Browser | Current versions of Chrome / Edge / Safari / Firefox |
| Python packages | `flask`, `paramiko`, `numpy` |
| Optional tools | `COLMAP`, `ImageMagick`/`magick`, `nvidia-smi` |
| Default address | Console (recommended): `http://127.0.0.1:8080/web/console/` · Legacy workbench: `http://127.0.0.1:8080/web/` |

The local machine only runs the web UI, uploads, job orchestration, and result viewing — no NVIDIA GPU is required. If you want to train locally, install the CUDA toolkit, PyTorch, and extension dependencies of the target algorithm.

### Remote Training Server

| Item | Requirement |
|---|---|
| OS | Linux |
| Python | Python 3.10 |
| GPU | NVIDIA GPU, 16GB+ VRAM recommended; 24GB+ for large scenes |
| CUDA / PyTorch | Installed or verified by the algorithm's `setup_env.sh` |
| Execution tools | SSH/SFTP, `tmux`, `xvfb-run` |
| Storage | At least 50GB free; SSD 500GB+ recommended for multi-task or large scenes |

### Extra Dependency for HAC++: tmc3

The GPCC compression path of HAC++ calls `tmc3`. Before running HAC++, install MPEG PCC TMC13 on the training server and make sure `tmc3` is directly executable:

```bash
which tmc3
tmc3 --help
```

If `which tmc3` prints nothing, add the directory containing the `tmc3` executable to `PATH`, or set the GPCC encoder path in the runtime environment.

## Environment Setup Scripts

Every algorithm directory ships with a `setup_env.sh`. Enter the target algorithm directory and run the script to provision the corresponding Python 3.10 Conda environment and CUDA extensions:

```bash
cd HAC-plus-main
bash setup_env.sh
```

You can also run them from the repository root:

```bash
bash HAC-plus-main/setup_env.sh
bash ContextGS-main/setup_env.sh
bash CompGS-main/setup_env.sh
bash GaussianPro-version1.0/setup_env.sh
bash gaussian-splatting-lightning-main/setup_env.sh
bash MEGS-2-main/setup_env.sh
bash reduced-3dgs-main/setup_env.sh
bash AtomGS-main/setup_env.sh
```

The scripts expect a working Conda or Mamba installation. If Conda is not loaded automatically on the server, run a command such as:

```bash
source /path/to/miniconda3/etc/profile.d/conda.sh
```

## Quick Start

### 1. Install the Web Service Dependencies

```bash
python3.10 -m pip install paramiko numpy
```

### 2. Start the API and Static Services

```bash
python3.10 web/server/api_server.py --host 127.0.0.1 --port 8080
```

### 3. Open the Web Page

```text
http://127.0.0.1:8080/web/console/          # new console (recommended)
http://127.0.0.1:8080/web/                  # legacy workbench
```

### 4. Access Token (enabled by default since v0.2)

The server generates an access token on first start, stores it in `web/config/server.token` (0600 permissions), and prints it to the console. Opening the page above picks up the token automatically; CLI calls must send it as a header:

```bash
curl -H "X-Auth-Token: <token>" http://127.0.0.1:8080/api/health
```

Related options and configuration:

- `--token <value>`: set an explicit token; `--no-auth`: disable token checks (not recommended).
- `--allowed-host 192.168.1.10,myhost.local`: allow access via other hostnames (e.g. from the LAN). By default only 127.0.0.1, localhost, and the bound address are accepted.
- Cross-origin requests are rejected (no more `Access-Control-Allow-Origin: *`), and static files are served from the `web/` subdirectory only.
- `web/config/security.json` (optional): `{"allow_command_override": false, "extra_allowed_roots": ["/path/to/dir"]}` controls the `command_override` switch and extra directories allowed for result file reading.

### 5. First-Run Workflow

1. In `Data`, choose `New Image Upload` to upload images, or select existing remote data.
2. In the remote configuration area, fill in Host, Port, Username, Password, Repo Path, Workspace Root, and Output Root.
3. Click `Remote Precheck` and confirm that SSH, directory permissions, Python, `tmux`, `xvfb-run`, and other checks pass.
4. In `Algorithms`, pick the algorithm and task parameters.
5. Click `Submit Remote Job` — the job runs in the background on the remote server.
6. Check training logs in `Logs and Metrics`, and view the returned 3D results in `Browser`.

## Workflow

```text
Image upload / remote data reuse
        |
        v
Data materialization & COLMAP preprocessing
        |
        v
SSH/SFTP workspace upload
        |
        v
Remote tmux background training
        |
        v
Result download & PLY materialization
        |
        v
Interactive web viewing & metric analysis
```

## Common Operations

### Quick-Load an Existing PLY

No retraining needed: pick `Browser` or `Quick Load PLY` on the web page to load a local PLY file. You can also call the API:

```bash
curl -X POST http://127.0.0.1:8080/api/load-ply \
  -H "X-Auth-Token: <token>" -H "Content-Type: application/json" \
  -d '{"ply_path": "/absolute/path/scene.ply"}'
```

The endpoint only accepts paths inside the project directory (or directories listed in `extra_allowed_roots` in `web/config/security.json`).

### View Job Logs and Metrics

The web page shows the log tail and the metrics table; the same data is available via API:

```text
GET /api/jobs/<job_id>/logs/download
GET /api/jobs/<job_id>/metrics.csv
```

## Troubleshooting

### Remote Precheck Fails

First check that SSH login works, the remote directories exist and are writable, the algorithm directory is complete, and the Conda environment has been provisioned with `setup_env.sh`.

### COLMAP Fails

Make sure `colmap` is executable locally or in the remote environment. Uploaded data should contain at least 8–12 sharp, multi-view photos; avoid strong reflections, dynamic objects, repetitive textures, and severe blur.

### HAC++ Compression Fails

Check `tmc3` first:

```bash
which tmc3
tmc3 --help
```

If the command is unavailable, install MPEG PCC TMC13, add `tmc3` to `PATH`, and resubmit the HAC++ job.

### PLY Loads Blank

Check whether the PLY finished downloading, whether its file size looks sane, and whether it contains vertex data; then retry result download or reload the PLY.

## Usage Tips

- For the first test, use a small dataset with default parameters to confirm the full remote path works.
- Keep dataset paths free of spaces to simplify remote commands and log inspection.
- Do not submit too many jobs at once to the same GPU server; VRAM contention degrades all of them.
- Back up important results separately from `web/generated/runs/` or the remote output directory.

## Progressive Web Serving (WebGS)

Beyond training, the console includes the first slice of the adaptive
serving pipeline: export a trained PLY into a manifest + additive chunks
(`POST /api/export-web` or `web/tools/export_web_assets.py`), stream it
progressively in the browser with bandwidth/viewport/GPU-aware scheduling,
and collect TTFR / bytes / frame-time telemetry for evaluation. See
`web/console/README.md`.

## Development & Testing

The hand-written test suite covers remote execution, download resumption, flow data, job cleanup, and PLY loading:

```bash
python3 web/tools/run_tests.py            # run all test modules
python3 web/tools/run_tests.py detached   # run a single module by keyword
```

Every push touching `web/` runs the same suite plus frontend syntax checks on GitHub Actions (`.github/workflows/tests.yml`).

### Server module layout

- `web/server/api_server.py` — HTTP routes, job orchestration, result discovery.
- `web/server/web_security.py` — access token, same-origin enforcement, allowed result roots, JSON/error response builders.
- `web/server/remote_executor.py` — SSH/SFTP execution, remote script generation, tmux detached runs, result download.

## License

This project is released under the MIT license. Each integrated algorithm may carry its own license — see the LICENSE file in the corresponding algorithm directory before use.

## Acknowledgements

Thanks to 3D Gaussian Splatting, COLMAP, PyTorch, and the original authors and open-source community behind each integrated algorithm.
