# PLY 文件快速加载指南

## 功能说明
已为您添加了一个独立的 PLY 文件加载接口，无需依赖整个训练流程即可直接展示本地或服务器上的 PLY 文件。

## 使用方式

### 1. 后端 API 接口
**端点：** `POST /api/load-ply`

**请求体：**
```json
{
  "ply_path": "/path/to/model.ply",
  "representation": "sh"
}
```

**参数说明：**
- `ply_path` (必需): PLY 文件的绝对路径或相对于根目录的路径
- `representation` (可选): 渲染器类型，支持：
  - `"sh"` (默认): Spherical Harmonics
  - `"sg"`: Spherical Gaussians

**响应示例（成功）：**
```json
{
  "ok": true,
  "point_cloud_url": "/web/generated/ply_cache/model_1234567890.ply",
  "viewer_url": "/web/viewers/sh.html?url=/web/generated/ply_cache/model_1234567890.ply",
  "path": "/abs/path/to/model.ply",
  "file_size": 5242880
}
```

**响应示例（失败）：**
```json
{
  "ok": false,
  "error": "PLY 文件不存在: /invalid/path.ply",
  "path": "/invalid/path.ply"
}
```

### 2. 前端使用

#### 方法A：UI 面板
1. 打开 Web 应用主页面
2. 在左侧面板中找到 "Quick Load PLY (Local Path)" 输入框
3. 输入 PLY 文件的绝对路径，例如：
   - `/Users/chen/Documents/Web_Scan/web/generated/output/model.ply`
   - `/path/to/HAC-results/point_cloud.ply`
4. 点击 "Load PLY" 按钮
5. 等待加载完成，模型将显示在查看器中

#### 方法B：直接调用 API
```javascript
// 在浏览器控制台或应用代码中
const result = await loadPlyFile('http://127.0.0.1:8080', {
  ply_path: '/path/to/model.ply',
  representation: 'sh'
});

if (result.ok) {
  // 使用 result.viewer_url 打开查看器
  window.location.href = result.viewer_url;
}
```

### 3. 文件路径说明

#### 支持的路径格式
1. **绝对路径**（推荐）
   - 示例: `/Users/chen/Documents/Web_Scan/web/generated/output/model.ply`
   - 必须是完整的绝对路径

2. **相对于 ROOT_DIR 的路径**
   - ROOT_DIR 通常是您的项目根目录
   - 示例: `web/generated/output/model.ply`

#### 文件位置建议
- HAC 训练结果通常在: `{output_dir}/point_cloud.ply`
- MEGS 训练结果通常在: `{output_dir}/point_cloud.ply`
- CompGS 训练结果通常在: `{output_dir}/point_cloud.ply`

### 4. 常见问题与解决方案

#### Q: 提示"PLY 文件不存在"
**A:** 
1. 检查文件路径是否正确（使用绝对路径）
2. 确认文件确实存在（可以在文件管理器中打开）
3. 检查文件权限是否允许读取
4. 尝试使用 `ls` 或 `find` 命令验证文件路径

#### Q: 提示"文件不是 PLY 格式"
**A:** 
1. 确认文件后缀为 `.ply`
2. 检查文件是否真的是 PLY 格式（可用文本编辑器查看前几行）
3. 确保文件没有损坏

#### Q: 无法访问 ROOT_DIR 外的文件
**A:** 
1. 如果您需要加载 ROOT_DIR 外的文件，系统会自动将其复制到缓存目录
2. 确保有足够的磁盘空间
3. 检查文件访问权限

### 5. 技术细节

#### 后端处理流程
1. 接收 PLY 文件路径
2. 验证文件是否存在且为 `.ply` 格式
3. 如果文件在 ROOT_DIR 内：直接返回相对 URL
4. 如果文件在 ROOT_DIR 外：复制到缓存目录 (`web/generated/ply_cache/`)
5. 返回可用于查看器的 URL

#### 前端处理流程
1. 用户输入 PLY 文件路径
2. 点击"Load PLY"按钮
3. 调用后端 `/api/load-ply` 接口
4. 获取查看器 URL
5. 使用该 URL 打开 3D 查看器

### 6. 集成到现有流程

这个新接口与现有的训练流程完全独立，您可以：
- **在不运行训练的情况下直接查看结果**
- **快速验证已有的 PLY 文件**
- **调试和检查中间结果**
- **分享结果给他人查看**

## 注意事项

1. **文件大小**：确保 PLY 文件大小在合理范围内（通常 < 100MB）
2. **权限**：确保服务器有读取文件的权限
3. **路径安全**：输入的路径会被验证，防止路径遍历攻击
4. **缓存清理**：缓存目录中的文件会逐渐累积，定期清理即可

## 示例用法

### 示例 1：加载 HAC 训练结果
```javascript
// 后端已生成结果在 /path/to/hac-output/point_cloud.ply
const result = await loadPlyFile('http://127.0.0.1:8080', {
  ply_path: '/path/to/hac-output/point_cloud.ply',
  representation: 'sh'
});

// 使用查看器 URL
if (result.ok) {
  window.open(result.viewer_url);
}
```

### 示例 2：从 UI 加载
1. 输入框输入: `/Users/chen/Documents/Web_Scan/FCGS-main/results/scene/point_cloud.ply`
2. 点击 "Load PLY" 按钮
3. 等待加载完成

## 相关文件修改

- `web/server/api_server.py`: 添加了 `load_ply_file()` 函数和 `/api/load-ply` 端点
- `web/src/api/server-client.js`: 添加了 `loadPlyFile()` 客户端函数
- `web/src/core/app.js`: 添加了 `handleLoadPlyQuick()` 处理函数和事件监听器
- `web/index.html`: 添加了"Quick Load PLY"输入框和按钮
