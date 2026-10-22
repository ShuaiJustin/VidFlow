# Mac 托盘图标生成说明

## 为什么需要 Template 图标？

在 macOS 上，系统托盘图标应该使用 "Template" 图标格式。这些图标是黑白的，系统会根据菜单栏的亮/暗模式自动调整颜色。

## 如何创建 Template 图标

### 方法 1: 使用 Python PIL (推荐)

运行以下脚本生成 Template 图标：

```bash
cd resources/icons
python generate_mac_tray_template.py
```

### 方法 2: 手动创建

1. 打开 `tray-icon.png`
2. 转换为纯黑白图标（黑色为不透明区域，白色/透明为透明区域）
3. 保存为 `tray-iconTemplate.png` (标准分辨率)
4. 保存为 `tray-iconTemplate@2x.png` (Retina 分辨率，2倍大小)

### 方法 3: 使用现有工具

使用图像编辑软件如 Photoshop 或 GIMP：
- 去色处理（Desaturate）
- 增加对比度，使其接近纯黑白
- 调整 Alpha 通道
- 导出为 PNG

## Template 图标命名规范

- `tray-iconTemplate.png` - Electron 会自动识别 "Template" 后缀
- `tray-iconTemplate@2x.png` - Retina 显示屏版本（可选但推荐）

## 在代码中使用

在 `electron/main.js` 中，必须显式加载 Template 文件。加载普通的
`tray-icon.png` 不会自动寻找同目录的 Template 文件：

```javascript
const trayIconPath = path.join(__dirname, '../resources/icons/tray-iconTemplate.png');
const trayImage = nativeImage.createFromPath(trayIconPath);
trayImage.setTemplateImage(true);
const tray = new Tray(trayImage);
```

## 图标设计建议

1. **尺寸**: 16x16 到 22x22 像素（标准），32x32 到 44x44 像素（@2x）
2. **颜色**: 纯黑色图标，透明背景
3. **简洁性**: 图标应该简单清晰，在小尺寸下可识别
4. **对称性**: 居中对齐，四周留有适当边距
