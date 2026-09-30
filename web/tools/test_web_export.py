"""Tests for the WebGS progressive export (Module A of the serving plan)."""
from __future__ import annotations

import json
import struct
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from web.tools.export_web_assets import export_web_assets, parse_ply  # noqa: E402


def write_test_ply(path: Path, n: int = 500) -> None:
    """A tiny but valid SH PLY: gaussian positions on a line, varying opacity."""
    header = ["ply", "format binary_little_endian 1.0", f"element vertex {n}"]
    props = ["x", "y", "z", "scale_0", "scale_1", "scale_2", "opacity",
             "rot_0", "rot_1", "rot_2", "rot_3", "f_dc_0", "f_dc_1", "f_dc_2"]
    props += [f"f_rest_{i}" for i in range(45)]
    for name in props:
        header.append(f"property float {name}")
    header.append("end_header")
    with open(path, "wb") as f:
        f.write(("\n".join(header) + "\n").encode())
        for i in range(n):
            values = [
                float(i), 0.0, 0.0,
                -2.3, -2.3, -2.3,          # exp -> ~0.1 scale
                float(6.0 - 8.0 * i / n),  # opacity declines along the line
                1.0, 0.0, 0.0, 0.0,
                float(i % 7) / 7.0 - 0.5, 0.2, -0.2,
            ] + [0.0] * 45
            f.write(struct.pack("<" + "f" * len(values), *values))


def test_export_produces_complete_additive_chunks() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ply = Path(tmp) / "scene.ply"
        write_test_ply(ply, 500)
        out = Path(tmp) / "assets"
        manifest = export_web_assets(ply, out)

        assert manifest["format"] == "splat-rows-v1"
        assert manifest["representation"] == "sh"
        assert manifest["vertexCount"] == 500
        assert manifest["shRowBytes"] == 192

        total_rows = 0
        seen_ids = set()
        for chunk in manifest["chunks"]:
            assert chunk["id"] not in seen_ids
            seen_ids.add(chunk["id"])
            rows_file = out / chunk["url"]
            assert rows_file.is_file()
            assert rows_file.stat().st_size == chunk["rows"] * 32
            assert chunk["bytes"] == chunk["rows"] * 32
            sh_file = out / chunk["shUrl"]
            assert sh_file.is_file()
            assert sh_file.stat().st_size == chunk["rows"] * 192
            assert chunk["shBytes"] == chunk["rows"] * 192
            total_rows += chunk["rows"]
        assert total_rows == 500, f"chunks must partition the scene, got {total_rows}"

        manifest_disk = json.loads((out / "manifest.json").read_text())
        assert manifest_disk["vertexCount"] == 500


def test_base_layer_is_most_important_and_first() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ply = Path(tmp) / "scene.ply"
        write_test_ply(ply, 500)
        out = Path(tmp) / "assets"
        manifest = export_web_assets(ply, out)

        base = manifest["chunks"][0]
        assert base["type"] == "base" and base["quality"] == 0
        assert base["rows"] == 50  # 10% of 500

        # importance declines along the input line, so the base must contain
        # the first 50 rows in file order (highest opacity).
        rows_file = out / base["url"]
        raw = rows_file.read_bytes()
        first_alpha = raw[27]
        last_alpha = raw[49 * 32 + 27]
        assert first_alpha >= last_alpha, "base rows must be importance-ordered"

        # all base alphas must be >= any refinement alpha (highest importance first)
        refinement = b"".join((out / c["url"]).read_bytes() for c in manifest["chunks"][1:])
        min_refine_alpha = min(refinement[i * 32 + 27] for i in range(len(refinement) // 32))
        assert first_alpha >= min_refine_alpha


def test_exporter_rejects_non_sh_and_non_binary() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ply = Path(tmp) / "plain.ply"
        header = ["ply", "format binary_little_endian 1.0", "element vertex 1",
                  "property float x", "property float y", "property float z", "end_header"]
        ply.write_bytes(("\n".join(header) + "\n").encode() + struct.pack("<fff", 0, 0, 0))
        try:
            export_web_assets(ply, Path(tmp) / "out")
            raise AssertionError("non-SH PLY must be rejected")
        except ValueError:
            pass

        ascii_ply = Path(tmp) / "ascii.ply"
        ascii_ply.write_text("ply\nformat ascii 1.0\nelement vertex 1\nend_header\n")
        try:
            export_web_assets(ascii_ply, Path(tmp) / "out2")
            raise AssertionError("ascii PLY must be rejected")
        except ValueError:
            pass


def test_parse_ply_reports_count() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ply = Path(tmp) / "scene.ply"
        write_test_ply(ply, 37)
        loaded = parse_ply(ply)
        assert loaded["count"] == 37


def main() -> None:
    test_export_produces_complete_additive_chunks()
    test_base_layer_is_most_important_and_first()
    test_exporter_rejects_non_sh_and_non_binary()
    test_parse_ply_reports_count()
    print("web export tests passed")


if __name__ == "__main__":
    main()
