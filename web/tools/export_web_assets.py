#!/usr/bin/env python3
"""Web Export: convert a 3DGS PLY into progressive Web serving assets.

Module A of the WebGS serving pipeline (WWW Industry Track plan): turns a
trained/compressed Gaussian scene into

    manifest.json        scene description + chunk index + scheduler hints
    base.bin             most important rows first, renderable immediately
    <tier>.bin           additive refinement rows by importance tier
    region_*.bin         spatial chunks inside later tiers (viewport-aware)
    *.sh.bin             matching spherical-harmonics rows (48 float32 each)

Rows use the viewer's 32-byte splat layout (pos/scale/rgba/rot float32+u8),
so the browser can append chunk payloads directly into its GPU staging
buffer. Chunk payloads are strictly additive: chunk k adds new Gaussians,
never rewrites earlier ones, which is what makes incremental WebGPU updates
possible.

v1 scope: binary_little_endian PLY with a single "vertex" element and SH
properties (f_dc_*/f_rest_*) — the standard 3DGS training output. Non-SH
inputs are rejected with a clear error (documented limitation).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

ROW_DTYPE = np.dtype([
    ("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
    ("sx", "<f4"), ("sy", "<f4"), ("sz", "<f4"),
    ("r", "u1"), ("g", "u1"), ("b", "u1"), ("a", "u1"),
    ("q0", "u1"), ("q1", "u1"), ("q2", "u1"), ("q3", "u1"),
])
ROW_BYTES = ROW_DTYPE.itemsize  # 32
SH_FLOATS = 48
SH_ROW_BYTES = SH_FLOATS * 4
SH_C0 = 0.28209479177387814

PLY_TYPE_MAP = {
    "float": "<f4", "double": "<f8",
    "int": "<i4", "uint": "<u4",
    "short": "<i2", "ushort": "<u2",
    "uchar": "<u1", "char": "<i1",
}

# importance tiers after the base layer: (tier_id, fraction of remaining rows)
DEFAULT_TIERS = [("q1", 0.15), ("q2", 0.25), ("q3", 1.0)]
REGION_SPLIT = 8  # octants for spatial chunks


def parse_ply(path: Path) -> dict:
    raw = path.read_bytes()
    head = raw[:65536]
    end_token = b"end_header\n"
    end_index = head.find(end_token)
    if end_index < 0:
        raise ValueError("PLY end_header not found within the first 64 KB")
    header = head[:end_index].decode("ascii", errors="replace")
    fmt = None
    elements = []
    current = None
    for line in header.split("\n"):
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "format":
            fmt = parts[1]
        elif parts[0] == "element":
            current = {"name": parts[1], "count": int(parts[2]), "props": []}
            elements.append(current)
        elif parts[0] == "property" and current is not None:
            current["props"].append((parts[2], parts[1]))
    if fmt != "binary_little_endian":
        raise ValueError(f"Unsupported PLY format: {fmt} (only binary_little_endian)")
    vertices = [e for e in elements if e["name"] == "vertex"]
    if len(elements) > 1 or not vertices:
        raise ValueError("v1 exporter supports single-element PLY files with one 'vertex' element")
    vertex = vertices[0]
    names = [name for name, _ in vertex["props"]]
    dtype = np.dtype([(name, PLY_TYPE_MAP[ptype]) for name, ptype in vertex["props"]])
    data_start = end_index + len(end_token)
    expected = vertex["count"] * dtype.itemsize
    if len(raw) - data_start < expected:
        raise ValueError(f"PLY payload truncated: expected {expected} bytes, got {len(raw) - data_start}")
    rows = np.frombuffer(raw, dtype=dtype, count=vertex["count"], offset=data_start)
    return {"names": set(names), "rows": rows, "count": vertex["count"], "props": vertex["props"]}


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def build_rows(loaded: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (32-byte splat rows, (N,48) f32 SH table, per-row importance)."""
    rows_in = loaded["rows"]
    n = loaded["count"]
    names = loaded["names"]
    for required in ("x", "y", "z"):
        if required not in names:
            raise ValueError(f"PLY lacks required property '{required}'")

    out = np.zeros(n, dtype=ROW_DTYPE)
    out["x"] = rows_in["x"]
    out["y"] = rows_in["y"]
    out["z"] = rows_in["z"]

    if {"scale_0", "scale_1", "scale_2"} <= names:
        out["sx"] = np.exp(rows_in["scale_0"])
        out["sy"] = np.exp(rows_in["scale_1"])
        out["sz"] = np.exp(rows_in["scale_2"])
        size = out["sx"] * out["sy"] * out["sz"]
    else:
        out["sx"] = out["sy"] = out["sz"] = 0.01
        size = np.full(n, 0.01 ** 3)

    if "opacity" in names:
        alpha = sigmoid(np.asarray(rows_in["opacity"], dtype=np.float64))
    else:
        alpha = np.ones(n)
    out["a"] = np.clip(alpha * 255.0, 0, 255).astype(np.uint8)

    if {"rot_0", "rot_1", "rot_2", "rot_3"} <= names:
        q = np.stack([
            np.asarray(rows_in["rot_0"], dtype=np.float64),
            np.asarray(rows_in["rot_1"], dtype=np.float64),
            np.asarray(rows_in["rot_2"], dtype=np.float64),
            np.asarray(rows_in["rot_3"], dtype=np.float64),
        ], axis=1)
        norm = np.linalg.norm(q, axis=1)
        norm[norm == 0] = 1.0
        qn = q / norm[:, None]
    else:
        qn = np.tile(np.array([1.0, 0.0, 0.0, 0.0]), (n, 1))
    for i, key in enumerate(("q0", "q1", "q2", "q3")):
        out[key] = np.clip(qn[:, i] * 128.0 + 128.0, 0, 255).astype(np.uint8)

    sh = np.zeros((n, SH_FLOATS), dtype="<f4")
    has_sh = {"f_dc_0", "f_dc_1", "f_dc_2"} <= names
    if not has_sh:
        raise ValueError(
            "v1 web export requires spherical-harmonics properties "
            "(f_dc_0/f_dc_1/f_dc_2); this PLY has none. Non-SH inputs are a "
            "documented limitation of the v1 chunk format."
        )
    if {"f_dc_0", "f_dc_1", "f_dc_2"} <= names:
        out["r"] = np.clip((0.5 + SH_C0 * np.asarray(rows_in["f_dc_0"], dtype=np.float64)) * 255.0, 0, 255).astype(np.uint8)
        out["g"] = np.clip((0.5 + SH_C0 * np.asarray(rows_in["f_dc_1"], dtype=np.float64)) * 255.0, 0, 255).astype(np.uint8)
        out["b"] = np.clip((0.5 + SH_C0 * np.asarray(rows_in["f_dc_2"], dtype=np.float64)) * 255.0, 0, 255).astype(np.uint8)
        sh[:, 0] = rows_in["f_dc_0"]
        sh[:, 1] = rows_in["f_dc_1"]
        sh[:, 2] = rows_in["f_dc_2"]
        for i in range(45):
            key = f"f_rest_{i}"
            if key in names:
                sh[:, 3 + i] = rows_in[key]
    elif {"red", "green", "blue"} <= names:
        out["r"] = np.asarray(rows_in["red"], dtype=np.uint8)
        out["g"] = np.asarray(rows_in["green"], dtype=np.uint8)
        out["b"] = np.asarray(rows_in["blue"], dtype=np.uint8)
    else:
        out["r"] = out["g"] = out["b"] = 220

    importance = size * alpha
    return out, sh, importance


