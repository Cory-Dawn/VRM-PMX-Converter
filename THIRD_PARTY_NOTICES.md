# 第三方声明

项目代码采用 GPL-3.0-only。以下内容保留各自的版权和许可：

- `vrm2pmx/spring_ref.py` 的 VRM SpringBone 参考实现移植自 [UniVRM](https://github.com/vrm-c/UniVRM)，原许可见 `LICENSES/UniVRM.txt`。
- `vrm2pmx/physics.py` 中的部分转换逻辑移植自 [PMXExporter](https://github.com/lispcoc/VRM2PMX-Unity/tree/963a5872a945da2e3d0c18c23a1e51af01bca18c/Assets/PMXExporter)，原许可见 `LICENSES/PMXExporter.txt`。
- pypinyin 的默认词典来自 [pinyin-data](https://github.com/mozillazg/pinyin-data/tree/fa9761fff402f8560196b1ba085c437c52b56d7c) 和 [phrase-pinyin-data](https://github.com/mozillazg/phrase-pinyin-data/tree/cee0ed6e6e4898580cafd2bd5e3723e20b214aa0)，其中使用 Unicode Unihan 数据。相关许可见 `LICENSES/pinyin-data.txt`、`LICENSES/phrase-pinyin-data.txt` 和 `LICENSES/Unicode.txt`。

源码运行依赖 NumPy、Pillow、pypinyin 和 tkinterdnd2。Windows v1.4.2 程序还包含 Python、Tcl/Tk、tkdnd、OpenSSL 和其他运行组件，由 PyInstaller 打包。预构建 ZIP 中的 `third-party-licenses/` 包含对应的许可和版权声明。Microsoft 运行库的分发条件适用于 Microsoft 组件；其 Windows 运行库声明见该目录中的 `Python-Windows-CRT.txt`。

这些第三方许可和版权不因本项目的 GPL 许可证而改变。模型文件及其使用权限由各模型作者决定，本项目不分发测试模型。
