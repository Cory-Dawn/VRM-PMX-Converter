<img src="assets/VRM2PMX.svg" width="80" alt="VRM → PMX 转换器图标">

# VRM → PMX 转换器

一款免费开源的 Windows 工具，将 UniVRM 输出的 VRM 0.x 角色模型转换为 MikuMikuDance 可使用的 PMX 模型。

## 主要特点

- **自动生成常用 MMD 骨骼**：转换时建立标准骨骼及常用辅助骨骼，减少手动追加骨骼的工作。
- **优化物理表现**：根据 VRM 弹簧骨参数生成物理，针对 MMD 中的抖动进行优化，尽量减少后续调整。
- **整理表情面板**：根据表情名称自动分类；无法识别的名称会归入“其他”。
- **支持材质表情**：可将部分材质颜色、透明度变化转换为 PMX 材质表情，包括以透明度控制部件显示的开关。
- **复用贴图**：多个材质引用同一贴图时复用输出文件，避免重复导出该引用。
- **继承材质设置**：根据 VRM 材质设置生成描边，并继承双面显示设置。
- **可选名称转换**：将材质和表情名中的部分中文转换为日文常用字形，缓解 MMD 或 PMX Editor 中的乱码。
- **轻量易用**：支持拖放与批量添加，提供浅色和深色界面。Windows 发布包约 66 MB，解压后无需安装 Python。

模型结构、材质和动作各不相同，物理、描边及表情效果不能保证在所有模型上完全一致；转换后建议在 MMD 或 PMX Editor 中检查。名称转换会改写名称，可能影响旧动作中对应的表情轨道。

## 软件界面

![深色主题下的模型列表和转换设置](docs/images/interface-dark.jpg)

<details>
<summary>查看浅色界面</summary>

![浅色主题下的模型列表和转换设置](docs/images/interface-light.jpg)

</details>

界面截图中的模型名称和作者为演示数据。

## 下载和使用

在本仓库的 Releases 页面下载最新的 `VRM2PMX_v1.4.2_release.zip`，解压后双击 `VRM2PMX.exe`。选择 VRM 模型、输出位置和转换选项后，点击“开始转换”。详细操作见 [使用说明](使用说明.md)。

转换涉及骨骼、表情、材质、贴图和物理。不同模型的效果可能不同，请在 MMD 或 PMX Editor 中检查结果，并遵守模型作者的使用条款。

## 从源码运行

Windows 需要 Python 3.13 或 3.14：

```powershell
py -3.14 -m pip install -r requirements.txt
py -3.14 main.py
```

命令行用法：

```powershell
py -3.14 -m vrm2pmx.cli "输入.vrm" -o "输出目录"
```

## 构建 Windows 程序

以 Python 3.13 环境为例，安装依赖及 PyInstaller，然后运行：

```powershell
py -3.13 -m pip install -r requirements.txt pyinstaller
py -3.13 -m PyInstaller --noconfirm --clean VRM2PMX.spec
py -3.13 -m PyInstaller --noconfirm --clean VRM2PMX-CLI.spec
```

生成的程序位于 `dist/`。v1.4.2 发布包使用 Python 3.13.17、NumPy 2.5.4、Pillow 13.0.0、pypinyin 0.55.0、tkinterdnd2 0.7.0 和 PyInstaller 6.22.3 构建。

图标原始文件为 `assets/VRM2PMX.svg`，可运行 `py -3.13 dev/make_icon.py` 重新生成 PNG 和 ICO。

## 许可证

Copyright (c) 2026 CoryDawn。项目代码采用 [GNU GPL v3.0](LICENSE)（GPL-3.0-only）许可证。

## 第三方许可

部分物理实现移植自 [UniVRM](https://github.com/vrm-c/UniVRM) 和 [PMXExporter](https://github.com/lispcoc/VRM2PMX-Unity/tree/master/Assets/PMXExporter)，原作者的许可声明见 `LICENSES/`。其他组件及发布包的许可信息见 [第三方声明](THIRD_PARTY_NOTICES.md)。

## 支持作者

软件可免费使用，赞助完全自愿。如果这个软件帮到了你，请作者喝杯咖啡吧！

[前往爱发电支持作者](https://afdian.com/a/CoryDawn)
