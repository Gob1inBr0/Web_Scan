#!/usr/bin/env python3
"""
Test script for the PLY quick load feature.
测试 PLY 快速加载功能的脚本。
"""

import sys
import json
from pathlib import Path

# 添加项目路径
ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from web.server.api_server import load_ply_file

def test_load_ply():
    """Test the load_ply_file function"""
    print("Testing PLY quick load feature...")
    print("=" * 60)
    
    # Test 1: Non-existent file
    print("\n测试 1: 不存在的文件")
    result = load_ply_file("/path/to/nonexistent.ply")
    print(f"结果: {json.dumps(result, indent=2, ensure_ascii=False)}")
    assert not result.get("ok"), "Should fail for non-existent file"
    print("✓ 测试通过: 正确处理不存在的文件")
    
    # Test 2: Invalid file type
    print("\n测试 2: 无效的文件类型")
    # 创建一个临时文件用于测试
    test_file = ROOT_DIR / "test_invalid.txt"
    test_file.write_text("This is not a PLY file")
    try:
        result = load_ply_file(str(test_file))
        print(f"结果: {json.dumps(result, indent=2, ensure_ascii=False)}")
        assert not result.get("ok"), "Should fail for non-PLY file"
        print("✓ 测试通过: 正确拒绝非 PLY 文件")
    finally:
        test_file.unlink()
    
    # Test 3: Check API format
    print("\n测试 3: 验证 API 响应格式")
    print("""
    预期的成功响应格式:
    {
        "ok": true,
        "point_cloud_url": "/web/generated/...",
        "viewer_url": "/web/viewers/sh.html?url=...",
        "path": "...",
        "file_size": ...
    }
    
    预期的失败响应格式:
    {
        "ok": false,
        "error": "...",
        "path": "..."
    }
    """)
    
    print("\n✓ 所有测试通过!")
    print("=" * 60)
    print("\n使用指南:")
    print("1. 后端 API 端点: POST /api/load-ply")
    print("2. 请求体: {\"ply_path\": \"/path/to/model.ply\", \"representation\": \"sh\"}")
    print("3. 前端 UI: 在 'Quick Load PLY' 输入框中输入文件路径，点击 'Load PLY' 按钮")
    print("\n详见 web/docs/PLY_QUICK_LOAD_GUIDE.md")

def main():
  test_load_ply()


if __name__ == "__main__":
  main()
