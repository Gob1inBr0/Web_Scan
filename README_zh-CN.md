# Web_Scan - 3D Gaussian Splatting Web 平台

![Python](https://img.shields.io/badge/Python-3.10-blue.svg) ![PyTorch](https://img.shields.io/badge/PyTorch-2.2%2B-red.svg) ![License](https://img.shields.io/badge/License-MIT-green.svg)

## 项目概述

**Web_Scan** 是一个面向 3D Gaussian Splatting 算法的 Web 工作台，提供统一的数据上传、远程训练、日志监控、结果下载、PLY 快速加载和交互式结果查看流程。

平台采用前后端轻量架构：浏览器负责操作界面和结果展示，Python API 服务负责数据物化、任务编排、SSH/SFTP 远程执行和结果回传。算法环境不在 README 中展开手动配置细节，统一通过各算法目录下的 `setup_env.sh` 完成。

## 核心能力

- **多算法接入**：通过适配器配置统一管理训练、渲染、压缩和评估任务。
- **Web 数据流**：支持图片上传、相机采集、远程数据复用和 COLMAP 预处理。
- **远程训练**：通过 SSH/SFTP 下发任务，支持 `tmux` 后台运行和结果自动下载。
- **实时监控**：展示任务状态、训练日志、Loss、PSNR、迭代数、FPS 等指标。
- **结果查看**：支持 PLY 快速加载、交互式 3D 浏览、结果包导出和指标分析。

## Web 界面截图

截图文件位于 [`web照片/`](web照片/)。

| 总览工作台 | 数据源配置 |
|---|---|
| ![Web-GSC总览工作台](<web照片/截屏2026-05-19 13.38.15.png>) | ![远程数据源与连接配置](<web照片/截屏2026-05-19 13.38.29.png>) |

| 新图片上传 | 远程训练命令预览 |
|---|---|
| ![新图片上传和相机采集](<web照片/截屏2026-05-19 13.38.44.png>) | ![算法训练配置与远程预检](<web照片/截屏2026-05-19 13.38.59.png>) |

| 日志与指标 | 交互式结果浏览 |
|---|---|
| ![训练日志和实时指标](<web照片/截屏2026-05-19 13.39.06.png>) | ![3D结果浏览器](<web照片/截屏2026-05-19 13.39.29.png>) |

| 训练指标分析 |
|---|
| ![PSNR、SSIM、LPIPS与模型大小分析](<web照片/截屏2026-05-19 13.39.37.png>) |

## 支持的算法

| 算法 | 主要用途 | 环境脚本 |
|---|---|---|
| **HAC++** | 高压缩率 Gaussian 表示与压缩评估 | `HAC-plus-main/setup_env.sh` |
| **ContextGS** | 上下文建模与压缩 | `ContextGS-main/setup_env.sh` |
| **CompGS** | 率失真优化压缩 | `CompGS-main/setup_env.sh` |
| **GaussianPro** | 渐进传播训练，适合纹理缺失场景 | `GaussianPro-version1.0/setup_env.sh` |
| **GSLightning** | PyTorch Lightning 训练框架 | `gaussian-splatting-lightning-main/setup_env.sh` |
| **MEGS-2** | 内存高效 Gaussian 表示 | `MEGS-2-main/setup_env.sh` |
| **reduced-3dgs** | 降低显存和存储占用 | `reduced-3dgs-main/setup_env.sh` |
| **AtomGS** | 原子化 Gaussian 表示 | `AtomGS-main/setup_env.sh` |

## 项目结构

```text
Web_Scan/
├── web/                                  # Web 应用核心代码
│   ├── index.html                       # 主页面入口
│   ├── styles.css                       # 页面样式
│   ├── src/                             # 前端模块
│   ├── server/                          # Python API 服务
│   ├── tools/                           # 数据处理、导出和测试工具
│   ├── config/                          # 算法适配器配置
│   ├── scenes/                          # 示例场景
│   └── viewers/                         # 结果查看器
├── web照片/                              # README 使用的界面截图
├── HAC-plus-main/                       # HAC++ 算法目录
├── ContextGS-main/                      # ContextGS 算法目录
├── CompGS-main/                         # CompGS 算法目录
├── GaussianPro-version1.0/              # GaussianPro 算法目录
├── gaussian-splatting-lightning-main/   # GSLightning 算法目录
├── MEGS-2-main/                         # MEGS-2 算法目录
├── reduced-3dgs-main/                   # reduced-3dgs 算法目录
└── AtomGS-main/                         # AtomGS 算法目录
```

## 运行环境

### 本地 Web 服务

| 项目 | 要求 |
|---|---|
| 操作系统 | macOS / Linux / Windows + WSL |
| Python | Python 3.10 |
| 浏览器 | Chrome / Edge / Safari / Firefox 现代版本 |
| Python 依赖 | `flask`、`paramiko`、`numpy` |
| 可选工具 | `COLMAP`、`ImageMagick`/`magick`、`nvidia-smi` |
| 默认地址 | `http://127.0.0.1:8080/web/` |

本地只运行 Web 界面、上传、任务编排和结果查看时，不强制要求 NVIDIA GPU。若在本机直接训练，需要安装对应算法的 CUDA、PyTorch 和扩展依赖。

### 远程训练服务器

| 项目 | 要求 |
|---|---|
| 系统 | Linux |
| Python | Python 3.10 |
| GPU | NVIDIA GPU，建议 16GB+ 显存；大场景建议 24GB+ |
| CUDA / PyTorch | 由目标算法的 `setup_env.sh` 安装或校验 |
| 执行工具 | SSH/SFTP、`tmux`、`xvfb-run` |
| 存储 | 至少 50GB 可用空间；多任务或大场景建议 SSD 500GB+ |

### HAC++ 额外依赖：tmc3

HAC++ 的 GPCC 压缩链路会调用 `tmc3`。运行 HAC++ 前请在训练服务器安装 MPEG PCC TMC13，并确保 `tmc3` 可直接执行：

```bash
which tmc3
tmc3 --help
```

如果 `which tmc3` 没有输出，需要把 `tmc3` 可执行文件所在目录加入 `PATH`，或在运行环境中设置对应的 GPCC 编码器路径。

## 环境配置脚本

每个算法目录都提供 `setup_env.sh`。进入目标算法目录后运行脚本即可配置对应的 Python 3.10 Conda 环境和 CUDA 扩展：

```bash
cd HAC-plus-main
bash setup_env.sh
```

也可以从仓库根目录直接运行：

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

脚本默认需要可用的 Conda 或 Mamba。若服务器没有自动加载 Conda，请先执行类似命令：

```bash
source /path/to/miniconda3/etc/profile.d/conda.sh
```

## 快速开始

### 1. 安装 Web 服务依赖

```bash
python3.10 -m pip install flask paramiko numpy
```

### 2. 启动 API 和静态服务

```bash
python3.10 web/server/api_server.py --host 127.0.0.1 --port 8080
```

### 3. 打开 Web 页面

```text
http://127.0.0.1:8080/web/
```

### 4. 首次运行流程

1. 在 `Data` 中选择 `New Image Upload` 上传图片，或选择已有远程数据。
2. 在远程配置区域填写 Host、Port、Username、Password、Repo Path、Workspace Root 和 Output Root。
3. 点击 `Remote Precheck`，确认 SSH、目录权限、Python、`tmux`、`xvfb-run` 等检查通过。
4. 在 `Algorithms` 中选择算法和任务参数。
5. 点击 `Submit Remote Job`，任务会在远程服务器后台执行。
6. 在 `Logs and Metrics` 查看训练日志，在 `Browser` 查看回传后的 3D 结果。

## 工作流

```text
图片上传 / 远程数据复用
        |
        v
数据物化与 COLMAP 预处理
        |
        v
SSH/SFTP 上传 workspace
        |
        v
远程 tmux 后台训练
        |
        v
结果下载与 PLY 物化
        |
        v
Web 端交互式查看与指标分析
```

## 常用操作

### 快速加载已有 PLY

无需重新训练，可直接在 Web 页面中选择 `Browser` 或 `Quick Load PLY` 加载本地 PLY 文件。也可以调用接口：

```bash
curl -X POST http://127.0.0.1:8080/api/load-ply \
  -F "ply_file=@scene.ply"
```

### 查看任务日志和指标

Web 页面会展示日志尾部和指标表，也可以通过接口下载：

```text
GET /api/jobs/<job_id>/logs/download
GET /api/jobs/<job_id>/metrics.csv
```

## 常见问题

### Remote Precheck 失败

优先检查 SSH 是否可登录、远程目录是否存在且可写、算法目录是否完整、Conda 环境是否已通过 `setup_env.sh` 配置完成。

### COLMAP 处理失败

确认 `colmap` 在本地或远程环境中可执行。上传数据建议至少 8-12 张清晰、多视角照片，避免强反光、动态物体、重复纹理和严重模糊。

### HAC++ 压缩失败

先检查 `tmc3`：

```bash
which tmc3
tmc3 --help
```

若命令不可用，安装 MPEG PCC TMC13 后把 `tmc3` 加入 `PATH`，再重新提交 HAC++ 任务。

### PLY 加载空白

检查 PLY 是否完整下载、文件大小是否异常、是否包含顶点数据，并尝试重新点击结果下载或重新加载 PLY。

## 使用建议

- 首次测试用小数据集和默认参数，确认远程链路能完整跑通。
- 数据集建议保持路径无空格，便于远程命令和日志排查。
- 同一台 GPU 服务器不要同时提交过多任务，避免显存争抢。
- 重要结果应从 `web/generated/runs/` 或远程输出目录单独备份。

## 许可证

本项目遵循 MIT 许可证。各算法实现可能包含各自许可证，使用前请参考对应算法目录内的 LICENSE 文件。

## 致谢

感谢 3D Gaussian Splatting、COLMAP、PyTorch 以及各算法原作者和开源社区的贡献。