def spatial_region(rows: np.ndarray) -> int:
    """Octant id by position relative to the scene median — cheap, stable."""
    cx = np.median(rows["x"])
    cy = np.median(rows["y"])
    cz = np.median(rows["z"])
    return (
        (rows["x"] >= cx).astype(np.int64)
        | ((rows["y"] >= cy).astype(np.int64) << 1)
        | ((rows["z"] >= cz).astype(np.int64) << 2)
    )


def export_web_assets(ply_path: Path, out_dir: Path, *, base_fraction: float = 0.10,
                      tiers: list | None = None, regions: bool = True) -> dict:
    started = time.time()
    tiers = tiers or DEFAULT_TIERS
    loaded = parse_ply(ply_path)
    n = loaded["count"]
    rows, sh, importance = build_rows(loaded)
    order = np.argsort(-importance, kind="stable")
    rows, sh, importance = rows[order], sh[order], importance[order]
    max_import = float(importance[0]) if n else 1.0

    positions = np.stack([rows["x"], rows["y"], rows["z"]], axis=1).astype(np.float64)
    lo = positions.min(axis=0)
    hi = positions.max(axis=0)
    center = ((lo + hi) / 2).tolist()
    radius = float(np.linalg.norm(hi - lo) / 2) or 1.0

    out_dir.mkdir(parents=True, exist_ok=True)
    base_rows = max(1, min(int(n * base_fraction), n))
    bounds = [0, base_rows]
    for _, fraction in tiers[:-1]:
        bounds.append(bounds[-1] + max(1, int(n * fraction)))
    bounds.append(n)

    chunks = []

    loaded_sh = bool({"f_dc_0", "f_dc_1", "f_dc_2"} <= loaded["names"])

    def write_parts(chunk_id: str, ctype: str, quality: int, part_rows: np.ndarray,
                    part_sh: np.ndarray, part_importance: np.ndarray,
                    region_center=None, region_radius=None) -> None:
        count = part_rows.shape[0]
        if count <= 0:
            return
        rows_file = out_dir / f"{chunk_id}.bin"
        rows_file.write_bytes(part_rows.tobytes())
        entry = {
            "id": chunk_id,
            "type": ctype,
            "quality": quality,
            "url": rows_file.name,
            "rows": int(count),
            "bytes": int(count * ROW_BYTES),
            # scheduler hint: mean normalized importance x row count
            "gain": round(float(part_importance.mean()) / max_import * count, 4),
        }
        if loaded_sh:
            sh_file = out_dir / f"{chunk_id}.sh.bin"
            sh_file.write_bytes(part_sh.tobytes())
            entry["shUrl"] = sh_file.name
            entry["shBytes"] = int(count * SH_ROW_BYTES)
        if region_center is not None:
            entry["region"] = {"center": [round(v, 4) for v in np.asarray(region_center).tolist()],
                               "radius": round(region_radius, 4)}
        chunks.append(entry)

    write_parts("base", "base", 0, rows[:base_rows], sh[:base_rows], importance[:base_rows])

    region_ids = spatial_region(rows) if regions else None

    for tier_index, (tier_id, _) in enumerate(tiers):
        start, stop = bounds[tier_index + 1], bounds[tier_index + 2]
        if regions and stop - start >= REGION_SPLIT * 32:
            for r in range(REGION_SPLIT):
                indices = np.nonzero(region_ids[start:stop] == r)[0]
                if indices.size == 0:
                    continue
                part_pos = positions[start:stop][indices]
                r_center = part_pos.mean(axis=0)
                r_radius = float(np.linalg.norm(part_pos - r_center, axis=1).max()) if indices.size > 1 else 1.0
                write_parts(
                    f"{tier_id}_r{r}", "region", tier_index + 1,
                    rows[start:stop][indices], sh[start:stop][indices],
                    importance[start:stop][indices], r_center, r_radius,
                )
        else:
            write_parts(tier_id, "tier", tier_index + 1,
                        rows[start:stop], sh[start:stop], importance[start:stop])

    manifest = {
        "version": "1.0",
        "format": "splat-rows-v1",
        "representation": "sh" if loaded_sh else "plain",
        "rowBytes": ROW_BYTES,
        "shRowBytes": SH_ROW_BYTES if loaded_sh else 0,
        "vertexCount": int(n),
        "baseRows": int(base_rows),
        "source": ply_path.name,
        "bounds": {"min": [round(v, 4) for v in lo.tolist()],
                   "max": [round(v, 4) for v in hi.tolist()],
                   "center": [round(v, 4) for v in center],
                   "radius": round(radius, 4)},
        "chunks": chunks,
        "generatedAt": time.time(),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    manifest["exportSeconds"] = round(time.time() - started, 3)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Export a 3DGS PLY into progressive Web serving chunks.")
    parser.add_argument("ply_path")
    parser.add_argument("out_dir")
    parser.add_argument("--base-fraction", type=float, default=0.10)
    args = parser.parse_args()
    manifest = export_web_assets(Path(args.ply_path), Path(args.out_dir),
                                 base_fraction=args.base_fraction)
    print(json.dumps({k: manifest[k] for k in ("vertexCount", "baseRows", "representation")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
