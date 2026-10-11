# ChickenRice 字幕工作台

日语ASMR特化！！！把日语 ASMR、音声或视频拖进窗口，就能生成中文字幕，也可以使用转模型，生成日文原文。

- 可一次处理多个文件，也能直接拖入整个文件夹。
- 支持轻声、耳语场景，可在窗口中切换预设。
- 生成后直接预览字幕，导出 SRT、VTT 或 LRC。
- 全程在电脑上处理音频，无需账号。

适用于 Windows 10 / 11。支持 NVIDIA、AMD 显卡加速，也支持 CPU。

## 开始使用

1. **安装 [Python 3.12](https://www.python.org/downloads/windows/)**，安装时勾选 **Add python.exe to PATH**。
2. **双击 `首次使用-下载所需文件.cmd`**。按回车只下载中文翻译模型；需要日文原文，要下载日语转录模型时选 `2`，这会同时下载翻译和转录模型。脚本会自动下载、安装并放好所需文件。
3. **双击 `启动字幕工作台.bat`**，拖入音频，点击“开始生成字幕”。

首次下载需要一些时间；中途断开后，重新双击下载脚本即可继续。已经准备好的文件会直接复用。

字幕默认保存在音频旁的 **`ChickenRice字幕`** 文件夹中，中文和日文分别存放。

## AMD 显卡

首次下载会识别显卡，自动选择对应引擎。支持上游列出的 **Radeon RX 5000、6000、7000、9000 系列独显**，具体型号见[上游支持列表](https://github.com/TransWithAI/Faster-Whisper-TransWithAI-ChickenRice/blob/v1.10/README.md#amd-显卡rocmhip-amd-gpu-rocmhip)。请先安装 AMD 显卡驱动；引擎已内置 ROCm/HIP 运行文件，无需另外安装 ROCm。

**已经使用旧版的 AMD 用户**：在工作台中将运行设备改为“AMD GPU”，关闭窗口保存选择，再运行 `首次使用-下载所需文件.cmd`。模型会复用，旧引擎会保存在 `.setup` 的备份目录。AMD 默认选择“自动精度”。

RX 580、Vega、890M / 8060S / 860M 等未支持型号会使用 CPU。同时有 NVIDIA 和 AMD 显卡时默认使用 NVIDIA，也可按上述步骤切换到 AMD。

## 日常使用

- **只想要日文字幕**：将任务切换为“日语音频 → 日文转录”。
- **轻声对白漏掉了**：试试“轻声 / 耳语”预设。
- **想重新生成**：选择新的输出文件夹，或勾选“覆盖已有字幕”。

更多操作见 [使用说明](GUI使用说明.md)。

## 常见问题

1. **下载失败怎么办？** 检查网络是否能访问 GitHub 和 Hugging Face，必要时开启代理（挂梯），然后重新双击下载脚本继续。

## 下载来源

下载脚本会自动获取以下文件，也可以点击链接自行下载。

| 文件 | 用途 |
|---|---|
| [ChickenRice 引擎](https://github.com/TransWithAI/Faster-Whisper-TransWithAI-ChickenRice/releases/tag/v1.10) | 处理音频、生成字幕 |
| [中文翻译模型](https://huggingface.co/chickenrice0721/whisper-large-v2-translate-zh-v0.2-st-ct2) | 日语音频 → 中文字幕 |
| [日文转录模型](https://huggingface.co/TransWithAI/whisper-ja-1.5B-ct2) | 日语音频 → 日文字幕，按需下载 |

## 致谢与许可

感谢 [TransWithAI](https://github.com/TransWithAI/Faster-Whisper-TransWithAI-ChickenRice) 与模型作者。工作台采用 [GPL-3.0](LICENSE) 许可证。
