#!/usr/bin/env python3
"""Run every hand-written test module in web/tools and report a summary.

Usage:  python3 web/tools/run_tests.py [test_name ...]
Without arguments all test modules run. Exit code is non-zero on any failure.
"""
from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

TEST_MODULES = [
  "web.tools.test_detached_remote",
  "web.tools.test_flow_data",
  "web.tools.test_flow_reset",
  "web.tools.test_job_cleanup",
  "web.tools.test_ply_quick_load",
  "web.tools.test_web_export",
]


def main() -> int:
  requested = sys.argv[1:]
  modules = [m for m in TEST_MODULES if not requested or any(r in m for r in requested)]
  failures = []
  for module_name in modules:
    started = time.time()
    print(f"[run] {module_name}", flush=True)
    try:
      module = importlib.import_module(module_name)
      entry = getattr(module, "main", None)
      if entry is None:
        raise RuntimeError("module has no main() entry point")
      entry()
      print(f"[pass] {module_name} ({time.time() - started:.1f}s)", flush=True)
    except Exception as exc:
      failures.append(module_name)
      print(f"[fail] {module_name}: {exc}", flush=True)
  if failures:
    print(f"\n{len(failures)} of {len(modules)} test modules failed: {', '.join(failures)}")
    return 1
  print(f"\nAll {len(modules)} test modules passed.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
